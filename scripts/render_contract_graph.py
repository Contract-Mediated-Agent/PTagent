"""Shared review-graph entrypoint for paper, SARAH and DRalgo contracts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--spec", type=Path, help="Evidence-bound graph JSON; see references/contract_review.md")
    parser.add_argument("--inspect", action="store_true", help="Print current contract, SHA256, and review sections")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--project-root", default="")
    args = parser.parse_args()
    from _bootstrap import ensure_ptagent_backend
    ensure_ptagent_backend(args.project_root)
    from ptagent.review_graph import inspect_template, render_graph
    try:
        template = args.template.expanduser().resolve()
        if args.inspect:
            result = inspect_template(template)
        else:
            if not args.spec:
                parser.error("--spec is required unless --inspect is used")
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            output = args.output_dir or template.parent / "proof_materials" / "contract_graph"
            result = render_graph(template, spec, output.resolve())
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"Contract graph unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
