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
    "Propose Arnold-Espinosa resummation, retaining the complete source spectrum without special "
    "Goldstone/photon removal; missing thermal data blocks this proposal rather than switching schemes. "
    "Keep an imported extra polynomial as a proposed separate correction when nonzero, otherwise propose none. "
    "Propose radiation counts from the retained spectrum only, explicitly listing any missing bath species. "
    "Propose no phase filtering or symmetry merging until the full potential symmetry is reviewed. "
    "All proposals require the same final human review for every model."
)
FILTER_GUIDANCE = (
    "If a full-potential sign symmetry is confirmed, suggest field < -epsilon with "
    "epsilon = 0.01*L, where L is a sourced nonzero reference field scale in that field's units. "
    "If a numerical field step is known, also require epsilon >= 10*abs(field_step). "
    "This is a starting tolerance, not a physical constant: vary it by a factor of two and check "
    "that the origin and distinct phases survive. With no reference scale, request one; "
    "do not assume an electroweak scale. Keep the proposed rule disabled until confirmed."
)


def phase_filter_suggestion(field: str, reference_scale: Any = None, field_step: Any = None) -> dict:
    def positive(value):
        try:
            number = abs(float(value)) if not isinstance(value, bool) else 0.0
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    scale, step = positive(reference_scale), positive(field_step)
    epsilon = max(0.01 * scale, 10 * step if step else 0.0) if scale else None
    return {
        "field": field, "threshold": -epsilon if epsilon is not None else "ASK_USER",
        "reference_scale": scale, "field_step": step,
        "enabled": False, "status": "proposal", "reason": FILTER_GUIDANCE,
    }
