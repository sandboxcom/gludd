"""Deep tests for language/charset_map.py — encoding tables, BOM data, code pages."""

from __future__ import annotations

import pytest

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

_VALID_CATEGORIES: set[EncodingCategory] = {
    "single-byte",
    "multi-byte",
    "variable-width",
    "fixed-width",
    "stateful",
}


def _encoding_names(items: list[dict]) -> list[str]:
    return [e["name"] for e in items]


# ── BOM signatures ────────────────────────────────────────────────────────


class TestBOMSignatures:
    def test_all_boms_have_non_empty_bytes(self):
        for name, seq in BOM_SIGNATURES.items():
            assert isinstance(seq, bytes), f"BOM for {name} is not bytes"
            assert len(seq) > 0, f"BOM for {name} is empty"

    def test_bom_bytes_are_unique(self):
        sequences = list(BOM_SIGNATURES.values())
        assert len(sequences) == len(set(sequences)), "BOM byte sequences collide"

    def test_reverse_lookup_covers_all(self):
        assert len(BOM_BY_SEQUENCE) == len(BOM_SIGNATURES)
        for name, seq in BOM_SIGNATURES.items():
            assert BOM_BY_SEQUENCE[seq] == name

    def test_bom_size_values_positive(self):
        for name, size in BOM_SIZE.items():
            assert size > 0, f"BOM_SIZE[{name}] = {size} not positive"
            assert size == len(BOM_SIGNATURES[name]), (
                f"BOM_SIZE[{name}] = {size} but sig is {len(BOM_SIGNATURES[name])} bytes"
            )

    def test_bom_required_set_non_empty(self):
        assert len(BOM_REQUIRED_BY_RFC) > 0

    def test_required_and_optional_disjoint(self):
        assert BOM_REQUIRED_BY_RFC.isdisjoint(BOM_OPTIONAL_BY_RFC)


# ── Encoding lists — structural integrity ─────────────────────────────────


class TestEncodingListIntegrity:
    @pytest.mark.parametrize(
        "enc_list_name",
        [
            "UTF_ENCODINGS",
            "SINGLE_BYTE_ENCODINGS",
            "WINDOWS_CODE_PAGES",
            "CJK_ENCODINGS",
            "CYRILLIC_ENCODINGS",
            "IBM_CODE_PAGES",
        ],
    )
    def test_list_is_non_empty(self, enc_list_name: str):
        import general_ludd.language.charset_map as m

        lst = getattr(m, enc_list_name)
        assert len(lst) > 0, f"{enc_list_name} is empty"

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_every_entry_has_required_fields(self, enc_list):
        required = {"name", "aliases", "category", "max_bytes_per_char", "is_ascii_compatible", "languages"}
        for entry in enc_list:
            missing = required - set(entry.keys())
            assert not missing, f"{entry.get('name', '?')} missing {missing}"

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_name_is_non_empty_str(self, enc_list):
        for entry in enc_list:
            assert isinstance(entry["name"], str) and len(entry["name"]) > 0

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_aliases_is_non_empty_list_of_str(self, enc_list):
        for entry in enc_list:
            assert isinstance(entry["aliases"], list) and len(entry["aliases"]) > 0
            for alias in entry["aliases"]:
                assert isinstance(alias, str), f"alias {alias!r} in {entry['name']}"

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_category_is_valid(self, enc_list):
        for entry in enc_list:
            assert entry["category"] in _VALID_CATEGORIES, f"{entry['name']} has bad category: {entry['category']}"

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_max_bytes_per_char_is_positive_int(self, enc_list):
        for entry in enc_list:
            assert isinstance(entry["max_bytes_per_char"], int)
            assert entry["max_bytes_per_char"] >= 1

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_is_ascii_compatible_is_bool(self, enc_list):
        for entry in enc_list:
            assert isinstance(entry["is_ascii_compatible"], bool)

    @pytest.mark.parametrize(
        "enc_list",
        [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ],
    )
    def test_languages_is_non_empty_list_of_str(self, enc_list):
        for entry in enc_list:
            assert isinstance(entry["languages"], list) and len(entry["languages"]) > 0
            for lang in entry["languages"]:
                assert isinstance(lang, str)


