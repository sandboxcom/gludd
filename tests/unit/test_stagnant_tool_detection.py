"""Behavior pin for BP.3: Stagnant Tool Call Detector.

Extends enforce-no-ci-poll.ts. After MAX_STAGNANT_CALLS (default 5)
consecutive read-only operations (read, glob, grep tools; ci-status,
ci-verdict, ci-view, ci-await, gate-status-check, verify-state,
git-status, git-log, verify-release-completeness, release-view bash
targets) without an intervening productive mutation (edit, write,
git-commit, git-push, git-tag-push, ship-commit, release-cut,
batch-push), the plugin DENIES with a STAGNANT TOOL CALLS directive.

This test pins the structural presence of the detector and its
configuration constants. Runtime invocation is covered by
make check-plugin-hook-invoke.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-no-ci-poll.ts"


def _src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin not found: {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestStagnantDetectorPresent:
    """BP.3 constants and state file must exist in the plugin."""

    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-no-ci-poll.ts must exist (BP.3 extends it)"
        )

    def test_max_stagnant_calls_constant(self):
        src = _src()
        assert "MAX_STAGNANT_CALLS" in src, (
            "MAX_STAGNANT_CALLS constant must be defined for BP.3"
        )

    def test_max_stagnant_calls_default_is_5(self):
        src = _src()
        # Default threshold of 5 consecutive read-only ops.
        assert re.search(r'GLUDD_STAGNANT_MAX\s*\|\|\s*"5"', src), (
            "Default MAX_STAGNANT_CALLS must be 5"
        )

    def test_stagnant_state_file_defined(self):
        src = _src()
        assert "gludd-stagnant-streak" in src, (
            "Must track stagnant streak in /tmp/gludd-stagnant-streak.json"
        )

    def test_env_var_override_present(self):
        src = _src()
        assert "GLUDD_STAGNANT_MAX" in src, (
            "Must allow GLUDD_STAGNANT_MAX env var override"
        )


class TestStagnantReadonlyTools:
    """Direct read-only TOOL calls that increment the stagnant counter."""

    def test_read_tool_tracked(self):
        src = _src()
        assert '"read"' in src and 'STAGNANT_TOOLS' in src

    def test_glob_tool_tracked(self):
        src = _src()
        assert '"glob"' in src

    def test_grep_tool_tracked(self):
        src = _src()
        assert '"grep"' in src

    def test_stagnant_tools_set_defined(self):
        src = _src()
        assert "STAGNANT_TOOLS" in src and "Set(" in src


class TestStagnantReadonlyBashTargets:
    """Read-only bash targets that increment the stagnant counter."""

    @pytest.mark.parametrize(
        "target",
        [
            "ci-status",
            "ci-verdict",
            "ci-view",
            "ci-await",
            "gate-status-check",
            "verify-state",
            "git-status",
            "git-log",
            "verify-release-completeness",
            "release-view",
        ],
    )
    def test_bash_target_tracked(self, target: str):
        src = _src()
        assert target in src, (
            f"BP.3 must track '{target}' as a stagnant bash target"
        )

    def test_stagnant_bash_regex_defined(self):
        src = _src()
        assert "STAGNANT_BASH_RE" in src


class TestStagnantProductiveResets:
    """Operations that reset the stagnant counter."""

    @pytest.mark.parametrize(
        "target",
        [
            "edit",
            "write",
            "git-commit",
            "git-push",
            "git-tag-push",
            "ship-commit",
            "release-cut",
            "batch-push",
        ],
    )
    def test_productive_resets_counter(self, target: str):
        src = _src()
        if target in ("edit", "write"):
            assert f'"{target}"' in src, (
                f"'{target}' tool must reset stagnant counter"
            )
        else:
            assert target in src, (
                f"'{target}' bash target must reset stagnant counter"
            )

    def test_productive_tools_set_defined(self):
        src = _src()
        assert "PRODUCTIVE_TOOLS" in src

    def test_write_stagnant_streak_on_reset(self):
        src = _src()
        # The reset path must write 0 to the stagnant state file.
        assert "writeStagnantStreak(0)" in src


class TestStagnantDenyMessage:
    """The deny message must match the BP.3 spec wording."""

    def test_deny_message_contains_required_phrases(self):
        src = _src()
        assert "STAGNANT TOOL CALLS" in src, (
            "Deny message must contain 'STAGNANT TOOL CALLS'"
        )
        assert "STOP investigating and START producing" in src, (
            "Deny message must contain the BP.3 spec directive"
        )

    def test_deny_uses_permission_decision_deny(self):
        src = _src()
        # The deny path must return permissionDecision: "deny".
        stagnant_block = src.split("STAGNANT TOOL CALLS")[0]
        last_deny = stagnant_block.rfind("permissionDecision")
        assert last_deny != -1, "Stagnant deny must use permissionDecision"


class TestStagnantFailOpenAndGuards:
    """Standard plugin hygiene: subagent guard, fail-open, hot-reload."""

    def test_subagent_guard_present(self):
        assert "isSubagent()" in _src()

    def test_fail_open_present(self):
        src = _src()
        assert "catch" in src, "Must fail-open with a catch block"

    def test_hot_reload_pattern(self):
        src = _src()
        assert "loadHotModule" in src, "Must use hot-reload proxy pattern"


class TestStagnantThresholdLogic:
    """The threshold logic: deny when count EXCEEDS MAX (count > MAX)."""

    def test_strict_greater_than_comparison(self):
        src = _src()
        # count > MAX_STAGNANT_CALLS means the 6th read-only op is denied
        # (after 5 stagnant calls recorded). Matches the spec: "After 5
        # consecutive."
        assert re.search(
            r"scount\s*>\s*MAX_STAGNANT_CALLS", src
        ), "Stagnant bash deny must use strict > comparison"

    def test_tool_count_comparison(self):
        src = _src()
        assert re.search(
            r"count\s*>\s*MAX_STAGNANT_CALLS", src
        ), "Stagnant tool deny must use strict > comparison"
