from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ThreeDeftBlocked, build_contract_template, validate_contract_template
from .mathematica_expr import convert_mathematica_inputform_expression, repair_reserved_identifier
from .wolfram_source import (
    format_multiple_perform_drsoft_message,
    perform_drsoft_calls,
    strip_mathematica_comments,
    wolfram_calls,
)


EXTRACTION_REPORT_NAME = "three_deft_extraction_report.json"


@dataclass(frozen=True)
class SourceExtractionResult:
    template: str
    report: dict[str, Any]


def extract_source_to_template(source_path: str | Path, *, model_name: str = "") -> SourceExtractionResult:
    source = Path(source_path).resolve()
    text, method = read_source_text(source)
    _ensure_single_perform_drsoft(text, source_label=str(source))
    code_text = strip_mathematica_comments(text)
    template = build_contract_template(
        source_path=str(source),
        source_sha256=_sha256(source),
        model_name=model_name or source.stem or "three_deft_model",
    )
    prefill, candidates, warnings = _extract_prefill(code_text, source=source)
    template = _apply_prefill(template, prefill)
    validation = validate_contract_template(template)
    report = {
        "schema": "ptagent.3deft.extraction_report.v1",
        "source_path": str(source),
        "text_extraction_method": method,
        "text_chars": len(text),
        "dralgo_like_source": _is_dralgo_like(code_text),
        "prefilled_fields": sorted(prefill.keys()),
        "candidates": candidates,
        "warnings": warnings,
        "ready_for_run_after_extract": validation.ready_for_run,
        "blocking_issue_count": len(validation.issues),
        "blocking_field_keys": [issue.field_key for issue in validation.issues],
    }
    return SourceExtractionResult(template=template, report=report)


def _ensure_single_perform_drsoft(text: str, *, source_label: str) -> None:
    calls = perform_drsoft_calls(text)
    if len(calls) > 1:
        raise ThreeDeftBlocked(format_multiple_perform_drsoft_message(calls, source_label=source_label))


def read_source_text(source_path: str | Path) -> tuple[str, str]:
    source = Path(source_path)
    suffix = source.suffix.casefold()
    if suffix not in {".m", ".wl"}:
        raise ThreeDeftBlocked("3DEFT extract accepts only reviewed DRalgo Mathematica source files (.m or .wl).")
    try:
        return source.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return source.read_text(encoding="latin-1"), "latin-1"


