from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ptagent.wolfram_runtime import (WolframRuntime, WolframRuntimeError,
                                    find_wolframscript, get_wolfram_runtime)

from .contract import ThreeDeftBlocked
from .loader import compatibility_prelude


DRALGO_INSTALL_SOURCES = {
    "wolfram": 'PacletInstall["DRalgo/DRalgo"]',
    "github": 'PacletInstall["https://github.com/DR-algo/DRalgo/releases/latest/download/DRalgo.paclet"]',
}


@dataclass(frozen=True)
class WolframPreflightResult:
    ok: bool
    wolframscript_path: str
    dralgo_available: bool
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    runtime_ready: bool = False
    status: str = "not_checked"
    dralgo_status: str = "not_checked"
    runtime: WolframRuntime | None = None


def check_wolfram_and_dralgo(
    explicit_wolframscript: str | None = None,
    *,
    timeout_seconds: int = 45,
    runtime: WolframRuntime | None = None,
    source: Path | None = None,
    check_dralgo: bool = True,
) -> WolframPreflightResult:
    executable = runtime.executable if runtime else find_wolframscript(explicit_wolframscript)
    if not executable:
        return WolframPreflightResult(
            ok=False,
            wolframscript_path="",
            dralgo_available=False,
            message="wolframscript was not found. Install Wolfram Engine/Mathematica or pass --wolframscript <path>.",
            status="not_found",
        )
    try:
        runtime = runtime or get_wolfram_runtime(executable)
        runtime.prepare()
        if not check_dralgo:
            return WolframPreflightResult(True, executable, False, message="Wolfram runtime is available; DRalgo not requested.",
                                          runtime_ready=True, status="passed", runtime=runtime)
        prelude = compatibility_prelude(source, executable, runtime=runtime)
        code = prelude + '\nPrint["PTAGENT_DRALGO_OK"]; Exit[0];' if prelude else _dralgo_check_code()
        completed = _run_wolfram_code(executable, code, timeout_seconds=timeout_seconds, runtime=runtime, probe=True)
    except WolframRuntimeError as exc:
        return WolframPreflightResult(False, executable, False,
                                      stdout=exc.result.get("stdout", ""), stderr=exc.result.get("stderr", ""),
                                      message=str(exc), runtime_ready=bool(runtime and runtime.ready),
                                      status=exc.status, dralgo_status="unknown", runtime=runtime)
    except ThreeDeftBlocked as exc:
        return WolframPreflightResult(False, executable, False, message=str(exc),
                                      runtime_ready=bool(runtime and runtime.ready), status="failed",
                                      dralgo_status="load_failed", runtime=runtime)
    loaded = "PTAGENT_DRALGO_OK" in completed.stdout
    available = completed.returncode == 0 and loaded
    return WolframPreflightResult(
        ok=available,
        wolframscript_path=executable,
        dralgo_available=available,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message=(
            "DRalgo is available."
            if available
            else _wolfram_session_error_message(executable, completed.stdout, completed.stderr)
            if loaded
            else _missing_dralgo_message(executable, completed.stdout, completed.stderr)
            if "PTAGENT_DRALGO_MISSING" in completed.stdout
            else "DRalgo initialization failed; package absence is not established.\n" + completed.stdout + completed.stderr
        ),
        runtime_ready=True,
        status="passed" if available else "failed",
        dralgo_status="available" if available else "missing" if "PTAGENT_DRALGO_MISSING" in completed.stdout else "load_failed",
        runtime=runtime,
    )


