"""Phase E TDD tests: CLI integration + advanced analysis.

Covers the Phase E goal: expose ALL 8 language knowledge modules via the
``gludd language`` CLI and add locale formatting helpers + composite
text-health analysis.

New CLI subcommands tested here:
- unicode-analyze    (unicode_data — was NOT in CLI before Phase E)
- locale-format      (locale_data — was NOT in CLI before Phase E)
- i18n-extract       (i18n_data   — was NOT in CLI before Phase E)
- font-analyze       (font_data   — was NOT in CLI before Phase E)
- analyze-text       (composite — NEW advanced analysis combining modules)

New locale_data helpers:
- format_number(value, locale) -> str
- format_currency(amount, currency_code, locale) -> str

These tests fail until the corresponding functions/subcommands exist.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# ── locale_data formatting helpers ─────────────────────────────────────────


class TestFormatNumber:
    """format_number() formats a number per locale conventions."""

    def test_us_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number
        assert format_number(1234567.89, "en-US") == "1,234,567.89"

    def test_german_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number
        assert format_number(1234567.89, "de-DE") == "1.234.567,89"

    def test_japanese_no_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number
        result = format_number(1234567, "ja-JP")
        assert "1" in result and "234" in result

    def test_unknown_locale_fallback(self) -> None:
        from general_ludd.language.locale_data import format_number
        result = format_number(1000, "xx-XX")
        assert "1000" in result


class TestFormatCurrency:
    """format_currency() formats an amount with currency symbol per locale."""

    def test_usd_us(self) -> None:
        from general_ludd.language.locale_data import format_currency
        result = format_currency(99.50, "USD", "en-US")
        assert "$" in result
        assert "99" in result

    def test_eur_de(self) -> None:
        from general_ludd.language.locale_data import format_currency
        result = format_currency(50.00, "EUR", "de-DE")
        assert "50" in result

    def test_unknown_currency(self) -> None:
        from general_ludd.language.locale_data import format_currency
        result = format_currency(10, "XYZ", "en-US")
        assert "10" in result


# ── unicode-analyze CLI subcommand ─────────────────────────────────────────


def _run_language_cli(argv: list[str]) -> dict[str, object]:
    """Invoke the language CLI with the given argv, capture stdout JSON."""
    from general_ludd.cli_language import add_language_subparser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_language_subparser(sub)

    args = parser.parse_args(argv)
    old_stdout = sys.stdout
    captured = io.StringIO()
    sys.stdout = captured
    try:
        if hasattr(args, "func") and args.func:
            args.func(args)
        else:
            return {}
    finally:
        sys.stdout = old_stdout

    output = captured.getvalue().strip()
    if output:
        return json.loads(output)  # type: ignore[no-any-return]
    return {}


class TestUnicodeAnalyzeCLI:
    """``gludd language unicode-analyze`` exposes unicode_data."""

    def test_codepoint_hex_input(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "U+0041"])
        assert result["codepoint"] == 0x0041
        assert result["character"] == "A"
        assert result["plane"] == "BMP"
        assert "block" in result

    def test_codepoint_0x_prefix(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "0x41"])
        assert result["codepoint"] == 65
        assert result["character"] == "A"

    def test_codepoint_decimal(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "65"])
        assert result["codepoint"] == 65

    def test_string_input_mode(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "AB", "--string"])
        assert result["length"] == 2
        assert len(result["characters"]) == 2  # type: ignore[arg-type]
        assert result["characters"][0]["character"] == "A"  # type: ignore[index]

    def test_surrogate_range_codepoint(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "U+D800"])
        assert result["is_surrogate"] is True
        assert result["is_high_surrogate"] is True

    def test_supplementary_plane(self) -> None:
        result = _run_language_cli(["language", "unicode-analyze", "U+1F600"])
        assert result["plane"] == "SMP"


# ── locale-format CLI subcommand ───────────────────────────────────────────


class TestLocaleFormatCLI:
    """``gludd language locale-format`` exposes locale_data."""

    def test_number_formatting(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "en-US", "--number", "1234567.89",
        ])
        assert result["formatted"] == "1,234,567.89"
        assert result["locale"] == "en-US"

    def test_currency_formatting(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "en-US", "--currency", "99.50", "USD",
        ])
        assert "$" in result["formatted"]  # type: ignore[operator]
        assert result["currency_code"] == "USD"

    def test_plural_evaluation(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "en-US", "--plural", "1",
        ])
        assert result["plural_category"] == "one"

    def test_plural_many(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "en-US", "--plural", "5",
        ])
        assert result["plural_category"] == "other"

    def test_plural_arabic(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "ar-SA", "--plural", "0",
        ])
        assert result["plural_category"] == "zero"

    def test_negotiate_locale(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "en-US",
            "--negotiate", "fr-FR;q=0.9,en-US;q=1.0",
            "--available", "en-US,de-DE,fr-FR",
        ])
        assert result["negotiated"] == "en-US"

    def test_locale_info(self) -> None:
        result = _run_language_cli([
            "language", "locale-format", "ar-SA", "--info",
        ])
        assert result["is_rtl"] is True  # type: ignore[unreachable]


# ── i18n-extract CLI subcommand ─────────────────────────────────────────────


class TestI18nExtractCLI:
    """``gludd language i18n-extract`` exposes i18n_data."""

    def test_pseudolocalize_accent(self) -> None:
        result = _run_language_cli([
            "language", "i18n-extract", "--pseudolocalize", "hello",
        ])
        assert result["method"] == "accent"
        assert len(result["output"]) >= len("hello")  # type: ignore[arg-type]
        assert "é" in result["output"] or "e" in result["output"]  # type: ignore[operator]

    def test_pseudolocalize_bracket(self) -> None:
        result = _run_language_cli([
            "language", "i18n-extract", "--pseudolocalize", "hi",
            "--method", "bracket",
        ])
        assert result["output"] == "[hi]"

    def test_extract_icu_placeholders(self) -> None:
        result = _run_language_cli([
            "language", "i18n-extract",
            "--extract-icu", "Hello {name}, you have {count} messages",
        ])
        assert set(result["placeholders"]) == {"name", "count"}  # type: ignore[arg-type]

    def test_parse_po_file(self, tmp_path: Path) -> None:
        po_content = (
            'msgid ""\n'
            'msgstr ""\n'
            '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            'msgid "Hello"\n'
            'msgstr "Hallo"\n\n'
            'msgid "World"\n'
            'msgstr "Welt"\n'
        )
        po_file = tmp_path / "test.po"
        po_file.write_text(po_content, encoding="utf-8")

        result = _run_language_cli([
            "language", "i18n-extract", "--parse-po", str(po_file),
        ])
        assert result["entry_count"] == 2  # type: ignore[unreachable]
        assert len(result["entries"]) == 2  # type: ignore[arg-type]


# ── font-analyze CLI subcommand ─────────────────────────────────────────────


class TestFontAnalyzeCLI:
    """``gludd language font-analyze`` exposes font_data."""

    def test_identify_ttf(self, tmp_path: Path) -> None:
        header = b"\x00\x01\x00\x00" + b"\x00" * 60
        font_file = tmp_path / "test.ttf"
        font_file.write_bytes(header)

        result = _run_language_cli([
            "language", "font-analyze", str(font_file),
        ])
        assert result["format"] == "ttf"
        assert result["file_size"] == len(header)

    def test_system_font_stacks(self) -> None:
        result = _run_language_cli([
            "language", "font-analyze", "--system-stacks",
        ])
        assert "macos" in result["stacks"]  # type: ignore[arg-type]
        assert "sans-serif" in result["stacks"]["macos"]  # type: ignore[index]

    def test_unknown_format(self, tmp_path: Path) -> None:
        header = b"XYZW" + b"\x00" * 60
        font_file = tmp_path / "unknown.bin"
        font_file.write_bytes(header)

        result = _run_language_cli([
            "language", "font-analyze", str(font_file),
        ])
        assert result["format"] == "unknown"


# ── analyze-text CLI subcommand (composite) ────────────────────────────────


class TestAnalyzeTextCLI:
    """``gludd language analyze-text`` combines all modules for health report."""

    def test_clean_text(self) -> None:
        result = _run_language_cli([
            "language", "analyze-text", "Hello World",
        ])
        assert result["length"] == 11
        assert result["safe"] is True
        assert result["total_findings"] == 0

    def test_homoglyph_detection(self) -> None:
        result = _run_language_cli([
            "language", "analyze-text", chr(0x0410) + "pple",
        ])
        assert result["safe"] is False
        assert result["total_findings"] >= 1
        assert result["mixed_script"]["is_mixed"] is True

    def test_invisible_char_detection(self) -> None:
        text = "hello\u200bworld"
        result = _run_language_cli([
            "language", "analyze-text", text,
        ])
        assert result["safe"] is False
        assert result["total_findings"] >= 1

    def test_bidi_override_detection(self) -> None:
        text = "print\u202eHello"
        result = _run_language_cli([
            "language", "analyze-text", text,
        ])
        assert result["safe"] is False

    def test_normalization_info(self) -> None:
        result = _run_language_cli([
            "language", "analyze-text", "café",
        ])
        assert "is_nfc" in result
        assert "is_nfd" in result

    def test_skeleton_generation(self) -> None:
        text = chr(0x0410)
        result = _run_language_cli([
            "language", "analyze-text", text,
        ])
        assert "skeleton" in result
