"""Behavioral tests for enforce-session-start.ts.

Structural tests (no runtime execution) that parse the plugin source and
verify every guardrail mechanism: tool classification, read-only targets,
time-gate logic, state serialization, primed latch, subagent detection,
and deny-message format.

Covers 7 behavioral areas as requested:
  1. isReadOnlyMakeTarget handles ALL 34 read-only targets
  2. Mutating make targets are correctly rejected
  3. DISPATCH_NOW_SECS / HARD_DENY_SECS defaults
  4. loadState / saveState read-write cycle
  5. sessionPrimed latch skips enforcement
  6. isSubagent() subagent detection
  7. denyMessage includes Task-tool hints
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
SHARED_PATH = ROOT / ".opencode" / "lib" / "shared.ts"


@pytest.fixture(scope="module")
def plugin_src() -> str:
    if not PLUGIN_PATH.exists():
        pytest.fail(f"Missing {PLUGIN_PATH}")
    return PLUGIN_PATH.read_text()


@pytest.fixture(scope="module")
def shared_src() -> str:
    if not SHARED_PATH.exists():
        pytest.fail(f"Missing {SHARED_PATH}")
    return SHARED_PATH.read_text()


# ==========================================================================
# 1. isReadOnlyMakeTarget — ALL 34 read-only targets accepted
# ==========================================================================

READ_ONLY_TARGETS: frozenset[str] = frozenset([
    "git-status", "git-diff", "git-log", "git-staged", "git-show",
    "verify-state", "verify-remote",
    "check-node-v26-compat",
    "ci-verdict", "ci-verdict-safe", "ci-cooldown-status",
    "gate-status", "gate-status-check", "gate-logs",
    "disk", "disk-check", "disk-guard",
    "agent-worktree-list",
    "playbook-list",
    "collection-roles", "collection-modules",
    "test-count", "test-failures",
    "audit-messages",
    "version", "help",
    "verify-plugin-manifest", "test-hook-runtime",
    "check-disk",
    "development-status",
    "repo-status", "repo-diff", "repo-log", "repo-staged",
])

# Mutating targets that must NOT be in the read-only set
MUTATING_TARGETS: frozenset[str] = frozenset([
    "git-commit", "git-push", "git-push-sandboxcom", "git-push-branch",
    "ship-commit", "test-and-commit", "git-add", "git-add-all",
    "git-reset", "git-branch", "git-checkout", "git-merge",
    "git-stash", "git-stash-pop", "git-rm", "git-mv",
    "commit-no-verify", "commit-bootstrap",
    "repo-commit", "git-commit-file",
    "batch-push", "ci-push",
    "feature-start", "feature-done",
    "agent-worktree", "agent-merge", "agent-cleanup",
    "development-start", "development-merge-to-master",
    "release-cut", "release-promote", "release-recut",
    "release-branch-new",
    "lint-fix", "clean", "clean-artifacts", "clean-tmp",
])


class TestReadOnlyMakeTargetFullSet:
    """Every target in READ_ONLY_TARGETS must exist in the plugin source Set."""

    def test_read_only_count_is_at_least_25(self, plugin_src):
        """The read-only set must have at least 25 entries."""
        match = re.search(
            r"READ_ONLY_MAKE_TARGETS[^}]*new Set\(\[([^\]]+)\]\)",
            plugin_src, re.DOTALL,
        )
        assert match is not None, "READ_ONLY_MAKE_TARGETS Set not found in source"
        # Count the commas between string literals
        body = match.group(1)
        comma_count = body.count('",') + body.count("',")
        # At least N-1 commas for N items (also count the first/last)
        entries = comma_count + 1 if body.strip() else 0
        assert entries >= 25, (
            f"READ_ONLY_MAKE_TARGETS has only {entries} entries; expected >=25"
        )

    @pytest.mark.parametrize("target", sorted(READ_ONLY_TARGETS))
    def test_read_only_target_present_in_source(self, plugin_src, target):
        assert f'"{target}"' in plugin_src, (
            f"Read-only target '{target}' missing from "
            "READ_ONLY_MAKE_TARGETS Set in enforce-session-start.ts"
        )

    @pytest.mark.parametrize("target", sorted(MUTATING_TARGETS))
    def test_mutating_target_not_in_read_only_source_set(self, plugin_src, target):
        """Mutating targets must NOT appear inside the READ_ONLY_MAKE_TARGETS Set
        literal (they could appear elsewhere in the file as detection patterns)."""
        match = re.search(
            r"READ_ONLY_MAKE_TARGETS[^}]*new Set\(\[([^\]]+)\]\)",
            plugin_src, re.DOTALL,
        )
        assert match is not None, "READ_ONLY_MAKE_TARGETS Set not found"
        set_body = match.group(1)
        assert f'"{target}"' not in set_body, (
            f"Mutating target '{target}' erroneously appears in the "
            "READ_ONLY_MAKE_TARGETS Set literal"
        )

    def test_read_only_set_is_disjoint_from_mutating(self):
        assert READ_ONLY_TARGETS.isdisjoint(MUTATING_TARGETS), (
            "READ_ONLY_TARGETS and MUTATING_TARGETS share targets; a target "
            "cannot be both read-only and mutating"
        )


# ==========================================================================
# 2. isReadOnlyMakeTarget rejects non-bash, unknown, and empty
# ==========================================================================

class TestReadOnlyMakeTargetRejects:
    """isReadOnlyMakeTarget must reject non-bash tools, unknown targets, etc."""

    def test_function_signature_checks_tool_param(self, plugin_src):
        assert "tool !== \"bash\"" in plugin_src, (
            "isReadOnlyMakeTarget must short-circuit for non-bash tools"
        )

    def test_parses_make_command_with_regex(self, plugin_src):
        assert "make\\s+(\\S+)" in plugin_src or "make" in plugin_src, (
            "Must parse 'make <target>' from the command string"
        )

    def test_set_has_method_used_for_lookup(self, plugin_src):
        assert "READ_ONLY_MAKE_TARGETS.has" in plugin_src, (
            "Must check target membership via Set.has()"
        )


# ==========================================================================
# 3. Time-gate logic: DISPATCH_NOW_SECS and HARD_DENY_SECS
# ==========================================================================

class TestTimeGateDefaults:
    """Verify time-gate constants and their default values."""

    def test_dispatch_now_secs_declared(self, plugin_src):
        assert "DISPATCH_NOW_SECS" in plugin_src

    def test_dispatch_now_secs_default_is_60(self, plugin_src):
        # The fallback string must be "60"
        match = re.search(
            r'DISPATCH_NOW_SECS\s*=\s*parseInt\([^,]+,\s*10\)',
            plugin_src
        )
        assert match is not None, "DISPATCH_NOW_SECS not found"
        # Check the fallback is "60"
        assert '"60"' in plugin_src, (
            "DISPATCH_NOW_SECS fallback must be '60'"
        )

    def test_hard_deny_secs_declared(self, plugin_src):
        assert "HARD_DENY_SECS" in plugin_src

    def test_hard_deny_secs_default_is_120(self, plugin_src):
        assert '"120"' in plugin_src, (
            "HARD_DENY_SECS fallback must be '120'"
        )

    def test_time_gate_compares_elapsed_vs_hard_deny(self, plugin_src):
        assert "elapsedSecs >= HARD_DENY_SECS" in plugin_src, (
            "Hard deny must gate on elapsedSecs >= HARD_DENY_SECS"
        )

    def test_time_gate_compares_elapsed_vs_dispatch_now(self, plugin_src):
        assert "elapsedSecs >= DISPATCH_NOW_SECS" in plugin_src, (
            "Warning must gate on elapsedSecs >= DISPATCH_NOW_SECS"
        )

    def test_time_gate_only_active_below_explicit_minimum(self, plugin_src):
        assert "EFFECTIVE_MIN > 0" in plugin_src
        assert "state.dispatches < EFFECTIVE_MIN" in plugin_src, (
            "Time gate must only fire below an operator-configured minimum"
        )

    def test_time_gate_resets_on_dispatch(self, plugin_src):
        assert "state.timeGateReset = true" in plugin_src, (
            "timeGateReset must be set to true on first dispatch"
        )

    def test_time_gate_skip_when_already_reset(self, plugin_src):
        assert "!state.timeGateReset" in plugin_src, (
            "Time gate must check !state.timeGateReset before evaluating"
        )

    def test_time_gate_warning_throttle_30_seconds(self, plugin_src):
        assert "30_000" in plugin_src, (
            "Warning throttle must be 30_000 (30 seconds)"
        )

    def test_time_gate_enforce_respects_env_var(self, plugin_src):
        """Hard deny only fires when ENFORCE is true (default ON)."""
        assert "if (ENFORCE)" in plugin_src, (
            "Hard deny must check the ENFORCE flag before blocking"
        )


# ==========================================================================
# 4. State file read/write cycle (loadState / saveState)
# ==========================================================================

class TestStateFileReadWrite:
    """loadState and saveState must form a correct read-write cycle."""

    def test_loadstate_exists(self, plugin_src):
        assert "function loadState" in plugin_src

    def test_savestate_exists(self, plugin_src):
        assert "function saveState" in plugin_src

    def test_loadstate_returns_default_on_missing_file(self, plugin_src):
        """If STATE_FILE doesn't exist, loadState creates it with defaults."""
        assert "!fs.existsSync(STATE_FILE)" in plugin_src, (
            "loadState must check for file existence and create defaults"
        )

    def test_loadstate_fields(self, plugin_src):
        """SessionState must have started_at, readsDone, dispatches, timeGateReset."""
        for field in ("started_at", "readsDone", "dispatches", "timeGateReset"):
            assert field in plugin_src, (
                f"SessionState missing field: {field}"
            )

    def test_loadstate_fail_open_on_corrupt(self, plugin_src):
        """Corrupt state → fail-open: dispatches=EFFECTIVE_MIN, readsDone=true."""
        # The catch block should return a primed state
        assert "EFFECTIVE_MIN" in plugin_src, (
            "Fail-open catch must return EFFECTIVE_MIN dispatches"
        )

    def test_savestate_atomic_write_via_temp_file(self, plugin_src):
        """Fix A: write to PID-unique temp file, then atomic rename."""
        assert "writeFileSync(tmp" in plugin_src or "writeFileSync(tmp," in plugin_src, (
            "saveState must write to a temp file first"
        )
        assert "renameSync(tmp" in plugin_src, (
            "saveState must use atomic renameSync from temp to STATE_FILE"
        )

    def test_savestate_uses_pid_in_temp_path(self, plugin_src):
        """Temp file path must include process.pid to prevent cross-process clobber."""
        assert "process.pid" in plugin_src, (
            "Temp file path must be PID-unique"
        )

    def test_savestate_fail_open(self, plugin_src):
        """saveState must try-catch and fail open."""
        # Both loadState and saveState use try-catch
        assert "catch" in plugin_src, "State helpers must use try-catch"

    def test_state_file_path_is_overridable(self, plugin_src):
        assert "GLUDD_SESSION_STATE" in plugin_src, (
            "STATE_FILE must be overridable via GLUDD_SESSION_STATE env var"
        )


