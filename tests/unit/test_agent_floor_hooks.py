"""Tests for agent-floor hook gates and the FLOOR_LIVE_OVERRIDE test seam.

These tests verify:
  - The FLOOR_LIVE_OVERRIDE env seam in scripts/agent_liveness.py works as
    intended (skip the filesystem probe and return the override value directly).
  - Each hook script behaves correctly at dead-band boundaries (below floor,
    in band, at/above ceiling).
  - Every hook is fail-open: a non-numeric override causes exit 0 with no
    blocking output.

FLOOR=6, CEILING=12, TARGET=10 (hook defaults).

Hook scripts are at /Users/shawnwilson/gludd/.claude/hooks/ (untracked).
The seam is in scripts/agent_liveness.py — both the worktree copy and the
main-checkout copy (at /Users/shawnwilson/gludd/scripts/agent_liveness.py)
must have it for hook tests to work.  A module-level fixture ensures the
main-checkout copy has the seam before any hook subprocess is launched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The worktree root is two levels up from tests/unit/
REPO_ROOT = Path(__file__).resolve().parents[2]

# The main gludd checkout (where the hooks cd to run python3)
MAIN_CHECKOUT = Path("/Users/shawnwilson/gludd")

HOOKS_DIR = MAIN_CHECKOUT / ".claude" / "hooks"
SCRIPT_SRC = REPO_ROOT / "scripts" / "agent_liveness.py"
SCRIPT_MAIN = MAIN_CHECKOUT / "scripts" / "agent_liveness.py"

# ---------------------------------------------------------------------------
# Module-level setup: ensure the main checkout has the seam-aware script
# ---------------------------------------------------------------------------


def _main_has_seam() -> bool:
    """Return True if the main-checkout's agent_liveness.py has FLOOR_LIVE_OVERRIDE."""
    try:
        return "FLOOR_LIVE_OVERRIDE" in SCRIPT_MAIN.read_text()
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def ensure_main_script_has_seam():
    """Copy the worktree's seam-aware agent_liveness.py to the main checkout
    if it is missing or outdated.  This fixture is TEST-ONLY infrastructure
    and is idempotent (a no-op if the seam is already present)."""
    if SCRIPT_SRC.exists() and not _main_has_seam():
        dest = SCRIPT_MAIN
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(SCRIPT_SRC), str(dest))
    yield


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

PYTHON = sys.executable


def _run_script(override: str | None, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the worktree's agent_liveness.py --count with an optional override."""
    env = os.environ.copy()
    if override is not None:
        env["FLOOR_LIVE_OVERRIDE"] = override
    else:
        env.pop("FLOOR_LIVE_OVERRIDE", None)
    args = [PYTHON, str(SCRIPT_SRC), "--count"] + (extra_args or [])
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)


