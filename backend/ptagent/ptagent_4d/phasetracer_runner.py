from __future__ import annotations

import json
import hashlib
import math
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PhaseTracerSmokeResult:
    ok: bool
    status: str
    stdout: str = ""
    stderr: str = ""
    report_path: Path | None = None
    transition_results: dict = field(default_factory=dict)
    stages: list[dict] = field(default_factory=list)


def phasetracer_stderr_warnings(result: PhaseTracerSmokeResult) -> list[str]:
    if result.stages:
        return [f"PhaseTracer {row['stage']} emitted stderr; inspect {row['stderr_path']}."
                for row in result.stages if row.get("stderr_bytes", 0)]
    return ["PhaseTracer emitted stderr output (stage unavailable)."] if result.stderr.strip() else []


def run_phasetracer_smoke(
    project_dir: str | Path,
    *,
    phasetracer_root: str = "",
    linux_runner: str = "native",
    wsl_distro: str = "",
    run_transition_smoke: bool = False,
    timeout: int = 180,
    build_jobs: int | None = None,
) -> PhaseTracerSmokeResult:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if build_jobs is not None and (isinstance(build_jobs, bool) or not isinstance(build_jobs, int) or build_jobs <= 0):
        raise ValueError("build_jobs must be a positive integer")
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
        ("configure", ["cmake", "-S", project_linux, "-B", build_linux, f"-DPHASETRACER_ROOT={root}"]),
        ("build", ["cmake", "--build", build_linux, "--target", "run_model", "--parallel", str(build_jobs or min(os.cpu_count() or 2, 8))]),
        ("run", [build_linux + "/run_model"] + (["--transition"] if run_transition_smoke else [])),
    ]
    project = Path(project_dir).expanduser().absolute()
    logs = project / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="phasetracer-", dir=logs))
    report_path = project / "phasetracer_run.json"
    report = {"schema": "ptagent.phasetracer.run.v1", "status": "running", "ok": False,
              "phasetracer_root": root, "project_dir": str(project), "log_dir": str(attempt),
              "transition_requested": run_transition_smoke, "timeout_seconds": timeout,
              "build_jobs": build_jobs or min(os.cpu_count() or 2, 8), "stages": [], "transition_results": {}}
    report["generated_file_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(project.iterdir()) if path.is_file()
        and (path.suffix in {".hpp", ".cpp"} or path.name in {"CMakeLists.txt", "metadata.json"})
    }

    def save():
        text = json.dumps(report, indent=2, allow_nan=False)
        (attempt / "report.json").write_text(text, encoding="utf-8")
        # Replacing the current receipt also invalidates a previous successful run.
        report_path.write_text(text, encoding="utf-8")

    save()
    stdout_parts, stderr_parts = [], []
    for stage, command in commands:
        args = (["wsl"] + (["-d", wsl_distro] if wsl_distro else [])
                + ["--", "bash", "-lc", shlex.join(command)]) if runner == "wsl" else command
        receipt = _run_stage(args, stage=stage, log_dir=attempt, timeout=timeout)
        report["stages"].append(receipt)
        stdout = Path(receipt["stdout_path"]).read_text(encoding="utf-8", errors="replace")
        stderr = Path(receipt["stderr_path"]).read_text(encoding="utf-8", errors="replace")
        stdout_parts.append(stdout)
        stderr_parts.append(stderr)
        if stage == "run":
            # A timeout or bounce failure must not discard already flushed Tc records.
            report["transition_results"] = parse_transition_output(stdout, requested=run_transition_smoke)
        if receipt["status"] != "success":
            report["status"] = f"{stage}_{receipt['status']}"
            break
        if stage == "run":
            report["ok"] = (report["transition_results"]["critical_status"] not in {"error", "invalid_output"}
                            and report["transition_results"]["nucleation_status"] not in {"error", "interrupted"})
            report["status"] = "success" if report["ok"] else "invalid_transition_output"
        save()
    save()
    ok_status = "phasetracer smoke+transition ok" if run_transition_smoke else "phasetracer smoke ok"
    if run_transition_smoke and report["transition_results"].get("nucleation_status") in {"partial", "unavailable"}:
        ok_status = "phasetracer smoke+critical ok; nucleation " + report["transition_results"]["nucleation_status"]
    status = ok_status if report["ok"] else f"phasetracer smoke failed: {report['status']}"
    status += f"; report: {report_path}"
    return PhaseTracerSmokeResult(report["ok"], status, "\n".join(stdout_parts), "\n".join(stderr_parts),
                                  report_path, report["transition_results"], report["stages"])