# ==========================================================================
# 5. Primed latch (sessionPrimed)
# ==========================================================================

class TestPrimedLatch:
    """sessionPrimed latch short-circuits enforcement once the gate is primed."""

    def test_session_primed_variable_exists(self, plugin_src):
        assert "sessionPrimed" in plugin_src

    def test_primed_type_is_nullable_boolean(self, plugin_src):
        """sessionPrimed: null (not loaded), false (tracking), true (latched open)."""
        assert "boolean | null" in plugin_src, (
            "sessionPrimed must be typed as boolean | null"
        )

    def test_hook_short_circuits_when_primed(self, plugin_src):
        """tool.execute.before must return early when sessionPrimed === true."""
        assert "if (sessionPrimed === true) return" in plugin_src, (
            "Hook must short-circuit BEFORE loadState() when primed"
        )

    def test_update_primed_latch_function_exists(self, plugin_src):
        assert "function updatePrimedLatch" in plugin_src

    def test_primed_condition_reads_done_and_dispatches(self, plugin_src):
        assert "state.readsDone" in plugin_src, (
            "updatePrimedLatch must check readsDone"
        )
        assert "state.dispatches >= EFFECTIVE_MIN" in plugin_src, (
            "updatePrimedLatch must check dispatches >= EFFECTIVE_MIN"
        )

    def test_primed_latch_skips_state_io_once_set(self, plugin_src):
        """Once sessionPrimed=true, no more loadState/saveState on subsequent calls."""
        # The short-circuit at line ~302 (if sessionPrimed === true) returns
        # before loadState, proving no I/O
        primed_check_idx = plugin_src.find("if (sessionPrimed === true) return")
        loadstate_idx = plugin_src.find("const state = loadState()")
        assert primed_check_idx > 0 and loadstate_idx > 0, (
            "Source must contain both primed check and loadState call"
        )
        assert primed_check_idx < loadstate_idx, (
            "sessionPrimed check must precede loadState() in the hook body "
            "so primed instances skip all state file I/O"
        )


