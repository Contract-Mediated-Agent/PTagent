from __future__ import annotations

import argparse
import sys

sys.dont_write_bytecode = True


def main() -> int:
    from _bootstrap import ensure_ptagent_backend

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--project-root", default="")
    args, remaining = parser.parse_known_args()
    try:
        ensure_ptagent_backend(args.project_root)
    except (FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))

    from ptagent.interface.facade import main as ptagent_main

    return ptagent_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
