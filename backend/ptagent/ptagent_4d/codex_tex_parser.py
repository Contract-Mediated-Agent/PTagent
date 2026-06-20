from __future__ import annotations

import re
from dataclasses import dataclass

from .formula_extractor import classify_formula_kind
from .latex_display import clean_latex_evidence_for_markdown
from .schemas import FormulaRecord


MATH_ENVIRONMENTS = {
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
}


@dataclass(frozen=True)
class ParsedTexFormula:
    latex: str
    source_id: str
    context: str = ""
    equation_number: str = ""
    kind: str = "unknown"
    confidence: float = 0.0


def parse_tex_formula_records(
    tex_text: str,
    *,
    paper_id: str,
    source_name: str = "tex",
    max_records: int = 120,
) -> list[FormulaRecord]:
    """Parse TeX into clean formula records for human-review evidence.

    This parser is not a full TeX engine. It is a local
    extraction layer for PTagent review templates: expand simple macros, ignore
    prose/preamble noise, and emit renderable Markdown math snippets.
    """

    formulas = parse_tex_formulas(tex_text, paper_id=paper_id, source_name=source_name)
    formulas = _dedupe_formulas(formulas)
    formulas.sort(key=lambda item: (_kind_sort_key(item.kind), -item.confidence, item.source_id))
    records: list[FormulaRecord] = []
    for index, item in enumerate(formulas[:max_records], start=1):
        records.append(
            FormulaRecord(
                formula_id=f"C{index}",
                latex=item.latex,
                source_id=item.source_id,
                label=_formula_label(item.latex, item.equation_number),
                kind=item.kind,
                evidence_level="source_exact",
                confidence=item.confidence,
                raw_text=item.latex,
                equation_number=item.equation_number,
                context=item.context,
                parser_sources=["codex_tex_parser"],
            )
        )
    return records


def parse_tex_formulas(
    tex_text: str,
    *,
    paper_id: str,
    source_name: str = "tex",
) -> list[ParsedTexFormula]:
    text = _strip_comments(tex_text)
    macros = _collect_simple_macros(text)
    body = _document_body(text)
    body = _drop_non_formula_environments(body)
    body = _expand_simple_macros(body, macros)

    formulas: list[ParsedTexFormula] = []
    for index, block in enumerate(_iter_math_blocks(body), start=1):
        cleaned = _clean_formula_block(block["body"], env=str(block["env"]))
        if not _useful_formula(cleaned):
            continue
        context = _nearby_context(body, int(block["start"]), int(block["end"]))
        kind = classify_formula_kind(cleaned, "", context)
        formulas.append(
            ParsedTexFormula(
                latex=cleaned,
                source_id=f"{paper_id}:{source_name}:codex{index}",
                context=context,
                equation_number=_extract_equation_number(str(block["raw"])),
                kind=kind,
                confidence=_confidence(cleaned, str(block["env"]), kind),
            )
        )
    return formulas


