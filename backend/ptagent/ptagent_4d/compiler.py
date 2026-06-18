from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CompileResult:
    model_path: Path
    import_check_status: str
    warnings: list[str] = field(default_factory=list)
    usage_instructions: list[str] = field(default_factory=list)


class CompileBlocked(RuntimeError):
    """Raised when a contract template is not deterministic enough to compile."""


def compile_usage_instructions(model_path: Path, backend: str, *, phasetracer_root: str = "") -> list[str]:
    path = Path(model_path).resolve()
    backend_key = str(backend or "").strip().lower()
    if backend_key == "cosmotransitions":
        return [
            f"Generated Python model: {path}",
            f"Run directly to build the model and print Tc: python {path}",
            "Import usage: load the file as a Python module, then call build_model().",
            "Tc usage: build_model() runs calcTcTrans() by default, prints PTAGENT_TCTRANS lines, and stores the raw result on model.PTAGENT_TC_TRANS.",
            "Quiet usage: call build_model(run_tc=False, print_tc=False) when you only want the model object.",
        ]
    if backend_key == "phasetracer":
        project_dir = path.parent.resolve()
        root = str(phasetracer_root or "").strip() or "<configured-PhaseTracer-root>"
        return [
            f"Generated C++ header: {path}",
            f"Generated PhaseTracer project directory: {project_dir}",
            f"C++ usage: include {path.name}, then construct PTagentPhaseTracer::GeneratedPotential model(...);",
            f"Linux build: cd <linux-path-to-generated-project> && cmake -S . -B build -DPHASETRACER_ROOT={root}",
            "Potential smoke: cmake --build build --target run_model && ./build/run_model",
            "Tc smoke: cmake --build build --target run_model && ./build/run_model --transition",
        ]
    return [f"Generated model path: {path}"]
