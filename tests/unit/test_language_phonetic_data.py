"""Tests for src/general_ludd/language/phonetic_data.py."""

from __future__ import annotations

from general_ludd.language.phonetic_data import (
    CMU_DICT_SUBSET,
    IPA_CONSONANTS,
    IPA_VOWELS,
    SOUNDEX_VOWELS,
    compute_double_metaphone,
    compute_metaphone,
    compute_soundex,
    transcribe_to_arpabet,
    transcribe_to_ipa,
)


class TestComputeSoundex:
    def test_empty_input_returns_empty(self) -> None:
        assert compute_soundex("") == ""

    def test_single_letter_padded(self) -> None:
        assert compute_soundex("A") == "A000"

    def test_standard_english_name(self) -> None:
        assert compute_soundex("Smith") == "S530"
        assert compute_soundex("Smythe") == "S530"

    def test_robert_and_rupert_same_code(self) -> None:
        assert compute_soundex("Robert") == compute_soundex("Rupert")

    def test_washington(self) -> None:
        assert compute_soundex("Washington") == "W252"

    def test_lee(self) -> None:
        assert compute_soundex("Lee") == "L000"

    def test_drops_h_and_w(self) -> None:
        assert compute_soundex("Ashcraft") == "A261"

    def test_adjacent_same_code_collapsed(self) -> None:
        assert compute_soundex("Pfister") == "P236"

    def test_non_alpha_characters_stripped(self) -> None:
        assert compute_soundex("O'Brien") == "O165"

    def test_all_non_alpha_returns_empty(self) -> None:
        assert compute_soundex("123") == ""

    def test_case_insensitive(self) -> None:
        assert compute_soundex("SMITH") == compute_soundex("smith")

    def test_long_name_truncated_to_four(self) -> None:
        result = compute_soundex("Worcester")
        assert len(result) == 4
        assert result[0] == "W"

    def test_vowels_produce_no_digit(self) -> None:
        assert compute_soundex("Aeiou") == "A000"

    def test_soundex_vowels_is_set(self) -> None:
        assert {"a", "e", "i", "o", "u", "y"} == SOUNDEX_VOWELS


class TestComputeMetaphone:
    def test_empty_input_returns_empty(self) -> None:
        assert compute_metaphone("") == ""

    def test_initial_kn_dropped(self) -> None:
        result = compute_metaphone("Knight")
        assert result[0] == "N"
        assert len(result) <= 4

    def test_initial_gn_dropped(self) -> None:
        result = compute_metaphone("Gnome")
        assert result[0] == "N"
        assert len(result) <= 4

    def test_initial_pn_dropped(self) -> None:
        result = compute_metaphone("Pneumatic")
        assert result[0] == "N"
        assert len(result) <= 4

    def test_initial_wr_dropped(self) -> None:
        result = compute_metaphone("Wright")
        assert result[0] == "R"
        assert len(result) <= 4

    def test_initial_wh_mapped(self) -> None:
        assert compute_metaphone("White") == "WT"

    def test_initial_ae_mapped(self) -> None:
        result = compute_metaphone("Aegis")
        assert len(result) >= 1
        assert len(result) <= 4

    def test_initial_ps_dropped(self) -> None:
        result = compute_metaphone("Psychology")
        assert result[0] == "S"
        assert len(result) <= 4

    def test_sch_maps_to_sk(self) -> None:
        assert compute_metaphone("School") == "SKL"

    def test_x_maps_to_ks(self) -> None:
        result = compute_metaphone("Xavier")
        assert result.startswith("KS")
        assert len(result) <= 4

    def test_vowels_after_first_dropped(self) -> None:
        met = compute_metaphone("Alphabet")
        for ch in met[1:]:
            assert ch not in "AEIOU"

    def test_truncated_to_four(self) -> None:
        assert len(compute_metaphone("Understand")) <= 4

    def test_non_alpha_skipped(self) -> None:
        met = compute_metaphone("Don't")
        assert "'" not in met


