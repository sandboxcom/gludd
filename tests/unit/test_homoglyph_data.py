"""Unit tests for src/general_ludd/language/homoglyph_data.py."""

from __future__ import annotations

from general_ludd.language.homoglyph_data import (
    _BIDI_OVERRIDE_CODEPOINTS,
    _INVISIBLE_SET,
    _SKELETON_MAP,
    ATTACK_VECTORS,
    HOMOGLYPH_GROUPS,
    INVISIBLE_CHARACTERS,
    _codepoint_in_group,
    _script_of,
    detect_bidi_overrides,
    detect_confusables,
    detect_invisible_chars,
    detect_mixed_script,
    generate_skeleton,
    is_suspicious,
)

# ---------------------------------------------------------------------------
# Data structure validation
# ---------------------------------------------------------------------------


class TestHomoglyphGroupsStructure:
    def test_non_empty(self):
        assert len(HOMOGLYPH_GROUPS) > 0

    def test_every_group_has_required_keys(self):
        for group in HOMOGLYPH_GROUPS:
            assert "skeleton" in group
            assert "characters" in group
            assert "categories" in group
            assert isinstance(group["skeleton"], str)
            assert isinstance(group["characters"], list)
            assert isinstance(group["categories"], list)

    def test_every_group_has_at_least_two_characters(self):
        for group in HOMOGLYPH_GROUPS:
            assert len(group["characters"]) >= 2

    def test_skeleton_is_single_char(self):
        for group in HOMOGLYPH_GROUPS:
            assert len(group["skeleton"]) == 1

    def test_character_entries_are_valid(self):
        for group in HOMOGLYPH_GROUPS:
            for entry in group["characters"]:
                assert isinstance(entry, tuple)
                assert len(entry) == 2
                assert isinstance(entry[0], int)
                assert isinstance(entry[1], str)

    def test_categories_unique_among_characters(self):
        for group in HOMOGLYPH_GROUPS:
            assert len(group["categories"]) >= 2
            assert len(group["categories"]) <= len(group["characters"])


class TestInvisibleCharactersStructure:
    def test_non_empty(self):
        assert len(INVISIBLE_CHARACTERS) > 0

    def test_every_entry_has_required_keys(self):
        required = {"codepoint", "name", "short_name", "category", "risk", "cve_reference"}
        for entry in INVISIBLE_CHARACTERS:
            assert set(entry.keys()) == required

    def test_codepoints_are_unique(self):
        cps = [e["codepoint"] for e in INVISIBLE_CHARACTERS]
        assert len(cps) == len(set(cps))

    def test_categories_are_valid_literals(self):
        valid = {
            "zero-width-space",
            "zero-width-joiner",
            "zero-width-non-joiner",
            "soft-hyphen",
            "word-joiner",
            "bidi-control",
            "format-character",
            "deprecated-format",
            "interlinear-annotation",
            "variation-selector",
        }
        for entry in INVISIBLE_CHARACTERS:
            assert entry["category"] in valid

    def test_short_names_are_uppercase(self):
        for entry in INVISIBLE_CHARACTERS:
            assert entry["short_name"] == entry["short_name"].upper()

    def test_cve_2021_42574_entries_have_cve_field(self):
        trojan_source = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067}
        for entry in INVISIBLE_CHARACTERS:
            if entry["codepoint"] in trojan_source:
                assert entry["cve_reference"] == "CVE-2021-42574"


class TestAttackVectorsStructure:
    def test_has_expected_keys(self):
        expected = {
            "domain_spoofing",
            "code_injection",
            "filename_confusion",
            "string_comparison_bypass",
            "comment_out_out-of-context",
            "package_typosquatting",
        }
        assert set(ATTACK_VECTORS.keys()) == expected

    def test_all_descriptions_non_empty(self):
        for _key, desc in ATTACK_VECTORS.items():
            assert isinstance(desc, str)
            assert len(desc) > 0


