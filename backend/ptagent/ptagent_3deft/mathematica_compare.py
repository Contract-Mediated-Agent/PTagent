from __future__ import annotations

import ast
import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ThreeDeftBlocked, parse_contract
from .environment import find_wolframscript
from .mathematica_expr import looks_like_mathematica_inputform
from .phasetracer import _clean_expr, _field_symbol_map, _normalize_known_coefficient_references


MATHEMATICA_COMPARE_JSON = "mathematica_fixed_v3d_compare.json"
MATHEMATICA_COMPARE_SCRIPT = "mathematica_fixed_v3d_compare.wl"


@dataclass(frozen=True)
class MathematicaComparisonResult:
    report_path: Path
    script_path: Path
    ok: bool
    max_abs_diff: float | None
    comparison_count: int


def compare_mathematica_fixed_v3d(
    template_path: str | Path,
    project_dir: str | Path,
    *,
    wolframscript_path: str | None = None,
    output_path: str | Path | None = None,
    script_path: str | Path | None = None,
    tolerance: float = 1.0e-8,
    timeout_seconds: int = 120,
) -> MathematicaComparisonResult:
    """Evaluate reviewed V3D expressions in Mathematica by direct replacement.

    This check intentionally compares only the fixed 3D-parameter layer used by
    ``run_model.cpp``. It does not rerun 4D RG, matching, or 3DUS evolution.
    """

    template = Path(template_path)
    project = Path(project_dir)
    metadata_path = project / "metadata.json"
    if not template.exists():
        raise ThreeDeftBlocked(f"3DEFT contract template does not exist: {template}")
    if not metadata_path.exists():
        raise ThreeDeftBlocked(f"Generated PhaseTracer metadata does not exist: {metadata_path}")

    contract = parse_contract(template.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("reference_validation_mode") != "fixed_3d_parameters_direct_v3d":
        raise ThreeDeftBlocked("metadata.json does not contain a fixed_3d_parameters_direct_v3d reference.")

    comparisons = _comparison_specs(contract, metadata)
    values = _replacement_values(metadata)
    symbol_map = _safe_symbol_map(comparisons, values)
    rendered_comparisons = [
        {
            **item,
            "wolfram_expression": _python_expression_to_wolfram(item["expression"], symbol_map),
        }
        for item in comparisons
    ]
    rendered_rules = _replacement_rules(values, symbol_map)

    script = _render_wolfram_script(rendered_comparisons, rendered_rules, symbol_map)
    script_target = Path(script_path) if script_path else project / MATHEMATICA_COMPARE_SCRIPT
    report_target = Path(output_path) if output_path else project / MATHEMATICA_COMPARE_JSON
    script_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    script_target.write_text(script, encoding="utf-8")

    executable = find_wolframscript(wolframscript_path)
    if not executable:
        raise ThreeDeftBlocked("wolframscript was not found; cannot run Mathematica fixed-V3D comparison.")
    completed = subprocess.run(
        [executable, "-file", str(script_target)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        details = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        raise ThreeDeftBlocked(f"Mathematica fixed-V3D comparison failed with code {completed.returncode}." + (f"\n{details}" if details else ""))
    math_payload = _extract_mathematica_json(completed.stdout)

    report = _build_report(
        metadata=metadata,
        comparisons=rendered_comparisons,
        wolfram_payload=math_payload,
        symbol_map=symbol_map,
        replacement_values=values,
        tolerance=tolerance,
        stdout=completed.stdout,
        stderr=completed.stderr,
        script_path=script_target,
    )
    report_target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return MathematicaComparisonResult(
        report_path=report_target,
        script_path=script_target,
        ok=bool(report["ok"]),
        max_abs_diff=report["max_abs_diff"],
        comparison_count=len(report["comparisons"]),
    )


def _comparison_specs(contract: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    consumption = contract.get("three_d_consumption", {})
    fields = contract.get("three_d_fields", [])
    field_map = _field_symbol_map(fields)
    coefficients = contract.get("three_d_coefficients", [])
    coefficient_names = {str(row.get("name", "")).strip() for row in coefficients if str(row.get("name", "")).strip()}
    terms: list[tuple[str, str]] = []
    for order in ("LO", "NLO", "NNLO"):
        raw = consumption.get(f"v3d_expression_{order}", consumption.get(f"v3d_expression_{order.lower()}", ""))
        if _missing(raw):
            continue
        expression = _normalize_known_coefficient_references(_clean_expr(str(raw), field_symbol_map=field_map), coefficient_names)
        terms.append((order, expression))

    total_expression = _clean_expr(str(consumption.get("v3d_expression", "")), field_symbol_map=field_map)
    total_expression = _normalize_known_coefficient_references(total_expression, coefficient_names)
    if terms:
        total_expression = " + ".join(f"({expression})" for _order, expression in terms)

    fixed = metadata.get("fixed_3d_reference", {})
    ordered_expected = fixed.get("ordered_term_values_python", {}) or {}
    comparisons: list[dict[str, Any]] = []
    for order, expression in terms:
        comparisons.append(
            {
                "label": f"V3D_{order}",
                "expression": expression,
                "expected": _optional_float(ordered_expected.get(order)),
            }
        )
    comparisons.append(
        {
            "label": "V3D_total",
            "expression": total_expression,
            "expected": _optional_float(fixed.get("v3d_value_python")),
        }
    )
    prefactor_expression = _clean_expr(str(consumption.get("potential_prefactor", "T")), field_symbol_map=field_map)
    comparisons.append(
        {
            "label": "prefactor",
            "expression": prefactor_expression,
            "expected": _optional_float(fixed.get("prefactor_value_python")),
        }
    )
    comparisons.append(
        {
            "label": "V_total",
            "expression": f"({prefactor_expression})*({total_expression})",
            "expected": _optional_float(fixed.get("value_python")),
        }
    )
    return comparisons


def _replacement_values(metadata: dict[str, Any]) -> dict[str, float]:
    fixed = metadata.get("fixed_3d_reference", {})
    values: dict[str, float] = {}
    for group in ("fields_3d", "parameters_3d", "scale_aliases", "input_values"):
        for key, value in (fixed.get(group, {}) or {}).items():
            number = _optional_float(value)
            if number is not None:
                values[str(key)] = number
    for key, spec in (metadata.get("input_parameters", {}) or {}).items():
        number = _optional_float((spec or {}).get("test_value"))
        if number is not None:
            values.setdefault(str(key), number)
    temperature = _optional_float(fixed.get("temperature", metadata.get("reference_temperature")))
    if temperature is not None:
        values["T"] = temperature
    values.setdefault("pi", math.pi)
    return values


def _safe_symbol_map(comparisons: list[dict[str, Any]], values: dict[str, float]) -> dict[str, str]:
    names = set(values)
    for item in comparisons:
        names.update(_expression_names(item["expression"]))
    names -= {"pi", "EulerGamma", "Glaisher", "True", "False"}
    return {name: f"ptm{index}" for index, name in enumerate(sorted(names))}


def _replacement_rules(values: dict[str, float], symbol_map: dict[str, str]) -> list[tuple[str, float]]:
    rules: list[tuple[str, float]] = []
    for name, value in sorted(values.items()):
        if name == "pi":
            continue
        symbol = symbol_map.get(name)
        if symbol:
            rules.append((symbol, value))
    return rules


def _render_wolfram_script(
    comparisons: list[dict[str, Any]],
    rules: list[tuple[str, float]],
    symbol_map: dict[str, str],
) -> str:
    rule_text = ", ".join(f"{symbol} -> {_wolfram_number(value)}" for symbol, value in rules)
    expression_lines = "\n".join(
        f'  <|"label" -> "{item["label"]}", "value" -> evaluate[{item["wolfram_expression"]}]|>'
        + ("," if index + 1 < len(comparisons) else "")
        for index, item in enumerate(comparisons)
    )
    symbol_map_text = ", ".join(f'"{raw}" -> "{safe}"' for raw, safe in sorted(symbol_map.items()))
    return f"""(* Generated by PTagent 3DEFT. Evaluates fixed V3D by replacement only. *)
ClearAll["Global`*"];
replacementRules = {{{rule_text}}};
safeSymbolMap = <|{symbol_map_text}|>;
evaluate[expr_] := Module[{{value, realValue}},
  value = Quiet[Check[N[expr /. replacementRules, 30], Indeterminate]];
  realValue = If[NumberQ[value] && TrueQ[PossibleZeroQ[Im[value]]], N[Re[value], 17], Null];
  <|
    "real" -> realValue,
    "real_valued" -> (realValue =!= Null),
    "input_form" -> ToString[InputForm[value]]
  |>
];
results = {{
{expression_lines}
}};
payload = <|
  "schema" -> "ptagent.3deft.mathematica_fixed_v3d.v1",
  "replacement_rules" -> ToString[InputForm[replacementRules]],
  "safe_symbol_map" -> safeSymbolMap,
  "results" -> results
|>;
Print["<<<PTAGENT_MATHEMATICA_COMPARE_JSON>>>" ];
Print[ExportString[payload, "RawJSON", "Compact" -> True]];
Print["<<<PTAGENT_MATHEMATICA_COMPARE_JSON_END>>>" ];
"""


def _build_report(
    *,
    metadata: dict[str, Any],
    comparisons: list[dict[str, Any]],
    wolfram_payload: dict[str, Any],
    symbol_map: dict[str, str],
    replacement_values: dict[str, float],
    tolerance: float,
    stdout: str,
    stderr: str,
    script_path: Path,
) -> dict[str, Any]:
    by_label = {item.get("label"): item.get("value", {}) for item in wolfram_payload.get("results", [])}
    rows: list[dict[str, Any]] = []
    diffs: list[float] = []
    ok = True
    for item in comparisons:
        label = item["label"]
        expected = item.get("expected")
        value_record = by_label.get(label, {})
        mathematica_value = _optional_float(value_record.get("real"))
        real_valued = bool(value_record.get("real_valued"))
        diff = None
        row_ok = real_valued and expected is not None and mathematica_value is not None
        if row_ok:
            diff = abs(mathematica_value - expected)
            diffs.append(diff)
            row_ok = diff <= max(tolerance, tolerance * max(abs(expected), abs(mathematica_value), 1.0))
        ok = ok and row_ok
        rows.append(
            {
                "label": label,
                "expression": item["expression"],
                "wolfram_expression": item["wolfram_expression"],
                "mathematica_value": mathematica_value,
                "expected_value": expected,
                "abs_diff": diff,
                "ok": row_ok,
                "real_valued": real_valued,
                "mathematica_input_form": value_record.get("input_form", ""),
            }
        )
    return {
        "schema": "ptagent.3deft.mathematica_compare.v1",
        "mode": "fixed_3d_parameters_direct_v3d",
        "comparison_layer": "mathematica_replacement_vs_generated_direct_v3d_reference",
        "ok": ok,
        "tolerance": tolerance,
        "max_abs_diff": max(diffs) if diffs else None,
        "metadata_reference_mode": metadata.get("reference_validation_mode"),
        "script_path": str(script_path),
        "symbol_map": symbol_map,
        "replacement_values": replacement_values,
        "wolfram_replacement_rules": wolfram_payload.get("replacement_rules", ""),
        "comparisons": rows,
        "stdout_preview": stdout[:2000],
        "stderr_preview": stderr[:2000],
    }


def _extract_mathematica_json(stdout: str) -> dict[str, Any]:
    match = re.search(
        r"<<<PTAGENT_MATHEMATICA_COMPARE_JSON>>>\s*(.*?)\s*<<<PTAGENT_MATHEMATICA_COMPARE_JSON_END>>>",
        stdout,
        flags=re.DOTALL,
    )
    if not match:
        raise ThreeDeftBlocked("Mathematica comparison output did not contain the expected JSON marker.")
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError as exc:
        raise ThreeDeftBlocked(f"Mathematica comparison JSON could not be parsed: {exc}") from exc


def _python_expression_to_wolfram(expression: str, symbol_map: dict[str, str]) -> str:
    expr = expression.strip().strip("`")
    if looks_like_mathematica_inputform(expr):
        expr = _clean_expr(expr)
    tree = ast.parse(expr, mode="eval")
    return _WolframExpressionRenderer(symbol_map).render(tree.body)


class _WolframExpressionRenderer:
    def __init__(self, symbol_map: dict[str, str]) -> None:
        self.symbol_map = symbol_map

    def render(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return self._constant(node.value)
        if isinstance(node, ast.Name):
            return self._name(node.id)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow):
                return f"Power[{self.render(node.left)}, {self.render(node.right)}]"
            op = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
            }.get(type(node.op))
            if op is None:
                raise ThreeDeftBlocked(f"Unsupported Mathematica comparison operator {type(node.op).__name__}.")
            return f"({self.render(node.left)} {op} {self.render(node.right)})"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return f"(-{self.render(node.operand)})"
            if isinstance(node.op, ast.UAdd):
                return f"(+{self.render(node.operand)})"
            raise ThreeDeftBlocked(f"Unsupported Mathematica comparison unary operator {type(node.op).__name__}.")
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Attribute):
            return self._attribute_name(node)
        raise ThreeDeftBlocked(f"Unsupported Mathematica comparison expression node {type(node).__name__}.")

    def _constant(self, value: Any) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, int | float):
            return _wolfram_number(float(value))
        raise ThreeDeftBlocked(f"Unsupported Mathematica comparison constant {value!r}.")

    def _name(self, name: str) -> str:
        if name == "pi":
            return "Pi"
        if name in {"EulerGamma", "Glaisher", "True", "False"}:
            return name
        return self.symbol_map.setdefault(name, f"ptm{len(self.symbol_map)}")

    def _call(self, node: ast.Call) -> str:
        if node.keywords:
            raise ThreeDeftBlocked("Mathematica comparison does not support keyword arguments.")
        name = self._call_name(node.func)
        args = [self.render(arg) for arg in node.args]
        if name in {"sqrt", "Sqrt"}:
            _require_arg_count(name, args, 1)
            return f"Sqrt[{args[0]}]"
        if name in {"log", "Log"}:
            _require_arg_count(name, args, 1)
            return f"Log[{args[0]}]"
        if name in {"exp", "Exp"}:
            _require_arg_count(name, args, 1)
            return f"Exp[{args[0]}]"
        if name in {"sin", "Sin"}:
            _require_arg_count(name, args, 1)
            return f"Sin[{args[0]}]"
        if name in {"cos", "Cos"}:
            _require_arg_count(name, args, 1)
            return f"Cos[{args[0]}]"
        if name in {"tan", "Tan"}:
            _require_arg_count(name, args, 1)
            return f"Tan[{args[0]}]"
        if name in {"abs", "fabs", "Abs"}:
            _require_arg_count(name, args, 1)
            return f"Abs[{args[0]}]"
        if name in {"pow", "Power"}:
            _require_arg_count(name, args, 2)
            return f"Power[{args[0]}, {args[1]}]"
        if name in {"max", "Max"}:
            _require_arg_count(name, args, 2)
            return f"Max[{args[0]}, {args[1]}]"
        if name in {"min", "Min"}:
            _require_arg_count(name, args, 2)
            return f"Min[{args[0]}, {args[1]}]"
        raise ThreeDeftBlocked(f"Unsupported Mathematica comparison function {name!r}.")

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._attribute_name(node)
        raise ThreeDeftBlocked("Unsupported Mathematica comparison call target.")

    def _attribute_name(self, node: ast.Attribute) -> str:
        if isinstance(node.value, ast.Name) and node.value.id in {"math", "np"}:
            return node.attr
        raise ThreeDeftBlocked("Mathematica comparison supports only math.<func> or np.<func> attributes.")


def _require_arg_count(name: str, args: list[str], expected: int) -> None:
    if len(args) != expected:
        raise ThreeDeftBlocked(f"Mathematica comparison function {name} expects {expected} argument(s).")


def _expression_names(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names - {"sqrt", "log", "exp", "sin", "cos", "tan", "abs", "fabs", "pow", "max", "min", "math", "np"}


def _wolfram_number(value: float) -> str:
    if math.isnan(value):
        return "Indeterminate"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return format(float(value), ".17g").replace("e", "*^").replace("E", "*^")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _missing(value: Any) -> bool:
    raw = str(value or "").strip().strip("`")
    if not raw:
        return True
    return raw.casefold() in {"ask_user", "tbd", "todo", "required", "unknown", "?", "-", "none", "not_applicable"}


__all__ = [
    "MATHEMATICA_COMPARE_JSON",
    "MATHEMATICA_COMPARE_SCRIPT",
    "MathematicaComparisonResult",
    "compare_mathematica_fixed_v3d",
]
