from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings
from .schemas import SourceSpan

SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".md", ".markdown", ".tex"}

RELEVANCE_KEYWORDS = [
    "appendix",
    "effective potential",
    "tree-level",
    "tree level",
    "coleman",
    "counterterm",
    "thermal",
    "thermal mass",
    "debye",
    "self-energy",
    "self energy",
    "longitudinal",
    "transverse",
    "eigenvalue",
    "eigenvalues",
    "finite temperature",
    "daisy",
    "ring",
    "mass matrix",
    "field-dependent",
    "background",
    "scalar potential",
    "model parameters",
    "input parameters",
    "parameter relation",
    "minimization condition",
    "vacuum expectation",
    "benchmark",
    "cosmotransitions",
    "phase transition",
    "nucleation",
    "critical temperature",
]


@dataclass
class SourceDocument:
    paper_id: str
    path: Path
    suffix: str
    spans: list[SourceSpan]
    diagnostics: dict[str, str]
    paper_markdown: str = ""


@dataclass
class _Page:
    page_number: int
    text: str
    parser: str


@dataclass
class _MarkdownPage:
    page_number: int
    markdown: str
    parser: str


def read_source(path: str | Path, settings: Settings) -> SourceDocument:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SOURCE_SUFFIXES:
        raise ValueError(
            f"Unsupported source suffix {suffix or '<none>'}; supported: {sorted(SUPPORTED_SOURCE_SUFFIXES)}"
        )

    if suffix == ".pdf":
        pages, diagnostics = _read_pdf_pages(source_path)
        page_spans = _select_pdf_spans(source_path.stem, pages, settings.source_span_limit)
        paper_markdown = ""
        paper_markdown_spans: list[SourceSpan] = []
        markdown_spans: list[SourceSpan] = []
        if settings.pdf_markdown_enabled:
            markdown_pages, markdown_diagnostics = _read_pdf_markdown_pages(source_path)
            paper_markdown = _build_paper_markdown(source_path.stem, source_path, pages, markdown_pages)
            paper_markdown_limit = max(settings.source_span_limit * 3, 24)
            projected_markdown_limit = max(settings.source_span_limit * 2, 16)
            paper_markdown_spans = _select_paper_markdown_spans(source_path.stem, paper_markdown, paper_markdown_limit)
            markdown_spans = _select_pdf_markdown_spans(source_path.stem, markdown_pages, projected_markdown_limit)
            diagnostics.update(markdown_diagnostics)
        spans = _merge_pdf_spans(paper_markdown_spans, markdown_spans, page_spans)
    else:
        text = source_path.read_text(encoding="utf-8", errors="replace")
        spans = _split_text_source(source_path.stem, text, suffix, settings.source_span_limit)
        diagnostics = {"parser": "text"}
        if suffix == ".tex":
            diagnostics["formula_parser"] = "codex_tex_parser"
            diagnostics["tex_formula_path"] = str(source_path.resolve())
        paper_markdown = text if suffix in {".md", ".markdown", ".tex"} else ""

    return SourceDocument(
        paper_id=source_path.stem,
        path=source_path,
        suffix=suffix,
        spans=spans,
        diagnostics=diagnostics,
        paper_markdown=paper_markdown,
    )


def build_context(
    spans: list[SourceSpan],
    max_chars: int = 18000,
    *,
    required_terms: list[str] | tuple[str, ...] | None = None,
) -> str:
    entries: list[tuple[int, SourceSpan, str]] = []
    for index, span in enumerate(spans):
        header = f"[{span.source_id} page={span.page_number} heading={span.heading}]"
        entries.append((index, span, f"{header}\n{span.text.strip()}"))
    if sum(len(block) for _, _, block in entries) <= max_chars:
        return "\n\n---\n\n".join(block for _, _, block in entries)

    required = _required_context_entries(entries, required_terms or [])
    ranked = sorted(entries, key=lambda item: (-float(item[1].score), item[1].page_number, item[0]))
    selected: list[tuple[int, SourceSpan, str]] = []
    selected_keys: set[str] = set()
    total = 0
    for pool in (required, ranked):
        for index, span, block in pool:
            key = span.source_id
            if key in selected_keys:
                continue
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining <= 300:
                    continue
                block = block[:remaining] + "\n[... truncated ...]"
            selected.append((index, span, block))
            selected_keys.add(key)
            total += len(block)
            if total >= max_chars:
                break
        if total >= max_chars:
            break
    selected.sort(key=lambda item: (item[1].page_number, item[0]))
    return "\n\n---\n\n".join(block for _, _, block in selected)


