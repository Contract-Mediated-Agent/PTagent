from __future__ import annotations

import os
import json
import math
import subprocess
import tempfile
import time
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
    log_dir: Path | None = None
    attempt_report_path: Path | None = None


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
    logs = project / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="model-check-", dir=logs))
    attempt_report_path = attempt / "report.json"
    pending = {"schema": "ptagent.3deft.model_check.v1", "status": "generated_unverified",
               "reason": "New verification attempt; no successful numerical run recorded yet.",
               "atol": atol, "rtol": rtol, "configured": False, "built": False, "ran": False,
               "phasetracer_root": str(root), "timeout_seconds": timeout_seconds,
               "log_dir": str(attempt), "attempt_report_path": str(attempt_report_path),
               "execution_status": "running", "stages": []}

    def save(report: dict) -> None:
        text = json.dumps(report, indent=2, allow_nan=False)
        attempt_report_path.write_text(text, encoding="utf-8")
        report_path.write_text(text, encoding="utf-8")

    def run_stage(stage: str, command: list[str], *, require_success: bool = True):
        receipt = {"stage": stage, "command": command, "status": "running", "returncode": None,
                   "stdout_path": str(attempt / f"{stage}.stdout.log"),
                   "stderr_path": str(attempt / f"{stage}.stderr.log"),
                   "timeout_seconds": timeout_seconds, "elapsed_seconds": None}
        pending["stages"].append(receipt)
        save(pending)
        try:
            completed = _run(command, timeout_seconds=timeout_seconds,
                             require_success=require_success, receipt=receipt)
            pending[{"configure": "configured", "build": "built", "run": "ran"}[stage]] = True
            return completed
        except ThreeDeftBlocked as exc:
            pending.update(execution_status=f"{stage}_{receipt['status']}", reason=str(exc))
            raise ThreeDeftBlocked(
                f"{exc}\nNumerical report: {report_path}\n"
                f"Stage logs: {attempt}\nAttempt report: {attempt_report_path}"
            ) from exc
        finally:
            save(pending)

    save(pending)
    if metadata:
        metadata.setdefault("numerical_validation", {}).update(
            status="generated_unverified", report_path=str(report_path))
        metadata_path.write_text(json.dumps(json_safe_numbers(metadata), indent=2, allow_nan=False), encoding="utf-8")

    run_stage("configure", ["cmake", "-S", str(project), "-B", str(build), f"-DPHASETRACER_ROOT={root}"])
    if configure_only:
        pending["execution_status"] = "configure_only"
        save(pending)
        return _result(project, build, configured=True, built=False, ran=False, report_path=report_path,
                       log_dir=attempt, attempt_report_path=attempt_report_path)

    run_stage("build", ["cmake", "--build", str(build), "--target", "run_model"])
    if no_run:
        pending["execution_status"] = "built_only"
        save(pending)
        return _result(project, build, configured=True, built=True, ran=False, report_path=report_path,
                       log_dir=attempt, attempt_report_path=attempt_report_path)

    exe = _run_model_executable(build)
    command = [str(exe)]
    if supports_cli_tolerances:
        command += ["--tolerances", str(atol), str(rtol)]
    completed = run_stage("run", command, require_success=False)
    report = check_reference_output(metadata, completed.stdout, atol=atol, rtol=rtol)
    if completed.returncode not in (0, 3) or (completed.returncode == 3 and report["status"] == "passed"):
        report["status"] = "failed"
    report.update(configured=True, built=True, ran=True, returncode=completed.returncode,
                  stdout=completed.stdout, stderr=completed.stderr, command=command,
                  phasetracer_root=str(root), timeout_seconds=timeout_seconds,
                  log_dir=str(attempt), attempt_report_path=str(attempt_report_path), stages=pending["stages"],
                  execution_status="success" if completed.returncode == 0 else "run_failed")
    report["software_versions"] = collect_software_versions(phasetracer_root=root)
    save(report)
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
        log_dir=attempt,
        attempt_report_path=attempt_report_path,
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

    from ..ptagent_4d.config import validate_phasetracer_root
    from ..ptagent_4d.phasetracer_discovery import discover_phasetracer

    raw = str(override or "").strip()
    source = "command_line"
    if not raw:
        raw = os.environ.get(PHASETRACER_ROOT_ENV, "").strip()
        source = "environment"
    if not raw:
        raw = _configured_phasetracer_root()
        source = "configuration"
    result = discover_phasetracer(
        Path(__file__).resolve().parent.parent,
        explicit_root=os.path.expandvars(raw), explicit_source=source,
        validator=validate_phasetracer_root,
        require_runtime=False,
    )
    if not result["available"]:
        raise ThreeDeftBlocked(f"{result['message']}\n{_phasetracer_root_help()}")
    return Path(str(result["root"]))


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
    log_dir: Path | None = None,
    attempt_report_path: Path | None = None,
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
        log_dir=log_dir,
        attempt_report_path=attempt_report_path,
    )


def _run(
    command: list[str], *, timeout_seconds: int, require_success: bool = True, receipt: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    receipt = receipt if receipt is not None else {}
    receipt.update(status="error", returncode=None)
    stdout, stderr = "", ""
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        stdout, stderr = completed.stdout, completed.stderr
        receipt.update(status="success" if completed.returncode == 0 else "failed", returncode=completed.returncode)
    except OSError as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, FileNotFoundError):
            raise ThreeDeftBlocked(f"Required executable was not found while running: {' '.join(command)}") from exc
        raise ThreeDeftBlocked(f"Could not run command: {' '.join(command)}\n{receipt['error']}") from exc
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired may contain bytes even when subprocess.run uses text=True.
        stdout, stderr = _output_text(exc.stdout), _output_text(exc.stderr)
        receipt.update(status="timeout", error=str(exc))
        raise ThreeDeftBlocked(f"Command timed out after {timeout_seconds} seconds: {' '.join(command)}") from exc
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - start
        for stream, output in (("stdout", stdout), ("stderr", stderr)):
            if path := receipt.get(f"{stream}_path"):
                Path(path).write_text(output, encoding="utf-8")
    if require_success and completed.returncode != 0:
        details = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        raise ThreeDeftBlocked(f"Command failed with code {completed.returncode}: {' '.join(command)}" + (f"\n{details}" if details else ""))
    return completed


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


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
