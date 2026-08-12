"""Deep tests for language/unicode_data.py — plane lookup, surrogate math, data integrity."""

from __future__ import annotations

import pytest

from general_ludd.language.unicode_data import (
    SURROGATE_HIGH_END,
    SURROGATE_HIGH_START,
    SURROGATE_LOW_END,
    SURROGATE_LOW_START,
    UNICODE_BLOCK_NAMES,
    UNICODE_CATEGORY_NAMES,
    UNICODE_PLANE_NAMES,
    UNICODE_VERSION_HISTORY,
    UTF8_HEADER_BYTES,
    UTF8_MAX_1,
    UTF8_MAX_2,
    UTF8_MAX_3,
    UTF8_MAX_4,
    UnicodeNormalizationForm,
    UnicodePenalty,
    UnicodePlane,
    UnicodeScript,
    UTFEncoding,
    is_high_surrogate,
    is_low_surrogate,
    is_surrogate,
    plane_of,
    surrogates_to_codepoint,
)

# ── Type literal constraints ─────────────────────────────────────────────


class TestUnicodePenaltyLiteral:
    def test_all_30_categories_present(self):
        expected = {
            "Lu",
            "Ll",
            "Lt",
            "Lm",
            "Lo",
            "Mn",
            "Mc",
            "Me",
            "Nd",
            "Nl",
            "No",
            "Pc",
            "Pd",
            "Ps",
            "Pe",
            "Pi",
            "Pf",
            "Po",
            "Sm",
            "Sc",
            "Sk",
            "So",
            "Zs",
            "Zl",
            "Zp",
            "Cc",
            "Cf",
            "Cs",
            "Co",
            "Cn",
        }
        assert set(UnicodePenalty.__args__) == expected


class TestUnicodeNormalizationFormLiteral:
    def test_four_forms_present(self):
        assert set(UnicodeNormalizationForm.__args__) == {"NFC", "NFD", "NFKC", "NFKD"}


class TestUnicodePlaneLiteral:
    def test_nine_planes_present(self):
        assert set(UnicodePlane.__args__) == {
            "BMP",
            "SMP",
            "SIP",
            "TIP",
            "SSP",
            "SPUA-A",
            "SPUA-B",
            "PUA",
            "UNASSIGNED",
        }


class TestUTFEncodingLiteral:
    def test_five_encodings_present(self):
        assert set(UTFEncoding.__args__) == {"UTF-8", "UTF-16-LE", "UTF-16-BE", "UTF-32-LE", "UTF-32-BE"}


class TestUnicodeScriptLiteral:
    """Script names cover ISO 15924. Only spot-check: not all 168 are enumerated."""

    def test_common_scripts_included(self):
        args = set(UnicodeScript.__args__)
        assert "Latin" in args
        assert "Greek" in args
        assert "Cyrillic" in args
        assert "Han" in args
        assert "Arabic" in args
        assert "Hiragana" in args
        assert "Katakana" in args
        assert "Hangul" in args

    def test_common_and_unknown_and_inherited(self):
        args = set(UnicodeScript.__args__)
        assert "Common" in args
        assert "Unknown" in args
        assert "Inherited" in args


# ── UNICODE_PLANE_NAMES ──────────────────────────────────────────────────


class TestUnicodePlaneNames:
    def test_all_plane_keys_have_name(self):
        all_planes: set[UnicodePlane] = {"BMP", "SMP", "SIP", "TIP", "SSP", "SPUA-A", "SPUA-B", "PUA", "UNASSIGNED"}
        assert set(UNICODE_PLANE_NAMES.keys()) == all_planes

    def test_all_names_non_empty(self):
        for plane, name in UNICODE_PLANE_NAMES.items():
            assert isinstance(name, str) and len(name) > 0, f"{plane} name empty"


# ── UNICODE_VERSION_HISTORY ──────────────────────────────────────────────


