#!/usr/bin/env python3
"""Diagnostic for agent_liveness over/under-counting.

Prints, for the newest task ``.output`` transcripts AND workflow agent jsonl
files: the age (now - mtime), what ``_is_terminal()`` decides, and the ACTUAL
last-line type/subtype/keys. This reveals whether terminal-detection matches the
harness's real completion format — the cause of counting completed agents as
live. Read-only; touches only /tmp + ~/.claude transcript files.
"""
from __future__ import annotations

import glob
import json
import os
import time

import agent_liveness as L  # same scripts/ dir → on sys.path[0] when run directly


def last_line(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4000))
            tail = fh.read()
        for ln in reversed(tail.split(b"\n")):
            s = ln.strip()
            if s:
                return s.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}"
    return ""


def show(label: str, files: list[str]) -> None:
    print(f"--- {label}: {len(files)} files")
    now = time.time()
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:15]:
        age = int(now - os.path.getmtime(f))
        ll = last_line(f)
        try:
            o = json.loads(ll)
            desc = (
                f"type={o.get('type')!r} subtype={o.get('subtype')!r} "
                f"keys={list(o.keys())[:7]}"
            )
        except Exception:  # noqa: BLE001
            desc = "(not-json) " + ll[:90]
        print(f"{age:6d}s term={str(L._is_terminal(f)):5} {os.path.basename(f)[:22]:22} {desc}")


def main() -> int:
    d = L._tasks_dir()
    task_files = glob.glob(d + "/*.output") if d else []
    wf_files = L._workflow_transcript_files()
    show("task .output", task_files)
    show("workflow agent jsonl", wf_files)
    live, total, _ = L.live_count()
    print(f"\nlive_count() => live={live} total={total} window={L.LIVENESS_WINDOW_SEC}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
