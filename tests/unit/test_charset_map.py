"""Tests for language/charset_map.py — encoding tables, BOM data, code page mappings."""

from __future__ import annotations

from typing import ClassVar

from general_ludd.language.charset_map import (
    ALL_ENCODINGS,
    BOM_BY_SEQUENCE,
    BOM_OPTIONAL_BY_RFC,
    BOM_REQUIRED_BY_RFC,
    BOM_SIGNATURES,
    BOM_SIZE,
    CHARDET_CONFIDENCE_THRESHOLDS,
    CJK_ENCODINGS,
    CYRILLIC_ENCODINGS,
    IBM_CODE_PAGES,
    MOJIBAKE_SIGNATURES,
    SINGLE_BYTE_ENCODINGS,
    UTF_ENCODINGS,
    WINDOWS_CODE_PAGES,
    EncodingCategory,
)


class TestBOMSignatures:
    def test_all_values_are_bytes(self) -> None:
        for encoding, bom in BOM_SIGNATURES.items():
            assert isinstance(bom, bytes), f"BOM for {encoding} is not bytes: {type(bom)}"

    def test_utf8_bom_is_three_bytes(self) -> None:
        assert BOM_SIGNATURES["UTF-8"] == b"\xef\xbb\xbf"

    def test_utf16_be_bom(self) -> None:
        assert BOM_SIGNATURES["UTF-16-BE"] == b"\xfe\xff"

    def test_utf16_le_bom(self) -> None:
        assert BOM_SIGNATURES["UTF-16-LE"] == b"\xff\xfe"

    def test_utf32_be_bom_is_four_bytes(self) -> None:
        assert BOM_SIGNATURES["UTF-32-BE"] == b"\x00\x00\xfe\xff"

    def test_utf32_le_bom_is_four_bytes(self) -> None:
        assert BOM_SIGNATURES["UTF-32-LE"] == b"\xff\xfe\x00\x00"

    def test_all_boms_are_unique(self) -> None:
        boms = list(BOM_SIGNATURES.values())
        assert len(boms) == len(set(boms))

    def test_known_encodings_present(self) -> None:
        for enc in ("UTF-8", "UTF-16-BE", "UTF-16-LE", "GB-18030", "UTF-7"):
            assert enc in BOM_SIGNATURES, f"Missing BOM for {enc}"

    def test_utf16_boms_are_two_bytes(self) -> None:
        assert len(BOM_SIGNATURES["UTF-16-BE"]) == 2
        assert len(BOM_SIGNATURES["UTF-16-LE"]) == 2


class TestBOMBySequence:
    def test_is_exact_inverse_of_bom_signatures(self) -> None:
        for enc, bom in BOM_SIGNATURES.items():
            assert BOM_BY_SEQUENCE[bom] == enc

    def test_all_sequences_map_back(self) -> None:
        assert len(BOM_BY_SEQUENCE) == len(BOM_SIGNATURES)

    def test_utf8_sequence_maps_to_utf8(self) -> None:
        assert BOM_BY_SEQUENCE[b"\xef\xbb\xbf"] == "UTF-8"

    def test_utf16le_sequence_maps_to_utf16_le(self) -> None:
        assert BOM_BY_SEQUENCE[b"\xff\xfe"] == "UTF-16-LE"


class TestBOMSize:
    def test_utf8_bom_size(self) -> None:
        assert BOM_SIZE["UTF-8"] == 3

    def test_utf16_bom_sizes_are_two(self) -> None:
        assert BOM_SIZE["UTF-16-BE"] == 2
        assert BOM_SIZE["UTF-16-LE"] == 2

    def test_utf32_bom_sizes_are_four(self) -> None:
        assert BOM_SIZE["UTF-32-BE"] == 4
        assert BOM_SIZE["UTF-32-LE"] == 4

    def test_bom_size_entries_match_bom_signatures_for_defines(self) -> None:
        for enc in BOM_SIZE:
            assert enc in BOM_SIGNATURES, f"BOM_SIZE entry {enc} missing from BOM_SIGNATURES"


