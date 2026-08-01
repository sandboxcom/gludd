"""Phase D TDD tests: Fonts + Phonetics + Homoglyph functional helpers.

Covers spec sections 4.6 (Fonts), 4.7 (Phonetics), 4.8 (Homoglyphs):
- Font format identification via magic bytes
- Font metric extraction from TTF/OTF headers
- System font stacks per OS
- Soundex / Metaphone / Double Metaphone phonetic hashing
- Text-to-IPA and text-to-ARPABET transcription
- Homoglyph/confusable detection
- Invisible character detection
- Bidi override (Trojan Source) detection
- Mixed-script detection
- Skeleton generation for confusables

These tests fail until the corresponding functions exist in
src/general_ludd/language/font_data.py, phonetic_data.py, and
homoglyph_data.py.
"""

from __future__ import annotations

import struct
from pathlib import Path

# ── Font format identification ─────────────────────────────────────────────


class TestFontFormatIdentification:
    """identify_font_format() reads magic bytes and returns format name."""

    def test_trueuetype_ttf(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"\x00\x01\x00\x00" + b"\x00" * 60
        assert identify_font_format(header) == "ttf"

    def test_opentype_otf(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"OTTO" + b"\x00" * 60
        assert identify_font_format(header) == "otf"

    def test_woff(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"wOFF" + b"\x00" * 60
        assert identify_font_format(header) == "woff"

    def test_woff2(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"wOF2" + b"\x00" * 60
        assert identify_font_format(header) == "woff2"

    def test_truetype_collection(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"ttcf" + b"\x00" * 60
        assert identify_font_format(header) == "ttc"

    def test_unknown_format(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        header = b"XYZW" + b"\x00" * 60
        assert identify_font_format(header) == "unknown"

    def test_empty_header(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        assert identify_font_format(b"") == "unknown"

    def test_short_header(self) -> None:
        from general_ludd.language.font_data import identify_font_format
        assert identify_font_format(b"\x00") == "unknown"


# ── Font metric extraction ─────────────────────────────────────────────────


class TestFontMetrics:
    """get_font_metrics() parses TTF/OTF 'head' and 'hhea' tables."""

    def _build_ttf(self, tmp_path: Path, *, em_units: int = 2048,
                   ascent: int = 800, descent: int = -200,
                   line_gap: int = 0) -> Path:
        """Build a minimal valid TTF with head + hhea tables."""
        font_path = tmp_path / "test.ttf"

        head_table = bytearray(54)
        struct.pack_into(">H", head_table, 18, em_units)

        hhea_table = bytearray(36)
        struct.pack_into(">h", hhea_table, 4, ascent)
        struct.pack_into(">h", hhea_table, 6, descent)
        struct.pack_into(">h", hhea_table, 8, line_gap)

        num_tables = 2
        header = struct.pack(">IHHHH", 0x00010000, num_tables, 0, 0, 0)

        head_offset = 12 + num_tables * 16
        hhea_offset = head_offset + len(head_table)

        head_record = struct.pack(
            ">4sIII", b"head", 0, head_offset, len(head_table)
        )
        hhea_record = struct.pack(
            ">4sIII", b"hhea", 0, hhea_offset, len(hhea_table)
        )

        font_path.write_bytes(
            header + head_record + hhea_record
            + bytes(head_table) + bytes(hhea_table)
        )
        return font_path

    def test_returns_em_units(self, tmp_path: Path) -> None:
        from general_ludd.language.font_data import get_font_metrics
        font_path = self._build_ttf(tmp_path, em_units=1000)
        metrics = get_font_metrics(str(font_path))
        assert metrics["em_units"] == 1000

    def test_returns_ascent_descent(self, tmp_path: Path) -> None:
        from general_ludd.language.font_data import get_font_metrics
        font_path = self._build_ttf(tmp_path, ascent=905, descent=-210)
        metrics = get_font_metrics(str(font_path))
        assert metrics["ascent"] == 905
        assert metrics["descent"] == -210

    def test_returns_line_gap(self, tmp_path: Path) -> None:
        from general_ludd.language.font_data import get_font_metrics
        font_path = self._build_ttf(tmp_path, line_gap=95)
        metrics = get_font_metrics(str(font_path))
        assert metrics["line_gap"] == 95

    def test_missing_file_raises(self) -> None:
        from general_ludd.language.font_data import get_font_metrics
        result = get_font_metrics("/nonexistent/font.ttf")
        assert "error" in result

    def test_non_font_file_returns_error(self, tmp_path: Path) -> None:
        from general_ludd.language.font_data import get_font_metrics
        not_font = tmp_path / "not_a_font.txt"
        not_font.write_text("hello", encoding="utf-8")
        result = get_font_metrics(str(not_font))
        assert "error" in result or result.get("format") == "unknown"


# ── Font table enumeration ─────────────────────────────────────────────────


class TestFontTables:
    """list_font_tables() returns the list of OpenType table records."""

    def test_lists_head_and_hhea(self, tmp_path: Path) -> None:
        from general_ludd.language.font_data import list_font_tables

        head = bytearray(54)
        hhea = bytearray(36)
        num_tables = 2
        header = struct.pack(">IHHHH", 0x00010000, num_tables, 0, 0, 0)
        head_offset = 12 + num_tables * 16
        hhea_offset = head_offset + len(head)
        head_rec = struct.pack(">4sIII", b"head", 0, head_offset, 54)
        hhea_rec = struct.pack(">4sIII", b"hhea", 0, hhea_offset, 36)

        font_path = tmp_path / "test.ttf"
        font_path.write_bytes(
            header + head_rec + hhea_rec + bytes(head) + bytes(hhea)
        )
        tables = list_font_tables(str(font_path))
        tags = [t["tag"] for t in tables]
        assert "head" in tags
        assert "hhea" in tags

    def test_nonexistent_file_returns_empty(self) -> None:
        from general_ludd.language.font_data import list_font_tables
        assert list_font_tables("/nonexistent/font.ttf") == []


# ── System font stacks ─────────────────────────────────────────────────────


class TestSystemFontStacks:
    """SYSTEM_FONT_STACKS has per-OS monospace/sans-serif/serif stacks."""

    def test_has_macos_key(self) -> None:
        from general_ludd.language.font_data import SYSTEM_FONT_STACKS
        assert "macos" in SYSTEM_FONT_STACKS

    def test_has_windows_key(self) -> None:
        from general_ludd.language.font_data import SYSTEM_FONT_STACKS
        assert "windows" in SYSTEM_FONT_STACKS

    def test_has_linux_key(self) -> None:
        from general_ludd.language.font_data import SYSTEM_FONT_STACKS
        assert "linux" in SYSTEM_FONT_STACKS

    def test_monospace_in_each(self) -> None:
        from general_ludd.language.font_data import SYSTEM_FONT_STACKS
        for os_name, stacks in SYSTEM_FONT_STACKS.items():
            assert "monospace" in stacks, (
                f"{os_name} missing monospace stack"
            )

    def test_macos_monospace_has_menlo(self) -> None:
        from general_ludd.language.font_data import SYSTEM_FONT_STACKS
        assert "Menlo" in SYSTEM_FONT_STACKS["macos"]["monospace"]


# ── Font format specs ──────────────────────────────────────────────────────


class TestFontFormatSpecs:
    """FONT_FORMAT_SPECS documents the OpenType/TrueType/WOFF formats."""

    def test_has_ttf_otf_woff_woff2(self) -> None:
        from general_ludd.language.font_data import FONT_FORMAT_SPECS
        for fmt in ("ttf", "otf", "woff", "woff2"):
            assert fmt in FONT_FORMAT_SPECS

    def test_each_has_magic_bytes(self) -> None:
        from general_ludd.language.font_data import FONT_FORMAT_SPECS
        for fmt, spec in FONT_FORMAT_SPECS.items():
            assert "magic" in spec, f"{fmt} missing magic bytes"


# ── Soundex ────────────────────────────────────────────────────────────────


class TestSoundex:
    """compute_soundex() returns the 4-char Soundex code."""

    def test_robert(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Robert") == "R163"

    def test_rupert(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Rupert") == "R163"

    def test_ashcraft(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Ashcraft") == "A261"

    def test_tymczak(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Tymczak") == "T522"

    def test_pfister(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Pfister") == "P236"

    def test_short_name_padded(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("Bo") == "B000"

    def test_empty_string(self) -> None:
        from general_ludd.language.phonetic_data import compute_soundex
        assert compute_soundex("") == ""


# ── Metaphone ──────────────────────────────────────────────────────────────


class TestMetaphone:
    """compute_metaphone() returns the primary metaphone code."""

    def test_smith(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone
        result = compute_metaphone("Smith")
        assert result.startswith("SM")

    def test_handles_kn_initial(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone
        result = compute_metaphone("knight")
        assert result.startswith("N")

    def test_empty_string(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone
        assert compute_metaphone("") == ""

    def test_non_alpha_preserved(self) -> None:
        from general_ludd.language.phonetic_data import compute_metaphone
        result = compute_metaphone("Hello123")
        assert len(result) > 0


# ── Double Metaphone ───────────────────────────────────────────────────────


class TestDoubleMetaphone:
    """compute_double_metaphone() returns (primary, alternate)."""

    def test_returns_tuple(self) -> None:
        from general_ludd.language.phonetic_data import (
            compute_double_metaphone,
        )
        result = compute_double_metaphone("Smith")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_smith_primary(self) -> None:
        from general_ludd.language.phonetic_data import (
            compute_double_metaphone,
        )
        primary, _ = compute_double_metaphone("Smith")
        assert primary.startswith("SM")

    def test_empty_string(self) -> None:
        from general_ludd.language.phonetic_data import (
            compute_double_metaphone,
        )
        primary, alternate = compute_double_metaphone("")
        assert primary == ""
        assert alternate == ""


# ── Text-to-IPA transcription ──────────────────────────────────────────────


class TestTranscribeToIPA:
    """transcribe_to_ipa() converts text to IPA using CMU dict."""

    def test_hello_from_cmu(self) -> None:
        from general_ludd.language.phonetic_data import transcribe_to_ipa
        result = transcribe_to_ipa("hello")
        assert len(result) > 0
        assert result != "hello"

    def test_world_from_cmu(self) -> None:
        from general_ludd.language.phonetic_data import transcribe_to_ipa
        result = transcribe_to_ipa("world")
        assert len(result) > 0

    def test_unknown_word_fallback(self) -> None:
        from general_ludd.language.phonetic_data import transcribe_to_ipa
        result = transcribe_to_ipa("qwxyzzz")
        assert isinstance(result, str)

    def test_empty_string(self) -> None:
        from general_ludd.language.phonetic_data import transcribe_to_ipa
        assert transcribe_to_ipa("") == ""


# ── Text-to-ARPABET transcription ──────────────────────────────────────────


class TestTranscribeToArpabet:
    """transcribe_to_arpabet() converts text to ARPABET using CMU dict."""

    def test_hello(self) -> None:
        from general_ludd.language.phonetic_data import (
            transcribe_to_arpabet,
        )
        result = transcribe_to_arpabet("hello")
        assert "HH" in result
        assert "AH" in result or "ER" in result
        assert "L" in result

    def test_world(self) -> None:
        from general_ludd.language.phonetic_data import (
            transcribe_to_arpabet,
        )
        result = transcribe_to_arpabet("world")
        assert "W" in result
        assert "L" in result
        assert "D" in result

    def test_empty_string(self) -> None:
        from general_ludd.language.phonetic_data import (
            transcribe_to_arpabet,
        )
        assert transcribe_to_arpabet("") == ""


# ── Homoglyph detection ────────────────────────────────────────────────────


class TestDetectConfusables:
    """detect_confusables() finds confusable characters in text."""

    def test_finds_cyrillic_a(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        text = "paypa\u0430l.com"
        findings = detect_confusables(text)
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x0430 for f in findings)

    def test_clean_ascii_no_findings(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        findings = detect_confusables("hello world")
        assert findings == []

    def test_empty_string(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        assert detect_confusables("") == []

    def test_finding_has_skeleton(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        text = "\u0430"
        findings = detect_confusables(text)
        assert len(findings) == 1
        assert findings[0]["skeleton"] == "a"

    def test_finding_has_codepoint_name(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        findings = detect_confusables("\u0430")
        assert len(findings) == 1
        assert "name" in findings[0]
        assert "CYRILLIC" in findings[0]["name"].upper()

    def test_multiple_findings(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_confusables,
        )
        text = "\u0430\u0435\u043E"
        findings = detect_confusables(text)
        assert len(findings) == 3


# ── Invisible character detection ──────────────────────────────────────────


class TestDetectInvisibleChars:
    """detect_invisible_chars() finds zero-width and bidi control chars."""

    def test_finds_zero_width_space(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_invisible_chars,
        )
        text = "hello\u200bworld"
        findings = detect_invisible_chars(text)
        assert len(findings) == 1
        assert findings[0]["codepoint"] == 0x200B

    def test_finds_soft_hyphen(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_invisible_chars,
        )
        text = "hyphen\u00adated"
        findings = detect_invisible_chars(text)
        assert len(findings) >= 1

    def test_clean_text_no_findings(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_invisible_chars,
        )
        assert detect_invisible_chars("clean text") == []

    def test_empty_string(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_invisible_chars,
        )
        assert detect_invisible_chars("") == []

    def test_finding_has_category(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_invisible_chars,
        )
        findings = detect_invisible_chars("a\u200bb")
        assert len(findings) == 1
        assert findings[0]["category"] == "zero-width-space"


# ── Bidi override (Trojan Source) detection ────────────────────────────────


class TestDetectBidiOverrides:
    """detect_bidi_overrides() finds CVE-2021-42574 attack characters."""

    def test_finds_rlo(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
        )
        text = "code\u202e; rm -rf /"
        findings = detect_bidi_overrides(text)
        assert len(findings) >= 1
        assert any(f["codepoint"] == 0x202E for f in findings)

    def test_finds_lre(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
        )
        findings = detect_bidi_overrides("\u202a")
        assert len(findings) == 1

    def test_clean_text_no_findings(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
        )
        assert detect_bidi_overrides("normal text") == []

    def test_empty_string(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
        )
        assert detect_bidi_overrides("") == []

    def test_finding_has_cve_reference(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_bidi_overrides,
        )
        findings = detect_bidi_overrides("\u202e")
        assert len(findings) == 1
        assert "CVE-2021-42574" in findings[0].get("cve", "")


# ── Mixed-script detection ─────────────────────────────────────────────────


class TestDetectMixedScript:
    """detect_mixed_script() flags text mixing Latin + Cyrillic/etc."""

    def test_mixed_latin_cyrillic(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_mixed_script,
        )
        result = detect_mixed_script("Hello\u0430")
        assert result["is_mixed"] is True
        assert len(result["scripts"]) >= 2

    def test_pure_latin_not_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_mixed_script,
        )
        result = detect_mixed_script("Hello world")
        assert result["is_mixed"] is False

    def test_pure_cyrillic_not_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_mixed_script,
        )
        result = detect_mixed_script("\u043f\u0440\u0438\u0432\u0435\u0442")
        assert result["is_mixed"] is False

    def test_empty_string(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_mixed_script,
        )
        result = detect_mixed_script("")
        assert result["is_mixed"] is False

    def test_returns_script_counts(self) -> None:
        from general_ludd.language.homoglyph_data import (
            detect_mixed_script,
        )
        result = detect_mixed_script("Hi\u0430")
        assert "scripts" in result
        assert "counts" in result


# ── Skeleton generation ────────────────────────────────────────────────────


class TestGenerateSkeleton:
    """generate_skeleton() normalizes confusables to ASCII skeleton."""

    def test_cyrillic_a_to_latin_a(self) -> None:
        from general_ludd.language.homoglyph_data import (
            generate_skeleton,
        )
        assert generate_skeleton("\u0430") == "a"

    def test_cyrillic_o_to_latin_o(self) -> None:
        from general_ludd.language.homoglyph_data import (
            generate_skeleton,
        )
        assert generate_skeleton("\u043E") == "o"

    def test_latin_preserved(self) -> None:
        from general_ludd.language.homoglyph_data import (
            generate_skeleton,
        )
        assert generate_skeleton("hello") == "hello"

    def test_mixed_paypal(self) -> None:
        from general_ludd.language.homoglyph_data import (
            generate_skeleton,
        )
        assert generate_skeleton("payp\u0430l") == "paypal"

    def test_empty_string(self) -> None:
        from general_ludd.language.homoglyph_data import (
            generate_skeleton,
        )
        assert generate_skeleton("") == ""


# ── Suspicious string check ────────────────────────────────────────────────


class TestIsSuspicious:
    """is_suspicious() returns True for text with any security risk."""

    def test_confusable_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious
        assert is_suspicious("paypa\u0430l.com") is True

    def test_invisible_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious
        assert is_suspicious("hello\u200bworld") is True

    def test_bidi_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious
        assert is_suspicious("code\u202e") is True

    def test_clean_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious
        assert is_suspicious("hello world") is False

    def test_empty_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious
        assert is_suspicious("") is False