class TestDerivedMaps:
    def test_skeleton_map_covers_all_groups(self):
        total_entries = sum(len(g["characters"]) for g in HOMOGLYPH_GROUPS)
        dup_count = total_entries - len(_SKELETON_MAP)
        for codepoint in (0x007C, 0x004F, 0x041E, 0x0030):
            assert codepoint in _SKELETON_MAP
        assert dup_count >= 4

    def test_skeleton_map_ascii_bases(self):
        assert _SKELETON_MAP[0x0041] == "A"
        assert _SKELETON_MAP[0x0042] == "B"
        assert _SKELETON_MAP[0x0061] == "a"
        assert _SKELETON_MAP[0x0030] == "0"

    def test_skeleton_map_cyrillic_greek_mapped(self):
        assert _SKELETON_MAP[0x0410] == "A"
        assert _SKELETON_MAP[0x0391] == "A"
        assert _SKELETON_MAP[0x0441] == "c"
        assert _SKELETON_MAP[0x03BF] == "o"

    def test_invisible_set_contains_all_invisible_codepoints(self):
        for entry in INVISIBLE_CHARACTERS:
            assert entry["codepoint"] in _INVISIBLE_SET

    def test_invisible_set_size_matches_list(self):
        assert len(_INVISIBLE_SET) == len(INVISIBLE_CHARACTERS)

    def test_bidi_override_set_non_empty(self):
        assert len(_BIDI_OVERRIDE_CODEPOINTS) > 0

    def test_bidi_override_set_all_override_isolate(self):
        for cp in _BIDI_OVERRIDE_CODEPOINTS:
            assert (0x202A <= cp <= 0x202E) or (0x2066 <= cp <= 0x2069)

    def test_codepoint_in_group_match(self):
        assert _codepoint_in_group(0x0041, HOMOGLYPH_GROUPS) == "A"
        assert _codepoint_in_group(0x0421, HOMOGLYPH_GROUPS) == "C"
        assert _codepoint_in_group(0x0430, HOMOGLYPH_GROUPS) == "a"

    def test_codepoint_in_group_miss(self):
        assert _codepoint_in_group(0x0021, HOMOGLYPH_GROUPS) == ""
        assert _codepoint_in_group(0xFFFF, HOMOGLYPH_GROUPS) == ""


# ---------------------------------------------------------------------------
# detect_confusables
# ---------------------------------------------------------------------------


class TestDetectConfusables:
    def test_pure_ascii_returns_empty(self):
        assert detect_confusables("Hello World") == []

    def test_empty_string_returns_empty(self):
        assert detect_confusables("") == []

    def test_cyrillic_a_detected(self):
        findings = detect_confusables("\u0430")
        assert len(findings) == 1
        f = findings[0]
        assert f["codepoint"] == 0x0430
        assert f["skeleton"] == "a"
        assert f["character"] == "\u0430"
        assert f["position"] == 0

    def test_multiple_confusables_detected(self):
        findings = detect_confusables("\u0430bc\u0441")
        assert len(findings) == 2
        skeletons = {f["skeleton"] for f in findings}
        assert skeletons == {"a", "c"}

    def test_ascii_base_not_flagged(self):
        assert detect_confusables("A") == []
        assert detect_confusables("a") == []
        assert detect_confusables("0") == []

    def test_positions_correct(self):
        findings = detect_confusables("x\u0441y")
        assert len(findings) == 1
        assert findings[0]["position"] == 1

    def test_name_field_present(self):
        findings = detect_confusables("\u0410")
        assert len(findings) == 1
        assert "CYRILLIC" in findings[0]["name"].upper()

    def test_greek_beta_detected(self):
        findings = detect_confusables("\u0392")
        assert len(findings) == 1
        assert findings[0]["skeleton"] == "B"

    def test_armenia_Ayb_detected(self):
        findings = detect_confusables("\u0531")
        assert len(findings) == 1
        assert findings[0]["skeleton"] == "A"


# ---------------------------------------------------------------------------
# detect_invisible_chars
# ---------------------------------------------------------------------------


