from __future__ import annotations

import re
from typing import Any

from .sarah_import import SarahImportedModel, SarahMassSector
from .sarah_thermal import add_thermal_matrix
from .template_contract import (
    _render_human_contract_template,
    _render_matrix_entry_blocks,
    _render_matrix_entry_metadata_table,
)


def build_sarah_contract(model: SarahImportedModel) -> str:
    fields = [
        {
            "name": name,
            "latex": name,
            "role": "background",
            "zeroT_default": _resolved_expression(model.dsb_minimum.get(name, "0.0"), model),
            "description": "Imported from Vevacious++ FieldVariables/DsbMinimum.",
        }
        for name in model.fields
    ]
    referenced = _referenced_parameter_names(model)
    constants = [
        {
            "name": name,
            "latex": name,
            "value": f"{model.parameter_values[name]:.17g}",
            "description": "Resolved deterministically from SLHA/ScaleAndBlock.xml.",
        }
        for name in sorted(referenced & set(model.parameter_values))
    ]
    if model.renormalization_scale_sq is not None:
        constants.append(
            {
                "name": "renormScaleSq",
                "latex": "Q^2",
                "value": f"{model.renormalization_scale_sq:.17g}",
                "description": "Resolved renormalization scale squared from the reviewed SARAH parameter policy.",
            }
        )
    unresolved = sorted(referenced - set(model.parameter_values) - set(model.fields) - {"pi"})
    if model.renormalization_scale_sq is None and "renormScaleSq" not in unresolved:
        unresolved.append("renormScaleSq")
    public_inputs = [
        {
            "name": name,
            "latex": name,
            "confirmed": "false",
            "test_value": "ASK_USER",
            "source_type": "unresolved_sarah_parameter",
            "description": "Provide a numerical SLHA/JSON value or remove this unresolved dependency.",
        }
        for name in unresolved
    ]
    for name, description in (
        ("num_boson_dof", "Total relativistic bosonic degrees of freedom used for the field-independent radiation term."),
        ("num_fermion_dof", "Total relativistic fermionic degrees of freedom used for the field-independent radiation term."),
    ):
        public_inputs.append(
            {
                "name": name,
                "latex": name,
                "confirmed": "false",
                "test_value": "ASK_USER",
                "source_type": "runtime_physics_choice",
                "description": description,
            }
        )
    boson_sectors = [sector for sector in model.mass_sectors if sector.kind in {"scalar", "gauge"}]
    fermion_sectors = [sector for sector in model.mass_sectors if sector.kind == "fermion"]
    seed_basis = list(boson_sectors[0].basis) if boson_sectors else list(model.fields)
    seed_matrix = [list(row) for row in boson_sectors[0].matrix] if boson_sectors else [
        ["0.0" if row != column else "0.0" for column in range(len(seed_basis))]
        for row in range(len(seed_basis))
    ]
    contract = _render_human_contract_template(
        paper_id=f"sarah_{_slug(model.model_name)}",
        fields=fields,
        public_inputs=public_inputs,
        constants=constants,
        formulas={"V0": model.tree_potential, "VCW": "SARAH Vevacious++ one-loop spectrum", "VCT": model.extra_polynomial, "thermal": "standard thermal integrals", "daisy": "companion thermal metadata", "Veff": "SARAH/Vevacious++ imported effective potential"},
        v0_python=model.tree_potential,
        v0_notes="TreeLevelPotential imported from Vevacious++ without LLM formula generation.",
        direct_bosons=[],
        direct_fermions=[],
        matrix_basis=seed_basis,
        matrix_rows=seed_matrix,
        matrix_entry_status="source_imported",
        matrix_dof=str(boson_sectors[0].dof_per_eigenvalue if boson_sectors else 1),
        matrix_notes="Temporary seed replaced by the complete SARAH mass-sector list below.",
        goldstone_defaults={},
        model_short_name=_slug(model.model_name),
        scalar_thermal_derivation_notes="Deterministic companion metadata; no LLM-derived thermal formula.",
        gauge_thermal_derivation_notes="Deterministic companion metadata; longitudinal modes only, with transverse modes left unresummed.",
    )
    contract = _replace_potential_compiler_expression(
        contract,
        "V_CT",
        model.extra_polynomial,
        "Vevacious++ ExtraPolynomialPart imported as a separate reviewed polynomial correction.",
    )
    contract = _replace_mass_matrix_sections(contract, boson_sectors, fermion_sectors, model)
    contract = _replace_model_card_value(contract, "zero_temperature", "standard_CW_V1")
    contract = _replace_model_card_value(contract, "counterterm", "ASK_USER")
    contract = _replace_model_card_value(contract, "thermal", "standard_thermal_integrals")
    contract = _replace_model_card_value(contract, "daisy", "none")
    contract = _replace_model_card_value(contract, "resummation_scheme", "ASK_USER")
    contract = _replace_model_card_value(contract, "gauge", model.gauge_fixing.title() or "ASK_USER")
    contract = _replace_model_card_value(contract, "reviewer_notes", "Batch-review species, resummation, special modes, phase handling, backend, and final approval.")
    source_hash = str(model.provenance.get("vin", {}).get("sha256", ""))
    parameter_map_hash = str(model.provenance.get("parameter_map", {}).get("sha256", ""))
    thermal_hash = str((model.provenance.get("thermal_metadata") or {}).get("sha256", ""))
    contract = _insert_model_card_rows(
        contract,
        [
            ("source_import_kind", "sarah_vevacious_v2", "Required provenance for source_imported expressions."),
            ("source_import_sha256", source_hash, "SHA256 of the imported .vin source."),
            ("source_parameter_map_sha256", parameter_map_hash, "SHA256 of the imported ScaleAndBlock.xml source."),
            ("source_thermal_metadata_sha256", thermal_hash or "not_supplied", "SHA256 of companion thermal metadata when supplied."),
            ("source_vin_path", str(model.provenance.get("vin", {}).get("path", "")), "Imported source path."),
            ("source_parameter_map_path", str(model.provenance.get("parameter_map", {}).get("path", "")), "Imported parameter-map path."),
            ("renormalization_scheme", model.renormalization_scheme, "Imported from LoopCorrections."),
            ("thermal_ready", str(model.thermal_ready).lower(), "False blocks Parwani and Arnold-Espinosa compilation."),
            ("numerical_ready", str(model.numerical_ready).lower(), "False blocks runtime/smoke checks."),
        ],
    )
    contract = contract.replace(
        "Status choices: `needs_review`, `agent_reviewed`, `human_modified`. Compilation requires `agent_reviewed`.",
        "Status choices: `needs_review`, `agent_reviewed`, `human_modified`, `source_imported`. `source_imported` is compile-ready only with verified SARAH provenance.",
    )
    return contract


