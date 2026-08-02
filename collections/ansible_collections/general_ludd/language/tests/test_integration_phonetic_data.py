import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

from general_ludd.language.phonetic_data import (
    ARPABET_TO_IPA,
    CMU_DICT_SUBSET,
    DOUBLE_METAPHONE,
    IPA_CONSONANTS,
    IPA_TO_ARPABET,
    IPA_VOWELS,
    METAPHONE_EXCEPTIONS,
    SOUNDEX_MAPPING,
)


def test_cmu_dict_hello():
    assert 'HELLO' in CMU_DICT_SUBSET
    assert isinstance(CMU_DICT_SUBSET['HELLO'], list)
    assert len(CMU_DICT_SUBSET['HELLO']) >= 1
    assert isinstance(CMU_DICT_SUBSET['HELLO'][0], str)


def test_cmu_dict_world():
    assert 'WORLD' in CMU_DICT_SUBSET


def test_arpabet_to_ipa():
    assert 'AA' in ARPABET_TO_IPA
    assert ARPABET_TO_IPA['AA'] == '\u0251'
    assert 'B' in ARPABET_TO_IPA


def test_ipa_to_arpabet_reverse():
    assert len(IPA_TO_ARPABET) == len(ARPABET_TO_IPA)
    for arpabet, ipa in ARPABET_TO_IPA.items():
        assert IPA_TO_ARPABET[ipa] == arpabet


def test_soundex_mapping():
    assert 'b' in SOUNDEX_MAPPING
    assert SOUNDEX_MAPPING['b'] == '1'
    assert SOUNDEX_MAPPING['f'] == '1'


def test_metaphone_exceptions():
    assert len(METAPHONE_EXCEPTIONS) > 0


def test_double_metaphone():
    assert len(DOUBLE_METAPHONE) > 0


def test_ipa_vowels():
    assert len(IPA_VOWELS) > 0
    entry = IPA_VOWELS[0]
    assert 'ipa' in entry
    assert 'arpabet' in entry
    assert 'description' in entry


def test_ipa_consonants():
    assert len(IPA_CONSONANTS) > 0
    entry = IPA_CONSONANTS[0]
    assert 'ipa' in entry
    assert 'arpabet' in entry
    assert 'description' in entry


def test_phoneme_count():
    assert len(IPA_VOWELS) >= 10
    assert len(IPA_CONSONANTS) >= 10