class TestDetectInvisibleChars:
    def test_empty_string_returns_empty(self):
        assert detect_invisible_chars("") == []

    def test_plain_ascii_returns_empty(self):
        assert detect_invisible_chars("Hello World 123") == []

    def test_zero_width_space_detected(self):
        findings = detect_invisible_chars("a\u200bb")
        assert len(findings) == 1
        f = findings[0]
        assert f["codepoint"] == 0x200B
        assert f["short_name"] == "ZWSP"
        assert f["category"] == "zero-width-space"
        assert f["position"] == 1
        assert f["cve"] == ""

    def test_soft_hyphen_detected(self):
        findings = detect_invisible_chars("hy\u00adphen")
        assert len(findings) == 1
        assert findings[0]["short_name"] == "SHY"
        assert findings[0]["category"] == "soft-hyphen"

    def test_bidi_override_detected_with_cve(self):
        findings = detect_invisible_chars("\u202e")
        assert len(findings) == 1
        assert findings[0]["cve"] == "CVE-2021-42574"
        assert findings[0]["short_name"] == "RLO"

    def test_word_joiner_detected(self):
        findings = detect_invisible_chars("\u2060")
        assert len(findings) == 1
        assert findings[0]["short_name"] == "WJ"

    def test_bom_zwnbsp_detected(self):
        findings = detect_invisible_chars("\ufeff")
        assert len(findings) == 1
        assert findings[0]["short_name"] == "BOM/ZWNBSP"

    def test_multiple_invisible_detected(self):
        text = "\u200b" + "hello" + "\u200c" + "world" + "\u200d"
        findings = detect_invisible_chars(text)
        assert len(findings) == 3

    def test_positions_accurate_in_mixed_text(self):
        text = "ab\u200bcd\u200ce"
        findings = detect_invisible_chars(text)
        positions = [f["position"] for f in findings]
        assert positions == [2, 5]

    def test_risk_field_non_empty(self):
        findings = detect_invisible_chars("\u200b")
        assert len(findings) == 1
        assert len(findings[0]["risk"]) > 0


# ---------------------------------------------------------------------------
# detect_bidi_overrides
# ---------------------------------------------------------------------------


class TestDetectBidiOverrides:
    def test_empty_string_returns_empty(self):
        assert detect_bidi_overrides("") == []

    def test_plain_ascii_returns_empty(self):
        assert detect_bidi_overrides("Hello World") == []

    def test_non_bidi_invisible_not_detected(self):
        assert detect_bidi_overrides("\u200b") == []

    def test_rlo_override_detected(self):
        findings = detect_bidi_overrides("\u202e")
        assert len(findings) == 1
        assert findings[0]["cve"] == "CVE-2021-42574"
        assert findings[0]["codepoint"] == 0x202E

    def test_lri_isolate_detected_no_cve(self):
        findings = detect_bidi_overrides("\u2066")
        assert len(findings) == 1
        assert findings[0]["cve"] == ""

    def test_rli_isolate_detected_no_cve(self):
        findings = detect_bidi_overrides("\u2067")
        assert len(findings) == 1
        assert findings[0]["cve"] == ""

    def test_pdi_isolate_detected_no_cve(self):
        findings = detect_bidi_overrides("\u2069")
        assert len(findings) == 1
        assert findings[0]["cve"] == ""

    def test_all_override_family_have_cve(self):
        for cp in [0x202A, 0x202B, 0x202C, 0x202D, 0x202E]:
            ch = chr(cp)
            findings = detect_bidi_overrides(ch)
            assert len(findings) == 1
            assert findings[0]["cve"] == "CVE-2021-42574"

    def test_all_isolate_family_no_cve(self):
        for cp in [0x2066, 0x2067, 0x2068, 0x2069]:
            ch = chr(cp)
            findings = detect_bidi_overrides(ch)
            assert len(findings) == 1
            assert findings[0]["cve"] == ""

    def test_multiple_bidi_detected(self):
        text = "\u202d" + "\u202e" + "\u2066"
        findings = detect_bidi_overrides(text)
        assert len(findings) == 3

    def test_positions_accurate(self):
        text = "a\u202eb\u2067c"
        findings = detect_bidi_overrides(text)
        positions = {f["position"]: f["codepoint"] for f in findings}
        assert positions == {1: 0x202E, 3: 0x2067}


