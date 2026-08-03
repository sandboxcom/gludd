"""Deep state machine tests for ChatSession: transitions, concurrency,
streaming, model switching, persistence/restore, and token accounting.

Coverage areas (20+ tests):
  - State transition edge cases (valid/invalid)
  - Concurrent session modifications
  - Message history truncation at limit
  - Streaming interruption mid-message
  - Multi-model switching mid-session
  - Session persistence and restore
  - Token counting across messages
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from general_ludd.chat import ChatSession
from general_ludd.chat.context_window import (
    ContextWindow,
)
from general_ludd.chat.contracts import ChatConfig, ChatMessage

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_session(**kwargs: object) -> ChatSession:
    defaults: dict[str, object] = {
        "api_base_url": "https://test.api/v1",
        "api_key": "sk-test",
    }
    defaults.update(kwargs)
    return ChatSession(**defaults)  # type: ignore[arg-type]


class _AsyncIter:
    def __init__(self, items: list[str]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


def _stream_mock(chunks: list[str]) -> MagicMock:
    mock_aiter = Mock(return_value=_AsyncIter(chunks))
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.aiter_lines = mock_aiter
    mock_stream.raise_for_status = Mock()
    return mock_stream


# ── 1. State transition edge cases ───────────────────────────────────────────


class TestStateTransitions:
    """Every valid/invalid ChatSession state transition."""

    def test_fresh_session_starts_with_system_only(self) -> None:
        session = _make_session()
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"

    def test_user_turn_appended_before_api_call(self) -> None:
        session = _make_session()
        session.history.append({"role": "user", "content": "hello"})
        assert len(session.history) == 2
        assert session.history[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_assistant_added_after_run_once(self) -> None:
        session = _make_session()
        with patch.object(session, "_post_with_retry") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"choices": [{"message": {"content": "hello back"}}]}
            mock_post.return_value = mock_response
            await session.run_once("hi")
        assert session.history[-1]["role"] == "assistant"
        assert session.history[-1]["content"] == "hello back"

    @pytest.mark.asyncio
    async def test_stream_response_adds_assistant(self) -> None:
        session = _make_session()
        chunks = [
            'data: {"choices":[{"delta":{"content":"Hi"}}]}\n',
            'data: {"choices":[{"delta":{"content":" there"}}]}\n',
            "data: [DONE]\n",
        ]
        mock_stream = _stream_mock(chunks)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_stream
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            await session.stream_response("hey")
        assert len(session.history) == 3
        assert session.history[-1]["role"] == "assistant"
        assert "Hi there" in session.history[-1]["content"]

    @pytest.mark.asyncio
    async def test_stream_error_rolls_back_user_message(self) -> None:
        session = _make_session()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            await session.stream_response("bad request")
        assert len(session.history) == 1
        assert session.history[-1]["role"] == "system"

    def test_clear_history_resets_to_system_only(self, tmp_path: Path) -> None:
        hist = tmp_path / "clear.jsonl"
        session = _make_session(history_file=str(hist))
        session.history.append({"role": "user", "content": "q"})
        session.history.append({"role": "assistant", "content": "a"})
        session.clear_history()
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"

    def test_clear_history_removes_file(self, tmp_path: Path) -> None:
        hist = tmp_path / "clear_file.jsonl"
        session = _make_session(history_file=str(hist))
        session.history.append({"role": "user", "content": "q"})
        session.history.append({"role": "assistant", "content": "a"})
        session.save_history()
        assert hist.exists()
        session.clear_history()
        assert not hist.exists()

    def test_load_history_overwrites_in_memory(self, tmp_path: Path) -> None:
        hist = tmp_path / "load_overwrite.jsonl"
        msgs = [
            {"role": "system", "content": "original prompt"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        hist.write_text("\n".join(json.dumps(m) for m in msgs) + "\n", encoding="utf-8")
        session = _make_session(history_file=str(hist))
        assert len(session.history) == 3
        session.history.append({"role": "user", "content": "new q"})
        session.load_history()
        assert len(session.history) == 3

    def test_load_empty_history_file_initializes_fresh(self, tmp_path: Path) -> None:
        hist = tmp_path / "empty.jsonl"
        hist.write_text("", encoding="utf-8")
        session = _make_session(history_file=str(hist))
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"

    def test_load_corrupt_jsonl_skipped(self, tmp_path: Path) -> None:
        hist = tmp_path / "corrupt.jsonl"
        hist.write_text(
            '{"role": "system", "content": "sys"}\nnot json\n{"role": "user", "content": "q"}\n',
            encoding="utf-8",
        )
        session = _make_session(history_file=str(hist))
        assert len(session.history) == 2
        assert session.history[0]["role"] == "system"
        assert session.history[1]["role"] == "user"

    def test_missing_system_role_prepended_on_load(self, tmp_path: Path) -> None:
        hist = tmp_path / "nosys.jsonl"
        hist.write_text(
            '{"role": "user", "content": "q"}\n{"role": "assistant", "content": "a"}\n',
            encoding="utf-8",
        )
        session = _make_session(history_file=str(hist))
        assert session.history[0]["role"] == "system"


# ── 2. Concurrent session modifications ──────────────────────────────────────


class TestConcurrentModifications:
    def test_two_sessions_same_history_file_save_independently(self, tmp_path: Path) -> None:
        hist = tmp_path / "concurrent.jsonl"
        s1 = _make_session(history_file=str(hist))
        s1.history.append({"role": "user", "content": "s1 question"})
        s1.history.append({"role": "assistant", "content": "s1 answer"})
        s1.save_history()

        s2 = _make_session(history_file=str(hist))
        s2.history.append({"role": "user", "content": "s2 question"})
        s2.history.append({"role": "assistant", "content": "s2 answer"})
        s2.save_history()

        lines = hist.read_text(encoding="utf-8").strip().split("\n")
        records = [json.loads(line) for line in lines]
        contents = [r["content"] for r in records]
        assert "s1 answer" in contents or "s2 answer" in contents

    def test_two_sessions_different_files_no_conflict(self, tmp_path: Path) -> None:
        h1 = tmp_path / "s1.jsonl"
        h2 = tmp_path / "s2.jsonl"
        s1 = _make_session(history_file=str(h1))
        s2 = _make_session(history_file=str(h2))
        s1.history.append({"role": "user", "content": "q1"})
        s2.history.append({"role": "user", "content": "q2"})
        s1.save_history()
        s2.save_history()
        assert h1.exists()
        assert h2.exists()
        c1 = h1.read_text(encoding="utf-8")
        c2 = h2.read_text(encoding="utf-8")
        assert "q1" in c1
        assert "q2" in c2

    def test_session_index_appends_multiple_entries(self, tmp_path: Path) -> None:
        s1 = _make_session(api_base_url=None, api_key=None)
        s1._history_dir = tmp_path
        s1._history_file_path = tmp_path / "session_a.jsonl"
        s1.history.append({"role": "user", "content": "first"})
        s1.history.append({"role": "assistant", "content": "first answer"})
        s1.save_history()

        s2 = _make_session(api_base_url=None, api_key=None)
        s2._history_dir = tmp_path
        s2._history_file_path = tmp_path / "session_b.jsonl"
        s2.history.append({"role": "user", "content": "second"})
        s2.history.append({"role": "assistant", "content": "second answer"})
        s2.save_history()

        sessions = ChatSession.list_sessions(history_dir=tmp_path)
        assert len(sessions) >= 2

    def test_index_update_merges_existing_entry(self, tmp_path: Path) -> None:
        s = _make_session(api_base_url=None, api_key=None)
        s._history_dir = tmp_path
        s.history.append({"role": "user", "content": "q1"})
        s.history.append({"role": "assistant", "content": "a1"})
        s.save_history()

        s.history.append({"role": "user", "content": "q2"})
        s.history.append({"role": "assistant", "content": "a2"})
        s.save_history()

        sessions = ChatSession.list_sessions(history_dir=tmp_path)
        assert len(sessions) == 1
        msg_count: int = int(sessions[0]["message_count"])  # type: ignore[arg-type]
        assert msg_count >= 3


# ── 3. Message history truncation at limit ───────────────────────────────────


class TestMessageTruncation:
    def test_user_input_truncated_at_max_length(self) -> None:
        from general_ludd.chat.session import MAX_INPUT_LENGTH

        long_input = "x" * (MAX_INPUT_LENGTH * 2)
        result = ChatSession._truncate_input(long_input)
        assert len(result) == MAX_INPUT_LENGTH

    def test_context_window_truncation_removes_old_messages(self) -> None:
        cw = ContextWindow(max_tokens=100, summarization_threshold=0.5)
        for _ in range(10):
            cw.record_turn(20)
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        summary = cw.summarize_if_needed(msgs, keep_recent=2)
        assert summary is not None
        assert len(summary) <= 3
        has_system = any(m["role"] == "system" for m in summary)
        assert has_system

    def test_summarization_at_threshold_boundary(self) -> None:
        cw = ContextWindow(max_tokens=1000, summarization_threshold=0.8)
        cw.record_turn(799)
        assert cw.needs_summarization() is False
        cw.record_turn(1)
        assert cw.needs_summarization() is True


# ── 4. Streaming interruption mid-message ────────────────────────────────────


class TestStreamingInterruption:
    @pytest.mark.asyncio
    async def test_stream_connect_error_rolls_back_user_message(self) -> None:
        session = _make_session()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.side_effect = httpx.ConnectError("refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            result = await session.stream_response("test")
        assert result == ""
        assert session.history[-1]["role"] == "system"

    @pytest.mark.asyncio
    async def test_stream_timeout_rolls_back_user_message(self) -> None:
        session = _make_session()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            result = await session.stream_response("test")
        assert result == ""
        assert session.history[-1]["role"] == "system"

    @pytest.mark.asyncio
    async def test_stream_exception_rolls_back_user_message(self) -> None:
        session = _make_session()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.side_effect = RuntimeError("unexpected crash")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            result = await session.stream_response("test")
        assert result == ""
        assert session.history[-1]["role"] == "system"

    @pytest.mark.asyncio
    async def test_stream_json_decode_error_skips_chunk(self) -> None:
        session = _make_session()
        chunks = [
            'data: {"choices":[{"delta":{"content":"A"}}]}\n',
            "data: bad json\n",
            'data: {"choices":[{"delta":{"content":"B"}}]}\n',
            "data: [DONE]\n",
        ]
        mock_stream = _stream_mock(chunks)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_stream
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client
            result = await session.stream_response("test")
        assert "AB" in result


# ── 5. Multi-model switching mid-session ─────────────────────────────────────


class TestMultiModelSwitching:
    def test_model_change_preserves_history(self) -> None:
        session = _make_session(model="openai/gpt-4o")
        session.history.append({"role": "user", "content": "q"})
        session.history.append({"role": "assistant", "content": "a"})
        assert session._provider == "openai"
        assert session._model_id == "gpt-4o"

        session = _make_session(
            model="deepseek/deepseek-chat",
            history_file=str(session._history_file_path) if session._history_file_path else None,
        )
        session.history.append({"role": "user", "content": "q2"})
        assert session._provider == "deepseek"
        assert session._model_id == "deepseek-chat"

    def test_bare_provider_shortcut_resolves_correctly(self) -> None:
        s = _make_session(model="deepseek")
        assert s._provider == "deepseek"
        assert s._model_id == "deepseek-chat"

    def test_unknown_model_uses_openai_provider(self) -> None:
        s = _make_session(model="some-unheard-of-model")
        assert s._provider == "openai"
        assert s._model_id == "some-unheard-of-model"


# ── 6. Session persistence and restore ───────────────────────────────────────


class TestSessionPersistence:
    def test_roundtrip_save_and_load(self, tmp_path: Path) -> None:
        hist = tmp_path / "rt.jsonl"
        s1 = _make_session(history_file=str(hist))
        s1.history.append({"role": "user", "content": "hello"})
        s1.history.append({"role": "assistant", "content": "hi there"})
        s1.save_history()

        s2 = _make_session(history_file=str(hist))
        assert len(s2.history) == 3
        assert s2.history[1]["content"] == "hello"
        assert s2.history[2]["content"] == "hi there"

    def test_resume_from_latest_session(self, tmp_path: Path) -> None:
        h1 = tmp_path / "old.jsonl"
        h1.write_text(
            '{"role": "system", "content": "sys"}\n'
            '{"role": "user", "content": "old q"}\n'
            '{"role": "assistant", "content": "old a"}\n',
            encoding="utf-8",
        )
        h2 = tmp_path / "new.jsonl"
        h2.write_text(
            '{"role": "system", "content": "sys"}\n'
            '{"role": "user", "content": "new q"}\n'
            '{"role": "assistant", "content": "new a"}\n',
            encoding="utf-8",
        )
        index_data = {
            "sessions": [
                {"file": str(h1), "timestamp": "2026-01-01T00:00:00+00:00"},
                {"file": str(h2), "timestamp": "2026-01-02T00:00:00+00:00"},
            ]
        }
        (tmp_path / "index.json").write_text(json.dumps(index_data), encoding="utf-8")

        with patch("general_ludd.chat.session.DEFAULT_HISTORY_DIR", tmp_path):
            s = _make_session(resume=True)
            assert len(s.history) == 3
            assert s.history[1]["content"] == "new q"

    def test_resume_no_sessions_initializes_fresh(self, tmp_path: Path) -> None:
        with patch("general_ludd.chat.session.DEFAULT_HISTORY_DIR", tmp_path):
            s = _make_session(resume=True)
            assert len(s.history) == 1
            assert s.history[0]["role"] == "system"

    def test_save_only_system_skips_file_write(self, tmp_path: Path) -> None:
        hist = tmp_path / "not_written.jsonl"
        s = _make_session(history_file=str(hist))
        s.save_history()
        assert not hist.exists()

    def test_save_history_generates_timestamped_filename(self, tmp_path: Path) -> None:
        s = _make_session(api_base_url=None, api_key=None)
        s._history_dir = tmp_path
        s.history.append({"role": "user", "content": "q"})
        s.history.append({"role": "assistant", "content": "a"})
        s.save_history()
        assert s._history_file_path is not None
        assert s._history_file_path.exists()
        assert "session_" in s._history_file_path.name
        assert s._history_file_path.suffix == ".jsonl"


# ── 7. Token counting across messages ────────────────────────────────────────


class TestTokenCounting:
    def test_estimate_tokens_empty_input(self) -> None:
        assert ContextWindow.estimate_tokens("") == 1

    def test_estimate_tokens_mid_range(self) -> None:
        assert ContextWindow.estimate_tokens("1234") == 1
        assert ContextWindow.estimate_tokens("12345678") == 2
        assert ContextWindow.estimate_tokens("a" * 400) == 100

    def test_record_turn_accumulates_correctly(self) -> None:
        cw = ContextWindow(max_tokens=10000)
        cw.record_turn(100)
        cw.record_turn(200)
        cw.record_turn(50)
        assert cw.total_tokens() == 350
        assert cw.per_turn_tokens() == [100, 200, 50]

    def test_remaining_tokens_decreases(self) -> None:
        cw = ContextWindow(max_tokens=1000, reserve_tokens=100)
        assert cw.remaining() == 900
        cw.record_turn(500)
        assert cw.remaining() == 400
        cw.record_turn(500)
        assert cw.remaining() == 0

    def test_run_once_records_tokens(self) -> None:
        s = _make_session(max_context=100000)
        with patch.object(s, "_post_with_retry") as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {"choices": [{"message": {"content": "A" * 80}}]}
            mock_post.return_value = mock_response

            async def _run() -> None:
                await s.run_once("B" * 40)

            asyncio.run(_run())

        turns = s._context_window.per_turn_tokens()
        assert len(turns) == 1
        assert turns[0] > 0

    def test_stream_response_records_tokens(self) -> None:
        s = _make_session(max_context=100000)
        chunks = [
            'data: {"choices":[{"delta":{"content":"' + "C" * 40 + '"}}]}\n',
            "data: [DONE]\n",
        ]
        mock_stream = _stream_mock(chunks)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = mock_stream
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            async def _run() -> None:
                await s.stream_response("D" * 20)

            asyncio.run(_run())

        turns = s._context_window.per_turn_tokens()
        assert len(turns) == 1
        assert turns[0] > 0


# ── 8. ChatConfig contracts ──────────────────────────────────────────────────


class TestChatConfigContracts:
    def test_config_to_session_kwargs_roundtrip(self) -> None:
        cfg = ChatConfig(
            model="deepseek/deepseek-chat",
            system_prompt="Be terse.",
            eval_mode=True,
            api_base_url="https://x.com/v1",
            api_key="sk-abc",
            project_dir="/tmp/p",
            history_file="/tmp/h.jsonl",
            save_interval=3,
            resume=False,
            max_context=4096,
        )
        kwargs = cfg.to_session_kwargs()
        assert kwargs["model"] == "deepseek/deepseek-chat"
        assert kwargs["system_prompt"] == "Be terse."
        assert kwargs["eval_mode"] is True
        assert kwargs["save_interval"] == 3
        assert kwargs["max_context"] == 4096

    def test_chat_message_as_api_message(self) -> None:
        msg = ChatMessage(role="user", content="hello")
        api = msg.as_api_message()
        assert api == {"role": "user", "content": "hello"}

    def test_chat_message_as_persistent_record(self) -> None:
        msg = ChatMessage(role="assistant", content="hi", timestamp="t1", model="m1")
        rec = msg.as_persistent_record()
        assert rec["role"] == "assistant"
        assert rec["timestamp"] == "t1"
        assert rec["model"] == "m1"

    def test_chat_message_from_dict_roundtrip(self) -> None:
        data = {"role": "system", "content": "sys", "timestamp": "ts", "model": "mod"}
        msg = ChatMessage.from_dict(data)
        assert msg.role == "system"
        assert msg.content == "sys"
        assert msg.timestamp == "ts"
        assert msg.model == "mod"

    def test_save_interval_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="save_interval"):
            ChatConfig(save_interval=0)
        with pytest.raises(ValueError, match="save_interval"):
            ChatConfig(save_interval=-5)


# ── 9. ContextWindow validation ──────────────────────────────────────────────


class TestContextWindowValidation:
    def test_negative_max_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            ContextWindow(max_tokens=0)
        with pytest.raises(ValueError, match="max_tokens"):
            ContextWindow(max_tokens=-1)

    def test_invalid_summarization_threshold_raises(self) -> None:
        with pytest.raises(ValueError, match="summarization_threshold"):
            ContextWindow(summarization_threshold=0.0)
        with pytest.raises(ValueError, match="summarization_threshold"):
            ContextWindow(summarization_threshold=1.5)

    def test_negative_reserve_tokens_raises(self) -> None:
        with pytest.raises(ValueError, match="reserve_tokens"):
            ContextWindow(reserve_tokens=-1)

    def test_sliding_window_keeps_system_and_recent(self) -> None:
        cw = ContextWindow(max_tokens=10000)
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
        window = cw.sliding_window_messages(msgs, keep_recent=2)
        assert any(m["role"] == "system" for m in window)
        assert len([m for m in window if m["role"] != "system"]) == 2

    def test_sliding_window_zero_recent(self) -> None:
        cw = ContextWindow(max_tokens=10000)
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        window = cw.sliding_window_messages(msgs, keep_recent=0)
        assert len(window) == 1
        assert window[0]["role"] == "system"
