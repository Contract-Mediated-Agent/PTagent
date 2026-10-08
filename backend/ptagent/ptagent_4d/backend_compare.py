from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .artifact_layout import generated_models_dir, model_artifact_slug, proof_materials_dir
from .compiler import CompileBlocked, CompileResult, compile_usage_instructions
from .config import ConfigurationError, Settings, require_configured_phasetracer_root, require_configured_runtime_python
from .phasetracer_runner import run_phasetracer_smoke
from .smoke_output import parse_detailed_smoke_output


BACKEND_COMPARE_ABS_TOL = 1e-8
BACKEND_COMPARE_REL_TOL = 1e-10
BACKEND_COMPARE_LOOP_ABS_TOL = 1e-7
BACKEND_COMPARE_LOOP_REL_TOL = 1e-9


@dataclass(frozen=True)
class BackendCompareResult:
    status: str
    python_result: CompileResult
    phasetracer_result: CompileResult
    report_json_path: Path
    report_markdown_path: Path
    phasetracer_smoke_status: str
    contract_reference_status: str
    python_model_status: str
    comparison_points: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifact_actions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "python_model_path": str(self.python_result.model_path),
            "phasetracer_model_path": str(self.phasetracer_result.model_path),
            "phasetracer_project_dir": str(self.phasetracer_result.model_path.parent),
            "report_json_path": str(self.report_json_path),
            "report_markdown_path": str(self.report_markdown_path),
            "phasetracer_smoke_status": self.phasetracer_smoke_status,
            "contract_reference_status": self.contract_reference_status,
            "python_model_status": self.python_model_status,
            "comparison_points": list(self.comparison_points),
            "python_usage_instructions": list(self.python_result.usage_instructions),
            "phasetracer_usage_instructions": list(self.phasetracer_result.usage_instructions),
            "artifact_actions": dict(self.artifact_actions),
            "warnings": list(self.warnings),
        }


def compare_backends_template(
    markdown_text: str,
    settings: Settings,
    *,
    template_path: Path | None = None,
    output_dir: Path | None = None,
    report_dir: Path | None = None,
    phasetracer_root: str = "",
    linux_runner: str = "native",
    wsl_distro: str = "",
    run_transition_smoke: bool = False,
    clang_format: bool = True,
    force_regenerate: bool = False,
) -> BackendCompareResult:
    from .phasetracer_backend import compare_phasetracer_smoke_output
    from .template_contract import validate_contract_template

    validation = validate_contract_template(markdown_text, review_required=True, compile_backend="compare-backends")
    if not validation.ok:
        preview = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.issues[:5])
        raise CompileBlocked("Template contract is not compare-ready: " + preview)
    try:
        require_configured_runtime_python(settings)
        resolved_phasetracer_root = require_configured_phasetracer_root(settings, phasetracer_root)
    except ConfigurationError as exc:
        raise CompileBlocked(str(exc)) from exc
    contract = validation.contract
    template_path = template_path.resolve() if template_path else None
    model_output_dir = output_dir or (generated_models_dir(template_path) if template_path else None)
    compare_report_dir = report_dir or (proof_materials_dir(template_path) if template_path else Path.cwd())
    compare_report_dir.mkdir(parents=True, exist_ok=True)
    candidate_root = model_output_dir or generated_models_dir(settings.artifact_root)
    hashes = _contract_hashes(markdown_text, contract)

    python_result, python_action = _reuse_or_compile_python_artifact(
        markdown_text,
        settings,
        contract=contract,
        output_dir=model_output_dir,
        candidate_root=candidate_root,
        contract_sha256=hashes["contract_sha256"],
        force_regenerate=force_regenerate,
    )
    phasetracer_result, phasetracer_action = _reuse_or_compile_phasetracer_artifact(
        markdown_text,
        settings,
        contract=contract,
        output_dir=model_output_dir,
        candidate_root=candidate_root,
        contract_sha256=hashes["contract_sha256"],
        force_regenerate=force_regenerate,
        clang_format=clang_format,
        phasetracer_root=resolved_phasetracer_root,
    )
    smoke_result = run_phasetracer_smoke(
        phasetracer_result.model_path.parent,
        phasetracer_root=resolved_phasetracer_root,
        linux_runner=linux_runner,
        wsl_distro=wsl_distro,
        run_transition_smoke=run_transition_smoke,
    )
    if not smoke_result.ok:
        raise CompileBlocked(f"Generated PhaseTracer project failed cross-backend smoke check: {smoke_result.status}\n{smoke_result.stderr or smoke_result.stdout}")
    contract_reference_status = compare_phasetracer_smoke_output(contract, smoke_result.stdout)
    python_model_status, comparison_points = _compare_phasetracer_smoke_output_to_python_model_details(contract, python_result.model_path, smoke_result.stdout)
    warnings = list(python_result.warnings) + list(phasetracer_result.warnings)
    if smoke_result.stderr.strip():
        warnings.append("PhaseTracer smoke emitted stderr output.")
    status = f"{smoke_result.status}; {contract_reference_status}; {python_model_status}"
    result = BackendCompareResult(
        status=status,
        python_result=python_result,
        phasetracer_result=phasetracer_result,
        report_json_path=compare_report_dir / "backend_compare.json",
        report_markdown_path=compare_report_dir / "backend_compare.md",
        phasetracer_smoke_status=smoke_result.status,
        contract_reference_status=contract_reference_status,
        python_model_status=python_model_status,
        comparison_points=comparison_points,
        warnings=warnings,
        artifact_actions={
            "cosmotransitions": python_action,
            "phasetracer": phasetracer_action,
        },
    )
    _write_backend_compare_reports(result)
    return result


