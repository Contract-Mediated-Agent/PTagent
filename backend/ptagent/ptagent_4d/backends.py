from __future__ import annotations


SUPPORTED_COMPILE_BACKENDS = ("cosmotransitions", "phasetracer")
COMPARE_BACKENDS_SENTINEL = "compare-backends"


def normalize_compile_backend(value: str | None) -> str:
    return (value or "").strip().lower()


def is_supported_compile_backend(value: str | None) -> bool:
    return normalize_compile_backend(value) in SUPPORTED_COMPILE_BACKENDS


def supported_backends_text() -> str:
    return ", ".join(SUPPORTED_COMPILE_BACKENDS)


def supported_backend_options_text() -> str:
    return ", ".join(f"`{name}`" for name in SUPPORTED_COMPILE_BACKENDS)
