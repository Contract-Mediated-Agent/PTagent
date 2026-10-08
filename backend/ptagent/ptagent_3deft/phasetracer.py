from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..symmetry_analysis import analyze_sign_flip_symmetries
from .contract import ThreeDeftBlocked, ensure_ready, parse_contract, validate_contract_template
from .layout import default_phasetracer_dir
from .mathematica_expr import convert_mathematica_inputform_expression, looks_like_mathematica_inputform

_THREE_D_SCALE_ALIAS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mu_3_scale", ("mu_3", "mu3")),
    ("mu_3_us_scale", ("mu_3_us", "mu_3us", "mu3us", "mu3US")),
)
_THREE_D_US_DYNAMIC_SCALE_NAMES = {
    "mu",
    "mu_3_us",
    "mu_3us",
    "mu3us",
    "mu3US",
    "log_mu",
    "log_mu_3_us",
    "log_mu3US",
    "t",
}
_THREE_D_US_ODE_STEPS_DEFAULT = 64
_MATH_CONSTANTS = {
    "pi": math.pi,
    "EulerGamma": 0.57721566490153286061,
    "Glaisher": 1.28242712910062263688,
}
_EXPRESSION_HELPER_NAMES = {
    "abs",
    "cos",
    "exp",
    "fabs",
    "log",
    "math",
    "max",
    "min",
    "np",
    "pow",
    "sin",
    "sqrt",
    "tan",
    *_MATH_CONSTANTS,
}
_NO_HOOK_VALUES = {"none", "false", "not_applicable", "not applicable", "no", "0"}
_PERTURBATIVE_ORDERS = ("LO", "NLO", "NNLO")
_SYMMETRY_RULE_KEYS = (
    "symmetry_rules",
    "symmetry_rule",
    "symmetry_fields",
    "symmetry_field_groups",
    "z2_reflection_fields",
)


@dataclass(frozen=True)
class PhaseTracerRenderResult:
    output_dir: Path
    header_path: Path
    run_model_path: Path
    cmake_path: Path
    metadata_path: Path
    reference_value: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RenderedExpressionBlock:
    lines: str
    result_name: str
    subexpressions: tuple[dict[str, Any], ...] = ()


def _model_artifact_name(contract: dict[str, Any]) -> str:
    raw = str(contract.get("model_name") or "").strip()
    if _missing(raw):
        raw = "three_deft_model"
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "three_deft_model"
    if cleaned[0].isdigit():
        cleaned = f"model_{cleaned}"
    return cleaned


def _cpp_class_name(artifact_name: str) -> str:
    parts = [part for part in re.split(r"_+", artifact_name) if part]
    class_name = "".join(part[:1].upper() + part[1:] for part in parts)
    return f"{class_name or 'ThreeDeft'}Potential"