class TestBOMRFCSets:
    def test_utf16_required_by_rfc(self) -> None:
        assert "UTF-16" in BOM_REQUIRED_BY_RFC

    def test_utf8_optional_by_rfc(self) -> None:
        assert "UTF-8" in BOM_OPTIONAL_BY_RFC

    def test_no_overlap_between_required_and_optional(self) -> None:
        assert len(BOM_REQUIRED_BY_RFC & BOM_OPTIONAL_BY_RFC) == 0


class TestEncodeInfoStructure:
    """Asserts every EncodingInfo dict across all lists is structurally valid."""

    VALID_CATEGORIES: ClassVar[set[str]] = {"single-byte", "multi-byte", "variable-width", "fixed-width", "stateful"}

    @staticmethod
    def _all_encodings():
        for group_name, group_list in [
            ("UTF_ENCODINGS", UTF_ENCODINGS),
            ("SINGLE_BYTE_ENCODINGS", SINGLE_BYTE_ENCODINGS),
            ("WINDOWS_CODE_PAGES", WINDOWS_CODE_PAGES),
            ("CJK_ENCODINGS", CJK_ENCODINGS),
            ("CYRILLIC_ENCODINGS", CYRILLIC_ENCODINGS),
            ("IBM_CODE_PAGES", IBM_CODE_PAGES),
        ]:
            for info in group_list:
                yield group_name, info

    def test_every_encoding_has_required_keys(self) -> None:
        required = {"name", "aliases", "category", "max_bytes_per_char", "is_ascii_compatible", "languages"}
        for group, info in self._all_encodings():
            missing = required - set(info.keys())
            assert not missing, f"{info.get('name', '?')} in {group} missing keys: {missing}"

    def test_category_is_valid(self) -> None:
        for group, info in self._all_encodings():
            assert info["category"] in self.VALID_CATEGORIES, (
                f"{info['name']} in {group}: invalid category {info['category']!r}"
            )

    def test_max_bytes_per_char_is_positive(self) -> None:
        for group, info in self._all_encodings():
            assert info["max_bytes_per_char"] >= 1, (
                f"{info['name']} in {group}: max_bytes_per_char={info['max_bytes_per_char']}"
            )

    def test_aliases_are_strings(self) -> None:
        for group, info in self._all_encodings():
            for alias in info["aliases"]:
                assert isinstance(alias, str), f"{info['name']} in {group}: alias {alias!r} is {type(alias)}"

    def test_languages_is_nonempty_list_of_strings(self) -> None:
        for _group, info in self._all_encodings():
            assert isinstance(info["languages"], list), f"{info['name']}: languages not a list"
            assert len(info["languages"]) > 0, f"{info['name']}: empty languages"
            for lang in info["languages"]:
                assert isinstance(lang, str), f"{info['name']}: language {lang!r} is {type(lang)}"

    def test_no_duplicate_names_across_all_lists(self) -> None:
        seen = set()
        for _group, info in self._all_encodings():
            assert info["name"] not in seen, f"Duplicate encoding name: {info['name']}"
            seen.add(info["name"])

    def test_is_ascii_compatible_is_bool(self) -> None:
        for group, info in self._all_encodings():
            assert isinstance(info["is_ascii_compatible"], bool), (
                f"{info['name']} in {group}: is_ascii_compatible is {type(info['is_ascii_compatible'])}"
            )


