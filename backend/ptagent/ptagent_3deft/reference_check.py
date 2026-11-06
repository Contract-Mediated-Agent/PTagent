from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contract import ThreeDeftBlocked, physical_spec_hash, validate_contract_template
from .layout import proof_dir_for_template
from .phasetracer import _render_cpp_project


REFERENCE_CHECK_NAME = "three_deft_reference_check.json"
_APPROVAL_FIELDS = {
    "phasetracer.approve_compile", "phasetracer.approval_record", "phasetracer.approval_spec_hash",
}


def check_reference_template(
    template_path: str | Path, *, output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Check a draft's numerical domain without approval, native execution or edits."""
    template = Path(template_path)
    markdown = template.read_text(encoding="utf-8")
    validation = validate_contract_template(markdown)
    target = Path(output_path) if output_path else proof_dir_for_template(template) / REFERENCE_CHECK_NAME
    if target.resolve() == template.resolve():
        raise ThreeDeftBlocked("Reference-check output must not overwrite the contract.")
    report: dict[str, Any] = {
        "schema": "ptagent.3deft.reference_check.v1",
        "template_path": str(template.resolve()),
        "template_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "physical_spec_hash": physical_spec_hash(validation.contract),
        "ok": False, "status": "blocked", "reference_point_auto_selected": False,
        "reference_suggestions": [], "suggestion_evaluation_count": 0,
        "scope": "Pre-approval Python domain/consistency check only; not C++ or independent DRalgo validation, a vacuum test, or a phase calculation.",
        "policy": "Preserve model inputs, scales, active orders and the current reference. Never discard imaginary parts or replace fields/temperature automatically. Any alternative remains subject to explicit review and exact-contract approval.",
    }
    blocking = [issue for issue in validation.compile_blocking_issues if issue.field_key not in _APPROVAL_FIELDS]
    if blocking:
        report["reason"] = "Resolve contract construction blockers before numerical reference review."
        report["blockers"] = [
            {"field": issue.field_key, "code": issue.code, "message": issue.message}
            for issue in blocking
        ]
    else:
        try:
            report.update(_render_cpp_project(validation.contract, reference_review_only=True))
            report["status"] = "finite_consistent" if report["ok"] else "invalid_reference"
        except (ThreeDeftBlocked, ValueError, OverflowError, ZeroDivisionError) as exc:
            report["reason"] = str(exc)
    finite = [row for row in report["reference_suggestions"] if row.get("valid")]
    report["finite_suggestion_count"] = len(finite)
    # The compiler's deterministic order tries the same temperature first, then
    # dimensionless field/temperature rescalings. No fit to an expected V occurs.
    report["suggested_reference_for_review"] = finite[0] if finite else None
    if report["ok"]:
        report["next_action"] = "Include this point and its checked specification in the final contract/graph review; obtain approval, then compile and run native checks."
    elif finite:
        report["next_action"] = "Present the invalid point and finite diagnostic proposal in the same review batch. Change reference rows only after acceptance, recheck, then obtain final exact-contract approval."
    else:
        report["next_action"] = "Report the domain/contract failure. Request an applicable reference or a separately justified physical prescription; do not tune parameters, drop orders, take absolute values or real parts to pass."
    target.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(target.resolve())
    target.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report
