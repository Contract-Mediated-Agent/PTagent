from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_layout import proof_materials_dir
from .backends import (
    COMPARE_BACKENDS_SENTINEL,
    SUPPORTED_COMPILE_BACKENDS,
    normalize_compile_backend,
    supported_backend_options_text,
    supported_backends_text,
)
from .schemas import FormulaRecord, PaperMemory, ValidationIssue, ValidationReport
from .template_contract import parse_contract, validate_contract_template


@dataclass(frozen=True)
class QuestionPacket:
    key: str
    issues: tuple[ValidationIssue, ...]

    @property
    def representative(self) -> ValidationIssue:
        return self.issues[0]


def backend_selection_question(compile_backend: str | None = None) -> dict[str, str]:
    backend = normalize_compile_backend(compile_backend)
    supported = supported_backends_text()
    if backend and backend not in SUPPORTED_COMPILE_BACKENDS:
        return {
            "field_key": "compile.backend",
            "question": f"Backend {compile_backend!r} is not supported yet. Choose one of: {supported}.",
            "reason": "unsupported_backend",
            "suggested_action": "Ask the user to choose `cosmotransitions` or `phasetracer`; do not compile with any other backend.",
        }
    return {
        "field_key": "compile.backend",
        "question": "Which compile backend should PTagent generate: `cosmotransitions` or `phasetracer`?",
        "reason": "backend_selection_required",
        "suggested_action": "Ask the user to choose one supported backend explicitly before running compile.",
    }


def build_question_packets(issues: list[ValidationIssue], contract: dict[str, Any]) -> list[QuestionPacket]:
    """Fuse related validation blockers into one user-answerable question packet."""

    groups: dict[str, list[ValidationIssue]] = {}
    order: list[str] = []
    for issue in sorted(issues, key=_issue_sort_key):
        key = _question_packet_key(issue, contract)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(issue)
    return [QuestionPacket(key=key, issues=tuple(groups[key])) for key in order]


def question_packet_to_dict(
    packet: QuestionPacket,
    contract: dict[str, Any],
    *,
    memory: PaperMemory | None = None,
    compile_backend: str = "",
) -> dict[str, Any]:
    issue = packet.representative
    target = _question_target(issue.field_key, contract)
    is_packet = len(packet.issues) > 1 or _always_render_as_packet(packet.key)
    return {
        "field_key": packet.key if is_packet else issue.field_key,
        "field_keys": [item.field_key for item in packet.issues],
        "question": _packet_prompt(packet, target),
        "reason": "question_packet" if is_packet else issue.code,
        "suggested_action": _packet_agent_action(packet),
        "current_thought": _packet_recommendation(packet, target, memory),
        "compile_backend": compile_backend,
    }


def build_user_guidance_markdown(
    *,
    template_markdown: str,
    validation: ValidationReport | None = None,
    template_path: str | Path | None = None,
    memory: PaperMemory | None = None,
    compile_backend: str | None = None,
) -> str:
    """Render validation blockers as human-facing questions and edit targets."""

    contract = parse_contract(template_markdown)
    backend = normalize_compile_backend(compile_backend)
    backend_context = backend if backend in SUPPORTED_COMPILE_BACKENDS or backend == COMPARE_BACKENDS_SENTINEL else None
    if validation is None or backend_context:
        validation = validate_contract_template(
            template_markdown,
            review_required=True,
            compile_backend=backend_context,
        ).to_report()
    template_label = str(template_path or "contract_template.md")
    all_blocking = sorted(
        [issue for issue in validation.issues if issue.severity == "error"],
        key=_issue_sort_key,
    )
    nonapproval_blocking = [issue for issue in all_blocking if issue.field_key != "review.approved"]
    approval_deferred = bool(nonapproval_blocking) and any(
        issue.field_key == "review.approved" for issue in all_blocking
    )
    blocking = nonapproval_blocking if approval_deferred else all_blocking
    packets = build_question_packets(blocking, contract)
    lines = [
        "# PTagent Question Mode",
        "",
        f"- Template to edit: `{template_label}`",
        "- `contract_template.md` is the only file users should edit.",
        "- Files under `proof_materials/` and `generated_models/` are derived and will be overwritten.",
        "- Related blockers are fused into one question packet, and packets are asked one at a time in dependency order.",
        "- The agent should patch source-evident fields itself before asking; only uncertain physics choices should reach the user.",
        "- The agent should patch the Markdown, rerun this guide, and then ask the next question.",
        "",
        "```powershell",
        f"python -m ptagent resolve --template \"{template_label}\"",
        "```",
        "",
    ]
    backend_gate = _backend_selection_gate_lines(compile_backend)
    lines.extend(backend_gate)
    if backend_gate:
        lines.extend(
            [
                "## Question Progress",
                "",
                "- Current round: 1 backend-selection question.",
                "- Current question: 1 of 1.",
                "- After the backend is selected, rerun `resolve` so PTagent can generate the backend-specific next question.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"
    if not all_blocking:
        status = "No blocking questions remain. Review the rendered Markdown, then run compile when ready."
        if backend_gate:
            status = "No template blockers remain. The backend selection gate above is still mandatory before running compile."
        lines.extend(
            [
                "## Status",
                "",
                status,
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "## How To Answer In Chat",
            "",
            "Answer only the current question packet. The agent can then edit `contract_template.md`, rerun `resolve`, and ask the next packet if one remains.",
            "",
            "```text",
            "Q1: ...",
            "```",
            "",
            "If a candidate is not really a model input, say whether it should become a fixed constant, a derived parameter, or be removed.",
            "After this answer, rerun `resolve`; if blockers remain, this file will be regenerated with the next single question.",
            "",
        ]
    )
    if approval_deferred:
        lines.extend(
            [
                "- Final code-generation approval is intentionally deferred until the physics/formula blockers below are resolved.",
                "",
            ]
        )
    lines.extend(
        [
            "## Question Progress",
            "",
            f"- Backend context: `{backend}`.",
            f"- Underlying blocking fields remaining: {len(blocking)}.",
            f"- Current question: 1 of {len(packets)}.",
            f"- Remaining after this answer: {max(len(packets) - 1, 0)}.",
            "",
            "## Current Question",
            "",
        ]
    )
    if packets:
        lines.extend(_render_question_packet(1, packets[0], contract, memory, compile_backend=backend))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_user_guidance(
    *,
    template_path: str | Path,
    output_path: str | Path | None = None,
    memory_path: str | Path | None = None,
    compile_backend: str | None = None,
) -> tuple[Path, ValidationReport]:
    template = Path(template_path).expanduser().resolve()
    template_markdown = template.read_text(encoding="utf-8")
    backend = normalize_compile_backend(compile_backend)
    backend_context = backend if backend in SUPPORTED_COMPILE_BACKENDS or backend == COMPARE_BACKENDS_SENTINEL else None
    validation = validate_contract_template(
        template_markdown,
        review_required=True,
        compile_backend=backend_context,
    ).to_report()
    memory = _load_memory(memory_path) if memory_path else _default_memory_for_template(template)
    output = Path(output_path).expanduser().resolve() if output_path else proof_materials_dir(template) / "user_questions.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_user_guidance_markdown(
            template_markdown=template_markdown,
            validation=validation,
            template_path=template,
            memory=memory,
            compile_backend=compile_backend,
        ),
        encoding="utf-8",
    )
    return output, validation


