from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

from .artifact_layout import proof_materials_dir
from .schemas import PaperMemory


MODEL_FOCUS_MARKER = "PTAGENT_MODEL_FOCUS:"


@dataclass(frozen=True)
class ModelCandidate:
    short_name: str
    display_name: str
    aliases: tuple[str, ...]
    score: float
    source_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    potential_hits: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ModelPreflightResult:
    selected_model: ModelCandidate | None
    candidates: tuple[ModelCandidate, ...]
    needs_user_selection: bool
    requested_model: str = ""
    question: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_model": self.selected_model.to_dict() if self.selected_model else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "needs_user_selection": self.needs_user_selection,
            "requested_model": self.requested_model,
            "question": self.question,
            "reason": self.reason,
        }


class ModelSelectionRequired(RuntimeError):
    def __init__(self, result: ModelPreflightResult, *, run_dir: Path, source_path: Path) -> None:
        self.result = result
        self.run_dir = run_dir
        self.source_path = source_path
        super().__init__(result.question or "Model selection is required before generating a contract.")


_KNOWN_MODELS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "CxSM",
        "complex singlet extension",
        (
            "CxSM",
            "complex singlet",
            "complex scalar singlet",
            "complex singlet extension",
        ),
    ),
    (
        "XSM",
        "real singlet extension",
        (
            "xSM",
            "XSM",
            "real singlet",
            "real scalar singlet",
            "singlet extension",
            "scalar singlet extension",
            "Z2 singlet",
            "Z_2 singlet",
        ),
    ),
    (
        "2HDM",
        "two-Higgs-doublet model",
        (
            "2HDM",
            "two-Higgs-doublet",
            "two Higgs doublet",
            "two-Higgs doublet",
            "two Higgs-doublet",
        ),
    ),
    (
        "IDM",
        "inert doublet model",
        (
            "IDM",
            "inert doublet",
            "inert Higgs doublet",
        ),
    ),
    (
        "GM",
        "Georgi-Machacek model",
        (
            "Georgi-Machacek",
            "Georgi Machacek",
            "GM model",
        ),
    ),
    (
        "NMSSM",
        "next-to-minimal supersymmetric standard model",
        (
            "NMSSM",
            "next-to-minimal supersymmetric",
        ),
    ),
    (
        "MSSM",
        "minimal supersymmetric standard model",
        (
            "MSSM",
            "minimal supersymmetric",
        ),
    ),
)


def preflight_model_selection(memory: PaperMemory, *, requested_model: str = "") -> ModelPreflightResult:
    """Find candidate model branches before the full contract is generated."""

    requested = _normalize_requested_model(requested_model)
    candidates = tuple(_scan_model_candidates(memory))
    if requested:
        selected = _scanned_candidate_for_requested_model(requested, candidates)
        if selected is None and candidates:
            return ModelPreflightResult(
                selected_model=None,
                candidates=candidates,
                needs_user_selection=True,
                requested_model=requested_model,
                question=_requested_model_mismatch_question(requested, candidates),
                reason="The requested model does not overlap with the lightweight preflight scan results.",
            )
        selected = selected or _candidate_for_requested_model(requested, candidates)
        return ModelPreflightResult(
            selected_model=selected,
            candidates=_promote_candidate(selected, candidates),
            needs_user_selection=False,
            requested_model=requested_model,
            reason="User supplied the model focus before contract generation.",
        )
    if len(candidates) > 1:
        return ModelPreflightResult(
            selected_model=None,
            candidates=candidates,
            needs_user_selection=True,
            question=_selection_question(candidates),
            reason="Multiple candidate model potentials were detected in the source material.",
        )
    if len(candidates) == 1:
        return ModelPreflightResult(
            selected_model=candidates[0],
            candidates=candidates,
            needs_user_selection=False,
            reason="A single candidate model branch was detected.",
        )
    return ModelPreflightResult(
        selected_model=None,
        candidates=(),
        needs_user_selection=False,
        reason="No named HEP model branch was detected by the lightweight preflight scan.",
    )


def memory_with_model_focus(memory: PaperMemory, selected_model: ModelCandidate | None) -> PaperMemory:
    if selected_model is None:
        return memory
    notes = str(memory.notes or "").rstrip()
    payload = json.dumps(selected_model.to_dict(), ensure_ascii=False, sort_keys=True)
    focus_line = f"{MODEL_FOCUS_MARKER} {payload}"
    if MODEL_FOCUS_MARKER in notes:
        lines = [focus_line if line.startswith(MODEL_FOCUS_MARKER) else line for line in notes.splitlines()]
        notes = "\n".join(lines)
    else:
        notes = "\n".join(line for line in (notes, focus_line) if line)
    return replace(memory, notes=notes)


