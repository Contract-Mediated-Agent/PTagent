from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .arxiv_source import is_arxiv_source_archive, read_arxiv_archive_source, read_arxiv_source
from .artifact_layout import GLOBAL_INPUT_DIRNAME
from .backends import COMPARE_BACKENDS_SENTINEL, SUPPORTED_COMPILE_BACKENDS, normalize_compile_backend
from .compiler import CompileBlocked, CompileResult
from .config import (
    ConfigurationError,
    Settings,
    get_settings,
    require_configured_phasetracer_root,
    require_configured_runtime_python,
)
from .extraction_material import PotentialExtractionMaterial, build_potential_material
from .memory import build_paper_memory
from .model_preflight import (
    ModelPreflightResult,
    ModelSelectionRequired,
    memory_with_model_focus,
    preflight_model_selection,
    write_model_preflight_artifacts,
)
from .reporting import create_run_id, persist_review_artifacts, persist_runtime_artifacts, prepare_run_dir
from .runner import run_phase_history
from .schemas import AgentRun, ModelIR, PaperMemory, PhaseHistoryResult, ValidationReport
from .source_reader import SourceDocument, read_source
from .template_contract import (
    build_contract_template,
    compile_contract_template,
    contract_to_model_ir,
    is_contract_template,
    parse_contract,
    resolved_contract_from_template,
    validate_contract_template,
)
from .user_guidance import backend_selection_question, build_question_packets, question_packet_to_dict

ProgressCallback = Callable[[str, float | None], None]


@dataclass
class ExtractionBundle:
    document: SourceDocument
    memory: PaperMemory
    model_preflight: ModelPreflightResult
    material: PotentialExtractionMaterial
    template_markdown: str
    ir: ModelIR
    validation: ValidationReport
    run: AgentRun


class PhaseTransitionAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(
        self,
        source_path: str | Path,
        *,
        run_id: str | None = None,
        run_dir: Path | None = None,
        model_focus: str = "",
        progress: ProgressCallback | None = None,
    ) -> ExtractionBundle:
        original_source_path = Path(source_path).expanduser().resolve()
        if run_dir is None:
            run_dir = prepare_run_dir(self.settings, run_id or create_run_id(original_source_path.name))
        else:
            run_dir = Path(run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)
        run_settings = _settings_with_run_input(self.settings, run_dir)
        _emit_progress(progress, "Reading uploaded source file", 0.10)
        if is_arxiv_source_archive(original_source_path):
            _emit_progress(progress, "Reading uploaded arXiv source archive", 0.12)
            document = read_arxiv_archive_source(original_source_path, run_settings, progress=progress)
        else:
            document = read_source(original_source_path, run_settings)
            document = _copy_document_input(document, original_source_path, run_dir)
        return self._extract_document(
            document,
            source_path=original_source_path,
            run_id=run_id or run_dir.name,
            run_dir=run_dir,
            model_focus=model_focus,
            progress=progress,
        )

    def extract_arxiv(
        self,
        arxiv_id: str,
        *,
        refresh: bool = False,
        model_focus: str = "",
        progress: ProgressCallback | None = None,
    ) -> ExtractionBundle:
        run_dir = prepare_run_dir(self.settings, create_run_id(arxiv_id))
        run_settings = _settings_with_run_input(self.settings, run_dir)
        document = read_arxiv_source(arxiv_id, run_settings, refresh=refresh, progress=progress)
        return self._extract_document(
            document,
            source_path=document.path,
            run_id=document.paper_id or create_run_id(document.path.name),
            run_dir=run_dir,
            model_focus=model_focus,
            progress=progress,
        )

    def _extract_document(
        self,
        document: SourceDocument,
        *,
        source_path: str | Path,
        run_id: str | None = None,
        run_dir: Path | None = None,
        model_focus: str = "",
        progress: ProgressCallback | None = None,
    ) -> ExtractionBundle:
        _emit_progress(progress, "Building PaperMemory, formula index, and evidence graph", 0.66)
        memory = build_paper_memory(document)
        _emit_progress(progress, "Checking whether the input is already a contract template", 0.70)
        template_markdown = _read_contract_template_if_present(Path(source_path))
        model_preflight = preflight_model_selection(memory, requested_model=model_focus) if not template_markdown else ModelPreflightResult(
            selected_model=None,
            candidates=(),
            needs_user_selection=False,
            reason="Existing contract template supplied; skipping pre-contract model selection.",
        )
        if model_preflight.needs_user_selection:
            if run_dir is None:
                run_dir = prepare_run_dir(self.settings, run_id or create_run_id(document.path.name))
            else:
                run_dir.mkdir(parents=True, exist_ok=True)
            write_model_preflight_artifacts(run_dir, model_preflight)
            raise ModelSelectionRequired(model_preflight, run_dir=run_dir, source_path=Path(source_path))
        memory = memory_with_model_focus(memory, model_preflight.selected_model)
        if not template_markdown:
            _emit_progress(progress, "Generating deterministic backend-neutral contract template", 0.76)
            template_markdown = build_contract_template(memory)

        _emit_progress(progress, "Validating contract template", 0.86)
        resolved_contract = resolved_contract_from_template(template_markdown)
        ir, validation = self.parse_contract_template(
            template_markdown,
            source_path=str(Path(source_path).resolve()),
        )
        _emit_progress(progress, "Preparing evidence material pack", 0.93)
        material = build_potential_material(
            memory,
            ir=ir,
            validation=validation,
            template_markdown=template_markdown,
        )
        if run_dir is None:
            run_dir = prepare_run_dir(self.settings, run_id or create_run_id(document.path.name))
        else:
            run_dir.mkdir(parents=True, exist_ok=True)
        _emit_progress(progress, "Saving memory, template, IR, material pack, and validation report", 0.96)
        run = persist_review_artifacts(
            run_dir=run_dir,
            source_path=document.path,
            memory=memory,
            material=material,
            template_markdown=template_markdown,
            resolved_contract=resolved_contract,
            ir=ir,
            validation=validation,
        )
        write_model_preflight_artifacts(run_dir, model_preflight)
        _emit_progress(progress, "Extraction pipeline complete", 1.0)
        return ExtractionBundle(
            document=document,
            memory=memory,
            model_preflight=model_preflight,
            material=material,
            template_markdown=template_markdown,
            ir=ir,
            validation=validation,
            run=run,
        )

    def parse_contract_template(
        self,
        template_markdown: str,
        *,
        source_path: str = "",
        persist_to: AgentRun | None = None,
        compile_backend: str | None = None,
    ) -> tuple[ModelIR, ValidationReport]:
        contract_validation = validate_contract_template(
            template_markdown,
            review_required=self.settings.review_required,
            compile_backend=compile_backend,
        )
        resolved_contract = contract_validation.contract
        ir = contract_to_model_ir(contract_validation.contract, source_path=source_path)
        validation = contract_validation.to_report()
        if persist_to and persist_to.validation_path:
            validation_dir = Path(persist_to.validation_path).parent
            run_dir = validation_dir.parent if validation_dir.name == "proof_materials" else validation_dir
            memory = PaperMemory(paper_id=ir.paper_id, source_path=source_path)
            persist_review_artifacts(
                run_dir=run_dir,
                source_path=Path(source_path or persist_to.source_path),
                memory=memory,
                material=build_potential_material(
                    memory,
                    ir=ir,
                    validation=validation,
                    template_markdown=template_markdown,
                ),
                template_markdown=template_markdown,
                resolved_contract=resolved_contract,
                ir=ir,
                validation=validation,
            )
        return ir, validation

    def template_questions(
        self,
        template_markdown: str,
        *,
        source_path: str = "",
        compile_backend: str = "",
    ) -> tuple[ModelIR, ValidationReport, list[dict[str, Any]]]:
        backend = normalize_compile_backend(compile_backend)
        questions = []
        if not backend or (backend not in SUPPORTED_COMPILE_BACKENDS and backend != COMPARE_BACKENDS_SENTINEL):
            questions.append(backend_selection_question(compile_backend))
            ir, validation = self.parse_contract_template(template_markdown, source_path=source_path)
            return ir, validation, questions
        compile_context = backend if backend in SUPPORTED_COMPILE_BACKENDS or backend == COMPARE_BACKENDS_SENTINEL else None
        ir, validation = self.parse_contract_template(
            template_markdown,
            source_path=source_path,
            compile_backend=compile_context,
        )
        blocking = sorted(
            [issue for issue in validation.issues if issue.severity == "error"],
            key=lambda issue: (99 if issue.field_key == "review.approved" else 0, issue.field_key),
        )
        if blocking:
            contract = parse_contract(template_markdown)
            packets = build_question_packets(blocking, contract)
            packet = next(
                (item for item in packets if item.key != "review.approved"),
                packets[0],
            )
            questions.append(question_packet_to_dict(packet, contract, compile_backend=backend))
        return ir, validation, questions

    def compile_cosmotransitions_template(
        self,
        template_markdown: str,
        *,
        output_dir: Path | None = None,
        output_path: Path | None = None,
        run_import_check: bool = True,
    ) -> CompileResult:
        try:
            require_configured_runtime_python(self.settings)
        except ConfigurationError as exc:
            raise CompileBlocked(str(exc)) from exc
        return compile_contract_template(
            template_markdown,
            self.settings,
            output_dir=output_dir,
            output_path=output_path,
            run_import_check=run_import_check,
        )

    def compile_phasetracer_template(
        self,
        template_markdown: str,
        *,
        output_dir: Path | None = None,
        output_path: Path | None = None,
        run_smoke: bool = True,
        run_transition_smoke: bool = False,
        phasetracer_root: str = "",
        linux_runner: str = "native",
        wsl_distro: str = "",
        clang_format: bool = True,
    ) -> CompileResult:
        from .phasetracer_backend import compile_phasetracer_template

        try:
            require_configured_runtime_python(self.settings)
            resolved_phasetracer_root = require_configured_phasetracer_root(self.settings, phasetracer_root)
        except ConfigurationError as exc:
            raise CompileBlocked(str(exc)) from exc
        return compile_phasetracer_template(
            template_markdown,
            self.settings,
            output_dir=output_dir,
            output_path=output_path,
            run_smoke=run_smoke,
            run_transition_smoke=run_transition_smoke,
            phasetracer_root=resolved_phasetracer_root,
            linux_runner=linux_runner,
            wsl_distro=wsl_distro,
            clang_format=clang_format,
        )

    def compare_backends_template(
        self,
        template_markdown: str,
        *,
        template_path: Path | None = None,
        output_dir: Path | None = None,
        report_dir: Path | None = None,
        phasetracer_root: str = "",
        linux_runner: str = "native",
        wsl_distro: str = "",
        run_transition_smoke: bool = False,
        clang_format: bool = True,
        force_regenerate: bool = False,
    ):
        from .backend_compare import compare_backends_template

        return compare_backends_template(
            template_markdown,
            self.settings,
            template_path=template_path,
            output_dir=output_dir,
            report_dir=report_dir,
            phasetracer_root=phasetracer_root,
            linux_runner=linux_runner,
            wsl_distro=wsl_distro,
            run_transition_smoke=run_transition_smoke,
            clang_format=clang_format,
            force_regenerate=force_regenerate,
        )

    def run_phase_history(
        self,
        model_path: str | Path,
        *,
        parameters: dict[str, Any] | None = None,
    ) -> PhaseHistoryResult:
        return run_phase_history(Path(model_path), self.settings, parameters=parameters)

    def persist_runtime(
        self,
        run: AgentRun,
        *,
        model_path: Path | None = None,
        phase_history: PhaseHistoryResult | None = None,
    ) -> AgentRun:
        return persist_runtime_artifacts(
            run=run,
            model_path=model_path,
            phase_history=phase_history,
        )


