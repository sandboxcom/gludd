"""TDD tests for StreamingChatFormatter — written BEFORE implementation."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from general_ludd.chat.formatter import StreamingChatFormatter


class _AsyncLineStream:
    """Wraps a list of SSE lines as an async iterable for httpx mocking."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._idx = 0

    def __aiter__(self) -> _AsyncLineStream:
        return self

    async def __anext__(self) -> str:
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line


class TestStreamingFormatterPlainText:
    def test_plain_text_passes_through(self) -> None:
        f = StreamingChatFormatter()
        assert f.feed("Hello") == "Hello"
        assert f.feed(" world") == " world"

    def test_empty_feed_returns_empty(self) -> None:
        f = StreamingChatFormatter()
        assert f.feed("") == ""

    def test_flush_empty_returns_empty(self) -> None:
        f = StreamingChatFormatter()
        assert f.flush() == ""

    def test_flush_emits_held_back_partial_fence(self) -> None:
        f = StreamingChatFormatter()
        emitted = f.feed("hello``")
        assert emitted == "hello"
        remaining = f.flush()
        assert "``" in remaining


class TestStreamingFormatterCodeBlocks:
    def test_code_block_buffered_then_highlighted(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("```python\n")
        result += f.feed("print(1)\n")
        result += f.feed("```")
        assert "print" in result
        assert "1" in result

    def test_code_block_not_emitted_until_closed(self) -> None:
        f = StreamingChatFormatter()
        r1 = f.feed("```python\n")
        r2 = f.feed("print(1)\n")
        assert r1 == ""
        assert r2 == ""

    def test_flush_emits_incomplete_code_block(self) -> None:
        f = StreamingChatFormatter()
        f.feed("```python\nprint(1)\n")
        result = f.flush()
        assert "print(1)" in result

    def test_mixed_plain_and_code(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("Here is code:\n")
        result += f.feed("```python\n")
        result += f.feed("x = 1\n")
        result += f.feed("```\n")
        result += f.feed("Done.")
        assert "Here is code:" in result
        assert "Done." in result

    def test_multiple_code_blocks(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("```python\nprint(1)\n```")
        result += f.feed(" and ")
        result += f.feed("```bash\necho hi\n```")
        assert "print" in result
        assert "echo" in result

    def test_plain_text_between_code_blocks_emitted(self) -> None:
        f = StreamingChatFormatter()
        f.feed("```python\nprint(1)\n```")
        result = f.feed(" middle text ")
        assert "middle text" in result

    def test_code_block_with_no_language(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("```\nhello\n```")
        assert "hello" in result


class TestStreamingFormatterFenceSplitting:
    def test_fence_split_across_tokens(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("hello ``")
        result += f.feed("`\nprint(42)\n```")
        assert "hello" in result
        assert "42" in result

    def test_code_body_split_across_tokens(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("```python\n")
        result += f.feed("def f")
        result += f.feed("oo():\n")
        result += f.feed("    return 1\n")
        result += f.feed("```")
        assert "def" in result
        assert "foo" in result

    def test_trailing_backticks_held(self) -> None:
        f = StreamingChatFormatter()
        r1 = f.feed("text with backtick `")
        assert "text with backtick" in r1

    def test_text_after_closing_fence_processed(self) -> None:
        f = StreamingChatFormatter()
        result = ""
        result += f.feed("```python\nx=1\n```")
        result += f.feed("after")
        assert "after" in result


class TestStreamingFormatterStateReset:
    def test_reuse_formatter_after_flush(self) -> None:
        f = StreamingChatFormatter()
        f.feed("```python\nprint(1)\n```")
        f.flush()
        assert f.feed("new text") == "new text"

    def test_is_not_in_code_after_plain(self) -> None:
        f = StreamingChatFormatter()
        assert f._in_code is False

    def test_is_in_code_during_block(self) -> None:
        f = StreamingChatFormatter()
        f.feed("```python\n")
        assert f._in_code is True

    def test_not_in_code_after_close(self) -> None:
        f = StreamingChatFormatter()
        f.feed("```python\nprint(1)\n```")
        assert f._in_code is False


class TestStreamResponseIntegration:
    @pytest.mark.asyncio
    async def test_stream_response_uses_streaming_formatter(self) -> None:
        from general_ludd.chat import ChatSession

        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.aiter_lines = Mock(return_value=_AsyncLineStream(sse_lines))

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(httpx.AsyncClient, "stream", return_value=mock_cm):
            result = await session.stream_response("test prompt")

        assert "Hello" in result
        assert "world" in result
        assert len(session.history) == 3
        assert session.history[-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_stream_response_code_block_buffering(self) -> None:
        from general_ludd.chat import ChatSession

        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"```python\\n"}}]}',
            'data: {"choices":[{"delta":{"content":"print(1)\\n"}}]}',
            'data: {"choices":[{"delta":{"content":"```"}}]}',
            "data: [DONE]",
        ]

        mock_response = AsyncMock()
        mock_response.raise_for_status = Mock()
        mock_response.aiter_lines = Mock(return_value=_AsyncLineStream(sse_lines))

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(httpx.AsyncClient, "stream", return_value=mock_cm):
            result = await session.stream_response("show me code")

        assert "print" in result

    @pytest.mark.asyncio
    async def test_stream_response_connect_error(self) -> None:
        from general_ludd.chat import ChatSession

        session = ChatSession(
            model="deepseek/deepseek-chat",
            api_base_url="https://test.api/v1",
            api_key="sk-test",
        )

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch.object(httpx.AsyncClient, "stream", return_value=mock_cm):
            result = await session.stream_response("hello")

        assert result == ""


class TestCLIStreamFlag:
    def test_stream_flag_exists_in_parser(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["chat", "--stream", "--eval", "hello"])
        assert getattr(args, "stream", None) is True

    def test_stream_defaults_false(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["chat", "--eval", "hello"])
        assert getattr(args, "stream", None) is False

    def test_stream_without_eval_is_accepted(self) -> None:
        from general_ludd.cli import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["chat", "--stream"])
        assert args.stream is True
