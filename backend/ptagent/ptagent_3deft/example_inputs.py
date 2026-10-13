"""Resolve the documented xSM example without replacing explicit user files."""

from __future__ import annotations

from pathlib import Path

from .contract import ThreeDeftBlocked


def resolve_dralgo_source(input_path: str = "", *, example: str = "") -> tuple[Path, str]:
    if input_path and example:
        raise ThreeDeftBlocked("Choose either --input or --example, not both.")
    if input_path:
        source = Path(input_path).expanduser()
        if source.is_file():
            return source.resolve(), "user_file"
        if source.exists() or input_path not in {"xsm_test.m", "./xsm_test.m"}:
            raise ThreeDeftBlocked(f"input is not an existing file: {source}. No example was substituted.")
    elif example != "xsm":
        raise ThreeDeftBlocked("Provide --input for your model, or --example xsm for the bundled 3DEFT example.")

    bundled = Path(__file__).resolve().parents[3] / "assets" / "examples" / "xsm_test.m"
    if not bundled.is_file():
        raise ThreeDeftBlocked(
            "The installed skill is missing assets/examples/xsm_test.m. Restore that file from "
            "Contract-Mediated-Agent/PTagent or reinstall the complete skill; do not invent a replacement model."
        )
    return bundled, "bundled_example"