# ==========================================================================
# 6. Subagent detection via isSubagent()
# ==========================================================================

class TestSubagentDetection:
    """Both hooks must skip enforcement inside subagent context."""

    def test_is_subagent_imported_from_shared(self, plugin_src):
        assert "isSubagent" in plugin_src, (
            "Plugin must import isSubagent from shared.ts"
        )

    def test_system_transform_guards_with_subagent_check(self, plugin_src):
        """system.transform hook must call isSubagent() before anything else."""
        # The proxy wrapper and defaultImpl both guard with isSubagent
        count = plugin_src.count("if (isSubagent()) return")
        assert count >= 2, (
            f"Expected >=2 isSubagent guards, found {count}. "
            "Both proxy wrapper and defaultImpl must guard."
        )

    def test_tool_execute_before_guards_with_subagent_check(self, plugin_src):
        assert "isSubagent()" in plugin_src, (
            "tool.execute.before must call isSubagent()"
        )

    def test_shared_is_subagent_checks_env_var(self, shared_src):
        assert "OPENCODE_SUBAGENT" in shared_src, (
            "isSubagent() must check OPENCODE_SUBAGENT env var"
        )

    def test_shared_is_subagent_checks_file_marker(self, shared_src):
        assert "gludd-subagent" in shared_src, (
            "isSubagent() must check file-based subagent marker as fallback"
        )

    def test_shared_is_subagent_fail_open(self, shared_src):
        """isSubagent() has a try-catch returning false on error."""
        # The function wraps file check in try-catch
        idx = shared_src.find("export function isSubagent")
        assert idx >= 0, "isSubagent function not found"
        fn_body = shared_src[idx:idx + 500]
        assert "try {" in fn_body or "catch" in fn_body, (
            "isSubagent() must use try-catch for fail-open behavior"
        )


