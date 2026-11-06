"""A reviewed subset of sorted eigenvalues, never a basis-component filter."""
from __future__ import annotations

import re
from typing import Any


def selected_eigenvalue_indices(matrix: dict[str, Any]) -> list[int]:
    size = len(matrix.get("basis", []))
    raw = matrix.get("eigenvalue_indices", "all")
    if isinstance(raw, str):
        if raw.strip() == "all":
            return list(range(size))
        if not re.fullmatch(r"\s*\d+\s*(?:,\s*\d+\s*)*", raw):
            raise ValueError("Use all or comma-separated zero-based ascending eigenvalue ranks.")
        indices = [int(item) for item in raw.split(",")]
    elif isinstance(raw, list) and all(type(item) is int for item in raw):
        indices = list(raw)
    else:
        raise ValueError("Eigenvalue ranks must be integers, not expressions or basis names.")
    if not indices or indices != sorted(set(indices)) or any(i < 0 or i >= size for i in indices):
        raise ValueError("Eigenvalue ranks must be nonempty, unique, ascending and inside the matrix dimension.")
    return indices


def eigenvalue_slice(matrix: dict[str, Any]) -> str:
    """Generated NumPy trailing-axis selection; empty preserves legacy all-mode code."""
    indices = selected_eigenvalue_indices(matrix)
    return "" if indices == list(range(len(matrix.get("basis", [])))) else f"[..., {indices!r}]"
