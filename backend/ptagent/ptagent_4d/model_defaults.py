"""Model-independent review proposals, never evidence or user approval."""
from __future__ import annotations

import math
from typing import Any


DEFAULTS_ID = "source-first-4d-v1"
POLICY_DEFAULTS = {
    "backend": "cosmotransitions",
    "resummation_scheme": "Arnold-Espinosa",
    "goldstone": "included",
    "photon": "retained",
    "phase_filter": "none",
    "phasetracer_symmetry": "none",
    "cubic_power_policy": "positive_part",
}
DEFAULT_RULES = (
    "Source and explicit user choices take precedence over these proposals. "
    "Bind numerical parameters and the renormalization scale only from source, SLHA or explicit inputs; "
    "missing values remain blockers, not benchmark guesses. "
    "If Q is unresolved, propose the Euclidean norm of the complete finite nonzero source "
    "reference vacuum, in its field basis and contract units, as a convention pending explicit acceptance. "
    "Propose Arnold-Espinosa resummation, retaining the complete source spectrum without special "
    "Goldstone/photon removal; missing thermal data blocks this proposal rather than switching schemes. "
    "Keep an imported extra polynomial as a proposed separate correction when nonzero, otherwise propose none. "
    "Propose radiation counts from the retained spectrum only, explicitly listing any missing bath species. "
    "Propose PhaseTracer sign-flip generators or CosmoTransitions symmetry handling from the "
    "detected symmetry: independent sign cuts, or trajectory symmetry_orbits for coupled generators. "
    "Check loop, thermal, counterterm and gauge terms before final approval. "
    "All proposals require the same final human review for every model."
)
FILTER_GUIDANCE = (
    "If a full-potential sign symmetry is confirmed, suggest field < -epsilon with "
    "epsilon = 0.01*L, where L is a sourced nonzero reference field scale in that field's units. "
    "If a numerical field step is known, also require epsilon >= 10*abs(field_step). "
    "This is a starting tolerance, not a physical constant: vary it by a factor of two and check "
    "that the origin and distinct phases survive. With no reference scale, use the exact zero "
    "boundary (or ten numerical field steps if known); do not assume an electroweak scale. "
    "Present the concrete representative-region rule for review, not a recommendation to disable filtering."
)


def renormalization_scale_recommendation(model, explicit: dict) -> dict:
    """Keep source selections; a vacuum-based fallback is advice, never a binding."""
    from .safe_expression import evaluate_safe_expression

    scale_sq = model.renormalization_scale_sq
    if scale_sq is not None:
        origin = "explicit_input" if {"Q", "renormScaleSq"} & explicit.keys() else "source_selection"
        return {"status": "resolved", "Q": math.sqrt(scale_sq), "renormScaleSq": scale_sq,
                "applied": True, "origin": origin, "source_scale_policy": model.scale_policy,
                "basis": "Preserve the explicit scale override." if origin == "explicit_input" else
                         "Preserve the source scale selection evaluated from the parameter map/SLHA."}
    unresolved = {"status": "unresolved", "Q": None, "renormScaleSq": None, "applied": False,
                  "origin": "unresolved", "source_scale_policy": model.scale_policy,
                  "basis": "No source-selected scale or complete finite nonzero reference vacuum is resolved. Supply an explicit positive Q; do not infer a benchmark from the model name."}
    try:
        vacuum = {field: evaluate_safe_expression(model.dsb_minimum[field], model.parameter_values)
                  for field in sorted(model.fields)}
        if not vacuum or not all(math.isfinite(value) for value in vacuum.values()):
            return unresolved
        q = math.hypot(*vacuum.values())
        scale_sq = q * q
        if q <= 0 or not math.isfinite(scale_sq) or scale_sq <= 0:
            return unresolved
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return unresolved
    return {"status": "pending_review", "Q": q, "renormScaleSq": scale_sq, "applied": False,
            "origin": "reference_vacuum_convention", "source_scale_policy": model.scale_policy,
            "reference_vacuum": vacuum,
            "basis": "Propose Q = sqrt(sum_i v_i^2) from the complete source reference vacuum in the exported field basis and contract units. This is a deterministic scale convention, not a source-mandated value; review field normalization and scale dependence. Bind through explicit Q or renormScaleSq only after acceptance; an explicit override wins."}