def _backend_selection_gate_lines(compile_backend: str | None) -> list[str]:
    backend = normalize_compile_backend(compile_backend)
    if backend in SUPPORTED_COMPILE_BACKENDS or backend == COMPARE_BACKENDS_SENTINEL:
        return []
    supported = supported_backend_options_text()
    if backend:
        lead = f"The requested backend `{compile_backend}` is not supported yet."
    else:
        lead = "No compile backend has been selected for this run yet."
    return [
        "## Mandatory Backend Gate",
        "",
        lead,
        f"Ask the user to choose exactly one supported backend before compile: {supported}.",
        "If the user chooses anything else, say that backend is not supported yet and do not compile.",
        "",
        "Answer slot:",
        "",
        "```text",
        "Backend: ",
        "```",
        "",
    ]


def _render_issue_question(
    number: int,
    issue: ValidationIssue,
    contract: dict[str, Any],
    memory: PaperMemory | None,
    *,
    compile_backend: str = "",
) -> list[str]:
    field_key = issue.field_key
    target = _question_target(field_key, contract)
    prompt = _question_prompt(issue, target)
    recommendation = _recommendation_line(field_key, target, memory)
    lines = [
        f"### Q{number}. {target['title']}",
        "",
        f"- Problem: `{issue.code}` - {issue.message}",
        f"- In plain language: {_plain_language_explanation(issue, target, compile_backend)}",
        "- Materials checked: source material, current contract, generated proof files, explicit user inputs, and selected backend context when available.",
        "- Missing or conflicting information: this field remains unresolved after evidence review.",
        "- Why this blocks: resolving it differently could change physics interpretation, generated backend behavior, or numerical results.",
        f"- Edit target: {target['edit_target']}",
        f"- Accepted format: {target['format']}",
        f"- Ask the user: {prompt}",
        f"- My current thought (reviewable): {recommendation or _default_recommendation(issue, target)}",
        "- If you accept my recommendation: the agent will patch the edit target, rerun `resolve`, and continue with the next blocker if one remains.",
    ]
    if target.get("notes"):
        lines.append(f"- Notes: {target['notes']}")
    evidence = _evidence_lines(field_key, target, memory)
    if evidence:
        lines.extend(["", "Evidence hints:"])
        lines.extend(evidence)
    lines.extend(
        [
            "",
            "Answer slot:",
            "",
            "```text",
            f"Q{number}: ",
            "```",
        ]
    )
    return lines


def _render_question_packet(
    number: int,
    packet: QuestionPacket,
    contract: dict[str, Any],
    memory: PaperMemory | None,
    *,
    compile_backend: str = "",
) -> list[str]:
    if len(packet.issues) == 1 and not _always_render_as_packet(packet.key):
        return _render_issue_question(number, packet.representative, contract, memory, compile_backend=compile_backend)

    issue = packet.representative
    target = _question_target(issue.field_key, contract)
    title = _packet_title(packet, target)
    edit_targets = _packet_edit_targets(packet, contract)
    underlying = _packet_underlying_lines(packet)
    agent_action = _packet_agent_action(packet)
    lines = [
        f"### Q{number}. {title}",
        "",
        f"- Problem packet: `{len(packet.issues)}` related blockers were grouped because they belong to the same decision.",
        f"- In plain language: {_packet_plain_language(packet, target, compile_backend)}",
        "- Materials checked: source material, current contract, generated proof files, explicit user inputs, previous extracted model information in this active run, and selected backend context.",
        "- Missing or conflicting information: the grouped blocker fields below still cannot be resolved from checked evidence.",
        "- Why this blocks: resolving this packet differently could change physics intent, generated code behavior, backend implementation, or numerical interpretation.",
        f"- Edit targets: {edit_targets}",
        f"- Accepted format: {_packet_format(packet, target)}",
        f"- Ask the user: {_packet_prompt(packet, target)}",
        f"- My current thought (reviewable): {_packet_recommendation(packet, target, memory)}",
        f"- The agent should handle first: {agent_action}",
        f"- If you accept my recommendation: the agent will {agent_action} Then it will patch the Markdown, rerun `resolve`, and ask the next packet only if blockers remain.",
    ]
    notes = _packet_notes(packet, target)
    if notes:
        lines.append(f"- Notes: {notes}")
    lines.extend(["", "Underlying blockers:"])
    lines.extend(underlying)
    evidence = _evidence_lines(issue.field_key, target, memory)
    if evidence:
        lines.extend(["", "Evidence hints:"])
        lines.extend(evidence)
    lines.extend(
        [
            "",
            "Answer slot:",
            "",
            "```text",
            f"Q{number}: ",
            "```",
        ]
    )
    return lines


def _always_render_as_packet(key: str) -> bool:
    return key in {
        "model_card.physics_conventions",
        "implementation.phase_filter",
        "implementation.symmetry",
        "implementation.counterterms",
        "implementation.goldstone",
        "implementation.daisy",
    }


