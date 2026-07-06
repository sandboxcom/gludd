"""Behavior pin for the no-wait enforcement plugin.

Per AGENTS.md "Background Operations NEVER Block Dispatch" (2026-07-06):
the main thread must dispatch subagents and poll — never sleep. This test
extracts the plugin's exported WAIT_PATTERNS regex list and DENY_MESSAGE
from the TypeScript source and exercises each against the spec cases.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-no-wait.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_regex_literals(src: str) -> list[str]:
    pat = re.compile(r"/\^(.*?)\$/", re.DOTALL)
    matches = pat.findall(src)
    return [m for m in matches]


def _extract_wait_patterns(src: str) -> list[re.Pattern[str]]:
    block_match = re.search(r"WAIT_PATTERNS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)", src, re.DOTALL)
    assert block_match, "WAIT_PATTERNS export not found in plugin source"
    block = block_match.group(1)
    return [re.compile(lit.strip().strip("/")) for lit in re.findall(r"/(.*?)/", block)]


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-no-wait.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_wait_patterns(self):
        src = _plugin_source()
        assert "WAIT_PATTERNS" in src, "WAIT_PATTERNS export missing"
        assert "DENY_MESSAGE" in src, "DENY_MESSAGE export missing"

    def test_tool_execute_before_hook_registered(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_NO_WAIT_ENFORCE" in src, "Env-var disable switch missing"


class TestWaitPatternMatcher:
    @pytest.fixture(scope="class")
    def patterns(self) -> list[re.Pattern[str]]:
        return _extract_wait_patterns(_plugin_source())

    @pytest.mark.parametrize(
        "cmd",
        [
            "sleep 60 && make gate-status-check",
            "sleep 300 && make gate-bg-check",
            "sleep 5",
            "make gate-tail",
            "make gate-status-check",
            "make gate-bg-check",
        ],
    )
    def test_deny_on_wait_patterns(self, patterns, cmd):
        assert any(p.search(cmd) for p in patterns), f"Should deny: {cmd!r}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "make gate-background",
            "make lint",
            "make test-unit",
            "make git-status",
            "make test-specific TESTFILE=tests/unit/test_foo.py",
        ],
    )
    def test_allow_on_normal_make(self, patterns, cmd):
        assert not any(p.search(cmd) for p in patterns), f"Should allow: {cmd!r}"


class TestDenyMessageContract:
    def test_message_mentions_dispatch(self):
        src = _plugin_source()
        assert "DISPATCH" in src.upper() or "dispatch" in src, (
            "DENY_MESSAGE should direct the agent to dispatch subagents"
        )

    def test_message_mentions_env_override(self):
        src = _plugin_source()
        assert "GLUDD_NO_WAIT_ENFORCE=0" in src, (
            "DENY_MESSAGE or plugin should name the env-var override"
        )
