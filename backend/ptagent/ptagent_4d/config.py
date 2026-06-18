from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .artifact_layout import GLOBAL_INPUT_DIRNAME


DEFAULT_RUNTIME_PYTHON = sys.executable
USER_CONFIG_ENV = "PTAGENT_CONFIG"
ARTIFACT_ROOT_ENV = "PTAGENT_ARTIFACT_ROOT"
RUNTIME_PYTHON_ENV = "PTAGENT_RUNTIME_PYTHON"
PHASETRACER_ROOT_ENV = "PTAGENT_PHASETRACER_ROOT"


class ConfigurationError(RuntimeError):
    """Raised when persistent PTagent runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    artifact_root: Path
    input_root: Path
    paper_root: Path
    upload_root: Path
    runtime_python: Path
    phasetracer_root: str
    source_span_limit: int
    pdf_markdown_enabled: bool
    vev_threshold: float
    review_required: bool
    runtime_python_configured: bool = False
    phasetracer_root_configured: bool = False

    @property
    def runtime_available(self) -> bool:
        return self.runtime_python.exists()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = Path(__file__).resolve().parent.parent
    config = load_user_config()
    artifact_root = _path_setting(
        key="artifact_root",
        env_name=ARTIFACT_ROOT_ENV,
        config=config,
        default=default_artifact_root,
    )
    # Compatibility fallback only. Normal workflow runs override this to
    # <task-dir>/input so each task is self-contained.
    input_root = artifact_root / GLOBAL_INPUT_DIRNAME
    paper_root = input_root
    upload_root = input_root / "uploads"
    runtime_python, runtime_python_configured = _path_setting_with_source(
        key="runtime_python",
        env_name=RUNTIME_PYTHON_ENV,
        config=config,
        default=Path(DEFAULT_RUNTIME_PYTHON),
    )
    phasetracer_root, phasetracer_root_configured = _string_setting_with_source(
        key="phasetracer_root",
        env_name=PHASETRACER_ROOT_ENV,
        config=config,
        default="",
    )

    return Settings(
        project_root=project_root,
        artifact_root=artifact_root,
        input_root=input_root,
        paper_root=paper_root,
        upload_root=upload_root,
        runtime_python=runtime_python,
        phasetracer_root=phasetracer_root,
        source_span_limit=_int_setting("source_span_limit", "PTAGENT_SOURCE_SPAN_LIMIT", config, 10),
        pdf_markdown_enabled=_bool_setting("pdf_markdown_enabled", "PTAGENT_PDF_MARKDOWN_ENABLED", config, True),
        vev_threshold=_float_setting("vev_threshold", "PTAGENT_VEV_THRESHOLD", config, 1.0),
        review_required=_bool_setting("review_required", "PTAGENT_REVIEW_REQUIRED", config, True),
        runtime_python_configured=runtime_python_configured,
        phasetracer_root_configured=phasetracer_root_configured,
    )


def default_user_config_path() -> Path:
    env_value = os.getenv(USER_CONFIG_ENV, "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path.home() / ".ptagent" / "config.toml"


def default_artifact_root() -> Path:
    try:
        return Path.home() / "PTagentRuns"
    except RuntimeError:
        return Path.cwd().resolve() / "PTagentRuns"


def load_user_config(config_path: str | Path | None = None) -> dict[str, object]:
    path = Path(config_path).expanduser().resolve() if config_path else default_user_config_path()
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}


def write_user_config(
    *,
    artifact_root: str | Path,
    runtime_python: str | Path,
    phasetracer_root: str = "",
    config_path: str | Path | None = None,
    force: bool = False,
) -> Path:
    path = Path(config_path).expanduser().resolve() if config_path else default_user_config_path()
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}. Pass --force to overwrite it.")
    artifact_path = Path(artifact_root).expanduser().resolve()
    lines = [
        "# PTagent user configuration",
        "# Command-line arguments override environment variables; environment variables override this file.",
        "",
        "# Basic configuration. Required for CosmoTransitions compile/check commands.",
        f"artifact_root = {_toml_string(str(artifact_path))}",
        f"runtime_python = {_toml_string(str(Path(runtime_python).expanduser().resolve()))}",
        "",
        "# Extended configuration. Leave empty until PhaseTracer or backend comparison is needed.",
        f"phasetracer_root = {_toml_string(str(phasetracer_root).strip())}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact_path.mkdir(parents=True, exist_ok=True)
    return path


def require_configured_runtime_python(settings: Settings) -> Path:
    ok, message = validate_runtime_python(settings.runtime_python)
    if not ok:
        if settings.runtime_python_configured:
            raise ConfigurationError(f"Configured runtime Python is not usable: {message}")
        raise ConfigurationError(
            "Default runtime Python is not usable for PTagent: "
            f"{message}. Run `python -m ptagent init --runtime-python <python-with-cosmoTransitions>`."
        )
    return settings.runtime_python


def require_configured_phasetracer_root(settings: Settings, override: str | None = None) -> str:
    root = str(override or "").strip()
    if root:
        ok, message = validate_phasetracer_root(root)
        if not ok:
            raise ConfigurationError(message)
        return root
    if not settings.phasetracer_root_configured or not str(settings.phasetracer_root).strip():
        raise ConfigurationError(
            "PhaseTracer source root is not configured. This is only required for "
            "`--backend phasetracer` or `compare-backends`. Run "
            "`python -m ptagent init --phasetracer-root <PhaseTracer-root>` first, "
            "or pass `--phasetracer-root <PhaseTracer-root>` to this command."
        )
    root = str(settings.phasetracer_root).strip()
    ok, message = validate_phasetracer_root(root)
    if not ok:
        raise ConfigurationError(message)
    return root


def validate_runtime_python(python_path: str | Path) -> tuple[bool, str]:
    path = Path(python_path).expanduser().resolve()
    if not path.exists():
        return False, f"Runtime Python does not exist: {path}"
    command = [
        str(path),
        "-c",
        "import cosmoTransitions, numpy, scipy, sympy; print('ok')",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "ok"


def validate_phasetracer_root(phasetracer_root: str) -> tuple[bool, str]:
    raw = str(phasetracer_root or "").strip()
    if not raw:
        return False, "PhaseTracer source root is required."
    path = Path(raw).expanduser()
    if path.exists():
        if not path.is_dir():
            return False, f"PhaseTracer source root is not a directory: {path}"
        if not (path / "CMakeLists.txt").exists():
            return False, f"PhaseTracer source root does not contain CMakeLists.txt: {path}"
        return True, "ok"
    if os.name == "nt" and _looks_like_linux_shell_path(raw):
        return True, "ok"
    return False, f"PhaseTracer source root does not exist: {path}"


def _path_setting(*, key: str, env_name: str, config: dict[str, object], default: Path | Callable[[], Path]) -> Path:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        value = config.get(key)
        raw = "" if value is None else str(value).strip()
    default_path = default() if callable(default) else default
    path = Path(raw).expanduser() if raw else Path(default_path).expanduser()
    return path.resolve()


def _path_setting_with_source(*, key: str, env_name: str, config: dict[str, object], default: Path | Callable[[], Path]) -> tuple[Path, bool]:
    raw = os.getenv(env_name, "").strip()
    configured = bool(raw)
    if not raw:
        value = config.get(key)
        raw = "" if value is None else str(value).strip()
        configured = bool(raw)
    default_path = default() if callable(default) else default
    path = Path(raw).expanduser() if raw else Path(default_path).expanduser()
    return path.resolve(), configured


def _string_setting_with_source(*, key: str, env_name: str, config: dict[str, object], default: str = "") -> tuple[str, bool]:
    raw = os.getenv(env_name, "").strip()
    configured = bool(raw)
    if not raw:
        value = config.get(key)
        raw = "" if value is None else str(value).strip()
        configured = bool(raw)
    return (raw if raw else default), configured


def _int_setting(key: str, env_name: str, config: dict[str, object], default: int) -> int:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return int(raw)
    value = config.get(key, default)
    return int(value)


def _float_setting(key: str, env_name: str, config: dict[str, object], default: float) -> float:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return float(raw)
    value = config.get(key, default)
    return float(value)


def _bool_setting(key: str, env_name: str, config: dict[str, object], default: bool) -> bool:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return _parse_bool(raw)
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    return _parse_bool(str(value))


def _parse_bool(value: str) -> bool:
    clean = value.strip().lower()
    if clean in {"1", "true", "yes", "on"}:
        return True
    if clean in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}.")


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _looks_like_linux_shell_path(value: str) -> bool:
    raw = value.strip()
    return raw.startswith("~/") or raw.startswith("/") or raw.startswith("$")
