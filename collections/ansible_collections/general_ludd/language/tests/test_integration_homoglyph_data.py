import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

from general_ludd.language.homoglyph_data import (
    _INVISIBLE_SET,
    ATTACK_VECTORS,
    HOMOGLYPH_GROUPS,
    INVISIBLE_CHARACTERS,
    _codepoint_in_group,
)


def test_homoglyph_groups():
    assert len(HOMOGLYPH_GROUPS) > 0
    entry = HOMOGLYPH_GROUPS[0]
    assert 'skeleton' in entry
    assert 'characters' in entry


def test_invisible_characters():
    assert len(INVISIBLE_CHARACTERS) > 0
    entry = INVISIBLE_CHARACTERS[0]
    assert 'codepoint' in entry
    assert 'risk' in entry
    assert 'cve_reference' in entry


def test_zwsp_invisible():
    assert 0x200B in _INVISIBLE_SET


def test_attack_vectors():
    assert 'domain_spoofing' in ATTACK_VECTORS
    assert 'code_injection' in ATTACK_VECTORS
    assert 'filename_confusion' in ATTACK_VECTORS


def test_codepoint_in_group():
    result = _codepoint_in_group(0x0041, HOMOGLYPH_GROUPS)
    assert result == 'A'


def test_bidi_trojan_source():
    cve_refs = {c['cve_reference'] for c in INVISIBLE_CHARACTERS}
    assert 'CVE-2021-42574' in cve_refs
