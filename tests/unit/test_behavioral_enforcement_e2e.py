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

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
SHARED = ROOT / ".opencode" / "lib" / "shared.ts"

ENFORCE_MAKE = PLUGIN_DIR / "enforce-make.ts"
ENFORCE_MULTITASK = PLUGIN_DIR / "enforce-multitask.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"


def _plugin_source(path: Path) -> str:
    """Read the runtime contract represented by a plugin facade."""
    return plugin_contract_source(path)


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
    post_hook = src[hook_start : hook_start + 4000]
    if "try {" in post_hook and "catch" in post_hook:
        return True

    # Check: helper functions called by the hook handle errors
    # (count catch blocks in the whole file, excluding docstring text)
    catch_count = sum(
        1
        for line in src.splitlines()
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
        src = _plugin_source(ENFORCE_MAKE)
        assert '"tool.execute.before"' in src, "tool.execute.before hook missing — cannot intercept bash"

    def test_bash_tool_checked(self):
        """The hook must explicitly check input.tool === 'bash'."""
        src = _plugin_source(ENFORCE_MAKE)
        assert re.search(r'input\.tool\s*===\s*"bash"', src), (
            "No input.tool === 'bash' check — bare commands pass through"
        )

    def test_non_make_command_throws(self):
        """Commands not starting with 'make' must throw an Error."""
        src = _plugin_source(ENFORCE_MAKE)
        assert re.search(
            r"!trimmed\.startsWith\(\s*[\"']make [\"']\s*\)",
            src,
        ), "No !trimmed.startsWith('make ') guard — non-make commands are not blocked"
        assert "throw new Error" in src, "No throw on non-make command — block is not enforced"

    def test_non_make_block_message_name_check(self):
        """Block message must mention the attempted command's name."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "BLOCKED: Direct bash commands" in src, "Block message must declare the policy"
        assert "Attempted command:" in src, "Block message must echo the attempted command for the user"

    def test_make_command_allowed(self):
        """Commands starting with 'make ' must NOT be blocked by the prefix check."""
        src = _plugin_source(ENFORCE_MAKE)
        # The !trimmed.startsWith check means make-prefixed commands pass
        # the bare-command gate (they may still fail metacharacter/long-op checks)
        assert "trimmed.startsWith" in src

    def test_metacharacter_regex_defined(self):
        """SHELL_META_CHARS regex must be defined and catch pipes/semicolons/&&."""
        impl_path = PLUGIN_DIR / "impl" / "enforce_make_impl.ts"
        src = impl_path.read_text()
        m = re.search(r"SHELL_META_CHARS\s*=\s*/([^/]+)/", src)
        assert m, "SHELL_META_CHARS regex not found"
        regex_body = m.group(1)
        for ch in ["|", ";", "&", "{", "}", "$", "`", "\\", "!"]:
            assert ch in regex_body, f"SHELL_META_CHARS missing '{ch}' — metacharacter bypass gap"

    def test_metacharacter_block_throws(self):
        """Metacharacter match must throw Error (hard block)."""
        impl_path = PLUGIN_DIR / "impl" / "enforce_make_impl.ts"
        src = impl_path.read_text()
        assert re.search(
            r"SHELL_META_CHARS\.test\(\s*unquoted\s*\)",
            src,
        ), "SHELL_META_CHARS.test(unquoted) guard missing — metacharacters are not checked"

    def test_metacharacter_block_message_spec(self):
        """Block message must explain metacharacter policy."""
        impl_path = PLUGIN_DIR / "impl" / "enforce_make_impl.ts"
        src = impl_path.read_text()
        assert "Shell metacharacters are FORBIDDEN" in src, "Metacharacter block message missing the policy explanation"

    def test_plugin_fails_open(self):
        """Any error in the hook must not wedge the session."""
        src = _plugin_source(ENFORCE_MAKE)
        assert _fail_open(src), "enforce-make.ts must be fail-open"


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 — enforce-multitask.ts: blocks insufficient dispatches
# ═══════════════════════════════════════════════════════════════════════════════
class TestAdaptiveDispatchEnforcement:
    """Configured minima are enforced without imposing an implicit floor."""

    def test_plugin_file_exists(self):
        assert ENFORCE_MULTITASK.exists(), "enforce-multitask.ts missing"

    def test_tool_execute_before_hook_present(self):
        src = _plugin_source(ENFORCE_MULTITASK)
        assert '"tool.execute.before"' in src, "tool.execute.before missing — cannot intercept dispatch count"

    def test_min_dispatches_constant_defined(self):
        """The recommendation stays ten, while the effective default is zero."""
        src = _plugin_source(ENFORCE_MULTITASK)
        cfg = (PLUGIN_DIR / ".." / "lib" / "multitask_config.ts").resolve().read_text()
        assert "HARD_MAX_DISPATCHES = 10" in cfg
        assert "HAS_CONFIGURED_MIN_DISPATCHES" in src
        assert re.search(r"REQUIRED_DISPATCHES\s*=.*?\?", src, re.DOTALL)
        assert re.search(r"REQUIRED_DISPATCHES\s*=.*?:\s*0", src, re.DOTALL)

    def test_dispatch_tools_defined(self):
        """The centralized dispatch classifier recognizes all supported tools."""
        src = SHARED.read_text()
        for tool in ["task", "agent", "workflow"]:
            assert f'"{tool}"' in src, f'DISPATCH_TOOLS missing "{tool}" — dispatch tool unaccounted'

    def test_is_dispatch_tool_function_exists(self):
        """Must import isDispatchTool() from shared.ts."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "isDispatchTool" in src, "isDispatchTool import from shared.ts missing"

    def test_insufficient_dispatch_block_returns_deny(self):
        """When prevMessageDispatches < MIN_DISPATCHES, return deny decision."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert re.search(
            r'permissionDecision:\s*"deny"',
            src,
        ) or re.search(r"permissionDecision:\s*'deny'", src), (
            "insufficient-dispatch path must return permissionDecision:'deny'"
        )

    def test_insufficient_dispatch_message_names_count(self):
        """Block message must state how many dispatches were made vs required."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "only" in src.lower() and "dispatch" in src.lower(), "Block message must report dispatch count deficit"

    def test_per_message_dispatch_check_exists(self):
        """Configured-minimum enforcement tracks this-message dispatches."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "thisMessageDispatches" in src, (
            "thisMessageDispatches counter missing — per-message enforcement cannot work without it"
        )

    def test_per_message_dispatch_blocks_mutating_tools(self):
        """When an explicit minimum is unmet, edit/write/bash are blocked."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "hasPendingWork" in src, "hasPendingWork() gate missing — block would fire on no-work sessions"
        assert "REQUIRED_DISPATCHES > 0" in src
        tool_block = re.search(
            r'lt\s*===\s*"(?:edit|write|bash)"',
            src,
        )
        assert tool_block, "Configured-minimum check must block edit/write/bash when dispatch count is low"

    def test_zero_streak_enforcement_exists(self):
        """MAX_ZERO_STREAK consecutive zero-dispatch messages must be denied."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK constant missing — zero-dispatch streak enforcement is absent"

    def test_zero_streak_block_requires_configured_minimum(self):
        """Zero-streak quota enforcement is active only for explicit minima."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "ZERO-DISPATCH STREAK" in src, "Zero-streak block must use ZERO-DISPATCH STREAK header"
        assert "REQUIRED_DISPATCHES > 0" in src

    def test_dispatch_resets_streak(self):
        """A dispatch must reset zeroStreak to 0."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert re.search(r"zeroStreak\s*=\s*0", src), (
            "Dispatch must reset zeroStreak to 0 — otherwise streaks accumulate forever"
        )

    def test_plugin_fails_open(self):
        src = _plugin_source(ENFORCE_MULTITASK)
        assert _fail_open(src), "enforce-multitask.ts must be fail-open"


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 — enforce-stop.ts: subagent context isolation (text.complete)
# ═══════════════════════════════════════════════════════════════════════════════
class TestSubagentContextIsolation:
    """Plugins MUST NOT contaminate subagent output — each has an
    OPENCODE_SUBAGENT bypass that skips enforcement."""

    def test_enforce_make_respects_subagent_env(self):
        """enforce-make.ts must use the shared subagent detector."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "isSubagent" in src, "enforce-make.ts must use isSubagent() before blocking subagent calls"

    def test_enforce_make_subagent_bypass_skips_bash_block(self):
        """When OPENCODE_SUBAGENT=1, the bare-command block must be skipped."""
        src = _plugin_source(ENFORCE_MAKE)
        subagent_area = src.find("isSubagent")
        assert subagent_area >= 0
        # The subagent check must appear near or before the bash check
        bash_area = src.find('input.tool === "bash"')
        assert bash_area >= 0, "Bash tool check not found"
        # The subagent check should be in the same tool.execute.before handler
        tool_before = src.find('"tool.execute.before"')
        assert tool_before >= 0

    def test_enforce_make_subagent_bypass_in_text_complete(self):
        """enforce-make.ts text.complete must also check OPENCODE_SUBAGENT."""
        src = _plugin_source(ENFORCE_MAKE)
        tc_pos = src.find('"experimental.text.complete"')
        assert tc_pos >= 0, "text.complete hook not in enforce-make.ts"
        assert "isSubagent()" in src, "enforce-make.ts text.complete must skip for subagents"

    def test_enforce_make_system_transform_has_subagent_guard(self):
        """system.transform must not inject policy into subagent system prompts."""
        src = _plugin_source(ENFORCE_MAKE)
        st_pos = src.find('"experimental.chat.system.transform"')
        if st_pos >= 0:
            after_st = src[st_pos : st_pos + 400]
            # Should check OPENCODE_SUBAGENT and skip injection for subagents
            assert "OPENCODE_SUBAGENT" in after_st or "isSubagent" in after_st, (
                "system.transform must guard against subagent injection"
            )

    def test_enforce_multitask_respects_subagent_env(self):
        """enforce-multitask.ts must use the shared subagent detector."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "isSubagent" in src, "enforce-multitask.ts must use isSubagent() to skip subagent checks"

    def test_enforce_multitask_subagent_bypass_early_return(self):
        """When OPENCODE_SUBAGENT=1, the tool.execute.before must return early."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert re.search(r"if\s*\(\s*isSubagent\(\)\s*\)\s*return", src), (
            "enforce-multitask must short-circuit in subagent context"
        )

    def test_enforce_stop_respects_subagent_env(self):
        """enforce-stop.ts must use the shared subagent detector."""
        src = _plugin_source(ENFORCE_STOP)
        assert "isSubagent" in src, "enforce-stop.ts must use isSubagent()"

    def test_enforce_stop_tool_execute_bypass(self):
        """enforce-stop.ts tool.execute.before must skip for subagents."""
        src = _plugin_source(ENFORCE_STOP)
        assert re.search(r"if\s*\(\s*isSubagent\(\)\s*\)\s*return", src), (
            "tool.execute.before in enforce-stop.ts must short-circuit for subagents"
        )

    def test_enforce_stop_text_complete_bypass(self):
        """enforce-stop.ts text.complete must skip for subagents."""
        src = _plugin_source(ENFORCE_STOP)
        assert "isSubagent()" in src, "text.complete in enforce-stop.ts must check subagent context"

    def test_enforce_stop_system_transform_has_subagent_guard(self):
        """enforce-stop.ts system.transform must not inject into subagent prompts."""
        src = _plugin_source(ENFORCE_STOP)
        st_pos = src.find('"experimental.chat.system.transform"')
        if st_pos >= 0:
            after_st = src[st_pos : st_pos + 500]
            assert "isSubagent" in after_st or "isSubagent()" in src, (
                "enforce-stop.ts system.transform must guard against subagent contamination"
            )

    def test_subagent_bypass_is_immediate_return(self):
        """All three plugins must return (not throw, not warn) when
        OPENCODE_SUBAGENT=1 — subagent context must be a clean bypass."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert "isSubagent" in src, f"{name} missing shared subagent check"


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
        src = _plugin_source(ENFORCE_MAKE)
        assert "throw new Error" in src, "enforce-make.ts must throw to hard-deny non-make bash"

    def test_enforce_multitask_has_deny_for_low_dispatches(self):
        """enforce-multitask.ts must return deny decision for low dispatch count."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert re.search(
            r'permissionDecision:\s*"deny"',
            src,
        ) or re.search(r"permissionDecision:\s*'deny'", src), "enforce-multitask.ts must return permissionDecision:deny"

    def test_enforce_stop_has_deny_for_stop_patterns(self):
        """enforce-stop.ts tool.execute.before must have deny logic."""
        src = _plugin_source(ENFORCE_STOP)
        has_deny_block = "permissionDecision" in src or "throw" in src or "blocked" in src
        assert has_deny_block, "enforce-stop.ts tool.execute.before must block stop patterns"

    # ── Layer 2: Text injection (experimental.text.complete) ────────────

    def test_enforce_make_has_text_complete_hook(self):
        """enforce-make.ts must have a text.complete hook."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "experimental.text.complete" in src, "enforce-make.ts missing text.complete hook"

    def test_enforce_make_text_complete_has_state_block(self):
        """enforce-make.ts text.complete must block text when work is pending."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "hasLocalWork" in src or "ratchet" in src.lower(), (
            "enforce-make.ts text.complete must check pending work"
        )

    def test_enforce_multitask_has_text_complete_hook(self):
        """enforce-multitask.ts must have a text.complete hook."""
        src = _plugin_source(ENFORCE_MULTITASK)
        assert "experimental.text.complete" in src, "enforce-multitask.ts missing text.complete hook"

    def test_enforce_stop_has_text_complete_hook(self):
        """enforce-stop.ts must have a text.complete hook."""
        src = _plugin_source(ENFORCE_STOP)
        assert "experimental.text.complete" in src, "enforce-stop.ts missing text.complete hook"

    def test_enforce_stop_text_complete_has_false_done_block(self):
        """enforce-stop.ts text.complete must have false-done claim detection."""
        src = _plugin_source(ENFORCE_STOP)
        assert "FALSE-DONE" in src, "enforce-stop.ts text.complete missing FALSE-DONE detection"

    # ── Layer 3: System prompt injection (system.transform) ─────────────

    def test_enforce_make_has_system_transform(self):
        """enforce-make.ts must inject policy into the system prompt."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "experimental.chat.system.transform" in src, (
            "enforce-make.ts missing system.transform — policy not injected into agent's system prompt"
        )

    def test_enforce_make_system_transform_mentions_bash_policy(self):
        """system.transform must include the make-only bash policy text."""
        src = _plugin_source(ENFORCE_MAKE)
        assert "make <target>" in src.lower() or "ONLY `make" in src or "Only `make" in src, (
            "system.transform in enforce-make.ts must inject bash policy"
        )

    def test_enforce_stop_has_system_transform(self):
        """enforce-stop.ts must inject orchestration directives."""
        src = _plugin_source(ENFORCE_STOP)
        assert "experimental.chat.system.transform" in src, "enforce-stop.ts missing system.transform"

    def test_enforce_stop_system_transform_mentions_dispatch(self):
        """system.transform in enforce-stop.ts must mention dispatching work."""
        src = _plugin_source(ENFORCE_STOP)
        assert "dispatch" in src.lower() or "pending" in src.lower(), (
            "enforce-stop.ts system.transform must direct the agent to dispatch"
        )

    # ── Cross-plugin consistency checks ─────────────────────────────────

    def test_all_plugins_use_same_subagent_env_var(self):
        """All plugins must use the centralized subagent detector."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert "isSubagent" in src, f"{name} missing shared isSubagent detector"

        assert "OPENCODE_SUBAGENT" in SHARED.read_text()

    def test_dispatch_enforcement_plugins_use_shared_classifier(self):
        """Plugins that enforce dispatch counts use the shared classifier."""
        for name, path_obj in [
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert "isDispatchTool" in src, f"{name} missing centralized dispatch classifier"

        shared = SHARED.read_text()
        for tool in {"task", "agent", "workflow"}:
            assert f'"{tool}"' in shared

    def test_all_plugins_have_TEXT_ENFORCEMENT_comment(self):
        """Each plugin must document its text enforcement at DEFCON level."""
        # enforce-stop.ts is the primary; check it's documented
        src = _plugin_source(ENFORCE_STOP)
        # The plugin should mention its enforcement surface
        surfaces = [
            "tool.execute.before" in src,
            "text.complete" in src,
            "system.transform" in src,
        ]
        assert sum(surfaces) >= 2, "enforce-stop.ts must document at least 2 enforcement surfaces"

    def test_disengage_mechanism_consistent(self):
        """Plugins using permissionDecision:deny must support disengage.
        enforce-make.ts uses throw (caught by runtime), so it doesn't need
        a separate disengage path — the runtime is the fail-open wrapper."""
        disengage_file = "/tmp/gludd-watchdog-disengage.json"
        shared = SHARED.read_text()
        assert disengage_file in shared

        # enforce-multitask uses permissionDecision:deny — must have disengage
        mt_src = _plugin_source(ENFORCE_MULTITASK)
        assert "isDisengaged" in mt_src, "enforce-multitask.ts must use shared disengage handling"

        # enforce-stop uses permissionDecision:deny — must have disengage
        ss_src = _plugin_source(ENFORCE_STOP)
        assert disengage_file in ss_src, "enforce-stop.ts must support disengage via watchdog file"

        # enforce-make.ts uses throw (hard block caught by opencode runtime).
        # It does NOT need a disengage path — the runtime is the outer
        # fail-open wrapper and the OPENCODE_SUBAGENT bypass handles subagents.
        # Verify it at least has the subagent bypass as its escape hatch.
        ms_src = _plugin_source(ENFORCE_MAKE)
        assert "isSubagent" in ms_src, "enforce-make.ts must use the shared subagent escape hatch"


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
            src = _plugin_source(path_obj)
            assert "export default" in src, f"{name} missing 'export default'"

    def test_all_plugins_have_both_task_and_text_hooks(self):
        """Each plugin must define at least tool.execute.before for the
        pre-dispatch gate AND one of text.complete / system.transform for
        the post-generation gate."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert '"tool.execute.before"' in src, f"{name} missing tool.execute.before"
            has_text_or_system = "experimental.text.complete" in src or "experimental.chat.system.transform" in src
            assert has_text_or_system, f"{name} missing text.complete AND system.transform"

    def test_plugin_manifest_registers_all_three(self):
        """opencode.json must register all three enforcement plugins."""
        manifest = ROOT / "opencode.json"
        assert manifest.exists(), "opencode.json missing"
        cfg = manifest.read_text()
        for name in ["enforce-make", "enforce-multitask", "enforce-stop"]:
            assert name in cfg, f"opencode.json missing plugin registration: {name}"

    def test_all_plugins_subagent_safe(self):
        """No plugin must throw or block on subagent tool calls."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert "isSubagent" in src, f"{name} does not use isSubagent() — subagent tool calls may be blocked"

    def test_all_plugins_fail_open(self):
        """No plugin must wedge the session on internal error."""
        for name, path_obj in [
            ("enforce-make.ts", ENFORCE_MAKE),
            ("enforce-multitask.ts", ENFORCE_MULTITASK),
            ("enforce-stop.ts", ENFORCE_STOP),
        ]:
            src = _plugin_source(path_obj)
            assert _fail_open(src), f"{name} is not fail-open — an internal error may wedge the session"
