"""Behavior pin for the enforce-batch-push.ts plugin.

Per AGENTS.md "Don't Push Every Commit" rule: pushing while CI is running
cancels the prior CI run, resulting in zero validation. The plugin blocks
`make git-push-sandboxcom`, `make development-push`, and `make batch-push`
when CI is in_progress on the target branch.

Tests:
  - Plugin structure: file exists, registered in opencode.json, has hooks
  - Push pattern matching: git-push-sandboxcom, development-push, batch-push
  - Branch resolution: master for git-push-sandboxcom, development for development-push
  - Env-var disable: GLUDD_BATCH_PUSH_ENFORCE=0, FORCE=1
  - Fail-open: exception safety
  - Subagent guard: OPENCODE_SUBAGENT check
  - Makefile target dependency checks
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-batch-push.ts"
MAKEFILE_PATH = ROOT / "Makefile"
OPENCODE_JSON_PATH = ROOT / "opencode.json"


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = OPENCODE_JSON_PATH.read_text()
        assert "enforce-batch-push.ts" in oc, "Plugin not registered in opencode.json"

    def test_tool_execute_before_hook(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_uses_exec_sync(self):
        src = _plugin_source()
        assert "execSync" in src, "Must use execSync for ci-verdict"

    def test_subagent_guard(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "Subagent guard missing"

    def test_proxy_pattern(self):
        src = _plugin_source()
        assert "loadHotModule" in src, "Hot-reload proxy pattern missing"

    def test_checks_bash_tool(self):
        src = _plugin_source()
        assert 'tool !== "bash"' in src, "Must check for bash tool"


class TestPushPatterns:
    def test_git_push_sandboxcom_pattern(self):
        src = _plugin_source()
        assert "git-push-sandboxcom" in src, (
            "Must reference git-push-sandboxcom in PUSH_PATTERNS"
        )

    def test_development_push_pattern(self):
        src = _plugin_source()
        assert "development-push" in src, (
            "Must reference development-push in PUSH_PATTERNS"
        )

    def test_batch_push_pattern(self):
        src = _plugin_source()
        assert "batch-push" in src, (
            "Must reference batch-push in PUSH_PATTERNS"
        )

    def test_patterns_are_word_boundary(self):
        src = _plugin_source()
        assert r"git-push-sandboxcom\b" in src or "git-push-sandboxcom\\b" in src, (
            "Patterns must use word boundaries to avoid partial matches"
        )


class TestBranchResolution:
    def test_git_push_sandboxcom_resolves_to_master(self):
        src = _plugin_source()
        assert '"master"' in src, "git-push-sandboxcom must resolve to master"

    def test_development_push_resolves_to_development(self):
        src = _plugin_source()
        assert '"development"' in src, "development-push must resolve to development"

    def test_unknown_command_returns_null(self):
        src = _plugin_source()
        assert "return null" in src, "Unknown push commands must return null (allow)"


class TestEnvVarDisable:
    def test_gludd_batch_push_enforce_disable(self):
        src = _plugin_source()
        assert 'GLUDD_BATCH_PUSH_ENFORCE === "0"' in src, (
            "Must check GLUDD_BATCH_PUSH_ENFORCE env var"
        )

    def test_force_bypass(self):
        src = _plugin_source()
        assert 'process.env.FORCE === "1"' in src, (
            "Must check FORCE=1 bypass"
        )

    def test_env_var_checked_before_ci_call(self):
        src = _plugin_source()
        enforce_pos = src.index("process.env.GLUDD_BATCH_PUSH_ENFORCE")
        ci_call_pos = src.index("if (isCiPending")
        assert enforce_pos < ci_call_pos, (
            "Env-var check must happen before CI check (early return)"
        )


class TestDenyMessage:
    def test_deny_message_mentions_ci_busy(self):
        src = _plugin_source()
        assert "CI-BUSY" in src, "Deny message must include CI-BUSY prefix"

    def test_deny_message_mentions_force_bypass(self):
        src = _plugin_source()
        assert "FORCE=1" in src, "Deny message must mention FORCE=1 bypass"

    def test_deny_message_mentions_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_BATCH_PUSH_ENFORCE=0" in src, (
            "Deny message must mention env override"
        )

    def test_ci_verdict_reference(self):
        src = _plugin_source()
        assert "ci-verdict" in src, "Deny message must reference ci-verdict"


class TestMakefilePushTargets:
    def test_git_push_sandboxcom_target_exists(self):
        makefile = MAKEFILE_PATH.read_text()
        assert re.search(r"^git-push-sandboxcom:", makefile, re.MULTILINE), (
            "git-push-sandboxcom target missing from Makefile"
        )

    def test_development_push_target_exists(self):
        makefile = MAKEFILE_PATH.read_text()
        assert re.search(r"^development-push:", makefile, re.MULTILINE), (
            "development-push target missing from Makefile"
        )

    def test_development_push_has_ci_busy_check(self):
        makefile = MAKEFILE_PATH.read_text()
        m = re.search(
            r"^development-push:(.*?)(?=\n[a-zA-Z_-]+:|\Z)",
            makefile, re.MULTILINE | re.DOTALL,
        )
        assert m, "development-push recipe block not found"
        assert "ci-busy-check" in m.group(1), (
            "development-push must depend on ci-busy-check"
        )

    def test_push_dev_has_ci_busy_check(self):
        makefile = MAKEFILE_PATH.read_text()
        m = re.search(
            r"^push-dev:(.*?)(?=\n[a-zA-Z_-]+:|\Z)",
            makefile, re.MULTILINE | re.DOTALL,
        )
        assert m, "push-dev recipe block not found"
        assert "ci-busy-check" in m.group(1), (
            "push-dev must depend on ci-busy-check"
        )
