from __future__ import annotations

import ast
import math
from dataclasses import dataclass


class PhaseTracerExpressionError(ValueError):
    """Raised when a reviewed contract expression cannot be rendered as C++."""


@dataclass(frozen=True)
class CompilerBlock:
    assignments: list[tuple[str, ast.expr]]
    result: ast.expr


_BARE_FUNCTIONS = {"abs", "max", "min", "pow"}
_STD_FUNCTIONS = {
    "abs": "std::abs",
    "acos": "std::acos",
    "arccos": "std::acos",
    "asin": "std::asin",
    "arcsin": "std::asin",
    "atan": "std::atan",
    "arctan": "std::atan",
    "cos": "std::cos",
    "cosh": "std::cosh",
    "exp": "std::exp",
    "log": "std::log",
    "sin": "std::sin",
    "sinh": "std::sinh",
    "sqrt": "std::sqrt",
    "tan": "std::tan",
    "tanh": "std::tanh",
}
_SPECIAL_FUNCTIONS = {"maximum", "minimum", "power", "where", "logical_and", "logical_or"}


def render_cpp_expression(expr: str, *, prefer_square_aliases: bool = False) -> str:
    block = parse_compiler_block(expr)
    if block.assignments:
        raise PhaseTracerExpressionError("Expected a single expression, got a compiler block with assignments.")
    aliases = _square_aliases_for_block(block, target="", enabled=prefer_square_aliases)
    return CppExpressionRenderer(square_aliases=aliases).render(block.result)


def render_cpp_assignment_block(
    expr: str,
    target: str,
    *,
    indent: str = "    ",
    prefer_square_aliases: bool = False,
) -> list[str]:
    block = parse_compiler_block(expr)
    aliases = _square_aliases_for_block(block, target=target, enabled=prefer_square_aliases)
    renderer = CppExpressionRenderer(square_aliases=aliases)
    lines = [f"{indent}const double {alias} = {name} * {name};" for name, alias in aliases.items()]
    lines.extend(f"{indent}const double {name} = {renderer.render(value)};" for name, value in block.assignments)
    if (
        lines
        and block.assignments
        and block.assignments[-1][0] == target
        and isinstance(block.result, ast.Name)
        and block.result.id == target
    ):
        return lines
    lines.append(f"{indent}const double {target} = {renderer.render(block.result)};")
    return lines


def render_cpp_accumulation_block(
    expr: str,
    target: str,
    *,
    indent: str = "    ",
    prefer_square_aliases: bool = False,
) -> list[str]:
    block = parse_compiler_block(expr)
    aliases = _square_aliases_for_block(block, target="", enabled=prefer_square_aliases)
    renderer = CppExpressionRenderer(square_aliases=aliases)
    lines = [f"{indent}const double {alias} = {name} * {name};" for name, alias in aliases.items()]
    lines.extend(f"{indent}const double {name} = {renderer.render(value)};" for name, value in block.assignments)
    lines.append(f"{indent}{target} += {renderer.render(block.result)};")
    return lines


def parse_compiler_block(expr: str) -> CompilerBlock:
    text = str(expr or "0.0").strip() or "0.0"
    try:
        tree = ast.parse(text, mode="eval")
        return CompilerBlock(assignments=[], result=tree.body)
    except SyntaxError as eval_error:
        try:
            module = ast.parse(text, mode="exec")
        except SyntaxError:
            raise PhaseTracerExpressionError(f"Invalid compiler expression: {eval_error.msg}.") from eval_error
    if not module.body:
        raise PhaseTracerExpressionError("Compiler block is empty.")
    final = module.body[-1]
    body = module.body[:-1]
    if isinstance(final, ast.Assign):
        body = module.body
        if len(final.targets) != 1 or not isinstance(final.targets[0], ast.Name):
            raise PhaseTracerExpressionError("Final compiler-block assignment must have one simple name target.")
        result: ast.expr = ast.Name(id=final.targets[0].id, ctx=ast.Load())
    elif isinstance(final, ast.Expr):
        result = final.value
    else:
        raise PhaseTracerExpressionError("Compiler block must end with a final expression or simple assignment.")

    assignments: list[tuple[str, ast.expr]] = []
    assigned_names: set[str] = set()
    for stmt in body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            raise PhaseTracerExpressionError("Compiler blocks may contain only simple `name = expression` assignments.")
        name = stmt.targets[0].id
        if name in {"np", *_BARE_FUNCTIONS}:
            raise PhaseTracerExpressionError(f"Compiler block temporary {name!r} is reserved.")
        if name in assigned_names:
            raise PhaseTracerExpressionError(f"Compiler block temporary {name!r} is assigned more than once.")
        assigned_names.add(name)
        assignments.append((name, stmt.value))
    return CompilerBlock(assignments=assignments, result=result)


