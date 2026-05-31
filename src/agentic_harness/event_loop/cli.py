"""Event loop CLI entrypoint (deprecated — use 'hottentot daemon')."""

from __future__ import annotations

import sys
import warnings


def main() -> None:
    warnings.warn(
        "hottentot-loop is deprecated. Use 'hottentot daemon' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    sys.argv = ["hottentot", "daemon", *sys.argv[1:]]
    from agentic_harness.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
