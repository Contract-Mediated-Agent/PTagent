from __future__ import annotations

from typing import Any

from .safe_expression import SafeExpressionError, parse_safe_expression


class ThermalMetadataError(ValueError):
    """Raised when companion metadata cannot define deterministic thermal masses."""


def derive_thermal_matrices(
    metadata: dict[str, Any],
    *,
    fields: list[str],
    parameter_names: set[str],
) -> dict[str, Any]:
    background_fields = _string_list(metadata.get("background_fields"), "background_fields")
    scalar_basis = _string_list(metadata.get("scalar_basis"), "scalar_basis")
    gauge_basis = _string_list(metadata.get("gauge_basis"), "gauge_basis")
    if background_fields != fields:
        raise ThermalMetadataError(
            "Thermal background_fields must exactly match the Vevacious++ FieldVariables order: "
            f"expected {fields}, got {background_fields}."
        )
    provenance = metadata.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("source_hashes") or not provenance.get("exporter_version"):
        raise ThermalMetadataError(
            "source_imported thermal data requires provenance.source_hashes and provenance.exporter_version."
        )

    if metadata.get("complete") is False:
        declared = metadata.get("blockers")
        blockers = [str(item) for item in declared] if isinstance(declared, list) else []
        if "thermal_metadata_incomplete" not in blockers:
            blockers.append("thermal_metadata_incomplete")
        return {
            "schema": "ptagent.thermal_derivation.v1",
            "ready": False,
            "background_fields": background_fields,
            "scalar_basis": scalar_basis,
            "gauge_basis": gauge_basis,
            "scalar_pi_over_t2": [],
            "gauge_longitudinal_pi_over_t2": [],
            "sector_mappings": metadata.get("sector_mappings") if isinstance(metadata.get("sector_mappings"), dict) else {},
            "formula_convention": {},
            "provenance": dict(provenance),
            "blockers": blockers,
        }

    _require_companion_source_schema(metadata)

    invariants = metadata.get("thermal_invariants")
    if not isinstance(invariants, dict):
        raise ThermalMetadataError("thermal_invariants must be an object.")
    scalar_size = len(scalar_basis)
    gauge_size = len(gauge_basis)
    allowed_names = set(fields) | set(parameter_names) | {"T"}
    quartic = _expression_matrix(invariants.get("scalar_quartic"), scalar_size, "scalar_quartic", allowed_names)
    scalar_gauge = _expression_matrix(invariants.get("scalar_gauge"), scalar_size, "scalar_gauge", allowed_names)
    yukawa = _expression_matrix(invariants.get("scalar_yukawa"), scalar_size, "scalar_yukawa", allowed_names)
    adjoint = _expression_matrix(invariants.get("gauge_adjoint"), gauge_size, "gauge_adjoint", allowed_names)
    scalar_trace = _expression_matrix(invariants.get("gauge_scalar_trace"), gauge_size, "gauge_scalar_trace", allowed_names)
    fermion_trace = _expression_matrix(invariants.get("gauge_fermion_trace"), gauge_size, "gauge_fermion_trace", allowed_names)

    scalar_coefficients = _combine_matrices(
        [(quartic, 24.0), (scalar_gauge, 4.0), (yukawa, 12.0)]
    )
    gauge_coefficients = _combine_matrices(
        [(adjoint, 3.0), (scalar_trace, 3.0), (fermion_trace, 6.0)]
    )
    blockers: list[str] = []
    mappings = metadata.get("sector_mappings")
    if not isinstance(mappings, dict):
        blockers.append("sector_mappings_missing")
    consistency = metadata.get("consistency")
    supertrace_check: dict[str, Any] = {"provided": False, "equivalent": False}
    if isinstance(consistency, dict) and consistency.get("scalar_pi_over_t2") is not None:
        reference = _expression_matrix(
            consistency.get("scalar_pi_over_t2"),
            scalar_size,
            "consistency.scalar_pi_over_t2",
            allowed_names,
        )
        equivalent = _matrices_equivalent(scalar_coefficients, reference)
        supertrace_check = {"provided": True, "equivalent": equivalent}
        if not equivalent:
            blockers.append("scalar_supertrace_consistency_failed")
    else:
        blockers.append("scalar_supertrace_consistency_missing")

    return {
        "schema": "ptagent.thermal_derivation.v1",
        "ready": not blockers,
        "background_fields": background_fields,
        "scalar_basis": scalar_basis,
        "gauge_basis": gauge_basis,
        "scalar_pi_over_t2": scalar_coefficients,
        "gauge_longitudinal_pi_over_t2": gauge_coefficients,
        "sector_mappings": mappings if isinstance(mappings, dict) else {},
        "formula_convention": {
            "scalar": "lambda_abcc/24 + gauge_ab/4 + yukawa_ab/12",
            "gauge_longitudinal": "C_A/3 + T_scalar/3 + T_Weyl/6",
            "fermion_resummation": "none",
            "transverse_gauge_resummation": "none",
        },
        "provenance": dict(provenance),
        "metadata_scalar_consistency_check": supertrace_check,
        "consistency": {
            "background_scalar_indices": consistency.get("background_scalar_indices")
            if isinstance(consistency, dict)
            else None,
        },
        "blockers": blockers,
    }