def _question_packet_key(issue: ValidationIssue, contract: dict[str, Any]) -> str:
    field_key = issue.field_key
    if issue.code == "unknown_symbol":
        return "symbols.unknown"
    if field_key == "review.approved":
        return field_key
    if field_key == "model_card.model_short_name":
        return field_key
    if field_key.startswith("model_card."):
        return "model_card.physics_conventions"
    if field_key.startswith("parameters.public_inputs"):
        return "parameters.public_inputs"
    if field_key.startswith("parameters.radiation_dof"):
        return "parameters.radiation_dof"
    if field_key.startswith("parameters.constants"):
        row = _row_for_field_key(field_key, contract)
        row_name = str(row.get("name", "")).strip()
        if row_name in {"num_boson_dof", "num_fermion_dof"}:
            return "parameters.radiation_dof"
        match = re.match(r"parameters\.constants\[(\d+)\]", field_key)
        return f"parameters.constants[{match.group(1)}]" if match else field_key
    if field_key.startswith("parameters.derived"):
        match = re.match(r"parameters\.derived\[(\d+)\]", field_key)
        return f"parameters.derived[{match.group(1)}]" if match else field_key
    if field_key.startswith("masses."):
        match = re.match(r"masses\.(bosons|fermions|boson_matrices)\[(\d+)\]", field_key)
        return f"masses.{match.group(1)}[{match.group(2)}]" if match else field_key
    if field_key.startswith("loops."):
        return "loops.potential_assembly"
    if field_key.startswith("implementation.phase_filter"):
        return "implementation.phase_filter"
    if field_key.startswith("implementation.symmetry"):
        return "implementation.symmetry"
    if field_key.startswith("implementation.counterterms") or field_key.startswith("potential.pieces.V_CT"):
        return "implementation.counterterms"
    if field_key.startswith("implementation.goldstone"):
        return "implementation.goldstone"
    if field_key.startswith("implementation.daisy"):
        return "implementation.daisy"
    return field_key or issue.code or "unknown"


def _packet_title(packet: QuestionPacket, target: dict[str, str]) -> str:
    titles = {
        "symbols.unknown": "Unknown symbols",
        "model_card.physics_conventions": "Model Card physics choices",
        "parameters.public_inputs": "Public input basis and test values",
        "parameters.radiation_dof": "Radiation d.o.f. totals",
        "loops.potential_assembly": "Potential assembly route",
        "implementation.phase_filter": "CosmoTransitions phase filtering",
        "implementation.symmetry": "PhaseTracer symmetry handling",
        "implementation.counterterms": "Counterterm route",
        "implementation.goldstone": "Goldstone handling",
        "implementation.daisy": "Daisy / thermal-mass handling",
    }
    return titles.get(packet.key, target["title"])


def _packet_edit_targets(packet: QuestionPacket, contract: dict[str, Any]) -> str:
    targets: list[str] = []
    for issue in packet.issues:
        target = _question_target(issue.field_key, contract)
        edit_target = target["edit_target"]
        if edit_target not in targets:
            targets.append(edit_target)
    if len(targets) <= 2:
        return "; ".join(targets)
    return "; ".join(targets[:2]) + f"; plus {len(targets) - 2} related cells listed below"


def _packet_format(packet: QuestionPacket, target: dict[str, str]) -> str:
    if packet.key == "parameters.public_inputs":
        return "One compact list: `name -> public true/false, test_value=<number>`; use `move_to=constant/derived/remove` for non-inputs."
    if packet.key == "model_card.physics_conventions":
        return "`key=value` lines for the listed Model Card rows; use the allowed choices shown in the template."
    if packet.key == "implementation.phase_filter":
        return "`mode=none` or `mode=negative_field_threshold; rule: field < threshold, phase_type=..., reason=...`."
    if packet.key == "implementation.symmetry":
        return "`mode=none` or `mode=z2_reflection; rules: fields=s, transformation=sign_flip, reason=...`."
    if packet.key == "symbols.unknown":
        return "`symbol -> field/public_input/constant/derived/typo`, with expression or correction when needed."
    if packet.key.startswith("masses."):
        return "One complete reviewed row: species/matrix name, field-dependent expression or entries, d.o.f., CW constant, and status."
    return target["format"]


def _packet_prompt(packet: QuestionPacket, target: dict[str, str]) -> str:
    if packet.key == "parameters.public_inputs":
        return "Please confirm the final public input list and give one numeric test_value for each public input. If a candidate is not a public input, say whether it should move to constants, derived parameters, or be removed."
    if packet.key == "model_card.physics_conventions":
        return "Please confirm the unresolved high-level physics choices as one set. If PTagent's current thought is correct, answer with the key=value lines to apply."
    if packet.key == "implementation.phase_filter":
        return "For CosmoTransitions, please confirm the forbidden-phase policy. My recommendation is `mode=none` unless the paper/reference code explicitly removes a duplicate or unphysical traced branch; if filtering is intended, give the complete rule in one answer: field, comparison, threshold, phase_type, and reason."
    if packet.key == "implementation.symmetry":
        return "For PhaseTracer, please confirm the apply_symmetry(phi) policy. My recommendation is `mode=none` unless you want PhaseTracer to merge reviewed symmetry-equivalent field points; if enabled, give the reviewed sign-flip field group(s) and reason."
    if packet.key == "symbols.unknown":
        return "Please classify these unknown symbols together, or point out typos in the compiler expressions."
    if packet.key.startswith("masses."):
        return "Please provide the complete reviewed mass row or matrix route for this species, instead of answering name, mass, d.o.f., and constants separately."
    if len(packet.issues) > 1:
        return f"Please answer these {len(packet.issues)} related fields together so the agent can patch them in one pass."
    return _question_prompt(packet.representative, target)


def _packet_plain_language(packet: QuestionPacket, target: dict[str, str], compile_backend: str) -> str:
    if packet.key == "parameters.public_inputs":
        return "The model needs a reviewed input basis and one numerical smoke-test point before generated code can be trusted."
    if packet.key == "model_card.physics_conventions":
        return "These top-level choices control later formula routes, so resolving them together avoids asking the user about downstream details too early."
    if packet.key == "implementation.phase_filter":
        return "The phase-filter mode and its concrete rule are the same CosmoTransitions decision. It affects which traced phases enter transition finding, so even the default `none` policy must be confirmed by the user."
    if packet.key == "implementation.symmetry":
        return "PhaseTracer symmetry mode and sign-flip rows describe one equivalence rule. It affects duplicate phase counting through apply_symmetry(phi) so symmetry-equivalent points are not counted as distinct phases; even the default `none` policy must be confirmed by the user."
    if packet.key.startswith("masses."):
        return "A mass row is useful only when its formula, d.o.f., and loop metadata are consistent as one reviewed object."
    return _plain_language_explanation(packet.representative, target, compile_backend)


