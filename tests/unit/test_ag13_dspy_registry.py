"""Tests for the DSPy prompt registry."""

from __future__ import annotations

import pytest

from general_ludd.ag13_dspy.registry import PromptRegistry, PromptSpec, PromptTemplate


class _NonStringTemplate:
    """Stand-in for a renderer that violates Jinja's string contract."""

    def __init__(self, _source: str) -> None:
        pass

    def render(self, **_kwargs: object) -> object:
        return object()


def test_call_rejects_non_string_renderer_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep untyped third-party values from escaping the typed boundary."""
    monkeypatch.setattr("jinja2.Template", _NonStringTemplate)
    prompt = PromptTemplate(spec=PromptSpec(name="example"), template="{{ value }}")

    with pytest.raises(TypeError, match="renderer must return a string"):
        prompt.call(value="hello")


def test_call_returns_rendered_string() -> None:
    """Render a prompt through the supported Jinja boundary."""
    prompt = PromptTemplate(spec=PromptSpec(name="greeting"), template="Hello, {{ name }}!")

    assert prompt.call(name="Ada") == "Hello, Ada!"


def test_registry_tracks_versions_scores_and_removal() -> None:
    """Exercise the registry's complete version lifecycle."""
    registry = PromptRegistry()
    first = PromptTemplate(spec=PromptSpec(name="greeting"), template="Hello")
    second = PromptTemplate(spec=PromptSpec(name="greeting"), template="Hi")

    assert registry.get("greeting", 1) is None
    assert registry.latest("greeting") is None
    assert registry.get_best("greeting") is None

    registry.put("greeting", 1, first)
    registry.put("greeting", 2, second, score=0.8)

    assert registry.get("greeting", 1) is first
    assert registry.latest("greeting") is second
    assert registry.get_best("greeting") is second
    assert registry.list_versions("greeting") == [1, 2]
    assert registry.list_names() == ["greeting"]
    assert len(registry) == 2
    assert first.version == 1
    assert first.score is None
    assert second.version == 2
    assert second.score == 0.8

    registry.remove("greeting", 2)

    assert registry.latest("greeting") is first
    assert len(registry) == 1
