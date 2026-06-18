from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .contract import ThreeDeftBlocked, ensure_ready, parse_contract, validate_contract_template
from .environment import ensure_dralgo_ready
from .layout import proof_dir_for_template
from .mathematica_expr import convert_mathematica_inputform_expression, looks_like_mathematica_inputform, repair_reserved_identifier
from .merge import merge_dralgo_output_into_contract
from .scriptgen import generate_marked_dralgo_script


DRALGO_OUTPUT_NAME = "dralgo_3deft_output.json"
DRALGO_PROBE_OUTPUT_NAME = "dralgo_3deft_output_probe.json"
AUTOSTAGE_CONTRACT_NAME = "three_deft_contract_autostage.md"
DRALGO_OUTPUT_SCHEMA = "ptagent.3deft.dralgo_output.v1"
WOLFRAM_MESSAGE_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9$`]*::[A-Za-z][A-Za-z0-9$`]*\b")


def run_dralgo_template(
    template_path: str | Path,
    *,
    output_path: str | Path | None = None,
    wolframscript_path: str | None = None,
    install_dralgo_if_missing: bool = False,
    install_source: str = "wolfram",
) -> Path:
    """Run the reviewed DRalgo source and normalize its marked output."""

    template_path = Path(template_path)
    markdown = template_path.read_text(encoding="utf-8")
    report = validate_contract_template(markdown)
    ensure_ready(report, for_dralgo_run=True)
    contract = parse_contract(markdown)
    runner = contract.get("runner", {})

    executable_hint = wolframscript_path or runner.get("wolframscript_path") or "wolframscript"
    executable = ensure_dralgo_ready(
        str(executable_hint),
        install_if_missing=install_dralgo_if_missing,
        install_source=install_source,
    )
    normalized, capture_labels = _run_capture_stage(template_path, contract=contract, executable=executable)
    proof_dir = proof_dir_for_template(template_path)
    if _should_auto_full_capture(normalized, capture_labels):
        probe_path = proof_dir / DRALGO_PROBE_OUTPUT_NAME
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        autostage_path = proof_dir / AUTOSTAGE_CONTRACT_NAME
        merge_dralgo_output_into_contract(template_path, probe_path, output_path=autostage_path)
        autostage_markdown = autostage_path.read_text(encoding="utf-8")
        autostage_contract = parse_contract(autostage_markdown)
        full_normalized, full_capture_labels = _run_capture_stage(autostage_path, contract=autostage_contract, executable=executable)
        if _has_full_capture_labels(full_capture_labels):
            full_normalized["auto_two_stage"] = {
                "enabled": True,
                "probe_output": str(probe_path),
                "autostage_contract": str(autostage_path),
                "probe_capture_labels": list(capture_labels),
                "full_capture_labels": list(full_capture_labels),
            }
            normalized = full_normalized
        else:
            normalized["auto_two_stage"] = {
                "enabled": False,
                "probe_output": str(probe_path),
                "autostage_contract": str(autostage_path),
                "reason": "Scalar map autostage did not enable soft/full DRalgo capture labels.",
                "probe_capture_labels": list(capture_labels),
                "full_capture_labels": list(full_capture_labels),
            }

    target = Path(output_path) if output_path else proof_dir_for_template(template_path) / DRALGO_OUTPUT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _run_capture_stage(
    template_path: Path,
    *,
    contract: dict[str, Any],
    executable: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    script_result = generate_marked_dralgo_script(template_path)
    program_path = script_result.output_path
    completed = subprocess.run(
        [str(executable), "-file", str(program_path)],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ThreeDeftBlocked(f"DRalgo wolframscript run failed with code {completed.returncode}: {completed.stderr[:600]}")
    normalized = normalize_dralgo_output(completed.stdout, source_label=str(program_path), contract=contract)
    wolfram_messages = _wolfram_messages(completed.stdout + "\n" + completed.stderr)
    captured = normalized.get("captured_outputs") or {}
    if script_result.capture_labels and not captured:
        details = "\n".join(part for part in (completed.stdout[-1200:].strip(), completed.stderr[-1200:].strip()) if part)
        raise ThreeDeftBlocked(
            "DRalgo run did not produce any PTAGENT capture markers."
            + (f"\nWolfram output tail:\n{details}" if details else "")
        )
    missing_labels = [label for label in script_result.capture_labels if label not in captured]
    if missing_labels:
        normalized["missing_capture_labels"] = missing_labels
    unevaluated_labels = _unevaluated_capture_labels(captured)
    if unevaluated_labels:
        raise ThreeDeftBlocked(
            "DRalgo run produced unevaluated capture outputs; the reviewed source likely failed before these calls: "
            + ", ".join(unevaluated_labels)
        )
    normalized["wolframscript"] = {
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "messages": wolfram_messages,
    }
    if wolfram_messages:
        normalized["warnings"] = list(normalized.get("warnings") or []) + [
            "wolframscript printed Mathematica messages; output was accepted because the process exited successfully and PTAGENT capture markers were parsed."
        ]
    return normalized, script_result.capture_labels


def _should_auto_full_capture(normalized: dict[str, Any], capture_labels: tuple[str, ...]) -> bool:
    if _has_full_capture_labels(capture_labels):
        return False
    if not normalized.get("scalar_rep_positions"):
        return False
    captured = normalized.get("captured_outputs") or {}
    return "PrintScalarRepPositions" in captured


def _has_full_capture_labels(capture_labels: tuple[str, ...]) -> bool:
    full_prefixes = (
        "PrintCouplingsUS",
        "PrintScalarMassUS",
        "PrintEffectivePotential",
        "PrintPressureUS",
        "BetaFunctions3DS",
        "BetaFunctions3DUS",
    )
    return any(label.startswith(full_prefixes) for label in capture_labels)


def normalize_dralgo_output(raw_text: str, *, source_label: str, contract: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any]
    try:
        parsed = json.loads(raw_text)
        data = parsed if isinstance(parsed, dict) else {"raw": parsed}
    except json.JSONDecodeError:
        data = _parse_text_output(raw_text)
    marked = _parse_ptagent_markers(raw_text)
    if marked:
        data["ptagent_marked_outputs"] = marked
    effective_potential = _collect_marked_or_data(
        data,
        (
            "PrintEffectivePotential_LO",
            "PrintEffectivePotential_LO_expand",
            "PrintEffectivePotential_NLO",
            "PrintEffectivePotential_NNLO",
        ),
        "effective_potential",
        "PrintEffectivePotential",
        "print_effective_potential",
    )
    field_map = _field_symbol_map(contract)
    beta_functions = _collect_marked_or_data(
        data,
        ("BetaFunctions4D", "BetaFunctions3DS", "BetaFunctions3DUS"),
        "beta_functions",
        "BetaFunctions",
        "beta_functions",
    )
    couplings = _collect_marked_or_data(
        data,
        ("PrintCouplings", "PrintTemporalScalarCouplings", "PrintCouplingsUS"),
        "couplings",
        "print_couplings",
    )
    scalar_masses = _collect_marked_or_data(
        data,
        (
            "PrintScalarMass_LO",
            "PrintScalarMass_NLO",
            "PrintScalarMass_NNLO",
            "PrintDebyeMass_LO",
            "PrintDebyeMass_NLO",
            "PrintDebyeMass_NNLO",
            "PrintScalarMassUS_LO",
            "PrintScalarMassUS_NLO",
            "PrintScalarMassUS_NNLO",
        ),
        "scalar_masses",
        "print_scalar_mass",
    )

    return {
        "schema": DRALGO_OUTPUT_SCHEMA,
        "source_label": source_label,
        "model_name": contract.get("model_name", "three_deft_model"),
        "orders": {
            "hard_matching_order": contract.get("rg_matching", {}).get("hard_matching_order", ""),
            "soft_matching_order": contract.get("rg_matching", {}).get("soft_matching_order", ""),
            "effective_potential_order": contract.get("rg_matching", {}).get("effective_potential_order", ""),
            "pressure_order": contract.get("rg_matching", {}).get("pressure_order", ""),
        },
        "scalar_index_map": contract.get("scalar_index_map", []),
        "eft_stages": contract.get("eft_stages", []),
        "input_parameters": contract.get("input_parameters", []),
        "symbol_audit": contract.get("symbol_audit", []),
        "phasetracer_interface": contract.get("phasetracer_interface", []),
        "scalar_rep_positions": _pick_marked_or_data(data, "PrintScalarRepPositions", "scalar_rep_positions", "PrintScalarRepPositions", "print_scalar_rep_positions"),
        "fermion_rep_positions": _pick_marked_or_data(data, "PrintFermionRepPositions", "fermion_rep_positions", "PrintFermionRepPositions", "print_fermion_rep_positions"),
        "couplings": couplings,
        "couplings_phase_tracer": _convert_rule_collection(couplings),
        "scalar_masses": scalar_masses,
        "scalar_masses_phase_tracer": _convert_rule_collection(scalar_masses),
        "effective_potential": effective_potential,
        "effective_potential_phase_tracer": _convert_effective_potential(effective_potential, field_map=field_map),
        "pressure": _collect_marked_or_data(data, ("PrintPressureUS_LO", "PrintPressureUS_NLO", "PrintPressureUS_NNLO"), "pressure", "PrintPressure", "print_pressure"),
        "beta_functions": beta_functions,
        "beta_functions_phase_tracer": _convert_beta_functions(beta_functions),
        "captured_outputs": marked,
        "raw_output": data,
    }


def _parse_text_output(raw_text: str) -> dict[str, Any]:
    data: dict[str, Any] = {"text": raw_text}
    for label in (
        "PrintScalarRepPositions",
        "PrintCouplings",
        "PrintScalarMass",
        "PrintEffectivePotential",
        "PrintPressure",
        "BetaFunctions",
    ):
        extracted = _extract_labeled_json(raw_text, label)
        if extracted is not None:
            data[label] = extracted
    return data


def _parse_ptagent_markers(raw_text: str) -> dict[str, list[str]]:
    pattern = re.compile(r"<<<PTAGENT_BEGIN:([^>]+)>>>(.*?)<<<PTAGENT_END:\1>>>", flags=re.DOTALL)
    outputs: dict[str, list[str]] = {}
    for match in pattern.finditer(raw_text):
        label = match.group(1).strip()
        body = match.group(2).strip()
        outputs.setdefault(label, []).append(body)
    return outputs


def _wolfram_messages(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in WOLFRAM_MESSAGE_RE.finditer(text):
        message = match.group(0)
        if message in seen:
            continue
        seen.add(message)
        result.append(message)
    return result


def _unevaluated_capture_labels(captured: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for label, values in captured.items():
        if not isinstance(values, list):
            continue
        function_name = _capture_function_name(label)
        for value in values:
            if _looks_like_unevaluated_call(str(value), function_name):
                result.append(label)
                break
    return result


def _capture_function_name(label: str) -> str:
    for prefix in (
        "PrintEffectivePotential",
        "PrintPressureUS",
        "PrintScalarMassUS",
        "PrintScalarMass",
        "PrintDebyeMass",
        "BetaFunctions3DUS",
        "BetaFunctions3DS",
        "BetaFunctions4D",
        "PrintCouplingsUS",
        "PrintTemporalScalarCouplings",
        "PrintCouplings",
        "PrintScalarRepPositions",
    ):
        if label.startswith(prefix):
            return prefix
    return label.split("_", 1)[0]


def _looks_like_unevaluated_call(value: str, function_name: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    return bool(re.fullmatch(rf"{re.escape(function_name)}\s*\[.*\]", stripped, flags=re.DOTALL))


def _extract_labeled_json(text: str, label: str) -> Any:
    marker = f"{label}:"
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    line = text[start:].splitlines()[0].strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return line


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _pick_marked_or_data(data: dict[str, Any], marker: str, *keys: str) -> Any:
    marked = data.get("ptagent_marked_outputs", {})
    if isinstance(marked, dict) and marker in marked:
        values = marked[marker]
        if isinstance(values, list):
            return values[0] if len(values) == 1 else values
    return _pick(data, *keys)


def _collect_marked_or_data(data: dict[str, Any], markers: tuple[str, ...], *keys: str) -> Any:
    marked = data.get("ptagent_marked_outputs", {})
    collected: dict[str, list[str]] = {}
    if isinstance(marked, dict):
        for marker in markers:
            values = marked.get(marker)
            if isinstance(values, list):
                collected[marker] = values
    if collected:
        return collected
    return _pick(data, *keys)


def _convert_effective_potential(effective_potential: Any, *, field_map: dict[str, str]) -> Any:
    if effective_potential is None:
        return None
    if isinstance(effective_potential, str):
        return _convert_effective_potential_expression(effective_potential, field_map=field_map)
    if isinstance(effective_potential, list):
        return [_convert_effective_potential(item, field_map=field_map) for item in effective_potential]
    if isinstance(effective_potential, dict):
        return {
            key: [_convert_effective_potential_expression(item, field_map=field_map) for item in value]
            if isinstance(value, list)
            else _convert_effective_potential(value, field_map=field_map)
            for key, value in effective_potential.items()
        }
    return None


def _convert_effective_potential_expression(expression: Any, *, field_map: dict[str, str]) -> dict[str, Any]:
    raw = str(expression).strip()
    if not raw:
        return {"source_expression": raw, "phase_tracer_expression": ""}
    should_convert = looks_like_mathematica_inputform(raw) or ("^" in raw and "**" not in raw)
    if should_convert:
        converted = convert_mathematica_inputform_expression(raw, field_map=field_map)
        return {
            "source_format": converted.source_format,
            "source_expression": raw,
            "phase_tracer_expression": converted.expression,
            "symbol_map": converted.symbol_map,
            "cform_symbol_map": converted.cform_symbol_map,
            "cform_post_map": converted.cform_post_map,
            "wolfram_replacement_rules": converted.wolfram_replacement_rules,
        }
    return {
        "source_format": "python_like",
        "source_expression": raw,
        "phase_tracer_expression": raw,
        "symbol_map": {},
        "cform_symbol_map": {},
        "cform_post_map": {},
        "wolfram_replacement_rules": "{}",
    }


def _convert_beta_functions(beta_functions: Any) -> Any:
    if beta_functions is None:
        return None
    if isinstance(beta_functions, str):
        return _convert_beta_expression(beta_functions)
    if isinstance(beta_functions, list):
        converted_items: list[Any] = []
        for item in beta_functions:
            converted = _convert_beta_functions(item)
            if isinstance(converted, list):
                converted_items.extend(converted)
            elif converted is not None:
                converted_items.append(converted)
        return converted_items
    if isinstance(beta_functions, dict):
        return {str(key): _convert_beta_functions(value) for key, value in beta_functions.items()}
    return None


def _convert_rule_collection(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _convert_rule_text(value)
    if isinstance(value, list):
        converted = [_convert_rule_collection(item) for item in value]
        flattened: list[Any] = []
        for item in converted:
            if isinstance(item, list):
                flattened.extend(item)
            elif item is not None:
                flattened.append(item)
        return flattened
    if isinstance(value, dict):
        return {str(key): _convert_rule_collection(item) for key, item in value.items()}
    return None


def _convert_rule_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, expression in _parse_rule_text(text):
        converted = convert_mathematica_inputform_expression(expression)
        rows.append(
            {
                "name": name,
                "source_expression": expression,
                "phase_tracer_expression": converted.expression,
                "symbol_map": converted.symbol_map,
                "cform_symbol_map": converted.cform_symbol_map,
                "cform_post_map": converted.cform_post_map,
                "wolfram_replacement_rules": converted.wolfram_replacement_rules,
            }
        )
    return rows


def _parse_rule_text(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for piece in _split_top_level_items(text.strip()):
        candidate = piece.strip().strip("{}")
        if not candidate:
            continue
        rule = _split_top_level_rule(candidate)
        if rule:
            raw_name, expression = rule
            rows.append((_compiler_rule_name(raw_name), expression.strip()))
            continue
        rule_match = re.match(r"^Rule\[\s*(.+)\s*,\s*(.+)\]$", candidate)
        if rule_match:
            rows.append((_compiler_rule_name(rule_match.group(1)), rule_match.group(2).strip()))
    return rows


def _split_top_level_items(text: str) -> list[str]:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        stripped = stripped[1:-1]
    pieces: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(stripped):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            pieces.append(stripped[start:index])
            start = index + 1
    pieces.append(stripped[start:])
    return [piece for piece in pieces if piece.strip()]


def _split_top_level_rule(text: str) -> tuple[str, str] | None:
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0:
            if text.startswith("->", index) or text.startswith("=>", index):
                return text[:index].strip(), text[index + 2 :].strip()
            if char == ":":
                return text[:index].strip(), text[index + 1 :].strip()
        index += 1
    return None


def _compiler_rule_name(raw_name: str) -> str:
    converted = convert_mathematica_inputform_expression(raw_name.strip()).expression
    power2 = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\*\*2", converted)
    if power2:
        return repair_reserved_identifier(power2.group(1) + "_sq")
    powern = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\*\*(\d+)", converted)
    if powern:
        return repair_reserved_identifier(f"{powern.group(1)}_pow{powern.group(2)}")
    cleaned = re.sub(r"\W+", "_", converted).strip("_")
    if not cleaned:
        cleaned = re.sub(r"\W+", "_", raw_name.strip()).strip("_") or "unnamed"
    if cleaned[:1].isdigit():
        cleaned = "p_" + cleaned
    return repair_reserved_identifier(cleaned)


def _convert_beta_expression(expression: Any) -> Any:
    raw = str(expression).strip()
    if not raw:
        return {"source_expression": raw, "phase_tracer_expression": ""}
    parsed_rules = _parse_rule_text(raw)
    if parsed_rules:
        rows: list[dict[str, Any]] = []
        for parameter, rhs in parsed_rules:
            converted = convert_mathematica_inputform_expression(rhs)
            rows.append(
                {
                    "parameter": parameter,
                    "source_expression": rhs,
                    "phase_tracer_expression": converted.expression,
                    "source_format": converted.source_format,
                    "symbol_map": converted.symbol_map,
                    "cform_symbol_map": converted.cform_symbol_map,
                    "cform_post_map": converted.cform_post_map,
                    "wolfram_replacement_rules": converted.wolfram_replacement_rules,
                }
            )
        return rows
    should_convert = looks_like_mathematica_inputform(raw) or ("^" in raw and "**" not in raw)
    if should_convert:
        converted = convert_mathematica_inputform_expression(raw)
        return {
            "source_format": converted.source_format,
            "source_expression": raw,
            "phase_tracer_expression": converted.expression,
            "symbol_map": converted.symbol_map,
            "cform_symbol_map": converted.cform_symbol_map,
            "cform_post_map": converted.cform_post_map,
            "wolfram_replacement_rules": converted.wolfram_replacement_rules,
        }
    return {
        "source_format": "python_like",
        "source_expression": raw,
        "phase_tracer_expression": raw,
        "symbol_map": {},
        "cform_symbol_map": {},
        "cform_post_map": {},
        "wolfram_replacement_rules": "{}",
    }


def _field_symbol_map(contract: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in contract.get("three_d_fields", []):
        raw_symbol = str(row.get("dralgo_background_symbol", "")).strip()
        field_name = str(row.get("name", "")).strip()
        if raw_symbol and field_name and raw_symbol.casefold() != "ask_user":
            result[raw_symbol] = field_name
    return result