def _read_contract_template_if_present(source_path: Path) -> str:
    if source_path.suffix.lower() not in {".md", ".markdown", ".tex"}:
        return ""
    try:
        text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if is_contract_template(text) else ""


def _settings_with_run_input(settings: Settings, run_dir: Path) -> Settings:
    input_root = run_dir / GLOBAL_INPUT_DIRNAME
    upload_root = input_root / "uploads"
    for path in (input_root, upload_root):
        path.mkdir(parents=True, exist_ok=True)
    return replace(
        settings,
        input_root=input_root,
        paper_root=input_root,
        upload_root=upload_root,
    )


def _copy_document_input(document: SourceDocument, source_path: Path, run_dir: Path) -> SourceDocument:
    input_root = (run_dir / GLOBAL_INPUT_DIRNAME).resolve()
    input_root.mkdir(parents=True, exist_ok=True)
    source_resolved = source_path.resolve()
    if _is_relative_to(source_resolved, input_root):
        return document
    target_path = input_root / source_resolved.name
    if source_resolved.is_dir():
        if _is_relative_to(input_root, source_resolved):
            return document
        shutil.copytree(source_resolved, target_path, dirs_exist_ok=True)
    else:
        shutil.copy2(source_resolved, target_path)
    return replace(document, path=target_path)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _emit_progress(progress: ProgressCallback | None, message: str, fraction: float | None = None) -> None:
    if progress is None:
        return
    progress(message, fraction)
