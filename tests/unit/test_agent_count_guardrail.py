"""Tests for the agent-count truth guardrail.

Guardrail 1 — anti-fabrication:
  - `make agent-count` emits LIVE_AGENTS=<int>
  - agent_count_truth.sh emits the [GROUND-TRUTH] line
  - agent_floor_stop.sh emits decision:block when live < FLOOR

Guardrail 2 — floor enforcement integrity:
  - agent_floor_stop.sh reads FLOOR from env (band-config values sourced at hook
    invocation time); confirmed to block when live < FLOOR and allow when live >= FLOOR.

All tests use FLOOR_LIVE_OVERRIDE (the documented test seam in agent_liveness.py)
and CLAUDE_AGENT_FLOOR/CEILING env vars so no real transcript files are needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
LIVENESS_SCRIPT = REPO_ROOT / "scripts" / "agent_liveness.py"

# Hooks live in the main repo's .claude/hooks/ (shared, not worktree-local).
# The worktree's .claude/ is a read-only copy of the main repo config.
_MAIN_HOOKS_DIR = Path("/Users/shawnwilson/gludd/.claude/hooks")
HOOK_COUNT_TRUTH = _MAIN_HOOKS_DIR / "agent_count_truth.sh"
HOOK_FLOOR_STOP = _MAIN_HOOKS_DIR / "agent_floor_stop.sh"

# settings.json: prefer main repo (authoritative); fall back to worktree copy.
_MAIN_SETTINGS = Path("/Users/shawnwilson/gludd/.claude/settings.json")
_SETTINGS_PATH = _MAIN_SETTINGS if _MAIN_SETTINGS.exists() else (REPO_ROOT / ".claude" / "settings.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_hook(
    hook: Path,
    stdin: str = "{}",
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a bash hook with optional stdin and env overrides.

    Returns (returncode, stdout, stderr).
    """
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["bash", str(hook)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _liveness_count(live_override: int) -> int:
    """Run agent_liveness.py --count with the FLOOR_LIVE_OVERRIDE seam."""
    result = subprocess.run(
        [sys.executable, str(LIVENESS_SCRIPT), "--count"],
        capture_output=True,
        text=True,
        env={**os.environ, "FLOOR_LIVE_OVERRIDE": str(live_override)},
    )
    assert result.returncode == 0, f"liveness script failed: {result.stderr}"
    return int(result.stdout.strip())


# ===========================================================================
# GROUP 1 — make agent-count emits LIVE_AGENTS=<int>
# ===========================================================================


class TestAgentCountMakeTarget:
    """make agent-count must emit exactly one line: LIVE_AGENTS=<integer>."""

    def test_agent_count_emits_live_agents_line(self) -> None:
        """make agent-count with FLOOR_LIVE_OVERRIDE=7 must emit LIVE_AGENTS=7."""
        result = subprocess.run(
            ["make", "agent-count"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "7"},
        )
        assert result.returncode == 0, f"make agent-count failed: {result.stderr}"
        # The output must contain the LIVE_AGENTS=<n> line
        output = result.stdout.strip()
        matching = [ln for ln in output.splitlines() if ln.startswith("LIVE_AGENTS=")]
        assert matching, (
            f"make agent-count must emit a LIVE_AGENTS=<n> line; got: {output!r}"
        )
        live_line = matching[0]
        # Value must be an integer
        value_str = live_line.split("=", 1)[1]
        assert value_str.isdigit(), (
            f"LIVE_AGENTS value must be an integer; got: {live_line!r}"
        )
        assert int(value_str) == 7

    def test_agent_count_override_zero(self) -> None:
        """FLOOR_LIVE_OVERRIDE=0 must yield LIVE_AGENTS=0."""
        result = subprocess.run(
            ["make", "agent-count"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "0"},
        )
        assert result.returncode == 0
        output = result.stdout.strip()
        matching = [ln for ln in output.splitlines() if ln.startswith("LIVE_AGENTS=")]
        assert matching, f"Expected LIVE_AGENTS= line; got {output!r}"
        assert matching[0] == "LIVE_AGENTS=0"

    def test_agent_count_value_is_integer(self) -> None:
        """Value part of LIVE_AGENTS=<n> must always be a non-negative integer."""
        result = subprocess.run(
            ["make", "agent-count"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "3"},
        )
        assert result.returncode == 0
        output = result.stdout.strip()
        matching = [ln for ln in output.splitlines() if ln.startswith("LIVE_AGENTS=")]
        assert matching
        val = matching[0].split("=", 1)[1]
        assert val.isdigit()


# ===========================================================================
# GROUP 2 — make floor-status emits structured status line
# ===========================================================================


class TestFloorStatusMakeTarget:
    """make floor-status must emit LIVE=<n> FLOOR=<n> TARGET=<n> CEILING=<n> REFILL_NEEDED=<n>."""

    def _parse_floor_status(self, output: str) -> dict[str, int]:
        """Parse 'LIVE=3 FLOOR=15 TARGET=18 CEILING=20 REFILL_NEEDED=12' style line."""
        result: dict[str, int] = {}
        for part in output.split():
            if "=" in part:
                k, v = part.split("=", 1)
                if v.lstrip("-").isdigit():
                    result[k] = int(v)
        return result

    def test_floor_status_emits_all_keys(self) -> None:
        """floor-status must emit LIVE, FLOOR, TARGET, CEILING, REFILL_NEEDED."""
        proc = subprocess.run(
            ["make", "floor-status"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "5"},
        )
        assert proc.returncode == 0, f"make floor-status failed: {proc.stderr}"
        output = proc.stdout.strip()
        parsed = self._parse_floor_status(output)
        for key in ("LIVE", "FLOOR", "TARGET", "CEILING", "REFILL_NEEDED"):
            assert key in parsed, (
                f"floor-status must emit {key}; got {output!r}"
            )

    def test_floor_status_live_matches_override(self) -> None:
        """LIVE value must reflect FLOOR_LIVE_OVERRIDE."""
        proc = subprocess.run(
            ["make", "floor-status"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "9"},
        )
        assert proc.returncode == 0
        parsed = self._parse_floor_status(proc.stdout)
        assert parsed.get("LIVE") == 9

    def test_floor_status_refill_needed_formula(self) -> None:
        """REFILL_NEEDED = max(0, TARGET - LIVE)."""
        proc = subprocess.run(
            ["make", "floor-status"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "2"},
        )
        assert proc.returncode == 0
        parsed = self._parse_floor_status(proc.stdout)
        live = parsed["LIVE"]
        target = parsed["TARGET"]
        expected_refill = max(0, target - live)
        assert parsed["REFILL_NEEDED"] == expected_refill, (
            f"REFILL_NEEDED should be max(0, TARGET-LIVE)={expected_refill}; got {parsed}"
        )

    def test_floor_status_refill_zero_when_above_target(self) -> None:
        """When LIVE >= TARGET, REFILL_NEEDED must be 0."""
        proc = subprocess.run(
            ["make", "floor-status"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "FLOOR_LIVE_OVERRIDE": "25"},
        )
        assert proc.returncode == 0
        parsed = self._parse_floor_status(proc.stdout)
        assert parsed["REFILL_NEEDED"] == 0, (
            f"REFILL_NEEDED must be 0 when LIVE >= TARGET; got {parsed}"
        )


# ===========================================================================
# GROUP 3 — agent_count_truth.sh emits [GROUND-TRUTH] line
# ===========================================================================


class TestAgentCountTruthHook:
    """agent_count_truth.sh (UserPromptSubmit + Stop hook) must emit [GROUND-TRUTH] line."""

    def test_hook_exists_and_executable(self) -> None:
        assert HOOK_COUNT_TRUTH.exists(), (
            f"Hook not found: {HOOK_COUNT_TRUTH}"
        )
        assert os.access(HOOK_COUNT_TRUTH, os.X_OK), (
            f"Hook must be executable: {HOOK_COUNT_TRUTH}"
        )

    def test_hook_emits_ground_truth_line(self) -> None:
        """Hook must emit a line containing [GROUND-TRUTH] with the live count."""
        rc, stdout, stderr = _run_hook(
            HOOK_COUNT_TRUTH,
            stdin="{}",
            env_extra={"FLOOR_LIVE_OVERRIDE": "5"},
        )
        assert rc == 0, f"Hook must exit 0 (fail-open); got {rc}; stderr={stderr}"
        assert "[GROUND-TRUTH]" in stdout, (
            f"Hook must emit [GROUND-TRUTH] line; got stdout={stdout!r}"
        )
        # The line must include the live count (5)
        ground_truth_lines = [ln for ln in stdout.splitlines() if "[GROUND-TRUTH]" in ln]
        assert ground_truth_lines, "No [GROUND-TRUTH] line found"
        line = ground_truth_lines[0]
        assert "5" in line, (
            f"[GROUND-TRUTH] line must include live count 5; got: {line!r}"
        )

    def test_hook_mentions_source_script(self) -> None:
        """[GROUND-TRUTH] line must reference scripts/agent_liveness.py as source."""
        rc, stdout, _ = _run_hook(
            HOOK_COUNT_TRUTH,
            stdin="{}",
            env_extra={"FLOOR_LIVE_OVERRIDE": "3"},
        )
        assert rc == 0
        assert "agent_liveness.py" in stdout, (
            f"[GROUND-TRUTH] line must cite scripts/agent_liveness.py; got: {stdout!r}"
        )

    def test_hook_mentions_fabrication(self) -> None:
        """[GROUND-TRUTH] line must contain 'fabrication' to warn against made-up counts."""
        rc, stdout, _ = _run_hook(
            HOOK_COUNT_TRUTH,
            stdin="{}",
            env_extra={"FLOOR_LIVE_OVERRIDE": "3"},
        )
        assert rc == 0
        assert "fabrication" in stdout.lower(), (
            f"Hook must mention 'fabrication'; got: {stdout!r}"
        )

    def test_hook_exit_0_on_empty_stdin(self) -> None:
        """Fail-open: empty stdin must not error."""
        rc, _out, stderr = _run_hook(HOOK_COUNT_TRUTH, stdin="")
        assert rc == 0, f"Hook must exit 0 on empty stdin; stderr={stderr}"

    def test_hook_exit_0_on_garbage_stdin(self) -> None:
        """Fail-open: garbage stdin must not crash hook."""
        rc, _out, stderr = _run_hook(HOOK_COUNT_TRUTH, stdin="NOT JSON AT ALL")
        assert rc == 0, f"Hook must exit 0 on garbage; stderr={stderr}"

    def test_hook_no_traceback_in_stderr(self) -> None:
        """No Python traceback must leak to stderr."""
        rc, _out, stderr = _run_hook(
            HOOK_COUNT_TRUTH,
            stdin="{}",
            env_extra={"FLOOR_LIVE_OVERRIDE": "2"},
        )
        assert "Traceback" not in stderr, f"Traceback leaked: {stderr}"
        assert rc == 0

    def test_hook_registered_in_userpromptsubmit(self) -> None:
        """agent_count_truth.sh must be registered under UserPromptSubmit in settings.json."""
        assert _SETTINGS_PATH.exists(), f"settings.json not found at {_SETTINGS_PATH}"
        settings = json.loads(_SETTINGS_PATH.read_text())
        hooks_block = settings.get("hooks", {})
        ups_hooks = hooks_block.get("UserPromptSubmit", [])
        commands = [
            h.get("command", "")
            for entry in ups_hooks
            for h in entry.get("hooks", [])
        ]
        assert any("agent_count_truth.sh" in cmd for cmd in commands), (
            f"agent_count_truth.sh must be in UserPromptSubmit hooks; found: {commands}"
        )

    def test_hook_registered_in_stop(self) -> None:
        """agent_count_truth.sh must be registered under Stop in settings.json."""
        settings = json.loads(_SETTINGS_PATH.read_text())
        stop_hooks = settings.get("hooks", {}).get("Stop", [])
        commands = [
            h.get("command", "")
            for entry in stop_hooks
            for h in entry.get("hooks", [])
        ]
        assert any("agent_count_truth.sh" in cmd for cmd in commands), (
            f"agent_count_truth.sh must be in Stop hooks; found: {commands}"
        )


# ===========================================================================
# GROUP 4 — agent_floor_stop.sh blocks when live < FLOOR
# ===========================================================================


class TestAgentFloorStopBlock:
    """agent_floor_stop.sh must block (decision:block) when live < FLOOR."""

    def test_floor_stop_blocks_when_below_floor(self) -> None:
        """With FLOOR=15 and live=5, hook must emit decision:block."""
        rc, stdout, stderr = _run_hook(
            HOOK_FLOOR_STOP,
            stdin='{"stop_hook_active":false}',
            env_extra={
                "CLAUDE_AGENT_FLOOR": "15",
                "CLAUDE_AGENT_CEILING": "20",
                "FLOOR_LIVE_OVERRIDE": "5",
            },
        )
        assert rc == 0, f"Hook must exit 0 even when blocking; stderr={stderr}"
        # Must produce valid JSON with decision:block
        assert stdout.strip(), "Hook must emit non-empty stdout when blocking"
        data = json.loads(stdout.strip())
        assert data.get("decision") == "block", (
            f"Must emit decision:block when live(5) < FLOOR(15); got: {data}"
        )

    def test_floor_stop_allows_when_at_floor(self) -> None:
        """With FLOOR=15 and live=15, hook must NOT block."""
        rc, stdout, _err = _run_hook(
            HOOK_FLOOR_STOP,
            stdin='{"stop_hook_active":false}',
            env_extra={
                "CLAUDE_AGENT_FLOOR": "15",
                "CLAUDE_AGENT_CEILING": "20",
                "FLOOR_LIVE_OVERRIDE": "15",
            },
        )
        assert rc == 0
        # stdout empty or decision != block
        if stdout.strip():
            data = json.loads(stdout.strip())
            assert data.get("decision") != "block", (
                f"Must NOT block when live(15) >= FLOOR(15); got: {data}"
            )

    def test_floor_stop_allows_when_above_floor(self) -> None:
        """With FLOOR=15 and live=20, hook must NOT block."""
        rc, stdout, _ = _run_hook(
            HOOK_FLOOR_STOP,
            stdin='{"stop_hook_active":false}',
            env_extra={
                "CLAUDE_AGENT_FLOOR": "15",
                "CLAUDE_AGENT_CEILING": "20",
                "FLOOR_LIVE_OVERRIDE": "20",
            },
        )
        assert rc == 0
        if stdout.strip():
            data = json.loads(stdout.strip())
            assert data.get("decision") != "block"

    def test_floor_stop_escape_hatch_respected(self) -> None:
        """stop_hook_active=true must bypass enforcement (anti-wedge)."""
        rc, stdout, _ = _run_hook(
            HOOK_FLOOR_STOP,
            stdin='{"stop_hook_active":true}',
            env_extra={
                "CLAUDE_AGENT_FLOOR": "999",
                "FLOOR_LIVE_OVERRIDE": "0",
            },
        )
        assert rc == 0
        # Must be empty (allow) or not block
        if stdout.strip():
            data = json.loads(stdout.strip())
            assert data.get("decision") != "block", (
                "stop_hook_active=true must bypass block even at FLOOR=999"
            )

    def test_floor_stop_exit_0_always(self) -> None:
        """Hook must ALWAYS exit 0 regardless of floor breach (no hook-error)."""
        for live in ("0", "5", "15", "25"):
            rc, _, _ = _run_hook(
                HOOK_FLOOR_STOP,
                stdin='{"stop_hook_active":false}',
                env_extra={
                    "CLAUDE_AGENT_FLOOR": "15",
                    "FLOOR_LIVE_OVERRIDE": live,
                },
            )
            assert rc == 0, f"Hook must exit 0 with live={live}; got {rc}"

    def test_floor_stop_block_mentions_floor(self) -> None:
        """Block reason must mention the floor value so the agent knows what to target."""
        rc, stdout, _ = _run_hook(
            HOOK_FLOOR_STOP,
            stdin='{"stop_hook_active":false}',
            env_extra={
                "CLAUDE_AGENT_FLOOR": "15",
                "CLAUDE_AGENT_CEILING": "20",
                "FLOOR_LIVE_OVERRIDE": "3",
            },
        )
        assert rc == 0
        if stdout.strip():
            data = json.loads(stdout.strip())
            if data.get("decision") == "block":
                reason = data.get("reason", "")
                assert "15" in reason or "floor" in reason.lower(), (
                    f"Block reason must mention floor 15; got: {reason!r}"
                )

    def test_floor_stop_reads_liveness_from_agent_liveness_py(self) -> None:
        """Verify agent_floor_stop.sh calls agent_liveness.py (not inline logic)."""
        content = HOOK_FLOOR_STOP.read_text()
        assert "agent_liveness.py" in content, (
            "agent_floor_stop.sh must use scripts/agent_liveness.py for ground-truth count"
        )


# ===========================================================================
# GROUP 5 — agent_liveness.py --count returns integer via override seam
# ===========================================================================


class TestAgentLivenessScript:
    """agent_liveness.py --count must return integer via FLOOR_LIVE_OVERRIDE seam."""

    @pytest.mark.parametrize("n", [0, 1, 5, 15, 20])
    def test_count_returns_override_value(self, n: int) -> None:
        """--count with FLOOR_LIVE_OVERRIDE=N must print N."""
        result = _liveness_count(n)
        assert result == n, f"Expected {n}, got {result}"

    def test_count_is_integer(self) -> None:
        """Output of --count must parse as a non-negative integer."""
        result = _liveness_count(7)
        assert isinstance(result, int)
        assert result >= 0