def _square_aliases_for_block(block: CompilerBlock, *, target: str, enabled: bool) -> dict[str, str]:
    if not enabled:
        return {}
    assigned_names = {name for name, _value in block.assignments}
    reserved_names = assigned_names | ({target} if target else set())
    aliases: dict[str, str] = {}
    for name in _square_base_names(block):
        if name in assigned_names:
            continue
        if name.endswith("_sq"):
            continue
        alias = f"{name}_sq"
        if alias in reserved_names:
            continue
        aliases.setdefault(name, alias)
    return aliases


def _square_base_names(block: CompilerBlock) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_BinOp(self, node: ast.BinOp) -> None:  # noqa: N802
            if isinstance(node.op, ast.Pow) and isinstance(node.left, ast.Name):
                exponent = _small_integer_literal(node.right)
                if exponent is not None and exponent in {2, 4, 6} and node.left.id not in seen:
                    seen.add(node.left.id)
                    names.append(node.left.id)
            self.generic_visit(node)

    visitor = Visitor()
    for _name, value in block.assignments:
        visitor.visit(value)
    visitor.visit(block.result)
    return names


def _small_integer_literal(node: ast.expr) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        value = float(node.value)
        integer = int(value)
        if value == integer and 0 <= integer <= 6:
            return integer
    return None


