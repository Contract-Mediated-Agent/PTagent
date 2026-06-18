from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifact_layout import proof_materials_dir
from .extraction_material import PotentialExtractionMaterial, render_potential_material_markdown
from .config import Settings
from .memory import build_memory_markdown
from .schemas import AgentRun, ModelIR, PaperMemory, PhaseHistoryResult, ValidationReport


def create_run_id(source_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", Path(source_name).stem.lower()).strip("_") or "run"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{slug}"


def prepare_run_dir(settings: Settings, run_id: str) -> Path:
    run_dir = settings.artifact_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def persist_review_artifacts(
    *,
    run_dir: Path,
    source_path: Path,
    memory: PaperMemory,
    material: PotentialExtractionMaterial | None = None,
    template_markdown: str,
    resolved_contract: dict[str, Any] | None = None,
    ir: ModelIR,
    validation: ValidationReport,
) -> AgentRun:
    proof_dir = proof_materials_dir(run_dir)
    proof_dir.mkdir(parents=True, exist_ok=True)
    memory_path = write_json(proof_dir / "paper_memory.json", memory.to_dict())
    (proof_dir / "paper_memory.md").write_text(build_memory_markdown(memory), encoding="utf-8")
    material_path = ""
    if material is not None:
        material_json_path = write_json(proof_dir / "potential_material.json", material.to_dict())
        (proof_dir / "potential_material.md").write_text(render_potential_material_markdown(material), encoding="utf-8")
        material_path = str(material_json_path.resolve())
    if memory.paper_markdown:
        (proof_dir / "paper.md").write_text(memory.paper_markdown, encoding="utf-8")
    template_path = run_dir / "contract_template.md"
    template_path.write_text(template_markdown, encoding="utf-8")
    contract_resolved_path = ""
    if resolved_contract is not None:
        contract_resolved_path = str(write_json(proof_dir / "contract_resolved.json", resolved_contract).resolve())
    ir_path = write_json(proof_dir / "model_ir.json", ir.to_dict())
    validation_path = write_json(proof_dir / "validation.json", validation.to_dict())
    return AgentRun(
        run_id=run_dir.name,
        source_path=str(source_path.resolve()),
        memory_path=str(memory_path.resolve()),
        material_path=material_path,
        template_path=str(template_path.resolve()),
        contract_resolved_path=contract_resolved_path,
        ir_path=str(ir_path.resolve()),
        validation_path=str(validation_path.resolve()),
    )


def persist_runtime_artifacts(
    *,
    run: AgentRun,
    model_path: Path | None = None,
    phase_history: PhaseHistoryResult | None = None,
) -> AgentRun:
    run_dir = Path(run.validation_path).parent if run.validation_path else proof_materials_dir(Path(run.template_path).parent)
    run_dir.mkdir(parents=True, exist_ok=True)
    if model_path is not None:
        run.generated_model_path = str(model_path.resolve())
    if phase_history is not None:
        history_path = write_json(run_dir / "phase_history.json", phase_history.to_dict())
        run.phase_history_path = str(history_path.resolve())
    report_path = run_dir / "report.md"
    report_path.write_text(render_report(run, phase_history), encoding="utf-8")
    run.report_path = str(report_path.resolve())
    write_json(run_dir / "agent_run.json", run.to_dict())
    return run


def render_report(
    run: AgentRun,
    phase_history: PhaseHistoryResult | None,
) -> str:
    lines = [
        f"# PTagent Run: {run.run_id}",
        "",
        "## Artifacts",
        "",
        f"- Source: `{run.source_path}`",
        f"- PaperMemory: `{run.memory_path}`",
        f"- Potential material: `{run.material_path}`",
        f"- Contract template: `{run.template_path}`",
        f"- ModelIR: `{run.ir_path}`",
        f"- Validation: `{run.validation_path}`",
        f"- Generated model: `{run.generated_model_path}`",
    ]
    if phase_history is not None:
        lines.extend(
            [
                "",
                "## Phase History",
                "",
                f"- Status: `{phase_history.status}`",
                f"- Tc list: `{phase_history.Tc_list}`",
                f"- Tn list: `{phase_history.Tn_list}`",
                f"- S3/T list: `{phase_history.action_over_T}`",
                f"- Labels: `{phase_history.path_labels}`",
                "",
                "```text",
                phase_history.summary,
                "```",
            ]
        )
    return "\n".join(lines)
