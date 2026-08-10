"""Tests for ``src/general_ludd/language/unicode_data.py``.

Covers: codepoint validation, category lookup, block ranges, edge cases,
plane determination, surrogate pair handling, UTF-8 header byte lookup,
and Unicode version history integrity.
"""

from __future__ import annotations

from general_ludd.language import unicode_data as ud

# ── Type exports ────────────────────────────────────────────────────────────


class TestTypeExports:
    def test_unicode_plane_type_value_union(self) -> None:
        assert ud.UnicodePlane  # Literal type exists

    def test_unicode_penalty_type_value_union(self) -> None:
        assert ud.UnicodePenalty  # Literal type exists

    def test_unicode_normalization_form_type(self) -> None:
        assert ud.UnicodeNormalizationForm  # Literal type exists

    def test_utf_encoding_type(self) -> None:
        assert ud.UTFEncoding  # Literal type exists

    def test_unicode_script_type(self) -> None:
        assert ud.UnicodeScript  # Literal type exists

    def test_grapheme_break_property_typeddict(self) -> None:
        assert ud._GraphemeBreakProperty  # TypedDict exists


# ── UNICODE_PLANE_NAMES ─────────────────────────────────────────────────────


class TestPlaneNames:
    def test_all_nine_planes_have_names(self) -> None:
        assert len(ud.UNICODE_PLANE_NAMES) == 9

    def test_bmp_has_correct_description(self) -> None:
        assert "BMP" in ud.UNICODE_PLANE_NAMES
        assert "0000-FFFF" in ud.UNICODE_PLANE_NAMES["BMP"]

    def test_smp_has_correct_description(self) -> None:
        assert "SMP" in ud.UNICODE_PLANE_NAMES
        assert "10000-1FFFF" in ud.UNICODE_PLANE_NAMES["SMP"]

    def test_sip_has_correct_description(self) -> None:
        assert "SIP" in ud.UNICODE_PLANE_NAMES
        assert "20000-2FFFF" in ud.UNICODE_PLANE_NAMES["SIP"]

    def test_tip_has_correct_description(self) -> None:
        assert "TIP" in ud.UNICODE_PLANE_NAMES
        assert "30000-3FFFF" in ud.UNICODE_PLANE_NAMES["TIP"]

    def test_spua_a_has_correct_description(self) -> None:
        assert "SPUA-A" in ud.UNICODE_PLANE_NAMES
        assert "F0000-FFFFF" in ud.UNICODE_PLANE_NAMES["SPUA-A"]

    def test_spua_b_has_correct_description(self) -> None:
        assert "SPUA-B" in ud.UNICODE_PLANE_NAMES
        assert "100000-10FFFF" in ud.UNICODE_PLANE_NAMES["SPUA-B"]

    def test_pua_has_correct_description(self) -> None:
        assert "PUA" in ud.UNICODE_PLANE_NAMES
        assert "E000-F8FF" in ud.UNICODE_PLANE_NAMES["PUA"]

    def test_ssp_has_correct_description(self) -> None:
        assert "SSP" in ud.UNICODE_PLANE_NAMES
        desc = ud.UNICODE_PLANE_NAMES["SSP"]
        assert len(desc) > 0
        assert "E0000" in desc


# ── UNICODE_CATEGORY_NAMES ──────────────────────────────────────────────────


