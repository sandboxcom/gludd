import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

from general_ludd.language.unicode_data import (
    UNICODE_VERSION_HISTORY,
    UNICODE_CATEGORY_NAMES,
    UNICODE_BLOCK_NAMES,
    UTF8_HEADER_BYTES,
    plane_of,
    is_surrogate,
    is_high_surrogate,
    is_low_surrogate,
    surrogates_to_codepoint,
)


def test_plane_of_bmp():
    assert plane_of(0x0041) == "BMP"


def test_plane_of_smp():
    assert plane_of(0x1F600) == "SMP"


def test_plane_of_unassigned():
    assert plane_of(0x999999) == "UNASSIGNED"


def test_is_surrogate_true():
    assert is_surrogate(0xD800) is True


def test_is_surrogate_false():
    assert is_surrogate(0x0041) is False


def test_high_surrogate():
    assert is_high_surrogate(0xD800) is True
    assert is_high_surrogate(0xDC00) is False


def test_low_surrogate():
    assert is_low_surrogate(0xDC00) is True
    assert is_low_surrogate(0xD800) is False


def test_surrogates_to_codepoint():
    result = surrogates_to_codepoint(0xD83D, 0xDE00)
    assert result == 0x1F600


def test_utf8_header_bytes():
    assert UTF8_HEADER_BYTES[0xF0] == 4


def test_version_history():
    assert isinstance(UNICODE_VERSION_HISTORY, list)
    assert len(UNICODE_VERSION_HISTORY) > 0


def test_category_names():
    assert "Lu" in UNICODE_CATEGORY_NAMES
    assert "Ll" in UNICODE_CATEGORY_NAMES
    assert "Nd" in UNICODE_CATEGORY_NAMES


def test_block_name_lookup():
    assert (0x0000, 0x007F) in UNICODE_BLOCK_NAMES
    assert UNICODE_BLOCK_NAMES[(0x0000, 0x007F)] == "Basic Latin"