def phase_filter_suggestion(field: str, reference_scale: Any = None, field_step: Any = None) -> dict:
    def positive(value):
        try:
            number = abs(float(value)) if not isinstance(value, bool) else 0.0
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    scale, step = positive(reference_scale), positive(field_step)
    epsilon = max(0.01 * scale if scale else 0.0, 10 * step if step else 0.0)
    return {
        "field": field, "threshold": -epsilon,
        "reference_scale": scale, "field_step": step,
        "enabled": True, "status": "pending_review", "reason": FILTER_GUIDANCE,
    }


def phase_handling_recommendation(contract: dict, backend: str) -> dict:
    """Propose concrete rules without equating tree evidence with final approval."""
    from ..symmetry_analysis import fundamental_domain_fields

    analysis = contract.get("symmetry_analysis", {})
    fields = [row["name"] for row in contract.get("fields", []) if row.get("name")]
    groups = [row["fields"] for row in analysis.get("candidates", []) if row.get("fields")]
    groups = [group for group in groups if all(f in fields for f in group)]
    review = "Check loop, thermal, counterterm and gauge terms for symmetry breaking before approving these rules."
    if not groups:
        return {"status": "unresolved", "mode": "ASK_USER", "rules": [],
                "reason": "No sign-flip symmetry was established. Inspect the complete expressions or request the missing symmetry evidence; do not invent a spatial cut."}
    if backend == "phasetracer":
        return {"status": "pending_review", "mode": "z2_reflection", "rules": [
            {"enabled": True, "fields": ",".join(group), "transformation": "sign_flip",
             "reason": "Detected tree-level generator; " + review, "source_reference": "V0 parity check"}
            for group in groups], "reason": review}
    rows = {row["name"]: row for row in contract.get("fields", []) if row.get("name")}
    field_step = (contract.get("numerical_settings") or {}).get("x_eps", 0.001)
    if any(len(set(group)) > 1 for group in groups):
        tolerance = -phase_filter_suggestion("", field_step=field_step)["threshold"] or 0.01
        reason = ("Keep all points during tracing and preserve all native CT phases and intervals. "
                  "Compare trajectories under one fixed transformation over their common temperature interval, "
                  "without extrapolation. Record unequal-domain matches separately from complete history groups. "
                  "Exclude only the current source's "
                  "verified symmetry partners from Tc/tunnelling searches; keep all orientations of distinct phases. "
                  "The threshold is an absolute Euclidean field-distance tolerance, not a sign cut. " + review)
        return {"status": "pending_review", "mode": "symmetry_orbits", "rules": [
            {"enabled": True, "field": ",".join(group), "comparison": "orbit", "threshold": tolerance,
             "phase_type": "symmetry_duplicate", "reason": reason,
             "source_reference": "V0 parity check; common-interval numerical matching convention"}
            for group in groups], "reason": reason}
    rules = []
    for field in fundamental_domain_fields(groups, fields):
        proposal = phase_filter_suggestion(field, rows[field].get("zeroT_default"), field_step)
        rules.append({"enabled": True, "field": field, "comparison": "<", "threshold": proposal["threshold"],
                      "phase_type": "symmetry_duplicate", "reason": "Retain the nonnegative pivot-sign representative, with a boundary tolerance. " + review,
                      "source_reference": "V0 parity check; numerical boundary convention"})
    return {"status": "pending_review", "mode": "negative_field_threshold", "rules": rules,
            "reason": review + " " + FILTER_GUIDANCE}
