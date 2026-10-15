from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PHASETRACER_DOWNLOAD_BRANCH = "main"
PHASETRACER_DOWNLOAD_URL = "https://github.com/PhaseTracer/PhaseTracer.git"
CURRENT_MODULES = ("numpy", "sympy", "scipy", "matplotlib", "pypdf", "pymupdf", "pdfplumber", "defusedxml", "cosmoTransitions")
RUNTIME_MODULES = ("numpy", "scipy", "sympy", "cosmoTransitions")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Check the local PTagent runtime environment before using the skill.")
    parser.add_argument("--project-root", default="", help="Optional PTagent repo root.")
    parser.add_argument("--phasetracer-root", default="", help="Optional explicit PhaseTracer source root.")
    parser.add_argument("--sarah-root", default="", help="Optional SARAH installation root used only by export-sarah.")
    parser.add_argument("--sarah-model", default="", help="Optional SARAH model name/directory, including the documentation placeholder path.")
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        print(json.dumps({"ready": False, "current_python_ok": False,
                          "missing": ["python_current"],
                          "advisories": ["PTagent requires Python 3.11 or newer; select a supported interpreter."]}))
        return 1

    from _bootstrap import ensure_ptagent_backend

    project_root = ensure_ptagent_backend(args.project_root)

    from ptagent import get_settings
    from ptagent.runtime_metadata import collect_software_versions
    from ptagent.ptagent_4d.config import validate_phasetracer_root
    from ptagent.ptagent_4d.sarah_discovery import SarahDiscoveryError, discover_sarah, resolve_sarah_model

    settings = get_settings()
    project_root = project_root or settings.project_root

    current_python = Path(sys.executable).resolve()
    current_python_ok = True
    current_modules = _probe_modules(current_python, CURRENT_MODULES)
    current_has_cosmo = current_modules["cosmoTransitions"]["ok"]
    current_has_defusedxml = current_modules["defusedxml"]["ok"]
    runtime_ok, runtime_message = _check_python_executable(settings.runtime_python)
    runtime_modules = ({name: current_modules[name] for name in RUNTIME_MODULES}
                       if Path(settings.runtime_python).expanduser().resolve() == current_python
                       else _probe_modules(settings.runtime_python, RUNTIME_MODULES,
                                           python_ready=runtime_ok, python_message=runtime_message))
    runtime_has_cosmo = runtime_modules["cosmoTransitions"]["ok"]
    runtime_cosmo_message = runtime_modules["cosmoTransitions"]["message"]

    explicit_phase_root = str(args.phasetracer_root or "").strip()
    configured_phase_root = explicit_phase_root or str(settings.phasetracer_root or "").strip()
    configured_phase_ok = False
    configured_phase_message = "not configured"
    if configured_phase_root:
        configured_phase_ok, configured_phase_message = validate_phasetracer_root(configured_phase_root)

    project_phase_candidates = _project_phasetracer_candidates(project_root, validate_phasetracer_root)
    phasetracer_ready = configured_phase_ok or bool(project_phase_candidates)

    wolfram_status = _check_wolfram_and_dralgo()
    wolfram_ready = bool(wolfram_status.get("wolfram_ready", wolfram_status["dralgo_available"]))
    dralgo_ready = bool(wolfram_status["dralgo_available"])
    sarah_options = {"sarah_root": args.sarah_root, "wolframscript": wolfram_status["wolframscript_path"]}
    if args.sarah_model:
        try:
            sarah_status = resolve_sarah_model(args.sarah_model, **sarah_options)
            sarah_status["model_available"] = True
        except SarahDiscoveryError as exc:
            sarah_status = discover_sarah(**sarah_options)
            sarah_status.update({"model_available": False, "diagnostics": [str(exc)],
                                 "requested_model": args.sarah_model})
    else:
        sarah_status = discover_sarah(**sarah_options)
    sarah_ready = bool(sarah_status["available"]) and sarah_status.get("model_available", True)

    operation_blockers = _operation_blockers(
        current_python_ok=current_python_ok,
        defusedxml_ready=current_has_defusedxml,
        runtime_python_ok=runtime_ok,
        cosmotransitions_ready=runtime_has_cosmo,
        phasetracer_ready=phasetracer_ready,
        wolfram_ready=wolfram_ready,
        dralgo_ready=dralgo_ready,
        sarah_ready=sarah_ready,
        current_modules={name: row["ok"] for name, row in current_modules.items()},
        runtime_modules={name: row["ok"] for name, row in runtime_modules.items()},
    )
    missing = _missing_components(
        runtime_python_ok=runtime_ok,
        cosmotransitions_ready=runtime_has_cosmo,
        phasetracer_ready=phasetracer_ready,
        wolfram_ready=wolfram_ready,
        dralgo_ready=dralgo_ready,
        defusedxml_ready=current_has_defusedxml,
        sarah_ready=sarah_ready,
    )
    advisories = _advisories(
        missing=missing,
        runtime_python_message=runtime_message,
        runtime_cosmo_message=runtime_cosmo_message,
        phasetracer_message=configured_phase_message,
        wolfram_message=str(wolfram_status["message"]),
        sarah_root=str(sarah_status.get("root") or ""),
    )
    advisories.extend(sarah_status.get("diagnostics", []))
    for label, modules, python in (("current", current_modules, current_python),
                                   ("runtime", runtime_modules, settings.runtime_python)):
        failed = [name for name, row in modules.items() if not row["ok"]]
        missing.extend(f"{name} ({label})" for name in failed)
        if failed:
            command = " ".join(shlex.quote(str(part)) for part in [python, "-m", "pip", "install", *failed])
            advisories.append(f"Missing or broken imports in {label} Python: {', '.join(failed)}. "
                              f"After user approval, install in that environment: {command}")

    phasetracer_download_dir = project_root / "PhaseTracer"
    payload: dict[str, Any] = {
        "schema": "ptagent.environment.v2",
        "ready": not operation_blockers["4d_extract"],
        "ready_meaning": "ready covers baseline 4D extraction only; inspect operation_blockers before backend-specific work.",
        "project_root": str(project_root),
        "current_python": str(current_python),
        "current_python_ok": current_python_ok,
        "current_python_has_cosmoTransitions": current_has_cosmo,
        "current_python_has_defusedxml": current_has_defusedxml,
        "python_modules": {"current": current_modules, "runtime": runtime_modules},
        "software_versions": collect_software_versions(
            runtime_python=settings.runtime_python,
            phasetracer_root=configured_phase_root,
            sarah_root=sarah_status.get("root"),
            wolframscript=wolfram_status["wolframscript_path"],
        ),
        "runtime_python": str(settings.runtime_python),
        "runtime_python_configured": settings.runtime_python_configured,
        "runtime_python_check_ok": runtime_ok,
        "runtime_python_check_message": runtime_message,
        "runtime_python_has_cosmoTransitions": runtime_has_cosmo,
        "runtime_python_cosmoTransitions_message": runtime_cosmo_message,
        "phasetracer_root": configured_phase_root,
        "phasetracer_root_configured": bool(configured_phase_root),
        "phasetracer_root_check_ok": configured_phase_ok,
        "phasetracer_root_check_message": configured_phase_message,
        "project_phasetracer_candidates": [str(path) for path in project_phase_candidates],
        "phasetracer_existing_version_check": "not_checked",
        "phasetracer_download_branch": PHASETRACER_DOWNLOAD_BRANCH,
        "phasetracer_download_url": PHASETRACER_DOWNLOAD_URL,
        "phasetracer_download_dir": str(phasetracer_download_dir),
        "phasetracer_download_command": _phasetracer_download_command(project_root),
        "wolfram": {
            "wolframscript_path": wolfram_status["wolframscript_path"],
            "wolframscript_found": bool(wolfram_status["wolframscript_path"]),
            "wolfram_ready": wolfram_ready,
            "dralgo_available": dralgo_ready,
            "message": wolfram_status["message"],
        },
        "sarah": {
            **sarah_status,
            "required_only_for": "export-sarah",
        },
        "missing": missing,
        "advisories": advisories,
        "operation_blockers": operation_blockers,
        "questions": [],
        "next_step": _next_step(operation_blockers),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


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


def _check_python_executable(python_path: str | Path) -> tuple[bool, str]:
    path = Path(python_path).expanduser().resolve()
    if not path.exists():
        return False, f"Runtime Python does not exist: {path}"
    command = [str(path), "-c", "import sys; print('ok')"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "ok"


def _check_python_module(
    python_path: str | Path,
    module: str,
    *,
    python_ready: bool,
    python_message: str,
) -> tuple[bool, str]:
    if not python_ready:
        return False, f"Runtime Python is not usable: {python_message}"
    path = Path(python_path).expanduser().resolve()
    command = [str(path), "-c", f"import {module}; print('ok')"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "ok"


def _probe_modules(python_path, modules, *, python_ready=True, python_message="ok"):
    results = {}
    for module in modules:
        # Import the same interface used by the PDF reader, not just a package
        # spec: an installed distribution can still have broken binary imports.
        import_name = "fitz" if module == "pymupdf" else module
        ok, message = _check_python_module(python_path, import_name,
                                          python_ready=python_ready, python_message=python_message)
        results[module] = {"ok": ok, "message": message}
    return results


def _check_wolfram_and_dralgo() -> dict[str, Any]:
    try:
        from ptagent.ptagent_3deft.environment import check_wolfram_and_dralgo

        result = check_wolfram_and_dralgo()
        return {
            "wolframscript_path": result.wolframscript_path,
            "wolfram_ready": result.ok or "PTAGENT_DRALGO_MISSING" in result.stdout,
            "dralgo_available": result.dralgo_available,
            "message": result.message,
        }
    except Exception as exc:
        return {
            "wolframscript_path": "",
            "wolfram_ready": False,
            "dralgo_available": False,
            "message": f"{type(exc).__name__}: {exc}",
        }


def _operation_blockers(
    *,
    current_python_ok: bool,
    defusedxml_ready: bool,
    runtime_python_ok: bool,
    cosmotransitions_ready: bool,
    phasetracer_ready: bool,
    wolfram_ready: bool,
    dralgo_ready: bool,
    sarah_ready: bool,
    current_modules: dict[str, bool] | None = None,
    runtime_modules: dict[str, bool] | None = None,
) -> dict[str, list[str]]:
    python_blockers = [] if current_python_ok else ["python_current"]
    xml_blockers = [] if defusedxml_ready else ["defusedxml"]
    runtime_blockers = [] if runtime_python_ok else ["python_runtime"]
    cosmo_blockers = [] if (cosmotransitions_ready or not runtime_python_ok) else ["cosmotransitions"]
    phasetracer_blockers = [] if phasetracer_ready else ["phasetracer"]
    wolfram_blockers = [] if wolfram_ready else ["wolfram"]
    dralgo_blockers = [] if dralgo_ready else ["dralgo"]
    sarah_blockers = [] if sarah_ready else ["sarah"]
    operations = {
        "4d_extract": python_blockers,
        "sarah_import": python_blockers + xml_blockers,
        "sarah_export": python_blockers + xml_blockers + wolfram_blockers + sarah_blockers,
        "4d_validate": python_blockers,
        "4d_resolve": python_blockers,
        "cosmotransitions_compile": runtime_blockers + cosmo_blockers,
        "4d_check_model": runtime_blockers + cosmo_blockers,
        "4d_run": runtime_blockers + cosmo_blockers,
        "phasetracer_compile": python_blockers + phasetracer_blockers,
        "compare_backends": runtime_blockers + cosmo_blockers + phasetracer_blockers,
        "3deft_extract": python_blockers + wolfram_blockers,
        "3deft_resolve": python_blockers,
        "3deft_run": python_blockers + wolfram_blockers + dralgo_blockers,
        "3deft_generate_dralgo": python_blockers,
        "3deft_merge_output": python_blockers,
        "3deft_compile": python_blockers,
        "3deft_check_model": python_blockers + phasetracer_blockers,
        "3deft_compare_mathematica": python_blockers + wolfram_blockers,
        "3deft_install_dralgo": python_blockers + wolfram_blockers,
    }
    # Missing optional arguments retain compatibility for external callers of
    # this helper; main always supplies the actual import-probe results.
    if current_modules is not None:
        core = [name for name in ("numpy", "sympy") if not current_modules.get(name, False)]
        for operation in ("4d_extract", "sarah_import", "sarah_export", "4d_validate", "4d_resolve",
                          "cosmotransitions_compile", "phasetracer_compile", "compare_backends"):
            operations[operation] = operations[operation] + core
    if runtime_modules is not None:
        missing_runtime = [f"{name} (runtime)" for name in ("numpy", "scipy", "sympy")
                           if not runtime_modules.get(name, False)]
        for operation in ("cosmotransitions_compile", "4d_check_model", "4d_run", "compare_backends"):
            operations[operation] = operations[operation] + missing_runtime
    pdf_ready = current_modules is None or any(current_modules.get(name, False)
                                               for name in ("pypdf", "pymupdf", "pdfplumber"))
    operations["4d_extract_pdf"] = operations["4d_extract"] + ([] if pdf_ready else ["pdf_reader"])
    operations["review_graph"] = python_blockers + ([] if current_modules is None or current_modules.get("matplotlib", False) else ["matplotlib"])
    operations["sarah_prepare"] = list(dict.fromkeys(operations["sarah_import"] + operations["review_graph"]))
    return operations


def _missing_components(
    *,
    runtime_python_ok: bool,
    cosmotransitions_ready: bool,
    phasetracer_ready: bool,
    wolfram_ready: bool,
    dralgo_ready: bool,
    defusedxml_ready: bool,
    sarah_ready: bool,
) -> list[str]:
    missing: list[str] = []
    if not runtime_python_ok:
        missing.append("python_runtime")
    if runtime_python_ok and not cosmotransitions_ready:
        missing.append("cosmotransitions")
    if not phasetracer_ready:
        missing.append("phasetracer")
    if not wolfram_ready:
        missing.append("wolfram")
    if wolfram_ready and not dralgo_ready:
        missing.append("dralgo")
    if not defusedxml_ready:
        missing.append("defusedxml")
    if not sarah_ready:
        missing.append("sarah")
    return missing


def _advisories(
    *,
    missing: list[str],
    runtime_python_message: str,
    runtime_cosmo_message: str,
    phasetracer_message: str,
    wolfram_message: str,
    sarah_root: str,
) -> list[str]:
    advisories: list[str] = []
    if "python_runtime" in missing:
        advisories.append(f"Runtime Python is not usable: {runtime_python_message}")
    if "cosmotransitions" in missing:
        advisories.append(
            "CosmoTransitions is missing from the configured runtime Python; "
            f"CosmoTransitions compile/check/run operations are blocked. Detail: {runtime_cosmo_message}"
        )
    if "phasetracer" in missing:
        advisories.append(
            "PhaseTracer is not configured or could not be validated; PhaseTracer compile and backend comparison are blocked. "
            f"Detail: {phasetracer_message}"
        )
    if "wolfram" in missing:
        advisories.append(
            "wolframscript was not found or could not be checked; 3DEFT extract/run/Mathematica comparison are blocked. "
            f"Detail: {wolfram_message}"
        )
    if "dralgo" in missing:
        advisories.append(
            "DRalgo could not be loaded; 3DEFT DRalgo run is blocked until DRalgo is installed. "
            f"Detail: {wolfram_message}"
        )
    if "defusedxml" in missing:
        advisories.append(
            "defusedxml is missing from the current Python; import-sarah and the import stage of export-sarah are blocked. "
            "Install the dependencies listed in requirements.txt in a user-managed Python environment."
        )
    if "sarah" in missing:
        advisories.append(
            "SARAH or the requested model could not be located; only export-sarah is blocked. "
            f"Selected root: {sarah_root or '<not found>'}. See sarah.diagnostics and searched_locations. "
            "import-sarah does not require SARAH."
        )
    return advisories


def _next_step(operation_blockers: dict[str, list[str]]) -> str:
    blocked = {name: blockers for name, blockers in operation_blockers.items() if blockers}
    if not blocked:
        return "Environment preflight passed for all known PTagent operations."
    return (
        "Environment preflight completed. Continue only with operations whose operation_blockers entry is empty; "
        "ask before installing packages, downloading PhaseTracer, or changing persistent config."
    )


def _phasetracer_download_command(project_root: Path) -> str:
    target = Path(project_root).expanduser() / "PhaseTracer"
    parts = [
        "git",
        "clone",
        "--branch",
        PHASETRACER_DOWNLOAD_BRANCH,
        "--depth",
        "1",
        PHASETRACER_DOWNLOAD_URL,
        str(target),
    ]
    return " ".join(shlex.quote(part) for part in parts)


if __name__ == "__main__":
    raise SystemExit(main())
