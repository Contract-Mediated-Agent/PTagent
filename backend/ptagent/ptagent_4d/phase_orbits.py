"""Overlap-aware CT symmetry matching, embedded in standalone generated models."""
from __future__ import annotations

import inspect
import textwrap

import numpy as np

from .phase_orbit_sampling import orbit_comparison_temperatures


MAX_ORBIT_TRANSFORMS = 4096


def sign_flip_closure(groups: list[list[int]], dimension: int) -> list[list[int]]:
    masks = {0}
    for group in groups:
        mask = sum(1 << index for index in set(group))
        masks.update([current ^ mask for current in masks])
        if len(masks) > MAX_ORBIT_TRANSFORMS:
            raise ValueError(f"symmetry_orbits supports at most {MAX_ORBIT_TRANSFORMS} group transformations.")
    return [[-1 if mask & (1 << index) else 1 for index in range(dimension)] for mask in sorted(masks)]


class _SymmetryOrbitMethods:
    def forbidPhaseCrit(self, X):
        return False

    def _symmetry_pair_match(self, left, right):
        """Numerical equivalence on the common domain, never beyond its bounds."""
        result = {"equivalent_on_overlap": False, "refined_samples": 0,
                  "temperature_interval": None, "reason": "invalid_trajectory"}
        signs = np.asarray(self._symmetry_transforms, dtype=float)
        tolerance = self._symmetry_tolerance
        dimension = signs.shape[1]
        try:
            times = [np.asarray(p.T, dtype=float) for p in (left, right)]
            for phase, ts in zip((left, right), times):
                xs = np.asarray(phase.X, dtype=float)
                if (ts.ndim != 1 or len(ts) < 2 or xs.shape != (len(ts), dimension)
                        or not np.all(np.isfinite(ts)) or not np.all(np.isfinite(xs))
                        or not np.all(np.diff(ts) > 0)):
                    return result
            lo, hi = max(ts[0] for ts in times), min(ts[-1] for ts in times)
            if lo >= hi:
                return {**result, "reason": "no_common_interval"}
            knots = np.unique(np.concatenate(([lo, hi], *times)))
            knots = knots[(knots >= lo) & (knots <= hi)]
            samples = np.unique(np.concatenate((knots, knots[:-1] + np.diff(knots)/2)))
            a, b = (np.asarray(p.valAt(samples), dtype=float) for p in (left, right))
            if (a.shape != (len(samples), dimension) or b.shape != a.shape
                    or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b))):
                return result
        except (AttributeError, TypeError, ValueError, IndexError, FloatingPointError):
            return result
        result.update(temperature_interval=[float(lo), float(hi)],
                      samples=len(samples), reason="field_mismatch")
        # A fixed transform must work across the whole overlap. Local refinement
        # resolves interpolation error, but cannot move a point by more than the
        # reviewed field tolerance or change the recorded phase trajectories.
        for sign in signs:
            aa, bb, refined = a.copy(), b.copy(), 0
            with np.errstate(over="ignore", invalid="ignore"):
                distance = np.linalg.norm(aa - bb*sign, axis=1)
            if not np.all(np.isfinite(distance)):
                continue
            max_distance = float(np.max(distance))
            if max_distance < result.get("max_raw_field_distance", float("inf")):
                result.update(max_raw_field_distance=max_distance, nearest_transform=sign.tolist())
            # Two displacements of at most tolerance can close at most 2*tolerance.
            if max_distance > 3*tolerance:
                continue
            try:
                check_times = self.orbit_comparison_temperatures(left, right, sign)
                aa, bb = (np.asarray(p.valAt(check_times), dtype=float) for p in (left, right))
                if (aa.shape != (len(check_times), dimension) or bb.shape != aa.shape
                        or not np.all(np.isfinite(aa)) or not np.all(np.isfinite(bb))):
                    raise ValueError("Invalid spline evaluation")
                distance = np.linalg.norm(aa-bb*sign, axis=1)
                result.update(samples=len(check_times), max_raw_field_distance=float(np.max(distance)),
                              nearest_transform=sign.tolist())
            except (ValueError, FloatingPointError):
                result["reason"] = "spline_verification_unresolved"
                continue
            if np.any(distance > 3*tolerance):
                continue
            valid = True
            for index in np.flatnonzero(distance > tolerance):
                try:
                    x = np.asarray(self.findMinimum(aa[index], check_times[index]), dtype=float)
                    y = np.asarray(self.findMinimum(bb[index], check_times[index]), dtype=float)
                    if (x.shape != (dimension,) or y.shape != (dimension,)
                            or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y))
                            or np.linalg.norm(x-aa[index]) > tolerance
                            or np.linalg.norm(y-bb[index]) > tolerance
                            or np.linalg.norm(x-y*sign) > tolerance):
                        valid = False
                        result["reason"] = "local_refinement_unresolved"
                        break
                    aa[index], bb[index] = x, y
                    refined += 1
                except (ValueError, RuntimeError, FloatingPointError):
                    valid = False
                    result["reason"] = "local_refinement_failed"
                    break
            if not valid:
                continue
            try:
                # Check the complete potential, not just tree-level parity.
                for points in (aa, bb):
                    original = np.asarray(self.Vtot(points, check_times, False), dtype=float)
                    reflected = np.asarray(self.Vtot(points*sign, check_times, False), dtype=float)
                    anchor = np.asarray(self.Vtot(np.zeros_like(points), check_times, False), dtype=float)
                    with np.errstate(over="raise", invalid="raise"):
                        local_scale = np.maximum(np.abs(original-anchor), np.abs(reflected-anchor))
                        accuracy = 1e-12 + 1e-10*local_scale
                        roundoff = 4*np.finfo(float).eps*(np.abs(original)+np.abs(reflected)+2*np.abs(anchor))
                        residual = np.abs(original-reflected)
                    if (not np.all(np.isfinite(original)) or not np.all(np.isfinite(reflected))
                            or not np.all(np.isfinite(anchor))
                            or not np.all(np.isfinite(accuracy)) or not np.all(np.isfinite(roundoff))
                            or np.any(residual > accuracy)):
                        valid = False
                        result["reason"] = "full_potential_symmetry_failed"
                        break
                    if np.any(roundoff > accuracy):
                        valid = False
                        result["reason"] = "full_potential_precision_unresolved"
                        break
            except (ValueError, RuntimeError, FloatingPointError):
                valid = False
                result["reason"] = "full_potential_check_failed"
            if valid:
                return {**result, "equivalent_on_overlap": True, "reason": "matched_on_overlap",
                        "transform": sign.tolist(), "refined_samples": refined,
                        "max_field_distance": float(np.max(np.linalg.norm(aa-bb*sign, axis=1)))}
        return result

    def _symmetry_pair_table(self, phases):
        from itertools import combinations
        matches = {}
        diagnostics = []
        for (key, phase), (other, candidate) in combinations(phases.items(), 2):
            match = self._symmetry_pair_match(phase, candidate)
            matches[key, other] = matches[other, key] = match["equivalent_on_overlap"]
            diagnostics.append({"phase_keys": [key, other], **match})
        self.symmetry_pair_diagnostics = diagnostics
        return matches

    def _deduplicate_symmetry_phases(self, phases):
        from copy import deepcopy
        matches = self._symmetry_pair_table(phases)
        # An overlap relation does not identify the unobserved tails. Keep it
        # separate from full-history representatives even when endpoints differ
        # only numerically; transition searches use the verified overlap itself.
        self.symmetry_phase_associations = [
            row for row in self.symmetry_pair_diagnostics if row["equivalent_on_overlap"]]
        kept, representatives = {}, {}
        for key, phase in phases.items():
            target = next((k for k in kept if matches.get((k, key), False)
                           and phases[k].T[0] == phase.T[0] and phases[k].T[-1] == phase.T[-1]), key)
            representatives[key] = target
            if target == key:
                kept[key] = deepcopy(phase)
        # Every native interval is retained in the group record; a representative
        # is for presentation only and must never replace the solver's phases.
        def native_interval(phase):
            times = np.asarray(getattr(phase, "T", []), dtype=float)
            if times.ndim != 1 or not len(times) or not np.all(np.isfinite(times)):
                return None
            return [float(times[0]), float(times[-1])]

        self.symmetry_phase_groups = {
            key: [{"key": member, "temperature_interval": native_interval(phases[member])}
                  for member, target in representatives.items() if target == key]
            for key in kept}
        for phase in kept.values():
            phase.low_trans, phase.high_trans = set(), set()
        for key, phase in phases.items():
            target = representatives[key]
            for attribute, reverse in (("low_trans", "high_trans"), ("high_trans", "low_trans")):
                for other in getattr(phase, attribute, ()):
                    mapped = representatives.get(other, target)
                    if mapped != target:
                        getattr(kept[target], attribute).add(mapped)
                        getattr(kept[mapped], reverse).add(target)
        return kept

    def _record_symmetry_phase_history(self, phases):
        self.phases = phases
        self.symmetry_phase_history = self._deduplicate_symmetry_phases(phases)
        return self.phases

    def _symmetry_calc_tc(self, startHigh=False):
        from itertools import combinations
        from cosmoTransitions import transitionFinder
        if startHigh:
            raise NotImplementedError("start_high=True not yet supported by CosmoTransitions")
        if self.phases is None:
            self.getPhases()
        self.TcTrans = None
        matches = self._symmetry_pair_table(self.phases)
        transitions = []
        for (key, phase), (other, candidate) in combinations(self.phases.items(), 2):
            if not matches[key, other]:
                transitions.extend(transitionFinder.findCriticalTemperatures(
                    {key: phase, other: candidate}, self.Vtot))
        for trans in transitions:
            t = trans["Tcrit"]
            trans["Delta_rho"] = (self.energyDensity(trans["high_vev"], t, False)
                                  - self.energyDensity(trans["low_vev"], t, False))
        self.TcTrans = sorted(transitions, key=lambda t: t["Tcrit"], reverse=True)
        return self.TcTrans

    def _symmetry_find_all_transitions(self, tunnelFromPhase_args=None):
        from cosmoTransitions import transitionFinder
        if self.phases is None:
            self.getPhases()
        self.TnTrans = None
        matches = self._symmetry_pair_table(self.phases)
        remaining = dict(self.phases)
        transitions = []
        source = remaining[transitionFinder.getStartPhase(remaining, self.Vtot)] if remaining else None
        ceiling = source.T[-1] if source is not None else None
        self.symmetry_tunnelling_channels = []
        visited = []
        while source is not None:
            remaining.pop(source.key)
            visited.append(source.key)
            # Exclude only the current source's own orbit. The two orientations
            # of a DIFFERENT orbit can have different bounce actions; keep both.
            def already_visited(key, phase):
                future_end = min(phase.T[-1], ceiling)
                return any(matches.get((previous, key), False)
                           and self.phases[previous].T[0] <= phase.T[0] <= future_end
                           and self.phases[previous].T[-1] >= future_end for previous in visited[:-1])

            targets = {key: phase for key, phase in remaining.items()
                       if not matches.get((source.key, key), False) and not already_visited(key, phase)}
            self.symmetry_tunnelling_channels.append({"source": source.key, "targets": list(targets),
                                                      "excluded": [key for key in remaining if key not in targets]})
            trans = (transitionFinder.tunnelFromPhase(targets, source, self.Vtot, self.gradV, ceiling,
                                                      **(tunnelFromPhase_args or {})) if targets else None)
            if trans is None:
                successor = next((phase for key, phase in targets.items() if key in source.low_trans), None)
                if successor is None:
                    break
                trans = transitionFinder.secondOrderTrans(source, successor)
                ceiling = successor.T[-1]
            else:
                successor = targets[trans["low_phase"]]
                ceiling = trans["Tnuc"]
            transitions.append(trans)
            source = successor
        if self.TcTrans is None:
            self._symmetry_calc_tc()
        transitionFinder.addCritTempsForFullTransitions(self.phases, self.TcTrans, transitions)
        for trans in transitions:
            t, high, low = trans["Tnuc"], trans["high_vev"], trans["low_vev"]
            trans["Delta_rho"] = self.energyDensity(high, t, False)-self.energyDensity(low, t, False)
            trans["Delta_p"] = self.Vtot(high, t, False)-self.Vtot(low, t, False)
        self.TnTrans = transitions
        return self.TnTrans


def render_symmetry_orbit_methods(groups: list[list[int]], dimension: int, tolerance: float) -> str:
    transforms = sign_flip_closure(groups, dimension)
    methods = (f"_symmetry_transforms = {transforms!r}\n_symmetry_tolerance = {tolerance!r}\n\n"
               + textwrap.dedent(inspect.getsource(_SymmetryOrbitMethods).split("\n", 1)[1])
               + "\n@staticmethod\n" + inspect.getsource(orbit_comparison_temperatures))
    return textwrap.indent(methods, "    ")