def _reuse_or_compile_python_artifact(
    markdown_text: str,
    settings: Settings,
    *,
    contract: dict[str, Any],
    output_dir: Path | None,
    candidate_root: Path,
    contract_sha256: str,
    force_regenerate: bool,
) -> tuple[CompileResult, str]:
    from .template_contract import compile_contract_template

    model_path = _candidate_python_model_path(contract, candidate_root)
    if not force_regenerate and _python_contract_hash(model_path) == contract_sha256:
        return (
            CompileResult(
                model_path=model_path,
                import_check_status="reused existing cosmotransitions artifact",
                usage_instructions=compile_usage_instructions(model_path, "cosmotransitions"),
            ),
            "reused",
        )
    existed_before = model_path.exists()
    result = compile_contract_template(
        markdown_text,
        settings,
        output_dir=output_dir,
        run_import_check=True,
    )
    action = "regenerated_forced" if force_regenerate else ("regenerated_stale" if existed_before else "generated")
    return result, action


def _reuse_or_compile_phasetracer_artifact(
    markdown_text: str,
    settings: Settings,
    *,
    contract: dict[str, Any],
    output_dir: Path | None,
    candidate_root: Path,
    contract_sha256: str,
    force_regenerate: bool,
    clang_format: bool,
    phasetracer_root: str,
) -> tuple[CompileResult, str]:
    from .phasetracer_backend import compile_phasetracer_template

    header_path = _candidate_phasetracer_header_path(contract, candidate_root)
    if not force_regenerate and _phasetracer_contract_hash(header_path.parent) == contract_sha256:
        return (
            CompileResult(
                model_path=header_path,
                import_check_status="reused existing phasetracer artifact",
                usage_instructions=compile_usage_instructions(header_path, "phasetracer", phasetracer_root=phasetracer_root),
            ),
            "reused",
        )
    existed_before = header_path.exists()
    result = compile_phasetracer_template(
        markdown_text,
        settings,
        output_dir=output_dir,
        run_smoke=False,
        clang_format=clang_format,
        phasetracer_root=phasetracer_root,
    )
    action = "regenerated_forced" if force_regenerate else ("regenerated_stale" if existed_before else "generated")
    return result, action


def _candidate_python_model_path(contract: dict[str, Any], root: Path) -> Path:
    slug = model_artifact_slug(contract)
    filename = "model.py" if slug == "model" else f"{slug}_model.py"
    return Path(root) / "cosmotransitions" / filename


def _candidate_phasetracer_header_path(contract: dict[str, Any], root: Path) -> Path:
    slug = model_artifact_slug(contract)
    return Path(root) / "phasetracer" / slug / f"{slug}_potential.hpp"