def _required_context_entries(
    entries: list[tuple[int, SourceSpan, str]],
    required_terms: list[str] | tuple[str, ...],
) -> list[tuple[int, SourceSpan, str]]:
    normalized_terms = [
        term.strip().lower()
        for term in required_terms
        if isinstance(term, str) and len(term.strip()) >= 2
    ]
    if not normalized_terms:
        return []
    scored: list[tuple[int, int, tuple[int, SourceSpan, str]]] = []
    for item in entries:
        index, span, block = item
        probe = f"{span.heading}\n{span.text}".lower()
        hits = sum(1 for term in normalized_terms if term.lower() in probe)
        if hits <= 0:
            continue
        scored.append((-hits, index, item))
    scored.sort(key=lambda value: (value[0], value[1]))
    return [item for _hits, _index, item in scored]


def _read_pdf_pages(path: Path) -> tuple[list[_Page], dict[str, str]]:
    readers: list[tuple[str, Callable[[Path], list[_Page]]]] = [
        ("pypdf", _read_with_pypdf),
        ("pymupdf", _read_with_pymupdf),
        ("pdfplumber", _read_with_pdfplumber),
    ]
    errors: dict[str, str] = {}
    for name, reader in readers:
        try:
            pages = reader(path)
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        if _formula_signal(pages) or any(page.text.strip() for page in pages):
            diagnostics = {"parser": name}
            diagnostics.update({f"{key}_error": value for key, value in errors.items()})
            return pages, diagnostics

    return [], {"parser": "none", **{f"{key}_error": value for key, value in errors.items()}}


def _read_pdf_markdown_pages(path: Path) -> tuple[list[_MarkdownPage], dict[str, str]]:
    errors: dict[str, str] = {}
    for name, reader in (
        ("pymupdf4llm", _read_pdf_markdown_with_pymupdf4llm),
        ("pymupdf_blocks", _read_pdf_markdown_with_pymupdf_blocks),
    ):
        try:
            pages = reader(path)
        except Exception as exc:
            errors[f"pdf2md_{name}_error"] = f"{type(exc).__name__}: {exc}"
            continue
        if pages:
            diagnostics = {"pdf2md_parser": pages[0].parser}
            diagnostics.update(errors)
            return pages, diagnostics
    return [], {"pdf2md_parser": "none", **errors}