def _replace_mass_matrix_sections(
    contract: str,
    bosons: list[SarahMassSector],
    fermions: list[SarahMassSector],
    model: SarahImportedModel,
) -> str:
    start = contract.index("### Boson Mass Matrices")
    end = contract.index("## 6. Counterterm, Goldstone, And Daisy Contracts")
    section = "\n\n".join(
        [
            "### Boson Mass Matrices\n\nAll entries below are imported deterministically from the Vevacious++ v2 source.",
            *[_render_sector(sector, model) for sector in bosons],
            "### Fermion Mass Matrices\n\nSARAH exports the Hermitian Weyl-fermion mass-squared matrices used by the one-loop potential.",
            *[_render_sector(sector, model) for sector in fermions],
        ]
    )
    return contract[:start] + section.rstrip() + "\n\n" + contract[end:]


def _render_sector(sector: SarahMassSector, model: SarahImportedModel) -> str:
    basis = list(sector.basis)
    matrix = [list(row) for row in sector.matrix]
    named_matrix_rows = [[basis[row_index], *row] for row_index, row in enumerate(matrix)]
    lines = [
        f"#### Matrix: {sector.name}",
        "",
        "| key | value |",
        "|---|---|",
        "| enabled | true |",
        f"| kind | {sector.kind} |",
        f"| basis | {', '.join(basis)} |",
        f"| dof_per_eigenvalue | {sector.dof_per_eigenvalue:g} |",
        f"| transverse_dof_per_eigenvalue | {sector.transverse_dof_per_eigenvalue:g} |",
        f"| longitudinal_dof_per_eigenvalue | {sector.longitudinal_dof_per_eigenvalue:g} |",
        f"| c | {sector.cw_constant:.17g} |",
        "| source_role | field_dependent |",
        f"| source_location | {sector.source_location} |",
        f"| thermal_matrix_mode | {'source_imported' if _thermal_matrix_for_sector(sector, model) else 'none'} |",
        f"| notes | Imported from `{sector.source_location}`; particles: {', '.join(sector.particle_names)}. |",
        "",
        "Matrix entries:",
        "",
        _render_matrix_entry_metadata_table(basis, status="source_imported"),
        "",
        _render_matrix_entry_blocks(sector.name, basis, named_matrix_rows),
    ]
    thermal_matrix = _thermal_matrix_for_sector(sector, model)
    if thermal_matrix:
        lines.extend(
            [
                "",
                "Thermal matrix entries:",
                "",
                _render_matrix_entry_metadata_table(basis, status="source_imported"),
                "",
                _render_thermal_entry_blocks(sector.name, basis, thermal_matrix),
            ]
        )
    return "\n".join(lines)


