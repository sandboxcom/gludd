"""Tests for game_generation — LLM response normalization into Python source."""

from __future__ import annotations

from typing import Any, cast

import pytest

from general_ludd.cloud.game_generation import (
    _content_text,
    _extract_python_fence,
    _message_text,
    ensure_lifecycle_start_method,
    normalize_generated_python,
)


class _FakeMessage:
    def __init__(self, content, text=None, raw_response=None):
        self.content = content
        self._text = text
        self.raw_response = raw_response

    @property
    def text(self):
        return self._text


class _CallableTextMessage:
    def __init__(self, content):
        self.content = content

    def text(self):
        return self.content


class TestNormalizeGeneratedPython:
    def test_plain_string_response(self):
        result = normalize_generated_python("print('hello')")
        assert result == "print('hello')"

    def test_strips_whitespace(self):
        result = normalize_generated_python("  \n  code  \n  ")
        assert result == "code"

    def test_extracts_from_python_fence(self):
        result = normalize_generated_python("```python\nprint('hello')\n```")
        assert result == "print('hello')"

    def test_extracts_from_unlabeled_fence(self):
        result = normalize_generated_python("```\nprint('hello')\n```")
        assert result == "print('hello')"

    def test_extracts_from_py_fence_short(self):
        result = normalize_generated_python("```py\nprint('hi')\n```")
        assert result == "print('hi')"

    def test_first_fence_wins_with_multiple(self):
        result = normalize_generated_python("```python\nfirst\n```\n```python\nsecond\n```")
        assert result == "first"

    def test_skips_empty_fence_uses_next(self):
        result = normalize_generated_python("```python\n\n```\n```python\nreal\n```")
        assert result == "real"

    def test_opening_fence_without_closing(self):
        result = normalize_generated_python("```python\ncode here")
        assert result == "code here"

    def test_opening_fence_with_trailing_backticks_not_matching(self):
        result = normalize_generated_python("```python\ncode\n`")
        assert result == "code\n`"

    def test_ignores_non_python_fence_language(self):
        result = normalize_generated_python('```json\n{"key": 1}\n```\nMore text')
        assert result == "More text"

    def test_fence_case_insensitive(self):
        result = normalize_generated_python("```PYTHON\nUPPER\n```")
        assert result == "UPPER"

    def test_fence_with_spaces_around_language(self):
        result = normalize_generated_python("```  python  \nspaces\n```")
        assert result == "spaces"

    def test_carriage_return_in_fence(self):
        result = normalize_generated_python("```python\r\nwin\r\n```")
        assert result == "win"

    def test_raw_response_preferred_over_outer(self):
        inner = _FakeMessage(content="inner code")
        outer = _FakeMessage(content="outer code", raw_response=inner)
        result = normalize_generated_python(outer)
        assert result == "inner code"

    def test_raw_response_none_falls_back_to_outer(self):
        msg = _FakeMessage(content="fallback code", raw_response=None)
        result = normalize_generated_python(msg)
        assert result == "fallback code"

    def test_raw_response_empty_falls_back_to_outer(self):
        inner = _FakeMessage(content="")
        outer = _FakeMessage(content="outer code", raw_response=inner)
        result = normalize_generated_python(outer)
        assert result == "outer code"

    def test_empty_content_raises(self):
        with pytest.raises(RuntimeError, match="no text content"):
            normalize_generated_python("")

    def test_whitespace_only_raises(self):
        with pytest.raises(RuntimeError, match="no text content"):
            normalize_generated_python("   \n\t  ")

    def test_none_raw_none_outer_raises(self):
        msg = _FakeMessage(content="", raw_response=None)
        with pytest.raises(RuntimeError, match="no text content"):
            normalize_generated_python(msg)


class TestEnsureLifecycleStartMethod:
    def test_adds_start_to_named_class_without_replacing_model_behavior(self):
        source = (
            "class Snake:\n"
            "    def __init__(self):\n"
            "        self.state = 'ready'\n"
            "        self.score = 7\n"
            "\n"
            "    def tick(self):\n"
            "        return self.score\n"
        )

        normalized = ensure_lifecycle_start_method(source, class_name="Snake")
        namespace: dict[str, object] = {}
        exec(normalized, namespace)
        snake = cast(Any, namespace["Snake"])()
        snake.start()

        assert snake.state == "playing"
        assert snake.tick() == 7

    def test_existing_start_is_preserved_byte_for_byte(self):
        source = "class Snake:\n    def start(self):\n        self.state = 'custom'"

        assert ensure_lifecycle_start_method(source, class_name="Snake") == source

    def test_infers_only_single_top_level_game_class(self):
        source = "class Snake:\n    def __init__(self):\n        self.state = 'ready'"

        normalized = ensure_lifecycle_start_method(source)
        namespace: dict[str, object] = {}
        exec(normalized, namespace)
        snake = cast(Any, namespace["Snake"])()
        snake.start()

        assert snake.state == "playing"

    def test_multiple_classes_are_left_for_fail_closed_validation(self):
        source = "class Snake:\n    pass\n\nclass Helper:\n    pass"

        assert ensure_lifecycle_start_method(source) == source

    @pytest.mark.parametrize(
        "source",
        [
            "class Snake(:\n    pass",
            "class Pong:\n    pass",
            "print('no classes')",
        ],
    )
    def test_unrepairable_source_is_left_for_fail_closed_validation(self, source):
        assert ensure_lifecycle_start_method(source, class_name="Snake") == source


