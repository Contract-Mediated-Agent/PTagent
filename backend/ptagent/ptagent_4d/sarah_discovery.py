"""Read-only SARAH discovery for installed skills and documentation examples."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class SarahDiscoveryError(ValueError):
    pass


def _application_directories() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Mathematica/Applications", home / "Library/Wolfram/Applications",
                Path("/Library/Mathematica/Applications"), Path("/Library/Wolfram/Applications")]
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
        shared = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        return [base / name / "Applications" for base in (roaming, shared)
                for name in ("Mathematica", "Wolfram")]
    return [home / ".Mathematica/Applications", home / ".Wolfram/Applications",
            Path("/usr/share/Mathematica/Applications"), Path("/usr/share/Wolfram/Applications")]


def _wolfram_search_paths(executable: str) -> tuple[list[Path], str]:
    # Query package locations only. Do not load SARAH or run any model here.
    code = (
        'Print["PTAGENT_SARAH_PATHS=" <> ExportString['
        'Select[Join[{Quiet[FindFile["SARAH`"]], '
        'FileNameJoin[{$UserBaseDirectory,"Applications"}], '
        'FileNameJoin[{$BaseDirectory,"Applications"}]}, $Path], StringQ], "RawJSON"]];'
    )
    try:
        result = subprocess.run([executable, "-code", code], capture_output=True, text=True,
                                timeout=8, check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("PTAGENT_SARAH_PATHS="):
                    values = json.loads(line.split("=", 1)[1])
                    if isinstance(values, list) and all(isinstance(item, str) for item in values):
                        return [Path(item).expanduser() for item in values], ""
        return [], "Wolfram package-path query failed; installation absence is not confirmed."
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return [], f"Wolfram package-path query failed ({type(exc).__name__}); installation absence is not confirmed."


def discover_sarah(sarah_root: str | Path = "", *, wolframscript: str = "",
                   model: str = "") -> dict[str, Any]:
    """Prefer explicit/configured roots; otherwise search package directories."""
    explicit = str(sarah_root or "").strip()
    configured = explicit or os.environ.get("PTAGENT_SARAH_ROOT", "").strip() or os.environ.get("SARAH_ROOT", "").strip()
    selection = "explicit" if explicit else "environment" if configured else "package_search"
    searched: list[str] = []
    candidates: list[Path] = []
    diagnostics: list[str] = []

    def add(path: Path) -> None:
        root = path.expanduser().resolve()
        if (root / "SARAH.m").is_file() and root not in candidates:
            candidates.append(root)

    def scan(path: Path) -> None:
        searched.append(str(path))
        if path.is_file():
            if path.name == "SARAH.m":
                add(path.parent)
            return
        add(path)
        add(path / "SARAH")
        # The unversioned package/alias wins; versioned installs are ordered numerically.
        versioned = sorted(path.glob("SARAH*"), key=lambda p: (
            tuple(int(n) for n in re.findall(r"\d+", p.name)), p.name), reverse=True)
        for child in versioned:
            if child.is_dir():
                add(child)

    if configured:
        searched.append(configured)
        add(Path(configured))
    else:
        for path in _application_directories():
            scan(path)
        if not candidates and wolframscript:
            paths, diagnostic = _wolfram_search_paths(wolframscript)
            if diagnostic:
                diagnostics.append(diagnostic)
            for path in paths:
                scan(path)

    matching = [root for root in candidates if not model or (root / "Models" / model / f"{model}.m").is_file()]
    root = (matching or candidates or [None])[0]
    if not root:
        if configured:
            diagnostics.append("Configured SARAH root does not contain SARAH.m; correct it rather than silently using another installation.")
        else:
            diagnostics.append("SARAH was not found in the checked package paths. If not installed, install SARAH in a Wolfram/Mathematica Applications directory, or set PTAGENT_SARAH_ROOT for an existing installation.")
    return {"available": root is not None, "root": str(root or ""),
            "configured": bool(configured), "selection": selection,
            "candidates": [str(item) for item in candidates],
            "searched_locations": searched, "diagnostics": diagnostics}


def resolve_sarah_model(model: str, *, sarah_root: str | Path = "",
                        model_search_path: str | Path | None = None,
                        wolframscript: str = "") -> dict[str, Any]:
    """Resolve a model name, real directory, or the documented placeholder path."""
    requested = model.strip()
    placeholder = "/path/to/SARAH/Models/"
    directory: Path | None = None
    root_hint = sarah_root
    if requested.startswith(placeholder):
        name = requested[len(placeholder):].rstrip("/")
    elif "/" in requested or "\\" in requested or Path(requested).expanduser().is_dir():
        directory = Path(requested).expanduser().resolve()
        if not directory.is_dir():
            raise SarahDiscoveryError(f"Explicit SARAH model directory does not exist: {directory}. It was not replaced by a bundled model.")
        name = directory.name
        if not sarah_root:
            root_hint = next((parent for parent in directory.parents if (parent / "SARAH.m").is_file()), "")
    else:
        name = requested
    if not re.fullmatch(r"[A-Za-z0-9_+.-]+", name) or name in {".", ".."}:
        raise SarahDiscoveryError("Provide a SARAH model name or an existing model directory; nested model variants require explicit support.")

    discovery = discover_sarah(root_hint, wolframscript=wolframscript,
                               model=name if directory is None and not model_search_path else "")
    if not discovery["available"]:
        raise SarahDiscoveryError(" ".join(discovery["diagnostics"]) + " Checked: " + ", ".join(discovery["searched_locations"]))
    root = Path(discovery["root"])
    parent = Path(model_search_path).expanduser().resolve() if model_search_path else (directory.parent if directory else root / "Models")
    if directory is not None and parent / name != directory:
        raise SarahDiscoveryError("--model-search-path conflicts with the explicit model directory.")
    source = parent / name
    if not (source / f"{name}.m").is_file():
        raise SarahDiscoveryError(f"SARAH is installed at {root}, but model source {source / (name + '.m')} was not found. Check the model name/path; reinstalling SARAH is not implied.")
    return {**discovery, "requested_model": model, "model": name,
            "model_source": str(source), "model_search_path": str(parent)}
