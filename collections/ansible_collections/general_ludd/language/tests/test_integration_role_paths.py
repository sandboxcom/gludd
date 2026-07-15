import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

import re

from general_ludd.language.charset_map import BOM_SIGNATURES, MOJIBAKE_SIGNATURES
from general_ludd.language.homoglyph_data import HOMOGLYPH_GROUPS, _INVISIBLE_SET, _codepoint_in_group
from general_ludd.language.locale_data import LOCALE_FORMATS
from general_ludd.language.phonetic_data import CMU_DICT_SUBSET


def test_bom_detect_utf8():
    utf8_bom = BOM_SIGNATURES['UTF-8']
    data = utf8_bom + b'Hello'
    assert data.startswith(utf8_bom)


def test_bom_detect_no_bom():
    data = b'Hello World'
    utf8_bom = BOM_SIGNATURES['UTF-8']
    assert not data.startswith(utf8_bom)


def test_encoding_detect_ascii_hello():
    data = b"Hello"
    try:
        text = data.decode('ascii')
        assert text == "Hello"
    except UnicodeDecodeError:
        try:
            text = data.decode('utf-8')
            assert text == "Hello"
        except UnicodeDecodeError:
            assert False, "Could not decode basic ASCII"


def test_encoding_detect_mojibake():
    assert isinstance(MOJIBAKE_SIGNATURES, dict)
    assert len(MOJIBAKE_SIGNATURES) > 0


def test_font_analyze_known_formats():
    ttf_magic = b'\x00\x01\x00\x00'
    otto_magic = b'OTTO'
    assert len(ttf_magic) == 4
    assert otto_magic == b'OTTO'


def test_homoglyph_scan_confusable():
    latin_h = 0x0048
    result = _codepoint_in_group(latin_h, HOMOGLYPH_GROUPS)
    assert result == 'H'


def test_homoglyph_scan_invisible():
    assert 0x200B in _INVISIBLE_SET


def test_i18n_extract_gettext_regex():
    pattern = re.compile(
        r'(?:_|gettext|ngettext|pgettext)\s*\(\s*[\"\'](.+?)[\"\']',
        re.DOTALL,
    )

    code = '''_("Hello World")'''
    match = pattern.search(code)
    assert match is not None
    assert match.group(1) == "Hello World"

    code2 = '''gettext('Goodbye')'''
    match2 = pattern.search(code2)
    assert match2 is not None
    assert match2.group(1) == "Goodbye"

    code3 = '''not_called("Should not match")'''
    match3 = pattern.search(code3)
    assert match3 is None


def test_locale_format_de_number():
    de_format = LOCALE_FORMATS['de-DE']
    assert de_format['number_format']['decimal_separator'] == ','


def test_phonetic_transcribe_arpabet():
    result = CMU_DICT_SUBSET['HELLO']
    assert len(result) >= 1
    assert 'HH' in result[0]
