"""Tests for game_generation — normalize_generated_python and content extraction."""

from __future__ import annotations

import pytest

from general_ludd.cloud.game_generation import (
    _content_text,
    _extract_python_fence,
    _message_text,
    normalize_generated_python,
)

# ── normalize_generated_python ───────────────────────────────────────────────


class TestNormalizeGeneratedPython:
    def test_plain_string(self) -> None:
        result = normalize_generated_python("print('hello')")
        assert result == "print('hello')"

    def test_whitespace_only_response_raises(self) -> None:
        threw = False
        try:
            normalize_generated_python("   \n  \t  ")
        except RuntimeError:
            threw = True
        assert threw, "Expected RuntimeError for whitespace-only response"

    def test_python_fence_extracted(self) -> None:
        code = "```python\nprint('hello world')\n```"
        result = normalize_generated_python(code)
        assert result == "print('hello world')"

    def test_unlabelled_fence_extracted(self) -> None:
        code = "```\nprint('bare fence')\n```"
        result = normalize_generated_python(code)
        assert result == "print('bare fence')"

    def test_first_fence_wins_with_multiple(self) -> None:
        code = "```python\nfirst()\n```\nextra\n```python\nsecond()\n```"
        result = normalize_generated_python(code)
        assert result == "first()"

    def test_empty_fence_skipped(self) -> None:
        code = "```python\n\n```\n```python\nreal code\n```"
        result = normalize_generated_python(code)
        assert result == "real code"

    def test_opening_fence_without_closing(self) -> None:
        code = "```python\nprint('unclosed fence')"
        result = normalize_generated_python(code)
        assert result == "print('unclosed fence')"

    def test_langchain_message_with_content_list(self) -> None:
        msg = type(
            "FakeMessage",
            (),
            {
                "content": [{"type": "text", "text": "```python\nhello()\n```"}],
                "raw_response": None,
            },
        )()
        result = normalize_generated_python(msg)
        assert result == "hello()"

    def test_langchain_message_with_raw_response(self) -> None:
        raw = type(
            "FakeRaw",
            (),
            {
                "content": "```python\nfrom raw\n```",
            },
        )()
        msg = type(
            "FakeMessage",
            (),
            {
                "content": "wrong content",
                "raw_response": raw,
            },
        )()
        result = normalize_generated_python(msg)
        assert result == "from raw"

    def test_langchain_message_text_property(self) -> None:
        msg = type(
            "FakeMessage",
            (),
            {
                "text": "```python\nfrom text prop\n```",
                "content": "ignored",
                "raw_response": None,
            },
        )()
        result = normalize_generated_python(msg)
        assert result == "from text prop"

    def test_langchain_message_callable_text(self) -> None:
        msg = type(
            "FakeMessage",
            (),
            {
                "text": lambda self: "```python\nfrom callable\n```",
                "content": "ignored",
                "raw_response": None,
            },
        )()
        result = normalize_generated_python(msg)
        assert result == "from callable"

    def test_empty_content_raises(self) -> None:
        msg = type(
            "FakeMessage",
            (),
            {
                "content": "",
                "raw_response": None,
            },
        )()
        with pytest.raises(RuntimeError, match="no text content"):
            normalize_generated_python(msg)

    def test_none_raw_response_falls_to_self(self) -> None:
        msg = type(
            "FakeMessage",
            (),
            {
                "content": "fallback content",
                "raw_response": None,
            },
        )()
        result = normalize_generated_python(msg)
        assert result == "fallback content"


# ── _message_text ────────────────────────────────────────────────────────────


class TestMessageText:
    def test_string_text_adapter(self) -> None:
        msg = type("Fake", (), {"text": "direct text", "content": "ignored"})()
        assert _message_text(msg) == "direct text"

    def test_callable_text_adapter(self) -> None:
        msg = type("Fake", (), {"text": lambda self: "called text", "content": "ignored"})()
        assert _message_text(msg) == "called text"

    def test_falls_to_content_when_text_none(self) -> None:
        msg = type("Fake", (), {"text": None, "content": "content string"})()
        assert _message_text(msg) == "content string"

    def test_falls_to_self_when_no_text_or_content(self) -> None:
        msg = type("Fake", (), {})()
        result = _message_text(msg)
        assert result == ""

    def test_callable_text_returns_empty_falls_to_content(self) -> None:
        msg = type("Fake", (), {"text": lambda self: "", "content": "content backup"})()
        assert _message_text(msg) == "content backup"


# ── _content_text ────────────────────────────────────────────────────────────


class TestContentText:
    def test_plain_string(self) -> None:
        assert _content_text("hello") == "hello"

    def test_nested_dict_with_text_key(self) -> None:
        assert _content_text({"type": "text", "text": "nested"}) == "nested"

    def test_dict_with_content_key(self) -> None:
        assert _content_text({"content": "inner"}) == "inner"

    def test_dict_with_value_key(self) -> None:
        assert _content_text({"value": "valued"}) == "valued"

    def test_non_text_block_type_returns_empty(self) -> None:
        assert _content_text({"type": "image_url", "text": "hidden"}) == ""

    def test_list_of_blocks(self) -> None:
        blocks = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        assert _content_text(blocks) == "first\nsecond"

    def test_list_filters_none(self) -> None:
        blocks = [
            {"type": "text", "text": "keep"},
            {"type": "image_url", "text": "drop"},
        ]
        assert _content_text(blocks) == "keep"

    def test_empty_list(self) -> None:
        assert _content_text([]) == ""

    def test_none_input(self) -> None:
        assert _content_text(None) == ""

    def test_int_input(self) -> None:
        assert _content_text(42) == ""

    def test_deeply_nested(self) -> None:
        assert _content_text({"content": {"content": "deep"}}) == "deep"

    def test_bytes_input(self) -> None:
        assert _content_text(b"test") == ""

    def test_mapping_no_type_is_fine(self) -> None:
        assert _content_text({"key": "value"}) == ""


# ── _extract_python_fence ────────────────────────────────────────────────────


class TestExtractPythonFence:
    def test_extracts_fenced_code(self) -> None:
        result = _extract_python_fence("```python\nx = 1\n```")
        assert result == "x = 1"

    def test_no_fence_returns_original(self) -> None:
        result = _extract_python_fence("plain text")
        assert result == "plain text"

    def test_multiline_fence(self) -> None:
        code = "hello\n```python\ndef foo():\n    pass\n```\nbye"
        result = _extract_python_fence(code)
        assert result == "def foo():\n    pass"

    def test_fence_case_insensitive(self) -> None:
        result = _extract_python_fence("```PYTHON\ncaps\n```")
        assert result == "caps"

    def test_py_fence(self) -> None:
        result = _extract_python_fence("```py\nshort\n```")
        assert result == "short"

    def test_opening_fence_no_close(self) -> None:
        result = _extract_python_fence("before\n```python\ncode only")
        assert result == "code only"

    def test_whitespace_around_fence(self) -> None:
        result = _extract_python_fence("  ```python  \n  indented  \n  ```  ")
        assert result == "indented"
