"""One bounded, process-local Wolfram configuration for discovery and execution.

Only the harmless startup probe may fall back from WSTP to the *same* kernel.
Package/model evaluations are never retried. No persistent Wolfram settings change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import glob
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any


class WolframRuntimeError(RuntimeError):
    def __init__(self, status: str, message: str, *, result: dict | None = None):
        super().__init__(message)
        self.status = status
        self.result = result or {}


def find_wolframscript(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        expanded = os.path.expanduser(os.path.expandvars(explicit_path))
        if Path(expanded).is_file():
            return os.path.abspath(expanded)
        return shutil.which(expanded)
    for name in ("WOLFRAMSCRIPT", "WOLFRAM_SCRIPT"):
        if os.environ.get(name):
            # An explicit selection must not silently turn into another install.
            return find_wolframscript(os.environ[name])
    for name in ("wolframscript", "wolframscript.exe", "WolframScript.exe"):
        if path := shutil.which(name):
            return path
    patterns = (
        r"C:\Program Files\Wolfram Research\Mathematica\*\wolframscript.exe",
        r"C:\Program Files\Wolfram Research\Wolfram\*\wolframscript.exe",
        r"C:\Program Files\Wolfram Research\Wolfram Engine\*\wolframscript.exe",
        "/Applications/Mathematica.app/Contents/MacOS/wolframscript",
        "/Applications/Wolfram.app/Contents/MacOS/wolframscript",
        "/opt/Wolfram/WolframEngine/*/Executables/wolframscript",
        "/usr/local/Wolfram/Mathematica/*/Executables/wolframscript",
    )
    return next((p for pattern in patterns for p in sorted(glob.glob(pattern), reverse=True)
                 if Path(p).is_file()), None)


def _timeout(value: object, name: str) -> float:
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    except (TypeError, ValueError):
        pass
    raise WolframRuntimeError("invalid_configuration", f"{name} must be a positive finite number of seconds.")


def _stop_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=3, check=False)
        process.kill()


def run_bounded(command: list[str], *, env: dict[str, str], cwd: str,
                timeout: float, stage: str) -> dict[str, Any]:
    """Kill the owned group even if the parent exited leaving pipe-holding children."""
    timeout = _timeout(timeout, "timeout")
    start = time.monotonic()
    result: dict[str, Any] = dict(command=command, cwd=cwd, stage=stage,
                                  timeout_seconds=timeout, stdout="", stderr="")
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace",
                                   start_new_session=os.name == "posix")
    except OSError as exc:
        result.update(status="inaccessible", returncode=None, stderr=str(exc),
                      elapsed_seconds=time.monotonic() - start)
        return result
    status = "completed"
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        status = "timed_out"
        _stop_group(process)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            # A detached child may retain a pipe; never wait indefinitely for EOF.
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            process.stdout.close()
            process.stderr.close()
        stdout = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    finally:
        _stop_group(process)
        process.wait(timeout=3)
    result.update(status=status, returncode=process.returncode, stdout=stdout, stderr=stderr,
                  elapsed_seconds=time.monotonic() - start)
    return result


@dataclass
class WolframRuntime:
    executable: str
    environment: dict[str, str]
    cwd: str
    probe_timeout: float = 15
    run_timeout: float = 600
    kernel: str = ""
    mode: str = "wolframscript"
    attempts: list[dict] = field(default_factory=list)
    probes: dict[str, dict] = field(default_factory=dict)
    ready: bool = False
    startup_error: WolframRuntimeError | None = None
    version: str = ""

    def describe(self) -> dict:
        return dict(executable=self.executable, kernel=self.kernel, mode=self.mode,
                    cwd=self.cwd, temporary_directory=self.environment.get("TMPDIR", ""),
                    probe_timeout_seconds=self.probe_timeout, run_timeout_seconds=self.run_timeout,
                    ready=self.ready, version=self.version, startup_attempts=self.attempts)

    def _execute(self, *, code: str | None = None, file: Path | None = None,
                 timeout: float, stage: str, cwd: str | None = None) -> dict:
        directory = cwd or self.cwd
        if self.mode == "kernel":
            if file is not None:
                return run_bounded([self.kernel, "-script", str(file.resolve())],
                                   env=self.environment, cwd=directory, timeout=timeout, stage=stage)
            try:
                with tempfile.TemporaryDirectory(prefix="ptw-", dir=self.environment.get("TMPDIR")) as tmp:
                    script = Path(tmp) / "probe.wl"
                    script.write_text(code or "", encoding="utf-8")
                    return run_bounded([self.kernel, "-script", str(script)],
                                       env=self.environment, cwd=directory, timeout=timeout, stage=stage)
            except OSError as exc:
                raise WolframRuntimeError("inaccessible", f"Wolfram temporary directory is inaccessible: {exc}") from exc
        command = [self.executable, "-local"]
        if self.kernel:
            command.append(self.kernel)
        if stage == "startup":
            command.append("-verbose")
        command += ["-file", str(file.resolve())] if file is not None else ["-code", code or ""]
        return run_bounded(command, env=self.environment, cwd=directory, timeout=timeout, stage=stage)

    def prepare(self) -> None:
        if self.ready:
            return
        if self.startup_error is not None:
            raise self.startup_error
        code = ('Print["PTAGENT_RUNTIME=" <> ExportString[<|"kernel" -> First[$CommandLine], '
                '"version" -> $Version|>, "RawJSON"]];')
        result = self._execute(code=code, timeout=self.probe_timeout, stage="startup")
        self.attempts.append(result)
        if result["status"] == "completed" and result["returncode"] == 0:
            try:
                info = marker_json(result["stdout"], "PTAGENT_RUNTIME=")
                self.version = str(info["version"])
                self.kernel = str(info["kernel"])
                self.ready = True
                return
            except (ValueError, KeyError, TypeError):
                pass
        # Verbose WolframScript names its selected local kernel before opening WSTP.
        # Never scan for or substitute a different version after a launch failure.
        match = re.search(r'Using WolframKernel at:\s*"([^"\r\n]+)"', result["stderr"])
        if self.mode == "wolframscript" and match and Path(match[1]).is_file():
            self.kernel, self.mode = str(Path(match[1]).absolute()), "kernel"
            direct = self._execute(code=code, timeout=self.probe_timeout, stage="startup")
            self.attempts.append(direct)
            if direct["status"] == "completed" and direct["returncode"] == 0:
                try:
                    info = marker_json(direct["stdout"], "PTAGENT_RUNTIME=")
                    self.version = str(info["version"])
                    self.ready = True
                    return
                except (ValueError, KeyError, TypeError):
                    pass
            result = direct
        self.startup_error = execution_error(result, "Wolfram startup")
        raise self.startup_error

    def run(self, *, code: str | None = None, file: str | Path | None = None,
            timeout: float | None = None, stage: str = "evaluation", cwd: str | Path | None = None,
            probe: bool = False) -> dict:
        limit = _timeout(timeout if timeout is not None else
                         self.probe_timeout if probe else self.run_timeout, "timeout")
        self.prepare()
        # Only explicitly read-only, successful probes are memoized, never source runs.
        key = json.dumps([code, str(cwd or self.cwd)]) if probe and file is None else ""
        if key and key in self.probes:
            return {**self.probes[key], "cached": True}
        result = self._execute(code=code, file=Path(file) if file is not None else None,
                               timeout=limit, stage=stage, cwd=str(cwd) if cwd else None)
        if result["status"] != "completed":
            raise execution_error(result, stage)
        if key and result["returncode"] == 0:
            self.probes[key] = result
        return result


def marker_json(stdout: str, marker: str) -> Any:
    payload = stdout.partition(marker)[2].lstrip()
    return json.JSONDecoder().raw_decode(payload)[0]


def execution_error(result: dict, stage: str) -> WolframRuntimeError:
    status = result["status"] if result["status"] != "completed" else "failed"
    detail = (result.get("stdout", "") + "\n" + result.get("stderr", "")).strip()[-2000:]
    message = (f"{stage}: {status} (limit {result.get('timeout_seconds')} seconds, "
               f"exit {result.get('returncode')}). Package absence is not established. "
               "No installation, activation, or permission diagnosis follows from this result.")
    return WolframRuntimeError(status, message + ("\n" + detail if detail else ""), result=result)


_RUNTIMES: dict[tuple, WolframRuntime] = {}


def get_wolfram_runtime(executable: str | None = None) -> WolframRuntime:
    selected = find_wolframscript(executable)
    if not selected:
        raise WolframRuntimeError("not_found", f"Wolfram executable was not found: {executable or 'wolframscript'}")
    env = dict(os.environ)
    cwd = os.getcwd()
    # Bind relative temp roots once, before any export changes the child cwd.
    temp = env.get("PTAGENT_WOLFRAM_TMPDIR") or next(
        (env[n] for n in ("TMPDIR", "TMP", "TEMP") if env.get(n)), tempfile.gettempdir())
    temp = os.path.abspath(os.path.expanduser(temp))
    try:
        Path(temp).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WolframRuntimeError("inaccessible", f"Wolfram temporary directory is inaccessible: {temp}: {exc}") from exc
    env.update(TMPDIR=temp, TMP=temp, TEMP=temp)
    probe = _timeout(env.get("PTAGENT_WOLFRAM_PROBE_TIMEOUT", "15"), "PTAGENT_WOLFRAM_PROBE_TIMEOUT")
    limit = _timeout(env.get("PTAGENT_WOLFRAM_TIMEOUT", "600"), "PTAGENT_WOLFRAM_TIMEOUT")
    kernel_hint = env.get("PTAGENT_WOLFRAM_KERNEL") or env.get("WOLFRAMSCRIPT_KERNELPATH")
    kernel = ""
    if kernel_hint:
        kernel = find_wolframscript(kernel_hint) or ""
        if not kernel:
            raise WolframRuntimeError("inaccessible", f"Configured Wolfram kernel is inaccessible: {kernel_hint}")
    elif Path(selected).stem.casefold() in {"wolframkernel", "mathkernel", "math"}:
        kernel = selected
    mode = "kernel" if kernel else "wolframscript"
    key = (selected, cwd, tuple(sorted(env.items())))
    if key not in _RUNTIMES:
        _RUNTIMES[key] = WolframRuntime(selected, env, cwd, probe, limit, kernel, mode)
    return _RUNTIMES[key]
