"""Unit tests for src/general_ludd/language/phonetic_data.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from general_ludd.language import phonetic_data


class TestDataIntegrity:
    def test_vowel_count(self) -> None:
        assert len(phonetic_data.IPA_VOWELS) == 27

    def test_consonant_count(self) -> None:
        assert len(phonetic_data.IPA_CONSONANTS) == 26

    def test_every_entry_has_required_keys(self) -> None:
        for entry in phonetic_data.IPA_VOWELS + phonetic_data.IPA_CONSONANTS:
            for key in ("ipa", "xsampa", "arpabet", "description", "examples"):
                assert key in entry, f"Missing key {key!r} in {entry['ipa']!r}"

    def test_no_duplicate_ipa_entries(self) -> None:
        combined = phonetic_data.IPA_VOWELS + phonetic_data.IPA_CONSONANTS
        ipas = [e["ipa"] for e in combined]
        assert len(ipas) == len(set(ipas)), (
            f"Duplicate IPA symbols: {sorted(set(x for x in ipas if ipas.count(x) > 1))}"
        )

    def test_arpabet_to_ipa_has_39_entries(self) -> None:
        assert len(phonetic_data.ARPABET_TO_IPA) == 39

    def test_ipa_to_arpabet_is_inverse(self) -> None:
        assert len(phonetic_data.IPA_TO_ARPABET) == len(phonetic_data.ARPABET_TO_IPA)
        for arpabet, ipa in phonetic_data.ARPABET_TO_IPA.items():
            assert phonetic_data.IPA_TO_ARPABET[ipa] == arpabet

    def test_arpabet_stress_has_three_levels(self) -> None:
        assert set(phonetic_data.ARPABET_STRESS.keys()) == {"0", "1", "2"}

    def test_cmu_dict_subset_non_empty(self) -> None:
        assert len(phonetic_data.CMU_DICT_SUBSET) == 16

    def test_cmu_dict_words_are_uppercase_alpha(self) -> None:
        for word in phonetic_data.CMU_DICT_SUBSET:
            assert word == word.upper()
            assert word.isalpha()


class TestIPARanges:
    def test_vowels_are_within_ipa_range(self) -> None:
        for entry in phonetic_data.IPA_VOWELS:
            char = ord(entry["ipa"])
            valid = (0x0250 <= char <= 0x02AF) or (0x0061 <= char <= 0x007A) or (0x00E6 <= char <= 0x0153)
            assert valid, f"IPA {entry['ipa']!r} U+{char:04X} not in expected range"

    def test_consonants_are_within_ipa_range(self) -> None:
        for entry in phonetic_data.IPA_CONSONANTS:
            char = ord(entry["ipa"])
            valid = (
                (0x0061 <= char <= 0x007A)
                or (0x0250 <= char <= 0x02AF)
                or (0x00F0 <= char <= 0x014B)
                or (0x03B8 <= char <= 0x03B8)
            )
            assert valid, f"IPA {entry['ipa']!r} U+{char:04X} not in expected IPA range"


class TestSoundex:
    def test_basic_names(self) -> None:
        assert phonetic_data.compute_soundex("Robert") == "R163"
        assert phonetic_data.compute_soundex("Rupert") == "R163"
        assert phonetic_data.compute_soundex("Rubin") == "R150"
        assert phonetic_data.compute_soundex("Ashcraft") == "A261"
        assert phonetic_data.compute_soundex("Ashcroft") == "A261"
        assert phonetic_data.compute_soundex("Tymczak") == "T522"
        assert phonetic_data.compute_soundex("Pfister") == "P236"

    def test_empty_and_non_alpha(self) -> None:
        assert phonetic_data.compute_soundex("") == ""
        assert phonetic_data.compute_soundex("123") == ""
        assert phonetic_data.compute_soundex("!@#") == ""

    def test_short_words(self) -> None:
        assert phonetic_data.compute_soundex("A") == "A000"
        assert phonetic_data.compute_soundex("I") == "I000"
        assert phonetic_data.compute_soundex("Mm") == "M000"

    def test_pads_to_four(self) -> None:
        assert len(phonetic_data.compute_soundex("Bob")) == 4
        assert len(phonetic_data.compute_soundex("Al")) == 4

    def test_truncates_to_four(self) -> None:
        result = phonetic_data.compute_soundex("Washington")
        assert len(result) == 4

    def test_drops_h_and_w(self) -> None:
        assert phonetic_data.compute_soundex("Harry") == "H600"
        assert phonetic_data.compute_soundex("Wood") == "W300"

    def test_collapse_adjacent_codes(self) -> None:
        assert phonetic_data.compute_soundex("Jackson") == "J250"

    def test_first_letter_is_code_for_itself(self) -> None:
        result = phonetic_data.compute_soundex("Lloyd")
        assert result[0] == "L"

    def test_returns_uppercase(self) -> None:
        result = phonetic_data.compute_soundex("hello")
        assert result == result.upper()
        assert result[0] == "H"


class TestMetaphone:
    def test_basic_words(self) -> None:
        assert phonetic_data.compute_metaphone("Gnu") == "N"
        assert phonetic_data.compute_metaphone("Write") == "RT"
        assert phonetic_data.compute_metaphone("Fox") == "FKS"
        assert len(phonetic_data.compute_metaphone("Knight")) <= 4
        assert len(phonetic_data.compute_metaphone("Phone")) <= 4

    def test_silent_initial_letters(self) -> None:
        assert phonetic_data.compute_metaphone("Knife") == "NF"
        assert phonetic_data.compute_metaphone("Gnome") == "NM"
        assert phonetic_data.compute_metaphone("Pneumatic")[:3] == "NMT"
        assert len(phonetic_data.compute_metaphone("Wreck")) <= 4

    def test_sch_trigraph(self) -> None:
        assert phonetic_data.compute_metaphone("School") == "SKL"

    def test_wh_initial(self) -> None:
        assert phonetic_data.compute_metaphone("Whale") == "WL"
        assert phonetic_data.compute_metaphone("What") == "WT"

    def test_x_handling(self) -> None:
        assert phonetic_data.compute_metaphone("Fox") == "FKS"

    def test_empty_and_short(self) -> None:
        assert phonetic_data.compute_metaphone("") == ""
        assert phonetic_data.compute_metaphone("A") == "A"
        assert phonetic_data.compute_metaphone("I") == "I"

    def test_stops_at_four(self) -> None:
        result = phonetic_data.compute_metaphone("Psychotherapy")
        assert len(result) <= 4

    def test_non_alpha_characters_skipped(self) -> None:
        result = phonetic_data.compute_metaphone("Oh-Ho")
        assert result == "OHH"


class TestDoubleMetaphone:
    def test_basic_words(self) -> None:
        p, a = phonetic_data.compute_double_metaphone("Knight")
        assert len(p) <= 4
        assert len(a) <= 4

        p2, a2 = phonetic_data.compute_double_metaphone("Gnu")
        assert p2 == "N"
        assert a2 == "N"

    def test_sch_alternate(self) -> None:
        p, a = phonetic_data.compute_double_metaphone("School")
        assert p == "SKL"
        assert a == "XL"

    def test_empty_input(self) -> None:
        p, a = phonetic_data.compute_double_metaphone("")
        assert p == ""
        assert a == ""

    def test_ae_prefix(self) -> None:
        _p, a = phonetic_data.compute_double_metaphone("Aerie")
        assert a[:1] == "E"

    def test_returns_tuple(self) -> None:
        result = phonetic_data.compute_double_metaphone("Hello")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_alternate_is_uppercase(self) -> None:
        _p, a = phonetic_data.compute_double_metaphone("school")
        assert a == a.upper() if a else True


class TestTranscribeToArpabet:
    def test_known_word(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("hello")
        assert result == "HH AH0 L OW1"

    def test_known_word_multiple_pronunciations_takes_first(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("data")
        assert result == "D EY1 T AH0"

    def test_unknown_word_falls_back_to_uppercase(self) -> None:
        assert phonetic_data.transcribe_to_arpabet("xyzzyt") == "XYZZYT"

    def test_multi_word_phrase(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("hello world")
        assert result == "HH AH0 L OW1 W ER1 L D"

    def test_empty_and_whitespace(self) -> None:
        assert phonetic_data.transcribe_to_arpabet("") == ""
        assert phonetic_data.transcribe_to_arpabet("   ") == ""

    def test_punctuation_stripped(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("hello!")
        assert result == "HH AH0 L OW1"

    def test_all_dict_words(self) -> None:
        for word in phonetic_data.CMU_DICT_SUBSET:
            result = phonetic_data.transcribe_to_arpabet(word)
            assert result != word, f"Word {word!r} should have been found in dict"
            assert len(result) > 0


class TestTranscribeToIpa:
    def test_known_word(self) -> None:
        result = phonetic_data.transcribe_to_ipa("hello")
        assert "h" in result
        assert len(result) > 0

    def test_data_word(self) -> None:
        result = phonetic_data.transcribe_to_ipa("data")
        assert len(result) > 0

    def test_unknown_word_falls_back_to_lowercase(self) -> None:
        assert phonetic_data.transcribe_to_ipa("XYZZY") == "xyzzy"

    def test_empty_and_whitespace(self) -> None:
        assert phonetic_data.transcribe_to_ipa("") == ""
        assert phonetic_data.transcribe_to_ipa("   ") == ""

    def test_output_has_no_digit_stress_markers(self) -> None:
        for word in phonetic_data.CMU_DICT_SUBSET:
            result = phonetic_data.transcribe_to_ipa(word)
            assert not any(c.isdigit() for c in result), f"Digits in IPA for {word!r}: {result!r}"

    def test_all_dict_words_produce_output(self) -> None:
        for word in phonetic_data.CMU_DICT_SUBSET:
            result = phonetic_data.transcribe_to_ipa(word)
            assert len(result) > 0, f"Empty IPA for {word!r}"

    def test_multi_word_phrase_has_space_separation(self) -> None:
        result = phonetic_data.transcribe_to_ipa("hello world")
        parts = result.split()
        assert len(parts) == 2


class TestSoundexMapping:
    def test_soundex_mapping_covers_basic_consonants(self) -> None:
        assert phonetic_data.SOUNDEX_MAPPING["b"] == "1"
        assert phonetic_data.SOUNDEX_MAPPING["f"] == "1"
        assert phonetic_data.SOUNDEX_MAPPING["c"] == "2"
        assert phonetic_data.SOUNDEX_MAPPING["d"] == "3"
        assert phonetic_data.SOUNDEX_MAPPING["l"] == "4"
        assert phonetic_data.SOUNDEX_MAPPING["m"] == "5"
        assert phonetic_data.SOUNDEX_MAPPING["r"] == "6"

    def test_soundex_vowels_set(self) -> None:
        assert "a" in phonetic_data.SOUNDEX_VOWELS
        assert "y" in phonetic_data.SOUNDEX_VOWELS
        assert len(phonetic_data.SOUNDEX_VOWELS) == 6

    def test_soundex_ignore_set(self) -> None:
        assert "h" in phonetic_data.SOUNDEX_IGNORE
        assert "w" in phonetic_data.SOUNDEX_IGNORE


class TestMetaphoneData:
    def test_metaphone_vowels(self) -> None:
        assert "a" in phonetic_data.METAPHONE_VOWELS
        assert "u" in phonetic_data.METAPHONE_VOWELS
        assert len(phonetic_data.METAPHONE_VOWELS) == 5

    def test_metaphone_exceptions(self) -> None:
        assert phonetic_data.METAPHONE_EXCEPTIONS["gn"] == "n"
        assert phonetic_data.METAPHONE_EXCEPTIONS["kn"] == "n"
        assert phonetic_data.METAPHONE_EXCEPTIONS["pn"] == "n"
        assert phonetic_data.METAPHONE_EXCEPTIONS["wr"] == "r"
        assert phonetic_data.METAPHONE_EXCEPTIONS["wh"] == "w"

    def test_double_metaphone_entries(self) -> None:
        assert phonetic_data.DOUBLE_METAPHONE["sch"][1] == "x"


class TestARPABETLookups:
    def test_common_mappings(self) -> None:
        assert phonetic_data.ARPABET_TO_IPA["IY"] == "i"
        assert phonetic_data.ARPABET_TO_IPA["UW"] == "u"
        assert phonetic_data.ARPABET_TO_IPA["AE"] == "\u00e6"
        assert phonetic_data.ARPABET_TO_IPA["TH"] == "\u03b8"

    def test_ipa_to_arpabet_reverse(self) -> None:
        assert phonetic_data.IPA_TO_ARPABET["i"] == "IY"
        assert phonetic_data.IPA_TO_ARPABET["u"] == "UW"
        assert phonetic_data.IPA_TO_ARPABET["\u02a7"] == "CH"

    def test_no_empty_keys_or_values(self) -> None:
        for k, v in phonetic_data.ARPABET_TO_IPA.items():
            assert k and v, f"Empty key or value in ARPABET_TO_IPA: {k!r} -> {v!r}"

    def test_all_arpabet_values_are_one_or_two_chars(self) -> None:
        for arpabet, ipa in phonetic_data.ARPABET_TO_IPA.items():
            if arpabet not in ("AW", "AY", "EY", "OW", "OY"):
                assert len(ipa) == 1, f"ARPABET {arpabet!r} maps to multi-char IPA {ipa!r}"


class TestPhoneticAlgorithmsRoundTrip:
    def test_soundex_knuth_examples(self) -> None:
        test_cases = [
            ("Robert", "R163"),
            ("Rupert", "R163"),
            ("Euler", "E460"),
            ("Ellery", "E460"),
            ("Gauss", "G200"),
            ("Ghosh", "G200"),
            ("Hilbert", "H416"),
            ("Heilbronn", "H416"),
            ("Knuth", "K530"),
            ("Kant", "K530"),
            ("Lloyd", "L300"),
            ("Ladd", "L300"),
            ("Lukasiewicz", "L222"),
            ("Lissajous", "L222"),
        ]
        for name, expected in test_cases:
            assert phonetic_data.compute_soundex(name) == expected, f"Soundex({name!r}) expected {expected}"


class TestEdgeCases:
    def test_metaphone_ps_prefix(self) -> None:
        result = phonetic_data.compute_metaphone("Psalm")
        assert result[0] == "S"

    def test_metaphone_ae_prefix(self) -> None:
        result = phonetic_data.compute_metaphone("Aerial")
        assert result[:1] == "E"

    def test_transcribe_arpabet_with_numbers_and_punctuation(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("hello123world")
        assert result == "HELLOWORLD"
        result = phonetic_data.transcribe_to_arpabet("hello world!")
        assert result == "HH AH0 L OW1 W ER1 L D"

    def test_metaphone_case_insensitive(self) -> None:
        assert phonetic_data.compute_metaphone("Knight") == phonetic_data.compute_metaphone("knight")

    def test_double_metaphone_case_insensitive(self) -> None:
        p1, a1 = phonetic_data.compute_double_metaphone("School")
        p2, a2 = phonetic_data.compute_double_metaphone("school")
        assert p1 == p2
        assert a1 == a2

    def test_transcribe_arpabet_unknown_multi_word(self) -> None:
        result = phonetic_data.transcribe_to_arpabet("foo bar baz")
        assert result == "FOO BAR BAZ"

    def test_transcribe_to_ipa_unknown_multi_word(self) -> None:
        result = phonetic_data.transcribe_to_ipa("foo bar baz")
        assert result == "foo bar baz"


class TestVowelChartCompleteness:
    def test_vowels_cover_all_heights(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_VOWELS}
        assert any("close" in d or "near-close" in d for d in descriptions)
        assert any("close-mid" in d for d in descriptions)
        assert any("open-mid" in d for d in descriptions)
        assert any("open" in d or "near-open" in d for d in descriptions)

    def test_vowels_have_front_central_back(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_VOWELS}
        assert any("front" in d for d in descriptions)
        assert any("central" in d for d in descriptions)
        assert any("back" in d for d in descriptions)

    def test_vowels_have_rounded_and_unrounded(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_VOWELS}
        assert any("rounded" in d for d in descriptions)
        assert any("unrounded" in d for d in descriptions)


class TestConsonantChartCompleteness:
    def test_consonants_cover_all_manners(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_CONSONANTS}
        assert any("plosive" in d for d in descriptions)
        assert any("fricative" in d for d in descriptions)
        assert any("nasal" in d for d in descriptions)
        assert any("approximant" in d or "lateral" in d or "trill" in d for d in descriptions)
        assert any("affricate" in d for d in descriptions)

    def test_consonants_cover_all_places(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_CONSONANTS}
        assert any("bilabial" in d for d in descriptions)
        assert any("labiodental" in d or "dental" in d for d in descriptions)
        assert any("alveolar" in d for d in descriptions)
        assert any("postalveolar" in d for d in descriptions)
        assert any("velar" in d for d in descriptions)
        assert any("glottal" in d for d in descriptions)

    def test_consonants_have_voiced_and_voiceless(self) -> None:
        descriptions = {e["description"] for e in phonetic_data.IPA_CONSONANTS}
        assert any("voiced" in d for d in descriptions)
        assert any("voiceless" in d for d in descriptions)
