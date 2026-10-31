"""Spline-aware temperature samples for CT symmetry comparisons."""


def orbit_comparison_temperatures(left, right, sign):
    """Return sorted unique samples on the phases' positive-width overlap.

    Include trajectory/spline knots, midpoints, and numerical stationary points
    of ``||left(T) - sign * right(T)||**2``. Native CT parametric ``tck`` tuples
    with degree 0 through 3 are supported. Constant residual intervals need only
    their knots/midpoints; nearly-real multiple roots are retained conservatively.
    Invalid, nonfinite, unsupported, or nonoverlapping inputs raise ValueError.
    All imports/helpers are local so inspect.getsource can embed this function.
    """
    import numpy as np
    from numpy.polynomial import polynomial
    from scipy.interpolate import PPoly

    def real_array(value):
        array = np.asarray(value)
        if array.dtype.kind not in "iuf":
            raise ValueError("Expected real numeric spline data")
        array = np.asarray(array, dtype=float)
        if not np.all(np.isfinite(array)):
            raise ValueError("Nonfinite spline data")
        return array

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            signs = real_array(sign)
            if signs.ndim != 1 or not signs.size or not np.all(np.abs(signs) == 1):
                raise ValueError("sign must contain one +/-1 per field")
            trajectories, spline_knots, derivatives = [], [], []
            for phase in (left, right):
                times, values = real_array(phase.T), real_array(phase.X)
                if (times.ndim != 1 or len(times) < 2
                        or not np.all(np.diff(times) > 0)
                        or values.shape != (len(times), len(signs))):
                    raise ValueError("Invalid phase trajectory")
                tck = phase.tck
                if not isinstance(tck, (tuple, list)) or len(tck) != 3:
                    raise ValueError("Expected a native CT (knots, coefficients, degree) tuple")
                knots, coefficients, degree = tck
                if (isinstance(degree, (bool, np.bool_))
                        or not isinstance(degree, (int, np.integer))
                        or not 0 <= degree <= 3):
                    raise ValueError("Only spline degrees 0 through 3 are supported")
                knots, coefficients = real_array(knots), real_array(coefficients)
                if (knots.ndim != 1 or len(knots) < 2*(degree+1)
                        or not np.all(np.diff(knots) >= 0)
                        or coefficients.shape != (len(signs), len(knots)-degree-1)
                        or knots[degree] >= knots[-degree-1]
                        or times[0] < knots[degree] or times[-1] > knots[-degree-1]):
                    raise ValueError("Invalid spline layout or unsupported phase domain")
                interior = knots[(knots > knots[degree]) & (knots < knots[-degree-1])]
                if interior.size and np.any(np.unique(interior, return_counts=True)[1] > degree):
                    raise ValueError("Discontinuous spline layouts are unsupported")
                components = []
                for row in coefficients:
                    poly = PPoly.from_spline((knots, row, degree), extrapolate=False)
                    if not np.all(np.isfinite(poly.c)):
                        raise ValueError("Nonfinite power-basis spline coefficients")
                    components.append([poly.derivative(order) for order in range(4)])
                trajectories.append(times)
                spline_knots.append(knots)
                derivatives.append(components)

            lo = max(times[0] for times in trajectories)
            hi = min(times[-1] for times in trajectories)
            if lo >= hi:
                raise ValueError("Phases have no positive-width common interval")
            knots = np.unique(np.concatenate(([lo, hi], *trajectories, *spline_knots)))
            knots = knots[(knots >= lo) & (knots <= hi)]
            samples = list(knots)
            samples.extend(knots[:-1] + np.diff(knots)/2)
            for start, end in zip(knots[:-1], knots[1:]):
                width = end-start
                # Taylor coefficients in u=(T-start)/width keep roots near [0, 1].
                scale = width**np.arange(4)/np.array([1., 1., 2., 6.])
                coefficients = []
                for index, reflection in enumerate(signs):
                    a = np.array([p(start) for p in derivatives[0][index]])
                    b = np.array([p(start) for p in derivatives[1][index]])
                    coefficients.append((a-reflection*b)*scale)
                coefficients = np.asarray(coefficients)
                if not np.all(np.isfinite(coefficients)):
                    raise ValueError("Nonfinite residual polynomial")
                magnitude = np.max(np.abs(coefficients))
                if magnitude == 0:
                    continue
                norm_squared = np.zeros(7)
                for row in coefficients/magnitude:
                    square = polynomial.polymul(row, row)
                    norm_squared[:len(square)] += square
                derivative = polynomial.polytrim(polynomial.polyder(norm_squared))
                if len(derivative) <= 1:
                    continue
                roots = polynomial.polyroots(derivative/np.max(np.abs(derivative)))
                if not np.all(np.isfinite(roots)):
                    raise ValueError("Nonfinite stationary roots")
                # Multiple real roots can split into a small complex cluster.
                root_tolerance = 8*np.finfo(float).eps**(1/(len(derivative)-1))
                for root in roots:
                    real = float(root.real)
                    if (abs(root.imag) <= root_tolerance*max(1., abs(real))
                            and -root_tolerance <= real <= 1+root_tolerance):
                        temperature = start+np.clip(real, 0., 1.)*width
                        samples.append(float(np.clip(temperature, start, end)))
            return np.unique(np.asarray(samples, dtype=float))
    except (AttributeError, TypeError, IndexError, ValueError, OverflowError,
            FloatingPointError, np.linalg.LinAlgError) as exc:
        raise ValueError("Cannot sample phase-orbit spline comparison: " + str(exc)) from exc