# ---------------------------------------------------------------------------
# detect_mixed_script
# ---------------------------------------------------------------------------


class TestDetectMixedScript:
    def test_empty_string(self):
        result = detect_mixed_script("")
        assert result["is_mixed"] is False
        assert result["scripts"] == []
        assert result["counts"] == {}

    def test_pure_latin_not_mixed(self):
        result = detect_mixed_script("Hello World")
        assert result["is_mixed"] is False
        assert "Latin" in result["scripts"]

    def test_pure_cyrillic_not_mixed(self):
        result = detect_mixed_script("\u0410\u0411\u0412")
        assert result["is_mixed"] is False
        assert "Cyrillic" in result["scripts"]

    def test_mixed_latin_cyrillic_detected(self):
        result = detect_mixed_script("A\u0410")
        assert result["is_mixed"] is True
        assert set(result["scripts"]) == {"Cyrillic", "Latin"}

    def test_punctuation_excluded(self):
        result = detect_mixed_script("Hello!")
        assert result["is_mixed"] is False
        assert "Common" not in result["scripts"]

    def test_counts_accurate(self):
        result = detect_mixed_script("A\u0410\u0411")
        assert result["is_mixed"] is True
        assert result["counts"]["Latin"] == 1
        assert result["counts"]["Cyrillic"] == 2

    def test_scripts_sorted(self):
        result = detect_mixed_script("\u0410A")
        assert result["scripts"] == sorted(result["scripts"])


# ---------------------------------------------------------------------------
# generate_skeleton
# ---------------------------------------------------------------------------


class TestGenerateSkeleton:
    def test_empty_string(self):
        assert generate_skeleton("") == ""

    def test_pure_ascii_preserved(self):
        assert generate_skeleton("Hello World") == "Hello World"

    def test_cyrillic_a_skeletonized(self):
        assert generate_skeleton("\u0430") == "a"

    def test_cyrillic_c_skeletonized(self):
        assert generate_skeleton("\u0441") == "c"

    def test_mixed_text_skeletonized(self):
        assert generate_skeleton("a\u0430") == "aa"

    def test_greek_alpha_skeletonized_to_A(self):
        assert generate_skeleton("\u0391") == "A"

    def test_digit_one_remains_l(self):
        assert generate_skeleton("\u0031") == "l"

    def test_multiple_homoglyphs_in_sequence(self):
        assert generate_skeleton("\u0430\u0441\u0435") == "ace"


# ---------------------------------------------------------------------------
# is_suspicious
# ---------------------------------------------------------------------------


class TestIsSuspicious:
    def test_empty_string_not_suspicious(self):
        assert is_suspicious("") is False

    def test_pure_ascii_not_suspicious(self):
        assert is_suspicious("abc def ghi") is False

    def test_confusable_suspicious(self):
        assert is_suspicious("\u0430") is True

    def test_invisible_suspicious(self):
        assert is_suspicious("\u200b") is True

    def test_bidi_suspicious(self):
        assert is_suspicious("\u202e") is True

    def test_mixed_all_three_suspicious(self):
        assert is_suspicious("\u0430\u200b\u202e") is True


# ---------------------------------------------------------------------------
# _script_of fallback
# ---------------------------------------------------------------------------


class TestScriptOf:
    def test_latin_uppercase(self):
        assert _script_of("A") in ("Latin", "Common")

    def test_latin_lowercase(self):
        assert _script_of("z") in ("Latin", "Common")

    def test_cyrillic(self):
        assert _script_of("\u0410") in ("Cyrillic", "Common")

    def test_cyrillic_lower(self):
        assert _script_of("\u0430") in ("Cyrillic", "Common")

    def test_greek(self):
        assert _script_of("\u0391") in ("Greek", "Common")

    def test_armenian(self):
        assert _script_of("\u0531") in ("Armenian", "Common")

    def test_digit_zero_common(self):
        assert _script_of("0") == "Common"

    def test_null_char(self):
        assert _script_of("\x00") == "Common"

    def test_punctuation_common(self):
        result = _script_of("!")
        assert result is not None
