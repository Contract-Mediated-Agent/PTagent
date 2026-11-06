"""Read-only observation of native CT searches, including handled failures."""


def observe_ct_nucleation(operation, model=None, diagnostics=None, serialize_transition=None):
    """Run unchanged solver code and retain its numerical/coverage evidence.

    CT does not expose handled PotentialErrors or its action cache publicly.
    Observe its channel/history and deformation frames; do not replace a solver, retry a
    channel, change a bracket, or interpret a missing solution as physical absence.
    This function is self-contained so generated models can embed it verbatim.
    """
    import math
    import sys

    previous = sys.gettrace()
    if getattr(previous, '_ptagent_ct_observer', False):
        return operation()
    report = diagnostics if diagnostics is not None else {}
    report.clear()
    report.update(schema='ptagent.ct_nucleation.v1', channels=[], history_complete=False,
                  status='unavailable', tn_status='numerical_unavailable', observation_errors=[],
                  path_deformations=[])
    state = {'model': model, 'phases': None, 'transitions': [], 'history_observed': False}
    active = {}
    previous_locals = {}

    def plain(value):
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in value.items() if k != 'instanton'}
        if isinstance(value, (list, tuple, set)):
            return [plain(v) for v in value]
        if hasattr(value, 'tolist'):
            return plain(value.tolist())
        return repr(value)

    def finite(value):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return False

    def interval(phase):
        times = getattr(phase, 'T', [])
        return plain([times[0], times[-1]]) if len(times) else None

    def trace(frame, event, arg):
        fid = id(frame)
        old = previous if event == 'call' else previous_locals.get(fid)
        if old is not None:
            continuation = old(frame, event, arg)
            if continuation is not None:
                previous_locals[fid] = continuation
            else:
                previous_locals.pop(fid, None)
        name = frame.f_code.co_name
        native = frame.f_globals.get('__name__') == 'cosmoTransitions.transitionFinder'
        history = name in ('findAllTransitions', '_symmetry_transition_history',
                           '_symmetry_find_all_transitions')
        channel = native and name == 'tunnelFromPhase'
        sample = native and name == '_tunnelFromPhaseAtT'
        deformation = (frame.f_globals.get('__name__') == 'cosmoTransitions.pathDeformation'
                       and name == 'deformPath')
        if not (history or channel or sample or deformation):
            if event == 'return':
                previous_locals.pop(fid, None)
            return trace if fid in previous_locals else None
        if previous is None:
            frame.f_trace_lines = False
        values = frame.f_locals
        if deformation and event == 'return':
            parent = frame.f_back
            while parent is not None and parent.f_code.co_name != '_tunnelFromPhaseAtT':
                parent = parent.f_back
            context = parent.f_locals if parent is not None else {}
            # Record the actual native decision, including first-step convergence.
            # A returned instanton's recomputed fRatio is not that decision.
            report['path_deformations'].append({
                'converged': plain(arg),
                'steps': plain(getattr(values.get('self'), 'num_steps', None)),
                'fRatio': plain(values.get('fRatio')),
                'fRatioConv': plain(values.get('fRatioConv')),
                'converge_0': plain(values.get('converge_0')),
                'temperature': plain(context.get('T')),
                'target': plain(context.get('tdict', {}).get('low_phase')),
            })
        if history:
            candidate = values.get('self')
            if candidate is not None and hasattr(candidate, 'phases'):
                state['model'] = candidate
            if event == 'call' and native:
                if 'phases' in values:
                    state['phases'] = dict(values['phases'])
                else:
                    report['observation_errors'].append('Native history no longer exposes phases.')
            if event == 'return':
                partial = values.get('transitions', arg)
                if isinstance(partial, list):
                    state['transitions'] = partial
                    state['history_observed'] = True
        if channel:
            if event == 'call':
                if not all(key in values for key in ('start_phase', 'phases', 'Tmax')):
                    report['observation_errors'].append('Unsupported native tunnelFromPhase arguments.')
                    return trace
                source = values['start_phase']
                row = {'source': plain(source.key), 'targets': plain(list(values['phases'])),
                       'source_interval': plain([source.T[0], source.T[-1]]),
                       'ceiling': plain(values['Tmax']), 'events': [], 'samples': [], 'attempts': []}
                report['channels'].append(row)
                active[fid] = row
            row = active.get(fid)
            if row is not None and event == 'exception':
                kind, error, _ = arg
                row['events'].append({'function': name, 'type': kind.__name__,
                                      'message': str(error), 'args': plain(error.args)})
            if row is not None and event == 'return':
                if 'outdict' not in values:
                    report['observation_errors'].append('Native channel action cache is unavailable.')
                row['samples'] = [dict(temperature=plain(t), **plain(data))
                                  for t, data in values.get('outdict', {}).items()]
                row['result'] = plain(arg)
                row['finite_action_samples'] = sum(
                    finite(s.get('action')) and s.get('trantype') == 1 for s in row['samples'])
                recovered = ({plain(arg.get('low_phase'))} if isinstance(arg, dict)
                             and arg.get('trantype') == 1 and finite(arg.get('action')) else set())
                for event_record in row['events']:
                    event_record['recovered'] = (
                        event_record.get('target') in recovered
                        if event_record['function'] == '_tunnelFromPhaseAtT' else isinstance(arg, dict))
                # Brent's unbracketed return and fmin's StopIteration are native
                # search control flow. A later native solution recovers them.
                unresolved = [e for e in row['events'] if not e['recovered']
                              and e['type'] != 'StopIteration'
                              and not (e['type'] == 'ValueError'
                                       and e['message'] == 'f(a) and f(b) must have different signs')]
                row['unresolved_events'] = unresolved
                row['invalid_action_attempts'] = [s for s in row['attempts']
                                                 if s.get('trantype') == 1 and not finite(s.get('action'))]
                row['outcome'] = ('unavailable' if 'outdict' not in values else
                                  'numerical_failure' if unresolved or row['invalid_action_attempts'] else
                                  'transition_found' if isinstance(arg, dict) else
                                  'no_nucleation_found')
                active.pop(fid, None)
        if sample and event in ('exception', 'return'):
            parent = frame.f_back
            while parent is not None and id(parent) not in active:
                parent = parent.f_back
        if sample and event == 'return' and parent is not None:
            for attempt in values.get('tunnel_list', []):
                record = plain(attempt)
                instanton = attempt.get('instanton')
                if hasattr(instanton, 'fRatio'):
                    record['tunnelling_fRatio'] = plain(instanton.fRatio)
                active[id(parent)]['attempts'].append(record)
        if sample and event == 'exception':
            kind, error, _ = arg
            # CT's scalar/array dispatch intentionally catches TypeError here.
            if kind.__name__ == 'PotentialError':
                if parent is not None:
                    active[id(parent)]['events'].append({
                        'function': name, 'type': kind.__name__, 'message': str(error),
                        'args': plain(error.args), 'temperature': plain(values.get('T')),
                        'target': plain(values.get('tdict', {}).get('low_phase'))})
        if event == 'return':
            previous_locals.pop(fid, None)
        return trace

    trace._ptagent_ct_observer = True
    failure = None
    try:
        sys.settrace(trace)
        return operation()
    except Exception as error:
        failure = {'type': type(error).__name__, 'message': str(error), 'args': plain(error.args)}
        raise
    finally:
        sys.settrace(previous)
        observed_model = state['model']
        phases = getattr(observed_model, 'phases', None)
        if phases is None:
            phases = state['phases']
        transitions = state['transitions']
        for channel in report['channels']:
            second_order_targets = {plain(t.get('low_phase')) for t in transitions
                                    if t.get('trantype') == 2 and plain(t.get('high_phase')) == channel['source']}
            for event_record in channel['events']:
                if (event_record.get('target') in second_order_targets
                        and event_record.get('args', [None])[-1] == 'no barrier'):
                    event_record['recovered'] = True
            channel['unresolved_events'] = [e for e in channel.get('unresolved_events', []) if not e['recovered']]
            if (channel['outcome'] == 'numerical_failure' and not channel['unresolved_events']
                    and not channel['invalid_action_attempts']):
                channel['outcome'] = 'second_order_link' if second_order_targets else 'no_nucleation_found'
        report['partial_transitions'] = plain(transitions)
        if serialize_transition is not None:
            report['validated_transitions'] = []
            for transition in transitions:
                try:
                    report['validated_transitions'].append(serialize_transition(transition))
                except Exception as error:
                    report.setdefault('transition_validation_errors', []).append(str(error))
        report['phase_intervals'] = ([{'key': plain(k), 'temperature_interval': interval(p)}
                                      for k, p in phases.items()] if phases else [])
        for attribute in ('symmetry_tunnelling_channels', 'symmetry_pair_diagnostics',
                          'symmetry_phase_groups', 'symmetry_phase_associations'):
            value = getattr(observed_model, attribute, None)
            if value is not None:
                report[attribute] = plain(value)
        terminal = None
        if phases and transitions:
            terminal = phases.get(transitions[-1].get('low_phase'))
        elif phases and report['channels']:
            terminal = next((p for p in phases.values()
                             if plain(p.key) == report['channels'][-1]['source']), None)
        elif phases:
            orbit_channels = getattr(observed_model, 'symmetry_tunnelling_channels', [])
            if orbit_channels:
                terminal = phases.get(orbit_channels[-1]['source'])
        failed_channels = any(c['outcome'] == 'numerical_failure' for c in report['channels'])
        reached_floor = terminal is not None and interval(terminal) is not None and float(terminal.T[0]) <= 0
        complete = (state['history_observed'] and reached_floor and not failed_channels and failure is None
                    and not report['observation_errors'] and not report.get('transition_validation_errors'))
        report['history_complete'] = bool(complete)
        report['termination'] = ('solver_exception' if failure else
                                 'unsupported_native_observation' if report['observation_errors'] else
                                 'no_phases' if phases == {} else
                                 'not_observed' if not state['history_observed'] else
                                 'reached_low_temperature' if reached_floor else 'cooling_history_stopped')
        report['terminal_phase'] = plain(terminal.key) if terminal is not None else None
        visited = {c['source'] for c in report['channels']}
        visited.update(plain(c['source']) for c in getattr(observed_model, 'symmetry_tunnelling_channels', []))
        report['unvisited_native_phases'] = [plain(k) for k in (phases or {}) if plain(k) not in visited]
        report['status'] = ('complete' if complete else 'partial' if transitions else
                            'failed' if failure or failed_channels or state['history_observed'] else 'unavailable')
        usable = report.get('validated_transitions', transitions)
        report['tn_status'] = ('available' if any(t.get('trantype') == 1
                               and finite(t.get('Tn', t.get('Tnuc'))) and finite(t.get('action'))
                               for t in usable) else
                               'not_found' if complete else 'numerical_unavailable')
        if failure:
            report['error'] = failure
        report['interpretation'] = ('Coverage refers only to the native cooling path and traced temperature range; '
                                    'an empty result is not proof of physical absence of a transition. '
                                    'Deformation decisions are recorded per native call, not inferred from a fixed residual cutoff; '
                                    'intermediate deformation failures do not alone establish final bounce failure. '
                                    'Unvisited native phases may include alternative branches, not required steps. '
                                    'Nonfinite diagnostic values are encoded as strings, not physical results.')
        if observed_model is not None:
            observed_model.nucleation_diagnostics = report
