"""Behavior pin for the no-idle-monitoring enforcement plugin.

Per AGENTS.md "No Idle Monitoring While Work Remains": when a background
operation is running, the main thread must interleave status checks with
concrete independent deliverables. Repeated monitoring calls without
productive work between them are denied.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-no-idle-monitoring.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_monitoring_patterns(src: str) -> list[re.Pattern[str]]:
    block_match = re.search(r"MONITORING_PATTERNS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)", src, re.DOTALL)
    assert block_match, "MONITORING_PATTERNS export not found in plugin source"
    block = block_match.group(1)
    return [re.compile(lit.strip().strip("/")) for lit in re.findall(r"/(.*?)/", block)]


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-no-idle-monitoring.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_monitoring_patterns(self):
        src = _plugin_source()
        assert "MONITORING_PATTERNS" in src, "MONITORING_PATTERNS export missing"
        assert "DENY_MESSAGE" in src, "DENY_MESSAGE export missing"

    def test_tool_execute_before_hook_registered(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_NO_IDLE_MONITORING_ENFORCE" in src, "Env-var disable switch missing"

    def test_threshold_present(self):
        src = _plugin_source()
        assert "THRESHOLD" in src, "Threshold constant missing"

    def test_state_file_present(self):
        src = _plugin_source()
        assert "gludd-idle-monitoring.json" in src, "State file path missing"


class TestMonitoringPatternMatcher:
    @pytest.fixture(scope="class")
    @staticmethod
    def patterns() -> list[re.Pattern[str]]:
        return _extract_monitoring_patterns(_plugin_source())

    @pytest.mark.parametrize(
        "cmd",
        [
            "make gate-status-check",
            "make gate-progress",
            "make gate-logs",
            "make gate-tail",
            "make gate-wait-report",
            "make ci-status",
            "make ci-verdict-fast",
            "make ci-cooldown-status",
            "make git-status",
            "make agent-worktree-list",
            "make worktree-health-check",
        ],
    )
    def test_deny_on_monitoring_patterns(self, patterns, cmd):
        assert any(p.search(cmd) for p in patterns), f"Should count as monitoring: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "make gate-background",
            "make lint",
            "make test-unit",
            "make ship-commit MSG='fix'",
            "make agent-worktree BRANCH=agent-fix",
            "make clean-tmp",
        ],
    )
    def test_allow_on_productive_make(self, patterns, cmd):
        assert not any(p.search(cmd) for p in patterns), f"Should not count as monitoring: {cmd!r}"