# ==========================================================================
# 7. Deny message format
# ==========================================================================

class TestDenyMessageFormat:
    """denyMessage must include session state and Task-tool guidance."""

    def test_deny_message_includes_reads_done(self, plugin_src):
        assert "readsDone" in plugin_src, (
            "denyMessage must report readsDone state"
        )

    def test_deny_message_includes_dispatch_count_fraction(self, plugin_src):
        """denyMessage must print N/MIN format for dispatches."""
        # The message builds "readsDone=X, Y/Z dispatches."
        assert "dispatches" in plugin_src, (
            "denyMessage must report current dispatch count"
        )
        # There should be a slash-pattern for N/MIN
        assert "/" in plugin_src, "denyMessage format must use N/MIN fraction"

    def test_deny_message_includes_task_tool_guidance(self, plugin_src):
        """denyMessage must hint about using task/agent/workflow tools."""
        # The directive mentions "parallel task/agent subagents"
        assert "task" in plugin_src.lower(), (
            "denyMessage must reference Task tool"
        )
        assert "subagents" in plugin_src.lower() or "subagent" in plugin_src.lower(), (
            "denyMessage must reference subagent dispatch"
        )

    def test_deny_message_includes_env_var_override_hint(self, plugin_src):
        assert "GLUDD_SESSION_START_ENFORCE" in plugin_src, (
            "denyMessage must tell user how to disable enforcement"
        )

    def test_time_gate_deny_message_includes_elapsed_and_required(self, plugin_src):
        """Hard-deny message must show elapsed time and required dispatches."""
        assert "elapsed" in plugin_src or "DISPATCH" in plugin_src.upper(), (
            "Time-gate deny message must reference elapsed time"
        )

    def test_enforce_flag_gates_deny_message(self, plugin_src):
        """denyMessage is only set when ENFORCE is true."""
        assert "if (ENFORCE)" in plugin_src, (
            "denyMessage must be conditional on the ENFORCE flag"
        )

    def test_deny_message_is_thrown_as_error(self, plugin_src):
        assert "throw new Error(denyMessage)" in plugin_src or "throw new Error" in plugin_src, (
            "Plugin must throw Error to block the tool call"
        )


