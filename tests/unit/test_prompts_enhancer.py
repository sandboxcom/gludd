"""Structural tests for general_ludd.prompts.enhancer — PromptEnhancer.

Tests the tool-call avoidance guidance injection without depending on a
real BadCallSituationStore. Uses in-memory stubs to verify the formatting,
grouping, and message manipulation logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.prompts.enhancer import PromptEnhancer


@dataclass
class _FakeBadCall:
    tool_name: str
    classification: str
    reason: str
    task_excerpt: str = ""


class _FakeStore:
    def __init__(self, situations: list[_FakeBadCall]) -> None:
        self._situations = situations

    def list_recent(self, limit: int = 20) -> list[_FakeBadCall]:
        return self._situations[:limit]

    def list_by_tool(self, tool_name: str, limit: int = 10) -> list[_FakeBadCall]:
        return [s for s in self._situations if s.tool_name == tool_name][:limit]


class TestGenerateAvoidanceWarning:
    def test_empty_when_store_is_none(self) -> None:
        enhancer = PromptEnhancer(store=None)
        assert enhancer.generate_avoidance_warning() == ""

    def test_empty_when_no_situations(self) -> None:
        enhancer = PromptEnhancer(store=_FakeStore([]))
        assert enhancer.generate_avoidance_warning() == ""

    def test_includes_tool_names_and_reasons(self) -> None:
        store = _FakeStore([
            _FakeBadCall("bash", "metachar", "pipe detected in command", "make test | grep"),
        ])
        enhancer = PromptEnhancer(store=store)
        warning = enhancer.generate_avoidance_warning()
        assert "bash" in warning
        assert "metachar" in warning
        assert "Tool Call Avoidance Guidance" in warning

    def test_groups_by_tool(self) -> None:
        store = _FakeStore([
            _FakeBadCall("bash", "metachar", "pipe", ""),
            _FakeBadCall("bash", "forbidden", "cd detected", ""),
            _FakeBadCall("write", "no_read", "file not read first", ""),
        ])
        enhancer = PromptEnhancer(store=store)
        warning = enhancer.generate_avoidance_warning()
        assert "2 times" in warning or "blocked 2" in warning

    def test_includes_guidance_footer(self) -> None:
        store = _FakeStore([_FakeBadCall("bash", "metachar", "pipe", "")])
        enhancer = PromptEnhancer(store=store)
        warning = enhancer.generate_avoidance_warning()
        assert "Guidance" in warning

    def test_respects_max_situations(self) -> None:
        situations = [_FakeBadCall("bash", "metachar", f"reason {i}") for i in range(10)]
        enhancer = PromptEnhancer(store=_FakeStore(situations), max_situations=3)
        warning = enhancer.generate_avoidance_warning()
        assert "3 times" in warning or "blocked 3" in warning


class TestEnhancePrompt:
    def test_appends_warning_to_system_prompt(self) -> None:
        store = _FakeStore([_FakeBadCall("bash", "metachar", "pipe", "")])
        enhancer = PromptEnhancer(store=store)
        result = enhancer.enhance_prompt("You are a helpful assistant.")
        assert result.startswith("You are a helpful assistant.")
        assert "Tool Call Avoidance Guidance" in result

    def test_unchanged_when_no_store(self) -> None:
        enhancer = PromptEnhancer(store=None)
        original = "system prompt"
        assert enhancer.enhance_prompt(original) == original

    def test_unchanged_when_no_situations(self) -> None:
        enhancer = PromptEnhancer(store=_FakeStore([]))
        original = "system prompt"
        assert enhancer.enhance_prompt(original) == original


class TestEnhanceMessages:
    def test_appends_to_existing_system_message(self) -> None:
        store = _FakeStore([_FakeBadCall("bash", "metachar", "pipe", "")])
        enhancer = PromptEnhancer(store=store)
        messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "hello"}]
        result = enhancer.enhance_messages(messages)
        assert result[0]["content"].startswith("base")
        assert "Tool Call Avoidance Guidance" in result[0]["content"]

    def test_inserts_system_message_if_none_present(self) -> None:
        store = _FakeStore([_FakeBadCall("bash", "metachar", "pipe", "")])
        enhancer = PromptEnhancer(store=store)
        messages = [{"role": "user", "content": "hello"}]
        result = enhancer.enhance_messages(messages)
        assert result[0]["role"] == "system"
        assert "Tool Call Avoidance Guidance" in result[0]["content"]

    def test_unchanged_when_no_warning(self) -> None:
        enhancer = PromptEnhancer(store=None)
        messages = [{"role": "user", "content": "hello"}]
        result = enhancer.enhance_messages(messages)
        assert result == messages


class TestGetRecentBlockedTools:
    def test_returns_set_of_tool_names(self) -> None:
        store = _FakeStore([
            _FakeBadCall("bash", "x", "r"),
            _FakeBadCall("write", "y", "r"),
            _FakeBadCall("bash", "z", "r"),
        ])
        enhancer = PromptEnhancer(store=store)
        tools = enhancer.get_recent_blocked_tools()
        assert tools == {"bash", "write"}

    def test_empty_when_no_store(self) -> None:
        enhancer = PromptEnhancer(store=None)
        assert enhancer.get_recent_blocked_tools() == set()


class TestGetBlockedToolCounts:
    def test_counts_per_tool(self) -> None:
        store = _FakeStore([
            _FakeBadCall("bash", "x", "r"),
            _FakeBadCall("bash", "y", "r"),
            _FakeBadCall("write", "z", "r"),
        ])
        enhancer = PromptEnhancer(store=store)
        counts = enhancer.get_blocked_tool_counts()
        assert counts == {"bash": 2, "write": 1}

    def test_empty_when_no_store(self) -> None:
        enhancer = PromptEnhancer(store=None)
        assert enhancer.get_blocked_tool_counts() == {}


class TestFormatToolAdvice:
    def test_returns_advice_for_known_tool(self) -> None:
        store = _FakeStore([
            _FakeBadCall("bash", "metachar", "pipe detected"),
        ])
        enhancer = PromptEnhancer(store=store)
        advice = enhancer.format_tool_advice("bash")
        assert "bash" in advice
        assert "pipe detected" in advice

    def test_empty_for_unknown_tool(self) -> None:
        store = _FakeStore([_FakeBadCall("bash", "x", "r")])
        enhancer = PromptEnhancer(store=store)
        assert enhancer.format_tool_advice("nonexistent") == ""

    def test_empty_when_no_store(self) -> None:
        enhancer = PromptEnhancer(store=None)
        assert enhancer.format_tool_advice("bash") == ""