def add_thermal_matrix(
    zero_temperature: list[list[str]],
    coefficient: list[list[str]],
) -> list[list[str]]:
    if len(zero_temperature) != len(coefficient):
        raise ThermalMetadataError("Zero-temperature and thermal coefficient matrices have different sizes.")
    result: list[list[str]] = []
    for row_index, row in enumerate(zero_temperature):
        if len(row) != len(coefficient[row_index]):
            raise ThermalMetadataError("Zero-temperature and thermal coefficient matrices have different shapes.")
        result.append(
            [
                parse_safe_expression(
                    f"({entry}) + ({coefficient[row_index][column_index]})*T**2"
                ).python
                for column_index, entry in enumerate(row)
            ]
        )
    return result


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ThermalMetadataError(f"{field} must be a list of non-empty strings.")
    return [item.strip() for item in value]


def _expression_matrix(
    value: Any,
    size: int,
    field: str,
    allowed_names: set[str],
) -> list[list[str]]:
    if size == 0:
        if value not in ([], None):
            raise ThermalMetadataError(f"{field} must be empty when its basis is empty.")
        return []
    if not isinstance(value, list) or len(value) != size:
        raise ThermalMetadataError(f"{field} must be a {size}x{size} matrix.")
    result: list[list[str]] = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != size:
            raise ThermalMetadataError(f"{field}[{row_index}] must contain {size} entries.")
        normalized_row: list[str] = []
        for column_index, expression in enumerate(row):
            try:
                normalized_row.append(
                    parse_safe_expression(str(expression), allowed_names=allowed_names).python
                )
            except SafeExpressionError as exc:
                raise ThermalMetadataError(
                    f"Invalid {field}[{row_index}][{column_index}] expression: {exc}"
                ) from exc
        result.append(normalized_row)
    _require_symmetric(result, field)
    return result


def _combine_matrices(parts: list[tuple[list[list[str]], float]]) -> list[list[str]]:
    if not parts or not parts[0][0]:
        return []
    size = len(parts[0][0])
    return [
        [
            parse_safe_expression(
                " + ".join(
                    f"({matrix[row][column]})/{denominator:g}"
                    for matrix, denominator in parts
                )
            ).python
            for column in range(size)
        ]
        for row in range(size)
    ]


def _require_symmetric(matrix: list[list[str]], field: str) -> None:
    for row in range(len(matrix)):
        for column in range(row + 1, len(matrix)):
            if not _expressions_equivalent(matrix[row][column], matrix[column][row]):
                raise ThermalMetadataError(
                    f"{field} must be symmetric; entries [{row},{column}] and [{column},{row}] differ."
                )


def _require_companion_source_schema(metadata: dict[str, Any]) -> None:
    list_fields = ("gauge_groups", "scalar_components", "fermion_components")
    object_fields = ("representation_data", "coupling_tensors", "basis_mappings", "conventions")
    for field in list_fields:
        if not isinstance(metadata.get(field), list):
            raise ThermalMetadataError(f"Complete thermal metadata requires {field} as a list.")
    for field in object_fields:
        if not isinstance(metadata.get(field), dict):
            raise ThermalMetadataError(f"Complete thermal metadata requires {field} as an object.")
    if not isinstance(metadata.get("discrete_symmetries", []), list):
        raise ThermalMetadataError("discrete_symmetries must be a list.")


def _matrices_equivalent(left: list[list[str]], right: list[list[str]]) -> bool:
    if len(left) != len(right):
        return False
    try:
        import sympy as sp
    except ImportError as exc:
        raise ThermalMetadataError("SymPy is required for the scalar supertrace consistency check.") from exc
    for row_index, row in enumerate(left):
        if len(row) != len(right[row_index]):
            return False
        for column_index, expression in enumerate(row):
            if sp.simplify(sp.sympify(expression) - sp.sympify(right[row_index][column_index])) != 0:
                return False
    return True


def _expressions_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        import sympy as sp

        return bool(sp.simplify(sp.sympify(left) - sp.sympify(right)) == 0)
    except Exception:
        return False
