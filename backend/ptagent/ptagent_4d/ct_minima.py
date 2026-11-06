"""Native tracing with accuracy consistent with the reviewed orbit tolerance."""


def trace_symmetry_phases(model, tracingArgs=None):
    """Use CT's native tracer, resolving trajectories before comparing them.

    CT normally targets a field displacement of 100*x_eps. That may be coarser
    than the separately reviewed symmetry matching tolerance. Limit the tracing
    displacement, rather than enlarging the tolerance or allowing nonlocal
    refinement after tracing. Preserve every native phase and interval.
    """
    import numpy as np
    from cosmoTransitions import transitionFinder

    options = dict(forbidCrit=model.forbidPhaseCrit)
    options.update(tracingArgs or {})
    step = float(np.min(np.abs(model.x_eps)))
    requested_target = float(options.pop('deltaX_target', 100*step))
    tolerance = float(model._symmetry_tolerance)
    if not all(np.isfinite(value) and value > 0 for value in (step, requested_target, tolerance)):
        raise ValueError("Positive finite tracing and orbit tolerances are required")
    target = min(requested_target, tolerance)
    points = [[point, 0.] for point in model.approxZeroTMin()]
    model.phases = transitionFinder.traceMultiMin(
        model.Vtot, model.dgradV_dT, model.d2V, points,
        tLow=0., tHigh=model.Tmax, deltaX_target=target, **options)
    transitionFinder.removeRedundantPhases(
        model.Vtot, model.phases, step*1e-2, step*10)
    model.symmetry_tracing_settings = {
        'deltaX_target': target, 'matching_tolerance': model._symmetry_tolerance,
        'method': 'native_traceMultiMin',
    }
    return model._record_symmetry_phase_history(model.phases)
