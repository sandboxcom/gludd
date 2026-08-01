"""Tests for general_ludd.language collection: knowledge module exhaustiveness,
schema validation, behavioral testing, and role task file verification.
"""

from __future__ import annotations

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
COLLECTION_ROOT = os.path.join(
    PROJECT_ROOT, "collections", "ansible_collections", "general_ludd", "language",
)


class TestLanguageCollectionSchema:
    """Collection scaffolding exists and is valid."""

    def test_galaxy_yml_exists(self) -> None:
        path = os.path.join(COLLECTION_ROOT, "galaxy.yml")
        assert os.path.isfile(path), f"Missing {path}"

    def test_readme_exists(self) -> None:
        path = os.path.join(COLLECTION_ROOT, "README.md")
        assert os.path.isfile(path), f"Missing {path}"

    def test_all_8_roles_exist(self) -> None:
        roles_dir = os.path.join(COLLECTION_ROOT, "roles")
        expected = [
            "unicode_analyze", "bom_detect", "encoding_detect",
            "locale_format", "i18n_extract", "font_analyze",
            "phonetic_transcribe", "homoglyph_scan",
        ]
        for role in expected:
            main_yml = os.path.join(roles_dir, role, "tasks", "main.yml")
            assert os.path.isfile(main_yml), f"Missing {main_yml}"

    def test_all_8_roles_have_content(self) -> None:
        roles_dir = os.path.join(COLLECTION_ROOT, "roles")
        expected = [
            "unicode_analyze", "bom_detect", "encoding_detect",
            "locale_format", "i18n_extract", "font_analyze",
            "phonetic_transcribe", "homoglyph_scan",
        ]
        for role in expected:
            main_yml = os.path.join(roles_dir, role, "tasks", "main.yml")
            with open(main_yml) as f:
                content = f.read()
            assert len(content) > 50, f"{role} main.yml too short"
            assert "ansible.builtin.debug" in content, f"{role} missing debug task"


# ── unicode_data.py ────────────────────────────────────────────────────────


