"""Verify displayed equation references without guessing TeX counter values."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


NUMBER = r"(?:[A-Za-z]+\.)?\d+(?:\.\d+)*(?:[a-z]|\([a-z]\))?"
_TOKEN = rf"\(?\s*({NUMBER})\s*\)?"
_REFERENCES = re.compile(
    rf"\b(?:Eqs?\.?|equations?)\s*~?\s*({_TOKEN}"
    rf"(?:(?:\s*(?:,|;|/|-|\u2013|\u2014|and|to)\s*){_TOKEN})*)",
    re.IGNORECASE,
)


def cited_equation_numbers(text: str) -> set[str]:
    """Return explicitly cited numbers (range endpoints), never internal ids."""
    return {number for match in _REFERENCES.finditer(text)
            for number in re.findall(NUMBER, match.group(1))}


def explicit_tex_tags(text: str) -> list[str]:
    # Comments are not equation labels, including commented-out alternatives.
    text = re.sub(r"(?<!\\)(?:\\\\)*%[^\n]*", "", text)
    return [tag.strip() for tag in re.findall(r"\\tag\*?\s*\{([^{}]+)\}", text)]


def verify_equation_reference(reference: dict[str, Any], path: Path) -> str:
    """Check a PDF page, compiled aux label, or explicit TeX tag as data only.

    This verifies the displayed number, not the scientific interpretation of
    its formula. No TeX execution or counter reconstruction is performed.
    """
    number = reference.get("number", "")
    if not isinstance(number, str) or not re.fullmatch(NUMBER, number):
        raise ValueError("Equation reference needs a displayed number, not a formula id")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        page = reference.get("page")
        if type(page) is not int or page < 1:
            raise ValueError("Equation verification requires a 1-based PDF page")
        import fitz

        with fitz.open(path) as document:
            if page > len(document):
                raise ValueError(f"Equation verification PDF page out of range: {page}")
            text = document[page - 1].get_text()
        if not re.search(r"\(\s*" + re.escape(number) + r"\s*\)", text):
            raise ValueError(f"Equation ({number}) not found on PDF page {page}")
    elif suffix == ".aux":
        label = reference.get("tex_label")
        if not isinstance(label, str) or not label:
            raise ValueError("Equation verification from aux requires tex_label")
        text = path.read_text(encoding="utf-8")
        values = re.findall(r"\\newlabel\{" + re.escape(label) + r"\}\{\{([^{}]+)\}", text)
        if values != [number]:
            raise ValueError(f"Equation ({number}) does not match compiled label {label!r}")
    elif suffix in {".tex", ".ltx"}:
        if number not in explicit_tex_tags(path.read_text(encoding="utf-8")):
            raise ValueError(f"Equation ({number}) has no explicit TeX tag; use PDF or aux evidence")
    else:
        raise ValueError("Verify equation numbers using a PDF page, .aux label, or explicit .tex tag")
    return number