class TestCategoryNames:
    def test_all_major_category_groups_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Lu"].startswith("Letter")
        assert cats["Ll"].startswith("Letter")
        assert cats["Lt"].startswith("Letter")
        assert cats["Lm"].startswith("Letter")
        assert cats["Lo"].startswith("Letter")

    def test_mark_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Mn"].startswith("Mark")
        assert cats["Mc"].startswith("Mark")
        assert cats["Me"].startswith("Mark")

    def test_number_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Nd"].startswith("Number")
        assert cats["Nl"].startswith("Number")
        assert cats["No"].startswith("Number")

    def test_punctuation_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Pc"].startswith("Punctuation")
        assert cats["Pd"].startswith("Punctuation")
        assert cats["Ps"].startswith("Punctuation")
        assert cats["Pe"].startswith("Punctuation")
        assert cats["Pi"].startswith("Punctuation")
        assert cats["Pf"].startswith("Punctuation")
        assert cats["Po"].startswith("Punctuation")

    def test_symbol_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Sm"].startswith("Symbol")
        assert cats["Sc"].startswith("Symbol")
        assert cats["Sk"].startswith("Symbol")
        assert cats["So"].startswith("Symbol")

    def test_separator_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Zs"].startswith("Separator")
        assert cats["Zl"].startswith("Separator")
        assert cats["Zp"].startswith("Separator")

    def test_other_categories_present(self) -> None:
        cats = ud.UNICODE_CATEGORY_NAMES
        assert cats["Cc"].startswith("Other")
        assert cats["Cf"].startswith("Other")
        assert cats["Cs"].startswith("Other")
        assert cats["Co"].startswith("Other")
        assert cats["Cn"].startswith("Other")

    def test_exact_count_30_categories(self) -> None:
        assert len(ud.UNICODE_CATEGORY_NAMES) == 30


# ── UNICODE_VERSION_HISTORY ─────────────────────────────────────────────────


class TestVersionHistory:
    def test_earliest_known_version_is_1_0(self) -> None:
        assert ud.UNICODE_VERSION_HISTORY[0]["version"] == "1.0"
        assert ud.UNICODE_VERSION_HISTORY[0]["year"] == 1991

    def test_latest_known_version_is_16_0(self) -> None:
        last = ud.UNICODE_VERSION_HISTORY[-1]
        assert last["version"] == "16.0"
        assert last["year"] == 2024

    def test_versions_are_monotonically_increasing(self) -> None:
        for i in range(1, len(ud.UNICODE_VERSION_HISTORY)):
            prev = ud.UNICODE_VERSION_HISTORY[i - 1]
            curr = ud.UNICODE_VERSION_HISTORY[i]
            prev_c = prev["characters"]
            curr_c = curr["characters"]
            assert isinstance(prev_c, int) and isinstance(curr_c, int)
            assert curr_c >= prev_c

    def test_scripts_count_grows_over_time(self) -> None:
        first = ud.UNICODE_VERSION_HISTORY[0]["scripts"]
        last = ud.UNICODE_VERSION_HISTORY[-1]["scripts"]
        assert isinstance(first, int) and isinstance(last, int)
        assert last >= first

    def test_unicode_1_1_massive_jump_from_1_0(self) -> None:
        v1_0 = ud.UNICODE_VERSION_HISTORY[0]
        v1_1 = ud.UNICODE_VERSION_HISTORY[1]
        assert v1_0["version"] == "1.0"
        assert v1_1["version"] == "1.1"
        c0 = v1_0["characters"]
        c1 = v1_1["characters"]
        assert isinstance(c0, int) and isinstance(c1, int)
        assert c1 > c0 * 4

    def test_all_entries_have_required_keys(self) -> None:
        for entry in ud.UNICODE_VERSION_HISTORY:
            assert "version" in entry
            assert "year" in entry
            assert "characters" in entry
            assert "scripts" in entry

    def test_years_are_monotonically_increasing(self) -> None:
        for i in range(1, len(ud.UNICODE_VERSION_HISTORY)):
            prev = ud.UNICODE_VERSION_HISTORY[i - 1]
            curr = ud.UNICODE_VERSION_HISTORY[i]
            prev_y = prev["year"]
            curr_y = curr["year"]
            assert isinstance(prev_y, int) and isinstance(curr_y, int)
            assert curr_y >= prev_y


# ── UNICODE_BLOCK_NAMES ─────────────────────────────────────────────────────


