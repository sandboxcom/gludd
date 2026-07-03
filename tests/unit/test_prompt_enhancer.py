"""Tests for PromptEnhancer — injecting bad-call avoidance into prompts."""
import tempfile
from pathlib import Path

import pytest

from general_ludd.execution.situation_store import BadCallSituationStore
from general_ludd.execution.tool_auditor import BadCallSituation as BCS
from general_ludd.prompts.enhancer import PromptEnhancer


class TestPromptEnhancer:
    """Tests for generating enhanced prompts from bad-call situations."""

    @pytest.fixture
    def store_with_data(self):
        with tempfile.TemporaryDirectory() as d:
            store = BadCallSituationStore(base_dir=Path(d))
            store.save(
                BCS(
                    tool_name="read_file",
                    tool_args={"path": "/etc/shadow"},
                    classification="irrelevant",
                    reason="not relevant to code task",
                    task_excerpt="write a sorting function",
                )
            )
            store.save(
                BCS(
                    tool_name="read_file",
                    tool_args={"path": "/foo"},
                    classification="redundant",
                    reason="called 3 times consecutively",
                    task_excerpt="read the source file",
                )
            )
            store.save(
                BCS(
                    tool_name="execute_command",
                    tool_args={"command": "rm -rf /"},
                    classification="error_loop",
                    reason="errored 3 times",
                    task_excerpt="run the tests",
                )
            )
            yield store

    def test_generate_warning_empty_store(self):
        enhancer = PromptEnhancer(store=None)
        warning = enhancer.generate_avoidance_warning()
        assert warning == ""

    def test_generate_warning_with_data(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data)
        warning = enhancer.generate_avoidance_warning()
        assert "read_file" in warning
        assert "execute_command" in warning
        assert "avoid" in warning.lower() or "do not" in warning.lower()

    def test_generate_warning_respects_limit(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data, max_situations=1)
        warning = enhancer.generate_avoidance_warning()
        lines = [ln for ln in warning.split("\n") if ln.strip()]
        assert len(lines) <= 8

    def test_inject_into_prompt(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data)
        original = "You are a helpful coding assistant."
        enhanced = enhancer.enhance_prompt(original)
        assert original in enhanced
        assert len(enhanced) > len(original)
        assert "avoid" in enhanced.lower()

    def test_inject_empty_when_no_data(self):
        enhancer = PromptEnhancer(store=None)
        original = "You are a helpful coding assistant."
        enhanced = enhancer.enhance_prompt(original)
        assert enhanced == original

    def test_get_recent_blocked_tools(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data)
        tools = enhancer.get_recent_blocked_tools()
        assert "read_file" in tools
        assert "execute_command" in tools

    def test_get_blocked_tool_counts(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data)
        counts = enhancer.get_blocked_tool_counts()
        assert counts.get("read_file", 0) >= 2
        assert counts.get("execute_command", 0) >= 1

    def test_format_tool_advice(self, store_with_data):
        enhancer = PromptEnhancer(store=store_with_data)
        advice = enhancer.format_tool_advice("read_file")
        assert "redundant" in advice.lower() or "irrelevant" in advice.lower()
