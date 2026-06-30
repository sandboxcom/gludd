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
import sqlite3
import sys
import time

# Single fixed window constant — every call reads this identically, eliminating
# inter-call variance caused by the old probe-sleep approach.
#
# WINDOW WIDENED 25s -> 300s (rev 2026-06-24): the 25 s window UNDER-counted live
# agents. A background subagent sitting in a single long LLM call (30-120+ s, no
# tool calls) writes NOTHING to its transcript for the duration, so its mtime aged
# out of the 25 s window and it was counted as DEAD while genuinely running — the
# floor/baseline read "0-2 live" while 5-10 agents were actually working, which is
# why floor enforcement looked perpetually unmet and had to be disabled. The PRIMARY
# liveness signal is terminal-detection (_is_terminal: last line == a "result"
# marker), which excludes COMPLETED agents accurately regardless of mtime. The window
# is therefore only a stale/orphaned-transcript backstop (an agent that died without
# writing a parseable result), for which 300 s is ample. Net: completed agents are
# still excluded immediately (terminal marker), live-but-quiet agents are no longer
# under-counted.
LIVENESS_WINDOW_SEC = float(os.environ.get("GLUDD_LIVENESS_WINDOW_SEC", "300.0"))


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


def _is_agent_transcript(path: str) -> bool:
    """True iff ``path`` is an AGENT JSONL transcript (not a plain-text bash
    background-command ``.output``).

    Bug fixed (2026-06-24): the tasks dir holds BOTH agent transcripts and the
    ``.output`` of every ``run_in_background`` bash command (gate/test/build).
    Those plain-text bash outputs were being counted as live "agents", inflating
    the floor count. An agent transcript is JSONL whose first non-empty line is a
    JSON object carrying agent fields; a bash output is plain text -> excluded.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
        for line in head.split(b"\n"):
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s.decode("utf-8", errors="replace"))
            return isinstance(obj, dict) and bool(
                {"type", "agentId", "message", "parentUuid"} & set(obj.keys())
            )
        return False
    except Exception:
        return False


def _is_terminal(path: str) -> bool:
    """Return True if the transcript's last message means the agent FINISHED.

    Format (verified 2026-06-24): the harness ``.output`` / ``agent-*.jsonl`` has
    NO top-level ``type=="result"`` marker — a transcript ends with the agent's
    final ``type=="assistant"`` message. So the OLD check (type/subtype=="result")
    NEVER matched and terminal-detection was dead, leaving completed agents to
    decay only via the mtime window (the 300s "ghost" over-count).

    New rule: TERMINAL iff the last non-empty line is an ``assistant`` message
    whose content carries NO ``tool_use`` block — i.e. a final text answer with no
    pending tool call. A last line that is assistant-with-tool_use, a user/tool
    result, an unparseable line, or an empty file is treated as NON-terminal
    (assume still running -> never under-count a genuinely live agent).
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            if size == 0:
                return False
            read_bytes = min(16384, size)
            fh.seek(-read_bytes, 2)
            tail = fh.read(read_bytes)
        for line in reversed(tail.split(b"\n")):
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped.decode("utf-8", errors="replace"))
            if not isinstance(obj, dict):
                return False
            # Legacy direct-result markers (harmless if a future format uses them).
            if obj.get("type") == "result" or obj.get("subtype") == "result":
                return True
            if obj.get("type") != "assistant":
                # last event is a user/tool-result or other -> still working.
                return False
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, list):
                # Pending tool call -> the agent is mid-turn, still running.
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        return False
                return True  # assistant text answer, no tool_use -> done
            return True  # string content (pure text answer) -> done
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
        # Exclude plain-text bash background-command .output files (gate/test/build
        # share this dir) — only genuine agent transcripts count toward the floor.
        if not _is_agent_transcript(f):
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


# ============================================================================
# HARNESS-AWARE BACKENDS (claude + opencode)
# ============================================================================
#
# BUG THIS FIXES (2026-06-29): the original probe globbed ONLY Claude Code
# transcript paths (~/.claude/projects/, /private/tmp/claude-<uid>/...). Under
# opencode those paths do not exist, so the probe returned 0 unconditionally —
# every floor/ceiling/delegate check was operating on permanently-zero data.
# The whole multitasking enforcement layer was inert in opencode sessions.
#
# FIX: probe BOTH harnesses and report the max. opencode persists sessions in a
# SQLite DB (~/.local/share/opencode/opencode.db) — there is no `status` column,
# so "live" = subagent session (parent_id IS NOT NULL), not archived, not
# compacting, and updated within the window. The DB is the ONLY signal under
# opencode (no .jsonl transcripts exist for it).

# Cache: the probe runs on every tool call (via the floor plugin). To stay
# under the 500ms budget we cache the result for a few seconds so only one
# actual probe runs per TTL window.
CACHE_TTL_SEC = float(os.environ.get("GLUDD_LIVENESS_CACHE_TTL", "3"))
CACHE_FILE = os.environ.get(
    "GLUDD_LIVENESS_CACHE_FILE", "/tmp/gludd-live-count-cache.json"
)


def _opencode_db_path() -> str | None:
    """Resolve the opencode SQLite DB path.

    Order:
      1. ``OPENCODE_DB_PATH`` env override.
      2. ``~/.local/share/opencode/opencode.db`` (XDG default).

    Returns the path string if it exists on disk, else ``None``.
    """
    override = os.environ.get("OPENCODE_DB_PATH")
    if override:
        return override if os.path.isfile(override) else None
    default = os.path.expanduser("~/.local/share/opencode/opencode.db")
    return default if os.path.isfile(default) else None


