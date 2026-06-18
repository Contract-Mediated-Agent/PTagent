from __future__ import annotations

import re


def strip_mathematica_comments(text: str) -> str:
    """Remove Mathematica comments while preserving newlines for diagnostics."""

    result: list[str] = []
    index = 0
    depth = 0
    while index < len(text):
        two = text[index : index + 2]
        if two == "(*":
            depth += 1
            index += 2
            continue
        if depth and two == "*)":
            depth -= 1
            index += 2
            continue
        char = text[index]
        if depth:
            if char in "\r\n":
                result.append(char)
            else:
                result.append(" ")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def wolfram_calls(text: str, name: str) -> list[str]:
    """Extract top-level-looking Wolfram calls, ignoring Mathematica comments."""

    code = strip_mathematica_comments(text)
    calls: list[str] = []
    pattern = re.compile(rf"\b{re.escape(name)}\s*\[")
    for match in pattern.finditer(code):
        start = match.start()
        pos = match.end()
        depth = 1
        while pos < len(code) and depth > 0:
            char = code[pos]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            pos += 1
        if depth == 0:
            calls.append(code[start:pos].replace("\n", " ").strip())
    return calls


def perform_drsoft_calls(text: str) -> list[str]:
    return wolfram_calls(text, "PerformDRsoft")


def format_multiple_perform_drsoft_message(calls: list[str], *, source_label: str) -> str:
    preview = "; ".join(calls[:5])
    if len(calls) > 5:
        preview += "; ..."
    return (
        f"Multiple active PerformDRsoft[...] calls were found in {source_label}. "
        "Mathematica comments (* ... *) are ignored. PTagent 3DEFT accepts only one "
        "reviewed PerformDRsoft[...] call because each soft step changes the later "
        "ultrasoft potential. Please provide a DRalgo source with only the intended "
        f"PerformDRsoft[...] call. Detected calls: {preview}"
    )
