"""CLI entrypoint for the agentic harness."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentic-harness",
        description="Agentic Harness - autonomous coding system",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="Show version")
    sub.add_parser("worker", help="Start the worker service")
    sub.add_parser("loop", help="Start the event loop")

    args = parser.parse_args()

    if args.command == "version":
        from agentic_harness import __version__
        print(f"agentic-harness {__version__}")
    elif args.command == "worker":
        from agentic_harness.worker.cli import main as worker_main
        worker_main()
    elif args.command == "loop":
        from agentic_harness.event_loop.cli import main as loop_main
        loop_main()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
