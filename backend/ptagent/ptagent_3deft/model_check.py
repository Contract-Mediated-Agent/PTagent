from __future__ import annotations

import os
import json
import math
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .contract import ThreeDeftBlocked
from ..runtime_metadata import collect_software_versions


USER_CONFIG_ENV = "PTAGENT_CONFIG"
PHASETRACER_ROOT_ENV = "PTAGENT_PHASETRACER_ROOT"
DEFAULT_ATOL = 1.0e-9
DEFAULT_RTOL = 1.0e-8


def validate_tolerances(atol: float, rtol: float) -> None:
    if not all(math.isfinite(value) and value >= 0 for value in (atol, rtol)):
        raise ThreeDeftBlocked("atol and rtol must be finite, nonnegative numbers.")


def json_safe_numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe_numbers(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe_numbers(item) for item in value]
    return value


def finite_comparison(actual: float | None, expected: float | None, *, atol: float, rtol: float) -> dict:
    validate_tolerances(atol, rtol)
    finite = all(value is not None and math.isfinite(value) for value in (actual, expected))
    difference = abs(actual - expected) if finite else None
    limit = atol + rtol * max(abs(actual), abs(expected), 1.0) if finite else None
    valid_error = finite and math.isfinite(difference) and math.isfinite(limit)
    return {"actual": actual if actual is not None and math.isfinite(actual) else None,
            "expected": expected if expected is not None and math.isfinite(expected) else None,
            "finite": finite, "abs_diff": difference if valid_error else None, "limit": limit if valid_error else None,
            "ok": bool(valid_error and difference <= limit)}


@dataclass(frozen=True)
class ModelRunResult:
    project_dir: Path
    build_dir: Path
    configured: bool
    built: bool
    ran: bool
    stdout_preview: str
    stderr_preview: str
    validation_status: str = "generated_unverified"
    report_path: Path | None = None


def configure_build_run_phasetracer_model(
    project_dir: str | Path,
    *,
    phasetracer_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    configure_only: bool = False,
    no_run: bool = False,
    timeout_seconds: int = 300,
    atol: float | None = None,
    rtol: float | None = None,
) -> ModelRunResult:
    """Configure, build, and optionally run the generated PhaseTracer run_model target."""

    project = Path(project_dir).resolve()
    metadata_path = project / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    tolerances = metadata.get("numerical_validation", {})
    atol = tolerances.get("atol", DEFAULT_ATOL) if atol is None else atol
    rtol = tolerances.get("rtol", DEFAULT_RTOL) if rtol is None else rtol
    validate_tolerances(atol, rtol)
    if not (project / "CMakeLists.txt").exists():
        raise ThreeDeftBlocked(f"Generated PhaseTracer project is missing CMakeLists.txt: {project}")
    root = resolve_phasetracer_root(phasetracer_root)
    build = Path(build_dir).resolve() if build_dir else project / "build"
    build.mkdir(parents=True, exist_ok=True)

    supports_cli_tolerances = bool(metadata.get("numerical_validation"))
    # An interrupted or failed rebuild must not leave the previous success current.
    report_path = project / "model_check.json"
    pending = {"schema": "ptagent.3deft.model_check.v1", "status": "generated_unverified",
               "reason": "New verification attempt; no successful numerical run recorded yet.",
               "atol": atol, "rtol": rtol, "configured": False, "built": False, "ran": False}
    report_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    if metadata:
        metadata.setdefault("numerical_validation", {}).update(
            status="generated_unverified", report_path=str(report_path))
        metadata_path.write_text(json.dumps(json_safe_numbers(metadata), indent=2, allow_nan=False), encoding="utf-8")

    _run(["cmake", "-S", str(project), "-B", str(build), f"-DPHASETRACER_ROOT={root}"], timeout_seconds=timeout_seconds)
    pending["configured"] = True
    report_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    if configure_only:
        return _result(project, build, configured=True, built=False, ran=False, report_path=report_path)

    _run(["cmake", "--build", str(build), "--target", "run_model"], timeout_seconds=timeout_seconds)
    pending["built"] = True
    report_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    if no_run:
        return _result(project, build, configured=True, built=True, ran=False, report_path=report_path)

    exe = _run_model_executable(build)
    command = [str(exe)]
    if supports_cli_tolerances:
        command += ["--tolerances", str(atol), str(rtol)]
    completed = _run(command, timeout_seconds=timeout_seconds, require_success=False)
    report = check_reference_output(metadata, completed.stdout, atol=atol, rtol=rtol)
    if completed.returncode not in (0, 3) or (completed.returncode == 3 and report["status"] == "passed"):
        report["status"] = "failed"
    report.update(configured=True, built=True, ran=True, returncode=completed.returncode,
                  stdout=completed.stdout, stderr=completed.stderr, command=command,
                  phasetracer_root=str(root), timeout_seconds=timeout_seconds)
    report["software_versions"] = collect_software_versions(phasetracer_root=root)
    report_path = project / "model_check.json"
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    metadata.setdefault("numerical_validation", {}).update(
        status=report["status"], atol=atol, rtol=rtol, report_path=str(report_path))
    metadata_path.write_text(json.dumps(json_safe_numbers(metadata), indent=2, allow_nan=False), encoding="utf-8")
    return _result(
        project,
        build,
        configured=True,
        built=True,
        ran=True,
        stdout=completed.stdout,
        stderr=completed.stderr,
        validation_status=report["status"],
        report_path=report_path,
    )


