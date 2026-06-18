from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .codex_tex_parser import parse_tex_formula_records
from .formula_extractor import extract_formula_records
from .schemas import EvidenceItem, FormulaRecord, PaperMemory, RelationEdge, SourceSpan, SymbolRecord
from .source_reader import SourceDocument

FIELD_QUERIES: dict[str, list[str]] = {
    "background_fields": ["background field", "field basis", "Phi", "Higgs doublet"],
    "tree_potential": ["tree-level potential", "tree level potential", "V0", "V_0"],
    "mass_spectrum": ["mass matrix", "field-dependent mass", "mass eigenvalues"],
    "zero_temp_loop": ["Coleman-Weinberg", "counterterm", "V1", "V_CW"],
    "thermal_potential": ["finite-temperature", "thermal correction", "V_T", "V1T"],
    "daisy_scheme": [
        "Daisy",
        "ring",
        "Parwani",
        "Arnold-Espinosa",
        "thermal mass",
        "appendix thermal mass",
        "Debye",
        "longitudinal transverse",
        "self-energy",
        "resummed eigenvalues",
        "W3 B gamma",
    ],
    "effective_potential": ["effective potential", "Veff", "V_eff"],
    "parameter_closure": ["input parameters", "benchmark", "scan", "parameter relation"],
}

LATEX_COMMANDS_TO_IGNORE = {
    "frac",
    "left",
    "right",
    "begin",
    "end",
    "text",
    "rm",
    "mathrm",
    "operatorname",
    "sum",
    "log",
    "ln",
    "pi",
    "sqrt",
    "sin",
    "cos",
    "tan",
    "exp",
    "partial",
    "dagger",
    "pmatrix",
    "qquad",
    "quad",
    "bar",
    "overline",
    "cal",
    "mathcal",
}


def build_paper_memory(document: SourceDocument) -> PaperMemory:
    formulas = _formula_records_for_document(document)
    symbols = _collect_symbols(document.spans, formulas)
    evidence = _build_field_evidence(document.spans, formulas)
    retrieval_index = {field: [span.source_id for span in retrieve_spans(document.spans, query)] for field, query in FIELD_QUERIES.items()}
    graph = _build_relation_graph(formulas, symbols)
    return PaperMemory(
        paper_id=document.paper_id,
        source_path=str(document.path.resolve()),
        source_type=document.suffix,
        paper_markdown=document.paper_markdown,
        source_spans=document.spans,
        formula_registry=formulas,
        symbol_registry=symbols,
        relation_graph=graph,
        field_evidence=evidence,
        retrieval_index=retrieval_index,
        notes="Paper-level memory is evidence-bearing. Model-family conventions are not treated as hard evidence.",
    )


def _formula_records_for_document(document: SourceDocument) -> list[FormulaRecord]:
    tex_path = _tex_formula_path(document)
    if tex_path is not None:
        try:
            tex_text = tex_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return extract_formula_records(document.spans)
        return parse_tex_formula_records(
            tex_text,
            paper_id=document.paper_id,
            source_name=tex_path.stem,
        )
    return extract_formula_records(document.spans)


def _tex_formula_path(document: SourceDocument) -> Path | None:
    diagnostics = document.diagnostics or {}
    diagnostic_path = str(diagnostics.get("tex_formula_path", "")).strip()
    if diagnostic_path:
        path = Path(diagnostic_path)
        return path if path.exists() else None
    if diagnostics.get("formula_parser") == "codex_tex_parser":
        return document.path if document.path.exists() and document.path.suffix.lower() == ".tex" else None
    if document.suffix in {".tex", ".arxiv", ".arxiv-archive"} and document.path.exists() and document.path.suffix.lower() == ".tex":
        return document.path
    return None


def retrieve_spans(spans: list[SourceSpan], query_terms: list[str], limit: int = 5) -> list[SourceSpan]:
    scored: list[tuple[float, SourceSpan]] = []
    query = " ".join(query_terms)
    query_vec = _token_counter(query)
    for span in spans:
        lexical = sum(span.text.lower().count(term.lower()) for term in query_terms)
        heading_bonus = sum(span.heading.lower().count(term.lower()) for term in query_terms) * 2
        vector = _cosine(query_vec, _token_counter(span.text))
        score = lexical * 3.0 + heading_bonus + vector * 5.0 + span.score * 0.02
        scored.append((score, span))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [span for score, span in scored[:limit] if score > 0]


