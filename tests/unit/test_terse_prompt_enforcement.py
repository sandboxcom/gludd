"""Behavior pin for terse prompt enforcement in enforce-deliverable.ts.

Per AGENTS.md COST-EFFICIENCY DIRECTIVE item 2: "Terse subagent prompts.
Each subagent prompt must be ≤20 lines."

This test extracts the MAX_PROMPT_LINES constant from the plugin source
and verifies the terse-prompt warning logic.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-deliverable.ts"


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


def _extract_max_prompt_lines(src: str) -> int:
    m = re.search(r"const\s+MAX_PROMPT_LINES\s*=\s*(\d+)", src)
    assert m, "MAX_PROMPT_LINES constant not found in plugin source"
    return int(m.group(1))


def _count_lines(prompt: str) -> int:
    return len(prompt.split("\n"))


class TestPluginStructure:
    def test_max_prompt_lines_equals_20(self):
        val = _extract_max_prompt_lines(_plugin_source())
        assert val == 20, f"Expected MAX_PROMPT_LINES=20, got {val}"

    def test_tool_execute_before_hook_present(self):
        src = _plugin_source()
        assert "tool.execute.before" in src, "tool.execute.before hook missing"

    def test_console_warn_for_terse_prompt(self):
        src = _plugin_source()
        assert "TERSE PROMPT RULE" in src, (
            "TERSE PROMPT RULE warning message missing from plugin source"
        )

    def test_prompt_lines_variable(self):
        src = _plugin_source()
        assert "promptLines" in src, "promptLines variable missing"

    def test_line_split_logic(self):
        src = _plugin_source()
        assert "split" in src and "\\n" in src, (
            "Line-splitting logic (split on newline) missing"
        )

    def test_fail_open_wrapped(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "No try/catch fail-open block found"

    def test_env_var_disable(self):
        src = _plugin_source()
        assert "GLUDD_DELIVERABLE_ENFORCE" in src, "Env-var disable switch missing"

    def test_subagent_guard(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "Subagent guard missing"


class TestTersePromptLogic:
    """Verify line-counting and threshold behavior."""

    def test_short_prompt_passes(self):
        prompt = "Do X. Return Y."
        lines = _count_lines(prompt)
        assert lines <= 20, f"Short prompt ({lines} lines) should be <= 20"

    def test_exactly_20_lines_passes(self):
        prompt = "\n".join(f"Line {i}" for i in range(20))
        lines = _count_lines(prompt)
        assert lines == 20, "Exactly 20 lines should still be allowed"

    def test_21_lines_triggers_warning(self):
        prompt = "\n".join(f"Line {i}" for i in range(21))
        lines = _count_lines(prompt)
        assert lines > 20, f"21-line prompt ({lines} lines) should exceed threshold"

    def test_empty_prompt(self):
        prompt = ""
        lines = _count_lines(prompt)
        assert lines <= 20, "Empty prompt should not trigger warning"

    def test_single_line_prompt(self):
        prompt = "One line of text."
        lines = _count_lines(prompt)
        assert lines == 1, "Single-line prompt should be 1 line"

    def test_whitespace_only_lines_count(self):
        prompt = "\n".join([""] * 25)
        lines = _count_lines(prompt)
        assert lines > 20, f"25 empty lines ({lines} lines) should exceed threshold"

    def test_warning_message_includes_line_count(self):
        src = _plugin_source()
        assert "${promptLines}" in src or "${" in src, (
            "Warning message should include the actual line count"
        )

    def test_warning_message_includes_max(self):
        src = _plugin_source()
        assert "Max:" in src, "Warning message should mention max lines"
        assert "${MAX_PROMPT_LINES}" in src or "${" in src, (
            "Warning message should include MAX_PROMPT_LINES"
        )
