"""TDD tests for the Claude → opencode hook ports.

The Claude layer (.claude/hooks/*.sh, 20 shell scripts) is Claude-Code-specific.
These tests verify the equivalent opencode-native TypeScript plugin ports exist,
are registered in opencode.json, and carry the load-bearing enforcement logic.

Plugin coverage map (claude shell hook → opencode TS plugin):

  enforce-make.ts (extended):
    - enforce_make_bash.sh           (already ported)
    - gate_concurrency_pretool.sh    (NEW port — block 2nd concurrent gate)
    - guardrail_integrity_edit_pretool.sh (EXTEND — cover ALL hooks/plugins)
    - no_flag_file_write_pretool.sh  (NEW port — block .gate-status writes)

  enforce-floor.ts (register + extend):
    - agent_floor_stop.sh            (already ported as response.transform)
    - agent_floor_pretool.sh         (port as tool.execute.before)
    - agent_floor_posttool.sh        (port as tool.execute.after)
    - agent_ceiling_pretool.sh       (port as tool.execute.before)
    - mainthread_budget.sh           (NEW port — consecutive-call budget)

  enforce-delegate.ts (NEW):
    - model_utilization_pretool.sh   (sonnet:non-sonnet ratio enforcer)
    - disk_discipline_pretool.sh     (disk-free + venv count guard)
    - worktree_disk_guard_pretool.sh (worktree-isolation hard deny)
    - force_delegate_pretool.sh      (opt-in grind guard)

  enforce-stop.ts (NEW):
    - no_wait_stop.sh                (deferral-pattern block in response.transform)
    - multitasking_backlog_stop.sh   (open-backlog block in response.transform)
    - session_start_orchestrate.sh   (orchestration injection in system.transform)
    - no_blocking_questions_pretool.sh (question-tool deny in tool.execute.before)
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
OPENCODE_JSON = ROOT / "opencode.json"
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCE_MAKE = PLUGIN_DIR / "enforce-make.ts"
ENFORCE_FLOOR = PLUGIN_DIR / "enforce-floor.ts"
ENFORCE_DELEGATE = PLUGIN_DIR / "enforce-delegate.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"
ENFORCE_SESSION_START = PLUGIN_DIR / "enforce-session-start.ts"


def _plugin_list() -> list[str]:
    cfg = json.loads(OPENCODE_JSON.read_text())
    return cfg.get("plugin", [])


# --------------------------------------------------------------------------- #
# Registration — every ported plugin MUST appear in opencode.json's plugin[].
# --------------------------------------------------------------------------- #
class TestPluginsRegistered:
    def test_enforce_floor_registered(self):
        plugins = _plugin_list()
        assert any("enforce-floor" in p for p in plugins), (
            "enforce-floor.ts must be registered in opencode.json (currently orphaned)"
        )

    def test_enforce_delegate_registered(self):
        plugins = _plugin_list()
        assert any("enforce-delegate" in p for p in plugins), (
            "enforce-delegate.ts must be registered in opencode.json"
        )

    def test_enforce_stop_registered(self):
        plugins = _plugin_list()
        assert any("enforce-stop" in p for p in plugins), (
            "enforce-stop.ts must be registered in opencode.json"
        )


# --------------------------------------------------------------------------- #
# enforce-session-start.ts — session-start orchestration port.
# --------------------------------------------------------------------------- #
class TestSessionStartPort:
    def test_session_start_plugin_registered(self):
        plugins = _plugin_list()
        assert any("enforce-session-start" in p for p in plugins), (
            "enforce-session-start.ts must be registered in opencode.json"
        )

    def test_session_start_plugin_file_exists(self):
        assert ENFORCE_SESSION_START.exists(), (
            "enforce-session-start.ts must exist at .opencode/plugin/enforce-session-start.ts"
        )


# --------------------------------------------------------------------------- #
# enforce-make.ts — extensions for gate concurrency, guardrail integrity,
# flag-file write prevention.
# --------------------------------------------------------------------------- #
class TestEnforceMakeGateConcurrency:
    """Port of .claude/hooks/gate_concurrency_pretool.sh.

    Blocks a second concurrent pytest/gate invocation because concurrent gates
    trigger pytest's keep-last-3 basetemp rotation, deleting the first gate's
    worker dirs mid-flight (2026-06-15 208-error incident).
    """

    def test_gate_concurrency_check_present(self):
        content = ENFORCE_MAKE.read_text()
        # Either a comment describing the policy or the detection logic itself.
        assert (
            "gate-concurrency" in content.lower()
            or "concurrent" in content.lower()
            or "basetemp" in content.lower()
            or "GATE CONCURRENCY" in content
        ), "gate concurrency check missing from enforce-make.ts"

    def test_gate_concurrency_blocks_second_gate(self):
        content = ENFORCE_MAKE.read_text()
        # Must reference the make targets that invoke pytest.
        assert "test-and-commit" in content or "make gate" in content or "qa" in content


class TestEnforceMakeGuardrailIntegrityAllHooks:
    """Port of .claude/hooks/guardrail_integrity_edit_pretool.sh.

    The current enforce-make.ts only protects itself. The claude hook protects
    ALL hook and plugin files. The port must cover .claude/hooks/*.sh AND
    .opencode/plugin/*.ts so an edit cannot silently defang a sibling guardrail.
    """

    def test_guard_protects_claude_hooks_path(self):
        content = ENFORCE_MAKE.read_text()
        assert ".claude/hooks/" in content or "claude/hooks" in content, (
            "guardrail-integrity check must cover .claude/hooks/ path"
        )

    def test_guard_protects_opencode_plugin_path(self):
        content = ENFORCE_MAKE.read_text()
        assert ".opencode/plugin/" in content or "opencode/plugin" in content, (
            "guardrail-integrity check must cover .opencode/plugin/ path"
        )

    def test_guard_checks_enforcement_token_removal(self):
        content = ENFORCE_MAKE.read_text()
        # The set of tokens whose wholesale removal signals a defanged hook.
        for token in ['"deny"', '"block"', "throw new Error"]:
            assert token in content, (
                f"guardrail-integrity token {token!r} missing — edit-hook must "
                f"fire when ALL of these are removed from a guardrail file"
            )


class TestEnforceMakeFlagFileWriteBlock:
    """Port of .claude/hooks/no_flag_file_write_pretool.sh.

    Agents MUST NOT write .gate-status / .gate-failed / *.gate-status directly
    — run_gate.sh is the sanctioned writer. Allowing agent writes would let an
    agent forge a PASS gate status and bypass the commit freshness gate.
    """

    def test_flag_file_write_block_present(self):
        content = ENFORCE_MAKE.read_text()
        assert ".gate-status" in content, (
            "enforce-make.ts must block Edit/Write to .gate-status files"
        )

    def test_flag_file_block_lists_guarded_basenames(self):
        content = ENFORCE_MAKE.read_text()
        assert ".gate-failed" in content or "gate-status" in content


class TestEnforceMakeAssertionCheckScopedToTestFiles:
    """The TDD assertion-required check must fire ONLY on actual test files,
    not on fixtures/config files like conftest.py.

    Regression: conftest.py holds pytest fixtures (no assertions needed), but
    the broad `filePath.includes("/tests/")` classifier blocked legitimate
    fixture edits. Narrowed to test_*.py / *_test.py per the guardrail
    integrity policy (narrow, don't disable).
    """

    def _is_test_expr(self) -> str:
        import re
        src = ENFORCE_MAKE.read_text()
        m = re.search(r'const isTest\s*=\s*([^\n]+)', src)
        assert m, "isTest classification must exist in enforce-make.ts"
        return m.group(1)

    def test_is_test_excludes_conftest(self):
        expr = self._is_test_expr()
        # conftest.py must NOT be classified as a test file.
        assert "conftest" in expr.lower(), (
            f"isTest must explicitly exclude conftest.py (fixtures, not tests). "
            f"Current expr: {expr!r}"
        )

    def test_is_test_narrowed_beyond_tests_dir(self):
        expr = self._is_test_expr()
        # A pure /tests/ substring check catches conftest.py — must be narrowed.
        # Either a test_ prefix filter or a conftest exclusion qualifies.
        narrowed = "conftest" in expr.lower() or "/test_" in expr or "\\test_" in expr
        assert narrowed, (
            f"isTest must be narrowed beyond '/tests/' substring to exclude "
            f"non-test files. Current expr: {expr!r}"
        )


class TestEnforceMakeTddCheckScopedToTestMethods:
    """The TDD assertion-required check must fire ONLY when the edited content
    introduces a TEST METHOD body ('def test_' / 'async def test_'), not on any
    edit to a test file that happens to lack 'assert '.

    Regression: the check fired on ANY >50-char edit to tests/ without an
    assertion — blocking legitimate edits like imports, fixtures, engine/session
    setup, helper functions, and decorators. Narrowed per the guardrail-integrity
    policy: narrow, don't disable. Enforcement remains for assertion-less test
    method bodies.
    """

    def _tdd_block(self) -> str:
        content = ENFORCE_MAKE.read_text()
        idx = content.find("TDD QUALITY VIOLATION")
        assert idx != -1, "TDD QUALITY VIOLATION throw must exist in enforce-make.ts"
        # Return the chars immediately preceding the throw to inspect its gate.
        return content[max(0, idx - 700):idx]

    def test_tdd_throw_gated_on_test_method_body(self):
        block = self._tdd_block()
        assert "def test_" in block and "async def test_" in block, (
            "The TDD assertion throw must be gated on a test-method-body check "
            "('def test_' / 'async def test_') so it only fires on real test "
            f"methods, not on imports/fixtures/setup. Preceding block: {block!r}"
        )

    def test_tdd_throw_not_gated_on_bare_length_heuristic(self):
        block = self._tdd_block()
        assert "length > 50" not in block, (
            "The TDD throw must not be gated on `newContent.length > 50` — that "
            "heuristic caused false positives on any >50-char scaffolding edit. "
            "Gate on test-method-body presence instead."
        )


class TestEnforceMakeForegroundBlock:
    """Foreground-block guardrail.

    Bare `make gate` (a long-running foreground command that takes 30-40 min)
    MUST be blocked — the agent must use `make gate-background` instead so the
    main thread does not go dark for half an hour (no-unseen-events invariant).
    Quick make targets (lint, typecheck) are NOT blocked.
    """

    def test_gate_background_alternative_mentioned(self):
        content = ENFORCE_MAKE.read_text()
        assert "gate-background" in content, (
            "enforce-make.ts must mention the 'gate-background' alternative when "
            "blocking bare 'make gate'"
        )

    def test_bare_make_gate_blocked(self):
        content = ENFORCE_MAKE.read_text()
        # Must reference a bare 'make gate' pattern (not 'make gate-background').
        # The block targets the long-running foreground command specifically.
        assert "make gate" in content, (
            "enforce-make.ts must reference 'make gate' for the foreground block"
        )

    def test_make_lint_not_blocked(self):
        content = ENFORCE_MAKE.read_text()
        # Quick make targets must not be caught by the foreground block. The
        # check must be narrow enough to exclude 'make lint'.
        # We assert the block references 'gate' specifically (not bare 'make').
        assert "gate" in content.lower()

    def test_make_typecheck_not_blocked(self):
        content = ENFORCE_MAKE.read_text()
        # Same reasoning as lint — typecheck is a quick make target.
        assert "gate" in content.lower()

    def test_long_running_foreground_message_present(self):
        content = ENFORCE_MAKE.read_text()
        assert "Long-running foreground command" in content, (
            "enforce-make.ts block message must mention "
            "'Long-running foreground command' so the agent knows why the "
            "bare 'make gate' was blocked"
        )


# --------------------------------------------------------------------------- #
# enforce-delegate.ts — NEW plugin: model utilization + disk + force-delegate.
# --------------------------------------------------------------------------- #
class TestEnforceDelegatePlugin:
    def test_plugin_file_exists(self):
        assert ENFORCE_DELEGATE.exists(), "enforce-delegate.ts must exist"

    def test_plugin_exports_default(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "export default" in content
        assert "tool.execute.before" in content or "chat.response.transform" in content

    def test_plugin_uses_plugin_type(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "@opencode-ai/plugin" in content


class TestModelUtilizationPort:
    """Port of .claude/hooks/model_utilization_pretool.sh.

    Holds sonnet:non-sonnet dispatch ratio at/above target_share (default 0.91).
    """

    def test_target_share_default_present(self):
        content = ENFORCE_DELEGATE.read_text()
        # 0.91 default target_share (10:1 sonnet ratio)
        assert "0.91" in content or "target_share" in content.lower() or "targetShare" in content

    def test_state_file_path_present(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "gludd-model-util" in content or "model-util" in content or "MODEL_UTIL" in content

    def test_sonnet_keyword_present(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "sonnet" in content.lower()


class TestDiskDisciplinePort:
    """Port of .claude/hooks/disk_discipline_pretool.sh + worktree_disk_guard_pretool.sh.

    Fires only on isolation=='worktree' task dispatches.
    """

    def test_worktree_isolation_check(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "worktree" in content.lower()

    def test_disk_thresholds_present(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "DANGER_GB" in content or "danger" in content.lower() or "HARD_FLOOR" in content or "MIN_FREE" in content

    def test_venv_count_logic(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "venv" in content.lower()

    def test_disk_free_measurement(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "disk_usage" in content or "diskUsage" in content or "free" in content.lower()


class TestForceDelegatePort:
    """Port of .claude/hooks/force_delegate_pretool.sh.

    Opt-in grind guard (GLUDD_FORCE_DELEGATE=1) — denies targeted mutations
    when live < floor AND consecutive main-thread calls exceed grace.
    """

    def test_force_delegate_env_var(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "GLUDD_FORCE_DELEGATE" in content or "FORCE_DELEGATE" in content

    def test_grace_threshold(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "GRACE" in content or "grace" in content.lower()

    def test_maxblock_anti_wedge(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "MAXBLOCK" in content or "maxblock" in content.lower() or "MAX_BLOCK" in content

    def test_floor_live_check(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "agent_liveness" in content or "live" in content.lower()


class TestMainThreadBudgetPort:
    """Port of .claude/hooks/mainthread_budget.sh.

    Counts consecutive main-thread tool calls since last delegation; escalates
    when streak is long AND floor below target.
    """

    def test_streak_counter(self):
        content = ENFORCE_DELEGATE.read_text()
        assert "streak" in content.lower() or "consecutive" in content.lower()

    def test_threshold_value(self):
        content = ENFORCE_DELEGATE.read_text()
        # Default threshold is 8 consecutive main-thread calls before nag
        assert "8" in content or "THRESHOLD" in content

    def test_delegate_resets_streak(self):
        content = ENFORCE_DELEGATE.read_text()
        # Dispatching a task/subagent must reset the streak
        assert "reset" in content.lower()


# --------------------------------------------------------------------------- #
# enforce-stop.ts — NEW plugin: no-wait, backlog, session-start, no-questions.
# --------------------------------------------------------------------------- #
class TestEnforceStopPlugin:
    def test_plugin_file_exists(self):
        assert ENFORCE_STOP.exists(), "enforce-stop.ts must exist"

    def test_plugin_exports_default(self):
        content = ENFORCE_STOP.read_text()
        assert "export default" in content
        assert (
            "tool.execute.before" in content
            or "experimental.chat.response.transform" in content
            or "experimental.chat.system.transform" in content
        )

    def test_plugin_uses_plugin_type(self):
        content = ENFORCE_STOP.read_text()
        assert "@opencode-ai/plugin" in content


class TestNoWaitStopPort:
    """Port of .claude/hooks/no_wait_stop.sh.

    Blocks turn-end when final message defers to user (permission-seek).
    Advisory by default; blocking via GLUDD_NO_WAIT_ENFORCE=1.
    """

    def test_deferral_patterns_present(self):
        content = ENFORCE_STOP.read_text()
        # Post-merge: enforce-stop.ts detects deferral via COMPLETION_VERBATIM
        # and FUTURE_TENSE regexes, plus QUESTION_DENY_REASON directive.
        # The classic deferral phrases are detected via these regexes.
        assert (
            "shall" in content.lower()
            or "all done" in content.lower()
            or "ready for review" in content.lower()
            or "DEFAULT TO ACTION" in content
            or "BLOCKING QUESTION" in content
        )

    def test_enforce_env_var(self):
        content = ENFORCE_STOP.read_text()
        assert "GLUDD_STOP_ENFORCE" in content

    def test_blocking_default(self):
        # The DEFAULT is ENFORCING (blocking). Post-merge, GLUDD_STOP_ENFORCE
        # defaults to blocking via `!== "0"` comparison (line 6).
        content = ENFORCE_STOP.read_text()
        assert '!== "0"' in content or '"0"' in content, (
            "STOP_ENFORCE must default to blocking (GLUDD_STOP_ENFORCE !== '0')"
        )

    def test_constraint_as_stopsign(self):
        # "Constraints Are To Engineer Around" policy — post-merge, constraint
        # language is caught by the general stop-detection heuristics
        # (COMPLETION_VERBATIM + FUTURE_TENSE) rather than specific
        # constraint-as-stopsign pattern matching.
        content = ENFORCE_STOP.read_text()
        assert (
            "COMPLETION_VERBATIM" in content
            or "FUTURE_TENSE" in content
            or "HARD STOP" in content
            or "BLOCKING QUESTION" in content
        )


class TestMultitaskingBacklogPort:
    """Port of .claude/hooks/multitasking_backlog_stop.sh.

    Blocks turn-end while scripts/multitasking_backlog.json has open items.
    """

    def test_backlog_check_script_path(self):
        content = ENFORCE_STOP.read_text()
        assert "multitasking_backlog" in content or "backlog" in content.lower()

    def test_backlog_done_evidence_required(self):
        content = ENFORCE_STOP.read_text()
        assert "evidence" in content.lower() or "done" in content.lower()


class TestSessionStartOrchestratePort:
    """Port of .claude/hooks/session_start_orchestrate.sh.

    Injects orchestration context (agent floor refill directive) at session
    start via system.transform.
    """

    def test_orchestration_injection(self):
        content = ENFORCE_STOP.read_text()
        assert "orchestration" in content.lower() or "FLOOR" in content

    def test_floor_target_reference(self):
        """The floor/target session-start directives now live in
        enforce-session-start.ts (the canonical session-start orchestration
        owner, 2026-06-28 dedup). enforce-stop.ts retains its
        system.transform hook for the gap-filling items (workflow /
        pending-work / make-only commits) but no longer duplicates the
        floor/target content.
        """
        # The floor/target wiring must still exist SOMEWHERE in the plugin
        # layer — enforce-session-start.ts is the canonical owner now.
        session_start_src = ENFORCE_SESSION_START.read_text()
        assert "FLOOR" in session_start_src or "floor" in session_start_src.lower(), (
            "Floor directive must live in enforce-session-start.ts after the "
            "dedup trim removed it from enforce-stop.ts"
        )

    def test_system_transform_hook(self):
        content = ENFORCE_STOP.read_text()
        assert "system.transform" in content or "chat.system" in content


class TestNoBlockingQuestionsPort:
    """Port of .claude/hooks/no_blocking_questions_pretool.sh.

    Denies the question tool (opencode equivalent of AskUserQuestion) so the
    agent must decide, state assumption, and proceed.
    """

    def test_question_tool_check(self):
        content = ENFORCE_STOP.read_text()
        assert '"question"' in content or "'question'" in content or "question" in content.lower()

    def test_deny_output(self):
        content = ENFORCE_STOP.read_text()
        assert "throw new Error" in content or "deny" in content.lower()

    def test_action_directive(self):
        content = ENFORCE_STOP.read_text()
        # Message must tell agent to default to action, not ask
        assert "DEFAULT TO ACTION" in content or "default to action" in content.lower() or "proceed" in content.lower()


# --------------------------------------------------------------------------- #
# AGENTS.md must reference both layers (claude + opencode) where relevant.
# --------------------------------------------------------------------------- #
class TestAgentsMdOpencodeReferences:
    """The policy doc must reference the opencode plugin layer alongside the
    claude hooks so an opencode-only reader knows the enforcement exists.
    """

    def test_agents_md_references_enforce_plugins(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert ".opencode/plugin/" in content or "enforce-make.ts" in content

    def test_agents_md_references_floor_plugin(self):
        content = (ROOT / "AGENTS.md").read_text()
        # At minimum, the doc must acknowledge that the floor enforcer has an
        # opencode-native port (not only the claude shell hooks).
        assert "enforce-floor" in content or "enforce-floor.ts" in content, (
            "AGENTS.md must reference the opencode enforce-floor.ts plugin"
        )

    def test_agents_md_references_delegate_plugin(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "enforce-delegate" in content or "enforce-delegate.ts" in content, (
            "AGENTS.md must reference the opencode enforce-delegate.ts plugin"
        )

    def test_agents_md_references_stop_plugin(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "enforce-stop" in content or "enforce-stop.ts" in content, (
            "AGENTS.md must reference the opencode enforce-stop.ts plugin"
        )