def _thermal_matrix_for_sector(
    sector: SarahMassSector,
    model: SarahImportedModel,
) -> list[list[str]]:
    if sector.kind not in {"scalar", "gauge"} or not model.thermal_result:
        return []
    mappings = model.thermal_result.get("sector_mappings", {}).get(sector.kind, [])
    mapping = next(
        (row for row in mappings if isinstance(row, dict) and row.get("sector") == sector.name),
        None,
    )
    if not mapping:
        return []
    indices = mapping.get("indices", [])
    key = "scalar_pi_over_t2" if sector.kind == "scalar" else "gauge_longitudinal_pi_over_t2"
    coefficient = model.thermal_result.get(key, [])
    try:
        submatrix = [[coefficient[row][column] for column in indices] for row in indices]
        return add_thermal_matrix([list(row) for row in sector.matrix], submatrix)
    except (IndexError, TypeError, ValueError):
        return []


def _render_thermal_entry_blocks(
    matrix_name: str,
    basis: list[str],
    matrix: list[list[str]],
) -> str:
    blocks: list[str] = []
    for row_index, row_name in enumerate(basis):
        for col_index, col_name in enumerate(basis):
            blocks.extend(
                [
                    f"##### Thermal matrix entry: {matrix_name}[{row_name},{col_name}]",
                    "",
                    "entry:",
                    "",
                    "```python",
                    str(matrix[row_index][col_index]),
                    "```",
                    "",
                ]
            )
    return "\n".join(blocks).rstrip()


def _replace_model_card_value(contract: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^(\|\s*{re.escape(key)}\s*\|)\s*[^|]*(\|.*)$", flags=re.MULTILINE)
    return pattern.sub(rf"\1 {value} \2", contract, count=1)


def _replace_potential_compiler_expression(
    contract: str,
    name: str,
    expression: str,
    notes: str,
) -> str:
    heading = f"### Potential Part: {name}"
    start = contract.index(heading)
    next_heading = contract.find("\n### ", start + len(heading))
    end = next_heading if next_heading >= 0 else len(contract)
    block = contract[start:end]
    block = re.sub(
        r"(Compiler expression:\s*\n+```python\s*\n).*?(\n```)",
        lambda match: match.group(1) + expression + match.group(2),
        block,
        count=1,
        flags=re.DOTALL,
    )
    block = re.sub(r"^Notes:.*$", f"Notes: {notes}", block, count=1, flags=re.MULTILINE)
    return contract[:start] + block + contract[end:]


def _insert_model_card_rows(
    contract: str,
    rows: list[tuple[str, str, str]],
) -> str:
    section_start = contract.index("## 1. Model Card")
    section_end = contract.index("## 2. Fields")
    section = contract[section_start:section_end]
    insertion = "".join(f"| {key} | {value} | {notes} |\n" for key, value, notes in rows)
    marker = "| reviewer_notes |"
    position = section.index(marker)
    line_start = section.rfind("\n", 0, position) + 1
    section = section[:line_start] + insertion + section[line_start:]
    return contract[:section_start] + section + contract[section_end:]


def _resolved_expression(expression: str, model: SarahImportedModel) -> str:
    value = model.parameter_values.get(expression)
    return f"{value:.17g}" if value is not None else expression


def _referenced_parameter_names(model: SarahImportedModel) -> set[str]:
    names: set[str] = set(model.unresolved_parameters)
    expressions = [model.tree_potential, model.extra_polynomial, *model.dsb_minimum.values()]
    expressions.extend(entry for sector in model.mass_sectors for row in sector.matrix for entry in row)
    from .safe_expression import parse_safe_expression

    for expression in expressions:
        names.update(parse_safe_expression(expression).names)
    names.difference_update(model.fields)
    names.discard("pi")
    return names


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "SARAHModel"
    return "M_" + slug if slug[0].isdigit() else slug