class TestBlockNames:
    def test_basic_latin_is_first_block(self) -> None:
        blocks = list(ud.UNICODE_BLOCK_NAMES.keys())
        assert blocks[0] == (0x0000, 0x007F)
        assert ud.UNICODE_BLOCK_NAMES[blocks[0]] == "Basic Latin"

    def test_bmp_common_blocks_present(self) -> None:
        blocks = ud.UNICODE_BLOCK_NAMES
        assert blocks[(0x0370, 0x03FF)] == "Greek and Coptic"
        assert blocks[(0x0400, 0x04FF)] == "Cyrillic"
        assert blocks[(0x0600, 0x06FF)] == "Arabic"
        assert blocks[(0x3040, 0x309F)] == "Hiragana"
        assert blocks[(0x30A0, 0x30FF)] == "Katakana"
        assert blocks[(0xAC00, 0xD7AF)] == "Hangul Syllables"

    def test_cjk_unified_ideographs_block_present(self) -> None:
        assert ud.UNICODE_BLOCK_NAMES[(0x4E00, 0x9FFF)] == "CJK Unified Ideographs"

    def test_private_use_area_block_present(self) -> None:
        assert ud.UNICODE_BLOCK_NAMES[(0xE000, 0xF8FF)] == "Private Use Area"

    def test_surrogate_sub_blocks_present(self) -> None:
        assert ud.UNICODE_BLOCK_NAMES[(0xD800, 0xDB7F)] == "High Surrogates"
        assert ud.UNICODE_BLOCK_NAMES[(0xDC00, 0xDFFF)] == "Low Surrogates"

    def test_supplementary_plane_blocks_present(self) -> None:
        blocks = ud.UNICODE_BLOCK_NAMES
        assert blocks[(0x10000, 0x1007F)] == "Linear B Syllabary"
        assert blocks[(0x1F600, 0x1F64F)] == "Emoticons"
        assert blocks[(0x1F300, 0x1F5FF)] == "Miscellaneous Symbols and Pictographs"
        assert blocks[(0x20000, 0x2A6DF)] == "CJK Unified Ideographs Extension B"

    def test_spua_blocks_present(self) -> None:
        assert ud.UNICODE_BLOCK_NAMES[(0xF0000, 0xFFFFF)] == "Supplementary Private Use Area-A"
        assert ud.UNICODE_BLOCK_NAMES[(0x100000, 0x10FFFF)] == "Supplementary Private Use Area-B"

    def test_block_ranges_are_disjoint_and_ordered(self) -> None:
        ranges = list(ud.UNICODE_BLOCK_NAMES.keys())
        for i in range(1, len(ranges)):
            assert ranges[i][0] >= ranges[i - 1][1], f"Overlap: {ranges[i - 1]} vs {ranges[i]}"

    def test_specials_block_includes_replacement_character(self) -> None:
        assert ud.UNICODE_BLOCK_NAMES[(0xFFF0, 0xFFFF)] == "Specials"
        assert 0xFFFD in range(0xFFF0, 0x10000)


# ── UTF8_HEADER_BYTES ───────────────────────────────────────────────────────