def _packet_recommendation(packet: QuestionPacket, target: dict[str, str], memory: PaperMemory | None) -> str:
    if packet.key == "parameters.public_inputs":
        return "ask this as one mandatory input-basis gate; fixed conventions should be moved out before bothering the user."
    if packet.key == "model_card.physics_conventions":
        return "auto-fill choices that are explicitly stated in the source or reference code, then ask only for rows that remain ambiguous or mutually inconsistent."
    if packet.key == "implementation.phase_filter":
        return "recommend `mode=none` unless the paper/reference code explicitly removes a duplicate or unphysical traced branch. For reviewed xSM-like mirror-branch removal, use `negative_field_threshold` with a tolerance-style threshold such as `field < -5.0`, not a zero threshold."
    if packet.key == "implementation.symmetry":
        return "recommend `mode=none` unless the user confirms PhaseTracer should merge reviewed symmetry-equivalent field points. Use `z2_reflection` only for an explicitly reviewed sign-flip invariance and list the field group(s)."
    if packet.key.startswith("masses."):
        return "The agent should fill source-exact mass expressions and standard CW constants itself, then ask only for conflicting or missing physics metadata."
    if packet.key == "symbols.unknown":
        return "classify obvious fixed constants and derived aliases locally from source context; ask the user only for genuinely ambiguous symbols."
    recommendations = [
        _recommendation_line(issue.field_key, _question_target(issue.field_key, {}), memory)
        for issue in packet.issues[:2]
    ]
    recommendations = [item for item in recommendations if item]
    return "; ".join(recommendations) if recommendations else _default_recommendation(packet.representative, target)


def _packet_agent_action(packet: QuestionPacket) -> str:
    if packet.key == "parameters.public_inputs":
        return "merge all public-input confirmations and test values into this one answer before patching the table."
    if packet.key == "model_card.physics_conventions":
        return "fill source-evident Model Card rows first; do not ask the user about rows already fixed by the paper or reference code."
    if packet.key == "implementation.phase_filter":
        return "present the recommendation and reason, then set the user-confirmed mode and any rule together; do not ask mode first and threshold second unless the rule remains genuinely unclear."
    if packet.key == "implementation.symmetry":
        return "present the recommendation and reason, then set the user-confirmed symmetry mode and sign-flip rules together."
    if packet.key.startswith("masses."):
        return "patch a complete species or matrix row when the source gives all entries; leave only missing/conflicting cells in the user answer."
    return "patch any field that is directly supported by source evidence, and keep only the uncertain part in this question packet."


def _packet_notes(packet: QuestionPacket, target: dict[str, str]) -> str:
    if packet.key == "implementation.phase_filter":
        return "This is a backend phase-tracing rule, not a potential term. It should not change Vtot, but it can remove phases from CosmoTransitions transition finding."
    if packet.key == "implementation.symmetry":
        return "This is a PhaseTracer phase-counting rule, not a potential term. It should not change V(phi,T), but it can merge symmetry-equivalent points."
    if packet.key == "parameters.public_inputs":
        return "This remains a hard user gate because it defines the build_model signature and smoke-test point."
    return target.get("notes", "")


def _packet_underlying_lines(packet: QuestionPacket) -> list[str]:
    lines: list[str] = []
    for issue in packet.issues[:8]:
        lines.append(f"- `{issue.field_key}`: `{issue.code}` - {issue.message}")
    if len(packet.issues) > 8:
        lines.append(f"- plus {len(packet.issues) - 8} more related blockers in this packet.")
    return lines


def _issue_sort_key(issue: ValidationIssue) -> tuple[int, str]:
    if issue.field_key == "review.approved":
        return (99, issue.field_key)
    if issue.field_key == "model_card.model_short_name":
        return (0, issue.field_key)
    if issue.field_key.startswith("model_card."):
        return (1, issue.field_key)
    if issue.field_key.startswith("implementation.phase_filter") or issue.field_key.startswith("implementation.symmetry"):
        return (4, issue.field_key)
    if issue.field_key.startswith("parameters.public_inputs") and issue.field_key.endswith(".confirmed"):
        return (5, issue.field_key)
    if issue.field_key.startswith("parameters.public_inputs") and (
        issue.field_key.endswith(".test_value") or issue.field_key.endswith(".default")
    ):
        return (6, issue.field_key)
    if issue.field_key.startswith("parameters.public_inputs"):
        return (7, issue.field_key)
    if issue.field_key.startswith("parameters."):
        return (15, issue.field_key)
    if issue.field_key.startswith("potential."):
        return (20, issue.field_key)
    if issue.field_key.startswith("masses."):
        return (30, issue.field_key)
    if issue.field_key.startswith("loops."):
        return (40, issue.field_key)
    if issue.field_key.startswith("implementation."):
        return (45, issue.field_key)
    return (50, issue.field_key)


