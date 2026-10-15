"""Read static SPheno example inputs as review proposals, without evaluation of Wolfram code."""
from __future__ import annotations

import ast
import hashlib
import math
import re
from pathlib import Path

from .safe_expression import evaluate_safe_expression, parse_safe_expression


_SYMBOL = r"[A-Za-z][A-Za-z0-9]*"
_SECTIONS = (
    "DefaultInputValues", "MINPAR", "EXTPAR", "BoundaryLowScaleInput",
    "DEFINITION[MatchingConditions]", "ParametersToSolveTadpoles",
)


def _without_comments(text: str) -> str:
    result, depth, quoted, i = [], 0, False, 0
    while i < len(text):
        pair = text[i:i + 2]
        if not quoted and pair == "(*":
            depth += 1
            result.append("  ")
            i += 2
        elif depth and pair == "*)":
            depth -= 1
            result.append("  ")
            i += 2
        elif depth:
            result.append("\n" if text[i] == "\n" else " ")
            i += 1
        elif quoted and text[i] == "\\":
            result.append(text[i:i + 2])
            i += 2
        else:
            if text[i] == '"':
                quoted = not quoted
            result.append(text[i])
            i += 1
    if depth or quoted:
        raise ValueError("Unclosed Wolfram comment or string.")
    return "".join(result)


