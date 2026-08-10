"""Deep edge-case tests for the transliteration module.

Covers: multi-char matching, ligature decomposition, mixed scripts,
unsupported scripts, punctuation/whitespace/number preservation,
roundtrip reversibility, empty/silent mappings, script tie-breaking,
range-boundary characters, explicit bogus scheme, and Arabic special cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class TestMultiCharMatching:
    def test_devanagari_tri_char_kṣa(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("क्ष")
        assert "kṣ" in result["transliterated_text"]

    def test_devanagari_tri_char_jña(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("ज्ञ")
        assert "jñ" in result["transliterated_text"]

    def test_devanagari_tri_char_tra(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("त्र")
        assert "tr" in result["transliterated_text"]

    def test_devanagari_tri_char_śra(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("श्र")
        assert "śr" in result["transliterated_text"]

    def test_hiragana_digraph_sha(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("しゃ")
        assert result["transliterated_text"] == "sha"

    def test_hiragana_digraph_kyu(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("きゅ")
        assert result["transliterated_text"] == "kyu"

    def test_katakana_digraph_sha(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("シャ")
        assert result["transliterated_text"] == "sha"

    def test_hiragana_digraph_priority_over_individual(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("しや")
        assert result["transliterated_text"] == "shiya"

    def test_no_false_tri_match_across_boundary(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("ककक")
        assert result["transliterated_text"] == "kkk"


class TestArabicLigature:
    def test_arabic_lam_alef_decomposed(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("لا")
        assert result["scheme"] == "arabic-to-latin"
        referenced = result["transliterated_text"]
        assert len(referenced) >= 2

    def test_arabic_with_hamza_variants(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("أإآؤئ")
        assert len(result["transliterated_text"]) > 5


class TestMixedAndUnsupportedScripts:
    def test_mixed_cyrillic_and_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Москва Tokyo")
        assert result["source_script"] == "Cyrillic"
        assert "Moskva" in result["transliterated_text"]
        assert "Tokyo" in result["transliterated_text"]

    def test_no_script_range_defaults_to_latin_identity(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("漢字試験")
        assert result["source_script"] == "Latin"
        assert result["scheme"] == "identity"
        assert result["transliterated_text"] == "漢字試験"

    def test_no_script_range_defaults_to_latin_armenian(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Բարև")
        assert result["source_script"] == "Latin"
        assert result["scheme"] == "identity"

    def test_explicit_bogus_scheme_passthrough(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет", scheme="bogus-nonexistent")
        assert result["scheme"] == "bogus-nonexistent"
        assert result["transliterated_text"] == "Привет"
        assert result["reversible"] is False


class TestPunctuationWhitespaceNumbers:
    def test_punctuation_preserved(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет, мир!")
        assert result["transliterated_text"].startswith("Privet")
        assert "," in result["transliterated_text"]
        assert "!" in result["transliterated_text"]
        assert "mir" in result["transliterated_text"].lower()

    def test_numbers_preserved(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Москва 2024")
        assert "2024" in result["transliterated_text"]

    def test_whitespace_only_text(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("   \t\n  ")
        assert result["source_script"] == "Unknown"
        assert result["transliterated_text"] == ""
        assert result["scheme"] == "none"

    def test_blank_line_with_unicode(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("\u00a0\u2003")
        assert result["source_script"] == "Unknown"
        assert result["transliterated_text"] == ""
        assert result["scheme"] == "none"


class TestRoundtripReversibility:
    def test_cyrillic_roundtrip_hello(self) -> None:
        from general_ludd.language.transliteration import transliterate

        fwd = transliterate("Привет")
        rev = transliterate(fwd["transliterated_text"], target_script="Cyrillic")
        assert rev["transliterated_text"] == "Привет"

    def test_cyrillic_roundtrip_moscow(self) -> None:
        from general_ludd.language.transliteration import transliterate

        fwd = transliterate("Москва")
        rev = transliterate(fwd["transliterated_text"], target_script="Cyrillic")
        assert rev["transliterated_text"] == "Москва"

    def test_greek_roundtrip_tonos_breaks_reversibility(self) -> None:
        from general_ludd.language.transliteration import transliterate

        fwd = transliterate("Καλημέρα")
        rev = transliterate(fwd["transliterated_text"], target_script="Greek")
        assert rev["transliterated_text"] != "Καλημέρα"

    def test_greek_roundtrip_bare_text(self) -> None:
        from general_ludd.language.transliteration import transliterate

        fwd = transliterate("Καλημερα")
        rev = transliterate(fwd["transliterated_text"], target_script="Greek")
        assert rev["transliterated_text"] == "Καλημερα"

    def test_latin_to_cyrillic_reversible_flag(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Privet", target_script="Cyrillic")
        assert result["reversible"] is True
        assert result["scheme"] == "latin-to-cyrillic"

    def test_latin_to_greek_reversible_flag(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Kalimera", target_script="Greek")
        assert result["reversible"] is True


class TestKoreanSilentIEUNG:
    def test_korean_ieung_empty_mapping(self) -> None:
        from general_ludd.language.transliteration import _KOREAN_TO_LATIN

        assert _KOREAN_TO_LATIN["ㅇ"] == ""

    def test_korean_jamo_not_detected_as_hangul(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("ㅇㅏ")
        assert result["source_script"] == "Latin"
        assert result["scheme"] == "identity"


class TestScriptDetectionTiebreak:
    def test_tie_equal_counts_returns_first_dict_key(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("AБ")
        assert result["source_script"] == "Cyrillic"

    def test_equal_counts_with_three(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("AБВ")
        assert result["source_script"] == "Cyrillic"


class TestLatinToCyrillicUnmapped:
    def test_latin_letters_not_in_reverse_table_preserved(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("qwx", target_script="Cyrillic")
        assert result["transliterated_text"] == "qwx"

    def test_mixed_mapped_and_unmapped(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("qwerty", target_script="Cyrillic")
        assert result["source_script"] == "Latin"
        assert "q" in result["transliterated_text"]
        assert "w" in result["transliterated_text"]


class TestLatinToGreekReversible:
    def test_latin_to_greek_alpha(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("a", target_script="Greek")
        assert result["transliterated_text"] == "α"

    def test_latin_to_greek_theta(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("th", target_script="Greek")
        assert result["transliterated_text"] == "θ"


class TestHiraganaKatakanaParallel:
    def test_hiragana_katakana_same_output(self) -> None:
        from general_ludd.language.transliteration import transliterate

        hi = transliterate("か")
        ka = transliterate("カ")
        assert hi["transliterated_text"] == ka["transliterated_text"] == "ka"

    def test_hiragana_dakuten_digraph(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("じゃ")
        assert result["transliterated_text"] == "ja"


class TestResultShapeConsistency:
    def test_all_keys_present_identity(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("abc")
        for key in ("source_text", "source_script", "target_script", "transliterated_text", "scheme", "reversible"):
            assert key in result

    def test_source_text_equals_input(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет")
        assert result["source_text"] == "Привет"

    def test_reversible_only_for_bidirectional(self) -> None:
        from general_ludd.language.transliteration import transliterate

        cyr = transliterate("Привет")
        lat_cyr = transliterate("Privet", target_script="Cyrillic")
        dev = transliterate("नमस्ते")
        assert cyr["reversible"] is True
        assert lat_cyr["reversible"] is True
        assert dev["reversible"] is False


class TestListSchemesEdgeCases:
    def test_reversible_schemes_frozenset(self) -> None:
        from general_ludd.language.transliteration import list_schemes

        schemes = list_schemes()
        reversible = [s for s in schemes if s["reversible"]]
        assert len(reversible) == 4

    def test_every_scheme_has_source_target(self) -> None:
        from general_ludd.language.transliteration import list_schemes

        schemes = list_schemes()
        for s in schemes:
            name = str(s["scheme"])
            parts = name.split("-to-")
            assert str(s["source_script"]).lower() == parts[0]
            assert str(s["target_script"]).lower() == parts[1]
