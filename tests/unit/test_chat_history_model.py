"""Tests for ChatHistory model — session index wrapper with search, stats, delete."""

from __future__ import annotations

import json
from pathlib import Path

from general_ludd.chat.history import ChatHistory
from general_ludd.chat.session import SESSION_INDEX_FILE


def _make_index(hist_dir: Path, sessions: list[dict]) -> None:
    hist_dir.mkdir(parents=True, exist_ok=True)
    (hist_dir / SESSION_INDEX_FILE).write_text(
        json.dumps({"sessions": sessions}, indent=2), encoding="utf-8"
    )


def _make_session_file(hist_dir: Path, filename: str, messages: list[dict]) -> Path:
    hist_dir.mkdir(parents=True, exist_ok=True)
    path = hist_dir / filename
    path.write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8"
    )
    return path


class TestListSessions:
    def test_empty(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path)
        assert ch.list_sessions() == []

    def test_single(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {
                "file": "s1.jsonl",
                "timestamp": "2026-01-01T12:00:00Z",
                "model": "openai/gpt-4o",
                "message_count": 3,
                "preview": "hello",
            },
        ])
        ch = ChatHistory(tmp_path)
        result = ch.list_sessions()
        assert len(result) == 1
        assert result[0]["model"] == "openai/gpt-4o"

    def test_multiple_sorted_by_timestamp_desc(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "old.jsonl", "timestamp": "2025-01-01T00:00:00Z"},
            {"file": "new.jsonl", "timestamp": "2026-06-15T00:00:00Z"},
            {"file": "mid.jsonl", "timestamp": "2026-01-01T00:00:00Z"},
        ])
        ch = ChatHistory(tmp_path)
        result = ch.list_sessions()
        assert result[0]["file"] == "new.jsonl"
        assert result[1]["file"] == "mid.jsonl"
        assert result[2]["file"] == "old.jsonl"

    def test_limit(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": f"s{i}.jsonl", "timestamp": f"2026-01-0{i}T00:00:00Z"}
            for i in range(1, 6)
        ])
        ch = ChatHistory(tmp_path)
        assert len(ch.list_sessions(limit=3)) == 3

    def test_model_filter(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "a.jsonl", "timestamp": "2026-01-01T00:00:00Z", "model": "openai/gpt-4o"},
            {"file": "b.jsonl", "timestamp": "2026-01-02T00:00:00Z", "model": "deepseek/deepseek-chat"},
            {"file": "c.jsonl", "timestamp": "2026-01-03T00:00:00Z", "model": "openai/gpt-3.5-turbo"},
        ])
        ch = ChatHistory(tmp_path)
        result = ch.list_sessions(model_filter="deepseek")
        assert len(result) == 1
        assert result[0]["file"] == "b.jsonl"


class TestGetSession:
    def test_found(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "s1.jsonl", "timestamp": "2026-01-01T12:00:00Z", "preview": "hi"},
        ])
        ch = ChatHistory(tmp_path)
        s = ch.get_session("s1.jsonl")
        assert s is not None
        assert s["preview"] == "hi"

    def test_not_found(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [])
        ch = ChatHistory(tmp_path)
        assert ch.get_session("missing.jsonl") is None


class TestGetMessages:
    def test_valid_file(self, tmp_path: Path) -> None:
        path = _make_session_file(tmp_path, "s1.jsonl", [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ])
        ch = ChatHistory(tmp_path)
        msgs = ch.get_messages(str(path))
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "hello"

    def test_missing_file(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path)
        assert ch.get_messages("/tmp/does_not_exist.jsonl") == []

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        ch = ChatHistory(tmp_path)
        assert ch.get_messages(str(path)) == []

    def test_corrupt_line_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.jsonl"
        path.write_text(
            json.dumps({"role": "user", "content": "good"}) + "\n"
            + "bad json\n"
            + json.dumps({"role": "assistant", "content": "good"}) + "\n",
            encoding="utf-8",
        )
        ch = ChatHistory(tmp_path)
        msgs = ch.get_messages(str(path))
        assert len(msgs) == 2


