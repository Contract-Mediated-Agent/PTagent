"""Conservative, compile-time sign-symmetry evidence for a resolved 4D contract.

``prove_contract_sign_symmetries(contract, transforms)`` accepts sign vectors in
contract field order (the format returned by ``sign_flip_closure``). The JSON
report's ``proven_transforms`` contains only full static proofs; each ``results``
entry records its checks and reasons. An absent or unsuccessful proof must fall
back to the existing numerical check, never authorize an orbit match by itself.

This is not contract validation or user approval. Call it on the exact reviewed
contract being compiled. It proves algebraic invariance wherever expressions
are defined, not floating-point accuracy, finiteness, or trajectory equivalence.
No expressions are evaluated and no benchmark values or model names are used.
Unknown syntax, modes, symbols and calls fail closed. Dynamic counterterm fits
are deliberately unsupported. All derived quantities must themselves be even:
this also covers runtime scales and radiation counts without assuming that a
declared parameter is field independent.
"""
from __future__ import annotations

import ast
import keyword
import math
from numbers import Real
from typing import Any

from ..symmetry_analysis import _parse_compiler_block


# Bit sets of established parities. Identically zero admits either parity.
_UNKNOWN, _EVEN, _ODD, _ZERO = 0, 1, 2, 3
_UNARY_FUNCTIONS = {
    "abs", "arccos", "arcsin", "arctan", "cos", "cosh", "exp", "log",
    "sign", "sin", "sinh", "sqrt", "tan", "tanh", "any", "all",
}
_BINARY_FUNCTIONS = {"maximum", "minimum", "logical_and", "logical_or", "power"}
_BARE_FUNCTIONS = {"abs", "max", "min", "pow"}
# Compiler blocks share generated function locals. A mathematically even final
# expression cannot certify a block that overwrites an accumulator or spectrum.
_RESERVED = {
    "np", "T", "X", "self", "y", "r", "base", "total", "prefactor",
    "include_radiation", "mass_parts", "massSq", "bosons", "bosons_0",
    "bosons_T", "fermions", *_BARE_FUNCTIONS,
}
_GENERATED_PREFIXES = ("m2_", "m2T_", "M_", "M0_", "MT_", "eig0_", "eigT_", "daisy_")
_LOOP_MODES = {
    "zero_temperature": {"none", "standard_CW_V1", "paper_os_like_V1", "custom_expr"},
    "thermal": {"none", "standard_thermal_integrals", "custom_expr"},
    "daisy": {"none", "custom_expr"},
}
_DAISY_MARKERS = {
    "", "none", "not_applicable", "same_as_zeroT", "parwani_replacement_only",
    "covered_by_unified_V_daisy", "covered_by_custom_V_daisy",
}


class _Unproven(ValueError):
    pass


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Unproven(f"{path}: expected an object")
    return value


