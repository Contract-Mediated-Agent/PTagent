"""Generated-model evaluation usable by a configured runtime without PTagent installed."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np


def load_model(model_path: Path):
    path = model_path.resolve()
    name = f"_ptagent_python_model_{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not import generated Python model from {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "build_model"):
        raise ValueError(f"Generated Python model {path} has no build_model() function.")
    try:
        return module.build_model(run_tc=False, print_tc=False)
    except TypeError:
        return module.build_model()


def evaluate_vtot(model, phi, temperature, *, include_radiation=True):
    value = model.Vtot(np.asarray(phi, dtype=float), np.asarray(float(temperature), dtype=float),
                       include_radiation=include_radiation)
    number = float(np.asarray(value, dtype=float).item())
    if not np.isfinite(number):
        raise ValueError(f"Non-finite numerical comparison value: {number}")
    return number


def main():
    request = json.load(sys.stdin)
    # A model may print diagnostics during import/evaluation. Keep stdout a
    # single JSON response, with diagnostics preserved on stderr.
    with contextlib.redirect_stdout(sys.stderr):
        model = load_model(Path(request["model_path"]))
        values = {str(point["index"]): evaluate_vtot(model, point["phi"], point["T"])
                  for point in request["points"]}
    print(json.dumps(values, allow_nan=False))


if __name__ == "__main__":
    main()
