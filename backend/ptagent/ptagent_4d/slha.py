from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


class SlhaParseError(ValueError):
    """Raised when a numerical parameter point cannot be read safely."""


_BLOCK_HEADER = re.compile(
    r"^\s*BLOCK\s+([A-Za-z0-9_]+)(?:\s+Q\s*=\s*([^\s#]+))?",
    re.IGNORECASE,
)
_COMPLEX_BLOCK_PREFIXES = ("IM",)


@dataclass(frozen=True)
class SlhaBlock:
    name: str
    scale: float | None
    entries: dict[tuple[int, ...], float] = field(default_factory=dict)


@dataclass(frozen=True)
class SlhaDocument:
    blocks: dict[str, SlhaBlock]

    def value(self, block: str, indices: Iterable[int]) -> float | None:
        item = self.blocks.get(str(block).upper())
        if item is None:
            return None
        return item.entries.get(tuple(int(index) for index in indices))


def parse_slha_file(path: str | Path, *, reject_complex: bool = True) -> SlhaDocument:
    source = Path(path).expanduser().resolve()
    return parse_slha(source.read_text(encoding="utf-8", errors="strict"), reject_complex=reject_complex)


def parse_slha(text: str, *, reject_complex: bool = True) -> SlhaDocument:
    block_entries: dict[str, dict[tuple[int, ...], float]] = {}
    block_scales: dict[str, float | None] = {}
    current = ""
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        header = _BLOCK_HEADER.match(line)
        if header:
            current = header.group(1).upper()
            if reject_complex and current.startswith(_COMPLEX_BLOCK_PREFIXES):
                raise SlhaParseError(
                    f"Complex SLHA block {current!r} is outside the real-parameter SARAH v1 scope."
                )
            block_entries.setdefault(current, {})
            block_scales[current] = _number(header.group(2), line_number) if header.group(2) else None
            continue
        if line.upper().startswith("DECAY"):
            current = ""
            continue
        if not current:
            continue
        tokens = line.split()
        if not tokens:
            continue
        try:
            indices = tuple(int(token) for token in tokens[:-1])
        except ValueError as exc:
            raise SlhaParseError(
                f"SLHA entry indices must be integers on line {line_number}: {raw_line!r}"
            ) from exc
        block_entries[current][indices] = _number(tokens[-1], line_number)

    return SlhaDocument(
        blocks={
            name: SlhaBlock(name=name, scale=block_scales.get(name), entries=dict(entries))
            for name, entries in block_entries.items()
        }
    )


def _number(value: str | None, line_number: int) -> float:
    if value is None:
        raise SlhaParseError(f"Missing numeric value on line {line_number}.")
    normalized = re.sub(r"(?<=\d)[dD](?=[+-]?\d)", "E", value.strip())
    try:
        result = float(normalized)
    except ValueError as exc:
        raise SlhaParseError(f"Invalid real number on line {line_number}: {value!r}") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise SlhaParseError(f"Non-finite number on line {line_number}: {value!r}")
    return result