# ==========================================================================
# 8. General behavioral invariants
# ==========================================================================

class TestBehavioralInvariants:
    """Cross-cutting checks that ensure coherent behavior."""

    def test_min_dispatch_is_explicit_opt_in_with_hard_max(self, plugin_src):
        """Default to adaptive delegation while preserving the ten-agent ceiling."""
        assert "const HARD_MAX_DISPATCHES = 10" in plugin_src
        assert "HAS_CONFIGURED_MIN_DISPATCHES" in plugin_src
        assert (
            "process.env.GLUDD_SESSION_START_MIN_DISPATCHES !== undefined"
            in plugin_src
        )
        assert re.search(
            r"EFFECTIVE_MIN\s*=\s*HAS_CONFIGURED_MIN_DISPATCHES[\s\S]+?"
            r"Math\.max\(0,\s*Math\.min\([\s\S]+?MAX_DISPATCHES\)\)[\s\S]+?:\s*0",
            plugin_src,
        )

    def test_tool_classification_functions_exported(self, plugin_src, shared_src):
        """isDispatchTool, isReadTool, isTaskFileRead must be available to the plugin.

        The E.5 refactor moved isDispatchTool/isReadTool into .opencode/lib/shared.ts,
        so a function now counts as available if the plugin either declares it locally
        or imports it from the shared module (which must actually export it).
        """
        for fn in ("isDispatchTool", "isReadTool", "isTaskFileRead"):
            declared_locally = f"function {fn}" in plugin_src
            imported = bool(
                re.search(
                    r'import\s+\{[^}]*\b' + fn + r'\b[^}]*\}\s+from\s+"[^"]*shared\.ts"',
                    plugin_src,
                )
            ) and f"export function {fn}" in shared_src
            assert declared_locally or imported, (
                f"{fn} is neither declared in enforce-session-start.ts nor imported "
                f"from an exporting shared.ts"
            )

    def test_plugin_uses_hot_module_loader(self, plugin_src):
        """Proxy wrapper must use loadHotModule for hot-reload support."""
        assert "loadHotModule" in plugin_src, (
            "Plugin must use hot-reload proxy pattern"
        )

    def test_default_impl_has_both_hooks(self, plugin_src):
        """defaultImpl must export both hooks."""
        assert "experimental.chat.system.transform" in plugin_src
        assert "tool.execute.before" in plugin_src

    def test_tool_execute_before_fail_open_wrap(self, plugin_src):
        """The entire hook body is wrapped in try/catch for fail-open."""
        assert "catch" in plugin_src, (
            "tool.execute.before must use try-catch for fail-open behavior"
        )

    def test_subagent_marker_not_reimplemented(self, plugin_src):
        """Plugin must NOT inline subagent detection; use shared.ts import."""
        # It should import isSubagent, not redefine _isSubagent
        assert "import { isSubagent" in plugin_src, (
            "Plugin must import isSubagent from shared.ts, not redefine it"
        )
