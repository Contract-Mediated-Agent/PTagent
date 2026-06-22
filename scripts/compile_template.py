from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a PTagent contract template to a backend model.")
    parser.add_argument("--template", required=True, help="contract_template.md path.")
    parser.add_argument("--output-dir", default="", help="Optional output directory; default is the template task's generated_models/ folder.")
    parser.add_argument("--report-dir", default="", help="Optional report directory for --compare-backends; default is the template task's proof_materials/ folder.")
    parser.add_argument("--project-root", default="", help="Optional PTagent repo root.")
    parser.add_argument("--no-smoke", action="store_true", help="Skip local import/smoke check.")
    parser.add_argument("--compare-backends", action="store_true", help="Generate CosmoTransitions and PhaseTracer artifacts from the same contract and compare smoke-point potentials.")
    parser.add_argument(
        "--backend",
        default="",
        metavar="{cosmotransitions,phasetracer}",
        help="Compile target backend. Required unless --compare-backends is used; supported: cosmotransitions, phasetracer.",
    )
    parser.add_argument(
        "--phasetracer-root",
        default=None,
        help="PhaseTracer source tree path in the Linux build environment.",
    )
    parser.add_argument("--linux-runner", choices=["native", "wsl"], default=None, help="How to run PhaseTracer smoke checks. Default: native Linux shell.")
    parser.add_argument("--wsl-distro", default=None, help="WSL distribution used only with --linux-runner wsl.")
    parser.add_argument("--transition-smoke", action="store_true", help="For --backend phasetracer, run the PhaseTracer TransitionFinder smoke check required by the 4D skill delivery flow unless explicitly skipped.")
    parser.add_argument("--no-clang-format", action="store_true", help="For PhaseTracer generation, skip optional clang-format cleanup even if clang-format is installed.")
    parser.add_argument("--force-regenerate", action="store_true", help="For --compare-backends, regenerate both backend artifacts instead of reusing hash-matched generated files.")
    args = parser.parse_args()

    from _bootstrap import ensure_ptagent_backend

    ensure_ptagent_backend(args.project_root)

    from ptagent import get_settings
    from ptagent.ptagent_4d.artifact_layout import generated_models_dir, proof_materials_dir
    from ptagent.ptagent_4d.backends import COMPARE_BACKENDS_SENTINEL
    from ptagent.ptagent_4d.compiler import CompileBlocked
    from ptagent.ptagent_4d.template_contract import contract_to_model_ir, compile_contract_template, validate_contract_template, write_resolved_contract_data
    from ptagent.ptagent_4d.user_guidance import build_user_guidance_markdown
    settings = get_settings()

    backend = ""
    if args.compare_backends:
        if args.backend:
            parser.error("--compare-backends generates both supported backends; do not pass --backend.")
    else:
        if args.force_regenerate:
            parser.error("--force-regenerate is only valid with --compare-backends.")
        backend = _require_compile_backend(args.backend)
        _reject_phasetracer_only_options_for_cosmotransitions(args, backend)

    template_path = Path(args.template).expanduser().resolve()
    template = template_path.read_text(encoding="utf-8")
    validation = validate_contract_template(
        template,
        review_required=True,
        compile_backend=COMPARE_BACKENDS_SENTINEL if args.compare_backends else backend,
    )
    proof_dir = proof_materials_dir(template_path)
    resolved_path = ""
    model_ir_path = ""
    validation_path = ""
    if validation.contract:
        resolved_path = str(write_resolved_contract_data(validation.contract, proof_dir / "contract_resolved.json"))
        model_ir = contract_to_model_ir(validation.contract)
        model_ir_path = str(proof_dir / "model_ir.json")
        Path(model_ir_path).write_text(json.dumps(model_ir.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    validation_path = str(proof_dir / "validation.json")
    Path(validation_path).write_text(json.dumps(validation.to_report().to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation.ok:
        user_questions_path = proof_dir / "user_questions.md"
        user_questions_path.write_text(
            build_user_guidance_markdown(
                template_markdown=template,
                validation=validation.to_report(),
                template_path=template_path,
                compile_backend=COMPARE_BACKENDS_SENTINEL if args.compare_backends else backend,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "ready_for_compile": False,
                    "template": str(template_path),
                    "contract_resolved": str(resolved_path),
                    "model_ir": str(model_ir_path),
                    "validation": str(validation_path),
                    "user_questions": str(user_questions_path),
                    "issues": [issue.to_dict() for issue in validation.issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else generated_models_dir(template_path)
    if args.compare_backends:
        if args.no_smoke:
            parser.error("--compare-backends requires PhaseTracer smoke output; remove --no-smoke.")
        from ptagent.ptagent_4d.backend_compare import compare_backends_template

        report_dir = Path(args.report_dir).expanduser().resolve() if args.report_dir else proof_dir
        try:
            compare_result = compare_backends_template(
                template,
                settings,
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
        from ptagent.ptagent_4d.backend_compare import render_backend_compare_summary

        print(render_backend_compare_summary(compare_result))
        return 0

    try:
        if backend == "phasetracer":
            from ptagent.ptagent_4d.phasetracer_backend import compile_phasetracer_template

            result = compile_phasetracer_template(
                template,
                settings,
                output_dir=output_dir,
                run_smoke=not args.no_smoke,
                run_transition_smoke=args.transition_smoke and not args.no_smoke,
                phasetracer_root=_phasetracer_root(args, settings),
                linux_runner=_linux_runner(args),
                wsl_distro=_wsl_distro(args),
                clang_format=not args.no_clang_format,
            )
        else:
            _runtime_python(settings)
            result = compile_contract_template(
                template,
                settings,
                output_dir=output_dir,
                run_import_check=not args.no_smoke,
            )
    except CompileBlocked as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "ready_for_compile": True,
                "backend": backend,
                "contract_resolved": str(resolved_path),
                "model_ir": str(model_ir_path),
                "validation": str(validation_path),
                "model_path": str(result.model_path),
                "import_check_status": result.import_check_status,
                "warnings": result.warnings,
                "usage_instructions": result.usage_instructions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _require_compile_backend(value: str) -> str:
    from ptagent.ptagent_4d.backends import SUPPORTED_COMPILE_BACKENDS, normalize_compile_backend, supported_backends_text

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


def _runtime_python(settings) -> None:
    from ptagent.ptagent_4d.config import ConfigurationError, require_configured_runtime_python

    try:
        require_configured_runtime_python(settings)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc


def _phasetracer_root(args: argparse.Namespace, settings) -> str:
    from ptagent.ptagent_4d.config import ConfigurationError, require_configured_phasetracer_root

    try:
        return require_configured_phasetracer_root(settings, args.phasetracer_root)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc


def _linux_runner(args: argparse.Namespace) -> str:
    return args.linux_runner or "native"


def _wsl_distro(args: argparse.Namespace) -> str:
    return args.wsl_distro or ""


if __name__ == "__main__":
    raise SystemExit(main())