class TestMessageText:
    def test_string_content(self):
        assert _message_text(_FakeMessage(content="hello")) == "hello"

    def test_text_property_string(self):
        msg = _FakeMessage(content="ignore", text="use me")
        assert _message_text(msg) == "use me"

    def test_text_property_empty_falls_back_to_content(self):
        msg = _FakeMessage(content="fallback", text="")
        assert _message_text(msg) == "fallback"

    def test_text_property_none_falls_back_to_content(self):
        msg = _FakeMessage(content="fallback", text=None)
        assert _message_text(msg) == "fallback"

    def test_callable_text(self):
        msg = _CallableTextMessage(content="callable result")
        assert _message_text(msg) == "callable result"

    def test_callable_text_preserves_whitespace(self):
        msg = _CallableTextMessage(content="  padded  ")
        assert _message_text(msg) == "  padded  "

    def test_content_as_raw_string(self):
        assert _message_text("bare string") == "bare string"


class TestContentText:
    def test_string(self):
        assert _content_text("hello") == "hello"

    def test_mapping_with_text_key(self):
        assert _content_text({"type": "text", "text": "msg"}) == "msg"

    def test_mapping_with_content_key(self):
        assert _content_text({"type": "text", "content": "msg"}) == "msg"

    def test_mapping_with_value_key(self):
        assert _content_text({"type": "text", "value": "msg"}) == "msg"

    def test_mapping_prefers_text_over_content(self):
        result = _content_text({"type": "text", "text": "first", "content": "second"})
        assert result == "first"

    def test_mapping_prefers_text_over_value(self):
        result = _content_text({"type": "text", "text": "first", "value": "second"})
        assert result == "first"

    def test_mapping_prefers_content_over_value(self):
        result = _content_text({"type": "text", "content": "first", "value": "second"})
        assert result == "first"

    def test_mapping_non_text_block_returns_empty(self):
        result = _content_text({"type": "image_url", "image_url": {"url": "x"}})
        assert result == ""

    def test_mapping_unknown_type_block_filtered(self):
        result = _content_text({"type": "unknown", "text": "hidden"})
        assert result == ""

    def test_mapping_type_case_insensitive(self):
        result = _content_text({"type": "IMAGE_URL", "image_url": {"url": "x"}})
        assert result == ""

    def test_mapping_output_text_block(self):
        assert _content_text({"type": "output_text", "text": "output"}) == "output"

    def test_mapping_input_text_block(self):
        assert _content_text({"type": "input_text", "text": "input"}) == "input"

    def test_mapping_no_type_uses_text(self):
        assert _content_text({"text": "typeless"}) == "typeless"

    def test_mapping_empty_no_keys(self):
        assert _content_text({}) == ""

    def test_sequence_joins_blocks(self):
        result = _content_text(
            [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ]
        )
        assert result == "a\nb"

    def test_sequence_filters_empty_blocks(self):
        result = _content_text(
            [
                {"type": "image_url", "image_url": {}},
                {"type": "text", "text": "only"},
            ]
        )
        assert result == "only"

    def test_sequence_empty_returns_empty(self):
        assert _content_text([]) == ""

    def test_sequence_with_nested_sequence(self):
        result = _content_text([{"type": "text", "text": [{"type": "text", "text": "nested"}]}])
        assert result == "nested"

    def test_bytes_not_treated_as_sequence(self):
        assert _content_text(b"hello") == ""

    def test_bytearray_not_treated_as_sequence(self):
        assert _content_text(bytearray(b"hello")) == ""

    def test_none_returns_empty(self):
        assert _content_text(None) == ""


class TestExtractPythonFence:
    def test_extracts_code_from_standard_fence(self):
        result = _extract_python_fence("```python\ncode\n```")
        assert result == "code"

    def test_no_fence_returns_original_stripped(self):
        result = _extract_python_fence("plain text")
        assert result == "plain text"

    def test_unclosed_fence_extracts_after_opening(self):
        result = _extract_python_fence("```python\nunclosed code")
        assert result == "unclosed code"

    def test_multiline_between_fences(self):
        result = _extract_python_fence("```python\nline1\nline2\nline3\n```")
        assert result == "line1\nline2\nline3"

    def test_code_with_triple_backticks_inside(self):
        result = _extract_python_fence("```python\nprint('```')\n```")
        assert result == "print('"

    def test_preserves_indentation(self):
        result = _extract_python_fence("```python\n    def foo():\n        pass\n```")
        assert result == "def foo():\n        pass"

    def test_empty_fence_body_falls_back_to_opening_strip(self):
        text = "```python\n\n```\n\nafter"
        result = _extract_python_fence(text)
        assert "after" in result

    def test_opening_fence_no_newline_returns_original(self):
        result = _extract_python_fence("```python code")
        assert result == "```python code"

    def test_crlf_in_fence(self):
        result = _extract_python_fence("```python\r\nwindows\r\n```")
        assert result == "windows"
