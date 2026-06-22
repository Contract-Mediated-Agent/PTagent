from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .config import (
    ConfigurationError,
    DEFAULT_RUNTIME_PYTHON,
    default_user_config_path,
    get_settings,
    load_user_config,
    require_configured_runtime_python,
    validate_phasetracer_root,
    validate_runtime_python,
    write_user_config,
)
from .artifact_layout import generated_models_dir, proof_materials_dir
from .arxiv_source import check_pdf_title_against_arxiv, resolve_latest_arxiv_metadata
from .backends import SUPPORTED_COMPILE_BACKENDS, normalize_compile_backend, supported_backends_text
from .compiler import CompileBlocked
from .model_preflight import ModelSelectionRequired
from .workflow import PhaseTransitionAgent
from .reporting import write_json
from .template_contract import contract_to_model_ir, validate_contract_template, write_resolved_contract_data
from .user_guidance import write_user_guidance
from .runner import import_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="General finite-temperature phase-transition agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Write a persistent user config for PTagent runs.")
    init.add_argument(
        "--artifact-root",
        default="",
        help="Directory where fresh task folders are created. Default: ~/PTagentRuns.",
    )
    init.add_argument(
        "--runtime-python",
        default=None,
        help="Python executable used for CosmoTransitions runtime/import checks. Default: current Python.",
    )
    init.add_argument(
        "--phasetracer-root",
        default=None,
        help="Optional PhaseTracer source tree path in the Linux build environment.",
    )
    init.add_argument(
        "--config",
        default="",
        help="Optional config path. Default: ~/.ptagent/config.toml or PTAGENT_CONFIG.",
    )
    init.add_argument("--force", action="store_true", help="Rewrite the config even if an existing config cannot be merged cleanly.")

    extract = sub.add_parser("extract", help="Build PaperMemory, evidence material, and a deterministic backend-neutral contract template.")
    extract.add_argument(
        "--input",
        default="",
        help="PDF/Markdown/TeX source or local arXiv e-print archive (.zip/.tar/.tar.gz/.tgz/.gz).",
    )
    extract.add_argument("--arxiv", default="", help="arXiv ID; latest version TeX source is preferred over PDF.")
    extract.add_argument("--refresh-arxiv", action="store_true", help="Re-download arXiv e-print source and PDF.")
    extract.add_argument(
        "--model",
        default="",
        help="Optional pre-contract model focus, e.g. XSM or 2HDM. If omitted and multiple models are detected, extraction stops with a model-selection question.",
    )

    validate = sub.add_parser("validate", help="Validate a reviewed template markdown file.")
    validate.add_argument("--template", required=True, help="Reviewed template markdown path.")
    validate.add_argument("--source", default="", help="Original source path for provenance.")
    validate.add_argument("--json-out", default="", help="Optional validation JSON output path.")

    resolve = sub.add_parser("resolve", help="Resolve human Markdown into derived JSON, validation, and questions.")
    resolve.add_argument("--template", required=True, help="Human-edited contract_template.md path.")
    resolve.add_argument("--source", default="", help="Original source path for provenance.")
    resolve.add_argument(
        "--backend",
        default="",
        metavar="{cosmotransitions,phasetracer}",
        help="Optional compile-backend context for user_questions.md; supported: cosmotransitions, phasetracer.",
    )

    guide = sub.add_parser("guide", help="Write a human-facing question guide for unresolved template fields.")
    guide.add_argument("--template", required=True, help="Contract template markdown path.")
    guide.add_argument("--memory", default="", help="Optional paper_memory.json path for source evidence hints.")
    guide.add_argument("--out", default="", help="Optional output markdown path; defaults to proof_materials/user_questions.md.")
    guide.add_argument(
        "--backend",
        default="",
        metavar="{cosmotransitions,phasetracer}",
        help="Optional compile-backend context; unsupported names are rendered as a backend gate question.",
    )

    compile_cmd = sub.add_parser("compile", help="Compile a reviewed contract template to a backend model.")
    compile_cmd.add_argument("--template", required=True, help="Reviewed and approved contract template markdown path.")
    compile_cmd.add_argument("--source", default="", help="Original source path for provenance.")
    compile_cmd.add_argument("--output-dir", default="", help="Optional output directory; default is the template task's generated_models/ folder.")
    compile_cmd.add_argument(
        "--backend",
        default="",
        metavar="{cosmotransitions,phasetracer}",
        help="Compile target backend. Required; supported: cosmotransitions, phasetracer.",
    )
    compile_cmd.add_argument("--no-smoke", action="store_true", help="Skip backend smoke/import check.")
    compile_cmd.add_argument("--phasetracer-root", default=None, help="PhaseTracer source tree path in the Linux build environment.")
    compile_cmd.add_argument("--linux-runner", choices=["native", "wsl"], default=None, help="How to run PhaseTracer smoke checks. Default: native Linux shell.")
    compile_cmd.add_argument("--wsl-distro", default=None, help="WSL distribution used only with --linux-runner wsl.")
    compile_cmd.add_argument("--transition-smoke", action="store_true", help="For --backend phasetracer, run the PhaseTracer TransitionFinder smoke check required by the 4D skill delivery flow unless explicitly skipped.")
    compile_cmd.add_argument("--no-clang-format", action="store_true", help="For --backend phasetracer, skip optional clang-format cleanup even if clang-format is installed.")

    compare = sub.add_parser("compare-backends", help="Generate both backends from one contract and compare smoke-point potentials.")
    compare.add_argument("--template", required=True, help="Reviewed and approved contract template markdown path.")
    compare.add_argument("--output-dir", default="", help="Optional generated model output directory; default is the template task's generated_models/ folder.")
    compare.add_argument("--report-dir", default="", help="Optional report directory; default is the template task's proof_materials/ folder.")
    compare.add_argument("--phasetracer-root", default=None, help="PhaseTracer source tree path in the Linux build environment.")
    compare.add_argument("--linux-runner", choices=["native", "wsl"], default=None, help="How to run PhaseTracer smoke checks. Default: native Linux shell.")
    compare.add_argument("--wsl-distro", default=None, help="WSL distribution used only with --linux-runner wsl.")
    compare.add_argument("--transition-smoke", action="store_true", help="Also run the PhaseTracer TransitionFinder smoke check.")
    compare.add_argument("--no-clang-format", action="store_true", help="Skip optional clang-format cleanup even if clang-format is installed.")
    compare.add_argument("--force-regenerate", action="store_true", help="Regenerate both backend artifacts before comparing instead of reusing hash-matched generated files.")

    check = sub.add_parser("check-model", help="Run local checks for an agent-authored model.")
    check.add_argument("--model", required=True, help="Generated Python model file.")
    check.add_argument("--template", default="", help="Optional contract_template.md path for stale-model hash checks.")

    run = sub.add_parser("run", help="Run phase-history analysis for a generated model.")
    run.add_argument("--model", required=True, help="Generated model Python file.")
    run.add_argument("--parameters", default="", help="Optional JSON object or JSON file with parameter overrides.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return _run_init(args)

    settings = get_settings()
    agent = PhaseTransitionAgent(settings)

    if args.command == "extract":
        if args.arxiv:
            if args.input and Path(args.input).suffix.lower() == ".pdf":
                metadata = resolve_latest_arxiv_metadata(args.arxiv)
                title_check = check_pdf_title_against_arxiv(args.input, metadata)
                if title_check.is_mismatch:
                    print("arXiv ID and uploaded/input PDF title are inconsistent; extraction stopped.")
                    print(f"arXiv latest version: {metadata.canonical_id}")
                    print(f"arXiv title: {title_check.arxiv_title or '<unreadable>'}")
                    print(f"PDF title: {title_check.pdf_title or '<unreadable>'}")
                    print(f"Similarity: {title_check.similarity:.2f}")
                    return 2
                if title_check.status == "unknown":
                    print(f"WARNING: {title_check.message} arXiv input will still take priority.")
            try:
                bundle = agent.extract_arxiv(args.arxiv, refresh=args.refresh_arxiv, model_focus=args.model)
            except ModelSelectionRequired as exc:
                return _print_model_selection_required(exc)
        elif args.input:
            if Path(args.input).suffix.lower() in {".m", ".wl"}:
                raise SystemExit(
                    "4D extraction does not accept Mathematica/Wolfram .m/.wl files. "
                    "Use --ptagent-engine 3deft for DRalgo/3DEFT sources, "
                    "or provide PDF/Markdown/TeX for 4D extraction."
                )
            try:
                bundle = agent.extract(args.input, model_focus=args.model)
            except ModelSelectionRequired as exc:
                return _print_model_selection_required(exc)
        else:
            raise SystemExit("extract requires either --input or --arxiv")
        print(f"Run ID: {bundle.run.run_id}")
        print(f"PaperMemory: {bundle.run.memory_path}")
        print(f"Potential material: {bundle.run.material_path}")
        print(f"Contract template: {bundle.run.template_path}")
        print(f"ModelIR: {bundle.run.ir_path}")
        print(f"Validation: {bundle.run.validation_path}")
        print(f"Ready for compile: {bundle.validation.ready_for_compile}")
        _print_issues(bundle.validation.to_dict()["issues"])
        return 0

    if args.command == "validate":
        template_path = Path(args.template)
        template = template_path.read_text(encoding="utf-8")
        contract_validation = validate_contract_template(template, review_required=agent.settings.review_required)
        resolved_path = ""
        if contract_validation.contract:
            resolved_path = str(write_resolved_contract_data(contract_validation.contract, proof_materials_dir(template_path) / "contract_resolved.json"))
        ir = contract_to_model_ir(contract_validation.contract, source_path=args.source)
        validation = contract_validation.to_report()
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(validation.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Model: {ir.model_name}")
        print(f"Resolved contract: {resolved_path}")
        print(f"Ready for compile: {validation.ready_for_compile}")
        print(f"Ready for runtime: {validation.ready_for_runtime}")
        _print_issues(validation.to_dict()["issues"])
        return 0 if validation.ready_for_compile else 2

    if args.command == "resolve":
        template_path = Path(args.template)
        template = template_path.read_text(encoding="utf-8")
        backend = normalize_compile_backend(args.backend)
        validation = validate_contract_template(
            template,
            review_required=agent.settings.review_required,
            compile_backend=backend if backend in SUPPORTED_COMPILE_BACKENDS else None,
        )
        resolved_path = ""
        if validation.contract:
            resolved_path = str(write_resolved_contract_data(validation.contract, proof_materials_dir(template_path) / "contract_resolved.json"))
        ir = contract_to_model_ir(validation.contract, source_path=args.source)
        proof_dir = proof_materials_dir(template_path)
        validation_path = write_json(proof_dir / "validation.json", validation.to_report().to_dict())
        ir_path = write_json(proof_dir / "model_ir.json", ir.to_dict())
        output_path, _ = write_user_guidance(template_path=template_path, compile_backend=args.backend)
        print(f"Resolved contract: {resolved_path}")
        print(f"ModelIR: {ir_path}")
        print(f"Validation: {validation_path}")
        print(f"User question guide: {output_path}")
        print(f"Ready for compile: {validation.ok}")
        _print_issues([issue.to_dict() for issue in validation.issues])
        return 0 if validation.ok else 2

    if args.command == "guide":
        output_path, validation = write_user_guidance(
            template_path=args.template,
            output_path=args.out or None,
            memory_path=args.memory or None,
            compile_backend=args.backend,
        )
        print(f"User question guide: {output_path}")
        print(f"Ready for compile: {validation.ready_for_compile}")
        _print_issues(validation.to_dict()["issues"])
        return 0

    if args.command == "compile":
        backend = _require_compile_backend(args.backend)
        _reject_phasetracer_only_options_for_cosmotransitions(args, backend)
        template_path = Path(args.template)
        template = template_path.read_text(encoding="utf-8")
        output_dir = Path(args.output_dir) if args.output_dir else generated_models_dir(template_path)
        try:
            if backend == "phasetracer":
                result = agent.compile_phasetracer_template(
                    template,
                    output_dir=output_dir,
                    run_smoke=not args.no_smoke,
                    run_transition_smoke=args.transition_smoke and not args.no_smoke,
                    phasetracer_root=args.phasetracer_root or "",
                    linux_runner=_linux_runner(args),
                    wsl_distro=_wsl_distro(args),
                    clang_format=not args.no_clang_format,
                )
            else:
                _runtime_python(settings)
                result = agent.compile_cosmotransitions_template(
                    template,
                    output_dir=output_dir,
                    run_import_check=not args.no_smoke,
                )
        except CompileBlocked as exc:
            raise SystemExit(str(exc)) from exc
        resolved_path = write_resolved_contract_data(validate_contract_template(template, review_required=True).contract, proof_materials_dir(template_path) / "contract_resolved.json")
        print(f"Resolved contract: {resolved_path}")
        print(f"Generated model path: {result.model_path}")
        print(f"Status: {result.import_check_status}")
        print("How to call this model:")
        for line in result.usage_instructions:
            print(f"  {line}")
        for warning in result.warnings:
            print(f"INFO: {warning}")
        return 0

    if args.command == "compare-backends":
        template_path = Path(args.template)
        template = template_path.read_text(encoding="utf-8")
        output_dir = Path(args.output_dir) if args.output_dir else generated_models_dir(template_path)
        report_dir = Path(args.report_dir) if args.report_dir else proof_materials_dir(template_path)
        try:
            result = agent.compare_backends_template(
                template,
                template_path=template_path,
                output_dir=output_dir,
                report_dir=report_dir,
                phasetracer_root=args.phasetracer_root or "",
                linux_runner=_linux_runner(args),
                wsl_distro=_wsl_distro(args),
                run_transition_smoke=args.transition_smoke,
                clang_format=not args.no_clang_format,
                force_regenerate=args.force_regenerate,
            )
        except CompileBlocked as exc:
            raise SystemExit(str(exc)) from exc
        from .backend_compare import render_backend_compare_summary

        print(render_backend_compare_summary(result))
        return 0

    if args.command == "check-model":
        _runtime_python(settings)
        ok, message = import_check(Path(args.model), settings)
        print(message)
        if args.template:
            status = _model_template_hash_status(Path(args.model), Path(args.template))
            print(f"Template hash status: {status}")
            return 0 if ok and status == "match" else 2
        return 0 if ok else 2

    if args.command == "run":
        _runtime_python(settings)
        parameters = _load_parameters(args.parameters)
        result = agent.run_phase_history(args.model, parameters=parameters)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status == "success" else 3

    return 1


def _run_init(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser() if args.config else default_user_config_path()
    try:
        existing = load_user_config(config_path)
    except Exception as exc:
        if not args.force:
            raise SystemExit(f"Could not read existing config {config_path}: {exc}. Pass --force to rewrite it.") from exc
        existing = {}
    artifact_root = Path(
        args.artifact_root
        or str(existing.get("artifact_root") or "")
        or str(Path.home() / "PTagentRuns")
    ).expanduser()
    runtime_python = str(
        args.runtime_python
        or existing.get("runtime_python")
        or DEFAULT_RUNTIME_PYTHON
    )
    phasetracer_root = str(
        args.phasetracer_root
        if args.phasetracer_root is not None
        else existing.get("phasetracer_root") or ""
    ).strip()
    ok, message = validate_runtime_python(runtime_python)
    if not ok:
        raise SystemExit(f"Configured runtime Python failed validation: {message}")
    if phasetracer_root:
        ok, message = validate_phasetracer_root(phasetracer_root)
        if not ok:
            raise SystemExit(f"Configured PhaseTracer source root failed validation: {message}")
    path = write_user_config(
        artifact_root=artifact_root,
        runtime_python=runtime_python,
        phasetracer_root=phasetracer_root,
        config_path=config_path,
        force=True,
    )
    print(f"Config path: {path}")
    print(f"Artifact root: {Path(artifact_root).expanduser().resolve()}")
    print(f"Runtime Python: {Path(runtime_python).expanduser().resolve()}")
    if phasetracer_root:
        print(f"PhaseTracer root: {phasetracer_root}")
    else:
        print("PhaseTracer root: not configured yet; add it later with `python -m ptagent init --phasetracer-root <PhaseTracer-root>`.")
    print("Fresh runs and backend compiles will now use this persistent config unless overridden explicitly.")
    return 0


def _require_compile_backend(value: str) -> str:
    backend = normalize_compile_backend(value)
    supported = supported_backends_text()
    if not backend:
        raise SystemExit(
            "No compile backend specified. Please choose one explicitly with "
            "`--backend cosmotransitions` or `--backend phasetracer`. "
            f"Supported backends: {supported}."
        )
    if backend not in SUPPORTED_COMPILE_BACKENDS:
        raise SystemExit(f"Backend {value!r} is not supported yet. Supported backends: {supported}.")
    return backend


def _reject_phasetracer_only_options_for_cosmotransitions(args: argparse.Namespace, backend: str) -> None:
    if backend != "cosmotransitions":
        return
    supplied: list[str] = []
    if args.phasetracer_root:
        supplied.append("--phasetracer-root")
    if args.linux_runner:
        supplied.append("--linux-runner")
    if args.wsl_distro:
        supplied.append("--wsl-distro")
    if args.transition_smoke:
        supplied.append("--transition-smoke")
    if args.no_clang_format:
        supplied.append("--no-clang-format")
    if supplied:
        options = ", ".join(supplied)
        raise SystemExit(f"PhaseTracer-only option(s) {options} are only valid with --backend phasetracer. Remove them or choose `--backend phasetracer`.")


def _runtime_python(settings) -> Path:
    try:
        return require_configured_runtime_python(settings)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc


def _linux_runner(args: argparse.Namespace) -> str:
    return args.linux_runner or "native"


def _wsl_distro(args: argparse.Namespace) -> str:
    return args.wsl_distro or ""


def _load_parameters(value: str) -> dict:
    if not value:
        return {}
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _print_issues(issues: list[dict]) -> None:
    if not issues:
        print("No validation issues.")
        return
    for issue in issues:
        print(f"[{issue['severity']}] {issue['code']} {issue.get('field_key', '')}: {issue['message']}")


def _print_model_selection_required(exc: ModelSelectionRequired) -> int:
    proof_dir = proof_materials_dir(exc.run_dir)
    print("Model selection is required before generating contract_template.md.")
    print(f"Task dir: {exc.run_dir}")
    print(f"User question guide: {proof_dir / 'user_questions.md'}")
    print(f"Model candidates: {proof_dir / 'model_candidates.md'}")
    print(f"Model candidates JSON: {proof_dir / 'model_candidates.json'}")
    print(f"Question: {exc.result.question}")
    print("Rerun extraction with one selected model, for example: --model XSM")
    return 2


def _model_template_hash_status(model_path: Path, template_path: Path) -> str:
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest() if template else ""
    model_hash = _model_constant(model_path, "PTAGENT_TEMPLATE_SHA256")
    if not template_hash:
        return "no_template"
    if not model_hash:
        return "missing_model_hash"
    return "match" if template_hash == model_hash else "stale_model"


def _model_constant(model_path: Path, name: str) -> str:
    if not model_path.exists():
        return ""
    text = model_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{re.escape(name)}\s*=\s*['\"]([0-9a-f]{{64}})['\"]", text, flags=re.MULTILINE)
    return match.group(1) if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