def _read_pdf_markdown_with_pymupdf4llm(path: Path) -> list[_MarkdownPage]:
    import pymupdf4llm

    result = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    if isinstance(result, str):
        return [_MarkdownPage(page_number=0, markdown=result, parser="pdf2md:pymupdf4llm")]
    pages: list[_MarkdownPage] = []
    for index, item in enumerate(result if isinstance(result, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        page_number = int(metadata.get("page", item.get("page", index)) or index)
        text = str(item.get("text") or item.get("markdown") or "")
        if text.strip():
            pages.append(_MarkdownPage(page_number=page_number, markdown=text, parser="pdf2md:pymupdf4llm"))
    return pages


def _read_pdf_markdown_with_pymupdf_blocks(path: Path) -> list[_MarkdownPage]:
    import fitz

    table_map = _read_pdf_tables_as_markdown(path)
    doc = fitz.open(str(path))
    pages: list[_MarkdownPage] = []
    try:
        for index, page in enumerate(doc, start=1):
            lines: list[str] = []
            for block in page.get_text("dict").get("blocks", []):
                block_text, font_sizes = _block_to_text_and_sizes(block)
                if not block_text:
                    continue
                if _looks_like_heading_block(block_text, font_sizes):
                    lines.append(f"## {block_text}")
                elif _looks_like_equation_text(block_text):
                    lines.append("$$\n" + _normalize_pdf_formula_text(block_text) + "\n$$")
                else:
                    lines.append(block_text)
            lines.extend(table_map.get(index, []))
            markdown = "\n\n".join(line for line in lines if line.strip())
            if markdown.strip():
                pages.append(_MarkdownPage(page_number=index, markdown=markdown, parser="pdf2md:pymupdf_blocks"))
    finally:
        doc.close()
    return pages


def _block_to_text_and_sizes(block: dict) -> tuple[str, list[float]]:
    lines: list[str] = []
    sizes: list[float] = []
    for line in block.get("lines", []):
        parts: list[str] = []
        for span in line.get("spans", []):
            text = str(span.get("text", ""))
            if text:
                parts.append(text)
            size = span.get("size")
            if isinstance(size, (int, float)):
                sizes.append(float(size))
        line_text = " ".join(part.strip() for part in parts if part.strip())
        if line_text:
            lines.append(line_text)
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip(), sizes


def _read_pdf_tables_as_markdown(path: Path) -> dict[int, list[str]]:
    tables_by_page: dict[int, list[str]] = {}
    try:
        import pdfplumber
    except Exception:
        return tables_by_page
    try:
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []
                rendered = [_table_to_markdown(table) for table in tables]
                tables_by_page[index] = [table for table in rendered if table]
    except Exception:
        return {}
    return tables_by_page


def _table_to_markdown(table: list[list[object]]) -> str:
    rows = [[_clean_table_cell(cell) for cell in row] for row in table if row]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _clean_table_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "/")


def _read_with_pypdf(path: Path) -> list[_Page]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [
        _Page(page_number=index + 1, text=page.extract_text() or "", parser="pypdf")
        for index, page in enumerate(reader.pages)
    ]


def _read_with_pymupdf(path: Path) -> list[_Page]:
    import fitz

    doc = fitz.open(str(path))
    pages: list[_Page] = []
    try:
        for index, page in enumerate(doc):
            pages.append(_Page(page_number=index + 1, text=page.get_text("text") or "", parser="pymupdf"))
    finally:
        doc.close()
    return pages


def _read_with_pdfplumber(path: Path) -> list[_Page]:
    import pdfplumber

    pages: list[_Page] = []
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages):
            pages.append(_Page(page_number=index + 1, text=page.extract_text() or "", parser="pdfplumber"))
    return pages


def _formula_signal(pages: list[_Page]) -> bool:
    text = "\n".join(page.text[:2000] for page in pages)
    return any(token in text for token in ("V_", "Veff", "Veff", "lambda", "thermal", "Daisy"))


def _select_pdf_spans(paper_id: str, pages: list[_Page], limit: int) -> list[SourceSpan]:
    scored = [
        SourceSpan(
            source_id=f"{paper_id}:p{page.page_number}",
            page_number=page.page_number,
            heading=_infer_heading(page.text),
            text=_clean_text(page.text),
            score=_score_text(page.text),
            parser=page.parser,
        )
        for page in pages
        if page.text.strip()
    ]
    by_page = {span.page_number: span for span in scored}
    scored.sort(key=lambda item: item.score, reverse=True)
    selected_pages: set[int] = set()
    for span in scored[: max(1, limit)]:
        selected_pages.add(span.page_number)
        selected_pages.add(span.page_number - 1)
        selected_pages.add(span.page_number + 1)
    selected = [by_page[page] for page in sorted(selected_pages) if page in by_page]
    selected.sort(key=lambda item: item.page_number)
    return selected


def _select_pdf_markdown_spans(paper_id: str, pages: list[_MarkdownPage], limit: int) -> list[SourceSpan]:
    spans: list[SourceSpan] = []
    for page in pages:
        chunks = _split_markdown_sections(page.markdown)
        for index, (heading, body) in enumerate(chunks, start=1):
            if not body.strip():
                continue
            spans.append(
                SourceSpan(
                    source_id=f"{paper_id}:mdp{page.page_number}s{index}",
                    page_number=page.page_number,
                    heading=heading,
                    text=_clean_text(body),
                    score=_score_text(f"{heading}\n{body}") + 1.0,
                    parser=page.parser,
                )
            )
    spans.sort(key=lambda item: item.score, reverse=True)
    selected = spans[: max(1, limit)]
    selected.sort(key=lambda item: (item.page_number, item.source_id))
    return selected


