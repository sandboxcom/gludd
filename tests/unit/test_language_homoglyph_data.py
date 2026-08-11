"""Unit tests for homoglyph data module.

Covers: detect_confusables, detect_invisible_chars, detect_bidi_overrides,
detect_mixed_script, generate_skeleton, is_suspicious, and data-integrity checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class TestDetectConfusables:
    def test_empty_string_returns_empty(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        assert detect_confusables("") == []

    def test_pure_ascii_no_findings(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("Hello World")
        assert result == []

    def test_cyrillic_a_detected_as_homoglyph(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("\u0430")
        assert len(result) == 1
        assert result[0]["skeleton"] == "a"
        assert result[0]["codepoint"] == 0x0430
        assert result[0]["position"] == 0

    def test_latin_a_not_detected_as_homoglyph(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("a")
        assert result == []

    def test_cyrillic_es_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("\u0441")
        assert len(result) == 1
        assert result[0]["skeleton"] == "c"

    def test_all_cyrillic_lookalikes_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        cyrillic_lookalikes = "\u0430\u0441\u0435\u043e\u0440\u0445\u0443"
        result = detect_confusables(cyrillic_lookalikes)
        skeletons = {r["skeleton"] for r in result}
        assert {"a", "c", "e", "o", "p", "x", "y"}.issubset(skeletons)

    def test_greek_alpha_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("\u0391")
        assert len(result) == 1
        assert result[0]["skeleton"] == "A"
        assert result[0]["codepoint"] == 0x0391

    def test_confusable_has_name_field(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("\u0435")
        assert len(result) == 1
        assert len(result[0]["name"]) > 0
        assert "CYRILLIC" in result[0]["name"]

    def test_mixed_ascii_and_homoglyphs(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result = detect_confusables("abc\u0430\u0441")
        assert len(result) == 2
        assert result[0]["position"] == 3
        assert result[1]["position"] == 4

    def test_capital_O_is_confusable_zero_is_not(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        result_O = detect_confusables("O")
        result_0 = detect_confusables("0")
        assert len(result_O) == 1
        assert result_O[0]["skeleton"] == "0"
        assert result_0 == []

    def test_confusable_characters_present_position_ordering(self) -> None:
        from general_ludd.language.homoglyph_data import detect_confusables

        text = "x\u0441y\u0430z"
        result = detect_confusables(text)
        assert len(result) == 2
        assert result[0]["position"] == 1
        assert result[1]["position"] == 3


class TestDetectInvisibleChars:
    def test_empty_string_returns_empty(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        assert detect_invisible_chars("") == []

    def test_normal_ascii_no_invisibles(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("normal text 123")
        assert result == []

    def test_zero_width_space_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u200b")
        assert len(result) == 1
        assert result[0]["short_name"] == "ZWSP"
        assert result[0]["category"] == "zero-width-space"
        assert result[0]["position"] == 0

    def test_zero_width_non_joiner_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u200c")
        assert len(result) == 1
        assert result[0]["short_name"] == "ZWNJ"

    def test_zero_width_joiner_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u200d")
        assert len(result) == 1
        assert result[0]["short_name"] == "ZWJ"

    def test_soft_hyphen_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u00ad")
        assert len(result) == 1
        assert result[0]["short_name"] == "SHY"

    def test_rlo_has_cve(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u202e")
        assert len(result) == 1
        assert result[0]["cve"] == "CVE-2021-42574"
        assert result[0]["short_name"] == "RLO"

    def test_bom_zwnbsp_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\ufeff")
        assert len(result) == 1
        assert result[0]["short_name"] == "BOM/ZWNBSP"

    def test_multiple_invisibles_at_positions(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        text = "a\u200b\u200cb"
        result = detect_invisible_chars(text)
        assert len(result) == 2
        assert result[0]["position"] == 1
        assert result[1]["position"] == 2

    def test_mongolian_vowel_separator_detected(self) -> None:
        from general_ludd.language.homoglyph_data import detect_invisible_chars

        result = detect_invisible_chars("\u180e")
        assert len(result) == 1
        assert result[0]["short_name"] == "MVS"

    def test_each_invisible_has_required_fields(self) -> None:
        from general_ludd.language.homoglyph_data import (
            INVISIBLE_CHARACTERS,
            detect_invisible_chars,
        )

        for entry in INVISIBLE_CHARACTERS:
            ch = chr(entry["codepoint"])
            result = detect_invisible_chars(ch)
            assert len(result) == 1, f"Missing: {entry['short_name']}"
            assert result[0]["short_name"] == entry["short_name"]
            assert result[0]["position"] == 0


class TestDetectBidiOverrides:
    def test_empty_string_returns_empty(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        assert detect_bidi_overrides("") == []

    def test_normal_text_no_bidi(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("normal text")
        assert result == []

    def test_lre_detected_with_cve(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("\u202a")
        assert len(result) == 1
        assert result[0]["cve"] == "CVE-2021-42574"
        assert result[0]["codepoint"] == 0x202A

    def test_rlo_detected_with_cve(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("\u202e")
        assert len(result) == 1
        assert result[0]["cve"] == "CVE-2021-42574"
        assert result[0]["codepoint"] == 0x202E

    def test_lri_no_cve(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("\u2066")
        assert len(result) == 1
        assert result[0]["cve"] == ""
        assert result[0]["codepoint"] == 0x2066

    def test_pdi_no_cve(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("\u2069")
        assert len(result) == 1
        assert result[0]["cve"] == ""
        assert result[0]["codepoint"] == 0x2069

    def test_mixed_text_finds_bidi_at_correct_positions(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        text = "a\u202ab\u202ec"
        result = detect_bidi_overrides(text)
        assert len(result) == 2
        assert result[0]["position"] == 1
        assert result[1]["position"] == 3

    def test_all_bidi_chars_in_range(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        bidi_chars = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
        result = detect_bidi_overrides(bidi_chars)
        assert len(result) == 9
        codepoints = {r["codepoint"] for r in result}
        assert codepoints == {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}

    def test_non_bidi_invisible_not_in_bidi_result(self) -> None:
        from general_ludd.language.homoglyph_data import detect_bidi_overrides

        result = detect_bidi_overrides("\u200b")
        assert result == []


class TestDetectMixedScript:
    def test_empty_string_not_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("")
        assert result["is_mixed"] is False
        assert result["scripts"] == []
        assert result["counts"] == {}

    def test_single_script_latin_not_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("Hello World")
        assert result["is_mixed"] is False
        assert result["scripts"] == ["Latin"]

    def test_single_script_cyrillic(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("\u041f\u0440\u0438\u0432\u0435\u0442")
        assert result["is_mixed"] is False
        assert result["scripts"] == ["Cyrillic"]

    def test_mixed_latin_and_cyrillic(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("Hello \u041f\u0440\u0438\u0432\u0435\u0442")
        assert result["is_mixed"] is True
        assert set(result["scripts"]) == {"Latin", "Cyrillic"}

    def test_latin_and_greek_mixed(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("abc \u03b1\u03b2\u03b3")
        assert result["is_mixed"] is True
        assert set(result["scripts"]) == {"Latin", "Greek"}

    def test_common_punctuation_not_counted(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        result = detect_mixed_script("Hello! 123")
        assert result["is_mixed"] is False
        assert result["scripts"] == ["Latin"]

    def test_counts_are_accurate(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        text = "AB\u041f\u0440"
        result = detect_mixed_script(text)
        assert result["is_mixed"] is True
        assert result["counts"]["Latin"] == 2
        assert result["counts"]["Cyrillic"] == 2

    def test_scripts_are_sorted(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        text = "\u0440AB"
        result = detect_mixed_script(text)
        assert result["scripts"] == sorted(result["scripts"])

    def test_armenian_detected_as_script(self) -> None:
        from general_ludd.language.homoglyph_data import detect_mixed_script

        text = "\u0531\u0532\u0533"
        result = detect_mixed_script(text)
        assert result["scripts"] == ["Armenian"]


class TestGenerateSkeleton:
    def test_empty_string_returns_empty(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("") == ""

    def test_pure_ascii_unchanged(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("Hello World") == "Hello World"

    def test_cyrillic_a_normalized_to_latin_a(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("\u0430") == "a"

    def test_greek_alpha_normalized_to_A(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("\u0391") == "A"

    def test_cyrillic_spoofed_apple(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        spoofed = "\u0430\u0440\u0440l\u0435"
        assert generate_skeleton(spoofed) == "apple"

    def test_mixed_text_partial_normalization(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        text = "c\u0441an"
        assert generate_skeleton(text) == "ccan"

    def test_numbers_preserved_except_confusable_1(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        assert generate_skeleton("abc23") == "abc23"

    def test_unknown_chars_preserved(self) -> None:
        from general_ludd.language.homoglyph_data import generate_skeleton

        text = "\u4e00\u9ad8"
        assert generate_skeleton(text) == "\u4e00\u9ad8"


class TestIsSuspicious:
    def test_empty_string_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("") is False

    def test_pure_ascii_letters_not_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("normal text") is False

    def test_ascii_with_confusable_digit_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("123") is True

    def test_homoglyph_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u0430") is True

    def test_invisible_char_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u200b") is True

    def test_bidi_override_is_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("\u202e") is True

    def test_mixed_script_not_automatically_suspicious(self) -> None:
        from general_ludd.language.homoglyph_data import is_suspicious

        assert is_suspicious("Hello \u0391\u0392") is True


class TestDataIntegrity:
    def test_homoglyph_groups_have_distinct_skeletons(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS

        skeletons = [g["skeleton"] for g in HOMOGLYPH_GROUPS]
        assert len(skeletons) == len(set(skeletons))

    def test_homoglyph_groups_have_codepoint_uniqueness(self) -> None:
        from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS

        all_cps: list[int] = []
        for g in HOMOGLYPH_GROUPS:
            for cp, _name in g["characters"]:
                all_cps.append(cp)
        unique = set(all_cps)
        dupes = [cp for cp in unique if all_cps.count(cp) > 1]
        assert len(dupes) <= 5, f"Too many shared codepoints: {dupes}"

    def test_invisible_characters_have_unique_codepoints(self) -> None:
        from general_ludd.language.homoglyph_data import INVISIBLE_CHARACTERS

        codepoints = [c["codepoint"] for c in INVISIBLE_CHARACTERS]
        assert len(codepoints) == len(set(codepoints))

    def test_attack_vectors_all_non_empty(self) -> None:
        from general_ludd.language.homoglyph_data import ATTACK_VECTORS

        for key, desc in ATTACK_VECTORS.items():
            assert len(desc) > 20, f"Attack vector {key} description too short"

    def test_skeleton_map_covers_all_homoglyph_codepoints(self) -> None:
        from general_ludd.language.homoglyph_data import (
            _SKELETON_MAP,
            HOMOGLYPH_GROUPS,
        )

        for group in HOMOGLYPH_GROUPS:
            for cp, _name in group["characters"]:
                assert cp in _SKELETON_MAP, f"CP {cp:04X} missing from skeleton map"

    def test_invisible_set_covers_all_entries(self) -> None:
        from general_ludd.language.homoglyph_data import (
            _INVISIBLE_SET,
            INVISIBLE_CHARACTERS,
        )

        for entry in INVISIBLE_CHARACTERS:
            assert entry["codepoint"] in _INVISIBLE_SET

    def test_bidi_set_has_nine_known_codepoints(self) -> None:
        from general_ludd.language.homoglyph_data import _BIDI_OVERRIDE_CODEPOINTS

        assert len(_BIDI_OVERRIDE_CODEPOINTS) == 9

    def test_bidi_embedding_range_has_cve(self) -> None:
        from general_ludd.language.homoglyph_data import _BIDI_OVERRIDE_CODEPOINTS

        embedding_range = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E}
        assert embedding_range.issubset(_BIDI_OVERRIDE_CODEPOINTS)

    def test_skeleton_map_is_non_empty(self) -> None:
        from general_ludd.language.homoglyph_data import _SKELETON_MAP

        assert len(_SKELETON_MAP) > 20

    def test_invisible_set_is_non_empty(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET

        assert len(_INVISIBLE_SET) == len(_INVISIBLE_SET)