class TestUtf8HeaderBytes:
    def test_ascii_range_all_map_to_one_byte(self) -> None:
        for b in range(0x00, 0x80):
            assert ud.UTF8_HEADER_BYTES[b] == 1, f"Byte 0x{b:02X} should be 1-byte"

    def test_two_byte_leaders_map_to_two(self) -> None:
        for b in range(0xC0, 0xE0):
            assert ud.UTF8_HEADER_BYTES[b] == 2, f"Byte 0x{b:02X} should be 2-byte"

    def test_three_byte_leaders_map_to_three(self) -> None:
        for b in range(0xE0, 0xF0):
            assert ud.UTF8_HEADER_BYTES[b] == 3, f"Byte 0x{b:02X} should be 3-byte"

    def test_four_byte_leaders_map_to_four(self) -> None:
        for b in range(0xF0, 0xF5):
            assert ud.UTF8_HEADER_BYTES[b] == 4, f"Byte 0x{b:02X} should be 4-byte"

    def test_continuation_bytes_not_present(self) -> None:
        for b in range(0x80, 0xC0):
            assert b not in ud.UTF8_HEADER_BYTES, f"Continuation 0x{b:02X} should be absent"

    def test_gap_f5_to_ff_not_present(self) -> None:
        for b in range(0xF5, 0x100):
            assert b not in ud.UTF8_HEADER_BYTES, f"Invalid leader 0x{b:02X} should be absent"

    def test_entry_count_matches_expected(self) -> None:
        expected = 128 + 32 + 16 + 5
        assert len(ud.UTF8_HEADER_BYTES) == expected


# ── Surrogate constants ─────────────────────────────────────────────────────


class TestSurrogateConstants:
    def test_constants_form_contiguous_ranges(self) -> None:
        assert ud.SURROGATE_HIGH_START == 0xD800
        assert ud.SURROGATE_HIGH_END == 0xDBFF
        assert ud.SURROGATE_LOW_START == 0xDC00
        assert ud.SURROGATE_LOW_END == 0xDFFF

    def test_surrogate_ranges_are_contiguous(self) -> None:
        assert ud.SURROGATE_HIGH_END + 1 == ud.SURROGATE_LOW_START

    def test_high_surrogate_range_size(self) -> None:
        assert ud.SURROGATE_HIGH_END - ud.SURROGATE_HIGH_START + 1 == 1024

    def test_low_surrogate_range_size(self) -> None:
        assert ud.SURROGATE_LOW_END - ud.SURROGATE_LOW_START + 1 == 1024

    def test_base_offset_is_0x10000(self) -> None:
        assert ud.SURROGATE_BASE_OFFSET == 0x010000


# ── plane_of() ───────────────────────────────────────────────────────────────


class TestPlaneOf:
    def test_ascii_codepoint_is_bmp(self) -> None:
        assert ud.plane_of(0x0041) == "BMP"

    def test_bmp_high_end_is_bmp(self) -> None:
        assert ud.plane_of(0x0000) == "BMP"
        assert ud.plane_of(0xFFFF) == "BMP"

    def test_pua_range_returns_pua(self) -> None:
        assert ud.plane_of(0xE000) == "PUA"
        assert ud.plane_of(0xF8FF) == "PUA"
        assert ud.plane_of(0xE100) == "PUA"

    def test_smp_range_returns_smp(self) -> None:
        assert ud.plane_of(0x10000) == "SMP"
        assert ud.plane_of(0x1FFFF) == "SMP"
        assert ud.plane_of(0x1F600) == "SMP"

    def test_sip_range_returns_sip(self) -> None:
        assert ud.plane_of(0x20000) == "SIP"
        assert ud.plane_of(0x2FFFF) == "SIP"
        assert ud.plane_of(0x20001) == "SIP"

    def test_tip_range_returns_tip(self) -> None:
        assert ud.plane_of(0x30000) == "TIP"
        assert ud.plane_of(0x3FFFF) == "TIP"

    def test_ssp_range_returns_ssp(self) -> None:
        assert ud.plane_of(0xE0000) == "SSP"
        assert ud.plane_of(0xE0FFF) == "SSP"
        assert ud.plane_of(0xE0100) == "SSP"

    def test_spua_a_range_returns_spua_a(self) -> None:
        assert ud.plane_of(0xF0000) == "SPUA-A"
        assert ud.plane_of(0xFFFFF) == "SPUA-A"

    def test_spua_b_range_returns_spua_b(self) -> None:
        assert ud.plane_of(0x100000) == "SPUA-B"
        assert ud.plane_of(0x10FFFF) == "SPUA-B"

    def test_unassigned_negative_codepoint(self) -> None:
        assert ud.plane_of(-1) == "UNASSIGNED"

    def test_unassigned_beyond_max(self) -> None:
        assert ud.plane_of(0x110000) == "UNASSIGNED"
        assert ud.plane_of(0x200000) == "UNASSIGNED"

    def test_unassigned_gap_between_ffff_and_10000(self) -> None:
        assert ud.plane_of(0xFFFE) == "BMP"
        # 0xFFFF is BMP; values between FFFF and 10000 don't exist as ints
        # but 0xFFFF is the last valid BMP codepoint

    def test_unassigned_gap_between_e0fff_and_f0000(self) -> None:
        assert ud.plane_of(0xE1000) == "UNASSIGNED"

    # ── pua subsumption bug: plane_of() checks 0xF0000-0xFFFFF
    #     inside the BMP branch, which never matches because BMP
    #     is 0x0000-0xFFFF. Verifying the actual behaviour.
    def test_pua_range_not_in_bmp_branch(self) -> None:
        """0xF0000 is > 0xFFFF, so it falls through to the outer if-elif chain."""
        assert ud.plane_of(0xF0000) == "SPUA-A"


