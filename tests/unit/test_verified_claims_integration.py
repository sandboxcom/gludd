"""Integration tests for enforce-verified-claims.ts via the hook harness.

Invokes the plugin's tool.execute.before hook with simulated bash commands
and verifies the verdict matches expected behavior from plugin_test_exports.ts.

Per AGENTS.md "Evidence-Based Response Policy" and "Done Claims Require
Observable Verification Evidence": the plugin must block commit messages
containing done-words without evidence, and allow them with evidence.
"""

from __future__ import annotations

import pytest

from tests.unit._hook_fixtures import HookEnv, hook_plugin_env_impl

PLUGIN = "enforce-verified-claims.ts"


@pytest.fixture
def hook_plugin_env(tmp_path):
    yield from hook_plugin_env_impl(tmp_path)


def _invoke_commit_msg(hook_plugin_env: HookEnv, msg: str) -> str:
    """Simulate a `make ship-commit MSG='...'` bash command and return verdict.

    The plugin's tool.execute.before throws with permissionDecision:"deny" on
    block (non-zero exit + error message), or returns cleanly on allow.
    Returns "block" or "allow".
    """
    cmd = f'make ship-commit MSG="{msg}"'
    result = hook_plugin_env.invoke(
        PLUGIN,
        "tool.execute.before",
        input={"tool": "bash", "args": {"command": cmd}},
        timeout=10,
    )
    if result.returncode != 0:
        stderr = result.stderr or ""
        stdout = result.stdout or ""
        combined = stderr + stdout
        if "permissionDecision" in combined and "deny" in combined:
            return "block"
        if "BLOCKED" in combined:
            return "block"
        return "block"  # any non-zero exit is a block for this plugin
    return "allow"


class TestToolExecuteBeforeBlocks:
    """Integration: tool.execute.before blocks commit messages with done-words
    and no evidence, allows them with evidence."""

    def test_done_without_evidence_blocked(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "done") == "block"

    def test_done_with_hash_allowed(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "done abc1234") == "allow"

    def test_green_without_evidence_blocked(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "the gate is green now") == "block"

    def test_green_with_ci_output_allowed(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "CI GREEN on the fix") == "allow"

    def test_fixed_without_evidence_blocked(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "fixed the bug") == "block"

    def test_fixed_with_test_count_allowed(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "fixed the bug, 42 passed") == "allow"

    def test_clean_message_not_blocked(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "update CI config") == "allow"

    def test_passing_alone_blocked(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "tests are passing") == "block"

    def test_landed_with_verified_allowed(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "landed, VERIFIED master@abc1234") == "allow"

    def test_complete_with_gate_passed_allowed(self, hook_plugin_env: HookEnv):
        assert _invoke_commit_msg(hook_plugin_env, "=== GATE: PASSED ===\ncomplete") == "allow"


class TestToolExecuteBeforeNonCommitPassthrough:
    """Non-commit `make` commands must pass through cleanly."""

    def test_make_test_not_blocked(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            PLUGIN,
            "tool.execute.before",
            input={"tool": "bash", "args": {"command": "make test done"}},
            timeout=10,
        )
        assert result.returncode == 0, result.stderr

    def test_non_bash_tool_not_blocked(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            PLUGIN,
            "tool.execute.before",
            input={"tool": "edit", "args": {"file_path": "foo.py", "new_string": "done"}},
            timeout=10,
        )
        assert result.returncode == 0, result.stderr

    def test_make_plain_not_starting_with_make_not_blocked(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            PLUGIN,
            "tool.execute.before",
            input={"tool": "bash", "args": {"command": "echo done"}},
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