def build_memory_markdown(memory: PaperMemory) -> str:
    lines = [
        f"# PaperMemory: {memory.paper_id}",
        "",
        "## Field Evidence",
        "",
        "| Field | Evidence level | Status | Sources | Summary |",
        "|---|---|---|---|---|",
    ]
    for key, item in sorted(memory.field_evidence.items()):
        status = "OK" if item.is_satisfied() else "Needs approval"
        lines.append(
            f"| {key} | {item.level} | {status} | {', '.join(item.source_ids)} | {item.summary.replace('|', '/')} |"
        )
    lines.extend(["", "## Formula Registry", ""])
    for formula in memory.formula_registry:
        equation = f" Eq. ({formula.equation_number})" if formula.equation_number else ""
        parsers = f" parsers={','.join(formula.parser_sources)}" if formula.parser_sources else ""
        lines.append(
            f"- `{formula.formula_id}` [{formula.kind}]{equation} {formula.source_id}"
            f" conf={formula.confidence:.2f}{parsers}: `{_shorten(formula.latex)}`"
        )
    lines.extend(["", "## Symbol Registry", ""])
    for symbol in memory.symbol_registry:
        lines.append(f"- `{symbol.symbol}` role={symbol.role}, sources={', '.join(symbol.source_ids[:4])}")
    return "\n".join(lines)


def _collect_symbols(spans: list[SourceSpan], formulas: list[FormulaRecord]) -> list[SymbolRecord]:
    sources: dict[str, set[str]] = defaultdict(set)
    definitions: dict[str, list[str]] = defaultdict(list)
    background_candidates = _extract_background_candidates(spans)
    for formula in formulas:
        for symbol in _symbols_from_latex(formula.latex):
            sources[symbol].add(formula.source_id)
            if "=" in formula.latex:
                definitions[symbol].append(formula.formula_id)
    for span in spans:
        for symbol in _symbols_from_latex(span.text[:5000]):
            sources[symbol].add(span.source_id)

    records: list[SymbolRecord] = []
    for symbol in sorted(sources):
        records.append(
            SymbolRecord(
                symbol=symbol,
                role=_infer_symbol_role(symbol, background_candidates),
                source_ids=sorted(sources[symbol]),
                definitions=definitions.get(symbol, [])[:8],
                evidence_level="source_inferred",
            )
        )
    return records


def _extract_background_candidates(spans: list[SourceSpan]) -> set[str]:
    candidates: set[str] = set()
    joined = "\n".join(span.text for span in spans)
    normalized = _normalize_unicode_math_text(joined)
    patterns = [
        r"background\s+field(?:s| configurations)?[^.\n:;]*?(?:are|as|basis|configuration)?[^.\n]*?\(([^)]{1,80})\)",
        r"field\s+basis[^.\n]*?\(([^)]{1,80})\)",
        r"basis[^.\n]*?\(([^)]{1,80})\)",
        r"Phi\s*=\s*\(([^)]{1,80})\)",
        r"\\Phi\s*=\s*\(([^)]{1,80})\)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            candidates.update(_split_symbol_list(match.group(1)))

    for match in re.finditer(
        r"background\s+field(?:s| configurations)?\s+([A-Za-z_\\,{}\s]+?)\s+(?:have|are|is|with)",
        normalized,
        flags=re.IGNORECASE,
    ):
        candidates.update(_split_symbol_list(match.group(1)))

    return {symbol for symbol in candidates if symbol and len(symbol) <= 20}


def _split_symbol_list(value: str) -> set[str]:
    cleaned = value.replace("\\", "")
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = cleaned.replace("and", ",")
    pieces = re.split(r"[,;\s]+", cleaned)
    ignored = {"", "the", "field", "fields", "background", "basis", "configuration", "configurations"}
    return {piece.strip() for piece in pieces if piece.strip().lower() not in ignored}


def _symbols_from_latex(text: str) -> set[str]:
    symbols: set[str] = set()
    for command in re.findall(r"\\([A-Za-z]+)(?:_\{?([A-Za-z0-9,+\-]+)\}?)?", text):
        base, sub = command
        if base in LATEX_COMMANDS_TO_IGNORE:
            continue
        symbols.add(f"{base}_{sub}" if sub else base)
    for name in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:_\{?[A-Za-z0-9,+\-]+\}?)?\b", text):
        if name.lower() in {"where", "with", "from", "and", "the", "for", "none", "section"}:
            continue
        if len(name) == 1 or any(char in name for char in "_") or name in {"V0", "Veff", "Tn", "Tc"}:
            symbols.add(name.replace("{", "").replace("}", ""))
    return symbols