def _run_stage(command: list[str], *, stage: str, log_dir: Path, timeout: int) -> dict:
    stdout_path, stderr_path = log_dir / f"{stage}.stdout.log", log_dir / f"{stage}.stderr.log"
    receipt = {"stage": stage, "command": command, "status": "error", "returncode": None,
               "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "timeout_seconds": timeout}
    start = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                       start_new_session=os.name != "nt")
            try:
                receipt["returncode"] = process.wait(timeout=timeout)
                receipt["status"] = "success" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:  # pragma: no cover - Windows process tree
                    try:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, timeout=5, check=False)
                    except (OSError, subprocess.SubprocessError):
                        process.kill()
                process.wait(timeout=5)
                receipt["status"] = "timeout"
                receipt["returncode"] = process.returncode
                stderr.write(f"\n{stage} timed out after {timeout}s; process group stopped.\n")
        except OSError as exc:
            stderr.write(f"{type(exc).__name__}: {exc}\n")
    receipt["elapsed_seconds"] = time.monotonic() - start
    receipt["stderr_bytes"] = stderr_path.stat().st_size
    return receipt


def parse_transition_output(stdout: str, *, requested: bool) -> dict:
    """Read the generated runner's protocol, never estimate a missing Tn."""
    result = {"critical_status": "not_requested", "nucleation_status": "not_requested",
              "nucleation_method": "unspecified", "transitions": [], "phases": {}}
    if not requested:
        return result
    rows, count, status = {}, None, ""
    protocol, nucleation_stage = 1, ""
    try:
        for line in stdout.splitlines():
            tokens = line.split()
            if not tokens:
                continue
            if tokens[0] == "PTAGENT_TRANSITION_PROTOCOL":
                if tokens[1:] != ["2"] or protocol != 1:
                    raise ValueError("unknown/duplicate transition protocol")
                protocol = 2
            elif tokens[0] == "PTAGENT_PHASE_POINT":
                if tokens[2] != "T" or tokens[4] != "V" or tokens[6] != "X":
                    raise ValueError("invalid phase point")
                values = list(map(float, [tokens[3], tokens[5], *tokens[7:]]))
                if len(values) < 3 or not all(math.isfinite(v) for v in values):
                    raise ValueError("non-finite/empty phase point")
                result["phases"].setdefault(str(int(tokens[1])), []).append(
                    {"T": values[0], "V": values[1], "phi": values[2:]})
            elif tokens[0] == "PTAGENT_TN_STAGE":
                if tokens[1] not in {"started", "complete", "error"}:
                    raise ValueError("unknown nucleation stage")
                if nucleation_stage in {"complete", "error"} or (tokens[1] == "started" and nucleation_stage):
                    raise ValueError("duplicate/out-of-order nucleation stage")
                nucleation_stage = tokens[1]
                if nucleation_stage == "error":
                    result["nucleation_error"] = " ".join(tokens[2:])
            elif tokens[0] == "PTAGENT_TN_METHOD":
                if tokens[1:] != ["direct_action"] or result["nucleation_method"] != "unspecified":
                    raise ValueError("unknown/duplicate nucleation method")
                result["nucleation_method"] = tokens[1]
            elif tokens[0] == "PTAGENT_TC_COUNT":
                if count is not None:
                    raise ValueError("duplicate critical count")
                count = int(tokens[1])
            elif tokens[0] == "PTAGENT_TC_STATUS":
                if status:
                    raise ValueError("duplicate critical status")
                status = tokens[1]
            elif tokens[0] in {"PTAGENT_TC", "PTAGENT_TN", "PTAGENT_TN_STATUS", "PTAGENT_TN_EVIDENCE", "PTAGENT_TRANSITION_VECTOR"}:
                index = int(tokens[1])
                row = rows.setdefault(index, {"index": index, "Tc": None, "Tn": None, "nucleation_status": "missing"})
                if tokens[0] == "PTAGENT_TC":
                    if tokens[2] != "TC" or row["Tc"] is not None:
                        raise ValueError("invalid/duplicate Tc record")
                    value = float(tokens[3])
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("non-finite/negative Tc")
                    row["Tc"] = value
                elif tokens[0] == "PTAGENT_TN_EVIDENCE":
                    keys = ["S3_over_T", "V_true", "V_false", "separation"]
                    if tokens[2::2] != keys or "evidence" in row:
                        raise ValueError("invalid/duplicate nucleation evidence")
                    row["evidence"] = dict(zip(keys, map(float, tokens[3::2])))
                elif tokens[0] == "PTAGENT_TRANSITION_VECTOR":
                    name = tokens[2]
                    vectors = row.setdefault("vectors", {})
                    if name in vectors:
                        raise ValueError("duplicate transition vector")
                    vector = list(map(float, tokens[3:]))
                    if not vector or not all(math.isfinite(value) for value in vector):
                        raise ValueError("non-finite/empty transition vector")
                    vectors[name] = vector
                else:
                    if row["nucleation_status"] != "missing":
                        raise ValueError("duplicate Tn record")
                    if tokens[0] == "PTAGENT_TN":
                        value = float(tokens[3])
                        if tokens[2] != "TN" or not math.isfinite(value) or value <= 0:
                            if protocol == 1:
                                raise ValueError("invalid Tn")
                            row.update(Tn=None, raw_Tn=tokens[3], nucleation_status="invalid", reason="invalid Tn")
                            continue
                        row.update(Tn=value, nucleation_status="available")
                    elif tokens[2] in {"unavailable", "invalid", "not_selected"}:
                        row["nucleation_status"] = tokens[2]
                        row["reason"] = " ".join(tokens[3:])
                    else:
                        raise ValueError("unknown Tn status")
        if status == "error":
            result.update(critical_status="error", nucleation_status="unavailable")
            return result
        if count is None or count < 0 or sorted(rows) != list(range(count)):
            raise ValueError("missing/inconsistent critical count")
        if status != ("success" if count else "no_transitions") or any(row["Tc"] is None for row in rows.values()):
            raise ValueError("missing/inconsistent critical output")
        if protocol == 1 and any(row["nucleation_status"] == "missing" for row in rows.values()):
            raise ValueError("missing nucleation result status")
        transitions = [rows[index] for index in sorted(rows)]
        for row in transitions:
            if row["nucleation_status"] == "missing":
                row["nucleation_status"] = "error" if nucleation_stage == "error" else "interrupted"
            reason = ""
            if row["Tn"] is not None and row["Tn"] > row["Tc"]:
                reason = "Tn exceeds Tc"
            evidence = row.get("evidence", {})
            if row["Tn"] is not None and protocol == 2:
                if not evidence or not all(math.isfinite(v) for v in evidence.values()):
                    reason = "missing or non-finite native action evidence"
                elif not (0 < evidence["S3_over_T"] < float.fromhex('0x1.fffffffffffffp+1023')
                          and evidence["V_false"] > evidence["V_true"] and evidence["separation"] > 0):
                    reason = "invalid action or vacuum ordering"
                vectors = row.get("vectors", {})
                true, false = vectors.get("true_vev_Tn", []), vectors.get("false_vev_Tn", [])
                if not true or len(true) != len(false) or true == false:
                    reason = "missing or coincident nucleation endpoints"
            if reason:
                row.update(raw_Tn=row["Tn"], Tn=None, nucleation_status="invalid", reason=reason)
            row["evidence_status"] = "native_action_checked" if row["Tn"] is not None and protocol == 2 else "not_verified"
            # Non-finite evidence remains in raw logs, never in a JSON numeric field.
            if evidence:
                row["evidence"] = {key: value if math.isfinite(value) else None for key, value in evidence.items()}
        available = sum(row["Tn"] is not None for row in transitions)
        selected = sum(row["nucleation_status"] != "not_selected" for row in transitions)
        result.update(critical_status=status, transitions=transitions,
                      nucleation_status=("not_applicable" if not count else "complete" if available and available == selected
                                         else "partial" if available else "unavailable"))
        if protocol == 2 and count and nucleation_stage != "complete":
            result["nucleation_status"] = "partial" if available else "error" if nucleation_stage == "error" else "interrupted"
        elif protocol == 2 and any(row["nucleation_status"] == "interrupted" for row in transitions):
            result["nucleation_status"] = "error"
            result["nucleation_error"] = "completed stage omitted per-transition results"
        result["protocol_version"] = protocol
    except (IndexError, ValueError) as exc:
        result.update(critical_status="invalid_output", nucleation_status="unavailable", error=str(exc))
    return result


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
