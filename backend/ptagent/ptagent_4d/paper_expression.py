"""Conservative scalar-expression translation; no model or normalization templates."""
from __future__ import annotations

import re

from .safe_expression import SafeExpressionError, parse_safe_expression


_GREEK = set("alpha beta gamma delta epsilon eta theta kappa lambda mu nu xi pi rho sigma tau phi chi psi omega".split())


def scalar_expression(source: str) -> str:
    """Accept explicit scalar arithmetic, refusing unsupported multiplet algebra.

    Products must be explicit or separated by whitespace. Adjacent letter names
    are one symbol: splitting them would silently choose a physics convention.
    """
    text = str(source).strip().strip("$").rstrip(".")
    if len(text) > 20000:
        raise ValueError("Expression exceeds the scalar translation limit.")
    if "=" in text:
        if text.count("=") != 1:
            raise ValueError("Expected one scalar expression, not a chain of equations.")
        text = text.split("=", 1)[1].strip()
    text = text.replace(r"\left", "").replace(r"\right", "")
    for command in (r"\,", r"\;", r"\!", r"\ "):
        text = text.replace(command, " ")
    text = text.replace(r"\cdot", "*").replace(r"\times", "*")
    # Reduce innermost braced fractions first; remaining commands are rejected.
    for _ in range(32):
        replaced = re.sub(r"\\(?:d?frac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"((\1)/(\2))", text)
        if replaced == text:
            break
        text = replaced
    def command(match):
        name = match.group(1)
        if name not in _GREEK:
            raise ValueError(f"Unsupported LaTeX operation: {name}; supply a reviewed scalar expression.")
        return name
    text = re.sub(r"\\([A-Za-z]+)", command, text)
    text = re.sub(r"_\{([A-Za-z0-9_]+)\}", r"_\1", text)
    text = re.sub(r"\blambda\b", "lambda_", text)
    text = re.sub(r"\^\{([^{}]+)\}", r"**(\1)", text)
    text = text.replace("^", "**")
    if any(char in text for char in "{}\\;:&"):
        raise ValueError("Unsupported grouping or non-scalar source expression.")
    # Only unambiguous whitespace-separated products are inserted.
    text = re.sub(r"(?<=[\w)])\s+(?=[A-Za-z_(])", "*", text)
    try:
        return parse_safe_expression(text).python
    except SafeExpressionError as exc:
        raise ValueError(str(exc)) from exc
