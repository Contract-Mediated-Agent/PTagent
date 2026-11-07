from __future__ import annotations

import json
import inspect
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .ct_diagnostics import observe_ct_nucleation
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
signature = inspect.signature(m.build_model)
controls = {{'run_tc': False, 'print_tc': False}}
try:
    signature.bind(**controls)
except TypeError:
    signature.bind()
    controls = {{}}
model = m.build_model(**controls)

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
        names.extend(str(name) for name, param in inspect.signature(m.build_model).parameters.items()
                     if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD))
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
    metadata = getattr(m, 'MODEL_METADATA', {{}})
    card = metadata.get('model_card', {{}}) if isinstance(metadata, dict) else {{}}
    if isinstance(card, dict) and str(card.get('numerical_ready', '')).lower() == 'false':
        return False, 'model metadata declares unresolved numerical inputs'
    sources, reason = default_parameter_sources()
    defaults = getattr(m, 'DEFAULT_INPUT_VALUES', None)
    if not sources:
        input_tables = [table for table in (
            getattr(m, 'INPUT_PARAMETERS', None),
            metadata.get('input_parameters') if isinstance(metadata, dict) else None,
        ) if table is not None]
        # An explicitly empty generated input contract is not missing provenance.
        if (input_tables and all(isinstance(table, (dict, list, tuple)) and not table for table in input_tables)
                and isinstance(defaults, dict) and not defaults and not public_input_names()):
            return True, 'generated model declares no public inputs; existing factory binds all constants'
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
    if isinstance(defaults, dict):
        missing = []
        for name in set(public_input_names()) | set(sources):
            try:
                finite = np.isfinite(float(defaults[name]))
            except (KeyError, TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                missing.append(name)
        if missing:
            return False, 'missing or non-finite public input defaults: ' + repr(sorted(missing))
    untraced = set(public_input_names()) - set(sources)
    if untraced:
        return False, 'missing public input provenance: ' + repr(sorted(untraced))
    return True, 'all defaults are reviewed benchmark/source/user/standard/range-midpoint values'

try:
    diagnostic_scale_sq = float(model.renormScaleSq)
except (AttributeError, TypeError, ValueError, OverflowError) as exc:
    raise ValueError('Import-check diagnostics require a finite positive model.renormScaleSq') from exc
if not np.isfinite(diagnostic_scale_sq) or diagnostic_scale_sq <= 0:
    raise ValueError('Import-check diagnostics require a finite positive model.renormScaleSq')
diagnostic_scale = float(np.sqrt(diagnostic_scale_sq))
print('INFO: import-check diagnostic scale Q=sqrt(model.renormScaleSq)=' + repr(diagnostic_scale)
      + '; diagnostic coordinates only, model settings unchanged')

mins = model.approxZeroTMin()
if not isinstance(mins, (list, tuple)) or len(mins) == 0:
    raise TypeError('approxZeroTMin() must return a non-empty list/tuple of one-dimensional field arrays')
x = np.asarray(mins[0], dtype=float)
if x.shape != (model.Ndim,):
    raise ValueError(f'approxZeroTMin()[0] must have shape (Ndim,), got {{x.shape}} with Ndim={{model.Ndim}}')
assert_finite('V0(x)', model.V0(x))
xb = np.stack([x, x + 0.0], axis=0)
Tb = diagnostic_scale * np.asarray([0.01, 0.02], dtype=float)
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
    for test_T in (0.0, 0.01*diagnostic_scale, diagnostic_scale):
        assert_finite(f'Vtot(x, T={{test_T}})', model.Vtot(x, test_T, include_radiation=False))
    for helper_name in ('Vdaisy', 'Vring', 'V1T_from_X'):
        helper = getattr(model, helper_name, None)
        if helper is not None:
            assert_finite(helper_name + '(x, T=Q)', helper(x, diagnostic_scale))
    x_probe = x + diagnostic_scale * np.linspace(0.01, 0.02, model.Ndim)
    assert_finite('Vtot(x_probe, T=Q)', model.Vtot(x_probe, diagnostic_scale, include_radiation=False))
    assert_finite('Vtot(xb, Tb)', model.Vtot(xb, Tb, include_radiation=False))
    print('INFO: finite Vtot BP check passed: ' + vtot_check_reason)
else:
    print('WARNING: finite Vtot BP check skipped: ' + vtot_check_reason + '. Please test Vtot with a reviewed benchmark point.')
print(m.MODEL_METADATA.get('model_name', 'ok'))
"""
    result = subprocess.run([str(settings.runtime_python), "-c", code], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return False, "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def run_phase_history(
    model_path: Path,
    settings: Settings,
    *,
    parameters: dict[str, Any] | None = None,
    stage: str = "critical",
    timeout: int = 180,
) -> PhaseHistoryResult:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {}, stage=stage,
            summary="timeout must be a positive integer in seconds; use --timeout or the timeout keyword argument.",
        )
    controls = {"stage": stage, "timeout_seconds": timeout}
    if stage not in ("critical", "nucleation"):
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {}, **controls,
            summary="Runtime stage must be critical or nucleation.",
        )
    if parameters and "stage" in parameters:
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters, **controls,
            summary="stage is a runtime control, not a model parameter; use --stage or the stage keyword argument.",
        )
    ok, message = check_runtime(settings)
    if not ok:
        return PhaseHistoryResult(status="runtime_unavailable", model_path=str(model_path), summary=message, **controls)

    with tempfile.TemporaryDirectory(prefix="ptagent-runtime-") as directory:
        params_path = Path(directory) / "parameters.json"
        result_path = Path(directory) / "result.json"
        params_path.write_text(json.dumps(parameters or {}), encoding="utf-8")
        # Solver progress (including native stdout writes) is not the JSON protocol.
        code = f"""
import importlib.util, inspect, json, traceback
{inspect.getsource(observe_ct_nucleation)}
p = {str(model_path)!r}
params_path = {str(params_path)!r}
result_path = {str(result_path)!r}
stage = {stage!r}
spec = importlib.util.spec_from_file_location('generated_model', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with open(params_path, 'r', encoding='utf-8') as handle:
    params = json.load(handle)
numerical = getattr(m, 'NUMERICAL_SETTINGS', {{}})
if numerical and not numerical.get('Tmax_approved', False):
    result = {{'status': 'waiting_for_Tmax_approval', 'parameters': params,
               'summary': m.TMAX_APPROVAL_MESSAGE, 'numerical_settings': numerical}}
else:
    signature = inspect.signature(m.run_transitions)
    stage_parameter = signature.parameters.get('stage')
    supports_stage = stage_parameter is not None and stage_parameter.kind in (
        inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    if stage == 'nucleation' and not supports_stage:
        raise RuntimeError('Generated model does not expose an explicit stage control; '
                           'regenerate it with nucleation support. No critical-stage fallback was run.')
    controls = {{'stage': stage}} if supports_stage else {{}}
    arguments = ()
    keywords = {{**params, **controls}}
    try:
        signature.bind(**keywords)
    except TypeError:
        signature.bind(params, **controls)
        arguments, keywords = (params,), controls
    diagnostics = {{}}
    try:
        operation = lambda: m.run_transitions(*arguments, **keywords)
        result = (observe_ct_nucleation(operation, diagnostics=diagnostics,
                                       serialize_transition=getattr(m, '_ptagent_transition_payload', None))
                  if stage == 'nucleation' else operation())
    except Exception as exc:
        traceback.print_exc()
        result = {{'status': 'error', 'stage': stage, 'parameters': params,
                   'summary': type(exc).__name__ + ': ' + str(exc), 'transitions': []}}
        # Keep validated earlier transitions usable; rejected/raw native values
        # remain separately available in the diagnostic record.
        result['transitions'] = diagnostics.get('validated_transitions', [])
        result.update(getattr(exc, 'ptagent_phase_history', {{}}))
    if isinstance(result, dict) and diagnostics:
        if diagnostics.get('status') != 'unavailable' or 'nucleation_diagnostics' not in result:
            result['nucleation_diagnostics'] = diagnostics
with open(result_path, 'w', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False, allow_nan=False)
"""
        try:
            result = subprocess.run(
                [str(settings.runtime_python), "-u", "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            summary = (
                f"Runtime TimeoutExpired after {exc.timeout} seconds."
                if isinstance(exc, subprocess.TimeoutExpired)
                else f"Runtime {type(exc).__name__}: {exc}"
            )
            return PhaseHistoryResult(
                status="error",
                model_path=str(model_path),
                inputs=parameters or {},
                summary=summary,
                **controls,
                solver_stdout=_runtime_log_text(getattr(exc, "stdout", None)),
                solver_stderr=_runtime_log_text(getattr(exc, "stderr", None)),
            )
        logs = {"solver_stdout": result.stdout, "solver_stderr": result.stderr, **controls}
        if result.returncode != 0:
            return PhaseHistoryResult(
                status="error",
                model_path=str(model_path),
                inputs=parameters or {},
                summary=f"Runtime exited with code {result.returncode}: " + (result.stderr or result.stdout).strip(),
                **logs,
            )
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return PhaseHistoryResult(
                status="error",
                model_path=str(model_path),
                inputs=parameters or {},
                summary=f"Could not read runtime JSON result: {type(exc).__name__}: {exc}",
                **logs,
            )

    if not isinstance(payload, dict):
        return PhaseHistoryResult(
            status="error",
            model_path=str(model_path),
            inputs=parameters or {},
            summary="Invalid runtime JSON result: expected an object.",
            **logs,
        )
    if payload.get("status") == "waiting_for_Tmax_approval":
        return PhaseHistoryResult(
            status="waiting_for_Tmax_approval",
            model_path=str(model_path),
            inputs=parameters or {},
            summary=payload.get("summary", "Runtime requires Tmax approval."),
            **logs,
        )
    if payload.get("stage", stage) != stage:
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {},
            summary=f"Runtime returned stage {payload['stage']!r} for requested stage {stage!r}.",
            **logs,
        )
    phases = payload.get("phases")
    phase_diagnostics = payload.get("phase_diagnostics")
    if phases is not None and (not isinstance(phases, list)
                               or not all(isinstance(item, dict) for item in phases)):
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {},
            summary="Invalid runtime JSON result: phases must be a list of objects or null.", **logs,
        )
    if phase_diagnostics is not None and not isinstance(phase_diagnostics, dict):
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {},
            summary="Invalid runtime JSON result: phase_diagnostics must be an object or null.", **logs,
        )
    reported_error = payload.get("status", "success") not in ("success", "partial")
    transitions = payload.get("transitions", [] if reported_error else None)
    if not isinstance(transitions, list) or not all(isinstance(item, dict) for item in transitions):
        return PhaseHistoryResult(
            status="error",
            model_path=str(model_path),
            inputs=parameters or {},
            summary="Invalid runtime JSON result: transitions must be a list of objects.",
            **logs,
        )
    for item in transitions:
        if item.get("trantype") == 2:
            if "Tnuc" in item:
                item.setdefault("second_order_temperature", item["Tnuc"])
            item.pop("Tn", None)
    nucleation_transitions = [item for item in transitions if item.get("trantype") != 2]
    tc_list = [_critical_temperature(item) for item in transitions]
    tn_list = [item.get("Tn", item.get("Tnuc")) for item in nucleation_transitions]
    action_over_t = [
        _action_over_t(item.get("action"), temperature)
        for item, temperature in zip(nucleation_transitions, tn_list)
    ]
    count = len(nucleation_transitions) if stage == "nucleation" else len(transitions)
    summary = f"Found {count} {stage} transition(s)."
    second_order_count = len(transitions) - len(nucleation_transitions)
    if stage == "nucleation" and second_order_count:
        summary += f" Preserved {second_order_count} second-order transition(s)."
    status = "error" if reported_error else payload.get("status", "success")
    warnings = list(payload.get("warnings", []))
    diagnostics = payload.get("nucleation_diagnostics", {})
    if not isinstance(diagnostics, dict):
        return PhaseHistoryResult(
            status="error", model_path=str(model_path), inputs=parameters or {},
            summary="Invalid runtime JSON result: nucleation_diagnostics must be an object.", **logs,
        )
    if diagnostics and diagnostics.get("status") in ("failed", "partial", "unavailable"):
        status = "partial" if transitions else "error"
        summary = "Nucleation history incomplete; native numerical/coverage diagnostics are retained."
        if not nucleation_transitions:
            summary += " Tn is numerically unavailable, not a physical no-transition result."
        warnings.append("The native cooling history is incomplete; unresolved channels were not skipped or retuned.")
        if diagnostics.get("status") == "unavailable" or diagnostics.get("observation_errors"):
            warnings.append("Native CT diagnostic coverage is unavailable or unsupported; no complete history is certified.")
    if reported_error:
        summary += " Runtime reported " + repr(payload.get("status")) + ": " + str(payload.get("summary") or payload.get("error") or "unspecified failure")
    labels = (_classify_path(transitions) if stage == "nucleation" else
              ["critical_transitions" if transitions else "no_critical_transitions"])
    if stage == "nucleation" and status != "success":
        labels = ["partial_nucleation_history" if transitions else "nucleation_numerically_unavailable"]
    return PhaseHistoryResult(
        status=status,
        model_path=str(model_path),
        inputs=payload.get("parameters", parameters or {}),
        transitions=transitions,
        Tc_list=tc_list,
        Tn_list=tn_list,
        action_over_T=action_over_t,
        path_labels=labels,
        summary=summary,
        warnings=warnings,
        nucleation_diagnostics=diagnostics,
        symmetry_diagnostics=payload.get("symmetry_diagnostics", []),
        phases=phases,
        phase_diagnostics=phase_diagnostics,
        **logs,
    )


def _runtime_log_text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def _critical_temperature(transition: dict[str, Any]) -> Any:
    associated = transition.get("crit_trans")
    associated_tc = associated.get("Tcrit") if isinstance(associated, dict) else None
    return transition.get("Tc", transition.get("Tcrit", associated_tc))


def _action_over_t(action: object, temperature: object) -> float | None:
    try:
        if action is None or temperature in (None, 0):
            return None
        return float(action) / float(temperature)
    except Exception:
        return None


def _classify_path(transitions: list[dict[str, Any]]) -> list[str]:
    transitions = [item for item in transitions if item.get("trantype") != 2]
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
