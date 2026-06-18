from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a locally compiled PTagent CosmoTransitions model.")
    parser.add_argument("--model", required=True, help="Compiled Python model file.")
    parser.add_argument("--task-dir", default="", help="PTagent task artifact directory containing contract_template.md.")
    parser.add_argument("--project-root", default="", help="Optional PTagent repo root.")
    parser.add_argument("--json-out", default="", help="Optional report JSON path.")
    args = parser.parse_args()

    from _bootstrap import ensure_ptagent_backend

    ensure_ptagent_backend(args.project_root)

    from ptagent import get_settings
    from ptagent.ptagent_4d.config import ConfigurationError, require_configured_runtime_python
    from ptagent.ptagent_4d.runner import import_check
    from ptagent.ptagent_4d.template_contract import validate_contract_template
    settings = get_settings()
    try:
        require_configured_runtime_python(settings)
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    model_path = Path(args.model).expanduser().resolve()
    if not args.task_dir:
        raise SystemExit("check_model requires --task-dir")
    task_dir = Path(args.task_dir).expanduser().resolve()
    template_path = task_dir / "contract_template.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    validation = validate_contract_template(template, review_required=True) if template else None
    ok, message = import_check(model_path, settings)
    template_hash = _sha256_text(template) if template else ""
    model_template_hash = _model_constant(model_path, "PTAGENT_TEMPLATE_SHA256")
    hash_status = _hash_status(template_hash, model_template_hash)

    report: dict[str, Any] = {
        "model": str(model_path),
        "task_dir": str(task_dir),
        "contract_template": str(template_path) if template_path.exists() else "",
        "template_sha256": template_hash,
        "model_template_sha256": model_template_hash,
        "model_template_hash_status": hash_status,
        "contract_ok": bool(validation and validation.ok),
        "contract_issues": [issue.to_dict() for issue in validation.issues] if validation else [],
        "import_check_ok": bool(ok),
        "import_check_message": message,
    }
    if args.json_out:
        Path(args.json_out).expanduser().resolve().write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["contract_ok"] and ok and hash_status == "match" else 2


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_constant(model_path: Path, name: str) -> str:
    if not model_path.exists():
        return ""
    text = model_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{re.escape(name)}\s*=\s*['\"]([0-9a-f]{{64}})['\"]", text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def _hash_status(template_hash: str, model_hash: str) -> str:
    if not template_hash:
        return "no_template"
    if not model_hash:
        return "missing_model_hash"
    return "match" if template_hash == model_hash else "stale_model"


if __name__ == "__main__":
    raise SystemExit(main())