def _count_claude_live(window: float = LIVENESS_WINDOW_SEC) -> int:
    """Claude Code backend: count live subagent transcripts via the existing
    filesystem probe. Returns just the live count (int)."""
    live, _total, _tasks = live_count(window=window)
    return live


def _count_opencode_live(window: float = LIVENESS_WINDOW_SEC) -> int:
    """opencode backend: count live subagent sessions in the SQLite DB.

    A session row counts as a LIVE subagent iff:
      - parent_id IS NOT NULL (it's a subagent, not a top-level orchestrator)
      - time_archived IS NULL (not archived)
      - time_compacting IS NULL (not in a compaction job)
      - time_updated > now_ms - window_ms (recently active)

    Fail-open: DB missing / schema unknown / any error → return 0.
    """
    db = _opencode_db_path()
    if not db:
        return 0
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2.0)
    except sqlite3.Error:
        return 0
    try:
        cur = con.execute("PRAGMA table_info(session)")
        cols = {row[1] for row in cur.fetchall()}
        required = {"parent_id", "time_updated", "time_archived", "time_compacting"}
        if not required.issubset(cols):
            # Unknown schema — cannot probe safely.
            return 0
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - int(window * 1000)
        cur = con.execute(
            """
            SELECT COUNT(*) FROM session
            WHERE parent_id IS NOT NULL
              AND time_archived IS NULL
              AND time_compacting IS NULL
              AND time_updated > ?
            """,
            (cutoff,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass


def _detect_harness() -> str:
    """Detect which agent harness we are running under.

    Returns one of:
      - ``"claude"``   — CLAUDE_PROJECT_DIR set, or ~/.claude/projects/ exists.
      - ``"opencode"`` — OPENCODE_SESSION_ID set, or ~/.local/share/opencode/ exists.
      - ``"unknown"``  — neither signal present; caller should probe both.
    """
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return "claude"
    if os.environ.get("OPENCODE_SESSION_ID"):
        return "opencode"
    if os.path.isdir(os.path.expanduser("~/.claude/projects")):
        return "claude"
    if os.path.isdir(os.path.expanduser("~/.local/share/opencode")):
        return "opencode"
    return "unknown"


def _read_cache() -> tuple[float, int] | None:
    """Return (timestamp, count) from the cache file, or None if missing/stale/
    unparseable. Caller decides freshness against CACHE_TTL_SEC."""
    try:
        with open(CACHE_FILE, "r") as fh:
            data = json.load(fh)
        ts = float(data["ts"])
        count = int(data["count"])
        return ts, count
    except Exception:
        return None


def _write_cache(count: int) -> None:
    try:
        with open(CACHE_FILE, "w") as fh:
            json.dump({"ts": time.time(), "count": int(count)}, fh)
    except Exception:
        pass


def _count_live_total(
    window: float = LIVENESS_WINDOW_SEC, use_cache: bool = True
) -> int:
    """Return ``max(claude_count, opencode_count)`` — the live-subagent count
    that drives floor enforcement. Works in either harness.

    When ``use_cache`` is True and the cache is fresh (within CACHE_TTL_SEC),
    returns the cached count without probing. This keeps the per-tool-call cost
    under the 500ms budget.
    """
    if use_cache and CACHE_TTL_SEC > 0:
        cached = _read_cache()
        if cached is not None and (time.time() - cached[0]) < CACHE_TTL_SEC:
            return cached[1]

    claude_n = _count_claude_live(window=window)
    oc_n = _count_opencode_live(window=window)
    total = max(claude_n, oc_n)
    if use_cache:
        _write_cache(total)
    return total


def _count_live_total_debug(
    window: float = LIVENESS_WINDOW_SEC,
) -> tuple[int, int, int, str, bool]:
    """Same as _count_live_total but returns diagnostic detail for --debug.

    Returns ``(total, claude_n, opencode_n, harness, cache_used)``.
    """
    harness = _detect_harness()
    cache_used = False
    if CACHE_TTL_SEC > 0:
        cached = _read_cache()
        if cached is not None and (time.time() - cached[0]) < CACHE_TTL_SEC:
            cache_used = True
            return cached[1], -1, -1, harness, cache_used
    claude_n = _count_claude_live(window=window)
    oc_n = _count_opencode_live(window=window)
    total = max(claude_n, oc_n)
    _write_cache(total)
    return total, claude_n, oc_n, harness, cache_used


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

    debug = "--debug" in argv

    try:
        if debug:
            live, claude_n, oc_n, harness, cached = _count_live_total_debug()
        else:
            live = _count_live_total()
            claude_n = oc_n = None
            harness = None
            cached = None
    except Exception:
        # Fail safe: emit 0 so callers err toward dispatching, never wedge.
        if "--count" in argv:
            print(0)
        else:
            print("[liveness] ERROR determining live agents (failing safe -> 0)")
        return 0

    if "--count" in argv:
        print(live)
    elif debug:
        lines = [
            "[liveness DEBUG]",
            "  harness detected: %s" % harness,
            "  claude backend live count:   %s" % claude_n,
            "  opencode backend live count: %s" % oc_n,
            "  cache used: %s" % cached,
            "  reported (max): %d" % live,
        ]
        print("\n".join(lines))
    else:
        tasks = _tasks_dir()
        print(
            "[liveness] live subagents (window=%.0fs + terminal-detection): "
            "%d live  (dir=%s)"
            % (LIVENESS_WINDOW_SEC, live, tasks)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