# ── is_surrogate() ──────────────────────────────────────────────────────────


class TestIsSurrogate:
    def test_high_surrogates_are_surrogates(self) -> None:
        assert ud.is_surrogate(0xD800) is True
        assert ud.is_surrogate(0xDBFF) is True
        assert ud.is_surrogate(0xD900) is True

    def test_low_surrogates_are_surrogates(self) -> None:
        assert ud.is_surrogate(0xDC00) is True
        assert ud.is_surrogate(0xDFFF) is True
        assert ud.is_surrogate(0xDD00) is True

    def test_bmp_below_surrogates_are_not_surrogates(self) -> None:
        assert ud.is_surrogate(0xD7FF) is False
        assert ud.is_surrogate(0x0000) is False
        assert ud.is_surrogate(0x0041) is False

    def test_bmp_above_surrogates_are_not_surrogates(self) -> None:
        assert ud.is_surrogate(0xE000) is False
        assert ud.is_surrogate(0xFFFF) is False

    def test_supplementary_plane_not_surrogates(self) -> None:
        assert ud.is_surrogate(0x10000) is False
        assert ud.is_surrogate(0x10FFFF) is False

    def test_negative_codepoint_not_surrogate(self) -> None:
        assert ud.is_surrogate(-1) is False
        assert ud.is_surrogate(-0xD800) is False


# ── is_high_surrogate() ─────────────────────────────────────────────────────


class TestIsHighSurrogate:
    def test_high_surrogate_boundaries(self) -> None:
        assert ud.is_high_surrogate(0xD800) is True
        assert ud.is_high_surrogate(0xDBFF) is True
        assert ud.is_high_surrogate(0xD900) is True

    def test_low_surrogate_is_not_high(self) -> None:
        assert ud.is_high_surrogate(0xDC00) is False
        assert ud.is_high_surrogate(0xDFFF) is False

    def test_bmp_outside_surrogate_range_not_high(self) -> None:
        assert ud.is_high_surrogate(0xD7FF) is False
        assert ud.is_high_surrogate(0xE000) is False
        assert ud.is_high_surrogate(0x0041) is False


# ── is_low_surrogate() ──────────────────────────────────────────────────────


class TestIsLowSurrogate:
    def test_low_surrogate_boundaries(self) -> None:
        assert ud.is_low_surrogate(0xDC00) is True
        assert ud.is_low_surrogate(0xDFFF) is True
        assert ud.is_low_surrogate(0xDD00) is True

    def test_high_surrogate_is_not_low(self) -> None:
        assert ud.is_low_surrogate(0xD800) is False
        assert ud.is_low_surrogate(0xDBFF) is False

    def test_bmp_outside_surrogate_range_not_low(self) -> None:
        assert ud.is_low_surrogate(0xD7FF) is False
        assert ud.is_low_surrogate(0xE000) is False
        assert ud.is_low_surrogate(0x0041) is False


