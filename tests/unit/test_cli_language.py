"""Tests for cli_language.py — ``gludd language`` subcommand handlers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from general_ludd.cli_language import (
    _cmd_language_detect_bom,
    _cmd_language_detect_encoding,
    _cmd_language_phonetic_transcribe,
    _cmd_language_scan_homoglyphs,
    add_language_subparser,
)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_language_subparser(subparsers)
    return parser.parse_args(argv)


def _run(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> dict[str, object]:
    args = _parse(argv)
    args.func(args)
    return json.loads(capsys.readouterr().out)


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


class TestDetectEncoding:
    @pytest.mark.parametrize(
        ("confidence", "expected_level"),
        [
            (0.99, "trusted"),
            (0.85, "reliable"),
            (0.60, "usable"),
            (0.10, "entry"),
        ],
    )
    def test_reports_detector_confidence_levels(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        confidence: float,
        expected_level: str,
    ) -> None:
        import chardet

        source = tmp_path / "source.txt"
        source.write_text("plain utf-8 text", encoding="utf-8")
        monkeypatch.setattr(
            chardet,
            "detect",
            lambda _data: {"encoding": "utf-8", "confidence": confidence},
        )

        result = _run(
            ["language", "detect-encoding", str(source)],
            capsys,
        )

        assert result["detected_encoding"] == "utf-8"
        assert result["confidence"] == confidence
        assert result["confidence_level"] == expected_level
        assert result["converted_preview"] == "plain utf-8 text"

    def test_detects_mojibake_and_lists_supported_encodings(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source = tmp_path / "mojibake.txt"
        source.write_text("cafÃ©", encoding="utf-8")

        result = _run(
            [
                "language",
                "detect-encoding",
                str(source),
                "--detect-mojibake",
                "--list-encodings",
            ],
            capsys,
        )

        assert result["file_size"] == len(source.read_bytes())
        assert result["mojibake_detected"] is True
        assert result["mojibake_pattern"] == "UTF-8 viewed as ISO-8859-1"
        assert "UTF-8" in result["supported_encodings"]

    @pytest.mark.parametrize("file_value", ["", "/missing/input.txt"])
    def test_rejects_missing_input(
        self,
        capsys: pytest.CaptureFixture[str],
        file_value: str,
    ) -> None:
        args = argparse.Namespace(file=file_value)

        with pytest.raises(SystemExit, match="1"):
            _cmd_language_detect_encoding(args)

        assert "Error:" in capsys.readouterr().err


class TestDetectBom:
    def test_strips_utf8_bom_preview_and_audits_directory(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source = tmp_path / "with-bom.txt"
        source.write_bytes(b"\xef\xbb\xbfhello")
        plain = tmp_path / "plain.txt"
        plain.write_bytes(b"hello")

        result = _run(
            [
                "language",
                "detect-bom",
                str(source),
                "--strip",
                "--audit-directory",
                str(tmp_path),
            ],
            capsys,
        )

        assert result["bom_found"] is True
        assert result["bom_type"] == "UTF-8"
        assert result["bom_size_bytes"] == 3
        assert result["stripped_text_preview"] == "hello"
        assert result["audit_file_count"] == 2
        assert {entry["bom"] for entry in result["audit_results"]} == {
            "UTF-8",
            None,
        }

    def test_reports_file_without_bom(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source = tmp_path / "plain.txt"
        source.write_text("hello", encoding="utf-8")

        result = _run(
            ["language", "detect-bom", str(source)],
            capsys,
        )

        assert result["bom_found"] is False
        assert result["bom_type"] is None

    def test_rejects_missing_file(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(file="/missing/input.txt")

        with pytest.raises(SystemExit, match="1"):
            _cmd_language_detect_bom(args)

        assert "file not found" in capsys.readouterr().err


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

    @pytest.mark.parametrize(
        "method",
        ["arpabet", "ipa", "metaphone", "double_metaphone"],
    )
    def test_dispatches_each_phonetic_method(
        self,
        capsys: pytest.CaptureFixture[str],
        method: str,
    ) -> None:
        result = _run(
            [
                "language",
                "phonetic-transcribe",
                "hello knight",
                "--method",
                method,
            ],
            capsys,
        )

        assert result["method"] == method
        assert result["word_count"] == 2
        assert all(entry["transcription"] for entry in result["words"])
        if method == "double_metaphone":
            assert all("metaphone_codes" in entry for entry in result["words"])

    def test_metaphone_handles_rule_families(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = _run(
            [
                "language",
                "phonetic-transcribe",
                "ciao check dock edge phone queen size theta vax",
                "--method",
                "double_metaphone",
            ],
            capsys,
        )

        assert result["word_count"] == 9
        assert all(entry["transcription"] for entry in result["words"])

    def test_rejects_unknown_method(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        args = argparse.Namespace(text="hello", method="unknown")

        with pytest.raises(SystemExit, match="1"):
            _cmd_language_phonetic_transcribe(args)

        assert "unknown method" in capsys.readouterr().err


class TestAdditionalLanguageHandlerEdges:
    def test_unicode_plain_character_fallback(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = _run(
            ["language", "unicode-analyze", "A"],
            capsys,
        )

        assert result["codepoint"] == 65
        assert result["character"] == "A"

    def test_locale_info_reports_unknown_locale(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = _run(
            ["language", "locale-format", "xx-XX", "--info"],
            capsys,
        )

        assert result["error"] == "locale not found"

    def test_i18n_extract_reports_missing_action(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = _run(
            ["language", "i18n-extract"],
            capsys,
        )

        assert result == {"error": "no action specified"}

    def test_i18n_extract_reports_missing_po_file(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        result = _run(
            ["language", "i18n-extract", "--parse-po", "/missing/messages.po"],
            capsys,
        )

        assert result["file"] == "/missing/messages.po"
        assert "error" in result

    def test_font_tables_and_metrics_are_reported(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        font_file = tmp_path / "empty.ttf"
        font_file.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 60)

        result = _run(
            [
                "language",
                "font-analyze",
                str(font_file),
                "--tables",
                "--metrics",
            ],
            capsys,
        )

        assert result["format"] == "ttf"
        assert result["tables"] == []
        assert "metrics" in result or "metrics_error" in result
