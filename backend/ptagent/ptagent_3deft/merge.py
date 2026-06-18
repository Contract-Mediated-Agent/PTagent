from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ThreeDeftBlocked, parse_contract, validate_contract_template
from .layout import contract_dir_for_template
from .mathematica_expr import repair_reserved_identifier


MERGED_CONTRACT_NAME = "three_deft_contract_merged.md"


@dataclass(frozen=True)
class MergeResult:
    output_path: Path
    updates: tuple[str, ...]
    ready_for_run: bool
    ready_for_compile: bool
    blocking_issue_count: int


def merge_dralgo_output_into_contract(
    template_path: str | Path,
    dralgo_output_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> MergeResult:
    """Merge normalized DRalgo output back into a 3DEFT markdown contract.

    This is intentionally conservative: it only imports values already present
    in ``dralgo_3deft_output.json`` and fixed pipeline conventions such as the
    canonical 3D field-normalization enum.
    """

    template_path = Path(template_path)
    dralgo_output_path = Path(dralgo_output_path)
    markdown = template_path.read_text(encoding="utf-8")
    output = json.loads(dralgo_output_path.read_text(encoding="utf-8"))
    contract = parse_contract(markdown)
    updates: list[str] = []

    markdown, identifier_updates = _normalize_input_parameter_identifiers(markdown, contract)
    updates.extend(identifier_updates)
    if identifier_updates:
        contract = parse_contract(markdown)

    markdown, normalization_updates = _normalize_three_d_field_normalizations(markdown, contract)
    updates.extend(normalization_updates)
    if normalization_updates:
        contract = parse_contract(markdown)

    scalar_rows = _scalar_index_rows(output, contract)
    if scalar_rows:
        markdown = _replace_table_rows(markdown, "Scalar Index Map", _render_rows(scalar_rows, ("scalar_index", "field_component", "parent_multiplet", "integrated_out", "print_scalar_rep_positions_evidence")))
        updates.append(f"scalar_index_map:{len(scalar_rows)}")

    coefficient_rows = _coefficient_rows(output, contract)
    if coefficient_rows:
        markdown = _replace_table_rows(markdown, "3D Coefficients", _render_rows(coefficient_rows, ("name", "expression_or_interpolator", "depends_on", "source", "evidence")))
        order_sum_rows = _coefficient_order_sum_rows(coefficient_rows)
        if order_sum_rows:
            markdown = _replace_table_rows(
                markdown,
                "3D Coefficient Order Sums",
                _render_rows(order_sum_rows, ("base_parameter", "components", "expression", "source", "status", "notes")),
            )
        coefficient_order = ",".join(row["name"] for row in coefficient_rows if row.get("name"))
        if coefficient_order:
            markdown = _set_key_status_value(
                markdown,
                "3D Parameter Evaluator",
                "coefficient_order",
                coefficient_order,
                "agent_reviewed",
                "Merged from DRalgo coefficient output order; user should review before PhaseTracer compile approval.",
            )
        updates.append(f"three_d_coefficients:{len(coefficient_rows)}")
        if order_sum_rows:
            updates.append(f"three_d_coefficient_order_sums:{len(order_sum_rows)}")

    potential_terms = _selected_effective_potential_terms(output, contract)
    if potential_terms:
        remapped_terms: list[tuple[str, str, str]] = []
        remap_note_parts: list[str] = []
        for order, label, term_expression in potential_terms:
            term_expression = _repair_reserved_identifier_references(term_expression)
            expression, remap_notes = _rewrite_potential_to_matched_coefficients(term_expression, coefficient_rows, contract)
            remapped_terms.append((order, label, expression))
            if remap_notes and remap_notes not in remap_note_parts:
                remap_note_parts.append(remap_notes)
            if order in {"LO", "NLO", "NNLO"}:
                markdown = _set_key_value(
                    markdown,
                    "3D Consumption",
                    f"v3d_expression_{order}",
                    expression,
                    f"Merged from {dralgo_output_path.name}:{label}; order-separated V3D contribution before the PhaseTracer prefactor.",
                )
                updates.append(f"v3d_expression_{order}:{label}")

        if len(remapped_terms) == 1:
            expression = remapped_terms[0][2]
        else:
            expression = " + ".join(f"({term_expression})" for _order, _label, term_expression in remapped_terms)
        label = "+".join(label for _order, label, _expression in remapped_terms)
        notes = f"Merged from {dralgo_output_path.name}:{label}; PhaseTracer uses the default 3DEFT field map and prefactor recorded in the contract."
        if remap_note_parts:
            notes += f" Matched coefficient remap: {'; '.join(remap_note_parts)}."
        markdown = _set_key_value(markdown, "3D Consumption", "v3d_expression", expression, notes)
        updates.append(f"v3d_expression:{label}")
        prefactor = str(contract.get("three_d_consumption", {}).get("potential_prefactor", "")).strip()
        if prefactor and not _missing_cell(prefactor):
            markdown = _set_key_value(
                markdown,
                "3D Consumption",
                "v_phase_tracer_expression",
                f"{prefactor}*({expression})",
                "Regenerated from the default potential_prefactor times merged V3D expression.",
            )
            updates.append("v_phase_tracer_expression:prefactor_times_v3d")

    beta4_rows = _beta_rows(output.get("beta_functions_phase_tracer"), "BetaFunctions4D", _input_parameter_names(contract))
    if beta4_rows:
        beta4_rows.extend(_zero_beta_rows_for_fixed_input_parameters(contract, beta4_rows))
    if beta4_rows:
        markdown = _replace_table_rows(markdown, "RG Beta Functions", _render_rows(beta4_rows, ("parameter", "beta_expression", "source", "status", "notes")))
        updates.append(f"rg_beta_functions:{len(beta4_rows)}")

    beta3_rows = _beta_rows(output.get("beta_functions_phase_tracer"), "BetaFunctions3DUS", _coefficient_names_after_merge(coefficient_rows, contract))
    if beta3_rows:
        markdown = _replace_table_rows(markdown, "3D Beta Functions", _render_rows(beta3_rows, ("parameter", "beta_expression", "source", "status", "notes")))
        updates.append(f"three_d_beta_functions:{len(beta3_rows)}")

    markdown = _set_key_value(
        markdown,
        "PhaseTracer",
        "approve_compile",
        "false",
        f"Reset by merge-output; user must review {MERGED_CONTRACT_NAME} before PhaseTracer compile approval.",
    )
    markdown = _set_key_value(
        markdown,
        "PhaseTracer",
        "approval_record",
        "ASK_USER",
        "Reset by merge-output; approval must refer to the merged contract currently on disk.",
    )

    target = Path(output_path) if output_path else contract_dir_for_template(template_path) / MERGED_CONTRACT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    report = validate_contract_template(markdown)
    return MergeResult(
        output_path=target,
        updates=tuple(updates),
        ready_for_run=report.ready_for_run,
        ready_for_compile=report.ready_for_compile,
        blocking_issue_count=len(report.issues),
    )


def _normalize_input_parameter_identifiers(markdown: str, contract: dict[str, Any]) -> tuple[str, list[str]]:
    rows = contract.get("input_parameters", [])
    if not rows:
        return markdown, []
    changed = False
    rewritten: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        original = str(item.get("name", "")).strip()
        repaired = repair_reserved_identifier(original)
        if original and repaired != original:
            item["name"] = repaired
            if not item.get("latex") or item.get("latex") == repaired:
                item["latex"] = original
            item["notes"] = _append_note(
                item.get("notes", ""),
                f"Auto-normalized compiler identifier from {original} to {repaired}.",
            )
            changed = True
        item["input_scale_GeV"] = str(item.get("input_scale", item.get("input_scale_GeV", "")))
        rewritten.append(item)
    if not changed:
        return markdown, []
    markdown = _replace_table_rows(
        markdown,
        "Input Parameters",
        _render_rows(rewritten, ("name", "latex", "review_status", "test_value", "input_scale_GeV", "source", "notes")),
    )
    symbol_rows = contract.get("symbol_audit", [])
    if symbol_rows:
        symbol_rewritten: list[dict[str, str]] = []
        symbol_changed = False
        for row in symbol_rows:
            item = dict(row)
            for key in ("suggested_symbol", "compiler_symbol"):
                repaired = repair_reserved_identifier(str(item.get(key, "")).strip())
                if repaired != item.get(key):
                    item[key] = repaired
                    symbol_changed = True
            symbol_rewritten.append(item)
        if symbol_changed:
            markdown = _replace_table_rows(
                markdown,
                "Mathematica Symbol Audit",
                _render_rows(
                    symbol_rewritten,
                    ("raw_symbol", "suggested_symbol", "compiler_symbol", "physics_role", "cform_temp_symbol", "source", "status", "notes"),
                ),
            )
    return markdown, ["input_parameter_identifiers:compiler_safe"]


def _normalize_three_d_field_normalizations(markdown: str, contract: dict[str, Any]) -> tuple[str, list[str]]:
    updates: list[str] = []
    rows = contract.get("three_d_fields", [])
    if rows:
        rewritten: list[dict[str, str]] = []
        changed = False
        for row in rows:
            item = dict(row)
            normalization = str(item.get("normalization", "")).strip()
            if _looks_like_canonical_field_formula(normalization):
                item["normalization"] = "canonical_3d"
                item["evidence"] = _append_note(
                    item.get("evidence", ""),
                    "Auto-normalized from explicit phi/sqrt(T) formula to canonical_3d enum.",
                )
                changed = True
            rewritten.append(item)
        if changed:
            markdown = _replace_table_rows(
                markdown,
                "3D Fields",
                _render_rows(rewritten, ("name", "dralgo_background_symbol", "source_scalar_indices", "normalization", "phase_tracer_slot", "evidence")),
            )
            updates.append("three_d_fields:canonical_3d")
    consumption_normalization = str(contract.get("three_d_consumption", {}).get("field_normalization", "")).strip()
    if _looks_like_canonical_field_formula(consumption_normalization):
        markdown = _set_key_value(
            markdown,
            "3D Consumption",
            "field_normalization",
            "canonical_3d",
            "Auto-normalized from explicit phi/sqrt(T) formula to the pipeline enum.",
        )
        updates.append("three_d_consumption.field_normalization:canonical_3d")
    return markdown, updates


def _looks_like_canonical_field_formula(value: str) -> bool:
    normalized = value.casefold().replace(" ", "")
    return "sqrt" in normalized and "/" in normalized and ("phi" in normalized or "field" in normalized or "phase_tracer" in normalized)


def _append_note(existing: Any, addition: str) -> str:
    text = str(existing or "").strip()
    if not text:
        return addition
    if addition in text:
        return text
    return f"{text} {addition}"


def _scalar_index_rows(output: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, str]]:
    positions = output.get("scalar_rep_positions")
    if isinstance(positions, str):
        try:
            positions = json.loads(positions)
        except json.JSONDecodeError:
            parsed = _parse_scalar_position_text(positions, contract)
            if parsed:
                positions = parsed
            else:
                return []
    if not isinstance(positions, list):
        return []
    integrated = _integrated_soft_indices(contract)
    rows: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        index = item.get("index") or item.get("scalar_index") or item.get("position")
        field = item.get("field_component") or item.get("field") or item.get("component")
        parent = item.get("parent_multiplet") or item.get("multiplet") or field
        if index is None or not field:
            continue
        index_text = str(index)
        rows.append(
            {
                "scalar_index": index_text,
                "field_component": str(field),
                "parent_multiplet": str(parent or "ASK_USER"),
                "integrated_out": "true" if index_text in integrated else "false",
                "print_scalar_rep_positions_evidence": "Merged from dralgo_3deft_output.json PrintScalarRepPositions.",
            }
        )
    return rows


