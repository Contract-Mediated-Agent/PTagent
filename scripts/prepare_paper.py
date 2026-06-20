from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a local PTagent contract template and evidence packet."
    )
    parser.add_argument("--input", required=True, help="Attached/local PDF, TeX, Markdown, or arXiv source archive.")
    parser.add_argument(
        "--mode",
        choices=("fresh", "continue"),
        default="fresh",
        help=(
            "fresh: make a new source-grounded task; continue: document that "
            "the provided contract/task is the source of truth for local repair."
        ),
    )
    parser.add_argument("--run-dir", default="", help="Optional artifact output directory.")
    parser.add_argument("--project-root", default="", help="Optional PTagent repo root.")
    parser.add_argument(
        "--model",
        default="",
        help="Optional pre-contract model focus, e.g. XSM or 2HDM. If omitted and multiple models are detected, the script writes model_candidates.md and stops.",
    )
    args = parser.parse_args()

    from _bootstrap import ensure_ptagent_backend

    ensure_ptagent_backend(args.project_root)

    from ptagent import ModelSelectionRequired, PhaseTransitionAgent, get_settings
    from ptagent.ptagent_4d.artifact_layout import GLOBAL_INPUT_DIRNAME, generated_models_dir, proof_materials_dir
    from ptagent.ptagent_4d.extraction_material import render_potential_material_markdown
    from ptagent.ptagent_4d.template_contract import is_contract_template
    from ptagent.ptagent_4d.user_guidance import build_user_guidance_markdown

    source_path = Path(args.input).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else None
    if args.mode == "continue":
        template_text = source_path.read_text(encoding="utf-8", errors="replace")
        if not is_contract_template(template_text):
            raise ValueError("--mode continue requires --input to be an existing contract_template.md file.")
        run_dir = run_dir or source_path.parent
    agent = PhaseTransitionAgent(get_settings())
    try:
        bundle = agent.extract(source_path, run_dir=run_dir, model_focus=args.model)
    except ModelSelectionRequired as exc:
        proof_dir = proof_materials_dir(exc.run_dir)
        summary = {
            "workflow_mode": args.mode,
            "source_of_truth": _source_of_truth(args.mode),
            "status": "model_selection_required",
            "task_dir": str(exc.run_dir),
            "input_dir": str(exc.run_dir / GLOBAL_INPUT_DIRNAME),
            "proof_materials_dir": str(proof_dir),
            "user_questions": str(proof_dir / "user_questions.md"),
            "model_candidates": str(proof_dir / "model_candidates.md"),
            "model_candidates_json": str(proof_dir / "model_candidates.json"),
            "question": exc.result.question,
            "next_step": "Rerun with --model <selected short name>, for example --model XSM.",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    task_dir = Path(bundle.run.template_path).parent
    proof_dir = proof_materials_dir(task_dir)
    proof_dir.mkdir(parents=True, exist_ok=True)

    material_md_path = proof_dir / "potential_material_codex.md"
    material_md_path.write_text(render_potential_material_markdown(bundle.material), encoding="utf-8")

    packet_path = proof_dir / "codex_packet.md"
    packet_path.write_text(
        _packet_markdown(
            source_path=source_path,
            task_dir=task_dir,
            proof_dir=proof_dir,
            bundle=bundle,
            material_md_path=material_md_path,
            user_questions_path=proof_dir / "user_questions.md",
            workflow_mode=args.mode,
        ),
        encoding="utf-8",
    )
    user_questions_path = proof_dir / "user_questions.md"
    user_questions_path.write_text(
        build_user_guidance_markdown(
            template_markdown=bundle.template_markdown,
            validation=bundle.validation,
            template_path=bundle.run.template_path,
            memory=bundle.memory,
        ),
        encoding="utf-8",
    )

    summary = {
        "workflow_mode": args.mode,
        "source_of_truth": _source_of_truth(args.mode),
        "packet": str(packet_path),
        "user_questions": str(user_questions_path),
        "task_dir": str(task_dir),
        "input_dir": str(task_dir / GLOBAL_INPUT_DIRNAME),
        "proof_materials_dir": str(proof_dir),
        "generated_models_dir": str(generated_models_dir(task_dir)),
        "contract_template": str(bundle.run.template_path),
        "contract_resolved": str(bundle.run.contract_resolved_path),
        "potential_material": str(bundle.run.material_path),
        "potential_material_markdown": str(material_md_path),
        "model_ir": str(bundle.run.ir_path),
        "validation": str(bundle.run.validation_path),
        "blocking_issue_count": len([issue for issue in bundle.validation.issues if issue.severity == "error"]),
        "ready_for_compile": bundle.validation.ready_for_compile,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _packet_markdown(
    *,
    source_path: Path,
    task_dir: Path,
    proof_dir: Path,
    bundle: Any,
    material_md_path: Path,
    user_questions_path: Path,
    workflow_mode: str,
) -> str:
    validation_issues = bundle.validation.to_dict().get("issues", [])
    blocking_issues = [item for item in validation_issues if item.get("severity") == "error"]
    warning_issues = [item for item in validation_issues if item.get("severity") == "warning"]
    material = bundle.material
    lines: list[str] = [
        "# PTagent Agent Packet",
        "",
        f"- Workflow mode: `{workflow_mode}`",
        f"- Source of truth: `{_source_of_truth(workflow_mode)}`",
        f"- Source: `{source_path}`",
        f"- Task dir: `{task_dir}`",
        f"- Input dir: `{task_dir / 'input'}`",
        f"- Proof materials dir: `{proof_dir}`",
        f"- Contract template: `{bundle.run.template_path}`",
        f"- Derived contract JSON: `{bundle.run.contract_resolved_path}`",
        f"- Potential material JSON: `{bundle.run.material_path}`",
        f"- Potential material Markdown: `{material_md_path}`",
        f"- User question guide: `{user_questions_path}`",
        f"- ModelIR: `{bundle.run.ir_path}`",
        f"- Validation: `{bundle.run.validation_path}`",
        f"- Ready for local template compile: `{bundle.validation.ready_for_compile}`",
        "",
        "## Model",
        "",
        f"- Paper/model id: `{bundle.ir.paper_id or bundle.ir.model_name}`",
        f"- Model name: `{bundle.ir.model_name}`",
        f"- Implementation mode: `{bundle.ir.implementation_mode}`",
        "",
        "## User Questions To Resolve First",
        "",
    ]
    if blocking_issues:
        for item in blocking_issues[:20]:
            lines.append(
                f"- `{item.get('field_key', '')}`: {item.get('code')} - {item.get('message')}"
            )
    else:
        lines.append("- No blocking template questions were detected. Compile only after physics review.")

    lines.extend(["", "## Public Inputs From Contract View", ""])
    graph = material.canonical_parameter_graph or {}
    public_inputs = graph.get("public_inputs", []) or []
    if public_inputs:
        for row in public_inputs:
            lines.append(
                f"- `{row.get('program_symbol') or row.get('symbol')}`: value/range `{row.get('value', '')}`; notes `{row.get('notes', '')}`"
            )
    else:
        lines.append("- No public input graph found. Inspect `contract_template.md` and ask the user.")

    lines.extend(["", "## Mass Blocks", ""])
    for block in material.mass_blocks[:12]:
        contract = block.get("mass_sector_contract") or {}
        lines.append(
            f"- `{block.get('id')}` `{block.get('category')}` role `{block.get('source_role')}` route `{block.get('implementation_kind')}`"
        )
        labels = contract.get("sector_labels") or []
        if labels:
            lines.append(f"  labels: `{', '.join(str(item) for item in labels)}`")
        auxiliaries = contract.get("auxiliary_definitions") or []
        for row in auxiliaries[:6]:
            lines.append(f"  aux: `{row.get('lhs')} = {row.get('rhs')}`")

    lines.extend(["", "## Daisy / Goldstone / Counterterm", ""])
    lines.append(f"- Daisy decisions: `{len(material.daisy_implementation_decisions)}`")
    for row in material.daisy_implementation_decisions[:8]:
        lines.append(f"  - `{row.get('sector')}`: `{row.get('route')}`")
    lines.append(f"- Goldstone contract: `{json.dumps(material.goldstone_contract, ensure_ascii=False)[:1200]}`")
    lines.append(f"- Counterterm notes: `{json.dumps(material.counterterm_notes, ensure_ascii=False)[:1200]}`")

    lines.extend(["", "## Validation Issues", ""])
    if not validation_issues:
        lines.append("- No validation issues.")
    else:
        for item in blocking_issues[:15]:
            lines.append(f"- ERROR `{item.get('code')}` `{item.get('field_key', '')}`: {item.get('message')}")
        for item in warning_issues[:10]:
            lines.append(f"- WARNING `{item.get('code')}` `{item.get('field_key', '')}`: {item.get('message')}")

    lines.extend(
        [
            "",
            "## Next Agent Steps",
            "",
            "1. Confirm the run mode. In `fresh`, do not copy old artifacts into the new contract; in `continue`, treat the named Markdown template as the source of truth.",
            "2. Read this packet and the fixed review worksheet `contract_template.md`.",
            "3. Read `user_questions.md` and ask the user concise physics/parameter questions if blockers remain.",
            "4. Edit only the human Markdown source `contract_template.md`; let PTagent and the agent maintain compiler expressions when needed.",
            "5. Do not edit `contract_resolved.json`; it is regenerated from the Markdown contract.",
            "6. When no blockers remain, stop and ask the user to review the rendered Markdown.",
            "7. Compile only after the user explicitly approves generating the model program.",
            "",
        ]
    )
    return "\n".join(lines)


def _source_of_truth(workflow_mode: str) -> str:
    if workflow_mode == "continue":
        return "existing contract_template.md plus current user corrections"
    return "current input material plus current user answers and explicitly supplied reference code"


if __name__ == "__main__":
    raise SystemExit(main())