class CppExpressionRenderer:
    def __init__(self, *, square_aliases: dict[str, str] | None = None) -> None:
        self.square_aliases = dict(square_aliases or {})

    def render(self, node: ast.AST) -> str:
        if isinstance(node, ast.Expression):
            return self.render(node.body)
        if isinstance(node, ast.Constant):
            return self._constant(node.value)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
        if isinstance(node, ast.Name):
            if node.id == "np":
                raise PhaseTracerExpressionError("Bare `np` is not a C++ value.")
            if node.id == "pi":
                return "3.141592653589793238462643383279502884"
            return node.id
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        if isinstance(node, ast.UnaryOp):
            return self._unary(node)
        if isinstance(node, ast.BoolOp):
            op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + op.join(self.render(value) for value in node.values) + ")"
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.IfExp):
            return f"(({self.render(node.test)}) ? ({self.render(node.body)}) : ({self.render(node.orelse)}))"
        if isinstance(node, ast.Call):
            return self._call(node)
        raise PhaseTracerExpressionError(f"Unsupported expression node: {type(node).__name__}.")

    def _constant(self, value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return f"{value}.0"
        if isinstance(value, float):
            if not math.isfinite(value):
                raise PhaseTracerExpressionError("Non-finite numeric literals are not supported.")
            return repr(float(value))
        raise PhaseTracerExpressionError(f"Unsupported constant literal: {value!r}.")

    def _binop(self, node: ast.BinOp) -> str:
        left = self.render(node.left)
        right = self.render(node.right)
        if isinstance(node.op, ast.Add):
            return f"({left} + {right})"
        if isinstance(node.op, ast.Sub):
            return f"({left} - {right})"
        if isinstance(node.op, ast.Mult):
            return f"({left} * {right})"
        if isinstance(node.op, ast.Div):
            return f"({left} / {right})"
        if isinstance(node.op, ast.Mod):
            return f"std::fmod({left}, {right})"
        if isinstance(node.op, ast.Pow):
            integer_power = self._small_integer_power(node.right)
            if integer_power is not None:
                alias = self._square_alias(node.left, integer_power)
                if alias is not None:
                    return alias
                return self._integer_power(left, integer_power)
            return f"std::pow({left}, {right})"
        raise PhaseTracerExpressionError(f"Unsupported binary operator: {type(node.op).__name__}.")

    def _small_integer_power(self, node: ast.expr) -> int | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            value = float(node.value)
            integer = int(value)
            if value == integer and 0 <= integer <= 6:
                return integer
        return None

    def _integer_power(self, base: str, exponent: int) -> str:
        if exponent == 0:
            return "1.0"
        if exponent == 1:
            return base
        factor = f"({base})"
        return "(" + " * ".join(factor for _ in range(exponent)) + ")"

    def _square_alias(self, base_node: ast.expr, exponent: int) -> str | None:
        if not isinstance(base_node, ast.Name) or exponent not in {2, 4, 6}:
            return None
        alias = self.square_aliases.get(base_node.id)
        if not alias:
            return None
        if exponent == 2:
            return alias
        factor_count = exponent // 2
        return "(" + " * ".join(alias for _ in range(factor_count)) + ")"

    def _unary(self, node: ast.UnaryOp) -> str:
        value = self.render(node.operand)
        if isinstance(node.op, ast.USub):
            return f"(-{value})"
        if isinstance(node.op, ast.UAdd):
            return f"(+{value})"
        if isinstance(node.op, ast.Not):
            return f"(!({value}))"
        raise PhaseTracerExpressionError(f"Unsupported unary operator: {type(node.op).__name__}.")

    def _compare(self, node: ast.Compare) -> str:
        parts: list[str] = []
        left = node.left
        for op, right in zip(node.ops, node.comparators):
            parts.append(f"({self.render(left)} {self._compare_op(op)} {self.render(right)})")
            left = right
        return parts[0] if len(parts) == 1 else "(" + " && ".join(parts) + ")"

    def _compare_op(self, op: ast.cmpop) -> str:
        if isinstance(op, ast.Eq):
            return "=="
        if isinstance(op, ast.NotEq):
            return "!="
        if isinstance(op, ast.Lt):
            return "<"
        if isinstance(op, ast.LtE):
            return "<="
        if isinstance(op, ast.Gt):
            return ">"
        if isinstance(op, ast.GtE):
            return ">="
        raise PhaseTracerExpressionError(f"Unsupported comparison operator: {type(op).__name__}.")

    def _attribute(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id in {"np", "math"} and node.attr == "pi":
            return "3.141592653589793238462643383279502884"
        raise PhaseTracerExpressionError(f"Unsupported attribute expression: {ast.unparse(node)}.")

    def _call(self, node: ast.Call) -> str:
        if node.keywords:
            raise PhaseTracerExpressionError("Keyword arguments are not supported in PhaseTracer C++ expressions.")
        name = self._call_name(node.func)
        args = [self.render(arg) for arg in node.args]
        if name in _STD_FUNCTIONS:
            return f"{_STD_FUNCTIONS[name]}({', '.join(args)})"
        if name == "max":
            return _two_arg_call("std::max", args)
        if name == "min":
            return _two_arg_call("std::min", args)
        if name == "pow":
            return _two_arg_call("std::pow", args)
        if name == "maximum":
            return _two_arg_call("std::max", args)
        if name == "minimum":
            return _two_arg_call("std::min", args)
        if name == "power":
            return _two_arg_call("std::pow", args)
        if name == "where":
            if len(args) != 3:
                raise PhaseTracerExpressionError("np.where requires exactly three scalar arguments.")
            return f"(({args[0]}) ? ({args[1]}) : ({args[2]}))"
        if name == "logical_and":
            if len(args) < 2:
                raise PhaseTracerExpressionError("np.logical_and requires at least two arguments.")
            return "(" + " && ".join(args) + ")"
        if name == "logical_or":
            if len(args) < 2:
                raise PhaseTracerExpressionError("np.logical_or requires at least two arguments.")
            return "(" + " || ".join(args) + ")"
        raise PhaseTracerExpressionError(f"Unsupported function call: {name}.")

    def _call_name(self, func: ast.expr) -> str:
        if isinstance(func, ast.Name):
            if func.id not in _BARE_FUNCTIONS:
                raise PhaseTracerExpressionError(f"Unsupported bare function call: {func.id}.")
            return func.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "np":
            name = func.attr
            if name in _STD_FUNCTIONS or name in _SPECIAL_FUNCTIONS:
                return name
        raise PhaseTracerExpressionError("Only approved bare functions and np.<function> calls are supported.")


def _two_arg_call(name: str, args: list[str]) -> str:
    if len(args) != 2:
        raise PhaseTracerExpressionError(f"{name} requires exactly two arguments.")
    return f"{name}({args[0]}, {args[1]})"
