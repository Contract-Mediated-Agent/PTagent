from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


PROJECT_ROOT_ENV = "PTAGENT_PROJECT_ROOT"


def ensure_ptagent_backend(project_root: str = "") -> Path | None:
    """Make the PTagent backend importable for skill wrapper scripts."""

    if project_root:
        root = _require_project_root(project_root, "--project-root")
        _prepend_sys_path(root)
        return root

    env_value = os.getenv(PROJECT_ROOT_ENV, "")
    if env_value:
        root = _require_project_root(env_value, PROJECT_ROOT_ENV)
        _prepend_sys_path(root)
        return root

    for candidate in _bundled_backend_candidates():
        root = _valid_project_root(candidate)
        if root is not None:
            _prepend_sys_path(root)
            return root

    for candidate in _discovered_candidates():
        root = _valid_project_root(candidate)
        if root is not None:
            _prepend_sys_path(root)
            return root

    if importlib.util.find_spec("ptagent") is not None:
        return None

    raise RuntimeError(
        "Could not locate the PTagent backend. Install the self-contained "
        "PTagent skill package, run from the cloned repository, pass "
        "--project-root, set PTAGENT_PROJECT_ROOT, or install the backend "
        "Python package."
    )


def _bundled_backend_candidates() -> list[Path]:
    script_path = Path(__file__).resolve()
    skill_dir = script_path.parents[1]
    skills_root = skill_dir.parent
    return _unique_candidates(
        [
            skills_root / "ptagent" / "backend",
            skill_dir / "backend",
            skills_root / "ptagent-4d" / "backend",
            skills_root / "ptagent-3deft" / "backend",
        ]
    )


def _discovered_candidates() -> list[Path]:
    candidates: list[Path] = []
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        candidates.extend(start.parents)
    return _unique_candidates(candidates)


def _valid_project_root(value: str | Path) -> Path | None:
    if not value:
        return None
    root = Path(value).expanduser().resolve()
    if (
        (root / "ptagent").is_dir()
        and (root / "ptagent" / "__main__.py").exists()
        and (root / "ptagent" / "interface" / "facade.py").exists()
    ):
        return root
    return None


def _require_project_root(value: str | Path, source: str) -> Path:
    root = _valid_project_root(value)
    if root is None:
        raise FileNotFoundError(
            f"{source} does not point to a PTagent repository root: {Path(value).expanduser()}"
        )
    return root


def _prepend_sys_path(root: Path) -> None:
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _unique_candidates(candidates: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique
