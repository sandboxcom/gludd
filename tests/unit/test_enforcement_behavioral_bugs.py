"""Structural verification that 9 enforcement-plugin bugs are fixed.

Each test parses the relevant plugin source and asserts the fix is in place.
These are source-parse tests — they verify code shape, not runtime behavior.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"


def _read_plugin(name: str) -> str:
    path = PLUGIN_DIR / name
    assert path.exists(), f"Plugin {name} not found at {path}"
    return plugin_contract_source(path)


# ── Bug 1: enforce-stop.ts — execSync is used (not undefined es) ──────────────

class TestBug1_EnforceStopExecSync:
    """Line 373 uses undefined `es`; should use `execSync`."""

    def test_imports_execSync_from_child_process(self):
        src = _read_plugin("enforce-stop.ts")
        assert "function execSync" in src
        assert 'node:child_' in src and '"process"' in src, (
            "enforce-stop.ts must resolve execSync from node:child_process"
        )

    def test_repo_has_pending_work_called_with_execSync_not_es(self):
        src = _read_plugin("enforce-stop.ts")
        assert "repoHasPendingWork(es," not in src, (
            "BUG STILL PRESENT: repoHasPendingWork(es, repoMode) uses undefined 'es'. "
            "Must be 'execSync'."
        )
        assert "repoHasPendingWork(execSync" in src, (
            "Call to repoHasPendingWork must use execSync, not undefined 'es'."
        )


# ── Bug 2: enforce-stop.ts — adaptive minimum, hard maximum ten ───────────────

class TestBug2_EnforceStopAdaptiveMinimum:
    """No implicit floor is imposed, while the hard maximum remains ten."""

    def test_floor_is_explicit_and_ceiling_is_ten(self):
        src = _read_plugin("enforce-stop.ts")
        assert 'CLAUDE_AGENT_FLOOR || "7"' not in src, (
            "BUG STILL PRESENT: FLOOR defaults to the retired value seven."
        )
        assert "HARD_MAX_DISPATCHES = 10" in src
        assert "CONFIGURED_AGENT_MIN !== undefined" in src
        assert "REQUIRED_AGENT_MIN" in src


# ── Bug 3: enforce-delegate.ts — CLAUDE_AGENT_FLOOR defaults to "10" not "7" ──

class TestBug3_EnforceDelegateFloorDefault:
    """FLOOR defaults to "7"; AGENTS.md mandates "10"."""

    def test_floor_defaults_to_10_not_7(self):
        src = _read_plugin("enforce-delegate.ts")
        assert 'CLAUDE_AGENT_FLOOR || "7"' not in src, (
            "BUG STILL PRESENT: FLOOR defaults to '7'. Must default to '10'."
        )
        assert 'CLAUDE_AGENT_FLOOR || "10"' in src, (
            "FLOOR must default to '10' to match AGENTS.md floor."
        )


# ── Bug 4: enforce-no-wait.ts — bash command uses input.args?.command ─────────

class TestBug4_EnforceNoWaitBashCommand:
    """Line 99 uses input.command fallback; opencode passes bash command via input.args.command."""

    def test_bash_command_reads_from_input_args_not_input_directly(self):
        src = _read_plugin("enforce-no-wait.ts")
        # The bug is `input.command` used as a direct property access for the bash command.
        # opencode's tool.execute.before API exposes command via input.args.command only.
        # The fix removes the `input.command` fallback entirely.
        # Look for the actual command-reading line.
        m = re.search(
            r'const\s+cmd[^;]*=\s*(.+?);',
            src,
        )
        assert m, "must have a cmd assignment line"
        cmd_assignment = m.group(1)
        assert "input.command" not in cmd_assignment, (
            f"BUG STILL PRESENT: cmd assignment uses input.command: {cmd_assignment!r}. "
            "Must use only input.args?.command."
        )
        assert "input.args?.command" in cmd_assignment or "input.args.command" in cmd_assignment, (
            f"bash command must be read from input.args: {cmd_assignment!r}"
        )


# ── Bug 5: enforce-verified-claims.ts — uses ctx?.tool not ctx?.toolName ──────

class TestBug5_EnforceVerifiedClaimsToolName:
    """Line 69 uses ctx?.toolName; opencode provides tool name as input.tool."""

    def test_uses_tool_not_toolName(self):
        src = _read_plugin("enforce-verified-claims.ts")
        assert "toolName" not in src, (
            "BUG STILL PRESENT: ctx?.toolName is not a valid opencode plugin field. "
            "Must use ctx?.tool or input.tool."
        )
        assert re.search(
            r'ctx\??\.\s*tool\b',
            src,
        ), "tool name must be read from ctx?.tool or input.tool"


# ── Bug 6: enforce-verified-claims.ts — uses input.args not ctx?.toolInput ────

class TestBug6_EnforceVerifiedClaimsArgs:
    """Line 71 uses ctx?.toolInput; opencode provides args via input.args."""

    def test_uses_args_not_toolInput(self):
        src = _read_plugin("enforce-verified-claims.ts")
        assert "toolInput" not in src, (
            "BUG STILL PRESENT: ctx?.toolInput is not a valid opencode plugin field. "
            "Must use ctx?.args or input.args."
        )
        assert re.search(
            r'ctx\??\.\s*args\b',
            src,
        ), "tool arguments must be read from ctx?.args or input.args"


# ── Bug 7: enforce-deletion-gate.ts — uses camelCase not snake_case ───────────

class TestBug7_EnforceDeletionGateCamelCase:
    """Lines 107-114 use snake_case (file_path, old_string); opencode uses camelCase."""

    def test_edit_args_use_camelCase_not_snake_case(self):
        src = _read_plugin("enforce-deletion-gate.ts")
        # CamelCase is canonical and must take precedence. Legacy snake_case
        # fallbacks may remain for compatibility with older tool adapters.
        assert "args.filePath || args.file_path" in src
        assert "args.oldString !== undefined" in src
        assert "args.newString !== undefined" in src


# ── Bug 8: enforce-deadline.ts — BLOCK mode skips dispatch tools ──────────────

class TestBug8_EnforceDeadlineBlockSkipsDispatch:
    """BLOCK mode denies ALL tool calls including dispatches, preventing replacement."""

    def test_block_check_excludes_dispatch_tools(self):
        src = _read_plugin("enforce-deadline.ts")
        # The bug: dispatch tools fall through to the BLOCK check which denies
        # them when a prior task breached. The fix: the dispatch-tool branch
        # must return early (before the for-loop that checks deadlines) OR the
        # BLOCK condition must include `&& !isDispatchTool(tool)`.
        #
        # Search for the dispatch-tool recording block and verify it has a
        # `return` before the deadline-checking for-loop.
        lines = src.split("\n")

        # Find the dispatch-tool recording block
        dispatch_block_start = None
        for i, line in enumerate(lines):
            if "if (isDispatchTool(tool))" in line.strip():
                dispatch_block_start = i
                break

        if dispatch_block_start is None:
            pytest.fail("isDispatchTool(tool) not found in source")

        # Check for `return` statements within a reasonable window after
        # the dispatch block starts. The fix should add `return` inside this
        # block (or immediately after it, before the for-loop).
        has_return_in_dispatch_block = False
        brace_depth = 0
        entered_block = False
        for j in range(dispatch_block_start, min(dispatch_block_start + 20, len(lines))):
            stripped = lines[j].strip()
            brace_depth += stripped.count("{") - stripped.count("}")
            if "{" in stripped:
                entered_block = True
            if entered_block and brace_depth == 0:
                break  # exited the dispatch block
            if entered_block and re.match(r'^\s*return\b', lines[j]):
                has_return_in_dispatch_block = True
                break

        # Also check: BLOCK condition includes isDispatchTool guard
        block_condition = re.search(
            r'if\s*\((?:BLOCK|firstBreachedId)[^)]*\)',
            src,
        )
        block_guards_dispatch = False
        if block_condition:
            cond = block_condition.group(0)
            block_guards_dispatch = "isDispatchTool" in cond

        assert has_return_in_dispatch_block or block_guards_dispatch, (
            "BUG STILL PRESENT: BLOCK mode denies dispatch tools, preventing "
            "replacement of stale tasks. The dispatch-tool recording branch "
            "must return early (before the BLOCK check) or the BLOCK condition "
            "must include '!isDispatchTool(tool)'."
        )

    def test_dispatch_tool_recording_still_exists(self):
        src = _read_plugin("enforce-deadline.ts")
        assert "isDispatchTool(tool)" in src, (
            "dispatch tool classification must still exist"
        )
        assert "d[id] = Date.now()" in src, (
            "deadline recording for dispatch tools must still exist"
        )


# ── Bug 9: enforce-session-start.ts — directive computed dynamically ──────────

class TestBug9_EnforceSessionStartDirectiveDynamic:
    """SESSION_START_DIRECTIVE is a static module-level const; must be dynamic."""

    def test_directive_not_static_module_level_const(self):
        src = _read_plugin("enforce-session-start.ts")
        # The directive must be computed inside a hook function, not as a
        # module-level `const SESSION_START_DIRECTIVE = ...` that captures
        # env-var values at load time.
        #
        # Check: the string "SESSION START PROTOCOL" content must appear
        # inside a function body (after an async function declaration), not
        # at module top-level.
        lines = src.split("\n")
        inside_function = False
        found_in_function = False
        brace_depth = 0
        for line in lines:
            stripped = line.strip()
            # Track function entry/exit
            if ("async" in stripped or ":" in stripped) and ("=>" in stripped or "function" in stripped):
                inside_function = True
                brace_depth = 0
            if inside_function:
                brace_depth += stripped.count("{") - stripped.count("}")
                if "SESSION START PROTOCOL" in stripped:
                    found_in_function = True
                    break
                if brace_depth <= 0 and "{" in stripped:
                    inside_function = False

        assert found_in_function, (
            "BUG STILL PRESENT: SESSION START PROTOCOL directive is a "
            "module-level constant. It must be computed dynamically inside "
            "the system.transform hook so it picks up runtime env-var values."
        )
