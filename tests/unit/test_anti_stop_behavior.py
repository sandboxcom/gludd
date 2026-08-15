"""TDD tests for the multitasking/anti-stop behavioral enforcement.

These tests verify that the CODE MECHANISMS preventing premature stops,
main-thread blocking, and batch-wait patterns actually exist and function.

Written FIRST (TDD red), then implementation verified against them.
"""

from pathlib import Path

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
AGENTS_MD = ROOT / "AGENTS.md"
ENFORCE_MAKE = ROOT / ".opencode" / "plugin" / "enforce-make.ts"
ENFORCE_STOP = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
ENFORCE_FLOOR = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
ENFORCE_DELEGATE = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
TEST_COMMIT_GATE = ROOT / "tests" / "unit" / "test_commit_gate_freshness.py"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestBatchPushTarget:
    """batch-push is the sanctioned push target (replaced ship-commit)."""

    def test_batch_push_exists(self):
        assert _recipe("batch-push"), "batch-push target must exist"

    def test_batch_push_uses_make_subcall(self):
        recipe = _recipe("batch-push")
        assert "$(MAKE)" in recipe, "batch-push must use $(MAKE) for sub-targets"

    def test_batch_push_uses_sandboxcom_key(self):
        recipe = _recipe("batch-push")
        # AA023 rework: batch-push pushes DIRECTLY (no nested git-push-sandboxcom
        # re-entry, which re-ran every guard) using the sandboxcom SSH key and
        # records the push verdict after the push lands.
        assert "GIT_SSH_COMMAND" in recipe and "sandboxcom" in recipe, (
            "batch-push must push directly to sandboxcom via the sandboxcom SSH key"
        )
        assert "_record-push-verdict" in recipe, "batch-push must record the push verdict AFTER the push lands"


class TestForegroundBlockGuardrail:
    """enforce-make.ts MUST block long foreground commands."""

    def test_foreground_block_exists(self):
        content = plugin_contract_source(ENFORCE_MAKE)
        assert "gate-background" in content or "Long-running foreground" in content, (
            "enforce-make.ts must contain foreground-block guardrail"
        )

    def test_blocks_make_gate(self):
        content = plugin_contract_source(ENFORCE_MAKE)
        assert '"gate"' in content or "'gate'" in content or "isGate" in content, (
            "Foreground block must target 'make gate'"
        )

    def test_blocks_make_test_unit(self):
        content = plugin_contract_source(ENFORCE_MAKE)
        assert "test-unit" in content, "Foreground block must target 'make test-unit'"

    def test_blocks_make_qa(self):
        content = plugin_contract_source(ENFORCE_MAKE)
        assert '"qa"' in content or "'qa'" in content or "isQa" in content, "Foreground block must target 'make qa'"

    def test_mentions_alternative(self):
        content = plugin_contract_source(ENFORCE_MAKE)
        assert "gate-background" in content, "Block message must mention gate-background alternative"


class TestStopPatternEnforcer:
    """enforce-stop.ts MUST default to blocking (not advisory)."""

    def test_blocking_default(self):
        content = plugin_contract_source(ENFORCE_STOP)
        assert 'permissionDecision: "deny"' in content, "enforce-stop.ts must have hard-deny permissionDecision blocks"

    def test_has_ratchet_stop_audit(self):
        content = plugin_contract_source(ENFORCE_STOP)
        assert "ratchet" in content.lower(), "enforce-stop.ts must have ratchet-based stop audit"

    def test_has_deferral_patterns(self):
        content = plugin_contract_source(ENFORCE_STOP)
        assert "SUBAGENT_TEXT_MARKERS" in content, "enforce-stop.ts must detect deferral/subagent-result patterns"

    def test_has_question_tool_block(self):
        content = plugin_contract_source(ENFORCE_STOP)
        assert '"question"' in content or "'question'" in content, "enforce-stop.ts must block the question tool"


class TestAdaptiveDelegationEnforcement:
    """Delegation stays adaptive while retaining a hard ten-agent ceiling."""

    def test_agents_md_has_10_minimum(self):
        content = AGENTS_MD.read_text()
        assert "Minimum 10 Subagents" in content, "AGENTS.md must document the 10-subagent minimum"

    def test_agents_md_has_anti_stall_rule(self):
        content = AGENTS_MD.read_text()
        assert "ANTI-STALL" in content or "anti-stall" in content.lower(), (
            "AGENTS.md must have the ANTI-STALL RULE section"
        )

    def test_agents_md_lists_forbidden_main_thread_commands(self):
        content = AGENTS_MD.read_text()
        assert "make gate" in content and "NEVER run on the main thread" in content, (
            "AGENTS.md must list forbidden main-thread commands"
        )

    def test_enforce_floor_defaults_to_ten(self):
        content = ENFORCE_FLOOR.read_text()
        assert '"10"' in content, "enforce-floor.ts FLOOR must default to 10"

    def test_enforce_delegate_defaults_to_ten(self):
        content = ENFORCE_DELEGATE.read_text()
        assert '"10"' in content, "enforce-delegate.ts FLOOR must default to 10"

    def test_enforce_stop_has_ten_agent_ceiling_and_opt_in_minimum(self):
        content = plugin_contract_source(ENFORCE_STOP)
        assert "HARD_MAX_DISPATCHES = 10" in content
        assert "REQUIRED_AGENT_MIN" in content
        assert "CONFIGURED_AGENT_MIN !== undefined" in content

    def test_settings_json_floor_is_five(self):
        settings = (ROOT / ".claude" / "settings.json").read_text()
        assert '"CLAUDE_AGENT_FLOOR": "5"' in settings, ".claude/settings.json must set CLAUDE_AGENT_FLOOR to 5"


class TestMainThreadRestriction:
    """AGENTS.md must codify the main-thread command restriction."""

    def test_has_dispatch_pattern(self):
        content = AGENTS_MD.read_text()
        assert "batch-push" in content, "AGENTS.md must reference batch-push as the dispatch mechanism"

    def test_forbids_lint_on_main_thread(self):
        content = AGENTS_MD.read_text()
        assert "make lint" in content, "AGENTS.md must mention make lint in the restriction"

    def test_forbids_typecheck_on_main_thread(self):
        content = AGENTS_MD.read_text()
        assert "make typecheck" in content, "AGENTS.md must mention make typecheck in the restriction"

    def test_describes_wave_pattern(self):
        content = AGENTS_MD.read_text()
        assert "ZERO analysis text" in content or "zero analysis" in content.lower(), (
            "AGENTS.md must describe the wave pattern (zero analysis text between waves)"
        )
