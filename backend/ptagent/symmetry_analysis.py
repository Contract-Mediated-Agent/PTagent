from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SignFlipCandidate:
    fields: tuple[str, ...]
    transformation: str = "sign_flip"
    evidence: str = "tree_level_expression"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": list(self.fields),
            "transformation": self.transformation,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class SignFlipAnalysis:
    status: str
    candidates: tuple[SignFlipCandidate, ...]
    inspected_fields: tuple[str, ...]
    reason: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "inspected_fields": list(self.inspected_fields),
            "reason": self.reason,
            "truncated": self.truncated,
        }


def analyze_sign_flip_symmetries(
    expression: str,
    field_names: list[str] | tuple[str, ...],
    *,
    max_exhaustive_fields: int = 10,
) -> SignFlipAnalysis:
    """Find a conservative generating set of tree-level sign-flip candidates.

    The expression is parsed but never evaluated. A candidate is returned only
    when every supported AST branch has a definite even parity under the same
    field reflection. Unknown constructs produce false negatives, not guessed
    symmetries.
    """

    fields = tuple(dict.fromkeys(str(name).strip() for name in field_names if str(name).strip()))
    try:
        assignments, result = _parse_compiler_block(expression)
    except (SyntaxError, ValueError) as exc:
        return SignFlipAnalysis(
            status="inconclusive",
            candidates=(),
            inspected_fields=fields,
            reason=f"Tree-level expression could not be parsed for a conservative parity check: {exc}",
        )

    referenced = _referenced_fields(assignments, result, fields)
    if not referenced:
        return SignFlipAnalysis(
            status="inconclusive",
            candidates=(),
            inspected_fields=(),
            reason="No declared background field appears in the tree-level expression.",
        )

    truncated = len(referenced) > max_exhaustive_fields
    subset_sizes = (1,) if truncated else range(1, len(referenced) + 1)
    basis: dict[int, int] = {}
    candidates: list[SignFlipCandidate] = []
    field_positions = {name: index for index, name in enumerate(referenced)}

    for size in subset_sizes:
        for group in itertools.combinations(referenced, size):
            flipped = frozenset(group)
            if _node_parity(result, flipped, assignments, set()) != 0:
                continue
            vector = sum(1 << field_positions[name] for name in group)
            if _add_independent_vector(vector, basis):
                candidates.append(SignFlipCandidate(fields=tuple(group)))

    if candidates:
        reason = (
            "The tree-level expression is invariant under the listed independent sign-flip generators "
            "by a conservative AST parity check. Loop, thermal, counterterm, gauge-fixing, and source "
            "conventions still require review."
        )
        status = "candidates_found"
    else:
        reason = (
            "No sign-flip generator was proven from the supported tree-level expression. This does not "
            "prove that the full model has no discrete symmetry."
        )
        status = "no_candidate"
    if truncated:
        reason += " More than ten fields were present, so only single-field reflections were checked."
    return SignFlipAnalysis(
        status=status,
        candidates=tuple(candidates),
        inspected_fields=referenced,
        reason=reason,
        truncated=truncated,
    )


def format_sign_flip_generators(analysis: SignFlipAnalysis) -> str:
    if not analysis.candidates:
        return "none"
    return "; ".join(
        ",".join(candidate.fields) if len(candidate.fields) > 1 else candidate.fields[0]
        for candidate in analysis.candidates
    )


def _parse_compiler_block(expression: str) -> tuple[dict[str, ast.expr], ast.expr]:
    text = str(expression or "").strip()
    if not text:
        raise ValueError("expression is empty")
    try:
        return {}, ast.parse(text, mode="eval").body
    except SyntaxError as eval_error:
        try:
            module = ast.parse(text, mode="exec")
        except SyntaxError:
            raise ValueError(eval_error.msg) from eval_error
    if not module.body:
        raise ValueError("expression block is empty")
    final = module.body[-1]
    statements = module.body[:-1]
    if isinstance(final, ast.Assign):
        statements = module.body
        if len(final.targets) != 1 or not isinstance(final.targets[0], ast.Name):
            raise ValueError("final assignment must have one simple name target")
        result: ast.expr = ast.Name(id=final.targets[0].id, ctx=ast.Load())
    elif isinstance(final, ast.Expr):
        result = final.value
    else:
        raise ValueError("expression block must end with an expression or simple assignment")
    assignments: dict[str, ast.expr] = {}
    for statement in statements:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise ValueError("expression block contains a non-simple assignment")
        name = statement.targets[0].id
        if name in assignments:
            raise ValueError(f"temporary {name!r} is assigned more than once")
        assignments[name] = statement.value
    return assignments, result