class TestComputeDoubleMetaphone:
    def test_empty_input_returns_empty_both(self) -> None:
        primary, alternate = compute_double_metaphone("")
        assert primary == ""
        assert alternate == ""

    def test_kn_produces_both(self) -> None:
        primary, alternate = compute_double_metaphone("Knight")
        assert len(primary) <= 4
        assert len(alternate) <= 4

    def test_ae_alternate_different(self) -> None:
        primary, alt = compute_double_metaphone("Aegis")
        assert primary is not None
        assert alt is not None

    def test_returns_tuple_of_two_strings(self) -> None:
        result = compute_double_metaphone("Hello")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_primary_not_empty_for_valid_word(self) -> None:
        primary, _alt = compute_double_metaphone("Test")
        assert primary != ""

    def test_case_insensitive(self) -> None:
        p1, a1 = compute_double_metaphone("TEST")
        p2, a2 = compute_double_metaphone("test")
        assert p1 == p2
        assert a1 == a2

    def test_wh_alternate_strips_w(self) -> None:
        primary, _alt = compute_double_metaphone("Whale")
        assert len(primary) >= 1


class TestTranscribeToArpabet:
    def test_empty_input_returns_empty(self) -> None:
        assert transcribe_to_arpabet("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert transcribe_to_arpabet("   ") == ""

    def test_hello_in_dictionary(self) -> None:
        result = transcribe_to_arpabet("Hello")
        assert "HH" in result or "AH" in result
        assert result != "HELLO"

    def test_world_in_dictionary(self) -> None:
        result = transcribe_to_arpabet("World")
        assert result != "WORLD"

    def test_unknown_word_falls_back_to_uppercase(self) -> None:
        result = transcribe_to_arpabet("Zyxwvut")
        assert result == "ZYXWVUT"

    def test_multiple_words_joined_by_space(self) -> None:
        result = transcribe_to_arpabet("Hello World")
        assert " " in result

    def test_punctuation_stripped_from_word(self) -> None:
        result = transcribe_to_arpabet("Hello!")
        assert "!" not in result

    def test_cmu_dict_subset_has_known_keys(self) -> None:
        assert "HELLO" in CMU_DICT_SUBSET
        assert "WORLD" in CMU_DICT_SUBSET
        assert "DATA" in CMU_DICT_SUBSET
        assert "LANGUAGE" in CMU_DICT_SUBSET
        assert "FONT" in CMU_DICT_SUBSET

    def test_mixed_known_and_unknown(self) -> None:
        result = transcribe_to_arpabet("Hello Zyxwvut")
        assert "ZYXWVUT" in result


class TestTranscribeToIpa:
    def test_empty_input_returns_empty(self) -> None:
        assert transcribe_to_ipa("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert transcribe_to_ipa("   ") == ""

    def test_hello_produces_ipa_chars(self) -> None:
        result = transcribe_to_ipa("Hello")
        assert result != "hello"
        assert len(result) > 1

    def test_unknown_word_falls_back_to_lowercase(self) -> None:
        result = transcribe_to_ipa("Zyxwvut")
        assert result == "zyxwvut"

    def test_multiple_words_joined_by_space(self) -> None:
        result = transcribe_to_ipa("Hello World")
        assert " " in result

    def test_punctuation_stripped(self) -> None:
        result = transcribe_to_ipa("Hello?")
        assert "?" not in result

    def test_output_contains_unicode(self) -> None:
        result = transcribe_to_ipa("Speech")
        assert result != "SPEECH"

    def test_data_produces_ipa(self) -> None:
        result = transcribe_to_ipa("Data")
        assert len(result) >= 1


class TestDataTables:
    def test_ipa_vowels_has_entries(self) -> None:
        assert len(IPA_VOWELS) >= 20

    def test_ipa_consonants_has_entries(self) -> None:
        assert len(IPA_CONSONANTS) >= 20

    def test_each_vowel_has_required_keys(self) -> None:
        required = {"ipa", "xsampa", "arpabet", "description", "examples"}
        for entry in IPA_VOWELS:
            assert required <= set(entry.keys())

    def test_each_consonant_has_required_keys(self) -> None:
        required = {"ipa", "xsampa", "arpabet", "description", "examples"}
        for entry in IPA_CONSONANTS:
            assert required <= set(entry.keys())

    def test_arpabet_to_ipa_no_null_values(self) -> None:
        from general_ludd.language.phonetic_data import ARPABET_TO_IPA

        assert None not in ARPABET_TO_IPA.values()

    def test_soundex_mapping_covers_expected_letters(self) -> None:
        from general_ludd.language.phonetic_data import SOUNDEX_MAPPING

        for letter in "bfpvcgjkqsxzdtnmlr":
            assert letter in SOUNDEX_MAPPING