def _question_target(field_key: str, contract: dict[str, Any]) -> dict[str, str]:
    row = _row_for_field_key(field_key, contract)
    row_name = str(row.get("name") or row.get("contribution") or row.get("key") or "").strip()
    base = {
        "title": field_key or "Template issue",
        "edit_target": f"`{field_key}` in `contract_template.md`",
        "format": "Fill the referenced Markdown cell or fenced Python block.",
        "notes": "",
        "symbol": row_name,
    }

    if field_key == "review.approved":
        return {
            **base,
            "title": "Final human approval",
            "edit_target": "Section 8 `Approval`, row `approved`.",
            "format": "`true` only after every physics/formula question is resolved and the user explicitly approves code generation.",
            "notes": "Do this last; it is intentionally a blocker.",
        }
    if field_key.startswith("model_card."):
        key = field_key.split(".", 1)[1]
        if key == "model_short_name":
            return {
                **base,
                "title": "Model short name",
                "edit_target": "Section 1 `Model Card`, row `model_short_name`.",
                "format": "A short HEP model name such as `XSM`, `2HDM`, `IDM`, or `model` only when no common short name exists.",
                "notes": "This names generated artifacts under `generated_models/`; it is model metadata, not a backend choice.",
                "symbol": key,
            }
        return {
            **base,
            "title": f"Model Card choice `{key}`",
            "edit_target": f"Section 1 `Model Card`, row `{key}`.",
            "format": "Use one of the listed choices, or a short reviewed convention such as `Landau`, `removed`, or `not_applicable`.",
            "notes": "This row controls whether ambiguous physics choices are allowed to reach code generation.",
            "symbol": key,
        }
    if field_key.startswith("parameters.public_inputs"):
        if field_key.endswith(".confirmed"):
            return {
                **base,
                "title": f"Confirm public input `{row_name or 'input'}`",
                "edit_target": f"Section 3 `Public Inputs`, row `{row_name or '?'}`, column `confirmed`.",
                "format": "`true` only if this is really a public build/scan input; otherwise remove the row or move it to Constants/Derived.",
                "notes": "This is the first mandatory decision: confirm the input basis before assigning test values or generating code.",
            }
        return {
            **base,
            "title": f"Public input test value for `{row_name or 'input'}`",
            "edit_target": f"Section 3 `Public Inputs`, row `{row_name or '?'}`, column `test_value`.",
            "format": "Numeric literal such as `125.0`, `0.1`, or `1e-2`.",
            "notes": "This value becomes the generated build_model default and is used by smoke tests. If this is not a scan/build input, move it to Constants/Derived or remove it.",
        }
    if field_key.startswith("parameters.constants"):
        if row_name in {"num_boson_dof", "num_fermion_dof"}:
            return {
                **base,
                "title": f"Radiation d.o.f. `{row_name}`",
                "edit_target": f"Section 3 `Fixed Constants`, row `{row_name}`, column `value`.",
                "format": "Numeric total radiation d.o.f. used by standard thermal integrals.",
                "notes": "Ask the user to confirm the total count. Use SM baseline `num_boson_dof=28`, `num_fermion_dof=90`; add reviewed BSM d.o.f. beyond the one SM Higgs scalar already in the baseline, and state what was added.",
                "symbol": row_name,
            }
        return {
            **base,
            "title": f"Constant value for `{row_name or 'constant'}`",
            "edit_target": f"Section 3 `Fixed Constants`, row `{row_name or '?'}`, column `value`.",
            "format": "Numeric literal.",
        }
    if field_key.startswith("parameters.derived"):
        if field_key.endswith("_status"):
            return {
                **base,
                "title": f"Formula sync review for `{row_name or 'derived parameter'}`",
                "edit_target": f"Section 3 `Derived Quantities`, row `{row_name or '?'}`, column `expr_status`.",
                "format": "`agent_reviewed` only after the reviewed formula and inline-code `expr` have been compared and found equivalent.",
                "notes": "If the Python expression disagrees with the reviewed formula, fix `expr` first and leave this unresolved until it is checked.",
            }
        return {
            **base,
            "title": f"Derived expression for `{row_name or 'derived parameter'}`",
            "edit_target": f"Section 3 `Derived Quantities`, row `{row_name or '?'}`, column `expr`.",
            "format": "Python expression using earlier inputs/constants/derived names.",
        }
    if field_key.startswith("parameters.radiation_dof"):
        name = field_key.rsplit(".", 1)[-1]
        return {
            **base,
            "title": f"Radiation d.o.f. `{name}`",
            "edit_target": "Section 3 `Fixed Constants` or `Derived Quantities`, rows `num_boson_dof` and `num_fermion_dof`.",
            "format": "Numeric total radiation d.o.f. used by standard thermal integrals.",
            "notes": "These are total thermal-radiation d.o.f.; backend V1T implementations subtract explicit species internally. Use SM baseline `num_boson_dof=28`, `num_fermion_dof=90`; add reviewed BSM d.o.f. beyond the one SM Higgs scalar already in the baseline and tell the user what was added.",
            "symbol": name,
        }
    if field_key == "potential.V0.python":
        return {
            **base,
            "title": "Tree-level potential `V0`",
            "edit_target": "Section 4 `Potential Part: V0`, fenced `Compiler expression` block.",
            "format": "Python expression using declared fields and parameters, for example `0.5*mu2*h**2 + 0.25*lam*h**4`.",
            "notes": "Use the full field-dependent tree potential, not a vacuum-only relation.",
        }
    if field_key.startswith("masses.bosons"):
        return _species_target(base, field_key, contract, table="Bosons")
    if field_key.startswith("masses.fermions"):
        return _species_target(base, field_key, contract, table="Fermions")
    if field_key.startswith("masses.boson_matrices"):
        return {
            **base,
            "title": "Boson mass matrix entry",
            "edit_target": "Section 5 `Boson Mass Matrices`.",
            "format": "Python expression for each matrix entry and a numeric `dof_per_eigenvalue`.",
            "notes": "This route is enabled by default. Use it for mixed field-dependent spectra; disable it only when the paper gives already-reviewed diagonal field-dependent eigenvalues.",
        }
    if field_key.startswith("loops."):
        return {
            **base,
            "title": "Potential assembly mode",
            "edit_target": "Section 1 `Model Card`, route rows `zero_temperature`, `thermal`, and `daisy`.",
            "format": "`none`, an allowed standard loop mode, or `custom_expr` plus a Python expression.",
        }
    if field_key.startswith("implementation.counterterms"):
        return {
            **base,
            "title": "Counterterm implementation contract",
            "edit_target": "Section 6 `Counterterm Basis` and `Counterterm Conditions`.",
            "format": "Use Python-safe coefficient names, operator expressions, reviewed conditions, and a clear CW/Goldstone source.",
            "notes": "Only fill this when the paper has a standalone reviewed V_CT or explicit CT solving conditions.",
        }
    if field_key.startswith("implementation.goldstone"):
        if ".species" in field_key:
            return {
                **base,
                "title": "Goldstone species row",
                "edit_target": "Section 6 `Goldstone Species Handling`.",
                "format": "One row per Goldstone mode/group with mass_sq, d.o.f., CW/CT/thermal policies, regulator, and replacement expression.",
                "notes": "Use this table to make the abstract Goldstone policy concrete.",
            }
        return {
            **base,
            "title": "Goldstone handling contract",
            "edit_target": "Section 6 `Goldstone Handling`.",
            "format": "Choose included/excluded/regulate/not_applicable policies and give the replacement expression when required.",
            "notes": "This prevents double-counting Goldstone contributions in V_CW and CT finite differences.",
        }
    if field_key.startswith("implementation.daisy"):
        if ".particles" in field_key:
            return {
                **base,
                "title": "Daisy particle term",
                "edit_target": "Section 6 `Daisy Particle Terms`.",
                "format": "One row per affected bosonic mode/group with zeroT_mass_sq, full thermal_mass_sq, d.o.f., longitudinal_only, and Daisy contribution.",
                "notes": "Parwani rows document thermal-mass replacement. Arnold-Espinosa rows document the explicit subtraction term. If the source gives only Delta Pi/additional pieces, assemble the total resummed mass before review and explain that derivation in the thermal-mass notes.",
            }
        if field_key.endswith(".cubic_power_policy"):
            return {
                **base,
                "title": "Arnold-Espinosa Daisy cubic-power convention",
                "edit_target": "Section 6 `Daisy / Thermal-Mass Handling`, row `cubic_power_policy`.",
                "format": "A reviewed convention such as `positive_part`, `signed_abs`, `regulated_abs`, or an explicit paper formula.",
                "notes": "The expression `(m^2)^(3/2)` is ambiguous when `m^2<0`; the compiler expression for `V_daisy` must implement the reviewed convention, for example with `np.where`, `np.maximum`, `np.sign`, and `np.abs`.",
            }
        return {
            **base,
            "title": "Daisy / thermal-mass contract",
            "edit_target": "Section 6 `Daisy / Thermal-Mass Handling`.",
            "format": "Choose none, Parwani, Arnold-Espinosa, or custom_expr; then state the matching implementation route, full thermal masses, gauge neutral-mode policy, and double-counting guard.",
            "notes": "Parwani uses reviewed lowest-order thermal masses in the one-loop finite-temperature route and no separate V_daisy. Arnold-Espinosa keeps ordinary masses in V1/V1T and adds one explicit V_daisy term. Use direct thermal eigenvalues when the paper gives them; diagonalize only when the paper gives a matrix. Do not mark Delta Pi/additional self-energies as complete thermal masses. Add derivation notes stating direct total vs assembled total, branch choices, and source context.",
        }
    if field_key.startswith("implementation.phase_filter"):
        if ".rules" in field_key:
            return {
                **base,
                "title": "CosmoTransitions phase filter rule",
                "edit_target": "Section 6 `CosmoTransitions Phase Filter Rules`.",
                "format": "One row per discarded phase branch: `field`, comparison `<`/`<=`/`>`/`>=`, numeric `threshold`, `phase_type`, and reason.",
                "notes": "The CosmoTransitions phase-filter hook discards a traced phase when it returns true. xSM-style example: discard negative mirror branches with `h < -5.0` and/or `s < -5.0`, not `h < 0`, so phases at zero survive floating-point noise.",
            }
        return {
            **base,
            "title": "CosmoTransitions phase filtering policy",
            "edit_target": "Section 6 `CosmoTransitions Phase Filtering`.",
            "format": "`none`, `negative_field_threshold`, or `custom_expr`; if filtering is enabled, fill the rule rows with phase type and threshold.",
            "notes": "This affects CosmoTransitions phase tracing only; it does not change the effective potential. PhaseTracer does not consume this section. For PhaseTracer symmetry-equivalent field points, use the PhaseTracer Symmetry section instead of a phase filter. Custom expressions may use natural Python `and`/`or`; the compiler renders them as backend-safe boolean logic. xSM.py-style hint for CosmoTransitions: `h < -5.0` and/or `s < -5.0` discards negative mirror branches while preserving phases at zero.",
        }
    if field_key.startswith("implementation.symmetry"):
        if ".rules" in field_key:
            return {
                **base,
                "title": "PhaseTracer symmetry rule",
                "edit_target": "Section 6 `PhaseTracer Symmetry Rules`.",
                "format": "One row per Z2 reflection: comma-separated `fields`, `transformation=sign_flip`, and a short reason/source.",
                "notes": "PhaseTracer uses `apply_symmetry(phi)` to identify symmetry-equivalent field points and avoid duplicate phase counting. Example: `s` means `s -> -s`; `h,s` means the simultaneous operation `(h,s) -> (-h,-s)`.",
            }
        return {
            **base,
            "title": "PhaseTracer symmetry policy",
            "edit_target": "Section 6 `PhaseTracer Symmetry`, row `mode`, plus `PhaseTracer Symmetry Rules` when enabled.",
            "format": "`none` or `z2_reflection`. Use `z2_reflection` only with reviewed sign-flip symmetry rows.",
            "notes": "This is PhaseTracer-specific. It is not the same as CosmoTransitions phase filtering: symmetry identifies equivalent field points through `apply_symmetry(phi)` so they are not counted as distinct phases; filtering forbids branches.",
        }
    if field_key.startswith("potential.pieces.V_CT"):
        return {
            **base,
            "title": "Counterterm potential `V_CT`",
            "edit_target": "Section 4 `Potential Part: V_CT`, fenced `Compiler expression` block.",
            "format": "Python expression using declared fields and parameters, or set Model Card `counterterm` to `none`/`implicit_in_V1`.",
        }
    if issue_symbol := _unknown_symbol_from_message(str(field_key), str(contract)):
        return {**base, "symbol": issue_symbol}
    return base


