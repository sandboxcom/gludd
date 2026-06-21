#!/usr/bin/env python3
"""Ground-truth live-subagent counter — the single source of truth shared by the
orchestrator's floor planner (scripts/floor_planner.py) and the agent-floor Stop
hook, so both agree on how many subagents are actually running RIGHT NOW.

WHY THIS EXISTS (the bug it fixes):
    The original heuristic counted every task .output transcript whose mtime was
    within the last ~90s. But a subagent gets a FINAL write to its transcript at
    the moment it COMPLETES — so a burst of completions all landed inside that
    window and were counted as "live". The orchestrator was told 11 agents were
    running when only 3 were. A counter that OVER-counts is worse than none: it
    hides a floor breach (reports "healthy" while the floor is actually breached)
    — the precise failure the agent-floor guardrail exists to prevent.

DETERMINISM FIX (the wobble bug this revision addresses):
    The prior implementation sampled mtime before and after a PROBE sleep, so two
    rapid back-to-back ``--count`` calls could return 6 / 13 / 18 / 21 depending
    on whether the sleep straddles an active write. Every hook that calls
    ``--count`` got a slightly different wall-clock slice, making the floor signal
    incoherent.

    Solution: eliminate the probe sleep entirely. Use a SINGLE fixed wall-clock
    window (``GLUDD_LIVENESS_WINDOW_SEC``, default 25 s) evaluated identically
    at every call site. A transcript is LIVE if its mtime falls within that
    window AND it does not end with a terminal result marker. No sleep → no
    sampling variance → two consecutive calls at the same instant return the
    same count.

DUAL-FILTER APPROACH (over-count fix):
    A recently-completed agent's transcript has a fresh mtime (the final write
    happened moments ago), so a window-only filter still counts it as live. This
    revision adds terminal-detection as the primary filter:

      1. TERMINAL DETECTION (primary): read the last non-empty line of each
         ``.output`` JSONL file. If it is valid JSON whose ``type`` field equals
         ``"result"`` OR whose ``subtype`` field equals ``"result"``, the agent
         has completed — exclude it from the live count regardless of mtime.
         Fail-open: if the last line cannot be parsed or the file is empty,
         assume the agent is still running (never under-count a live agent).

      2. SHORT WINDOW (secondary/fallback): ``GLUDD_LIVENESS_WINDOW_SEC``
         (default 25 s). A genuinely running agent streams tool-calls every few
         seconds, so a 25 s window catches it. A completed agent whose terminal
         marker could not be parsed (e.g. partial final write) decays out of the
         window within 25 s anyway. This provides defense-in-depth.

    A transcript is counted LIVE iff:
        mtime >= (now - window)  AND  NOT _is_terminal(path)

BIAS (floor STABILITY):
    The 25 s window is narrow enough to exclude completed agents within half a
    minute, without under-counting a live agent that is temporarily quiet (e.g.
    waiting for a long LLM call). Terminal detection catches completions
    immediately, before the window even expires.

Format-independent and hook-independent: depends only on filesystem mtimes and
last-line JSONL content.
Fail-safe by construction — any error yields 0 / exit 0 (callers treat 0 as
"could not determine, dispatch toward the floor" rather than wedging).

TESTABILITY:
    The transcript dir is configurable via GLUDD_TASKS_DIR so the counter can be
    unit-tested against a temp dir. FLOOR_LIVE_OVERRIDE is a test seam that skips
    probing entirely (print the override and exit). GLUDD_LIVENESS_WINDOW_SEC is
    env-tunable so tests can pass a short window without patching module globals.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

# Single fixed window constant — every call reads this identically, eliminating
# inter-call variance caused by the old probe-sleep approach. Default 25 s: a
# live agent streams output every few seconds so 25 s catches it; a completed
# agent whose terminal marker is unparseable decays out quickly.
LIVENESS_WINDOW_SEC = float(os.environ.get("GLUDD_LIVENESS_WINDOW_SEC", "25.0"))


def _tasks_dir() -> str | None:
    """Resolve the transcript dir holding per-agent ``*.output`` files.

    Resolution order:
      1. ``GLUDD_TASKS_DIR`` env override (used by tests against a temp dir).
      2. The newest claude session's ``tasks/`` dir for this repo+uid.

    Returns the directory path, or ``None`` if none can be resolved.
    """
    override = os.environ.get("GLUDD_TASKS_DIR")
    if override:
        return override if os.path.isdir(override) else None

    base = "/private/tmp/claude-%d/-Users-shawnwilson-gludd" % os.getuid()
    sessions = sorted(glob.glob(base + "/*/"), key=os.path.getmtime, reverse=True)
    return next((s + "tasks" for s in sessions if os.path.isdir(s + "tasks")), None)


def _is_terminal(path: str) -> bool:
    """Return True if the transcript at ``path`` ends with a completion marker.

    Reads the last ~512 bytes of the file to find the last non-empty line, then
    tries to parse it as JSON. Returns True (completed) if:
      - ``type == "result"``  (direct result object), OR
      - ``subtype == "result"``  (system envelope around a result)

    Returns False on any exception (fail-open: assume still running if we cannot
    determine the terminal state). An empty file also returns False (agent just
    started, no content yet).
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return False
            read_bytes = min(512, size)
            fh.seek(-read_bytes, 2)
            tail = fh.read(read_bytes)
        lines = tail.split(b"\n")
        # Walk backwards to find last non-empty line.
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped.decode("utf-8", errors="replace"))
            if not isinstance(obj, dict):
                return False
            if obj.get("type") == "result":
                return True
            if obj.get("subtype") == "result":
                return True
            return False
        return False
    except Exception:
        return False


