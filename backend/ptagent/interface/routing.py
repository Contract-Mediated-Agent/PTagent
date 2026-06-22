from __future__ import annotations

from pathlib import Path


FOUR_D_EXCLUSIVE_COMMANDS = {
    "compare-backends",
    "guide",
    "init",
    "validate",
}

THREE_DEFT_EXCLUSIVE_COMMANDS = {
    "approve",
    "compare-mathematica",
    "env-check",
    "generate-dralgo",
    "install-dralgo",
    "merge-output",
}


def decide_engine(command: str, raw_args: list[str], engine_override: str | None = None) -> str:
    """Return the engine bucket for a top-level `ptagent` invocation."""
    normalized = (engine_override or "").strip().lower()
    if normalized in {"4d", "3deft"}:
        return normalized

    if command in FOUR_D_EXCLUSIVE_COMMANDS:
        return "4d"
    if command in THREE_DEFT_EXCLUSIVE_COMMANDS:
        return "3deft"

    if not command:
        return "4d"

    if command in {"extract", "resolve", "compile", "run", "check-model"}:
        return _decide_shared_route(command, raw_args)

    return "4d"


def _decide_shared_route(command: str, args: list[str]) -> str:
    if _has_option(args, "--backend"):
        return "4d"
    if _has_option(args, "--model"):
        return "4d"

    if command == "extract":
        input_path = _option_value(args, "--input")
        if input_path and _is_3deft_input_file(input_path):
            return "3deft"
        if _has_option(args, "--model-name") or _has_option(args, "--run-dir"):
            return "3deft"
        return "4d"

    if command == "resolve":
        template_path = _option_value(args, "--template")
        if template_path and _is_3deft_contract_path(template_path):
            return "3deft"
        if _has_option(args, "--require-compile-approval"):
            return "3deft"
        return "4d"

    if command == "compile":
        template_path = _option_value(args, "--template")
        if template_path and _is_3deft_contract_path(template_path):
            return "3deft"
        return "4d"

    if command == "run":
        template_path = _option_value(args, "--template")
        if template_path and _is_3deft_contract_path(template_path):
            return "3deft"
        if (
            _has_option(args, "--wolframscript")
            or _has_option(args, "--install-dralgo")
            or _has_option(args, "--install-source")
        ):
            return "3deft"
        if _has_option(args, "--template"):
            return "3deft"
        return "4d"

    if command == "check-model":
        if _has_option(args, "--project-dir") or _has_option(args, "--phasetracer-root"):
            return "3deft"
        return "4d"

    return "4d"


def _has_option(args: list[str], option: str) -> bool:
    for token in args:
        if token == option or token.startswith(f"{option}="):
            return True
    return False


def _option_value(args: list[str], option: str) -> str:
    for index, token in enumerate(args):
        if token == option:
            if index + 1 < len(args):
                return args[index + 1]
            continue
        if token.startswith(f"{option}="):
            return token.split("=", 1)[1]
    return ""


def _is_3deft_input_file(path_text: str) -> bool:
    return Path(path_text).suffix.lower() in {".m", ".wl"}

def _is_3deft_contract_path(path_text: str) -> bool:
    path = Path(path_text)
    lower_name = path.name.lower()
    return (
        "three_deft_contract" in lower_name
        or "_3deft" in lower_name
        or "ptagent.3deft" in lower_name
    )
