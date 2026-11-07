"""Read-only software provenance; discovery is not a compatibility guarantee."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PYTHON_DISTRIBUTIONS = (
    "numpy", "sympy", "scipy", "matplotlib", "pypdf", "pymupdf",
    "pdfplumber", "defusedxml", "cosmoTransitions",
)


def _current_python_versions() -> dict[str, Any]:
    packages = {}
    for name in PYTHON_DISTRIBUTIONS:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not_installed"
        except (OSError, ValueError):
            packages[name] = "unavailable"
    return {"status": "recorded", "executable": sys.executable,
            "version": platform.python_version(), "prefix": sys.prefix,
            "distributions": packages}


def python_versions(executable: str | Path | None = None) -> dict[str, Any]:
    """Probe the selected interpreter without resolving away a virtualenv symlink."""
    selected = os.path.abspath(os.path.expanduser(str(executable or sys.executable)))
    if selected == os.path.abspath(sys.executable):
        return _current_python_versions()
    try:
        result = subprocess.run([selected, str(Path(__file__).resolve())],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout).strip()[:1000])
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("status") != "recorded":
            raise ValueError("Unrecognized Python version probe output")
        return payload
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "executable": selected, "version": None,
                "distributions": {}, "diagnostic": f"{type(exc).__name__}: {exc}"}


def _git_bytes(root: Path, *args: str) -> bytes | None:
    # An enclosing agent/test checkout must not redirect this read-only probe.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1",
                       GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run(["git", "--no-pager", "--no-replace-objects",
                                 "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                capture_output=True, timeout=5, check=False, env=environment)
        if result.returncode != 0:
            return None
        return result.stdout.encode("utf-8") if isinstance(result.stdout, str) else result.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _git_output(root: Path, *args: str) -> str | None:
    output = _git_bytes(root, *args)
    if output is None:
        return None
    try:
        return output.decode("utf-8").strip()
    except UnicodeError:
        return None


def _ptagent_version() -> dict[str, Any]:
    # Keep this import out of the standalone selected-Python version probe.
    from .version import RELEASE_STATUS, __version__

    root = Path(__file__).resolve().parents[2]
    record: dict[str, Any] = {
        "version": __version__, "release_status": RELEASE_STATUS,
        "version_source": "backend/ptagent/version.py", "path": str(root),
        "status": "recorded", "git_status": "no_checkout", "commit": None,
        "tracked_changes": None, "dirty_diff_sha256": None,
        "dirty_digest_scope": "staged and unstaged tracked diffs; excludes untracked files and submodule contents",
    }
    # Archives installed inside another repository must not inherit its HEAD.
    # A .git file is valid for a linked worktree or a submodule checkout.
    if not (root / ".git").exists():
        return record
    record["git_status"] = "unavailable"
    top = _git_output(root, "rev-parse", "--show-toplevel")
    if not top:
        return record
    try:
        matches_root = Path(top).is_absolute() and Path(top).resolve() == root
    except (OSError, ValueError):
        return record
    if not matches_root:
        record["git_status"] = "root_mismatch"
        return record
    commit = _git_output(root, "rev-parse", "--verify", "HEAD")
    if not commit or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
        return record
    record.update(commit=commit, git_status="revision_only")
    options = ("--binary", "--no-ext-diff", "--no-textconv", "--no-color",
               "--no-renames", "--ignore-submodules=none", "--full-index",
               "--src-prefix=a/", "--dst-prefix=b/", "--diff-algorithm=myers",
               "--no-indent-heuristic", "--unified=0")
    staged = _git_bytes(root, "diff", *options, "--cached", commit, "--")
    unstaged = _git_bytes(root, "diff", *options, "--")
    if staged is None or unstaged is None:
        return record
    dirty = bool(staged or unstaged)
    digest = hashlib.sha256(b"ptagent.tracked-diff.v1\0")
    for diff in (staged, unstaged):
        digest.update(len(diff).to_bytes(8, "big"))
        digest.update(diff)
    record.update(git_status="recorded", tracked_changes=dirty,
                  dirty_diff_sha256=digest.hexdigest() if dirty else None)
    return record


def _source_tool(path: str | Path | None) -> dict[str, Any]:
    record: dict[str, Any] = {"path": str(path) if path else None,
                              "version": None, "commit": None, "status": "not_recorded"}
    if not path:
        return record
    root = Path(path).expanduser()
    if not root.exists():
        record["status"] = "unavailable"
        return record
    record.update(path=str(root.resolve()), status="path_only")
    if root.is_dir() and (root / ".git").exists():
        commit = _git_output(root, "rev-parse", "HEAD")
        if commit:
            dirty = _git_output(root, "status", "--porcelain", "--untracked-files=no")
            record.update(commit=commit, status="recorded",
                          describe=_git_output(root, "describe", "--tags", "--always"),
                          tracked_changes=bool(dirty) if dirty is not None else None)
    return record


def _declared_version(record: dict[str, Any], candidates: tuple[str, ...], pattern: str) -> None:
    if not record["path"] or record["status"] == "unavailable":
        return
    for name in candidates:
        try:
            with (Path(record["path"]) / name).open(encoding="utf-8") as handle:
                match = re.search(pattern, handle.read(256 * 1024), re.MULTILINE)
        except (OSError, UnicodeError):
            continue
        if match:
            record.update(version=match.group(1), version_source=name, status="recorded")
            return


def collect_software_versions(
    *, runtime_python: str | Path | None = None,
    phasetracer_root: str | Path | None = None,
    sarah_root: str | Path | None = None,
    wolframscript: str | Path | None = None,
    dralgo_root: str | Path | None = None,
    wolfram_version: str | None = None,
    dralgo_version: str | None = None,
) -> dict[str, Any]:
    """Record actual selected paths/versions, never infer versions from names/main.

    Callers that have executed Wolfram should pass its reported version and the
    loaded DRalgo root/version. This helper does not start a licensed kernel,
    download tools, change settings, or assert that a paper was reproduced.
    """
    phase = _source_tool(phasetracer_root)
    sarah = _source_tool(sarah_root)
    dralgo = _source_tool(dralgo_root)
    _declared_version(sarah, ("SARAH.m",), r'^\s*SA`Version\s*=\s*"([^"]+)"')
    _declared_version(dralgo, ("PacletInfo.wl", "PacletInfo.m"), r'\bVersion\s*->\s*"([^"]+)"')
    if dralgo_version:
        dralgo.update(version=dralgo_version, version_source="runtime", status="recorded")
    executable = shutil.which(str(wolframscript)) if wolframscript else None
    wolfram = _source_tool(executable or wolframscript)
    if wolfram_version:
        wolfram.update(version=wolfram_version, version_source="runtime", status="recorded")
    return {
        "schema": "ptagent.software_versions.v1",
        "ptagent": _ptagent_version(),
        "python": {"current": python_versions(), "runtime": python_versions(runtime_python)
                   if runtime_python else {"status": "not_selected", "version": None,
                                           "executable": None, "distributions": {}}},
        "tools": {"phasetracer": phase, "sarah": sarah, "wolfram": wolfram, "dralgo": dralgo},
        "interpretation": "Observed software provenance only; not a compatibility or paper-reproduction claim.",
    }


if __name__ == "__main__":
    print(json.dumps(_current_python_versions()))
