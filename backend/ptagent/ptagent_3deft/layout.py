from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


USER_CONFIG_ENV = "PTAGENT_CONFIG"
ARTIFACT_ROOT_ENV = "PTAGENT_ARTIFACT_ROOT"
DEFAULT_ARTIFACT_ROOT_NAME = "PTagentRuns"
INPUT_DIR_NAME = "input"
PROOF_MATERIALS_DIR_NAME = "proof_materials"
DRALGO_MODEL_DIR_NAME = "DRalgo_model"
CONTRACT_TEMPLATE_NAME = "three_deft_contract_template.md"
MERGED_CONTRACT_NAME = "three_deft_contract_merged.md"
CONTRACT_FILE_NAMES = frozenset({CONTRACT_TEMPLATE_NAME, MERGED_CONTRACT_NAME})


@dataclass(frozen=True)
class ThreeDeftRunLayout:
    run_dir: Path
    input_dir: Path
    proof_dir: Path
    dralgo_model_dir: Path


def build_run_layout(source: Path, *, run_dir: str | Path | None = None) -> ThreeDeftRunLayout:
    root = Path(run_dir) if run_dir else _default_run_dir(source)
    return ThreeDeftRunLayout(
        run_dir=root,
        input_dir=root / INPUT_DIR_NAME,
        proof_dir=root / PROOF_MATERIALS_DIR_NAME,
        dralgo_model_dir=root / DRALGO_MODEL_DIR_NAME,
    )


def _default_run_dir(source: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = _safe_stem(source.stem)
    return _unique_run_dir(_artifact_root(), f"{timestamp}_{stem}")


def _artifact_root() -> Path:
    env_value = os.getenv(ARTIFACT_ROOT_ENV, "").strip()
    if env_value:
        return Path(env_value).expanduser()
    config_path = Path(os.getenv(USER_CONFIG_ENV, "")).expanduser() if os.getenv(USER_CONFIG_ENV, "").strip() else Path.home() / ".ptagent" / "config.toml"
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
            configured = str(config.get("artifact_root") or "").strip()
            if configured:
                return Path(configured).expanduser()
        except (OSError, tomllib.TOMLDecodeError):
            pass
    try:
        return Path.home() / DEFAULT_ARTIFACT_ROOT_NAME
    except RuntimeError:
        return Path.cwd().resolve() / DEFAULT_ARTIFACT_ROOT_NAME


def _safe_stem(raw: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw.strip()).strip("._-")
    return stem or "dralgo_source"


def _unique_run_dir(root: Path, dirname: str) -> Path:
    candidate = root / dirname
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = root / f"{dirname}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def layout_from_template(template_path: str | Path) -> ThreeDeftRunLayout | None:
    template = Path(template_path)
    parent = template.parent
    if parent.name == PROOF_MATERIALS_DIR_NAME:
        root = parent.parent
        proof_dir = parent
    elif template.name in CONTRACT_FILE_NAMES and _looks_like_run_root(parent):
        root = parent
        proof_dir = root / PROOF_MATERIALS_DIR_NAME
    else:
        return None
    return ThreeDeftRunLayout(
        run_dir=root,
        input_dir=root / INPUT_DIR_NAME,
        proof_dir=proof_dir,
        dralgo_model_dir=root / DRALGO_MODEL_DIR_NAME,
    )


def _looks_like_run_root(path: Path) -> bool:
    return any(
        (path / dirname).exists()
        for dirname in (INPUT_DIR_NAME, PROOF_MATERIALS_DIR_NAME, DRALGO_MODEL_DIR_NAME)
    )


def contract_dir_for_template(template_path: str | Path) -> Path:
    layout = layout_from_template(template_path)
    if layout:
        return layout.run_dir
    return Path(template_path).parent


def proof_dir_for_template(template_path: str | Path) -> Path:
    layout = layout_from_template(template_path)
    if layout:
        return layout.proof_dir
    return Path(template_path).parent


def dralgo_model_dir_for_template(template_path: str | Path) -> Path:
    layout = layout_from_template(template_path)
    if layout:
        return layout.dralgo_model_dir
    return Path(template_path).parent


def default_phasetracer_dir(template_path: str | Path, dirname: str) -> Path:
    layout = layout_from_template(template_path)
    if layout:
        return layout.dralgo_model_dir / dirname
    return Path(template_path).parent / dirname
