from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import numpy as np

from .artifact_layout import generated_models_dir, model_artifact_slug
from .compiler import CompileBlocked, CompileResult, compile_usage_instructions
from .config import Settings
from .phasetracer_expr import (
    PhaseTracerExpressionError,
    render_cpp_accumulation_block,
    render_cpp_assignment_block,
    render_cpp_expression,
)
from .phasetracer_runner import run_phasetracer_smoke
from .smoke_output import parse_detailed_smoke_output as _parse_detailed_smoke_output
from .template_contract import reviewed_renormalization_scale_name, validate_contract_template


PHASETRACER_SMOKE_ABS_TOL = 1e-8
PHASETRACER_SMOKE_REL_TOL = 1e-10
PHASETRACER_LOOP_SMOKE_ABS_TOL = 1e-7
PHASETRACER_LOOP_SMOKE_REL_TOL = 1e-9


def compile_phasetracer_template(
    markdown_text: str,
    settings: Settings,
    *,
    output_dir: Path | None = None,
    output_path: Path | None = None,
    run_smoke: bool = True,
    run_transition_smoke: bool = False,
    phasetracer_root: str = "",
    linux_runner: str = "native",
    wsl_distro: str = "",
    clang_format: bool = True,
) -> CompileResult:
    validation = validate_contract_template(markdown_text, review_required=True, compile_backend="phasetracer")
    if not validation.ok:
        preview = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.issues[:5])
        raise CompileBlocked("Template contract is not compile-ready: " + preview)
    contract = validation.contract
    _validate_phasetracer_contract(contract)
    project_dir = output_path or _candidate_project_dir(contract, settings, output_dir=output_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    template_sha256 = _sha256_text(markdown_text)
    contract_sha256 = _sha256_contract(contract)
    files = _render_phasetracer_project(
        contract,
        template_sha256=template_sha256,
        contract_sha256=contract_sha256,
    )
    for name, content in files.items():
        (project_dir / name).write_text(content, encoding="utf-8")

    warnings: list[str] = []
    warnings.extend(_maybe_clang_format_project(project_dir, enabled=clang_format))
    status = "generated"
    if run_smoke:
        result = run_phasetracer_smoke(
            project_dir,
            phasetracer_root=phasetracer_root,
            linux_runner=linux_runner,
            wsl_distro=wsl_distro,
            run_transition_smoke=run_transition_smoke,
        )
        status = result.status
        if not result.ok:
            raise CompileBlocked(f"Generated PhaseTracer project failed local smoke check: {result.status}\n{result.stderr or result.stdout}")
        numerical_status = compare_phasetracer_smoke_output(contract, result.stdout)
        status = f"{result.status}; {numerical_status}"
        if result.stderr.strip():
            warnings.append("PhaseTracer smoke emitted stderr output.")
    model_path = project_dir / _phasetracer_header_filename(contract)
    return CompileResult(
        model_path=model_path,
        import_check_status=status,
        warnings=warnings,
        usage_instructions=compile_usage_instructions(model_path, "phasetracer", phasetracer_root=phasetracer_root),
    )


def evaluate_contract_potential(contract: dict[str, Any], phi: list[float], temperature: float) -> float:
    _validate_phasetracer_contract(contract)
    if _uses_standard_thermal_integrals(contract):
        raise CompileBlocked("Python PhaseTracer reference does not implement backend-native standard thermal integrals.")
    return _evaluate_total_without_standard_thermal_integrals(contract, phi, temperature)


def evaluate_contract_v0(contract: dict[str, Any], phi: list[float], temperature: float = 0.0) -> float:
    env = _reference_environment(contract, phi, temperature)
    return _eval_compiler_block(str(_dict(_dict(contract.get("potential")).get("V0")).get("python", "0.0")), env)


def compare_phasetracer_smoke_output(
    contract: dict[str, Any],
    stdout: str,
    *,
    abs_tol: float = PHASETRACER_SMOKE_ABS_TOL,
    rel_tol: float = PHASETRACER_SMOKE_REL_TOL,
) -> str:
    detailed = _parse_detailed_smoke_output(stdout)
    if not detailed:
        raise CompileBlocked("PhaseTracer numerical smoke check failed: no PTAGENT_POINT smoke output found; regenerate and run the current run_model.cpp.")
    return _compare_detailed_smoke_output(contract, detailed, abs_tol=abs_tol, rel_tol=rel_tol)


def _compare_detailed_smoke_output(
    contract: dict[str, Any],
    parsed: dict[int, dict[str, Any]],
    *,
    abs_tol: float,
    rel_tol: float,
) -> str:
    points = _smoke_points(contract)
    errors: list[str] = []
    compare_total = not _uses_standard_thermal_integrals(contract)
    for index, (phi, temperature) in enumerate(points):
        if index not in parsed:
            errors.append(f"missing smoke point {index}")
            continue
        point = parsed[index]
        scalars = _dict(point.get("scalars"))
        vectors = _dict(point.get("vectors"))
        if "V0" in scalars:
            _append_scalar_error(errors, f"point {index} V0", evaluate_contract_v0(contract, phi, temperature), scalars["V0"], abs_tol=abs_tol, rel_tol=rel_tol)
        if compare_total and "V" in scalars:
            py_value = evaluate_contract_potential(contract, phi, temperature)
            _append_scalar_error(errors, f"point {index} V", py_value, scalars["V"], abs_tol=abs_tol, rel_tol=rel_tol)
        if "raddof" in scalars:
            _append_scalar_error(
                errors,
                f"point {index} radiation d.o.f.",
                _radiation_dof(contract),
                scalars["raddof"],
                abs_tol=PHASETRACER_LOOP_SMOKE_ABS_TOL,
                rel_tol=PHASETRACER_LOOP_SMOKE_REL_TOL,
            )
        expected_vectors = {
            "scalar_masses": _evaluate_masses(contract, phi, "scalar"),
            "vector_masses": _evaluate_masses(contract, phi, "vector"),
            "fermion_masses": _evaluate_fermion_masses(contract, phi),
            "scalar_debye": _evaluate_debye_masses(contract, phi, temperature, "scalar"),
            "vector_debye": _evaluate_debye_masses(contract, phi, temperature, "vector"),
            "scalar_dofs": _mass_dofs(contract, "scalar"),
            "vector_dofs": _mass_dofs(contract, "vector"),
            "fermion_dofs": _fermion_dofs(contract),
        }
        for name, expected in expected_vectors.items():
            if name in vectors:
                _append_vector_error(
                    errors,
                    f"point {index} {name}",
                    expected,
                    vectors[name],
                    abs_tol=PHASETRACER_LOOP_SMOKE_ABS_TOL,
                    rel_tol=PHASETRACER_LOOP_SMOKE_REL_TOL,
                )
    unexpected = sorted(set(parsed) - set(range(len(points))))
    if unexpected:
        errors.append(f"unexpected smoke point indices: {unexpected}")
    if errors:
        raise CompileBlocked("PhaseTracer numerical smoke check failed: " + "; ".join(errors[:8]))
    total_note = "total skipped for backend-native standard thermal integrals" if not compare_total else "total checked"
    return f"numerical check ok ({len(points)} detailed points, {total_note}, abs_tol={abs_tol:g}, rel_tol={rel_tol:g})"


def _append_scalar_error(
    errors: list[str],
    label: str,
    expected: float,
    actual: float,
    *,
    abs_tol: float,
    rel_tol: float,
) -> None:
    abs_error = abs(actual - expected)
    denominator = max(abs(expected), 1e-300)
    rel_error = abs_error / denominator
    if not (abs_error < abs_tol or rel_error < rel_tol):
        errors.append(
            f"{label} mismatch: python={expected:.17g}, cpp={actual:.17g}, "
            f"abs_err={abs_error:.3g}, rel_err={rel_error:.3g}"
        )


def _append_vector_error(
    errors: list[str],
    label: str,
    expected: list[float],
    actual: list[float],
    *,
    abs_tol: float,
    rel_tol: float,
) -> None:
    if len(expected) != len(actual):
        errors.append(f"{label} length mismatch: python={len(expected)}, cpp={len(actual)}")
        return
    for index, (py_value, cpp_value) in enumerate(zip(expected, actual)):
        before = len(errors)
        _append_scalar_error(errors, f"{label}[{index}]", py_value, cpp_value, abs_tol=abs_tol, rel_tol=rel_tol)
        if len(errors) > before:
            return


def _render_phasetracer_project(
    contract: dict[str, Any],
    *,
    template_sha256: str,
    contract_sha256: str,
) -> dict[str, str]:
    metadata = _metadata(contract, template_sha256=template_sha256, contract_sha256=contract_sha256)
    header_filename = _phasetracer_header_filename(contract)
    return {
        header_filename: _render_potential_header(contract, metadata),
        "run_model.cpp": _render_model_runner(contract, header_filename=header_filename),
        "CMakeLists.txt": _render_cmake(),
        "metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
    }


def _phasetracer_header_filename(contract: dict[str, Any]) -> str:
    from .artifact_layout import model_artifact_slug

    return f"{model_artifact_slug(contract)}_potential.hpp"


def _render_potential_header(contract: dict[str, Any], metadata: dict[str, Any]) -> str:
    fields = _list(contract.get("fields"))
    params = _dict(contract.get("parameters"))
    public_inputs = _list(params.get("public_inputs"))
    constants = _list(params.get("constants"))
    derived = _list(params.get("derived"))
    class_name = "GeneratedPotential"
    try:
        methods = [
            _render_constructor(class_name, public_inputs, contract),
            f"size_t get_n_scalars() const override {{ return {len(fields)}; }}",
            _render_v0_method(contract, fields, public_inputs, constants, derived),
            _render_v_method(contract, fields, public_inputs, constants, derived),
            _render_component_methods(contract, fields, public_inputs, constants, derived),
            _render_mass_methods(contract, fields, public_inputs, constants, derived),
            _render_debye_methods(contract, fields, public_inputs, constants, derived),
            _render_dof_methods(contract),
            _render_counterterm_method(contract, fields, public_inputs, constants, derived),
            _render_v1_override(contract),
            _render_raddof_method(contract),
            _render_zero_t_vacuum_method(contract, fields, public_inputs, constants, derived),
            _render_symmetry_methods(contract, fields),
        ]
    except PhaseTracerExpressionError as exc:
        raise CompileBlocked(f"PhaseTracer could not render C++ source: {exc}") from exc
    private_members = [f"double {row['name']}_;" for row in public_inputs]
    private_members.extend(member.strip() for member in _render_ct_private_members(contract))
    private_body = textwrap.indent("\n".join(private_members), "  ") if private_members else "  // No private state."
    body = "\n\n".join(method.strip("\n") for method in methods if method.strip())
    method_body = textwrap.indent(body, "  ")
    source = "\n".join(
        [
            "#pragma once",
            "",
            "#include <algorithm>",
            "#include <cmath>",
            "#include <functional>",
            "#include <iomanip>",
            "#include <stdexcept>",
            "#include <string>",
            "#include <vector>",
            "",
            "#include <eigen3/Eigen/Core>",
            "#include <eigen3/Eigen/Eigenvalues>",
            "#include <eigen3/Eigen/LU>",
            '#include "effectivepotential/one_loop_potential.hpp"',
            "",
            "namespace PTagentPhaseTracer {",
            "",
            "// Generated from PTagent reviewed contract.",
            f'// PTAGENT_TEMPLATE_SHA256={metadata["template_sha256"]}',
            f'// PTAGENT_CONTRACT_SHA256={metadata["contract_sha256"]}',
            f'// FIELD_ORDER={json.dumps(metadata["field_order"])}',
            f"class {class_name} : public EffectivePotential::OneLoopPotential {{",
            "public:",
            method_body,
            "",
            "private:",
            private_body,
            "};",
            "",
            "} // namespace PTagentPhaseTracer",
            "",
        ]
    )
    return _cleanup_generated_cpp_source(source)


def _render_constructor(class_name: str, public_inputs: list[dict[str, Any]], contract: dict[str, Any]) -> str:
    constructor_args = ", ".join(
        f"double {row['name']} = {_float_literal(_param_default(row))}" for row in public_inputs
    )
    member_initializers = ", ".join(f"{row['name']}_({row['name']})" for row in public_inputs)
    constructor = f"{class_name}({constructor_args})"
    if member_initializers:
        constructor += f" : {member_initializers}"
    method = _phasetracer_daisy_method(contract)
    lines = [constructor + " {"]
    lines.append(f"  set_daisy_method(EffectivePotential::DaisyMethod::{method});")
    if _counterterm_mode(contract) == "explicit_linear_system":
        lines.append("  solve_counterterms();")
    lines.append("}")
    return "\n".join(lines)


def _render_v0_method(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    expr = str(_dict(_dict(contract.get("potential")).get("V0")).get("python", "0.0"))
    lines = [
        "double V0(Eigen::VectorXd phi) const override {",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        *render_cpp_assignment_block(expr, "value", indent="  ", prefer_square_aliases=True),
        "  return value;",
        "}",
    ]
    return "\n".join(lines)


def _render_v_method(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    del public_inputs, constants, derived
    lines = [
        "double V(Eigen::VectorXd phi, double T) const override {",
        _render_field_size_check(fields, indent="  "),
        "  double total = V0(phi);",
    ]
    for expression in (
        _v1_component_expression(contract),
        "counter_term(phi, T)",
        _v1t_component_expression(contract),
        _daisy_component_expression(contract),
    ):
        if expression:
            lines.append(f"  total += {expression};")
    lines.extend(["  return total;", "}"])
    return "\n".join(lines)


def _render_component_methods(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    methods = []
    if _needs_v1_component_helper(contract):
        methods.append(_render_v1_component(contract, fields, public_inputs, constants, derived))
    if _needs_v1t_component_helper(contract):
        methods.append(_render_v1t_component(contract, fields, public_inputs, constants, derived))
    if _needs_daisy_component_helper(contract):
        methods.append(_render_daisy_component(contract, fields, public_inputs, constants, derived))
        methods.append(_render_custom_daisy_helpers(contract))
    return "\n\n".join(method for method in methods if method.strip())


def _v1_component_expression(contract: dict[str, Any], *, receiver: str = "") -> str:
    zero = _dict(_dict(contract.get("loops")).get("zero_temperature"))
    mode = str(zero.get("mode", "none"))
    if mode == "none":
        return ""
    if _needs_v1_component_helper(contract):
        return f"{receiver}ptagent_V1_component(phi, T)"
    if mode in {"standard_CW_V1", "paper_os_like_V1"}:
        return (
            f"{receiver}V1({receiver}get_scalar_masses_sq(phi, {receiver}get_xi()), "
            f"{receiver}get_fermion_masses_sq(phi), {receiver}get_vector_masses_sq(phi), "
            f"{receiver}get_ghost_masses_sq(phi, {receiver}get_xi()))"
        )
    raise CompileBlocked(f"PhaseTracer does not support zero-temperature loop mode {mode!r}.")


def _v1t_component_expression(contract: dict[str, Any], *, receiver: str = "") -> str:
    thermal = _dict(_dict(contract.get("loops")).get("thermal"))
    mode = str(thermal.get("mode", "none"))
    if mode == "none":
        return ""
    if _needs_v1t_component_helper(contract):
        return f"{receiver}ptagent_V1T_component(phi, T)"
    if mode == "standard_thermal_integrals":
        return (
            f"{receiver}V1T({receiver}get_scalar_masses_sq(phi, {receiver}get_xi()), "
            f"{receiver}get_fermion_masses_sq(phi), {receiver}get_vector_masses_sq(phi), "
            f"{receiver}get_ghost_masses_sq(phi, {receiver}get_xi()), T)"
        )
    raise CompileBlocked(f"PhaseTracer does not support thermal loop mode {mode!r}.")


def _daisy_component_expression(contract: dict[str, Any], *, receiver: str = "") -> str:
    daisy = _dict(_dict(contract.get("loops")).get("daisy"))
    mode = str(daisy.get("mode", "none"))
    if mode == "none":
        return ""
    if _uses_default_arnold_espinosa_daisy(contract):
        return (
            f"{receiver}daisy({receiver}get_scalar_masses_sq(phi, {receiver}get_xi()), "
            f"{receiver}get_scalar_debye_sq(phi, {receiver}get_xi(), T), "
            f"{receiver}get_vector_masses_sq(phi), {receiver}get_vector_debye_sq(phi, T), T)"
        )
    if _needs_daisy_component_helper(contract):
        return f"{receiver}ptagent_daisy_component(phi, T)"
    raise CompileBlocked(f"PhaseTracer does not support Daisy loop mode {mode!r}.")


def _needs_v1_component_helper(contract: dict[str, Any]) -> bool:
    zero = _dict(_dict(contract.get("loops")).get("zero_temperature"))
    mode = str(zero.get("mode", "none"))
    return mode == "custom_expr" or (mode in {"standard_CW_V1", "paper_os_like_V1"} and _phasetracer_daisy_method(contract) == "Parwani")


def _needs_v1t_component_helper(contract: dict[str, Any]) -> bool:
    thermal = _dict(_dict(contract.get("loops")).get("thermal"))
    mode = str(thermal.get("mode", "none"))
    return mode == "custom_expr" or (mode == "standard_thermal_integrals" and _phasetracer_daisy_method(contract) == "Parwani")


def _needs_daisy_component_helper(contract: dict[str, Any]) -> bool:
    daisy = _dict(_dict(contract.get("loops")).get("daisy"))
    mode = str(daisy.get("mode", "none"))
    return mode == "custom_expr" and not _uses_default_arnold_espinosa_daisy(contract)


def _render_v1_component(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    zero = _dict(_dict(contract.get("loops")).get("zero_temperature"))
    mode = str(zero.get("mode", "none"))
    lines = ["double ptagent_V1_component(Eigen::VectorXd phi, double T) const {"]
    if mode == "none":
        lines.append("  return 0.0;")
    elif mode == "custom_expr":
        lines.extend(_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "))
        lines.extend(render_cpp_assignment_block(str(zero.get("custom_expr", "0.0")), "value", indent="  ", prefer_square_aliases=True))
        lines.append("  return value;")
    elif mode in {"standard_CW_V1", "paper_os_like_V1"}:
        if _phasetracer_daisy_method(contract) == "Parwani":
            lines.extend(
                [
                    "  const auto fermion_masses_sq = get_fermion_masses_sq(phi);",
                    "  const auto ghost_masses_sq = get_ghost_masses_sq(phi, get_xi());",
                    "  if (T > 0.0) {",
                    "    return V1(get_scalar_debye_sq(phi, get_xi(), T), fermion_masses_sq, get_vector_debye_sq(phi, T), ghost_masses_sq);",
                    "  }",
                    "  return V1(get_scalar_masses_sq(phi, get_xi()), fermion_masses_sq, get_vector_masses_sq(phi), ghost_masses_sq);",
                ]
            )
        else:
            lines.extend(
                [
                    "  return V1(get_scalar_masses_sq(phi, get_xi()), get_fermion_masses_sq(phi), get_vector_masses_sq(phi), get_ghost_masses_sq(phi, get_xi()));",
                ]
            )
    else:
        raise CompileBlocked(f"PhaseTracer does not support zero-temperature loop mode {mode!r}.")
    lines.append("}")
    return "\n".join(lines)


def _render_v1t_component(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    thermal = _dict(_dict(contract.get("loops")).get("thermal"))
    mode = str(thermal.get("mode", "none"))
    lines = ["double ptagent_V1T_component(Eigen::VectorXd phi, double T) const {"]
    if mode == "none":
        lines.append("  return 0.0;")
    elif mode == "custom_expr":
        lines.extend(_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "))
        lines.extend(render_cpp_assignment_block(str(thermal.get("custom_expr", "0.0")), "value", indent="  ", prefer_square_aliases=True))
        lines.append("  return value;")
    elif mode == "standard_thermal_integrals":
        if _phasetracer_daisy_method(contract) == "Parwani":
            lines.extend(
                [
                    "  return V1T(get_scalar_debye_sq(phi, get_xi(), T), get_fermion_masses_sq(phi), get_vector_debye_sq(phi, T), get_ghost_masses_sq(phi, get_xi()), T);",
                ]
            )
        else:
            lines.extend(
                [
                    "  return V1T(get_scalar_masses_sq(phi, get_xi()), get_fermion_masses_sq(phi), get_vector_masses_sq(phi), get_ghost_masses_sq(phi, get_xi()), T);",
                ]
            )
    else:
        raise CompileBlocked(f"PhaseTracer does not support thermal loop mode {mode!r}.")
    lines.append("}")
    return "\n".join(lines)


def _render_daisy_component(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    daisy = _dict(_dict(contract.get("loops")).get("daisy"))
    mode = str(daisy.get("mode", "none"))
    lines = ["double ptagent_daisy_component(Eigen::VectorXd phi, double T) const {"]
    if mode == "none":
        lines.append("  return 0.0;")
    elif _uses_default_arnold_espinosa_daisy(contract):
        lines.append("  return daisy(get_scalar_masses_sq(phi, get_xi()), get_scalar_debye_sq(phi, get_xi(), T), get_vector_masses_sq(phi), get_vector_debye_sq(phi, T), T);")
    elif mode == "custom_expr":
        lines.extend(_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "))
        lines.extend(render_cpp_assignment_block(str(daisy.get("custom_expr", "0.0")), "value", indent="  ", prefer_square_aliases=True))
        lines.append("  return value;")
    else:
        raise CompileBlocked(f"PhaseTracer does not support Daisy loop mode {mode!r}.")
    lines.append("}")
    return "\n".join(lines)


def _render_custom_daisy_helpers(contract: dict[str, Any]) -> str:
    implementation = _dict(_dict(contract.get("implementation")).get("daisy"))
    policy = _normalize_cubic_policy(str(implementation.get("cubic_power_policy", "")))
    if policy in {"", "not_applicable", "positive_part"}:
        return ""
    if policy == "signed_abs":
        return "\n".join(
            [
                "double ptagent_daisy_cubic(double mass_sq) const {",
                "  return (mass_sq < 0.0 ? -1.0 : 1.0) * std::pow(std::abs(mass_sq), 1.5);",
                "}",
            ]
        )
    if policy == "regulated_abs":
        return "\n".join(
            [
                "double ptagent_daisy_cubic(double mass_sq) const {",
                "  return std::pow(std::abs(mass_sq), 1.5);",
                "}",
            ]
        )
    return ""


def _render_mass_methods(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    return "\n\n".join(
        [
            _render_boson_mass_method(contract, "scalar", fields, public_inputs, constants, derived),
            _render_boson_mass_method(contract, "vector", fields, public_inputs, constants, derived),
            _render_fermion_mass_method(contract, fields, public_inputs, constants, derived),
        ]
    )


def _render_boson_mass_method(
    contract: dict[str, Any],
    kind: str,
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    signature = "std::vector<double> get_scalar_masses_sq(Eigen::VectorXd phi, double xi) const override" if kind == "scalar" else "std::vector<double> get_vector_masses_sq(Eigen::VectorXd phi) const override"
    specs = _mass_specs(contract, kind)
    lines = [
        f"{signature} {{",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        "  std::vector<double> masses;",
    ]
    if _mass_specs_use_temperature(specs):
        lines.append("  const double T = 0.0;")
    if kind == "scalar":
        lines.append("  (void)xi;")
    lines.extend(_render_mass_spec_pushes(specs, indent="  "))
    lines.extend(["  return masses;", "}"])
    return "\n".join(lines)


def _render_fermion_mass_method(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    specs = _fermion_specs(contract)
    lines = [
        "std::vector<double> get_fermion_masses_sq(Eigen::VectorXd phi) const override {",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        "  std::vector<double> masses;",
    ]
    lines.extend(_render_mass_spec_pushes(specs, indent="  "))
    lines.extend(["  return masses;", "}"])
    return "\n".join(lines)


def _render_debye_methods(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    return "\n\n".join(
        [
            _render_debye_method(contract, "scalar", fields, public_inputs, constants, derived),
            _render_debye_method(contract, "vector", fields, public_inputs, constants, derived),
        ]
    )


def _render_debye_method(
    contract: dict[str, Any],
    kind: str,
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    signature = "std::vector<double> get_scalar_debye_sq(Eigen::VectorXd phi, double xi, double T) const override" if kind == "scalar" else "std::vector<double> get_vector_debye_sq(Eigen::VectorXd phi, double T) const override"
    specs = _aligned_thermal_mass_specs(contract, kind, required=_requires_debye_alignment(contract))
    lines = [
        f"{signature} {{",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "),
        "  std::vector<double> masses;",
    ]
    if kind == "scalar":
        lines.append("  (void)xi;")
    lines.extend(_render_mass_spec_pushes(specs, indent="  "))
    lines.extend(["  return masses;", "}"])
    return "\n".join(lines)


def _render_dof_methods(contract: dict[str, Any]) -> str:
    scalar_dofs = _mass_dofs(contract, "scalar")
    vector_dofs = _mass_dofs(contract, "vector")
    fermion_dofs = _fermion_dofs(contract)
    zero_mode = str(_dict(_dict(contract.get("loops")).get("zero_temperature")).get("mode", "none"))
    lines = [
        f"std::vector<double> get_scalar_dofs() const override {{ return {_cpp_vector_literal(scalar_dofs)}; }}",
        f"std::vector<double> get_vector_dofs() const override {{ return {_cpp_vector_literal(vector_dofs)}; }}",
        f"std::vector<double> get_fermion_dofs() const override {{ return {_cpp_vector_literal(fermion_dofs)}; }}",
    ]
    if zero_mode == "standard_CW_V1":
        scalar_c = _mass_c_values(contract, "scalar")
        vector_c = _mass_c_values(contract, "vector")
        lines.append(f"std::vector<double> ptagent_scalar_c() const {{ return {_cpp_vector_literal(scalar_c)}; }}")
        lines.append(f"std::vector<double> ptagent_vector_c() const {{ return {_cpp_vector_literal(vector_c)}; }}")
    if zero_mode == "standard_CW_V1" or _counterterm_mode(contract) == "explicit_linear_system":
        lines.append(f"double ptagent_renorm_scale_sq() const {{ return {_float_literal(_renorm_scale_sq(contract))}; }}")
    return "\n\n".join(lines)


def _render_counterterm_method(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    counterterm = _counterterm_mode(contract)
    if counterterm == "explicit_linear_system":
        return _render_explicit_counterterm_methods(contract, fields, public_inputs, constants, derived)
    lines = ["double counter_term(Eigen::VectorXd phi, double T) const override {"]
    if counterterm == "custom_expr":
        expr = str(_dict(_dict(contract.get("potential")).get("V_CT")).get("python", "0.0"))
        lines.extend(_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "))
        lines.extend(render_cpp_assignment_block(expr, "value", indent="  ", prefer_square_aliases=True))
        lines.append("  return value;")
    else:
        lines.append("  (void)phi;")
        lines.append("  (void)T;")
        lines.append("  return 0.0;")
    lines.append("}")
    return "\n".join(lines)


def _render_explicit_counterterm_methods(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    spec = _explicit_counterterm_spec(contract)
    basis = spec["basis"]
    conditions = spec["conditions"]
    member_names = spec["member_names"]
    axes = spec["axes"]
    points = spec["points"]
    handlers = spec["handlers"]
    field_count = len(fields)

    counter_lines = [
        "double counter_term(Eigen::VectorXd phi, double T) const override {",
        "  const auto basis = ptagent_ct_basis_values(phi, T);",
        f"  if (basis.size() != {len(basis)}) throw std::runtime_error(\"CT basis size mismatch\");",
        "  double value = 0.0;",
    ]
    for index, member_name in enumerate(member_names):
        counter_lines.append(f"  value += {member_name} * basis[{index}];")
    counter_lines.extend(["  return value;", "}"])

    basis_lines = [
        "std::vector<double> ptagent_ct_basis_values(Eigen::VectorXd phi, double T) const {",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=True, indent="  "),
        "  std::vector<double> basis;",
    ]
    for index, row in enumerate(basis):
        var = f"ct_basis_{index + 1}"
        basis_lines.append("  {")
        basis_lines.extend(render_cpp_assignment_block(str(row.get("operator_expr", "0.0")), var, indent="    ", prefer_square_aliases=True))
        basis_lines.append(f"    basis.push_back({var});")
        basis_lines.append("  }")
    basis_lines.extend(["  return basis;", "}"])

    point_methods: list[str] = []
    for index, components in enumerate(points):
        lines = [
            f"Eigen::VectorXd ptagent_ct_point_{index + 1}() const {{",
            f"  Eigen::VectorXd point({field_count});",
            *_render_parameter_bindings(public_inputs, constants, derived, indent="  "),
        ]
        for component_index, component in enumerate(components):
            lines.append("  {")
            lines.extend(_render_cpp_assignment_to_existing(str(component), f"point[{component_index}]", indent="    "))
            lines.append("  }")
        lines.extend(["  return point;", "}"])
        point_methods.append("\n".join(lines))

    target_methods: list[str] = []
    for index, row in enumerate(conditions):
        lines = [
            f"double ptagent_ct_target_{index + 1}() const {{",
            f"  Eigen::VectorXd phi = ptagent_ct_point_{index + 1}();",
            *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        ]
        lines.extend(render_cpp_assignment_block(str(row.get("target_expr", "0.0")), "value", indent="  ", prefer_square_aliases=True))
        lines.extend(["  return value;", "}"])
        target_methods.append("\n".join(lines))

    axis_rows = ", ".join(_cpp_int_vector_literal(axis) for axis in axes)
    point_rows = ", ".join(f"ptagent_ct_point_{index + 1}()" for index in range(len(points)))
    target_rows = ", ".join(f"ptagent_ct_target_{index + 1}()" for index in range(len(conditions)))
    handler_rows = ", ".join(json.dumps(handler) for handler in handlers)
    coefficient_assignments = [f"  {member_name} = coeffs[{index}];" for index, member_name in enumerate(member_names)]
    solve_lines = [
        "void solve_counterterms() {",
        f"  constexpr int kCount = {len(basis)};",
        f"  std::vector<Eigen::VectorXd> points = {{{point_rows}}};",
        f"  std::vector<std::vector<int>> axes = {{{axis_rows}}};",
        f"  std::vector<std::string> handlers = {{{handler_rows}}};",
        f"  std::vector<double> targets = {{{target_rows}}};",
        "  if (points.size() != kCount || axes.size() != kCount || targets.size() != kCount) throw std::runtime_error(\"CT linear system shape mismatch\");",
        "  Eigen::MatrixXd A(kCount, kCount);",
        "  Eigen::VectorXd b(kCount);",
        "  auto basis_func = [this](Eigen::VectorXd x) { return ptagent_ct_basis_values(x, 0.0); };",
        "  for (int row = 0; row < kCount; ++row) {",
        "    const auto values = ptagent_ct_apply_operator_vector(basis_func, points[row], axes[row]);",
        "    if (values.size() != kCount) throw std::runtime_error(\"CT operator returned wrong basis length\");",
        "    for (int col = 0; col < kCount; ++col) A(row, col) = values[static_cast<size_t>(col)];",
        "    b[row] = targets[static_cast<size_t>(row)] - ptagent_ct_source_derivative(points[row], axes[row], handlers[static_cast<size_t>(row)]);",
        "  }",
        "  Eigen::FullPivLU<Eigen::MatrixXd> lu(A);",
        "  if (lu.rank() < kCount) throw std::runtime_error(\"CT linear system is singular; review the counterterm basis and conditions\");",
        "  const Eigen::VectorXd coeffs = lu.solve(b);",
        *coefficient_assignments,
        "}",
    ]

    return "\n\n".join(
        [
            "\n".join(counter_lines),
            "\n".join(basis_lines),
            *point_methods,
            *target_methods,
            _render_explicit_counterterm_goldstone_methods(contract, fields, public_inputs, constants, derived),
            _render_explicit_counterterm_operator_methods(),
            "\n".join(solve_lines),
        ]
    )


def _render_explicit_counterterm_goldstone_methods(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    goldstone = _dict(_dict(contract.get("implementation")).get("goldstone"))
    species = _list(goldstone.get("species"))
    dofs = [float(_dict(row).get("dof", 1.0)) for row in species]
    replacements = [
        str(_dict(row).get("replacement_expr") or goldstone.get("replacement_formula") or "0.0")
        for row in species
    ]

    mass_lines = [
        "std::vector<double> ptagent_ct_goldstone_masses_sq(Eigen::VectorXd phi) const {",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        "  std::vector<double> masses;",
    ]
    for index, row in enumerate(species):
        var = f"ct_goldstone_m2_{index + 1}"
        mass_lines.append("  {")
        mass_lines.extend(render_cpp_assignment_block(str(_dict(row).get("mass_sq", "0.0")), var, indent="    ", prefer_square_aliases=True))
        mass_lines.append(f"    masses.push_back({var});")
        mass_lines.append("  }")
    mass_lines.extend(["  return masses;", "}"])

    replacement_lines = [
        "std::vector<double> ptagent_ct_goldstone_replacements_sq(Eigen::VectorXd phi) const {",
        *_render_method_prelude(fields, public_inputs, constants, derived, include_temperature=False, indent="  "),
        "  std::vector<double> replacements;",
    ]
    for index, expr in enumerate(replacements):
        var = f"ct_goldstone_replacement_{index + 1}"
        replacement_lines.append("  {")
        replacement_lines.extend(render_cpp_assignment_block(expr, var, indent="    ", prefer_square_aliases=True))
        replacement_lines.append(f"    replacements.push_back({var});")
        replacement_lines.append("  }")
    replacement_lines.extend(["  return replacements;", "}"])

    return "\n\n".join(
        [
            "\n".join(mass_lines),
            "\n".join(replacement_lines),
            f"std::vector<double> ptagent_ct_goldstone_dofs() const {{ return {_cpp_vector_literal(dofs)}; }}",
            textwrap.dedent(
                """
                double ptagent_ct_vcw(Eigen::VectorXd phi) const {
                  return ptagent_V1_component(phi, 0.0);
                }

                double ptagent_ct_goldstone_cw_for_ct(Eigen::VectorXd phi) const {
                  const auto masses_sq = ptagent_ct_goldstone_masses_sq(phi);
                  const auto dofs = ptagent_ct_goldstone_dofs();
                  if (masses_sq.size() != dofs.size()) throw std::runtime_error("Goldstone CT masses and d.o.f. do not match");
                  const double Q_sq = ptagent_renorm_scale_sq();
                  double value = 0.0;
                  for (size_t i = 0; i < masses_sq.size(); ++i) {
                    const double m2 = masses_sq[i];
                    value += dofs[i] * m2 * m2 * (std::log(std::abs(m2 / Q_sq) + 1e-100) - 1.5);
                  }
                  return value / (64.0 * 3.141592653589793238462643383279502884 * 3.141592653589793238462643383279502884);
                }

                double ptagent_ct_non_goldstone_vcw(Eigen::VectorXd phi) const {
                  return ptagent_ct_vcw(phi) - ptagent_ct_goldstone_cw_for_ct(phi);
                }

                Eigen::MatrixXd ptagent_ct_goldstone_mass_derivatives(Eigen::VectorXd point) const {
                  const auto center = ptagent_ct_goldstone_masses_sq(point);
                  Eigen::MatrixXd deriv(center.size(), static_cast<Eigen::Index>(get_n_scalars()));
                  if (center.empty()) return deriv;
                  const double eps = 0.001;
                  for (Eigen::Index axis = 0; axis < static_cast<Eigen::Index>(get_n_scalars()); ++axis) {
                    Eigen::VectorXd step = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
                    step[axis] = eps;
                    const auto plus = ptagent_ct_goldstone_masses_sq(point + step);
                    const auto minus = ptagent_ct_goldstone_masses_sq(point - step);
                    if (plus.size() != center.size() || minus.size() != center.size()) throw std::runtime_error("Goldstone CT derivative size mismatch");
                    for (size_t i = 0; i < center.size(); ++i) deriv(static_cast<Eigen::Index>(i), axis) = (plus[i] - minus[i]) / (2.0 * eps);
                  }
                  return deriv;
                }

                Eigen::MatrixXd ptagent_ct_regulated_goldstone_hessian(Eigen::VectorXd point) const {
                  Eigen::MatrixXd hessian = Eigen::MatrixXd::Zero(static_cast<Eigen::Index>(get_n_scalars()), static_cast<Eigen::Index>(get_n_scalars()));
                  const auto masses_sq = ptagent_ct_goldstone_masses_sq(point);
                  if (masses_sq.empty()) return hessian;
                  const auto replacements = ptagent_ct_goldstone_replacements_sq(point);
                  const auto dofs = ptagent_ct_goldstone_dofs();
                  if (masses_sq.size() != replacements.size() || masses_sq.size() != dofs.size()) throw std::runtime_error("Goldstone CT replacement size mismatch");
                  const Eigen::MatrixXd dm = ptagent_ct_goldstone_mass_derivatives(point);
                  const double Q_sq = ptagent_renorm_scale_sq();
                  for (size_t mode = 0; mode < masses_sq.size(); ++mode) {
                    const double weight = dofs[mode] * std::log(std::abs(replacements[mode] / Q_sq) + 1e-100) / (32.0 * 3.141592653589793238462643383279502884 * 3.141592653589793238462643383279502884);
                    for (Eigen::Index i = 0; i < hessian.rows(); ++i) {
                      for (Eigen::Index j = 0; j < hessian.cols(); ++j) {
                        hessian(i, j) += weight * dm(static_cast<Eigen::Index>(mode), i) * dm(static_cast<Eigen::Index>(mode), j);
                      }
                    }
                  }
                  return hessian;
                }
                """
            ).strip(),
        ]
    )


def _render_explicit_counterterm_operator_methods() -> str:
    return textwrap.dedent(
        """
        double ptagent_ct_apply_operator_scalar(const std::function<double(Eigen::VectorXd)>& func,
                                                Eigen::VectorXd point,
                                                const std::vector<int>& axes) const {
          const double eps = 0.001;
          if (axes.size() == 1) {
            Eigen::VectorXd step = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step[axes[0]] = eps;
            return (func(point + step) - func(point - step)) / (2.0 * eps);
          }
          if (axes.size() == 2 && axes[0] == axes[1]) {
            Eigen::VectorXd step = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step[axes[0]] = eps;
            return (func(point + step) - 2.0 * func(point) + func(point - step)) / (eps * eps);
          }
          if (axes.size() == 2) {
            Eigen::VectorXd step_a = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            Eigen::VectorXd step_b = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step_a[axes[0]] = eps;
            step_b[axes[1]] = eps;
            return (func(point + step_a + step_b) - func(point + step_a - step_b) - func(point - step_a + step_b) + func(point - step_a - step_b)) / (4.0 * eps * eps);
          }
          throw std::runtime_error("Unsupported CT operator order");
        }

        std::vector<double> ptagent_ct_apply_operator_vector(const std::function<std::vector<double>(Eigen::VectorXd)>& func,
                                                             Eigen::VectorXd point,
                                                             const std::vector<int>& axes) const {
          const double eps = 0.001;
          if (axes.size() == 1) {
            Eigen::VectorXd step = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step[axes[0]] = eps;
            const auto plus = func(point + step);
            const auto minus = func(point - step);
            if (plus.size() != minus.size()) throw std::runtime_error("CT vector derivative size mismatch");
            std::vector<double> result(plus.size());
            for (size_t i = 0; i < plus.size(); ++i) result[i] = (plus[i] - minus[i]) / (2.0 * eps);
            return result;
          }
          if (axes.size() == 2 && axes[0] == axes[1]) {
            Eigen::VectorXd step = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step[axes[0]] = eps;
            const auto plus = func(point + step);
            const auto center = func(point);
            const auto minus = func(point - step);
            if (plus.size() != center.size() || plus.size() != minus.size()) throw std::runtime_error("CT vector Hessian size mismatch");
            std::vector<double> result(plus.size());
            for (size_t i = 0; i < plus.size(); ++i) result[i] = (plus[i] - 2.0 * center[i] + minus[i]) / (eps * eps);
            return result;
          }
          if (axes.size() == 2) {
            Eigen::VectorXd step_a = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            Eigen::VectorXd step_b = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(get_n_scalars()));
            step_a[axes[0]] = eps;
            step_b[axes[1]] = eps;
            const auto pp = func(point + step_a + step_b);
            const auto pm = func(point + step_a - step_b);
            const auto mp = func(point - step_a + step_b);
            const auto mm = func(point - step_a - step_b);
            if (pp.size() != pm.size() || pp.size() != mp.size() || pp.size() != mm.size()) throw std::runtime_error("CT vector mixed Hessian size mismatch");
            std::vector<double> result(pp.size());
            for (size_t i = 0; i < pp.size(); ++i) result[i] = (pp[i] - pm[i] - mp[i] + mm[i]) / (4.0 * eps * eps);
            return result;
          }
          throw std::runtime_error("Unsupported CT vector operator order");
        }

        double ptagent_ct_source_derivative(Eigen::VectorXd point,
                                            const std::vector<int>& axes,
                                            const std::string& goldstone_handling) const {
          if (goldstone_handling == "regulated_replacement") {
            double source = ptagent_ct_apply_operator_scalar(
                [this](Eigen::VectorXd x) { return ptagent_ct_non_goldstone_vcw(x); },
                point,
                axes);
            if (axes.size() == 2) {
              const Eigen::MatrixXd hessian = ptagent_ct_regulated_goldstone_hessian(point);
              source += hessian(axes[0], axes[1]);
            }
            return source;
          }
          return ptagent_ct_apply_operator_scalar(
              [this](Eigen::VectorXd x) { return ptagent_ct_vcw(x); },
              point,
              axes);
        }
        """
    ).strip()


def _render_v1_override(contract: dict[str, Any]) -> str:
    zero_mode = str(_dict(_dict(contract.get("loops")).get("zero_temperature")).get("mode", "none"))
    if zero_mode == "paper_os_like_V1":
        return _render_os_like_v1_override()
    if zero_mode == "standard_CW_V1":
        return _render_standard_v1_override()
    return ""


def _render_standard_v1_override() -> str:
    return textwrap.dedent(
        """
        double V1(std::vector<double> scalar_masses_sq,
                  std::vector<double> fermion_masses_sq,
                  std::vector<double> vector_masses_sq,
                  std::vector<double> ghost_masses_sq) const override {
          (void)ghost_masses_sq;
          double correction = 0.0;
          correction += ptagent_standard_v1_sum(scalar_masses_sq, get_scalar_dofs(), ptagent_scalar_c(), 1.0);
          correction += ptagent_standard_v1_sum(vector_masses_sq, get_vector_dofs(), ptagent_vector_c(), 1.0);
          correction += ptagent_standard_v1_sum(fermion_masses_sq, get_fermion_dofs(), std::vector<double>(fermion_masses_sq.size(), 1.5), -1.0);
          return correction / (64.0 * 3.141592653589793238462643383279502884 * 3.141592653589793238462643383279502884);
        }

        double ptagent_standard_v1_sum(const std::vector<double>& masses_sq,
                                       const std::vector<double>& dofs,
                                       const std::vector<double>& c_values,
                                       double sign) const {
          if (masses_sq.size() != dofs.size() || masses_sq.size() != c_values.size()) {
            throw std::runtime_error("V1 masses, d.o.f., and Coleman-Weinberg constants do not match");
          }
          const double Q_sq = ptagent_renorm_scale_sq();
          double correction = 0.0;
          for (size_t i = 0; i < masses_sq.size(); ++i) {
            const double m2 = masses_sq[i];
            const double x = m2 / Q_sq;
            correction += sign * dofs[i] * m2 * (Q_sq * EffectivePotential::xlogx(x) - c_values[i] * m2);
          }
          return correction;
        }

        """
    ).strip()


def _render_os_like_v1_override() -> str:
    return textwrap.dedent(
        """
        double V1(std::vector<double> scalar_masses_sq,
                  std::vector<double> fermion_masses_sq,
                  std::vector<double> vector_masses_sq,
                  std::vector<double> ghost_masses_sq) const override {
          (void)ghost_masses_sq;
          const Eigen::VectorXd vacuum = ptagent_zeroT_vacuum_point();
          const auto scalar_vacuum = get_scalar_masses_sq(vacuum, get_xi());
          const auto vector_vacuum = get_vector_masses_sq(vacuum);
          const auto fermion_vacuum = get_fermion_masses_sq(vacuum);
          double correction = 0.0;
          correction += ptagent_os_like_sum(scalar_masses_sq, get_scalar_dofs(), scalar_vacuum);
          correction += ptagent_os_like_sum(vector_masses_sq, get_vector_dofs(), vector_vacuum);
          correction -= ptagent_os_like_sum(fermion_masses_sq, get_fermion_dofs(), fermion_vacuum);
          return correction / (64.0 * 3.141592653589793238462643383279502884 * 3.141592653589793238462643383279502884);
        }

        double ptagent_os_like_sum(const std::vector<double>& masses_sq,
                                   const std::vector<double>& dofs,
                                   const std::vector<double>& vacuum_masses_sq) const {
          if (masses_sq.size() != dofs.size() || masses_sq.size() != vacuum_masses_sq.size()) {
            throw std::runtime_error("OS-like V1 masses, d.o.f., and vacuum masses do not match");
          }
          double correction = 0.0;
          for (size_t i = 0; i < masses_sq.size(); ++i) {
            const double m2 = masses_sq[i];
            const double m2_vac = vacuum_masses_sq[i];
            if (std::abs(m2_vac) <= 1e-80) continue;
            const double denom = std::abs(m2_vac);
            correction += dofs[i] * (m2 * m2 * (std::log(std::abs(m2 / denom) + 1e-100) - 1.5) + 2.0 * m2 * m2_vac);
          }
          return correction;
        }
        """
    ).strip()


def _render_raddof_method(contract: dict[str, Any]) -> str:
    if not _uses_standard_thermal_integrals(contract):
        return "double get_raddof() const override { return 0.0; }"
    values = _parameter_values(contract)
    if "num_boson_dof" not in values or "num_fermion_dof" not in values:
        raise CompileBlocked("PhaseTracer standard thermal integrals need reviewed num_boson_dof and num_fermion_dof constants.")
    return f"double get_raddof() const override {{ return {_float_literal(_radiation_dof(contract))}; }}"


def _render_zero_t_vacuum_method(
    contract: dict[str, Any],
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> str:
    lines = [
        "Eigen::VectorXd ptagent_zeroT_vacuum_point() const {",
        *_render_parameter_bindings(public_inputs, constants, derived, indent="  "),
        f"  Eigen::VectorXd point({len(fields)});",
    ]
    for index, row in enumerate(fields):
        expr = str(row.get("zeroT_default", "0.0")).strip() or "0.0"
        lines.append(f"  point[{index}] = {render_cpp_expression(expr)};")
    lines.extend(["  return point;", "}"])
    return "\n".join(lines)


def _render_symmetry_methods(contract: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    symmetry = _dict(_dict(contract.get("implementation")).get("symmetry"))
    mode = str(symmetry.get("mode", "none")).strip() or "none"
    if mode == "none":
        return "\n".join(
            [
                "std::vector<Eigen::VectorXd> apply_symmetry(Eigen::VectorXd phi) const override {",
                "  (void)phi;",
                "  return {};",
                "}",
                "",
                "std::vector<std::vector<int>> get_symmetry_axes() const override {",
                "  return {};",
                "}",
            ]
        )
    if mode != "z2_reflection":
        raise CompileBlocked(f"PhaseTracer does not support symmetry mode {mode!r}.")
    field_order = [str(row.get("name", "")) for row in fields]
    reflection_groups: list[list[int]] = []
    for row in _list(symmetry.get("rules")):
        group: list[int] = []
        for field in _split_symmetry_fields(str(_dict(row).get("fields", ""))):
            try:
                group.append(field_order.index(field))
            except ValueError as exc:
                raise CompileBlocked(f"PhaseTracer symmetry field {field!r} is not in field order {field_order!r}.") from exc
        if group:
            reflection_groups.append(group)
    lines = ["std::vector<Eigen::VectorXd> apply_symmetry(Eigen::VectorXd phi) const override {", "  std::vector<Eigen::VectorXd> partners;"]
    for group_index, group in enumerate(reflection_groups):
        lines.append(f"  auto reflected_{group_index} = phi;")
        for field_index in group:
            lines.append(f"  reflected_{group_index}[{field_index}] = -reflected_{group_index}[{field_index}];")
        lines.append(f"  partners.push_back(reflected_{group_index});")
    lines.extend(["  return partners;", "}", "", "std::vector<std::vector<int>> get_symmetry_axes() const override {"])
    if reflection_groups:
        group_literals = ["{" + ", ".join(str(index) for index in group) + "}" for group in reflection_groups]
        lines.append("  return {" + ", ".join(group_literals) + "};")
    else:
        lines.append("  return {};")
    lines.append("}")
    return "\n".join(lines)


def _split_symmetry_fields(fields_text: str) -> list[str]:
    return [item.strip() for item in fields_text.split(",") if item.strip()]


def _render_method_prelude(
    fields: list[dict[str, Any]],
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    *,
    include_temperature: bool,
    indent: str,
) -> list[str]:
    lines = [
        _render_field_size_check(fields, indent=indent),
        *_render_field_unpack(fields, indent=indent),
    ]
    if include_temperature:
        lines.append(f"{indent}(void)T;")
    lines.extend(_render_parameter_bindings(public_inputs, constants, derived, indent=indent))
    return lines


def _render_field_size_check(fields: list[dict[str, Any]], *, indent: str) -> str:
    return f"{indent}if (static_cast<size_t>(phi.size()) != get_n_scalars()) throw std::runtime_error(\"Expected {len(fields)} scalar field(s).\");"


def _render_field_unpack(fields: list[dict[str, Any]], *, indent: str) -> list[str]:
    return [f"{indent}const double {row['name']} = phi[{index}];" for index, row in enumerate(fields)]


def _render_parameter_bindings(
    public_inputs: list[dict[str, Any]],
    constants: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    *,
    indent: str,
) -> list[str]:
    lines = [f"{indent}const double {row['name']} = {row['name']}_;" for row in public_inputs]
    lines.extend(f"{indent}const double {row['name']} = {_float_literal(row.get('value'))};" for row in constants)
    try:
        for row in derived:
            lines.extend(render_cpp_assignment_block(str(row.get("expr", "0.0")), str(row.get("name", "")), indent=indent))
    except PhaseTracerExpressionError as exc:
        raise CompileBlocked(f"PhaseTracer could not render derived expression: {exc}") from exc
    return lines


def _render_mass_spec_pushes(specs: list[dict[str, Any]], *, indent: str) -> list[str]:
    lines: list[str] = []
    for spec in specs:
        name = _program_symbol(spec["name"])
        if spec["source"] == "direct":
            var = f"m2_{name}"
            lines.append(f"{indent}{{")
            lines.extend(render_cpp_assignment_block(spec["expr"], var, indent=indent + "  ", prefer_square_aliases=True))
            lines.append(f"{indent}  masses.push_back({var});")
            lines.append(f"{indent}}}")
            continue
        matrix = _dict(spec["matrix"])
        basis = _list(matrix.get("basis"))
        entries = _list(matrix.get("matrix"))
        size = len(basis)
        matrix_var = f"M_{name}"
        eigen_var = f"eig_{name}"
        lines.append(f"{indent}Eigen::MatrixXd {matrix_var}({size}, {size});")
        for row_index, row in enumerate(entries):
            for col_index, expr in enumerate(_list(row)):
                lines.append(f"{indent}{{")
                lines.extend(_render_cpp_assignment_to_existing(str(expr), f"{matrix_var}({row_index}, {col_index})", indent=indent + "  "))
                lines.append(f"{indent}}}")
        lines.append(f"{indent}Eigen::SelfAdjointEigenSolver<Eigen::MatrixXd> {eigen_var}({matrix_var});")
        lines.append(f"{indent}if ({eigen_var}.info() != Eigen::Success) throw std::runtime_error(\"Eigenvalue decomposition failed for {name}\");")
        lines.append(f"{indent}for (int i = 0; i < {eigen_var}.eigenvalues().size(); ++i) masses.push_back({eigen_var}.eigenvalues()[i]);")
    return lines


def _render_cpp_assignment_to_existing(expr: str, target: str, *, indent: str) -> list[str]:
    tmp_name = f"ptagent_tmp_{_program_symbol(target)}"
    lines = render_cpp_assignment_block(expr, tmp_name, indent=indent, prefer_square_aliases=True)
    if lines and lines[-1].startswith(f"{indent}const double {tmp_name} = "):
        lines[-1] = f"{indent}{target} = " + lines[-1].split(" = ", 1)[1]
        return lines
    lines.append(f"{indent}{target} = {tmp_name};")
    return lines


def _cleanup_generated_cpp_source(source: str) -> str:
    lines = source.strip().splitlines()
    for _iteration in range(12):
        remove_indices: set[int] = set()
        depths = _cpp_line_depths(lines)
        for index, line in enumerate(lines):
            const_match = re.match(r"^\s*const\s+double\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if const_match:
                name = const_match.group(1)
                if not _cpp_name_used_later_in_scope(lines, depths, index, name):
                    remove_indices.add(index)
                continue
            void_match = re.match(r"^\s*\(void\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$", line)
            if void_match and _cpp_name_used_later_in_scope(lines, depths, index, void_match.group(1)):
                remove_indices.add(index)
        if not remove_indices:
            break
        lines = [line for index, line in enumerate(lines) if index not in remove_indices]
        lines = _cleanup_empty_cpp_blocks(lines)
    lines = _cleanup_unused_ptagent_value_helpers(lines)
    lines = _cleanup_redundant_blank_cpp_lines(lines)
    return "\n".join(lines).strip() + "\n"


def _cleanup_unused_ptagent_value_helpers(lines: list[str]) -> list[str]:
    helper_pattern = re.compile(
        r"^\s*(?:std::vector<double>|double)\s+(ptagent_[A-Za-z_][A-Za-z0-9_]*)\(\)\s+const\s+\{\s+return\b.*\}\s*$"
    )
    remove_indices: set[int] = set()
    for index, line in enumerate(lines):
        match = helper_pattern.match(line)
        if not match:
            continue
        name = match.group(1)
        use_pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
        if not any(other_index != index and use_pattern.search(other_line) for other_index, other_line in enumerate(lines)):
            remove_indices.add(index)
    if not remove_indices:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indices]


def _cleanup_redundant_blank_cpp_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            cleaned.append(line)
            continue
        blank_count += 1
        if blank_count <= 1:
            cleaned.append(line)
    return cleaned


def _maybe_clang_format_project(project_dir: Path, *, enabled: bool) -> list[str]:
    if not enabled:
        return []
    formatter = shutil.which("clang-format")
    if not formatter:
        return []
    paths = [
        *sorted(project_dir.glob("*_potential.hpp")),
        project_dir / "run_model.cpp",
    ]
    existing = [str(path) for path in paths if path.exists()]
    if not existing:
        return []
    result = subprocess.run(
        [formatter, "-i", *existing],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode == 0:
        return []
    message = (result.stderr or result.stdout or "").strip()
    return [f"clang-format failed for generated PhaseTracer C++ files: {message or result.returncode}"]


def _cpp_line_depths(lines: list[str]) -> list[int]:
    depths: list[int] = []
    depth = 0
    for line in lines:
        depths.append(depth)
        depth += _cpp_brace_delta(line)
        depth = max(depth, 0)
    return depths


def _cpp_brace_delta(line: str) -> int:
    text = _strip_cpp_string_literals(line)
    return text.count("{") - text.count("}")


def _strip_cpp_string_literals(line: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', line)


def _cpp_name_used_later_in_scope(lines: list[str], depths: list[int], index: int, name: str) -> bool:
    scope_depth = depths[index]
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for later_index in range(index + 1, len(lines)):
        if depths[later_index] < scope_depth:
            break
        if pattern.search(lines[later_index]):
            return True
    return False


def _cleanup_empty_cpp_blocks(lines: list[str]) -> list[str]:
    cleaned = list(lines)
    for _iteration in range(12):
        removed = False
        next_lines: list[str] = []
        index = 0
        while index < len(cleaned):
            if (
                index + 1 < len(cleaned)
                and re.match(r"^\s*\{\s*$", cleaned[index])
                and re.match(r"^\s*\}\s*$", cleaned[index + 1])
            ):
                index += 2
                removed = True
                continue
            next_lines.append(cleaned[index])
            index += 1
        cleaned = next_lines
        if not removed:
            break
    return cleaned


def _render_model_runner(contract: dict[str, Any], *, header_filename: str | None = None) -> str:
    if header_filename is None:
        header_filename = _phasetracer_header_filename(contract)
    points = _smoke_points(contract)
    point_rows = []
    for index, (phi, temperature) in enumerate(points):
        values = ", ".join(_float_literal(value) for value in phi)
        point_rows.append(
            f"    {{std::vector<double>{{{values}}}, {_float_literal(temperature)}, {index}}},"
        )
    component_declarations: list[str] = []
    point_scalar_stream = [
        '    std::cout << "PTAGENT_POINT " << point.index',
        '              << " T " << point.T',
        '              << " V " << value',
        '              << " V0 " << v0',
    ]
    if _needs_v1_component_helper(contract):
        component_declarations.append(f"    const double v1 = {_v1_component_expression(contract, receiver='model.')};")
        point_scalar_stream.append('              << " V1 " << v1')
    if _needs_v1t_component_helper(contract):
        component_declarations.append(f"    const double v1t = {_v1t_component_expression(contract, receiver='model.')};")
        point_scalar_stream.append('              << " V1T " << v1t')
    if _needs_daisy_component_helper(contract):
        component_declarations.append(f"    const double daisy = {_daisy_component_expression(contract, receiver='model.')};")
        point_scalar_stream.append('              << " daisy " << daisy')
    component_declarations.append("    const double counter_term = model.counter_term(phi, point.T);")
    point_scalar_stream.extend(
        [
            '              << " counter_term " << counter_term',
            '              << " raddof " << model.get_raddof()',
            '              << "\\n";',
        ]
    )
    lines = [
        "#include <cmath>",
        "#include <iomanip>",
        "#include <iostream>",
        "#include <stdexcept>",
        "#include <string>",
        "#include <vector>",
        "",
        f'#include "{header_filename}"',
        '#include "logger.hpp"',
        '#include "phasetracer.hpp"',
        '#include "phase_finder.hpp"',
        '#include "action_calculator.hpp"',
        '#include "transition_finder.hpp"',
        "",
        "struct SmokePoint {",
        "  std::vector<double> phi;",
        "  double T;",
        "  int index;",
        "};",
        "",
        "struct TransitionOptions {",
        "  bool relax_dxdt_check = true;",
        "  bool merge_phase_gaps = false;",
        "  bool print_phases = false;",
        "  bool has_seed = true;",
        "  int seed = 0;",
        "  double hessian_singular_rel_tol = 1.0e-5;",
        "};",
        "",
        "void print_vector(int index, const char* name, const std::vector<double>& values) {",
        '  std::cout << "PTAGENT_VECTOR " << index << " " << name;',
        '  for (const double value : values) std::cout << " " << value;',
        '  std::cout << "\\n";',
        "}",
        "",
        "void print_transition_vector(size_t index, const char* name, const Eigen::VectorXd& values) {",
        '  std::cout << "PTAGENT_TRANSITION_VECTOR " << index << " " << name;',
        "  for (Eigen::Index i = 0; i < values.size(); ++i) std::cout << \" \" << values[i];",
        '  std::cout << "\\n";',
        "}",
        "",
        "int run_potential_check() {",
        "  PTagentPhaseTracer::GeneratedPotential model;",
        "  std::cout << std::setprecision(17);",
        "  const std::vector<SmokePoint> points = {",
        *point_rows,
        "  };",
        "  for (const auto& point : points) {",
        "    Eigen::VectorXd phi(point.phi.size());",
        "    for (int i = 0; i < phi.size(); ++i) phi[i] = point.phi[static_cast<size_t>(i)];",
        "    const double T = point.T;",
        "    const double value = model.V(phi, point.T);",
        '    if (!std::isfinite(value)) throw std::runtime_error("non-finite potential");',
        "    const double v0 = model.V0(phi);",
        *component_declarations,
        *point_scalar_stream,
        '    print_vector(point.index, "phi", point.phi);',
        '    print_vector(point.index, "scalar_masses", model.get_scalar_masses_sq(phi, model.get_xi()));',
        '    print_vector(point.index, "vector_masses", model.get_vector_masses_sq(phi));',
        '    print_vector(point.index, "fermion_masses", model.get_fermion_masses_sq(phi));',
        '    print_vector(point.index, "scalar_debye", model.get_scalar_debye_sq(phi, model.get_xi(), point.T));',
        '    print_vector(point.index, "vector_debye", model.get_vector_debye_sq(phi, point.T));',
        '    print_vector(point.index, "scalar_dofs", model.get_scalar_dofs());',
        '    print_vector(point.index, "vector_dofs", model.get_vector_dofs());',
        '    print_vector(point.index, "fermion_dofs", model.get_fermion_dofs());',
        "    const auto symmetry_partners = model.apply_symmetry(phi);",
        '    std::cout << "PTAGENT_SYMMETRY " << point.index << " partners " << symmetry_partners.size() << "\\n";',
        "    for (size_t partner_index = 0; partner_index < symmetry_partners.size(); ++partner_index) {",
        "      std::vector<double> partner_values;",
        "      for (Eigen::Index i = 0; i < symmetry_partners[partner_index].size(); ++i) partner_values.push_back(symmetry_partners[partner_index][i]);",
        '      const std::string partner_name = "symmetry_partner_" + std::to_string(partner_index);',
        "      print_vector(point.index, partner_name.c_str(), partner_values);",
        "    }",
        "  }",
        "  return 0;",
        "}",
        "",
        "int run_transition_check(const TransitionOptions& options) {",
        "  try {",
        "    PTagentPhaseTracer::GeneratedPotential model;",
        "    std::cout << std::setprecision(17);",
        "    PhaseTracer::PhaseFinder phase_finder(model);",
        "    if (options.has_seed) phase_finder.set_seed(options.seed);",
        "    if (options.relax_dxdt_check) phase_finder.set_check_dx_min_dt(false);",
        "    if (options.merge_phase_gaps) phase_finder.set_check_merge_phase_gaps(true);",
        "    if (options.hessian_singular_rel_tol > 0.0) {",
        "      phase_finder.set_hessian_singular_rel_tol(options.hessian_singular_rel_tol);",
        "    }",
        "    phase_finder.find_phases();",
        "    if (options.print_phases) std::cout << phase_finder;",
        "    PhaseTracer::ActionCalculator action_calculator(model);",
        "    PhaseTracer::TransitionFinder transition_finder(phase_finder, action_calculator);",
        "    transition_finder.set_fit_action_curve(false);",
        "    transition_finder.find_transitions();",
        "    const auto transitions = transition_finder.get_transitions();",
        '    std::cout << "PTAGENT_TRANSITION transitions " << transitions.size() << "\\n";',
        '    std::cout << "PTAGENT_TC_COUNT " << transitions.size() << "\\n";',
        "    if (transitions.empty()) {",
        '      std::cout << "PTAGENT_TC_STATUS no_transitions\\n";',
        "    } else {",
        '      std::cout << "PTAGENT_TC_STATUS success " << transitions.size() << "\\n";',
        "    }",
        "    for (size_t index = 0; index < transitions.size(); ++index) {",
        "      const auto& transition = transitions[index];",
        '      std::cout << "PTAGENT_TC " << index',
        '                << " TC " << transition.TC',
        '                << " gamma " << transition.gamma',
        '                << " delta_potential " << transition.delta_potential',
        '                << " subcritical " << transition.subcritical',
        '                << "\\n";',
        '      print_transition_vector(index, "low_vev", transition.true_vacuum);',
        '      print_transition_vector(index, "high_vev", transition.false_vacuum);',
        "    }",
        "  } catch (const std::exception& exc) {",
        '    std::cout << "PTAGENT_TC_STATUS error " << exc.what() << "\\n";',
        "    return 1;",
        "  } catch (...) {",
        '    std::cout << "PTAGENT_TC_STATUS error unknown exception\\n";',
        "    return 1;",
        "  }",
        "  return 0;",
        "}",
        "",
        "int main(int argc, char** argv) {",
        "  LOGGER(debug);",
        "  bool run_transition = false;",
        "  TransitionOptions transition_options;",
        "  for (int i = 1; i < argc; ++i) {",
        "    const std::string arg = argv[i];",
        '    if (arg == "--transition") {',
        "      run_transition = true;",
        '    } else if (arg == "--relax-dxdt-check") {',
        "      transition_options.relax_dxdt_check = true;",
        '    } else if (arg == "--strict-dxdt-check") {',
        "      transition_options.relax_dxdt_check = false;",
        '    } else if (arg == "--merge-phase-gaps") {',
        "      transition_options.merge_phase_gaps = true;",
        '    } else if (arg == "--no-merge-phase-gaps") {',
        "      transition_options.merge_phase_gaps = false;",
        '    } else if (arg == "--print-phases") {',
        "      transition_options.print_phases = true;",
        '    } else if (arg == "--seed") {',
        "      if (++i >= argc) {",
        '        std::cerr << "Missing value after --seed\\n";',
        "        return 2;",
        "      }",
        "      transition_options.seed = std::stoi(argv[i]);",
        "      transition_options.has_seed = true;",
        '    } else if (arg == "--no-seed") {',
        "      transition_options.has_seed = false;",
        '    } else if (arg == "--hessian-singular-rel-tol") {',
        "      if (++i >= argc) {",
        '        std::cerr << "Missing value after --hessian-singular-rel-tol\\n";',
        "        return 2;",
        "      }",
        "      transition_options.hessian_singular_rel_tol = std::stod(argv[i]);",
        "    } else {",
        '      std::cerr << "Unknown argument: " << arg << "\\n";',
        "      return 2;",
        "    }",
        "  }",
        "  const int smoke_status = run_potential_check();",
        "  if (smoke_status != 0 || !run_transition) return smoke_status;",
        "  return run_transition_check(transition_options);",
        "}",
        "",
    ]
    return "\n".join(lines)


def _render_cmake() -> str:
    return (
        textwrap.dedent(
            """
            cmake_minimum_required(VERSION 3.16)
            project(ptagent_generated_phasetracer LANGUAGES CXX)

            set(CMAKE_CXX_STANDARD 17)
            set(CMAKE_CXX_STANDARD_REQUIRED ON)
            set(CMAKE_CXX_EXTENSIONS OFF)

            set(PHASETRACER_ROOT "" CACHE PATH "Path to the PhaseTracer source tree")
            if(NOT PHASETRACER_ROOT)
              message(FATAL_ERROR "Set -DPHASETRACER_ROOT=/path/to/PhaseTracer")
            endif()

            set(PHASETRACER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
            set(PHASETRACER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
            add_subdirectory(${PHASETRACER_ROOT} ${CMAKE_BINARY_DIR}/PhaseTracer EXCLUDE_FROM_ALL)

            add_executable(run_model run_model.cpp)
            target_include_directories(run_model PRIVATE
              ${CMAKE_CURRENT_SOURCE_DIR}
              ${PHASETRACER_ROOT}/EffectivePotential/include
              ${PHASETRACER_ROOT}/EffectivePotential/include/effectivepotential
              ${PHASETRACER_ROOT}/include
            )
            target_link_libraries(run_model PRIVATE phasetracer effectivepotential)
            """
        ).strip()
        + "\n"
    )


def _validate_phasetracer_contract(contract: dict[str, Any]) -> None:
    counterterm = _counterterm_mode(contract)
    if counterterm not in {"none", "custom_expr", "implicit_in_V1", "explicit_linear_system"}:
        raise CompileBlocked(
            "PhaseTracer OneLoopPotential backend does not yet support counterterm route(s): "
            f"counterterm={counterterm}."
        )
    if counterterm == "explicit_linear_system":
        _explicit_counterterm_spec(contract)
    loops = _dict(contract.get("loops"))
    zero_mode = str(_dict(loops.get("zero_temperature")).get("mode", "none"))
    thermal_mode = str(_dict(loops.get("thermal")).get("mode", "none"))
    daisy_mode = str(_dict(loops.get("daisy")).get("mode", "none"))
    if counterterm == "explicit_linear_system" and zero_mode == "paper_os_like_V1":
        raise CompileBlocked(
            "PhaseTracer backend does not combine paper_os_like_V1 with explicit_linear_system counterterms; "
            "OS-like V1 already fixes the zero-temperature renormalization route."
        )
    unsupported = []
    if zero_mode not in {"none", "custom_expr", "standard_CW_V1", "paper_os_like_V1"}:
        unsupported.append(f"zero_temperature={zero_mode}")
    if thermal_mode not in {"none", "custom_expr", "standard_thermal_integrals"}:
        unsupported.append(f"thermal={thermal_mode}")
    if daisy_mode not in {"none", "custom_expr"}:
        unsupported.append(f"daisy={daisy_mode}")
    if unsupported:
        raise CompileBlocked("PhaseTracer does not support route(s): " + ", ".join(unsupported))
    if _phasetracer_daisy_method(contract) == "Parwani":
        for kind in ("scalar", "vector"):
            _aligned_thermal_mass_specs(contract, kind, required=True)
    if _uses_default_arnold_espinosa_daisy(contract):
        for kind in ("scalar", "vector"):
            _aligned_thermal_mass_specs(contract, kind, required=True)


def _evaluate_total_without_standard_thermal_integrals(contract: dict[str, Any], phi: list[float], temperature: float) -> float:
    total = evaluate_contract_v0(contract, phi, temperature)
    loops = _dict(contract.get("loops"))
    zero = _dict(loops.get("zero_temperature"))
    thermal = _dict(loops.get("thermal"))
    daisy = _dict(loops.get("daisy"))
    model_card = _dict(contract.get("model_card"))
    zero_mode = str(zero.get("mode", "none"))
    thermal_mode = str(thermal.get("mode", "none"))
    daisy_mode = str(daisy.get("mode", "none"))
    if zero_mode in {"standard_CW_V1", "paper_os_like_V1"}:
        total += _evaluate_v1(contract, phi, temperature)
    elif zero_mode == "custom_expr":
        total += _eval_compiler_block(str(zero.get("custom_expr", "0.0")), _reference_environment(contract, phi, temperature))
    total += _evaluate_counterterm(contract, phi, temperature)
    if thermal_mode == "custom_expr":
        total += _eval_compiler_block(str(thermal.get("custom_expr", "0.0")), _reference_environment(contract, phi, temperature))
    elif thermal_mode == "standard_thermal_integrals":
        raise CompileBlocked("Python PhaseTracer reference does not implement backend-native standard thermal integrals.")
    if daisy_mode == "custom_expr":
        if _uses_default_arnold_espinosa_daisy(contract):
            total += _evaluate_default_daisy(contract, phi, temperature)
        else:
            total += _eval_compiler_block(str(daisy.get("custom_expr", "0.0")), _reference_environment(contract, phi, temperature))
    return float(total)


def _evaluate_counterterm(contract: dict[str, Any], phi: list[float], temperature: float) -> float:
    mode = _counterterm_mode(contract)
    if mode == "custom_expr":
        return _eval_compiler_block(
            str(_dict(_dict(contract.get("potential")).get("V_CT")).get("python", "0.0")),
            _reference_environment(contract, phi, temperature),
        )
    if mode == "explicit_linear_system":
        spec = _explicit_counterterm_spec(contract)
        coeffs = _solve_explicit_counterterms_reference(contract, spec)
        basis = _evaluate_ct_basis_values(contract, phi, temperature, spec)
        return float(np.dot(np.asarray(coeffs, dtype=float), np.asarray(basis, dtype=float)))
    return 0.0


def _evaluate_v1(contract: dict[str, Any], phi: list[float], temperature: float) -> float:
    zero_mode = str(_dict(_dict(contract.get("loops")).get("zero_temperature")).get("mode", "none"))
    scalar_masses = _evaluate_loop_masses(contract, phi, temperature, "scalar")
    vector_masses = _evaluate_loop_masses(contract, phi, temperature, "vector")
    fermion_masses = _evaluate_fermion_masses(contract, phi)
    if zero_mode == "paper_os_like_V1":
        vacuum = _zero_t_vacuum_values(contract)
        scalar_vacuum = _evaluate_masses(contract, vacuum, "scalar")
        vector_vacuum = _evaluate_masses(contract, vacuum, "vector")
        fermion_vacuum = _evaluate_fermion_masses(contract, vacuum)
        correction = _os_like_sum(scalar_masses, _mass_dofs(contract, "scalar"), scalar_vacuum)
        correction += _os_like_sum(vector_masses, _mass_dofs(contract, "vector"), vector_vacuum)
        correction -= _os_like_sum(fermion_masses, _fermion_dofs(contract), fermion_vacuum)
        return correction / (64.0 * math.pi * math.pi)
    q_sq = _renorm_scale_sq(contract)
    correction = _standard_v1_sum(scalar_masses, _mass_dofs(contract, "scalar"), _mass_c_values(contract, "scalar"), 1.0, q_sq)
    correction += _standard_v1_sum(vector_masses, _mass_dofs(contract, "vector"), _mass_c_values(contract, "vector"), 1.0, q_sq)
    correction += _standard_v1_sum(fermion_masses, _fermion_dofs(contract), [1.5] * len(fermion_masses), -1.0, q_sq)
    return correction / (64.0 * math.pi * math.pi)


def _evaluate_loop_masses(contract: dict[str, Any], phi: list[float], temperature: float, kind: str) -> list[float]:
    if _phasetracer_daisy_method(contract) == "Parwani" and temperature > 0.0:
        return _evaluate_debye_masses(contract, phi, temperature, kind)
    return _evaluate_masses(contract, phi, kind)


def _solve_explicit_counterterms_reference(contract: dict[str, Any], spec: dict[str, Any]) -> np.ndarray:
    points = [np.asarray([_eval_compiler_block(component, _parameter_reference_environment(contract)) for component in point], dtype=float) for point in spec["points"]]
    axes = spec["axes"]
    targets = [
        _eval_compiler_block(str(row.get("target_expr", "0.0")), _reference_environment(contract, point.tolist(), 0.0))
        for row, point in zip(spec["conditions"], points)
    ]
    rows = [
        _ct_apply_operator_vector(lambda value: _evaluate_ct_basis_values(contract, value.tolist(), 0.0, spec), point, axis)
        for point, axis in zip(points, axes)
    ]
    a_matrix = np.asarray(rows, dtype=float)
    b_vector = np.asarray(
        [
            target - _evaluate_ct_source_derivative(contract, point, axis, handler)
            for target, point, axis, handler in zip(targets, points, axes, spec["handlers"])
        ],
        dtype=float,
    )
    if a_matrix.shape[0] != a_matrix.shape[1]:
        raise CompileBlocked(f"CT linear system must be square, got {a_matrix.shape}.")
    if np.linalg.matrix_rank(a_matrix) < a_matrix.shape[1]:
        raise CompileBlocked("CT linear system is singular; review the counterterm basis and conditions.")
    return np.linalg.solve(a_matrix, b_vector)


def _evaluate_ct_basis_values(contract: dict[str, Any], phi: list[float], temperature: float, spec: dict[str, Any]) -> list[float]:
    env = _reference_environment(contract, phi, temperature)
    return [_eval_compiler_block(str(row.get("operator_expr", "0.0")), env) for row in spec["basis"]]


def _evaluate_ct_source_derivative(contract: dict[str, Any], point: np.ndarray, axes: tuple[int, ...], handler: str) -> float:
    if handler == "regulated_replacement":
        source = _ct_apply_operator_scalar(lambda value: _evaluate_ct_non_goldstone_vcw(contract, value.tolist()), point, axes)
        if len(axes) == 2:
            source += float(_evaluate_ct_regulated_goldstone_hessian(contract, point)[axes[0], axes[1]])
        return float(source)
    return float(_ct_apply_operator_scalar(lambda value: _evaluate_ct_vcw(contract, value.tolist()), point, axes))


def _evaluate_ct_vcw(contract: dict[str, Any], phi: list[float]) -> float:
    zero = _dict(_dict(contract.get("loops")).get("zero_temperature"))
    mode = str(zero.get("mode", "none"))
    if mode in {"standard_CW_V1", "paper_os_like_V1"}:
        return _evaluate_v1(contract, phi, 0.0)
    if mode == "custom_expr":
        return _eval_compiler_block(str(zero.get("custom_expr", "0.0")), _reference_environment(contract, phi, 0.0))
    return 0.0


def _evaluate_ct_non_goldstone_vcw(contract: dict[str, Any], phi: list[float]) -> float:
    return _evaluate_ct_vcw(contract, phi) - _evaluate_ct_goldstone_cw_for_ct(contract, phi)


def _evaluate_ct_goldstone_masses(contract: dict[str, Any], phi: list[float]) -> tuple[list[float], list[float], list[float]]:
    goldstone = _dict(_dict(contract.get("implementation")).get("goldstone"))
    env = _reference_environment(contract, phi, 0.0)
    masses: list[float] = []
    replacements: list[float] = []
    dofs: list[float] = []
    for row_value in _list(goldstone.get("species")):
        row = _dict(row_value)
        masses.append(_eval_compiler_block(str(row.get("mass_sq", "0.0")), env))
        replacements.append(_eval_compiler_block(str(row.get("replacement_expr") or goldstone.get("replacement_formula") or "0.0"), env))
        dofs.append(float(row.get("dof", 1.0)))
    return masses, replacements, dofs


def _evaluate_ct_goldstone_cw_for_ct(contract: dict[str, Any], phi: list[float]) -> float:
    masses, _replacements, dofs = _evaluate_ct_goldstone_masses(contract, phi)
    q_sq = _renorm_scale_sq(contract)
    total = 0.0
    for m2, dof in zip(masses, dofs):
        total += dof * m2 * m2 * (math.log(abs(m2 / q_sq) + 1e-100) - 1.5)
    return total / (64.0 * math.pi * math.pi)


def _evaluate_ct_regulated_goldstone_hessian(contract: dict[str, Any], point: np.ndarray) -> np.ndarray:
    masses, replacements, dofs = _evaluate_ct_goldstone_masses(contract, point.tolist())
    field_count = len(_list(contract.get("fields")))
    if not masses:
        return np.zeros((field_count, field_count), dtype=float)
    derivatives = []
    eps = 0.001
    for axis in range(field_count):
        step = np.zeros(field_count, dtype=float)
        step[axis] = eps
        plus = np.asarray(_evaluate_ct_goldstone_masses(contract, (point + step).tolist())[0], dtype=float)
        minus = np.asarray(_evaluate_ct_goldstone_masses(contract, (point - step).tolist())[0], dtype=float)
        derivatives.append((plus - minus) / (2.0 * eps))
    dm = np.stack(derivatives, axis=-1)
    q_sq = _renorm_scale_sq(contract)
    logs = np.log(np.abs(np.asarray(replacements, dtype=float) / q_sq) + 1e-100)
    weights = np.asarray(dofs, dtype=float) * logs / (32.0 * math.pi * math.pi)
    return np.einsum("a,ai,aj->ij", weights, dm, dm)


def _ct_apply_operator_scalar(func: Any, point: np.ndarray, axes: tuple[int, ...]) -> float:
    eps = 0.001
    if len(axes) == 1:
        step = np.zeros_like(point, dtype=float)
        step[axes[0]] = eps
        return float((func(point + step) - func(point - step)) / (2.0 * eps))
    if len(axes) == 2 and axes[0] == axes[1]:
        step = np.zeros_like(point, dtype=float)
        step[axes[0]] = eps
        return float((func(point + step) - 2.0 * func(point) + func(point - step)) / (eps * eps))
    if len(axes) == 2:
        step_a = np.zeros_like(point, dtype=float)
        step_b = np.zeros_like(point, dtype=float)
        step_a[axes[0]] = eps
        step_b[axes[1]] = eps
        return float((func(point + step_a + step_b) - func(point + step_a - step_b) - func(point - step_a + step_b) + func(point - step_a - step_b)) / (4.0 * eps * eps))
    raise CompileBlocked("Unsupported CT operator order.")


def _ct_apply_operator_vector(func: Any, point: np.ndarray, axes: tuple[int, ...]) -> list[float]:
    eps = 0.001
    if len(axes) == 1:
        step = np.zeros_like(point, dtype=float)
        step[axes[0]] = eps
        plus = np.asarray(func(point + step), dtype=float)
        minus = np.asarray(func(point - step), dtype=float)
        return ((plus - minus) / (2.0 * eps)).tolist()
    if len(axes) == 2 and axes[0] == axes[1]:
        step = np.zeros_like(point, dtype=float)
        step[axes[0]] = eps
        plus = np.asarray(func(point + step), dtype=float)
        center = np.asarray(func(point), dtype=float)
        minus = np.asarray(func(point - step), dtype=float)
        return ((plus - 2.0 * center + minus) / (eps * eps)).tolist()
    if len(axes) == 2:
        step_a = np.zeros_like(point, dtype=float)
        step_b = np.zeros_like(point, dtype=float)
        step_a[axes[0]] = eps
        step_b[axes[1]] = eps
        pp = np.asarray(func(point + step_a + step_b), dtype=float)
        pm = np.asarray(func(point + step_a - step_b), dtype=float)
        mp = np.asarray(func(point - step_a + step_b), dtype=float)
        mm = np.asarray(func(point - step_a - step_b), dtype=float)
        return ((pp - pm - mp + mm) / (4.0 * eps * eps)).tolist()
    raise CompileBlocked("Unsupported CT operator order.")


def _renorm_scale_sq(contract: dict[str, Any]) -> float:
    name = reviewed_renormalization_scale_name(contract)
    values = _parameter_reference_environment(contract)
    if name not in values:
        raise CompileBlocked(f"Reviewed renormalization scale {name!r} could not be evaluated.")
    value = float(values[name])
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return value if normalized == "renormscalesq" else value**2


def _radiation_dof(contract: dict[str, Any]) -> float:
    if not _uses_standard_thermal_integrals(contract):
        return 0.0
    values = _parameter_values(contract)
    return float(values["num_boson_dof"]) + 0.875 * float(values["num_fermion_dof"])


def _standard_v1_sum(masses: list[float], dofs: list[float], c_values: list[float], sign: float, q_sq: float) -> float:
    if len(masses) != len(dofs) or len(masses) != len(c_values):
        raise CompileBlocked("Reference V1 masses, d.o.f., and constants do not match.")
    total = 0.0
    for m2, dof, c_value in zip(masses, dofs, c_values):
        x = m2 / q_sq
        total += sign * dof * m2 * (q_sq * _xlogx(x) - c_value * m2)
    return total


def _os_like_sum(masses: list[float], dofs: list[float], vacuum: list[float]) -> float:
    if len(masses) != len(dofs) or len(masses) != len(vacuum):
        raise CompileBlocked("Reference OS-like V1 masses, d.o.f., and vacuum masses do not match.")
    total = 0.0
    for m2, dof, m2_vac in zip(masses, dofs, vacuum):
        if abs(m2_vac) <= 1e-80:
            continue
        denom = abs(m2_vac)
        total += dof * (m2 * m2 * (math.log(abs(m2 / denom) + 1e-100) - 1.5) + 2.0 * m2 * m2_vac)
    return total


def _xlogx(x: float) -> float:
    abs_x = abs(x)
    if abs_x <= np.finfo(float).tiny:
        return 0.0
    return x * math.log(abs_x)


def _evaluate_default_daisy(contract: dict[str, Any], phi: list[float], temperature: float) -> float:
    scalar_zero = _evaluate_masses(contract, phi, "scalar")
    scalar_thermal = _evaluate_debye_masses(contract, phi, temperature, "scalar")
    vector_zero = _evaluate_masses(contract, phi, "vector")
    vector_thermal = _evaluate_debye_masses(contract, phi, temperature, "vector")
    total = 0.0
    for m2, m2t, dof in zip(scalar_zero, scalar_thermal, _mass_dofs(contract, "scalar")):
        total += dof * (max(0.0, m2t) ** 1.5 - max(0.0, m2) ** 1.5)
    for m2, m2t, dof in zip(vector_zero, vector_thermal, _mass_dofs(contract, "vector")):
        total += dof * (max(0.0, m2t) ** 1.5 - max(0.0, m2) ** 1.5)
    return -temperature * total / (12.0 * math.pi)


def _evaluate_masses(contract: dict[str, Any], phi: list[float], kind: str) -> list[float]:
    env = _reference_environment(contract, phi, 0.0)
    values: list[float] = []
    for spec in _mass_specs(contract, kind):
        if spec["source"] == "direct":
            values.append(_eval_compiler_block(spec["expr"], env))
            continue
        matrix = _dict(spec["matrix"])
        entries = []
        for row in _list(matrix.get("matrix")):
            entries.append([_eval_compiler_block(str(expr), env) for expr in _list(row)])
        if entries:
            values.extend(float(value) for value in np.linalg.eigvalsh(np.asarray(entries, dtype=float)))
    return values


def _evaluate_fermion_masses(contract: dict[str, Any], phi: list[float]) -> list[float]:
    env = _reference_environment(contract, phi, 0.0)
    values: list[float] = []
    for spec in _fermion_specs(contract):
        if spec["source"] == "direct":
            values.append(_eval_compiler_block(spec["expr"], env))
            continue
        matrix = _dict(spec["matrix"])
        entries = [
            [_eval_compiler_block(str(expr), env) for expr in _list(row)]
            for row in _list(matrix.get("matrix"))
        ]
        if entries:
            values.extend(float(value) for value in np.linalg.eigvalsh(np.asarray(entries, dtype=float)))
    return values


def _evaluate_debye_masses(contract: dict[str, Any], phi: list[float], temperature: float, kind: str) -> list[float]:
    env = _reference_environment(contract, phi, temperature)
    values: list[float] = []
    for spec in _aligned_thermal_mass_specs(contract, kind, required=_requires_debye_alignment(contract)):
        if spec["source"] == "direct":
            values.append(_eval_compiler_block(spec["expr"], env))
            continue
        matrix = _dict(spec["matrix"])
        entries = []
        for row in _list(matrix.get("matrix")):
            entries.append([_eval_compiler_block(str(expr), env) for expr in _list(row)])
        if entries:
            values.extend(float(value) for value in np.linalg.eigvalsh(np.asarray(entries, dtype=float)))
    return values


def _mass_specs(contract: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    masses = _dict(contract.get("masses"))
    specs: list[dict[str, Any]] = []
    for index, row in enumerate(_list(masses.get("bosons"))):
        item = _dict(row)
        if not _truthy(item.get("enabled", True)):
            continue
        if _boson_kind(item) != kind:
            continue
        specs.append(
            {
                "source": "direct",
                "name": str(item.get("name", f"{kind}_{index + 1}")),
                "expr": str(item.get("mass_sq", "0.0")),
                "dof": float(item.get("dof", 1.0)),
                "c": float(item.get("c", 1.5 if kind == "scalar" else 5.0 / 6.0)),
                "thermal_policy": str(item.get("thermal_policy", "")),
            }
        )
    for index, matrix in enumerate(_list(masses.get("boson_matrices"))):
        item = _dict(matrix)
        if not _truthy(item.get("enabled", True)):
            continue
        if _boson_kind(item) != kind:
            continue
        if kind == "vector" and str(item.get("thermal_matrix_mode", "none")) != "none" and _list(item.get("thermal_matrix")):
            transverse = dict(item)
            transverse["name"] = f"{item.get('name', f'vector_matrix_{index + 1}')}_transverse"
            transverse["thermal_matrix"] = _list(item.get("matrix"))
            transverse["ptagent_polarization"] = "transverse"
            longitudinal = dict(item)
            longitudinal["name"] = f"{item.get('name', f'vector_matrix_{index + 1}')}_longitudinal"
            longitudinal["ptagent_polarization"] = "longitudinal"
            specs.extend(
                [
                    {
                        "source": "matrix",
                        "name": str(transverse["name"]),
                        "matrix": transverse,
                        "dof": _matrix_polarization_dof(item, "transverse_dof_per_eigenvalue", 2.0 / 3.0),
                        "c": float(item.get("c", 5.0 / 6.0)),
                    },
                    {
                        "source": "matrix",
                        "name": str(longitudinal["name"]),
                        "matrix": longitudinal,
                        "dof": _matrix_polarization_dof(item, "longitudinal_dof_per_eigenvalue", 1.0 / 3.0),
                        "c": float(item.get("c", 5.0 / 6.0)),
                    },
                ]
            )
            continue
        specs.append(
            {
                "source": "matrix",
                "name": str(item.get("name", f"{kind}_matrix_{index + 1}")),
                "matrix": item,
                "dof": float(item.get("dof_per_eigenvalue", 1.0)),
                "c": float(item.get("c", 1.5 if kind == "scalar" else 5.0 / 6.0)),
            }
        )
    return specs


def _matrix_polarization_dof(matrix: dict[str, Any], key: str, fraction: float) -> float:
    try:
        explicit = float(matrix.get(key, 0.0))
    except (TypeError, ValueError):
        explicit = 0.0
    if explicit > 0.0:
        return explicit
    return float(matrix.get("dof_per_eigenvalue", 0.0)) * fraction


def _fermion_specs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    specs = []
    masses = _dict(contract.get("masses"))
    for index, row in enumerate(_list(masses.get("fermions"))):
        item = _dict(row)
        if not _truthy(item.get("enabled", True)):
            continue
        specs.append(
            {
                "source": "direct",
                "name": str(item.get("name", f"fermion_{index + 1}")),
                "expr": str(item.get("mass_sq", "0.0")),
                "dof": float(item.get("dof", 1.0)),
            }
        )
    for index, matrix in enumerate(_list(masses.get("fermion_matrices"))):
        item = _dict(matrix)
        if not _truthy(item.get("enabled", True)):
            continue
        specs.append(
            {
                "source": "matrix",
                "name": str(item.get("name", f"fermion_matrix_{index + 1}")),
                "matrix": item,
                "dof": float(item.get("dof_per_eigenvalue", 2.0)),
            }
        )
    return specs


def _boson_kind(row: dict[str, Any]) -> str:
    kind = str(row.get("kind", "scalar")).strip().lower()
    return "vector" if "vector" in kind or "gauge" in kind else "scalar"


def _mass_labels(contract: dict[str, Any], kind: str) -> list[str]:
    labels: list[str] = []
    for spec in _mass_specs(contract, kind):
        if spec["source"] == "direct":
            labels.append(str(spec["name"]))
        else:
            matrix = _dict(spec["matrix"])
            size = len(_list(matrix.get("basis")))
            base = str(spec["name"])
            labels.extend(f"{base}_eig{index + 1}" for index in range(size))
    return labels


def _mass_dofs(contract: dict[str, Any], kind: str) -> list[float]:
    values: list[float] = []
    for spec in _mass_specs(contract, kind):
        if spec["source"] == "direct":
            values.append(float(spec["dof"]))
        else:
            values.extend([float(spec["dof"])] * len(_list(_dict(spec["matrix"]).get("basis"))))
    return values


def _mass_c_values(contract: dict[str, Any], kind: str) -> list[float]:
    values: list[float] = []
    for spec in _mass_specs(contract, kind):
        if spec["source"] == "direct":
            values.append(float(spec["c"]))
        else:
            values.extend([float(spec["c"])] * len(_list(_dict(spec["matrix"]).get("basis"))))
    return values


def _fermion_dofs(contract: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for spec in _fermion_specs(contract):
        if spec["source"] == "direct":
            values.append(float(spec["dof"]))
        else:
            values.extend([float(spec["dof"])] * len(_list(_dict(spec["matrix"]).get("basis"))))
    return values


def _aligned_thermal_mass_exprs(contract: dict[str, Any], kind: str, *, required: bool) -> list[str]:
    exprs: list[str] = []
    for spec in _aligned_thermal_mass_specs(contract, kind, required=required):
        if spec["source"] != "direct":
            raise CompileBlocked(
                f"PhaseTracer {kind} thermal masses include matrix row {spec['name']!r}; use structured thermal specs instead."
            )
        exprs.append(str(spec["expr"]))
    return exprs


def _aligned_thermal_mass_specs(contract: dict[str, Any], kind: str, *, required: bool) -> list[dict[str, Any]]:
    labels = _mass_labels(contract, kind)
    particles = _daisy_particles(contract, kind)
    if not labels and not particles:
        return []
    by_name = {str(row.get("name", "")): row for row in particles}
    specs: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    for mass_spec in _mass_specs(contract, kind):
        if mass_spec["source"] == "direct":
            name = str(mass_spec["name"])
            row = by_name.get(name)
            if row is None:
                if _thermal_policy_is_same_as_zero_t(str(mass_spec.get("thermal_policy", ""))):
                    specs.append({"source": "direct", "name": name, "expr": str(mass_spec["expr"])})
                    continue
                missing.append(name)
                continue
            expr = _thermal_expr_from_daisy_row(row)
            if not _valid_formula_expr(expr):
                invalid.append(name)
                continue
            specs.append({"source": "direct", "name": name, "expr": expr})
            continue
        base = str(mass_spec["name"])
        matrix = _dict(mass_spec["matrix"])
        size = len(_list(matrix.get("basis")))
        thermal_matrix = _list(matrix.get("thermal_matrix"))
        if str(matrix.get("thermal_matrix_mode", "none")) != "none" and thermal_matrix:
            thermal_spec = dict(matrix)
            thermal_spec["matrix"] = thermal_matrix
            specs.append({"source": "matrix", "name": base, "matrix": thermal_spec})
            continue
        eigen_labels = [f"{base}_eig{index + 1}" for index in range(size)]
        if size and all(label in by_name for label in eigen_labels):
            for label in eigen_labels:
                expr = _thermal_expr_from_daisy_row(by_name[label])
                if not _valid_formula_expr(expr):
                    invalid.append(label)
                    continue
                specs.append({"source": "direct", "name": label, "expr": expr})
            continue
        row = by_name.get(base)
        if row is None:
            missing.extend(eigen_labels or [base])
            continue
        expr = _thermal_expr_from_daisy_row(row)
        if str(expr).strip() == "parwani_replacement_only":
            specs.append({"source": "matrix", "name": base, "matrix": matrix})
            continue
        if size == 1 and _valid_formula_expr(expr):
            specs.append({"source": "direct", "name": f"{base}_eig1", "expr": expr})
            continue
        invalid.append(base)
    if missing and required:
        raise CompileBlocked(
            f"PhaseTracer {_phasetracer_daisy_method(contract)} requires {kind} thermal masses aligned with "
            f"zero-temperature mass labels {labels}; got Daisy rows {[str(row.get('name', '')) for row in particles]}."
        )
    if invalid and required:
        raise CompileBlocked(
            f"PhaseTracer requires reviewed formula thermal_mass_sq entries or parwani_replacement_only matrix rows for "
            f"{kind} Daisy rows: {invalid}."
        )
    if missing or invalid:
        return []
    return specs


def _daisy_particles(contract: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [
        _dict(row)
        for row in _list(_dict(_dict(contract.get("implementation")).get("daisy")).get("particles"))
        if _truthy(_dict(row).get("enabled", True)) and _boson_kind(_dict(row)) == kind
    ]


def _thermal_expr_from_daisy_row(row: dict[str, Any]) -> str:
    expr = str(row.get("thermal_mass_sq", "")).strip()
    if expr == "same_as_zeroT":
        expr = str(row.get("zeroT_mass_sq", "0.0")).strip()
    return expr


def _thermal_policy_is_same_as_zero_t(policy: str) -> bool:
    text = str(policy or "").strip().lower().replace("-", "_")
    return text in {"none", "same_as_zerot", "same_as_zero_t", "zero_t", "zerot", "zero_t_mass", "zerot_mass"} or "no_debye" in text


def _mass_specs_use_temperature(specs: list[dict[str, Any]]) -> bool:
    for spec in specs:
        if spec["source"] == "direct":
            if _expr_mentions_temperature(str(spec.get("expr", ""))):
                return True
            continue
        matrix = _dict(spec["matrix"])
        for row in _list(matrix.get("matrix")):
            if any(_expr_mentions_temperature(str(expr)) for expr in _list(row)):
                return True
    return False


def _expr_mentions_temperature(expr: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])T(?![A-Za-z0-9_])", str(expr or "")) is not None


def _valid_formula_expr(expr: str) -> bool:
    text = str(expr or "").strip()
    return bool(text) and text not in {
        "none",
        "not_applicable",
        "covered_by_unified_V_daisy",
        "covered_by_custom_V_daisy",
        "parwani_replacement_only",
    } and "ASK_USER" not in text


def _requires_debye_alignment(contract: dict[str, Any]) -> bool:
    return _phasetracer_daisy_method(contract) == "Parwani" or _uses_default_arnold_espinosa_daisy(contract)


def _uses_default_arnold_espinosa_daisy(contract: dict[str, Any]) -> bool:
    daisy = _dict(_dict(contract.get("loops")).get("daisy"))
    if str(daisy.get("mode", "none")) != "custom_expr":
        return False
    if _phasetracer_daisy_method(contract) != "ArnoldEspinosa":
        return False
    implementation = _dict(_dict(contract.get("implementation")).get("daisy"))
    policy = _normalize_cubic_policy(str(implementation.get("cubic_power_policy", "")))
    if policy != "positive_part":
        return False
    for kind in ("scalar", "vector"):
        try:
            _aligned_thermal_mass_specs(contract, kind, required=True)
        except CompileBlocked:
            return False
    return True


def _normalize_cubic_policy(value: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"positive", "positive_part", "max0", "max_0", "phase_tracer_default"}:
        return "positive_part"
    if text in {"signed_abs", "signed"}:
        return "signed_abs"
    if text in {"regulated_abs", "abs"}:
        return "regulated_abs"
    if text in {"", "none", "not_applicable"}:
        return text or "not_applicable"
    return text


def _phasetracer_daisy_method(contract: dict[str, Any]) -> str:
    implementation = _dict(_dict(contract.get("implementation")).get("daisy"))
    model_card = _dict(contract.get("model_card"))
    raw = str(implementation.get("scheme") or model_card.get("resummation_scheme") or "None").strip().lower()
    normalized = raw.replace("-", "").replace("_", "").replace(" ", "")
    if "parwani" in normalized:
        return "Parwani"
    if "arnoldespinosa" in normalized:
        return "ArnoldEspinosa"
    return "None"


def _uses_standard_thermal_integrals(contract: dict[str, Any]) -> bool:
    return str(_dict(_dict(contract.get("loops")).get("thermal")).get("mode", "none")) == "standard_thermal_integrals"


def _counterterm_mode(contract: dict[str, Any]) -> str:
    return str(_dict(contract.get("model_card")).get("counterterm", "none")).strip() or "none"


def _render_ct_private_members(contract: dict[str, Any]) -> list[str]:
    if _counterterm_mode(contract) != "explicit_linear_system":
        return []
    return [f"  double {member_name} = 0.0;" for member_name in _explicit_counterterm_spec(contract)["member_names"]]


def _explicit_counterterm_spec(contract: dict[str, Any]) -> dict[str, Any]:
    counterterms = _dict(_dict(contract.get("implementation")).get("counterterms"))
    basis = [_dict(row) for row in _list(counterterms.get("basis"))]
    conditions = [_dict(row) for row in _list(counterterms.get("conditions"))]
    if not basis or not conditions:
        raise CompileBlocked("PhaseTracer explicit_linear_system counterterm needs reviewed Counterterm Basis and Counterterm Conditions rows.")
    if len(basis) != len(conditions):
        raise CompileBlocked(f"PhaseTracer explicit_linear_system counterterm needs a square system, got {len(conditions)} condition(s) and {len(basis)} basis operator(s).")
    field_names = [str(row.get("name", "")) for row in _list(contract.get("fields"))]
    try:
        axes = [_parse_ct_operator(str(row.get("operator", "")), field_names) for row in conditions]
        points = [_parse_ct_point_components(str(row.get("point", "")), len(field_names)) for row in conditions]
    except ValueError as exc:
        raise CompileBlocked(f"PhaseTracer could not parse explicit counterterm system: {exc}") from exc
    raw_coefficients = [
        str(row.get("coefficient", f"ct_{index + 1}")).strip() or f"ct_{index + 1}"
        for index, row in enumerate(basis)
    ]
    member_names: list[str] = []
    seen: dict[str, int] = {}
    for index, raw_name in enumerate(raw_coefficients):
        base = f"ct_coeff_{_program_symbol(raw_name)}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        suffix = "" if count == 0 else f"_{count + 1}"
        member_names.append(f"{base}{suffix}_")
    handlers = [str(row.get("goldstone_handling", "")).strip() for row in conditions]
    return {
        "basis": basis,
        "conditions": conditions,
        "axes": axes,
        "points": points,
        "handlers": handlers,
        "member_names": member_names,
    }


def _parse_ct_operator(operator: str, field_names: list[str]) -> tuple[int, ...]:
    text = re.sub(r"\s+", "", str(operator or ""))
    if text.startswith("d/d"):
        field = text[len("d/d") :]
        return (_field_index(field, field_names),)
    if text.startswith("d2/d"):
        rest = text[len("d2/d") :]
        first = _consume_field_name(rest, field_names)
        remainder = rest[len(first) :]
        if remainder.startswith("d"):
            remainder = remainder[1:]
        if remainder.startswith("^2") or remainder.startswith("2"):
            second = first
        else:
            second = _consume_field_name(remainder, field_names)
        return (_field_index(first, field_names), _field_index(second, field_names))
    raise ValueError(f"Unsupported CT operator {operator!r}")


def _consume_field_name(text: str, field_names: list[str]) -> str:
    for name in sorted(field_names, key=len, reverse=True):
        if text.startswith(name):
            return name
    raise ValueError(f"Could not parse field name from CT operator fragment {text!r}.")


def _field_index(field_name: str, field_names: list[str]) -> int:
    try:
        return field_names.index(field_name)
    except ValueError as exc:
        raise ValueError(f"Unknown CT operator field {field_name!r}; expected one of {field_names!r}.") from exc


def _parse_ct_point_components(point: str, field_count: int) -> list[str]:
    text = str(point or "").strip()
    if (text.startswith("(") and text.endswith(")")) or (text.startswith("[") and text.endswith("]")):
        text = text[1:-1]
    components = _split_top_level_commas(text)
    if len(components) != field_count:
        raise ValueError(f"CT point {point!r} must have {field_count} component(s).")
    return components


def _split_top_level_commas(text: str) -> list[str]:
    components: list[str] = []
    start = 0
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    for index, char in enumerate(text):
        if char in pairs:
            depth += 1
        elif char in closing:
            depth -= 1
            if depth < 0:
                raise ValueError(f"Unbalanced CT point expression {text!r}.")
        elif char == "," and depth == 0:
            component = text[start:index].strip()
            if component:
                components.append(component)
            start = index + 1
    if depth != 0:
        raise ValueError(f"Unbalanced CT point expression {text!r}.")
    tail = text[start:].strip()
    if tail:
        components.append(tail)
    return components


def _smoke_points(contract: dict[str, Any]) -> list[tuple[list[float], float]]:
    zero = _zero_t_vacuum_values(contract)
    if not zero:
        zero = [0.0]
    positive = [value if abs(value) > 1e-12 else 1.0 for value in zero]
    negative = [-value for value in positive]
    return [(zero, 0.0), (positive, 0.0), (positive, 100.0), (negative, 25.0)]


def _zero_t_vacuum_values(contract: dict[str, Any]) -> list[float]:
    return [float(_eval_field_default(row.get("zeroT_default", 0.0), contract)) for row in _list(contract.get("fields"))]


def _eval_field_default(expr: Any, contract: dict[str, Any]) -> float:
    try:
        return float(expr)
    except (TypeError, ValueError):
        env = _parameter_values(contract)
        env.update(_derived_values(contract, env))
        return _eval_compiler_block(str(expr), env)


def _metadata(contract: dict[str, Any], *, template_sha256: str, contract_sha256: str) -> dict[str, Any]:
    params = _dict(contract.get("parameters"))
    return {
        "contract_schema": contract.get("schema", ""),
        "artifact_backend": "phasetracer",
        "paper_id": contract.get("paper_id", ""),
        "model_name": contract.get("model_name", ""),
        "model_short_name": contract.get("model_short_name", ""),
        "template_sha256": template_sha256,
        "contract_sha256": contract_sha256,
        "field_order": [str(row.get("name", "")) for row in _list(contract.get("fields"))],
        "input_defaults": {str(row.get("name", "")): _param_default(row) for row in _list(params.get("public_inputs"))},
        "loop_modes": contract.get("loops", {}),
        "phasetracer_base": "EffectivePotential::OneLoopPotential",
        "daisy_method": _phasetracer_daisy_method(contract),
    }


def _reference_environment(contract: dict[str, Any], phi: list[float], temperature: float) -> dict[str, float]:
    fields = _list(contract.get("fields"))
    env = {str(row.get("name", "")): float(phi[index]) for index, row in enumerate(fields)}
    env["T"] = float(temperature)
    env.update(_parameter_values(contract))
    env.update(_derived_values(contract, env))
    return env


def _parameter_reference_environment(contract: dict[str, Any]) -> dict[str, float]:
    env = _parameter_values(contract)
    env.update(_derived_values(contract, env))
    return env


def _parameter_values(contract: dict[str, Any]) -> dict[str, float]:
    params = _dict(contract.get("parameters"))
    values = {str(row.get("name", "")): float(_param_default(row)) for row in _list(params.get("public_inputs"))}
    values.update({str(row.get("name", "")): float(row.get("value", 0.0)) for row in _list(params.get("constants"))})
    return values


def _derived_values(contract: dict[str, Any], env: dict[str, float]) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in _list(_dict(contract.get("parameters")).get("derived")):
        name = str(row.get("name", ""))
        value = _eval_compiler_block(str(row.get("expr", "0.0")), {**env, **values})
        values[name] = float(value)
    return values


def _eval_compiler_block(expr: str, env: dict[str, float]) -> float:
    text = str(expr or "0.0").strip() or "0.0"
    allowed = {
        "abs": abs,
        "max": max,
        "min": min,
        "pow": pow,
        "pi": math.pi,
        "np": _NumpyScalarShim(),
        "math": _MathScalarShim(),
        **env,
    }
    try:
        tree = ast.parse(text, mode="eval")
        return float(eval(compile(tree, "<ptagent-contract>", "eval"), {"__builtins__": {}}, allowed))
    except SyntaxError:
        module = ast.parse(text, mode="exec")
        local_env = dict(allowed)
        exec(compile(ast.Module(body=module.body[:-1], type_ignores=[]), "<ptagent-contract>", "exec"), {"__builtins__": {}}, local_env)
        final = module.body[-1]
        if isinstance(final, ast.Expr):
            return float(eval(compile(ast.Expression(final.value), "<ptagent-contract>", "eval"), {"__builtins__": {}}, local_env))
        if isinstance(final, ast.Assign) and len(final.targets) == 1 and isinstance(final.targets[0], ast.Name):
            exec(compile(ast.Module(body=[final], type_ignores=[]), "<ptagent-contract>", "exec"), {"__builtins__": {}}, local_env)
            return float(local_env[final.targets[0].id])
        raise CompileBlocked("Compiler block must end with a final expression or simple assignment.")


class _NumpyScalarShim:
    pi = math.pi
    abs = staticmethod(abs)
    arccos = staticmethod(math.acos)
    arcsin = staticmethod(math.asin)
    arctan = staticmethod(math.atan)
    cos = staticmethod(math.cos)
    cosh = staticmethod(math.cosh)
    exp = staticmethod(math.exp)
    log = staticmethod(math.log)
    maximum = staticmethod(max)
    minimum = staticmethod(min)
    power = staticmethod(pow)
    sign = staticmethod(lambda x: -1.0 if x < 0 else (1.0 if x > 0 else 0.0))
    sin = staticmethod(math.sin)
    sinh = staticmethod(math.sinh)
    sqrt = staticmethod(math.sqrt)
    tan = staticmethod(math.tan)
    tanh = staticmethod(math.tanh)
    where = staticmethod(lambda condition, a, b: a if condition else b)
    logical_and = staticmethod(lambda a, b: bool(a) and bool(b))
    logical_or = staticmethod(lambda a, b: bool(a) or bool(b))


class _MathScalarShim(_NumpyScalarShim):
    pass


def _candidate_project_dir(contract: dict[str, Any], settings: Settings, *, output_dir: Path | None = None) -> Path:
    model_root = output_dir or generated_models_dir(settings.artifact_root)
    return model_root / "phasetracer" / model_artifact_slug(contract)


def _param_default(row: dict[str, Any]) -> Any:
    return row.get("default", row.get("test_value", 0.0))


def _float_literal(value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise CompileBlocked(f"PhaseTracer numeric value is not finite: {value!r}")
    return repr(number)


def _cpp_vector_literal(values: list[float]) -> str:
    if not values:
        return "{}"
    return "{" + ", ".join(_float_literal(value) for value in values) + "}"


def _cpp_int_vector_literal(values: tuple[int, ...]) -> str:
    if not values:
        return "{}"
    return "{" + ", ".join(str(int(value)) for value in values) + "}"


def _program_symbol(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]+", "_", str(value or "value")).strip("_")
    if not name:
        return "value"
    if name[0].isdigit():
        name = f"_{name}"
    return name


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_contract(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "on", "enabled"}
