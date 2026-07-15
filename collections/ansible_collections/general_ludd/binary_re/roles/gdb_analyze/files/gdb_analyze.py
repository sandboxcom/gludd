#!/usr/bin/env python3
"""GDB automation generator for the gdb_analyze role.

Generates GDB command scripts for breakpoints, stack traces, register
dumps, and Python-API scripted analysis. Report-only — does not invoke gdb.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

VALID_MODES = ("breakpoint", "stack_trace", "register_dump", "scripted")


def gen_breakpoint(target: str, breakpoints: list[str]) -> dict:
    cmds = ["file {}".format(shlex.quote(target))]
    for bp in breakpoints:
        cmds.append("break {}".format(bp))
    cmds.append("run")
    cmds.append("quit")
    return {"commands": cmds}


def gen_stack_trace(target: str) -> dict:
    cmds = [
        "file {}".format(shlex.quote(target)),
        "set pagination off",
        "catch throw",
        "run",
        "bt full",
        "info frame",
        "quit",
    ]
    return {"commands": cmds}


def gen_register_dump(target: str) -> dict:
    cmds = [
        "file {}".format(shlex.quote(target)),
        "set pagination off",
        "break main",
        "run",
        "info registers",
        "print $pc",
        "print $sp",
        "print $bp",
        "x/16xw $sp",
        "continue",
        "quit",
    ]
    return {"commands": cmds}


_SCRIPTED_TEMPLATE = '''import gdb

gdb.execute("file {target}")
gdb.execute("set pagination off")
gdb.execute("set logging file {log_file}")
gdb.execute("set logging overwrite on")
gdb.execute("set logging redirect on")
gdb.execute("set logging enabled on")

for bp in ["main"]:
    gdb.Breakpoint(bp)

gdb.execute("run")

frames = []
frame = gdb.newest_frame()
while frame is not None:
    frames.append({{
        "name": frame.name(),
        "pc": frame.pc(),
        "older": frame.older() is not None,
    }})
    frame = frame.older()

gdb.write("FRAMES: " + repr(frames) + "\\n")
gdb.execute("info registers")
gdb.execute("quit")
'''


def gen_scripted(target: str) -> dict:
    log_file = "/tmp/gludd-gdb-scripted.log"
    script = _SCRIPTED_TEMPLATE.format(
        target=shlex.quote(target), log_file=log_file
    )
    return {
        "script": script,
        "commands": [
            "file {}".format(shlex.quote(target)),
            "source /tmp/gludd-gdb-script.py",
        ],
        "log_file": log_file,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GDB automation command generator (report-only)"
    )
    parser.add_argument("--target", required=True, help="Target binary path")
    parser.add_argument(
        "--mode", required=True, choices=VALID_MODES,
        help="Analysis mode",
    )
    parser.add_argument(
        "--breakpoints", default="main",
        help="Comma-separated breakpoint symbols (breakpoint mode)",
    )
    parser.add_argument(
        "--output", default="-", help="Output file (default stdout)",
    )
    args = parser.parse_args()

    if args.mode == "breakpoint":
        payload = gen_breakpoint(args.target, args.breakpoints.split(","))
    elif args.mode == "stack_trace":
        payload = gen_stack_trace(args.target)
    elif args.mode == "register_dump":
        payload = gen_register_dump(args.target)
    elif args.mode == "scripted":
        payload = gen_scripted(args.target)
    else:
        print("ERROR: unknown mode {}".format(args.mode), file=sys.stderr)
        sys.exit(2)

    artifact = {
        "target": args.target,
        "mode": args.mode,
        "backend": "report-only",
        **payload,
    }

    text = json.dumps(artifact, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