# ── Encoding names are unique within and across lists ─────────────────────


class TestEncodingNameUniqueness:
    def test_names_unique_within_each_list(self):
        for lst in [
            UTF_ENCODINGS,
            SINGLE_BYTE_ENCODINGS,
            WINDOWS_CODE_PAGES,
            CJK_ENCODINGS,
            CYRILLIC_ENCODINGS,
            IBM_CODE_PAGES,
        ]:
            names = _encoding_names(lst)
            dupes = [n for n in names if names.count(n) > 1]
            assert not dupes, f"Duplicate names in list: {set(dupes)}"

    def test_aliases_unique_across_all_encodings(self):
        seen: set[str] = set()
        for entry in ALL_ENCODINGS:
            for alias in entry["aliases"]:
                lower = alias.lower()
                assert lower not in seen, f"Alias collision: {alias!r} in {entry['name']}"
                seen.add(lower)

    def test_encoding_names_dont_repeat_across_lists(self):
        # Just checking that ALL_ENCODINGS has no duplicate primary names
        names = [e["name"] for e in ALL_ENCODINGS]
        assert len(names) == len(set(names)), f"Duplicate encoding names: {[n for n in names if names.count(n) > 1]}"


# ── Specific encoding spot-checks ─────────────────────────────────────────


class TestEncodingSpotChecks:
    def test_utf8_in_utf_list_and_bom(self):
        assert BOM_SIGNATURES["UTF-8"] == b"\xef\xbb\xbf"
        names = _encoding_names(UTF_ENCODINGS)
        assert "UTF-8" in names

    def test_iso_8859_1_is_latin1(self):
        names = _encoding_names(SINGLE_BYTE_ENCODINGS)
        assert "ISO-8859-1" in names
        e1 = next(e for e in SINGLE_BYTE_ENCODINGS if e["name"] == "ISO-8859-1")
        assert "latin1" in e1["aliases"]
        assert "English" in e1["languages"]

    def test_windows_1252_is_ansi(self):
        e = next(e for e in WINDOWS_CODE_PAGES if e["name"] == "windows-1252")
        assert "ANSI" in e["aliases"]
        assert e["category"] == "single-byte"

    def test_shift_jis_cjk(self):
        names = _encoding_names(CJK_ENCODINGS)
        assert "Shift_JIS" in names
        e = next(e for e in CJK_ENCODINGS if e["name"] == "Shift_JIS")
        assert "sjis" in e["aliases"]
        assert "Japanese" in e["languages"]

    def test_koi8_r_cyrillic(self):
        e = next(e for e in CYRILLIC_ENCODINGS if e["name"] == "KOI8-R")
        assert "Russian" in e["languages"]
        assert e["max_bytes_per_char"] == 1

    def test_ibm437_english(self):
        e = next(e for e in IBM_CODE_PAGES if e["name"] == "IBM437")
        assert "English" in e["languages"]
        assert e["max_bytes_per_char"] == 1

    def test_ibm_code_pages_count(self):
        assert len(IBM_CODE_PAGES) == 13, f"Expected 13 IBM code pages, got {len(IBM_CODE_PAGES)}"


# ── ALL_ENCODINGS consistency ─────────────────────────────────────────────


