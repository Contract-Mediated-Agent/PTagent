from __future__ import annotations

import math
from typing import Any

from .safe_expression import evaluate_safe_expression


OS_MASS_RELATIVE_CUTOFF = 1e-12
OS_MASS_SCALE_FLOOR = 1.0
OS_MASS_CRITERION = {
    "relative_cutoff": OS_MASS_RELATIVE_CUTOFF,
    "mass_squared_scale_floor": OS_MASS_SCALE_FLOOR,
    "exclude_when": "abs(m2_vac) <= relative_cutoff * max(1.0, max(abs(m2_vac_group)))",
    "groups": "all bosons (scalars and vectors combined); fermions separately",
    "reference": "zero-temperature vacuum masses squared, in contract units",
}
TMAX_IMPACT = (
    "Transitions above Tmax will not be searched; a larger range increases runtime. "
    "Check high-temperature phases and range convergence; this is not a universal physical bound."
)


def os_mass_scale(vacuum_masses: list[float]) -> float:
    return max(OS_MASS_SCALE_FLOOR, max((abs(value) for value in vacuum_masses), default=0.0))


def _positive_number(value: Any, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"numerical_settings.{key} must be a finite positive number.") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"numerical_settings.{key} must be a finite positive number.")
    return number


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() not in {"", "auto", "ASK_USER", "TBD"}


def _recommend_tmax(contract: dict[str, Any], raw: dict[str, Any]) -> tuple[float, str]:
    if _present(raw.get("source_temperature_max")):
        value = _positive_number(raw["source_temperature_max"], "source_temperature_max")
        return value, "Upper endpoint of the source temperature range supplied in numerical_settings.source_temperature_max."
    if _present(raw.get("model_scale")):
        scale = _positive_number(raw["model_scale"], "model_scale")
        return _positive_number(4.0 * scale, "Tmax"), f"Exploratory range of four times the supplied model scale ({scale:g}), in contract units."

    params = contract.get("parameters") or {}
    env: dict[str, float] = {}
    for row in [*params.get("public_inputs", []), *params.get("constants", []), *params.get("derived", [])]:
        expr = row.get("value", row.get("default", row.get("test_value", row.get("expr", ""))))
        try:
            value = evaluate_safe_expression(str(expr), env)
            if math.isfinite(value):
                env[str(row.get("name", ""))] = value
        except (ValueError, TypeError, ArithmeticError):
            continue
    scales: list[tuple[float, str]] = []
    for row in contract.get("fields", []):
        try:
            value = abs(evaluate_safe_expression(str(row.get("zeroT_default", "")), env))
            if math.isfinite(value) and value > 0.0:
                scales.append((value, f"vacuum field {row.get('name', '')}"))
        except (ValueError, TypeError, ArithmeticError):
            continue
    # Use explicitly dimensional parameter labels, not arbitrary large couplings.
    for row in [*params.get("public_inputs", []), *params.get("constants", [])]:
        name = str(row.get("name", ""))
        role = str(row.get("role", "")).strip().lower()
        if name in env and (role in {"mass", "mass_scale", "renormalization_scale"} or name.lower() in {"mur", "qren"}):
            if abs(env[name]) > 0.0:
                scales.append((abs(env[name]), f"model parameter {name}"))
    if scales:
        scale, origin = max(scales)
        return _positive_number(4.0 * scale, "Tmax"), f"Exploratory range of four times {origin} ({scale:g}), evaluated at the contract benchmark."
    return 1000.0, (
        "No reliable temperature range or dimensional model scale was available. "
        "1000 is only an initial electroweak-scale exploration suggestion if contract units are GeV; "
        "replace it for other scales or units before approving."
    )


def resolve_numerical_settings(contract: dict[str, Any]) -> dict[str, Any]:
    raw = contract.get("numerical_settings") or {}
    if not isinstance(raw, dict):
        raise ValueError("numerical_settings must be a mapping.")
    explicit = _present(raw.get("Tmax"))
    if explicit:
        tmax = _positive_number(raw["Tmax"], "Tmax")
        basis = str(raw.get("Tmax_basis") or "Explicit numerical search limit supplied in the contract.")
    else:
        tmax, basis = _recommend_tmax(contract, raw)
    approved_flag = str(raw.get("Tmax_approved", "false")).strip().lower() in {"true", "yes", "1", "approved"}
    approved_value = None
    if _present(raw.get("Tmax_approved_value")):
        approved_value = _positive_number(raw["Tmax_approved_value"], "Tmax_approved_value")
    approved = explicit and approved_flag and approved_value == tmax
    return {
        "Tmax": tmax,
        "Tmax_basis": basis,
        "Tmax_impact": TMAX_IMPACT,
        "Tmax_approved": approved,
        "Tmax_approved_value": approved_value,
        "scan_status": "approved" if approved else "waiting_for_Tmax_approval",
        "x_eps": _positive_number(raw.get("x_eps", 0.001), "x_eps"),
        "x_eps_scope": "CosmoTransitions finite-difference field step",
    }


def scan_approval_message(settings: dict[str, Any]) -> str:
    return (
        f"Tmax={settings['Tmax']:g}. Basis: {settings['Tmax_basis']} "
        f"Impact: {settings['Tmax_impact']} "
        "Automatic temperature scans are blocked until the user confirms this range. "
        "In contract section '11. Numerical Settings', set Tmax to the confirmed value, "
        "Tmax_approved=true and Tmax_approved_value to that same value, then regenerate. "
        "Potential evaluation and non-transition smoke checks do not require this approval."
    )
