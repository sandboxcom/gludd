"""Functional tests for enforcement Makefile targets.

Tests reload-enforcement, disengage-enforcement, rearm-enforcement,
enforcement-status, and verify-enforcement targets.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Every test in this module mutates shared global /tmp/gludd-* state files.
# Under xdist (--dist loadgroup), concurrent workers corrupt each other's
# setup (worker B deletes /tmp/gludd-tool-streak.json while worker A reads it).
# Force the whole module onto a single worker to eliminate the race.
pytestmark = pytest.mark.xdist_group("enforcement-shared-state")

ENFORCEMENT_STATE_FILES = [
    "/tmp/gludd-floor-override",
    "/tmp/gludd-tool-streak.json",
    "/tmp/gludd-mainthread-streak.json",
    "/tmp/gludd-watchdog-disengage.json",
    "/tmp/gludd-enhancement-ratio.json",
    "/tmp/gludd-session-start.json",
    "/tmp/gludd-task-deadlines.json",
    "/tmp/gludd-task-stale.json",
    "/tmp/gludd-multitask-state.json",
    "/tmp/gludd-block-counter.json",
    "/tmp/gludd-watchdog-ci.json",
]


def _cleanup_enf_state() -> None:
    for f in ENFORCEMENT_STATE_FILES:
        with contextlib.suppress(FileNotFoundError):
            os.remove(f)


class TestReloadEnforcement:
    def test_reload_resets_streak_files(self, tmp_path: Path):
        streak_path = tmp_path / "tool-streak.json"
        streak_path.write_text(
            json.dumps({"count": 99, "ts": int(time.time() * 1000)})
        )
        env = os.environ.copy()
        env["GLUDD_STREAK_FILE"] = str(streak_path)

        result = subprocess.run(
            ["make", "reload-enforcement"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env=env,
        )

        assert result.returncode == 0, result.stderr
        streak = json.loads(streak_path.read_text())
        assert streak["count"] == 0

    def test_reload_removes_disengage_signal(self):
        try:
            _cleanup_enf_state()
            with open("/tmp/gludd-watchdog-disengage.json", "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists("/tmp/gludd-watchdog-disengage.json")
        finally:
            _cleanup_enf_state()

    def test_reload_writes_floor_override(self):
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists("/tmp/gludd-floor-override")
        finally:
            _cleanup_enf_state()

    def test_reload_removes_enhancement_ratio(self):
        try:
            _cleanup_enf_state()
            with open("/tmp/gludd-enhancement-ratio.json", "w") as f:
                json.dump({"wave": [], "session": {}}, f)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists("/tmp/gludd-enhancement-ratio.json")
        finally:
            _cleanup_enf_state()

    def test_reload_removes_multitask_state(self):
        """PID staleness guard: reload must clear multitask-state.json so a
        dead prior-process pid does not persist into the new session."""
        try:
            _cleanup_enf_state()
            with open("/tmp/gludd-multitask-state.json", "w") as f:
                json.dump({"pid": 99999, "zeroStreak": 5}, f)
            result = subprocess.run(
                ["make", "reload-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists("/tmp/gludd-multitask-state.json")
        finally:
            _cleanup_enf_state()


class TestDisengageEnforcement:
    def test_disengage_writes_disengage_signal(self):
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists("/tmp/gludd-watchdog-disengage.json")
            with open("/tmp/gludd-watchdog-disengage.json") as f:
                sig = json.load(f)
            assert "disengage_until" in sig
            assert sig["disengage_until"] > int(time.time() * 1000)
        finally:
            _cleanup_enf_state()

    def test_disengage_writes_block_counter(self):
        try:
            _cleanup_enf_state()
            subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert os.path.exists("/tmp/gludd-block-counter.json")
        finally:
            _cleanup_enf_state()

    def test_disengage_writes_ci_state(self):
        try:
            _cleanup_enf_state()
            subprocess.run(
                ["make", "disengage-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert os.path.exists("/tmp/gludd-watchdog-ci.json")
        finally:
            _cleanup_enf_state()


class TestRearmEnforcement:
    def test_rearm_removes_disengage_when_present(self):
        try:
            _cleanup_enf_state()
            with open("/tmp/gludd-watchdog-disengage.json", "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "rearm-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists("/tmp/gludd-watchdog-disengage.json")
            assert "REMOVED" in result.stdout or "REARMED" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_rearm_no_op_when_not_disengaged(self):
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "rearm-enforcement"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, result.stderr
            assert "no-op" in result.stdout
        finally:
            _cleanup_enf_state()


class TestEnforcementStatus:
    def test_status_prints_valid_output(self):
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "enforcement-status"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "ENFORCEMENT STATUS" in result.stdout
            assert "floor-override" in result.stdout
            assert "disengaged" in result.stdout
            assert "NO" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_status_shows_disengaged_when_active(self):
        try:
            _cleanup_enf_state()
            with open("/tmp/gludd-watchdog-disengage.json", "w") as f:
                json.dump({"disengage_until": 9999999999999}, f)
            result = subprocess.run(
                ["make", "enforcement-status"],
                capture_output=True, text=True, timeout=15,
                cwd=str(ROOT),
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "YES" in result.stdout
        finally:
            _cleanup_enf_state()


class TestVerifyEnforcement:
    def test_passes_when_all_enforcement_active(self, monkeypatch):
        try:
            _cleanup_enf_state()
            # ensure no disable vars leak
            monkeypatch.delenv("GLUDD_FLOOR_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_MAINTHREAD_STREAK_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_MULTITASK_FLOOR_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_STOP_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_TASK_DEADLINE_BLOCK", raising=False)
            monkeypatch.delenv("GLUDD_ENHANCEMENT_RATIO_BLOCK", raising=False)
            monkeypatch.delenv("GLUDD_CLEAN_TREE_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_VERIFIED_CLAIMS_ENFORCE", raising=False)
            monkeypatch.delenv("GLUDD_SESSION_START_ENFORCE", raising=False)
            result = subprocess.run(
                ["make", "verify-enforcement"],
                capture_output=True, text=True,
                cwd=str(ROOT),
                env={**os.environ},  # inherit cleaned env
            )
            assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
            assert "PASS" in result.stdout
        finally:
            _cleanup_enf_state()

    def test_fails_when_enforcement_disabled(self, monkeypatch):
        try:
            _cleanup_enf_state()
            result = subprocess.run(
                ["make", "verify-enforcement"],
                capture_output=True, text=True,
                cwd=str(ROOT),
                env={**os.environ, "GLUDD_FLOOR_ENFORCE": "0"},
            )
            assert result.returncode != 0, (
                "verify-enforcement should fail when GLUDD_FLOOR_ENFORCE=0"
            )
        finally:
            _cleanup_enf_state()
