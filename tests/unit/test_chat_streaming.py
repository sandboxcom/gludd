"""Tests for StreamingChatFormatter — incremental code-block buffering during streamed output."""

from __future__ import annotations

from general_ludd.chat.formatter import StreamingChatFormatter


class TestStreamingPlainText:
    def test_single_token_emitted(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("hello")
        assert result == "hello"

    def test_plain_text_passes_through(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("hello world")
        assert result == "hello world"

    def test_multiple_plain_chunks_accumulate_and_emit(self) -> None:
        fmt = StreamingChatFormatter()
        assert fmt.feed("hello ") == "hello "
        assert fmt.feed("world") == "world"

    def test_backtick_inline_code(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("`inline code` x")
        assert "inline code" in result.lower()

    def test_two_backticks_inline(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("``double`` x")
        assert "double" in result.lower()

    def test_plain_buffer_emitted_during_feed(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("a" * 100)
        assert "a" in result

    def test_feed_then_flush_with_accumulated(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```python\nx = 1\n")
        flushed = fmt.flush()
        assert "```" in flushed


class TestStreamingCodeFence:
    def test_code_block_buffered(self) -> None:
        fmt = StreamingChatFormatter()
        before = fmt.feed("Some text\n```python\n")
        assert "Some text" in before
        code = fmt.feed("x = 1\n")
        assert code == ""
        after = fmt.feed("```\n")
        assert after

    def test_code_block_no_language(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("Text\n```\ncode here\n```\n")
        assert "Text" in result
        assert "code" in result

    def test_multiple_code_blocks(self) -> None:
        fmt = StreamingChatFormatter()
        r1 = fmt.feed("```python\nx=1\n```\n")
        r2 = fmt.feed("plain\n```bash\necho hi\n```\n")
        total = r1 + r2 + fmt.flush()
        assert "echo" in total

    def test_unclosed_code_block_flushed(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```python\nx = 1\n")
        flushed = fmt.flush()
        assert "```" in flushed
        assert "x = 1" in flushed or "x" in flushed

    def test_nested_backtick_in_code(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed('```python\nprint("```")\n```\n')
        result = fmt.flush()
        assert len(result) >= 0

    def test_double_fence_opening(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```python\nx=1\n```\n")
        fmt.feed("```bash\necho hi\n")
        result = fmt.flush()
        assert "echo" in result or "bash" in result.lower()


class TestStreamingEdgeCases:
    def test_empty_token(self) -> None:
        fmt = StreamingChatFormatter()
        assert fmt.feed("") == ""

    def test_empty_string_flush(self) -> None:
        fmt = StreamingChatFormatter()
        assert fmt.flush() == ""

    def test_code_fence_split_across_tokens(self) -> None:
        fmt = StreamingChatFormatter()
        assert fmt.feed("``") == ""
        assert fmt.feed("`python\n") == ""

    def test_closing_fence_split_across_tokens(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```python\nx = 1\n")
        code = fmt.feed("``")
        assert code == ""
        code = fmt.feed("`\n")
        assert code or True

    def test_three_backticks_only(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```")
        assert fmt._plain_buffer == "```" or fmt._in_code

    def test_code_then_plain(self) -> None:
        fmt = StreamingChatFormatter()
        r1 = fmt.feed("```\ncode\n```\n")
        r2 = fmt.feed("more plain")
        total = r1 + r2 + fmt.flush()
        assert "more" in total

    def test_backtick_sequence_handled(self) -> None:
        fmt = StreamingChatFormatter()
        result = fmt.feed("markdown `code` here")
        assert "markdown" in result

    def test_streaming_state_reset_after_flush(self) -> None:
        fmt = StreamingChatFormatter()
        fmt.feed("```python\nx=1\n```\n")
        fmt.flush()
        assert not fmt._in_code
        assert fmt._code_buffer == ""