def _extract_prefill(text: str, *, source: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    warnings: list[str] = []
    candidates: dict[str, Any] = {
        "gauge_tokens": _gauge_tokens(text),
        "dralgo_calls": _call_candidates(text),
        "numeric_assignments": _numeric_assignments(text),
        "orders": _order_candidates(text),
        "soft_indices": _soft_indices(text),
    }
    prefill: dict[str, Any] = {}
    prefill["runner.dralgo_construction_source"] = "reviewed_source_file"
    prefill["runner.dralgo_program_path"] = str(source)
    if not _is_dralgo_like(text):
        warnings.append("No explicit DRalgo/Wolfram construction calls were found; physics tables remain ASK_USER blockers.")
        return prefill, candidates, warnings

    scalars = _scalar_rows(text)
    if scalars:
        prefill["scalar_multiplets"] = scalars
        prefill["dralgo_construction_fields"] = [
            {
                "dralgo_name": row["dralgo_symbol"],
                "parent_field": row["name"],
                "group_representation": row["representation"],
                "real_components": "ASK_USER",
                "evidence": row["evidence"],
            }
            for row in scalars
        ]
    else:
        warnings.append("DRalgo-like source found, but no RepScalar[...] call could be parsed.")

    fermions = _fermion_rows(text)
    if fermions:
        prefill["fermion_multiplets"] = fermions
        prefill["yukawa_sector"] = [
            {
                "policy": "ASK_USER",
                "retained_terms": _join_unique(row["yukawa_symbols"] for row in fermions),
                "ignored_terms": "ASK_USER",
                "evidence": "Yukawa candidates from DRalgo fermion calls; user must confirm retained/ignored policy.",
            }
        ]
    yukawa_map = _yukawa_construction_map_rows(text)
    if yukawa_map:
        prefill["yukawa_construction_map"] = yukawa_map
    scalar_potential_map = _scalar_potential_construction_map_rows(text)
    if scalar_potential_map:
        prefill["scalar_potential_construction_map"] = scalar_potential_map
    inputs = _input_parameter_rows(text)
    if inputs:
        prefill["input_parameters"] = inputs
        prefill["symbol_audit"] = _symbol_audit_rows_from_input_parameters(inputs)

    gauge_rows = _gauge_rows(text)
    if gauge_rows:
        prefill["gauge_groups"] = gauge_rows

    stages = _stage_rows(text)
    if stages:
        prefill["eft_stages"] = stages
    orders = _order_candidates(text)
    for key, value in orders.items():
        prefill[f"rg_matching.{key}"] = value
        prefill[f"rg_matching.{key.replace('_order', '_source')}"] = "dralgo_generated"

    scalar_positions = _scalar_positions_rows(text)
    if scalar_positions:
        prefill["scalar_index_map"] = scalar_positions
    else:
        warnings.append("PrintScalarRepPositions[] output was not found; soft matching remains blocked until scalar indices are mapped.")

    return prefill, candidates, warnings


def _apply_prefill(template: str, prefill: dict[str, Any]) -> str:
    replacements = {
        "gauge_groups": ("Gauge Groups", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "scalar_multiplets": ("Scalar Multiplets", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "fermion_multiplets": ("Fermion Multiplets", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "yukawa_sector": ("Yukawa Sector", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "yukawa_construction_map": (
            "Yukawa Construction Map",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | Review the source-derived coupling -> invariant_label -> operator_structure map; do not use xSM defaults unless the reviewed source actually contains them. |",
        ),
        "scalar_potential_construction_map": (
            "Scalar Potential Construction Map",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | Tree-level scalar potential terms must be reviewed before generating GradMass/GradQuartic/GradCubic/Tadpole code. |",
        ),
        "dralgo_construction_fields": ("DRalgo Construction Fields", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "input_parameters": ("Input Parameters", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |"),
        "symbol_audit": (
            "Mathematica Symbol Audit",
            "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
        ),
        "eft_stages": (
            "EFT Stages",
            "| hard | PerformDRhard[] | ASK_USER | none | ASK_USER | ASK_USER |\n"
            "| soft | PerformDRsoft[ASK_USER] | ASK_USER | ASK_USER | ASK_USER | ASK_USER |\n"
            "| ultrasoft | ASK_USER | ASK_USER | ASK_USER | ASK_USER | ASK_USER |",
        ),
        "scalar_index_map": ("Scalar Index Map", "| ASK_USER | ASK_USER | ASK_USER | ASK_USER | PrintScalarRepPositions[] required. |"),
    }
    table_columns = {
        "gauge_groups": ["name", "group", "coupling", "representation_notes", "evidence"],
        "scalar_multiplets": ["name", "representation", "components", "vev_policy", "dralgo_symbol", "evidence"],
        "fermion_multiplets": ["name", "representation", "retained", "yukawa_symbols", "evidence"],
        "yukawa_sector": ["policy", "retained_terms", "ignored_terms", "evidence"],
        "yukawa_construction_map": ["coupling", "invariant_label", "operator_structure", "invariant_expression", "source", "status", "notes"],
        "scalar_potential_construction_map": ["coupling", "invariant_label", "operator_structure", "potential_term", "tensor_derivative", "source", "status", "notes"],
        "dralgo_construction_fields": ["dralgo_name", "parent_field", "group_representation", "real_components", "evidence"],
        "input_parameters": ["name", "latex", "review_status", "test_value", "input_scale", "source", "notes"],
        "symbol_audit": ["raw_symbol", "suggested_symbol", "compiler_symbol", "physics_role", "cform_temp_symbol", "source", "status", "notes"],
        "eft_stages": ["stage", "command", "order", "integrated_scalar_indices", "generated_by", "evidence"],
        "scalar_index_map": ["scalar_index", "field_component", "parent_multiplet", "integrated_out", "print_scalar_rep_positions_evidence"],
    }
    for key, replacement in replacements.items():
        heading, placeholder = replacement
        rows = prefill.get(key)
        if rows:
            template = _replace_in_section(template, heading, placeholder, _markdown_rows(rows, table_columns[key]))
    for key, value in prefill.items():
        if key.startswith("rg_matching."):
            table_key = key.split(".", 1)[1]
            template = _replace_key_value(template, table_key, str(value), "Extracted from an explicit output call in the reviewed DRalgo source.")
        elif key == "runner.dralgo_program_path":
            template = _replace_key_value(template, "dralgo_program_path", str(value), "Extracted from source path.")
        elif key == "runner.dralgo_construction_source":
            template = _replace_key_value(template, "dralgo_construction_source", str(value), "Extracted from source path; user must review.")
    return template


def _markdown_rows(rows: list[dict[str, Any]], columns: list[str]) -> str:
    return "\n".join("| " + " | ".join(str(row.get(column, "ASK_USER")) for column in columns) + " |" for row in rows)


def _replace_key_value(template: str, key: str, value: str, notes: str) -> str:
    pattern = re.compile(rf"^\|\s*{re.escape(key)}\s*\|\s*.*?\s*\|\s*.*?\s*\|$", flags=re.MULTILINE)
    return pattern.sub(lambda _match: f"| {key} | {value} | {notes} |", template)


def _replace_in_section(template: str, heading: str, old: str, new: str) -> str:
    match = re.search(rf"^(#{{2,6}})\s+{re.escape(heading)}\s*$", template, flags=re.MULTILINE)
    if not match:
        return template
    level = len(match.group(1))
    start = match.end()
    next_heading = re.search(rf"^#{{1,{level}}}\s+", template[start:], flags=re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(template)
    section = template[start:end]
    if old not in section:
        return template
    section = section.replace(old, new, 1)
    return template[:start] + section + template[end:]


def _is_dralgo_like(text: str) -> bool:
    return bool(re.search(r"\b(RepScalar|RepFermion|AllocateTensors|ImportModelDRalgo|PerformDRhard|PerformDRsoft|PrintEffectivePotential|PrintScalarRepPositions|DRalgo`)", text))


def _call_candidates(text: str) -> dict[str, list[str]]:
    names = [
        "RepScalar",
        "RepFermion",
        "AllocateTensors",
        "ImportModelDRalgo",
        "PerformDRhard",
        "PerformDRsoft",
        "PrintScalarMass",
        "PrintScalarMassUS",
        "PrintEffectivePotential",
        "PrintPressure",
        "PrintPressureUS",
        "PrintScalarRepPositions",
    ]
    return {name: _extract_calls(text, name) for name in names if _extract_calls(text, name)}


def _extract_calls(text: str, name: str) -> list[str]:
    return wolfram_calls(text, name)


def _gauge_tokens(text: str) -> list[str]:
    tokens = set(re.findall(r"\b(?:SU|SO|Sp)\s*(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\d+)\b|\bU\s*(?:\[\s*1\s*\]|\(\s*1\s*\)|1)\b", text))
    return sorted(_compact_token(token) for token in tokens)


def _gauge_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, token in enumerate(_gauge_tokens(text), start=1):
        coupling = _guess_coupling(token)
        rows.append(
            {
                "name": f"gauge_{index}",
                "group": token,
                "coupling": coupling,
                "representation_notes": "ASK_USER",
                "evidence": "Candidate gauge token found in source; user must confirm representations.",
            }
        )
    return rows


def _scalar_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, call in enumerate(_extract_calls(text, "RepScalar"), start=1):
        symbol = _first_symbol(_inside_call(call)) or f"scalar_{index}"
        rows.append(
            {
                "name": symbol,
                "representation": _truncate(call),
                "components": "ASK_USER",
                "vev_policy": "ASK_USER",
                "dralgo_symbol": symbol,
                "evidence": "RepScalar[...] call extracted; user must confirm components and background fields.",
            }
        )
    return rows


def _fermion_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, call in enumerate(_extract_calls(text, "RepFermion"), start=1):
        symbol = _first_symbol(_inside_call(call)) or f"fermion_{index}"
        rows.append(
            {
                "name": symbol,
                "representation": _truncate(call),
                "retained": "ASK_USER",
                "yukawa_symbols": _join_unique(_yukawa_candidates(call)) or "ASK_USER",
                "evidence": "RepFermion[...] call extracted; user must confirm retained/ignored policy.",
            }
        )
    return rows


def _yukawa_construction_map_rows(text: str) -> list[dict[str, str]]:
    invariant_labels = set(re.findall(r"\b([A-Za-z$][A-Za-z0-9$]*)\s*=\s*CreateInvariantYukawa\s*\[", text))
    grad_match = re.search(r"\b[A-Za-z$][A-Za-z0-9$]*\s*=\s*-?\s*GradYukawa\s*\[(.*?)\]\s*;", text, flags=re.DOTALL)
    if not grad_match:
        return []
    expression = re.sub(r"\s+", " ", grad_match.group(1)).strip()
    rows: list[dict[str, str]] = []
    for coupling, invariant in re.findall(r"\b([A-Za-z$][A-Za-z0-9$]*)\s*\*\s*([A-Za-z$][A-Za-z0-9$]*)\b", expression):
        if invariant_labels and invariant not in invariant_labels:
            continue
        rows.append(
            {
                "coupling": coupling,
                "invariant_label": invariant,
                "operator_structure": "ASK_USER",
                "invariant_expression": f"{coupling}*({invariant})",
                "source": "Extracted from GradYukawa[...] in DRalgo source.",
                "status": "needs_user_review",
                "notes": f"{invariant} is the local invariant label; {coupling} is the numeric Yukawa coupling. Ysff/YsffC are generated containers.",
            }
        )
    return rows


def _scalar_potential_construction_map_rows(text: str) -> list[dict[str, str]]:
    invariant_labels = set(re.findall(r"\b([A-Za-z$][A-Za-z0-9$]*)\s*=\s*CreateInvariant\s*\[", text))
    rows: list[dict[str, str]] = []
    for tensor_derivative in ("GradMass", "GradQuartic", "GradCubic", "GradTadpole", "Tadpole"):
        for call in _extract_calls(text, tensor_derivative):
            inside = _call_inside(call)
            for coupling, invariant in _coupling_invariant_terms(inside, invariant_labels):
                rows.append(
                    {
                        "coupling": coupling,
                        "invariant_label": invariant,
                        "operator_structure": "ASK_USER",
                        "potential_term": f"{coupling}*{invariant}",
                        "tensor_derivative": tensor_derivative,
                        "source": f"Extracted from {tensor_derivative}[...] in DRalgo source.",
                        "status": "needs_user_review",
                        "notes": "Review against the tree-level scalar potential before generating DRalgo tensor code.",
                    }
                )
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["coupling"], row["invariant_label"], row["tensor_derivative"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _call_inside(call: str) -> str:
    start = call.find("[")
    end = call.rfind("]")
    if start < 0 or end <= start:
        return call
    return call[start + 1 : end]


def _coupling_invariant_terms(expression: str, invariant_labels: set[str]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    if not invariant_labels:
        return terms
    compact = re.sub(r"\s+", "", expression)
    label_pattern = "|".join(re.escape(label) for label in sorted(invariant_labels, key=len, reverse=True))
    symbol = r"[A-Za-z$][A-Za-z0-9$]*"
    patterns = [
        rf"\b({symbol})\*(({label_pattern})(?:\^\d+)?)",
        rf"(({label_pattern})(?:\^\d+)?)\*({symbol})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            groups = match.groups()
            if len(groups) == 3:
                if re.fullmatch(symbol, groups[0]) and groups[1].startswith(groups[2]):
                    terms.append((groups[0], groups[1]))
                else:
                    terms.append((groups[2], groups[0]))
    return terms


def _input_parameter_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, value in _numeric_assignments(text).items():
        if name in {"i", "j", "k", "n", "T"}:
            continue
        converted = convert_mathematica_inputform_expression(name)
        compiler_name = repair_reserved_identifier(converted.expression)
        notes = "Numeric assignment extracted from source; user must confirm input scale."
        if compiler_name != name:
            notes += f" Source symbol {name} was normalized to compiler-safe identifier {compiler_name}."
        rows.append(
            {
                "name": compiler_name,
                "latex": name,
                "review_status": "needs_user_review",
                "test_value": value,
                "input_scale": "ASK_USER",
                "source": "numeric_assignment",
                "notes": notes,
            }
        )
    return rows


def _symbol_audit_rows_from_input_parameters(inputs: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in inputs:
        raw_symbol = row.get("latex") or row["name"]
        converted = convert_mathematica_inputform_expression(raw_symbol)
        suggested = repair_reserved_identifier(converted.expression)
        rows.append(
            {
                "raw_symbol": raw_symbol,
                "suggested_symbol": suggested,
                "compiler_symbol": suggested,
                "physics_role": "input_parameter",
                "cform_temp_symbol": converted.cform_symbol_map.get(raw_symbol, _fallback_cform_temp_symbol(suggested)),
                "source": "numeric_assignment",
                "status": "needs_user_review",
                "notes": "Agent-suggested symbol map from source assignment; user must confirm physics role and final compiler spelling.",
            }
        )
    return rows


def _fallback_cform_temp_symbol(identifier: str) -> str:
    pieces = re.findall(r"[A-Za-z0-9]+", identifier)
    compact = "".join(piece[:1].upper() + piece[1:] for piece in pieces) or "Symbol"
    if compact[:1].isdigit():
        compact = "Symbol" + compact
    return "pt" + compact


def _numeric_assignments(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"(?m)(?:^|(?<=;))\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;")
    for match in pattern.finditer(text):
        result.setdefault(match.group(1), match.group(2))
    return result


def _order_candidates(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    hard_order = _explicit_order_from_calls(text, "PrintScalarMass")
    if hard_order:
        result["hard_matching_order"] = hard_order
    soft_order = _explicit_order_from_calls(text, "PrintScalarMassUS")
    if soft_order:
        result["soft_matching_order"] = soft_order
    potential_order = _explicit_order_from_calls(text, "PrintEffectivePotential")
    if potential_order:
        result["effective_potential_order"] = potential_order
    pressure_order = _explicit_order_from_calls(text, "PrintPressureUS") or _explicit_order_from_calls(text, "PrintPressure")
    if pressure_order:
        result["pressure_order"] = pressure_order
    return result


def _explicit_order_from_calls(text: str, name: str) -> str:
    orders = _explicit_orders_from_calls(text, name)
    return orders[-1] if orders else ""


def _explicit_orders_from_calls(text: str, name: str) -> list[str]:
    rank = {"LO": 0, "NLO": 1, "NNLO": 2}
    orders: set[str] = set()
    for call in _extract_calls(text, name):
        match = re.search(r"\[\s*\"?(LO|NLO|NNLO)\"?\s*\]", call, flags=re.IGNORECASE)
        if match:
            orders.add(match.group(1).upper())
    return sorted(orders, key=lambda order: rank[order])


def _first_call(text: str, name: str) -> str:
    calls = _extract_calls(text, name)
    return calls[0] if calls else ""


def _soft_indices(text: str) -> tuple[bool, list[str]]:
    calls = perform_drsoft_calls(text)
    if not calls:
        return False, []
    match = re.search(r"PerformDRsoft\s*\[\s*\{([^}]*)\}", calls[0])
    if not match:
        return True, []
    return True, re.findall(r"\d+", match.group(1))


def _stage_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    orders = _order_candidates(text)
    if "PerformDRhard" in text:
        rows.append(
            {
                "stage": "hard",
                "command": "PerformDRhard[]",
                "order": orders.get("hard_matching_order", "not_applicable"),
                "integrated_scalar_indices": "none",
                "generated_by": "DRalgo",
                "evidence": "PerformDRhard[] call extracted.",
            }
        )
    if "PerformDRsoft" in text:
        has_soft_indices, indices = _soft_indices(text)
        formatted = "{" + ",".join(indices) + "}" if has_soft_indices else "ASK_USER"
        rows.append(
            {
                "stage": "soft",
                "command": f"PerformDRsoft[{formatted}]",
                "order": orders.get("soft_matching_order", "not_applicable"),
                "integrated_scalar_indices": ",".join(indices) if indices else "none" if has_soft_indices else "ASK_USER",
                "generated_by": "DRalgo",
                "evidence": "PerformDRsoft[...] call extracted; scalar index map still required.",
            }
        )
    if "PrintEffectivePotential" in text:
        order = orders.get("effective_potential_order", "ASK_USER")
        effective_orders = _explicit_orders_from_calls(text, "PrintEffectivePotential")
        command = ", ".join(f'PrintEffectivePotential["{item}"]' for item in effective_orders) if effective_orders else _first_call(text, "PrintEffectivePotential") or f'PrintEffectivePotential["{order}"]'
        rows.append(
            {
                "stage": "ultrasoft",
                "command": command,
                "order": order,
                "integrated_scalar_indices": "none",
                "generated_by": "DRalgo",
                "evidence": "PrintEffectivePotential[...] call extracted.",
            }
        )
    return rows


def _scalar_positions_rows(text: str) -> list[dict[str, str]]:
    json_match = re.search(r"PrintScalarRepPositions\s*:\s*(\[[^\n]+)", text)
    if not json_match:
        return []
    try:
        parsed = json.loads(json_match.group(1))
    except json.JSONDecodeError:
        return []
    rows: list[dict[str, str]] = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            index = item.get("index") or item.get("scalar_index") or item.get("position")
            field = item.get("field") or item.get("field_component") or item.get("component")
            parent = item.get("parent_multiplet") or item.get("multiplet") or item.get("field")
            if index is None or not field:
                continue
            rows.append(
                {
                    "scalar_index": str(index),
                    "field_component": str(field),
                    "parent_multiplet": str(parent or "ASK_USER"),
                    "integrated_out": "ASK_USER",
                    "print_scalar_rep_positions_evidence": "Extracted from labeled PrintScalarRepPositions output.",
                }
            )
    return rows


def _inside_call(call: str) -> str:
    start = call.find("[")
    end = call.rfind("]")
    return call[start + 1 : end] if start >= 0 and end > start else call


def _first_symbol(args: str) -> str:
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", args)
    return match.group(1) if match else ""


def _yukawa_candidates(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b[yY][A-Za-z0-9_]*\b", text)))


def _join_unique(values: Any) -> str:
    if isinstance(values, str):
        return values
    result: list[str] = []
    for value in values:
        if isinstance(value, list):
            result.extend(str(item) for item in value if item)
        elif value:
            result.append(str(value))
    return ",".join(sorted(set(result)))


def _compact_token(token: str) -> str:
    token = re.sub(r"\s+", "", token)
    token = token.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
    return token


def _guess_coupling(group: str) -> str:
    normalized = group.casefold()
    if normalized in {"u1"}:
        return "g1"
    if normalized in {"su2"}:
        return "g2"
    if normalized in {"su3"}:
        return "g3"
    return "ASK_USER"


def _truncate(value: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