def _species_target(base: dict[str, str], field_key: str, contract: dict[str, Any], *, table: str) -> dict[str, str]:
    row = _row_for_field_key(field_key, contract)
    name = str(row.get("name", "")).strip() or "species"
    if field_key.endswith("_status"):
        fmt = "`agent_reviewed` only after the reviewed mass formula and inline-code `mass_sq` expression have been compared and found equivalent."
        notes = "If the Python expression disagrees with the reviewed formula, fix `mass_sq` first and leave this unresolved until it is checked."
    elif field_key.endswith(".name"):
        fmt = "Short Python-safe species name, e.g. `h_scalar`, `W_longitudinal`."
        notes = "If the paper gives a mass matrix instead, disable this direct row and fill Section 5 `Boson Mass Matrices`."
    elif field_key.endswith(".mass_sq"):
        fmt = "Field-dependent mass squared Python expression."
        notes = "If the paper gives a mass matrix instead, disable this direct row and fill Section 5 `Boson Mass Matrices`."
    elif field_key.endswith(".dof"):
        fmt = "Numeric degree of freedom, e.g. `1`, `2`, `3`, `6`, `12`."
        notes = "If the paper gives a mass matrix instead, disable this direct row and fill Section 5 `Boson Mass Matrices`."
    elif field_key.endswith(".c"):
        fmt = "Coleman-Weinberg constant, commonly `1.5` for scalars/fermions and `0.8333333333333334` for gauge bosons."
        notes = "If the paper gives a mass matrix instead, disable this direct row and fill Section 5 `Boson Mass Matrices`."
    else:
        fmt = "Fill the referenced row cell."
        notes = "If the paper gives a mass matrix instead, disable this direct row and fill Section 5 `Boson Mass Matrices`."
    return {
        **base,
        "title": f"{table[:-1]} species `{name}`",
        "edit_target": f"Section 5 `{table}`, row `{name}`, column from `{field_key}`.",
        "format": fmt,
        "notes": notes,
    }


