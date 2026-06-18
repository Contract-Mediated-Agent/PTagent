from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contract import ThreeDeftBlocked, looks_like_compile_approval_text, validate_contract_template, write_resolved_artifacts
from .merge import MERGED_CONTRACT_NAME


@dataclass(frozen=True)
class ApprovalResult:
    output_path: Path
    gate: str
    key: str
    validation_ok: bool


def approve_contract_gate(
    template_path: str | Path,
    *,
    gate: str,
    user_approval: str = "",
    output_path: str | Path | None = None,
) -> ApprovalResult:
    """Record a whole-template user approval gate in the Markdown contract."""

    gate = gate.casefold().replace("-", "_")
    if gate in {"phasetracer", "phasetracer_compile", "compile"}:
        heading = "PhaseTracer"
        key = "approve_compile"
        review_gate = "phasetracer_compile"
    else:
        raise ThreeDeftBlocked("approve gate must be phasetracer_compile.")

    template_path = Path(template_path)
    target = Path(output_path) if output_path else template_path
    if key == "approve_compile" and target.name != MERGED_CONTRACT_NAME:
        raise ThreeDeftBlocked(
            f"PhaseTracer compile approval must be recorded on {MERGED_CONTRACT_NAME} "
            "after DRalgo output has been merged and reviewed."
        )
    if key == "approve_compile" and not looks_like_compile_approval_text(user_approval):
        raise ThreeDeftBlocked(
            "PhaseTracer compile approval needs explicit user approval text. "
            "Pass --user-approval with the user's review/approval sentence after they inspect the merged contract."
        )
    markdown = template_path.read_text(encoding="utf-8")
    report = validate_contract_template(markdown)
    if key == "approve_compile":
        blocking = report.compile_blocking_issues
        ready = not blocking
    else:
        blocking = report.dralgo_blocking_issues
        ready = report.ready_for_run
    if not ready:
        preview = "; ".join(f"{issue.field_key}: {issue.message}" for issue in blocking[:5])
        raise ThreeDeftBlocked(f"Cannot approve {gate}: contract still has blockers. {preview}")

    updated = _replace_key_value(markdown, heading=heading, key=key, value="true")
    if key == "approve_compile":
        updated = _replace_or_insert_key_value(
            updated,
            heading=heading,
            key="approval_record",
            value=_approval_record(user_approval),
            notes="Explicit user approval text recorded by the approve command.",
        )
    target.write_text(updated, encoding="utf-8")
    write_resolved_artifacts(target, gate=review_gate)
    validation = validate_contract_template(target.read_text(encoding="utf-8"), require_compile_approval=True)
    validation_ok = validation.ready_for_compile
    return ApprovalResult(output_path=target, gate=review_gate, key=key, validation_ok=validation_ok)


def _replace_key_value(markdown: str, *, heading: str, key: str, value: str) -> str:
    lines = markdown.splitlines()
    start = _find_heading(lines, heading)
    if start is None:
        raise ThreeDeftBlocked(f"Could not find section {heading!r} in contract.")
    end = _next_heading(lines, start)
    for index in range(start + 1, end):
        row = lines[index].strip()
        if not row.startswith("|") or row.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if cells and cells[0] == key:
            while len(cells) < 3:
                cells.append("")
            cells[1] = value
            if "approved" not in cells[2].casefold():
                cells[2] = (cells[2] + " User approved whole-template review gate.").strip()
            lines[index] = "| " + " | ".join(cells) + " |"
            return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
    raise ThreeDeftBlocked(f"Could not find key {key!r} under section {heading!r}.")


def _replace_or_insert_key_value(markdown: str, *, heading: str, key: str, value: str, notes: str) -> str:
    try:
        return _replace_key_value(markdown, heading=heading, key=key, value=value)
    except ThreeDeftBlocked:
        lines = markdown.splitlines()
        start = _find_heading(lines, heading)
        if start is None:
            raise ThreeDeftBlocked(f"Could not find section {heading!r} in contract.")
        end = _next_heading(lines, start)
        insert_at = end
        for index in range(start + 1, end):
            if lines[index].strip().startswith("| reference_fields |"):
                insert_at = index
                break
        lines.insert(insert_at, f"| {key} | {value} | {notes} |")
        return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def _approval_record(text: str) -> str:
    compact = " ".join(text.strip().split())
    compact = compact.replace("|", "/")
    return compact[:240] if compact else "user reviewed and approved"


def _find_heading(lines: list[str], heading: str) -> int | None:
    pattern = heading.casefold()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip().casefold() == pattern:
            return index
    return None


def _next_heading(lines: list[str], start: int) -> int:
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                return index
    return len(lines)


__all__ = ["ApprovalResult", "approve_contract_gate"]
