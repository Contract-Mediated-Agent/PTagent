from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from .latex_display import wrap_latex_alignment_if_needed
from .schemas import FormulaRecord, ModelIR, PaperMemory, SourceSpan, ValidationReport
from .source_reader import build_context


CORE_FORMULA_SECTORS = [
    "working_potential",
    "tree_potential",
    "zero_temp_loop",
    "thermal_potential",
    "daisy_resummation",
    "mass_spectrum",
    "parameter_relations",
]


@dataclass
class MaterialFormula:
    formula_id: str
    sector: str
    latex: str
    source_id: str = ""
    equation_number: str = ""
    evidence_level: str = "source_inferred"
    confidence: float = 0.0
    score: float = 0.0
    parser_sources: list[str] = field(default_factory=list)
    context: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class MaterialGap:
    key: str
    severity: str
    message: str
    suggested_action: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class PotentialExtractionMaterial:
    paper_id: str
    source_path: str = ""
    extraction_focus: str = "finite-temperature effective potential and mass data"
    implementation_mode: str = "unknown"
    implementation_guidance: dict[str, object] = field(default_factory=dict)
    compile_plan: dict[str, object] = field(default_factory=dict)
    canonical_parameter_graph: dict[str, object] = field(default_factory=dict)
    compile_defaults: list[dict[str, object]] = field(default_factory=list)
    mass_blocks: list[dict[str, object]] = field(default_factory=list)
    goldstone_contract: dict[str, object] = field(default_factory=dict)
    daisy_implementation_decisions: list[dict[str, str]] = field(default_factory=list)
    sector_formulas: dict[str, list[MaterialFormula]] = field(default_factory=dict)
    selected_formula_ids: dict[str, str] = field(default_factory=dict)
    zero_temp_loop_notes: dict[str, object] = field(default_factory=dict)
    counterterm_notes: dict[str, object] = field(default_factory=dict)
    symbol_candidates: list[dict[str, object]] = field(default_factory=list)
    field_evidence: dict[str, dict[str, object]] = field(default_factory=dict)
    validation_issues: list[dict[str, object]] = field(default_factory=list)
    gaps: list[MaterialGap] = field(default_factory=list)
    formula_selection: list[dict[str, str]] = field(default_factory=list)
    source_exact_overrides: list[dict[str, str]] = field(default_factory=list)
    source_context: str = ""
    reviewed_model_card_excerpt: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["sector_formulas"] = {
            key: [item.to_dict() for item in values]
            for key, values in self.sector_formulas.items()
        }
        data["gaps"] = [item.to_dict() for item in self.gaps]
        return data


def build_potential_material(
    memory: PaperMemory,
    *,
    ir: ModelIR | None = None,
    validation: ValidationReport | None = None,
    template_markdown: str = "",
    max_per_sector: int = 8,
) -> PotentialExtractionMaterial:
    sector_formulas = {
        sector: _rank_sector_formulas(memory.formula_registry, sector, max_per_sector=max_per_sector)
        for sector in CORE_FORMULA_SECTORS
    }
    selected_formula_ids = {
        sector: formulas[0].formula_id
        for sector, formulas in sector_formulas.items()
        if formulas
    }
    validation_issues = [item.to_dict() for item in validation.issues] if validation else []
    implementation_mode = _implementation_mode(ir, template_markdown, memory)
    gaps = _build_gaps(memory, sector_formulas, ir, validation, implementation_mode=implementation_mode)
    reviewed_excerpt = _truncate(_strip_evidence_appendix(template_markdown), 42000)
    source_exact_overrides = _build_source_exact_overrides(
        reviewed_excerpt,
        sector_formulas,
        memory.formula_registry,
        memory.source_spans,
        memory.paper_markdown,
    )
    implementation_guidance = _build_implementation_guidance(implementation_mode, ir)
    canonical_parameter_graph = _build_canonical_parameter_graph(ir)
    compile_defaults = _build_compile_defaults(ir, paper_id=memory.paper_id)
    mass_blocks = _build_mass_blocks(ir)
    _enrich_mass_blocks_with_source_evidence(mass_blocks, memory)
    goldstone_contract = _build_goldstone_contract(ir)
    daisy_decisions = _build_daisy_implementation_decisions(ir)
    zero_temp_loop_notes = _build_zero_temp_loop_notes(ir)
    counterterm_notes = _build_counterterm_notes(ir, sector_formulas)
    compile_plan = _build_compile_plan(
        ir=ir,
        implementation_mode=implementation_mode,
        implementation_guidance=implementation_guidance,
        canonical_parameter_graph=canonical_parameter_graph,
        compile_defaults=compile_defaults,
        mass_blocks=mass_blocks,
        goldstone_contract=goldstone_contract,
        daisy_decisions=daisy_decisions,
        zero_temp_loop_notes=zero_temp_loop_notes,
        counterterm_notes=counterterm_notes,
    )
    material = PotentialExtractionMaterial(
        paper_id=memory.paper_id,
        source_path=memory.source_path,
        implementation_mode=implementation_mode,
        implementation_guidance=implementation_guidance,
        compile_plan=compile_plan,
        canonical_parameter_graph=canonical_parameter_graph,
        compile_defaults=compile_defaults,
        mass_blocks=mass_blocks,
        goldstone_contract=goldstone_contract,
        daisy_implementation_decisions=daisy_decisions,
        sector_formulas=sector_formulas,
        selected_formula_ids=selected_formula_ids,
        zero_temp_loop_notes=zero_temp_loop_notes,
        counterterm_notes=counterterm_notes,
        symbol_candidates=[item.to_dict() for item in memory.symbol_registry[:160]],
        field_evidence={key: value.to_dict() for key, value in memory.field_evidence.items()},
        validation_issues=validation_issues,
        gaps=gaps,
        formula_selection=_build_formula_selection(sector_formulas, source_exact_overrides),
        source_exact_overrides=source_exact_overrides,
        source_context=build_context(
            memory.source_spans,
            max_chars=26000,
            required_terms=_required_source_context_terms(ir, mass_blocks, goldstone_contract, daisy_decisions),
        ),
        reviewed_model_card_excerpt=reviewed_excerpt,
    )
    return material


def _build_mass_blocks(ir: ModelIR | None) -> list[dict[str, object]]:
    if ir is None:
        return []
    blocks: list[dict[str, object]] = []
    for category, text in (ir.masses or {}).items():
        if not _meaningful_mass_text(text):
            continue
        source_role = _infer_mass_source_role(text)
        implementation_kind = _infer_mass_implementation_kind(text)
        usage_contexts = _infer_mass_usage_contexts(ir, category, source_role)
        block_id = f"mass_block_{len(blocks) + 1}"
        block = {
            "id": block_id,
            "display_name": f"{category} mass formulas",
            "category": category,
            "particles": _infer_mass_block_particles(ir, category, text),
            "background_fields": _infer_mass_block_background_fields(ir, text),
            "formula_symbols": _infer_mass_formula_symbols(text),
            "usage_contexts": usage_contexts,
            "source_role": source_role,
            "implementation_kind": implementation_kind,
            "diagonalization_required": _mass_diagonalization_required(source_role, implementation_kind, text),
            "eigenvalue_source": _infer_mass_eigenvalue_source(implementation_kind, text),
            "formula": _truncate(_single_line(text), 1800),
            "allowed_usage": _allowed_mass_usage(source_role),
            "forbidden_usage": _forbidden_mass_usage(source_role),
            "notes": _mass_block_notes(source_role, implementation_kind),
        }
        blocks.append(block)
    return blocks


def _enrich_mass_blocks_with_source_evidence(blocks: list[dict[str, object]], memory: PaperMemory) -> None:
    if not blocks:
        return
    macro_aliases = _latex_macro_aliases_for_override_scan(
        "",
        memory.formula_registry,
        memory.source_spans,
        memory.paper_markdown,
    )
    for block in blocks:
        snippets = _mass_source_evidence_snippets(block, memory, macro_aliases)
        if snippets:
            block["source_evidence_snippets"] = snippets
        contract_texts = [str(block.get("formula", "") or "")]
        contract_texts.extend(str(item.get("text", "") or "") for item in snippets)
        block["mass_sector_contract"] = _mass_sector_contract(block, contract_texts, macro_aliases)


def _mass_source_evidence_snippets(
    block: dict[str, object],
    memory: PaperMemory,
    macro_aliases: dict[str, str],
) -> list[dict[str, object]]:
    candidates: list[tuple[float, int, dict[str, object]]] = []
    for index, formula in enumerate(memory.formula_registry):
        text = _formula_text_for_override_scan(formula)
        score, reason = _mass_source_evidence_score(block, text, formula.kind, formula.evidence_level, formula.parser_sources)
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                index,
                {
                    "source_type": "formula_registry",
                    "formula_id": formula.formula_id,
                    "source_id": formula.source_id,
                    "equation_number": formula.equation_number,
                    "evidence_level": formula.evidence_level,
                    "parser_sources": list(formula.parser_sources),
                    "text": _mass_evidence_window(block, text, 2600),
                    "reason": reason,
                },
            )
        )
    for index, span in enumerate(memory.source_spans):
        score, reason = _mass_source_evidence_score(block, span.text, "source_span", "source_exact", [span.parser])
        if score < 5.0:
            continue
        candidates.append(
            (
                score - 0.25,
                10_000 + index,
                {
                    "source_type": "source_span",
                    "source_id": span.source_id,
                    "evidence_level": "source_exact" if not _span_is_projected_or_pdf(span) else "source_inferred",
                    "parser_sources": [span.parser] if span.parser else [],
                    "text": _mass_evidence_window(block, span.text, 3600),
                    "reason": reason,
                },
            )
        )
    candidates.sort(key=lambda item: (-item[0], item[1]))
    snippets: list[dict[str, object]] = []
    seen_text_keys: set[str] = set()
    for _score, _index, item in candidates:
        text_key = _normalize_formula_token(str(item.get("text", "")), macro_aliases)[:240]
        if not text_key or text_key in seen_text_keys:
            continue
        seen_text_keys.add(text_key)
        snippets.append(item)
        if len(snippets) >= 5:
            break
    return snippets