def _question_prompt(issue: ValidationIssue, target: dict[str, str]) -> str:
    symbol = target.get("symbol", "")
    if issue.code == "mass_role_conflict":
        return "Is this a reviewed field-dependent mass matrix for the effective potential? If it is vacuum-only or phenomenological, move it to notes/parameter solving and provide the correct field-dependent spectrum."
    if issue.code == "unknown_symbol":
        unknown = _unknown_symbol_from_message(issue.message, "")
        return (
            f"What is `{unknown}`? Declare it as a field, public input, constant, or derived expression, "
            "or correct the expression if it is a typo."
        )
    if issue.code == "formula_sync_unreviewed":
        return "Please compare the rendered formula with the inline-code Python expression. If equivalent, set the status cell to `reviewed`; if not, correct the Python expression first."
    if target["title"].startswith("Confirm public input"):
        return f"Should `{symbol}` be one of the public input parameters? Answer true, or say where it should move."
    if target["title"].startswith("Public input test value"):
        return f"What numeric benchmark/scan point should `{symbol}` use for build_model and smoke tests?"
    if target["title"].startswith("Tree-level potential"):
        return "Please give the tree-level potential as a Python expression using the declared field names."
    if target["title"].startswith("Radiation"):
        return "What total radiation degrees of freedom should V1T use? State the SM baseline and any extra model particles added."
    if target["title"].startswith("Boson mass matrix"):
        return "Please confirm this matrix route, give the missing field-dependent matrix entries, and state the d.o.f. per eigenvalue."
    if target["title"].startswith("Model Card"):
        return f"What is the reviewed choice for `{symbol}` in this paper?"
    if target["title"].startswith("Model short name"):
        return "What short model name should generated files use? If the paper has multiple model potentials, choose the specific branch first; if there is no common short name, answer `model`."
    if target["title"].startswith("Counterterm"):
        return "Does the paper use no CT, CT implicit in V1, an explicit linear CT solve, or a custom V_CT expression? Please give the needed rows."
    if target["title"].startswith("Goldstone"):
        return "How should Goldstones enter V_CW, CT-equation derivatives, and thermal terms? If Goldstones exist, list each mode/group explicitly."
    if target["title"].startswith("Daisy"):
        return "Does the paper use Parwani thermal-mass replacement, Arnold-Espinosa explicit V_daisy, no Daisy resummation, or a custom prescription? List every affected particle/mode."
    if target["title"].startswith("Arnold-Espinosa"):
        return "How should `(m^2)^(3/2)` be evaluated in V_daisy when a mass squared becomes negative? Please give the convention and make the V_daisy expression implement it."
    if target["title"].startswith("CosmoTransitions phase filtering"):
        return "Should the selected backend discard any traced phase branches? If yes, which field direction/type should be removed and what tolerance threshold should be used?"
    if target["title"].startswith("CosmoTransitions phase filter rule"):
        return "Which phase branch should be forbidden, and at what field threshold? For CosmoTransitions xSM-like Z2 duplicates, a typical answer is `h < -5.0` and/or `s < -5.0` for the negative branch."
    if target["title"].startswith("PhaseTracer symmetry policy"):
        return "Should PhaseTracer identify symmetry-equivalent field points with `apply_symmetry(phi)` to avoid duplicate phase counting? If yes, which field sign flips are reviewed symmetries?"
    if target["title"].startswith("PhaseTracer symmetry rule"):
        return "Which field or simultaneous fields should PhaseTracer flip as a Z2 symmetry, and what is the source or physics reason?"
    if "species" in target["title"].lower():
        return "Please provide the species name, field-dependent mass squared, d.o.f., and CW constant, or say to use a mass matrix instead."
    if target["title"].startswith("Final"):
        return "After reviewing the rendered Markdown, do you explicitly approve generating backend code? If no backend has been selected yet, choose `cosmotransitions` or `phasetracer` before compile."
    return "Please provide the missing value in the accepted format."


def _recommendation_line(field_key: str, target: dict[str, str], memory: PaperMemory | None) -> str:
    title = target.get("title", "")
    if title.startswith("Confirm public input"):
        symbol = target.get("symbol", "input")
        return f"treat `{symbol}` as public only if it is a user scan/build input; otherwise move it to constants or derived quantities."
    if title.startswith("Model short name"):
        return "use the paper's common model acronym if available, such as `XSM` or `2HDM`; if several potentials are present, ask for the branch before asking branch-specific parameters."
    if title.startswith("Radiation"):
        return "start from the SM thermal-integral baseline `num_boson_dof=28`, `num_fermion_dof=90`, then add only reviewed extra model degrees of freedom."
    if title.startswith("Boson mass matrix"):
        return "use the matrix route when the source or Hessian has mixing/off-diagonal entries; diagonalize direct eigenvalues only after they are explicitly reviewed."
    if title.startswith("Counterterm"):
        return "if CT coefficients are fixed by tadpole/Hessian conditions, generate an explicit linear solve for the coefficients instead of asking the user to input them."
    if title.startswith("Goldstone"):
        return "list Goldstone mode groups explicitly and keep CW, CT-source, thermal, regulator, and replacement policies separate."
    if title.startswith("Daisy"):
        return _daisy_recommendation(memory)
    if title.startswith("Arnold-Espinosa"):
        return "ask for the cubic-power convention explicitly; use `positive_part` only when the user/source confirms `(max(m^2, 0))^(3/2)`."
    if title.startswith("CosmoTransitions phase filtering"):
        return "keep this as a phase-tracing rule, not a potential term; use a tolerance-style threshold only when the user/source identifies the branch to discard."
    if title.startswith("PhaseTracer symmetry"):
        return "prefer `z2_reflection` when the potential is invariant under field sign flips such as xSM-like `s -> -s`; otherwise use `none` rather than filtering phases."
    return ""


def _plain_language_explanation(issue: ValidationIssue, target: dict[str, str], compile_backend: str) -> str:
    title = target.get("title", "")
    backend_note = f" for `{compile_backend}`" if compile_backend else ""
    if title.startswith("Confirm public input"):
        return "We need to know whether this parameter is something the user will scan or pass to build_model, instead of a fixed convention hidden inside the model."
    if title.startswith("Public input test value"):
        return "The generated model needs one concrete number for smoke tests, even if the parameter will later be scanned."
    if title.startswith("Model Card"):
        return "This is a high-level physics convention choice; later detailed formula questions depend on it."
    if title.startswith("Model short name"):
        return "Generated files should be named by the reviewed physics model, not by the paper id or run id."
    if title.startswith("Tree-level potential"):
        return "The backend cannot build masses or evaluate phases until the field-dependent tree potential is written in executable Python syntax."
    if title.startswith("CosmoTransitions phase filtering"):
        return "CosmoTransitions needs to know whether any branch should be forbidden during tracing; this is not a potential term."
    if title.startswith("PhaseTracer symmetry"):
        return "PhaseTracer handles mirror/equivalent branches by recognizing them through `apply_symmetry(phi)`, so we need the reviewed field sign-flip symmetry instead of a CosmoTransitions-style phase filter."
    if title.startswith("Daisy"):
        return "The finite-temperature resummation choice changes which masses enter V1, V1T, and any explicit ring term."
    if title.startswith("Goldstone"):
        return "Goldstones can affect CW terms, counterterms, and thermal pieces differently, so the policy must be explicit before code generation."
    if title.startswith("Counterterm"):
        return "Counterterms either are absent, folded into the loop convention, or require their own reviewed equations; the compiler should not guess this."
    if "mass" in title.lower() or "species" in title.lower():
        return "The loop potential needs field-dependent masses and d.o.f.; vacuum-only mass relations are not enough."
    if title.startswith("Final"):
        return "All physics choices are filled, so this is the explicit permission gate before generating backend code."
    return f"This field is still unresolved{backend_note}, so code generation would require guessing."


