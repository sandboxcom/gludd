#!/usr/bin/env python3
"""Mirror a command's combined output to the terminal and a durable log."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def stream_command(command: Sequence[str], log_path: Path) -> int:
    """Run ``command``, flushing each output chunk to stdout and ``log_path``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
            raise RuntimeError("streamed command did not expose stdout")

        while chunk := process.stdout.read(64 * 1024):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            log_file.write(chunk)
            log_file.flush()

        return process.wait()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    return stream_command(args.command, args.log)


if __name__ == "__main__":
    raise SystemExit(main())