class TestUnicodeVersionHistory:
    def test_non_empty_list(self):
        assert len(UNICODE_VERSION_HISTORY) > 0

    def test_every_entry_has_required_keys(self):
        required = {"version", "year", "characters", "scripts"}
        for entry in UNICODE_VERSION_HISTORY:
            missing = required - set(entry.keys())
            assert not missing, f"{entry.get('version')} missing {missing}"

    def test_versions_are_strings(self):
        for entry in UNICODE_VERSION_HISTORY:
            assert isinstance(entry["version"], str)

    def test_years_are_positive_ints(self):
        for entry in UNICODE_VERSION_HISTORY:
            assert isinstance(entry["year"], int) and entry["year"] >= 1991

    def test_characters_positive(self):
        for entry in UNICODE_VERSION_HISTORY:
            assert isinstance(entry["characters"], int) and entry["characters"] > 0

    def test_characters_monotonically_increasing(self):
        chars = [e["characters"] for e in UNICODE_VERSION_HISTORY]
        for i in range(1, len(chars)):
            assert chars[i] >= chars[i - 1], f"Character count decreased: {chars[i - 1]} → {chars[i]}"

    def test_years_monotonically_increasing(self):
        years = [e["year"] for e in UNICODE_VERSION_HISTORY]
        for i in range(1, len(years)):
            assert years[i] >= years[i - 1]

    def test_scripts_positive(self):
        for entry in UNICODE_VERSION_HISTORY:
            assert isinstance(entry["scripts"], int) and entry["scripts"] > 0

    def test_first_is_1_0(self):
        assert UNICODE_VERSION_HISTORY[0]["version"] == "1.0"

    def test_last_is_16_0(self):
        assert UNICODE_VERSION_HISTORY[-1]["version"] == "16.0"

    def test_version_16_0_count_plausible(self):
        last = UNICODE_VERSION_HISTORY[-1]
        assert 150000 <= last["characters"] <= 200000


# ── UNICODE_CATEGORY_NAMES ────────────────────────────────────────────────


class TestUnicodeCategoryNames:
    def test_all_30_categories_present(self):
        expected = set(UnicodePenalty.__args__)
        assert expected == {
            "Lu",
            "Ll",
            "Lt",
            "Lm",
            "Lo",
            "Mn",
            "Mc",
            "Me",
            "Nd",
            "Nl",
            "No",
            "Pc",
            "Pd",
            "Ps",
            "Pe",
            "Pi",
            "Pf",
            "Po",
            "Sm",
            "Sc",
            "Sk",
            "So",
            "Zs",
            "Zl",
            "Zp",
            "Cc",
            "Cf",
            "Cs",
            "Co",
            "Cn",
        }

    def test_keys_match_penalty_literal(self):
        penalty_set = set(UnicodePenalty.__args__)
        assert set(UNICODE_CATEGORY_NAMES.keys()) == penalty_set

    def test_all_descriptions_non_empty(self):
        for cat, desc in UNICODE_CATEGORY_NAMES.items():
            assert isinstance(desc, str) and len(desc) > 0, f"Empty desc for {cat}"

    def test_descriptions_follow_pattern(self):
        for cat, desc in UNICODE_CATEGORY_NAMES.items():
            assert ", " in desc, f"{cat} desc '{desc}' missing ', ' separator"


# ── UNICODE_BLOCK_NAMES ──────────────────────────────────────────────────


class TestUnicodeBlockNames:
    def test_non_empty(self):
        assert len(UNICODE_BLOCK_NAMES) > 100

    def test_all_keys_are_two_tuple_of_ints(self):
        for key in UNICODE_BLOCK_NAMES:
            assert isinstance(key, tuple) and len(key) == 2
            assert isinstance(key[0], int) and isinstance(key[1], int)

    def test_all_ranges_low_le_high(self):
        for lo, hi in UNICODE_BLOCK_NAMES:
            assert lo <= hi, f"Range {lo:#x}-{hi:#x} is inverted"

    def test_all_names_non_empty_str(self):
        for name in UNICODE_BLOCK_NAMES.values():
            assert isinstance(name, str) and len(name) > 0

    def test_ranges_do_not_overlap(self):
        sorted_ranges = sorted(UNICODE_BLOCK_NAMES.keys())
        for i in range(len(sorted_ranges) - 1):
            prev_lo, prev_hi = sorted_ranges[i]
            next_lo, next_hi = sorted_ranges[i + 1]
            assert prev_hi < next_lo, f"Overlap: [{prev_lo:#x}-{prev_hi:#x}] and [{next_lo:#x}-{next_hi:#x}]"

    def test_basic_latin_at_zero(self):
        assert UNICODE_BLOCK_NAMES.get((0x0000, 0x007F)) == "Basic Latin"

    def test_latin_1_supplement_adjacent(self):
        assert (0x0080, 0x00FF) in UNICODE_BLOCK_NAMES

    def test_cjk_unified_ideographs_present(self):
        assert (0x4E00, 0x9FFF) in UNICODE_BLOCK_NAMES

    def test_private_use_area_present(self):
        assert (0xE000, 0xF8FF) in UNICODE_BLOCK_NAMES

    def test_high_and_low_surrogates_present(self):
        assert (0xD800, 0xDB7F) in UNICODE_BLOCK_NAMES
        assert (0xDC00, 0xDFFF) in UNICODE_BLOCK_NAMES

    def test_supplementary_extensions_present(self):
        assert (0x20000, 0x2A6DF) in UNICODE_BLOCK_NAMES  # CJK Ext B
        assert (0xF0000, 0xFFFFF) in UNICODE_BLOCK_NAMES  # SPUA-A
        assert (0x100000, 0x10FFFF) in UNICODE_BLOCK_NAMES  # SPUA-B


