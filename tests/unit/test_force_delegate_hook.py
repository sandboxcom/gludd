"""Subprocess-driven tests for .claude/hooks/force_delegate_pretool.sh — the
force-delegate PreToolUse guardrail that denies main-thread mutations when too
few subagents are live and the operator is grinding inline.

No test harness previously existed anywhere in this repo for testing a
.claude/hooks/*.sh script by actually invoking it (verified 2026-07-10 via a
repo-wide search: every existing "hook" test either drives the OPENCODE
TypeScript plugin layer, e.g. tests/unit/test_hook_runtime_verification.py, or
statically inspects .ts source text). This file establishes that pattern for
the .sh hook layer: invoke the script as a real subprocess with a fabricated
PreToolUse JSON payload on stdin and a controlled environment, then parse its
stdout -- either empty (allow) or a
``{"hookSpecificOutput": {"permissionDecision": "deny", ...}}`` JSON block --
to assert the deny/allow decision and inspect the persisted streak state file.

FLOOR_LIVE_OVERRIDE (an existing agent_liveness.py test seam, see
scripts/agent_liveness.py) is reused to pin the "live" count the hook's
internal ``python3 scripts/agent_liveness.py --count`` subprocess would
otherwise compute, so these tests never depend on real task transcripts.

Covers the 2026-07-10 defect #4 fixes:
  (a) HEALTHY-DELEGATION RESET: live >= floor resets the grind streak and
      always allows, even mid-streak.
  (b) TIME DECAY: a streak idle longer than GLUDD_FORCE_DELEGATE_DECAY_SEC is
      discarded before being incremented.
  (c) SendMessage reset: resuming a subagent thread via SendMessage resets the
      streak the same way an Agent/Workflow dispatch does.
Plus baseline/regression coverage for the pre-existing grace/maxblock/opt-in
behavior so the new logic doesn't regress it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "force_delegate_pretool.sh"
FLOOR_OVERRIDE_FILE = "/tmp/gludd-floor-override"

# The _neutralize_ambient_floor_override fixture below os.remove()s the
# literal shared path FLOOR_OVERRIDE_FILE ("/tmp/gludd-floor-override") on
# every test in this module, un-namespaced and without xdist-worker
# isolation. Under CI's `-n>1 --dist loadgroup`, this module's 10 tests can
# be split across workers and race with each other (or with the
# hook_plugin_env-driven tests in tests/conftest.py:58-78, which share the
# same class of hardcoded-/tmp-path hazard). Pin the whole module to the
# same xdist group conftest.py uses for that hazard class so it is always
# serialized onto one worker.
pytestmark = pytest.mark.xdist_group(name="hook-hardcoded-tmp")


@pytest.fixture(autouse=True)
def _neutralize_ambient_floor_override():
    """The hook ALSO reads /tmp/gludd-floor-override (a live-session floor
    retune file managed elsewhere, see agent_floor_stop.sh) BEFORE
    CLAUDE_AGENT_FLOOR, and it wins when present. That file is shared
    machine-global state that may belong to a live orchestrator session, so
    neutralize it for the duration of each test (making CLAUDE_AGENT_FLOOR
    deterministic) and faithfully restore whatever was there afterward."""
    had_file = os.path.exists(FLOOR_OVERRIDE_FILE)
    original = None
    if had_file:
        with open(FLOOR_OVERRIDE_FILE) as fh:
            original = fh.read()
        os.remove(FLOOR_OVERRIDE_FILE)
    try:
        yield
    finally:
        if had_file:
            with open(FLOOR_OVERRIDE_FILE, "w") as fh:
                fh.write(original)
        elif os.path.exists(FLOOR_OVERRIDE_FILE):
            os.remove(FLOOR_OVERRIDE_FILE)


def _run_hook(
    tool_name: str,
    tool_input: dict | None = None,
    *,
    state_file: Path,
    grace: int = 3,
    maxblock: int = 4,
    floor: int = 6,
    live_override: str = "",
    decay_sec: int = 600,
    session_id: str | None = None,
    force_delegate: str = "1",
) -> subprocess.CompletedProcess:
    payload: dict = {"tool_name": tool_name, "tool_input": tool_input or {}}
    if session_id is not None:
        payload["session_id"] = session_id
    env = dict(os.environ)
    env.update({
        "GLUDD_FORCE_DELEGATE": force_delegate,
        "GLUDD_FORCE_DELEGATE_STATE": str(state_file),
        "GLUDD_FORCE_DELEGATE_GRACE": str(grace),
        "GLUDD_FORCE_DELEGATE_MAXBLOCK": str(maxblock),
        "GLUDD_FORCE_DELEGATE_DECAY_SEC": str(decay_sec),
        "CLAUDE_AGENT_FLOOR": str(floor),
        "FLOOR_LIVE_OVERRIDE": live_override,
    })
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=15,
    )


def _is_deny(result: subprocess.CompletedProcess) -> bool:
    out = result.stdout.strip()
    if not out:
        return False
    try:
        data = json.loads(out)
    except Exception:
        return False
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _load_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except Exception:
        return {}


# --- 0. sanity: opt-in gate + basic classification --------------------------

def test_opt_out_by_default_always_allows(tmp_path: Path) -> None:
    """GLUDD_FORCE_DELEGATE unset/0 -> hook exits immediately, no output, no
    state file, regardless of tool or live count."""
    state_file = tmp_path / "state.json"
    result = _run_hook(
        "Edit", {"file_path": "/tmp/whatever.py"},
        state_file=state_file, force_delegate="0", live_override="0", floor=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert not state_file.exists()


def test_readonly_tool_always_allowed_no_state_change(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    for _ in range(5):
        result = _run_hook(
            "Read", {"file_path": "/tmp/whatever.py"},
            state_file=state_file, grace=1, maxblock=1, floor=10, live_override="0",
        )
        assert not _is_deny(result)
    # Read is allowlisted -> never writes/mutates the streak state file.
    assert not state_file.exists()


# --- 1. baseline grace/deny behavior (regression guard) ---------------------

def test_deny_after_grace_exceeded_when_below_floor(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    grace = 2
    results = []
    for _ in range(grace + 1):
        results.append(
            _run_hook(
                "Edit", {"file_path": "/tmp/x.py"},
                state_file=state_file, grace=grace, maxblock=10, floor=10,
                live_override="3",  # below floor
            )
        )
    # First `grace` calls allowed, the (grace+1)th denied.
    for r in results[:grace]:
        assert not _is_deny(r)
    assert _is_deny(results[-1]), "the call past the grace threshold must be denied"
    state = _load_state(state_file)
    assert state["consecutive_targeted"] == grace + 1
    assert state["consecutive_denied"] == 1


def test_maxblock_failopen_unchanged(tmp_path: Path) -> None:
    """After GLUDD_FORCE_DELEGATE_MAXBLOCK consecutive denials, the hook fails
    open (allows) and resets state -- unchanged anti-wedge behavior."""
    state_file = tmp_path / "state.json"
    grace = 0
    maxblock = 2
    results = []
    for _ in range(6):
        results.append(
            _run_hook(
                "Edit", {"file_path": "/tmp/x.py"},
                state_file=state_file, grace=grace, maxblock=maxblock, floor=10,
                live_override="0",
            )
        )
    # Some call among these must eventually fail open (allow) after maxblock denials.
    assert any(not _is_deny(r) for r in results[1:]), (
        "maxblock must eventually fail open so the operator is never permanently wedged"
    )


def test_agent_dispatch_resets_streak(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=1, maxblock=10, floor=10, live_override="0",
    )
    state_before = _load_state(state_file)
    assert state_before["consecutive_targeted"] == 1

    result = _run_hook("Agent", {}, state_file=state_file, live_override="0")
    assert not _is_deny(result)
    state_after = _load_state(state_file)
    assert state_after["consecutive_targeted"] == 0
    assert state_after["consecutive_denied"] == 0


# --- 2. defect 4a: healthy-delegation reset ---------------------------------

def test_healthy_delegation_resets_streak_when_live_meets_floor(tmp_path: Path) -> None:
    """If live >= floor, the streak is reset and the call allowed -- even if a
    grind streak had already accumulated toward denial."""
    state_file = tmp_path / "state.json"
    grace = 1
    floor = 5

    # Build up a streak below the floor: first call allowed, second denied.
    r1 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=floor, live_override="2",
    )
    assert not _is_deny(r1)
    r2 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=floor, live_override="2",
    )
    assert _is_deny(r2), "sanity: streak must be denying before the reset scenario"

    # Now the fleet is healthy (live >= floor) -- must reset and allow.
    r3 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=floor, live_override=str(floor),
    )
    assert not _is_deny(r3), "a healthy fleet (live>=floor) must reset the streak and allow"
    state = _load_state(state_file)
    assert state["consecutive_targeted"] == 0
    assert state["consecutive_denied"] == 0

    # Prove the reset actually happened (not just "denial ignored this once"):
    # the streak restarts from zero, so the NEXT below-floor call is allowed
    # again (targeted=1 <= grace=1) instead of immediately denying.
    r4 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=floor, live_override="2",
    )
    assert not _is_deny(r4), "streak must have genuinely restarted from zero after the reset"


# --- 3. defect 4b: time decay ------------------------------------------------

def test_stale_streak_denies_without_decay(tmp_path: Path) -> None:
    """Sanity/contrast case: with decay effectively disabled (a huge decay_sec),
    a stale streak still counts toward denial on the next targeted call."""
    state_file = tmp_path / "state.json"
    grace = 1
    _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
        decay_sec=600,
    )
    # Manually age the recorded timestamp by only a few seconds -- well within
    # the 600s decay window, so the streak must still be considered "hot".
    state = _load_state(state_file)
    state["last_ts"] = time.time() - 2
    state_file.write_text(json.dumps(state))

    result = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
        decay_sec=600,
    )
    assert _is_deny(result), "a streak well within the decay window must still deny"


def test_time_decay_resets_stale_streak(tmp_path: Path) -> None:
    """A streak whose last activity is OLDER than GLUDD_FORCE_DELEGATE_DECAY_SEC
    is discarded before being incremented -- the equivalent second call that
    would otherwise deny (grace=1) is allowed instead."""
    state_file = tmp_path / "state.json"
    grace = 1
    decay_sec = 5

    _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
        decay_sec=decay_sec,
    )
    state = _load_state(state_file)
    assert state["consecutive_targeted"] == 1

    # Simulate the streak having gone idle far past decay_sec (faster than
    # actually sleeping) by rewriting last_ts directly.
    state["last_ts"] = time.time() - (decay_sec + 100)
    state_file.write_text(json.dumps(state))

    result = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
        decay_sec=decay_sec,
    )
    assert not _is_deny(result), (
        "a streak idle past the decay window must be discarded, so this call "
        "is treated as a FRESH first targeted call (<=grace), not a deny"
    )
    new_state = _load_state(state_file)
    assert new_state["consecutive_targeted"] == 1, "decay must restart the count at 1, not continue at 2"


# --- 4. defect 4c: SendMessage reset -----------------------------------------

def test_sendmessage_resets_streak(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    grace = 1

    r1 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
    )
    assert not _is_deny(r1)
    state = _load_state(state_file)
    assert state["consecutive_targeted"] == 1

    r2 = _run_hook("SendMessage", {"to": "some-agent-id"}, state_file=state_file, live_override="0")
    assert not _is_deny(r2), "SendMessage must never be denied"
    state_after = _load_state(state_file)
    assert state_after["consecutive_targeted"] == 0
    assert state_after["consecutive_denied"] == 0

    # Prove the reset took effect: the next targeted call is treated as a
    # fresh streak (allowed), not as consecutive call #2 (which would deny).
    r3 = _run_hook(
        "Edit", {"file_path": "/tmp/x.py"},
        state_file=state_file, grace=grace, maxblock=10, floor=10, live_override="0",
    )
    assert not _is_deny(r3), "streak must have genuinely restarted after SendMessage"


def test_sendmessage_without_prior_streak_is_noop_allow(tmp_path: Path) -> None:
    """SendMessage on a completely fresh state file is a harmless allow (no
    crash / no spurious deny)."""
    state_file = tmp_path / "state.json"
    result = _run_hook("SendMessage", {"to": "x"}, state_file=state_file, live_override="0")
    assert result.returncode == 0
    assert not _is_deny(result)