class TestSearch:
    def test_preview_match(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "s1.jsonl", "timestamp": "2026-01-01T00:00:00Z", "preview": "hello world", "model": "gpt-4o"},
            {"file": "s2.jsonl", "timestamp": "2026-01-02T00:00:00Z", "preview": "goodbye", "model": "gpt-4o"},
        ])
        ch = ChatHistory(tmp_path)
        results = ch.search("hello")
        assert len(results) == 1
        assert results[0]["match_source"] == "preview"

    def test_model_match(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {
                "file": "s1.jsonl",
                "timestamp": "2026-01-01T00:00:00Z",
                "preview": "x",
                "model": "deepseek/deepseek-chat",
            },
        ])
        ch = ChatHistory(tmp_path)
        results = ch.search("deepseek")
        assert len(results) == 1
        assert results[0]["model"] == "deepseek/deepseek-chat"

    def test_content_match(self, tmp_path: Path) -> None:
        path = _make_session_file(tmp_path, "s1.jsonl", [
            {"role": "user", "content": "how do I configure ansible"},
            {"role": "assistant", "content": "here is how..."},
        ])
        _make_index(tmp_path, [
            {"file": str(path), "timestamp": "2026-01-01T00:00:00Z", "preview": "no match", "model": "gpt-4o"},
        ])
        ch = ChatHistory(tmp_path)
        results = ch.search("ansible")
        assert len(results) == 1
        assert results[0]["match_source"] == "content"

    def test_no_match(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "s1.jsonl", "timestamp": "2026-01-01T00:00:00Z", "preview": "hello", "model": "gpt-4o"},
        ])
        ch = ChatHistory(tmp_path)
        assert ch.search("zzz_nonexistent_zzz") == []

    def test_limit(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": f"s{i}.jsonl", "timestamp": f"2026-01-0{i}T00:00:00Z", "preview": f"match{i}"}
            for i in range(1, 6)
        ])
        ch = ChatHistory(tmp_path)
        assert len(ch.search("match", limit=2)) == 2


class TestDeleteSession:
    def test_delete_existing(self, tmp_path: Path) -> None:
        path = _make_session_file(tmp_path, "s1.jsonl", [
            {"role": "user", "content": "test"},
        ])
        _make_index(tmp_path, [
            {"file": str(path), "timestamp": "2026-01-01T00:00:00Z"},
        ])
        ch = ChatHistory(tmp_path)
        assert ch.delete_session(str(path)) is True
        assert not path.exists()
        assert ch.list_sessions() == []

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [])
        ch = ChatHistory(tmp_path)
        assert ch.delete_session("/tmp/fake.jsonl") is False

    def test_delete_updates_index(self, tmp_path: Path) -> None:
        path1 = _make_session_file(tmp_path, "s1.jsonl", [{"role": "user", "content": "a"}])
        path2 = _make_session_file(tmp_path, "s2.jsonl", [{"role": "user", "content": "b"}])
        _make_index(tmp_path, [
            {"file": str(path1), "timestamp": "2026-01-01T00:00:00Z"},
            {"file": str(path2), "timestamp": "2026-01-02T00:00:00Z"},
        ])
        ch = ChatHistory(tmp_path)
        ch.delete_session(str(path1))
        sessions = ch.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["file"] == str(path2)


class TestStats:
    def test_empty(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path)
        stats = ch.stats()
        assert stats["total_sessions"] == 0
        assert stats["total_messages"] == 0
        assert stats["unique_models"] == []

    def test_populated(self, tmp_path: Path) -> None:
        _make_index(tmp_path, [
            {"file": "a.jsonl", "model": "openai/gpt-4o", "message_count": 5},
            {"file": "b.jsonl", "model": "deepseek/deepseek-chat", "message_count": 10},
            {"file": "c.jsonl", "model": "openai/gpt-4o", "message_count": 3},
        ])
        ch = ChatHistory(tmp_path)
        stats = ch.stats()
        assert stats["total_sessions"] == 3
        assert stats["total_messages"] == 18
        assert len(stats["unique_models"]) == 2


class TestMissingDirectory:
    def test_list_sessions_missing_dir(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path / "does_not_exist")
        assert ch.list_sessions() == []

    def test_get_session_missing_dir(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path / "does_not_exist")
        assert ch.get_session("any.jsonl") is None

    def test_stats_missing_dir(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path / "does_not_exist")
        stats = ch.stats()
        assert stats["total_sessions"] == 0

    def test_search_missing_dir(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path / "does_not_exist")
        assert ch.search("anything") == []

    def test_delete_missing_dir(self, tmp_path: Path) -> None:
        ch = ChatHistory(tmp_path / "does_not_exist")
        assert ch.delete_session("x.jsonl") is False
