from __future__ import annotations

from typing import Any


def parse_detailed_smoke_output(stdout: str) -> dict[int, dict[str, Any]]:
    parsed: dict[int, dict[str, Any]] = {}
    for raw_line in str(stdout or "").splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 3 or not parts[0].startswith("PTAGENT_"):
            continue
        try:
            index = int(parts[1])
        except ValueError:
            continue
        entry = parsed.setdefault(index, {"scalars": {}, "vectors": {}})
        if parts[0] == "PTAGENT_POINT":
            if (len(parts) - 2) % 2:
                continue
            for key, value in zip(parts[2::2], parts[3::2]):
                try:
                    entry["scalars"][key] = float(value)
                except ValueError:
                    pass
        elif parts[0] == "PTAGENT_VECTOR":
            name = parts[2]
            values: list[float] = []
            ok = True
            for value in parts[3:]:
                try:
                    values.append(float(value))
                except ValueError:
                    ok = False
                    break
            if ok:
                entry["vectors"][name] = values
    return parsed
