from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from shlex import quote


@dataclass
class CompileResult:
    model_path: Path
    import_check_status: str
    warnings: list[str] = field(default_factory=list)
    usage_instructions: list[str] = field(default_factory=list)
    execution_report_path: Path | None = None
    execution_results: dict = field(default_factory=dict)


class CompileBlocked(RuntimeError):
    """Raised when a contract template is not deterministic enough to compile."""


def compile_usage_instructions(model_path: Path, backend: str, *, phasetracer_root: str = "") -> list[str]:
    path = Path(model_path).resolve()
    backend_key = str(backend or "").strip().lower()
    if backend_key == "cosmotransitions":
        return [
            f"Generated Python model: {path}",
            f"Standard phase-history entry: python <skill-dir>/scripts/ptagent_cli.py run --model {quote(str(path))} --stage critical --timeout 180",
            "For bubble nucleation use --stage nucleation; inspect the structured result and native diagnostics separately from critical-transition success.",
            f"Run directly to build the model and print Tc: python {path}",
            "Import usage: load the file as a Python module, then call build_model().",
            "Tc usage: build_model() runs calcTcTrans() by default, prints PTAGENT_TCTRANS lines, and stores the raw result on model.PTAGENT_TC_TRANS.",
            "Quiet usage: call build_model(run_tc=False, print_tc=False) when you only want the model object.",
            "Scan approval: confirm Tmax in contract section 11. Numerical Settings, set Tmax_approved=true and Tmax_approved_value=Tmax, then regenerate. Potential/smoke calls remain available without scan approval; x_eps is configurable in the same section.",
        ]
    if backend_key == "phasetracer":
        project_dir = path.parent.resolve()
        root = str(phasetracer_root or "").strip() or "<configured-PhaseTracer-root>"
        return [
            f"Generated C++ header: {path}",
            f"Generated PhaseTracer project directory: {project_dir}",
            f"C++ usage: include {path.name}, then construct PTagentPhaseTracer::GeneratedPotential model(...);",
            f"Standard compile/run entry: python <skill-dir>/scripts/compile_template.py --template <reviewed-contract.md> --backend phasetracer --phasetracer-root {quote(root)} --transition-smoke --smoke-timeout 180",
            "Set --smoke-timeout and --build-jobs as needed through that entry; retain its execution report. Do not create an alternative driver to bypass a missing workflow step.",
            f"Linux build: cd <linux-path-to-generated-project> && cmake -S . -B build -DPHASETRACER_ROOT={root}",
            "Potential smoke: cmake --build build --target run_model && ./build/run_model",
            "Generated native Tc/Tn executable (integration use): ./build/run_model --transition",
            "Scan approval: --transition requires user-confirmed Tmax in contract section 11. Numerical Settings (Tmax_approved=true, Tmax_approved_value=Tmax); regenerate after confirmation. Ordinary potential smoke needs no Tmax approval.",
        ]
    return [f"Generated model path: {path}"]
