"""Evaluate source-exported tree relations; no model potentials or fits live here."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .safe_expression import parse_safe_expression


class TreeBindingError(ValueError):
    pass


class MissingSourceValue(TreeBindingError):
    pass


def _numeric(value: Any) -> np.ndarray:
    try:
        if any(isinstance(v, (bool, np.bool_)) for v in np.asarray(value, dtype=object).flat):
            raise TreeBindingError("Boolean parameter values are not numerical inputs.")
        a = np.asarray(value)
        if a.dtype.kind not in "iuf" or not 1 <= a.size <= 64 or a.ndim > 2:
            raise TreeBindingError("Only bounded real scalar/vector/matrix inputs are supported.")
        a = a.astype(float)
    except (ValueError, TypeError) as exc:
        raise TreeBindingError("Invalid numerical source input.") from exc
    if not np.all(np.isfinite(a)):
        raise TreeBindingError("Source inputs and computed relations must be finite.")
    return a


def evaluate_relation(node: dict, values: dict, *, _depth: int = 0) -> np.ndarray:
    """Interpret a bounded data AST. Never eval/exec a Wolfram/Python string."""
    if _depth > 40 or not isinstance(node, dict):
        raise TreeBindingError("Invalid or excessively nested relation AST.")
    op = node.get("op")
    if op == "number":
        return _numeric(node.get("value"))
    if op == "symbol":
        name = node.get("name")
        if not isinstance(name, str):
            raise TreeBindingError("Invalid symbol name.")
        if name == "Pi":
            return _numeric(math.pi)
        if name not in values:
            raise MissingSourceValue(name)
        return _numeric(values[name])
    args = node.get("args")
    if not isinstance(args, list) or not 1 <= len(args) <= 64:
        raise TreeBindingError("Invalid operation arguments.")
    a = [evaluate_relation(n, values, _depth=_depth + 1) for n in args]
    with np.errstate(all="raise"):
        try:
            if op == "array":
                result = np.stack(a)
            elif op in {"add", "mul"}:
                # Scalar factors may multiply tensors; tensor products need an explicit convention.
                if op == "mul" and sum(x.ndim > 0 for x in a) > 1:
                    raise TreeBindingError("Ambiguous tensor multiplication is unsupported.")
                shapes = {x.shape for x in a if x.ndim > 0}
                if len(shapes) > 1:
                    raise TreeBindingError("Incompatible tensor shapes.")
                result = a[0]
                for x in a[1:]:
                    result = result + x if op == "add" else result * x
            elif op == "pow" and len(a) == 2 and a[1].ndim == 0:
                if abs(float(a[1])) > 64:
                    raise TreeBindingError("Power exponent exceeds the bounded grammar.")
                result = np.power(a[0], a[1])
            elif op in {"sin", "cos", "tan", "atan", "sqrt", "exp", "log", "abs", "conj"} and len(a) == 1:
                functions = {"sin": np.sin, "cos": np.cos, "tan": np.tan, "atan": np.arctan,
                             "sqrt": np.sqrt, "exp": np.exp, "log": np.log, "abs": np.abs,
                             "conj": np.conjugate}
                result = functions[op](a[0])
            else:
                raise TreeBindingError(f"Unsupported relation operation: {op!r}")
        except TreeBindingError:
            raise
        except (FloatingPointError, ValueError, OverflowError) as exc:
            raise TreeBindingError(f"Non-real, singular or invalid source relation ({op}).") from exc
    return _numeric(result)


def _aliases(binding: dict) -> tuple[list[int], list[str]]:
    shape = binding.get("shape")
    if not isinstance(shape, list) or len(shape) > 2 or any(type(x) is not int or x <= 0 for x in shape):
        raise TreeBindingError("Invalid source parameter shape.")
    if math.prod(shape) > 64:
        raise TreeBindingError("Source parameter shape exceeds the limit.")
    raw = np.asarray(binding.get("aliases"), dtype=object)
    if list(raw.shape) != shape:
        raise TreeBindingError("Source parameter aliases do not match the declared shape.")
    names = []
    from .sarah_import import _normalize_expression
    for text in raw.flat:
        if not isinstance(text, str):
            raise TreeBindingError("Invalid target alias.")
        parsed = parse_safe_expression(_normalize_expression(text, {}))
        if len(parsed.names) != 1 or parsed.python != parsed.names[0]:
            raise TreeBindingError("Target aliases must be individual parameter names.")
        names.append(parsed.python)
    return shape, names


def _names(node: Any) -> set[str]:
    names, stack, count = set(), [(node, 0)], 0
    while stack:
        current, depth = stack.pop()
        count += 1
        if count > 4096 or depth > 40 or not isinstance(current, dict):
            raise TreeBindingError("Invalid or oversized relation AST.")
        if current.get("op") == "symbol":
            if not isinstance(current.get("name"), str):
                raise TreeBindingError("Invalid source symbol.")
            names.add(current["name"])
        elif current.get("op") != "number":
            args = current.get("args")
            if not isinstance(args, list) or len(args) > 64:
                raise TreeBindingError("Invalid relation arguments.")
            stack.extend((x, depth + 1) for x in args)
    return names


def resolve_tree_bindings(metadata: dict, source_values: dict, *, existing: dict | None = None,
                          branch: int | None = None, loop_policy: str = "unresolved") -> dict:
    """Return bindings plus proof. Missing loop data remains missing by default.

    `existing` is already resolved explicit/SLHA data and takes precedence. Source
    example defaults are never applied implicitly. `equal_tree` is an explicit
    numerical prescription, not a calculation of loop-corrected parameters.
    """
    if metadata.get("schema") != "ptagent.sarah_tree_relations.v1" or metadata.get("complete") is not True:
        raise TreeBindingError("Complete native SARAH tree-relation metadata is required.")
    if loop_policy not in {"unresolved", "equal_tree"}:
        raise TreeBindingError("Unsupported tree/loop parameter prescription.")
    solutions = metadata.get("tree_solutions")
    if not isinstance(solutions, list) or not 1 <= len(solutions) <= 16:
        raise TreeBindingError("No explicit native SARAH tree solution is available.")
    if branch is None:
        if len(solutions) != 1:
            raise TreeBindingError("Multiple source tadpole branches require an explicit branch index.")
        branch = 0
    if type(branch) is not int or not 0 <= branch < len(solutions):
        raise TreeBindingError("Invalid tadpole branch index.")
    equations = metadata.get("tadpole_equations")
    targets = metadata.get("tadpole_parameters")
    selected = solutions[branch]
    if not isinstance(targets, list) or set(selected) != set(targets) or not isinstance(equations, list) or len(equations) != len(targets):
        raise TreeBindingError("Tadpole solution/equation coverage is inconsistent.")
    bindings = metadata.get("bindings", [])
    alias_rows = {b["source_symbol"]: _aliases(b) for b in bindings}
    if len(alias_rows) != len(bindings):
        raise TreeBindingError("Duplicate source parameter binding.")
    flat_aliases = [n for _, names in alias_rows.values() for n in names]
    if len(set(flat_aliases)) != len(flat_aliases):
        raise TreeBindingError("Overlapping target parameter aliases.")
    relations = metadata.get("relations", [])
    pending = {r["target"]: r["expression"] for r in relations}
    if len(pending) != len(relations) or set(pending) & set(targets):
        raise TreeBindingError("Duplicate or conflicting source relations.")
    known = set(alias_rows) | set().union(*(_names(n) for n in [*pending.values(), *selected.values()]))
    if not isinstance(source_values, dict):
        raise TreeBindingError("Source inputs must be a JSON object.")
    if set(source_values) - known:
        raise TreeBindingError("Unknown source input names: " + ", ".join(sorted(set(source_values) - known)))
    existing = {k: float(_numeric(v)) for k, v in (existing or {}).items()}
    values = {k: _numeric(v) for k, v in source_values.items()}
    anchored = set()
    partial = {}
    for name, (shape, aliases) in alias_rows.items():
        present = [n in existing for n in aliases]
        if all(present):
            values[name] = np.array([existing[n] for n in aliases]).reshape(shape)
            anchored.add(name)
        elif any(present):
            partial[name] = {j: existing[n] for j, n in enumerate(aliases) if n in existing}
    pending.update(selected)
    applied = []
    unresolved = {}
    while pending:
        progress = False
        for target, expression in list(pending.items()):
            if target in anchored and target not in targets:
                del pending[target]
                progress = True
                continue
            try:
                result = evaluate_relation(expression, values)
            except MissingSourceValue as exc:
                unresolved[target] = str(exc)
                continue
            if target in alias_rows and list(result.shape) != alias_rows[target][0]:
                raise TreeBindingError(f"Computed shape mismatch for {target}.")
            if target in anchored:
                # Never override a reviewed explicit/SLHA binding with a source relation.
                if target in targets and not np.allclose(values[target], result, rtol=1e-10, atol=1e-10):
                    raise TreeBindingError(f"Explicit tadpole parameter {target} conflicts with the selected source solution.")
            elif target in values and not np.allclose(values[target], result, rtol=1e-10, atol=1e-10):
                raise TreeBindingError(f"Source input {target} conflicts with its derived relation.")
            else:
                if target in partial:
                    for j, v in partial[target].items():
                        result.flat[j] = v
                values[target] = result
            applied.append(target)
            del pending[target]
            unresolved.pop(target, None)
            progress = True
        if not progress:
            break
    # Remaining relations are reported, never filled from source example values.
    emitted = {}
    for name, (shape, aliases) in alias_rows.items():
        if name not in values:
            continue
        value = _numeric(values[name])
        if list(value.shape) != shape:
            raise TreeBindingError(f"Source input shape mismatch for {name}.")
        for alias, scalar in zip(aliases, value.flat):
            emitted[alias] = float(scalar)
    equalities = []
    if loop_policy == "equal_tree":
        for name in targets:
            if name not in alias_rows:
                raise TreeBindingError("Missing alias for a tadpole parameter.")
            _, aliases = alias_rows[name]
            for tree_alias in aliases:
                if not tree_alias.endswith("Tree"):
                    raise TreeBindingError("No explicit Tree/Loop alias pair in native export.")
                loop_alias = tree_alias[:-4] + "Loop"
                if tree_alias in emitted:
                    if loop_alias not in existing:
                        emitted[loop_alias] = emitted[tree_alias]
                        equalities.append({"tree": tree_alias, "loop": loop_alias, "prescription": "explicit_equal_tree"})
    emitted.update(existing)
    residuals = []
    if all(name in values and name not in pending for name in targets):
        for equation in equations:
            try:
                residuals.append(float(evaluate_relation(equation, values)))
            except MissingSourceValue:
                residuals = []
                break
        # Relative to the sum of source equation terms, rather than an invented mass scale.
        for equation, residual in zip(equations, residuals):
            terms = equation.get("args", []) if equation.get("op") == "add" else [equation]
            scale = max(1.0, sum(abs(float(evaluate_relation(t, values))) for t in terms))
            if abs(residual) > 1e-10 * scale:
                raise TreeBindingError("Source tadpole residual exceeds the relative tolerance.")
    dependency_graph = {name: _names(expr) & pending.keys() for name, expr in pending.items()}
    cyclic = []
    for name in dependency_graph:
        queue, seen = list(dependency_graph[name]), set()
        while queue:
            current = queue.pop()
            if current == name:
                cyclic.append(name)
                break
            if current not in seen:
                seen.add(current)
                queue.extend(dependency_graph.get(current, ()))
    missing_inputs = sorted(set().union(*(_names(expr) for expr in pending.values()))
                            - set(pending) - set(values) - {"Pi"})
    return {"schema": "ptagent.sarah_tree_binding.v1", "bindings": emitted,
            "source_values": {k: v.tolist() for k, v in values.items()}, "applied_relations": applied,
            "unresolved_relations": unresolved, "selected_branch": branch,
            "cyclic_relations": sorted(cyclic),
            "missing_source_inputs": missing_inputs,
            "tadpole_residuals": residuals, "loop_parameter_policy": loop_policy,
            "explicit_equalities": equalities, "loop_tadpoles_evaluated": False,
            "review_approved": False}


def load_tree_metadata(path: str | Path, declaration_source: Path | None = None) -> dict:
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise TreeBindingError("Tree-relation metadata exceeds the 2 MiB limit.")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema") != "ptagent.sarah_tree_relations.v1":
        raise TreeBindingError("Expected native SARAH tree-relation metadata object.")
    if declaration_source:
        expected = data.get("declaration_source", {}).get("sha256")
        observed = hashlib.sha256(declaration_source.read_bytes()).hexdigest()
        if not expected or expected != observed:
            raise TreeBindingError("Tree-relation metadata does not match its source declaration file.")
    return data


def tree_review_summary(metadata: dict, source_values: dict, existing: dict,
                        benchmark: dict, *, branch: int | None = None) -> dict:
    """Preview defaults without applying them to the prepared physics contract."""
    defaults = benchmark.get("default_inputs", {})
    candidate = {**defaults, **source_values}
    summary = {"status": "pending_review", "source_defaults": defaults,
               "applied": False, "loop_policy_recommendation": "equal_tree",
               "loop_policy_meaning": "Tree input matching only; no numerical loop tadpole shifts. CW and thermal loops are retained."}
    if metadata.get("complete") is not True:
        return {**summary, "status": "unavailable", "blockers": metadata.get("blockers", [])}
    aliases = [alias for row in metadata.get("bindings", []) for alias in _aliases(row)[1]]
    summary["supported_aliases"] = sorted(set(aliases + [a[:-4] + "Loop" for a in aliases if a.endswith("Tree")]))
    if branch is None and len(metadata.get("tree_solutions", [])) > 1:
        return {**summary, "status": "branch_required", "branch_count": len(metadata["tree_solutions"])}
    try:
        preview = resolve_tree_bindings(metadata, candidate, existing=existing, branch=branch)
    except TreeBindingError as exc:
        return {**summary, "status": "blocked", "blockers": [str(exc)]}
    return {**summary, "missing_source_inputs": preview["missing_source_inputs"],
            "cyclic_relations": preview["cyclic_relations"],
            "derived_targets": preview["applied_relations"],
            "selected_branch": preview["selected_branch"]}