# ── surrogates_to_codepoint() ───────────────────────────────────────────────


class TestSurrogatesToCodepoint:
    def test_first_supplementary_plane_codepoint(self) -> None:
        result = ud.surrogates_to_codepoint(0xD800, 0xDC00)
        assert result == 0x010000

    def test_last_valid_codepoint(self) -> None:
        result = ud.surrogates_to_codepoint(0xDBFF, 0xDFFF)
        assert result == 0x10FFFF

    def test_linear_projection_codepoint(self) -> None:
        result = ud.surrogates_to_codepoint(0xD801, 0xDC01)
        assert result == 0x10401

    def test_emoji_range_codepoint(self) -> None:
        result = ud.surrogates_to_codepoint(0xD83D, 0xDE00)
        assert result == 0x1F600

    def test_roundtrip_with_known_plane_of(self) -> None:
        high, low = 0xD83D, 0xDE00
        cp = ud.surrogates_to_codepoint(high, low)
        assert ud.plane_of(cp) == "SMP"

    def test_result_is_always_greater_than_0xFFFF(self) -> None:
        for high in [0xD800, 0xD900, 0xDA00, 0xDB00, 0xDBFF]:
            for low in [0xDC00, 0xDD00, 0xDE00, 0xDF00, 0xDFFF]:
                assert ud.surrogates_to_codepoint(high, low) > 0xFFFF


# ── UTF-8 max constants ─────────────────────────────────────────────────────


class TestUtf8MaxConstants:
    def test_utf8_max_1_is_ascii_boundary(self) -> None:
        assert ud.UTF8_MAX_1 == 0x7F
        assert chr(ud.UTF8_MAX_1) == "\x7f"

    def test_utf8_max_2_is_two_byte_boundary(self) -> None:
        assert ud.UTF8_MAX_2 == 0x7FF
        assert ud.UTF8_MAX_2 > ud.UTF8_MAX_1

    def test_utf8_max_3_is_bmp_boundary(self) -> None:
        assert ud.UTF8_MAX_3 == 0xFFFF
        assert ud.UTF8_MAX_3 > ud.UTF8_MAX_2

    def test_utf8_max_4_is_unicode_max(self) -> None:
        assert ud.UTF8_MAX_4 == 0x10FFFF
        assert ud.UTF8_MAX_4 > ud.UTF8_MAX_3

    def test_constants_form_strict_total_order(self) -> None:
        assert ud.UTF8_MAX_1 < ud.UTF8_MAX_2 < ud.UTF8_MAX_3 < ud.UTF8_MAX_4


# ── Edge-case / integration tests ────────────────────────────────────────────


class TestEdgeCases:
    def test_plane_of_null_codepoint(self) -> None:
        assert ud.plane_of(0x0000) == "BMP"

    def test_plane_of_replacement_character(self) -> None:
        assert ud.plane_of(0xFFFD) == "BMP"

    def test_plane_of_bom_codepoint(self) -> None:
        assert ud.plane_of(0xFEFF) == "BMP"

    def test_plane_of_max_unicode(self) -> None:
        assert ud.plane_of(0x10FFFF) == "SPUA-B"

    def test_surrogate_and_plane_consistency(self) -> None:
        for cp in range(0xD800, 0xE000):
            assert ud.is_surrogate(cp) is True

    def test_pua_when_plane_of_called_with_exact_pua_boundary(self) -> None:
        assert ud.plane_of(0xE000) == "PUA"
        assert ud.plane_of(0xF8FF) == "PUA"

    def test_bmp_upper_half_not_pua(self) -> None:
        assert ud.plane_of(0xD7FF) == "BMP"
        assert ud.plane_of(0xF900) == "BMP"
