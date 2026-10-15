from __future__ import annotations

import json
import re
from pathlib import Path


FOUR_D_EXCLUSIVE_COMMANDS = {
    "compare-backends",
    "export-sarah",
    "guide",
    "import-sarah",
    "prepare-sarah",
    "approve-sarah",
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


class RoutingError(ValueError):
    """The supplied arguments do not identify a unique workflow."""


def _decide_shared_route(command: str, args: list[str]) -> str:
    template_path = _option_value(args, "--template")
    if template_path:
        engine = _contract_engine(template_path)
        if engine:
            return engine
    if _has_option(args, "--model"):
        return "4d"

    if command == "extract":
        if _has_option(args, "--example"):
            return "3deft"
        input_path = _option_value(args, "--input")
        if input_path and _is_3deft_input_file(input_path):
            return "3deft"
        if _has_option(args, "--model-name") or _has_option(args, "--run-dir"):
            return "3deft"
        return "4d"

    if command == "resolve":
        if _has_option(args, "--require-compile-approval"):
            return "3deft"
        if template_path:
            raise _ambiguous_route(command)
        return "4d"  # Let argparse report missing required options or show help.

    if command == "compile":
        if _option_value(args, "--backend").lower() == "cosmotransitions":
            return "4d"
        if template_path or _has_option(args, "--backend"):
            raise _ambiguous_route(command)
        return "4d"

    if command == "run":
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


def _ambiguous_route(command: str) -> RoutingError:
    return RoutingError(
        f"Cannot determine the workflow for {command!r}. "
        "Specify --ptagent-engine 4d or --ptagent-engine 3deft; "
        "--backend phasetracer selects an output backend, not a workflow."
    )


def _contract_engine(path_text: str) -> str | None:
    path = Path(path_text).expanduser()
    try:
        # Inspect declarations only; do not execute or import either model backend.
        with path.open(encoding="utf-8") as handle:
            text = handle.read(1024 * 1024)
    except (OSError, UnicodeError):
        text = ""
    markers = {
        "4d": "<!-- PTAGENT template_contract: contract_v1 -->",
        "3deft": "<!-- PTAGENT 3DEFT contract_v1 -->",
    }
    schemas = {"ptagent.contract.template.v1": "4d", "ptagent.3deft.contract.v1": "3deft"}
    engines = {engine for engine, marker in markers.items() if marker in text}
    declarations = re.findall(r"^\s*schema:\s*(\S+)\s*$", text, re.MULTILINE)
    declarations += re.findall(r"^\s*\|\s*schema\s*\|\s*([^|]+)\|", text, re.MULTILINE)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        declarations.append(payload.get("schema", ""))
    engines.update(schemas[value.strip()] for value in declarations
                   if isinstance(value, str) and value.strip() in schemas)
    if len(engines) > 1:
        raise RoutingError("Conflicting contract workflow markers. Specify --ptagent-engine 4d or 3deft.")
    if engines:
        return engines.pop()
    if _is_3deft_contract_path(path_text):
        return "3deft"
    if path.name.lower() == "contract_template.md":
        return "4d"
    return None


def _is_3deft_contract_path(path_text: str) -> bool:
    path = Path(path_text)
    lower_name = path.name.lower()
    return (
        "three_deft_contract" in lower_name
        or "_3deft" in lower_name
        or "ptagent.3deft" in lower_name
    )
