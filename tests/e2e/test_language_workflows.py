"""E2E tests for the language expert subsystem.

Covers all 9 modules under ``src/general_ludd/language/`` with realistic
cross-module workflows: detect a mixed-language directory, analyze homoglyphs
in the detected files, surface encoding conflicts, generate phonetic codes,
negotiate locales, pseudolocalize strings, and validate corpus statistics.

Run:  make test-specific TESTFILE=tests/e2e/test_language_workflows.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.language.charset_map import (
    ALL_ENCODINGS,
    BOM_BY_SEQUENCE,
    BOM_SIGNATURES,
    CHARDET_CONFIDENCE_THRESHOLDS,
    CJK_ENCODINGS,
    CYRILLIC_ENCODINGS,
    IBM_CODE_PAGES,
    MOJIBAKE_SIGNATURES,
    SINGLE_BYTE_ENCODINGS,
    UTF_ENCODINGS,
    WINDOWS_CODE_PAGES,
)
from general_ludd.language.corpus import CorpusAnalyzer
from general_ludd.language.font_data import (
    FONT_FORMAT_SPECS,
    OPENTYPE_REQUIRED_TABLES,
    SYSTEM_FONT_STACKS,
    VARIABLE_FONT_AXES,
    get_font_metrics,
    has_kerning,
    has_variable_axes,
    identify_font_format,
    is_web_font_format,
    list_font_tables,
)
from general_ludd.language.homoglyph_data import (
    ATTACK_VECTORS,
    HOMOGLYPH_GROUPS,
    INVISIBLE_CHARACTERS,
    detect_bidi_overrides,
    detect_confusables,
    detect_invisible_chars,
    detect_mixed_script,
    generate_skeleton,
    is_suspicious,
)
from general_ludd.language.i18n_data import (
    PSEUDO_ACCENT_MAP,
    extract_icu_placeholders,
    find_untranslated_strings,
    parse_po,
    pseudolocalize,
    serialize_po,
)
from general_ludd.language.locale_data import (
    CLDR_FIRST_DAY_OF_WEEK,
    CLDR_MEASUREMENT_SYSTEMS,
    COMMON_CURRENCIES,
    ISO_639_1_TO_NAME,
    ISO_3166_TO_NAME,
    ISO_15924_TO_NAME,
    LOCALE_FORMATS,
    RTL_SCRIPTS,
    evaluate_plural,
    format_currency,
    format_number,
    get_locale_data,
    negotiate_locale,
    parse_bcp47,
)
from general_ludd.language.phonetic_data import (
    ARPABET_TO_IPA,
    CMU_DICT_SUBSET,
    IPA_CONSONANTS,
    IPA_TO_ARPABET,
    IPA_VOWELS,
    SOUNDEX_MAPPING,
    compute_double_metaphone,
    compute_metaphone,
    compute_soundex,
    transcribe_to_arpabet,
    transcribe_to_ipa,
)
from general_ludd.language.polyglot import (
    cross_language_homoglyph_scan,
    detect_languages_in_directory,
    encoding_conflict_report,
)
from general_ludd.language.unicode_data import (
    UNICODE_BLOCK_NAMES,
    UNICODE_CATEGORY_NAMES,
    UNICODE_PLANE_NAMES,
    UNICODE_VERSION_HISTORY,
    is_high_surrogate,
    is_low_surrogate,
    is_surrogate,
    plane_of,
    surrogates_to_codepoint,
)

# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def polyglot_tmpdir() -> str:
    """Create a scratch directory with mixed-language source files."""
    root = tempfile.mkdtemp(prefix="gludd-lang-e2e-")
    root_p = Path(root)

    (root_p / "main.py").write_text("def hello():\n    print('hello world')\n")
    (root_p / "utils.js").write_text("function add(a, b) { return a + b; }\n")
    (root_p / "types.ts").write_text("interface Foo { bar: string }\n")
    (root_p / "lib.rs").write_text("fn main() {\n    println!(\"hello\");\n}\n")
    (root_p / "go.mod").write_text("module example\n\ngo 1.21\n")
    (root_p / "Makefile").write_text("all:\n\t@echo done\n")
    (root_p / "style.css").write_text("body { margin: 0; }\n")
    (root_p / "README.md").write_text("# Hello\n")
    (root_p / "bom_utf16_le.txt").write_bytes(b"\xff\xfeH\x00e\x00l\x00l\x00o\x00")
    (root_p / "utf8_bom.txt").write_bytes(b"\xef\xbb\xbfhello\n")
    (root_p / "plain.txt").write_text("hello\n")

    return str(root_p)


# ═══════════════════════════════════════════════════════════════════════════════
# polyglot — directory detection + encoding reports
# ═══════════════════════════════════════════════════════════════════════════════


class TestPolyglotDetection:
    """NF.9: polyglot directory-level language detection."""

    def test_detect_languages_in_scratch_dir(self, polyglot_tmpdir: str) -> None:
        """Walk a multi-language directory and classify every file."""
        result = detect_languages_in_directory(polyglot_tmpdir)
        assert result["total_files"] >= 8
        langs = {p["language"] for p in result["languages"]}
        assert "python" in langs
        assert "javascript" in langs
        assert "typescript" in langs
        assert "rust" in langs
        assert "css" in langs

    def test_detect_languages_marker_files_reported(self, polyglot_tmpdir: str) -> None:
        result = detect_languages_in_directory(polyglot_tmpdir)
        assert "go.mod" in result["marker_files"]
        assert "Makefile" in result["marker_files"]

    def test_detect_languages_nonexistent_path(self) -> None:
        result = detect_languages_in_directory("/nonexistent/path/12345")
        assert result["languages"] == []
        assert result["total_files"] == 0

    def test_cross_language_homoglyph_scan_hits(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        confusable_py = root_p / "confuse.py"
        confusable_py.write_text("# x = '\u0441'  # Cyrillic small es\n")
        js_file = str(root_p / "utils.js")
        findings = cross_language_homoglyph_scan([str(confusable_py), js_file])
        assert len(findings) >= 1
        py_finding = next(f for f in findings if f["file"].endswith(".py"))
        assert py_finding["language"] == "python"
        assert len(py_finding["confusables"]) >= 1

    def test_encoding_conflict_report_mixed(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        files = [
            str(root_p / "main.py"),
            str(root_p / "bom_utf16_le.txt"),
            str(root_p / "utf8_bom.txt"),
            str(root_p / "plain.txt"),
        ]
        report = encoding_conflict_report(files)
        assert not report["is_consistent"]
        assert len(report["encodings_present"]) >= 2
        assert len(report["conflicts"]) >= 1

    def test_encoding_conflict_report_consistent(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        report = encoding_conflict_report([str(root_p / "main.py"), str(root_p / "plain.txt")])
        assert report["is_consistent"]


# ═══════════════════════════════════════════════════════════════════════════════
# homoglyph_data — security scanning
# ═══════════════════════════════════════════════════════════════════════════════


class TestHomoglyphWorkflows:
    """NF.9: homoglyph + invisible-char detection workflows."""

    def test_detect_confusables_cyrillic_a(self) -> None:
        findings = detect_confusables("\u0430")  # Cyrillic small a
        assert len(findings) == 1
        assert findings[0]["skeleton"] == "a"
        assert findings[0]["codepoint"] == 0x0430

    def test_detect_confusables_pure_ascii(self) -> None:
        assert detect_confusables("hello world") == []

    def test_detect_confusables_empty(self) -> None:
        assert detect_confusables("") == []

    def test_detect_invisible_chars_zwsp(self) -> None:
        findings = detect_invisible_chars("hello\u200bworld")
        assert len(findings) == 1
        assert findings[0]["short_name"] == "ZWSP"
        assert findings[0]["category"] == "zero-width-space"

    def test_detect_bidi_overrides_trojan_source(self) -> None:
        findings = detect_bidi_overrides("\u202e")  # RLO
        assert len(findings) == 1
        assert findings[0]["cve"] == "CVE-2021-42574"

    def test_detect_mixed_script_latin_cyrillic(self) -> None:
        result = detect_mixed_script("Hello\u0410")  # Latin + Cyrillic A
        assert result["is_mixed"]
        assert "Latin" in result["scripts"]
        assert "Cyrillic" in result["scripts"]

    def test_generate_skeleton_normalizes_confusables(self) -> None:
        result = generate_skeleton("\u0430\u043E\u0441")  # Cyrillic a, o, s
        assert result == "aoc"

    def test_is_suspicious_finds_confusable(self) -> None:
        assert is_suspicious("\u0430pple")

    def test_is_suspicious_empty_false(self) -> None:
        assert not is_suspicious("")

    def test_attack_vectors_populated(self) -> None:
        assert "domain_spoofing" in ATTACK_VECTORS
        assert "code_injection" in ATTACK_VECTORS
        assert len(ATTACK_VECTORS) >= 5

    def test_homoglyph_groups_structure(self) -> None:
        assert len(HOMOGLYPH_GROUPS) >= 20
        for group in HOMOGLYPH_GROUPS:
            assert "skeleton" in group
            assert len(group["characters"]) >= 2

    def test_invisible_characters_structure(self) -> None:
        assert len(INVISIBLE_CHARACTERS) >= 15
        categories = {c["category"] for c in INVISIBLE_CHARACTERS}
        assert "zero-width-space" in categories
        assert "bidi-control" in categories


# ═══════════════════════════════════════════════════════════════════════════════
# corpus — analysis workflows
# ═══════════════════════════════════════════════════════════════════════════════


class TestCorpusWorkflows:
    """NF.9: corpus analysis across the polyglot-tmpdir."""

    def test_frequency_analysis_on_scratch(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        files = [
            str(p)
            for p in list(root_p.glob("*.py")) + list(root_p.glob("*.js"))
        ]
        analyzer = CorpusAnalyzer(files)
        freq = analyzer.frequency_analysis(top_n=5)
        assert freq["total_chars"] > 0
        assert len(freq["top_chars"]) <= 5
        assert "word_counts" in freq

    def test_ngram_char_analysis(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        analyzer = CorpusAnalyzer([str(root_p / "main.py")])
        grams = analyzer.extract_ngrams(3, "char")
        assert "def" in grams
        assert grams["def"] >= 1

    def test_ngram_invalid_unit_raises(self, polyglot_tmpdir: str) -> None:
        analyzer = CorpusAnalyzer([str(Path(polyglot_tmpdir) / "main.py")])
        with pytest.raises(ValueError, match="unit"):
            analyzer.extract_ngrams(2, "sentence")

    def test_language_distribution(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        all_files = [str(p) for p in root_p.glob("*") if p.suffix]
        analyzer = CorpusAnalyzer(all_files)
        dist = analyzer.language_distribution()
        assert dist["python"] >= 1

    def test_encoding_statistics(self, polyglot_tmpdir: str) -> None:
        root_p = Path(polyglot_tmpdir)
        all_files = [str(p) for p in root_p.glob("*") if p.suffix]
        analyzer = CorpusAnalyzer(all_files)
        stats = analyzer.encoding_statistics()
        assert stats["total_files"] >= 1
        assert "by_encoding" in stats
        assert isinstance(stats["is_consistent"], bool)

    def test_empty_corpus(self) -> None:
        analyzer = CorpusAnalyzer([])
        assert analyzer.frequency_analysis()["total_chars"] == 0
        assert analyzer.extract_ngrams(2, "char") == {}


# ═══════════════════════════════════════════════════════════════════════════════
# locale_data — locale formatting + plural rules
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocaleWorkflows:
    """NF.9: locale data, plural evaluation, and number formatting."""

    def test_parse_bcp47_en_us(self) -> None:
        parsed = parse_bcp47("en-US")
        assert parsed["language"] == "en"
        assert parsed["territory"] == "US"
        assert parsed["canonical"] == "en-US"

    def test_parse_bcp47_underscore_variant(self) -> None:
        parsed = parse_bcp47("en_US.UTF-8")
        assert parsed["language"] == "en"
        assert parsed["territory"] == "US"
        assert parsed["codeset"] == "UTF-8"

    def test_parse_bcp47_zh_hans_cn(self) -> None:
        parsed = parse_bcp47("zh-Hans-CN")
        assert parsed["language"] == "zh"
        assert parsed["script"] == "Hans"
        assert parsed["territory"] == "CN"

    def test_parse_bcp47_empty(self) -> None:
        parsed = parse_bcp47("")
        assert parsed["language"] == ""

    def test_get_locale_data_exact(self) -> None:
        data = get_locale_data("en-US")
        assert data is not None
        assert data["is_rtl"] is False
        assert data["number_format"]["decimal_separator"] == "."

    def test_get_locale_data_none(self) -> None:
        assert get_locale_data("") is None
        assert get_locale_data("zz-ZZ") is None

    def test_negotiate_locale_best_match(self) -> None:
        result = negotiate_locale("fr-FR,en;q=0.8", ["en-US", "fr-FR", "de-DE"])
        assert result == "fr-FR"

    def test_negotiate_locale_language_fallback(self) -> None:
        result = negotiate_locale("en-GB", ["en-US", "fr-FR"])
        assert result == "en-US"

    def test_negotiate_locale_wildcard(self) -> None:
        result = negotiate_locale("*", ["en-US", "fr-FR"])
        assert result == "en-US"

    def test_evaluate_plural_english(self) -> None:
        assert evaluate_plural("en-US", 1) == "one"
        assert evaluate_plural("en-US", 5) == "other"

    def test_evaluate_plural_russian(self) -> None:
        assert evaluate_plural("ru-RU", 1) == "one"
        assert evaluate_plural("ru-RU", 2) == "few"
        assert evaluate_plural("ru-RU", 5) == "many"
        assert evaluate_plural("ru-RU", 21) == "one"
        assert evaluate_plural("ru-RU", 11) == "many"

    def test_evaluate_plural_arabic(self) -> None:
        assert evaluate_plural("ar-SA", 0) == "zero"
        assert evaluate_plural("ar-SA", 1) == "one"
        assert evaluate_plural("ar-SA", 2) == "two"
        assert evaluate_plural("ar-SA", 5) == "few"
        assert evaluate_plural("ar-SA", 12) == "many"

    def test_format_number_en_us(self) -> None:
        assert format_number(1234.56, "en-US") == "1,234.56"
        assert format_number(1000000, "en-US") == "1,000,000"

    def test_format_number_de_de(self) -> None:
        assert format_number(1234.56, "de-DE") == "1.234,56"

    def test_format_currency_usd(self) -> None:
        result = format_currency(42.5, "USD", "en-US")
        assert "$" in result and "42.50" in result

    def test_format_currency_eur_after(self) -> None:
        result = format_currency(42.5, "EUR", "fr-FR")
        assert result.endswith("\u20ac")

    def test_format_number_unknown_locale(self) -> None:
        result = format_number(1234.56, "zz-ZZ")
        assert "1234.56" in result


# ═══════════════════════════════════════════════════════════════════════════════
# i18n_data — pseudolocalization + gettext
# ═══════════════════════════════════════════════════════════════════════════════


class TestI18nWorkflows:
    """NF.9: pseudolocalization, PO parsing, ICU extraction."""

    def test_pseudolocalize_accent(self) -> None:
        result = pseudolocalize("Hello World", "accent")
        assert result != "Hello World"
        assert PSEUDO_ACCENT_MAP.get("H", "H") == "H" or result[0] != "H"

    def test_pseudolocalize_bracket(self) -> None:
        assert pseudolocalize("Hello", "bracket") == "[Hello]"

    def test_pseudolocalize_empty(self) -> None:
        assert pseudolocalize("", "accent") == ""
        assert pseudolocalize("", "bracket") == ""

    def test_pseudolocalize_preserves_placeholders(self) -> None:
        result = pseudolocalize("Hello {name}, you have %d messages", "accent")
        assert "{name}" in result
        assert "%d" in result

    def test_parse_po_basic(self) -> None:
        po = '''msgid "hello"
msgstr "bonjour"
'''
        entries = parse_po(po)
        assert len(entries) == 1
        assert entries[0]["msgid"] == "hello"
        assert entries[0]["msgstr"] == "bonjour"

    def test_parse_po_empty(self) -> None:
        assert parse_po("") == []

    def test_serialize_po_roundtrip(self) -> None:
        entries = [
            {"msgid": "hello", "msgstr": "bonjour", "references": ["app.py:10"], "flags": ["fuzzy"]}
        ]
        po_text = serialize_po(entries)
        parsed = parse_po(po_text)
        assert len(parsed) == 1
        assert parsed[0]["msgid"] == "hello"
        assert parsed[0]["msgstr"] == "bonjour"

    def test_extract_icu_placeholders(self) -> None:
        result = extract_icu_placeholders("{count, plural, one {# item} other {# items}} and {name}")
        assert "count" in result
        assert "name" in result

    def test_find_untranslated_strings(self) -> None:
        source = 'label = "Hello World this is a test"'
        findings = find_untranslated_strings(source)
        assert len(findings) >= 1
        assert "Hello World this is a test" in [f["string"] for f in findings]

    def test_find_untranslated_strings_empty(self) -> None:
        assert find_untranslated_strings("") == []


# ═══════════════════════════════════════════════════════════════════════════════
# phonetic_data — soundex, metaphone, transcription
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhoneticWorkflows:
    """NF.9: phonetic algorithms and CMU dictionary transcription."""

    def test_compute_soundex(self) -> None:
        assert compute_soundex("Washington") == "W252"
        assert compute_soundex("Lee") == "L000"
        assert compute_soundex("Gutierrez") == "G362"

    def test_compute_soundex_empty(self) -> None:
        assert compute_soundex("") == ""

    def test_compute_metaphone(self) -> None:
        assert len(compute_metaphone("Smith")) >= 1
        assert len(compute_metaphone("Knight")) >= 1

    def test_compute_metaphone_empty(self) -> None:
        assert compute_metaphone("") == ""

    def test_compute_double_metaphone(self) -> None:
        primary, alternate = compute_double_metaphone("Smith")
        assert len(primary) >= 1
        assert len(alternate) >= 1

    def test_compute_double_metaphone_empty(self) -> None:
        assert compute_double_metaphone("") == ("", "")

    def test_transcribe_to_arpabet(self) -> None:
        result = transcribe_to_arpabet("hello world")
        assert "HH" in result
        assert "OW" in result

    def test_transcribe_to_arpabet_empty(self) -> None:
        assert transcribe_to_arpabet("") == ""

    def test_transcribe_to_ipa(self) -> None:
        result = transcribe_to_ipa("hello")
        assert len(result) > 0

    def test_transcribe_to_ipa_empty(self) -> None:
        assert transcribe_to_ipa("") == ""

    def test_ipa_vowel_data_integrity(self) -> None:
        assert len(IPA_VOWELS) >= 20
        for entry in IPA_VOWELS:
            assert "ipa" in entry
            assert "arpabet" in entry

    def test_ipa_consonant_data_integrity(self) -> None:
        assert len(IPA_CONSONANTS) >= 20
        for entry in IPA_CONSONANTS:
            assert "ipa" in entry
            assert "xsampa" in entry

    def test_arpabet_to_ipa_roundtrip(self) -> None:
        assert len(ARPABET_TO_IPA) >= 30
        assert len(IPA_TO_ARPABET) >= 30
        assert IPA_TO_ARPABET[ARPABET_TO_IPA["AA"]] == "AA"

    def test_cmu_dict_subset(self) -> None:
        assert len(CMU_DICT_SUBSET) >= 10
        assert "HELLO" in CMU_DICT_SUBSET
        assert "UNICODE" in CMU_DICT_SUBSET

    def test_soundex_mapping_coverage(self) -> None:
        # Standard Soundex maps the 18 consonants into six phonetic groups;
        # vowels and H/W/Y are intentionally separators rather than groups.
        assert len(SOUNDEX_MAPPING) == 18
        assert set(SOUNDEX_MAPPING.values()) == {"1", "2", "3", "4", "5", "6"}


# ═══════════════════════════════════════════════════════════════════════════════
# font_data — format detection, metrics, system stacks
# ═══════════════════════════════════════════════════════════════════════════════


class TestFontDataWorkflows:
    """NF.9: font format identification and metric extraction."""

    def test_identify_font_format_ttf(self) -> None:
        assert identify_font_format(b"\x00\x01\x00\x00") == "ttf"

    def test_identify_font_format_otf(self) -> None:
        assert identify_font_format(b"OTTO") == "otf"

    def test_identify_font_format_woff(self) -> None:
        assert identify_font_format(b"wOFF") == "woff"

    def test_identify_font_format_woff2(self) -> None:
        assert identify_font_format(b"wOF2") == "woff2"

    def test_identify_font_format_ttc(self) -> None:
        assert identify_font_format(b"ttcf") == "ttc"

    def test_identify_font_format_unknown(self) -> None:
        assert identify_font_format(b"XXXX") == "unknown"

    def test_identify_font_format_short_header(self) -> None:
        assert identify_font_format(b"\x00") == "unknown"

    def test_is_web_font_format_missing(self) -> None:
        assert not is_web_font_format("/nonexistent/font.woff")

    def test_system_font_stacks_populated(self) -> None:
        for os_key in ("macos", "windows", "linux", "ios", "android"):
            assert os_key in SYSTEM_FONT_STACKS
            for style in ("sans-serif", "serif", "monospace"):
                assert style in SYSTEM_FONT_STACKS[os_key]

    def test_variable_font_axes(self) -> None:
        assert "wght" in VARIABLE_FONT_AXES
        assert VARIABLE_FONT_AXES["wght"]["default"] == 400.0
        assert "opsz" in VARIABLE_FONT_AXES

    def test_font_format_specs(self) -> None:
        for fmt_key in ("ttf", "otf", "woff", "woff2", "ttc"):
            assert fmt_key in FONT_FORMAT_SPECS
            assert "magic" in FONT_FORMAT_SPECS[fmt_key]
            assert "mime" in FONT_FORMAT_SPECS[fmt_key]

    def test_opentype_required_tables(self) -> None:
        assert len(OPENTYPE_REQUIRED_TABLES) >= 6
        assert "cmap" in OPENTYPE_REQUIRED_TABLES
        assert "head" in OPENTYPE_REQUIRED_TABLES

    def test_list_font_tables_invalid_path(self) -> None:
        assert list_font_tables("/nonexistent/font.ttf") == []

    def test_get_font_metrics_invalid_path(self) -> None:
        result = get_font_metrics("/nonexistent/font.ttf")
        assert "error" in result

    def test_has_variable_axes_invalid(self) -> None:
        assert not has_variable_axes("/nonexistent/font.ttf")

    def test_has_kerning_invalid(self) -> None:
        assert not has_kerning("/nonexistent/font.ttf")


# ═══════════════════════════════════════════════════════════════════════════════
# charset_map — encoding tables
# ═══════════════════════════════════════════════════════════════════════════════


class TestCharsetMapWorkflows:
    """NF.9: charset_map data integrity and BOM round-trips."""

    def test_bom_signatures_roundtrip(self) -> None:
        for encoding, bom in BOM_SIGNATURES.items():
            assert BOM_BY_SEQUENCE[bom] == encoding

    def test_all_encodings_not_empty(self) -> None:
        assert len(ALL_ENCODINGS) >= 40

    def test_utf_encodings_structure(self) -> None:
        assert len(UTF_ENCODINGS) >= 5
        for enc in UTF_ENCODINGS:
            assert "name" in enc
            assert "category" in enc
            assert "max_bytes_per_char" in enc

    def test_single_byte_encodings_coverage(self) -> None:
        names = {e["name"] for e in SINGLE_BYTE_ENCODINGS}
        assert "ISO-8859-1" in names
        assert "ISO-8859-15" in names

    def test_windows_code_pages_coverage(self) -> None:
        names = {e["name"] for e in WINDOWS_CODE_PAGES}
        assert "windows-1252" in names
        assert "windows-1251" in names

    def test_cjk_encodings_coverage(self) -> None:
        names = {e["name"] for e in CJK_ENCODINGS}
        assert "Shift_JIS" in names
        assert "GB18030" in names
        assert "Big5" in names

    def test_cyrillic_encodings_coverage(self) -> None:
        names = {e["name"] for e in CYRILLIC_ENCODINGS}
        assert "KOI8-R" in names
        assert "KOI8-U" in names

    def test_ibm_code_pages_coverage(self) -> None:
        assert len(IBM_CODE_PAGES) >= 10

    def test_chardet_thresholds_monotonic(self) -> None:
        thresholds = CHARDET_CONFIDENCE_THRESHOLDS
        assert thresholds["entry"] < thresholds["usable"]
        assert thresholds["usable"] < thresholds["reliable"]
        assert thresholds["reliable"] < thresholds["trusted"]

    def test_mojibake_signatures_nonempty(self) -> None:
        assert len(MOJIBAKE_SIGNATURES) >= 4
        for _name, sigs in MOJIBAKE_SIGNATURES.items():
            assert len(sigs) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# unicode_data — planes, surrogates, version history
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnicodeDataWorkflows:
    """NF.9: unicode_data properties and surrogate handling."""

    def test_plane_of_bmp(self) -> None:
        assert plane_of(0x0041) == "BMP"  # 'A'

    def test_plane_of_smp(self) -> None:
        assert plane_of(0x1F600) == "SMP"  # emoji

    def test_plane_of_unassigned(self) -> None:
        assert plane_of(0x200000) == "UNASSIGNED"

    def test_is_surrogate_high(self) -> None:
        assert is_surrogate(0xD800)
        assert is_high_surrogate(0xD800)
        assert not is_low_surrogate(0xD800)

    def test_is_surrogate_low(self) -> None:
        assert is_surrogate(0xDC00)
        assert not is_high_surrogate(0xDC00)
        assert is_low_surrogate(0xDC00)

    def test_is_surrogate_not(self) -> None:
        assert not is_surrogate(0x0041)

    def test_surrogates_to_codepoint(self) -> None:
        cp = surrogates_to_codepoint(0xD800, 0xDC00)
        assert cp >= 0x10000

    def test_unicode_version_history(self) -> None:
        assert len(UNICODE_VERSION_HISTORY) >= 20
        latest = UNICODE_VERSION_HISTORY[-1]
        assert latest["year"] >= 2024
        assert latest["characters"] >= 140000

    def test_unicode_plane_names(self) -> None:
        assert "BMP" in UNICODE_PLANE_NAMES
        assert "SMP" in UNICODE_PLANE_NAMES
        assert "SPUA-A" in UNICODE_PLANE_NAMES

    def test_unicode_category_names(self) -> None:
        assert "Lu" in UNICODE_CATEGORY_NAMES
        assert "Ll" in UNICODE_CATEGORY_NAMES
        assert "Nd" in UNICODE_CATEGORY_NAMES
        assert len(UNICODE_CATEGORY_NAMES) >= 25

    def test_unicode_block_names(self) -> None:
        assert len(UNICODE_BLOCK_NAMES) >= 100
        assert UNICODE_BLOCK_NAMES[(0x0000, 0x007F)] == "Basic Latin"
        assert UNICODE_BLOCK_NAMES[(0x0400, 0x04FF)] == "Cyrillic"


# ═══════════════════════════════════════════════════════════════════════════════
# cross-module workflow (polyglot → homoglyph → corpus)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossModuleWorkflow:
    """NF.9: end-to-end: detect → scan → analyze a polyglot directory."""

    def test_full_workflow_detect_scan_analyze(self, polyglot_tmpdir: str) -> None:
        detection = detect_languages_in_directory(polyglot_tmpdir)
        assert detection["total_files"] >= 8

        homoglyph_results = cross_language_homoglyph_scan(
            [str(Path(polyglot_tmpdir) / "main.py"), str(Path(polyglot_tmpdir) / "utils.js")]
        )
        assert isinstance(homoglyph_results, list)

        encoding_report = encoding_conflict_report(
            [str(Path(polyglot_tmpdir) / f) for f in os.listdir(polyglot_tmpdir)
             if Path(polyglot_tmpdir, f).is_file()]
        )
        assert "encodings_present" in encoding_report

        all_files = [
            str(Path(polyglot_tmpdir, f)) for f in os.listdir(polyglot_tmpdir)
            if Path(polyglot_tmpdir, f).suffix
        ]
        analyzer = CorpusAnalyzer(all_files)
        dist = analyzer.language_distribution()
        freq = analyzer.frequency_analysis(top_n=10)
        assert freq["total_chars"] > 0
        assert len(dist) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# locale reference-data integrity
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocaleReferenceData:
    """NF.9: reference data integrity for locale tables."""

    def test_locale_formats_count(self) -> None:
        assert len(LOCALE_FORMATS) >= 8

    def test_common_currencies_count(self) -> None:
        assert len(COMMON_CURRENCIES) >= 10
        assert "USD" in COMMON_CURRENCIES
        assert COMMON_CURRENCIES["USD"]["symbol"] == "$"

    def test_iso_639_coverage(self) -> None:
        assert len(ISO_639_1_TO_NAME) >= 100
        assert ISO_639_1_TO_NAME["en"] == "English"
        assert ISO_639_1_TO_NAME["ja"] == "Japanese"

    def test_iso_3166_coverage(self) -> None:
        assert len(ISO_3166_TO_NAME) >= 50
        assert ISO_3166_TO_NAME["US"] == "United States"

    def test_iso_15924_coverage(self) -> None:
        assert len(ISO_15924_TO_NAME) >= 100
        assert ISO_15924_TO_NAME["Latn"] == "Latin"
        assert ISO_15924_TO_NAME["Cyrl"] == "Cyrillic"

    def test_rtl_scripts(self) -> None:
        assert "Arab" in RTL_SCRIPTS
        assert "Hebr" in RTL_SCRIPTS

    def test_first_day_of_week(self) -> None:
        assert CLDR_FIRST_DAY_OF_WEEK["US"] == 0  # Sunday
        assert CLDR_FIRST_DAY_OF_WEEK["DE"] == 1  # Monday
        assert CLDR_FIRST_DAY_OF_WEEK["SA"] == 5  # Friday

    def test_measurement_systems(self) -> None:
        assert CLDR_MEASUREMENT_SYSTEMS["US"] == "US"
        assert CLDR_MEASUREMENT_SYSTEMS["DE"] == "metric"
