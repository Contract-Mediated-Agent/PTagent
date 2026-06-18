from __future__ import annotations

import sys

from ptagent.ptagent_4d.cli import main as four_deft_main
from .routing import decide_engine


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    engine_override, forwarded_args = _extract_engine_override(args)
    command = _first_positional_arg(forwarded_args)

    if not command and any(flag in {"-h", "--help"} for flag in args):
        if engine_override == "3deft":
            from ptagent.ptagent_3deft.cli import main as three_deft_main

            return three_deft_main(["--help"])
        return four_deft_main(["--help"])

    engine = decide_engine(command, forwarded_args, engine_override)
    if engine == "3deft":
        from ptagent.ptagent_3deft.cli import main as three_deft_main

        return three_deft_main(forwarded_args)

    return four_deft_main(forwarded_args)


def _extract_engine_override(args: list[str]) -> tuple[str | None, list[str]]:
    engine_override: str | None = None
    forwarded: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token in {"--ptagent-engine", "--engine"}:
            if i + 1 >= len(args):
                raise SystemExit(f"Missing value for {token}. Expected one of: 4d, 3deft, auto.")
            value = args[i + 1].strip().lower()
            if value not in {"4d", "3deft", "auto"}:
                raise SystemExit(f"Unknown engine value {value!r}. Expected 4d, 3deft, or auto.")
            engine_override = None if value == "auto" else value
            i += 2
            continue

        if token.startswith("--ptagent-engine=") or token.startswith("--engine="):
            value = token.split("=", 1)[1].strip().lower()
            if value not in {"4d", "3deft", "auto"}:
                raise SystemExit(f"Unknown engine value {value!r}. Expected 4d, 3deft, or auto.")
            engine_override = None if value == "auto" else value
            i += 1
            continue

        forwarded.append(token)
        i += 1
    return engine_override, forwarded


def _first_positional_arg(args: list[str]) -> str:
    for token in args:
        if token.startswith("-"):
            continue
        return token
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
