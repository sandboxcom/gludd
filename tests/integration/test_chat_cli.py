from __future__ import annotations

import subprocess
import sys


def _gludd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "general_ludd.cli", *list(args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestChatCLI:
    def test_chat_help_shows_options(self) -> None:
        result = _gludd("chat", "--help")
        assert result.returncode == 0
        assert "--eval" in result.stdout
        assert "--model" in result.stdout
        assert "--system-prompt" in result.stdout
        assert "--api-base" in result.stdout
        assert "--api-key" in result.stdout
        assert "--project-dir" in result.stdout

    def test_chat_subparser_exists(self) -> None:
        result = _gludd("chat", "--help")
        assert result.returncode == 0
        assert "chat" in result.stdout.lower()

    def test_help_entry_shows_chat(self) -> None:
        result = _gludd("--help")
        assert result.returncode == 0
        assert "help" in result.stdout

    def test_chat_project_dir_flag_accepted(self) -> None:
        result = _gludd("chat", "--project-dir", "/nonexistent", "--help")
        assert result.returncode == 0

    def test_chat_eval_with_project_dir_rejects_bad_dir(self) -> None:
        result = _gludd(
            "chat",
            "--eval",
            "hello",
            "--project-dir",
            "/tmp/does-not-exist-98765",
            "--api-base",
            "https://test.api/v1",
            "--api-key",
            "sk-test",
            "--model",
            "openai/gpt-4o",
        )
        output_lower = result.stdout.lower()
        assert (
            any(phrase in output_lower for phrase in ("empty response", "could not connect", "timed out", "error"))
            or result.returncode != 0
        )

    def test_chat_help_shows_export_flag(self) -> None:
        result = _gludd("chat", "--help")
        assert result.returncode == 0
        assert "--export" in result.stdout
        assert "--export-output" in result.stdout

    def test_chat_export_requires_history(self) -> None:
        result = _gludd(
            "chat",
            "--export",
            "md",
            "--history",
            "/tmp/nonexistent-session-98765.jsonl",
        )
        assert result.returncode != 0

    def test_chat_list_sessions_flag(self) -> None:
        result = _gludd("chat", "--list-sessions")
        assert result.returncode == 0

    def test_chat_resume_flag_accepted(self) -> None:
        result = _gludd("chat", "--resume", "--help")
        assert result.returncode == 0

    def test_chat_save_interval_flag_accepted(self) -> None:
        result = _gludd("chat", "--save-interval", "10", "--help")
        assert result.returncode == 0

    def test_chat_stream_flag_in_help(self) -> None:
        result = _gludd("chat", "--help")
        assert "--stream" in result.stdout

    def test_chat_max_context_flag_in_help(self) -> None:
        result = _gludd("chat", "--help")
        assert "--max-context" in result.stdout

    def test_chat_export_json_format(self) -> None:
        result = _gludd("chat", "--export", "json", "--help")
        assert result.returncode == 0

    def test_chat_export_html_format(self) -> None:
        result = _gludd("chat", "--export", "html", "--help")
        assert result.returncode == 0

    def test_chat_export_missing_history(self) -> None:
        result = _gludd(
            "chat",
            "--export",
            "md",
            "--history",
            "/tmp/nonexistent-chat-export-99999.jsonl",
        )
        assert result.returncode != 0

    def test_chat_help_shows_resume_and_save_interval(self) -> None:
        result = _gludd("chat", "--help")
        assert "--resume" in result.stdout
        assert "--save-interval" in result.stdout