class TestALLEncodingAggregate:
    def test_all_encodings_length_matches_sum_of_parts(self) -> None:
        expected = (
            len(UTF_ENCODINGS)
            + len(SINGLE_BYTE_ENCODINGS)
            + len(WINDOWS_CODE_PAGES)
            + len(CJK_ENCODINGS)
            + len(CYRILLIC_ENCODINGS)
            + len(IBM_CODE_PAGES)
        )
        assert len(ALL_ENCODINGS) == expected

    def test_all_encodings_contains_utf8_first(self) -> None:
        assert ALL_ENCODINGS[0]["name"] == "UTF-8"

    def test_all_encodings_contains_ibm_last(self) -> None:
        assert ALL_ENCODINGS[-1]["name"] == "IBM869"

    def test_encoding_count_matches_expected(self) -> None:
        assert len(ALL_ENCODINGS) == 51  # 6 UTF + 15 ISO + 9 Win + 6 CJK + 2 Cyrillic + 13 IBM

    def test_all_individual_lists_nonempty(self) -> None:
        assert len(UTF_ENCODINGS) > 0
        assert len(SINGLE_BYTE_ENCODINGS) > 0
        assert len(WINDOWS_CODE_PAGES) > 0
        assert len(CJK_ENCODINGS) > 0
        assert len(CYRILLIC_ENCODINGS) > 0
        assert len(IBM_CODE_PAGES) > 0


class TestSingleByteEncodings:
    def test_all_single_byte_encodings_are_iso_8859(self) -> None:
        for info in SINGLE_BYTE_ENCODINGS:
            assert info["name"].startswith("ISO-8859-")

    def test_correct_count_of_iso_encodings(self) -> None:
        # ISO-8859-1 through 11, 13-16 (12 is skipped in the standard too)
        assert len(SINGLE_BYTE_ENCODINGS) == 15

    def test_iso8859_1_latin1_alias(self) -> None:
        iso1 = SINGLE_BYTE_ENCODINGS[0]
        assert iso1["name"] == "ISO-8859-1"
        assert "latin1" in iso1["aliases"]

    def test_all_single_byte_have_max_bytes_one(self) -> None:
        for info in SINGLE_BYTE_ENCODINGS:
            assert info["max_bytes_per_char"] == 1, f"{info['name']}: {info['max_bytes_per_char']}"

    def test_all_single_byte_are_ascii_compatible(self) -> None:
        for info in SINGLE_BYTE_ENCODINGS:
            assert info["is_ascii_compatible"] is True, f"{info['name']}: not ascii-compatible"


class TestWindowsCodePages:
    def test_correct_count(self) -> None:
        assert len(WINDOWS_CODE_PAGES) == 9

    def test_all_names_start_with_windows(self) -> None:
        for info in WINDOWS_CODE_PAGES:
            assert info["name"].startswith("windows-125")

    def test_cp_alias_for_each(self) -> None:
        for info in WINDOWS_CODE_PAGES:
            has_cp_alias = any(a.startswith("cp") for a in info["aliases"])
            assert has_cp_alias, f"{info['name']} missing cp alias"


class TestCJKEncodings:
    def test_correct_count(self) -> None:
        assert len(CJK_ENCODINGS) == 6

    def test_shift_jis_present(self) -> None:
        names = [e["name"] for e in CJK_ENCODINGS]
        assert "Shift_JIS" in names

    def test_big5_present(self) -> None:
        names = [e["name"] for e in CJK_ENCODINGS]
        assert "Big5" in names

    def test_all_are_ascii_compatible(self) -> None:
        for info in CJK_ENCODINGS:
            assert info["is_ascii_compatible"] is True, f"{info['name']}: not ascii-compatible"


class TestCyrillicEncodings:
    def test_correct_count(self) -> None:
        assert len(CYRILLIC_ENCODINGS) == 2

    def test_koi8r_present(self) -> None:
        names = [e["name"] for e in CYRILLIC_ENCODINGS]
        assert "KOI8-R" in names

    def test_koi8u_present(self) -> None:
        names = [e["name"] for e in CYRILLIC_ENCODINGS]
        assert "KOI8-U" in names


class TestIBMCodePages:
    def test_correct_count(self) -> None:
        assert len(IBM_CODE_PAGES) == 13

    def test_ibm437_first(self) -> None:
        assert IBM_CODE_PAGES[0]["name"] == "IBM437"

    def test_all_have_cp_alias(self) -> None:
        for info in IBM_CODE_PAGES:
            has_cp = any(a.startswith("cp") for a in info["aliases"])
            assert has_cp, f"{info['name']} missing cp alias"

    def test_all_max_bytes_one(self) -> None:
        for info in IBM_CODE_PAGES:
            assert info["max_bytes_per_char"] == 1


