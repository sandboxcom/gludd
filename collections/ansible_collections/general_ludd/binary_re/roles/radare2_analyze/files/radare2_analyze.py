#!/usr/bin/env python3
"""Radare2 command generator for the radare2_analyze role.

Generates r2pipe command sequences for disassembly, entropy scan,
string search, and CFG analysis. Report-only — does not invoke r2.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

VALID_MODES = ("disassembly", "entropy_scan", "string_search", "cfg_analysis")


def gen_disassembly(target: str, depth: int) -> dict:
    cmds = [
        "aaa",
        "afl",
        "s main",
        "pdf" if depth >= 1 else "pds",
    ]
    if depth >= 2:
        cmds.append("agCd > {}.dot".format(target.replace("/", "_")))
    return {"commands": cmds}


def gen_entropy_scan(target: str) -> dict:
    cmds = [
        "aaa",
        "p=e 100",
        "p=H entropy_section",
        "iS~entropy",
        "/x 00000000",
    ]
    return {"commands": cmds}


def gen_string_search(target: str, regex: str) -> dict:
    cmds = ["iz", "izz"]
    if regex:
        cmds.append("/ {}".format(regex))
        cmds.append("/j {}".format(regex))
    else:
        cmds.append("/ password")
    return {"commands": cmds, "regex": regex or "password"}


def gen_cfg_analysis(target: str) -> dict:
    out_dot = "/tmp/gludd-r2-cfg.dot"
    cmds = [
        "aaa",
        "agCd > {}".format(out_dot),
        "agCj > /tmp/gludd-r2-cfg.json",
        "agl > /tmp/gludd-r2-cfg-callgraph.dot",
    ]
    return {"commands": cmds, "dot_file": out_dot}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Radare2 command generator (report-only)"
    )
    parser.add_argument("--target", required=True, help="Target binary path")
    parser.add_argument("--mode", required=True, choices=VALID_MODES)
    parser.add_argument("--depth", type=int, default=1, help="Disassembly depth")
    parser.add_argument("--string-regex", default="", help="String search regex")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    if args.mode == "disassembly":
        payload = gen_disassembly(args.target, args.depth)
    elif args.mode == "entropy_scan":
        payload = gen_entropy_scan(args.target)
    elif args.mode == "string_search":
        payload = gen_string_search(args.target, args.string_regex)
    elif args.mode == "cfg_analysis":
        payload = gen_cfg_analysis(args.target)
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