def _split(text: str, separator: str) -> list[str]:
    stack, parts, start, quoted, escaped = [], [], 0, False, False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[(":
            stack.append(char)
        elif char in "}])":
            if not stack or stack.pop() != {"}": "{", "]": "[", ")": "("}[char]:
                raise ValueError("Unbalanced Wolfram delimiters.")
        elif char == separator and not stack:
            parts.append(text[start:index].strip())
            start = index + 1
    if stack or quoted:
        raise ValueError("Unclosed Wolfram expression.")
    parts.append(text[start:].strip())
    return parts


def _list(text: str) -> list[str]:
    text = text.strip()
    if not text.startswith("{") or not text.endswith("}"):
        raise ValueError("Only explicit static lists are supported.")
    return [part for part in _split(text[1:-1], ",") if part]


def _default_value(expression: str, values: dict) -> float:
    if len(expression) > 2048:
        raise ValueError("Default expression is too large.")
    parsed = parse_safe_expression(expression)
    tree = ast.parse(parsed.python, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 128:
        raise ValueError("Default expression is too complex.")
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and abs(node.value) > 1e100:
            raise ValueError("Default literal exceeds the static-reader limit.")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            node.value = float(node.value)  # Avoid unbounded integer growth in nested powers.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right.operand if isinstance(node.right, ast.UnaryOp) else node.right
            if not isinstance(exponent, ast.Constant) or not isinstance(exponent.value, (int, float)) or abs(exponent.value) > 64:
                raise ValueError("Default exponent is outside the bounded static grammar.")
    value = evaluate_safe_expression(ast.unparse(tree), values)
    if not math.isfinite(value):
        raise ValueError("Non-finite default value.")
    return value


def read_spheno_benchmark(model_source: str | Path | None) -> dict:
    """Extract source values and relations, never infer a complete numerical point."""
    report = {
        "schema": "ptagent.spheno_benchmark.v1", "status": "unavailable",
        "source": None, "default_inputs": {}, "default_expressions": {},
        "input_blocks": [], "boundary_conditions": [], "matching_conditions": [],
        "tadpole_parameters": [], "diagnostics": [], "applied": False,
        "message": "No model-local SPheno.m is available. Supply a benchmark through SLHA or explicit parameters.",
    }
    if not model_source:
        return report
    path = Path(model_source).expanduser().resolve() / "SPheno.m"
    if not path.is_file():
        return report
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("SPheno.m exceeds the 1 MiB static-reader limit.")
        raw = path.read_bytes()
        report["source"] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
        text = _without_comments(raw.decode("utf-8"))
        sections, invalid = {}, set()
        for statement in _split(text, ";"):
            match = re.fullmatch(r"\s*(DefaultInputValues|MINPAR|EXTPAR|BoundaryLowScaleInput|"
                                 r"ParametersToSolveTadpoles|DEFINITION\s*\[\s*MatchingConditions\s*\])\s*=\s*(.*)",
                                 statement, re.S)
            if match:
                name = re.sub(r"\s+", "", match[1])
                if name in sections:
                    invalid.add(name)
                sections[name] = match[2]
            else:
                # Conditional, delayed, or computed assignments are not static evidence.
                compact = re.sub(r"\s+", "", statement)
                for name in _SECTIONS:
                    if re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", compact):
                        invalid.add(name)
        for name in invalid:
            sections.pop(name, None)
            report["diagnostics"].append(f"{name}: repeated or non-static assignment; not used.")
        defaults = {}
        for rule in _list(sections.get("DefaultInputValues", "{}")):
            match = re.fullmatch(r"(" + _SYMBOL + r")\s*->\s*(.+)", rule, re.S)
            if not match or match[1] in defaults:
                raise ValueError("DefaultInputValues must contain unique symbol -> expression rules.")
            defaults[match[1]] = match[2].strip()
        report["default_expressions"] = defaults
        pending = dict(defaults)
        while pending:
            progress = False
            for name, expression in list(pending.items()):
                # Source expressions use the existing restricted arithmetic interpreter.
                try:
                    value = _default_value(expression, report["default_inputs"])
                except (ValueError, ArithmeticError, KeyError, TypeError, RecursionError):
                    continue
                report["default_inputs"][name] = value
                del pending[name]
                progress = True
            if not progress:
                break
        report["diagnostics"].extend(f"{name}: unresolved or unsupported default expression {expr!r}." for name, expr in pending.items())
        for name in ("MINPAR", "EXTPAR", "BoundaryLowScaleInput", "DEFINITION[MatchingConditions]", "ParametersToSolveTadpoles"):
            if name not in sections:
                continue
            try:
                rows = _list(sections[name])
                if name == "ParametersToSolveTadpoles":
                    if any(not re.fullmatch(_SYMBOL, row) for row in rows):
                        raise ValueError("Only scalar parameter symbols are supported.")
                    report["tadpole_parameters"] = rows
                    continue
                parsed = []
                for row in rows:
                    pair = _list(row)
                    if len(pair) != 2:
                        raise ValueError("Expected two entries in each mapping row.")
                    if name in {"MINPAR", "EXTPAR"}:
                        if not re.fullmatch(r"\d+", pair[0]) or not re.fullmatch(_SYMBOL, pair[1]):
                            raise ValueError("Expected an integer SLHA index and input symbol.")
                        parsed.append({"block": name, "indices": [int(pair[0])], "symbol": pair[1],
                                       "value": report["default_inputs"].get(pair[1])})
                    else:
                        parsed.append({"parameter": pair[0], "expression": pair[1], "source_location": name})
                key = "input_blocks" if name in {"MINPAR", "EXTPAR"} else "boundary_conditions" if name == "BoundaryLowScaleInput" else "matching_conditions"
                report[key].extend(parsed)
            except ValueError as exc:
                report["diagnostics"].append(f"{name}: {exc}")
        report["status"] = "pending_review" if report["default_inputs"] else "unavailable"
        report["message"] = (
            "Recommend this model's SPheno.m DefaultInputValues as the starting benchmark when the user has not supplied one. "
            "These are example inputs, not a fitted spectrum or a complete physical prescription. "
            "No SPheno run is required to read them. Preserve explicit user inputs and SLHA values. "
            "Review input mappings and source tadpole equations; derive remaining quantities only when their source relations and branch are established. "
            "Do not invent SM inputs, a renormalization scale, loop/tree equalities, or resummation choices. "
            "Present the proposed values and all remaining decisions together; final contract approval is still required."
        ) if report["default_inputs"] else "No safely resolved DefaultInputValues are available; inspect the diagnostics and request the missing benchmark inputs together."
    except (OSError, UnicodeError, ValueError) as exc:
        report.update(status="unsupported", default_inputs={}, message="Cannot safely read the SPheno example inputs; request source clarification or explicit parameters.")
        report["diagnostics"].append(str(exc))
    return report


def benchmark_review_lines(report: dict) -> list[str]:
    lines = ["## Source Benchmark Recommendation", "", report["message"], ""]
    if report.get("source"):
        lines += [f"Source: `{report['source']['path']}`, SHA256 `{report['source']['sha256']}`.", ""]
    if report["default_inputs"]:
        lines += ["| SPheno input | Source example value |", "| --- | --- |"]
        lines += [f"| `{name}` | {value:g} |" for name, value in report["default_inputs"].items()]
        lines += ["", "These values are proposed, not applied or user-confirmed.", ""]
    for key, title in (("boundary_conditions", "Source boundary relations"), ("matching_conditions", "Source matching relations")):
        if report[key]:
            lines += [title + ":"]
            lines += [f"- `{row['parameter']} = {row['expression']}`" for row in report[key]]
            lines.append("")
    if report["tadpole_parameters"]:
        lines += ["Parameters designated for tadpole solving: " + ", ".join(f"`{name}`" for name in report["tadpole_parameters"]) + ".", ""]
    lines += [f"- {message}" for message in report["diagnostics"]]
    return lines + [""]