def _contract_hashes(markdown_text: str, contract: dict[str, Any]) -> dict[str, str]:
    return {
        "template_sha256": _sha256_text(markdown_text),
        "contract_sha256": _sha256_contract(contract),
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_contract(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _python_contract_hash(model_path: Path) -> str:
    if not model_path.exists():
        return ""
    text = model_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^PTAGENT_CONTRACT_SHA256\s*=\s*['\"]([0-9a-f]{64})['\"]", text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _phasetracer_contract_hash(project_dir: Path) -> str:
    metadata_path = project_dir / "metadata.json"
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        value = str(payload.get("contract_sha256", ""))
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    for header_path in sorted(project_dir.glob("*_potential.hpp")):
        if not header_path.exists():
            continue
        text = header_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"PTAGENT_CONTRACT_SHA256=([0-9a-f]{64})", text)
        if match:
            return match.group(1)
    return ""


def compare_phasetracer_smoke_output_to_python_model(
    contract: dict[str, Any],
    python_model_path: str | Path,
    stdout: str,
    *,
    abs_tol: float = BACKEND_COMPARE_ABS_TOL,
    rel_tol: float = BACKEND_COMPARE_REL_TOL,
) -> str:
    status, _points = _compare_phasetracer_smoke_output_to_python_model_details(
        contract,
        python_model_path,
        stdout,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
    )
    return status


def _compare_phasetracer_smoke_output_to_python_model_details(
    contract: dict[str, Any],
    python_model_path: str | Path,
    stdout: str,
    *,
    abs_tol: float = BACKEND_COMPARE_ABS_TOL,
    rel_tol: float = BACKEND_COMPARE_REL_TOL,
) -> tuple[str, list[dict[str, Any]]]:
    """Compare a generated CosmoTransitions Python model to PhaseTracer smoke output.

    This is intentionally outside both backend renderers: the Python and C++
    programs are generated independently, and this function acts only as a
    cross-backend referee.
    """
    _ = contract
    model = load_generated_python_model(Path(python_model_path))
    detailed = parse_detailed_smoke_output(stdout)
    indices = sorted(detailed)
    errors: list[str] = []
    comparison_points: list[dict[str, Any]] = []
    if not indices:
        raise CompileBlocked("PhaseTracer/Python-model numerical comparison failed: no PTAGENT_POINT smoke output found; regenerate and run the current run_model.cpp.")
    for index in indices:
        point_location = _point_location_from_smoke(detailed.get(index))
        if point_location is None:
            errors.append(f"smoke point {index} is missing T scalar or phi vector metadata")
            continue
        phi, temperature = point_location
        scalars: dict[str, float] = {}
        point = detailed.get(index)
        if point is None:
            errors.append(f"missing smoke point {index}")
            continue
        scalars = _as_dict(point.get("scalars"))
        if "V" not in scalars:
            errors.append(f"missing smoke point {index} V")
            continue
        cpp_value = float(scalars["V"])
        py_value = evaluate_python_model_vtot(model, phi, temperature, include_radiation=True)
        abs_diff = abs(float(py_value) - float(cpp_value))
        rel_diff = abs_diff / max(abs(float(py_value)), abs(float(cpp_value)), 1.0)
        comparison_points.append(
            {
                "index": index,
                "phi": phi,
                "T": temperature,
                "cosmotransitions_vtot": float(py_value),
                "phasetracer_v": float(cpp_value),
                "abs_diff": abs_diff,
                "rel_diff": rel_diff,
            }
        )
        _append_scalar_error(
            errors,
            f"point {index} Python model Vtot(include_radiation=True) vs C++ V",
            py_value,
            cpp_value,
            abs_tol=abs_tol,
            rel_tol=rel_tol,
        )
        if detailed and "raddof" in scalars:
            _append_scalar_error(
                errors,
                f"point {index} radiation d.o.f.",
                _python_model_raddof(model),
                scalars["raddof"],
                abs_tol=BACKEND_COMPARE_LOOP_ABS_TOL,
                rel_tol=BACKEND_COMPARE_LOOP_REL_TOL,
            )
    if errors:
        raise CompileBlocked("PhaseTracer/Python-model numerical comparison failed: " + "; ".join(errors[:8]))
    return f"python-model comparison ok ({len(indices)} points, abs_tol={abs_tol:g}, rel_tol={rel_tol:g})", comparison_points


def _write_backend_compare_reports(result: BackendCompareResult) -> None:
    payload = result.to_dict()
    result.report_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result.report_markdown_path.write_text(_render_backend_compare_markdown(payload), encoding="utf-8")


def _render_backend_compare_markdown(payload: dict[str, Any]) -> str:
    warnings = payload.get("warnings") or []
    warning_text = "\n".join(f"- {item}" for item in warnings) if warnings else "- none"
    artifact_actions = payload.get("artifact_actions") or {}
    points = payload.get("comparison_points") or []
    point_rows = "\n".join(
        "| {index} | `{phi}` | {temperature} | {python_v} | {cpp_v} | {abs_diff} | {rel_diff} |".format(
            index=point.get("index", ""),
            phi=_format_compare_vector(point.get("phi", [])),
            temperature=_format_compare_number(point.get("T", 0.0)),
            python_v=_format_compare_number(point.get("cosmotransitions_vtot", 0.0)),
            cpp_v=_format_compare_number(point.get("phasetracer_v", 0.0)),
            abs_diff=_format_compare_number(point.get("abs_diff", 0.0)),
            rel_diff=_format_compare_number(point.get("rel_diff", 0.0)),
        )
        for point in points
    ) or "| none | `[]` | n/a | n/a | n/a | n/a | n/a |"
    conclusion = "consistent" if "python-model comparison ok" in str(payload.get("python_model_status", "")) else "inconsistent"
    python_call = _first_usage_line(payload.get("python_usage_instructions") or [], "Run directly")
    python_import = _first_usage_line(payload.get("python_usage_instructions") or [], "Import usage")
    phasetracer_build = _first_usage_line(payload.get("phasetracer_usage_instructions") or [], "Linux build")
    phasetracer_smoke = _first_usage_line(payload.get("phasetracer_usage_instructions") or [], "Potential smoke")
    return textwrap.dedent(
        f"""
        # Backend Compare Summary

        Conclusion: **{conclusion}**

        ## Models

        - CosmoTransitions Python model: `{payload.get("python_model_path", "")}`
        - PhaseTracer C++ model: `{payload.get("phasetracer_model_path", "")}`
        - PhaseTracer project: `{payload.get("phasetracer_project_dir", "")}`
        - CosmoTransitions artifact action: `{artifact_actions.get("cosmotransitions", "")}`
        - PhaseTracer artifact action: `{artifact_actions.get("phasetracer", "")}`

        ## How To Call

        - CosmoTransitions direct: `{python_call}`
        - CosmoTransitions import: `{python_import}`
        - PhaseTracer build: `{phasetracer_build}`
        - PhaseTracer smoke: `{phasetracer_smoke}`

        ## Compared Points

        | point | phi | T | CosmoTransitions Vtot | PhaseTracer V | abs diff | rel diff |
        |---|---|---:|---:|---:|---:|---:|
        {point_rows}

        ## Result

        - PhaseTracer smoke: `{payload.get("phasetracer_smoke_status", "")}`
        - Contract reference: `{payload.get("contract_reference_status", "")}`
        - Python model comparison: `{payload.get("python_model_status", "")}`
        - Summary: **{conclusion}**

        ## Warnings

        {warning_text}
        """
    ).strip() + "\n"


def render_backend_compare_summary(result: BackendCompareResult) -> str:
    return _render_backend_compare_markdown(result.to_dict())


def _first_usage_line(lines: list[Any], prefix: str) -> str:
    for line in lines:
        text = str(line).strip()
        if text.startswith(prefix):
            return text
    return str(lines[0]).strip() if lines else "not recorded"


def _format_compare_vector(values: Any) -> str:
    if not isinstance(values, list):
        return "[]"
    return "[" + ", ".join(_format_compare_number(value) for value in values) + "]"


def _format_compare_number(value: Any) -> str:
    try:
        return f"{float(value):.12g}"
    except (TypeError, ValueError):
        return str(value)


def load_generated_python_model(model_path: Path) -> Any:
    path = model_path.resolve()
    module_name = f"_ptagent_python_model_{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CompileBlocked(f"Could not import generated Python model from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "build_model"):
        raise CompileBlocked(f"Generated Python model {path} has no build_model() function.")
    try:
        return module.build_model(run_tc=False, print_tc=False)
    except TypeError:
        return module.build_model()


def evaluate_python_model_vtot(model: Any, phi: list[float], temperature: float, *, include_radiation: bool = True) -> float:
    value = model.Vtot(np.asarray(phi, dtype=float), np.asarray(float(temperature), dtype=float), include_radiation=include_radiation)
    return _finite_float(np.asarray(value, dtype=float).item())


def _point_location_from_smoke(point: dict[str, Any] | None) -> tuple[list[float], float] | None:
    if not isinstance(point, dict):
        return None
    scalars = _as_dict(point.get("scalars"))
    vectors = _as_dict(point.get("vectors"))
    phi = vectors.get("phi")
    if "T" not in scalars or not isinstance(phi, list):
        return None
    return [float(value) for value in phi], float(scalars["T"])


def _python_model_raddof(model: Any) -> float:
    # Generated models include the contract's full radiation contribution in
    # Vtot. PhaseTracer's get_raddof() therefore has no omitted species to add.
    return 0.0


def _optional_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite_float(value: float) -> float:
    number = float(value)
    if not np.isfinite(number):
        raise CompileBlocked(f"Non-finite numerical comparison value: {number}")
    return number


def _append_scalar_error(
    errors: list[str],
    label: str,
    expected: float,
    actual: float,
    *,
    abs_tol: float,
    rel_tol: float,
) -> None:
    abs_err = abs(float(expected) - float(actual))
    scale = max(abs(float(expected)), abs(float(actual)), 1.0)
    rel_err = abs_err / scale
    if abs_err > abs_tol and rel_err > rel_tol:
        errors.append(
            f"{label} mismatch: python={expected:.17g}, cpp={actual:.17g}, "
            f"abs_err={abs_err:.3g}, rel_err={rel_err:.3g}"
        )