def _build_paper_markdown(
    paper_id: str,
    source_path: Path,
    raw_pages: list[_Page],
    markdown_pages: list[_MarkdownPage],
) -> str:
    markdown_by_page = {page.page_number: page for page in markdown_pages}
    raw_by_page = {page.page_number: page for page in raw_pages}
    page_numbers = sorted(set(markdown_by_page) | set(raw_by_page))
    if not page_numbers:
        return ""

    lines = [
        f"# Paper Markdown: {paper_id}",
        "",
        f"<!-- PTAGENT source_path: {source_path} -->",
        "<!-- PTAGENT note: machine-generated reading layer; verify formulas against source pages before compilation -->",
        "",
    ]
    for page_number in page_numbers:
        raw_page = raw_by_page.get(page_number)
        md_page = markdown_by_page.get(page_number)
        source_id = f"{paper_id}:p{page_number}"
        parsers = ", ".join(item for item in [raw_page.parser if raw_page else "", md_page.parser if md_page else ""] if item)
        lines.extend(
            [
                f"## Page {page_number}",
                "",
                f"Source: `{source_id}`",
                f"Parser sources: `{parsers or 'unknown'}`",
                "",
            ]
        )
        formula_blocks = _page_formula_blocks(source_id, raw_page, md_page)
        if formula_blocks:
            lines.extend(["### Formula Candidates", ""])
            for block in formula_blocks:
                lines.extend(block)
                lines.append("")
        if md_page and md_page.markdown.strip():
            lines.extend(["### Structured Text", "", _clean_projected_markdown(md_page.markdown), ""])
        elif raw_page and raw_page.text.strip():
            lines.extend(["### Page Text", "", _light_markdown_from_raw_text(raw_page.text), ""])
    return "\n".join(lines).strip() + "\n"


def _page_formula_blocks(source_id: str, raw_page: _Page | None, md_page: _MarkdownPage | None) -> list[list[str]]:
    spans: list[SourceSpan] = []
    if raw_page and raw_page.text.strip():
        spans.append(
            SourceSpan(
                source_id=source_id,
                page_number=raw_page.page_number,
                heading=_infer_heading(raw_page.text),
                text=raw_page.text,
                score=_score_text(raw_page.text),
                parser=raw_page.parser,
            )
        )
    if md_page and md_page.markdown.strip():
        spans.append(
            SourceSpan(
                source_id=f"{source_id}:md",
                page_number=md_page.page_number,
                heading=_infer_heading(md_page.markdown),
                text=md_page.markdown,
                score=_score_text(md_page.markdown),
                parser=md_page.parser,
            )
        )
    if not spans:
        return []
    from .formula_extractor import extract_formula_records

    records = extract_formula_records(spans)
    records.sort(key=lambda item: (item.kind == "unknown", -item.confidence, item.formula_id))
    blocks: list[list[str]] = []
    seen: set[str] = set()
    for record in records[:10]:
        compact = re.sub(r"\s+", "", record.latex)
        if compact in seen:
            continue
        seen.add(compact)
        equation = f" Eq. ({record.equation_number})" if record.equation_number else ""
        blocks.append(
            [
                f"<!-- formula_id: {record.formula_id}; source: {record.source_id}; kind: {record.kind}; confidence: {record.confidence:.2f}{equation} -->",
                "$$",
                record.latex,
                "$$",
            ]
        )
    return blocks


def _clean_projected_markdown(markdown: str) -> str:
    text = markdown.replace("\x00", " ")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _light_markdown_from_raw_text(text: str) -> str:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            continue
        if _looks_like_equation_text(stripped):
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            paragraphs.append("$$\n" + _normalize_pdf_formula_text(stripped) + "\n$$")
        else:
            buffer.append(stripped)
    if buffer:
        paragraphs.append(" ".join(buffer))
    return "\n\n".join(paragraphs)