def _referenced_fields(
    assignments: dict[str, ast.expr],
    result: ast.expr,
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    names = {node.id for node in ast.walk(result) if isinstance(node, ast.Name)}
    pending = list(names & assignments.keys())
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        nested = {node.id for node in ast.walk(assignments[name]) if isinstance(node, ast.Name)}
        names.update(nested)
        pending.extend(nested & assignments.keys() - visited)
    return tuple(name for name in fields if name in names)


def _node_parity(
    node: ast.AST,
    flipped: frozenset[str],
    assignments: dict[str, ast.expr],
    resolving: set[str],
) -> int | None:
    if isinstance(node, ast.Constant):
        return 0
    if isinstance(node, ast.Name):
        if node.id in flipped:
            return 1
        if node.id in assignments:
            if node.id in resolving:
                return None
            return _node_parity(assignments[node.id], flipped, assignments, resolving | {node.id})
        return 0
    if isinstance(node, ast.UnaryOp):
        return _node_parity(node.operand, flipped, assignments, resolving)
    if isinstance(node, ast.BinOp):
        left = _node_parity(node.left, flipped, assignments, resolving)
        right = _node_parity(node.right, flipped, assignments, resolving)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return left if left is not None and left == right else None
        if isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv)):
            return None if left is None or right is None else (left + right) % 2
        if isinstance(node.op, ast.Pow):
            exponent = _literal_integer(node.right)
            if exponent is None:
                return 0 if not _contains_flipped_field(node, flipped, assignments) else None
            return None if left is None else (left * exponent) % 2
        return None
    if isinstance(node, ast.IfExp):
        test = _node_parity(node.test, flipped, assignments, resolving)
        body = _node_parity(node.body, flipped, assignments, resolving)
        other = _node_parity(node.orelse, flipped, assignments, resolving)
        return body if test == 0 and body is not None and body == other else None
    if isinstance(node, ast.Call):
        parities = [
            _node_parity(argument, flipped, assignments, resolving)
            for argument in node.args
        ]
        parities.extend(
            _node_parity(keyword.value, flipped, assignments, resolving)
            for keyword in node.keywords
        )
        if parities and all(parity == 0 for parity in parities):
            return 0
        function_name = _call_name(node.func)
        if len(parities) == 1 and parities[0] is not None:
            if function_name in {"abs", "fabs", "cos", "cosh"}:
                return 0
            if function_name in {"sin", "sinh", "tan", "tanh"}:
                return parities[0]
        return 0 if not _contains_flipped_field(node, flipped, assignments) else None
    if isinstance(node, (ast.Attribute, ast.Subscript)):
        return 0 if not _contains_flipped_field(node, flipped, assignments) else None
    return 0 if not _contains_flipped_field(node, flipped, assignments) else None


def _contains_flipped_field(
    node: ast.AST,
    flipped: frozenset[str],
    assignments: dict[str, ast.expr],
    seen: frozenset[str] = frozenset(),
) -> bool:
    names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
    if names & flipped:
        return True
    return any(
        name in assignments
        and name not in seen
        and _contains_flipped_field(assignments[name], flipped, assignments, seen | {name})
        for name in names
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _literal_integer(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        value = float(node.value)
        return int(value) if value.is_integer() else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_integer(node.operand)
        return -value if value is not None else None
    return None


def _add_independent_vector(vector: int, basis: dict[int, int]) -> bool:
    reduced = vector
    while reduced:
        pivot = reduced.bit_length() - 1
        if pivot not in basis:
            basis[pivot] = reduced
            return True
        reduced ^= basis[pivot]
    return False