def _rows(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _Unproven(f"{path}: expected a list")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.isidentifier() or keyword.iskeyword(value):
        raise _Unproven(f"Invalid symbol {value!r}")
    if value in _RESERVED or value.startswith(_GENERATED_PREFIXES):
        raise _Unproven(f"Reserved symbol {value!r}")
    return value


def _literal_number(node: ast.AST) -> int | float | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _literal_number(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    return None


def _power_parity(base: int, exponent: int, exponent_node: ast.AST) -> int:
    value = _literal_number(exponent_node)
    if value is not None and float(value).is_integer():
        if base == _UNKNOWN or (base == _ZERO and value <= 0):
            return _UNKNOWN
        if base == _ZERO:
            return _ZERO
        return _EVEN if int(value) % 2 == 0 else base
    return _EVEN if base & _EVEN and exponent & _EVEN else _UNKNOWN


def _parity(node: ast.AST, symbols: dict[str, int]) -> int:
    """Strict scalar AST parity; unlike the tree heuristic, no implicit symbols."""
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float, bool) or not math.isfinite(node.value):
            raise _Unproven("Only finite numeric literals are supported")
        return _ZERO if node.value == 0 else _EVEN
    if isinstance(node, ast.Name):
        if node.id not in symbols:
            raise _Unproven(f"Unknown or forward-referenced symbol {node.id!r}")
        return symbols[node.id]
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "np" and node.attr == "pi":
            return _EVEN
        raise _Unproven("Only the numeric attribute np.pi is supported")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _parity(node.operand, symbols)
    if isinstance(node, ast.BinOp):
        left, right = _parity(node.left, symbols), _parity(node.right, symbols)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return left & right
        if isinstance(node.op, (ast.Mult, ast.Div)):
            if not left or not right or (isinstance(node.op, ast.Div) and right == _ZERO):
                return _UNKNOWN
            if left == _ZERO or (isinstance(node.op, ast.Mult) and right == _ZERO):
                return _ZERO
            return _EVEN if left == right else _ODD
        if isinstance(node.op, ast.Pow):
            return _power_parity(left, right, node.right)
        raise _Unproven(f"Unsupported operator {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _BARE_FUNCTIONS:
            name = node.func.id
        elif (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "np"
              and node.func.attr in _UNARY_FUNCTIONS | _BINARY_FUNCTIONS | {"where"}):
            name = node.func.attr
        else:
            raise _Unproven("Unknown call; only explicitly supported numeric functions are pure")
        if node.keywords:
            raise _Unproven("Call keywords are not supported by the static proof")
        arity = 3 if name == "where" else 2 if name in _BINARY_FUNCTIONS | {"pow", "min", "max"} else 1
        if len(node.args) != arity:
            raise _Unproven(f"Unsupported argument count for {name}")
        args = [_parity(arg, symbols) for arg in node.args]
        if name in {"power", "pow"}:
            return _power_parity(*args, node.args[1])
        if all(value & _EVEN for value in args):
            return _EVEN
        if len(args) == 1 and args[0]:
            if name in {"abs", "cos", "cosh"}:
                return _EVEN
            if name in {"sin", "sinh", "tan", "tanh", "arcsin", "arctan", "sign"}:
                return args[0]
        return _UNKNOWN
    if isinstance(node, ast.Compare):
        if not all(isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops):
            raise _Unproven("Unsupported comparison")
        values = [_parity(part, symbols) for part in [node.left, *node.comparators]]
        return _EVEN if all(value & _EVEN for value in values) else _UNKNOWN
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        values = [_parity(part, symbols) for part in node.values]
        return _EVEN if all(value & _EVEN for value in values) else _UNKNOWN
    if isinstance(node, ast.IfExp):
        condition = _parity(node.test, symbols)
        body, other = _parity(node.body, symbols), _parity(node.orelse, symbols)
        return body & other if condition & _EVEN else _UNKNOWN
    raise _Unproven(f"Unsupported syntax {type(node).__name__}")


def _expression_parity(expression: Any, symbols: dict[str, int]) -> int:
    if not isinstance(expression, (str, int, float)) or isinstance(expression, bool):
        raise _Unproven("Missing or invalid scalar compiler expression")
    assignments, result = _parse_compiler_block(str(expression))
    local = dict(symbols)
    for name, node in assignments.items():
        _name(name)
        if name in symbols:
            raise _Unproven(f"Temporary {name!r} shadows a field or parameter")
        local[name] = _parity(node, local)
    return _parity(result, local)


class _Proof:
    def __init__(self, contract: dict[str, Any], fields: list[str], signs: list[int]):
        self.contract = contract
        self.symbols = {name: _ODD if sign == -1 else _EVEN for name, sign in zip(fields, signs)}
        self.symbols["T"] = _EVEN
        self.checks: list[dict[str, Any]] = []

    def record(self, path: str, proven: bool, reason: str, **evidence: Any) -> None:
        self.checks.append({"path": path, "proven": proven, "reason": reason, **evidence})

    def scalar(self, path: str, expression: Any) -> int:
        try:
            parity = _expression_parity(expression, self.symbols)
        except (ValueError, SyntaxError, TypeError, RecursionError, OverflowError) as exc:
            self.record(path, False, str(exc))
            return _UNKNOWN
        self.record(path, bool(parity & _EVEN),
                    "Invariant scalar expression" if parity & _EVEN else
                    "Odd expression" if parity == _ODD else "Expression parity is unresolved")
        return parity

    def mode(self, path: str, value: Any, allowed: set[str]) -> None:
        self.record(path, isinstance(value, str) and value in allowed,
                    f"Implementation mode {value!r}" if isinstance(value, str) and value in allowed
                    else f"Unsupported or missing implementation mode {value!r}")

    def parameters(self) -> None:
        params = _mapping(self.contract.get("parameters", {}), "parameters")
        declared = set(self.symbols)
        for group in ("public_inputs", "constants", "derived"):
            for index, raw in enumerate(_rows(params.get(group, []), f"parameters.{group}")):
                path = f"parameters.{group}[{index}]"
                row = _mapping(raw, path)
                name = _name(row.get("name"))
                if name in declared:
                    raise _Unproven(f"{path}: duplicate symbol {name!r}")
                declared.add(name)
                if group == "public_inputs":
                    self.symbols[name] = _EVEN
                elif group == "constants":
                    try:
                        value = float(row.get("value"))
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise _Unproven(f"{path}: constant must be a finite number") from exc
                    if not math.isfinite(value):
                        raise _Unproven(f"{path}: constant must be a finite number")
                    # Keep constants symbolic: no special symmetry at a benchmark.
                    self.symbols[name] = _EVEN
                else:
                    self.symbols[name] = self.scalar(f"{path}.expr", row.get("expr"))

    def matrix(self, path: str, entries: Any, size: int) -> None:
        if (not isinstance(entries, list) or len(entries) != size or size == 0
                or any(not isinstance(row, list) or len(row) != size for row in entries)):
            self.record(path, False, "Expected a nonempty square matrix matching the declared basis")
            return
        edges: list[list[tuple[int, int]]] = [[] for _ in range(size)]
        odd = False
        for i, row in enumerate(entries):
            for j, expression in enumerate(row):
                try:
                    parity = _expression_parity(expression, self.symbols)
                except (ValueError, SyntaxError, TypeError, RecursionError, OverflowError) as exc:
                    self.record(f"{path}[{i}][{j}]", False, str(exc))
                    return
                if not parity or (i == j and not parity & _EVEN):
                    self.record(f"{path}[{i}][{j}]", False,
                                "Matrix entry has unresolved parity or a non-invariant diagonal")
                    return
                if parity == _ZERO:
                    continue
                bit = int(parity == _ODD)
                odd |= bool(bit)
                edges[i].append((j, bit))
                edges[j].append((i, bit))
        # M(S phi) = D M(phi) D requires d_i XOR d_j = entry parity.
        # Checking all edges, including cycles, establishes one fixed D.
        labels: dict[int, int] = {}
        for root in range(size):
            if root in labels:
                continue
            labels[root] = 0
            pending = [root]
            while pending:
                i = pending.pop()
                for j, bit in edges[i]:
                    expected = labels[i] ^ bit
                    if j in labels and labels[j] != expected:
                        self.record(path, False, "Inconsistent signed-similarity parity constraints")
                        return
                    if j not in labels:
                        labels[j] = expected
                        pending.append(j)
        self.record(path, True, "Spectrum invariant by signed similarity" if odd else
                    "All matrix entries invariant", matrix_signs=[(-1 if labels[i] else 1) for i in range(size)])

    def masses(self) -> None:
        masses = _mapping(self.contract.get("masses"), "masses")
        for group in ("bosons", "fermions", "boson_matrices", "fermion_matrices"):
            for index, raw in enumerate(_rows(masses.get(group, []), f"masses.{group}")):
                path = f"masses.{group}[{index}]"
                row = _mapping(raw, path)
                # Resolved mass lists are retained compiler inputs, even if a
                # hand-built row still carries an enabled=false annotation.
                if group.endswith("matrices"):
                    basis = _rows(row.get("basis"), f"{path}.basis")
                    self.matrix(f"{path}.matrix", row.get("matrix"), len(basis))
                    mode = row.get("thermal_matrix_mode", "none")
                    self.mode(f"{path}.thermal_matrix_mode", mode, {"none", "source_imported", "reviewed"})
                    thermal = row.get("thermal_matrix", [])
                    if mode != "none" or thermal:
                        # These are total matrices, not unevaluated self-energies.
                        # Zero-T and thermal spectra may have different witnesses.
                        self.matrix(f"{path}.thermal_matrix", thermal, len(basis))
                    keys = {"dof_per_eigenvalue": "1", "c": "1.5"}
                    for key in ("transverse_dof_per_eigenvalue", "longitudinal_dof_per_eigenvalue"):
                        if key in row:
                            keys[key] = "0"
                else:
                    self.scalar(f"{path}.mass_sq", row.get("mass_sq"))
                    keys = {"dof": "1", "c": "1.5"}
                for key, default in keys.items():
                    self.scalar(f"{path}.{key}", row.get(key, default))

    def run(self) -> None:
        self.parameters()
        potential = _mapping(self.contract.get("potential"), "potential")
        v0 = _mapping(potential.get("V0"), "potential.V0")
        self.scalar("potential.V0.python", v0.get("python"))
        # Also inspect the reviewed pieces, including explicit/static V_CT,
        # even when a generator folds them into another custom expression.
        for name, raw in _mapping(potential.get("pieces", {}), "potential.pieces").items():
            row = _mapping(raw, f"potential.pieces.{name}")
            self.scalar(f"potential.pieces.{name}.python", row.get("python"))
        loops = _mapping(self.contract.get("loops"), "loops")
        card = _mapping(self.contract.get("model_card", {}), "model_card")
        implementation = _mapping(self.contract.get("implementation", {}), "implementation")
        for name, allowed in _LOOP_MODES.items():
            row = _mapping(loops.get(name), f"loops.{name}")
            self.mode(f"loops.{name}.mode", row.get("mode"), allowed)
            if name in card and card[name] != row.get("mode"):
                self.record(f"model_card.{name}", False, "Model Card and compiled loop modes disagree")
            if row.get("mode") == "custom_expr" or row.get("custom_expr") not in (None, "", "0.0", "0", 0):
                self.scalar(f"loops.{name}.custom_expr", row.get("custom_expr"))
        ct = _mapping(implementation.get("counterterms", {}), "implementation.counterterms")
        ct_mode = card.get("counterterm", ct.get("mode"))
        self.mode("model_card.counterterm", ct_mode, {"none", "implicit_in_V1", "custom_expr"})
        if ct.get("mode", ct_mode) != ct_mode:
            self.record("implementation.counterterms.mode", False, "Counterterm modes disagree")
        if ct_mode == "explicit_linear_system" or ct.get("basis") or ct.get("conditions"):
            self.record("implementation.counterterms", False, "Dynamic counterterm systems are not statically proven")
        for key in ("custom_expr", "python", "expr"):
            if key in ct:
                self.scalar(f"implementation.counterterms.{key}", ct[key])
        if ct_mode == "custom_expr":
            pieces = potential.get("pieces", {})
            if "V_CT" not in pieces and not any(key in ct for key in ("custom_expr", "python", "expr")):
                self.record("implementation.counterterms", False, "Custom counterterm expression is missing")
        self.masses()
        daisy = _mapping(implementation.get("daisy", {}), "implementation.daisy")
        scheme = daisy.get("scheme", card.get("resummation_scheme", "none"))
        self.mode("implementation.daisy.scheme", scheme, {"none", "Parwani", "Arnold-Espinosa", "custom_expr"})
        self.mode("implementation.daisy.implementation", daisy.get("implementation", "none"),
                  {"none", "not_applicable", "parwani_thermal_masses", "arnold_espinosa_explicit_daisy", "custom_expr"})
        if "resummation_scheme" in card and card["resummation_scheme"] != scheme:
            self.record("model_card.resummation_scheme", False, "Resummation schemes disagree")
        if "cubic_power_policy" in daisy:
            self.mode("implementation.daisy.cubic_power_policy", daisy["cubic_power_policy"],
                      {"positive_part", "signed_abs", "none", "not_applicable", ""})
        if "custom_expr" in daisy:
            self.scalar("implementation.daisy.custom_expr", daisy["custom_expr"])
        # scalar_thermal_masses/gauge_thermal_masses are review prose, not
        # compiler expressions. Actual replacements are matrices/particle rows.
        for index, raw in enumerate(_rows(daisy.get("particles", []), "implementation.daisy.particles")):
            path = f"implementation.daisy.particles[{index}]"
            row = _mapping(raw, path)
            if str(row.get("enabled", "true")).strip().lower() == "false":
                continue
            if "implementation_owner" in row:
                self.mode(f"{path}.implementation_owner", row["implementation_owner"],
                          {"", "none", "not_applicable", "parwani_thermal_masses",
                           "arnold_espinosa_explicit_daisy", "custom_expr"})
            for key in ("zeroT_mass_sq", "thermal_mass_sq", "daisy_term"):
                expr = row.get(key, "")
                if isinstance(expr, str) and expr.strip() in _DAISY_MARKERS:
                    continue
                self.scalar(f"{path}.{key}", expr)
            self.scalar(f"{path}.dof", row.get("dof", "1"))
        goldstone = _mapping(implementation.get("goldstone", {}), "implementation.goldstone")
        if goldstone.get("replacement_formula"):
            self.scalar("implementation.goldstone.replacement_formula", goldstone["replacement_formula"])
        for index, raw in enumerate(_rows(goldstone.get("species", []), "implementation.goldstone.species")):
            path = f"implementation.goldstone.species[{index}]"
            row = _mapping(raw, path)
            for key in ("mass_sq", "dof", "replacement_expr"):
                if row.get(key) not in (None, "", "none", "not_applicable"):
                    self.scalar(f"{path}.{key}", row[key])


def prove_contract_sign_symmetries(
    contract: dict[str, Any], transforms: list[list[int]],
) -> dict[str, Any]:
    """Return proven sign vectors and per-transform evidence, without mutation.

    Only ``proven_transforms`` may bypass the numerical full-potential symmetry
    residual. Unproven candidates are not disproven symmetries: leave them to
    the existing numerical fallback. This routine never changes tolerances.
    """
    report: dict[str, Any] = {
        "schema": "ptagent.contract_sign_symmetries.v1", "status": "unproven",
        "field_order": [], "proven_transforms": [], "results": [], "reasons": [],
    }
    try:
        _mapping(contract, "contract")
        if contract.get("schema") != "ptagent.contract.template.v1":
            raise _Unproven("Unsupported or missing contract schema")
        fields = [_name(_mapping(row, "fields entry").get("name"))
                  for row in _rows(contract.get("fields"), "fields")]
        if not fields or len(set(fields)) != len(fields):
            raise _Unproven("Background fields must be nonempty and unique")
        report["field_order"] = fields
        if not isinstance(transforms, (list, tuple)):
            raise _Unproven("Transforms must be a sequence of sign vectors in field order")
    except (ValueError, TypeError) as exc:
        report["reasons"].append(str(exc))
        return report
    for transform in transforms:
        proof = None
        entry: dict[str, Any] = {"transform": None, "proven": False, "checks": [], "reasons": []}
        try:
            if (not isinstance(transform, (list, tuple)) or len(transform) != len(fields)
                    or any(isinstance(sign, bool) or not isinstance(sign, Real) or sign not in (-1, 1)
                           for sign in transform)):
                raise _Unproven("Invalid sign vector; expected one +1 or -1 per field")
            signs = [int(sign) for sign in transform]
            entry["transform"] = signs
            proof = _Proof(contract, fields, signs)
            proof.run()
        except (ValueError, SyntaxError, TypeError, RecursionError, OverflowError) as exc:
            entry["reasons"].append(str(exc))
        if proof is not None:
            entry["checks"] = proof.checks
            entry["reasons"].extend(f"{check['path']}: {check['reason']}"
                                    for check in proof.checks if not check["proven"])
        entry["proven"] = not entry["reasons"]
        if entry["proven"]:
            entry["reasons"] = ["All retained full-potential contributions are statically invariant"]
            if entry["transform"] not in report["proven_transforms"]:
                report["proven_transforms"].append(entry["transform"])
        report["results"].append(entry)
    if report["results"] and all(entry["proven"] for entry in report["results"]):
        report["status"] = "proven"
    elif report["proven_transforms"]:
        report["status"] = "partial"
    report["reasons"] = ["Only listed transforms have a static proof; all others require numerical fallback"]
    return report
