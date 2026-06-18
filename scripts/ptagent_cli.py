from __future__ import annotations

import sys

sys.dont_write_bytecode = True


def main() -> int:
    from _bootstrap import ensure_ptagent_backend

    ensure_ptagent_backend("")

    from ptagent.interface.facade import main as ptagent_main

    return ptagent_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
