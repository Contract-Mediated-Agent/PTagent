from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .contract import ThreeDeftBlocked


USER_CONFIG_ENV = "PTAGENT_CONFIG"
PHASETRACER_ROOT_ENV = "PTAGENT_PHASETRACER_ROOT"


@dataclass(frozen=True)
class ModelRunResult:
    project_dir: Path
    build_dir: Path
    configured: bool
    built: bool
    ran: bool
    stdout_preview: str
    stderr_preview: str


def configure_build_run_phasetracer_model(
    project_dir: str | Path,
    *,
    phasetracer_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    configure_only: bool = False,
    no_run: bool = False,
    timeout_seconds: int = 300,
) -> ModelRunResult:
    """Configure, build, and optionally run the generated PhaseTracer run_model target."""

    project = Path(project_dir)
    if not (project / "CMakeLists.txt").exists():
        raise ThreeDeftBlocked(f"Generated PhaseTracer project is missing CMakeLists.txt: {project}")
    root = resolve_phasetracer_root(phasetracer_root)
    build = Path(build_dir) if build_dir else project / "build"
    build.mkdir(parents=True, exist_ok=True)

    _run(["cmake", "-S", str(project), "-B", str(build), f"-DPHASETRACER_ROOT={root}"], timeout_seconds=timeout_seconds)
    if configure_only:
        return _result(project, build, configured=True, built=False, ran=False)

    _run(["cmake", "--build", str(build), "--target", "run_model"], timeout_seconds=timeout_seconds)
    if no_run:
        return _result(project, build, configured=True, built=True, ran=False)

    exe = _run_model_executable(build)
    completed = _run([str(exe)], timeout_seconds=timeout_seconds)
    return _result(
        project,
        build,
        configured=True,
        built=True,
        ran=True,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


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
) -> ModelRunResult:
    return ModelRunResult(
        project_dir=project,
        build_dir=build,
        configured=configured,
        built=built,
        ran=ran,
        stdout_preview=stdout[:2000],
        stderr_preview=stderr[:2000],
    )


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
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
    if completed.returncode != 0:
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
