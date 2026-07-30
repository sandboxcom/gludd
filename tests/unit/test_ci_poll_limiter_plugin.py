"""Verify CI polling limiter prevents obsessive ci-status checking.

The CI poll limiter (enforce-no-ci-poll.ts) tracks consecutive CI status
checks and blocks after MAX_CONSECUTIVE_POLLS (default 3) without an
intervening productive mutation. This test pins the structural presence
of the limiter and its configuration.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-ci-poll.ts"


def _src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin not found: {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestCiPollLimiterPlugin:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-no-ci-poll.ts must exist to prevent obsessive CI polling"
        )

    def test_subagent_guard_present(self):
        assert "isSubagent()" in _src()

    def test_fail_open_present(self):
        assert "catch" in _src() or "Fail-open" in _src()

    def test_hot_reload_pattern(self):
        src = _src()
        assert "loadHotModule" in src, "Must use hot-reload proxy pattern"

    def test_ci_commands_tracked(self):
        src = _src()
        for cmd in ["ci-status", "ci-verdict", "ci-view", "ci-await"]:
            assert cmd in src, f"Must track '{cmd}' as a CI poll command"

    def test_productive_commands_reset(self):
        src = _src()
        for cmd in ["git-commit", "git-push", "git-tag-push", "release-cut"]:
            assert cmd in src, f"Must reset counter on '{cmd}'"


class TestCiPollLimiterConfig:
    def test_max_polls_defined(self):
        src = _src()
        assert "MAX_CONSECUTIVE_POLLS" in src

    def test_max_polls_default_is_3(self):
        src = _src()
        assert '"3"' in src or "'3'" in src, "Default max polls should be 3"

    def test_state_file_defined(self):
        src = _src()
        assert "gludd-ci-poll" in src, "Must use a gludd-ci-poll state file"

    def test_env_var_override(self):
        src = _src()
        assert "GLUDD_CI_POLL_MAX" in src, "Must allow env var override"

    def test_plugin_and_stagnation_disable_switches_are_enforced(self):
        src = _src()
        assert 'process.env.GLUDD_NO_CI_POLL_ENFORCE !== "0"' in src
        assert 'process.env.GLUDD_STAGNANT_ENFORCE !== "0"' in src
        assert "if (!ENFORCE) return" in src


class TestCiPollLimiterRegistered:
    def test_plugin_in_opencode_json(self):
        cfg = (ROOT / "opencode.json").read_text()
        assert "enforce-no-ci-poll" in cfg, (
            "enforce-no-ci-poll must be registered in opencode.json plugin list"
        )