def install_dralgo(
    explicit_wolframscript: str | None = None,
    *,
    source: str = "wolfram",
    timeout_seconds: int = 600,
) -> WolframPreflightResult:
    executable = find_wolframscript(explicit_wolframscript)
    if not executable:
        raise ThreeDeftBlocked("Cannot install DRalgo because wolframscript was not found.")
    source_key = source.casefold()
    if source_key not in DRALGO_INSTALL_SOURCES:
        raise ThreeDeftBlocked(f"Unsupported DRalgo install source {source!r}; choose wolfram or github.")
    code = "\n".join(
        [
            'Print["PTAGENT_DRALGO_INSTALL_START"];',
            DRALGO_INSTALL_SOURCES[source_key] + ";",
            _dralgo_check_code(),
        ]
    )
    try:
        completed = _run_wolfram_code(executable, code, timeout_seconds=timeout_seconds)
    except WolframRuntimeError as exc:
        raise ThreeDeftBlocked(str(exc)) from exc
    available = completed.returncode == 0 and "PTAGENT_DRALGO_OK" in completed.stdout
    return WolframPreflightResult(
        ok=available,
        wolframscript_path=executable,
        dralgo_available=available,
        stdout=completed.stdout,
        stderr=completed.stderr,
        message="DRalgo installed and loaded." if available else _missing_dralgo_message(executable, completed.stdout, completed.stderr),
    )


def ensure_dralgo_ready(
    explicit_wolframscript: str | None = None,
    *,
    install_if_missing: bool = False,
    install_source: str = "wolfram",
    runtime: WolframRuntime | None = None,
    source: Path | None = None,
) -> str:
    preflight = check_wolfram_and_dralgo(explicit_wolframscript, runtime=runtime, source=source)
    if preflight.ok:
        return preflight.wolframscript_path
    if install_if_missing and preflight.dralgo_status == "missing":
        installed = install_dralgo(explicit_wolframscript or preflight.wolframscript_path, source=install_source)
        if installed.ok:
            return installed.wolframscript_path
        raise ThreeDeftBlocked(installed.message)
    raise ThreeDeftBlocked(preflight.message)


def _run_wolfram_code(executable: str, code: str, *, timeout_seconds: int,
                      runtime: WolframRuntime | None = None, probe: bool = False) -> subprocess.CompletedProcess[str]:
    runtime = runtime or get_wolfram_runtime(executable)
    result = runtime.run(code=code, timeout=timeout_seconds, stage="DRalgo initialization", probe=probe)
    return subprocess.CompletedProcess(result["command"], result["returncode"], result["stdout"], result["stderr"])


def _dralgo_check_code() -> str:
    return r'''
Module[{loadResult, symbolsReady},
  If[Quiet[FindFile["DRalgo`DRalgo`"]] === $Failed,
    Print["PTAGENT_DRALGO_MISSING"]; Exit[91]];
  Quiet[loadResult = Check[Get["DRalgo`DRalgo`"], $Failed]];
  symbolsReady = And[
    NameQ["AllocateTensors"],
    NameQ["ImportModelDRalgo"],
    NameQ["PerformDRhard"],
    NameQ["PerformDRsoft"]
  ];
  If[loadResult === $Failed || ! TrueQ[symbolsReady],
    Print["PTAGENT_DRALGO_LOAD_FAILED"];
    Print["PTAGENT_DRALGO_LOAD_RESULT=" <> ToString[InputForm[loadResult]]];
    Print["PTAGENT_DRALGO_SYMBOLS_READY=" <> ToString[symbolsReady]];
    Print["PTAGENT_DRALGO_PACLET=" <> ToString[PacletFind["DRalgo"]]];
    Exit[91],
    Print["PTAGENT_DRALGO_OK"];
    Print["PTAGENT_DRALGO_PACLET=" <> ToString[PacletFind["DRalgo"]]];
    Exit[0]
  ]
]
'''


def _missing_dralgo_message(executable: str, stdout: str, stderr: str) -> str:
    details = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    install_hint = (
        "DRalgo was not found on the selected kernel's package path. Check that path or install it explicitly:\n"
        f"  python -m ptagent --ptagent-engine 3deft install-dralgo --wolframscript \"{executable}\" --yes\n"
        "Alternative GitHub paclet route:\n"
        f"  python -m ptagent --ptagent-engine 3deft install-dralgo --wolframscript \"{executable}\" --source github --yes"
    )
    return install_hint + (f"\nWolfram output:\n{details}" if details else "")


def _wolfram_session_error_message(executable: str, stdout: str, stderr: str) -> str:
    details = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    lead = (
        "DRalgo loaded, but wolframscript exited with a nonzero status. "
        "Inspect the recorded session output before running DRalgo; package absence is not established."
    )
    return lead + (f"\nWolfram output:\n{details}" if details else f"\nExecutable: {executable}")