def selected_model_from_memory(memory: PaperMemory) -> ModelCandidate | None:
    for line in reversed(str(memory.notes or "").splitlines()):
        if not line.startswith(MODEL_FOCUS_MARKER):
            continue
        raw = line[len(MODEL_FOCUS_MARKER) :].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return ModelCandidate(
            short_name=str(data.get("short_name", "")),
            display_name=str(data.get("display_name", "")),
            aliases=tuple(str(item) for item in _as_list(data.get("aliases"))),
            score=float(data.get("score", 0.0) or 0.0),
            source_ids=tuple(str(item) for item in _as_list(data.get("source_ids"))),
            evidence=tuple(str(item) for item in _as_list(data.get("evidence"))),
            potential_hits=int(data.get("potential_hits", 0) or 0),
        )
    return None


def model_focus_terms(memory: PaperMemory) -> tuple[str, ...]:
    selected = selected_model_from_memory(memory)
    if selected is None:
        return ()
    terms = [selected.short_name, selected.display_name, *selected.aliases]
    return tuple(item for item in dict.fromkeys(term.strip() for term in terms) if item)


def write_model_preflight_artifacts(run_dir: Path, result: ModelPreflightResult) -> tuple[Path, Path]:
    proof_dir = proof_materials_dir(run_dir)
    proof_dir.mkdir(parents=True, exist_ok=True)
    json_path = proof_dir / "model_candidates.json"
    md_path = proof_dir / "model_candidates.md"
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_model_preflight_markdown(result)
    md_path.write_text(markdown, encoding="utf-8")
    if result.needs_user_selection:
        (proof_dir / "user_questions.md").write_text(markdown, encoding="utf-8")
    return json_path, md_path


def render_model_preflight_markdown(result: ModelPreflightResult) -> str:
    lines = [
        "# PTagent Model Preflight",
        "",
        f"- Needs user selection: `{str(result.needs_user_selection).lower()}`",
        f"- Requested model: `{result.requested_model or '<none>'}`",
        f"- Reason: {result.reason or 'No preflight reason recorded.'}",
        "",
    ]
    if result.selected_model is not None:
        lines.extend(
            [
                "## Selected Model",
                "",
                f"- Short name: `{result.selected_model.short_name}`",
                f"- Display name: {result.selected_model.display_name}",
                "",
            ]
        )
    if result.candidates:
        lines.extend(
            [
                "## Candidate Models",
                "",
                "| short_name | display_name | score | potential_hits | source_ids | evidence |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for candidate in result.candidates:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(candidate.short_name),
                        _table_cell(candidate.display_name),
                        f"{candidate.score:.2f}",
                        str(candidate.potential_hits),
                        _table_cell(", ".join(candidate.source_ids[:6])),
                        _table_cell(" / ".join(candidate.evidence[:3])),
                    ]
                )
                + " |"
            )
        lines.append("")
    if result.needs_user_selection:
        lines.extend(
            [
                "## Current Question",
                "",
                "- Current question: 1 of 1.",
                f"- Plain-language issue: {_plain_language_issue(result)}",
                f"- My current leaning: {_current_leaning(result.candidates)}",
                "",
                result.question,
                "",
                _next_step_text(result),
                "",
            ]
        )
    return "\n".join(lines)


def _scan_model_candidates(memory: PaperMemory) -> list[ModelCandidate]:
    candidates: list[ModelCandidate] = []
    for short_name, display_name, aliases in _KNOWN_MODELS:
        score = 0.0
        potential_hits = 0
        source_ids: list[str] = []
        evidence: list[str] = []
        for source_id, heading, text, is_potential in _iter_evidence_text(memory):
            hit_aliases = [alias for alias in aliases if _contains_alias(text, alias) or _contains_alias(heading, alias)]
            if not hit_aliases:
                continue
            local = 1.0 + 0.35 * min(len(hit_aliases), 3)
            if _model_context_bonus_text(heading, text):
                local += 0.9
            if is_potential:
                local += 1.3
                potential_hits += 1
            score += local
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
            if len(evidence) < 5:
                evidence.append(_shorten(f"{heading}: {text}", 180))
        if score >= 1.5:
            candidates.append(
                ModelCandidate(
                    short_name=short_name,
                    display_name=display_name,
                    aliases=aliases,
                    score=score,
                    source_ids=tuple(source_ids),
                    evidence=tuple(evidence),
                    potential_hits=potential_hits,
                )
            )
    candidates = _remove_shadowed_candidates(candidates)
    candidates.sort(key=lambda candidate: (candidate.potential_hits, candidate.score), reverse=True)
    return candidates


def _iter_evidence_text(memory: PaperMemory) -> Iterable[tuple[str, str, str, bool]]:
    for span in memory.source_spans:
        yield span.source_id, span.heading, span.text, _looks_like_potential_context(span.heading, span.text)
    for record in memory.formula_registry:
        text = "\n".join(item for item in (record.latex, record.raw_text, record.context) if item)
        yield record.source_id or record.formula_id, record.label, text, record.kind in {"tree_potential", "effective_potential"}