def check_reference_output(metadata: dict, stdout: str, *, atol: float, rtol: float) -> dict:
    """Check against metadata, not the executable's self-reported success/expected values."""
    validate_tolerances(atol, rtol)
    pairs = {
        "DirectV3D": "v3d_reference_value_python",
        "DirectPrefactor": "prefactor_reference_value_python",
        "V": "reference_value_python",
        "FullPathVDiagnostic": "full_pipeline_reference_value_python",
    }
    report = {"schema": "ptagent.3deft.model_check.v1", "status": "generated_unverified",
              "atol": atol, "rtol": rtol, "comparisons": {},
              "full_path_layer": "generated_runtime_vs_python_runtime_not_independent_dralgo",
              "downstream_policy": "Do not use an unverified/failed model for production phase scans."}
    if not metadata.get("reference_point_valid"):
        report["reason"] = "No finite valid reference point; generation is not numerical verification."
        return report
    values = {}
    for line in stdout.splitlines():
        tokens = line.split()
        if len(tokens) == 2 and tokens[0].startswith("Reference."):
            try:
                values[tokens[0].split(".", 1)[1]] = float(tokens[1])
            except ValueError:
                pass
    for label, key in pairs.items():
        report["comparisons"][label] = finite_comparison(values.get(label), metadata.get(key), atol=atol, rtol=rtol)
    report["status"] = "passed" if all(row["ok"] for row in report["comparisons"].values()) else "failed"
    return report


def resolve_phasetracer_root(override: str | Path | None = None) -> Path:
    """Resolve and validate the PhaseTracer source root for 3DEFT model checks."""

    raw = str(override or "").strip()
    if not raw:
        raw = os.environ.get(PHASETRACER_ROOT_ENV, "").strip()
    if not raw:
        raw = _configured_phasetracer_root()
    if not raw:
        raise ThreeDeftBlocked(_missing_phasetracer_root_message())

    root = Path(os.path.expandvars(raw)).expanduser()
    if not root.exists():
        raise ThreeDeftBlocked(f"PhaseTracer source root does not exist: {root}\n{_phasetracer_root_help()}")
    if not root.is_dir():
        raise ThreeDeftBlocked(f"PhaseTracer source root is not a directory: {root}\n{_phasetracer_root_help()}")
    if not (root / "CMakeLists.txt").exists():
        raise ThreeDeftBlocked(f"PhaseTracer source root does not contain CMakeLists.txt: {root}\n{_phasetracer_root_help()}")
    return root.resolve()


def _configured_phasetracer_root() -> str:
    path = _default_user_config_path()
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception as exc:
        raise ThreeDeftBlocked(f"Could not read PTagent config file {path}: {type(exc).__name__}: {exc}") from exc
    value = data.get("phasetracer_root") if isinstance(data, dict) else ""
    return str(value or "").strip()


def _default_user_config_path() -> Path:
    env_value = os.environ.get(USER_CONFIG_ENV, "").strip()
    if env_value:
        return Path(os.path.expandvars(env_value)).expanduser()
    return Path.home() / ".ptagent" / "config.toml"


def _missing_phasetracer_root_message() -> str:
    return "PhaseTracer source root is required.\n" + _phasetracer_root_help()


def _phasetracer_root_help() -> str:
    return (
        "PhaseTracer must be provided as a C++ source root, not as a Python package. "
        "Use the directory that contains PhaseTracer's CMakeLists.txt. "
        "Configure it once with `python -m ptagent init --phasetracer-root <PhaseTracer-root>`, "
        "or pass `--phasetracer-root <PhaseTracer-root>` to this command."
    )


def _result(
    project: Path,
    build: Path,
    *,
    configured: bool,
    built: bool,
    ran: bool,
    stdout: str = "",
    stderr: str = "",
    validation_status: str = "generated_unverified",
    report_path: Path | None = None,
) -> ModelRunResult:
    return ModelRunResult(
        project_dir=project,
        build_dir=build,
        configured=configured,
        built=built,
        ran=ran,
        stdout_preview=stdout[:2000],
        stderr_preview=stderr[:2000],
        validation_status=validation_status,
        report_path=report_path,
    )


def _run(command: list[str], *, timeout_seconds: int, require_success: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ThreeDeftBlocked(f"Required executable was not found while running: {' '.join(command)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ThreeDeftBlocked(f"Command timed out after {timeout_seconds} seconds: {' '.join(command)}") from exc
    if require_success and completed.returncode != 0:
        details = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        raise ThreeDeftBlocked(f"Command failed with code {completed.returncode}: {' '.join(command)}" + (f"\n{details}" if details else ""))
    return completed


def _run_model_executable(build: Path) -> Path:
    candidates = [
        build / "run_model.exe",
        build / "run_model",
        build / "Debug" / "run_model.exe",
        build / "Release" / "run_model.exe",
        build / "RelWithDebInfo" / "run_model.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    suffix = ".exe" if os.name == "nt" else ""
    return build / f"run_model{suffix}"


__all__ = ["ModelRunResult", "configure_build_run_phasetracer_model", "resolve_phasetracer_root"]
