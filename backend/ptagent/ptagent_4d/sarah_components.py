"""Model-independent contractions of canonical SARAH component data."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sympy as sp

from .safe_expression import parse_safe_expression, validate_identifier
from .sarah_import import MAX_JSON_BYTES, SarahImportError, SarahImportedModel, _normalize_expression


COMPONENT_SCHEMA = "ptagent.sarah_components.v1"


def derive_component_metadata(data: dict[str, Any], model: SarahImportedModel) -> dict[str, Any]:
    """Contract full real scalar and Weyl data, then check the .vin projection.

    For canonical real q and left-handed Weyl mass M(q), the leading high-T
    scalar correction is Hessian[Tr(H_V)/24 + Tr(M M^dagger)/24
    + Tr(M_vector^2)/8]. No model/field name selects a formula.
    """
    if data.get("schema") != COMPONENT_SCHEMA:
        raise SarahImportError("Unsupported SARAH component schema.")
    metadata: dict[str, Any] = {
        "schema": "ptagent.sarah_thermal.v1", "complete": False,
        "background_fields": model.fields, "scalar_basis": [], "gauge_basis": [],
        "sector_mappings": {"scalar": [], "gauge": []},
        "provenance": {"exporter_version": "ptagent-sarah-components-1", "source_hashes": {
            key: model.provenance[key]["sha256"] for key in ("vin", "parameter_map")}},
    }
    if data.get("complete") is not True:
        metadata["blockers"] = data.get("blockers") or ["sarah_component_extraction_incomplete"]
        return metadata
    if data.get("background_fields") != model.fields:
        raise SarahImportError("Component backgrounds differ from Vevacious++ FieldVariables.")
    names = data.get("scalar_basis")
    if not isinstance(names, list) or not 0 < len(names) <= 64 or len(set(names)) != len(names):
        raise SarahImportError("Expected 1..64 unique canonical real scalar components.")
    for name in names:
        validate_identifier(name, subject="scalar component")
    if set(names) & (set(model.unresolved_parameters) | set(model.parameter_values) | set(model.fields)):
        raise SarahImportError("Component names collide with source parameters/backgrounds.")
    q = [sp.Symbol(name) for name in names]
    fields = [sp.Symbol(name) for name in model.fields]
    indices = data.get("background_scalar_indices")
    if (not isinstance(indices, list) or len(indices) != len(fields) or len(set(indices)) != len(indices)
            or any(type(i) is not int or not 0 <= i < len(q) for i in indices)):
        raise SarahImportError("Invalid canonical background scalar indices.")
    background = dict.fromkeys(q, sp.Integer(0))
    background.update({q[index]: field for index, field in zip(indices, fields)})
    references = dict(model.block_references)
    known = set(model.parameter_values) | set(model.unresolved_parameters) | set(model.derived_parameters)

    def expression(text: Any) -> sp.Expr:
        normalized = _normalize_expression(str(text), references)
        parsed = parse_safe_expression(normalized)
        unknown = set(parsed.names) - known - set(references) - set(names) - set(model.fields) - {"pi"}
        if unknown:
            raise SarahImportError("Unresolved component symbols: " + ", ".join(sorted(unknown)))
        return sp.sympify(parsed.python, locals={n: sp.Symbol(n) for n in parsed.names}, rational=True)

    def matrix(value: Any, size: int) -> sp.Matrix:
        if not isinstance(value, list) or len(value) != size or any(not isinstance(row, list) or len(row) != size for row in value):
            raise SarahImportError("Invalid component matrix dimensions.")
        result = sp.Matrix([[expression(x) for x in row] for row in value]) if size else sp.zeros(0)
        if not _zero(result-result.T):
            raise SarahImportError("Component matrix is not symmetric.")
        return result

    potential = expression(data["full_scalar_potential"])
    trace_f = expression(data["weyl_mass_squared_trace"])
    if (potential.free_symbols | trace_f.free_symbols) & set(fields):
        raise SarahImportError("Component interactions must use the full canonical basis, not background fields.")
    try:
        if sp.Poly(potential, *q).total_degree() > 4 or sp.Poly(trace_f, *q).total_degree() > 2:
            raise SarahImportError("Nonrenormalizable component interactions are not supported.")
    except sp.PolynomialError as exc:
        raise SarahImportError("Component interactions must be polynomial in canonical fields.") from exc
    hessian = sp.hessian(potential, q)
    quartic = sp.hessian(sp.trace(hessian), q)
    scalar_gauge = matrix(data["scalar_gauge"], len(q))
    yukawa = sp.hessian(trace_f, q)/2
    scalar_pi = (quartic/24 + scalar_gauge/4 + yukawa/12).applyfunc(sp.simplify)
    if any(entry.free_symbols & (set(q) | set(fields)) for entry in scalar_pi):
        raise SarahImportError("Leading high-T self energies must be background independent.")
    if not _source_equal(potential.subs(background), expression(model.tree_potential)):
        raise SarahImportError("Full component potential does not reproduce the imported tree potential.")
    mappings: dict[str, list[dict[str, Any]]] = {"scalar": [], "gauge": []}
    gauge_names = data.get("gauge_basis", [])
    if not isinstance(gauge_names, list) or len(gauge_names) > 128 or len(set(gauge_names)) != len(gauge_names):
        raise SarahImportError("Invalid gauge basis.")
    for kind, size in (("scalar", len(q)), ("gauge", len(gauge_names))):
        for row in data.get("sector_mappings", {}).get(kind, []):
            source_index = row.get("source_index")
            if type(source_index) is not int or not 1 <= source_index <= len(model.mass_sectors):
                raise SarahImportError("Invalid source sector index.")
            sector = model.mass_sectors[source_index-1]
            positions = row.get("indices", [])
            if (sector.kind != kind or len(positions) != len(sector.matrix)
                    or len(set(positions)) != len(positions)
                    or any(type(i) is not int or not 0 <= i < size for i in positions)):
                raise SarahImportError("Component sector/basis alignment failed: " + sector.name)
            if kind == "scalar":
                expected = hessian.extract(positions, positions).subs(background)
                if not _source_equal(expected, matrix([list(r) for r in sector.matrix], len(positions))):
                    raise SarahImportError("Component Hessian differs from source matrix: " + sector.name)
            mappings[kind].append({"sector": sector.name, "indices": positions})
        if {r["sector"] for r in mappings[kind]} != {s.name for s in model.mass_sectors if s.kind == kind}:
            raise SarahImportError("Missing or unmatched " + kind + " sector mappings.")
        positions = [i for row in mappings[kind] for i in row["indices"]]
        if sorted(positions) != list(range(size)):
            raise SarahImportError("Component mappings must cover the basis exactly once.")
    # Existing backends preserve source blocks; never discard newly induced mixing.
    for row in mappings["scalar"]:
        rest = sorted(set(range(len(q)))-set(row["indices"]))
        if not _zero(hessian.extract(row["indices"], rest).subs(background)):
            raise SarahImportError("Scalar Hessian mixing crosses source sectors; a merged mass sector is required.")
        if not _zero(scalar_pi.extract(row["indices"], rest)):
            raise SarahImportError("Thermal mixing crosses source sectors; a merged mass sector is required.")
    trace_imported_f = sum(
        sp.Rational(str(sec.dof_per_eigenvalue))/2 * sp.trace(matrix([list(r) for r in sec.matrix], len(sec.matrix)))
        for sec in model.mass_sectors if sec.kind == "fermion")
    if not _source_equal(trace_f.subs(background), trace_imported_f):
        raise SarahImportError("Full Weyl trace differs from the imported fermion spectrum.")
    gauge = [matrix(data[key], len(gauge_names)) for key in
             ("gauge_adjoint", "gauge_scalar_trace", "gauge_fermion_trace")]
    if any(entry.free_symbols & (set(q) | set(fields)) for part in gauge for entry in part):
        raise SarahImportError("Leading high-T gauge self energies must be background independent.")
    # Independent background check uses the imported .vin spectrum, including dof.
    high_t = sum(sp.Rational(str(sec.dof_per_eigenvalue)) *
                 sp.trace(matrix([list(r) for r in sec.matrix], len(sec.matrix))) /
                 (48 if sec.kind == "fermion" else 24) for sec in model.mass_sectors)
    if not _source_equal(sp.hessian(high_t, fields), scalar_pi.extract(indices, indices)):
        raise SarahImportError("Component thermal contractions fail the .vin high-T supertrace check.")
    metadata.update({
        "complete": True, "blockers": [], "scalar_basis": names, "gauge_basis": gauge_names,
        "scalar_components": [{"name": name} for name in names],
        "fermion_components": [{"name": name} for name in data.get("fermion_components", [])],
        "gauge_groups": data.get("gauge_groups", []),
        "representation_data": {"gauge_groups": data.get("gauge_groups", [])},
        "coupling_tensors": {"full_scalar_potential": str(potential), "weyl_mass_squared_trace": str(trace_f)},
        "basis_mappings": {"background_scalar_indices": indices, "source_scalar_bases": data.get("scalar_source_bases", [])},
        "conventions": {"scalar": "canonical real", "fermion": "left-handed Weyl",
                        "approximation": "leading high-T; complete source matter is relativistic"},
        "discrete_symmetries": [], "sector_mappings": mappings,
        "parameter_references": {key: value for key, value in references.items() if key not in model.block_references},
        "thermal_invariants": dict(zip(
            ("scalar_quartic", "scalar_gauge", "scalar_yukawa", "gauge_adjoint", "gauge_scalar_trace", "gauge_fermion_trace"),
            map(_matrix_strings, (quartic, scalar_gauge, yukawa, *gauge)))),
        "consistency": {"scalar_pi_over_t2": _matrix_strings(scalar_pi), "background_scalar_indices": indices,
                        "source_tree_matches": True, "source_scalar_matrices_match": True,
                        "source_fermion_trace_matches": True, "source_high_t_supertrace_matches": True,
                        "source_coefficient_rtol": 1e-12,
                        "unmixed_scalar_sectors": data.get("unmixed_scalar_sectors", [])},
    })
    return metadata


def complete_component_export(path: Path, model: SarahImportedModel) -> dict[str, Any]:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise SarahImportError("SARAH component export exceeds the JSON safety limit.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SarahImportError("SARAH component data must be a JSON object.")
    try:
        metadata = derive_component_metadata(data, model)
    except (SarahImportError, ValueError, KeyError, TypeError) as exc:
        partial = {"schema": COMPONENT_SCHEMA, "complete": False, "blockers": ["component_derivation_failed: " + str(exc)]}
        metadata = derive_component_metadata(partial, model)
    metadata["provenance"]["sarah_version"] = data.get("sarah_version", "unknown")
    return metadata


def _matrix_strings(matrix: sp.Matrix) -> list[list[str]]:
    return [[str(sp.factor(x)) for x in row] for row in matrix.tolist()]


def _zero(value: Any) -> bool:
    entries = list(value) if isinstance(value, sp.MatrixBase) else [value]
    return all(sp.expand(x) == 0 or sp.simplify(x) == 0 for x in entries)


def _source_equal(left: Any, right: Any) -> bool:
    # SARAH writes N[expression] in .vin, e.g. 1/3 becomes 0.3333333333333333.
    # Compare coefficients, not random field points; never ignore a missing term.
    if isinstance(left, sp.MatrixBase):
        return left.shape == right.shape and all(_source_equal(a, b) for a, b in zip(left, right))
    left, right = sp.sympify(left), sp.sympify(right)
    if _zero(left-right):
        return True
    symbols = sorted(left.free_symbols | right.free_symbols, key=str)
    if not symbols:
        return bool(abs(left-right) <= sp.Float("1e-12")*(abs(left)+abs(right)))
    try:
        a, b = [sp.Poly(sp.expand(x), *symbols).as_dict() for x in (left, right)]
    except sp.PolynomialError:
        return False
    return all(_source_equal(a.get(term, sp.Integer(0)), b.get(term, sp.Integer(0))) for term in a.keys() | b.keys())
