#!/usr/bin/env python3
"""Ghidra headless invocation generator for the ghidra_analyze role.

Builds analyzeHeadless command lines and GhidraScript postscripts for
auto-analysis, scripted export, and function-signature extraction.
Report-only — does not invoke analyzeHeadless.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

VALID_MODES = ("headless_analysis", "scripted_export", "function_signature")


def _launcher(ghidra_path: str) -> str:
    return "{}/support/analyzeHeadless".format(ghidra_path.rstrip("/"))


def gen_headless(target: str, ghidra_path: str, project_dir: str) -> dict:
    launcher = _launcher(ghidra_path)
    invocation = f"{shlex.quote(launcher)} {shlex.quote(project_dir)} gludd_proj -import {shlex.quote(target)} -overwrite -deleteProject"
    return {"invocation": invocation, "project_dir": project_dir}


_EXPORT_POSTSCRIPT = """import ghidra.app.decompiler.DecompInterface as DecompInterface
import ghidra.util.task.TaskMonitor as TaskMonitor

decomp = DecompInterface()
decomp.openProgram(currentProgram)
fm = currentProgram.getFunctionManager()
for fn in fm.getFunctions(True):
    print("FUNC: {} @ {}".format(fn.getName(), fn.getEntryPoint()))
    res = decomp.decompileFunction(fn, 60, TaskMonitor.DUMMY)
    if res is not None and res.getDecompiledFunction() is not None:
        print(res.getDecompiledFunction().getC())
decomp.dispose()
"""


def gen_scripted_export(target: str, ghidra_path: str, project_dir: str) -> dict:
    launcher = _launcher(ghidra_path)
    postscript_path = "/tmp/gludd-ghidra-decompile.py"
    invocation = (
        f"{shlex.quote(launcher)} {shlex.quote(project_dir)} gludd_proj -import {shlex.quote(target)} -overwrite "
        f"-deleteProject -postScript {shlex.quote(postscript_path)}"
    )
    return {
        "invocation": invocation,
        "postscript": _EXPORT_POSTSCRIPT,
        "postscript_path": postscript_path,
    }


_SIG_POSTSCRIPT = """import ghidra.program.model.listing.Function as Function

fm = currentProgram.getFunctionManager()
for fn in fm.getFunctions(True):
    sig = fn.getSignature().getPrototypeString(True, True)
    print("SIGNATURE: {} :: {}".format(fn.getName(), sig))
    cmd = ghidra.app.cmd.function.CaptureFunctionDataCmd(fn.getEntryPoint())
    state = ghidra.framework.plugintool.util.PluginUtils.getCurrentState()
"""


def gen_function_signature(target: str, ghidra_path: str, project_dir: str) -> dict:
    launcher = _launcher(ghidra_path)
    postscript_path = "/tmp/gludd-ghidra-fnsig.py"
    invocation = (
        f"{shlex.quote(launcher)} {shlex.quote(project_dir)} gludd_proj -import {shlex.quote(target)} -overwrite "
        f"-deleteProject -postScript {shlex.quote(postscript_path)}"
    )
    return {
        "invocation": invocation,
        "postscript": _SIG_POSTSCRIPT,
        "postscript_path": postscript_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ghidra headless invocation generator (report-only)"
    )
    parser.add_argument("--target", required=True, help="Target binary path")
    parser.add_argument("--mode", required=True, choices=VALID_MODES)
    parser.add_argument("--ghidra-path", default="/opt/ghidra")
    parser.add_argument("--project-dir", default="/tmp/gludd-ghidra-proj")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    if args.mode == "headless_analysis":
        payload = gen_headless(args.target, args.ghidra_path, args.project_dir)
    elif args.mode == "scripted_export":
        payload = gen_scripted_export(args.target, args.ghidra_path, args.project_dir)
    elif args.mode == "function_signature":
        payload = gen_function_signature(args.target, args.ghidra_path, args.project_dir)
    else:
        print(f"ERROR: unknown mode {args.mode}", file=sys.stderr)
        sys.exit(2)

    artifact = {
        "target": args.target,
        "mode": args.mode,
        "backend": "report-only",
        "ghidra_path": args.ghidra_path,
        **payload,
    }

    text = json.dumps(artifact, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
