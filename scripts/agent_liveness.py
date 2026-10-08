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

    Solution: eliminate the probe sleep entirely. Liveness is determined SOLELY
    by terminal-detection (see below). No sleep → no sampling variance → two
    consecutive calls at the same instant return the same count.

UNDERCOUNT FIX (the idle-agent bug this revision addresses):
    The prior dual-filter required BOTH a fresh mtime AND no terminal marker.
    An alive-but-idle agent (waiting on a long LLM call, no writes for >25s)
    failed the mtime gate and was silently dropped from the live count even though
    it had no terminal marker. An agent that is RUNNING but QUIET was being
    reported as not running — an under-count that hides a floor breach in the
    opposite direction.

TERMINAL-DETECTION ONLY (current approach):
    A transcript is counted LIVE iff it does NOT end with a terminal result marker:

      TERMINAL DETECTION: read the last non-empty line of each ``.output`` JSONL
      file. If it is valid JSON whose ``type`` field equals ``"result"`` OR whose
      ``subtype`` field equals ``"result"``, the agent has completed — exclude it
      from the live count. Fail-open: if the last line cannot be parsed or the
      file is empty, assume the agent is still running (never under-count a live
      agent).

    The mtime / window gate has been removed entirely. An alive-but-idle agent is
    correctly counted live regardless of how long it has been quiet.

Format-independent and hook-independent: depends only on last-line JSONL content.
Fail-safe by construction — any error yields 0 / exit 0 (callers treat 0 as
"could not determine, dispatch toward the floor" rather than wedging).