def _contains_alias(text: str, alias: str) -> bool:
    if not text or not alias:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(alias).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _model_context_bonus_text(heading: str, text: str) -> bool:
    probe = f"{heading}\n{text}".lower()
    return any(
        token in probe
        for token in (
            "model",
            "potential",
            "tree-level",
            "tree level",
            "scalar sector",
            "benchmark",
            "phase transition",
        )
    )


def _looks_like_potential_context(heading: str, text: str) -> bool:
    probe = f"{heading}\n{text}".lower()
    return (
        "potential" in probe
        or "v_0" in probe
        or "v0" in probe
        or "tree-level" in probe
        or "tree level" in probe
    )


def _remove_shadowed_candidates(candidates: list[ModelCandidate]) -> list[ModelCandidate]:
    by_short = {candidate.short_name: candidate for candidate in candidates}
    if "CxSM" in by_short and "XSM" in by_short:
        cxsm = by_short["CxSM"]
        xsm = by_short["XSM"]
        complex_evidence = " ".join(cxsm.evidence).lower()
        if "complex" in complex_evidence and xsm.potential_hits <= cxsm.potential_hits:
            candidates = [candidate for candidate in candidates if candidate.short_name != "XSM"]
    return candidates


def _normalize_requested_model(value: str) -> str:
    text = str(value or "").strip().strip("`'\"")
    if not text:
        return ""
    compact = re.sub(r"[^A-Za-z0-9]+", "", text).lower()
    known = {
        "xsm": "XSM",
        "cxsm": "CxSM",
        "2hdm": "2HDM",
        "idm": "IDM",
        "gm": "GM",
        "mssm": "MSSM",
        "nmssm": "NMSSM",
    }
    return known.get(compact, text)


def _candidate_for_requested_model(requested: str, candidates: tuple[ModelCandidate, ...]) -> ModelCandidate:
    scanned = _scanned_candidate_for_requested_model(requested, candidates)
    if scanned is not None:
        return scanned
    for short_name, display_name, aliases in _KNOWN_MODELS:
        probes = [short_name, display_name, *aliases]
        if any(_model_probe(probe) == _model_probe(requested) for probe in probes):
            return ModelCandidate(
                short_name=short_name,
                display_name=display_name,
                aliases=aliases,
                score=0.0,
                source_ids=(),
                evidence=("user_supplied model focus",),
                potential_hits=0,
            )
    return ModelCandidate(
        short_name=requested,
        display_name=requested,
        aliases=(requested,),
        score=0.0,
        source_ids=(),
        evidence=("user_supplied model focus",),
        potential_hits=0,
    )


def _scanned_candidate_for_requested_model(requested: str, candidates: tuple[ModelCandidate, ...]) -> ModelCandidate | None:
    requested_probe = _model_probe(requested)
    for candidate in candidates:
        probes = [candidate.short_name, candidate.display_name, *candidate.aliases]
        if any(_model_probe(probe) == requested_probe for probe in probes):
            return candidate
    return None


def _model_probe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "")).lower()


def _promote_candidate(selected: ModelCandidate, candidates: tuple[ModelCandidate, ...]) -> tuple[ModelCandidate, ...]:
    rest = [candidate for candidate in candidates if candidate.short_name != selected.short_name]
    return (selected, *rest)


def _selection_question(candidates: tuple[ModelCandidate, ...]) -> str:
    names = ", ".join(f"`{candidate.short_name}`" for candidate in candidates)
    return (
        "Which model/potential branch should PTagent implement from this source? "
        f"I found these candidates: {names}. Reply with one short HEP model name, "
        "for example `XSM` or `2HDM`; after that PTagent can extract the contract around that branch."
    )


def _requested_model_mismatch_question(requested: str, candidates: tuple[ModelCandidate, ...]) -> str:
    names = ", ".join(f"`{candidate.short_name}`" for candidate in candidates)
    return (
        f"You requested `{requested}`, but the lightweight scan did not find that model in the source evidence. "
        f"I found these candidates instead: {names}. Which model/potential branch should PTagent implement?"
    )


def _plain_language_issue(result: ModelPreflightResult) -> str:
    if result.requested_model and result.selected_model is None:
        return (
            "the requested model does not match the model candidates found in the source, "
            "so PTagent should stop before mixing formulas from the wrong branch."
        )
    return "this source appears to contain more than one model or potential branch, so PTagent should not mix formulas before you choose the target."


def _next_step_text(result: ModelPreflightResult) -> str:
    if result.requested_model and result.selected_model is None:
        return "After answering, rerun extraction with a model short name that matches the source evidence, for example `--model XSM` or `--model 2HDM`."
    return "After answering, rerun extraction with the selected short name, for example `--model XSM` or `--model 2HDM`."


def _current_leaning(candidates: tuple[ModelCandidate, ...]) -> str:
    if not candidates:
        return "no clear preference; ask the user for the target model."
    best = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    if runner_up and best.score < runner_up.score * 1.5:
        return "the candidates are close, so I should not choose automatically."
    return f"`{best.short_name}` has the strongest evidence, but user confirmation is still required because more than one candidate was detected."


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _shorten(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def _table_cell(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip()
