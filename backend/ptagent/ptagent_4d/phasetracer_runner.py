from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PhaseTracerSmokeResult:
    ok: bool
    status: str
    stdout: str = ""
    stderr: str = ""


def run_phasetracer_smoke(
    project_dir: str | Path,
    *,
    phasetracer_root: str = "",
    linux_runner: str = "native",
    wsl_distro: str = "",
    run_transition_smoke: bool = False,
    timeout: int = 180,
) -> PhaseTracerSmokeResult:
    runner = (linux_runner or "native").strip().lower()
    if runner not in {"native", "wsl"}:
        return PhaseTracerSmokeResult(False, f"unsupported PhaseTracer Linux runner: {linux_runner!r}")
    root = str(phasetracer_root or "").strip()
    if not root:
        return PhaseTracerSmokeResult(
            False,
            "PhaseTracer source root is not configured. Run "
            "`python -m ptagent init --phasetracer-root <PhaseTracer-root>` first, "
            "or pass `--phasetracer-root <PhaseTracer-root>` to the PhaseTracer command.",
        )
    project_linux = windows_path_to_wsl(project_dir) if runner == "wsl" else native_linux_path(project_dir)
    build_linux = project_linux + "/build"
    commands = [
        f"cmake -S {shlex.quote(project_linux)} -B {shlex.quote(build_linux)} -DPHASETRACER_ROOT={shlex.quote(root)}",
        f"cmake --build {shlex.quote(build_linux)} --target run_model -j$(nproc)",
        shlex.quote(build_linux + "/run_model") + (" --transition" if run_transition_smoke else ""),
    ]
    command = " && ".join(commands)
    if runner == "wsl":
        args = ["wsl", "-d", wsl_distro or "Ubuntu", "--", "bash", "-lc", command]
    else:
        args = ["bash", "-lc", command]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - depends on host Linux environment.
        return PhaseTracerSmokeResult(False, f"{type(exc).__name__}: {exc}")
    ok_status = "phasetracer smoke+transition ok" if run_transition_smoke else "phasetracer smoke ok"
    status = ok_status if result.returncode == 0 else f"phasetracer smoke failed: exit {result.returncode}"
    return PhaseTracerSmokeResult(
        ok=result.returncode == 0,
        status=status,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def native_linux_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve()).replace("\\", "/")


def windows_path_to_wsl(path: str | Path) -> str:
    raw = str(path)
    converted = _windows_drive_path_to_wsl(raw)
    if converted:
        return converted
    resolved = str(Path(raw).expanduser().resolve())
    converted = _windows_drive_path_to_wsl(resolved)
    if converted:
        return converted
    return resolved.replace("\\", "/")


def _windows_drive_path_to_wsl(path_text: str) -> str:
    raw = str(path_text)
    raw_slashes = raw.replace("\\", "/")
    if len(raw_slashes) >= 3 and raw_slashes[1:3] == ":/":
        drive = raw_slashes[0].lower()
        rest = raw_slashes[3:].lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return ""
