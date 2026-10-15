"""Whole-trajectory CT symmetry matching, embedded in standalone generated models."""
from __future__ import annotations

import inspect
import textwrap

import numpy as np


MAX_ORBIT_TRANSFORMS = 4096


def sign_flip_closure(groups: list[list[int]], dimension: int) -> list[list[int]]:
    masks = {0}
    for group in groups:
        mask = sum(1 << index for index in set(group))
        masks.update([current ^ mask for current in masks])
        if len(masks) > MAX_ORBIT_TRANSFORMS:
            raise ValueError(f"symmetry_orbits supports at most {MAX_ORBIT_TRANSFORMS} group transformations.")
    return [[-1 if mask & (1 << index) else 1 for index in range(dimension)] for mask in sorted(masks)]


def _deduplicate_phase_orbits(phases, transforms, tolerance):
    """Keep uncertain matches; compare each candidate directly to a retained phase."""
    signs = np.asarray(transforms, dtype=float)
    dimension = signs.shape[1]

    def trajectory(phase):
        try:
            times = np.asarray(phase.T, dtype=float)
            values = np.asarray(phase.X, dtype=float)
            if (times.ndim != 1 or len(times) < 2 or values.shape != (len(times), dimension)
                    or not np.all(np.isfinite(times)) or not np.all(np.isfinite(values))
                    or not np.all(np.diff(times) > 0)):
                return None
            return times
        except (AttributeError, TypeError, ValueError):
            return None

    def equivalent(left, right, left_times, right_times):
        if left_times is None or right_times is None:
            return False
        # No extrapolation or merging of merely overlapping temperature domains.
        if left_times[0] != right_times[0] or left_times[-1] != right_times[-1]:
            return False
        knots = np.unique(np.concatenate((left_times, right_times)))
        samples = np.unique(np.concatenate((knots, knots[:-1] + np.diff(knots) / 2)))
        try:
            a = np.asarray(left.valAt(samples), dtype=float)
            b = np.asarray(right.valAt(samples), dtype=float)
        except (AttributeError, TypeError, ValueError, IndexError, FloatingPointError):
            return False
        if (a.shape != (len(samples), dimension) or b.shape != a.shape
                or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b))):
            return False
        # One fixed element must work at every temperature, not a different sign per sample.
        with np.errstate(over="ignore", invalid="ignore"):
            return any(np.all(np.linalg.norm(a - b * sign, axis=1) <= tolerance) for sign in signs)

    kept = {}
    representatives = {}
    times = {key: trajectory(phase) for key, phase in phases.items()}
    for key, phase in phases.items():
        for target, representative in kept.items():
            if equivalent(representative, phase, times[target], times[key]):
                representatives[key] = target
                break
        else:
            kept[key] = phase
            representatives[key] = key
    if len(kept) == len(phases):
        return phases

    low = {key: set() for key in kept}
    high = {key: set() for key in kept}
    for key, phase in phases.items():
        target = representatives[key]
        for attribute, links, reverse in (("low_trans", low, high), ("high_trans", high, low)):
            for other in getattr(phase, attribute, ()):
                if other not in representatives:
                    continue
                other_target = representatives[other]
                if other_target != target:
                    links[target].add(other_target)
                    reverse[other_target].add(target)
    for key, phase in kept.items():
        phase.low_trans = low[key]
        phase.high_trans = high[key]
    return kept


def render_symmetry_orbit_methods(groups: list[list[int]], dimension: int, tolerance: float) -> str:
    transforms = sign_flip_closure(groups, dimension)
    helper = "@staticmethod\n" + inspect.getsource(_deduplicate_phase_orbits)
    methods = (
        "def forbidPhaseCrit(self, X):\n"
        "    return False\n\n"
        "def _deduplicate_symmetry_phases(self, phases):\n"
        "    from copy import deepcopy\n"
        "    history = {key: deepcopy(phase) for key, phase in phases.items()}\n"
        f"    return self._deduplicate_phase_orbits(history, {transforms!r}, {tolerance!r})\n\n"
        "def _record_symmetry_phase_history(self, phases):\n"
        "    # Numeric transition searches must retain all relative-orientation channels.\n"
        "    self.phases = phases\n"
        "    self.symmetry_phase_history = self._deduplicate_symmetry_phases(phases)\n"
        "    return self.phases\n\n" + helper
    )
    return textwrap.indent(methods, "    ")
