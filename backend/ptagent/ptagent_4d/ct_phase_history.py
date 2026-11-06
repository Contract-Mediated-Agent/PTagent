"""Read-only, JSON-safe snapshots of existing CosmoTransitions phase data."""


def snapshot_ct_phase_history(model):
    """Export native samples and relationships without evaluating any phase.

    This function is self-contained for embedding in standalone models. Phase
    mappings become records: ``id`` is the mapping key, ``key`` is Phase.key.
    Neither is coerced to a string. Missing/uncomputed attributes are null;
    empty containers mean the attribute was available but contained no entries.
    Nonfinite diagnostic floats use explicit tags, not invalid JSON or null.
    """
    import json
    import math

    import numpy as np

    def plain(value):
        if isinstance(value, np.ndarray):
            return plain(value.tolist())
        if isinstance(value, np.generic):
            return plain(value.item())
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else {"__float__": str(value)}
        if isinstance(value, dict):
            if all(isinstance(key, str) for key in value):
                return {key: plain(item) for key, item in value.items()}
            # Retain typed keys, including numeric/string lookalikes.
            return {"__mapping__": [
                {"key": plain(key), "value": plain(item)} for key, item in value.items()
            ]}
        if isinstance(value, (set, frozenset)):
            return sorted((plain(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        raise TypeError("Unsupported CT phase diagnostic value: " + type(value).__name__)

    def trajectories(phases):
        if phases is None:
            return None
        return [
            {"id": plain(key), **{
                attribute: plain(getattr(phase, attribute, None))
                for attribute in ("key", "T", "X", "dXdT", "low_trans", "high_trans")
            }}
            for key, phase in phases.items()
        ]

    groups = getattr(model, "symmetry_phase_groups", None)
    diagnostics = {
        "schema": "ptagent.ct_phase_diagnostics.v1",
        "symmetry_phase_history": trajectories(getattr(model, "symmetry_phase_history", None)),
        "symmetry_phase_groups": ([
            {"id": plain(key), "members": plain(members)} for key, members in groups.items()
        ] if groups is not None else None),
    }
    for attribute in ("symmetry_phase_associations", "symmetry_pair_diagnostics",
                      "symmetry_tunnelling_channels", "symmetry_proof"):
        diagnostics[attribute] = plain(getattr(model, attribute, None))
    diagnostics["symmetry_transforms"] = plain(getattr(model, "_symmetry_transforms", None))
    diagnostics["symmetry_tolerance"] = plain(getattr(model, "_symmetry_tolerance", None))
    return {
        "phases": trajectories(getattr(model, "phases", None)),
        "phase_diagnostics": diagnostics,
        # Preserve the existing public alias, now also normalizing NumPy IDs.
        "symmetry_diagnostics": diagnostics["symmetry_pair_diagnostics"] or [],
    }
