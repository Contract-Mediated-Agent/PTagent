from __future__ import annotations

import re
from typing import Any

from pathlib import Path


GLOBAL_INPUT_DIRNAME = "input"
PROOF_MATERIALS_DIRNAME = "proof_materials"
GENERATED_MODELS_DIRNAME = "generated_models"
_GENERIC_MODEL_SLUG_VALUES = {
    "",
    "ask_user",
    "unknown",
    "unclear",
    "review_required",
    "needs_review",
    "uploaded_model",
    "contract_model",
}


def proof_materials_dir(run_or_template_path: str | Path) -> Path:
    """Directory for derived evidence, JSON, reports, and question guides."""

    path = Path(run_or_template_path)
    root = path.parent if path.suffix.lower() in {".md", ".json", ".py"} else path
    return root / PROOF_MATERIALS_DIRNAME


def generated_models_dir(run_or_template_path: str | Path) -> Path:
    """Directory for generated CosmoTransitions model programs."""

    path = Path(run_or_template_path)
    root = path.parent if path.suffix.lower() in {".md", ".json", ".py"} else path
    return root / GENERATED_MODELS_DIRNAME


def model_artifact_slug(contract: dict[str, Any]) -> str:
    """Return the backend-neutral model slug used under generated_models/.

    The slug intentionally prefers reviewed HEP model short names such as XSM or
    2HDM. Paper ids are provenance, not model names, so they are not used for
    default artifact names.
    """

    model_card = contract.get("model_card") if isinstance(contract.get("model_card"), dict) else {}
    paper_id = str(contract.get("paper_id", "") or "").strip()
    explicit_candidates = [
        contract.get("model_short_name"),
        model_card.get("model_short_name") if isinstance(model_card, dict) else "",
        model_card.get("model_abbreviation") if isinstance(model_card, dict) else "",
    ]
    for candidate in explicit_candidates:
        slug = _slugify_model_name(candidate, allow_generic_model=True)
        if slug:
            return slug

    fallback_candidates = [
        model_card.get("model_name") if isinstance(model_card, dict) else "",
        contract.get("model_name"),
    ]
    for candidate in fallback_candidates:
        text = str(candidate or "").strip()
        if not text or text == paper_id:
            continue
        slug = _slugify_model_name(text, allow_generic_model=False)
        if slug:
            return slug
    return "model"


def _slugify_model_name(value: Any, *, allow_generic_model: bool) -> str:
    text = str(value or "").strip().strip("`'\"")
    if not text:
        return ""
    if text.lower() in _GENERIC_MODEL_SLUG_VALUES:
        return "model" if allow_generic_model and text.lower() == "model" else ""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    if not slug:
        return ""
    if slug.lower() in _GENERIC_MODEL_SLUG_VALUES:
        return "model" if allow_generic_model and slug.lower() == "model" else ""
    return slug