def _workflow_transcript_files() -> list[str]:
    """Return Workflow-subagent transcript file paths to include in liveness counting.

    Workflow subagents (the parallel pools spawned by a Workflow run) write their
    per-agent transcripts to a DIFFERENT tree than Agent-tool tasks — the
    agent-floor probe used to miss them entirely and report "0 live" while a full
    Workflow pool was running, falsely tripping the floor hook. This discovers them.

    VERIFIED on-disk layout (2026-06-21, via `make discover-workflow-transcripts`):

        ~/.claude/projects/-Users-shawnwilson-gludd/<session>/subagents/workflows/<runid>/agent-<id>.jsonl

    i.e. each running Workflow agent has an ``agent-<id>.jsonl`` transcript under a
    per-run ``workflows/<runid>/`` dir. (Siblings ``agent-<id>.meta.json`` and a
    per-run ``journal.jsonl`` are NOT agent transcripts — the ``agent-*.jsonl``
    glob deliberately excludes both so a workflow is not over-counted.)

    Resolution:
      1. ``GLUDD_WORKFLOW_DIRS`` (colon-separated) test override — each dir is
         scanned non-recursively for ``*.jsonl`` and ``*.output`` files (the test
         fixtures use those names directly).
      2. Otherwise the verified ``~/.claude/projects/.../agent-*.jsonl`` glob, plus
         a defensive ``/private/tmp/claude-<uid>/.../agent-*.jsonl`` fallback in
         case a future harness version relocates the tree there (currently empty —
         confirmed no workflow transcripts live under /private/tmp today).

    Returns an empty list on any error (fail-open).
    """
    try:
        override = os.environ.get("GLUDD_WORKFLOW_DIRS", "")
        if override:
            results: list[str] = []
            for d in override.split(":"):
                d = d.strip()
                if not d:
                    continue
                results.extend(glob.glob(os.path.join(d, "*.jsonl")))
                results.extend(glob.glob(os.path.join(d, "*.output")))
            return results

        uid = os.getuid()
        # Match the VERIFIED real filename (agent-*.jsonl) so journal.jsonl and
        # *.meta.json siblings are excluded. recursive=True lets ** span the
        # workflows/<runid>/ nesting level.
        patterns = [
            os.path.expanduser(
                "~/.claude/projects/-Users-shawnwilson-gludd/*/subagents/workflows/**/agent-*.jsonl"
            ),
            "/private/tmp/claude-%d/-Users-shawnwilson-gludd/*/subagents/workflows/**/agent-*.jsonl"
            % uid,
        ]
        results = []
        for pattern in patterns:
            try:
                results.extend(glob.glob(pattern, recursive=True))
            except Exception:
                pass
        return results
    except Exception:
        return []


def live_count(
    window: float = LIVENESS_WINDOW_SEC,
) -> tuple[int, int, str | None]:
    """Return ``(live, total, tasks_dir)``.

    live  = transcripts whose mtime falls within the last ``window`` seconds
            AND whose last line does NOT indicate a terminal result. Uses a
            single ``time.time()`` snapshot so every transcript is evaluated
            against the SAME clock value — no probe sleep, no drift.
            Both Agent-task transcripts AND Workflow-subagent transcripts are
            counted.
    total = all transcripts found (tasks dir ``*.output`` + workflow files).
    tasks = the resolved tasks dir (or ``None`` if unresolved).

    A transcript is live iff BOTH conditions hold:
        (1) mtime >= now - window  (recently active)
        (2) NOT _is_terminal(path)  (no terminal result marker on last line)

    Terminal detection is fail-open: an unparseable last line is treated as
    non-terminal (agent assumed running). The short window provides defense-in-
    depth for completed agents whose terminal marker could not be parsed.
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    # Also include workflow-subagent transcripts so the floor hook never
    # reports "0 live" while a Workflow-based parallel pool is running.
    wf_files = _workflow_transcript_files()

    # Single clock snapshot — all files evaluated against the same ``now``.
    now = time.time()
    cutoff = now - window

    live = 0
    total = 0
    for f in fs:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            # File vanished (agent dir cleaned up): skip it.
            continue
        total += 1
        if mtime >= cutoff and not _is_terminal(f):
            live += 1

    # Apply the same dual-filter to workflow transcripts.
    for f in wf_files:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            continue
        total += 1
        if mtime >= cutoff and not _is_terminal(f):
            live += 1

    return live, total, tasks


def main(argv: list[str]) -> int:
    # TEST-ONLY seam: if FLOOR_LIVE_OVERRIDE is an all-digit string, skip the
    # filesystem probe entirely and print that integer directly. Makes callers
    # deterministically testable without real task-transcript files. NEVER set
    # this in production; it is exclusively for unit/integration tests.
    override = os.environ.get("FLOOR_LIVE_OVERRIDE", "")
    if override and override.isdigit():
        if "--count" in argv:
            print(int(override))
        else:
            print("[liveness] FLOOR_LIVE_OVERRIDE=%s (test seam active)" % override)
        return 0

    try:
        live, total, tasks = live_count()
    except Exception:
        # Fail safe: emit 0 so callers err toward dispatching, never wedge.
        if "--count" in argv:
            print(0)
        else:
            print("[liveness] ERROR determining live agents (failing safe -> 0)")
        return 0

    if "--count" in argv:
        print(live)
    else:
        print(
            "[liveness] live subagents (window=%.0fs + terminal-detection): "
            "%d live  of %d total transcripts  (dir=%s)"
            % (LIVENESS_WINDOW_SEC, live, total, tasks)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
