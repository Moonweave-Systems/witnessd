"""Run the witnessd-hosted ORRO surface as ``python3 -m orro``."""

from __future__ import annotations

import sys

from witnessd.__main__ import main as witnessd_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        return witnessd_main(["--help"])
    return witnessd_main(["orro", *args])


if __name__ == "__main__":
    sys.exit(main())