# ── UTF8_HEADER_BYTES ────────────────────────────────────────────────────


class TestUTF8HeaderBytes:
    def test_all_ascii_are_1_byte(self):
        for byte in range(0x00, 0x80):
            assert UTF8_HEADER_BYTES[byte] == 1, f"ASCII {byte:#04x} should be 1-byte"

    def test_two_byte_leaders_c0_to_df(self):
        for byte in range(0xC0, 0xE0):
            assert UTF8_HEADER_BYTES[byte] == 2, f"{byte:#04x} should be 2-byte leader"

    def test_three_byte_leaders_e0_to_ef(self):
        for byte in range(0xE0, 0xF0):
            assert UTF8_HEADER_BYTES[byte] == 3, f"{byte:#04x} should be 3-byte leader"

    def test_four_byte_leaders_f0_to_f4(self):
        for byte in range(0xF0, 0xF5):
            assert UTF8_HEADER_BYTES[byte] == 4, f"{byte:#04x} should be 4-byte leader"

    def test_no_5_byte_sequences(self):
        for length in UTF8_HEADER_BYTES.values():
            assert length in {1, 2, 3, 4}

    def test_continuation_bytes_not_present(self):
        for byte in range(0x80, 0xC0):
            assert byte not in UTF8_HEADER_BYTES

    def test_overlong_0xc0_0xc1_are_2_byte(self):
        assert UTF8_HEADER_BYTES.get(0xC0) == 2
        assert UTF8_HEADER_BYTES.get(0xC1) == 2

    def test_f5_and_above_omitted(self):
        for byte in range(0xF5, 0x100):
            assert byte not in UTF8_HEADER_BYTES


# ── plane_of ──────────────────────────────────────────────────────────────


class TestPlaneOf:
    def test_bmp_basic_latin(self):
        assert plane_of(0x0041) == "BMP"  # 'A'

    def test_bmp_cjk(self):
        assert plane_of(0x4E00) == "BMP"  # CJK

    def test_pua(self):
        assert plane_of(0xE000) == "PUA"
        assert plane_of(0xF8FF) == "PUA"

    def test_pua_interior(self):
        assert plane_of(0xE500) == "PUA"

    def test_smp_linear_b(self):
        assert plane_of(0x10000) == "SMP"
        assert plane_of(0x1FFFF) == "SMP"

    def test_sip_cjk_ext_b(self):
        assert plane_of(0x20000) == "SIP"
        assert plane_of(0x2FFFF) == "SIP"

    def test_tip(self):
        assert plane_of(0x30000) == "TIP"
        assert plane_of(0x3FFFF) == "TIP"

    def test_ssp_tags_block(self):
        assert plane_of(0xE0000) == "SSP"
        assert plane_of(0xE0FFF) == "SSP"

    def test_spua_a(self):
        assert plane_of(0xF0000) == "SPUA-A"
        assert plane_of(0xFFFFF) == "SPUA-A"

    def test_spua_b(self):
        assert plane_of(0x100000) == "SPUA-B"
        assert plane_of(0x10FFFF) == "SPUA-B"

    def test_negative_codepoint(self):
        assert plane_of(-1) == "UNASSIGNED"

    def test_beyond_max_codepoint(self):
        assert plane_of(0x110000) == "UNASSIGNED"
        assert plane_of(0xFFFFFF) == "UNASSIGNED"

    @pytest.mark.parametrize(
        "cp,expected",
        [
            (0x0000, "BMP"),
            (0x007F, "BMP"),
            (0x00FF, "BMP"),
            (0xFFFF, "BMP"),
            (0xDFFF, "BMP"),
            (0x0FFF, "BMP"),
        ],
    )
    def test_bmp_boundary_cases(self, cp, expected):
        assert plane_of(cp) == expected

    def test_spua_a_before_spua_b_gap(self):
        assert plane_of(0x100000) == "SPUA-B"
        assert plane_of(0xFFFFF) == "SPUA-A"
        # gap between SPUA-A and SPUA-B
        assert plane_of(0x100001) == "SPUA-B"

    def test_plane_of_known_unicode_org_reference_points(self):
        assert plane_of(0x00E9) == "BMP"  # é (U+00E9, BMP)
        assert plane_of(0x1F600) == "SMP"  # 😀 (U+1F600, SMP)
        assert plane_of(0x1F4A9) == "SMP"  # 💩 (U+1F4A9, SMP)
        assert plane_of(0x1D11E) == "SMP"  # 𝄞 musical symbol G clef (U+1D11E)
        assert plane_of(0x24B62) == "SIP"  # 𤭢 CJK Ext B (U+24B62)


