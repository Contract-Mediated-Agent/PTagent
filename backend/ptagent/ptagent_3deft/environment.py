from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .contract import ThreeDeftBlocked


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


def find_wolframscript(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        expanded = Path(os.path.expanduser(os.path.expandvars(explicit_path)))
        if expanded.exists():
            return str(expanded)
        located = shutil.which(explicit_path)
        if located:
            return located
        return None
    for env_name in ("WOLFRAMSCRIPT", "WOLFRAM_SCRIPT"):
        value = os.environ.get(env_name)
        if value:
            found = find_wolframscript(value)
            if found:
                return found
    for command in ("wolframscript", "wolframscript.exe", "WolframScript.exe"):
        located = shutil.which(command)
        if located:
            return located
    for candidate in _common_wolframscript_candidates():
        if Path(candidate).exists():
            return candidate
    return None


def check_wolfram_and_dralgo(
    explicit_wolframscript: str | None = None,
    *,
    timeout_seconds: int = 45,
) -> WolframPreflightResult:
    executable = find_wolframscript(explicit_wolframscript)
    if not executable:
        return WolframPreflightResult(
            ok=False,
            wolframscript_path="",
            dralgo_available=False,
            message="wolframscript was not found. Install Wolfram Engine/Mathematica or pass --wolframscript <path>.",
        )
    completed = _run_wolfram_code(executable, _dralgo_check_code(), timeout_seconds=timeout_seconds)
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
        ),
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
    completed = _run_wolfram_code(executable, code, timeout_seconds=timeout_seconds)
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
) -> str:
    preflight = check_wolfram_and_dralgo(explicit_wolframscript)
    if preflight.ok:
        return preflight.wolframscript_path
    if install_if_missing:
        installed = install_dralgo(explicit_wolframscript or preflight.wolframscript_path, source=install_source)
        if installed.ok:
            return installed.wolframscript_path
        raise ThreeDeftBlocked(installed.message)
    raise ThreeDeftBlocked(preflight.message)


def _run_wolfram_code(executable: str, code: str, *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [executable, "-code", code],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ThreeDeftBlocked(f"wolframscript executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ThreeDeftBlocked(f"wolframscript timed out after {timeout_seconds} seconds while checking DRalgo.") from exc


def _dralgo_check_code() -> str:
    return r'''
Module[{loadResult, symbolsReady},
  Quiet[loadResult = Check[Get["DRalgo`DRalgo`"], $Failed]];
  symbolsReady = And[
    NameQ["AllocateTensors"],
    NameQ["ImportModelDRalgo"],
    NameQ["PerformDRhard"],
    NameQ["PerformDRsoft"]
  ];
  If[loadResult === $Failed || ! TrueQ[symbolsReady],
    Print["PTAGENT_DRALGO_MISSING"];
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
        "DRalgo could not be loaded before the run. Install it first, or allow the 3DEFT CLI to install it:\n"
        f"  python -m ptagent --ptagent-engine 3deft install-dralgo --wolframscript \"{executable}\" --yes\n"
        "Alternative GitHub paclet route:\n"
        f"  python -m ptagent --ptagent-engine 3deft install-dralgo --wolframscript \"{executable}\" --source github --yes"
    )
    return install_hint + (f"\nWolfram output:\n{details}" if details else "")


def _wolfram_session_error_message(executable: str, stdout: str, stderr: str) -> str:
    details = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    lead = (
        "DRalgo loaded, but wolframscript exited with a nonzero status. "
        "Fix the Mathematica/WolframScript session, license, or kernel startup error before running DRalgo."
    )
    return lead + (f"\nWolfram output:\n{details}" if details else f"\nExecutable: {executable}")


def _common_wolframscript_candidates() -> list[str]:
    patterns = [
        r"C:\\Program Files\\Wolfram Research\\Mathematica\\*\\wolframscript.exe",
        r"C:\\Program Files\\Wolfram Research\\Wolfram\\*\\wolframscript.exe",
        r"C:\\Program Files\\Wolfram Research\\Wolfram Engine\\*\\wolframscript.exe",
        "/Applications/Mathematica.app/Contents/MacOS/wolframscript",
        "/Applications/Wolfram.app/Contents/MacOS/wolframscript",
        "/usr/local/bin/wolframscript",
        "/usr/bin/wolframscript",
        "/opt/Wolfram/WolframEngine/*/Executables/wolframscript",
        "/usr/local/Wolfram/Mathematica/*/Executables/wolframscript",
    ]
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(glob.glob(pattern))
    return sorted(matches, reverse=True)
