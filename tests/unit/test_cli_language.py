"""Tests for cli_language.py — ``gludd language`` subcommand handlers."""

from __future__ import annotations

import argparse
import json

import pytest

from general_ludd.cli_language import (
    _cmd_language_phonetic_transcribe,
    _cmd_language_scan_homoglyphs,
    add_language_subparser,
)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_language_subparser(subparsers)
    return parser.parse_args(argv)


class TestAddLanguageSubparser:
    def test_registers_language_command(self) -> None:
        args = _parse(["language", "scan-homoglyphs", "hello"])

        assert args.command == "language"
        assert args.language_command == "scan-homoglyphs"
        assert args.text == "hello"

    def test_detect_encoding_requires_file_argument(self) -> None:
        args = _parse(["language", "detect-encoding", "/tmp/somefile.txt"])

        assert args.file == "/tmp/somefile.txt"

    def test_phonetic_method_choices_enforced(self) -> None:
        with pytest.raises(SystemExit):
            _parse(
                ["language", "phonetic-transcribe", "hi", "--method", "bogus"]
            )


class TestScanHomoglyphs:
    def test_clean_ascii_text_is_safe(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = _parse(["language", "scan-homoglyphs", "hello world"])

        _cmd_language_scan_homoglyphs(args)

        result = json.loads(capsys.readouterr().out)
        assert result["safe"] is True
        assert result["total_findings"] == 0

    def test_cyrillic_confusable_is_flagged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = _parse(["language", "scan-homoglyphs", "p\u0430ypal"])

        _cmd_language_scan_homoglyphs(args)

        result = json.loads(capsys.readouterr().out)
        assert result["safe"] is False
        assert result["total_findings"] >= 1

    def test_empty_text_reports_safe(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(text="", min_severity="low")

        _cmd_language_scan_homoglyphs(args)

        result = json.loads(capsys.readouterr().out)
        assert result["safe"] is True
        assert result["input_length"] == 0


class TestPhoneticTranscribe:
    def test_soundex_transcription_shape(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = _parse(
            ["language", "phonetic-transcribe", "Robert", "--method", "soundex"]
        )

        _cmd_language_phonetic_transcribe(args)

        result = json.loads(capsys.readouterr().out)
        assert result["method"] == "soundex"
        assert result["word_count"] == 1
        code = result["words"][0]["soundex_code"]
        assert len(code) == 4
        assert code[0] == "R"

    def test_empty_text_returns_no_words(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(text="", method="arpabet")

        _cmd_language_phonetic_transcribe(args)

        result = json.loads(capsys.readouterr().out)
        assert result["words"] == []