def _default_recommendation(issue: ValidationIssue, target: dict[str, str]) -> str:
    title = target.get("title", "")
    if issue.code == "unknown_symbol":
        return "first decide whether the symbol is a field, public input, fixed constant, or derived quantity; do not patch the expression blindly."
    if issue.code == "placeholder_value":
        return "fill the smallest reviewed value that removes this blocker, and leave unrelated model details for later questions."
    if issue.code == "contract_conflict":
        return "resolve the higher-level convention first, then regenerate the guide before changing detailed rows."
    if title.startswith("Final"):
        return "approve only after the rendered Markdown has been reviewed with the selected backend in mind."
    return "make the conservative reviewed choice and keep uncertainty as `ASK_USER` rather than inventing a convention."


def _daisy_recommendation(memory: PaperMemory | None) -> str:
    text = _memory_search_text(memory).lower()
    if "parwani" in text and ("arnold" in text or "espinosa" in text or "former" in text):
        return "the paper likely distinguishes Parwani from Arnold-Espinosa; state the source hint and ask the user to confirm the exact route before compiling."
    if "parwani" in text:
        return "the source mentions Parwani; ask whether V1/V1T should use thermal-mass replacement and whether a separate V_daisy is absent."
    if "arnold" in text or "espinosa" in text or "ring" in text or "daisy" in text:
        return "the source mentions daisy/ring resummation; ask whether it is explicit Arnold-Espinosa V_daisy, Parwani replacement, or a paper-specific custom rule."
    return "ask for the route explicitly; Daisy thermal masses and V1T assembly differ between Parwani and Arnold-Espinosa."


def _memory_search_text(memory: PaperMemory | None) -> str:
    if memory is None:
        return ""
    parts: list[str] = []
    for record in memory.formula_registry:
        parts.extend([record.latex, record.context, record.raw_text])
    for item in memory.symbol_registry:
        parts.extend([item.symbol, item.role, *item.definitions])
    return "\n".join(part for part in parts if part)


def _row_for_field_key(field_key: str, contract: dict[str, Any]) -> dict[str, Any]:
    match = re.match(r"parameters\.(public_inputs|constants|derived)\[(\d+)\]", field_key)
    if match:
        section, index_text = match.groups()
        return _list(_dict(contract.get("parameters")).get(section))[int(index_text)] if int(index_text) < len(_list(_dict(contract.get("parameters")).get(section))) else {}
    match = re.match(r"masses\.(bosons|fermions|boson_matrices)\[(\d+)\]", field_key)
    if match:
        section, index_text = match.groups()
        return _list(_dict(contract.get("masses")).get(section))[int(index_text)] if int(index_text) < len(_list(_dict(contract.get("masses")).get(section))) else {}
    match = re.match(r"implementation\.(goldstone\.species|daisy\.particles)\[(\d+)\]", field_key)
    if match:
        section, index_text = match.groups()
        implementation = _dict(contract.get("implementation"))
        rows = _list(_dict(implementation.get("goldstone" if section.startswith("goldstone") else "daisy")).get("species" if section.startswith("goldstone") else "particles"))
        return rows[int(index_text)] if int(index_text) < len(rows) else {}
    return {}


def _evidence_lines(field_key: str, target: dict[str, str], memory: PaperMemory | None) -> list[str]:
    if memory is None:
        return []
    if field_key == "review.approved":
        return []
    formula_kinds = _formula_kinds_for_field(field_key)
    symbol = target.get("symbol", "")
    formulas = [
        record
        for record in memory.formula_registry
        if (not formula_kinds or record.kind in formula_kinds)
        and (not symbol or _formula_mentions_symbol(record, symbol))
    ]
    if symbol:
        matching_symbols = [
            item
            for item in memory.symbol_registry
            if item.symbol == symbol or symbol in item.definitions
        ][:3]
    else:
        matching_symbols = []
    lines: list[str] = []
    for item in matching_symbols:
        defs = "; ".join(item.definitions[:2])
        lines.append(f"- Symbol `{item.symbol}` role `{item.role}` from `{', '.join(item.source_ids[:3])}` {defs}".rstrip())
    for record in sorted(formulas, key=lambda item: item.confidence, reverse=True)[:3]:
        lines.append(_formula_line(record))
    return lines


def _formula_kinds_for_field(field_key: str) -> set[str]:
    if field_key.startswith("potential.V0"):
        return {"tree_potential", "effective_potential"}
    if field_key.startswith("masses."):
        return {"mass"}
    if field_key.startswith("loops.zero_temperature"):
        return {"zero_temp_loop"}
    if field_key.startswith("loops.thermal"):
        return {"thermal"}
    if field_key.startswith("loops.daisy"):
        return {"daisy"}
    if field_key.startswith("implementation.goldstone") or field_key.startswith("implementation.counterterms"):
        return {"zero_temp_loop", "mass"}
    if field_key.startswith("implementation.daisy"):
        return {"daisy", "thermal", "mass"}
    if field_key.startswith("parameters."):
        return set()
    return set()


def _formula_mentions_symbol(record: FormulaRecord, symbol: str) -> bool:
    if not symbol:
        return True
    variants = {symbol, symbol.replace("_", ""), symbol.replace("_", r"\_")}
    text = f"{record.latex}\n{record.context}\n{record.raw_text}"
    return any(variant and variant in text for variant in variants)


def _formula_line(record: FormulaRecord) -> str:
    latex = re.sub(r"\s+", " ", record.latex).strip()
    if len(latex) > 260:
        latex = latex[:257] + "..."
    eq = f", Eq. {record.equation_number}" if record.equation_number else ""
    return f"- `{record.formula_id}` `{record.kind}` from `{record.source_id}`{eq}: `{latex}`"


def _unknown_symbol_from_message(message: str, _fallback: str) -> str:
    match = re.search(r"unknown symbol ['`]([^'`]+)['`]", message, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _default_memory_for_template(template_path: Path) -> PaperMemory | None:
    for candidate in (
        proof_materials_dir(template_path) / "paper_memory.json",
        template_path.parent / "paper_memory.json",
    ):
        if candidate.exists():
            return _load_memory(candidate)
    return None


def _load_memory(path: str | Path | None) -> PaperMemory | None:
    if not path:
        return None
    memory_path = Path(path).expanduser().resolve()
    if not memory_path.exists():
        return None
    return PaperMemory.from_dict(json.loads(memory_path.read_text(encoding="utf-8")))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