def _infer_symbol_role(symbol: str, background_candidates: set[str] | None = None) -> str:
    lowered = symbol.lower()
    background_candidates = background_candidates or set()
    if symbol in background_candidates or lowered in {item.lower() for item in background_candidates}:
        return "candidate_background_field"
    if lowered.startswith("v") or "vev" in lowered:
        return "vev_or_background_scale"
    if lowered.startswith("m") or "mass" in lowered:
        return "mass_or_mass_matrix"
    if "lambda" in lowered or lowered.startswith("lam"):
        return "coupling"
    if "mu" in lowered:
        return "quadratic_parameter"
    if lowered.startswith("d") or "pi" in lowered:
        return "thermal_coefficient"
    if lowered.startswith("v"):
        return "potential"
    return "unknown"


def _build_field_evidence(spans: list[SourceSpan], formulas: list[FormulaRecord]) -> dict[str, EvidenceItem]:
    evidence: dict[str, EvidenceItem] = {}
    formula_by_kind: dict[str, list[FormulaRecord]] = defaultdict(list)
    spans_by_id = {span.source_id: span for span in spans}
    for formula in formulas:
        formula_by_kind[formula.kind].append(formula)

    formula_field_map = {
        "tree_potential": "tree_potential",
        "effective_potential": "effective_potential",
        "mass": "mass_spectrum",
        "thermal": "thermal_potential",
        "zero_temp_loop": "zero_temp_loop",
        "daisy": "daisy_scheme",
    }
    for kind, field_key in formula_field_map.items():
        records = formula_by_kind.get(kind, [])
        if not records:
            continue
        source_ids = []
        summaries = []
        for record in records[:4]:
            source_ids.append(record.source_id)
            span = spans_by_id.get(record.source_id)
            summaries.append(_shorten(record.latex or (span.text if span else ""), 180))
        has_only_projected_sources = all(record.evidence_level != "source_exact" for record in records[:4])
        level = (
            "source_inferred"
            if has_only_projected_sources
            else "source_exact" if kind in {"tree_potential", "effective_potential", "daisy"} else "source_inferred"
        )
        evidence[field_key] = EvidenceItem(
            field_key=field_key,
            level=level,
            summary=" | ".join(summaries),
            source_ids=source_ids,
            requires_user_approval=False,
        )

    for field_key, query in FIELD_QUERIES.items():
        if field_key in evidence:
            continue
        matches = retrieve_spans(spans, query, limit=4)
        if matches:
            level = "source_exact" if field_key in {"tree_potential", "daisy_scheme", "effective_potential"} else "source_inferred"
            evidence[field_key] = EvidenceItem(
                field_key=field_key,
                level=level,
                summary=_shorten(matches[0].text, 220),
                source_ids=[span.source_id for span in matches],
                requires_user_approval=False,
            )
        else:
            evidence[field_key] = EvidenceItem(
                field_key=field_key,
                level="machine_inferred",
                summary="No direct source evidence found. User must approve inference or provide this field manually.",
                source_ids=[],
                requires_user_approval=True,
                approved=False,
            )
    return evidence


def _normalize_unicode_math_text(value: str) -> str:
    replacements = {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "µ": "mu",
        "λ": "lambda",
        "ϕ": "phi",
        "φ": "phi",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value


def _build_relation_graph(formulas: list[FormulaRecord], symbols: list[SymbolRecord]) -> list[RelationEdge]:
    edges: list[RelationEdge] = []
    symbol_names = {item.symbol for item in symbols}
    for formula in formulas:
        label = formula.label or formula.formula_id
        edges.append(RelationEdge(source=formula.formula_id, target=formula.kind, relation="used_in_Veff" if formula.kind != "unknown" else "appears_in"))
        for symbol in _symbols_from_latex(formula.latex):
            if symbol in symbol_names:
                relation = "defined_by" if formula.latex.split("=", 1)[0].find(symbol) >= 0 else "appears_in"
                edges.append(RelationEdge(source=symbol, target=label, relation=relation, evidence_id=formula.formula_id))
    return edges


def _token_counter(text: str) -> Counter[str]:
    return Counter(re.findall(r"[A-Za-z0-9_\\]+", text.lower()))


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _shorten(text: str, max_len: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."