def compile_phasetracer_template(
    template_path: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> PhaseTracerRenderResult:
    """Generate a PhaseTracer-compatible project from a 3DEFT contract."""

    template_path = Path(template_path)
    markdown = template_path.read_text(encoding="utf-8")
    report = validate_contract_template(markdown, require_compile_approval=True)
    ensure_ready(report, for_compile=True)
    contract = parse_contract(markdown)

    artifact_name = _model_artifact_name(contract)
    target = Path(output_dir) if output_dir else default_phasetracer_dir(template_path, f"{artifact_name}_phasetracer")
    target.mkdir(parents=True, exist_ok=True)

    render = _render_cpp_project(contract)
    header_path = target / render["header_filename"]
    run_model_path = target / "run_model.cpp"
    cmake_path = target / "CMakeLists.txt"
    metadata_path = target / "metadata.json"
    header_path.write_text(render["header"], encoding="utf-8")
    run_model_path.write_text(render["run_model"], encoding="utf-8")
    cmake_path.write_text(render["cmake"], encoding="utf-8")
    metadata_path.write_text(json.dumps(render["metadata"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    return PhaseTracerRenderResult(
        output_dir=target,
        header_path=header_path,
        run_model_path=run_model_path,
        cmake_path=cmake_path,
        metadata_path=metadata_path,
        reference_value=render["metadata"].get("reference_value_python"),
        warnings=tuple(render["metadata"].get("warnings", [])),
    )


def _render_cpp_project(contract: dict[str, Any]) -> dict[str, Any]:
    artifact_name = _model_artifact_name(contract)
    class_name = _cpp_class_name(artifact_name)
    header_filename = f"{artifact_name}.hpp"
    project_name = f"{artifact_name}_phasetracer"
    inputs = _input_parameters(contract)
    running_inputs = _running_input_parameters(inputs)
    fields = _fields(contract)
    all_coefficients = _ordered_coefficients(contract, _coefficients(contract))
    all_coefficient_names = {coeff["name"] for coeff in all_coefficients}
    all_coefficients = _normalize_coefficient_expressions(all_coefficients, all_coefficient_names)
    consumption = contract.get("three_d_consumption", {})
    phasetracer = contract.get("phasetracer", {})
    interface = _phasetracer_interface(contract)
    rg_matching = contract.get("rg_matching", {})
    runtime_rg = _runtime_rg_enabled(contract)
    beta_functions = _beta_functions(contract, running_inputs) if runtime_rg else {}
    beta_variable = _beta_variable_4d(contract)
    beta_factor = _beta_derivative_factor(beta_variable)
    ode_steps = _ode_steps(contract) if runtime_rg else 0
    input_scale = _common_input_scale(running_inputs) if runtime_rg else None
    running_scale_expression = _running_scale_expression(
        rg_matching.get("four_d_to_three_d_matching_scale", rg_matching.get("running_scale_choice", "T"))
    )
    three_d_scale_expressions = _three_d_scale_alias_expressions(contract)
    for hard_alias in ("mu", "mu4", "mu_4"):
        three_d_scale_expressions.setdefault(hard_alias, running_scale_expression)
    scale_alias_names = set(three_d_scale_expressions)
    field_symbol_map = _field_symbol_map(fields)
    v3d_expression = _clean_expr(str(consumption.get("v3d_expression", "")), field_symbol_map=field_symbol_map)
    prefactor_expression = _clean_expr(str(consumption.get("potential_prefactor", "")), field_symbol_map=field_symbol_map)
    potential_expression = _clean_expr(str(consumption.get("v_phase_tracer_expression", "")), field_symbol_map=field_symbol_map)
    v3d_expression = _normalize_known_coefficient_references(v3d_expression, all_coefficient_names)
    potential_expression = _normalize_known_coefficient_references(potential_expression, all_coefficient_names)
    v3d_terms = _v3d_ordered_terms(consumption, field_symbol_map, all_coefficient_names)
    if v3d_terms:
        v3d_expression = " + ".join(f"({expression})" for _order, expression in v3d_terms)
    three_d_beta_functions_all = {
        parameter: _normalize_known_coefficient_references(expression, all_coefficient_names)
        for parameter, expression in _three_d_beta_functions(contract).items()
    }
    coefficient_seed_names = (
        _expression_names(v3d_expression)
        | _expression_names(potential_expression)
        | set(three_d_beta_functions_all)
        | _expression_names_many(three_d_beta_functions_all.values())
    )
    coefficients = _prune_coefficients_for_potential(all_coefficients, coefficient_seed_names)
    coefficients = _topological_sort_coefficients(coefficients)
    field_scale = _interface_number(interface, "field_scale")
    temperature_scale = _interface_number(interface, "temperature_scale")
    _require_supported_hook(interface, "symmetry_hook", allowed=_NO_HOOK_VALUES | {"z2", "z2_reflection", "z2 reflection"})
    symmetry_policy = _phase_tracer_symmetry_policy(interface, fields)
    low_t_phase_guesses = _phase_tracer_low_t_phase_guesses(interface, fields)

    input_names = set(inputs)
    coefficient_names = {coeff["name"] for coeff in coefficients}
    three_d_beta_functions = {
        name: expression
        for name, expression in three_d_beta_functions_all.items()
        if name in coefficient_names
    }
    three_d_us_running_mode = _three_d_us_running_mode(three_d_beta_functions)
    three_d_us_ode_steps = _three_d_us_ode_steps(contract)
    field_names = {field["name"] for field in fields}
    potential_allowed_names = field_names | coefficient_names | input_names | scale_alias_names | {"T", "pi"}
    coefficient_allowed_names = set(inputs) | scale_alias_names | {"T", "pi"}
    cpp_coeff_blocks: list[str] = []
    cleaned_coefficient_expressions: dict[str, str] = {}
    coefficient_block_metadata: list[dict[str, Any]] = []
    eval_env = {name: spec["test_value"] for name, spec in inputs.items()}
    reference_fields = _numbers(phasetracer.get("reference_fields", ""))
    if len(reference_fields) != len(fields):
        raise ThreeDeftBlocked("reference_fields length must match the reviewed 3D field count.")
    eval_env["T"] = _number(phasetracer.get("reference_temperature"))
    eval_env["pi"] = math.pi
    eval_env.setdefault("xi4", 1.0)
    eval_env.setdefault("xi_4", eval_env["xi4"])
    reference_rg_target_scale = None
    if runtime_rg:
        target_scale = _eval_expr(_clean_expr(running_scale_expression), eval_env)
        reference_rg_target_scale = target_scale
        eval_env.update(
            _run_rg_python(
                running_inputs,
                beta_functions,
                target_scale,
                input_scale=input_scale,
                ode_steps=ode_steps,
                beta_variable=beta_variable,
                beta_factor=beta_factor,
            )
        )
    for spec, value in zip(fields, reference_fields):
        eval_env[spec["name"]] = _field_reference_value(spec, value, eval_env["T"])
    eval_env.update(_eval_scale_aliases(three_d_scale_expressions, eval_env))

    for coeff in coefficients:
        expression = _clean_expr(coeff["expression"], field_symbol_map=field_symbol_map)
        cleaned_coefficient_expressions[coeff["name"]] = expression
        if expression == coeff["name"] and coeff["name"] in inputs:
            block = _RenderedExpressionBlock(lines="", result_name=coeff["name"])
        else:
            block = _render_coefficient_expression_block(
                expression,
                result_name=coeff["name"],
                allowed_names=coefficient_allowed_names,
            )
        cpp_coeff_blocks.append(block.lines)
        for subexpression in block.subexpressions:
            coefficient_block_metadata.append(
                {
                    "coefficient": coeff["name"],
                    **subexpression,
                }
            )
        value = _eval_expr_or_nan(expression, eval_env)
        eval_env[coeff["name"]] = value
        coefficient_allowed_names.add(coeff["name"])
        potential_allowed_names.add(coeff["name"])

    if three_d_beta_functions:
        try:
            eval_env.update(
                _apply_3dus_parameter_running_python(
                    coefficient_names,
                    three_d_beta_functions,
                    eval_env,
                    ode_steps=three_d_us_ode_steps,
                )
            )
        except (OverflowError, ValueError, ZeroDivisionError):
            for name in three_d_beta_functions:
                eval_env[name] = math.nan

    _require_v3d_symbols_are_3d_parameters(v3d_expression, potential_allowed_names)
    _require_v3d_symbols_are_3d_parameters(potential_expression, potential_allowed_names)
    try:
        v3d_block = _render_v3d_expression_block(
            v3d_terms,
            v3d_expression,
            allowed_names=potential_allowed_names,
            field_names=field_names,
        )
        prefactor_block = _render_expression_assignment(
            prefactor_expression,
            result_name="prefactor",
            allowed_names=set(inputs) | scale_alias_names | {"T", "pi"},
            input_names=set(),
        )
        _CppExprRenderer(allowed_names=potential_allowed_names, input_names=set()).render(potential_expression)
    except ValueError as exc:
        raise ThreeDeftBlocked(f"Could not render PhaseTracer V(phi,T): {exc}") from exc
    expected = phasetracer.get("reference_expected_v", "")
    expected_numeric = None if _optional_reference_value_missing(expected) else _number(expected)

    def invalid_reference_result(
        *,
        candidate_env: dict[str, float],
        candidate_fields: list[float],
        candidate_temperature: float,
        candidate_target_scale: float | None,
        reason: str,
        invalid_terms: list[str] | None = None,
        v3d_value: float = math.nan,
        prefactor_value: float = math.nan,
        reference_v: float = math.nan,
        declared_v: float = math.nan,
        term_values: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        return {
            "valid": False,
            "reason": reason,
            "invalid_terms": invalid_terms or [],
            "fields": list(candidate_fields),
            "temperature": float(candidate_temperature),
            "env": candidate_env,
            "rg_target_scale": candidate_target_scale,
            "v3d": v3d_value,
            "prefactor": prefactor_value,
            "reference": reference_v,
            "declared": declared_v,
            "term_values": term_values or {order: None for order, _expression in v3d_terms},
        }

    def evaluate_reference(candidate_fields: list[float], candidate_temperature: float) -> dict[str, Any]:
        candidate_env = {name: spec["test_value"] for name, spec in inputs.items()}
        candidate_env["T"] = float(candidate_temperature)
        candidate_env["pi"] = math.pi
        candidate_env.setdefault("xi4", 1.0)
        candidate_env.setdefault("xi_4", candidate_env["xi4"])
        candidate_target_scale = None
        try:
            if runtime_rg:
                target_scale = _eval_expr(_clean_expr(running_scale_expression), candidate_env)
                if not math.isfinite(target_scale):
                    return invalid_reference_result(
                        candidate_env=candidate_env,
                        candidate_fields=candidate_fields,
                        candidate_temperature=candidate_temperature,
                        candidate_target_scale=target_scale,
                        reason="4D RG target scale is not finite.",
                    )
                candidate_target_scale = target_scale
                candidate_env.update(
                    _run_rg_python(
                        running_inputs,
                        beta_functions,
                        target_scale,
                        input_scale=input_scale,
                        ode_steps=ode_steps,
                        beta_variable=beta_variable,
                        beta_factor=beta_factor,
                    )
                )
            for spec, value in zip(fields, candidate_fields):
                candidate_env[spec["name"]] = _field_reference_value(spec, value, candidate_env["T"])
            candidate_env.update(_eval_scale_aliases(three_d_scale_expressions, candidate_env))
            for coeff in coefficients:
                expression = cleaned_coefficient_expressions[coeff["name"]]
                candidate_env[coeff["name"]] = _eval_expr_or_nan(expression, candidate_env)
            if three_d_beta_functions:
                candidate_env.update(
                    _apply_3dus_parameter_running_python(
                        coefficient_names,
                        three_d_beta_functions,
                        candidate_env,
                        ode_steps=three_d_us_ode_steps,
                    )
                )
        except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
            return invalid_reference_result(
                candidate_env=candidate_env,
                candidate_fields=candidate_fields,
                candidate_temperature=candidate_temperature,
                candidate_target_scale=candidate_target_scale,
                reason=f"Reference parameter evaluation failed: {exc}.",
            )
        v3d_value = _eval_expr_or_nan(v3d_expression, candidate_env)
        prefactor_value = _eval_expr_or_nan(prefactor_expression, candidate_env)
        reference_v = prefactor_value * v3d_value
        declared_v = _eval_expr_or_nan(potential_expression, candidate_env)
        raw_term_values = {
            order: _eval_expr_or_nan(expression, candidate_env)
            for order, expression in v3d_terms
        }
        term_values = {
            order: _metadata_number(value)
            for order, value in raw_term_values.items()
        }
        invalid_terms = [
            f"V3D_{order}"
            for order, value in raw_term_values.items()
            if not math.isfinite(value)
        ]
        invalid_values = [
            name
            for name, value in (
                ("V3D", v3d_value),
                ("prefactor", prefactor_value),
                ("prefactor*V3D", reference_v),
                ("declared V(phi,T)", declared_v),
            )
            if not math.isfinite(value)
        ]
        if invalid_values:
            return invalid_reference_result(
                candidate_env=candidate_env,
                candidate_fields=candidate_fields,
                candidate_temperature=candidate_temperature,
                candidate_target_scale=candidate_target_scale,
                reason=(
                    "Reference candidate produced non-finite or non-real values in "
                    + ", ".join(invalid_values)
                    + ". This usually means a sqrt/log/fractional-power term entered a non-real domain."
                ),
                invalid_terms=invalid_terms,
                v3d_value=v3d_value,
                prefactor_value=prefactor_value,
                reference_v=reference_v,
                declared_v=declared_v,
                term_values=term_values,
            )
        if not _close_enough(reference_v, declared_v):
            return invalid_reference_result(
                candidate_env=candidate_env,
                candidate_fields=candidate_fields,
                candidate_temperature=candidate_temperature,
                candidate_target_scale=candidate_target_scale,
                reason="prefactor*V3D is inconsistent with v_phase_tracer_expression at the reference candidate.",
                invalid_terms=invalid_terms,
                v3d_value=v3d_value,
                prefactor_value=prefactor_value,
                reference_v=reference_v,
                declared_v=declared_v,
                term_values=term_values,
            )
        valid = (
            all(math.isfinite(value) for value in (v3d_value, prefactor_value, reference_v, declared_v))
            and _close_enough(reference_v, declared_v)
        )
        return {
            "valid": valid,
            "reason": "ok",
            "invalid_terms": [],
            "fields": list(candidate_fields),
            "temperature": float(candidate_temperature),
            "env": candidate_env,
            "rg_target_scale": candidate_target_scale,
            "v3d": v3d_value,
            "prefactor": prefactor_value,
            "reference": reference_v,
            "declared": declared_v,
            "term_values": term_values,
        }

    reference_attempts: list[dict[str, Any]] = []
    attempted_reference_keys: set[tuple[tuple[float, ...], float]] = set()

    def record_reference_attempt(candidate_fields: list[float], candidate_temperature: float) -> dict[str, Any]:
        result = evaluate_reference(candidate_fields, candidate_temperature)
        key = (tuple(float(value) for value in candidate_fields), float(candidate_temperature))
        if key not in attempted_reference_keys:
            attempted_reference_keys.add(key)
            reference_attempts.append(
                {
                    "fields": [float(value) for value in candidate_fields],
                    "temperature": float(candidate_temperature),
                    "valid": bool(result["valid"]),
                    "reason": result.get("reason", ""),
                    "invalid_terms": list(result.get("invalid_terms", [])),
                }
            )
        return result

    reference_result = record_reference_attempt(reference_fields, eval_env["T"])
    reference_point_auto_selected = False
    if expected_numeric is None and not reference_result["valid"]:
        for candidate_temperature in _reference_candidate_temperatures(eval_env["T"]):
            for candidate_fields in _reference_candidate_vectors(len(fields), reference_fields):
                candidate_result = record_reference_attempt(candidate_fields, candidate_temperature)
                if candidate_result["valid"]:
                    reference_fields = candidate_fields
                    reference_result = candidate_result
                    reference_point_auto_selected = True
                    break
            if reference_point_auto_selected:
                break
    reference_warning = ""
    if expected_numeric is None and not reference_result["valid"]:
        reference_warning = (
            "No finite real PhaseTracer reference point was found after "
            f"{len(reference_attempts)} candidate(s). The C++ project was still generated, "
            "but reference metadata values are diagnostic only. "
            f"Last failure: {reference_result.get('reason', 'unknown')} "
            f"Invalid potential pieces: {', '.join(reference_result.get('invalid_terms', [])) or 'not isolated'}."
        )
    if expected_numeric is not None and not reference_result["valid"]:
        raise ThreeDeftBlocked(
            "The reviewed PhaseTracer reference point is not finite or has inconsistent V3D/prefactor values, "
            "so it cannot be compared with reference_expected_v."
        )

    eval_env = reference_result["env"]
    reference_rg_target_scale = reference_result["rg_target_scale"]
    v3d_reference_value = reference_result["v3d"]
    v3d_term_reference_values = reference_result["term_values"]
    prefactor_reference_value = reference_result["prefactor"]
    reference_value = reference_result["reference"]
    declared_reference_value = reference_result["declared"]
    if all(math.isfinite(value) for value in (reference_value, declared_reference_value)) and not _close_enough(reference_value, declared_reference_value):
        raise ThreeDeftBlocked(
            "3D Consumption is inconsistent at the reference point: "
            f"potential_prefactor*v3d_expression gives {reference_value}, "
            f"but v_phase_tracer_expression gives {declared_reference_value}."
        )
    warnings = [reference_warning] if reference_warning else []
    fixed_three_d_field_values = [float(eval_env.get(spec["name"], math.nan)) for spec in fields]
    fixed_three_d_parameter_values = [float(eval_env.get(coeff["name"], math.nan)) for coeff in coefficients]
    fixed_three_d_parameter_map = {
        coeff["name"]: _metadata_number(float(eval_env.get(coeff["name"], math.nan)))
        for coeff in coefficients
    }
    fixed_three_d_field_map = {
        spec["name"]: _metadata_number(float(eval_env.get(spec["name"], math.nan)))
        for spec in fields
    }
    fixed_scale_alias_map = {
        name: _metadata_number(float(eval_env.get(name, math.nan)))
        for name in sorted(three_d_scale_expressions)
        if name in eval_env
    }
    fixed_input_value_map = {
        name: _metadata_number(float(eval_env.get(name, math.nan)))
        for name in inputs
        if name in eval_env
    }

    input_struct = _render_input_struct(inputs)
    private_members = "  InputParameters input_;"
    input_initializers = _render_input_initializers(inputs)

    field_unpack = "\n".join(
        _field_cpp_assignment(spec, index)
        for index, spec in enumerate(fields)
    )
    field_uses_temperature = any(
        _normalization_mode(spec.get("normalization", "")) != "identity"
        for spec in fields
    )
    field_void_lines = _render_void_lines(("T", not field_uses_temperature))
    field_struct = _render_named_struct_fields(fields)
    field_return = _render_named_struct_return("ThreeDFields", fields)
    running_struct = _render_running_struct(running_inputs)
    running_body = _render_running_body(
        running_inputs,
        all_inputs=inputs,
        beta_functions=beta_functions,
        runtime_rg=runtime_rg,
        running_scale_expression=running_scale_expression,
        input_scale=input_scale,
        ode_steps=ode_steps,
    )
    beta_method = _render_beta_method(running_inputs, beta_functions, beta_variable=beta_variable, beta_factor=beta_factor) if runtime_rg else ""
    rg_spline = _running_spline_config(interface, input_scale=input_scale, target_scale=reference_rg_target_scale, ode_steps=ode_steps) if runtime_rg else None
    running_helpers = _render_phase_tracer_rg_helpers(running_inputs, rg_spline=rg_spline) if runtime_rg else ""
    matched_used_names = _expression_names_many(cleaned_coefficient_expressions.values())
    v3d_used_names = _expression_names(v3d_expression)
    prefactor_used_names = _expression_names(prefactor_expression)
    matched_running_used_names = matched_used_names | _scale_dependency_names(three_d_scale_expressions, matched_used_names)
    matched_running_alias_block = _render_running_alias_block(inputs, matched_running_used_names, running_inputs=running_inputs)
    matched_scale_alias_block = _render_scale_alias_block(three_d_scale_expressions, matched_used_names, inputs)
    matched_void_lines = _render_void_lines(
        ("T", "T" not in matched_running_used_names and not matched_scale_alias_block and not matched_running_alias_block)
    )
    v3d_field_alias_lines = _render_field_aliases(fields, v3d_used_names)
    v3d_coefficient_alias_lines = _render_coefficient_aliases(coefficients, v3d_used_names)
    v3d_running_used_names = v3d_used_names | _scale_dependency_names(three_d_scale_expressions, v3d_used_names)
    v3d_running_alias_block = _render_running_alias_block(inputs, v3d_running_used_names, running_inputs=running_inputs)
    v3d_scale_alias_block = _render_scale_alias_block(three_d_scale_expressions, v3d_used_names, inputs)
    v3d_void_lines = _render_void_lines(
        ("fields", not (v3d_used_names & field_names)),
        ("params", not (v3d_used_names & coefficient_names)),
        ("T", "T" not in v3d_running_used_names and not v3d_scale_alias_block and not v3d_running_alias_block),
    )
    prefactor_running_used_names = prefactor_used_names | _scale_dependency_names(three_d_scale_expressions, prefactor_used_names)
    prefactor_running_alias_block = _render_running_alias_block(inputs, prefactor_running_used_names, running_inputs=running_inputs)
    prefactor_scale_alias_block = _render_scale_alias_block(three_d_scale_expressions, prefactor_used_names, inputs)
    prefactor_void_lines = _render_void_lines(
        ("T", "T" not in prefactor_running_used_names and not prefactor_scale_alias_block and not prefactor_running_alias_block)
    )
    parameter_struct = _render_parameter_struct(coefficients)
    parameter_return = _render_parameter_return(coefficients)
    symmetry_methods = _render_phase_tracer_symmetry_methods(symmetry_policy)
    low_t_phase_method = _render_phase_tracer_low_t_phases_method(low_t_phase_guesses, field_count=len(fields))
    three_d_us_running_method = _render_3dus_parameter_running_method(
        coefficients,
        inputs,
        running_inputs,
        three_d_scale_expressions,
        three_d_beta_functions,
        mode=three_d_us_running_mode,
        ode_steps=three_d_us_ode_steps,
    )
    raddof = _interface_number_any(interface, "raddof", "radiation_dof", "radiation_degrees_of_freedom")
    minimum_temperature = _interface_number_any(interface, "minimum_temperature", "min_temperature", "t_min", "t_low")
    raddof_method = _render_optional_scalar_override("get_raddof", raddof)
    minimum_temperature_method = _render_optional_scalar_override("get_minimum_temperature", minimum_temperature)
    scale_setters = "\n".join(
        line
        for line in (
            f"    set_field_scale({_cpp_float(field_scale)});" if field_scale is not None else "",
            f"    set_temperature_scale({_cpp_float(temperature_scale)});" if temperature_scale is not None else "",
            "    initialise_phase_tracer_rg();" if runtime_rg else "",
        )
        if line
    )
    constructor_body = f" {{\n{scale_setters}\n  }}" if scale_setters else " {}"
    class_comment = (
        "  // This class evaluates the reviewed total 3D EFT potential.\n"
        "  // The field map, parameter evaluation, and V3D-to-V(phi,T) prefactor\n"
        "  // are recorded in the contract and implemented below."
    )
    header = f"""#pragma once

#include <algorithm>
#include <cstddef>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

#include <eigen3/Eigen/Core>
#include "effectivepotential/potential.hpp"

namespace ptagent_3deft {{

class {class_name} final : public EffectivePotential::Potential {{
 public:
  struct InputParameters {{
{input_struct}
  }};

  explicit {class_name}(InputParameters input) : input_(input){constructor_body}

{class_comment}
  std::size_t get_n_scalars() const override {{
    return {len(fields)};
  }}

  struct RunningParameters {{
{running_struct}
  }};

  struct ThreeDParameters {{
{parameter_struct}
  }};

  struct ThreeDFields {{
{field_struct}
  }};

  RunningParameters get_running_parameters(double T) const {{
{running_body}
  }}

{beta_method}
{running_helpers}

  // Matched 3D parameters before optional ultrasoft running.
  ThreeDParameters get_matched_3d_parameters(double T) const {{
{matched_void_lines}
{matched_running_alias_block}
{matched_scale_alias_block}
{chr(10).join(cpp_coeff_blocks)}
{parameter_return}
  }}

{three_d_us_running_method}

  // Final 3D parameters consumed by the potential.
  ThreeDParameters get_3d_parameters(double T) const {{
    const ThreeDParameters matched = get_matched_3d_parameters(T);
    return apply_3dus_parameter_running(matched, T);
  }}

  ThreeDFields get_3d_fields(Eigen::VectorXd phase_tracer_fields, double T) const {{
    check_field_count(phase_tracer_fields);
{field_void_lines}
{field_unpack}
{field_return}
  }}

  // Total reviewed V3D in canonical 3D fields.
  double evaluate_v3d(const ThreeDFields& fields, const ThreeDParameters& params, double T) const {{
{v3d_void_lines}
{v3d_field_alias_lines}
{v3d_coefficient_alias_lines}
{v3d_running_alias_block}
{v3d_scale_alias_block}
{v3d_block.lines}
    return {v3d_block.result_name};
  }}

  double potential_prefactor(double T) const {{
{prefactor_void_lines}
{prefactor_running_alias_block}
{prefactor_scale_alias_block}
{prefactor_block.lines}
    return {prefactor_block.result_name};
  }}

  double V(Eigen::VectorXd phase_tracer_fields, double T) const override {{
    const ThreeDFields fields = get_3d_fields(phase_tracer_fields, T);
    const ThreeDParameters params = get_3d_parameters(T);
    const double v3d = evaluate_v3d(fields, params, T);
    return potential_prefactor(T) * v3d;
  }}

{symmetry_methods}

{low_t_phase_method}

{raddof_method}
{minimum_temperature_method}

 private:
  static constexpr double kPi = 3.14159265358979323846;
  static constexpr double kEulerGamma = 0.57721566490153286061;
  static constexpr double kGlaisher = 1.28242712910062263688;
  static constexpr double kTemperatureFloor = 1e-15;

  static double sqr(double value) {{
    return value * value;
  }}

  static double cube(double value) {{
    return value * value * value;
  }}

  void check_field_count(const Eigen::VectorXd& phase_tracer_fields) const {{
    if (static_cast<std::size_t>(phase_tracer_fields.size()) != get_n_scalars()) {{
      throw std::invalid_argument("{class_name} expected {len(fields)} scalar field component(s).");
    }}
  }}

{private_members}
}};

}}  // namespace ptagent_3deft
"""
    reference_field_assignments = "\n".join(
        f"  phi[{index}] = {_cpp_float(value)};"
        for index, value in enumerate(reference_fields)
    )
    reference_temperature = _cpp_float(eval_env["T"])
    direct_field_initializers = ", ".join(_cpp_float(value) for value in fixed_three_d_field_values)
    direct_parameter_initializers = ", ".join(_cpp_float(value) for value in fixed_three_d_parameter_values)
    direct_field_print = "".join(
        f' << " {spec["name"]}=" << fields.{spec["name"]}'
        for spec in fields
    )
    direct_parameter_print = "".join(
        f' << " {coeff["name"]}=" << params.{coeff["name"]}'
        for coeff in coefficients
    )
    expected_v3d = _cpp_float(v3d_reference_value)
    expected_prefactor = _cpp_float(prefactor_reference_value)
    expected_reference_value = _cpp_float(reference_value)
    run_model = f"""#include "{header_filename}"

#include <cstdlib>
#include <iomanip>
#include <iostream>

int main() {{
  ptagent_3deft::{class_name}::InputParameters input{{}};
{input_initializers}
  ptagent_3deft::{class_name} model(input);

  ptagent_3deft::{class_name}::ThreeDFields fields{{{direct_field_initializers}}};
  ptagent_3deft::{class_name}::ThreeDParameters params{{{direct_parameter_initializers}}};
  const double T = {reference_temperature};
  const double direct_v3d = model.evaluate_v3d(fields, params, T);
  const double direct_prefactor = model.potential_prefactor(T);
  const double direct_value = direct_prefactor * direct_v3d;
  const double expected_v3d = {expected_v3d};
  const double expected_prefactor = {expected_prefactor};
  const double expected_value = {expected_reference_value};

  Eigen::VectorXd phi({len(fields)});
{reference_field_assignments}
  const double full_path_value = model.V(phi, T);
  std::cout << std::setprecision(17);
  std::cout << "Reference.mode fixed_3d_parameters_direct_v3d\\n";
  std::cout << "Reference.T " << T << "\\n";
  std::cout << "Reference.fields_3d"{direct_field_print} << "\\n";
  std::cout << "Reference.params_3d"{direct_parameter_print} << "\\n";
  std::cout << "Reference.DirectV3D " << direct_v3d << "\\n";
  std::cout << "Reference.ExpectedV3D " << expected_v3d << "\\n";
  std::cout << "Reference.DiffV3D " << (direct_v3d - expected_v3d) << "\\n";
  std::cout << "Reference.DirectPrefactor " << direct_prefactor << "\\n";
  std::cout << "Reference.ExpectedPrefactor " << expected_prefactor << "\\n";
  std::cout << "Reference.V " << direct_value << "\\n";
  std::cout << "Reference.ExpectedV " << expected_value << "\\n";
  std::cout << "Reference.DiffV " << (direct_value - expected_value) << "\\n";
  std::cout << "Reference.phase_tracer_fields";
  for (Eigen::Index i = 0; i < phi.size(); ++i) {{
    std::cout << " " << phi[i];
  }}
  std::cout << "\\n";
  std::cout << "Reference.FullPathVDiagnostic " << full_path_value << "\\n";
  return EXIT_SUCCESS;
}}
"""
    if expected_numeric is not None and not _close_enough(reference_value, expected_numeric):
        raise ThreeDeftBlocked(
            "Reference V(phi,T) does not agree with the reviewed expected value: "
            f"computed {reference_value}, expected {expected_numeric}."
        )
    cmake = f"""cmake_minimum_required(VERSION 3.16)
project({project_name} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(PHASETRACER_ROOT "" CACHE PATH "Path to the PhaseTracer source tree")
if(NOT PHASETRACER_ROOT)
  message(FATAL_ERROR "Set -DPHASETRACER_ROOT=/path/to/PhaseTracer")
endif()

set(PHASETRACER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(PHASETRACER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${{PHASETRACER_ROOT}} ${{CMAKE_BINARY_DIR}}/PhaseTracer EXCLUDE_FROM_ALL)

add_executable(run_model run_model.cpp)
target_include_directories(run_model PRIVATE
  ${{CMAKE_CURRENT_SOURCE_DIR}}
  ${{PHASETRACER_ROOT}}/EffectivePotential/include
  ${{PHASETRACER_ROOT}}/EffectivePotential/include/effectivepotential
  ${{PHASETRACER_ROOT}}/include
)
target_link_libraries(run_model PRIVATE phasetracer effectivepotential)
"""
    metadata = {
        "schema": "ptagent.3deft.phasetracer_project.v1",
        "model_name": contract.get("model_name", "three_deft_model"),
        "artifact_name": artifact_name,
        "cpp_class": class_name,
        "header": header_filename,
        "backend": "phasetracer",
        "phasetracer_base": "EffectivePotential::Potential",
        "requires_phasetracer_root": True,
        "potential_mode": "external_total_3deft",
        "evaluation_policy": "reviewed_total_3d_eft_potential",
        "input_parameters": _input_parameters_for_metadata(inputs),
        "fields": fields,
        "symbol_audit": contract.get("symbol_audit", []),
        "phasetracer_interface": contract.get("phasetracer_interface", []),
        "phase_tracer_symmetry": symmetry_policy,
        "candidate_symmetry_from_v3d": _candidate_z2_symmetries_from_expression(
            v3d_expression,
            [field["name"] for field in fields],
        ),
        "low_t_phase_guesses": low_t_phase_guesses,
        "field_scale": field_scale,
        "temperature_scale": temperature_scale,
        "coefficient_names": [coeff["name"] for coeff in coefficients],
        "coefficient_expression_blocks": [
            {
                **subexpression,
                "reference_value_python": _metadata_number(_eval_expr_or_nan(subexpression["expression"], eval_env)),
            }
            for subexpression in coefficient_block_metadata
        ],
        "coefficient_order_sums": contract.get("three_d_coefficient_order_sums", []),
        "parameter_evaluator": contract.get("three_d_parameter_evaluator", []),
        "runtime_rg_running": runtime_rg,
        "phase_tracer_native_rg": runtime_rg,
        "rg_initial_scale": input_scale,
        "rg_target_scale_expression": running_scale_expression,
        "rg_spline_min_scale": rg_spline["min_scale"] if rg_spline else None,
        "rg_spline_max_scale": rg_spline["max_scale"] if rg_spline else None,
        "rg_spline_step": rg_spline["step"] if rg_spline else None,
        "rg_ode_solver": "rk4" if runtime_rg else "none",
        "rg_ode_steps": ode_steps,
        "rg_beta_function_variable": beta_variable if runtime_rg else "none",
        "rg_beta_derivative_factor": beta_factor if runtime_rg else 1.0,
        "rg_beta_dependent_variable_policy": rg_matching.get("four_d_beta_dependent_variable_policy", "dralgo_native") if runtime_rg else "none",
        "rg_beta_functions": beta_functions,
        "three_d_scale_substitutions": three_d_scale_expressions,
        "three_d_us_parameter_running": {
            "mode": three_d_us_running_mode,
            "ode_steps": three_d_us_ode_steps if three_d_us_running_mode == "rk4_log_mu" else None,
            "initial_scale": "mu_3_scale",
            "target_scale": "mu_3_us_scale",
            "initial_values": "get_matched_3d_parameters(T)",
            "beta_variable": "log_mu_3_us",
            "beta_functions": three_d_beta_functions,
        },
        "three_d_beta_functions": contract.get("three_d_beta_functions", []),
        "running_policy": _parameter_evaluator(contract),
        "v3d_expression": v3d_expression,
        "v3d_ordered_terms": [
            {"order": order, "expression": expression, "reference_value_python": v3d_term_reference_values.get(order)}
            for order, expression in v3d_terms
        ],
        "v3d_expression_blocks": [
            {
                **subexpression,
                "reference_value_python": _metadata_number(_eval_expr_or_nan(subexpression["expression"], eval_env)),
            }
            for subexpression in v3d_block.subexpressions
        ],
        "potential_prefactor_expression": prefactor_expression,
        "potential_expression": potential_expression,
        "declared_phase_tracer_expression_value": _metadata_number(declared_reference_value),
        "reference_validation_mode": "fixed_3d_parameters_direct_v3d",
        "reference_comparison_layer": "evaluate_v3d_with_frozen_3d_fields_and_parameters",
        "fixed_3d_reference": {
            "temperature": _metadata_number(eval_env["T"]),
            "fields_3d": fixed_three_d_field_map,
            "parameters_3d": fixed_three_d_parameter_map,
            "scale_aliases": fixed_scale_alias_map,
            "input_values": fixed_input_value_map,
            "v3d_value_python": _metadata_number(v3d_reference_value),
            "prefactor_value_python": _metadata_number(prefactor_reference_value),
            "value_python": _metadata_number(reference_value),
            "ordered_term_values_python": v3d_term_reference_values,
            "notes": (
                "Use these frozen 3D fields and 3D parameters for Mathematica/C++ "
                "expression-conversion checks. This intentionally bypasses the runtime "
                "4D RG, 4D-to-3D matching, and 3DUS-running path."
            ),
        },
        "full_pipeline_reference_value_python": _metadata_number(reference_value),
        "full_pipeline_reference_is_diagnostic": True,
        "v3d_reference_value_python": _metadata_number(v3d_reference_value),
        "prefactor_reference_value_python": _metadata_number(prefactor_reference_value),
        "reference_value_python": _metadata_number(reference_value),
        "reference_point_valid": bool(reference_result["valid"]),
        "reference_point_warning": reference_warning or None,
        "reference_candidate_attempt_count": len(reference_attempts),
        "reference_candidate_attempts": reference_attempts,
        "reference_invalid_terms": list(reference_result.get("invalid_terms", [])),
        "warnings": warnings,
        "reference_point_auto_selected": reference_point_auto_selected,
        "reference_fields": reference_fields,
        "reference_temperature": eval_env["T"],
    }
    return {
        "header_filename": header_filename,
        "header": header,
        "run_model": run_model,
        "cmake": cmake,
        "metadata": metadata,
    }


def _input_parameters(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in contract.get("input_parameters", []):
        name = str(row.get("name", "")).strip()
        if not _identifier(name):
            continue
        metadata = {
            "latex": row.get("latex", ""),
            "test_value": _number(row.get("test_value")),
            "input_scale": row.get("input_scale", ""),
            "source": row.get("source", ""),
            "notes": row.get("notes", ""),
        }
        result[name] = metadata
    return result


def _running_input_parameters(inputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: spec
        for name, spec in inputs.items()
        if not _is_scale_control_input(name, spec)
    }


def _is_scale_control_input(name: str, spec: dict[str, Any]) -> bool:
    normalized = name.strip().casefold()
    if normalized in {"xi4", "xi_4"}:
        return True
    evidence = " ".join(str(spec.get(key, "")) for key in ("source", "notes", "latex")).casefold()
    return "scale_policy" in evidence or "scale-control" in evidence or "matching scale" in evidence


def _input_parameters_for_metadata(inputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, spec in inputs.items():
        result[name] = {
            "latex": spec.get("latex", ""),
            "test_value": spec.get("test_value"),
            "input_scale_GeV": spec.get("input_scale", ""),
        }
    return result


def _fields(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(contract.get("three_d_fields", []))
    rows.sort(key=lambda row: _slot_key(row.get("phase_tracer_slot", "")))
    result: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not _identifier(name):
            continue
        result.append(
            {
                "name": name,
                "source_scalar_indices": row.get("source_scalar_indices", ""),
                "dralgo_background_symbol": row.get("dralgo_background_symbol", ""),
                "normalization": row.get("normalization", ""),
                "phase_tracer_slot": row.get("phase_tracer_slot", ""),
            }
        )
    return result


def _field_symbol_map(fields: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in fields:
        symbol = str(spec.get("dralgo_background_symbol", "")).strip()
        if symbol and not _missing(symbol):
            result[symbol] = spec["name"]
    return result


def _field_cpp_assignment(spec: dict[str, Any], index: int) -> str:
    name = spec["name"]
    source = f"phase_tracer_fields(static_cast<Eigen::Index>({index}))"
    mode = _normalization_mode(spec.get("normalization", ""))
    if mode == "identity":
        expression = source
    elif mode == "phase_tracer_over_sqrt_t":
        expression = f"({source} / std::sqrt(T + kTemperatureFloor))"
    elif mode == "phase_tracer_times_sqrt_t":
        expression = f"({source} * std::sqrt(T + kTemperatureFloor))"
    else:
        raise ThreeDeftBlocked(
            f"Unsupported 3D field normalization {spec.get('normalization')!r} for field {name!r}. "
            "Use canonical_3d/phase_tracer/sqrt(T), explicit identity, or phase_tracer*sqrt(T), "
            "or extend the independent 3DEFT renderer."
        )
    return f"    const double {name} = {expression};"


def _field_reference_value(spec: dict[str, Any], phase_tracer_value: float, temperature: float) -> float:
    mode = _normalization_mode(spec.get("normalization", ""))
    if mode == "identity":
        return phase_tracer_value
    if mode == "phase_tracer_over_sqrt_t":
        return phase_tracer_value / math.sqrt(temperature + 1e-15)
    if mode == "phase_tracer_times_sqrt_t":
        return phase_tracer_value * math.sqrt(temperature + 1e-15)
    raise ThreeDeftBlocked(f"Unsupported 3D field normalization {spec.get('normalization')!r}.")


def _normalization_mode(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")
    if normalized in {"identity", "none", "phase_tracer", "phasetracer"}:
        return "identity"
    if normalized in {
        "",
        "canonical",
        "canonical_3d",
        "phase_tracer_sqrt_t",
        "phase_tracer_over_sqrt_t",
        "phasetracer_sqrt_t",
        "phasetracer_over_sqrt_t",
        "pt_over_sqrt_t",
        "phi_over_sqrt_t",
        "1_sqrt_t",
    }:
        return "phase_tracer_over_sqrt_t"
    if normalized in {
        "phase_tracer_times_sqrt_t",
        "phasetracer_times_sqrt_t",
        "pt_times_sqrt_t",
        "sqrt_t_phase_tracer",
        "sqrt_t_phasetracer",
    }:
        return "phase_tracer_times_sqrt_t"
    return "unsupported"


def _phasetracer_interface(contract: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in contract.get("phasetracer_interface", []):
        key = str(row.get("key", "")).strip()
        value = str(row.get("value", "")).strip()
        if key:
            if key in result and key in _SYMMETRY_RULE_KEYS:
                result[key] = f"{result[key]}; {value}"
            else:
                result[key] = value
    return result


def _hook_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _phase_tracer_symmetry_policy(interface: dict[str, str], fields: list[dict[str, Any]]) -> dict[str, Any]:
    raw_mode = str(interface.get("symmetry_hook", "")).strip()
    mode = _hook_token(raw_mode)
    if _missing(raw_mode) or mode in {_hook_token(value) for value in _NO_HOOK_VALUES}:
        return {"mode": "none", "rules": []}
    if mode not in {"z2", "z2_reflection"}:
        raise ThreeDeftBlocked(
            f"PhaseTracer symmetry_hook={raw_mode!r} is not supported by the independent 3DEFT renderer. "
            "Supported values are none and z2_reflection."
        )
    raw_rules = _interface_text_any(interface, *_SYMMETRY_RULE_KEYS)
    if _missing(raw_rules) and ":" in raw_mode:
        raw_rules = raw_mode.split(":", 1)[1]
    groups = _split_symmetry_groups(raw_rules)
    if not groups:
        raise ThreeDeftBlocked(
            "PhaseTracer symmetry_hook=z2_reflection requires a reviewed symmetry_rules row, "
            "for example `s` or `phi,s` for simultaneous sign flips."
        )
    field_slots = {field["name"]: index for index, field in enumerate(fields)}
    rules: list[dict[str, Any]] = []
    for group in groups:
        slots: list[int] = []
        for field_name in group:
            if field_name not in field_slots:
                raise ThreeDeftBlocked(
                    f"PhaseTracer symmetry field {field_name!r} is not in the reviewed 3D field order "
                    f"{list(field_slots)}."
                )
            slot = field_slots[field_name]
            if slot not in slots:
                slots.append(slot)
        if slots:
            rules.append({"fields": [fields[slot]["name"] for slot in slots], "slots": slots})
    if not rules:
        raise ThreeDeftBlocked("PhaseTracer symmetry_hook=z2_reflection did not contain any non-empty field groups.")
    return {"mode": "z2_reflection", "rules": rules}


def _phase_tracer_low_t_phase_guesses(interface: dict[str, str], fields: list[dict[str, Any]]) -> list[list[float]]:
    raw = str(interface.get("low_t_phase_guesses", "")).strip()
    if _missing(raw) or _hook_token(raw) in {_hook_token(value) for value in _NO_HOOK_VALUES}:
        return []
    guesses: list[list[float]] = []
    for group in re.split(r"[;|]", raw):
        cleaned = group.strip().strip("[](){}")
        if not cleaned:
            continue
        if "=" in cleaned:
            cleaned = cleaned.split("=", 1)[1].strip()
        values = [item for item in re.split(r"[,\s]+", cleaned) if item]
        if len(values) != len(fields):
            raise ThreeDeftBlocked(
                "PhaseTracer low_t_phase_guesses must be numeric vectors with one value per reviewed 3D field. "
                f"Expected {len(fields)} entries in {cleaned!r}."
            )
        guesses.append([_number(value) for value in values])
    if not guesses:
        raise ThreeDeftBlocked("PhaseTracer low_t_phase_guesses is non-empty but no numeric vector was parsed.")
    return guesses


def _interface_text_any(interface: dict[str, str], *keys: str) -> str:
    for key in keys:
        raw = str(interface.get(key, "")).strip()
        if not _missing(raw):
            return raw
    return ""


def _split_symmetry_groups(raw: Any) -> list[list[str]]:
    text = str(raw or "").strip().strip("`")
    if _missing(text) or _hook_token(text) in {_hook_token(value) for value in _NO_HOOK_VALUES}:
        return []
    groups: list[list[str]] = []
    for group_text in re.split(r"[;|]", text):
        cleaned = group_text.strip().strip("[](){}")
        if not cleaned:
            continue
        if "=" in cleaned:
            cleaned = cleaned.split("=", 1)[1].strip()
        fields = [field.strip() for field in re.split(r"[,\s]+", cleaned) if field.strip()]
        if fields:
            groups.append(fields)
    return groups


def _render_phase_tracer_symmetry_methods(policy: dict[str, Any]) -> str:
    mode = str(policy.get("mode", "none"))
    if mode == "none":
        return "\n".join(
            [
                "  std::vector<Eigen::VectorXd> apply_symmetry(Eigen::VectorXd phase_tracer_fields) const override {",
                "    check_field_count(phase_tracer_fields);",
                "    return {};",
                "  }",
                "",
                "  std::vector<std::vector<int>> get_symmetry_axes() const override {",
                "    return {};",
                "  }",
            ]
        )
    if mode != "z2_reflection":
        raise ThreeDeftBlocked(f"PhaseTracer symmetry mode {mode!r} is not supported.")
    lines = [
        "  std::vector<Eigen::VectorXd> apply_symmetry(Eigen::VectorXd phase_tracer_fields) const override {",
        "    check_field_count(phase_tracer_fields);",
        "    std::vector<Eigen::VectorXd> partners;",
    ]
    for index, rule in enumerate(policy.get("rules", [])):
        lines.append(f"    Eigen::VectorXd partner_{index} = phase_tracer_fields;")
        for slot in rule.get("slots", []):
            lines.append(f"    partner_{index}(static_cast<Eigen::Index>({int(slot)})) *= -1.0;")
        lines.append(f"    partners.push_back(partner_{index});")
    lines.extend(["    return partners;", "  }", "", "  std::vector<std::vector<int>> get_symmetry_axes() const override {"])
    rule_literals = [
        "{" + ", ".join(str(int(slot)) for slot in rule.get("slots", [])) + "}"
        for rule in policy.get("rules", [])
        if rule.get("slots")
    ]
    if rule_literals:
        lines.append("    return {" + ", ".join(rule_literals) + "};")
    else:
        lines.append("    return {};")
    lines.append("  }")
    return "\n".join(lines)


def _render_phase_tracer_low_t_phases_method(guesses: list[list[float]], *, field_count: int) -> str:
    if not guesses:
        return "\n".join(
            [
                "  std::vector<Eigen::VectorXd> get_low_t_phases() const override {",
                "    return {};",
                "  }",
            ]
        )
    lines = [
        "  std::vector<Eigen::VectorXd> get_low_t_phases() const override {",
        "    std::vector<Eigen::VectorXd> phases;",
    ]
    for index, guess in enumerate(guesses):
        if len(guess) != field_count:
            raise ThreeDeftBlocked("Internal low-T phase guess length mismatch.")
        values = ", ".join(_cpp_float(value) for value in guess)
        lines.extend(
            [
                f"    Eigen::VectorXd phase_{index}({field_count});",
                f"    phase_{index} << {values};",
                f"    phases.push_back(phase_{index});",
            ]
        )
    lines.extend(["    return phases;", "  }"])
    return "\n".join(lines)


def _parameter_evaluator(contract: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in contract.get("three_d_parameter_evaluator", []):
        key = str(row.get("key", "")).strip()
        value = str(row.get("value", "")).strip()
        if key:
            result[key] = value
    return result


def _three_d_scale_alias_expressions(contract: dict[str, Any]) -> dict[str, str]:
    policy = _parameter_evaluator(contract)
    result: dict[str, str] = {}
    for key, aliases in _THREE_D_SCALE_ALIAS_GROUPS:
        raw = str(policy.get(key, "")).strip().strip("`")
        if "=" in raw:
            raw = raw.split("=", 1)[1].strip()
        if _missing(raw) or raw.casefold() in {"none", "not_applicable", "not applicable", "false", "no", "0"}:
            continue
        expression = _clean_expr(raw)
        for alias in aliases:
            result[alias] = expression
    input_names = set(_input_parameters(contract))
    if "xi4" not in input_names:
        result.setdefault("xi4", "1")
    if "xi_4" not in input_names:
        result.setdefault("xi_4", "xi4")
    xi4_expression = "xi4" if "xi4" in input_names else "1"
    if "Lb" not in input_names:
        result.setdefault("Lb", f"2*log({xi4_expression})")
    if "Lf" not in input_names:
        result.setdefault("Lf", f"2*log({xi4_expression}) + 4*log(2)")
    return result


def _scale_aliases_needed(scale_expressions: dict[str, str], used_names: set[str]) -> list[str]:
    needed = set(used_names) & set(scale_expressions)
    changed = True
    while changed:
        changed = False
        for alias in list(needed):
            dependencies = _expression_names(scale_expressions[alias]) & set(scale_expressions)
            new_dependencies = dependencies - needed
            if new_dependencies:
                needed.update(new_dependencies)
                changed = True
    return [alias for alias in scale_expressions if alias in needed]


def _scale_dependency_names(scale_expressions: dict[str, str], used_names: set[str]) -> set[str]:
    dependencies: set[str] = set()
    for alias in _scale_aliases_needed(scale_expressions, used_names):
        dependencies.update(_expression_names(scale_expressions[alias]) - _EXPRESSION_HELPER_NAMES)
    return dependencies - set(scale_expressions)


def _render_scale_alias_block(
    scale_expressions: dict[str, str],
    used_names: set[str],
    inputs: dict[str, dict[str, Any]],
) -> str:
    lines: list[str] = []
    defined_aliases: set[str] = set()
    input_names = set(inputs)
    for alias in _scale_aliases_needed(scale_expressions, used_names):
        expression = scale_expressions[alias]
        rendered = _CppExprRenderer(
            allowed_names=input_names | defined_aliases | {"T", "pi"},
            input_names=set(),
        ).render(expression)
        lines.append(f"    const double {alias} = {rendered};")
        defined_aliases.add(alias)
    return "\n".join(lines)


def _eval_scale_aliases(scale_expressions: dict[str, str], env: dict[str, float]) -> dict[str, float]:
    values: dict[str, float] = {}
    for alias, expression in scale_expressions.items():
        values[alias] = _eval_expr(expression, {**env, **values})
    return values


def _runtime_rg_enabled(contract: dict[str, Any]) -> bool:
    raw = str(contract.get("rg_matching", {}).get("runtime_rg_running", "")).strip()
    if raw and not _missing(raw):
        return raw.casefold() in {"true", "yes", "1", "enabled", "reviewed"}
    return bool(_beta_functions(contract))


def _beta_variable_4d(contract: dict[str, Any]) -> str:
    raw = str(contract.get("rg_matching", {}).get("beta_function_variable", "log_mu")).strip().casefold()
    return _canonical_beta_variable(raw, three_d=False)


def _canonical_beta_variable(value: str, *, three_d: bool) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    squared = normalized in {"log_mu_squared", "ln_mu_squared", "logmu_squared", "log_mu2", "ln_mu2", "logmu2"}
    if three_d:
        if normalized in {"log_mu3", "ln_mu3", "logmu3", "log_mu", "ln_mu", "logmu"}:
            return "log_mu3"
        if normalized in {"log_mu3_squared", "ln_mu3_squared", "logmu3_squared", "log_mu32", "ln_mu32", "logmu32"} or squared:
            return "log_mu3_squared"
        return "log_mu3"
    if normalized in {"log_mu", "ln_mu", "logmu"}:
        return "log_mu"
    if squared:
        return "log_mu_squared"
    return "log_mu"


def _beta_derivative_factor(beta_variable: str) -> float:
    return 2.0 if beta_variable.endswith("_squared") else 1.0


def _beta_t_cpp(beta_variable: str, log_name: str) -> str:
    return f"(2.0 * {log_name})" if beta_variable.endswith("_squared") else log_name


def _beta_t_python(beta_variable: str, log_value: float) -> float:
    return 2.0 * log_value if beta_variable.endswith("_squared") else log_value


def _ode_steps(contract: dict[str, Any]) -> int:
    raw = str(contract.get("rg_matching", {}).get("ode_steps", "64")).strip()
    if _missing(raw):
        raw = "64"
    try:
        steps = int(raw)
    except ValueError as exc:
        raise ThreeDeftBlocked(f"4D running needs integer ode_steps, got {raw!r}.") from exc
    if steps <= 0:
        raise ThreeDeftBlocked("4D running needs positive ode_steps.")
    return steps


def _beta_functions(contract: dict[str, Any], inputs: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in contract.get("rg_beta_functions", []):
        parameter = str(row.get("parameter", "")).strip()
        expression = str(row.get("beta_expression", "")).strip()
        if parameter and expression and _identifier(parameter) and not _missing(expression):
            result[parameter] = _clean_expr(expression)
    input_names = set(_input_parameters(contract) if inputs is None else inputs)
    converted: dict[str, str] = {}
    for name in input_names:
        if name in result:
            converted[name] = result[name]
            continue
        squared_name = f"{name}_sq"
        compact_squared_name = f"{name}sq"
        if squared_name in result:
            converted[name] = f"({result[squared_name]})/(2*{name})"
        elif compact_squared_name in result:
            converted[name] = f"({result[compact_squared_name]})/(2*{name})"
    for name, expression in result.items():
        if name not in converted and name in input_names:
            converted[name] = expression
    return converted


def _three_d_beta_functions(contract: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in contract.get("three_d_beta_functions", []):
        parameter = str(row.get("parameter", "")).strip()
        expression = str(row.get("beta_expression", "")).strip()
        if parameter and expression and _identifier(parameter) and not _missing(expression):
            result[parameter] = _clean_expr(expression)
    return result


def _three_d_us_running_mode(beta_functions: dict[str, str]) -> str:
    if not beta_functions:
        return "none"
    return "rk4_log_mu" if _three_d_us_needs_numeric_running(beta_functions) else "analytic_log"


def _three_d_us_needs_numeric_running(beta_functions: dict[str, str]) -> bool:
    running_names = set(beta_functions)
    for expression in beta_functions.values():
        names = _expression_names(expression)
        if names & running_names:
            return True
        if names & _THREE_D_US_DYNAMIC_SCALE_NAMES:
            return True
    return False


def _three_d_us_ode_steps(contract: dict[str, Any]) -> int:
    raw = str(contract.get("rg_matching", {}).get("ode_steps", "")).strip()
    if _missing(raw):
        return _THREE_D_US_ODE_STEPS_DEFAULT
    try:
        steps = int(raw)
    except ValueError as exc:
        raise ThreeDeftBlocked(f"3DUS parameter running needs integer ode_steps, got {raw!r}.") from exc
    if steps <= 0:
        raise ThreeDeftBlocked("3DUS parameter running needs positive ode_steps.")
    return steps


def _scale_expressions_for_key(scale_expressions: dict[str, str], key: str) -> dict[str, str]:
    aliases = dict(_THREE_D_SCALE_ALIAS_GROUPS).get(key, ())
    return {alias: scale_expressions[alias] for alias in aliases if alias in scale_expressions}


def _common_input_scale(inputs: dict[str, dict[str, Any]]) -> float:
    scales = {_number(spec.get("input_scale")) for spec in inputs.values()}
    if len(scales) != 1:
        raise ThreeDeftBlocked("4D running currently requires all input parameters to share one numeric input_scale.")
    return next(iter(scales))


def _running_scale_expression(value: Any) -> str:
    raw = str(value or "").strip().strip("`")
    if "=" in raw:
        raw = raw.split("=", 1)[1].strip()
    if not raw:
        raise ThreeDeftBlocked("four_d_to_three_d_matching_scale must define the 4D RG target and 4D->3D matching scale, for example mu4=pi*T.")
    return raw


def _interface_number(interface: dict[str, str], key: str) -> float | None:
    raw = str(interface.get(key, "")).strip()
    if _missing(raw) or raw.casefold() in {"none", "not_applicable", "not applicable", "base_default", "default"}:
        return None
    return _number(raw)


def _interface_number_any(interface: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        if key not in interface:
            continue
        value = _interface_number(interface, key)
        if value is not None:
            return value
    return None


def _require_supported_hook(interface: dict[str, str], key: str, *, allowed: set[str]) -> None:
    raw = str(interface.get(key, "")).strip()
    normalized = _hook_token(raw)
    allowed_normalized = {_hook_token(value) for value in allowed}
    if _missing(raw) or normalized in allowed:
        return
    if normalized in allowed_normalized:
        return
    raise ThreeDeftBlocked(
        f"PhaseTracer interface key {key}={raw!r} is recorded, but the independent 3DEFT renderer "
        "does not yet generate that custom hook. Use an explicit supported value or extend the 3DEFT renderer."
    )


def _coefficients(contract: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in contract.get("three_d_coefficients", []):
        name = str(row.get("name", "")).strip()
        expression = str(row.get("expression_or_interpolator", "")).strip()
        if not _identifier(name) or _missing(expression):
            continue
        if _looks_like_nonanalytic_source(expression):
            raise ThreeDeftBlocked(f"3D coefficient {name} needs an analytic expression or generated interpolator code for PhaseTracer.")
        result.append({"name": name, "expression": expression})
    return result


def _ordered_coefficients(contract: dict[str, Any], coefficients: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = contract.get("three_d_parameter_evaluator", [])
    by_key = {str(row.get("key", "")).strip(): str(row.get("value", "")).strip() for row in rows}
    order = _csv(by_key.get("coefficient_order", ""))
    if not order:
        return coefficients
    by_name = {coeff["name"]: coeff for coeff in coefficients}
    ordered: list[dict[str, str]] = []
    for name in order:
        coeff = by_name.get(name)
        if coeff is None:
            raise ThreeDeftBlocked(f"3D Parameter Evaluator coefficient_order references unknown coefficient {name!r}.")
        ordered.append(coeff)
    return ordered


def _prune_coefficients_for_potential(
    coefficients: list[dict[str, str]],
    seed_names: set[str],
) -> list[dict[str, str]]:
    by_name = {coeff["name"]: coeff for coeff in coefficients}
    needed = {name for name in seed_names if name in by_name}
    changed = True
    while changed:
        changed = False
        for name in list(needed):
            expression = by_name[name]["expression"]
            dependencies = _expression_names(expression) & set(by_name)
            new_dependencies = dependencies - needed
            if new_dependencies:
                needed.update(new_dependencies)
                changed = True
    return [coeff for coeff in coefficients if coeff["name"] in needed]


def _normalize_coefficient_expressions(
    coefficients: list[dict[str, str]],
    coefficient_names: set[str],
) -> list[dict[str, str]]:
    return [
        {
            "name": coeff["name"],
            "expression": _normalize_known_coefficient_references(coeff["expression"], coefficient_names),
        }
        for coeff in coefficients
    ]


def _normalize_known_coefficient_references(expression: str, coefficient_names: set[str]) -> str:
    """Align DRalgo algebra references with scalar coefficient names.

    DRalgo often prints rules like ``g23dUS^2 -> g23d^2``. The left-hand side is
    normalized to the scalar coefficient ``g23dUS_sq``; this helper performs the
    corresponding RHS rewrite when ``g23d_sq`` is known. It also accepts DRalgo
    indexed-call spelling such as ``lamVL(2)`` and treats it as ``lamVL_2``.
    """

    math_functions = _EXPRESSION_HELPER_NAMES | set(_MATH_CONSTANTS)

    def indexed_replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in math_functions:
            return match.group(0)
        return f"{name}_{match.group(2)}"

    result = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\((\d+)\)", indexed_replacement, str(expression))
    square_bases = sorted(
        {name[:-3] for name in coefficient_names if name.endswith("_sq")}
        | {
            name[:-2]
            for name in coefficient_names
            if name.endswith("sq") and not name.endswith("_sq") and f"{name[:-2]}_sq" not in coefficient_names
        },
        key=len,
        reverse=True,
    )
    for base in square_bases:
        square_name = f"{base}_sq" if f"{base}_sq" in coefficient_names else f"{base}sq"
        result = re.sub(rf"\b{re.escape(base)}\s*\*\*\s*4\b", f"({square_name}**2)", result)
        result = re.sub(rf"\b{re.escape(base)}\s*\*\*\s*2\b", square_name, result)
    return result


def _topological_sort_coefficients(coefficients: list[dict[str, str]]) -> list[dict[str, str]]:
    by_name = {coeff["name"]: coeff for coeff in coefficients}
    names = set(by_name)
    remaining = list(coefficients)
    ordered: list[dict[str, str]] = []
    emitted: set[str] = set()
    while remaining:
        progressed = False
        next_remaining: list[dict[str, str]] = []
        for coeff in remaining:
            deps = (_expression_names(coeff["expression"]) & names) - {coeff["name"]}
            if deps <= emitted:
                ordered.append(coeff)
                emitted.add(coeff["name"])
                progressed = True
            else:
                next_remaining.append(coeff)
        if not progressed:
            cycle = ", ".join(coeff["name"] for coeff in next_remaining)
            raise ThreeDeftBlocked(f"3D coefficient dependency cycle or unresolved ordering: {cycle}")
        remaining = next_remaining
    return ordered


def _render_input_struct(inputs: dict[str, dict[str, Any]]) -> str:
    if not inputs:
        return "    // No reviewed input parameters were declared."
    return "\n".join(f"    double {name} = 0.0;" for name in inputs)


def _render_input_initializers(inputs: dict[str, dict[str, Any]]) -> str:
    return "\n".join(
        f"  input.{name} = {_cpp_float(spec['test_value'])};"
        for name, spec in inputs.items()
    )


def _render_parameter_struct(coefficients: list[dict[str, str]]) -> str:
    if not coefficients:
        return "    // No reviewed 3D coefficients were declared."
    return "\n".join(f"    double {coeff['name']};" for coeff in coefficients)


def _render_parameter_return(coefficients: list[dict[str, str]]) -> str:
    if not coefficients:
        return "    return ThreeDParameters{};"
    values = ", ".join(coeff["name"] for coeff in coefficients)
    return f"    return ThreeDParameters{{{values}}};"


def _render_3dus_parameter_running_method(
    coefficients: list[dict[str, str]],
    inputs: dict[str, dict[str, Any]],
    running_inputs: dict[str, dict[str, Any]],
    scale_expressions: dict[str, str],
    beta_functions: dict[str, str],
    *,
    mode: str,
    ode_steps: int,
) -> str:
    if not beta_functions:
        return """  ThreeDParameters apply_3dus_parameter_running(const ThreeDParameters& matched, double T) const {
    (void)T;
    return matched;
  }"""
    if mode == "rk4_log_mu":
        return _render_3dus_numeric_running_method(coefficients, inputs, running_inputs, scale_expressions, beta_functions, ode_steps=ode_steps)
    return _render_3dus_analytic_running_method(coefficients, inputs, running_inputs, scale_expressions, beta_functions)


def _render_3dus_analytic_running_method(
    coefficients: list[dict[str, str]],
    inputs: dict[str, dict[str, Any]],
    running_inputs: dict[str, dict[str, Any]],
    scale_expressions: dict[str, str],
    beta_functions: dict[str, str],
) -> str:
    coefficient_names = {coeff["name"] for coeff in coefficients}
    beta_used_names = _expression_names_many(beta_functions.values())
    initial_scale_expressions = _scale_expressions_for_key(scale_expressions, "mu_3_scale")
    target_scale_expressions = _scale_expressions_for_key(scale_expressions, "mu_3_us_scale")
    scale_used_names = (
        set(initial_scale_expressions)
        | set(target_scale_expressions)
        | (beta_used_names & set(scale_expressions))
    )
    scale_dependency_names = _scale_dependency_names(scale_expressions, scale_used_names)
    running_used_names = beta_used_names | scale_dependency_names
    running_alias_block = _render_running_alias_block(inputs, running_used_names, running_inputs=running_inputs)
    coefficient_alias_block = _render_coefficient_aliases(coefficients, beta_used_names | set(beta_functions), source="matched")
    scale_alias_block = _render_scale_alias_block(scale_expressions, scale_used_names, inputs)
    method_void_lines = _render_void_lines(
        ("T", not running_alias_block and "T" not in scale_dependency_names)
    )
    allowed_names = set(inputs) | coefficient_names | set(scale_expressions) | {"T", "pi"}
    beta_blocks: list[str] = []
    for name, expression in beta_functions.items():
        block = _render_expression_assignment(
            expression,
            result_name=f"beta3dus_{name}",
            allowed_names=allowed_names,
            input_names=set(),
        )
        beta_blocks.append(block.lines)
    update_lines = "\n".join(
        f"    const double {name}_3dus = {name} + beta3dus_{name} * log_ratio_3dus;"
        for name in beta_functions
    )
    values = ", ".join(
        f"{coeff['name']}_3dus" if coeff["name"] in beta_functions else f"matched.{coeff['name']}"
        for coeff in coefficients
    )
    return f"""  ThreeDParameters apply_3dus_parameter_running(const ThreeDParameters& matched, double T) const {{
{method_void_lines}
{running_alias_block}
{coefficient_alias_block}
{scale_alias_block}
    if (mu3 <= 0.0 || mu3US <= 0.0) {{
      throw std::invalid_argument("3DUS parameter running requires positive mu_3_scale and mu_3_us_scale.");
    }}
    const double log_ratio_3dus = std::log(mu3US / mu3);
{chr(10).join(beta_blocks)}
{update_lines}
    return ThreeDParameters{{{values}}};
  }}"""


def _render_3dus_numeric_running_method(
    coefficients: list[dict[str, str]],
    inputs: dict[str, dict[str, Any]],
    running_inputs: dict[str, dict[str, Any]],
    scale_expressions: dict[str, str],
    beta_functions: dict[str, str],
    *,
    ode_steps: int,
) -> str:
    coefficient_names = {coeff["name"] for coeff in coefficients}
    beta_used_names = _expression_names_many(beta_functions.values())
    initial_scale_expressions = _scale_expressions_for_key(scale_expressions, "mu_3_scale")
    target_scale_expressions = _scale_expressions_for_key(scale_expressions, "mu_3_us_scale")
    all_scale_used_names = (
        set(initial_scale_expressions)
        | set(target_scale_expressions)
        | (beta_used_names & set(scale_expressions))
    )
    initial_scale_used_names = set(initial_scale_expressions) & beta_used_names
    apply_scale_dependency_names = _scale_dependency_names(scale_expressions, all_scale_used_names)
    beta_scale_dependency_names = _scale_dependency_names(initial_scale_expressions, initial_scale_used_names)
    apply_running_alias_block = _render_running_alias_block(inputs, apply_scale_dependency_names, running_inputs=running_inputs)
    apply_scale_alias_block = _render_scale_alias_block(scale_expressions, all_scale_used_names, inputs)
    apply_void_lines = _render_void_lines(
        ("T", not apply_running_alias_block and "T" not in apply_scale_dependency_names)
    )
    beta_running_used_names = beta_used_names | beta_scale_dependency_names
    beta_running_alias_block = _render_running_alias_block(inputs, beta_running_used_names, running_inputs=running_inputs)
    beta_coefficient_alias_block = _render_coefficient_aliases(coefficients, beta_used_names, source="state")
    beta_scale_alias_block = _render_scale_alias_block(initial_scale_expressions, initial_scale_used_names, inputs)
    beta_void_lines = _render_void_lines(
        ("T", not beta_running_alias_block and "T" not in beta_used_names and "T" not in beta_scale_dependency_names)
    )
    dynamic_scale_alias_block = "\n".join(
        [
            "    const double mu = mu3US;",
            "    const double mu_3_us = mu3US;",
            "    const double mu_3us = mu3US;",
            "    const double mu3us = mu3US;",
            "    const double log_mu = std::log(mu3US);",
            "    const double log_mu_3_us = log_mu;",
            "    const double log_mu3US = log_mu;",
            "    const double t = log_mu;",
        ]
    )
    allowed_names = (
        set(inputs)
        | coefficient_names
        | set(initial_scale_expressions)
        | _THREE_D_US_DYNAMIC_SCALE_NAMES
        | {"T", "pi"}
    )
    beta_blocks: list[str] = []
    for name, expression in beta_functions.items():
        block = _render_expression_assignment(
            expression,
            result_name=f"beta3dus_{name}",
            allowed_names=allowed_names,
            input_names=set(),
        )
        beta_blocks.append(block.lines)
    beta_return_values = ", ".join(
        f"beta3dus_{coeff['name']}" if coeff["name"] in beta_functions else "0.0"
        for coeff in coefficients
    )
    add_scaled_values = ", ".join(
        f"base.{coeff['name']} + scale * delta.{coeff['name']}"
        for coeff in coefficients
    )
    rk4_values = ", ".join(
        f"k1.{coeff['name']} + 2.0 * k2.{coeff['name']} + 2.0 * k3.{coeff['name']} + k4.{coeff['name']}"
        for coeff in coefficients
    )
    return f"""  ThreeDParameters apply_3dus_parameter_running(const ThreeDParameters& matched, double T) const {{
{apply_void_lines}
{apply_running_alias_block}
{apply_scale_alias_block}
    return solve_3dus_parameter_running_numeric(matched, T, mu3, mu3US);
  }}

  ThreeDParameters beta_functions_3dus(const ThreeDParameters& state, double T, double mu3US) const {{
{beta_void_lines}
{beta_running_alias_block}
{beta_coefficient_alias_block}
{beta_scale_alias_block}
{dynamic_scale_alias_block}
{chr(10).join(beta_blocks)}
    return ThreeDParameters{{{beta_return_values}}};
  }}

  ThreeDParameters add_scaled_3dus_parameters(const ThreeDParameters& base, const ThreeDParameters& delta, double scale) const {{
    return ThreeDParameters{{{add_scaled_values}}};
  }}

  ThreeDParameters combine_3dus_rk4(const ThreeDParameters& k1, const ThreeDParameters& k2, const ThreeDParameters& k3, const ThreeDParameters& k4) const {{
    return ThreeDParameters{{{rk4_values}}};
  }}

  ThreeDParameters solve_3dus_parameter_running_numeric(const ThreeDParameters& matched, double T, double mu3, double mu3US) const {{
    if (mu3 <= 0.0 || mu3US <= 0.0) {{
      throw std::invalid_argument("3DUS parameter running requires positive mu_3_scale and mu_3_us_scale.");
    }}
    const double log_start = std::log(mu3);
    const double log_end = std::log(mu3US);
    const double step = (log_end - log_start) / static_cast<double>({ode_steps});
    if (std::abs(step) < 1e-15) {{
      return matched;
    }}
    ThreeDParameters state = matched;
    for (int index = 0; index < {ode_steps}; ++index) {{
      const double log_mu = log_start + step * static_cast<double>(index);
      const ThreeDParameters k1 = beta_functions_3dus(state, T, std::exp(log_mu));
      const ThreeDParameters k2 = beta_functions_3dus(add_scaled_3dus_parameters(state, k1, 0.5 * step), T, std::exp(log_mu + 0.5 * step));
      const ThreeDParameters k3 = beta_functions_3dus(add_scaled_3dus_parameters(state, k2, 0.5 * step), T, std::exp(log_mu + 0.5 * step));
      const ThreeDParameters k4 = beta_functions_3dus(add_scaled_3dus_parameters(state, k3, step), T, std::exp(log_mu + step));
      state = add_scaled_3dus_parameters(state, combine_3dus_rk4(k1, k2, k3, k4), step / 6.0);
    }}
    return state;
  }}"""


def _render_named_struct_fields(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "    // No reviewed scalar fields were declared."
    return "\n".join(f"    double {row['name']};" for row in rows)


def _render_named_struct_return(type_name: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"    return {type_name}{{}};"
    values = ", ".join(row["name"] for row in rows)
    return f"    return {type_name}{{{values}}};"


def _render_running_struct(inputs: dict[str, dict[str, Any]]) -> str:
    if not inputs:
        return "    // No reviewed input parameters were declared."
    return "\n".join(f"    double {name};" for name in inputs)


def _render_running_return(inputs: dict[str, dict[str, Any]], *, member_values: bool) -> str:
    if not inputs:
        return "    return RunningParameters{};"
    values = ", ".join(f"input_.{name}" if member_values else name for name in inputs)
    return f"    return RunningParameters{{{values}}};"


def _render_running_aliases(inputs: dict[str, dict[str, Any]], used_names: set[str] | None = None) -> str:
    return "\n".join(
        f"    const double {name} = running.{name};"
        for name in inputs
        if used_names is None or name in used_names
    )


def _render_fixed_input_aliases(
    inputs: dict[str, dict[str, Any]],
    running_inputs: dict[str, dict[str, Any]],
    used_names: set[str],
) -> str:
    running_names = set(running_inputs)
    return "\n".join(
        f"    const double {name} = input_.{name};"
        for name in inputs
        if name in used_names and name not in running_names
    )


def _render_running_alias_block(
    inputs: dict[str, dict[str, Any]],
    used_names: set[str],
    *,
    running_inputs: dict[str, dict[str, Any]] | None = None,
) -> str:
    active_running_inputs = inputs if running_inputs is None else running_inputs
    running_aliases = _render_running_aliases(active_running_inputs, used_names)
    fixed_aliases = _render_fixed_input_aliases(inputs, active_running_inputs, used_names)
    lines: list[str] = []
    if running_aliases:
        lines.append(f"    const RunningParameters running = get_running_parameters(T);\n{running_aliases}")
    if fixed_aliases:
        lines.append(fixed_aliases)
    return "\n".join(lines)


def _render_field_aliases(fields: list[dict[str, Any]], used_names: set[str] | None = None) -> str:
    return "\n".join(
        f"    const double {field['name']} = fields.{field['name']};"
        for field in fields
        if used_names is None or field["name"] in used_names
    )


def _render_coefficient_aliases(coefficients: list[dict[str, str]], used_names: set[str] | None = None, *, source: str = "params") -> str:
    return "\n".join(
        f"    const double {coeff['name']} = {source}.{coeff['name']};"
        for coeff in coefficients
        if used_names is None or coeff["name"] in used_names
    )


def _render_void_lines(*items: tuple[str, bool]) -> str:
    return "\n".join(f"    (void){name};" for name, needed in items if needed)


def _render_optional_scalar_override(method_name: str, value: float | None) -> str:
    if value is None:
        return ""
    return f"""  double {method_name}() const override {{
    return {_cpp_float(value)};
  }}
"""


def _render_running_body(
    inputs: dict[str, dict[str, Any]],
    *,
    all_inputs: dict[str, dict[str, Any]] | None = None,
    beta_functions: dict[str, str],
    runtime_rg: bool,
    running_scale_expression: str,
    input_scale: float | None,
    ode_steps: int,
) -> str:
    if not runtime_rg:
        return "    (void)T;\n" + _render_running_return(inputs, member_values=True)
    if input_scale is None:
        raise ThreeDeftBlocked("4D running needs an input scale.")
    all_input_specs = inputs if all_inputs is None else all_inputs
    cleaned_scale_expression = _clean_expr(running_scale_expression)
    scale_used_names = _expression_names(cleaned_scale_expression)
    fixed_aliases = _render_fixed_input_aliases(all_input_specs, inputs, scale_used_names)
    scale_aliases = _default_hard_scale_alias_expressions(all_input_specs)
    scale_alias_block = _render_scale_alias_block(scale_aliases, scale_used_names, all_input_specs)
    scale_cpp = _CppExprRenderer(
        allowed_names=set(all_input_specs) | set(scale_aliases) | {"T", "pi"},
        input_names=set(),
    ).render(cleaned_scale_expression)
    return "\n".join(
        [
            fixed_aliases,
            scale_alias_block,
            f"    const double target_scale = {scale_cpp};",
            '    if (target_scale <= 0.0) {',
            '      throw std::invalid_argument("RG target scale must be positive.");',
            "    }",
            "    return running_from_phase_tracer_splines(target_scale);",
        ]
    ).lstrip("\n")


def _default_hard_scale_alias_expressions(inputs: dict[str, dict[str, Any]]) -> dict[str, str]:
    input_names = set(inputs)
    aliases: dict[str, str] = {}
    if "xi4" not in input_names:
        aliases["xi4"] = "1"
    if "xi_4" not in input_names:
        aliases["xi_4"] = "xi4"
    return aliases


def _render_beta_method(
    inputs: dict[str, dict[str, Any]],
    beta_functions: dict[str, str],
    *,
    beta_variable: str,
    beta_factor: float,
) -> str:
    input_names = set(inputs)
    used_names = _expression_names_many(beta_functions.values())
    aliases = _render_running_aliases(inputs, used_names)
    blocks: list[str] = []
    for name in inputs:
        expression = beta_functions.get(name)
        if expression is None:
            raise ThreeDeftBlocked(f"Missing beta function for input parameter {name!r}.")
        block = _render_expression_assignment(
            expression,
            result_name=f"beta_{name}",
            allowed_names=input_names | {"mu", "t", "log_mu", "pi"},
            input_names=set(),
        )
        blocks.append(block.lines)
    factor = _cpp_float(beta_factor)
    values = ", ".join(f"{factor} * beta_{name}" if beta_factor != 1.0 else f"beta_{name}" for name in inputs)
    t_expr = _beta_t_cpp(beta_variable, "log_mu")
    void_lines = _render_void_lines(("running", not aliases))
    vector_values = ", ".join(f"x.at({index})" for index, _name in enumerate(inputs))
    dxdt_lines = "\n".join(
        f"    dxdt[{index}] = beta.{name} / t;"
        for index, name in enumerate(inputs)
    )
    return f"""  RunningParameters beta_functions(const RunningParameters& running, double mu) const {{
{void_lines}
{aliases}
    const double log_mu = std::log(mu);
    const double t = {t_expr};
    (void)log_mu;
    (void)t;
{chr(10).join(blocks)}
    return RunningParameters{{{values}}};
  }}

  void Betas(const std::vector<double>& x, std::vector<double>& dxdt, const double t) override {{
    if (t <= 0.0) {{
      throw std::invalid_argument("PhaseTracer RG scale t must be positive.");
    }}
    if (x.size() != {len(inputs)}) {{
      throw std::invalid_argument("3DEFT potential received a malformed RG state vector.");
    }}
    RunningParameters running{{{vector_values}}};
    const RunningParameters beta = beta_functions(running, t);
    dxdt.resize({len(inputs)});
{dxdt_lines}
  }}
"""


def _running_spline_config(
    interface: dict[str, str],
    *,
    input_scale: float | None,
    target_scale: float | None,
    ode_steps: int,
) -> dict[str, float]:
    if input_scale is None or target_scale is None:
        raise ThreeDeftBlocked("PhaseTracer-native RG setup needs numeric input and target scales.")
    default_min = min(input_scale, target_scale, 1.0)
    default_max = max(input_scale, target_scale, 5000.0)
    min_scale = _interface_number_any(interface, "rg_spline_min_scale", "rg_min_scale") or default_min
    max_scale = _interface_number_any(interface, "rg_spline_max_scale", "rg_max_scale") or default_max
    if min_scale <= 0.0 or max_scale <= 0.0:
        raise ThreeDeftBlocked("PhaseTracer RG spline scale bounds must be positive.")
    if min_scale >= max_scale:
        max_scale = min_scale * 1.01
    default_step = min(1.0, max(1e-5, (max_scale - min_scale) / max(float(ode_steps), 1.0)))
    step = _interface_number_any(interface, "rg_spline_step", "rg_step") or default_step
    if step <= 0.0:
        raise ThreeDeftBlocked("PhaseTracer RG spline step must be positive.")
    return {"input_scale": input_scale, "min_scale": min_scale, "max_scale": max_scale, "step": step}


def _render_phase_tracer_rg_helpers(inputs: dict[str, dict[str, Any]], *, rg_spline: dict[str, float] | None) -> str:
    if not inputs or rg_spline is None:
        return ""
    vector_values = ", ".join(f"input_.{name}" for name in inputs)
    spline_values = ", ".join(f"alglib::spline1dcalc(RGEs[{index}], scale)" for index, _name in enumerate(inputs))
    return f"""  void initialise_phase_tracer_rg() {{
    const std::vector<double> x0 = initial_running_vector();
    solveBetas(x0, {_cpp_float(rg_spline['input_scale'])}, {_cpp_float(rg_spline['min_scale'])}, {_cpp_float(rg_spline['max_scale'])}, {_cpp_float(rg_spline['step'])});
  }}

  std::vector<double> initial_running_vector() const {{
    return std::vector<double>{{{vector_values}}};
  }}

  RunningParameters running_from_phase_tracer_splines(double scale) const {{
    if (RGEs.size() != {len(inputs)}) {{
      throw std::runtime_error("PhaseTracer RG splines were not initialized.");
    }}
    return RunningParameters{{{spline_values}}};
  }}
"""


def _render_expression_assignment(
    expression: str,
    *,
    result_name: str,
    allowed_names: set[str],
    input_names: set[str],
) -> _RenderedExpressionBlock:
    rendered = _CppExprRenderer(allowed_names=allowed_names, input_names=input_names).render(expression)
    return _RenderedExpressionBlock(lines=f"    const double {result_name} = {rendered};", result_name=result_name)


def _render_coefficient_expression_block(
    expression: str,
    *,
    result_name: str,
    allowed_names: set[str],
) -> _RenderedExpressionBlock:
    ordered_block = _render_ordered_coefficient_sum_assignment(
        expression,
        result_name=result_name,
        allowed_names=allowed_names,
    )
    if ordered_block is not None:
        return ordered_block
    return _render_expression_assignment(
        expression,
        result_name=result_name,
        allowed_names=allowed_names,
        input_names=set(),
    )


def _render_ordered_coefficient_sum_assignment(
    expression: str,
    *,
    result_name: str,
    allowed_names: set[str],
) -> _RenderedExpressionBlock | None:
    additive_terms = _top_level_additive_terms(expression)
    if not additive_terms:
        return None
    ordered_terms: list[tuple[str, str, str]] = []
    for sign, term_expression in additive_terms:
        order = _coefficient_order_component(term_expression, result_name)
        if order is None:
            return None
        ordered_terms.append((order, _signed_term_expression(sign, term_expression), term_expression))
    if not ordered_terms:
        return None

    lines: list[str] = [f"    // Perturbative-order components for {result_name}."]
    contribution_terms: list[str] = []
    subexpressions: list[dict[str, Any]] = []
    for order, signed_expression, source_expression in ordered_terms:
        contribution_terms.append(_CppExprRenderer(allowed_names=allowed_names, input_names=set()).render(signed_expression))
        subexpressions.append(
            {
                "name": f"{result_name}_{order}",
                "category": "perturbative_order",
                "order": order,
                "expression": signed_expression,
                "source_expression": source_expression,
            }
        )
    lines.append(_render_sum_assignment(result_name, contribution_terms))
    return _RenderedExpressionBlock(lines="\n".join(lines), result_name=result_name, subexpressions=tuple(subexpressions))


def _coefficient_order_component(expression: str, result_name: str) -> str | None:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    node = tree.body
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        node = node.operand
    if not isinstance(node, ast.Name):
        return None
    split = _split_perturbative_order_suffix(node.id)
    if split is None:
        return None
    base, order = split
    return order if base == result_name else None


def _split_perturbative_order_suffix(name: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)(?:_)?(NNLO|NLO|LO)", name)
    if not match:
        return None
    base = match.group(1).rstrip("_")
    if not base:
        return None
    return base, match.group(2)


def _v3d_ordered_terms(
    consumption: dict[str, Any],
    field_symbol_map: dict[str, str],
    coefficient_names: set[str],
) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for order in ("LO", "NLO", "NNLO"):
        raw = consumption.get(f"v3d_expression_{order}", consumption.get(f"v3d_expression_{order.lower()}", ""))
        expression = _clean_expr(str(raw), field_symbol_map=field_symbol_map)
        if _missing(expression):
            continue
        expression = _normalize_known_coefficient_references(expression, coefficient_names)
        terms.append((order, expression))
    return terms


def _render_v3d_expression_block(
    terms: list[tuple[str, str]],
    fallback_expression: str,
    *,
    allowed_names: set[str],
    field_names: set[str],
) -> _RenderedExpressionBlock:
    if not terms:
        return _render_expression_assignment(
            fallback_expression,
            result_name="v3d",
            allowed_names=allowed_names,
            input_names=set(),
        )
    lines: list[str] = []
    term_names: list[str] = []
    subexpressions: list[dict[str, Any]] = []
    for order, expression in terms:
        block = _render_v3d_order_block(
            order,
            expression,
            allowed_names=allowed_names,
            field_names=field_names,
        )
        lines.append(block.lines)
        term_names.append(block.result_name)
        subexpressions.extend(block.subexpressions)
    lines.append(_render_sum_assignment("v3d", term_names))
    return _RenderedExpressionBlock(lines="\n".join(lines), result_name="v3d", subexpressions=tuple(subexpressions))


def _render_v3d_order_block(
    order: str,
    expression: str,
    *,
    allowed_names: set[str],
    field_names: set[str],
) -> _RenderedExpressionBlock:
    result_name = f"V_{order}"
    additive_terms = _top_level_additive_terms(expression)
    signed_terms = [_signed_term_expression(sign, term_expression) for sign, term_expression in additive_terms]
    if not _should_split_v3d_order(expression, signed_terms):
        return _RenderedExpressionBlock(
            lines=_render_expression_assignment(
                expression,
                result_name=result_name,
                allowed_names=allowed_names,
                input_names=set(),
            ).lines,
            result_name=result_name,
            subexpressions=(
                {
                    "name": result_name,
                    "category": "perturbative_order",
                    "order": order,
                    "expression": expression,
                },
            ),
        )

    grouped: dict[str, list[str]] = {}
    for signed_expression in signed_terms:
        category = _v3d_physical_category(signed_expression, field_names=field_names)
        grouped.setdefault(category, []).append(signed_expression)
    if len(grouped) <= 1:
        return _RenderedExpressionBlock(
            lines=_render_expression_assignment(
                expression,
                result_name=result_name,
                allowed_names=allowed_names,
                input_names=set(),
            ).lines,
            result_name=result_name,
            subexpressions=(
                {
                    "name": result_name,
                    "category": "perturbative_order",
                    "order": order,
                    "expression": expression,
                },
            ),
        )

    lines: list[str] = [f"    // {result_name} split by physical structure for review."]
    block_names: list[str] = []
    subexpressions: list[dict[str, Any]] = []
    renderer = _CppExprRenderer(allowed_names=allowed_names, input_names=set())
    for category, category_terms in grouped.items():
        block_name = f"{result_name}_{category}"
        rendered_terms = [renderer.render(term_expression) for term_expression in category_terms]
        lines.append(_render_sum_assignment(block_name, rendered_terms))
        block_names.append(block_name)
        subexpressions.append(
            {
                "name": block_name,
                "category": "physical_block",
                "order": order,
                "block": category,
                "expression": " + ".join(f"({term_expression})" for term_expression in category_terms),
            }
        )
    lines.append(_render_sum_assignment(result_name, block_names))
    subexpressions.append(
        {
            "name": result_name,
            "category": "perturbative_order",
            "order": order,
            "expression": expression,
            "blocks": block_names,
        }
    )
    return _RenderedExpressionBlock(lines="\n".join(lines), result_name=result_name, subexpressions=tuple(subexpressions))


def _should_split_v3d_order(expression: str, signed_terms: list[str]) -> bool:
    if len(signed_terms) < 4:
        return False
    return len(expression) > 180 or len(signed_terms) >= 6


def _v3d_physical_category(expression: str, *, field_names: set[str]) -> str:
    names = _expression_names(expression)
    lower_expression = expression.casefold()
    lower_names = {name.casefold() for name in names}
    if "log(" in lower_expression or "math.log" in lower_expression or "np.log" in lower_expression:
        return "log"
    if any(name in {"lb", "lf"} or "log" in name for name in lower_names):
        return "log"
    if _has_fractional_power(expression) or "sqrt(" in lower_expression or "math.sqrt" in lower_expression or "np.sqrt" in lower_expression:
        return "nonanalytic_power"
    if any(_looks_like_gauge_name(name) for name in names):
        return "gauge"
    if len(names & field_names) >= 2:
        return "mixed_fields"
    if any(_looks_like_mass_name(name) for name in names):
        return "mass"
    return "polynomial"


def _looks_like_gauge_name(name: str) -> bool:
    normalized = name.strip("_").casefold()
    if normalized in {"g", "g1", "g2", "g3", "gs", "gw", "gp", "gprime", "g3d", "g13d", "g23d", "g33d"}:
        return True
    return bool(re.fullmatch(r"g[123][a-z0-9_]*", normalized)) or "gauge" in normalized


def _looks_like_mass_name(name: str) -> bool:
    normalized = name.strip("_").casefold()
    return normalized.startswith(("m", "mu")) and not normalized.startswith(("mu_3", "mu3", "mu_4", "mu4"))


def _has_fractional_power(expression: str) -> bool:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Pow):
            continue
        exponent = _literal_or_ratio_number(node.right)
        if exponent is None:
            continue
        if not float(exponent).is_integer():
            return True
    return False


def _literal_or_ratio_number(node: ast.AST) -> float | None:
    value = _literal_number(node)
    if value is not None:
        return value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        numerator = _literal_number(node.left)
        denominator = _literal_number(node.right)
        if numerator is None or denominator in {None, 0.0}:
            return None
        return numerator / denominator
    return None


def _top_level_additive_terms(expression: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []
    terms: list[tuple[int, str]] = []

    def collect(node: ast.AST, sign: int) -> None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            collect(node.left, sign)
            collect(node.right, sign)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            collect(node.left, sign)
            collect(node.right, -sign)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            collect(node.operand, -sign)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            collect(node.operand, sign)
            return
        terms.append((sign, ast.unparse(node)))

    collect(tree.body, 1)
    return terms


def _signed_term_expression(sign: int, expression: str) -> str:
    return expression if sign >= 0 else f"-({expression})"


def _render_sum_assignment(name: str, terms: list[str]) -> str:
    if not terms:
        return f"    const double {name} = 0.0;"
    if len(terms) == 1:
        return f"    const double {name} = {terms[0]};"
    return f"    const double {name} =\n        " + "\n      + ".join(terms) + ";"


def _expression_names(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _expression_names_many(expressions: Any) -> set[str]:
    names: set[str] = set()
    for expression in expressions:
        names.update(_expression_names(str(expression)))
    return names


def _candidate_z2_symmetries_from_expression(expression: str, field_names: list[str]) -> list[dict[str, str]]:
    analysis = analyze_sign_flip_symmetries(expression, field_names)
    return [
        {
            "mode": "z2_reflection",
            "field": ",".join(candidate.fields),
            "reason": "V3D is invariant under this sign-flip generator by the shared conservative AST parity check.",
        }
        for candidate in analysis.candidates
    ]


def _require_v3d_symbols_are_3d_parameters(expression: str, allowed_names: set[str]) -> None:
    helper_names = _EXPRESSION_HELPER_NAMES | {"pi"}
    used = _expression_names(expression) - helper_names
    unmapped = sorted(used - allowed_names)
    if unmapped:
        raise ThreeDeftBlocked(
            "V3D expressions may only consume reviewed 3D fields, T, pi, reviewed scale substitutions, "
            "reviewed input parameters, and entries from the 3D Coefficients table. "
            f"Add explicit input rows, 3D coefficient rows, scale rows, or symbol-audit repairs for: {', '.join(unmapped)}."
        )


class _CppExprRenderer:
    def __init__(self, *, allowed_names: set[str], input_names: set[str], boolean: bool = False) -> None:
        self.allowed_names = allowed_names
        self.input_names = input_names
        self.boolean = boolean

    def render(self, expression: str) -> str:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"invalid expression {expression!r}: {exc.msg}") from exc
        return self._expr(tree.body)

    def _expr(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return self._constant(node.value)
        if isinstance(node, ast.Name):
            return self._name(node.id)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                return self._power(node.left, node.right, render=self._expr)
            op = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
                ast.Mod: "%",
            }.get(type(node.op))
            if not op:
                raise ValueError(f"unsupported binary operator {type(node.op).__name__}")
            return f"({self._expr(node.left)} {op} {self._expr(node.right)})"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return f"(-{self._expr(node.operand)})"
            if isinstance(node.op, ast.UAdd):
                return f"(+{self._expr(node.operand)})"
            if isinstance(node.op, ast.Not):
                return f"(!({self._expr(node.operand)}))"
            raise ValueError(f"unsupported unary operator {type(node.op).__name__}")
        if isinstance(node, ast.BoolOp):
            op = " && " if isinstance(node.op, ast.And) else " || " if isinstance(node.op, ast.Or) else None
            if op is None:
                raise ValueError(f"unsupported boolean operator {type(node.op).__name__}")
            return "(" + op.join(self._expr(value) for value in node.values) + ")"
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return self._attribute_name(node)
        raise ValueError(f"unsupported expression node {type(node).__name__}")

    def _constant(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            number = float(value)
            if _close_enough(number, math.pi):
                return "kPi"
            if _close_enough(number, 0.5772156649015329):
                return "kEulerGamma"
            return _cpp_float(number)
        raise ValueError(f"unsupported constant {value!r}")

    def _name(self, name: str) -> str:
        if name in {"True", "False"}:
            return "true" if name == "True" else "false"
        if name == "pi":
            return "kPi"
        if name == "EulerGamma":
            return "kEulerGamma"
        if name == "Glaisher":
            return "kGlaisher"
        if name not in self.allowed_names:
            raise ValueError(f"name {name!r} is not declared in input parameters, 3D fields, coefficients, or T")
        if name in self.input_names:
            return f"input_.{name}"
        return name

    def _power(self, base_node: ast.AST, exponent_node: ast.AST, *, render) -> str:
        base = render(base_node)
        exponent = _literal_number(exponent_node)
        if exponent is not None:
            if _close_enough(exponent, 2.0):
                return f"sqr({base})"
            if _close_enough(exponent, 3.0):
                return f"cube({base})"
            if _close_enough(exponent, 4.0):
                return f"sqr(sqr({base}))"
            if _close_enough(exponent, 0.5):
                return f"std::sqrt({base})"
        return f"std::pow({base}, {render(exponent_node)})"

    def _compare(self, node: ast.Compare) -> str:
        left = self._expr(node.left)
        pieces: list[str] = []
        for op, comparator in zip(node.ops, node.comparators):
            right = self._expr(comparator)
            cpp_op = {
                ast.Lt: "<",
                ast.LtE: "<=",
                ast.Gt: ">",
                ast.GtE: ">=",
                ast.Eq: "==",
                ast.NotEq: "!=",
            }.get(type(op))
            if not cpp_op:
                raise ValueError(f"unsupported comparison operator {type(op).__name__}")
            pieces.append(f"({left} {cpp_op} {right})")
            left = right
        return "(" + " && ".join(pieces) + ")"

    def _call(self, node: ast.Call) -> str:
        if node.keywords:
            raise ValueError("keyword arguments are not supported")
        name = self._call_name(node.func)
        args = [self._expr(arg) for arg in node.args]
        if name == "pow":
            if len(args) != 2:
                raise ValueError("pow requires two arguments")
            return self._power(node.args[0], node.args[1], render=self._expr)
        if name in {"sqrt", "log", "exp", "sin", "cos", "tan", "abs", "fabs"}:
            if len(args) != 1:
                raise ValueError(f"{name} requires one argument")
            cpp_name = "abs" if name == "fabs" else name
            return f"std::{cpp_name}({args[0]})"
        if name in {"max", "min"}:
            if len(args) != 2:
                raise ValueError(f"{name} requires two arguments")
            return f"std::{name}({args[0]}, {args[1]})"
        raise ValueError(f"unsupported function call {name}")

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._attribute_name(node)
        raise ValueError("unsupported call target")

    def _attribute_name(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id in {"math", "np"}:
            return node.attr
        raise ValueError("only math.<func> and np.<func> attributes are supported")


def _eval_expr(expression: str, env: dict[str, float]) -> float:
    numeric_namespace = _NumericNamespace()
    allowed = {
        "sqrt": math.sqrt,
        "log": math.log,
        "exp": math.exp,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "abs": abs,
        "pow": pow,
        "max": max,
        "min": min,
        "pi": math.pi,
        "EulerGamma": _MATH_CONSTANTS["EulerGamma"],
        "Glaisher": _MATH_CONSTANTS["Glaisher"],
        "math": math,
        "np": numeric_namespace,
    }
    allowed.update(env)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ThreeDeftBlocked(f"Invalid potential expression {expression!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in allowed:
            raise ThreeDeftBlocked(f"Potential expression uses undeclared name {node.id!r}.")
        if isinstance(node, ast.Attribute) and not (isinstance(node.value, ast.Name) and node.value.id in {"math", "np"}):
            raise ThreeDeftBlocked("Potential expression evaluator permits only math.<func> or np.<func> attributes.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in allowed:
            raise ThreeDeftBlocked(f"Potential expression uses unsupported call {node.func.id!r}.")
    value = eval(compile(tree, "<ptagent_3deft>", "eval"), {"__builtins__": {}}, allowed)
    if isinstance(value, complex):
        tolerance = max(1.0e-10, 1.0e-9 * max(abs(value.real), 1.0))
        if abs(value.imag) <= tolerance:
            value = value.real
        else:
            raise ValueError("expression evaluated outside the real domain")
    return float(value)


def _eval_expr_or_nan(expression: str, env: dict[str, float]) -> float:
    try:
        return _eval_expr(expression, env)
    except (OverflowError, TypeError, ValueError, ZeroDivisionError):
        return math.nan


def _metadata_number(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _apply_3dus_parameter_running_python(
    coefficient_names: set[str],
    beta_functions: dict[str, str],
    env: dict[str, float],
    *,
    ode_steps: int,
) -> dict[str, float]:
    if not beta_functions:
        return {}
    if "mu3" not in env or "mu3US" not in env:
        raise ThreeDeftBlocked("3DUS parameter running needs reviewed mu_3_scale and mu_3_us_scale aliases.")
    mu_initial = float(env["mu3"])
    mu_target = float(env["mu3US"])
    if mu_initial <= 0.0 or mu_target <= 0.0:
        raise ThreeDeftBlocked("3DUS parameter running requires positive mu_3_scale and mu_3_us_scale.")
    state = {name: float(env[name]) for name in coefficient_names if name in env}
    active_betas = {name: expression for name, expression in beta_functions.items() if name in state}
    if not active_betas:
        return {}
    if not _three_d_us_needs_numeric_running(active_betas):
        log_ratio = math.log(mu_target / mu_initial)
        values = dict(state)
        beta_env = _with_3dus_dynamic_scale_aliases({**env, **values}, mu_target)
        for name, expression in active_betas.items():
            values[name] = values[name] + _eval_expr(expression, beta_env) * log_ratio
        return values
    return _run_3dus_python(active_betas, state, env, mu_initial, mu_target, ode_steps=ode_steps)


def _run_3dus_python(
    beta_functions: dict[str, str],
    state: dict[str, float],
    env: dict[str, float],
    mu_initial: float,
    mu_target: float,
    *,
    ode_steps: int,
) -> dict[str, float]:
    log_start = math.log(mu_initial)
    log_end = math.log(mu_target)
    step = (log_end - log_start) / float(ode_steps)
    if abs(step) < 1e-15:
        return state
    values = dict(state)
    for index in range(ode_steps):
        log_mu = log_start + step * index
        k1 = _eval_3dus_beta_vector(beta_functions, values, env, math.exp(log_mu))
        k2 = _eval_3dus_beta_vector(beta_functions, _add_scaled_full(values, k1, 0.5 * step), env, math.exp(log_mu + 0.5 * step))
        k3 = _eval_3dus_beta_vector(beta_functions, _add_scaled_full(values, k2, 0.5 * step), env, math.exp(log_mu + 0.5 * step))
        k4 = _eval_3dus_beta_vector(beta_functions, _add_scaled_full(values, k3, step), env, math.exp(log_mu + step))
        values = {
            name: values[name] + step * (k1.get(name, 0.0) + 2.0 * k2.get(name, 0.0) + 2.0 * k3.get(name, 0.0) + k4.get(name, 0.0)) / 6.0
            for name in values
        }
    return values


def _eval_3dus_beta_vector(
    beta_functions: dict[str, str],
    state: dict[str, float],
    env: dict[str, float],
    mu: float,
) -> dict[str, float]:
    beta_env = _with_3dus_dynamic_scale_aliases({**env, **state}, mu)
    return {name: _eval_expr(expression, beta_env) for name, expression in beta_functions.items()}


def _with_3dus_dynamic_scale_aliases(env: dict[str, float], mu: float) -> dict[str, float]:
    log_mu = math.log(mu)
    result = dict(env)
    result.update(
        {
            "mu": mu,
            "mu_3_us": mu,
            "mu_3us": mu,
            "mu3us": mu,
            "mu3US": mu,
            "log_mu": log_mu,
            "log_mu_3_us": log_mu,
            "log_mu3US": log_mu,
            "t": log_mu,
            "pi": math.pi,
        }
    )
    return result


def _run_rg_python(
    inputs: dict[str, dict[str, Any]],
    beta_functions: dict[str, str],
    target_scale: float,
    *,
    input_scale: float | None,
    ode_steps: int,
    beta_variable: str,
    beta_factor: float,
) -> dict[str, float]:
    if input_scale is None:
        raise ThreeDeftBlocked("4D running needs an input scale.")
    if target_scale <= 0.0 or input_scale <= 0.0:
        raise ThreeDeftBlocked("4D running scales must be positive.")
    state = {name: spec["test_value"] for name, spec in inputs.items()}
    log_start = math.log(input_scale)
    log_end = math.log(target_scale)
    step = (log_end - log_start) / float(ode_steps)
    for index in range(ode_steps):
        log_mu = log_start + step * index
        k1 = _eval_beta_vector(beta_functions, state, math.exp(log_mu), beta_variable=beta_variable, beta_factor=beta_factor)
        k2 = _eval_beta_vector(
            beta_functions,
            _add_scaled(state, k1, 0.5 * step),
            math.exp(log_mu + 0.5 * step),
            beta_variable=beta_variable,
            beta_factor=beta_factor,
        )
        k3 = _eval_beta_vector(
            beta_functions,
            _add_scaled(state, k2, 0.5 * step),
            math.exp(log_mu + 0.5 * step),
            beta_variable=beta_variable,
            beta_factor=beta_factor,
        )
        k4 = _eval_beta_vector(beta_functions, _add_scaled(state, k3, step), math.exp(log_mu + step), beta_variable=beta_variable, beta_factor=beta_factor)
        state = {
            name: state[name] + step * (k1[name] + 2.0 * k2[name] + 2.0 * k3[name] + k4[name]) / 6.0
            for name in state
        }
    return state


def _eval_beta_vector(
    beta_functions: dict[str, str],
    state: dict[str, float],
    mu: float,
    *,
    beta_variable: str,
    beta_factor: float,
) -> dict[str, float]:
    env = dict(state)
    log_mu = math.log(mu)
    env["mu"] = mu
    env["log_mu"] = log_mu
    env["t"] = _beta_t_python(beta_variable, log_mu)
    env["pi"] = math.pi
    return {name: beta_factor * _eval_expr(expression, env) for name, expression in beta_functions.items()}


def _add_scaled(base: dict[str, float], delta: dict[str, float], scale: float) -> dict[str, float]:
    return {name: base[name] + scale * delta[name] for name in base}


def _add_scaled_full(base: dict[str, float], delta: dict[str, float], scale: float) -> dict[str, float]:
    return {name: base[name] + scale * delta.get(name, 0.0) for name in base}


def _number(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ThreeDeftBlocked(f"Expected numeric value, got {value!r}.") from exc


def _numbers(value: Any) -> list[float]:
    raw = str(value or "").strip()
    if _missing(raw):
        return []
    return [_number(item) for item in raw.split(",") if item.strip()]


def _reference_candidate_temperatures(original: float) -> list[float]:
    candidates = [original, 100.0, 150.0, 50.0, 200.0, 10.0, 300.0]
    result: list[float] = []
    for value in candidates:
        if value > 0.0 and all(not _close_enough(value, existing) for existing in result):
            result.append(value)
    return result


def _reference_candidate_vectors(field_count: int, original: list[float]) -> list[list[float]]:
    candidates: list[list[float]] = []

    def add(values: list[float]) -> None:
        if len(values) != field_count:
            return
        if not all(math.isfinite(value) for value in values):
            return
        if not any(all(_close_enough(left, right) for left, right in zip(values, existing)) for existing in candidates):
            candidates.append(values)

    add(list(original))
    for magnitude in (1.0, 10.0, 50.0, 100.0, 150.0, 200.0):
        add([magnitude] * field_count)
    if field_count == 2:
        for left in (10.0, 50.0, 100.0, 200.0):
            for right in (10.0, 50.0, 100.0, 200.0):
                add([left, right])
    return candidates


def _close_enough(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-9, 1e-8 * max(abs(left), abs(right), 1.0))


def _literal_number(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number(node.operand)
        return None if value is None else -value
    return None


def _csv(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if _missing(raw):
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _slot_key(value: Any) -> tuple[int, str]:
    raw = str(value or "").strip()
    try:
        return (int(raw), raw)
    except ValueError:
        return (10_000, raw)


def _identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))


def _missing(value: Any) -> bool:
    raw = str(value or "").strip().strip("`")
    if not raw:
        return True
    return raw.casefold() in {"ask_user", "tbd", "todo", "required", "unknown", "?", "-"}


def _optional_reference_value_missing(value: Any) -> bool:
    raw = str(value or "").strip().strip("`")
    if _missing(raw):
        return True
    return raw.casefold() in {"none", "not_applicable", "not applicable", "n/a", "na", "no"}


def _looks_like_nonanalytic_source(expression: str) -> bool:
    normalized = expression.strip().casefold()
    return normalized in {"table_interpolator", "dralgo_runtime"}


def _clean_expr(expression: str, *, field_symbol_map: dict[str, str] | None = None) -> str:
    expr = expression.strip().strip("`")
    if looks_like_mathematica_inputform(expr):
        expr = convert_mathematica_inputform_expression(expr, field_map=field_symbol_map or {}).expression
    if "^" in expr and "**" not in expr:
        expr = expr.replace("^", "**")
    return expr


def _cpp_float(value: float) -> str:
    number = float(value)
    if math.isnan(number):
        return "std::numeric_limits<double>::quiet_NaN()"
    if math.isinf(number):
        return "std::numeric_limits<double>::infinity()" if number > 0 else "-std::numeric_limits<double>::infinity()"
    rendered = format(number, ".17g")
    if "." not in rendered and "e" not in rendered and "E" not in rendered:
        rendered += ".0"
    return rendered


class _NumericNamespace:
    sqrt = staticmethod(math.sqrt)
    log = staticmethod(math.log)
    exp = staticmethod(math.exp)
    sin = staticmethod(math.sin)
    cos = staticmethod(math.cos)
    tan = staticmethod(math.tan)
    abs = staticmethod(abs)
    fabs = staticmethod(math.fabs)
    pow = staticmethod(pow)
    max = staticmethod(max)
    min = staticmethod(min)
