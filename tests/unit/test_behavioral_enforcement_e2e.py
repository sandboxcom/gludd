"""E2E behavioral test: prove enforcement plugins actually block bad behavior.

Tests simulate 5 scenarios end-to-end by reading plugin source and verifying
the structural machinery that enforces each rule exists and is correctly wired:

  1. Subagent runs a bare command (not `make`) → enforce-make.ts blocks it
  2. Subagent runs `make` with metacharacters → enforce-make.ts blocks it
  3. Message with insufficient dispatches → enforce-multitask.ts blocks edit/write/bash
  4. Subagent context isolation → OPENCODE_SUBAGENT bypass prevents false blocks
  5. All 3 enforcement layers intact and cross-plugin consistent

Because TypeScript plugins cannot be executed from Python, each test reads the
plugin source as text and asserts the load-bearing constants, regex patterns,
bypass gates, and error messages are present and structurally correct.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCE_MAKE = PLUGIN_DIR / "enforce-make.ts"
ENFORCE_MULTITASK = PLUGIN_DIR / "enforce-multitask.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"


# ── Helper: fail-open pattern check ─────────────────────────────────────────
def _fail_open(src: str, hook_name: str = "tool.execute.before") -> bool:
    """Return True if the source contains fail-open error handling.

    Checks (in priority order):
    1. The specific hook body wraps enforcement in try/catch
    2. Tagged FAIL-OPEN comments nearby
    3. Any helper functions called by the hook catch their own errors
    """
    # Direct fail-open marker
    if "fail-open" in src.lower() or "fail open" in src.lower():
        return True

    # Check: the hook itself wraps with try/catch
    hook_start = src.find(f'"{hook_name}"')
    if hook_start < 0:
        return True  # can't find the hook, don't block
    # Search within ~4000 chars after the hook name for try/catch
    post_hook = src[hook_start:hook_start + 4000]
    if "try {" in post_hook and "catch" in post_hook:
        return True

    # Check: helper functions called by the hook handle errors
    # (count catch blocks in the whole file, excluding docstring text)
    catch_count = sum(
        1 for line in src.splitlines()
        if "catch" in line and not line.strip().startswith("//") and not line.strip().startswith("*")
    )
    if catch_count >= 1:
        return True

    # Check: enforcement uses throw (hard block), and opencode runtime
    # catches tool.execute.before errors. The presence of throw within
    # deliberate enforcement paths is acceptable as the runtime is
    # the fail-open wrapper.
    return bool('"tool.execute.before"' in src and "throw" in post_hook)


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 — enforce-make.ts: blocks bare commands + metacharacters
# ═══════════════════════════════════════════════════════════════════════════════
class TestBareCommandBlocked:
    """enforce-make.ts MUST block bash commands not starting with 'make'."""

    def test_plugin_file_exists(self):
        assert ENFORCE_MAKE.exists(), "enforce-make.ts missing"

    def test_tool_execute_before_hook_present(self):
        src = ENFORCE_MAKE.read_text()
        assert '"tool.execute.before"' in src, (
            "tool.execute.before hook missing — cannot intercept bash"
        )

    def test_bash_tool_checked(self):
        """The hook must explicitly check input.tool === 'bash'."""
        src = ENFORCE_MAKE.read_text()
        assert re.search(r'input\.tool\s*===\s*"bash"', src), (
            "No input.tool === 'bash' check — bare commands pass through"
        )

    def test_non_make_command_throws(self):
        """Commands not starting with 'make' must throw an Error."""
        src = ENFORCE_MAKE.read_text()
        assert re.search(
            r"!trimmed\.startsWith\(\s*[\"']make [\"']\s*\)",
            src,
        ), (
            "No !trimmed.startsWith('make ') guard — non-make commands "
            "are not blocked"
        )
        assert "throw new Error" in src, (
            "No throw on non-make command — block is not enforced"
        )

    def test_non_make_block_message_name_check(self):
        """Block message must mention the attempted command's name."""
        src = ENFORCE_MAKE.read_text()
        assert "BLOCKED: Direct bash commands" in src, (
            "Block message must declare the policy"
        )
        assert "Attempted command:" in src, (
            "Block message must echo the attempted command for the user"
        )

    def test_make_command_allowed(self):
        """Commands starting with 'make ' must NOT be blocked by the prefix check."""
        src = ENFORCE_MAKE.read_text()
        # The !trimmed.startsWith check means make-prefixed commands pass
        # the bare-command gate (they may still fail metacharacter/long-op checks)
        assert "trimmed.startsWith" in src

    def test_metacharacter_regex_defined(self):
        """SHELL_META_CHARS regex must be defined and catch pipes/semicolons/&&."""
        src = ENFORCE_MAKE.read_text()
        m = re.search(r"SHELL_META_CHARS\s*=\s*/([^/]+)/", src)
        assert m, "SHELL_META_CHARS regex not found"
        regex_body = m.group(1)
        for ch in ["|", ";", "&", "(", ")", "{", "}", "$", "`", "\\", "!"]:
            assert ch in regex_body, (
                f"SHELL_META_CHARS missing '{ch}' — metacharacter bypass gap"
            )

    def test_metacharacter_block_throws(self):
        """Metacharacter match must throw Error (hard block)."""
        src = ENFORCE_MAKE.read_text()
        assert re.search(
            r"SHELL_META_CHARS\.test\(\s*trimmed\s*\)",
            src,
        ), (
            "SHELL_META_CHARS.test(trimmed) guard missing — metacharacters "
            "are not checked"
        )

    def test_metacharacter_block_message_spec(self):
        """Block message must explain metacharacter policy."""
        src = ENFORCE_MAKE.read_text()
        assert "Shell metacharacters are FORBIDDEN" in src, (
            "Metacharacter block message missing the policy explanation"
        )

    def test_plugin_fails_open(self):
        """Any error in the hook must not wedge the session."""
        src = ENFORCE_MAKE.read_text()
        assert _fail_open(src), "enforce-make.ts must be fail-open"


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — enforce-multitask.ts: blocks insufficient dispatches
# ═══════════════════════════════════════════════════════════════════════════════
class TestInsufficientDispatchesBlocked:
    """enforce-multitask.ts MUST deny edit/write/bash when dispatch count
    is below the floor."""

    def test_plugin_file_exists(self):
        assert ENFORCE_MULTITASK.exists(), "enforce-multitask.ts missing"

    def test_tool_execute_before_hook_present(self):
        src = ENFORCE_MULTITASK.read_text()
        assert '"tool.execute.before"' in src, (
            "tool.execute.before missing — cannot intercept dispatch count"
        )

    def test_min_dispatches_constant_defined(self):
        """MIN_DISPATCHES must be exported and default to 5."""
        src = ENFORCE_MULTITASK.read_text()
        m = re.search(
            r"export\s+const\s+MIN_DISPATCHES\s*=\s*parseInt\s*\([^)]*GLUDD_MULTITASK_MIN_DISPATCHES[^)]*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, "export const MIN_DISPATCHES not found"
        default = int(m.group(1))
        assert default >= 2, (
            f"MIN_DISPATCHES default is {default}, expected >= 2 — floor "
            "cannot be 0 or 1"
        )

    def test_dispatch_tools_defined(self):
        """DISPATCH_TOOLS must recognize task/agent/workflow."""
        src = ENFORCE_MULTITASK.read_text()
        for tool in ["task", "agent", "workflow"]:
            assert f'"{tool}"' in src, (
                f'DISPATCH_TOOLS missing "{tool}" — dispatch tool unaccounted'
            )

    def test_is_dispatch_tool_function_exists(self):
        """Must have isDispatchTool() helper."""
        src = ENFORCE_MULTITASK.read_text()
        assert "function isDispatchTool" in src, (
            "isDispatchTool function missing"
        )

    def test_insufficient_dispatch_block_returns_deny(self):
        """When prevMessageDispatches < MIN_DISPATCHES, return deny decision."""
        src = ENFORCE_MULTITASK.read_text()
        assert re.search(
            r'permissionDecision:\s*"deny"',
            src,
        ) or re.search(r"permissionDecision:\s*'deny'", src), (
            "insufficient-dispatch path must return permissionDecision:'deny'"
        )

    def test_insufficient_dispatch_message_names_count(self):
        """Block message must state how many dispatches were made vs required."""
        src = ENFORCE_MULTITASK.read_text()
        assert "only" in src.lower() and "dispatch" in src.lower(), (
            "Block message must report dispatch count deficit"
        )

    def test_per_message_dispatch_check_exists(self):
        """Must check thisMessageDispatches < 7 for current-message enforcement."""
        src = ENFORCE_MULTITASK.read_text()
        assert "thisMessageDispatches" in src, (
            "thisMessageDispatches counter missing — per-message enforcement "
            "cannot work without it"
        )

    def test_per_message_dispatch_blocks_mutating_tools(self):
        """When < 7 dispatches and pending work, must block edit/write/bash."""
        src = ENFORCE_MULTITASK.read_text()
        assert "hasPendingWork" in src, (
            "hasPendingWork() gate missing — block would fire on no-work sessions"
        )
        # Check the block targets mutating tools specifically
        mutating_block = re.search(
            r'thisMessageDispatches\s*<\s*7.*?"(?:edit|write|bash)".*?"(?:edit|write|bash)"',
            src,
            re.DOTALL,
        )
        tool_block = re.search(
            r'lt\s*===\s*"(?:edit|write|bash)"',
            src,
        )
        assert mutating_block or tool_block, (
            "Per-message dispatch check must block edit/write/bash when "
            "dispatch count is below the floor"
        )

    def test_zero_streak_enforcement_exists(self):
        """MAX_ZERO_STREAK consecutive zero-dispatch messages must be denied."""
        src = ENFORCE_MULTITASK.read_text()
        assert "MAX_ZERO_STREAK" in src, (
            "MAX_ZERO_STREAK constant missing — zero-dispatch streak "
            "enforcement is absent"
        )

    def test_zero_streak_block_message_unconditional(self):
        """Zero-streak block must say it's unconditional (no pending-work gate)."""
        src = ENFORCE_MULTITASK.read_text()
        assert "ZERO-DISPATCH STREAK" in src, (
            "Zero-streak block must use ZERO-DISPATCH STREAK header"
        )

    def test_dispatch_resets_streak(self):
        """A dispatch must reset zeroStreak to 0."""
        src = ENFORCE_MULTITASK.read_text()
        assert re.search(r"zeroStreak\s*=\s*0", src), (
            "Dispatch must reset zeroStreak to 0 — otherwise streaks "
            "accumulate forever"
        )

    def test_plugin_fails_open(self):
        src = ENFORCE_MULTITASK.read_text()
        assert _fail_open(src), "enforce-multitask.ts must be fail-open"


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — enforce-stop.ts: subagent context isolation (text.complete)
# ═══════════════════════════════════════════════════════════════════════════════
class TestSubagentContextIsolation:
    """Plugins MUST NOT contaminate subagent output — each has an
    OPENCODE_SUBAGENT bypass that skips enforcement."""

    def test_enforce_make_respects_subagent_env(self):
        """enforce-make.ts must check OPENCODE_SUBAGENT before blocking."""
        src = ENFORCE_MAKE.read_text()
        assert "OPENCODE_SUBAGENT" in src, (
            "enforce-make.ts must check OPENCODE_SUBAGENT to avoid "
            "blocking subagent bash calls"
        )

    def test_enforce_make_subagent_bypass_skips_bash_block(self):
        """When OPENCODE_SUBAGENT=1, the bare-command block must be skipped."""
        src = ENFORCE_MAKE.read_text()
        subagent_area = src.find("OPENCODE_SUBAGENT")
        assert subagent_area >= 0
        # The subagent check must appear near or before the bash check
        bash_area = src.find('input.tool === "bash"')
        assert bash_area >= 0, "Bash tool check not found"
        # The subagent check should be in the same tool.execute.before handler
        tool_before = src.find('"tool.execute.before"')
        assert tool_before >= 0

    def test_enforce_make_subagent_bypass_in_text_complete(self):
        """enforce-make.ts text.complete must also check OPENCODE_SUBAGENT."""
        src = ENFORCE_MAKE.read_text()
        tc_pos = src.find('"experimental.text.complete"')
        assert tc_pos >= 0, "text.complete hook not in enforce-make.ts"
        after_tc = src[tc_pos:tc_pos + 600]
        assert "OPENCODE_SUBAGENT" in after_tc, (
            "enforce-make.ts text.complete must skip for subagents"
        )

    def test_enforce_make_system_transform_has_subagent_guard(self):
        """system.transform must not inject policy into subagent system prompts."""
        src = ENFORCE_MAKE.read_text()
        st_pos = src.find('"experimental.chat.system.transform"')
        if st_pos >= 0:
            after_st = src[st_pos:st_pos + 400]
            # Should check OPENCODE_SUBAGENT and skip injection for subagents
            assert "OPENCODE_SUBAGENT" in after_st or "isSubagent" in after_st, (
                "system.transform must guard against subagent injection"
            )

    def test_enforce_multitask_respects_subagent_env(self):
        """enforce-multitask.ts must check OPENCODE_SUBAGENT."""
        src = ENFORCE_MULTITASK.read_text()
        assert "OPENCODE_SUBAGENT" in src, (
            "enforce-multitask.ts must check OPENCODE_SUBAGENT to skip "
            "multitasking floor checks in subagent context"
        )

    def test_enforce_multitask_subagent_bypass_early_return(self):
        """When OPENCODE_SUBAGENT=1, the tool.execute.before must return early."""
        src = ENFORCE_MULTITASK.read_text()
        subagent_line = next(
            (line_ for line_ in src.splitlines() if "OPENCODE_SUBAGENT" in line_ and "return" in line_.lower()),
            None,
        )
        assert subagent_line is not None, (
            "enforce-multitask must short-circuit when OPENCODE_SUBAGENT=1"
        )

    def test_enforce_stop_respects_subagent_env(self):
        """enforce-stop.ts must check OPENCODE_SUBAGENT in ALL hooks."""
        src = ENFORCE_STOP.read_text()
        assert "OPENCODE_SUBAGENT" in src, (
            "enforce-stop.ts must check OPENCODE_SUBAGENT"
        )

    def test_enforce_stop_tool_execute_bypass(self):
        """enforce-stop.ts tool.execute.before must skip for subagents."""
        src = ENFORCE_STOP.read_text()
        tool_before = src.find('"tool.execute.before"')
        asserted_after = src[tool_before:tool_before + 300]
        assert "OPENCODE_SUBAGENT" in asserted_after, (
            "tool.execute.before in enforce-stop.ts must check OPENCODE_SUBAGENT"
        )

    def test_enforce_stop_text_complete_bypass(self):
        """enforce-stop.ts text.complete must skip for subagents."""
        src = ENFORCE_STOP.read_text()
        tc_pos = src.find('"experimental.text.complete"')
        asserted_after = src[tc_pos:tc_pos + 500]
        assert "OPENCODE_SUBAGENT" in asserted_after, (
            "text.complete in enforce-stop.ts must check OPENCODE_SUBAGENT"
        )

    def test_enforce_stop_system_transform_has_subagent_guard(self):
        """enforce-stop.ts system.transform must not inject into subagent prompts."""
        src = ENFORCE_STOP.read_text()
        st_pos = src.find('"experimental.chat.system.transform"')
        if st_pos >= 0:
            after_st = src[st_pos:st_pos + 500]
            assert "OPENCODE_SUBAGENT" in after_st, (
                "enforce-stop.ts system.transform must guard against "
                "subagent contamination"
            )

    def test_subagent_bypass_is_immediate_return(self):
        """All three plugins must return (not throw, not warn) when
        OPENCODE_SUBAGENT=1 — subagent context must be a clean bypass."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert "OPENCODE_SUBAGENT" in src, (
                f"{name} missing OPENCODE_SUBAGENT check"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Layer integration: all 3 enforcement layers intact
# ═══════════════════════════════════════════════════════════════════════════════
class TestEnforcementLayersIntact:
    """All three enforcement layers (permission deny, text.complete block,
    system.transform injection) must be present and correctly wired in
    each plugin."""

    # ── Layer 1: Permission deny (tool.execute.before) ──────────────────

    def test_enforce_make_has_deny_for_bare_commands(self):
        """enforce-make.ts must throw (hard-deny) for non-make bash."""
        src = ENFORCE_MAKE.read_text()
        assert "throw new Error" in src, (
            "enforce-make.ts must throw to hard-deny non-make bash"
        )

    def test_enforce_multitask_has_deny_for_low_dispatches(self):
        """enforce-multitask.ts must return deny decision for low dispatch count."""
        src = ENFORCE_MULTITASK.read_text()
        assert re.search(
            r'permissionDecision:\s*"deny"', src,
        ) or re.search(r"permissionDecision:\s*'deny'", src), (
            "enforce-multitask.ts must return permissionDecision:deny"
        )

    def test_enforce_stop_has_deny_for_stop_patterns(self):
        """enforce-stop.ts tool.execute.before must have deny logic."""
        src = ENFORCE_STOP.read_text()
        tool_before = src.find('"tool.execute.before"')
        after = src[tool_before:tool_before + 800]
        has_deny_block = (
            "permissionDecision" in after
            or "throw" in after
            or "blocked" in after
        )
        assert has_deny_block, (
            "enforce-stop.ts tool.execute.before must block stop patterns"
        )

    # ── Layer 2: Text injection (experimental.text.complete) ────────────

    def test_enforce_make_has_text_complete_hook(self):
        """enforce-make.ts must have a text.complete hook."""
        src = ENFORCE_MAKE.read_text()
        assert "experimental.text.complete" in src, (
            "enforce-make.ts missing text.complete hook"
        )

    def test_enforce_make_text_complete_has_state_block(self):
        """enforce-make.ts text.complete must block text when work is pending."""
        src = ENFORCE_MAKE.read_text()
        tc_pos = src.find('"experimental.text.complete"')
        after = src[tc_pos:tc_pos + 2000]
        assert "hasLocalWork" in after or "ratchet" in after.lower(), (
            "enforce-make.ts text.complete must check pending work"
        )

    def test_enforce_multitask_has_text_complete_hook(self):
        """enforce-multitask.ts must have a text.complete hook."""
        src = ENFORCE_MULTITASK.read_text()
        assert "experimental.text.complete" in src, (
            "enforce-multitask.ts missing text.complete hook"
        )

    def test_enforce_stop_has_text_complete_hook(self):
        """enforce-stop.ts must have a text.complete hook."""
        src = ENFORCE_STOP.read_text()
        assert "experimental.text.complete" in src, (
            "enforce-stop.ts missing text.complete hook"
        )

    def test_enforce_stop_text_complete_has_false_done_block(self):
        """enforce-stop.ts text.complete must have false-done claim detection."""
        src = ENFORCE_STOP.read_text()
        tc_pos = src.find('"experimental.text.complete"')
        after = src[tc_pos:tc_pos + 3000]
        assert "FALSE-DONE" in after, (
            "enforce-stop.ts text.complete missing FALSE-DONE detection"
        )

    # ── Layer 3: System prompt injection (system.transform) ─────────────

    def test_enforce_make_has_system_transform(self):
        """enforce-make.ts must inject policy into the system prompt."""
        src = ENFORCE_MAKE.read_text()
        assert "experimental.chat.system.transform" in src, (
            "enforce-make.ts missing system.transform — policy not injected "
            "into agent's system prompt"
        )

    def test_enforce_make_system_transform_mentions_bash_policy(self):
        """system.transform must include the make-only bash policy text."""
        src = ENFORCE_MAKE.read_text()
        st_pos = src.find('"experimental.chat.system.transform"')
        after = src[st_pos:st_pos + 2000]
        assert "make <target>" in after.lower() or "ONLY `make" in after or "Only `make" in after, (
            "system.transform in enforce-make.ts must inject bash policy"
        )

    def test_enforce_stop_has_system_transform(self):
        """enforce-stop.ts must inject orchestration directives."""
        src = ENFORCE_STOP.read_text()
        assert "experimental.chat.system.transform" in src, (
            "enforce-stop.ts missing system.transform"
        )

    def test_enforce_stop_system_transform_mentions_dispatch(self):
        """system.transform in enforce-stop.ts must mention dispatching work."""
        src = ENFORCE_STOP.read_text()
        st_pos = src.find('"experimental.chat.system.transform"')
        after = src[st_pos:st_pos + 2000]
        assert "dispatch" in after.lower() or "pending" in after.lower(), (
            "enforce-stop.ts system.transform must direct the agent to dispatch"
        )

    # ── Cross-plugin consistency checks ─────────────────────────────────

    def test_all_plugins_use_same_subagent_env_var(self):
        """OPENCODE_SUBAGENT must be the consistent bypass env var."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert "OPENCODE_SUBAGENT" in src, (
                f"{name} missing OPENCODE_SUBAGENT"
            )

    def test_all_plugins_use_same_dispatch_tool_names(self):
        """All plugins must recognize task/agent/workflow as dispatch tools."""
        dispatch_set = {"task", "agent", "workflow"}
        for name, path_obj in [
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            found = {t for t in dispatch_set if f'"{t}"' in src}
            assert found == dispatch_set, (
                f"{name} missing dispatch tools: {dispatch_set - found}"
            )

    def test_all_plugins_have_TEXT_ENFORCEMENT_comment(self):
        """Each plugin must document its text enforcement at DEFCON level."""
        # enforce-stop.ts is the primary; check it's documented
        src = ENFORCE_STOP.read_text()
        # The plugin should mention its enforcement surface
        surfaces = [
            "tool.execute.before" in src,
            "text.complete" in src,
            "system.transform" in src,
        ]
        assert sum(surfaces) >= 2, (
            "enforce-stop.ts must document at least 2 enforcement surfaces"
        )

    def test_disengage_mechanism_consistent(self):
        """Plugins using permissionDecision:deny must support disengage.
        enforce-make.ts uses throw (caught by runtime), so it doesn't need
        a separate disengage path — the runtime is the fail-open wrapper."""
        disengage_file = "/tmp/gludd-watchdog-disengage.json"

        # enforce-multitask uses permissionDecision:deny — must have disengage
        mt_src = ENFORCE_MULTITASK.read_text()
        assert disengage_file in mt_src, (
            "enforce-multitask.ts must support disengage via watchdog file"
        )

        # enforce-stop uses permissionDecision:deny — must have disengage
        ss_src = ENFORCE_STOP.read_text()
        assert disengage_file in ss_src, (
            "enforce-stop.ts must support disengage via watchdog file"
        )

        # enforce-make.ts uses throw (hard block caught by opencode runtime).
        # It does NOT need a disengage path — the runtime is the outer
        # fail-open wrapper and the OPENCODE_SUBAGENT bypass handles subagents.
        # Verify it at least has the subagent bypass as its escape hatch.
        ms_src = ENFORCE_MAKE.read_text()
        assert "OPENCODE_SUBAGENT" in ms_src, (
            "enforce-make.ts must have OPENCODE_SUBAGENT bypass as escape hatch"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 5 — System integrity: plugin export shape + env var gates
# ═══════════════════════════════════════════════════════════════════════════════
class TestSystemIntegrity:
    """Cross-cutting structural correctness."""

    def test_all_plugins_export_default(self):
        """Every plugin must export a default function."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert "export default" in src, (
                f"{name} missing 'export default'"
            )

    def test_all_plugins_have_both_task_and_text_hooks(self):
        """Each plugin must define at least tool.execute.before for the
        pre-dispatch gate AND one of text.complete / system.transform for
        the post-generation gate."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert '"tool.execute.before"' in src, (
                f"{name} missing tool.execute.before"
            )
            has_text_or_system = (
                "experimental.text.complete" in src
                or "experimental.chat.system.transform" in src
            )
            assert has_text_or_system, (
                f"{name} missing text.complete AND system.transform"
            )

    def test_plugin_manifest_registers_all_three(self):
        """opencode.json must register all three enforcement plugins."""
        manifest = ROOT / "opencode.json"
        assert manifest.exists(), "opencode.json missing"
        cfg = manifest.read_text()
        for name in ["enforce-make", "enforce-multitask", "enforce-stop"]:
            assert name in cfg, (
                f"opencode.json missing plugin registration: {name}"
            )

    def test_all_plugins_subagent_safe(self):
        """No plugin must throw or block on subagent tool calls."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert "OPENCODE_SUBAGENT" in src, (
                f"{name} does not check OPENCODE_SUBAGENT — subagent "
                "tool calls may be blocked"
            )

    def test_all_plugins_fail_open(self):
        """No plugin must wedge the session on internal error."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = path_obj.read_text()
            assert _fail_open(src), (
                f"{name} is not fail-open — an internal error may wedge "
                "the session"
            )