TESTABILITY:
    The transcript dir is configurable via GLUDD_TASKS_DIR so the counter can be
    unit-tested against a temp dir. FLOOR_LIVE_OVERRIDE is a test seam that skips
    probing entirely (print the override and exit). GLUDD_LIVENESS_WINDOW_SEC is
    env-tunable so tests can pass a short window without patching module globals.
    GLUDD_SESSION_ID (added 2026-07-10, defect #1/#3 fix) lets a caller that
    knows the current session id (e.g. a hook reading it from the tool-call
    payload) resolve the tasks dir and workflow transcripts deterministically
    instead of relying on the mtime-activity heuristic. GLUDD_LIVENESS_CACHE_FILE
    is a hard override for the per-session cache path (see _cache_file_for);
    without it the cache file is derived from a hash of the resolved tasks dir
    so concurrent sessions never share/clobber each other's cached count
    (defect #2 fix).
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
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


def _dir_activity_mtime(path: str) -> float | None:
    """Return the max mtime among the files directly inside ``path``, or
    ``None`` if the directory is empty/unreadable.

    Ranking session dirs by this INSTEAD OF the containing dir's own mtime is
    the fix for defect #1 (2026-07-10): a brand-new near-empty session dir
    (created a moment ago, 0-1 small files) can sort ABOVE an older session dir
    that has 10 actively-streaming transcripts, because dir mtime reflects
    entry creation/removal, not the content writes happening inside the files.
    Ranking by the newest FILE mtime inside ``tasks/`` directly measures actual
    activity instead of directory-entry churn.
    """
    try:
        names = os.listdir(path)
    except OSError:
        return None
    best: float | None = None
    for name in names:
        try:
            m = os.path.getmtime(os.path.join(path, name))
        except OSError:
            continue
        if best is None or m > best:
            best = m
    return best


def _claude_project_slug() -> str:
    """Return Claude's filesystem-safe slug for this checkout."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if not project_dir:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.abspath(project_dir).replace("\\", "-").replace("/", "-")


def _claude_sessions_base() -> str:
    """Return this project's Claude transcript-session directory.

    ``GLUDD_CLAUDE_SESSIONS_BASE`` is an explicit isolation/test override.
    Otherwise use Claude's platform temp root and derive the project slug from
    ``CLAUDE_PROJECT_DIR`` (or this checkout), avoiding the old macOS-only
    ``/private/tmp`` and user-specific project path.
    """
    override = os.environ.get("GLUDD_CLAUDE_SESSIONS_BASE", "").strip()
    if override:
        return override

    temp_root = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    return os.path.join(
        temp_root,
        f"claude-{os.getuid()}",
        _claude_project_slug(),
    )


def _tasks_dir() -> str | None:
    """Resolve the transcript dir holding per-agent ``*.output`` files.

    Resolution order:
      1. ``GLUDD_TASKS_DIR`` env override (used by tests against a temp dir).
      2. ``GLUDD_SESSION_ID`` env override (set by hooks that have the current
         session id from the hook payload, e.g. force_delegate_pretool.sh) --
         deterministic: this repo+uid+session's ``tasks/`` dir wins outright
         when it exists, no ranking heuristic needed.
      3. The claude session whose ``tasks/`` dir has the most recent ACTIVITY
         (max mtime of the files inside ``tasks/``, not the dir's own mtime --
         see ``_dir_activity_mtime``), falling back to the dir's own mtime only
         when ``tasks/`` is empty or unreadable.

    Returns the directory path, or ``None`` if none can be resolved.

    BUG FIXED (2026-07-10, defect #1): the prior implementation ranked
    candidate session dirs by ``os.path.getmtime`` of the SESSION DIR itself.
    A brand-new session dir (created moments ago, near-empty ``tasks/``) sorts
    above an older session dir with a full, actively-streaming fleet, because
    appends to files inside ``tasks/`` don't bump the session dir's own mtime.
    This silently hijacked the live count to ~0 mid-fleet. Ranking by the
    ``tasks/`` dir's actual file activity fixes it.
    """
    override = os.environ.get("GLUDD_TASKS_DIR")
    if override:
        return override if os.path.isdir(override) else None

    base = _claude_sessions_base()

    session_id = os.environ.get("GLUDD_SESSION_ID", "").strip()
    if session_id:
        candidate = os.path.join(base, session_id, "tasks")
        if os.path.isdir(candidate):
            return candidate
        # Declared session has no tasks dir (yet, or never will) -> fall
        # through to the activity-ranking heuristic below rather than
        # returning None outright (fail toward still finding a usable dir).

    sessions = glob.glob(base + "/*/")
    candidates: list[tuple[float, str]] = []
    for s in sessions:
        tasks = s + "tasks"
        if not os.path.isdir(tasks):
            continue
        activity = _dir_activity_mtime(tasks)
        if activity is None:
            try:
                activity = os.path.getmtime(s)
            except OSError:
                activity = 0.0
        candidates.append((activity, tasks))

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def _is_agent_transcript(path: str) -> bool:
    """True iff ``path`` is an AGENT JSONL transcript (not a plain-text bash
    background-command ``.output``).

    Bug fixed (2026-06-24): the tasks dir holds BOTH agent transcripts and the
    ``.output`` of every ``run_in_background`` bash command (gate/test/build).
    Those plain-text bash outputs were being counted as live "agents", inflating
    the floor count. An agent transcript is JSONL whose first non-empty line is a
    JSON object carrying agent fields; a bash output is plain text -> excluded.

    BUG FIXED (2026-07-10, defect #5): the original check decided on LINE 1
    ALONE -- if the very first non-empty line parsed as a JSON dict lacking the
    marker fields (e.g. a leading metadata/system line before the first real
    agent message), the transcript was misclassified as non-agent and dropped
    entirely, even though later lines clearly carry ``agentId``/``parentUuid``.
    Fix: scan up to the first 5 non-empty lines; a match on ANY of them counts
    the file as an agent transcript. A line that fails to parse as JSON is
    skipped (not treated as a verdict) so a single malformed/partial line does
    not short-circuit the scan.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
        checked = 0
        for line in head.split(b"\n"):
            s = line.strip()
            if not s:
                continue
            checked += 1
            try:
                obj = json.loads(s.decode("utf-8", errors="replace"))
            except Exception:
                obj = None
            if isinstance(obj, dict) and bool({"type", "agentId", "message", "parentUuid"} & set(obj.keys())):
                return True
            if checked >= 5:
                break
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
                return not any(isinstance(part, dict) and part.get("type") == "tool_use" for part in content)
            return True  # string content (pure text answer) -> done
        return False
    except Exception:
        return False


def _workflow_transcript_files(window: float = LIVENESS_WINDOW_SEC) -> list[str]:
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

    BUG FIXED (2026-07-10, defect #3): the prior implementation globbed
    ``.../<session>/subagents/workflows/**/agent-*.jsonl`` across ALL session
    dirs under ``~/.claude/projects/-Users-shawnwilson-gludd/`` unconditionally
    — every stale/completed session tree left over from earlier work fed
    transcripts into every subsequent liveness count, permanently inflating
    ``total`` (and, for any transcript whose terminal-detection false-negatived,
    ``live`` too). Fix: per SESSION dir, only include its workflow transcripts
    when EITHER (a) ``GLUDD_SESSION_ID`` is set and matches that session dir's
    name exactly (deterministic — the hook told us which session is live), OR
    (b) the newest agent-transcript mtime within that session's workflow tree
    falls inside the liveness ``window`` (recency fallback, reusing the same
    check ``live_count`` applies to individual files). Session dirs whose
    workflow tree is entirely stale are excluded outright.

    Resolution:
      1. ``GLUDD_WORKFLOW_DIRS`` (colon-separated) test override — each dir is
         scanned non-recursively for ``*.jsonl`` and ``*.output`` files (the test
         fixtures use those names directly). Back-compat: NOT recency-filtered,
         matching the pre-existing test seam contract (callers that use this
         override are asserting file presence directly).
      2. Otherwise: enumerate session dirs under the verified
         ``~/.claude/projects/.../*/`` glob (plus a defensive
         ``/private/tmp/claude-<uid>/.../*/`` fallback in case a future harness
         version relocates the tree there), apply the session/window filter
         above, then collect ``agent-*.jsonl`` files from the kept sessions.

    Returns an empty list on any error (fail-open).
    """
    try:
        override = os.environ.get("GLUDD_WORKFLOW_DIRS", "")
        if override:
            override_results: list[str] = []
            for d in override.split(":"):
                d = d.strip()
                if not d:
                    continue
                override_results.extend(glob.glob(os.path.join(d, "*.jsonl")))
                override_results.extend(glob.glob(os.path.join(d, "*.output")))
            return override_results

        session_id = os.environ.get("GLUDD_SESSION_ID", "").strip()
        # Match the VERIFIED real filename (agent-*.jsonl) so journal.jsonl and
        # *.meta.json siblings are excluded. recursive=True lets ** span the
        # workflows/<runid>/ nesting level.
        session_dir_patterns = [
            os.path.expanduser(f"~/.claude/projects/{_claude_project_slug()}/*/"),
            os.path.join(_claude_sessions_base(), "*/"),
        ]

        now = time.time()
        cutoff = now - window
        results: list[str] = []
        seen_sessions: set[str] = set()
        for sp in session_dir_patterns:
            try:
                session_dirs = glob.glob(sp)
            except Exception:
                session_dirs = []
            for sess_dir in session_dirs:
                norm = os.path.normpath(sess_dir)
                if norm in seen_sessions:
                    continue
                seen_sessions.add(norm)

                wf_root = os.path.join(sess_dir, "subagents", "workflows")
                if not os.path.isdir(wf_root):
                    continue
                try:
                    agent_files = glob.glob(os.path.join(wf_root, "**", "agent-*.jsonl"), recursive=True)
                except Exception:
                    agent_files = []
                if not agent_files:
                    continue

                sess_name = os.path.basename(norm)
                if session_id and sess_name == session_id:
                    # Deterministic match -> always include, regardless of mtime.
                    results.extend(agent_files)
                    continue

                newest_mtime = 0.0
                for f in agent_files:
                    try:
                        m = os.path.getmtime(f)
                    except OSError:
                        continue
                    if m > newest_mtime:
                        newest_mtime = m
                if newest_mtime >= cutoff:
                    results.extend(agent_files)
                # else: this session's workflow tree is entirely stale -> excluded.
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

    A transcript is live iff:
        mtime >= cutoff                (written within the last ``window`` secs)
        AND NOT _is_terminal(path)     (no terminal result marker on last line)

    Terminal detection is fail-open: an unparseable last line is treated as
    non-terminal (agent assumed running). An empty file is also treated as live
    (agent just started).
    """
    tasks = _tasks_dir()
    fs = glob.glob(tasks + "/*.output") if tasks else []

    # Also include workflow-subagent transcripts so the floor hook never
    # reports "0 live" while a Workflow-based parallel pool is running. The
    # SAME window is passed through so stale workflow session trees are
    # excluded consistently with the recency check applied below (defect #3).
    wf_files = _workflow_transcript_files(window=window)

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
        if not _is_terminal(f):
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


def _cache_file_for(tasks_dir: str | None) -> str:
    """Resolve the cache file path for the CURRENT session.

    BUG FIXED (2026-07-10, defect #2): the cache used a single machine-global
    path (``/tmp/gludd-live-count-cache.json``) shared by EVERY concurrent
    Claude Code process on the box. Two sessions running at once would read
    and overwrite each other's cached count within the TTL window, so one
    session's liveness read could silently leak into another's floor/delegate
    decision. Fix: key the cache file by a short hash of the RESOLVED tasks-dir
    path (which is unique per session — see ``_tasks_dir``), so each session
    gets an independent cache file and no cross-talk is possible.

    ``GLUDD_LIVENESS_CACHE_FILE`` remains a hard override (test seam / explicit
    opt-out of per-session keying) and wins outright when set.
    """
    override = os.environ.get("GLUDD_LIVENESS_CACHE_FILE")
    if override:
        return override
    key = tasks_dir or "__unresolved__"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"/tmp/gludd-live-count-{digest}.json"


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
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
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
        with contextlib.suppress(Exception):
            con.close()


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


def _read_cache(cache_file: str) -> tuple[float, int] | None:
    """Return (timestamp, count) from ``cache_file``, or None if missing/stale/
    unparseable. Caller decides freshness against CACHE_TTL_SEC."""
    try:
        with open(cache_file) as fh:
            data = json.load(fh)
        ts = float(data["ts"])
        count = int(data["count"])
        return ts, count
    except Exception:
        return None


def _write_cache(count: int, cache_file: str) -> None:
    try:
        with open(cache_file, "w") as fh:
            json.dump({"ts": time.time(), "count": int(count)}, fh)
    except Exception:
        pass


def _count_live_total(window: float = LIVENESS_WINDOW_SEC, use_cache: bool = True) -> int:
    """Return ``max(claude_count, opencode_count)`` — the live-subagent count
    that drives floor enforcement. Works in either harness.

    When ``use_cache`` is True and the cache is fresh (within CACHE_TTL_SEC),
    returns the cached count without probing. This keeps the per-tool-call cost
    under the 500ms budget. The cache file is keyed per-session (defect #2) —
    see ``_cache_file_for``.
    """
    tasks_dir = _tasks_dir()
    cache_file = _cache_file_for(tasks_dir)

    if use_cache and CACHE_TTL_SEC > 0:
        cached = _read_cache(cache_file)
        if cached is not None and (time.time() - cached[0]) < CACHE_TTL_SEC:
            return cached[1]

    claude_n = _count_claude_live(window=window)
    oc_n = _count_opencode_live(window=window)
    total = max(claude_n, oc_n)
    if use_cache:
        _write_cache(total, cache_file)
    return total


def _count_live_total_debug(
    window: float = LIVENESS_WINDOW_SEC,
) -> tuple[int, int, int, str, bool]:
    """Same as _count_live_total but returns diagnostic detail for --debug.

    Returns ``(total, claude_n, opencode_n, harness, cache_used)``.
    """
    harness = _detect_harness()
    tasks_dir = _tasks_dir()
    cache_file = _cache_file_for(tasks_dir)
    cache_used = False
    if CACHE_TTL_SEC > 0:
        cached = _read_cache(cache_file)
        if cached is not None and (time.time() - cached[0]) < CACHE_TTL_SEC:
            cache_used = True
            return cached[1], -1, -1, harness, cache_used
    claude_n = _count_claude_live(window=window)
    oc_n = _count_opencode_live(window=window)
    total = max(claude_n, oc_n)
    _write_cache(total, cache_file)
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
            print(f"[liveness] FLOOR_LIVE_OVERRIDE={override} (test seam active)")
        return 0

    debug = "--debug" in argv
    live: int
    claude_n: int | None
    oc_n: int | None
    harness: str | None
    cached: bool | None

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
            f"  harness detected: {harness}",
            f"  claude backend live count:   {claude_n}",
            f"  opencode backend live count: {oc_n}",
            f"  cache used: {cached}",
            f"  reported (max): {live}",
        ]
        print("\n".join(lines))
    else:
        tasks = _tasks_dir()
        print(
            f"[liveness] live subagents "
            f"(window={LIVENESS_WINDOW_SEC:.0f}s + terminal-detection): "
            f"{live} live  (dir={tasks})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