def _iter_math_blocks(text: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    env_alternatives = "|".join(re.escape(env) for env in sorted(MATH_ENVIRONMENTS, key=len, reverse=True))
    env_pattern = re.compile(rf"\\begin\{{({env_alternatives})\}}(.*?)\\end\{{\1\}}", re.DOTALL)
    for match in env_pattern.finditer(text):
        blocks.append(
            {
                "env": match.group(1),
                "body": match.group(2),
                "raw": match.group(0),
                "start": match.start(),
                "end": match.end(),
            }
        )
    for pattern_name, pattern in (
        ("display_math", re.compile(r"\\\[(.*?)\\\]", re.DOTALL)),
        ("display_math", re.compile(r"\$\$(.*?)\$\$", re.DOTALL)),
    ):
        for match in pattern.finditer(text):
            if _inside_existing_block(blocks, match.start(), match.end()):
                continue
            blocks.append(
                {
                    "env": pattern_name,
                    "body": match.group(1),
                    "raw": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    blocks.sort(key=lambda item: int(item["start"]))
    return blocks


def _clean_formula_block(value: str, *, env: str) -> str:
    text = value.strip()
    text = re.sub(r"\\(?:label|notag|nonumber)\b(?:\{[^{}]*\})?", " ", text)
    text = re.sub(r"\\tag\{[^{}]*\}", " ", text)
    text = re.sub(r"\\qquad|\\quad|\\,", " ", text)
    text = re.sub(r"\\\\\s*\[[^\]]+\]", r"\\", text)
    text = _normalize_alignment_rows(text)
    already_aligned = bool(re.search(r"\\begin\{(?:aligned|array|pmatrix|bmatrix|matrix)\}", text))
    if not already_aligned and (env.startswith(("align", "gather", "eqnarray")) or r"\\" in text or "&" in text):
        text = r"\begin{aligned} " + text + r" \end{aligned}"
    return clean_latex_evidence_for_markdown(text, max_chars=1800)


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


def _strip_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        index = 0
        while True:
            hit = line.find("%", index)
            if hit < 0:
                lines.append(line.rstrip())
                break
            slash_count = 0
            cursor = hit - 1
            while cursor >= 0 and line[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2:
                index = hit + 1
                continue
            lines.append(line[:hit].rstrip())
            break
    return "\n".join(lines)


def _document_body(text: str) -> str:
    match = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", text, flags=re.DOTALL)
    return match.group(1) if match else text


def _drop_non_formula_environments(text: str) -> str:
    for env in ("figure", "figure*", "table", "table*", "thebibliography", "abstract"):
        text = re.sub(rf"\\begin\{{{re.escape(env)}\}}.*?\\end\{{{re.escape(env)}\}}", "\n", text, flags=re.DOTALL)
    return text


def _collect_simple_macros(text: str) -> dict[str, tuple[int, str]]:
    macros: dict[str, tuple[int, str]] = {}
    pattern = re.compile(r"\\(?:re)?newcommand\*?\s*\{\\([A-Za-z]+)\}\s*(?:\[(\d+)\])?\s*\{", re.DOTALL)
    for match in pattern.finditer(text):
        name = match.group(1)
        argc = int(match.group(2) or 0)
        replacement, _end = _read_balanced_group(text, match.end() - 1)
        if _simple_replacement(replacement):
            macros[name] = (argc, replacement)
    def_pattern = re.compile(r"\\def\\([A-Za-z]+)(?:#1)?\s*\{", re.DOTALL)
    for match in def_pattern.finditer(text):
        name = match.group(1)
        argc = 1 if "#1" in match.group(0) else 0
        replacement, _end = _read_balanced_group(text, match.end() - 1)
        if _simple_replacement(replacement):
            macros[name] = (argc, replacement)
    return macros


def _read_balanced_group(text: str, open_index: int) -> tuple[str, int]:
    if open_index >= len(text) or text[open_index] != "{":
        return "", open_index
    depth = 0
    body_start = open_index + 1
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[body_start:index], index + 1
    return "", open_index


def _simple_replacement(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and len(stripped) <= 160


def _expand_simple_macros(text: str, macros: dict[str, tuple[int, str]]) -> str:
    for name, (argc, replacement) in sorted(macros.items(), key=lambda item: len(item[0]), reverse=True):
        if argc == 0:
            text = re.sub(rf"\\{re.escape(name)}(?![A-Za-z])", lambda _m, repl=replacement: repl, text)
        elif argc == 1:
            text = _expand_one_arg_macro(text, name, replacement)
    return text


def _expand_one_arg_macro(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"\\{re.escape(name)}\s*\{{")
    cursor = 0
    parts: list[str] = []
    for match in pattern.finditer(text):
        parts.append(text[cursor : match.start()])
        body, end = _read_balanced_group(text, match.end() - 1)
        parts.append(replacement.replace("#1", body))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _nearby_context(text: str, start: int, end: int, window: int = 420) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    context = text[left:start] + " " + text[end:right]
    context = re.sub(r"\\(?:cite|ref|label|cref|Cref|eqref)\{[^{}]*\}", " ", context)
    return re.sub(r"\s+", " ", context).strip()[:900]


def _extract_equation_number(raw: str) -> str:
    match = re.search(r"\\tag\{([^{}]+)\}", raw)
    return match.group(1).strip() if match else ""


def _useful_formula(value: str) -> bool:
    if not value or value == "not detected":
        return False
    if len(value) < 6:
        return False
    if re.search(r"\\(?:documentclass|usepackage|begin\{document\}|end\{document\})", value):
        return False
    return any(token in value for token in ("=", "\\", "^", "_", "+", "-", "V", "m"))


def _confidence(value: str, env: str, kind: str) -> float:
    score = 0.45
    if env.startswith(("equation", "align", "gather")):
        score += 0.18
    if "=" in value:
        score += 0.12
    if kind != "unknown":
        score += 0.12
    if len(value) <= 900:
        score += 0.06
    if value.startswith(r"\begin{aligned}"):
        score += 0.03
    return min(score, 0.98)


def _dedupe_formulas(items: list[ParsedTexFormula]) -> list[ParsedTexFormula]:
    seen: set[str] = set()
    result: list[ParsedTexFormula] = []
    for item in items:
        key = re.sub(r"\s+", "", item.latex)[:300]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _inside_existing_block(blocks: list[dict[str, object]], start: int, end: int) -> bool:
    return any(int(block["start"]) <= start and end <= int(block["end"]) for block in blocks)


def _formula_label(formula: str, equation_number: str) -> str:
    left = formula.split("=", 1)[0].strip()
    label = re.sub(r"\s+", " ", left)[:80]
    return f"Eq. ({equation_number}) {label}" if equation_number else label


def _kind_sort_key(kind: str) -> int:
    order = {
        "tree_potential": 0,
        "effective_potential": 1,
        "zero_temp_loop": 2,
        "thermal": 3,
        "daisy": 4,
        "mass": 5,
        "unknown": 9,
    }
    return order.get(kind, 8)
