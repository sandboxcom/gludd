from __future__ import annotations

from unittest import mock

from general_ludd.chat import MessageFormatter


class TestDetectCodeBlocks:
    def test_single_block_with_language(self) -> None:
        blocks = MessageFormatter.detect_code_blocks(
            "Some text\n```python\nprint(1)\n```\nMore text"
        )
        assert blocks == [("python", "print(1)")]

    def test_multiple_blocks(self) -> None:
        text = "```python\nx = 1\n```\n```json\n{}\n```"
        blocks = MessageFormatter.detect_code_blocks(text)
        assert blocks == [("python", "x = 1"), ("json", "{}")]

    def test_no_language_tag_returns_empty_string(self) -> None:
        blocks = MessageFormatter.detect_code_blocks("```\nhello\n```")
        assert blocks == [("", "hello")]

    def test_no_code_blocks(self) -> None:
        blocks = MessageFormatter.detect_code_blocks("Plain text only.")
        assert blocks == []

    def test_empty_code_block(self) -> None:
        blocks = MessageFormatter.detect_code_blocks("```python\n\n```")
        assert blocks == [("python", "")]

    def test_empty_code_block_no_lang(self) -> None:
        blocks = MessageFormatter.detect_code_blocks("```\n\n```")
        assert blocks == [("", "")]

    def test_mismatched_fences_consumed_by_regex(self) -> None:
        text = "```python\nprint(1)\n``\nMore text\n```shell\nwhoami\n```"
        blocks = MessageFormatter.detect_code_blocks(text)
        assert len(blocks) >= 1
        assert blocks[0][0] == "python"
        assert "print(1)" in blocks[0][1]

    def test_nested_fence_like_content(self) -> None:
        text = "```python\nx = '```'\n```\nplain\n```json\n{}\n```"
        blocks = MessageFormatter.detect_code_blocks(text)
        assert len(blocks) >= 1
        assert blocks[0][0] == "python"
        assert "x = '" in blocks[0][1]

    def test_nested_triple_backtick_in_code(self) -> None:
        text = '```python\nprint("```")\n```'
        blocks = MessageFormatter.detect_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0][0] == "python"
        assert 'print("' in blocks[0][1]


class TestResolveLexerName:
    def test_known_language_tag_maps_correctly(self) -> None:
        assert MessageFormatter._resolve_lexer_name("python", "x = 1") == "python"
        assert MessageFormatter._resolve_lexer_name("json", '{"a": 1}') == "json"
        assert MessageFormatter._resolve_lexer_name("bash", "echo hi") == "bash"
        assert MessageFormatter._resolve_lexer_name("yaml", "key: val") == "yaml"

    def test_empty_tag_auto_detects_or_falls_back(self) -> None:
        result = MessageFormatter._resolve_lexer_name(
            "", "import os\n\ndef foo(x):\n    return x + 1\n"
        )
        assert result in ("python", "python2", "python3", "text")

    def test_unknown_tag_falls_back_to_auto_detect(self) -> None:
        result = MessageFormatter._resolve_lexer_name(
            "foobarxyz", "import os\n\ndef foo(x):\n    return x + 1\n"
        )
        assert result in ("python", "python2", "python3", "text")

    def test_empty_code_falls_back_to_text(self) -> None:
        assert MessageFormatter._resolve_lexer_name("python", "") == "text"
        assert MessageFormatter._resolve_lexer_name("", "   ") == "text"

    def test_unknown_tag_with_generic_code(self) -> None:
        result = MessageFormatter._resolve_lexer_name("wombat999", "hello world")
        assert isinstance(result, str)
        assert len(result) > 0


class TestHighlight:
    def test_highlight_preserves_non_code_text(self) -> None:
        result = MessageFormatter.highlight("Hello world")
        assert "Hello world" in result

    def test_no_code_blocks_returns_plain_text(self) -> None:
        result = MessageFormatter.highlight("Just some plain text with no fences.")
        assert result == "Just some plain text with no fences."

    def test_highlight_python_code_block(self) -> None:
        result = MessageFormatter.highlight("```python\ndef foo():\n    return 42\n```")
        assert "def" in result
        assert "foo" in result
        assert "42" in result

    def test_highlight_unknown_language_falls_back_to_auto_detect(self) -> None:
        result = MessageFormatter.highlight("```unknownxyz\ndef foo():\n    return 1\n```")
        assert "def" in result
        assert "return" in result

    def test_multiple_code_blocks_with_different_languages(self) -> None:
        result = MessageFormatter.highlight(
            "```python\nprint(1)\n```\n```bash\necho hi\n```"
        )
        assert "print" in result
        assert "echo" in result

    def test_empty_code_block_passthrough(self) -> None:
        result = MessageFormatter.highlight("```python\n\n```")
        assert "```" in result

    def test_empty_code_block_no_lang_passthrough(self) -> None:
        result = MessageFormatter.highlight("```\n\n```")
        assert "```" in result

    def test_plain_text_no_backticks_passthrough(self) -> None:
        result = MessageFormatter.highlight("No backticks here at all.")
        assert result == "No backticks here at all."

    def test_inline_backtick_not_matched(self) -> None:
        result = MessageFormatter.highlight("Use `print()` to output data.")
        assert result == "Use `print()` to output data."

    def test_rich_not_available_falls_back(self) -> None:
        with (
            mock.patch.dict("sys.modules", {"rich.console": None, "rich.syntax": None}),
            mock.patch("builtins.__import__", side_effect=ImportError),
        ):
            result = MessageFormatter.highlight("```python\nx = 1\n```")
            assert "```python" in result

    def test_auto_detect_no_tag(self) -> None:
        result = MessageFormatter.highlight("```\ndef foo():\n    pass\n```")
        assert "def" in result
        assert "foo" in result

    def test_highlight_code_block(self) -> None:
        result = MessageFormatter.highlight("```python\nprint(1)\n```")
        assert "print" in result
        assert "1" in result

    def test_empty_string(self) -> None:
        result = MessageFormatter.highlight("")
        assert result == ""

    def test_only_backticks_no_code(self) -> None:
        result = MessageFormatter.highlight("``` ```")
        assert len(result) >= 0

    def test_unclosed_fence_not_matched(self) -> None:
        result = MessageFormatter.highlight("```python\nprint(1)")
        assert "python" in result
        assert "print(1)" in result

    def test_nested_triple_backtick_rendered(self) -> None:
        result = MessageFormatter.highlight('```python\nprint("```")\n```')
        assert len(result) > 0

    def test_very_long_code_block_truncated(self) -> None:
        from general_ludd.chat.formatter import MAX_CODE_BLOCK_LENGTH

        long_code = "x = 1\n" * (MAX_CODE_BLOCK_LENGTH // 6 + 100)
        result = MessageFormatter.highlight(f"```python\n{long_code}\n```")
        assert "truncated" in result.lower()

    def test_nested_fence_like_mid_block(self) -> None:
        text = "```python\nx = 1\ny = '''\nz = 2\n```"
        result = MessageFormatter.highlight(text)
        assert len(result) > 0
        assert "```" not in result or result.count("```") <= 2
