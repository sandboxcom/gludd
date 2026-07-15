"""Tests for ChatSession history persistence and session resume.

Adapted to match the actual session.py implementation:
  - No `history_path` property; uses `_history_file_path` (private) and `DEFAULT_HISTORY_DIR`
  - No public `load_history()` method; history loads in `__init__` via `_load_history()`
  - No `ChatSession.resume()` classmethod; uses `ChatSession(history_file=...)`
  - No `clear_history()` method
  - `DEFAULT_HISTORY_DIR` = `Path.home() / ".gludd" / "chat_history"`
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from general_ludd.chat import ChatSession
from general_ludd.chat.history import ChatHistory
from general_ludd.chat.session import DEFAULT_HISTORY_DIR


class TestChatHistoryPath:
    """History file location uses DEFAULT_HISTORY_DIR from session module."""

    def test_default_history_path_resolves_to_cache_dir(self) -> None:
        """Default history path is ~/.gludd/chat_history."""
        assert DEFAULT_HISTORY_DIR is not None, (
            "DEFAULT_HISTORY_DIR must be set in session module"
        )

        assert "gludd" in str(DEFAULT_HISTORY_DIR), (
            f"DEFAULT_HISTORY_DIR={DEFAULT_HISTORY_DIR} must contain 'gludd'"
        )
        assert str(DEFAULT_HISTORY_DIR).endswith("chat_history"), (
            "history dir should end with 'chat_history'"
        )

    def test_custom_history_path_from_cli_flag(self) -> None:
        """--history FILE overrides default to user-supplied path."""
        session = ChatSession(history_file="/tmp/test_history.jsonl")

        assert session._history_file_path == Path("/tmp/test_history.jsonl"), (
            "ChatSession must accept --history flag and use it as _history_file_path"
        )


class TestChatHistorySave:
    """ChatSession.save_history() writes chat history to disk."""

    def test_save_history_writes_file(self, tmp_path: Path) -> None:
        """save_history() creates the history file at the configured path."""
        history_file = tmp_path / "test_history.jsonl"
        session = ChatSession(history_file=str(history_file))

        session.history = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        session.save_history()

        assert os.path.isfile(history_file), (
            "save_history() must create the history file on disk"
        )

    def test_save_history_writes_jsonl_format(self, tmp_path: Path) -> None:
        """save_history() writes one JSON object per line (JSON-lines format)."""
        history_file = tmp_path / "test_history.jsonl"
        session = ChatSession(history_file=str(history_file))

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        session.history = messages

        session.save_history()

        lines = history_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3, (
            f"expected 3 JSON lines, got {len(lines)}"
        )
        for line in lines:
            assert line.strip(), "no empty lines allowed in JSONL history"
            parsed = json.loads(line)
            assert "role" in parsed
            assert "content" in parsed

    def test_save_history_writes_to_existing_dir(self, tmp_path: Path) -> None:
        """save_history() writes to configured file within an existing directory."""
        history_file = tmp_path / "chat.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [{"role": "user", "content": "test"}]

        session.save_history()

        assert os.path.isfile(history_file), (
            "save_history() must write the file when parent dir exists"
        )

    def test_save_history_writes_all_messages(self, tmp_path: Path) -> None:
        """save_history() persists all messages when more than just system prompt."""
        history_file = tmp_path / "all_history.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hello"},
        ]

        session.save_history()

        assert os.path.isfile(history_file), (
            "save_history() persists all messages including system"
        )


class TestChatHistoryLoad:
    """History loads in ChatSession.__init__ via _load_history()."""

    def test_load_history_restores_messages(self, tmp_path: Path) -> None:
        """History loads from JSONL file at construction time."""
        history_file = tmp_path / "load_test.jsonl"
        original_messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        with open(history_file, "w") as f:
            for msg in original_messages:
                f.write(json.dumps(msg) + "\n")

        session = ChatSession(history_file=str(history_file))

        assert session.history, "history must be loaded at construction"
        assert len(session.history) == len(original_messages), (
            f"expected {len(original_messages)} messages, got {len(session.history)}"
        )
        for i, (expected, actual) in enumerate(
            zip(original_messages, session.history, strict=True)
        ):
            assert actual["role"] == expected["role"], f"message {i}: role mismatch"
            assert actual["content"] == expected["content"], f"message {i}: content mismatch"

    def test_load_history_missing_file_returns_system_prompt(self, tmp_path: Path) -> None:
        """Missing history file results in a session with only the system prompt."""
        history_file = tmp_path / "does_not_exist.jsonl"
        session = ChatSession(history_file=str(history_file))

        assert len(session.history) == 1, (
            f"missing history file: expected 1 message (system), got {len(session.history)}"
        )
        assert session.history[0]["role"] == "system"

    def test_load_history_corrupt_file_fallback_to_system(self, tmp_path: Path) -> None:
        """Corrupt JSONL lines are skipped per-line; valid messages are loaded."""
        history_file = tmp_path / "corrupt.jsonl"
        with open(history_file, "w") as f:
            f.write(json.dumps({"role": "user", "content": "good"}) + "\n")
            f.write("this is not valid json\n")
            f.write(json.dumps({"role": "assistant", "content": "also good"}) + "\n")

        session = ChatSession(history_file=str(history_file))

        assert len(session.history) == 3, (
            f"corrupt line skipped per-line, system prompt added; "
            f"expected 3 (system + user + assistant), got {len(session.history)}"
        )
        assert session.history[0]["role"] == "system"
        assert session.history[1]["role"] == "user"
        assert session.history[2]["role"] == "assistant"


class TestChatSessionLoadFromFile:
    """Loading history from a specific file at construction time."""

    def test_create_from_specific_history_file(self, tmp_path: Path) -> None:
        """ChatSession(history_file=...) loads history from a specific file."""
        history_file = tmp_path / "resume_test.jsonl"
        with open(history_file, "w") as f:
            f.write(
                json.dumps({"role": "system", "content": "Old system prompt"}) + "\n"
            )
            f.write(json.dumps({"role": "user", "content": "previous question"}) + "\n")
            f.write(
                json.dumps({"role": "assistant", "content": "previous answer"}) + "\n"
            )

        session = ChatSession(
            history_file=str(history_file),
            system_prompt="You are a new assistant.",
        )

        assert session is not None
        assert isinstance(session, ChatSession)
        assert len(session.history) == 3, (
            f"session must load history messages; got {len(session.history)}"
        )
        assert session.history[1]["role"] == "user"
        assert session.history[1]["content"] == "previous question"
        assert session.history[2]["role"] == "assistant"
        assert session.history[2]["content"] == "previous answer"

    def test_create_with_missing_history_file(self) -> None:
        """ChatSession(history_file=...) with missing file creates a valid session."""
        session = ChatSession(history_file="/tmp/nonexistent_chat_file.jsonl")

        assert session is not None, (
            "ChatSession must be created even with missing history file"
        )
        assert len(session.history) == 1, (
            f"missing file: expected 1 (system prompt), got {len(session.history)}"
        )
        assert session.history[0]["role"] == "system"

    def test_clear_history_removes_file_and_resets(self, tmp_path: Path) -> None:
        """Manually remove history file and reset in-memory history."""
        history_file = tmp_path / "clear_test.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        session.save_history()
        assert os.path.isfile(history_file)

        os.remove(str(history_file))
        session.history = [{"role": "system", "content": session._system_prompt}]

        assert not os.path.isfile(history_file), (
            "history file must be removed"
        )
        assert len(session.history) == 1, (
            f"history must be reset to system prompt only; got {len(session.history)}"
        )
        assert session.history[0]["role"] == "system"


class TestChatHistoryIndex:
    """ChatHistory read-only index wrapper (general_ludd.chat.history)."""

    def _write_index(self, history_dir: Path, sessions: list[dict]) -> None:
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / "index.json").write_text(
            json.dumps({"sessions": sessions}), encoding="utf-8"
        )

    def test_missing_index_yields_empty_listing(self, tmp_path: Path) -> None:
        history = ChatHistory(history_dir=tmp_path / "nonexistent")

        assert history.list_sessions() == []
        assert history.stats()["total_sessions"] == 0

    def test_list_sessions_sorted_and_filtered(self, tmp_path: Path) -> None:
        self._write_index(tmp_path, [
            {"file": "a.jsonl", "timestamp": "2026-01-01", "model": "sonnet",
             "message_count": 2},
            {"file": "b.jsonl", "timestamp": "2026-02-01", "model": "opus",
             "message_count": 4},
        ])
        history = ChatHistory(history_dir=tmp_path)

        sessions = history.list_sessions()
        assert sessions[0]["file"] == "b.jsonl", "newest first"

        filtered = history.list_sessions(model_filter="opus")
        assert len(filtered) == 1
        assert filtered[0]["model"] == "opus"

    def test_get_session_and_messages(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "s1.jsonl"
        msg_file.write_text(
            json.dumps({"role": "user", "content": "hello there"}) + "\n",
            encoding="utf-8",
        )
        self._write_index(tmp_path, [
            {"file": str(msg_file), "timestamp": "2026-01-01",
             "model": "sonnet", "message_count": 1, "preview": "hello"},
        ])
        history = ChatHistory(history_dir=tmp_path)

        found = history.get_session(str(msg_file))
        assert found is not None
        assert found["model"] == "sonnet"

        messages = history.get_messages(str(msg_file))
        assert len(messages) == 1
        assert messages[0]["content"] == "hello there"

        assert history.get_session("missing.jsonl") is None
        assert history.get_messages("/tmp/does_not_exist.jsonl") == []

    def test_search_matches_preview_and_content(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "s1.jsonl"
        msg_file.write_text(
            json.dumps({"role": "user", "content": "deep needle text"}) + "\n",
            encoding="utf-8",
        )
        self._write_index(tmp_path, [
            {"file": str(msg_file), "timestamp": "2026-01-01",
             "model": "sonnet", "message_count": 1, "preview": "surface hit"},
        ])
        history = ChatHistory(history_dir=tmp_path)

        by_preview = history.search("surface")
        assert len(by_preview) == 1
        assert by_preview[0]["match_source"] == "preview"

        by_content = history.search("needle")
        assert len(by_content) == 1
        assert by_content[0]["match_source"] == "content"

        assert history.search("nomatchanywhere") == []

    def test_delete_session_removes_file_and_index_entry(
        self, tmp_path: Path
    ) -> None:
        msg_file = tmp_path / "s1.jsonl"
        msg_file.write_text("{}\n", encoding="utf-8")
        self._write_index(tmp_path, [
            {"file": str(msg_file), "timestamp": "2026-01-01",
             "model": "sonnet", "message_count": 0},
        ])
        history = ChatHistory(history_dir=tmp_path)

        assert history.delete_session(str(msg_file)) is True
        assert not msg_file.exists()
        assert history.list_sessions() == []

    def test_stats_aggregates_counts_and_models(self, tmp_path: Path) -> None:
        self._write_index(tmp_path, [
            {"file": "a.jsonl", "timestamp": "1", "model": "sonnet",
             "message_count": 3},
            {"file": "b.jsonl", "timestamp": "2", "model": "opus",
             "message_count": 5},
        ])
        history = ChatHistory(history_dir=tmp_path)

        stats = history.stats()

        assert stats["total_sessions"] == 2
        assert stats["total_messages"] == 8
        assert stats["unique_models"] == ["opus", "sonnet"]