def _mass_evidence_window(block: dict[str, object], text: str, limit: int) -> str:
    value = str(text or "").strip()
    family = _mass_block_family(block)
    if family in {"vector", "fermion"}:
        limit = min(limit, 1800)
    if len(value) <= limit:
        return value
    case_sensitive_probes, lower_probes = _mass_evidence_window_probes(block, family)
    lowered = value.lower()
    positions = []
    for probe in case_sensitive_probes:
        if not probe:
            continue
        pos = value.find(str(probe))
        if pos >= 0:
            positions.append(pos)
            break
    if not positions:
        for probe in lower_probes:
            pos = lowered.find(probe)
            if pos >= 0:
                positions.append(pos)
                break
    if not positions:
        weak_probes = []
        for item in list(block.get("formula_symbols", []) or []) + list(block.get("particles", []) or []):
            probe = str(item or "").strip()
            if len(_normalize_formula_token(probe)) >= 4:
                weak_probes.append(probe)
        for probe in weak_probes:
            pos = lowered.find(probe.lower())
            if pos >= 0:
                positions.append(pos)
    if not positions:
        return _truncate(value, limit)
    center = min(positions)
    start = max(0, center - limit // 4)
    end = min(len(value), start + limit)
    start = max(0, end - limit)
    prefix = "[... preceding text omitted ...]\n" if start > 0 else ""
    suffix = "\n[... following text omitted ...]" if end < len(value) else ""
    return prefix + value[start:end].strip() + suffix


def _mass_evidence_window_probes(block: dict[str, object], family: str) -> tuple[list[str], list[str]]:
    formula_symbols = [str(item) for item in block.get("formula_symbols", []) or []]
    if family == "vector":
        return (
            [symbol for symbol in formula_symbols if any(token in symbol for token in ("M_V", "m_W", "m_Z", "gamma"))],
            ["m_w", "mw", "m_z", "mz", "m_gamma", "gamma", "photon", "gauge", "vector"],
        )
    if family == "fermion":
        return (
            [symbol for symbol in formula_symbols if any(token in symbol for token in ("m_t", "y_t", "m_f"))],
            ["m_t", "mt", "y_t", "yt", "top", "fermion", "yukawa"],
        )
    if family == "scalar":
        return (
            [r"\Theta", r"\widehat{\mathcal", "field-dependent", "field dependent", "mass matrix", "eigenvalue"],
            ["field-dependent", "field dependent", "mass matrix", "eigenvalue", "eigenvalues", "scalar", "higgs"],
        )
    return (
        [r"\Theta", "field-dependent", "field dependent", "mass matrix", "eigenvalue", "eigenvalues"],
        ["field-dependent", "field dependent", "mass matrix", "eigenvalue", "eigenvalues"],
    )


def _mass_source_evidence_score(
    block: dict[str, object],
    text: str,
    kind: str,
    evidence_level: str,
    parser_sources: Iterable[str],
) -> tuple[float, str]:
    if not text:
        return 0.0, ""
    block_formula = str(block.get("formula", "") or "")
    block_probe = _normalize_probe(" ".join([block_formula, str(block.get("category", ""))]))
    text_probe = _normalize_probe(text)
    score = 0.0
    reasons: list[str] = []
    family = _mass_block_family(block)
    text_families = _mass_text_families(text_probe)
    if family and text_families and family not in text_families:
        return 0.0, "mass-family mismatch"
    if kind == "mass":
        score += 3.0
        reasons.append("formula kind=mass")
    if evidence_level == "source_exact" and not _parser_sources_are_projected_or_pdf(parser_sources):
        score += 1.0
        reasons.append("trusted source-exact parser")
    mass_tokens = ("mass", "matrix", "eigenvalue", "eigenvalues", "fielddependent", "fielddependentmass")
    if any(token in text_probe for token in mass_tokens):
        score += 2.0
        reasons.append("mass/matrix/eigenvalue token")
    if "theta" in block_probe and "theta" in text_probe:
        score += 4.0
        reasons.append("Theta table evidence")
    if "theta" in text_probe and any(token in text_probe for token in ("bar", "lambda", "lam")):
        score += 1.5
        reasons.append("Theta auxiliary definitions")
    if any(token in block_probe for token in ("scalar", "higgs", "goldstone")) and any(
        token in text_probe for token in ("scalar", "higgs", "goldstone", "cpodd", "cpeven", "charged")
    ):
        score += 1.0
        reasons.append("matching scalar sector")
    for symbol in block.get("formula_symbols", []) or []:
        key = _normalize_formula_token(str(symbol))
        if key and key in text_probe:
            score += 0.8
            reasons.append(f"symbol {symbol}")
    for particle in block.get("particles", []) or []:
        key = _normalize_formula_token(str(particle))
        if key and len(key) >= 2 and key in text_probe:
            score += 0.4
    if family and family in text_families:
        score += 1.2
        reasons.append(f"matching {family} mass family")
    if _parser_sources_are_projected_or_pdf(parser_sources):
        score -= 1.0
    return (score if score >= 3.0 else 0.0), "; ".join(_dedupe(reasons))


def _mass_block_family(block: dict[str, object]) -> str:
    category = str(block.get("category", "") or "").strip().lower()
    if any(token in category for token in ("fermion", "quark", "lepton", "yukawa", "top")):
        return "fermion"
    if any(token in category for token in ("vector", "gauge", "boson", "w", "z", "photon", "gamma")):
        return "vector"
    if any(token in category for token in ("scalar", "higgs", "goldstone", "charged", "neutral", "cp")):
        return "scalar"
    particles = " ".join(str(item) for item in block.get("particles", []) or []).lower()
    if any(token in particles for token in ("fermion", "quark", "lepton", "top")):
        return "fermion"
    if any(token in particles for token in ("vector", "gauge", "photon", "gamma", " w", " z")):
        return "vector"
    if any(token in particles for token in ("scalar", "higgs", "goldstone")):
        return "scalar"
    return ""


def _mass_text_families(text_probe: str) -> set[str]:
    probe = str(text_probe or "")
    families: set[str] = set()
    if any(token in probe for token in ("theta", "lambda", "lam345", "goldstone", "higgs", "cpodd", "cpeven", "chargedscalar")):
        families.add("scalar")
    if any(token in probe for token in ("gauge", "vector", "mv2", "mvsq", "mw2", "mz2", "photon", "gamma", "gboson")):
        families.add("vector")
    if any(token in probe for token in ("fermion", "yukawa", "mt2", "mtsq", "yt2", "top", "quark", "lepton")):
        families.add("fermion")
    return families


def _mass_sector_contract(
    block: dict[str, object],
    texts: list[str],
    macro_aliases: dict[str, str],
) -> dict[str, object]:
    combined = "\n".join(text for text in texts if text)
    assignments = _mass_contract_assignments(combined, macro_aliases)
    assignments["auxiliaries"] = _filter_mass_auxiliaries_for_block(assignments["auxiliaries"], block)
    return {
        "schema": "MassSectorContract.v1",
        "contract_source": "reviewed_template_plus_source_evidence",
        "category": block.get("category", ""),
        "usage_contexts": block.get("usage_contexts", []),
        "source_role": block.get("source_role", ""),
        "implementation_kind": block.get("implementation_kind", ""),
        "sector_labels": _filter_mass_sector_labels(_mass_sector_labels_from_text(combined), block),
        "entry_requirements": assignments["entries"],
        "auxiliary_definitions": assignments["auxiliaries"],
        "implementation_route": _mass_contract_route(block),
        "vacuum_anchor_policy": (
            "At approxZeroTMin()[0], every BOSON_NAMES label matching a public physical mass input "
            "must reproduce that input squared; Goldstone labels must be near-zero when the reviewed vacuum breaks the symmetry."
        ),
        "forbidden_rewrites": [
            "Do not substitute a vacuum physical-mass relation for this field-dependent route.",
            "Do not invent a base+Theta split unless the reviewed source writes that split.",
            "Do not drop auxiliary definitions such as barred/composite couplings from source evidence.",
        ],
    }


def _mass_contract_assignments(text: str, macro_aliases: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    auxiliaries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    prepared = str(text or "").replace(r"\equiv", "=").replace(r"\simeq", "=").replace(":=", "=")
    for assignment in _extract_formula_assignments(prepared):
        lhs = _clean_mass_contract_lhs(assignment["lhs"])
        rhs = assignment["rhs"].strip()
        lhs_key = _normalize_formula_token(lhs, macro_aliases)
        rhs_key = _normalize_formula_token(rhs, macro_aliases)
        if _formula_assignment_is_fragment(lhs, rhs, lhs_key, rhs_key):
            continue
        kind = _mass_contract_assignment_kind(lhs_key, rhs_key)
        if not kind:
            continue
        item = {"lhs": lhs, "rhs": rhs, "normalized_lhs": lhs_key}
        key = (kind, lhs_key, rhs_key)
        if key in seen:
            continue
        seen.add(key)
        if kind == "entry":
            entries.append(item)
        else:
            auxiliaries.append(item)
    return {"entries": entries[:80], "auxiliaries": auxiliaries[:40]}


def _clean_mass_contract_lhs(lhs: str) -> str:
    text = str(lhs or "").strip()
    text = re.sub(r"^(?:in\s+which|where|with|and|here)\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" $`.,;:")


def _mass_contract_assignment_kind(lhs_key: str, rhs_key: str) -> str:
    if not lhs_key or not rhs_key:
        return ""
    if len(lhs_key) > 80 or any(token in lhs_key for token in ("caption", "figure", "section", "subsection")):
        return ""
    if any(token in lhs_key for token in ("theta", "massmatrix", "mathcalm", "mhat")):
        return "entry"
    if re.fullmatch(r"m\d{2}[a-z0-9]*", lhs_key):
        return "entry"
    auxiliary_tokens = ("bar", "lambda", "lam", "beta", "tanbeta", "tb", "vev")
    if any(token in lhs_key for token in auxiliary_tokens) and any(
        token in rhs_key for token in ("lambda", "lam", "beta", "tan", "sin", "cos", "v", "m")
    ):
        return "auxiliary"
    return ""


def _filter_mass_auxiliaries_for_block(
    auxiliaries: list[dict[str, str]],
    block: dict[str, object],
) -> list[dict[str, str]]:
    if not auxiliaries:
        return []
    family = _mass_block_family(block)
    block_probe = _normalize_probe(
        " ".join(
            [
                str(block.get("category", "") or ""),
                str(block.get("formula", "") or ""),
                " ".join(str(item) for item in block.get("formula_symbols", []) or []),
                " ".join(str(item) for item in block.get("particles", []) or []),
            ]
        )
    )
    filtered: list[dict[str, str]] = []
    for row in auxiliaries:
        lhs = str(row.get("lhs", "") or "")
        rhs = str(row.get("rhs", "") or "")
        row_probe = _normalize_probe(f"{lhs} {rhs}")
        row_families = _mass_text_families(row_probe)
        if family and row_families and family not in row_families:
            continue
        if family in {"vector", "fermion"} and any(token in row_probe for token in ("lambda", "lam")):
            if not any(token in block_probe for token in ("lambda", "lam")):
                continue
        if family == "fermion" and any(token in row_probe for token in ("gauge", "mw", "mz", "gamma", "photon")):
            if not any(token in block_probe for token in ("gauge", "mw", "mz", "gamma", "photon")):
                continue
        filtered.append(row)
    return filtered


def _mass_sector_labels_from_text(text: str) -> list[str]:
    labels: list[str] = []
    patterns = [
        r"\\Theta\^\{?([^}_{\s]+)\}?",
        r"\\widehat\{?\\mathcal\{M\}\}?\^?2?_?\{?([^}_{\s]+)\}?",
        r"\\mathcal\{M\}\^?2?_?\{?([^}_{\s]+)\}?",
        r"M_?\{?([^}_{\s]+)\}?\^2",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text or ""):
            for raw_label in _mass_sector_label_candidates(match.group(1)):
                label = _normalize_mass_sector_label(raw_label)
                if label:
                    labels.append(label)
    return _dedupe(labels)


def _mass_sector_label_candidates(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    text = text.replace(r"\pm", "pm")
    if "=" in text:
        text = text.rsplit("=", 1)[-1]
    parts = [part.strip() for part in re.split(r"[,;/|]", text) if part.strip()]
    return parts or [text]


def _normalize_mass_sector_label(value: str) -> str:
    text = str(value or "").replace(r"\pm", "pm")
    text = re.sub(r"[^A-Za-z0-9]+", "", text).strip()
    lowered = text.lower()
    entry_match = re.fullmatch(r"([a-z]+)\d{1,2}", lowered)
    if entry_match:
        lowered = entry_match.group(1)
    if lowered in {"", "s", "scalar", "x", "i", "j", "ij"}:
        return ""
    return lowered


def _filter_mass_sector_labels(labels: list[str], block: dict[str, object]) -> list[str]:
    category = str(block.get("category", "") or "").strip().lower()
    filtered: list[str] = []
    for label in labels:
        clean = str(label or "").strip().lower()
        if not clean or len(clean) > 6:
            continue
        if category == "scalar" and clean in {"z", "w", "wpm", "gamma", "ph", "photon", "t", "top"}:
            continue
        if category in {"vector", "gauge", "gauge_boson"} and clean in {"p", "a", "pm", "h", "g0", "gpm"}:
            continue
        if category in {"fermion", "fermions", "yukawa"} and clean not in {
            "t",
            "top",
            "b",
            "bottom",
            "tau",
            "f",
        }:
            continue
        filtered.append(clean)
    return _dedupe(filtered)


def _mass_contract_route(block: dict[str, object]) -> str:
    role = str(block.get("source_role", "") or "")
    kind = str(block.get("implementation_kind", "") or "")
    if role == "field_dependent_mass" and kind in {"matrix_eigenvalues", "matrix_entries"}:
        return "build reviewed field-dependent matrix entries, then diagonalize or return entries exactly as reviewed"
    if role == "field_dependent_mass":
        return "evaluate reviewed field-dependent expressions directly"
    if role == "thermal_resummed_mass":
        return "use only inside a reviewed Daisy resummation route"
    if role == "vacuum_mass_relation":
        return "use only for parameter inversion and vacuum consistency checks"
    return "review required before code generation"


def _required_source_context_terms(
    ir: ModelIR | None,
    mass_blocks: list[dict[str, object]],
    goldstone_contract: dict[str, object],
    daisy_decisions: list[dict[str, str]],
) -> list[str]:
    terms = ["mass matrix", "field-dependent", "eigenvalue", "theta", "daisy", "thermal mass", "counterterm"]
    for block in mass_blocks:
        terms.extend(str(item) for item in block.get("formula_symbols", []) or [])
        terms.extend(str(item) for item in block.get("particles", []) or [])
        contract = block.get("mass_sector_contract")
        if isinstance(contract, dict):
            terms.extend(str(item) for item in contract.get("sector_labels", []) or [])
            for row in contract.get("auxiliary_definitions", []) or []:
                if isinstance(row, dict):
                    terms.append(str(row.get("lhs", "")))
    if goldstone_contract:
        terms.extend(["goldstone", "IR cutoff", "second derivative"])
    if daisy_decisions:
        terms.extend(["longitudinal", "transverse", "Debye", "self-energy", "gamma"])
    if ir is not None:
        terms.extend(str(field.program_symbol or field.latex_symbol) for field in ir.background_fields)
    return _dedupe(term for term in terms if str(term or "").strip())


def _meaningful_mass_text(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value) and not _looks_absent(value)


def _infer_mass_source_role(text: str) -> str:
    probe = _mass_probe(text)
    if any(token in probe for token in ("thermal mass", "thermal correction", "debye", "self energy", "self-energy", "pi_x", "pi_", "m_i^2(h,t)", "m_i^2(phi,t)")):
        return "thermal_resummed_mass"
    if any(token in probe for token in ("field dependent", "field-dependent", "background field", "hat m", "\\hat", "m_i^2(phi)", "m_i^2(h", "m^2(phi)")):
        return "field_dependent_mass"
    if any(token in probe for token in ("vacuum", "vev", "physical mass", "at the minimum", "at minimum", "at phi=v", "at h=v")):
        return "vacuum_mass_relation"
    return "field_dependent_mass" if _contains_background_symbol(text) else "unknown"


def _infer_mass_implementation_kind(text: str) -> str:
    probe = _mass_probe(text)
    has_matrix = any(token in probe for token in ("matrix", "pmatrix", "begin{pmatrix}", "\\mathcal{m}", "m_ij", "m_{ij}"))
    has_eigen = any(token in probe for token in ("eigenvalue", "eigenvalues", "eig(", "diagonaliz", "diagonalise"))
    if has_matrix and has_eigen:
        return "matrix_eigenvalues"
    if has_matrix:
        return "matrix_entries"
    if has_eigen:
        return "explicit_eigenvalues"
    if "=" in str(text or ""):
        return "direct_expression"
    return "unknown"


def _mass_diagonalization_required(source_role: str, implementation_kind: str, text: str) -> bool:
    if source_role not in {"field_dependent_mass", "thermal_resummed_mass"}:
        return False
    if implementation_kind in {"matrix_eigenvalues", "matrix_entries"}:
        return True
    probe = _mass_probe(text)
    return "matrix" in probe and "diagonal" not in probe


def _infer_mass_eigenvalue_source(implementation_kind: str, text: str) -> str:
    if implementation_kind == "matrix_eigenvalues":
        return "explicit_eigenvalues"
    if implementation_kind == "matrix_entries":
        return "matrix_diagonalization"
    if implementation_kind == "explicit_eigenvalues":
        return "explicit_eigenvalues"
    if implementation_kind == "direct_expression":
        return "direct_expression"
    return "review_required"


def _infer_mass_usage_contexts(ir: ModelIR, category: str, source_role: str) -> list[str]:
    if source_role == "vacuum_mass_relation":
        return ["parameter_inversion", "vacuum_check"]
    if source_role == "thermal_resummed_mass":
        return ["Daisy"]
    contexts: list[str] = []
    mode = _implementation_mode(ir, "", PaperMemory(paper_id=ir.paper_id or ir.model_name))
    if mode in {"full_one_loop", "unknown"}:
        contexts.extend(["CW", "VT"])
    elif mode == "tree_plus_thermal_masses":
        contexts.append("VT")
    if _daisy_scheme_active(ir) and category.lower() in {"scalar", "vector", "gauge", "gauge_boson", "boson"}:
        contexts.append("Daisy")
    return _dedupe(contexts) or ["review_required"]


def _daisy_scheme_active(ir: ModelIR) -> bool:
    scheme = str(getattr(ir.daisy, "scheme", "") or "").strip().lower()
    return bool(scheme) and scheme not in {"none", "not_used", "not used", "absent", "ai infer", "unknown", "unclear"}


def _allowed_mass_usage(source_role: str) -> str:
    if source_role == "vacuum_mass_relation":
        return "parameter_inversion, vacuum_check"
    if source_role == "thermal_resummed_mass":
        return "Daisy resummation or explicitly reviewed Parwani thermal-mass replacement only"
    if source_role == "field_dependent_mass":
        return "CW, VT, Daisy zero-temperature source as specified by usage_contexts"
    return "review_required"


def _forbidden_mass_usage(source_role: str) -> str:
    if source_role == "vacuum_mass_relation":
        return "CW, VT, Daisy field-dependent spectra"
    if source_role == "thermal_resummed_mass":
        return "zero-temperature VCW/V1 and ordinary VT unless the scheme is Parwani/replacement"
    if source_role == "field_dependent_mass":
        return "parameter inversion unless explicitly evaluated at the reviewed vacuum"
    return "unreviewed automatic implementation"


def _mass_block_notes(source_role: str, implementation_kind: str) -> str:
    if source_role == "field_dependent_mass" and implementation_kind in {"matrix_eigenvalues", "matrix_entries"}:
        return "Implement source-reviewed field-dependent matrix entries directly; do not substitute vacuum physical-mass relations."
    if source_role == "vacuum_mass_relation":
        return "Use only for solving parameters and vacuum sanity checks."
    if source_role == "thermal_resummed_mass":
        return "Keep thermal/resummed masses in a reviewed Daisy route."
    return "Requires review before code generation if this formula enters Vtot."


def _infer_mass_block_particles(ir: ModelIR, category: str, text: str) -> list[str]:
    matches: list[str] = []
    normalized_text = _normalize_latex_identifier(text)
    for field in ir.physical_fields:
        if not _field_matches_mass_category(field, category):
            continue
        symbol = (field.program_symbol or field.latex_symbol or "").strip()
        if not symbol:
            continue
        candidates = [
            _normalize_latex_identifier(symbol),
            _normalize_latex_identifier(field.latex_symbol),
        ]
        if any(_particle_candidate_in_mass_text(candidate, text, normalized_text) for candidate in candidates):
            matches.append(symbol)
    category_key = category.strip().lower()
    role_tokens = {
        "scalar": ("scalar", "higgs", "goldstone"),
        "vector": ("vector", "gauge", "photon"),
        "fermion": ("fermion", "quark", "lepton", "top"),
    }.get(category_key, ())
    if role_tokens:
        for field in ir.physical_fields:
            if not _field_matches_mass_category(field, category):
                continue
            role = str(field.role or "").lower()
            if category_key == "vector" or any(token in role for token in role_tokens):
                symbol = (field.program_symbol or field.latex_symbol or "").strip()
                if symbol:
                    matches.append(symbol)
    return _dedupe(matches)


def _field_matches_mass_category(field: object, category: str) -> bool:
    category_key = str(category or "").strip().lower()
    role = str(getattr(field, "role", "") or "").lower()
    symbol = str(getattr(field, "program_symbol", "") or getattr(field, "latex_symbol", "") or "").lower()
    normalized_symbol = _normalize_latex_identifier(symbol)
    if re.fullmatch(r"phi\d+", normalized_symbol):
        return False
    if category_key == "scalar":
        return any(token in role for token in ("scalar", "higgs", "goldstone")) or symbol.startswith(("h", "s", "g"))
    if category_key == "vector":
        if any(token in role for token in ("scalar", "higgs", "goldstone", "fermion", "quark", "lepton")):
            return False
        return any(token in role for token in ("vector", "gauge", "photon")) or symbol in {"w", "z", "gamma", "photon", "wpm", "w_l", "z_l"}
    if category_key == "fermion":
        return any(token in role for token in ("fermion", "quark", "lepton", "top")) or symbol in {"t", "top", "mt"}
    return True


def _infer_mass_block_background_fields(ir: ModelIR, text: str) -> list[str]:
    matches: list[str] = []
    normalized_text = _normalize_latex_identifier(text)
    for field in ir.background_fields:
        symbol = (field.program_symbol or field.latex_symbol or "").strip()
        if not symbol:
            continue
        candidates = [
            _normalize_latex_identifier(symbol),
            _normalize_latex_identifier(field.latex_symbol),
        ]
        if any(_particle_candidate_in_mass_text(candidate, text, normalized_text) for candidate in candidates):
            matches.append(symbol)
    return _dedupe(matches)


def _infer_mass_formula_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for token in re.findall(r"\\[A-Za-z]+|[A-Za-z][A-Za-z0-9_]*", str(text or "")):
        clean = token.lstrip("\\")
        if clean and clean.lower() not in {"begin", "end", "frac", "text", "left", "right", "pmatrix"}:
            symbols.append(clean)
    return _dedupe(symbols[:80])


def _particle_candidate_in_mass_text(candidate: str, raw_text: str, normalized_text: str) -> bool:
    if not candidate:
        return False
    if len(candidate) <= 1:
        return bool(re.search(rf"(?<![A-Za-z0-9])_?{re.escape(candidate)}(?![A-Za-z0-9])", raw_text, flags=re.IGNORECASE))
    return candidate in normalized_text


def _normalize_latex_identifier(value: str) -> str:
    text = str(value or "").lower()
    replacements = {
        "\\pm": "pm",
        "^\\pm": "pm",
        "^{\\pm}": "pm",
        "^+": "p",
        "^-": "m",
        "^0": "0",
        "\\alpha": "alpha",
        "\\beta": "beta",
        "\\theta": "theta",
        "\\gamma": "gamma",
        "\\lambda": "lambda",
        "\\phi": "phi",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _mass_probe(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower())


def _contains_background_symbol(text: str) -> bool:
    probe = str(text or "")
    return bool(re.search(r"\bh_?\d\b|\\Phi|\\phi|\\varphi|\bX\b", probe))


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _build_goldstone_contract(ir: ModelIR | None) -> dict[str, object]:
    if ir is None:
        return {}
    loop = ir.zero_temp_loop
    prescription = str(loop.goldstone_prescription or "").strip()
    if _looks_absent(prescription) or prescription.lower() in {"", "none", "not_used", "not used"}:
        return {}
    mass_text = "\n".join(str(value or "") for value in (ir.masses or {}).values())
    loop_text = "\n".join(
        [
            str(loop.goldstone_applies_to or ""),
            str(loop.goldstone_notes or ""),
            str(ir.potentials.V_CW or ""),
            str(ir.potentials.V_CT_conditions or ""),
            mass_text,
        ]
    )
    probe = loop_text.lower()
    applies_to = str(loop.goldstone_applies_to or "").strip() or "review_required"
    omit_goldstone = _goldstone_is_omitted(prescription, loop_text)
    regulated = any(token in probe for token in ("ir", "cutoff", "cut-off", "regulat", "m_ir"))
    hessian_replacement = False if omit_goldstone else any(token in probe for token in ("second derivative", "hessian", "mass matrix", "outer product", "partial_i"))
    applies_to_ct = "ct" in applies_to.lower() or "counterterm" in applies_to.lower()
    return {
        "prescription": prescription,
        "applies_to": applies_to,
        "included_in_CW": False if omit_goldstone else ("goldstone" in probe and "cw" in probe),
        "mass_source": "omitted_from_CW" if omit_goldstone else ("field_dependent_mass" if _goldstone_mass_looks_field_dependent(ir) else "review_required"),
        "tadpole_replacement": _goldstone_tadpole_replacement(loop_text),
        "hessian_replacement": hessian_replacement,
        "requires_non_goldstone_ct_source": regulated and hessian_replacement and applies_to_ct,
        "regulator": "not_applicable" if omit_goldstone else _infer_goldstone_regulator(loop_text),
        "notes": _truncate(_single_line(loop.goldstone_notes or ""), 900),
    }


def _goldstone_is_omitted(prescription: str, text: str) -> bool:
    probe = " ".join([str(prescription or ""), str(text or "")]).lower().replace("-", "_")
    return any(
        token in probe
        for token in (
            "omit_goldstone",
            "goldstone omitted",
            "goldstones omitted",
            "omit goldstone",
            "omitted from",
            "excluded from",
            "not included",
            "do not include goldstone",
            "without goldstone",
        )
    )


def _goldstone_mass_looks_field_dependent(ir: ModelIR) -> bool:
    for block in _build_mass_blocks(ir):
        particles = " ".join(str(item) for item in block.get("particles", [])).lower()
        if "g" in particles and block.get("source_role") == "field_dependent_mass":
            return True
    text = "\n".join(str(value or "") for value in (ir.masses or {}).values()).lower()
    return "goldstone" in text and ("field-dependent" in text or "field dependent" in text or "eigenvalue" in text)


def _goldstone_tadpole_replacement(text: str) -> bool:
    probe = text.lower()
    if "tadpole" not in probe and "first derivative" not in probe:
        return False
    if any(token in probe for token in ("unaffected", "vanishing contribution", "do not add", "no tadpole")):
        return False
    return any(token in probe for token in ("regulat", "ir", "cutoff", "cut-off", "replace"))


def _infer_goldstone_regulator(text: str) -> str:
    match = re.search(r"m_\{?\\?rm?\s*IR\}?[^=]{0,30}=\s*([^|,\n]+)", text)
    if match:
        return _single_line(match.group(1))
    if re.search(r"m[_\s-]*h\^?2|light higgs", text, flags=re.IGNORECASE):
        return "mh**2"
    return "review_required"


def _build_daisy_implementation_decisions(ir: ModelIR | None) -> list[dict[str, str]]:
    if ir is None or not ir.daisy.thermal_coefficients:
        return []
    return [_canonical_daisy_implementation_decision(row) for row in ir.daisy.thermal_coefficients]


def _canonical_daisy_implementation_decision(row: dict[str, str]) -> dict[str, str]:
    sector = str(row.get("sector", "")).strip() or "unnamed mode"
    source_type = _normalize_source_type(row.get("implementation_source_type", ""))
    coefficient = str(row.get("coefficient", "")).strip()
    thermal_matrix = str(row.get("thermal_matrix", "")).strip()
    matrix_elements = str(row.get("matrix_elements", "")).strip()
    working_basis = str(row.get("working_basis", "")).strip()
    diagonalization_rule = str(row.get("diagonalization_rule", "")).strip()
    matrix_evidence = _join_nonempty(
        [
            f"working_basis={working_basis}" if working_basis else "",
            f"matrix_elements={matrix_elements}" if matrix_elements else "",
            f"thermal_matrix={thermal_matrix}" if thermal_matrix else "",
            f"reviewed_rule={diagonalization_rule}" if diagonalization_rule else "",
        ]
    )
    explicit_eigenvalue = source_type == "paper_explicit_eigenvalue"
    matrix_or_diagonalization = _looks_like_matrix_or_diagonalization(
        " ".join([thermal_matrix, matrix_elements, working_basis, diagonalization_rule])
    )
    direct_formula = source_type in {"paper_explicit_formula", "user_supplied"} and not matrix_or_diagonalization

    if explicit_eigenvalue:
        implementation_kind = "direct_eigenvalue"
        formula_to_implement = coefficient
        matrix_evidence_role = (
            "provenance_and_branch_check_only" if matrix_evidence else "none"
        )
        code_diagonalization_rule = "do_not_diagonalize_for_code; implement formula_to_implement directly"
        conflict_resolution = (
            "paper_explicit_eigenvalue wins over any matrix/diagonalization wording in the raw table"
            if matrix_or_diagonalization
            else "no conflict detected"
        )
    elif matrix_or_diagonalization:
        implementation_kind = "diagonalize_corrected_matrix"
        formula_to_implement = ""
        matrix_evidence_role = "implementation_source"
        code_diagonalization_rule = (
            diagonalization_rule
            or "build the corrected matrix in the reviewed working basis and diagonalize it"
        )
        conflict_resolution = "matrix path selected because no paper_explicit_eigenvalue was reviewed"
    elif direct_formula:
        implementation_kind = "direct_formula"
        formula_to_implement = coefficient
        matrix_evidence_role = "none"
        code_diagonalization_rule = "do_not_diagonalize_for_code"
        conflict_resolution = "direct formula selected because no matrix/eigenvalue ambiguity was reviewed"
    else:
        implementation_kind = "needs_review"
        formula_to_implement = coefficient
        matrix_evidence_role = "unresolved"
        code_diagonalization_rule = "block_or_raise_notimplemented_until_reviewed"
        conflict_resolution = "no source-exact eigenvalue, matrix, or direct formula was selected"

    return {
        "sector": sector,
        "implementation_kind": implementation_kind,
        "implementation_source_type": source_type or str(row.get("implementation_source_type", "")),
        "formula_to_implement": formula_to_implement,
        "matrix_evidence_role": matrix_evidence_role,
        "matrix_evidence": matrix_evidence,
        "code_diagonalization_rule": code_diagonalization_rule,
        "branch_rule": _daisy_branch_rule(row, implementation_kind),
        "conflict_resolution": conflict_resolution,
    }


def _normalize_source_type(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _looks_like_matrix_or_diagonalization(value: str) -> bool:
    probe = _normalize_probe(value)
    return any(token in probe for token in ("matrix", "pmatrix", "basis", "diagonalize", "diagonalise", "eig", "eigen"))


def _daisy_branch_rule(row: dict[str, str], implementation_kind: str) -> str:
    probe = _normalize_probe(
        " ".join(
            [
                str(row.get("sector", "")),
                str(row.get("target_mass_matrix", row.get("target", ""))),
                str(row.get("matrix_elements", "")),
                str(row.get("coefficient", "")),
                str(row.get("notes", "")),
                str(row.get("working_basis", "")),
                str(row.get("thermal_matrix", "")),
                str(row.get("diagonalization_rule", "")),
            ]
        )
    )
    neutral_gauge = (
        any(token in probe for token in ("zgamma", "zlgammal", "gamma", "photon"))
        or (
            any(token in probe for token in ("w3", "w^3", "w3b", "neutral"))
            and any(token in probe for token in ("gprime", "g^prime", "g'", "hypercharge", " b", "b)"))
        )
    )
    if not neutral_gauge:
        return "none"
    common = (
        "Determine labels at T=0 and the electroweak Higgs vacuum: the branch with eigenvalue 0 is "
        "gamma/photon; the branch matching (g^2+g'^2) v^2 / 4 is Z. Daisy zero-temperature subtraction "
        "must use the matching branch."
    )
    if implementation_kind == "direct_eigenvalue":
        return common + " For a two-branch A +/- Delta convention in the W3/B sector, minus is gamma and plus is Z."
    return common + " Do not rely on raw eigensolver order; sort or label by this zero-temperature vacuum test."


def _join_nonempty(values: list[str]) -> str:
    return "; ".join(value for value in values if value)


def _build_zero_temp_loop_notes(ir: ModelIR | None) -> dict[str, object]:
    if ir is None:
        return {
            "representation": "unknown",
            "counterterm_status": "unclear",
            "goldstone_prescription": "unclear",
            "message": "No ModelIR was supplied; inspect reviewed template section 4 manually.",
        }
    loop = ir.zero_temp_loop
    return {
        "representation": loop.representation,
        "counterterm_status": loop.counterterm_status,
        "goldstone_prescription": loop.goldstone_prescription,
        "goldstone_applies_to": loop.goldstone_applies_to,
        "goldstone_notes": loop.goldstone_notes,
        "renormalization_scheme": loop.renormalization_scheme,
        "renormalization_scale": loop.renormalization_scale,
        "message": _zero_temp_loop_message(loop.representation, loop.counterterm_status, loop.goldstone_prescription),
    }


def _zero_temp_loop_message(representation: str, counterterm_status: str, goldstone_prescription: str) -> str:
    if representation == "explicit_CW_plus_CT" or counterterm_status == "explicit_ansatz":
        return "Standalone V_CW and V_CT are expected; CT coefficients must be solved only if reviewed conditions are present."
    if representation == "combined_renormalized_V1" or counterterm_status == "implicit_in_V1":
        return "Counterterm/subtraction effects are already merged into V1; do not generate an extra V_CT term."
    if representation == "CW_only_with_prescription":
        return "Implement the CW expression together with the reviewed renormalization/Goldstone prescription; do not invent CT coefficients."
    if representation == "CW_only":
        return "Implement the plain reviewed CW expression; CT effects are not used unless later reviewed."
    if goldstone_prescription not in {"", "none", "unclear"}:
        return "Goldstone prescription must be applied consistently to V_CW and any CT-equation derivatives that use V_CW."
    return "Zero-temperature one-loop convention needs review."


def _build_counterterm_notes(
    ir: ModelIR | None,
    sector_formulas: dict[str, list[MaterialFormula]],
) -> dict[str, object]:
    if ir is None:
        return {
            "status": "unknown",
            "message": "No ModelIR was supplied; inspect reviewed template section 4 manually.",
        }
    V_CT = (ir.potentials.V_CT or "").strip()
    V_CW = (ir.potentials.V_CW or "").strip()
    V1 = (ir.potentials.V1 or "").strip()
    conditions = (ir.potentials.V_CT_conditions or "").strip()
    loop = ir.zero_temp_loop
    has_ct_ansatz = bool(V_CT) and not _looks_absent(V_CT) and "included" not in V_CT.lower()
    has_conditions = _looks_like_reviewed_conditions(conditions)
    status = "not_applicable"
    message = "No standalone counterterm ansatz was reviewed."
    if loop.counterterm_status == "implicit_in_V1":
        status = "implicit_in_V1"
        message = (
            "The reviewed model card marks counterterm/subtraction effects as implicit in a combined V1 or "
            "prescription. Backend code must preserve the combined expression and must not add a separate V_CT."
        )
    elif loop.counterterm_status == "not_used":
        status = "not_used"
        message = "The reviewed model card marks counterterms as not used."
    elif has_ct_ansatz and has_conditions:
        status = "ct_ansatz_with_conditions"
        message = (
            "Standalone V_CT and renormalization conditions are present. The backend code should solve internal "
            "counterterm coefficients from these conditions as a reviewed linear algebra system in init()."
        )
    elif has_ct_ansatz:
        status = "ct_ansatz_missing_conditions"
        message = (
            "Standalone V_CT is present but renormalization conditions are missing or marked None. Template "
            "validation must ask for the missing conditions before compilation."
        )
    elif V1 and ("ct" in V1.lower() or "counter" in V1.lower()):
        status = "combined_loop_contains_ct"
        message = (
            "The reviewed model card indicates counterterm effects inside V1. If CT coefficients are implicit, "
            "the contract must preserve that prescription and ask for details before inventing coefficients."
        )
    linear_system = _build_ct_linear_system_notes(
        V_CT,
        conditions,
        status=status,
        reviewed_basis=ir.potentials.V_CT_linear_basis,
        reviewed_conditions=ir.potentials.V_CT_linear_conditions,
    )
    return {
        "status": status,
        "message": message,
        "V_CW": V_CW,
        "V_CT": V_CT,
        "V1": V1,
        "renormalization_conditions": conditions,
        "zero_temp_loop_representation": loop.representation,
        "counterterm_status": loop.counterterm_status,
        "goldstone_prescription": loop.goldstone_prescription,
        "goldstone_applies_to": loop.goldstone_applies_to,
        "linear_system_notes": linear_system,
        "zero_temp_loop_candidates": [item.to_dict() for item in sector_formulas.get("zero_temp_loop", [])[:5]],
    }


def _build_ct_linear_system_notes(
    V_CT: str,
    conditions: str,
    *,
    status: str,
    reviewed_basis: list[dict[str, str]] | None = None,
    reviewed_conditions: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    coefficients = _reviewed_ct_basis_candidates(reviewed_basis or []) or _infer_ct_basis_candidates(V_CT)
    condition_rows = _reviewed_ct_condition_candidates(reviewed_conditions or []) or _infer_ct_condition_candidates(conditions)
    coefficient_count = len(coefficients)
    condition_count = len(condition_rows)
    can_count = coefficient_count > 0 and condition_count > 0
    counts_match = bool(can_count and coefficient_count == condition_count)
    if status != "ct_ansatz_with_conditions":
        gate = "not_applicable"
        message = "No reviewed standalone V_CT ansatz with conditions is available."
    elif not can_count:
        gate = "needs_review"
        message = (
            "PTagent could not count both CT coefficients and independent conditions. Ask the user to fill "
            "a CT basis table and an independent condition table before compiling."
        )
    elif not counts_match:
        gate = "blocked_count_mismatch"
        message = (
            f"Count mismatch: {coefficient_count} CT coefficient candidate(s) but {condition_count} independent "
            "condition candidate(s). Review missing/redundant conditions; do not fit with an optimizer."
        )
    else:
        gate = "ready_for_linear_solve"
        message = (
            "Counts match. The compiler can construct A_ai = L_a[O_i](vacuum), "
            "b_a = -L_a[V_CW](vacuum), then np.linalg.solve(A, b)."
        )
    return {
        "status": gate,
        "message": message,
        "coefficient_count": coefficient_count,
        "condition_count": condition_count,
        "counts_match": counts_match,
        "ct_basis_candidates": coefficients,
        "condition_candidates": condition_rows,
        "construction_rule": "V_CT=sum_i delta_i O_i(phi); A_ai=L_a[O_i](vacuum); b_a=-L_a[V_CW](vacuum).",
    }


def _reviewed_ct_basis_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in rows:
        coefficient = str(row.get("coefficient", "")).strip()
        basis = str(row.get("basis_function", "")).strip()
        if not coefficient or _looks_absent(coefficient) or not basis or _looks_absent(basis):
            continue
        candidates.append(
            {
                "coefficient": coefficient,
                "basis_function": basis,
                "source": "reviewed_linear_spec",
            }
        )
    return _dedupe_candidate_dicts(candidates, key="coefficient")


def _reviewed_ct_condition_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in rows:
        label = str(row.get("label", "")).strip()
        operator = str(row.get("operator", "")).strip()
        independent = str(row.get("independent", "")).strip()
        if not label or _looks_absent(label) or not operator or _looks_absent(operator):
            continue
        if independent.lower().replace(" ", "_") in {"no", "false", "dependent"}:
            continue
        candidates.append(
            {
                "condition": f"{label}: {operator}",
                "operator_hint": _condition_operator_hint(operator),
                "quantity_determined": str(row.get("rhs_source", "")).strip(),
                "evaluation_point": str(row.get("evaluation_point", "")).strip(),
                "independent": independent or "unclear",
                "source": "reviewed_linear_spec",
            }
        )
    return candidates


def _infer_ct_basis_candidates(V_CT: str) -> list[dict[str, str]]:
    text = str(V_CT or "").strip()
    if not text or _looks_absent(text):
        return []
    rows = _parse_markdown_table_rows(text)
    table_candidates: list[dict[str, str]] = []
    for row in rows:
        if len(row) >= 2:
            coefficient = row[0].strip()
            basis = row[1].strip()
            if not coefficient or _looks_absent(coefficient) or not basis or _looks_absent(basis):
                continue
            table_candidates.append(
                {
                    "coefficient": coefficient,
                    "basis_function": basis,
                    "source": "reviewed_table",
                }
            )
    if table_candidates:
        return _dedupe_candidate_dicts(table_candidates, key="coefficient")

    candidates: list[dict[str, str]] = []
    for term in _split_latex_sum_terms(text):
        if "delta" not in term.lower() and "\\delta" not in term.lower():
            continue
        coefficient = _extract_delta_coefficient(term)
        if not coefficient:
            continue
        basis = _remove_first_delta_coefficient(term, coefficient)
        candidates.append(
            {
                "coefficient": coefficient,
                "basis_function": basis or "needs_review",
                "source": "latex_term_candidate",
            }
        )
    return _dedupe_candidate_dicts(candidates, key="coefficient")


def _infer_ct_condition_candidates(conditions: str) -> list[dict[str, str]]:
    text = str(conditions or "").strip()
    if not text or _looks_absent(text):
        return []
    rows = _parse_markdown_table_rows(text)
    candidates: list[dict[str, str]] = []
    if rows:
        for row in rows:
            if len(row) < 2:
                continue
            condition = row[1].strip() if row[0].strip().isdigit() or row[0].strip().lower() in {"no.", "no"} else row[0].strip()
            quantity = row[2].strip() if len(row) > 2 else ""
            if not condition or _looks_absent(condition):
                continue
            candidates.append(
                {
                    "condition": condition,
                    "quantity_determined": quantity,
                    "operator_hint": _condition_operator_hint(condition),
                    "source": "reviewed_table",
                }
            )
    if candidates:
        return candidates

    for idx, line in enumerate(_nonempty_condition_lines(text), start=1):
        candidates.append(
            {
                "condition": line,
                "quantity_determined": "",
                "operator_hint": _condition_operator_hint(line),
                "source": f"condition_line_{idx}",
            }
        )
    return candidates


def _parse_markdown_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or all(not cell for cell in cells):
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
            continue
        lowered = " ".join(cells).lower()
        if "condition" in lowered and ("quantity" in lowered or "determined" in lowered):
            continue
        if "coefficient" in lowered and ("basis" in lowered or "operator" in lowered):
            continue
        rows.append(cells)
    return rows


def _split_latex_sum_terms(text: str) -> list[str]:
    normalized = str(text or "")
    normalized = normalized.replace("\n", " ")
    normalized = re.sub(r"\\begin\{[^}]+\}|\\end\{[^}]+\}", " ", normalized)
    normalized = re.sub(r"^\s*V_\{?\\?rm?\s*CT\}?[^=]*=", "", normalized)
    parts = re.split(r"(?<![\^_])\s*(?=[+-])", normalized)
    return [part.strip().lstrip("+").strip() for part in parts if part.strip()]


def _extract_delta_coefficient(term: str) -> str:
    python_match = re.search(r"\bdelta_[A-Za-z0-9_]+", term)
    if python_match:
        return python_match.group(0)
    match = re.search(r"(\\delta|delta)\s*(?:\{)?\s*([^+\-*/=,;|]{1,45})", term, flags=re.IGNORECASE)
    if not match:
        return ""
    tail = match.group(2).strip()
    tail = re.split(r"\s{2,}|\\,|\\quad|\\text|(?=[A-Za-z]\\s*(?:\^|_)?\s*[A-Za-z0-9]*\s*(?:\^|\{|\())", tail, maxsplit=1)[0].strip()
    tail = tail.strip("{} ")
    return (match.group(1) + (" " + tail if tail else "")).strip()


def _remove_first_delta_coefficient(term: str, coefficient: str) -> str:
    basis = term
    if coefficient:
        basis = basis.replace(coefficient, "1", 1)
    basis = re.sub(r"^\s*[+-]?\s*\*?\s*", "", basis).strip()
    basis = re.sub(r"^1\s*\\,?\s*\*?\s*", "", basis).strip()
    return basis.strip() or "needs_review"


def _nonempty_condition_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line in {"\\[", "\\]", "$$", "---"}:
            continue
        if line.startswith("|"):
            continue
        if _looks_absent(line):
            continue
        lines.append(line)
    return lines


def _condition_operator_hint(condition: str) -> str:
    probe = str(condition or "").lower()
    partial_count = probe.count("\\partial") + probe.count("partial")
    if "mix" in probe or partial_count >= 2 or "hessian" in probe or "mass" in probe:
        return "hessian_or_mixed_second_derivative"
    if partial_count == 1 or "tadpole" in probe or "vev" in probe:
        return "gradient_or_tadpole"
    return "needs_review"


def _dedupe_candidate_dicts(items: list[dict[str, str]], *, key: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        value = re.sub(r"\s+", " ", str(item.get(key, "")).strip())
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        clean = dict(item)
        clean[key] = value
        result.append(clean)
    return result


def _implementation_mode(ir: ModelIR | None, template_markdown: str, memory: PaperMemory) -> str:
    if ir is not None:
        mode = _normalize_implementation_mode(ir.implementation_mode)
        if mode != "unknown":
            return mode
    text = " ".join(
        [
            template_markdown[:12000],
            " ".join(span.text for span in memory.source_spans[:20]),
        ]
    ).lower()
    if any(token in text for token in ("high-temperature", "high temperature", "high-t", "improved tree", "phenomenological")):
        return "simplified_effective_potential"
    if any(token in text for token in ("thermal mass replacement", "thermal-mass replacement", "replace", "thermal masses are inserted")):
        return "tree_plus_thermal_masses"
    if any(token in text for token in ("coleman", "v_cw", "v_{\\rm cw}", "j_b", "j_f", "v1t", "v_1t")):
        return "full_one_loop"
    return "unknown"


def _normalize_implementation_mode(value: object) -> str:
    normalized = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "full": "full_one_loop",
        "full_loop": "full_one_loop",
        "one_loop": "full_one_loop",
        "full_one_loop_cosmotransitions": "full_one_loop",
        "direct_high_temperature": "simplified_effective_potential",
        "direct_high_t": "simplified_effective_potential",
        "high_temperature": "simplified_effective_potential",
        "high_t": "simplified_effective_potential",
        "simplified": "simplified_effective_potential",
        "phenomenological": "simplified_effective_potential",
        "thermal_mass_replacement": "tree_plus_thermal_masses",
        "tree_thermal": "tree_plus_thermal_masses",
        "tree_plus_thermal": "tree_plus_thermal_masses",
        "ai_infer": "unknown",
        "none": "unknown",
    }
    mode = aliases.get(normalized, normalized)
    return mode if mode in {"full_one_loop", "simplified_effective_potential", "tree_plus_thermal_masses", "unknown"} else "unknown"


def _build_implementation_guidance(mode: str, ir: ModelIR | None) -> dict[str, object]:
    mode = _normalize_implementation_mode(mode)
    if mode == "full_one_loop":
        return {
            "require_vtot": True,
            "require_mass_interface_in_vtot": True,
            "require_v1t": True,
            "vtot_strategy": "Implement V0 + zero-temperature one-loop pieces + standard thermal integrals + Daisy according to the reviewed scheme.",
            "mass_interface": "boson_massSq and fermion_massSq are required and must be used by V1/Vtot/V1T.",
        }
    if mode == "tree_plus_thermal_masses":
        return {
            "require_vtot": True,
            "require_mass_interface_in_vtot": False,
            "require_v1t": False,
            "vtot_strategy": "Implement Vtot from V0 plus the reviewed thermal-mass replacement or T-dependent parameters; do not double count a separate V1T unless reviewed.",
            "mass_interface": "boson_massSq/fermion_massSq may be provided for diagnostics or smoke tests, but Vtot does not have to call them.",
        }
    if mode == "simplified_effective_potential":
        return {
            "require_vtot": True,
            "require_mass_interface_in_vtot": False,
            "require_v1t": False,
            "vtot_strategy": "Implement Vtot directly from the final reviewed finite-temperature effective potential used in the paper.",
            "mass_interface": "Do not invent a full mass spectrum merely to satisfy an interface; minimal empty mass arrays are acceptable if the simplified Vtot does not use masses.",
        }
    return {
        "require_vtot": True,
        "require_mass_interface_in_vtot": "review",
        "require_v1t": "review",
        "vtot_strategy": "The working potential mode is not resolved. Ask for review before generating trusted code.",
        "mass_interface": "Decide whether the paper uses a full one-loop mass-spectrum workflow or a direct simplified Vtot.",
    }


def _build_compile_plan(
    *,
    ir: ModelIR | None,
    implementation_mode: str,
    implementation_guidance: dict[str, object],
    canonical_parameter_graph: dict[str, object],
    compile_defaults: list[dict[str, object]],
    mass_blocks: list[dict[str, object]],
    goldstone_contract: dict[str, object],
    daisy_decisions: list[dict[str, str]],
    zero_temp_loop_notes: dict[str, object],
    counterterm_notes: dict[str, object],
) -> dict[str, object]:
    mode = _normalize_implementation_mode(implementation_mode)
    public_inputs = [
        str(row.get("program_symbol", "")).strip()
        for row in canonical_parameter_graph.get("public_inputs", []) or []
        if str(row.get("program_symbol", "")).strip()
    ]
    defaults = {
        str(row.get("program_symbol", "")).strip(): {
            "default_value": row.get("default_value"),
            "source_type": row.get("source_type", ""),
            "original_value_or_range": row.get("original_value_or_range", ""),
        }
        for row in compile_defaults
        if str(row.get("program_symbol", "")).strip()
    }
    zero_temp_status = str(zero_temp_loop_notes.get("counterterm_status", "") or "")
    has_explicit_ct = zero_temp_status == "explicit_ansatz"
    goldstone_route = _compile_plan_goldstone_route(goldstone_contract)
    required_methods = _compile_plan_required_methods(
        mode=mode,
        has_explicit_ct=has_explicit_ct,
        has_daisy=bool(daisy_decisions),
        goldstone_route=goldstone_route,
    )
    required_helpers = _compile_plan_required_helpers(goldstone_route)
    plan = {
        "version": 1,
        "purpose": "Compile-readiness summary for the deterministic contract compiler. Treat unresolved entries as template questions.",
        "reviewer_role": "Use this summary to fill the Markdown contract worksheet; do not choose formula roles or parameter basis implicitly.",
        "implementation_mode": mode,
        "public_inputs": public_inputs,
        "compile_defaults": defaults,
        "derived_parameters": [
            _compile_plan_parameter_row(row)
            for row in canonical_parameter_graph.get("derived_parameters", []) or []
        ],
        "branch_constraints": list(canonical_parameter_graph.get("branch_constraints", []) or []),
        "calibration_conditions": list(canonical_parameter_graph.get("calibration_conditions", []) or []),
        "required_module_symbols": ["MODEL_METADATA", "FIELD_ORDER", "BOSON_NAMES", "FERMION_NAMES", "DEFAULT_PARAMETER_SOURCES"],
        "required_functions": ["build_model", "run_transitions"],
        "required_class_methods": required_methods,
        "required_helpers": required_helpers,
        "init_order": [
            "set self.Ndim",
            "assign public inputs and fixed constants",
            "assign renormalization scale and any IR regulators/cutoffs",
            "solve derived/calibration parameters",
            "solve internal CT coefficients only after every helper dependency is initialized",
        ],
        "mass_routes": [_compile_plan_mass_route(block) for block in mass_blocks],
        "goldstone_ct_route": goldstone_route,
        "daisy_routes": [_compile_plan_daisy_route(row) for row in daisy_decisions],
        "zero_temperature_loop_route": {
            "representation": zero_temp_loop_notes.get("representation", "unknown"),
            "counterterm_status": zero_temp_status,
            "message": zero_temp_loop_notes.get("message", ""),
        },
        "counterterm_route": _compile_plan_counterterm_route(counterterm_notes, has_explicit_ct),
        "vtot_strategy": implementation_guidance.get("vtot_strategy", ""),
        "forbidden_actions": [
            "Do not introduce public inputs outside public_inputs.",
            "Do not rename public inputs, DEFAULT_PARAMETER_SOURCES keys, build_model args, or init args.",
            "Do not use vacuum_mass_relation blocks inside boson_massSq/CW/VT/Daisy field-dependent spectra.",
            "Do not invent CT coefficients or solve CT systems unless counterterm_route.requires_solver is true.",
            "Do not finite-difference full Goldstone-including VCW when goldstone_ct_route.requires_non_goldstone_ct_source is true.",
            "Do not copy model-specific CT/Goldstone formulas from another paper.",
            "Do not hide V_CW, V_CT, Daisy, or mass-routing logic inside one opaque expression.",
        ],
    }
    if ir is not None:
        plan["paper_id"] = ir.paper_id
        plan["model_name"] = ir.model_name
    return plan


def _compile_plan_parameter_row(row: object) -> dict[str, object]:
    if isinstance(row, dict):
        return {
            "latex_symbol": row.get("latex_symbol", ""),
            "program_symbol": row.get("program_symbol", ""),
            "expression": row.get("value", row.get("expression", "")),
            "notes": row.get("role", row.get("notes", "")),
        }
    return {
        "latex_symbol": getattr(row, "latex_symbol", ""),
        "program_symbol": getattr(row, "program_symbol", ""),
        "expression": getattr(row, "value", ""),
        "notes": getattr(row, "role", ""),
    }


def _compile_plan_required_methods(
    *,
    mode: str,
    has_explicit_ct: bool,
    has_daisy: bool,
    goldstone_route: dict[str, object],
) -> list[str]:
    methods = [
        "init(self, <public inputs>)",
        "approxZeroTMin(self)",
        "forbidPhaseCrit(self, X)",
        "V0(self, X)",
        "Vtot(self, X, T, include_radiation=True)",
        "boson_massSq(self, X, T)",
        "fermion_massSq(self, X)",
    ]
    if mode == "full_one_loop":
        methods.extend(["V1(self, bosons, fermions)", "V1T_from_X(self, X, T, include_radiation=True)"])
    if has_daisy:
        methods.append("Vdaisy(self, X, T, bosons=None)")
    if has_explicit_ct:
        methods.extend(
            [
                "VCW(self, bosons, fermions) or reviewed equivalent",
                "VCT(self, X)",
                "_build_counterterm_linear_system(self, vacuum)",
                "_solve_counterterms(self)",
            ]
        )
    if _compile_plan_truthy(goldstone_route.get("requires_non_goldstone_ct_source")):
        methods.append("_VCW_non_goldstone_for_ct(self, X)")
    return _dedupe(methods)


def _compile_plan_required_helpers(goldstone_route: dict[str, object]) -> list[str]:
    helpers: list[str] = []
    helper = goldstone_route.get("required_helper")
    if isinstance(helper, str) and helper.strip():
        helpers.append(helper.strip())
    return _dedupe(helpers)


def _compile_plan_mass_route(block: dict[str, object]) -> dict[str, object]:
    return {
        "id": block.get("id", ""),
        "category": block.get("category", ""),
        "particles": block.get("particles", []),
        "background_fields": block.get("background_fields", []),
        "formula_symbols": block.get("formula_symbols", []),
        "usage_contexts": block.get("usage_contexts", []),
        "source_role": block.get("source_role", ""),
        "implementation_kind": block.get("implementation_kind", ""),
        "diagonalization_required": block.get("diagonalization_required", False),
        "eigenvalue_source": block.get("eigenvalue_source", ""),
        "allowed_usage": block.get("allowed_usage", ""),
        "forbidden_usage": block.get("forbidden_usage", ""),
        "formula_snippet": block.get("formula", ""),
        "source_evidence_snippets": block.get("source_evidence_snippets", [])[:3],
        "mass_sector_contract": block.get("mass_sector_contract", {}),
        "implementation_contract": (
            "Implement this source formula directly for the listed usage contexts. "
            "At the reviewed zero-temperature vacuum, any public physical-mass inputs matching returned "
            "BOSON_NAMES must be reproduced by boson_massSq(X_vac, 0)."
        ),
    }


def _compile_plan_goldstone_route(contract: dict[str, object]) -> dict[str, object]:
    if not contract:
        return {"required": False}
    requires_non_goldstone = _compile_plan_truthy(contract.get("requires_non_goldstone_ct_source"))
    route: dict[str, object] = {
        "required": True,
        "prescription": contract.get("prescription", ""),
        "applies_to": contract.get("applies_to", ""),
        "mass_source": contract.get("mass_source", ""),
        "included_in_CW": contract.get("included_in_CW", ""),
        "requires_non_goldstone_ct_source": requires_non_goldstone,
        "hessian_replacement": contract.get("hessian_replacement", ""),
        "regulator": contract.get("regulator", ""),
    }
    if requires_non_goldstone:
        route.update(
            {
                "required_helper": "_VCW_non_goldstone_for_ct",
                "finite_difference_source": "non_goldstone_CW_only",
                "exclude_boson_name_substrings": ["G0", "Gpm", "Goldstone"],
                "implementation_pattern": [
                    "bosons = self.boson_massSq(X, 0.0)",
                    "m2, dof, c = bosons",
                    "mask = np.array([not any(tag in name for tag in ('G0', 'Gpm', 'Goldstone')) for name in BOSON_NAMES], dtype=bool)",
                    "return self.V1((m2[..., mask], dof[mask], c[mask]), self.fermion_massSq(X))",
                    "CT finite differences call _VCW_non_goldstone_for_ct(X), then add reviewed regulated Goldstone Hessian replacement exactly once.",
                ],
                "forbidden": "Do not finite-difference full self.V1(self.boson_massSq(X, 0.0), fermions) with Goldstones still included.",
            }
        )
    return route


def _compile_plan_daisy_route(row: dict[str, str]) -> dict[str, str]:
    return {
        "mode": str(row.get("mode", "")),
        "implementation_kind": str(row.get("implementation_kind", "")),
        "formula_to_implement": str(row.get("formula_to_implement", "")),
        "code_diagonalization_rule": str(row.get("code_diagonalization_rule", "")),
        "branch_rule": str(row.get("branch_rule", "")),
    }


def _compile_plan_counterterm_route(counterterm: dict[str, object], has_explicit_ct: bool) -> dict[str, object]:
    return {
        "requires_solver": has_explicit_ct,
        "status": counterterm.get("status", ""),
        "message": counterterm.get("message", ""),
        "linear_system_notes": counterterm.get("linear_system_notes", {}),
        "rules": [
            "Use reviewed CT basis and reviewed condition/operator specs.",
            "Build A and b visibly from those specs.",
            "Use np.linalg.solve only for square independent systems.",
            "Treat CT coefficients as internal solved attributes, not public inputs.",
        ],
    }


def _compile_plan_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "1", "required"}


def _build_compile_defaults(ir: ModelIR | None, *, paper_id: str) -> list[dict[str, object]]:
    if ir is None:
        return []
    rows: list[dict[str, object]] = []
    for param in ir.input_parameters:
        original = (param.value or "").strip()
        if not param.program_symbol or _looks_absent(param.program_symbol):
            continue
        if _input_value_needs_user_resolution(original):
            continue
        default_value, source_type, notes = _default_value_from_text(
            original,
            seed_key=f"{paper_id}:{param.program_symbol}:{original}",
        )
        rows.append(
            {
                "latex_symbol": param.latex_symbol,
                "program_symbol": param.program_symbol,
                "original_value_or_range": original,
                "default_value": default_value,
                "source_type": source_type,
                "notes": notes,
            }
        )
    return rows


def _fixed_parameter_rows(ir: ModelIR | None, *, paper_id: str) -> list[dict[str, object]]:
    if ir is None:
        return []
    rows: list[dict[str, object]] = []
    for param in getattr(ir, "fixed_parameters", []):
        original = (param.value or "").strip()
        if not param.program_symbol or _looks_absent(param.program_symbol):
            continue
        default_value, source_type, notes = _default_value_from_text(
            original,
            seed_key=f"{paper_id}:fixed:{param.program_symbol}:{original}",
        )
        rows.append(
            {
                "latex_symbol": param.latex_symbol,
                "program_symbol": param.program_symbol,
                "value_or_range": original,
                "default_value": default_value,
                "source_type": source_type if source_type != "missing" else "fixed_constant",
                "notes": param.role or notes,
            }
        )
    return rows


def _build_canonical_parameter_graph(ir: ModelIR | None) -> dict[str, object]:
    if ir is None:
        return {}
    public_inputs = [
        {
            "latex_symbol": param.latex_symbol,
            "program_symbol": param.program_symbol,
            "value_or_range": param.value,
            "notes": param.role,
        }
        for param in ir.input_parameters
    ]
    return {
        "public_inputs": public_inputs,
        "fixed_parameters": _fixed_parameter_rows(ir, paper_id=ir.paper_id or ir.model_name),
        "branch_constraints": list(ir.branch_constraints),
        "calibration_conditions": list(ir.calibration_conditions),
        "derived_parameters": list(ir.derived_parameters),
        "rules": [
            "Public inputs are the runtime/build_model parameter basis: scanned or user-supplied model parameters only.",
            "Fixed parameters/constants are available to generated code as constants/defaults, but are not build_model inputs.",
            "Branch constraints are applied before calibration conditions and closure checks.",
            "Calibration conditions use public inputs to solve potential parameters; their left-hand physical masses are not derived symbols unless explicitly listed in Derived Parameters.",
            "Derived Parameters are the canonical expressions code generation should implement.",
        ],
    }


def _default_value_from_text(value: str, *, seed_key: str) -> tuple[float | None, str, str]:
    if _looks_absent(value):
        return None, "missing", "No numeric default could be extracted."
    text = value.replace("–", "--").replace("—", "--").replace("−", "-")
    text = _normalize_range_text(value)
    numbers = [float(item) for item in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", text)]
    if not numbers:
        return None, "missing", "No numeric value was found in the reviewed input field."
    bounds = _range_bounds_from_text(text)
    if bounds is not None:
        low, high = bounds
        if high < low:
            low, high = high, low
        midpoint = 0.5 * (low + high)
        return (
            round(midpoint, 12),
            "scan_range_midpoint_default",
            f"Midpoint of reviewed scan range [{low}, {high}].",
        )
    range_match = re.search(
        r"([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*(?:--|-|to|~|,|/|per)\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if range_match:
        low, high = float(range_match.group(1)), float(range_match.group(2))
        if high < low:
            low, high = high, low
        midpoint = 0.5 * (low + high)
        return (
            round(midpoint, 12),
            "scan_range_midpoint_default",
            f"Midpoint of reviewed scan range [{low}, {high}].",
        )
    return numbers[0], "source_default", "Numeric default read from the reviewed input field."


def _normalize_range_text(value: str) -> str:
    text = str(value or "")
    replacements = {
        "–": "-",
        "—": "-",
        "−": "-",
        "≤": "<=",
        "≥": ">=",
        r"\leq": "<=",
        r"\le": "<=",
        r"\geq": ">=",
        r"\ge": ">=",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("--", " to ")
    text = re.sub(r"(?<=\d)\s*-\s*(?=\d)", " to ", text)
    return text


def _range_bounds_from_text(text: str) -> tuple[float, float] | None:
    if not _looks_like_range_text(text):
        return None
    squared_bounds = _squared_range_bounds_from_text(text)
    if squared_bounds is not None:
        return squared_bounds
    tokens = _number_unit_tokens(text)
    if len(tokens) < 2:
        return None
    low = _unit_scaled_number(tokens[0][0], tokens[0][1])
    high = _unit_scaled_number(tokens[-1][0], tokens[-1][1])
    return low, high


def _looks_like_range_text(text: str) -> bool:
    probe = str(text or "").lower()
    return any(token in probe for token in ("<=", ">=", " to ", "--", " - ", "range", "[", "]", ","))


def _number_unit_tokens(text: str) -> list[tuple[float, str]]:
    tokens: list[tuple[float, str]] = []
    source = str(text or "")
    pattern = re.compile(
        r"([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:\\?text\{?\s*)?"
        r"(tev|gev|mev|kev)?",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(source):
        if _number_match_is_power_marker(source, match):
            continue
        try:
            number = float(match.group(1))
        except ValueError:
            continue
        unit = (match.group(2) or "").lower()
        tokens.append((number, unit))
    return tokens


def _squared_range_bounds_from_text(text: str) -> tuple[float, float] | None:
    if not _looks_like_range_text(text):
        return None
    source = str(text or "")
    context_unit = _squared_range_context_unit(source)
    pattern = re.compile(
        r"(?:\\?\(\s*)?"
        r"(?P<sign>[-+]?)\s*"
        r"(?P<body>\\?\(?\s*"
        r"(?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:\\\s*)?"
        r"(?:(?:\\?text\{\s*(?P<unit1>tev|gev|mev|kev)\s*\})|(?P<unit2>tev|gev|mev|kev))?"
        r"\s*\\?\)?)"
        r"\s*(?:\^|\*\*)\s*\{?\s*2\s*\}?",
        flags=re.IGNORECASE,
    )
    values: list[float] = []
    for match in pattern.finditer(source):
        unit = (match.group("unit1") or match.group("unit2") or context_unit or "").lower()
        matched = match.group(0)
        body = match.group("body") or ""
        if not unit and "(" not in matched and r"\(" not in matched and ")" not in body and r"\)" not in body:
            continue
        try:
            base = float(match.group("number"))
        except (TypeError, ValueError):
            continue
        scaled = _unit_scaled_number(base, unit)
        value = scaled * scaled
        if (match.group("sign") or "").strip() == "-":
            value = -value
        values.append(value)
    if len(values) < 2:
        return None
    return values[0], values[-1]


def _squared_range_context_unit(text: str) -> str:
    match = re.search(
        r"(?:\\?text\{\s*)?(tev|gev|mev|kev)\s*\}?\s*(?:\\?\(\s*)?(?:\^|\*\*)\s*\{?\s*2\s*\}?",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    return (match.group(1).lower() if match else "")


def _number_match_is_power_marker(text: str, match: re.Match[str]) -> bool:
    token = match.group(1)
    try:
        number = float(token)
    except ValueError:
        return False
    if number != 2:
        return False
    prefix = re.sub(r"\s+", "", text[max(0, match.start() - 12): match.start()]).lower()
    return (
        prefix.endswith("^")
        or prefix.endswith("^{")
        or prefix.endswith("**")
        or prefix.endswith(r"\(^")
        or prefix.endswith(r"\(^{")
    )


def _unit_scaled_number(number: float, unit: str) -> float:
    if unit == "tev":
        return number * 1000.0
    if unit == "mev":
        return number / 1000.0
    if unit == "kev":
        return number / 1_000_000.0
    return number


def _input_value_needs_user_resolution(value: str) -> bool:
    normalized = str(value or "").strip().strip("$`").lower().replace("-", "_")
    normalized = re.sub(r"\s+", "_", normalized)
    if normalized in {
        "",
        "none",
        "unknown",
        "missing",
        "ambiguous",
        "unclear",
        "tbd",
        "user_supplied",
        "manual_input",
        "to_be_supplied",
    }:
        return True
    probe = normalized.replace("_", " ")
    if any(
        token in probe
        for token in (
            "derived from",
            "determined by",
            "computed from",
            "solved from",
            "calibration condition",
            "minimization",
            "minimisation",
            "tadpole",
        )
    ):
        return True
    if " or " in probe and any(token in probe for token in ("alignment", "general", "branch", "choice", "convention")):
        return True
    if any(token in probe for token in ("alignment limit", "sin beta alpha", "sin beta-alpha")):
        return True
    return any(
        token in probe
        for token in (
            "ai infer",
            "placeholder",
            "needs review",
            "user supplied",
            "to be supplied",
            "supply later",
        )
    )


def _counterterm_notes_markdown(counterterm: dict[str, object]) -> list[str]:
    lines = [
        "",
        "## Counterterm / Renormalization Conditions",
        "",
        f"- Status: `{counterterm.get('status', 'unknown')}`",
        f"- Guidance: {counterterm.get('message', '')}",
        "",
        "### Reviewed one-loop pieces",
        "",
        "| Piece | Content |",
        "|---|---|",
        f"| V_CW | {_table_cell(counterterm.get('V_CW', ''))} |",
        f"| V_CT | {_table_cell(counterterm.get('V_CT', ''))} |",
        f"| V1 | {_table_cell(counterterm.get('V1', ''))} |",
        "",
        "### Renormalization conditions",
        "",
    ]
    conditions = str(counterterm.get("renormalization_conditions", "") or "").strip()
    if conditions:
        lines.extend(["```markdown", conditions, "```", ""])
    else:
        lines.extend(["No reviewed renormalization conditions were extracted.", ""])
    linear = counterterm.get("linear_system_notes")
    if isinstance(linear, dict):
        lines.extend(_ct_linear_system_notes_markdown(linear))
    lines.extend(
        [
            "Rules for filling the contract:",
            "- If a standalone `V_CT` ansatz and renormalization conditions are present, CT coefficients are internal solved quantities, not user free inputs.",
            "- If the zero-temperature convention says `implicit_in_V1` or `combined_renormalized_V1`, do not create a new standalone `V_CT`; preserve the reviewed combined expression.",
            "- If the paper gives only a CW expression with a prescription, implement that prescription and ask for review before inventing CT coefficients.",
            "- Build equations from the reviewed conditions, usually preserving zero-temperature tadpoles, mass matrix entries, and mixing information at the reviewed renormalization point.",
            "- A Goldstone prescription alone is not evidence for standalone `V_CT`. Only solve CT coefficients when both a reviewed CT ansatz and reviewed CT conditions are present.",
            "- When reviewed CT conditions contain derivatives of `V_CW`, and the paper states a Goldstone IR/on-shell/omission prescription for divergent zero-temperature Goldstone terms, use that prescription inside those `V_CW` derivatives.",
            "- Treat Goldstone handling at the derivative/prescription level specified by the paper. Derive Goldstone masses/eigenvalues, degeneracies, regulator, and derivative contributions from this model's reviewed mass matrices; do not copy a 2HDM/xSM-specific CT coefficient matrix or hard-coded Goldstone formula.",
            "- If the reviewed prescription says Goldstone modes are removed/omitted from `V_CW` derivatives and replaced by regulated Goldstone Hessian terms, implement that as a true replacement: finite-difference a non-Goldstone `V_CW` source, then add the regulated Goldstone term exactly once. Do not finite-difference the full `V_CW` including Goldstones and then add the replacement term.",
            "- Count reviewed CT coefficients and independent reviewed RG/renormalization conditions first. Compile CT solving code only when the counts match.",
            "- Solve CT coefficients in `init()` as linear algebra: build `A @ delta = b` from reviewed CT basis and condition/operator specs, then use `np.linalg.solve(A, b)`.",
            "- Scalarize single-point `V_CW` derivative samples with `float(np.asarray(value, dtype=float).reshape(-1)[0])`; these samples belong on the numeric right-hand side `b`.",
            "- Do not use optimizer/root/least-squares style solvers for CT coefficients. If the linear system is not square or is singular, raise a review question instead of fitting a solution.",
            "- Keep the CosmoTransitions public one-loop entrypoint as `V1(self, bosons, fermions)`. Optional `VCW`/`VCT` helpers may be used, but do not change `V1` into `V1(self, X)`. If a reviewed standalone `VCT(self, X)` field polynomial is needed, add it as a named Vtot term.",
            "",
        ]
    )
    candidates = counterterm.get("zero_temp_loop_candidates")
    if isinstance(candidates, list) and candidates:
        lines.extend(["### Zero-temperature loop candidates touching CT/CW", ""])
        for item in candidates[:5]:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"#### {item.get('formula_id', '')}",
                    "",
                    f"- Source: `{item.get('source_id', '')}`",
                    f"- Evidence: `{item.get('evidence_level', '')}`, confidence={float(item.get('confidence', 0.0)):.2f}",
                    "",
                    "$$",
                    wrap_latex_alignment_if_needed(str(item.get("latex", ""))),
                    "$$",
                    "",
                ]
            )
    return lines


def _ct_linear_system_notes_markdown(linear: dict[str, object]) -> list[str]:
    lines = [
        "### CT linear-system notes",
        "",
        f"- Status: `{linear.get('status', 'unknown')}`",
        f"- Guidance: {linear.get('message', '')}",
        f"- Coefficient count: `{linear.get('coefficient_count', 0)}`",
        f"- Independent condition count: `{linear.get('condition_count', 0)}`",
        f"- Counts match: `{linear.get('counts_match', False)}`",
        f"- Construction rule: `{linear.get('construction_rule', '')}`",
        "",
        "#### CT coefficient / basis candidates",
        "",
        r"| CT coefficient | Basis function/operator \(O_i(\Phi)\) | Source |",
        "|---|---|---|",
    ]
    basis = linear.get("ct_basis_candidates")
    if isinstance(basis, list) and basis:
        for item in basis:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(item.get("coefficient", "")),
                        _table_cell(item.get("basis_function", "")),
                        _table_cell(item.get("source", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| needs_review | needs_review | no_countable_basis |")
    lines.extend(
        [
            "",
            "#### Independent RG / renormalization condition candidates",
            "",
            "| Condition | Operator hint | Quantity determined | Source |",
            "|---|---|---|---|",
        ]
    )
    conditions = linear.get("condition_candidates")
    if isinstance(conditions, list) and conditions:
        for item in conditions:
            if not isinstance(item, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(item.get("condition", "")),
                        _table_cell(item.get("operator_hint", "")),
                        _table_cell(item.get("quantity_determined", "")),
                        _table_cell(item.get("source", "")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| needs_review | needs_review | needs_review | no_countable_conditions |")
    lines.extend(
        [
            "",
            "Compile requirement: do not hand-fit CT coefficients. Expose reviewed CT basis and "
            "condition/operator specs (variable names may vary), fill `A[a, i] = L_a[O_i](vacuum)` and "
            "`b[a] = -L_a[V_CW](vacuum)`, then solve with `np.linalg.solve(A, b)` only when the count/rank checks pass.",
            "",
        ]
    )
    return lines


def _zero_temp_loop_notes_markdown(notes_payload: dict[str, object]) -> list[str]:
    lines = [
        "",
        "## Zero-Temperature One-Loop Convention",
        "",
        "| Item | Content |",
        "|---|---|",
        f"| Representation | `{notes_payload.get('representation', 'unknown')}` |",
        f"| Counterterm status | `{notes_payload.get('counterterm_status', 'unclear')}` |",
        f"| Renormalization scheme | {_table_cell(notes_payload.get('renormalization_scheme', ''))} |",
        f"| Renormalization scale | {_table_cell(notes_payload.get('renormalization_scale', ''))} |",
        f"| Goldstone prescription | `{notes_payload.get('goldstone_prescription', 'unclear')}` |",
        f"| Goldstone applies to | `{notes_payload.get('goldstone_applies_to', 'unclear')}` |",
        f"| Guidance | {_table_cell(notes_payload.get('message', ''))} |",
        "",
    ]
    notes = str(notes_payload.get("goldstone_notes", "") or "").strip()
    if notes:
        lines.extend(
            [
                "### Reviewed Goldstone notes",
                "",
                "```markdown",
                notes,
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "Rules for filling the contract:",
            "- If `counterterm_status=explicit_ansatz`, solve CT coefficients only from the reviewed ansatz and conditions.",
            "- If `counterterm_status=implicit_in_V1`, implement the reviewed combined/renormalized V1 and do not add a separate V_CT.",
            "- If `representation=CW_only_with_prescription`, implement the CW expression together with the reviewed prescription; do not invent CT coefficients.",
            "- A reviewed Goldstone prescription does not by itself create a standalone V_CT. CT code is allowed only when the reviewed card has a CT ansatz and conditions.",
            "- If Goldstone modes enter V_CW, apply the reviewed Goldstone prescription only in the reviewed zero-temperature one-loop places, and to CT-equation derivatives of V_CW only when explicit CT conditions require those derivatives.",
            "- Derive any Goldstone derivative terms from the reviewed model-specific Goldstone mass eigenvalues and degeneracies. Do not reuse another model's CT coefficient matrix or Goldstone formula.",
            "- Keep vacuum mass matrices, physical-mass relations, and field-dependent mass matrices separate. Do not reuse a vacuum matrix's common factor as a base term in a field-dependent matrix unless the field-dependent source formula explicitly has that structure.",
            "",
        ]
    )
    return lines


def render_potential_material_markdown(
    material: PotentialExtractionMaterial,
) -> str:
    lines: list[str] = [
        f"# Potential Extraction Material: {material.paper_id}",
        "",
        "This is an evidence-first physics reading pack for filling the contract template.",
        "It is not executable code. Only the reviewed Markdown contract worksheet is compiled.",
        "",
        "## Extraction Status",
        "",
        "| Sector | Top formula | Candidates | Status |",
        "|---|---|---:|---|",
    ]
    for sector in CORE_FORMULA_SECTORS:
        formulas = material.sector_formulas.get(sector, [])
        top = formulas[0].formula_id if formulas else ""
        status = "candidate found" if formulas else "missing / needs review"
        lines.append(f"| {sector} | {top or 'None'} | {len(formulas)} | {status} |")

    guidance = material.implementation_guidance or _build_implementation_guidance(material.implementation_mode, None)
    lines.extend(
        [
            "",
            "## Working Potential Decision",
            "",
            "| Item | Content |",
            "|---|---|",
            f"| Working potential mode | `{material.implementation_mode}` |",
            f"| Required `Vtot` | {_table_cell(guidance.get('require_vtot', True))} |",
            f"| Must `Vtot` call `boson_massSq` / `fermion_massSq` | {_table_cell(guidance.get('require_mass_interface_in_vtot', 'review'))} |",
            f"| Must use standard thermal integrals | {_table_cell(guidance.get('require_v1t', 'review'))} |",
            f"| `Vtot` strategy | {_table_cell(guidance.get('vtot_strategy', ''))} |",
            f"| Mass-interface guidance | {_table_cell(guidance.get('mass_interface', ''))} |",
            "",
            "Template rule: the contract must define an explicit final `Vtot` route. "
            "Only `full_one_loop` requires `Vtot` to call the native mass-spectrum interface and standard thermal integrals. "
            "For simplified or high-temperature potentials, the reviewed final `Vtot` should be filled directly and no "
            "full mass spectrum should be invented just to satisfy a complete one-loop template.",
            "",
        ]
    )

    if material.compile_plan:
        lines.extend(
            [
                "## Compile Plan JSON",
                "",
                "This machine-readable summary helps review the contract before compilation. Unresolved items should "
                "be filled in the Markdown contract worksheet, not patched in generated Python.",
                "",
                "```json",
                json.dumps(material.compile_plan, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    graph = material.canonical_parameter_graph or {}
    if graph:
        lines.extend(
            [
                "## Canonical Parameter Graph",
                "",
                "This section summarizes the intended public input basis and the order in which constraints and "
                "derived parameters are applied.",
                "",
                "Role order: public inputs -> branch/convention constraints -> calibration conditions -> derived parameters -> formula closure.",
                "",
                "Public inputs are runtime numeric/scan parameters only. Discrete model branches or conventions "
                "such as Type I/Type II Yukawa sector, gauge choice, alignment choice, renormalization scheme, "
                "and fixed SM constants are not `build_model` inputs unless the user explicitly promotes them. "
                "Record such choices in branch/convention constraints, code comments, or MODEL_METADATA.",
                "",
                "### Public Inputs",
                "",
                "| LaTeX symbol | Program symbol | Value/range | Notes |",
                "|---|---|---|---|",
            ]
        )
        inputs = graph.get("public_inputs") or []
        if inputs:
            for row in inputs:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _table_cell(row.get("latex_symbol", "")),
                            _table_cell(row.get("program_symbol", "")),
                            _table_cell(row.get("value_or_range", "")),
                            _table_cell(row.get("notes", "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| None | None | None | No reviewed public input basis. |")
        lines.extend(
            [
                "",
                "### Fixed Parameters / Constants",
                "",
                "These values are available to generated code as constants or internal defaults, but they are not "
                "`build_model` public inputs unless the user explicitly promotes them.",
                "",
                "| LaTeX symbol | Program symbol | Value/range | Default | Source type | Notes |",
                "|---|---|---|---:|---|---|",
            ]
        )
        fixed = graph.get("fixed_parameters") or []
        if fixed:
            for row in fixed:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _table_cell(row.get("latex_symbol", "")),
                            _table_cell(row.get("program_symbol", "")),
                            _table_cell(row.get("value_or_range", "")),
                            _table_cell(row.get("default_value", "")),
                            _table_cell(row.get("source_type", "")),
                            _table_cell(row.get("notes", "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| None | None | None |  | None | No fixed constants extracted. |")
        lines.extend(["", "### Branch / Convention Constraints", "", "| Constraint | Applies before | Notes |", "|---|---|---|"])
        constraints = graph.get("branch_constraints") or []
        if constraints:
            for row in constraints:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _table_cell(row.get("constraint", "")),
                            _table_cell(row.get("applies_before", "")),
                            _table_cell(row.get("notes", "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| None | parameter closure | No reviewed branch constraint. |")
        lines.extend(["", "### Calibration Conditions", "", "| Condition | Solve for | Notes |", "|---|---|---|"])
        conditions = graph.get("calibration_conditions") or []
        if conditions:
            for row in conditions:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _table_cell(row.get("condition", "")),
                            _table_cell(row.get("solve_for", "")),
                            _table_cell(row.get("notes", "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| None | None | No reviewed calibration condition. |")
        lines.extend(["", "### Derived Parameters", "", "| LaTeX symbol | Program symbol | Expression | Notes |", "|---|---|---|---|"])
        derived = graph.get("derived_parameters") or []
        if derived:
            for row in derived:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _table_cell(row.get("latex_symbol", "")),
                            _table_cell(row.get("program_symbol", "")),
                            _table_cell(row.get("expression", "")),
                            _table_cell(row.get("notes", "")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("| None | None | None | No reviewed derived parameter expressions. |")
        lines.append("")

    if material.mass_blocks:
        lines.extend(
            [
                "## Mass Implementation Blocks",
                "",
                "This section is canonical for code generation. It assigns each reviewed mass formula a usage role. "
                "Do not use a `vacuum_mass_relation` block as a field-dependent mass in CW, VT, or Daisy. "
                "For `field_dependent_mass` blocks, implement the listed route for the listed usage contexts.",
                "",
                "| Block | Particles | Background fields | Usage contexts | Source role | Implementation kind | Diagonalization | Eigenvalue source | Allowed usage | Forbidden usage | Formula / notes |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for block in material.mass_blocks:
            formula_notes = f"{block.get('formula', '')} Notes: {block.get('notes', '')}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(block.get("id", "")),
                        _table_cell(", ".join(str(item) for item in block.get("particles", []) or [])),
                        _table_cell(", ".join(str(item) for item in block.get("background_fields", []) or [])),
                        _table_cell(", ".join(str(item) for item in block.get("usage_contexts", []) or [])),
                        _table_cell(block.get("source_role", "")),
                        _table_cell(block.get("implementation_kind", "")),
                        _table_cell(block.get("diagonalization_required", "")),
                        _table_cell(block.get("eigenvalue_source", "")),
                        _table_cell(block.get("allowed_usage", "")),
                        _table_cell(block.get("forbidden_usage", "")),
                        _table_cell(formula_notes),
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.extend(["### Mass Sector Contracts", ""])
        lines.append(
            "Each contract below is canonical for code generation. Use the source evidence snippets and auxiliary "
            "definitions when writing field-dependent mass matrices; do not rely only on the compact reviewed row."
        )
        lines.append("")
        for block in material.mass_blocks:
            contract = block.get("mass_sector_contract", {}) if isinstance(block, dict) else {}
            if not isinstance(contract, dict):
                contract = {}
            lines.append(f"#### `{_table_cell(block.get('id', 'mass_block'))}`")
            lines.append("")
            lines.append(f"- Route: {_table_cell(contract.get('implementation_route', block.get('notes', '')))}")
            sectors = ", ".join(str(item) for item in contract.get("sector_labels", []) or [])
            lines.append(f"- Matrix/sector labels: {_table_cell(sectors or 'none detected')}")
            lines.append(f"- Vacuum anchor policy: {_table_cell(contract.get('vacuum_anchor_policy', ''))}")
            entries = contract.get("entry_requirements", []) or []
            if entries:
                lines.append("- Required matrix/entry assignments:")
                for row in entries[:16]:
                    if isinstance(row, dict):
                        lines.append(f"  - `{_table_cell(row.get('lhs', ''))}` = `{_table_cell(row.get('rhs', ''))}`")
            auxiliaries = contract.get("auxiliary_definitions", []) or []
            if auxiliaries:
                lines.append("- Required auxiliary definitions:")
                for row in auxiliaries[:12]:
                    if isinstance(row, dict):
                        lines.append(f"  - `{_table_cell(row.get('lhs', ''))}` = `{_table_cell(row.get('rhs', ''))}`")
            snippets = block.get("source_evidence_snippets", []) or []
            if snippets:
                lines.append("- Source evidence snippets:")
                for snippet in snippets[:3]:
                    if not isinstance(snippet, dict):
                        continue
                    source_label = snippet.get("formula_id") or snippet.get("source_id") or "source"
                    text = _truncate(_single_line(str(snippet.get("text", ""))), 900)
                    lines.append(f"  - `{_table_cell(source_label)}`: {_table_cell(text)}")
            lines.append("")

    if material.goldstone_contract:
        contract = material.goldstone_contract
        lines.extend(
            [
                "## Goldstone Contract",
                "",
                "This section is canonical for code generation whenever a Goldstone prescription is reviewed. "
                "A comment mentioning the prescription is not enough; the implementation must follow this contract.",
                "",
                "| Item | Content |",
                "|---|---|",
                f"| Prescription | `{_table_cell(contract.get('prescription', ''))}` |",
                f"| Applies to | {_table_cell(contract.get('applies_to', ''))} |",
                f"| Goldstones included in CW | {_table_cell(contract.get('included_in_CW', ''))} |",
                f"| Goldstone mass source | `{_table_cell(contract.get('mass_source', ''))}` |",
                f"| Tadpole replacement | {_table_cell(contract.get('tadpole_replacement', ''))} |",
                f"| Hessian replacement | {_table_cell(contract.get('hessian_replacement', ''))} |",
                f"| Requires non-Goldstone CT source | {_table_cell(contract.get('requires_non_goldstone_ct_source', ''))} |",
                f"| Regulator | {_table_cell(contract.get('regulator', ''))} |",
                f"| Notes | {_table_cell(contract.get('notes', ''))} |",
                "",
            ]
        )
        if str(contract.get("requires_non_goldstone_ct_source", "")).strip().lower() in {"true", "yes", "1", "required"}:
            lines.extend(
                [
                    "Implementation route required for CT equations:",
                    "- Define a clearly named non-Goldstone Coleman-Weinberg helper for CT RHS finite differences, e.g. `_VCW_non_goldstone_for_ct(X)`.",
                    "- Inside that helper, call `boson_massSq(X, 0.0)`, construct a boolean mask from `BOSON_NAMES`, and exclude labels containing `G0`, `Gpm`, or `Goldstone`.",
                    "- Call `self.V1((m2[..., mask], dof[mask], c[mask]), fermions)` or an equivalent reviewed CW helper using only the masked boson species.",
                    "- Add the reviewed regulated Goldstone Hessian/replacement term exactly once after the non-Goldstone finite-difference source. Do not finite-difference the full Goldstone-including CW sum and then add the replacement.",
                    "",
                ]
            )

    if material.daisy_implementation_decisions:
        lines.extend(
            [
                "## Daisy Thermal-Mode Implementation Decisions",
                "",
                "This section is canonical for code generation. It resolves raw section 6.2 table conflicts by "
                "choosing exactly one implementation path per resummed mode. If this section conflicts with the "
                "reviewed model-card excerpt, follow this section and record a warning.",
                "",
            ]
        )
        for item in material.daisy_implementation_decisions:
            lines.extend(
                [
                    f"### {_single_line(item.get('sector', 'unnamed mode'))}",
                    "",
                    f"- Implementation kind: `{item.get('implementation_kind', '')}`",
                    f"- Implementation source type: `{item.get('implementation_source_type', '')}`",
                    f"- Formula to implement: {_table_cell(item.get('formula_to_implement', ''))}",
                    f"- Matrix evidence role: {_table_cell(item.get('matrix_evidence_role', ''))}",
                    f"- Matrix evidence: {_table_cell(item.get('matrix_evidence', ''))}",
                    f"- Code diagonalization rule: {_table_cell(item.get('code_diagonalization_rule', ''))}",
                    f"- Branch rule: {_table_cell(item.get('branch_rule', ''))}",
                    f"- Conflict resolution: {_table_cell(item.get('conflict_resolution', ''))}",
                    "",
                ]
            )

    if material.compile_defaults:
        lines.extend(
            [
                "## Compile Defaults",
                "",
                "These are the explicit keyword defaults that `build_model()` should use. Prefer benchmark points "
                "explicitly reported in the paper. If the paper only gives a scan range, PTagent chooses the range "
                "midpoint for smoke testing; this is not a paper benchmark unless the source says so.",
                "",
                "| Parameter | Default for `build_model` | Source type | Original value/range | Notes |",
                "|---|---:|---|---|---|",
            ]
        )
        for row in material.compile_defaults:
            default = row.get("default_value")
            default_text = "None" if default is None else str(default)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(row.get("program_symbol", "")),
                        _table_cell(default_text),
                        _table_cell(row.get("source_type", "")),
                        _table_cell(row.get("original_value_or_range", "")),
                        _table_cell(row.get("notes", "")),
                    ]
                )
                + " |"
            )

    if material.gaps:
        lines.extend(["", "## Blocking Or Risky Gaps", ""])
        for gap in material.gaps:
            lines.append(f"- **{gap.key}** ({gap.severity}): {gap.message} Action: {gap.suggested_action}")

    lines.extend(_zero_temp_loop_notes_markdown(material.zero_temp_loop_notes))
    lines.extend(_counterterm_notes_markdown(material.counterterm_notes))

    if material.formula_selection:
        lines.extend(
            [
                "",
                "## Formula Selection",
                "",
                "This section is the compile-template allow-list for formulas. Accepted rows may be used in the contract; "
                "PDF/OCR projections and short fragments are kept as context only and are not listed as hard "
                "overrides.",
                "",
                "| Sector | Formula | Role | Source quality | Status | Notes |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in material.formula_selection:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(row.get("sector", "")),
                        _table_cell(row.get("formula_id", "")),
                        _table_cell(row.get("role", "")),
                        _table_cell(row.get("source_quality", "")),
                        _table_cell(row.get("status", "")),
                        _table_cell(row.get("notes", "")),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Formula Candidates By Sector",
            "",
            "Rules for filling the contract:",
            "- Treat accepted `source_exact` formula-selection rows as canonical formulas copied from the source.",
            "- The reviewed model card is an interpretation layer; it may label or explain source formulas, but it must not rewrite `source_exact` formulas.",
            "- If the reviewed model card or ModelIR conflicts with an accepted same-LHS `source_exact` formula, use the accepted source formula and record the demotion as a warning.",
            "- Block compilation when accepted source-exact formulas conflict with each other, a formula cannot be mapped to a contract object, or required branch/normalization information is unresolved.",
            "- Do not silently infer convention mappings, mass normalizations, or Daisy counting.",
            "- Before promoting a formula into the contract, read its surrounding prose and classify its role. A formula can be a definition, a partial correction, a final mass/eigenvalue, or a phenomenology-only relation; only final field-dependent compiled objects belong in mass and Daisy rows.",
            "- Do not mix formula roles: a vacuum mass matrix or physical-mass relation is not a field-dependent CW/V_T/Daisy mass matrix. Implement source-exact field-dependent mass entries directly; only use vacuum formulas for vacuum checks, parameter relations, or physical-mass inversion.",
            "- For Daisy/Ring resummation, explicitly check appendix sections as well as the main text. "
            "Thermal masses, Debye masses, self-energies, longitudinal/transverse polarization rules, "
            "mixed gauge-basis matrices, and resummed eigenvalues are often given only in appendices.",
            "- Add derivation notes for thermal-mass blocks. State whether the source gives the final total mass/eigenvalue directly, or whether the reviewed expression assembles zero-temperature masses, baseline/SM self-energies, and BSM increments.",
            "- Treat `Delta Pi`, `additional contribution`, and `extra scalar/doublet contribution` formulas as partial thermal self-energies. "
            "They cannot become Daisy `thermal_mass_sq` rows until they are added to the baseline self-energies and the final mass matrix/eigenvalues are written.",
            "- Do not use a universal or standard Debye matrix as hard evidence. Thermal self-energies and resummed "
            "eigenvalues are model-dependent and must be paper-explicit, user-supplied, or user-approved derivations.",
            "",
        ]
    )
    if material.source_exact_overrides:
        lines.extend(["### Accepted Source-Exact Overrides", ""])
        lines.append(
            "These same-LHS conflicts were detected after filtering out PDF/OCR projections and formula fragments. "
            "For code generation, the accepted source-exact RHS overrides the reviewed model-card / ModelIR RHS."
        )
        lines.append("")
        shown_overrides = material.source_exact_overrides[:60]
        for item in shown_overrides:
            lines.extend(
                [
                    f"- `{item.get('lhs', '')}` in sector `{item.get('sector', '')}`: use `{item.get('formula_id', '')}` from `{item.get('source_id', '')}`.",
                    f"  Source-exact RHS: `{_single_line(item.get('source_rhs', ''))[:260]}`",
                    f"  Demoted reviewed RHS: `{_single_line(item.get('reviewed_rhs', ''))[:260]}`",
                    "",
                ]
            )
        omitted = len(material.source_exact_overrides) - len(shown_overrides)
        if omitted > 0:
            lines.append(f"- {omitted} lower-priority source-exact override(s) omitted from this rendered excerpt.")
            lines.append("")
    else:
        lines.extend(["### Accepted Source-Exact Overrides", "", "No trusted same-LHS source-exact conflicts were detected.", ""])
    for sector in CORE_FORMULA_SECTORS:
        formulas = material.sector_formulas.get(sector, [])
        lines.extend([f"### {sector}", ""])
        if not formulas:
            lines.extend(["No reliable candidate was found.", ""])
            continue
        for formula in formulas:
            eq = f" Eq. ({formula.equation_number})" if formula.equation_number else ""
            parsers = ", ".join(formula.parser_sources)
            lines.extend(
                [
                    f"#### {formula.formula_id}{eq}",
                    "",
                    f"- Source: `{formula.source_id}`",
                    f"- Evidence: `{formula.evidence_level}`, confidence={formula.confidence:.2f}, sector_score={formula.score:.2f}",
                    f"- Parsers: `{parsers or 'unknown'}`",
                    f"- Reason: {formula.reason or 'semantic match'}",
                    "",
                    "$$",
                    wrap_latex_alignment_if_needed(formula.latex),
                    "$$",
                    "",
                ]
            )
            if formula.context:
                lines.extend(["Context:", "", f"> {_single_line(formula.context[:900])}", ""])

    lines.extend(["## Symbol And Field Candidates", ""])
    for item in material.symbol_candidates[:80]:
        sources = ", ".join(str(source) for source in item.get("source_ids", [])[:4])
        lines.append(
            f"- `{item.get('symbol', '')}` role=`{item.get('role', '')}` sources=`{sources}`"
        )

    lines.extend(
        [
            "",
            "## Reviewed Model Card Excerpt",
            "",
            "```markdown",
            material.reviewed_model_card_excerpt or "No reviewed model card excerpt is available.",
            "```",
            "",
            "## Source Context",
            "",
            "```text",
            material.source_context,
            "```",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _build_source_exact_overrides(
    reviewed_text: str,
    sector_formulas: dict[str, list[MaterialFormula]],
    formula_registry: list[FormulaRecord],
    source_spans: list[SourceSpan],
    paper_markdown: str,
) -> list[dict[str, str]]:
    if not reviewed_text.strip():
        return []
    macro_aliases = _latex_macro_aliases_for_override_scan(
        reviewed_text,
        formula_registry,
        source_spans,
        paper_markdown,
    )
    reviewed_by_lhs: dict[str, list[dict[str, str]]] = {}
    for assignment in _extract_formula_assignments(reviewed_text):
        lhs_key = _normalize_formula_token(assignment["lhs"], macro_aliases)
        rhs_key = _normalize_formula_token(assignment["rhs"], macro_aliases)
        if not lhs_key or not rhs_key:
            continue
        if _formula_assignment_is_fragment(assignment["lhs"], assignment["rhs"], lhs_key, rhs_key):
            continue
        reviewed_by_lhs.setdefault(lhs_key, []).append(
            {
                "lhs": assignment["lhs"],
                "rhs": assignment["rhs"],
                "rhs_key": rhs_key,
            }
        )

    overrides: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    source_exact_formulas = _source_exact_formulas_for_override_scan(sector_formulas, formula_registry)
    source_exact_formulas.extend(_source_exact_span_formulas_for_override_scan(source_spans, reviewed_by_lhs, macro_aliases))
    for formula in source_exact_formulas:
        for assignment in _extract_formula_assignments(formula.latex):
            lhs_key = _normalize_formula_token(assignment["lhs"], macro_aliases)
            rhs_key = _normalize_formula_token(assignment["rhs"], macro_aliases)
            if not lhs_key or not rhs_key:
                continue
            if _formula_assignment_is_fragment(assignment["lhs"], assignment["rhs"], lhs_key, rhs_key):
                continue
            for reviewed in reviewed_by_lhs.get(lhs_key, []):
                if reviewed["rhs_key"] == rhs_key:
                    continue
                key = (formula.sector, lhs_key, rhs_key, reviewed["rhs_key"])
                if key in seen:
                    continue
                seen.add(key)
                overrides.append(
                    {
                        "sector": formula.sector,
                        "lhs": assignment["lhs"].strip(),
                        "source_rhs": assignment["rhs"].strip(),
                        "reviewed_rhs": reviewed["rhs"].strip(),
                        "formula_id": formula.formula_id,
                        "source_id": formula.source_id,
                        "action": "use_source_exact_formula",
                    }
                )
    overrides.sort(key=_source_exact_override_preference, reverse=True)
    return overrides


def _source_exact_override_preference(item: dict[str, str]) -> tuple[int, int, int, int, int]:
    formula_id = item.get("formula_id", "")
    sector = item.get("sector", "")
    probe = _normalize_probe(" ".join([item.get("lhs", ""), item.get("source_rhs", ""), item.get("reviewed_rhs", "")]))
    physics_key = any(
        token in probe
        for token in (
            "theta",
            "mass",
            "m_",
            "mz",
            "gamma",
            "photon",
            "daisy",
            "debye",
            "longitudinal",
            "thermal",
        )
    )
    return (
        1 if formula_id.startswith("source_span:") else 0,
        1 if sector in {"mass_spectrum", "daisy_resummation", "thermal_potential"} else 0,
        1 if physics_key else 0,
        -len(item.get("lhs", "")),
        -len(item.get("source_rhs", "")),
    )


def _build_formula_selection(
    sector_formulas: dict[str, list[MaterialFormula]],
    source_exact_overrides: list[dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    override_ids = {item.get("formula_id", "") for item in source_exact_overrides}
    for sector in CORE_FORMULA_SECTORS:
        formulas = sector_formulas.get(sector, [])
        if not formulas:
            continue
        formula = formulas[0]
        source_quality = _formula_source_quality(formula)
        status = "candidate_needs_review" if source_quality == "source_exact_candidate_needs_review" else "accepted"
        rows.append(
            {
                "sector": sector,
                "formula_id": formula.formula_id,
                "role": _formula_role_for_sector(sector),
                "source_quality": source_quality,
                "status": status,
                "notes": formula.reason or "top-ranked formula candidate",
            }
        )
    for item in source_exact_overrides:
        formula_id = item.get("formula_id", "")
        if formula_id in override_ids:
            rows.append(
                {
                    "sector": item.get("sector", ""),
                    "formula_id": formula_id,
                    "role": "same_LHS_conflict_resolution",
                    "source_quality": "trusted_source_exact",
                    "status": "accepted_override",
                    "notes": f"{item.get('lhs', '')}: reviewed RHS demoted because trusted source RHS differs",
                }
            )
            override_ids.discard(formula_id)
    return rows


def _formula_role_for_sector(sector: str) -> str:
    roles = {
        "working_potential": "working_potential_definition",
        "tree_potential": "tree_level_potential",
        "zero_temp_loop": "zero_temperature_loop_term",
        "thermal_potential": "finite_temperature_integral_or_high_T_term",
        "daisy_resummation": "thermal_resummation_or_explicit_daisy_term",
        "mass_spectrum": "field_dependent_mass_or_mass_relation",
        "parameter_relations": "parameter_closure_relation",
    }
    return roles.get(sector, "formula_candidate")


def _formula_source_quality(formula: MaterialFormula) -> str:
    if formula.evidence_level == "source_exact" and not _parser_sources_are_projected_or_pdf(formula.parser_sources):
        return "trusted_source_exact"
    if formula.evidence_level == "source_exact":
        return "source_exact_candidate_needs_review"
    return formula.evidence_level or "source_inferred"


def _source_exact_formulas_for_override_scan(
    sector_formulas: dict[str, list[MaterialFormula]],
    formula_registry: list[FormulaRecord],
) -> list[MaterialFormula]:
    scanned: dict[tuple[str, str], MaterialFormula] = {}
    for sector, formulas in sector_formulas.items():
        for formula in formulas:
            if formula.evidence_level == "source_exact":
                if _parser_sources_are_projected_or_pdf(formula.parser_sources):
                    continue
                scanned[(sector, formula.formula_id)] = formula
    for formula in formula_registry:
        if formula.evidence_level != "source_exact":
            continue
        if _formula_record_is_projected_or_pdf(formula):
            continue
        sector_hits: list[tuple[str, float, str]] = []
        for sector in CORE_FORMULA_SECTORS:
            score, reason = _sector_score(formula, sector)
            if score > 0:
                sector_hits.append((sector, score, reason))
        if not sector_hits:
            sector_hits = [
                (_fallback_sector_for_formula_kind(formula.kind), formula.confidence, "source_exact fallback")
            ]
        for sector, score, reason in sector_hits:
            key = (sector, formula.formula_id)
            candidate = MaterialFormula(
                formula_id=formula.formula_id,
                sector=sector,
                latex=_formula_text_for_override_scan(formula),
                source_id=formula.source_id,
                equation_number=formula.equation_number,
                evidence_level=formula.evidence_level,
                confidence=formula.confidence,
                score=round(score, 3),
                parser_sources=formula.parser_sources,
                context=formula.context,
                reason=reason,
            )
            previous = scanned.get(key)
            if previous is None or _material_formula_preference(candidate) > _material_formula_preference(previous):
                scanned[key] = candidate
    return list(scanned.values())


def _source_exact_span_formulas_for_override_scan(
    source_spans: list[SourceSpan],
    reviewed_by_lhs: dict[str, list[dict[str, str]]],
    macro_aliases: dict[str, str],
) -> list[MaterialFormula]:
    if not reviewed_by_lhs:
        return []
    formulas: list[MaterialFormula] = []
    for span in source_spans:
        if _span_is_projected_or_pdf(span):
            continue
        text = span.text or ""
        if "=" not in text:
            continue
        assignments = _extract_formula_assignments(text)
        if not any(_normalize_formula_token(item["lhs"], macro_aliases) in reviewed_by_lhs for item in assignments):
            continue
        sector = _sector_for_source_span_text(text)
        formulas.append(
            MaterialFormula(
                formula_id=f"source_span:{span.source_id}",
                sector=sector,
                latex=text,
                source_id=span.source_id,
                evidence_level="source_exact",
                confidence=max(0.0, float(span.score or 0.0)),
                score=max(0.0, float(span.score or 0.0)),
                parser_sources=[span.parser] if span.parser else [],
                context=span.heading,
                reason="same-LHS source span override scan",
            )
        )
    return formulas


def _latex_macro_aliases_for_override_scan(
    reviewed_text: str,
    formula_registry: list[FormulaRecord],
    source_spans: list[SourceSpan],
    paper_markdown: str,
) -> dict[str, str]:
    texts: list[str] = [paper_markdown, reviewed_text]
    for formula in formula_registry:
        texts.extend([formula.latex, formula.raw_text, formula.context])
    texts.extend(span.text for span in source_spans)
    return _extract_latex_no_arg_macros(texts)


def _extract_latex_no_arg_macros(texts: Iterable[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for text in texts:
        if not text:
            continue
        for name, replacement in _iter_latex_no_arg_macros(text):
            if name and replacement and "#" not in replacement:
                aliases[name] = replacement
    return _resolve_latex_macro_aliases(aliases)


def _iter_latex_no_arg_macros(text: str) -> Iterable[tuple[str, str]]:
    for match in re.finditer(r"\\def\s*\\([A-Za-z]+)", text):
        name = match.group(1)
        pos = _skip_ws(text, match.end())
        if pos < len(text) and text[pos] == "#":
            continue
        replacement, _ = _read_braced_group(text, pos)
        if replacement:
            yield name, replacement

    command_pattern = re.compile(
        r"\\(?:re)?newcommand\*?\s*(?:\{\\([A-Za-z]+)\}|\\([A-Za-z]+))"
    )
    for match in command_pattern.finditer(text):
        name = match.group(1) or match.group(2) or ""
        pos = _skip_ws(text, match.end())
        arg_count = 0
        if pos < len(text) and text[pos] == "[":
            option, pos = _read_square_group(text, pos)
            arg_count = int(option.strip()) if option.strip().isdigit() else 1
        if arg_count:
            continue
        replacement, _ = _read_braced_group(text, pos)
        if replacement:
            yield name, replacement

    operator_pattern = re.compile(
        r"\\DeclareMathOperator\*?\s*(?:\{\\([A-Za-z]+)\}|\\([A-Za-z]+))"
    )
    for match in operator_pattern.finditer(text):
        name = match.group(1) or match.group(2) or ""
        replacement, _ = _read_braced_group(text, match.end())
        if replacement:
            yield name, replacement


def _resolve_latex_macro_aliases(aliases: dict[str, str]) -> dict[str, str]:
    resolved = dict(aliases)
    for _ in range(8):
        changed = False
        for name, replacement in list(resolved.items()):
            expanded = _expand_latex_macros(replacement, resolved, skip_name=name)
            if expanded != replacement:
                resolved[name] = expanded
                changed = True
        if not changed:
            break
    return resolved


def _expand_latex_macros(value: str, aliases: dict[str, str], *, skip_name: str = "") -> str:
    expanded = value
    for _ in range(8):
        previous = expanded
        for name in sorted(aliases, key=len, reverse=True):
            if name == skip_name:
                continue
            pattern = rf"\\{re.escape(name)}(?=[^A-Za-z]|$)"
            expanded = re.sub(pattern, lambda _match, replacement=aliases[name]: replacement, expanded)
        if expanded == previous:
            break
    return expanded


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _read_braced_group(text: str, pos: int) -> tuple[str, int]:
    return _read_delimited_group(text, pos, "{", "}")


def _read_square_group(text: str, pos: int) -> tuple[str, int]:
    return _read_delimited_group(text, pos, "[", "]")


def _read_delimited_group(text: str, pos: int, open_char: str, close_char: str) -> tuple[str, int]:
    pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != open_char:
        return "", pos
    depth = 0
    start = pos + 1
    escaped = False
    for index in range(pos, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
    return "", pos


def _formula_record_is_projected_or_pdf(formula: FormulaRecord) -> bool:
    return _parser_sources_are_projected_or_pdf(formula.parser_sources)


def _parser_sources_are_projected_or_pdf(parser_sources: Iterable[str]) -> bool:
    parsers = [(parser or "").strip().lower() for parser in parser_sources if (parser or "").strip()]
    return bool(parsers) and all(parser.startswith(("pdf2md", "paper_md", "pypdf")) for parser in parsers)


def _span_is_projected_or_pdf(span: SourceSpan) -> bool:
    parser = (span.parser or "").strip().lower()
    return parser.startswith(("pdf2md", "paper_md", "pypdf"))


def _sector_for_source_span_text(text: str) -> str:
    probe = _normalize_probe(text)
    if any(token in probe for token in ("theta", "massmatrix", "massmatrix", "fielddependent", "eigenvalue")):
        return "mass_spectrum"
    if any(token in probe for token in ("daisy", "ring", "debye", "selfenergy", "selfenergy", "longitudinal")):
        return "daisy_resummation"
    if any(token in probe for token in ("vcw", "coleman", "counterterm", "renormalization")):
        return "zero_temp_loop"
    if any(token in probe for token in ("v0", "treelevel", "scalarpotential")):
        return "tree_potential"
    if any(token in probe for token in ("veff", "thermal", "v1t", "j_b", "j_f")):
        return "working_potential"
    return "unclassified"


def _formula_text_for_override_scan(formula: FormulaRecord) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in (formula.latex, formula.raw_text, formula.context):
        text = (value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return "\n".join(parts)


def _fallback_sector_for_formula_kind(kind: str) -> str:
    normalized = (kind or "").strip().lower()
    if normalized == "mass":
        return "mass_spectrum"
    if normalized == "thermal":
        return "thermal_potential"
    if normalized == "daisy":
        return "daisy_resummation"
    if normalized == "zero_temp_loop":
        return "zero_temp_loop"
    if normalized == "tree_potential":
        return "tree_potential"
    if normalized == "effective_potential":
        return "working_potential"
    return "unclassified"


def _extract_formula_assignments(text: str) -> list[dict[str, str]]:
    assignments: list[dict[str, str]] = []
    if not text:
        return assignments
    chunks = re.split(r"(?:\\\\|\n|;)", text)
    for chunk in chunks:
        for segment in chunk.split("|"):
            for piece in _split_assignment_pieces(segment):
                cleaned = _clean_formula_assignment_segment(piece)
                if "=" not in cleaned:
                    continue
                lhs, rhs = cleaned.split("=", 1)
                lhs = _trim_formula_lhs(lhs)
                rhs = _trim_formula_rhs(rhs)
                if lhs and rhs:
                    assignments.append({"lhs": lhs, "rhs": rhs})
    return assignments


def _split_assignment_pieces(segment: str) -> list[str]:
    return [
        item
        for item in re.split(
            r",\s*(?=(?:\$|\\\()?\s*(?:\\quad\s*)?&?\s*(?:\\?[A-Za-z]+|[A-Za-z])[^=]{0,120}=)",
            segment,
        )
        if item.strip()
    ]


def _clean_formula_assignment_segment(segment: str) -> str:
    cleaned = segment.strip()
    cleaned = cleaned.replace("`", "")
    cleaned = cleaned.replace(r"\equiv", "=").replace(r"\simeq", "=").replace(":=", "=")
    cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\d+\s*[.)]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"\\\[(.*?)\\\]", r"\1", cleaned)
    cleaned = re.sub(r"\\\((.*?)\\\)", r"\1", cleaned)
    cleaned = cleaned.replace("$$", "").replace("$", "")
    return cleaned.strip()


def _trim_formula_lhs(lhs: str) -> str:
    lhs = re.split(r"[:\uFF1A]", lhs)[-1]
    lhs = lhs.strip()
    lhs = re.sub(r"^(?:\\quad\s*)?&\s*", "", lhs)
    lhs = re.sub(r"^(?:\\quad|\\;|\\,|\s)+", "", lhs)
    lhs = re.sub(r"^[\[\(\s]+", "", lhs)
    return lhs.strip()


def _trim_formula_rhs(rhs: str) -> str:
    rhs = re.split(r"\b(?:AI infer|Pending|verify from paper text|approval|notes?)\b", rhs, maxsplit=1, flags=re.IGNORECASE)[0]
    rhs = rhs.strip()
    rhs = re.sub(r"[\]\)\.,\uFF0C;\uFF1B\s]+$", "", rhs)
    return rhs.strip()


def _normalize_formula_token(value: str, macro_aliases: dict[str, str] | None = None) -> str:
    normalized = _expand_latex_macros(value, macro_aliases or {}) if macro_aliases else value
    normalized = normalized.lower()
    normalized = re.sub(r"\\(?:mathrm|operatorname|text|rm)\{([^{}]*)\}", r"\1", normalized)
    normalized = re.sub(r"\\(?:left|right|big|bigg|,|;|!|quad|qquad)\b", "", normalized)
    normalized = normalized.replace("\\", "")
    normalized = normalized.replace("{", "").replace("}", "")
    normalized = normalized.replace(" ", "")
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _formula_assignment_is_fragment(lhs: str, rhs: str, lhs_key: str, rhs_key: str) -> bool:
    lhs_raw = lhs.strip()
    rhs_raw = rhs.strip()
    if not lhs_key or not rhs_key:
        return True
    if len(rhs_key) < 5:
        return True
    if len(lhs_key) < 3 and "\\" not in lhs_raw:
        return True
    if lhs_key in {"t", "h", "h1", "h2", "x", "y", "ci", "ni", "beta", "delta"}:
        return True
    if _has_unmatched_closing_delimiter(lhs_raw) or _has_unmatched_closing_delimiter(rhs_raw):
        return True
    if re.search(r"\b(?:we|where|then|thus|therefore|section|equation|table|fig)\b", rhs_raw, flags=re.IGNORECASE):
        math_markers = ("\\", "^", "_", "{", "}", "+", "-", "*", "/", "pi", "lambda")
        if not any(marker in rhs_raw.lower() for marker in math_markers):
            return True
    return False


def _has_unmatched_closing_delimiter(text: str) -> bool:
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    for open_char, close_char in pairs:
        depth = 0
        escaped = False
        for char in text:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth < 0:
                    return True
    return False


def _rank_sector_formulas(
    formulas: list[FormulaRecord],
    sector: str,
    *,
    max_per_sector: int,
) -> list[MaterialFormula]:
    by_key: dict[str, MaterialFormula] = {}
    for formula in formulas:
        score, reason = _sector_score(formula, sector)
        if score <= 0:
            continue
        key = _formula_key(formula.latex)
        candidate = MaterialFormula(
            formula_id=formula.formula_id,
            sector=sector,
            latex=formula.latex,
            source_id=formula.source_id,
            equation_number=formula.equation_number,
            evidence_level=formula.evidence_level,
            confidence=formula.confidence,
            score=round(score, 3),
            parser_sources=formula.parser_sources,
            context=formula.context,
            reason=reason,
        )
        previous = by_key.get(key)
        if previous is None or _material_formula_preference(candidate) > _material_formula_preference(previous):
            by_key[key] = candidate
    ranked = list(by_key.values())
    ranked.sort(key=_material_formula_preference, reverse=True)
    return ranked[:max_per_sector]


def _material_formula_preference(item: MaterialFormula) -> tuple[int, float, float, int]:
    return (
        1 if item.evidence_level == "source_exact" else 0,
        item.score,
        item.confidence,
        len(item.latex),
    )


def _sector_score(formula: FormulaRecord, sector: str) -> tuple[float, str]:
    latex = formula.latex or ""
    probe = _normalize_probe(" ".join([latex, formula.label, formula.context]))
    lhs = _normalize_probe(latex.split("=", 1)[0] if "=" in latex else latex[:80])
    score = formula.confidence * 3.0
    reasons: list[str] = []

    def hit(value: float, label: str) -> None:
        nonlocal score
        score += value
        reasons.append(label)

    if formula.evidence_level == "source_exact":
        hit(0.5, "source_exact")
    if formula.equation_number:
        hit(0.35, "numbered_equation")
    if any(parser.startswith("paper_md") for parser in formula.parser_sources):
        hit(0.35, "paper_markdown")

    if sector == "working_potential":
        if formula.kind == "effective_potential":
            hit(4.0, "kind=effective_potential")
        if any(token in lhs for token in ("veff", "v_eff", "vthermal", "vfinite")):
            hit(3.0, "Veff-like lhs")
        if "v0" in probe and any(token in probe for token in ("vcw", "vct", "v1t", "vring", "v_t", "thermalmass")):
            hit(5.0, "complete working potential decomposition")
        if re.search(r"\bv\s*\(", latex, flags=re.IGNORECASE) and "t" in probe and "thermal" in probe:
            hit(2.0, "thermal working potential")
        if "v0" in probe and ("thermal" in probe or "t2" in probe or "pi" in probe):
            hit(1.5, "V0 plus thermal terms")
    elif sector == "tree_potential":
        if formula.kind == "tree_potential":
            hit(4.0, "kind=tree_potential")
        if any(token in lhs for token in ("v0", "v_0", "vtree", "v_tree")):
            hit(3.5, "tree lhs")
        if "scalarpotential" in probe or "treelevelpotential" in probe or "gaugeinvariantscalarpotential" in probe:
            hit(2.4, "tree/scalar potential context")
        if re.match(r"^v(?:\(|h|s|phi|\\phi)", lhs) and any(token in probe for token in ("hdaggerh", "phi", "lambda", "quartic")):
            hit(1.5, "generic scalar V")
    elif sector == "zero_temp_loop":
        if formula.kind == "zero_temp_loop":
            hit(4.0, "kind=zero_temp_loop")
        if any(token in lhs for token in ("v1", "v_1", "vcw", "v_cw", "vct", "v_ct")):
            hit(3.0, "one-loop lhs")
        if any(token in probe for token in ("coleman", "counterterm", "64pi2", "64\\pi", "renormalization")):
            hit(2.0, "CW/counterterm context")
    elif sector == "thermal_potential":
        if formula.kind == "thermal":
            hit(4.0, "kind=thermal")
        if any(token in lhs for token in ("v1t", "v_1t", "vt", "v_t")):
            hit(3.0, "thermal lhs")
        if any(token in probe for token in ("j_b", "jb", "j_f", "jf", "t4", "thermalmass", "thermalcorrection", "high-temperature")):
            hit(2.0, "thermal context")
    elif sector == "daisy_resummation":
        if formula.kind == "daisy":
            hit(4.0, "kind=daisy")
        if any(
            token in probe
            for token in (
                "daisy",
                "ring",
                "parwani",
                "arnold",
                "espinosa",
                "debye",
                "resummation",
                "appendix",
                "thermal mass",
                "self-energy",
                "self energy",
                "longitudinal",
                "transverse",
                "resummed eigenvalue",
            )
        ):
            hit(2.5, "Daisy words")
        if "appendix" in probe and any(token in probe for token in ("thermal mass", "debye", "longitudinal", "transverse", "eigenvalue", "self-energy")):
            hit(3.0, "Appendix Daisy evidence")
        if re.search(r"\bd[_a-z]*\s*=", lhs) and ("thermal" in probe or "longitudinal" in probe):
            hit(2.0, "thermal coefficient")
        if "pi" in lhs and ("t2" in probe or "thermal" in probe):
            hit(1.5, "self-energy")
    elif sector == "mass_spectrum":
        if formula.kind == "mass":
            hit(4.0, "kind=mass")
        if any(token in lhs for token in ("m2", "m_2", "m^2", "ms", "mh", "mv", "mf", "m_s", "m_h")):
            hit(2.5, "mass lhs")
        if any(token in probe for token in ("massmatrix", "massmatrix", "fielddependentmass", "eigenvalue")):
            hit(2.0, "mass context")
        if "pmatrix" in probe or "matrix" in probe:
            hit(1.0, "matrix expression")
    elif sector == "parameter_relations":
        if any(token in probe for token in ("input", "scan", "tadpole", "minimization", "stationary", "derived", "specifiedby")):
            hit(2.0, "parameter relation context")
        if any(token in lhs for token in ("lambda", "mu", "m11", "m22", "b2", "a1", "a2")) and "=" in latex:
            hit(2.0, "parameter lhs")

    # Penalize common false positives from bounce equations or unrelated observables.
    if sector == "working_potential" and any(token in probe for token in ("vorigin", "vbroken", "criticaltemperature", "bubble", "bounce", "nucleation")):
        if not any(token in probe for token in ("v0", "vcw", "v1t", "vring", "thermalmass")):
            score -= 4.0
    if sector in {"tree_potential", "working_potential"} and any(token in probe for token in ("bounce", "nucleation", "hubble", "relicdensity")):
        score -= 1.5
    if sector == "daisy_resummation" and any(token in lhs for token in ("dr", "dphi", "dphidr")):
        score -= 4.0
    if sector != "parameter_relations" and _looks_like_observable_not_potential(probe):
        score -= 2.5

    structural_reasons = {"source_exact", "numbered_equation", "paper_markdown"}
    if not any(reason not in structural_reasons for reason in reasons):
        return 0.0, ""
    return max(0.0, score), ", ".join(reasons)


def _build_gaps(
    memory: PaperMemory,
    sector_formulas: dict[str, list[MaterialFormula]],
    ir: ModelIR | None,
    validation: ValidationReport | None,
    *,
    implementation_mode: str = "unknown",
) -> list[MaterialGap]:
    gaps: list[MaterialGap] = []
    required_sectors = ["working_potential"]
    mode = _normalize_implementation_mode(implementation_mode)
    if mode in {"full_one_loop", "tree_plus_thermal_masses", "unknown"}:
        required_sectors.append("tree_potential")
    if mode == "full_one_loop":
        required_sectors.append("mass_spectrum")
    for sector in required_sectors:
        if not sector_formulas.get(sector):
            gaps.append(
                MaterialGap(
                    key=sector,
                    severity="blocking",
                    message=f"No strong candidate was found for `{sector}`.",
                    suggested_action="Inspect source context, upload TeX/source, or fill this field manually.",
                )
            )
    if ir and ir.daisy.scheme in {"Parwani", "Arnold-Espinosa"} and not ir.daisy.thermal_coefficients:
        gaps.append(
            MaterialGap(
                key="daisy_modes",
                severity="blocking",
                message="Daisy is enabled but the thermal-mode table is empty or not machine-actionable.",
                suggested_action="Recover mode, polarization, target matrix element, coefficient, and d.o.f. from the paper.",
            )
        )
    if validation:
        for issue in validation.issues:
            if issue.severity == "error":
                gaps.append(
                    MaterialGap(
                        key=issue.field_key or issue.code,
                        severity="blocking",
                        message=issue.message,
                        suggested_action=issue.suggested_action,
                    )
                )
    if ir:
        for param in ir.input_parameters:
            symbol = (param.program_symbol or param.latex_symbol or "").strip()
            if not symbol or _looks_absent(symbol) or not _input_value_needs_user_resolution(param.value):
                continue
            gaps.append(
                MaterialGap(
                    key=f"input_default:{symbol}",
                    severity="blocking",
                    message=f"Public input `{symbol}` has no reviewed numeric default, benchmark value, or scan range.",
                    suggested_action="Resolve it in the template before compilation.",
                )
            )
    unresolved = [
        key
        for key, evidence in memory.field_evidence.items()
        if evidence.requires_user_approval and not evidence.approved
    ]
    for key in unresolved:
        gaps.append(
            MaterialGap(
                key=key,
                severity="review",
                message="Field evidence requires user approval.",
                suggested_action="Approve the inference, provide a source-backed value, or mark it None.",
            )
        )
    return _dedupe_gaps(gaps)


def _dedupe_gaps(gaps: list[MaterialGap]) -> list[MaterialGap]:
    seen: set[tuple[str, str]] = set()
    result: list[MaterialGap] = []
    for gap in gaps:
        key = (gap.key, gap.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(gap)
    return result


def _looks_like_observable_not_potential(probe: str) -> bool:
    tokens = ("yb=", "rho", "relic", "omega", "signalstrength", "crosssection", "branchingratio")
    return any(token in probe for token in tokens)


def _normalize_probe(value: str) -> str:
    value = value.lower()
    value = value.replace("\\", "")
    value = value.replace("{", "").replace("}", "")
    value = value.replace("^", "")
    return re.sub(r"[^a-z0-9_]+", "", value)


def _formula_key(value: str) -> str:
    return _normalize_probe(value)[:260]


def _strip_evidence_appendix(markdown_text: str) -> str:
    for marker in ("\n---\n\n# PTagent Evidence Appendix", "\n# PTagent Evidence Appendix"):
        if marker in markdown_text:
            return markdown_text.split(marker, 1)[0]
    return markdown_text


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n\n[... truncated ...]"


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _looks_absent(value: str) -> bool:
    probe = _normalize_probe(value)
    if probe in {"", "none", "aiinfer", "notgiven", "notprovided", "missing"} or probe.startswith("none"):
        return True
    if "=" in str(value or ""):
        rhs_probe = _normalize_probe(str(value).split("=", 1)[1])
        return rhs_probe in {"", "none", "textnone", "mathrmnone", "aiinfer", "missing", "unknown"} or rhs_probe.startswith("none")
    return False


def _looks_like_reviewed_conditions(value: str) -> bool:
    text = str(value or "")
    if _looks_absent(text):
        return False
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if "renormalization condition" in cells[1].lower():
            continue
        payload = " ".join(cells[1:3]).strip()
        if payload and payload.lower() not in {"none", "ai infer"} and re.search(r"[A-Za-z\\=]", payload):
            return True
    lowered = text.lower()
    lowered = re.sub(r"(?m)^\s*\|.*\|\s*$", "", lowered)
    for phrase in (
        "if no renormalization conditions are provided",
        "if conditions are provided",
        "typical examples include preserving",
        "write `none`",
        "zero-temperature vevs",
        "scalar masses",
        "mixing angles",
    ):
        lowered = lowered.replace(phrase, "")
    return bool(re.search(r"(partial|\\partial|tadpole|mass matrix|mixing|=|v_cw|v_ct)", lowered))


def _table_cell(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "None"
    return _single_line(text).replace("|", "/")[:1000]