class TestAllEncodings:
    def test_all_encodings_is_concatenation(self):
        expected = (
            UTF_ENCODINGS
            + SINGLE_BYTE_ENCODINGS
            + WINDOWS_CODE_PAGES
            + CJK_ENCODINGS
            + CYRILLIC_ENCODINGS
            + IBM_CODE_PAGES
        )
        assert expected == ALL_ENCODINGS

    def test_every_encoding_has_unique_name(self):
        names = [e["name"] for e in ALL_ENCODINGS]
        assert len(names) == len(set(names))

    def test_single_byte_encodings_all_have_max_1(self):
        for entry in SINGLE_BYTE_ENCODINGS + WINDOWS_CODE_PAGES + IBM_CODE_PAGES + CYRILLIC_ENCODINGS:
            assert entry["max_bytes_per_char"] == 1, (
                f"{entry['name']} should be single-byte but max is {entry['max_bytes_per_char']}"
            )


# ── Chardet confidence thresholds ─────────────────────────────────────────


class TestChardetThresholds:
    def test_four_keys_expected(self):
        expected_keys = {"entry", "usable", "reliable", "trusted"}
        assert set(CHARDET_CONFIDENCE_THRESHOLDS.keys()) == expected_keys

    def test_values_in_0_1(self):
        for key, val in CHARDET_CONFIDENCE_THRESHOLDS.items():
            assert 0.0 <= val <= 1.0, f"{key}={val} out of [0,1]"

    def test_values_monotonic(self):
        vals = [CHARDET_CONFIDENCE_THRESHOLDS[k] for k in ("entry", "usable", "reliable", "trusted")]
        for i in range(len(vals) - 1):
            assert vals[i] <= vals[i + 1], f"Not monotonic: {vals}"


# ── Mojibake signatures ───────────────────────────────────────────────────


class TestMojibakeSignatures:
    def test_all_keys_are_strings_with_specified_encodings(self):
        for key in MOJIBAKE_SIGNATURES:
            assert "viewed as" in key.lower() or "viewed as" in key

    def test_all_values_are_non_empty_lists_of_str(self):
        for key, patterns in MOJIBAKE_SIGNATURES.items():
            assert isinstance(patterns, list) and len(patterns) > 0, f"Empty patterns for {key}"
            for p in patterns:
                assert isinstance(p, str), f"Non-str pattern {p!r} in {key}"

    def test_every_pattern_is_non_empty_string(self):
        for key, patterns in MOJIBAKE_SIGNATURES.items():
            for p in patterns:
                assert len(p) > 0, f"Empty string pattern in {key}"


# ── ISO 8859 coverage ─────────────────────────────────────────────────────


class TestISO8859Coverage:
    def test_all_iso_8859_variants_present(self):
        present = {e["name"] for e in SINGLE_BYTE_ENCODINGS}
        missing = sorted(set(ISO_8859_NAMES) - present)
        assert not missing, f"Missing ISO 8859 variants: {missing}"


ISO_8859_NAMES = {
    "ISO-8859-1",
    "ISO-8859-2",
    "ISO-8859-3",
    "ISO-8859-4",
    "ISO-8859-5",
    "ISO-8859-6",
    "ISO-8859-7",
    "ISO-8859-8",
    "ISO-8859-9",
    "ISO-8859-10",
    "ISO-8859-11",
    "ISO-8859-13",
    "ISO-8859-14",
    "ISO-8859-15",
    "ISO-8859-16",
}


# ── Windows code page coverage ────────────────────────────────────────────


class TestWindowsCodePageCoverage:
    def test_all_common_cp_present(self):
        present = {e["name"] for e in WINDOWS_CODE_PAGES}
        expected = {
            "windows-1250",
            "windows-1251",
            "windows-1252",
            "windows-1253",
            "windows-1254",
            "windows-1255",
            "windows-1256",
            "windows-1257",
            "windows-1258",
        }
        assert present == expected


# ── CJK coverage ──────────────────────────────────────────────────────────


class TestCJKCoverage:
    def test_all_major_cjk_present(self):
        names = {e["name"] for e in CJK_ENCODINGS}
        assert "Shift_JIS" in names
        assert "EUC-JP" in names
        assert "EUC-KR" in names
        assert "GB2312" in names
        assert "GB18030" in names
        assert "Big5" in names
