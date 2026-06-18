from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PHASETRACER_DOWNLOAD_TAG = "2.2.2"
PHASETRACER_DOWNLOAD_URL = "https://github.com/PhaseTracer/PhaseTracer.git"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the local PTagent runtime environment before using the skill.")
    parser.add_argument("--project-root", default="", help="Optional PTagent repo root.")
    parser.add_argument("--phasetracer-root", default="", help="Optional explicit PhaseTracer source root.")
    args = parser.parse_args()

    from _bootstrap import ensure_ptagent_backend

    project_root = ensure_ptagent_backend(args.project_root)

    from ptagent import get_settings
    from ptagent.ptagent_4d.config import validate_phasetracer_root, validate_runtime_python

    settings = get_settings()
    project_root = project_root or settings.project_root

    current_python = Path(sys.executable).resolve()
    current_has_cosmo = importlib.util.find_spec("cosmoTransitions") is not None
    runtime_ok, runtime_message = validate_runtime_python(settings.runtime_python)

    explicit_phase_root = str(args.phasetracer_root or "").strip()
    configured_phase_root = explicit_phase_root or str(settings.phasetracer_root or "").strip()
    configured_phase_ok = False
    configured_phase_message = "not configured"
    if configured_phase_root:
        configured_phase_ok, configured_phase_message = validate_phasetracer_root(configured_phase_root)

    project_phase_candidates = _project_phasetracer_candidates(project_root, validate_phasetracer_root)
    phasetracer_ready = configured_phase_ok or bool(project_phase_candidates)
    cosmotransitions_ready = current_has_cosmo or runtime_ok

    questions: list[str] = []
    if not cosmotransitions_ready:
        questions.append(
            "CosmoTransitions is not available in the current Python or configured PTagent runtime. "
            "Ask the user whether to install it before continuing."
        )
    if not phasetracer_ready:
        questions.append(
            "No usable PhaseTracer source tree was found under the selected PTagent backend/project directory "
            "and none is configured. "
            f"Ask the user whether to download PhaseTracer tag {PHASETRACER_DOWNLOAD_TAG} "
            "into phasetracer_download_dir or provide an existing path."
        )

    phasetracer_download_dir = project_root / "PhaseTracer"
    payload: dict[str, Any] = {
        "ready": not questions,
        "project_root": str(project_root),
        "current_python": str(current_python),
        "current_python_has_cosmoTransitions": current_has_cosmo,
        "runtime_python": str(settings.runtime_python),
        "runtime_python_configured": settings.runtime_python_configured,
        "runtime_python_check_ok": runtime_ok,
        "runtime_python_check_message": runtime_message,
        "phasetracer_root": configured_phase_root,
        "phasetracer_root_configured": bool(configured_phase_root),
        "phasetracer_root_check_ok": configured_phase_ok,
        "phasetracer_root_check_message": configured_phase_message,
        "project_phasetracer_candidates": [str(path) for path in project_phase_candidates],
        "phasetracer_existing_version_check": "not_checked",
        "phasetracer_download_tag": PHASETRACER_DOWNLOAD_TAG,
        "phasetracer_download_url": PHASETRACER_DOWNLOAD_URL,
        "phasetracer_download_dir": str(phasetracer_download_dir),
        "phasetracer_download_command": _phasetracer_download_command(project_root),
        "questions": questions,
        "next_step": _next_step(questions),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 2


def _project_phasetracer_candidates(project_root: Path, validate_phasetracer_root) -> list[Path]:
    names = (
        "PhaseTracer",
        "phasetracer",
        "phase-tracer",
        "external/PhaseTracer",
        "external/phasetracer",
        "extern/PhaseTracer",
        "extern/phasetracer",
        "third_party/PhaseTracer",
        "third_party/phasetracer",
        "vendor/PhaseTracer",
        "vendor/phasetracer",
    )
    candidates: list[Path] = []
    for name in names:
        path = (project_root / name).expanduser()
        ok, _message = validate_phasetracer_root(str(path))
        if ok:
            candidates.append(path.resolve())
    return candidates


def _next_step(questions: list[str]) -> str:
    if not questions:
        return "Environment gate passed; continue with the PTagent workflow."
    return (
        "Stop before extraction or compilation. Ask the user the listed environment question(s); "
        "do not install packages or download PhaseTracer until the user explicitly approves."
    )


def _phasetracer_download_command(project_root: Path) -> str:
    target = Path(project_root).expanduser() / "PhaseTracer"
    parts = [
        "git",
        "clone",
        "--branch",
        PHASETRACER_DOWNLOAD_TAG,
        "--depth",
        "1",
        PHASETRACER_DOWNLOAD_URL,
        str(target),
    ]
    return " ".join(shlex.quote(part) for part in parts)


if __name__ == "__main__":
    raise SystemExit(main())