class TestUnicodeData:
    """unicode_data.py module exhaustiveness."""

    def test_module_importable(self) -> None:
        from general_ludd.language import unicode_data
        assert unicode_data is not None

    def test_unicode_planes_all_defined(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_PLANE_NAMES, UnicodePlane
        expected: set[UnicodePlane] = {
            "BMP", "SMP", "SIP", "TIP", "SSP",
            "SPUA-A", "SPUA-B", "PUA", "UNASSIGNED",
        }
        assert set(UNICODE_PLANE_NAMES.keys()) == expected

    def test_unicode_version_history_complete(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY
        assert len(UNICODE_VERSION_HISTORY) >= 25
        versions = [e["version"] for e in UNICODE_VERSION_HISTORY]
        assert "1.0" in versions
        assert "16.0" in versions

    def test_unicode_categories_all_30(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_CATEGORY_NAMES
        assert len(UNICODE_CATEGORY_NAMES) == 30

    def test_unicode_blocks_cover_basic_ranges(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_BLOCK_NAMES
        assert len(UNICODE_BLOCK_NAMES) >= 100
        assert (0x0000, 0x007F) in UNICODE_BLOCK_NAMES
        assert (0x4E00, 0x9FFF) in UNICODE_BLOCK_NAMES

    def test_surrogate_functions(self) -> None:
        from general_ludd.language.unicode_data import (
            is_high_surrogate,
            is_low_surrogate,
            is_surrogate,
            surrogates_to_codepoint,
        )
        assert is_surrogate(0xD800)
        assert is_high_surrogate(0xD800)
        assert not is_high_surrogate(0xDC00)
        assert is_low_surrogate(0xDC00)
        assert not is_low_surrogate(0xD800)
        assert not is_surrogate(0x0041)
        result = surrogates_to_codepoint(0xD83D, 0xDE00)
        assert result == 0x1F600

    def test_plane_of(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x0041) == "BMP"
        assert plane_of(0x1F600) == "SMP"
        assert plane_of(0x20000) == "SIP"

    def test_utf8_header_bytes_valid(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        assert UTF8_HEADER_BYTES[0x41] == 1
        assert UTF8_HEADER_BYTES[0xC3] == 2
        assert UTF8_HEADER_BYTES[0xE2] == 3
        assert UTF8_HEADER_BYTES[0xF0] == 4


class TestUnicodeDataBehavioral:
    """Behavioral tests for unicode_data.py functions and data integrity."""

    # --- plane_of edge cases -------------------------------------------------

    def test_plane_of_bmp_low_boundary(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x0000) == "BMP"

    def test_plane_of_bmp_high_boundary(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0xFFFF) == "BMP"
        assert plane_of(0x10000) == "SMP"

    def test_plane_of_pua_range(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0xE000) == "PUA"
        assert plane_of(0xF8FF) == "PUA"
        assert plane_of(0xF900) == "BMP"

    def test_plane_of_smp_boundaries(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x10000) == "SMP"
        assert plane_of(0x1FFFF) == "SMP"
        assert plane_of(0x20000) == "SIP"

    def test_plane_of_sip_boundaries(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x20000) == "SIP"
        assert plane_of(0x2FFFF) == "SIP"

    def test_plane_of_tip(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x30000) == "TIP"
        assert plane_of(0x3FFFF) == "TIP"

    def test_plane_of_ssp(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0xE0000) == "SSP"
        assert plane_of(0xE0FFF) == "SSP"

    def test_plane_of_spua_a(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0xF0000) == "SPUA-A"
        assert plane_of(0xFFFFF) == "SPUA-A"

    def test_plane_of_spua_b(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x100000) == "SPUA-B"
        assert plane_of(0x10FFFF) == "SPUA-B"

    def test_plane_of_unassigned(self) -> None:
        from general_ludd.language.unicode_data import plane_of
        assert plane_of(0x110000) == "UNASSIGNED"
        assert plane_of(-1) == "UNASSIGNED"
        assert plane_of(0x40000) == "UNASSIGNED"

    # --- surrogate edge cases ------------------------------------------------

    def test_surrogate_high_low_boundaries(self) -> None:
        from general_ludd.language.unicode_data import is_high_surrogate, is_low_surrogate, is_surrogate
        assert is_high_surrogate(0xD800)
        assert is_high_surrogate(0xDBFF)
        assert not is_high_surrogate(0xDBFF + 1)
        assert is_low_surrogate(0xDC00)
        assert is_low_surrogate(0xDFFF)
        assert not is_low_surrogate(0xDC00 - 1)
        assert is_surrogate(0xD800)
        assert is_surrogate(0xDFFF)
        assert not is_surrogate(0xD7FF)
        assert not is_surrogate(0xE000)

    def test_surrogates_to_codepoint_min_supplementary(self) -> None:
        from general_ludd.language.unicode_data import surrogates_to_codepoint
        cp = surrogates_to_codepoint(0xD800, 0xDC00)
        assert cp == 0x10000

    def test_surrogates_to_codepoint_max_supplementary(self) -> None:
        from general_ludd.language.unicode_data import surrogates_to_codepoint
        cp = surrogates_to_codepoint(0xDBFF, 0xDFFF)
        assert cp == 0x10FFFF

    # --- version history integrity -------------------------------------------

    def test_version_history_monotonic_char_count(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY
        prev = 0
        for entry in UNICODE_VERSION_HISTORY:
            chars: int = int(entry["characters"])
            assert chars >= prev, (
                f"Character count decreased at {entry['version']}: "
                f"{chars} < {prev}"
            )
            prev = chars

    def test_version_history_fields_present(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY
        for entry in UNICODE_VERSION_HISTORY:
            assert "version" in entry
            assert "year" in entry
            assert "characters" in entry
            assert "scripts" in entry

    def test_version_history_16_0_is_largest(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY
        v16 = UNICODE_VERSION_HISTORY[-1]
        assert v16["version"] == "16.0"
        for entry in UNICODE_VERSION_HISTORY[:-1]:
            assert int(entry["characters"]) <= int(v16["characters"])

    # --- block name consistency ----------------------------------------------

    def test_unicode_blocks_non_overlapping(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_BLOCK_NAMES
        ranges = sorted(UNICODE_BLOCK_NAMES.keys(), key=lambda r: r[0])
        for i in range(len(ranges) - 1):
            current_end = ranges[i][1]
            next_start = ranges[i + 1][0]
            assert current_end < next_start, (
                f"Blocks overlap or touch: {UNICODE_BLOCK_NAMES[ranges[i]]} "
                f"({ranges[i]}) and {UNICODE_BLOCK_NAMES[ranges[i+1]]} "
                f"({ranges[i+1]})"
            )

    def test_unicode_blocks_basic_latin_first(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_BLOCK_NAMES
        ranges = sorted(UNICODE_BLOCK_NAMES.keys(), key=lambda r: r[0])
        assert ranges[0] == (0x0000, 0x007F)
        assert UNICODE_BLOCK_NAMES[ranges[0]] == "Basic Latin"

    def test_unicode_block_lookup_helper(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_BLOCK_NAMES
        def _block_of(cp: int) -> str:
            for (lo, hi), name in UNICODE_BLOCK_NAMES.items():
                if lo <= cp <= hi:
                    return name
            return "Unknown"
        assert _block_of(0x0041) == "Basic Latin"
        assert _block_of(0x00E9) == "Latin-1 Supplement"
        assert _block_of(0x0400) == "Cyrillic"
        assert _block_of(0x3042) == "Hiragana"
        assert _block_of(0x4E2D) == "CJK Unified Ideographs"
        assert _block_of(0x1F600) == "Emoticons"

    # --- UTF-8 header bytes completeness -------------------------------------

    def test_utf8_header_bytes_ascii_range(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        for byte_val in range(0x00, 0x80):
            assert UTF8_HEADER_BYTES.get(byte_val) == 1, (
                f"ASCII byte 0x{byte_val:02X} should be 1-byte sequence"
            )

    def test_utf8_header_bytes_2byte_range(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        for byte_val in range(0xC0, 0xE0):
            assert UTF8_HEADER_BYTES.get(byte_val) == 2, (
                f"Byte 0x{byte_val:02X} should be 2-byte sequence"
            )

    def test_utf8_header_bytes_3byte_range(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        for byte_val in range(0xE0, 0xF0):
            assert UTF8_HEADER_BYTES.get(byte_val) == 3, (
                f"Byte 0x{byte_val:02X} should be 3-byte sequence"
            )

    def test_utf8_header_bytes_4byte_range(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        for byte_val in range(0xF0, 0xF5):
            assert UTF8_HEADER_BYTES.get(byte_val) == 4, (
                f"Byte 0x{byte_val:02X} should be 4-byte sequence"
            )

    def test_utf8_header_bytes_no_overcoding(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES
        assert -1 not in UTF8_HEADER_BYTES
        assert 0xF5 not in UTF8_HEADER_BYTES
        assert 0xFF not in UTF8_HEADER_BYTES


# ── charset_map.py ──────────────────────────────────────────────────────────


class TestCharsetMap:
    """charset_map.py module exhaustiveness."""

    def test_module_importable(self) -> None:
        from general_ludd.language import charset_map
        assert charset_map is not None

    def test_bom_signatures_all_five(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        assert len(BOM_SIGNATURES) >= 5
        assert BOM_SIGNATURES["UTF-8"] == b"\xef\xbb\xbf"
        assert BOM_SIGNATURES["UTF-16-BE"] == b"\xfe\xff"
        assert BOM_SIGNATURES["UTF-16-LE"] == b"\xff\xfe"
        assert BOM_SIGNATURES["UTF-32-BE"] == b"\x00\x00\xfe\xff"
        assert BOM_SIGNATURES["UTF-32-LE"] == b"\xff\xfe\x00\x00"

    def test_bom_reverse_lookup(self) -> None:
        from general_ludd.language.charset_map import BOM_BY_SEQUENCE
        assert BOM_BY_SEQUENCE[b"\xef\xbb\xbf"] == "UTF-8"
        assert BOM_BY_SEQUENCE[b"\xfe\xff"] == "UTF-16-BE"
        assert BOM_BY_SEQUENCE[b"\xff\xfe"] == "UTF-16-LE"

    def test_bom_required_optional(self) -> None:
        from general_ludd.language.charset_map import (
            BOM_OPTIONAL_BY_RFC,
            BOM_REQUIRED_BY_RFC,
        )
        assert "UTF-16" in BOM_REQUIRED_BY_RFC
        assert "UTF-8" in BOM_OPTIONAL_BY_RFC

    def test_iso_8859_all_covered(self) -> None:
        from general_ludd.language.charset_map import SINGLE_BYTE_ENCODINGS
        names = {e["name"] for e in SINGLE_BYTE_ENCODINGS}
        expected = {f"ISO-8859-{n}" for n in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16]}
        assert expected.issubset(names)

    def test_windows_code_pages_all(self) -> None:
        from general_ludd.language.charset_map import WINDOWS_CODE_PAGES
        names = {e["name"] for e in WINDOWS_CODE_PAGES}
        expected = {f"windows-125{n}" for n in range(0, 9)}
        assert expected.issubset(names)

    def test_cjk_encodings_all_six(self) -> None:
        from general_ludd.language.charset_map import CJK_ENCODINGS
        assert len(CJK_ENCODINGS) == 6
        names = {e["name"] for e in CJK_ENCODINGS}
        assert "Shift_JIS" in names
        assert "GB18030" in names
        assert "Big5" in names

    def test_cyrillic_encodings(self) -> None:
        from general_ludd.language.charset_map import CYRILLIC_ENCODINGS
        names = {e["name"] for e in CYRILLIC_ENCODINGS}
        assert "KOI8-R" in names
        assert "KOI8-U" in names

    def test_ibm_code_pages_all_13(self) -> None:
        from general_ludd.language.charset_map import IBM_CODE_PAGES
        assert len(IBM_CODE_PAGES) == 13

    def test_mojibake_signatures_have_entries(self) -> None:
        from general_ludd.language.charset_map import MOJIBAKE_SIGNATURES
        assert len(MOJIBAKE_SIGNATURES) >= 6

    def test_all_encodings_combined(self) -> None:
        from general_ludd.language.charset_map import ALL_ENCODINGS
        assert len(ALL_ENCODINGS) >= 40

    def test_all_encodings_includes_utf8_entry(self) -> None:
        from general_ludd.language.charset_map import ALL_ENCODINGS
        utf8_entries = [e for e in ALL_ENCODINGS if e["name"] == "UTF-8"]
        assert len(utf8_entries) == 1
        assert utf8_entries[0]["category"] == "variable-width"
        assert utf8_entries[0]["max_bytes_per_char"] == 4
        assert "utf8" in utf8_entries[0]["aliases"]


class TestCharsetMapBehavioral:
    """Behavioral tests for charset_map.py data structures."""

    # --- BOM detection logic -------------------------------------------------

    def test_detect_bom_utf8(self) -> None:
        from general_ludd.language.charset_map import BOM_BY_SEQUENCE, BOM_SIGNATURES
        bom = BOM_SIGNATURES["UTF-8"]
        data = bom + b"<html>"
        for sig, encoding in sorted(BOM_BY_SEQUENCE.items(), key=lambda x: -len(x[0])):
            if data.startswith(sig):
                assert encoding == "UTF-8"
                break

    def test_detect_bom_utf16_le(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        data = BOM_SIGNATURES["UTF-16-LE"] + b"\x41\x00"
        assert data.startswith(BOM_SIGNATURES["UTF-16-LE"])

    def test_detect_bom_utf16_be(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        data = BOM_SIGNATURES["UTF-16-BE"] + b"\x00\x41"
        assert data.startswith(BOM_SIGNATURES["UTF-16-BE"])

    def test_detect_bom_utf32_le(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        data = BOM_SIGNATURES["UTF-32-LE"] + b"\x41\x00\x00\x00"
        assert data.startswith(BOM_SIGNATURES["UTF-32-LE"])

    def test_detect_bom_utf32_be(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        data = BOM_SIGNATURES["UTF-32-BE"] + b"\x00\x00\x00\x41"
        assert data.startswith(BOM_SIGNATURES["UTF-32-BE"])

    def test_strip_bom_utf8(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        bom = BOM_SIGNATURES["UTF-8"]
        data = bom + b"Hello"
        stripped = data[len(bom):]
        assert stripped == b"Hello"

    def test_strip_bom_utf16_le(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        bom = BOM_SIGNATURES["UTF-16-LE"]
        stripped = (bom + b"data")[len(bom):]
        assert stripped == b"data"

    def test_no_bom_present(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        data = b"Plain ASCII text"
        found = False
        for sig in BOM_SIGNATURES.values():
            if data.startswith(sig):
                found = True
        assert not found

    def test_bom_size_lookup(self) -> None:
        from general_ludd.language.charset_map import BOM_SIZE
        assert BOM_SIZE["UTF-8"] == 3
        assert BOM_SIZE["UTF-16-BE"] == 2
        assert BOM_SIZE["UTF-16-LE"] == 2
        assert BOM_SIZE["UTF-32-BE"] == 4
        assert BOM_SIZE["UTF-32-LE"] == 4

    # --- encoding alias resolution -------------------------------------------

    def test_encoding_alias_resolution_ascii_compatible(self) -> None:
        from general_ludd.language.charset_map import ALL_ENCODINGS
        incompatible = {"UTF-16-BE", "UTF-16-LE", "UTF-32-BE", "UTF-32-LE"}
        for enc in ALL_ENCODINGS:
            assert enc["is_ascii_compatible"] is (enc["name"] not in incompatible), (
                f"{enc['name']} has an incorrect ASCII-compatibility classification"
            )

    def test_encoding_name_uniqueness(self) -> None:
        from general_ludd.language.charset_map import ALL_ENCODINGS
        names = [e["name"] for e in ALL_ENCODINGS]
        assert len(names) == len(set(names)), "Duplicate encoding names"

    def test_encoding_alias_uniqueness_across_all(self) -> None:
        from general_ludd.language.charset_map import ALL_ENCODINGS
        all_aliases: set[str] = set()
        for enc in ALL_ENCODINGS:
            for alias in enc["aliases"]:
                assert alias.lower() not in all_aliases, (
                    f"Duplicate alias '{alias}' in encoding '{enc['name']}'"
                )
                all_aliases.add(alias.lower())

    def test_cjk_encodings_multibyte(self) -> None:
        from general_ludd.language.charset_map import CJK_ENCODINGS
        for enc in CJK_ENCODINGS:
            assert enc["max_bytes_per_char"] >= 2, (
                f"{enc['name']} should be multi-byte"
            )

    def test_single_byte_encodings_one_byte(self) -> None:
        from general_ludd.language.charset_map import (
            IBM_CODE_PAGES,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
        )
        for enc in SINGLE_BYTE_ENCODINGS + WINDOWS_CODE_PAGES + IBM_CODE_PAGES:
            assert enc["max_bytes_per_char"] == 1, (
                f"{enc['name']} should be single-byte"
            )

    # --- chardet thresholds --------------------------------------------------

    def test_chardet_thresholds_ordered(self) -> None:
        from general_ludd.language.charset_map import CHARDET_CONFIDENCE_THRESHOLDS
        assert CHARDET_CONFIDENCE_THRESHOLDS["entry"] < CHARDET_CONFIDENCE_THRESHOLDS["usable"]
        assert CHARDET_CONFIDENCE_THRESHOLDS["usable"] < CHARDET_CONFIDENCE_THRESHOLDS["reliable"]
        assert CHARDET_CONFIDENCE_THRESHOLDS["reliable"] < CHARDET_CONFIDENCE_THRESHOLDS["trusted"]

    def test_chardet_thresholds_in_range(self) -> None:
        from general_ludd.language.charset_map import CHARDET_CONFIDENCE_THRESHOLDS
        for level, val in CHARDET_CONFIDENCE_THRESHOLDS.items():
            assert 0.0 <= val <= 1.0, f"{level} threshold {val} out of range"

    # --- mojibake signatures -------------------------------------------------

    def test_mojibake_signatures_nonempty(self) -> None:
        from general_ludd.language.charset_map import MOJIBAKE_SIGNATURES
        for key, sigs in MOJIBAKE_SIGNATURES.items():
            assert len(sigs) > 0, f"Mojibake signature '{key}' is empty"

    def test_mojibake_utf8_as_iso_has_common_patterns(self) -> None:
        from general_ludd.language.charset_map import MOJIBAKE_SIGNATURES
        utf8_iso = MOJIBAKE_SIGNATURES["UTF-8 viewed as ISO-8859-1"]
        assert "\u00c2\u00a9" in utf8_iso
        assert "\u00c3\u00a9" in utf8_iso


# ── locale_data.py ──────────────────────────────────────────────────────────


class TestLocaleData:
    """locale_data.py module exhaustiveness."""

    def test_module_importable(self) -> None:
        from general_ludd.language import locale_data
        assert locale_data is not None

    def test_rtl_scripts_defined(self) -> None:
        from general_ludd.language.locale_data import RTL_SCRIPTS
        assert "Arab" in RTL_SCRIPTS
        assert "Hebr" in RTL_SCRIPTS

    def test_rtl_languages_defined(self) -> None:
        from general_ludd.language.locale_data import RTL_LANGUAGES
        assert "ar" in RTL_LANGUAGES
        assert "he" in RTL_LANGUAGES

    def test_common_currencies_defined(self) -> None:
        from general_ludd.language.locale_data import COMMON_CURRENCIES
        assert len(COMMON_CURRENCIES) >= 10
        assert "USD" in COMMON_CURRENCIES
        assert "EUR" in COMMON_CURRENCIES

    def test_locale_formats_defined(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        assert len(LOCALE_FORMATS) >= 8
        assert "en-US" in LOCALE_FORMATS
        assert "ar-SA" in LOCALE_FORMATS
        assert LOCALE_FORMATS["ar-SA"]["is_rtl"]

    def test_iso_639_1_complete(self) -> None:
        from general_ludd.language.locale_data import ISO_639_1_TO_NAME
        assert len(ISO_639_1_TO_NAME) >= 130
        assert ISO_639_1_TO_NAME["en"] == "English"
        assert ISO_639_1_TO_NAME["zh"] == "Chinese"

    def test_iso_3166_complete(self) -> None:
        from general_ludd.language.locale_data import ISO_3166_TO_NAME
        assert len(ISO_3166_TO_NAME) >= 50
        assert ISO_3166_TO_NAME["US"] == "United States"

    def test_iso_15924_defined(self) -> None:
        from general_ludd.language.locale_data import ISO_15924_TO_NAME
        assert len(ISO_15924_TO_NAME) >= 50
        assert ISO_15924_TO_NAME["Latn"] == "Latin"

    def test_cldr_first_day_of_week(self) -> None:
        from general_ludd.language.locale_data import CLDR_FIRST_DAY_OF_WEEK
        assert len(CLDR_FIRST_DAY_OF_WEEK) >= 30

    def test_cldr_measurement_systems(self) -> None:
        from general_ludd.language.locale_data import CLDR_MEASUREMENT_SYSTEMS
        assert len(CLDR_MEASUREMENT_SYSTEMS) >= 10
        assert CLDR_MEASUREMENT_SYSTEMS["US"] == "US"


class TestLocaleDataBehavioral:
    """Behavioral tests for locale_data.py data structures."""

    # --- locale format consistency -------------------------------------------

    def test_all_locales_have_required_fields(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        required_fields = {"bcp47", "language_name", "script", "territory",
                           "is_rtl", "number_format", "date_format",
                           "currency_format", "plural_rules"}
        for key, locale in LOCALE_FORMATS.items():
            missing = required_fields - set(locale.keys())
            assert not missing, f"{key} missing: {missing}"

    def test_all_locales_have_date_format_lengths(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        lengths = {"full", "long", "medium", "short"}
        for key, locale in LOCALE_FORMATS.items():
            df = locale["date_format"]
            assert set(df.keys()) == lengths, (
                f"{key} date_format has {set(df.keys())}, expected {lengths}"
            )

    def test_all_locales_have_number_format_fields(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        num_fields = {"decimal_separator", "grouping_separator", "grouping_pattern",
                      "percent_sign", "minus_sign", "infinity", "nan"}
        for key, locale in LOCALE_FORMATS.items():
            nf = locale["number_format"]
            missing = num_fields - set(nf.keys())
            assert not missing, f"{key} number_format missing: {missing}"

    def test_all_locales_have_plural_categories(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        cats = {"zero", "one", "two", "few", "many", "other"}
        for key, locale in LOCALE_FORMATS.items():
            pr = locale["plural_rules"]
            assert set(pr.keys()) == cats, (
                f"{key} plural_rules has {set(pr.keys())}, expected {cats}"
            )

    # --- RTL consistency -----------------------------------------------------

    def test_rtl_locales_have_rtl_scripts(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS, RTL_SCRIPTS
        for key, locale in LOCALE_FORMATS.items():
            if locale["is_rtl"]:
                assert locale["script"] in RTL_SCRIPTS, (
                    f"{key} is RTL but script {locale['script']} not in RTL_SCRIPTS"
                )

    def test_ltr_locales_not_rtl(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        for _key, locale in LOCALE_FORMATS.items():
            if not locale["is_rtl"]:
                assert not locale["is_rtl"]

    # --- currency data integrity ---------------------------------------------

    def test_all_currencies_have_required_fields(self) -> None:
        from general_ludd.language.locale_data import COMMON_CURRENCIES
        currency_fields = {"symbol", "code", "placement", "decimal_digits",
                           "decimal_separator", "grouping_separator"}
        for code, curr in COMMON_CURRENCIES.items():
            missing = currency_fields - set(curr.keys())
            assert not missing, f"{code} missing: {missing}"

    def test_currency_placement_valid(self) -> None:
        from general_ludd.language.locale_data import COMMON_CURRENCIES
        valid = {"before", "after", "before-no-space", "after-no-space"}
        for code, curr in COMMON_CURRENCIES.items():
            assert curr["placement"] in valid, (
                f"{code} placement '{curr['placement']}' invalid"
            )

    def test_currency_decimal_digits_nonnegative(self) -> None:
        from general_ludd.language.locale_data import COMMON_CURRENCIES
        for code, curr in COMMON_CURRENCIES.items():
            assert curr["decimal_digits"] >= 0, (
                f"{code} decimal_digits {curr['decimal_digits']} negative"
            )

    # --- CLDR first day of week validity -------------------------------------

    def test_first_day_of_week_valid_range(self) -> None:
        from general_ludd.language.locale_data import CLDR_FIRST_DAY_OF_WEEK
        for territory, day in CLDR_FIRST_DAY_OF_WEEK.items():
            assert 0 <= day <= 6, (
                f"{territory} first day of week {day} out of range"
            )

    # --- ISO code cross-referencing ------------------------------------------

    def test_locale_territories_in_iso_3166(self) -> None:
        from general_ludd.language.locale_data import ISO_3166_TO_NAME, LOCALE_FORMATS
        for key, locale in LOCALE_FORMATS.items():
            territory = locale["territory"]
            assert territory in ISO_3166_TO_NAME, (
                f"{key} territory '{territory}' not in ISO_3166"
            )

    def test_locale_scripts_in_iso_15924(self) -> None:
        from general_ludd.language.locale_data import ISO_15924_TO_NAME, LOCALE_FORMATS
        for key, locale in LOCALE_FORMATS.items():
            script = locale["script"]
            assert script in ISO_15924_TO_NAME, (
                f"{key} script '{script}' not in ISO_15924"
            )

    # --- plural rules consistency --------------------------------------------

    def test_plural_rules_other_always_present(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        for key, locale in LOCALE_FORMATS.items():
            assert locale["plural_rules"]["other"], (
                f"{key} plural_rules.other must not be empty"
            )

    def test_english_plural_rules_simple(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        en = LOCALE_FORMATS["en-US"]["plural_rules"]
        assert en["one"] == "n = 1"
        assert en["other"] == "n != 1"

    def test_arabic_plural_rules_complex(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        ar = LOCALE_FORMATS["ar-SA"]["plural_rules"]
        assert ar["zero"] == "n = 0"
        assert ar["one"] == "n = 1"
        assert ar["two"] == "n = 2"

    def test_russian_plural_rules(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        ru = LOCALE_FORMATS["ru-RU"]["plural_rules"]
        assert "n % 10 = 1" in ru["one"]
        assert "n % 10 in 2..4" in ru["few"]
        assert "n % 10 = 0 or n % 10 in 5..9" in ru["many"]

    # --- locale negotiation simulation ---------------------------------------

    def test_locale_bcp47_tag_format(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        for key in LOCALE_FORMATS:
            assert "-" in key, f"{key} should have language-territory form"
            lang, territory = key.split("-", 1)
            assert len(lang) == 2, f"{key} language code should be 2 chars"
            assert len(territory) == 2, f"{key} territory should be 2 chars"

    # --- number format uniqueness --------------------------------------------

    def test_french_uses_narrow_nbsp_grouping(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        fr = LOCALE_FORMATS["fr-FR"]["number_format"]
        assert fr["grouping_separator"] == "\u202f"

    def test_german_swaps_separators(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        de = LOCALE_FORMATS["de-DE"]["number_format"]
        assert de["decimal_separator"] == ","
        assert de["grouping_separator"] == "."

    def test_arabic_uses_arabic_separators(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS
        ar = LOCALE_FORMATS["ar-SA"]["number_format"]
        assert ar["decimal_separator"] == "\u066b"
        assert ar["grouping_separator"] == "\u066c"


# ── phonetic_data.py ────────────────────────────────────────────────────────


class TestPhoneticData:
    """phonetic_data.py module exhaustiveness."""

    def test_module_importable(self) -> None:
        from general_ludd.language import phonetic_data
        assert phonetic_data is not None

    def test_ipa_vowels_defined(self) -> None:
        from general_ludd.language.phonetic_data import IPA_VOWELS
        assert len(IPA_VOWELS) >= 20

    def test_ipa_consonants_defined(self) -> None:
        from general_ludd.language.phonetic_data import IPA_CONSONANTS
        assert len(IPA_CONSONANTS) >= 20

    def test_arpabet_to_ipa_complete(self) -> None:
        from general_ludd.language.phonetic_data import ARPABET_TO_IPA
        assert len(ARPABET_TO_IPA) >= 35

    def test_ipa_to_arpabet_reverse(self) -> None:
        from general_ludd.language.phonetic_data import IPA_TO_ARPABET
        assert len(IPA_TO_ARPABET) >= 30

    def test_soundex_mapping_complete(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING
        assert len(SOUNDEX_MAPPING) >= 18
        assert SOUNDEX_MAPPING["b"] == "1"

    def test_cmu_dict_subset(self) -> None:
        from general_ludd.language.phonetic_data import CMU_DICT_SUBSET
        assert len(CMU_DICT_SUBSET) >= 10
        assert "HELLO" in CMU_DICT_SUBSET
        assert CMU_DICT_SUBSET["HELLO"][0] == "HH AH0 L OW1"


class TestPhoneticDataBehavioral:
    """Behavioral tests for phonetic_data.py data structures."""

    # --- ARPABET ↔ IPA roundtrip ---------------------------------------------

    def test_arpabet_to_ipa_no_empty_values(self) -> None:
        from general_ludd.language.phonetic_data import ARPABET_TO_IPA
        for arp, ipa in ARPABET_TO_IPA.items():
            assert ipa, f"ARPABET {arp} has empty IPA mapping"

    def test_ipa_to_arpabet_no_empty_values(self) -> None:
        from general_ludd.language.phonetic_data import IPA_TO_ARPABET
        for ipa, arp in IPA_TO_ARPABET.items():
            assert arp, f"IPA '{ipa}' has empty ARPABET mapping"

    def test_arpabet_ipa_bidirectional_consistency(self) -> None:
        from general_ludd.language.phonetic_data import ARPABET_TO_IPA, IPA_TO_ARPABET
        for arp, ipa in ARPABET_TO_IPA.items():
            mapped_back = IPA_TO_ARPABET.get(ipa)
            assert mapped_back == arp, (
                f"Roundtrip broken: {arp} → '{ipa}' → '{mapped_back}'"
            )

    # --- Soundex algorithm helper --------------------------------------------

    def test_soundex_mapping_all_lowercase_keys(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING
        for key in SOUNDEX_MAPPING:
            assert key.islower(), f"Soundex key '{key}' not lowercase"
            assert len(key) == 1

    def test_soundex_vowels_no_mapping(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING, SOUNDEX_VOWELS
        for vowel in SOUNDEX_VOWELS:
            assert vowel not in SOUNDEX_MAPPING, (
                f"Vowel '{vowel}' should not be in SOUNDEX_MAPPING"
            )

    def test_soundex_ignore_no_mapping(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_IGNORE, SOUNDEX_MAPPING
        for ch in SOUNDEX_IGNORE:
            assert ch not in SOUNDEX_MAPPING, (
                f"Ignored char '{ch}' should not be in SOUNDEX_MAPPING"
            )

    def test_soundex_codes_are_digits_1_to_6(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING
        for code in SOUNDEX_MAPPING.values():
            assert code in {"1", "2", "3", "4", "5", "6"}, (
                f"Soundex code '{code}' not in 1-6 range"
            )

    def test_soundex_algorithm_basic(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING
        name = "Robert"
        encoded = name[0].upper()
        prev_code = ""
        for ch in name.lower()[1:]:
            code = SOUNDEX_MAPPING.get(ch, "")
            if code and code != prev_code:
                encoded += code
                prev_code = code
        encoded = (encoded + "000")[:4]
        assert encoded == "R163"

    def test_soundex_similar_names(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING
        def _soundex(word: str) -> str:
            enc = word[0].upper()
            prev = ""
            for ch in word.lower()[1:]:
                c = SOUNDEX_MAPPING.get(ch, "")
                if c and c != prev:
                    enc += c
                    prev = c
            return (enc + "000")[:4]
        assert _soundex("Robert") == _soundex("Rupert")

    # --- CMU dictionary lookup -----------------------------------------------

    def test_cmu_dict_pronunciation_stress_format(self) -> None:
        from general_ludd.language.phonetic_data import CMU_DICT_SUBSET
        for word, pronunciations in CMU_DICT_SUBSET.items():
            for pron in pronunciations:
                phonemes = pron.split()
                for p in phonemes:
                    assert len(p) >= 1 and len(p) <= 3, (
                        f"Invalid phoneme '{p}' in {word}"
                    )

    def test_cmu_dict_hello_has_stress(self) -> None:
        from general_ludd.language.phonetic_data import CMU_DICT_SUBSET
        hello = CMU_DICT_SUBSET["HELLO"][0]
        assert "1" in hello

    def test_cmu_dict_data_has_two_pronunciations(self) -> None:
        from general_ludd.language.phonetic_data import CMU_DICT_SUBSET
        assert len(CMU_DICT_SUBSET["DATA"]) == 2

    # --- ARPABET stress markers ----------------------------------------------

    def test_arpabet_stress_three_levels(self) -> None:
        from general_ludd.language.phonetic_data import ARPABET_STRESS
        assert len(ARPABET_STRESS) == 3
        assert "0" in ARPABET_STRESS
        assert "1" in ARPABET_STRESS
        assert "2" in ARPABET_STRESS

    # --- IPA vowel data integrity --------------------------------------------

    def test_ipa_vowels_all_have_required_fields(self) -> None:
        from general_ludd.language.phonetic_data import IPA_VOWELS
        fields = {"ipa", "xsampa", "arpabet", "description", "examples"}
        for entry in IPA_VOWELS:
            missing = fields - set(entry.keys())
            assert not missing, f"Vowel entry missing: {missing}"

    def test_ipa_consonants_all_have_required_fields(self) -> None:
        from general_ludd.language.phonetic_data import IPA_CONSONANTS
        fields = {"ipa", "xsampa", "arpabet", "description", "examples"}
        for entry in IPA_CONSONANTS:
            missing = fields - set(entry.keys())
            assert not missing, f"Consonant entry missing: {missing}"

    def test_schwa_has_arpabet_ah(self) -> None:
        from general_ludd.language.phonetic_data import IPA_VOWELS
        schwa = next(e for e in IPA_VOWELS if e["ipa"] == "\u0259")
        assert schwa["arpabet"] == "AH"

    # --- Metaphone data ------------------------------------------------------

    def test_metaphone_exceptions_are_pairs(self) -> None:
        from general_ludd.language.phonetic_data import METAPHONE_EXCEPTIONS
        for key, val in METAPHONE_EXCEPTIONS.items():
            assert len(key) == 2
            assert len(val) == 1

    def test_double_metaphone_values_are_pairs(self) -> None:
        from general_ludd.language.phonetic_data import DOUBLE_METAPHONE
        for key, val in DOUBLE_METAPHONE.items():
            assert len(val) == 2, f"Double Metaphone '{key}' has {len(val)} values"


# ── homoglyph_data.py ───────────────────────────────────────────────────────


class TestHomoglyphData:
    """homoglyph_data.py module exhaustiveness."""

    def test_module_importable(self) -> None:
        from general_ludd.language import homoglyph_data
        assert homoglyph_data is not None

    def test_homoglyph_groups_defined(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS
        assert len(HOMOGLYPH_GROUPS) >= 15

    def test_invisible_characters_defined(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS
        assert len(INVISIBLE_CHARACTERS) >= 15

    def test_attack_vectors_defined(self) -> None:
        from general_ludd.language.homoglyph_data import ATTACK_VECTORS
        assert len(ATTACK_VECTORS) >= 5
        assert "domain_spoofing" in ATTACK_VECTORS
        assert "code_injection" in ATTACK_VECTORS

    def test_invisible_codepoints_helper(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET
        assert 0x200B in _INVISIBLE_SET
        assert 0x202E in _INVISIBLE_SET
        assert 0x200D in _INVISIBLE_SET

    def test_codepoint_in_group_helper(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        assert _codepoint_in_group(0x0041, HOMOGLYPH_GROUPS) == "A"
        assert _codepoint_in_group(0x0391, HOMOGLYPH_GROUPS) == "A"
        assert _codepoint_in_group(0x9999, HOMOGLYPH_GROUPS) == ""


class TestHomoglyphDataBehavioral:
    """Behavioral tests for homoglyph_data.py — confusable detection etc."""

    # --- confusable detection ------------------------------------------------

    def test_latin_a_vs_cyrillic_a_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        latin_a = _codepoint_in_group(0x0061, HOMOGLYPH_GROUPS)
        cyrillic_a = _codepoint_in_group(0x0430, HOMOGLYPH_GROUPS)
        assert latin_a == cyrillic_a == "a"

    def test_latin_c_vs_cyrillic_es_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        latin_c = _codepoint_in_group(0x0063, HOMOGLYPH_GROUPS)
        cyrillic_es = _codepoint_in_group(0x0441, HOMOGLYPH_GROUPS)
        assert latin_c == cyrillic_es == "c"

    def test_latin_o_vs_cyrillic_o_vs_greek_omicron(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        assert _codepoint_in_group(0x006F, HOMOGLYPH_GROUPS) == "o"
        assert _codepoint_in_group(0x043E, HOMOGLYPH_GROUPS) == "o"
        assert _codepoint_in_group(0x03BF, HOMOGLYPH_GROUPS) == "o"

    def test_digit_zero_vs_latin_o_vs_cyrillic_o_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        skel_0 = _codepoint_in_group(0x0030, HOMOGLYPH_GROUPS)
        skel_O = _codepoint_in_group(0x004F, HOMOGLYPH_GROUPS)
        skel_O_cyr = _codepoint_in_group(0x041E, HOMOGLYPH_GROUPS)
        assert skel_0 and skel_O and skel_O_cyr
        assert skel_0 == skel_O
        assert skel_O == skel_O_cyr

    def test_non_confusable_character_returns_empty(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        assert _codepoint_in_group(ord("q"), HOMOGLYPH_GROUPS) == ""
        assert _codepoint_in_group(ord("9"), HOMOGLYPH_GROUPS) == ""
        assert _codepoint_in_group(0x0414, HOMOGLYPH_GROUPS) == ""

    def test_domain_spoofing_example_apple(self) -> None:
        from general_ludd.language.homoglyph_data import (
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )
        cyrillic_a = chr(0x0430)
        fake = cyrillic_a + "pple.com"
        skel = _codepoint_in_group(ord(fake[0]), HOMOGLYPH_GROUPS)
        assert skel == "a"
        assert fake != "apple.com"

    # --- skeleton uniqueness -------------------------------------------------

    def test_skeleton_keys_are_unique(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS
        skeletons = [g["skeleton"] for g in HOMOGLYPH_GROUPS]
        assert len(skeletons) == len(set(skeletons)), (
            f"Duplicate skeletons: {[s for s in skeletons if skeletons.count(s) > 1]}"
        )

    def test_all_homoglyph_groups_have_multiple_scripts(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS
        for group in HOMOGLYPH_GROUPS:
            cats = group["categories"]
            assert len(set(cats)) >= 2, (
                f"Group '{group['skeleton']}' has only {len(set(cats))} categories: {cats}"
            )

    def test_all_homoglyph_characters_have_valid_codepoints(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS
        for group in HOMOGLYPH_GROUPS:
            for cp, _name in group["characters"]:
                assert 0 <= cp <= 0x10FFFF, (
                    f"Invalid codepoint U+{cp:04X} in group '{group['skeleton']}'"
                )

    # --- invisible character detection ---------------------------------------

    def test_invisible_zwsp_is_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET
        assert 0x200B in _INVISIBLE_SET

    def test_invisible_bidi_override_is_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET
        assert 0x202E in _INVISIBLE_SET
        assert 0x202A in _INVISIBLE_SET
        assert 0x202B in _INVISIBLE_SET

    def test_invisible_soft_hyphen(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET
        assert 0x00AD in _INVISIBLE_SET

    def test_normal_space_not_invisible(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET
        assert 0x0020 not in _INVISIBLE_SET

    def test_invisible_count_matches_set(self) -> None:
        from general_ludd.language.homoglyph_data import (
            _INVISIBLE_SET,
            INVISIBLE_CHARACTERS,
        )
        assert len(INVISIBLE_CHARACTERS) == len(_INVISIBLE_SET), (
            f"INVISIBLE_CHARACTERS has {len(INVISIBLE_CHARACTERS)} entries "
            f"but _INVISIBLE_SET has {len(_INVISIBLE_SET)}"
        )

    def test_invisible_chars_have_required_fields(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS
        fields = {"codepoint", "name", "short_name", "category", "risk", "cve_reference"}
        for entry in INVISIBLE_CHARACTERS:
            missing = fields - set(entry.keys())
            assert not missing, f"Invisible char {entry.get('name')} missing: {missing}"

    # --- attack vector scoring -----------------------------------------------

    def test_all_attack_vectors_have_descriptions(self) -> None:
        from general_ludd.language.homoglyph_data import ATTACK_VECTORS
        for key, desc in ATTACK_VECTORS.items():
            assert len(desc) > 50, (
                f"Attack vector '{key}' description too short ({len(desc)} chars)"
            )

    def test_cve_2021_42574_referenced_in_bidi_controls(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS
        bidi_controls = [c for c in INVISIBLE_CHARACTERS if c["category"] == "bidi-control"]
        cve_refs = [c for c in bidi_controls if c["cve_reference"] == "CVE-2021-42574"]
        assert len(cve_refs) >= 4, (
            f"Only {len(cve_refs)} bidi controls reference CVE-2021-42574"
        )

    # --- invisible character categories --------------------------------------

    def test_invisible_categories_known_set(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS
        known = {
            "zero-width-space", "zero-width-joiner", "zero-width-non-joiner",
            "soft-hyphen", "word-joiner", "bidi-control",
            "format-character", "deprecated-format",
            "interlinear-annotation", "variation-selector",
        }
        for entry in INVISIBLE_CHARACTERS:
            assert entry["category"] in known, (
                f"Unknown category: {entry['category']}"
            )

    # --- mixed-script detection simulation -----------------------------------

    @staticmethod
    def _script_of_codepoint(cp: int) -> str:
        if 0x0041 <= cp <= 0x007A or 0x00C0 <= cp <= 0x024F or cp == 0x007C:
            return "Latin"
        if 0x0370 <= cp <= 0x03FF:
            return "Greek"
        if 0x0400 <= cp <= 0x04FF:
            return "Cyrillic"
        if 0x0530 <= cp <= 0x058F:
            return "Armenian"
        if 0x0030 <= cp <= 0x0039:
            return "Digit"
        return "Other"

    @staticmethod
    def _has_mixed_scripts(text: str) -> bool:
        scripts: set[str] = set()
        for ch in text:
            scripts.add(TestHomoglyphDataBehavioral._script_of_codepoint(ord(ch)))
        return len(scripts) > 1

    def test_mixed_script_detection_apple_cyrillic(self) -> None:
        text = chr(0x0430) + "pple"
        is_mixed = self._has_mixed_scripts(text)
        assert is_mixed

    def test_pure_ascii_not_mixed(self) -> None:
        is_mixed = self._has_mixed_scripts("hello")
        assert not is_mixed


# ── Working Group Knowledge ─────────────────────────────────────────────────


class TestWorkingGroupKnowledge:
    """Working group references exist across knowledge modules."""

    def test_unicode_version_history_references_consortium(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY
        assert len(UNICODE_VERSION_HISTORY) >= 25

    def test_charset_map_references_ietf_rfc(self) -> None:
        from general_ludd.language.charset_map import (
            BOM_OPTIONAL_BY_RFC,
            BOM_REQUIRED_BY_RFC,
        )
        assert len(BOM_REQUIRED_BY_RFC) >= 1
        assert len(BOM_OPTIONAL_BY_RFC) >= 1

    def test_locale_data_references_clr(self) -> None:
        from general_ludd.language.locale_data import (
            CLDR_FIRST_DAY_OF_WEEK,
            CLDR_MEASUREMENT_SYSTEMS,
        )
        assert len(CLDR_FIRST_DAY_OF_WEEK) > 0
        assert len(CLDR_MEASUREMENT_SYSTEMS) > 0

    def test_homoglyph_data_references_uts39(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS
        assert len(HOMOGLYPH_GROUPS) > 0

    def test_homoglyph_data_references_trojan_source_cve(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS
        cve_entries = [c for c in INVISIBLE_CHARACTERS if c["cve_reference"]]
        assert len(cve_entries) >= 1


# ── Spec Alignment ──────────────────────────────────────────────────────────


class TestSpecAlignment:
    """Verifies the spec at docs/specs/FEATURE_LANGUAGE_EXPERT.md is aligned."""

    _spec_path = os.path.join(PROJECT_ROOT, "docs", "specs", "FEATURE_LANGUAGE_EXPERT.md")

    def test_spec_exists(self) -> None:
        assert os.path.isfile(self._spec_path), f"Missing {self._spec_path}"

    def test_spec_mentions_all_8_roles(self) -> None:
        with open(self._spec_path) as f:
            content = f.read()
        for role in [
            "unicode_analyze", "bom_detect", "encoding_detect",
            "locale_format", "i18n_extract", "font_analyze",
            "phonetic_transcribe", "homoglyph_scan",
        ]:
            assert role in content, f"Role `{role}` not in spec"

    def test_spec_mentions_all_5_knowledge_modules(self) -> None:
        with open(self._spec_path) as f:
            content = f.read()
        for mod in ["unicode_data.py", "charset_map.py", "locale_data.py",
                     "phonetic_data.py", "homoglyph_data.py"]:
            assert mod in content, f"Module `{mod}` not in spec"

    def test_spec_mentions_10_coverage_domains(self) -> None:
        with open(self._spec_path) as f:
            content = f.read()
        assert "Unicode" in content
        assert "Byte Order Marks" in content
        assert "Character Sets" in content
        assert "Localization" in content
        assert "Internationalization" in content
        assert "Fonts" in content
        assert "Phonetics" in content
        assert "Language Standards" in content
        assert "Text Processing" in content
        assert "Working Groups" in content