def _run_hook(hook_name: str, override: str, stdin: str = "{}") -> subprocess.CompletedProcess[str]:
    """Run a hook script with FLOOR_LIVE_OVERRIDE set."""
    hook_path = HOOKS_DIR / hook_name
    env = os.environ.copy()
    env["FLOOR_LIVE_OVERRIDE"] = override
    # Inherit FLOOR_PROBE_SECS / FLOOR_TAIL_SECS from env (or let hooks default)
    return subprocess.run(
        ["bash", str(hook_path)],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Task 1 tests: FLOOR_LIVE_OVERRIDE seam in agent_liveness.py
# ---------------------------------------------------------------------------


class TestFloorLiveOverrideSeam:
    """FLOOR_LIVE_OVERRIDE must short-circuit the probe and return the value."""

    def test_override_returns_exact_value(self):
        result = _run_script("7")
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_override_zero(self):
        result = _run_script("0")
        assert result.returncode == 0
        assert result.stdout.strip() == "0"

    def test_override_above_ceiling(self):
        result = _run_script("15")
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_non_numeric_override_is_ignored(self):
        """Non-digit override must NOT print the value — it falls through to
        the normal probe path (or fails open with 0).  Either way exit 0."""
        result = _run_script("abc")
        assert result.returncode == 0
        # Must not print "abc"
        assert result.stdout.strip() != "abc"

    def test_no_override_still_exits_zero(self):
        """Without the seam, the script should still exit 0 (fail-open)."""
        result = _run_script(None)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Task 3 tests: hook behaviour at dead-band boundaries
# ---------------------------------------------------------------------------

# Hooks existence guard — skip all hook tests if hooks dir is missing
hooks_exist = HOOKS_DIR.exists()
hooks_reason = f"hooks dir not found: {HOOKS_DIR}"


@pytest.mark.skipif(not hooks_exist, reason=hooks_reason)
class TestPreToolHook:
    """agent_floor_pretool.sh: advisory, fires only below floor."""

    def test_below_floor_emits_agent_floor_advisory(self):
        result = _run_hook("agent_floor_pretool.sh", "5")
        assert result.returncode == 0
        assert "AGENT-FLOOR" in result.stdout, (
            f"Expected AGENT-FLOOR advisory in stdout; got: {result.stdout!r}"
        )

    def test_in_band_at_floor_is_silent(self):
        result = _run_hook("agent_floor_pretool.sh", "6")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout, (
            f"Expected no advisory in band; got: {result.stdout!r}"
        )

    def test_in_band_above_floor_is_silent(self):
        result = _run_hook("agent_floor_pretool.sh", "9")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout

    def test_fail_open_non_numeric(self):
        result = _run_hook("agent_floor_pretool.sh", "abc")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout
        assert '"decision":"block"' not in result.stdout


@pytest.mark.skipif(not hooks_exist, reason=hooks_reason)
class TestPostToolHook:
    """agent_floor_posttool.sh: advisory, fires only below floor."""

    def test_below_floor_emits_agent_floor_advisory(self):
        result = _run_hook("agent_floor_posttool.sh", "5")
        assert result.returncode == 0
        assert "AGENT-FLOOR" in result.stdout

    def test_in_band_at_floor_is_silent(self):
        result = _run_hook("agent_floor_posttool.sh", "6")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout

    def test_in_band_middle_is_silent(self):
        result = _run_hook("agent_floor_posttool.sh", "9")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout

    def test_fail_open_non_numeric(self):
        result = _run_hook("agent_floor_posttool.sh", "abc")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout
        assert '"decision":"block"' not in result.stdout


@pytest.mark.skipif(not hooks_exist, reason=hooks_reason)
class TestUserPromptHook:
    """agent_floor_userprompt.sh: advisory below floor, ceiling note at/above ceiling."""

    def test_below_floor_emits_agent_floor_advisory(self):
        result = _run_hook("agent_floor_userprompt.sh", "5")
        assert result.returncode == 0
        assert "AGENT-FLOOR" in result.stdout

    def test_in_band_at_floor_is_silent(self):
        result = _run_hook("agent_floor_userprompt.sh", "6")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout
        assert "AGENT-CEILING" not in result.stdout

    def test_in_band_middle_is_silent(self):
        result = _run_hook("agent_floor_userprompt.sh", "9")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout
        assert "AGENT-CEILING" not in result.stdout

    def test_at_ceiling_emits_ceiling_message(self):
        result = _run_hook("agent_floor_userprompt.sh", "12")
        assert result.returncode == 0
        assert "CEILING" in result.stdout, (
            f"Expected CEILING message at override=12; got: {result.stdout!r}"
        )

    def test_fail_open_non_numeric(self):
        result = _run_hook("agent_floor_userprompt.sh", "abc")
        assert result.returncode == 0
        assert "AGENT-FLOOR" not in result.stdout
        assert '"decision":"block"' not in result.stdout


@pytest.mark.skipif(not hooks_exist, reason=hooks_reason)
class TestCeilingPreToolHook:
    """agent_ceiling_pretool.sh: fires at/above ceiling, silent below."""

    def test_at_ceiling_emits_ceiling_message(self):
        result = _run_hook("agent_ceiling_pretool.sh", "12")
        assert result.returncode == 0
        assert "CEILING" in result.stdout, (
            f"Expected CEILING message at override=12; got: {result.stdout!r}"
        )

    def test_below_ceiling_is_silent(self):
        result = _run_hook("agent_ceiling_pretool.sh", "9")
        assert result.returncode == 0
        assert "CEILING" not in result.stdout

    def test_fail_open_non_numeric(self):
        result = _run_hook("agent_ceiling_pretool.sh", "abc")
        assert result.returncode == 0
        assert "CEILING" not in result.stdout
        assert '"decision":"block"' not in result.stdout


@pytest.mark.skipif(not hooks_exist, reason=hooks_reason)
class TestStopHook:
    """agent_floor_stop.sh: BLOCKS (decision=block) below floor; allows stop in band."""

    def test_below_floor_emits_block_decision(self):
        result = _run_hook("agent_floor_stop.sh", "3")
        assert result.returncode == 0
        out = result.stdout
        assert '"decision":"block"' in out or '"decision": "block"' in out, (
            f"Expected block decision at override=3; got: {out!r}"
        )

    def test_below_floor_reason_contains_band_info(self):
        """Reason must state live count, floor, and ceiling (calm language)."""
        result = _run_hook("agent_floor_stop.sh", "3")
        out = result.stdout
        # Reason should mention floor/ceiling band and dispatch
        assert "3" in out  # live count
        assert "6" in out  # floor

    def test_in_band_allows_stop(self):
        """At override=8 (in band [6,12)), the hook must NOT emit a block."""
        result = _run_hook("agent_floor_stop.sh", "8")
        assert result.returncode == 0
        out = result.stdout
        assert '"decision":"block"' not in out and '"decision": "block"' not in out, (
            f"Expected NO block in band; got: {out!r}"
        )

    def test_stop_hook_active_short_circuits(self):
        """stop_hook_active=true must always allow stop (anti-wedge)."""
        result = _run_hook(
            "agent_floor_stop.sh",
            "0",  # way below floor
            stdin='{"stop_hook_active": true}',
        )
        assert result.returncode == 0
        out = result.stdout
        assert '"decision":"block"' not in out and '"decision": "block"' not in out

    def test_fail_open_non_numeric(self):
        """Non-numeric override -> hook exits 0, no block."""
        result = _run_hook("agent_floor_stop.sh", "abc")
        assert result.returncode == 0
        out = result.stdout
        assert '"decision":"block"' not in out and '"decision": "block"' not in out
