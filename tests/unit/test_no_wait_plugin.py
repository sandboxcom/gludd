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


def _extract_ci_poll_patterns(src: str) -> list[re.Pattern[str]]:
    block_match = re.search(r"CI_POLL_DISPATCH_PATTERNS[^=]*=\s*Object\.freeze\(\[(.*?)\]\)", src, re.DOTALL)
    assert block_match, "CI_POLL_DISPATCH_PATTERNS export not found in plugin source"
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


class TestCiPollDispatchPatterns:
    """Structural + behavioral pin on CI_POLL_DISPATCH_PATTERNS.

    Per AGENTS.md 'CI-Poll Subagents Are Forbidden' (2026-07-08):
    dispatch prompts matching poll/loop/wait-for-CI patterns are denied.
    """

    @pytest.fixture(scope="class")
    def patterns(self) -> list[re.Pattern[str]]:
        return _extract_ci_poll_patterns(_plugin_source())

    @pytest.fixture(scope="class")
    def src(self) -> str:
        return _plugin_source()

    def test_exports_ci_poll_dispatch_patterns(self, src):
        assert "CI_POLL_DISPATCH_PATTERNS" in src, "CI_POLL_DISPATCH_PATTERNS export missing from plugin source"

    def test_exports_ci_poll_deny_message(self, src):
        assert "CI_POLL_DENY_MESSAGE" in src, "CI_POLL_DENY_MESSAGE export missing from plugin source"

    def test_pattern_every_n_min_exists(self, src):
        assert re.search(r"/\\bevery\\s\+\\d\+\\s\*\(\?:min\|minutes\?\)\\b/i", src), (
            "Pattern for 'every N min' not found in CI_POLL_DISPATCH_PATTERNS"
        )

    def test_pattern_poll_ci_verdict_exists(self, src):
        assert re.search(r"/\\bpoll\\s\.\*\\bmake\\s\+ci-verdict/i", src), (
            "Pattern for 'poll.*ci-verdict' not found in CI_POLL_DISPATCH_PATTERNS"
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "poll CI until terminal and report back",
            "wait for CI green then commit",
            "loop make ci-verdict every 30 seconds until it passes",
            "wait until CI is green",
            "polling for CI status until green",
            "make ci-await for the current branch",
            "check every 5 min whether CI finished",
            "poll the make ci-verdict output and summarize",
            "run every 2 minutes up to 10 iterations to check CI",
        ],
    )
    def test_deny_on_ci_poll_prompts(self, patterns, prompt):
        assert any(p.search(prompt) for p in patterns), f"Should deny CI-poll dispatch prompt: {prompt!r}"

    @pytest.mark.parametrize(
        "prompt",
        [
            "add a new test for the enforce-no-wait plugin",
            "fix lint errors in src/general_ludd/daemon.py",
            "run make ci-verdict-safe FORCE=1 to check CI once",
            "write a structural test for the guardrail-pattern skill",
            "make gate-background and poll gate-status-check from a subagent",
        ],
    )
    def test_allow_on_normal_prompts(self, patterns, prompt):
        assert not any(p.search(prompt) for p in patterns), f"Should allow normal dispatch prompt: {prompt!r}"


class TestDenyMessageContract:
    def test_message_mentions_dispatch(self):
        src = _plugin_source()
        assert "DISPATCH" in src.upper() or "dispatch" in src, (
            "DENY_MESSAGE should direct the agent to dispatch subagents"
        )

    def test_message_mentions_env_override(self):
        src = _plugin_source()
        assert "GLUDD_NO_WAIT_ENFORCE=0" in src, "DENY_MESSAGE or plugin should name the env-var override"
