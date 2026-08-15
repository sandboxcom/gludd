#!/usr/bin/env python3
"""Enforce a minimum interval between CI status checks.

Anti-pattern this prevents: an agent dispatches a "push + poll CI until
terminal" subagent that loops ``make ci-verdict`` every 60-90s for 30-40
minutes, holding a subagent slot and the orchestrator's attention while
producing zero value. CI runs on its own schedule; polling it does not
make it finish faster.

Usage::

    make ci-verdict-safe           # checks cooldown, then runs ci-verdict
    make ci-verdict-safe FORCE=1   # bypass cooldown (for release-cut only)
    make deploy-and-forget         # push + record timestamp + print checkback
    make ci-cooldown-status        # show remaining cooldown seconds

Exit codes for ``check``: 0=proceed to ci-verdict, 1=cooldown blocks AND last
verdict was RED/failure (the failure IS surfaced), 3=cooldown blocks and last
verdict was success/pending/unknown. In all cases the last known verdict is
printed to stderr so the caller never sees "PENDING" when CI was already RED.

State file: ``/tmp/gludd-ci-check-state.json``
    {"last_check_epoch": <float>, "last_push_epoch": <float>,
     "last_head_sha": "<sha>", "check_count": <int>,
     "last_verdict": "<success|failure|pending|unknown>",
     "last_verdict_epoch": <float>}

Verdict history file: ``/tmp/gludd-ci-verdict-history.json``
    ``record-verdict <verdict> <sha>`` additionally sets ``last_checked_sha``
    (and ``last_verdict``/``last_checked_ts``) in this file via an atomic
    tmp+rename write, preserving ``last_push_sha`` and any other existing
    keys. This is what unblocks the Makefile ``_ci-verdict-history-guard``
    (AA032), which refuses a push while ``last_checked_sha`` !=
    ``last_push_sha``.

Default cooldown: 600s (10 min). Override via ``CI_CHECK_COOLDOWN_SEC``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_FILE = Path(os.environ.get("GLUDD_CI_STATE_FILE", "/tmp/gludd-ci-check-state.json"))
HISTORY_FILE = Path(os.environ.get("GLUDD_CI_HISTORY_FILE", "/tmp/gludd-ci-verdict-history.json"))
RESTART_COUNT_FILE = Path(os.environ.get("GLUDD_CI_RESTART_COUNT_FILE", "/tmp/gludd-ci-restart-count"))
DEFAULT_COOLDOWN_SEC = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "last_check_epoch": 0.0,
            "last_push_epoch": 0.0,
            "last_head_sha": "",
            "check_count": 0,
            "last_verdict": "",
            "last_verdict_epoch": 0.0,
        }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def load_history() -> dict:
    """Read the CI verdict history file; missing/corrupt → empty dict."""
    try:
        return json.loads(HISTORY_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history_atomic(history: dict) -> None:
    """Write the history file atomically (tmp + rename) so a partial write
    can never leave the file corrupt (AB074 requires valid JSON at all times)."""
    tmp_path = HISTORY_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(history))
    os.replace(tmp_path, HISTORY_FILE)


def remaining_cooldown_sec(state: dict, cooldown: int = DEFAULT_COOLDOWN_SEC) -> float:
    elapsed = time.time() - state.get("last_check_epoch", 0.0)
    return max(0.0, cooldown - elapsed)


def record_check(head_sha: str) -> None:
    state = load_state()
    state["last_check_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    state["check_count"] = state.get("check_count", 0) + 1
    save_state(state)


def record_push(head_sha: str) -> None:
    state = load_state()
    state["last_push_epoch"] = time.time()
    state["last_head_sha"] = head_sha
    save_state(state)


def get_head_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return ""


def cmd_check(cooldown: int, force: bool) -> int:
    state = load_state()
    head_sha = get_head_sha()
    remaining = remaining_cooldown_sec(state, cooldown)
    if remaining > 0 and not force:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        last_verdict = state.get("last_verdict", "")
        last_verdict_epoch = state.get("last_verdict_epoch", 0.0)
        verdict_detail = _format_last_verdict(last_verdict, last_verdict_epoch)
        print(
            f"CI-COOLDOWN: {mins}m{secs}s remaining since last check "
            f"(check #{state.get('check_count', 0)}). "
            f"{verdict_detail} "
            f"CI runs on its own schedule; polling does not speed it up. "
            f"Resume real work; check back later with `make ci-verdict-safe`. "
            f"(override with FORCE=1 ONLY for release-cut)",
            file=sys.stderr,
        )
        if last_verdict in ("failure", "red", "error", "cancelled", "timed_out"):
            return 1
        return 3
    record_check(head_sha)
    print(f"CI-CHECK-{state.get('check_count', 0) + 1}: running ci-verdict for {head_sha[:12]}")
    return 0


def _format_last_verdict(verdict: str, epoch: float) -> str:
    if not verdict:
        return "Last known verdict: unknown (no prior check recorded)."
    age_str = _age_str(epoch)
    return f"Last known verdict: {verdict.upper()}{age_str}."


def _age_str(epoch: float) -> str:
    if not epoch:
        return ""
    age = int(time.time() - epoch)
    if age < 60:
        return f" ({age}s ago)"
    return f" ({age // 60}m{age % 60}s ago)"


def cmd_record_verdict(verdict: str, sha: str | None = None) -> int:
    state = load_state()
    state["last_verdict"] = verdict.strip().lower()
    state["last_verdict_epoch"] = time.time()
    save_state(state)
    if sha:
        history = load_history()
        history["last_checked_sha"] = sha
        history["last_verdict"] = verdict.strip().lower()
        history["last_checked_ts"] = int(time.time())
        save_history_atomic(history)
    _reset_restart_cap_on_terminal_verdict(verdict)
    return 0


def _reset_restart_cap_on_terminal_verdict(verdict: str) -> None:
    """Reset the AA023 CI-restart counter once CI has reported a terminal
    verdict (GREEN or RED) for the pushed SHA.

    The Makefile `_ci-restart-cap` limits CI restarts to 3 per session and
    documents that pushes unblock "when CI reports GREEN or RED". Without
    this reset the counter never decreases — a blocked push loop permanently
    wedges. PENDING/unknown verdicts keep the cap in force (CI has not
    reported yet, so thrash protection is still needed)."""
    normalized = verdict.strip().lower()
    if normalized not in ("success", "failure"):
        return
    try:
        RESTART_COUNT_FILE.write_text("0")
    except OSError:
        pass


def cmd_deploy() -> int:
    head_sha = get_head_sha()
    record_push(head_sha)
    cooldown = DEFAULT_COOLDOWN_SEC
    print(f"DEPLOY-RECORDED: pushed {head_sha[:12]} at epoch {time.time():.0f}")
    print(f"CHECKBACK: run `make ci-verdict-safe` in at least {cooldown // 60} minutes.")
    print("In the meantime: dispatch real work. Do NOT poll.")
    return 0


def cmd_status(cooldown: int) -> int:
    state = load_state()
    remaining = remaining_cooldown_sec(state, cooldown)
    last_check = state.get("last_check_epoch", 0.0)
    last_push = state.get("last_push_epoch", 0.0)
    count = state.get("check_count", 0)
    head = state.get("last_head_sha", "")
    last_verdict = state.get("last_verdict", "")
    last_verdict_epoch = state.get("last_verdict_epoch", 0.0)
    if remaining > 0:
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        print(f"COOLDOWN-ACTIVE: {mins}m{secs}s remaining (check #{count})")
    else:
        print(f"COOLDOWN-EXPIRED: may check now (last check was {int(time.time() - last_check)}s ago)")
    if last_push:
        print(f"  last push: {int(time.time() - last_push)}s ago ({head[:12]})")
    if last_verdict:
        age = _age_str(last_verdict_epoch)
        print(f"  last verdict: {last_verdict.upper()}{age}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_check_cooldown.py {check|deploy|status|record-verdict} [args...]", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    force = os.environ.get("FORCE", "") == "1"
    if cmd == "check":
        cooldown = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COOLDOWN_SEC
        return cmd_check(cooldown, force)
    if cmd == "deploy":
        return cmd_deploy()
    if cmd == "status":
        cooldown = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COOLDOWN_SEC
        return cmd_status(cooldown)
    if cmd == "record-verdict":
        verdict = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        sha = sys.argv[3] if len(sys.argv) > 3 else None
        return cmd_record_verdict(verdict, sha)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
