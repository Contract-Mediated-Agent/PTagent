from __future__ import annotations

import re
from dataclasses import dataclass

from .latex_display import clean_latex_evidence_for_markdown
from .schemas import FormulaRecord, SourceSpan


MATH_ENVIRONMENTS = (
    "equation",
    "equation*",
    "align",
    "align*",
    "aligned",
    "aligned*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "split",
    "eqnarray",
    "eqnarray*",
)

NARRATIVE_START_RE = re.compile(
    r"^(where|thus|since|we|the|if|for|from|among|owing|because|to calculate|in this|after|before)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedFormula:
    latex: str
    raw_text: str
    source_id: str
    page_number: int
    parser: str
    heading: str = ""
    equation_number: str = ""
    context: str = ""
    kind: str = "unknown"
    evidence_level: str = "source_inferred"
    confidence: float = 0.0


def extract_formula_records(spans: list[SourceSpan]) -> list[FormulaRecord]:
    """Extract clean formula evidence from non-TeX spans.

    TeX source files use `codex_tex_parser` directly. This span parser handles
    PDF/Markdown projected text with the same Codex-authored display rules:
    avoid prose in math blocks, keep evidence renderable, and prefer concise
    formula candidates over maximal recall.
    """

    formulas: list[_ParsedFormula] = []
    for span in spans:
        formulas.extend(_extract_from_span(span))
    formulas = _dedupe_formulas(formulas)
    formulas.sort(key=lambda item: (item.page_number, _kind_sort_key(item.kind), -item.confidence, item.source_id))
    return [
        FormulaRecord(
            formula_id=f"F{index}",
            latex=item.latex,
            source_id=item.source_id,
            label=_formula_label(item.latex, item.equation_number),
            kind=item.kind,
            evidence_level=item.evidence_level,
            confidence=item.confidence,
            raw_text=item.raw_text,
            equation_number=item.equation_number,
            context=item.context,
            parser_sources=[item.parser],
        )
        for index, item in enumerate(formulas, start=1)
    ]


def classify_formula_kind(formula: str, heading: str = "", context: str = "") -> str:
    probe = f"{heading} {context} {formula}".lower()
    normalized = _normalize_formula_probe(formula)
    lhs = _normalize_formula_probe(formula.split("=", 1)[0] if "=" in formula else formula[:80])

    if _looks_like_effective_potential(lhs, normalized, probe):
        return "effective_potential"
    if _looks_like_tree_potential(lhs, normalized, probe):
        return "tree_potential"
    if _looks_like_daisy(lhs, normalized, probe):
        return "daisy"
    if _looks_like_thermal(lhs, normalized, probe):
        return "thermal"
    if _looks_like_zero_temp_loop(lhs, normalized, probe):
        return "zero_temp_loop"
    if _looks_like_mass(lhs, normalized, probe):
        return "mass"
    return "unknown"


def _extract_from_span(span: SourceSpan) -> list[_ParsedFormula]:
    formulas: list[_ParsedFormula] = []
    occupied: list[tuple[int, int]] = []
    for index, block in enumerate(_math_blocks(span.text), start=1):
        occupied.append((int(block["start"]), int(block["end"])))
        formula = _formula_from_block(span, block, index=index)
        if formula is not None:
            formulas.append(formula)
    for index, block in enumerate(_plain_formula_blocks(span.text, occupied), start=1):
        formula = _formula_from_block(span, block, index=10_000 + index)
        if formula is not None:
            formulas.append(formula)
    return formulas


def _formula_from_block(span: SourceSpan, block: dict[str, object], *, index: int) -> _ParsedFormula | None:
    raw = str(block["body"]).strip()
    latex = _clean_formula(raw, env=str(block.get("env", "")))
    if not _useful_formula(latex):
        return None
    context = _nearby_context(span.text, int(block["start"]), int(block["end"]))
    kind = classify_formula_kind(latex, span.heading, context)
    return _ParsedFormula(
        latex=latex,
        raw_text=raw,
        source_id=f"{span.source_id}:f{index}",
        page_number=span.page_number,
        parser=span.parser or "codex_span_parser",
        heading=span.heading,
        equation_number=_extract_equation_number(raw),
        context=context,
        kind=kind,
        evidence_level="source_inferred" if span.parser.startswith(("pdf", "pdf2md", "paper_md")) else "source_exact",
        confidence=_confidence(latex, env=str(block.get("env", "")), kind=kind, parser=span.parser),
    )


def _math_blocks(text: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    env_alternatives = "|".join(re.escape(env) for env in sorted(MATH_ENVIRONMENTS, key=len, reverse=True))
    env_pattern = re.compile(rf"\\begin\{{({env_alternatives})\}}(.*?)\\end\{{\1\}}", re.DOTALL)
    for match in env_pattern.finditer(text):
        blocks.append(
            {
                "env": match.group(1),
                "body": match.group(2),
                "start": match.start(),
                "end": match.end(),
            }
        )
    for env, pattern in (
        ("display_math", re.compile(r"\\\[(.*?)\\\]", re.DOTALL)),
        ("display_math", re.compile(r"\$\$(.*?)\$\$", re.DOTALL)),
    ):
        for match in pattern.finditer(text):
            if _inside_existing_block(blocks, match.start(), match.end()):
                continue
            blocks.append({"env": env, "body": match.group(1), "start": match.start(), "end": match.end()})
    blocks.sort(key=lambda item: int(item["start"]))
    return blocks


def _plain_formula_blocks(text: str, occupied: list[tuple[int, int]]) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    lines = _line_ranges(text)
    index = 0
    while index < len(lines):
        start, end, line = lines[index]
        stripped = line.strip()
        if _range_overlaps(start, end, occupied) or not _looks_like_formula_line(stripped):
            index += 1
            continue
        chunk = [stripped]
        block_start = start
        block_end = end
        cursor = index + 1
        while cursor < len(lines) and len(chunk) < 8:
            next_start, next_end, next_line = lines[cursor]
            next_text = next_line.strip()
            if _range_overlaps(next_start, next_end, occupied) or not next_text:
                break
            if NARRATIVE_START_RE.match(next_text.lower()) and "=" not in next_text:
                break
            if not (_looks_like_formula_line(next_text) or _looks_like_formula_continuation(next_text)):
                break
            chunk.append(next_text)
            block_end = next_end
            cursor += 1
        blocks.append(
            {
                "env": "plain_formula",
                "body": "\n".join(chunk),
                "start": block_start,
                "end": block_end,
            }
        )
        index = max(cursor, index + 1)
    return blocks


def _clean_formula(value: str, *, env: str) -> str:
    text = value.strip()
    text = re.sub(r"\\(?:label|notag|nonumber)\b(?:\{[^{}]*\})?", " ", text)
    text = re.sub(r"\\tag\{[^{}]*\}", " ", text)
    text = re.sub(r"\\\\\s*\[[^\]]+\]", r"\\", text)
    text = _drop_trailing_narrative(text)
    if env.startswith(("align", "gather", "eqnarray")) or _needs_aligned_wrapper(text):
        text = r"\begin{aligned} " + _normalize_alignment_rows(text) + r" \end{aligned}"
    return clean_latex_evidence_for_markdown(text, max_chars=1800)


def _drop_trailing_narrative(text: str) -> str:
    text = re.sub(r"\s+where\s+\$.*$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\.\s+(Then|Because|This|These|The)\b.*$", ".", text, flags=re.DOTALL)
    return text.strip()


def _needs_aligned_wrapper(text: str) -> bool:
    if re.search(r"\\begin\{(?:aligned|array|pmatrix|bmatrix|matrix)\}", text):
        return False
    return "&" in text or r"\\" in text


def _normalize_alignment_rows(text: str) -> str:
    rows = _split_rows(text)
    cleaned: list[str] = []
    for row in rows:
        row = row.strip()
        row = re.sub(r"(?<!\\)&", " & ", row)
        row = re.sub(r"\s+", " ", row).strip()
        if row:
            cleaned.append(row)
    return r" \\ ".join(cleaned) if len(cleaned) > 1 else (cleaned[0] if cleaned else "")


def _split_rows(text: str) -> list[str]:
    rows: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif text.startswith(r"\\", index) and depth == 0:
            rows.append(text[start:index])
            index += 2
            if index < len(text) and text[index] == "[":
                end = text.find("]", index)
                if end != -1:
                    index = end + 1
            start = index
            continue
        index += 1
    rows.append(text[start:])
    return rows


def _line_ranges(text: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        clean = line.rstrip("\r\n")
        ranges.append((offset, offset + len(clean), clean))
        offset += len(line)
    return ranges


def _looks_like_formula_line(line: str) -> bool:
    if len(line) < 6 or NARRATIVE_START_RE.match(line.lower()):
        return False
    if "=" not in line:
        return False
    if len(line) > 900:
        return False
    return bool(re.search(r"\\[A-Za-z]+|[_^{}]|\b[VvmM]\b|lambda|mu|thermal|daisy", line))


def _looks_like_formula_continuation(line: str) -> bool:
    return bool(re.match(r"^(?:[&+*/^_{}\\,.\-\s]|\d|\(|\)|\[|\])+$", line)) or line.startswith(("&", "+", "-", r"\\"))


def _nearby_context(text: str, start: int, end: int, window: int = 420) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    context = text[left:start] + " " + text[end:right]
    context = re.sub(r"\\(?:cite|ref|label|cref|Cref|eqref)\{[^{}]*\}", " ", context)
    return re.sub(r"\s+", " ", context).strip()[:900]


def _extract_equation_number(raw: str) -> str:
    tag = re.search(r"\\tag\{([^{}]+)\}", raw)
    if tag:
        return tag.group(1).strip()
    match = re.search(r"\(\s*(\d+(?:\.\d+)*)\s*\)", raw)
    return match.group(1) if match else ""


def _useful_formula(value: str) -> bool:
    if not value or value == "not detected" or len(value) < 6:
        return False
    if NARRATIVE_START_RE.match(value.strip()):
        return False
    if re.search(r"\\(?:documentclass|usepackage|begin\{document\}|end\{document\})", value):
        return False
    return any(token in value for token in ("=", "\\", "^", "_", "+", "-", "V", "m"))


def _confidence(value: str, *, env: str, kind: str, parser: str) -> float:
    score = 0.42
    if env.startswith(("equation", "align", "gather", "display")):
        score += 0.18
    if parser.startswith(("tex", "paper_md", "pdf2md")):
        score += 0.08
    if "=" in value:
        score += 0.12
    if kind != "unknown":
        score += 0.12
    if len(value) <= 900:
        score += 0.06
    return min(score, 0.98)


def _dedupe_formulas(items: list[_ParsedFormula]) -> list[_ParsedFormula]:
    seen: set[str] = set()
    result: list[_ParsedFormula] = []
    for item in items:
        key = re.sub(r"\s+", "", item.latex)[:300]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _inside_existing_block(blocks: list[dict[str, object]], start: int, end: int) -> bool:
    return any(int(block["start"]) <= start and end <= int(block["end"]) for block in blocks)


def _range_overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < right and left < end for left, right in ranges)


def _normalize_formula_probe(value: str) -> str:
    text = value.lower()
    replacements = {
        r"\mathrm": "",
        r"\rm": "",
        r"\text": "",
        r"\left": "",
        r"\right": "",
        r"\{": "{",
        r"\}": "}",
        "{": "",
        "}": "",
        " ": "",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = text.replace("\\", "")
    return text


def _looks_like_effective_potential(lhs: str, normalized: str, probe: str) -> bool:
    return (
        "veff" in lhs
        or "v_eff" in lhs
        or ("effective potential" in probe and "v0" in normalized and ("vcw" in normalized or "v1t" in normalized))
    )


def _looks_like_tree_potential(lhs: str, normalized: str, probe: str) -> bool:
    return lhs.startswith(("v0", "v_0", "vtree")) or ("tree" in probe and "v0" in normalized)


def _looks_like_mass(lhs: str, normalized: str, probe: str) -> bool:
    if lhs.startswith(("m", "m2", "m_", "m^")) and not lhs.startswith(("model", "manual")):
        return True
    return "mass" in probe and any(token in normalized for token in ("m2", "m^2", "m_", "matrix"))


def _looks_like_thermal(lhs: str, normalized: str, probe: str) -> bool:
    if any(token in lhs for token in ("v1t", "v_1t", "vt", "v_t")):
        return True
    if any(token in lhs for token in ("ch", "c_h", "cs", "c_s", "pi", "pi_")) and (
        "thermal" in probe or "finite-temperature" in probe or "t^2" in normalized
    ):
        return True
    return any(token in normalized for token in ("jb", "j_b", "jf", "j_f", "t^4"))


def _looks_like_zero_temp_loop(lhs: str, normalized: str, probe: str) -> bool:
    if any(token in lhs for token in ("v1", "v_1", "vcw", "v_cw", "vct", "v_ct")):
        return True
    return "coleman" in probe or "counterterm" in probe


def _looks_like_daisy(lhs: str, normalized: str, probe: str) -> bool:
    if any(token in lhs for token in ("vring", "v_ring", "vdaisy", "v_daisy")):
        return True
    return "daisy" in probe or "ring" in probe or "debye" in probe or "resumm" in probe


def _formula_label(formula: str, equation_number: str) -> str:
    left = formula.split("=", 1)[0].strip()
    label = re.sub(r"\s+", " ", left)[:80]
    return f"Eq. ({equation_number}) {label}" if equation_number else label


def _kind_sort_key(kind: str) -> int:
    order = {
        "effective_potential": 10,
        "tree_potential": 20,
        "zero_temp_loop": 30,
        "thermal": 40,
        "daisy": 50,
        "mass": 60,
        "unknown": 90,
    }
    return order.get(kind, 80)