def _parse_scalar_position_text(text: str, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse DRalgo strings like ``{1 ;; 4, 5 ;; 5}`` into scalar rows."""

    raw = text.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return []
    pieces = [piece.strip() for piece in raw[1:-1].split(",") if piece.strip()]
    scalar_multiplets = contract.get("parent_theory_4d", {}).get("scalar_multiplets", [])
    rows: list[dict[str, Any]] = []
    for multiplet_index, piece in enumerate(pieces):
        interval = re.fullmatch(r"(\d+)\s*;;\s*(\d+)", piece)
        singleton = re.fullmatch(r"(\d+)", piece)
        if interval:
            start = int(interval.group(1))
            stop = int(interval.group(2))
        elif singleton:
            start = stop = int(singleton.group(1))
        else:
            return []
        parent = (
            str(scalar_multiplets[multiplet_index].get("name", "")).strip()
            if multiplet_index < len(scalar_multiplets)
            else f"scalar_multiplet_{multiplet_index + 1}"
        )
        if not parent or parent.casefold().startswith("ask_user"):
            parent = f"scalar_multiplet_{multiplet_index + 1}"
        width = stop - start + 1
        for offset, scalar_index in enumerate(range(start, stop + 1), start=1):
            rows.append(
                {
                    "index": str(scalar_index),
                    "field_component": parent if width == 1 else f"{parent}[{offset}]",
                    "parent_multiplet": parent,
                }
            )
    return rows


def _coefficient_rows(output: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, str]]:
    explicit = output.get("three_d_coefficients_phase_tracer") or output.get("three_d_coefficients")
    rules = _rule_rows(explicit, source="dralgo_output")
    rules.extend(_rule_rows(output.get("couplings_phase_tracer"), source="PrintCouplings"))
    rules.extend(_rule_rows(output.get("scalar_masses_phase_tracer"), source="PrintScalarMass"))
    existing = _existing_coefficient_rows(contract)
    if not rules and not existing:
        return []
    allowed_order = _coefficient_order(contract)
    by_name: dict[str, dict[str, str]] = {row["name"]: row for row in existing}
    for rule in rules:
        name = _ordered_name_from_source(rule["name"], rule.get("source", ""))
        expression = _repair_reserved_identifier_references(rule["expression"])
        by_name[name] = {
            "name": name,
            "expression_or_interpolator": expression,
            "depends_on": _depends_on(expression),
            "source": "dralgo_generated",
            "evidence": f"Merged from {rule['source']}; user must confirm coefficient role/order.",
        }
    _add_order_summed_coefficient_rows(by_name)
    coefficient_names = set(by_name)
    for input_name in _input_parameter_names(contract):
        if input_name in by_name and _matched_coefficient_name(input_name, coefficient_names):
            del by_name[input_name]
    ordered_names = _review_ordered_coefficient_names(by_name, allowed_order)
    return [by_name[name] for name in ordered_names]


def _ordered_name_from_source(name: str, source: str) -> str:
    repaired = repair_reserved_identifier(name)
    order = _order_from_source_label(source)
    if order is None:
        return repaired
    if _split_perturbative_order_suffix(repaired) is not None:
        return repaired
    return f"{repaired}_{order}"


def _order_from_source_label(source: str) -> str | None:
    match = re.search(r"(?:^|_)(LO|NLO|NNLO)(?:$|_)", str(source))
    return match.group(1) if match else None


def _add_order_summed_coefficient_rows(by_name: dict[str, dict[str, str]]) -> None:
    grouped: dict[str, dict[str, str]] = {}
    for name in list(by_name):
        split = _split_perturbative_order_suffix(name)
        if split is None:
            continue
        base, order = split
        grouped.setdefault(base, {})[order] = name
    for base, components_by_order in grouped.items():
        if base in by_name:
            continue
        ordered_components = [
            components_by_order[order]
            for order in ("LO", "NLO", "NNLO")
            if order in components_by_order
        ]
        if not ordered_components:
            continue
        expression = " + ".join(ordered_components)
        by_name[base] = {
            "name": base,
            "expression_or_interpolator": expression,
            "depends_on": ",".join(ordered_components),
            "source": "dralgo_generated_order_sum",
            "evidence": "Auto-assembled from DRalgo ordered coefficient pieces: "
            + ", ".join(ordered_components)
            + ". User must confirm the perturbative-order sum before scans.",
        }


def _review_ordered_coefficient_names(by_name: dict[str, dict[str, str]], allowed_order: list[str]) -> list[str]:
    component_map = _order_sum_component_map(by_name)
    component_to_base = {
        component: base
        for base, components in component_map.items()
        for component in components
    }
    ordered: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name in by_name and name not in seen:
            ordered.append(name)
            seen.add(name)

    def add_with_components(name: str) -> None:
        for component in component_map.get(name, []):
            add(component)
        add(name)

    for name in allowed_order:
        if name in component_map:
            add_with_components(name)
        else:
            add(name)

    for name in by_name:
        if name in seen:
            continue
        base = component_to_base.get(name)
        if base and base in by_name:
            add_with_components(base)
        elif name in component_map:
            add_with_components(name)
        else:
            add(name)
    return ordered


def _coefficient_order_sum_rows(coefficient_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_name = {row["name"]: row for row in coefficient_rows if row.get("name")}
    rows: list[dict[str, str]] = []
    for name, row in by_name.items():
        if row.get("source") != "dralgo_generated_order_sum":
            continue
        components = _order_sum_components_from_expression(row.get("expression_or_interpolator", ""), name, by_name)
        if not components:
            continue
        expression = " + ".join(components)
        rows.append(
            {
                "base_parameter": name,
                "components": ",".join(components),
                "expression": expression,
                "source": row.get("source", "dralgo_generated_order_sum"),
                "status": "agent_reviewed",
                "notes": row.get("evidence", "Auto-assembled from ordered coefficient pieces; user should review before scans."),
            }
        )
    return rows


def _order_sum_components_from_expression(expression: str, base: str, by_name: dict[str, dict[str, str]]) -> list[str]:
    names = [part.strip() for part in str(expression).split("+") if part.strip()]
    components: list[tuple[str, str]] = []
    for name in names:
        split = _split_perturbative_order_suffix(name)
        if split is None:
            return []
        component_base, order = split
        if component_base != base or name not in by_name:
            return []
        components.append((order, name))
    order_rank = {"LO": 0, "NLO": 1, "NNLO": 2}
    return [name for _order, name in sorted(components, key=lambda item: order_rank[item[0]])]


def _order_sum_component_map(by_name: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, dict[str, str]] = {}
    for name in by_name:
        split = _split_perturbative_order_suffix(name)
        if split is None:
            continue
        base, order = split
        if base in by_name:
            grouped.setdefault(base, {})[order] = name
    return {
        base: [components[order] for order in ("LO", "NLO", "NNLO") if order in components]
        for base, components in grouped.items()
    }


def _split_perturbative_order_suffix(name: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)(?:_)?(NNLO|NLO|LO)", name)
    if not match:
        return None
    base = match.group(1).rstrip("_")
    if not base:
        return None
    return base, match.group(2)


def _existing_coefficient_rows(contract: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in contract.get("three_d_coefficients", []):
        name = str(row.get("name", "")).strip()
        expression = str(row.get("expression_or_interpolator", "")).strip()
        if not name or _missing_cell(name) or not expression or _missing_cell(expression):
            continue
        rows.append(
            {
                "name": name,
                "expression_or_interpolator": expression,
                "depends_on": str(row.get("depends_on") or _depends_on(expression)).strip(),
                "source": str(row.get("source") or "reviewed_contract").strip(),
                "evidence": str(row.get("evidence") or "Preserved from reviewed contract.").strip(),
            }
        )
    return rows


def _rule_rows(value: Any, *, source: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if value is None:
        return rows
    if isinstance(value, dict):
        if "name" in value or "parameter" in value or "phase_tracer_expression" in value:
            name = str(value.get("name") or value.get("parameter") or "").strip()
            expression = _expression_from_item(value)
            if name and expression:
                return [{"name": name, "expression": expression, "source": source}]
        for key, item in value.items():
            if isinstance(item, list):
                for nested in item:
                    rows.extend(_rule_rows(nested, source=str(key)))
            elif isinstance(item, dict) and ("name" in item or "parameter" in item or "phase_tracer_expression" in item):
                name = str(item.get("name") or item.get("parameter") or key).strip()
                expression = _expression_from_item(item)
                if name and expression:
                    rows.append({"name": name, "expression": expression, "source": source})
            elif isinstance(item, (str, int, float)):
                expression = str(item).strip()
                if key and expression:
                    rows.append({"name": str(key), "expression": expression, "source": source})
    elif isinstance(value, list):
        for item in value:
            rows.extend(_rule_rows(item, source=source))
    elif isinstance(value, str):
        for name, expression in _parse_rule_text(value):
            rows.append({"name": name, "expression": expression, "source": source})
    return rows


def _selected_effective_potential(output: dict[str, Any], contract: dict[str, Any]) -> tuple[str, str] | None:
    terms = _selected_effective_potential_terms(output, contract)
    if terms:
        if len(terms) == 1:
            return terms[0][2], terms[0][1]
        expression = " + ".join(f"({term})" for _order, _label, term in terms)
        label = "+".join(label for _order, label, _term in terms)
        return expression, label
    return None


def _selected_effective_potential_terms(output: dict[str, Any], contract: dict[str, Any]) -> list[tuple[str, str, str]]:
    potentials = output.get("effective_potential_phase_tracer")
    if not potentials:
        return []
    order = str(contract.get("rg_matching", {}).get("effective_potential_order", "")).upper()
    if isinstance(potentials, dict):
        sum_labels = _effective_potential_sum_labels(order)
        terms: list[tuple[str, str, str]] = []
        for label in sum_labels:
            expression = _first_expression(potentials.get(label))
            if expression:
                terms.append((_effective_potential_order_from_label(label), label, expression))
        if terms:
            return terms
        labels = [f"PrintEffectivePotential_{order}"] if order else []
        labels.extend(["PrintEffectivePotential_NNLO", "PrintEffectivePotential_NLO", "PrintEffectivePotential_LO", "PrintEffectivePotential"])
        for label in labels:
            expression = _first_expression(potentials.get(label))
            if expression:
                return [(_effective_potential_order_from_label(label), label, expression)]
    expression = _first_expression(potentials)
    if expression:
        return [("TOTAL", "PrintEffectivePotential", expression)]
    return []


def _effective_potential_sum_labels(order: str) -> list[str]:
    normalized = order.upper()
    if normalized == "LO":
        return ["PrintEffectivePotential_LO"]
    if normalized == "NLO":
        return ["PrintEffectivePotential_LO", "PrintEffectivePotential_NLO"]
    if normalized == "NNLO":
        return ["PrintEffectivePotential_LO", "PrintEffectivePotential_NLO", "PrintEffectivePotential_NNLO"]
    return []


def _effective_potential_order_from_label(label: str) -> str:
    for order in ("NNLO", "NLO", "LO"):
        if label.endswith(f"_{order}") or label == order:
            return order
    return "TOTAL"


def _beta_rows(beta_data: Any, marker: str, allowed_names: set[str]) -> list[dict[str, str]]:
    if not beta_data:
        return []
    block = beta_data.get(marker) if isinstance(beta_data, dict) else beta_data
    rows: list[dict[str, str]] = []
    for parameter, expression in _parameter_expression_pairs(block):
        target_parameter = repair_reserved_identifier(parameter)
        expression = _repair_reserved_identifier_references(expression)
        notes = "Merged from normalized DRalgo output; user should confirm before scans."
        if allowed_names and target_parameter not in allowed_names:
            squared_base = _squared_beta_base(target_parameter, allowed_names)
            if marker == "BetaFunctions4D" and squared_base:
                target_parameter = squared_base
                expression = f"({expression})/(2*{target_parameter})"
                notes = (
                    f"Converted from DRalgo native beta for {parameter}=({target_parameter})^2 "
                    f"to beta for input parameter {target_parameter}; user should confirm before production use."
                )
            else:
                continue
        if any(row["parameter"] == target_parameter for row in rows):
            continue
        rows.append(
            {
                "parameter": target_parameter,
                "beta_expression": expression,
                "source": f"{marker}[]",
                "status": "agent_reviewed",
                "notes": notes,
            }
        )
    return rows


def _repair_reserved_identifier_references(expression: str) -> str:
    repaired = expression
    tokens = sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression)), key=len, reverse=True)
    for token in tokens:
        safe = repair_reserved_identifier(token)
        if safe != token:
            repaired = re.sub(rf"\b{re.escape(token)}\b", safe, repaired)
    return repaired


def _zero_beta_rows_for_fixed_input_parameters(
    contract: dict[str, Any],
    existing_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing = {row["parameter"] for row in existing_rows if row.get("parameter")}
    rows: list[dict[str, str]] = []
    for row in contract.get("input_parameters", []):
        name = str(row.get("name", "")).strip()
        if not name or name in existing or _is_scale_control_input_parameter(row) or not _fixed_input_parameter_without_beta(row):
            continue
        rows.append(
            {
                "parameter": name,
                "beta_expression": "0",
                "source": "fixed_external_input",
                "status": "agent_reviewed",
                "notes": "No DRalgo BetaFunctions4D[] row was emitted; treated as fixed external input parameter for 4D running.",
            }
        )
    return rows


def _fixed_input_parameter_without_beta(row: dict[str, Any]) -> bool:
    name = str(row.get("name", "")).strip()
    normalized_name = name.casefold()
    if normalized_name in {"lb", "lf", "nf"}:
        return True
    notes = " ".join(str(row.get(key, "")) for key in ("source", "notes", "evidence")).casefold()
    return any(token in notes for token in ("fixed", "constant", "fermion generation", "logarithmic matching"))


def _is_scale_control_input_parameter(row: dict[str, Any]) -> bool:
    name = str(row.get("name", "")).strip().casefold()
    if name in {"xi4", "xi_4"}:
        return True
    notes = " ".join(str(row.get(key, "")) for key in ("source", "notes", "evidence")).casefold()
    return "scale_policy" in notes or "scale-control" in notes or "matching scale" in notes


def _parameter_expression_pairs(value: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        if "parameter" in value or "name" in value:
            parameter = str(value.get("parameter") or value.get("name") or "").strip()
            expression = _expression_from_item(value)
            if parameter and expression:
                return [(parameter, expression)]
        for key, item in value.items():
            if isinstance(item, list):
                for nested in item:
                    nested_pairs = _parameter_expression_pairs(nested)
                    if nested_pairs:
                        pairs.extend(nested_pairs)
                    else:
                        expression = _expression_from_item(nested)
                        if expression:
                            pairs.append((str(key), expression))
            else:
                expression = _expression_from_item(item)
                if expression:
                    pairs.append((str(key), expression))
    elif isinstance(value, list):
        for item in value:
            pairs.extend(_parameter_expression_pairs(item))
    elif isinstance(value, str):
        pairs.extend(_parse_rule_text(value))
    return pairs


def _parse_rule_text(text: str) -> list[tuple[str, str]]:
    stripped = text.strip()
    if not stripped:
        return []
    results: list[tuple[str, str]] = []
    for piece in re.split(r"\n|,", stripped):
        candidate = piece.strip().strip("{}")
        if not candidate:
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*(?:->|=>|:)\s*(.+)$", candidate)
        if match:
            results.append((match.group(1), match.group(2).strip()))
            continue
        rule_match = re.match(r"^Rule\[\s*([A-Za-z][A-Za-z0-9_]*)\s*,\s*(.+)\]$", candidate)
        if rule_match:
            results.append((rule_match.group(1), rule_match.group(2).strip()))
    return results


def _expression_from_item(item: Any) -> str:
    if isinstance(item, dict):
        raw = str(item.get("phase_tracer_expression") or item.get("expression") or item.get("beta_expression") or "").strip()
        if not _usable_expression(raw):
            return ""
        parsed = _parse_rule_text(raw)
        if parsed:
            return parsed[0][1]
        return raw
    if isinstance(item, (str, int, float)):
        raw = str(item).strip()
        if not _usable_expression(raw):
            return ""
        parsed = _parse_rule_text(raw)
        if parsed:
            return parsed[0][1]
        return raw
    return ""


def _first_expression(value: Any) -> str:
    if isinstance(value, dict):
        return _expression_from_item(value)
    if isinstance(value, list):
        for item in value:
            expression = _first_expression(item)
            if expression:
                return expression
    if isinstance(value, str):
        raw = value.strip()
        return raw if _usable_expression(raw) else ""
    return ""


def _usable_expression(value: str) -> bool:
    return value.strip().casefold() not in {"", "$failed", "failed", "null", "none"}


def _replace_table_rows(markdown: str, heading: str, rows: str) -> str:
    lines = markdown.splitlines()
    start = _heading_line(lines, heading)
    if start is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} was not found.")
    separator = None
    for index in range(start + 1, len(lines)):
        if lines[index].lstrip().startswith("| ---"):
            separator = index
            break
        if lines[index].startswith("#"):
            raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} does not contain a markdown table.")
    if separator is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} does not contain a markdown table separator.")
    end = separator + 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    new_lines = lines[: separator + 1] + rows.splitlines() + lines[end:]
    return "\n".join(new_lines) + ("\n" if markdown.endswith("\n") else "")


def _set_key_value(markdown: str, heading: str, key: str, value: str, notes: str) -> str:
    lines = markdown.splitlines()
    start = _heading_line(lines, heading)
    if start is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} was not found.")
    end = _next_heading_line(lines, start)
    rendered = f"| {_cell(key)} | {_cell(value)} | {_cell(notes)} |"
    pattern = re.compile(rf"^\|\s*{re.escape(key)}\s*\|")
    for index in range(start, end):
        if pattern.match(lines[index]):
            lines[index] = rendered
            return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
    insert_at = None
    for index in range(start + 1, end):
        if lines[index].lstrip().startswith("| ---"):
            insert_at = index + 1
            break
    if insert_at is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} does not contain a markdown key/value table.")
    lines.insert(insert_at, rendered)
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def _set_key_status_value(markdown: str, heading: str, key: str, value: str, status: str, notes: str) -> str:
    lines = markdown.splitlines()
    start = _heading_line(lines, heading)
    if start is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} was not found.")
    end = _next_heading_line(lines, start)
    rendered = f"| {_cell(key)} | {_cell(value)} | {_cell(status)} | {_cell(notes)} |"
    pattern = re.compile(rf"^\|\s*{re.escape(key)}\s*\|")
    for index in range(start, end):
        if pattern.match(lines[index]):
            lines[index] = rendered
            return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
    insert_at = None
    for index in range(start + 1, end):
        if lines[index].lstrip().startswith("| ---"):
            insert_at = index + 1
            break
    if insert_at is None:
        raise ThreeDeftBlocked(f"Cannot merge DRalgo output: contract section {heading!r} does not contain a markdown key/value/status table.")
    lines.insert(insert_at, rendered)
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def _heading_line(lines: list[str], heading: str) -> int | None:
    pattern = re.compile(rf"^#{{2,6}}\s+{re.escape(heading)}\s*$")
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def _next_heading_line(lines: list[str], start: int) -> int:
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    pattern = re.compile(rf"^#{{1,{level}}}\s+")
    for index in range(start + 1, len(lines)):
        if pattern.match(lines[index]):
            return index
    return len(lines)


def _render_rows(rows: list[dict[str, str]], columns: tuple[str, ...]) -> str:
    return "\n".join("| " + " | ".join(_cell(row.get(column, "")) for column in columns) + " |" for row in rows)


def _cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _coefficient_order(contract: dict[str, Any]) -> list[str]:
    for row in contract.get("three_d_parameter_evaluator", []):
        if row.get("key") == "coefficient_order":
            return [part.strip() for part in str(row.get("value", "")).split(",") if part.strip() and not part.strip().casefold().startswith("ask_user")]
    return []


def _rewrite_potential_to_matched_coefficients(
    expression: str,
    coefficient_rows: list[dict[str, str]],
    contract: dict[str, Any],
) -> tuple[str, str]:
    coefficient_names = {str(row.get("name", "")).strip() for row in coefficient_rows if row.get("name")}
    if not coefficient_names:
        return expression, ""
    protected_names = (
        _input_parameter_names(contract)
        | {str(row.get("name", "")).strip() for row in contract.get("three_d_fields", []) if row.get("name")}
        | {"T", "pi"}
    )
    used_names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression))
    replacements: dict[str, str] = {}
    for name in sorted(used_names, key=len, reverse=True):
        if name not in protected_names:
            continue
        target = _matched_coefficient_name(name, coefficient_names)
        if target:
            replacements[name] = target
    if not replacements:
        return expression, ""
    rewritten = expression
    for source, target in replacements.items():
        rewritten = re.sub(rf"\b{re.escape(source)}\b", target, rewritten)
    notes = ", ".join(f"{source}->{target}" for source, target in sorted(replacements.items()))
    return rewritten, notes


def _matched_coefficient_name(name: str, coefficient_names: set[str]) -> str:
    candidates = (
        f"{name}3dUS",
        f"{name}_3dUS",
        f"{name}3d",
        f"{name}_3d",
    )
    for candidate in candidates:
        if candidate in coefficient_names:
            return candidate
    return ""


def _squared_beta_base(parameter: str, allowed_names: set[str]) -> str:
    for suffix in ("_sq", "sq"):
        if parameter.endswith(suffix):
            base = parameter[: -len(suffix)]
            if base in allowed_names:
                return base
    return ""


def _coefficient_names_after_merge(rows: list[dict[str, str]], contract: dict[str, Any]) -> set[str]:
    merged = {row["name"] for row in rows if row.get("name")}
    existing = {str(row.get("name", "")).strip() for row in contract.get("three_d_coefficients", []) if row.get("name")}
    return {name for name in (merged | existing) if name and not name.casefold().startswith("ask_user")}


def _input_parameter_names(contract: dict[str, Any]) -> set[str]:
    return {
        str(row.get("name", "")).strip()
        for row in contract.get("input_parameters", [])
        if row.get("name") and not str(row.get("name")).casefold().startswith("ask_user")
    }


def _integrated_soft_indices(contract: dict[str, Any]) -> set[str]:
    soft_rows = [row for row in contract.get("eft_stages", []) if str(row.get("stage", "")).strip().casefold() == "soft"]
    result: set[str] = set()
    for row in soft_rows:
        result.update(re.findall(r"\d+", str(row.get("integrated_scalar_indices", ""))))
    return result


def _depends_on(expression: str) -> str:
    names = sorted(set(re.findall(r"\b[A-Za-z][A-Za-z0-9_]*\b", expression)) - {"sqrt", "pow", "sin", "cos", "tan", "exp", "log"})
    return ",".join(names) if names else "none"


def _missing_cell(value: Any) -> bool:
    normalized = str(value or "").strip().strip("`").casefold()
    return not normalized or normalized.startswith("ask_user") or normalized in {"todo", "tbd", "unknown", "required", "?"}


__all__ = ["MERGED_CONTRACT_NAME", "MergeResult", "merge_dralgo_output_into_contract"]
