from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from typing import Any, Mapping


class SafeExpressionError(ValueError):
    """Raised when an imported expression is outside PTagent's safe grammar."""


_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "cos": None,
    "cosh": None,
    "exp": None,
    "ifnonzero": None,
    "log": None,
    "max": max,
    "min": min,
    "sin": None,
    "sinh": None,
    "sqrt": None,
    "tan": None,
    "tanh": None,
}

_FUNCTION_ALIASES = {
    "Abs": "abs",
    "Cos": "cos",
    "Cosh": "cosh",
    "Exp": "exp",
    "IFNONZERO": "ifnonzero",
    "Log": "log",
    "Max": "max",
    "Min": "min",
    "Sin": "sin",
    "Sinh": "sinh",
    "Sqrt": "sqrt",
    "Tan": "tan",
    "Tanh": "tanh",
}

_FORTRAN_EXPONENT = re.compile(r"(?<=\d)[dD](?=[+-]?\d)")
_MATHEMATICA_EXPONENT = re.compile(r"(?<=\d)\*\^(?=[+-]?\d)")
_FUNCTION_BRACKET = re.compile(r"\b([A-Za-z_]\w*)\[")
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


@dataclass(frozen=True)
class SafeExpression:
    source: str
    python: str
    names: tuple[str, ...]


def parse_safe_expression(
    expression: str,
    *,
    allowed_names: set[str] | None = None,
    allow_block_references: bool = False,
) -> SafeExpression:
    source = str(expression).strip()
    if not source:
        raise SafeExpressionError("Expression is empty.")
    normalized = _normalize_sarah_expression(source, allow_block_references=allow_block_references)
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise SafeExpressionError(f"Invalid expression syntax: {source!r}") from exc
    validator = _ExpressionValidator(allowed_names=allowed_names)
    validator.visit(tree)
    return SafeExpression(source=source, python=ast.unparse(tree), names=tuple(sorted(validator.names)))


def evaluate_safe_expression(expression: str, values: Mapping[str, float]) -> float:
    parsed = parse_safe_expression(expression, allowed_names=set(values))
    functions: dict[str, Any] = {
        "abs": abs,
        "cos": math.cos,
        "cosh": math.cosh,
        "exp": math.exp,
        "ifnonzero": lambda primary, fallback: primary if primary != 0 else fallback,
        "log": math.log,
        "max": max,
        "min": min,
        "sin": math.sin,
        "sinh": math.sinh,
        "sqrt": math.sqrt,
        "tan": math.tan,
        "tanh": math.tanh,
    }
    result = _evaluate_node(
        ast.parse(parsed.python, mode="eval").body,
        {**functions, "pi": math.pi, **{name: float(value) for name, value in values.items()}},
    )
    if isinstance(result, complex):
        raise SafeExpressionError("Complex expression values are not supported by the SARAH v1 importer.")
    return float(result)


def _evaluate_node(node: ast.AST, environment: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return environment[node.id]
    if isinstance(node, ast.UnaryOp):
        operation = {ast.UAdd: operator.pos, ast.USub: operator.neg}[type(node.op)]
        return operation(_evaluate_node(node.operand, environment))
    if isinstance(node, ast.BinOp):
        operation = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
        }[type(node.op)]
        return operation(_evaluate_node(node.left, environment), _evaluate_node(node.right, environment))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = environment[node.func.id]
        return function(*(_evaluate_node(argument, environment) for argument in node.args))
    raise SafeExpressionError(f"Unsupported evaluated node: {type(node).__name__}.")


def validate_identifier(value: str, *, subject: str = "identifier") -> str:
    value = str(value).strip()
    if not _IDENTIFIER.fullmatch(value):
        raise SafeExpressionError(f"Invalid {subject}: {value!r}.")
    return value


def _normalize_sarah_expression(expression: str, *, allow_block_references: bool) -> str:
    normalized = " ".join(expression.split())
    normalized = _FORTRAN_EXPONENT.sub("e", normalized)
    normalized = _MATHEMATICA_EXPONENT.sub("e", normalized)
    normalized = normalized.replace("^", "**")
    normalized = normalized.replace("\\[Pi]", "pi")
    normalized = re.sub(r"\bPi\b", "pi", normalized)
    for source, target in _FUNCTION_ALIASES.items():
        normalized = re.sub(rf"\b{re.escape(source)}\b", target, normalized)
    normalized = _convert_function_brackets(normalized, allow_block_references=allow_block_references)
    if re.search(r"\b(?:I|Complex|Conjugate|conj|Re|Im)\b", normalized):
        raise SafeExpressionError("Complex-valued expressions are outside the real-parameter SARAH v1 scope.")
    return normalized


def _convert_function_brackets(expression: str, *, allow_block_references: bool) -> str:
    chars = list(expression)
    stack: list[tuple[int, bool]] = []
    index = 0
    while index < len(chars):
        if chars[index] == "[":
            prefix = "".join(chars[:index])
            match = re.search(r"([A-Za-z_]\w*)\s*$", prefix)
            if not match:
                raise SafeExpressionError("Only named function or SLHA block brackets are supported.")
            name = match.group(1)
            is_function = name in _ALLOWED_FUNCTIONS
            if not is_function and not allow_block_references:
                raise SafeExpressionError(f"Unsupported function or indexed symbol {name!r}.")
            if not is_function:
                raise SafeExpressionError(
                    "SLHA block references must be resolved before expression validation."
                )
            chars[index] = "("
            stack.append((index, is_function))
        elif chars[index] == "]":
            if not stack:
                raise SafeExpressionError("Unmatched closing bracket in expression.")
            stack.pop()
            chars[index] = ")"
        index += 1
    if stack:
        raise SafeExpressionError("Unmatched opening bracket in expression.")
    return "".join(chars)


class _ExpressionValidator(ast.NodeVisitor):
    def __init__(self, *, allowed_names: set[str] | None) -> None:
        self.allowed_names = allowed_names
        self.names: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        allowed = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Constant,
            ast.Name,
            ast.Call,
            ast.Load,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Pow,
            ast.UAdd,
            ast.USub,
        )
        if not isinstance(node, allowed):
            raise SafeExpressionError(f"Unsupported expression construct: {type(node).__name__}.")
        super().generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise SafeExpressionError("Only real numeric literals are allowed in imported expressions.")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _ALLOWED_FUNCTIONS or node.id == "pi":
            return
        validate_identifier(node.id)
        if self.allowed_names is not None and node.id not in self.allowed_names:
            raise SafeExpressionError(f"Unresolved name {node.id!r} in expression.")
        self.names.add(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCTIONS:
            raise SafeExpressionError("Only allowlisted scalar functions may be called.")
        if node.keywords:
            raise SafeExpressionError("Keyword arguments are not supported in imported expressions.")
        if node.func.id == "ifnonzero" and len(node.args) != 2:
            raise SafeExpressionError("IFNONZERO requires exactly two arguments.")
        if node.func.id in {"sqrt", "log", "exp", "abs", "sin", "cos", "tan", "sinh", "cosh", "tanh"} and len(node.args) != 1:
            raise SafeExpressionError(f"{node.func.id} requires exactly one argument.")
        for argument in node.args:
            self.visit(argument)
