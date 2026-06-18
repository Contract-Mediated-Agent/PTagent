from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .approval import approve_contract_gate
from .contract import (
    REVIEW_GATE_NAME,
    RESOLVED_CONTRACT_NAME,
    USER_QUESTIONS_NAME,
    VALIDATION_NAME,
    ThreeDeftBlocked,
    validate_contract_template,
    write_resolved_artifacts,
)
from .environment import check_wolfram_and_dralgo, install_dralgo
from .extraction import EXTRACTION_REPORT_NAME, extract_source_to_template
from .layout import CONTRACT_TEMPLATE_NAME, build_run_layout
from .mathematica_compare import compare_mathematica_fixed_v3d
from .merge import MERGED_CONTRACT_NAME, merge_dralgo_output_into_contract
from .model_check import configure_build_run_phasetracer_model
from .phasetracer import compile_phasetracer_template
from .runner import DRALGO_OUTPUT_NAME, run_dralgo_template
from .scriptgen import GENERATED_DRALGO_SCRIPT_NAME, generate_marked_dralgo_script


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent 3DEFT -> PhaseTracer pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="Create a standalone 3DEFT contract template from a reviewed DRalgo source file.")
    extract.add_argument("--input", required=True, help="Reviewed DRalgo Mathematica .m/.wl source file.")
    extract.add_argument("--run-dir", default="", help="Task directory. Default: <artifact_root>/<timestamp>_<input stem>.")
    extract.add_argument("--output-dir", dest="run_dir", help=argparse.SUPPRESS)
    extract.add_argument("--model-name", default="", help="Model name for the generated 3DEFT contract. Default: input file stem.")

    resolve = sub.add_parser("resolve", help="Resolve and validate a standalone 3DEFT contract template.")
    resolve.add_argument("--template", required=True, help=f"{CONTRACT_TEMPLATE_NAME} path.")
    resolve.add_argument("--require-compile-approval", action="store_true", help="Treat PhaseTracer approval as a validation blocker.")

    run = sub.add_parser("run", help="Run the reviewed DRalgo source and normalize marked output.")
    run.add_argument("--template", required=True, help="Resolved 3DEFT contract template path.")
    run.add_argument("--output", default="", help=f"Output JSON path. Default: proof_materials/{DRALGO_OUTPUT_NAME} for task-local runs.")
    run.add_argument("--wolframscript", default="", help="wolframscript executable path for real DRalgo execution.")
    run.add_argument("--install-dralgo", action="store_true", help="If DRalgo is missing, install it before running.")
    run.add_argument("--install-source", choices=["wolfram", "github"], default="wolfram", help="DRalgo install source used with --install-dralgo.")

    generate = sub.add_parser("generate-dralgo", help="Generate a marked Wolfram wrapper around a reviewed user-supplied DRalgo .m file.")
    generate.add_argument("--template", required=True, help="3DEFT contract template path.")
    generate.add_argument("--output", default="", help=f"Output .m path. Default: DRalgo_model/{GENERATED_DRALGO_SCRIPT_NAME} for task-local runs.")

    merge = sub.add_parser("merge-output", help="Merge normalized DRalgo output back into a 3DEFT contract candidate.")
    merge.add_argument("--template", required=True, help="3DEFT contract template path.")
    merge.add_argument("--dralgo-output", required=True, help=f"Normalized {DRALGO_OUTPUT_NAME} path.")
    merge.add_argument("--output", default="", help=f"Merged contract path. Default: {MERGED_CONTRACT_NAME} beside the template in the run root.")

    compile_cmd = sub.add_parser("compile", help="Generate a PhaseTracer-compatible C++ project from a reviewed 3DEFT contract.")
    compile_cmd.add_argument("--template", required=True, help="Resolved and approved 3DEFT contract template path.")
    compile_cmd.add_argument("--backend", default="phasetracer", help="Only phasetracer is supported for 3DEFT v1.")
    compile_cmd.add_argument("--output-dir", default="", help="Output directory. Default: DRalgo_model/<model_name>_phasetracer for task-local runs.")

    approve = sub.add_parser("approve", help="Record a whole-template review gate approval in a 3DEFT contract.")
    approve.add_argument("--template", required=True, help="3DEFT contract template or merged contract path.")
    approve.add_argument("--gate", required=True, choices=["phasetracer_compile"], help="Review gate to approve.")
    approve.add_argument("--user-approval", default="", help="Exact user review/approval sentence required for phasetracer_compile.")
    approve.add_argument("--output", default="", help="Optional output path. Default: update template in place.")

    check_model = sub.add_parser("check-model", help="Configure/build/run the generated 3DEFT PhaseTracer run_model target.")
    check_model.add_argument("--project-dir", required=True, help="Generated 3DEFT PhaseTracer project directory.")
    check_model.add_argument(
        "--phasetracer-root",
        default="",
        help="PhaseTracer source tree path. Default: PTAGENT_PHASETRACER_ROOT or ~/.ptagent/config.toml.",
    )
    check_model.add_argument("--build-dir", default="", help="CMake build directory. Default: <project-dir>/build.")
    check_model.add_argument("--configure-only", action="store_true", help="Only run CMake configure.")
    check_model.add_argument("--no-run", action="store_true", help="Build run_model but do not execute it.")

    compare_math = sub.add_parser("compare-mathematica", help="Run fixed-3D-parameter Mathematica replacement checks for generated V3D.")
    compare_math.add_argument("--template", required=True, help=f"Reviewed {MERGED_CONTRACT_NAME} path.")
    compare_math.add_argument("--project-dir", required=True, help="Generated 3DEFT PhaseTracer project directory containing metadata.json.")
    compare_math.add_argument("--wolframscript", default="", help="Optional wolframscript executable path.")
    compare_math.add_argument("--output", default="", help="Output comparison JSON. Default: <project-dir>/mathematica_fixed_v3d_compare.json.")
    compare_math.add_argument("--script", default="", help="Output Wolfram script. Default: <project-dir>/mathematica_fixed_v3d_compare.wl.")
    compare_math.add_argument("--tolerance", type=float, default=1.0e-8, help="Absolute/relative tolerance for Mathematica vs fixed direct-V3D reference.")
    compare_math.add_argument("--timeout-seconds", type=int, default=120, help="wolframscript timeout.")

    env_check = sub.add_parser("env-check", help="Find Mathematica/wolframscript and verify DRalgo can be loaded.")
    env_check.add_argument("--wolframscript", default="", help="Optional wolframscript path.")

    install = sub.add_parser("install-dralgo", help="Install DRalgo through wolframscript, then verify it loads.")
    install.add_argument("--wolframscript", default="", help="Optional wolframscript path.")
    install.add_argument("--source", choices=["wolfram", "github"], default="wolfram", help="Paclet source.")
    install.add_argument("--yes", action="store_true", help="Required to perform installation.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "extract":
            return _cmd_extract(args)
        if args.command == "resolve":
            return _cmd_resolve(args)
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "generate-dralgo":
            return _cmd_generate_dralgo(args)
        if args.command == "merge-output":
            return _cmd_merge_output(args)
        if args.command == "compile":
            return _cmd_compile(args)
        if args.command == "approve":
            return _cmd_approve(args)
        if args.command == "check-model":
            return _cmd_check_model(args)
        if args.command == "compare-mathematica":
            return _cmd_compare_mathematica(args)
        if args.command == "env-check":
            return _cmd_env_check(args)
        if args.command == "install-dralgo":
            return _cmd_install_dralgo(args)
    except ThreeDeftBlocked as exc:
        print(f"3DEFT blocked: {_console_safe(str(exc))}")
        return 2
    raise SystemExit(f"unsupported command {args.command!r}")


def _console_safe(message: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return message.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _cmd_extract(args: argparse.Namespace) -> int:
    source = Path(args.input)
    if not source.exists():
        raise ThreeDeftBlocked(f"input does not exist: {source}")
    run_dir_arg = args.run_dir or None
    layout = build_run_layout(source, run_dir=run_dir_arg)
    layout.input_dir.mkdir(parents=True, exist_ok=True)
    layout.proof_dir.mkdir(parents=True, exist_ok=True)
    layout.dralgo_model_dir.mkdir(parents=True, exist_ok=True)

    reviewed_source = layout.input_dir / source.name
    if source.resolve() != reviewed_source.resolve():
        shutil.copy2(source, reviewed_source)

    template_path = layout.run_dir / CONTRACT_TEMPLATE_NAME
    extraction = extract_source_to_template(reviewed_source, model_name=args.model_name or _default_model_name(source))
    template_path.write_text(extraction.template, encoding="utf-8")
    report_path = layout.proof_dir / EXTRACTION_REPORT_NAME
    report_path.write_text(json.dumps(extraction.report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    artifacts = write_resolved_artifacts(template_path)
    print(f"3DEFT run directory: {layout.run_dir}")
    print(f"Reviewed DRalgo source copy: {reviewed_source}")
    print(f"3DEFT contract template: {template_path}")
    print(f"Extraction report: {report_path}")
    print(f"Validation: {artifacts['validation']}")
    print(f"User questions: {artifacts['questions']}")
    print(f"Review/status report: {artifacts['review_gate']}")
    print(f"Contract resolved: {extraction.report['ready_for_run_after_extract']}")
    print(f"Ready for DRalgo run: {validate_contract_template(extraction.template).ready_for_dralgo_run}")
    print(f"Resolve next: run the 3DEFT resolve command with --template {template_path}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    template_path = Path(args.template)
    artifacts = write_resolved_artifacts(template_path, require_compile_approval=args.require_compile_approval)
    validation = validate_contract_template(
        template_path.read_text(encoding="utf-8"),
        require_compile_approval=args.require_compile_approval,
    )
    print(f"Resolved contract: {artifacts['resolved']}")
    print(f"Validation: {artifacts['validation']}")
    print(f"User questions: {artifacts['questions']}")
    print(f"Review gate: {artifacts['review_gate']}")
    print(f"Contract resolved: {validation.ready_for_run}")
    print(f"Ready for DRalgo run: {validation.ready_for_dralgo_run}")
    print(f"Ready for compile: {validation.ready_for_compile}")
    _print_issues(validation)
    return 0 if validation.ready_for_run else 2


def _cmd_run(args: argparse.Namespace) -> int:
    output = run_dralgo_template(
        args.template,
        output_path=args.output or None,
        wolframscript_path=args.wolframscript or None,
        install_dralgo_if_missing=args.install_dralgo,
        install_source=args.install_source,
    )
    print(f"DRalgo 3DEFT output: {output}")
    return 0


def _cmd_generate_dralgo(args: argparse.Namespace) -> int:
    result = generate_marked_dralgo_script(args.template, output_path=args.output or None)
    print(f"Generated marked DRalgo wrapper: {result.output_path}")
    print("Capture labels: " + ", ".join(result.capture_labels))
    return 0


def _cmd_merge_output(args: argparse.Namespace) -> int:
    result = merge_dralgo_output_into_contract(args.template, args.dralgo_output, output_path=args.output or None)
    artifacts = write_resolved_artifacts(result.output_path, gate="phasetracer_compile")
    print(f"Merged 3DEFT contract: {result.output_path}")
    print("Updates: " + (", ".join(result.updates) if result.updates else "none"))
    validation = validate_contract_template(result.output_path.read_text(encoding="utf-8"))
    print(f"Review gate: {artifacts['review_gate']}")
    print(f"Contract resolved: {result.ready_for_run}")
    print(f"Ready for DRalgo run: {validation.ready_for_dralgo_run}")
    print(f"Ready for compile: {result.ready_for_compile}")
    print(f"Blocking issue count: {result.blocking_issue_count}")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    if args.backend.casefold() != "phasetracer":
        raise ThreeDeftBlocked("3DEFT v1 supports only --backend phasetracer.")
    template = Path(args.template)
    if template.name != MERGED_CONTRACT_NAME:
        raise ThreeDeftBlocked(
            f"PhaseTracer compile must use {MERGED_CONTRACT_NAME}; "
            "run merge-output, review the merged contract, then approve PhaseTracer compile."
        )
    result = compile_phasetracer_template(template, output_dir=args.output_dir or None)
    print(f"Generated 3DEFT PhaseTracer project: {result.output_dir}")
    print(f"Header: {result.header_path}")
    print(f"Run model source: {result.run_model_path}")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    result = approve_contract_gate(args.template, gate=args.gate, user_approval=args.user_approval, output_path=args.output or None)
    print(f"Approved gate: {result.gate}")
    print(f"Updated contract: {result.output_path}")
    print(f"Approval key: {result.key}")
    return 0


def _cmd_check_model(args: argparse.Namespace) -> int:
    result = configure_build_run_phasetracer_model(
        args.project_dir,
        phasetracer_root=args.phasetracer_root,
        build_dir=args.build_dir or None,
        configure_only=args.configure_only,
        no_run=args.no_run,
    )
    print(f"Configured: {result.configured}")
    print(f"Built: {result.built}")
    print(f"Ran: {result.ran}")
    if result.stdout_preview:
        print("run_model stdout preview:")
        print(result.stdout_preview)
    if result.stderr_preview:
        print("run_model stderr preview:")
        print(result.stderr_preview)
    return 0


def _cmd_compare_mathematica(args: argparse.Namespace) -> int:
    result = compare_mathematica_fixed_v3d(
        args.template,
        args.project_dir,
        wolframscript_path=args.wolframscript or None,
        output_path=args.output or None,
        script_path=args.script or None,
        tolerance=args.tolerance,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"Mathematica fixed-V3D script: {result.script_path}")
    print(f"Mathematica fixed-V3D report: {result.report_path}")
    print(f"Comparison count: {result.comparison_count}")
    print(f"Max abs diff: {result.max_abs_diff}")
    print(f"OK: {result.ok}")
    return 0 if result.ok else 2


def _default_model_name(source: Path) -> str:
    name = source.stem.strip()
    return name or "three_deft_model"


def _cmd_env_check(args: argparse.Namespace) -> int:
    result = check_wolfram_and_dralgo(args.wolframscript or None)
    print(f"wolframscript: {result.wolframscript_path or '<not found>'}")
    print(f"DRalgo available: {result.dralgo_available}")
    if result.stdout.strip():
        _safe_print(result.stdout.strip())
    if result.stderr.strip():
        _safe_print(result.stderr.strip())
    if result.message:
        _safe_print(result.message)
    return 0 if result.ok else 2


def _cmd_install_dralgo(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ThreeDeftBlocked("Refusing to install DRalgo without --yes. Re-run with --yes after confirming network/package installation is allowed.")
    result = install_dralgo(args.wolframscript or None, source=args.source)
    print(f"wolframscript: {result.wolframscript_path or '<not found>'}")
    print(f"DRalgo available: {result.dralgo_available}")
    if result.stdout.strip():
        _safe_print(result.stdout.strip())
    if result.stderr.strip():
        _safe_print(result.stderr.strip())
    if result.message:
        _safe_print(result.message)
    return 0 if result.ok else 2


def _safe_print(text: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(str(text).encode(encoding, errors="replace").decode(encoding, errors="replace"))
    sys.stdout.write("\\n")


def _print_issues(validation) -> None:
    seen_field_keys: set[str] = set()
    for issue in validation.issues:
        if issue.field_key in seen_field_keys:
            continue
        seen_field_keys.add(issue.field_key)
        stage = issue.to_dict().get("stage", "unknown")
        print(f"- [{issue.severity}/{stage}] {issue.field_key}: {issue.message}")


__all__ = [
    "DRALGO_OUTPUT_NAME",
    "GENERATED_DRALGO_SCRIPT_NAME",
    "MERGED_CONTRACT_NAME",
    "REVIEW_GATE_NAME",
    "RESOLVED_CONTRACT_NAME",
    "USER_QUESTIONS_NAME",
    "VALIDATION_NAME",
    "build_parser",
    "main",
]