class TestChardetConfidenceThresholds:
    def test_keys_are_correct(self) -> None:
        assert set(CHARDET_CONFIDENCE_THRESHOLDS.keys()) == {"entry", "usable", "reliable", "trusted"}

    def test_values_are_monotonically_increasing(self) -> None:
        vals = list(CHARDET_CONFIDENCE_THRESHOLDS.values())
        for i in range(1, len(vals)):
            assert vals[i] > vals[i - 1], f"Not monotonic: {vals}"

    def test_all_values_between_zero_and_one(self) -> None:
        for key, val in CHARDET_CONFIDENCE_THRESHOLDS.items():
            assert 0.0 < val < 1.0, f"{key}={val} not in (0, 1)"

    def test_trusted_is_highest(self) -> None:
        assert CHARDET_CONFIDENCE_THRESHOLDS["trusted"] == max(CHARDET_CONFIDENCE_THRESHOLDS.values())

    def test_entry_is_lowest(self) -> None:
        assert CHARDET_CONFIDENCE_THRESHOLDS["entry"] == min(CHARDET_CONFIDENCE_THRESHOLDS.values())


class TestMojibakeSignatures:
    def test_known_scenarios_present(self) -> None:
        expected = {
            "UTF-8 viewed as ISO-8859-1",
            "ISO-8859-1 viewed as UTF-8",
            "UTF-8 viewed as Windows-1252",
            "Shift_JIS viewed as ISO-8859-1",
            "EUC-KR viewed as ISO-8859-1",
            "GB2312 viewed as Windows-1252",
        }
        assert set(MOJIBAKE_SIGNATURES.keys()) == expected

    def test_all_signatures_are_nonempty_string_lists(self) -> None:
        for scenario, patterns in MOJIBAKE_SIGNATURES.items():
            assert isinstance(patterns, list), f"{scenario}: not a list"
            assert len(patterns) > 0, f"{scenario}: empty list"
            for p in patterns:
                assert isinstance(p, str), f"{scenario}: {p!r} is {type(p)}"

    def test_utf8_viewed_as_iso_has_known_patterns(self) -> None:
        patterns = MOJIBAKE_SIGNATURES["UTF-8 viewed as ISO-8859-1"]
        assert "\u00c3\u00a9" in patterns  # é mojibake
        assert "\u00c2\u00bf" in patterns  # ¿ mojibake

    def test_iso_viewed_as_utf8_has_replacement_chars(self) -> None:
        patterns = MOJIBAKE_SIGNATURES["ISO-8859-1 viewed as UTF-8"]
        assert "\ufffd" in patterns  # U+FFFD REPLACEMENT CHARACTER


class TestUTFEncodings:
    def test_correct_count(self) -> None:
        assert len(UTF_ENCODINGS) == 6

    def test_utf8_is_ascii_compatible(self) -> None:
        utf8 = UTF_ENCODINGS[0]
        assert utf8["name"] == "UTF-8"
        assert utf8["is_ascii_compatible"] is True

    def test_utf16_not_ascii_compatible(self) -> None:
        for info in UTF_ENCODINGS:
            if "UTF-16" in info["name"]:
                assert info["is_ascii_compatible"] is False

    def test_utf32_is_fixed_width(self) -> None:
        for info in UTF_ENCODINGS:
            if "UTF-32" in info["name"]:
                assert info["category"] == "fixed-width"

    def test_utf7_is_stateful(self) -> None:
        utf7 = next(e for e in UTF_ENCODINGS if e["name"] == "UTF-7")
        assert utf7["category"] == "stateful"
        assert utf7["max_bytes_per_char"] == 5


class TestEncodingCategoryType:
    def test_encoding_category_is_str_alias(self) -> None:
        assert EncodingCategory is str
