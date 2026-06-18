from __future__ import annotations

import re
from collections.abc import Iterator


def normalize_latex_for_display(value: str) -> str:
    """Make common paper shorthand render cleanly in Markdown/KaTeX previews.

    This is display-only normalization. It must not be used as evidence or as
    the machine-actionable expression consumed by the template compiler.
    """

    expr = value.strip()
    if not expr:
        return expr

    expr = _normalize_text_placeholders(expr)
    expr = _normalize_named_potentials(expr)
    expr = _normalize_numbered_potentials(expr)
    expr = _normalize_common_temperatures(expr)
    expr = _normalize_common_masses(expr)
    expr = wrap_latex_alignment_if_needed(expr)
    return expr


def clean_latex_evidence_for_markdown(value: str, *, max_chars: int = 1400) -> str:
    """Return a short renderable LaTeX evidence snippet for human Markdown review.

    This intentionally cleans only the display copy used in review documents.
    The original TeX/evidence remains available in PaperMemory and `paper.md`.
    """

    text = str(value or "").strip()
    if not text or text.lower() == "not detected":
        return "not detected"
    text = _strip_outer_fence(text)
    text = _drop_latex_document_noise(text)
    text = _unwrap_render_block_environments(text)
    text = _remove_latex_spacing_rows(text)
    text = _collapse_latex_whitespace(text)
    text = normalize_latex_for_display(text)
    text = _truncate_latex_for_display(text, max_chars=max_chars)
    return text or "not detected"


def wrap_latex_alignment_if_needed(value: str) -> str:
    """Wrap naked align bodies so Markdown/KaTeX can render ``&`` and ``\\``."""

    expr = value.strip()
    if not expr:
        return expr
    if re.search(r"\\begin\{(?:align|aligned|gather|multline|split)\*?\}", expr):
        return expr
    if "&" not in expr and r"\\" not in expr:
        return expr
    return r"\begin{aligned} " + expr + r" \end{aligned}"


def render_latex_evidence_block(value: str, *, max_chars: int = 1400) -> str:
    """Render display-only LaTeX evidence as Markdown math."""

    cleaned = clean_latex_evidence_for_markdown(value, max_chars=max_chars)
    if cleaned == "not detected":
        return "not detected"
    return "$$\n" + cleaned + "\n$$"


def normalize_markdown_math(markdown_text: str) -> str:
    """Normalize only Markdown math spans/blocks, leaving editable text intact."""

    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return "$$" + normalize_latex_for_display(match.group(2)) + "$$"
        return "$" + normalize_latex_for_display(match.group(5)) + "$"

    return re.sub(r"(\$\$)(.*?)(\$\$)|(\$)(.*?)(\$)", replace, markdown_text, flags=re.DOTALL)


def normalize_markdown_tables_for_display(markdown_text: str) -> str:
    """Repair common generated-table separator mistakes for preview rendering only."""

    lines = markdown_text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_table_line(line) and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            header_cells = _table_cells(line)
            separator_cells = _table_cells(lines[index + 1])
            output.append(line)
            if len(separator_cells) != len(header_cells):
                output.append("|" + "|".join("---" for _ in header_cells) + "|")
            else:
                output.append(lines[index + 1])
            index += 2
            continue
        output.append(line)
        index += 1
    return "\n".join(output)


def iter_display_math_segments(markdown_text: str) -> Iterator[tuple[str, str]]:
    """Yield ("markdown"|"latex", text) segments split on display math blocks."""

    pattern = re.compile(r"\\\[(.*?)\\\]|\$\$(.*?)\$\$", re.DOTALL)
    pos = 0
    for match in pattern.finditer(markdown_text):
        if match.start() > pos:
            yield "markdown", markdown_text[pos : match.start()]
        formula = match.group(1) if match.group(1) is not None else match.group(2)
        yield "latex", formula
        pos = match.end()
    if pos < len(markdown_text):
        yield "markdown", markdown_text[pos:]


def unwrap_markdown_fence(markdown_text: str) -> str:
    """Remove an outer ```markdown fence often returned by generated drafts."""

    text = markdown_text.strip()
    full = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE)
    if full:
        return full.group(1).strip()
    for match in re.finditer(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE):
        body = match.group(1).strip()
        if _looks_like_model_template(body):
            return body
        before = text[: match.start()].strip()
        after = text[match.end() :].strip()
        if not before and not after:
            return body
    return markdown_text


def _looks_like_model_template(text: str) -> bool:
    lowered = text.lower()
    return (
        "finite-temperature phase transition model input" in lowered
        or "# 0. basic model information" in lowered
        or "# 1. fields, background fields" in lowered
        or "# 3. tree-level potential" in lowered
    )


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _normalize_text_placeholders(expr: str) -> str:
    placeholders = {
        r"\text{AI infer}": "\x00AI_INFER\x00",
        r"\text{Pending}": "\x00PENDING\x00",
        r"\text{None}": "\x00NONE\x00",
    }
    for raw, token in placeholders.items():
        expr = expr.replace(raw, token)

    expr = expr.replace("AI_infer", r"\text{AI infer}")
    expr = expr.replace("AI infer", r"\text{AI infer}")
    expr = re.sub(r"(?<![A-Za-z\\])Pending(?![A-Za-z])", lambda _: r"\text{Pending}", expr)
    expr = re.sub(r"(?<![A-Za-z\\])None(?![A-Za-z])", lambda _: r"\text{None}", expr)

    for raw, token in placeholders.items():
        expr = expr.replace(token, raw)
    return expr


