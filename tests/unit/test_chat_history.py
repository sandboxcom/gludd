"""TDD tests for Chat CLI P5 — history file + persistent context + session resume.

All tests MUST FAIL — the implementation does not exist yet.

Per FEATURE_CHAT_CLI.md §4 P5:
  "History file (~/.cache/gludd/chat_history), persistent context, session resume."

Tests:
  1. ChatSession.save_history() writes to the default history file path
  2. ChatSession.save_history() writes JSON-lines format (one message per line)
  3. ChatSession.load_history() restores conversation from a history file
  4. ChatSession.resume() re-creates session from history + system prompt
  5. ChatSession.load_history() handles missing file gracefully (empty history)
  6. ChatSession.clear_history() resets in-memory and persisted history
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from general_ludd.chat import ChatSession


class TestChatHistoryPath:
    """P5: history file location must use ~/.cache/gludd/chat_history."""

    def test_default_history_path_resolves_to_cache_dir(self) -> None:
        """Default history path is ~/.cache/gludd/chat_history."""
        session = ChatSession()

        assert session.history_path is not None, (
            "P5 gap: ChatSession.__init__ must set a default history_path "
            "pointing to ~/.cache/gludd/chat_history"
        )

        # On macOS/Linux: ~/.cache/gludd/chat_history
        assert "gludd" in str(session.history_path), (
            f"P5 gap: history_path={session.history_path} must be under "
            "~/.cache/gludd/ for the gludd project namespace"
        )
        assert str(session.history_path).endswith("chat_history") or str(
            session.history_path
        ).endswith("chat_history.jsonl"), (
            "P5 gap: history file should be named 'chat_history' or 'chat_history.jsonl'"
        )

    def test_custom_history_path_from_cli_flag(self) -> None:
        """--history FILE overrides default to user-supplied path."""
        session = ChatSession(history_file="/tmp/test_history.jsonl")

        assert session.history_path == Path("/tmp/test_history.jsonl"), (
            "P5 gap: ChatSession must accept --history flag and use it "
            "as history_path, overriding the default cache location"
        )


class TestChatHistorySave:
    """P5: ChatSession.save_history() writes chat history to disk."""

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
            "P5 gap: save_history() must create the history file on disk"
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
            f"P5 gap: expected 3 JSON lines, got {len(lines)} — "
            "history must be persisted as JSON-lines"
        )
        for line in lines:
            assert line.strip(), "P5 gap: no empty lines allowed in JSONL history"
            parsed = json.loads(line)
            assert "role" in parsed
            assert "content" in parsed

    def test_save_history_creates_parent_dirs(self, tmp_path: Path) -> None:
        """save_history() creates any missing parent directories."""
        history_file = tmp_path / "deeply" / "nested" / "chat.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [{"role": "user", "content": "test"}]

        session.save_history()

        assert os.path.isfile(history_file), (
            "P5 gap: save_history() must create parent dirs so the user "
            "doesn't need to mkdir -p first"
        )

    def test_save_history_empty_noops(self, tmp_path: Path) -> None:
        """save_history() with only system message does not write (no meaningful content)."""
        history_file = tmp_path / "empty_history.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [{"role": "system", "content": "sys"}]

        session.save_history()

        assert not os.path.isfile(history_file), (
            "P5 gap: don't write a history file with only the system prompt — "
            "there's nothing to resume"
        )


class TestChatHistoryLoad:
    """P5: ChatSession.load_history() restores conversation from file."""

    def test_load_history_restores_messages(self, tmp_path: Path) -> None:
        """load_history() restores full conversation from JSONL file."""
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
        session.load_history()

        assert session.history, "P5 gap: load_history() must restore messages"
        assert len(session.history) == len(original_messages), (
            f"P5 gap: expected {len(original_messages)} messages, "
            f"got {len(session.history)}"
        )
        for i, (expected, actual) in enumerate(
            zip(original_messages, session.history, strict=True)
        ):
            assert actual["role"] == expected["role"], (
                f"message {i}: role mismatch"
            )
            assert actual["content"] == expected["content"], (
                f"message {i}: content mismatch"
            )

    def test_load_history_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """load_history() on missing file returns empty history gracefully."""
        history_file = tmp_path / "does_not_exist.jsonl"
        session = ChatSession(history_file=str(history_file))

        session.load_history()

        assert len(session.history) == 1, (
            f"P5 gap: missing history file should leave only the system message; "
            f"got {len(session.history)} messages"
        )
        assert session.history[0]["role"] == "system"

    def test_load_history_handles_corrupt_lines(self, tmp_path: Path) -> None:
        """load_history() skips malformed JSON lines without crashing."""
        history_file = tmp_path / "corrupt.jsonl"
        with open(history_file, "w") as f:
            f.write(json.dumps({"role": "user", "content": "good"}) + "\n")
            f.write("this is not valid json\n")
            f.write(json.dumps({"role": "assistant", "content": "also good"}) + "\n")

        session = ChatSession(history_file=str(history_file))
        session.load_history()

        assert len(session.history) == 3, (
            f"P5 gap: load_history() must skip corrupt lines; "
            f"expected 3 messages (1 system + 2 valid), got {len(session.history)}"
        )


class TestChatSessionResume:
    """P5: ChatSession.resume() re-creates session from history + current system prompt."""

    def test_resume_creates_session_from_history_file(self, tmp_path: Path) -> None:
        """resume() factory method re-creates a ChatSession from a history file."""
        history_file = tmp_path / "resume_test.jsonl"
        with open(history_file, "w") as f:
            f.write(
                json.dumps({"role": "system", "content": "Old system prompt"}) + "\n"
            )
            f.write(json.dumps({"role": "user", "content": "previous question"}) + "\n")
            f.write(
                json.dumps({"role": "assistant", "content": "previous answer"}) + "\n"
            )

        session = ChatSession.resume(
            history_file=str(history_file),
            system_prompt="You are a new assistant.",
        )

        assert session is not None, "P5 gap: ChatSession.resume() must return a ChatSession"
        assert isinstance(session, ChatSession)
        assert len(session.history) == 3, (
            f"P5 gap: resumed session must have history messages; "
            f"got {len(session.history)}"
        )
        # System prompt must be from the NEW session, not overridden by history
        assert (
            session.history[0]["content"] == "You are a new assistant."
        ), (
            "P5 gap: resume() must use the new system_prompt argument, "
            "not the old one from the history file"
        )

    def test_resume_with_missing_file_returns_none(self) -> None:
        """resume() returns None when history file doesn't exist."""
        session = ChatSession.resume(history_file="/tmp/nonexistent_chat_file.jsonl")

        assert session is None, (
            "P5 gap: resume() must return None for missing history file "
            "so the CLI can fall back to a fresh session"
        )

    def test_clear_history_removes_file_and_resets(self, tmp_path: Path) -> None:
        """clear_history() removes the file and resets in-memory history."""
        history_file = tmp_path / "clear_test.jsonl"
        session = ChatSession(history_file=str(history_file))
        session.history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ]
        session.save_history()
        assert os.path.isfile(history_file)

        session.clear_history()

        assert not os.path.isfile(history_file), (
            "P5 gap: clear_history() must delete the persisted history file"
        )
        assert len(session.history) == 1, (
            f"P5 gap: clear_history() must reset to system prompt only; "
            f"got {len(session.history)} messages"
        )
        assert session.history[0]["role"] == "system"
