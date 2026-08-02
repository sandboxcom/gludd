import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

from general_ludd.language.charset_map import (
    ALL_ENCODINGS,
    BOM_BY_SEQUENCE,
    BOM_SIGNATURES,
    BOM_SIZE,
    CHARDET_CONFIDENCE_THRESHOLDS,
    CJK_ENCODINGS,
    MOJIBAKE_SIGNATURES,
    SINGLE_BYTE_ENCODINGS,
    WINDOWS_CODE_PAGES,
)


def test_bom_utf8_signature():
    assert BOM_SIGNATURES['UTF-8'] == b'\xef\xbb\xbf'


def test_bom_reverse_lookup():
    assert isinstance(BOM_BY_SEQUENCE, dict)
    assert b'\xef\xbb\xbf' in BOM_BY_SEQUENCE
    assert BOM_BY_SEQUENCE[b'\xef\xbb\xbf'] == 'UTF-8'


def test_bom_size():
    assert 'UTF-8' in BOM_SIZE
    assert BOM_SIZE['UTF-8'] == 3
    assert BOM_SIZE['UTF-16-BE'] == 2
    assert BOM_SIZE['UTF-32-BE'] == 4


def test_bom_in_input():
    data = b'\xef\xbb\xbfHello'
    for sig in BOM_SIGNATURES.values():
        if data.startswith(sig):
            detected = BOM_BY_SEQUENCE[sig]
            assert detected == 'UTF-8'
            return
    assert False, "BOM not detected"


def test_chardet_thresholds():
    assert 'trusted' in CHARDET_CONFIDENCE_THRESHOLDS
    assert CHARDET_CONFIDENCE_THRESHOLDS['trusted'] > 0.8


def test_mojibake_signatures():
    assert isinstance(MOJIBAKE_SIGNATURES, dict)
    assert len(MOJIBAKE_SIGNATURES) > 0


def test_single_byte_encodings():
    assert len(SINGLE_BYTE_ENCODINGS) > 0
    entry = SINGLE_BYTE_ENCODINGS[0]
    assert 'name' in entry
    assert isinstance(entry['name'], str)
    assert 'languages' in entry


def test_all_encodings():
    assert len(ALL_ENCODINGS) > len(SINGLE_BYTE_ENCODINGS)
    assert len(ALL_ENCODINGS) > len(WINDOWS_CODE_PAGES)


def test_windows_code_pages():
    assert len(WINDOWS_CODE_PAGES) > 0
    assert WINDOWS_CODE_PAGES[0]['name'].startswith('windows-')


def test_cjk_encodings():
    cjk_names = {e['name'] for e in CJK_ENCODINGS}
    assert 'Shift_JIS' in cjk_names
    assert 'EUC-JP' in cjk_names
    assert 'GB18030' in cjk_names