def _strip_outer_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:latex|tex)?\s*\n(.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _drop_latex_document_noise(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(
            r"\\(?:documentclass|usepackage|RequirePackage|newcommand|renewcommand|providecommand|def|title|author|affiliation|emailAdd|date|bibliography|bibliographystyle)\b",
            stripped,
        ):
            continue
        if re.match(r"\\(?:begin|end)\{document\}|\\(?:maketitle|flushbottom|acknowledgments|addcontentsline)\b", stripped):
            continue
        if re.match(r"\\(?:section|subsection|subsubsection)\*?\{", stripped):
            continue
        stripped = re.sub(r"(?<!\\)%.*$", "", stripped).strip()
        if stripped:
            kept.append(stripped)
    text = "\n".join(kept)
    text = re.sub(r"\\(?:label|cite|cref|Cref|ref|eqref)\{[^{}]*\}", "", text)
    return text.strip()


def _unwrap_render_block_environments(text: str) -> str:
    text = text.strip()
    for env in ("equation", "equation*", "displaymath"):
        text = re.sub(rf"\\begin\{{{re.escape(env)}\}}", "", text)
        text = re.sub(rf"\\end\{{{re.escape(env)}\}}", "", text)
    for env in ("align", "align*", "gather", "gather*", "multline", "multline*", "eqnarray", "eqnarray*"):
        text = re.sub(rf"\\begin\{{{re.escape(env)}\}}", r"\\begin{aligned}", text)
        text = re.sub(rf"\\end\{{{re.escape(env)}\}}", r"\\end{aligned}", text)
    return text.strip()


def _remove_latex_spacing_rows(text: str) -> str:
    return re.sub(r"\\\\\s*\[[^\]]+\]", r"\\\\", text)


def _collapse_latex_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_latex_for_display(text: str, *, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    last_break = max(cut.rfind(r"\\"), cut.rfind("\n"), cut.rfind(";"))
    if last_break > max_chars * 0.55:
        cut = cut[:last_break].rstrip()
    return cut + r"\quad \cdots"


def _normalize_named_potentials(expr: str) -> str:
    names = {
        "CW": r"V_{\mathrm{CW}}",
        "CT": r"V_{\mathrm{CT}}",
        "ring": r"V_{\mathrm{ring}}",
        "daisy": r"V_{\mathrm{daisy}}",
        "eff": r"V_{\mathrm{eff}}",
        "tot": r"V_{\mathrm{tot}}",
    }
    for name, rendered in names.items():
        expr = _sub(rf"(?<![A-Za-z\\])V_\{{\s*\\mathrm\{{{re.escape(name)}\}}\s*\}}", rendered, expr)
        expr = _sub(rf"(?<![A-Za-z\\])V_\{{\s*\\rm\s+{re.escape(name)}\s*\}}", rendered, expr)
        expr = _sub(rf"(?<![A-Za-z\\])V_\{{\s*{re.escape(name)}\s*\}}", rendered, expr)
        expr = _sub(rf"(?<![A-Za-z\\])V_{re.escape(name)}\b", rendered, expr)
        expr = _sub(rf"(?<![A-Za-z\\])V{re.escape(name)}\b", rendered, expr)
    expr = _sub(r"(?<![A-Za-z\\])VT\b", r"V_T", expr)
    return expr


def _normalize_numbered_potentials(expr: str) -> str:
    expr = _sub(r"(?<![A-Za-z\\])V_\{\s*1T\s*\}", r"V_{1T}", expr)
    expr = _sub(r"(?<![A-Za-z\\])V_\{\s*1\s*\}", r"V_1", expr)
    expr = _sub(r"(?<![A-Za-z\\])V_\{\s*0\s*\}", r"V_0", expr)
    expr = _sub(r"(?<![A-Za-z\\])V_1T\b", r"V_{1T}", expr)
    expr = _sub(r"(?<![A-Za-z\\])V1T\b", r"V_{1T}", expr)
    expr = _sub(r"(?<![A-Za-z\\])V_1\b", r"V_1", expr)
    expr = _sub(r"(?<![A-Za-z\\])V1\b", r"V_1", expr)
    expr = _sub(r"(?<![A-Za-z\\])V_0\b", r"V_0", expr)
    expr = _sub(r"(?<![A-Za-z\\])V0\b", r"V_0", expr)
    return expr


def _normalize_common_temperatures(expr: str) -> str:
    for symbol in ("c", "n", "f"):
        expr = _sub(rf"(?<![A-Za-z\\])T{symbol}\b", rf"T_{symbol}", expr)
    return expr


def _normalize_common_masses(expr: str) -> str:
    expr = re.sub(r"(?<![A-Za-z\\])m([A-Z])\b", lambda match: f"m_{match.group(1)}", expr)
    return expr


def _sub(pattern: str, replacement: str, text: str) -> str:
    return re.sub(pattern, lambda _: replacement, text)
