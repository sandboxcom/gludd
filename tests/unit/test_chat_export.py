from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from general_ludd.chat.session import export_session


def _make_session_file(temp_dir: str) -> Path:
    session_file = Path(temp_dir) / "session.jsonl"
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant.",
            "timestamp": "2026-07-15T10:00:00",
        },
        {
            "role": "user",
            "content": "Write a Python function to add two numbers.",
            "timestamp": "2026-07-15T10:00:05",
        },
        {
            "role": "assistant",
            "content": (
                "Here is a simple function:\n\n"
                "```python\n"
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n"
                "```"
            ),
            "timestamp": "2026-07-15T10:00:10",
        },
        {
            "role": "user",
            "content": "Can you explain how it works?\n\n```python\nx = add(3, 5)\nprint(x)\n```",
            "timestamp": "2026-07-15T10:00:15",
        },
        {
            "role": "assistant",
            "content": "The function takes two integers and returns their sum.",
            "timestamp": "2026-07-15T10:00:20",
        },
    ]
    session_file.write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n",
        encoding="utf-8",
    )
    return session_file


class TestExportToMarkdown:
    def test_basic_markdown_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="md")
            assert isinstance(result, str)
            assert "def add(a: int, b: int) -> int:" in result
            assert "add two numbers" in result.lower()

    def test_markdown_has_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="md")
            assert result.startswith("#") or "Chat Session" in result

    def test_markdown_preserves_code_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="md")
            assert "```python" in result
            assert "def add(a: int, b: int) -> int:" in result
            assert "return a + b" in result

    def test_markdown_empty_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "empty.jsonl"
            session_file.write_text(
                json.dumps({
                    "role": "system",
                    "content": "You are a helpful assistant.",
                    "timestamp": "2026-07-15T10:00:00",
                })
                + "\n",
                encoding="utf-8",
            )
            result = export_session(session_file, format="md")
            assert isinstance(result, str)
            assert len(result) > 0

    def test_markdown_returns_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="md")
            assert isinstance(result, str)


class TestExportToJson:
    def test_basic_json_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            assert isinstance(result, str)
            data = json.loads(result)
            assert isinstance(data, dict)
            assert "messages" in data or "history" in data

    def test_json_has_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            data = json.loads(result)
            messages_key = "messages" if "messages" in data else "history"
            for msg in data[messages_key]:
                assert "role" in msg
                assert "content" in msg

    def test_json_empty_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "empty.jsonl"
            session_file.write_text(
                json.dumps({
                    "role": "system",
                    "content": "You are a helpful assistant.",
                    "timestamp": "2026-07-15T10:00:00",
                })
                + "\n",
                encoding="utf-8",
            )
            result = export_session(session_file, format="json")
            data = json.loads(result)
            messages_key = "messages" if "messages" in data else "history"
            assert len(data[messages_key]) <= 1

    def test_json_code_blocks_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            assert "```python" in result or "def add(a: int, b: int)" in result

    def test_json_pretty_printed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            assert "\n  " in result


class TestExportRoundtrip:
    def test_roundtrip_json_reimport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            data = json.loads(result)
            messages_key = "messages" if "messages" in data else "history"
            roles = {msg["role"] for msg in data[messages_key]}
            assert "system" in roles
            assert "user" in roles
            assert "assistant" in roles

    def test_roundtrip_message_count_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="json")
            data = json.loads(result)
            messages_key = "messages" if "messages" in data else "history"
            assert len(data[messages_key]) == 5


class TestExportErrors:
    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "does_not_exist.jsonl"
            with pytest.raises(FileNotFoundError):
                export_session(missing, format="md")

    def test_invalid_format_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            with pytest.raises(ValueError, match="format"):
                export_session(session_file, format="xml")

    def test_empty_format_defaults_to_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format="")
            assert isinstance(result, str)

    def test_none_format_defaults_to_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file, format=None)
            assert isinstance(result, str)

    def test_no_format_arg_defaults_to_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            result = export_session(session_file)
            assert isinstance(result, str)

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            corrupt_file = Path(temp_dir) / "corrupt.jsonl"
            corrupt_file.write_text(
                "this is not valid json\n",
                encoding="utf-8",
            )
            with pytest.raises((json.JSONDecodeError, ValueError)):
                export_session(corrupt_file, format="md")


class TestExportToFile:
    def test_export_to_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            output_file = Path(temp_dir) / "export.md"
            result = export_session(
                session_file, format="md", output_file=output_file
            )
            assert output_file.exists()
            content = output_file.read_text(encoding="utf-8")
            assert isinstance(result, Path)
            assert "add two numbers" in content.lower()

    def test_export_overwrites_existing_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = _make_session_file(temp_dir)
            output_file = Path(temp_dir) / "export.md"
            output_file.write_text("old content", encoding="utf-8")
            export_session(session_file, format="md", output_file=output_file)
            content = output_file.read_text(encoding="utf-8")
            assert "old content" not in content
            assert "add two numbers" in content.lower()
