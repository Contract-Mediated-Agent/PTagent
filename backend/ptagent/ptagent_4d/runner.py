from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import PhaseHistoryResult


def check_runtime(settings: Settings) -> tuple[bool, str]:
    if not settings.runtime_python.exists():
        return False, f"Runtime Python does not exist: {settings.runtime_python}"
    command = [
        str(settings.runtime_python),
        "-c",
        "import cosmoTransitions, numpy, scipy, sympy; print('ok')",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "ok"


def import_check(model_path: Path, settings: Settings) -> tuple[bool, str]:
    code = f"""
import importlib.util
import inspect
import numpy as np
import re
p = {str(model_path)!r}
spec = importlib.util.spec_from_file_location('generated_model', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
try:
    model = m.build_model(run_tc=False, print_tc=False)
except TypeError:
    model = m.build_model()

def assert_finite(name, value):
    arr = np.asarray(value, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise ValueError(name + ' returned non-finite values: ' + repr(arr))
    return arr

def normalized_source(value):
    if isinstance(value, dict):
        value = value.get('source_type') or value.get('source') or value.get('kind') or ''
    return str(value).strip().lower().replace('-', '_').replace(' ', '_')

def default_parameter_sources():
    data = getattr(m, 'DEFAULT_PARAMETER_SOURCES', None)
    metadata = getattr(m, 'MODEL_METADATA', {{}})
    if data is None and isinstance(metadata, dict):
        data = metadata.get('default_parameter_sources') or metadata.get('parameter_sources')
    if data is None:
        inputs = getattr(m, 'INPUT_PARAMETERS', None)
        if inputs is None and isinstance(metadata, dict):
            inputs = metadata.get('input_parameters')
        if isinstance(inputs, dict):
            inferred = {{}}
            for key, item in inputs.items():
                if isinstance(item, dict) and ('source_type' in item or 'source' in item or 'kind' in item):
                    inferred[key] = item
            data = inferred or None
    if not isinstance(data, dict) or not data:
        return {{}}, 'no DEFAULT_PARAMETER_SOURCES or source-typed INPUT_PARAMETERS table'
    return {{str(key): normalized_source(value) for key, value in data.items()}}, ''

def generated_source_text():
    chunks = [
        repr(getattr(m, 'MODEL_METADATA', {{}})),
        repr(getattr(m, 'FIELD_ORDER', [])),
        repr(getattr(m, 'BOSON_NAMES', [])),
        repr(getattr(m, 'FERMION_NAMES', [])),
    ]
    for name in ('boson_massSq', '_scalar_mass_matrices', 'V1', 'VCW'):
        func = getattr(model, name, None)
        if func is None:
            continue
        try:
            chunks.append(inspect.getsource(func))
        except Exception:
            pass
    return '\\n'.join(chunks).lower()

def particle_names(attr, metadata_key):
    names = getattr(m, attr, None)
    metadata = getattr(m, 'MODEL_METADATA', {{}})
    if names is None and isinstance(metadata, dict):
        names = metadata.get(metadata_key)
    if not isinstance(names, (list, tuple)):
        return []
    clean = []
    for item in names:
        if isinstance(item, str) and item.strip():
            clean.append(item.strip())
    return clean

def check_particle_name_lengths(bosons, fermions):
    boson_names = particle_names('BOSON_NAMES', 'boson_names')
    fermion_names = particle_names('FERMION_NAMES', 'fermion_names')
    if boson_names:
        n_bosons = int(np.asarray(bosons[0]).shape[-1])
        if len(boson_names) != n_bosons:
            raise ValueError(
                'BOSON_NAMES length must match boson_massSq particle axis: '
                + str(len(boson_names))
                + ' names for '
                + str(n_bosons)
                + ' masses'
            )
    if fermion_names:
        n_fermions = int(np.asarray(fermions[0]).shape[-1])
        if len(fermion_names) != n_fermions:
            raise ValueError(
                'FERMION_NAMES length must match fermion_massSq particle axis: '
                + str(len(fermion_names))
                + ' names for '
                + str(n_fermions)
                + ' masses'
            )

def check_particle_tuple_axis_lengths(bosons, fermions):
    def particle_axis_length(value):
        shape = np.asarray(value).shape
        return int(shape[-1]) if shape else 1

    n_bosons = particle_axis_length(bosons[0])
    n_boson_dof = particle_axis_length(bosons[1])
    n_boson_c = particle_axis_length(bosons[2])
    if n_boson_dof != n_bosons or n_boson_c != n_bosons:
        raise ValueError(
            'boson_massSq tuple particle axes must agree: massSq has '
            + str(n_bosons)
            + ', dof has '
            + str(n_boson_dof)
            + ', c has '
            + str(n_boson_c)
        )
    n_fermions = particle_axis_length(fermions[0])
    n_fermion_dof = particle_axis_length(fermions[1])
    if n_fermion_dof != n_fermions:
        raise ValueError(
            'fermion_massSq tuple particle axes must agree: massSq has '
            + str(n_fermions)
            + ', dof has '
            + str(n_fermion_dof)
        )

def expected_goldstone_zero_count():
    boson_names = particle_names('BOSON_NAMES', 'boson_names')
    if boson_names:
        count = 0
        for name in boson_names:
            label = name.lower().replace('-', '_').replace(' ', '_')
            if 'gamma' in label or 'photon' in label:
                continue
            if 'goldstone' in label or re.search(r'\\bg(?:0|pm|p|m|plus|minus)\\b', label):
                count += 1
        return count
    text = generated_source_text()
    pattern = r'\\bm(?:ass_?)?g(?:oldstone|0|pm|p|m|plus|minus)[a-z0-9_]*_sq\\b'
    if 'goldstone' not in text and not re.search(pattern, text):
        return 0
    names = {{item for item in re.findall(pattern, text) if 'gamma' not in item}}
    expected = len(names)
    if any(token in text for token in ('g0', 'g^0', 'g^{{0}}')) and any(token in text for token in ('gpm', 'g^\\\\pm', 'g^{{\\\\pm}}')):
        expected = max(expected, 2)
    elif 'goldstone' in text:
        expected = max(expected, 1)
    return expected

def check_goldstone_zero_modes(bosons):
    expected = expected_goldstone_zero_count()
    if expected <= 0:
        return
    m2 = np.asarray(bosons[0])
    if m2.size == 0:
        raise ValueError('Goldstone zero-mode check expected Goldstone species, but boson_massSq returned no bosonic masses')
    flat = np.asarray(np.real_if_close(m2), dtype=float).reshape(-1, m2.shape[-1])[0]
    finite = flat[np.isfinite(flat)]
    if finite.size == 0:
        raise ValueError('Goldstone zero-mode check could not find finite bosonic masses at approxZeroTMin()[0]')
    scale = max(1.0, float(np.max(np.abs(finite))))
    tol = max(1e-5, 1e-8 * scale)
    zero_count = int(np.sum(np.abs(finite) <= tol))
    if zero_count < expected:
        raise ValueError(
            'Goldstone zero-mode check failed at approxZeroTMin()[0]: expected at least '
            + str(expected)
            + ' near-zero bosonic mass-squared entries from generated Goldstone species, found '
            + str(zero_count)
            + ' with tolerance '
            + repr(tol)
            + '. This usually means a vacuum mass matrix or physical-mass relation was mixed into a field-dependent mass matrix.'
        )

def public_input_names():
    names = []
    metadata = getattr(m, 'MODEL_METADATA', {{}})
    for source in (
        getattr(m, 'INPUT_PARAMETERS', None),
        metadata.get('input_parameters') if isinstance(metadata, dict) else None,
        getattr(m, 'DEFAULT_PARAMETER_SOURCES', None),
    ):
        if isinstance(source, dict):
            names.extend(str(key) for key in source.keys())
        elif isinstance(source, (list, tuple)):
            for item in source:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    for key in ('name', 'symbol', 'program_symbol'):
                        value = item.get(key)
                        if value:
                            names.append(str(value))
                            break
    try:
        names.extend(str(name) for name in inspect.signature(m.build_model).parameters.keys())
    except Exception:
        pass
    control_names = {'run_tc', 'print_tc'}
    return [name for name in names if name not in control_names]

def normalized_identifier(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())

def _is_goldstone_label(label):
    probe = str(label or '').lower().replace('-', '_').replace(' ', '_')
    return 'goldstone' in probe or bool(re.search(r'\\bg(?:0|pm|p|m|plus|minus)\\b', probe))

def _candidate_mass_attrs(label):
    raw = re.sub(r'[^A-Za-z0-9_]+', '', str(label or '').strip())
    if not raw:
        return []
    bases = [raw]
    lowered = raw.lower()
    for suffix in ('_longitudinal', '_transverse', '_l', '_t'):
        if lowered.endswith(suffix):
            bases.append(raw[: -len(suffix)])
            break
    if '_' in raw:
        bases.append(raw.replace('_', ''))
    candidates = []
    seen = set()
    for base in bases:
        clean = base.strip('_')
        if not clean:
            continue
        for item in ('m' + clean, 'm_' + clean, clean):
            key = normalized_identifier(item)
            if key and key not in seen:
                seen.add(key)
                candidates.append(item)
    return candidates

def _physical_mass_anchor_for_label(label, public_inputs):
    probe = str(label or '').lower()
    if 'gamma' in probe or 'photon' in probe or _is_goldstone_label(label):
        return None
    public_keys = {{normalized_identifier(name) for name in public_inputs}}
    for attr in _candidate_mass_attrs(label):
        if public_keys and normalized_identifier(attr) not in public_keys:
            continue
        if not hasattr(model, attr):
            continue
        try:
            value = float(getattr(model, attr))
        except Exception:
            continue
        if np.isfinite(value) and value > 0.0:
            return attr, value
    return None

def check_vacuum_physical_mass_anchors(bosons):
    boson_names = particle_names('BOSON_NAMES', 'boson_names')
    if not boson_names:
        return
    m2 = np.asarray(bosons[0])
    if m2.size == 0 or m2.shape[-1] != len(boson_names):
        return
    flat = np.asarray(np.real_if_close(m2), dtype=float).reshape(-1, m2.shape[-1])[0]
    public_inputs = public_input_names()
    failures = []
    for idx, name in enumerate(boson_names):
        anchor = _physical_mass_anchor_for_label(name, public_inputs)
        if anchor is None:
            continue
        attr, expected_mass = anchor
        expected = expected_mass * expected_mass
        got = float(flat[idx])
        if not np.isfinite(got):
            failures.append(str(name) + ': non-finite mass-squared for physical input ' + attr)
            continue
        scale = max(1.0, abs(expected), abs(got))
        tol = max(1e-3, 2e-5 * scale)
        if abs(got - expected) > tol:
            failures.append(
                str(name)
                + ': boson_massSq(vacuum,T=0)='
                + repr(got)
                + ', expected '
                + attr
                + '^2='
                + repr(expected)
                + ' within tolerance '
                + repr(tol)
            )
    if failures:
        raise ValueError(
            'Vacuum physical-mass anchor check failed at approxZeroTMin()[0]: '
            + '; '.join(failures)
            + '. Field-dependent mass matrices must reproduce reviewed public physical mass inputs at the reviewed vacuum; '
            + 'do not replace a source-exact field-dependent sector by a vacuum relation or an invented base+Theta split.'
        )

def should_run_finite_vtot_check():
    sources, reason = default_parameter_sources()
    if not sources:
        return False, reason
    trusted = {{
        'benchmark',
        'paper_benchmark',
        'source_exact',
        'source_default',
        'source_inferred',
        'paper_explicit',
        'paper_explicit_value',
        'reviewed_default',
        'user_supplied',
        'standard_convention',
        'scan_range_midpoint_default',
        'scan_range_midpoint',
        'range_midpoint',
    }}
    bad = {{name: source for name, source in sources.items() if source not in trusted}}
    if bad:
        return False, 'untrusted or placeholder defaults: ' + repr(bad)
    return True, 'all defaults are reviewed benchmark/source/user/standard/range-midpoint values'

mins = model.approxZeroTMin()
if not isinstance(mins, (list, tuple)) or len(mins) == 0:
    raise TypeError('approxZeroTMin() must return a non-empty list/tuple of one-dimensional field arrays')
x = np.asarray(mins[0], dtype=float)
if x.shape != (model.Ndim,):
    raise ValueError(f'approxZeroTMin()[0] must have shape (Ndim,), got {{x.shape}} with Ndim={{model.Ndim}}')
assert_finite('V0(x)', model.V0(x))
xb = np.stack([x, x + 0.0], axis=0)
Tb = np.asarray([1.0, 2.0], dtype=float)
assert_finite('V0(xb)', model.V0(xb))
for forbid_label, forbid_X in (('x', x), ('xb', xb)):
    forbid_value = model.forbidPhaseCrit(forbid_X)
    if not isinstance(forbid_value, (bool, np.bool_)):
        raise TypeError(
            'forbidPhaseCrit('
            + forbid_label
            + ') must return a Python bool or np.bool_, got '
            + type(forbid_value).__name__
        )
bosons = model.boson_massSq(x, 0.0)
fermions = model.fermion_massSq(x)
bosons_batch = model.boson_massSq(xb, Tb)
fermions_batch = model.fermion_massSq(xb)
if len(bosons) != 3:
    raise TypeError('boson_massSq must return (massSq, dof, c)')
if len(fermions) != 2:
    raise TypeError('fermion_massSq must return (massSq, dof)')
check_particle_name_lengths(bosons, fermions)
check_particle_tuple_axis_lengths(bosons, fermions)
check_goldstone_zero_modes(bosons)
check_vacuum_physical_mass_anchors(bosons)
if np.asarray(bosons_batch[0]).shape[:-1] != np.shape(xb[..., 0] * Tb):
    raise ValueError('boson_massSq massSq shape must be (X[...,0]*T).shape + (Nbosons,)')
if np.asarray(fermions_batch[0]).shape[:-1] != np.shape(xb[..., 0]):
    raise ValueError('fermion_massSq massSq shape must be X[...,0].shape + (Nfermions,)')

run_vtot_check, vtot_check_reason = should_run_finite_vtot_check()
if run_vtot_check:
    for test_T in (0.0, 1.0, 100.0):
        assert_finite(f'Vtot(x, T={{test_T}})', model.Vtot(x, test_T, include_radiation=False))
    for helper_name in ('Vdaisy', 'Vring', 'V1T_from_X'):
        helper = getattr(model, helper_name, None)
        if helper is not None:
            assert_finite(helper_name + '(x, T=100)', helper(x, 100.0))
    x_probe = x + np.linspace(0.5, 1.5, model.Ndim)
    assert_finite('Vtot(x_probe, T=100)', model.Vtot(x_probe, 100.0, include_radiation=False))
    assert_finite('Vtot(xb, Tb)', model.Vtot(xb, Tb, include_radiation=False))
else:
    print('WARNING: finite Vtot BP check skipped: ' + vtot_check_reason + '. Please test Vtot with a reviewed benchmark point.')
print(m.MODEL_METADATA.get('model_name', 'ok'))
"""
    result = subprocess.run([str(settings.runtime_python), "-c", code], capture_output=True, text=True, timeout=30)
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def run_phase_history(
    model_path: Path,
    settings: Settings,
    *,
    parameters: dict[str, Any] | None = None,
) -> PhaseHistoryResult:
    ok, message = check_runtime(settings)
    if not ok:
        return PhaseHistoryResult(status="runtime_unavailable", model_path=str(model_path), summary=message)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(parameters or {}, handle)
        params_path = Path(handle.name)
    try:
        code = f"""
import importlib.util, json
p = {str(model_path)!r}
params_path = {str(params_path)!r}
spec = importlib.util.spec_from_file_location('generated_model', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with open(params_path, 'r', encoding='utf-8') as handle:
    params = json.load(handle)
if isinstance(params, dict) and params:
    try:
        result = m.run_transitions(**params)
    except TypeError:
        result = m.run_transitions(params)
else:
    result = m.run_transitions()
print(json.dumps(result, ensure_ascii=False))
"""
        result = subprocess.run(
            [str(settings.runtime_python), "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        try:
            params_path.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        return PhaseHistoryResult(
            status="error",
            model_path=str(model_path),
            inputs=parameters or {},
            summary=(result.stderr or result.stdout).strip(),
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return PhaseHistoryResult(
            status="error",
            model_path=str(model_path),
            inputs=parameters or {},
            summary=f"Could not parse runtime JSON: {exc}: {result.stdout[:400]}",
        )
    transitions = payload.get("transitions", [])
    tc_list = [item.get("Tc") for item in transitions]
    tn_list = [item.get("Tn") for item in transitions]
    action_over_t = [
        _action_over_t(item.get("action"), item.get("Tn"))
        for item in transitions
    ]
    return PhaseHistoryResult(
        status="success",
        model_path=str(model_path),
        inputs=payload.get("parameters", parameters or {}),
        transitions=transitions,
        Tc_list=tc_list,
        Tn_list=tn_list,
        action_over_T=action_over_t,
        path_labels=_classify_path(transitions),
        summary=f"Found {len(transitions)} nucleation transition(s).",
    )


def _action_over_t(action: object, temperature: object) -> float | None:
    try:
        if action is None or temperature in (None, 0):
            return None
        return float(action) / float(temperature)
    except Exception:
        return None


def _classify_path(transitions: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    if not transitions:
        return ["no_nucleation_history"]
    if len(transitions) == 1:
        labels.append("one-step")
    elif len(transitions) == 2:
        labels.append("two-step")
    else:
        labels.append("multi-step")
    return labels