def _select_paper_markdown_spans(paper_id: str, paper_markdown: str, limit: int) -> list[SourceSpan]:
    if not paper_markdown.strip():
        return []
    chunks = _split_paper_markdown_pages(paper_markdown)
    spans: list[SourceSpan] = []
    for index, (heading, body) in enumerate(chunks, start=1):
        page_number = _page_number_from_heading(heading) or index
        if not body.strip():
            continue
        spans.append(
            SourceSpan(
                source_id=f"{paper_id}:paper{index}",
                page_number=page_number,
                heading=heading,
                text=_clean_text(body),
                score=_score_text(f"{heading}\n{body}") + 2.5,
                parser="paper_md",
            )
        )
    spans.sort(key=lambda item: item.score, reverse=True)
    selected = spans[: max(1, limit)]
    selected.sort(key=lambda item: (item.page_number, item.source_id))
    return selected


def _split_paper_markdown_pages(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^##\s+Page\s+(\d+)\s*$", text))
    if not matches:
        return _split_markdown_sections(text)
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page_number = match.group(1)
        chunks.append((f"Page {page_number}", text[match.start() : end]))
    return chunks


def _page_number_from_heading(heading: str) -> int:
    match = re.search(r"\bPage\s+(\d+)\b", heading, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _merge_pdf_spans(
    paper_markdown_spans: list[SourceSpan],
    markdown_spans: list[SourceSpan],
    page_spans: list[SourceSpan],
) -> list[SourceSpan]:
    merged = [*paper_markdown_spans, *markdown_spans, *page_spans]
    merged.sort(
        key=lambda item: (
            item.page_number,
            0 if item.parser.startswith("paper_md") else 1 if item.parser.startswith("pdf2md:") else 2,
            item.source_id,
        )
    )
    return merged


def _split_text_source(paper_id: str, text: str, suffix: str, limit: int) -> list[SourceSpan]:
    chunks: list[tuple[str, str]] = []
    if suffix in {".md", ".markdown"}:
        chunks = _split_markdown_sections(text)
    elif suffix == ".tex":
        chunks = _split_latex_sections(text)
    else:
        chunks = _chunk_text(text)

    spans = [
        SourceSpan(
            source_id=f"{paper_id}:s{index}",
            page_number=index,
            heading=heading,
            text=_clean_text(body),
            score=_score_text(f"{heading}\n{body}"),
            parser="text",
        )
        for index, (heading, body) in enumerate(chunks, start=1)
        if body.strip()
    ]
    spans.sort(key=lambda item: item.score, reverse=True)
    forced = [span for span in spans if _must_keep_source_span(span)]
    selected = _dedupe_source_spans([*forced, *spans[: max(1, limit)]])
    selected = selected[: max(limit, min(len(selected), limit * 3))]
    selected.sort(key=lambda item: item.page_number)
    return selected


def _must_keep_source_span(span: SourceSpan) -> bool:
    text = f"{span.heading}\n{span.text}"
    lowered = text.lower()
    has_appendix_daisy_evidence = bool(
        re.search(r"\bappendix\b|\\appendix|\\section\{[^}]*thermal", lowered)
        and re.search(
            r"daisy|ring|thermal mass|debye|self[- ]energy|longitudinal|transverse|"
            r"eigenvalues?|\\pi|pi\s*_|w\^?3|w3|gamma|\\gamma",
            lowered,
        )
    )
    has_potential = bool(re.search(r"\\begin\{align\*?\}|\\begin\{equation\*?\}|v\s*\(|v_\{|potential", text, re.IGNORECASE))
    has_parameter_definition = bool(
        re.search(
            r"model parameters|input parameters|free parameters|fully specified|expressed in terms|"
            r"minimi[sz]ation conditions?|vacuum expectation|vev",
            lowered,
        )
    )
    has_core_formula = bool(
        re.search(
            r"\\lambda\s*&?=|\\mu\^?2\s*&?=|a_\{?1\}?\s*&?=|a_\{?2\}?\s*&?=|b_\{?2\}?\s*&?=|"
            r"v_\{?\\?rm\s*eff\}?|v_\{?\\?mathrm\{eff\}\}?",
            text,
            flags=re.IGNORECASE,
        )
    )
    return has_appendix_daisy_evidence or (has_parameter_definition and has_core_formula) or (has_potential and has_parameter_definition)


def _dedupe_source_spans(spans: list[SourceSpan]) -> list[SourceSpan]:
    seen: set[str] = set()
    result: list[SourceSpan] = []
    for span in spans:
        key = span.source_id
        if key in seen:
            continue
        seen.add(key)
        result.append(span)
    return result


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    heading = "Document"
    buffer: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            if buffer:
                chunks.append((heading, "\n".join(buffer)))
                buffer = []
            heading = line.strip("# ").strip() or "Section"
        buffer.append(line)
    if buffer:
        chunks.append((heading, "\n".join(buffer)))
    return chunks or [("Document", text)]


def _split_latex_sections(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"\\(?:section|subsection|subsubsection)\*?\{([^}]*)\}")
    matches = list(pattern.finditer(text))
    if not matches:
        return _chunk_text(text)
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunks.append((match.group(1), text[start:end]))
    return chunks


def _chunk_text(text: str, size: int = 3200) -> list[tuple[str, str]]:
    chunks = []
    for index in range(0, len(text), size):
        chunks.append((f"Chunk {index // size + 1}", text[index : index + size]))
    return chunks or [("Document", text)]


def _score_text(text: str) -> float:
    lowered = text.lower()
    score = 0.0
    for keyword in RELEVANCE_KEYWORDS:
        score += lowered.count(keyword) * 4.0
    if re.search(r"\bappendix\b|\\appendix", lowered) and re.search(
        r"daisy|ring|thermal mass|debye|self[- ]energy|longitudinal|transverse|"
        r"eigenvalues?|\\pi|pi\s*_|w\^?3|w3|gamma|\\gamma",
        lowered,
    ):
        score += 25.0
    score += len(re.findall(r"V_\{|V_|V\\|m_\{|m\\|\\lambda|lambda|\\mu|mu", text)) * 0.6
    score += len(re.findall(r"\\begin\{(?:equation|align|aligned|gather)\*?\}|\$\$|\\\[", text)) * 1.5
    return score


def _looks_like_heading_block(text: str, font_sizes: list[float]) -> bool:
    stripped = " ".join(text.split())
    if not 4 <= len(stripped) <= 140:
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    lowered = stripped.lower()
    has_section_number = bool(re.match(r"^(?:\d+|[ivx]+)\.?\s+", lowered))
    has_keyword = any(token in lowered for token in RELEVANCE_KEYWORDS)
    has_large_font = bool(font_sizes) and max(font_sizes) >= 12 and max(font_sizes) >= sum(font_sizes) / len(font_sizes) + 0.8
    return has_section_number or has_keyword or has_large_font


def _looks_like_equation_text(text: str) -> bool:
    compact = " ".join(text.split())
    if "=" not in compact or len(compact) > 700:
        return False
    math_tokens = (
        "V",
        "m_",
        "lambda",
        "mu",
        "phi",
        "Phi",
        "thermal",
        "daisy",
        "Π",
        "λ",
        "μ",
        "∂",
        "^",
    )
    if any(token in compact for token in math_tokens):
        return True
    return len(re.findall(r"[+\-*/^=(){}\[\]]", compact)) >= 4


def _normalize_pdf_formula_text(text: str) -> str:
    text = " ".join(text.split())
    replacements = {
        "λ": r"\lambda",
        "Λ": r"\Lambda",
        "μ": r"\mu",
        "ϕ": r"\phi",
        "Φ": r"\Phi",
        "π": r"\pi",
        "Π": r"\Pi",
        "∂": r"\partial",
        "†": r"^\dagger",
        "−": "-",
        "×": r"\times",
        "·": r"\cdot",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _infer_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if 4 <= len(stripped) <= 120 and any(token in stripped.lower() for token in RELEVANCE_KEYWORDS):
            return stripped
    return ""


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