# ── Surrogate helpers ──────────────────────────────────────────────────────


class TestSurrogateHelpers:
    def test_high_surrogate_range(self):
        assert is_high_surrogate(SURROGATE_HIGH_START)
        assert is_high_surrogate(SURROGATE_HIGH_END)
        assert is_high_surrogate(0xD950)
        assert not is_high_surrogate(SURROGATE_HIGH_START - 1)
        assert not is_high_surrogate(SURROGATE_HIGH_END + 1)

    def test_low_surrogate_range(self):
        assert is_low_surrogate(SURROGATE_LOW_START)
        assert is_low_surrogate(SURROGATE_LOW_END)
        assert is_low_surrogate(0xDD00)
        assert not is_low_surrogate(SURROGATE_LOW_START - 1)
        assert not is_low_surrogate(SURROGATE_LOW_END + 1)

    def test_is_surrogate_covers_both(self):
        assert is_surrogate(0xD800)
        assert is_surrogate(0xDC00)
        assert is_surrogate(0xDFFF)
        assert is_surrogate(0xDA00)
        assert is_surrogate(0xDE00)
        assert not is_surrogate(0xD7FF)
        assert not is_surrogate(0xE000)
        assert not is_surrogate(0x0041)

    def test_surrogate_constants_are_in_order(self):
        assert SURROGATE_HIGH_START < SURROGATE_HIGH_END
        assert SURROGATE_LOW_START < SURROGATE_LOW_END
        assert SURROGATE_HIGH_END < SURROGATE_LOW_START


class TestSurrogatesToCodepoint:
    def test_ascii_a_utf16(self):
        cp = surrogates_to_codepoint(0xD800, 0xDC00)
        assert cp == 0x10000

    def test_known_high_supplementary(self):
        cp = surrogates_to_codepoint(0xD83D, 0xDE02)
        assert cp == 0x1F602  # 😂

    def test_emoji_surrogate_pair(self):
        cp = surrogates_to_codepoint(0xD83D, 0xDE00)
        assert cp == 0x1F600  # 😀

    def test_max_supplementary(self):
        cp = surrogates_to_codepoint(0xDBFF, 0xDFFF)
        assert cp == 0x10FFFF

    def test_min_supplementary(self):
        cp = surrogates_to_codepoint(0xD800, 0xDC00)
        assert cp == 0x10000

    def test_roundtrip_linear(self):
        for cp in range(0x10000, 0x110000, 0x1000):
            offset = cp - 0x10000
            high = SURROGATE_HIGH_START + (offset >> 10)
            low = SURROGATE_LOW_START + (offset & 0x3FF)
            assert surrogates_to_codepoint(high, low) == cp


# ── UTF8 constants ───────────────────────────────────────────────────────


class TestUTF8Constants:
    def test_max_values_are_monotonic(self):
        assert 0 < UTF8_MAX_1 < UTF8_MAX_2 < UTF8_MAX_3 < UTF8_MAX_4

    def test_max_1_is_7f(self):
        assert UTF8_MAX_1 == 0x7F

    def test_max_4_is_max_codepoint(self):
        assert UTF8_MAX_4 == 0x10FFFF


# ── plane_of exhaustive boundary proximity ───────────────────────────────


class TestPlaneOfBoundaryProximity:
    def test_plane_boundary_00ffff_to_010000(self):
        assert plane_of(0xFFFF) == "BMP"
        assert plane_of(0x10000) == "SMP"

    def test_plane_boundary_1ffff_to_20000(self):
        assert plane_of(0x1FFFF) == "SMP"
        assert plane_of(0x20000) == "SIP"

    def test_plane_boundary_2ffff_to_30000(self):
        assert plane_of(0x2FFFF) == "SIP"
        assert plane_of(0x30000) == "TIP"

    def test_plane_boundary_3ffff_to_40000(self):
        assert plane_of(0x3FFFF) == "TIP"
        assert plane_of(0x40000) == "UNASSIGNED"

    def test_plane_boundary_dffff_to_e0000(self):
        assert plane_of(0xDFFFF) == "UNASSIGNED"
        assert plane_of(0xE0000) == "SSP"
