"""TDD tests for the daemon-owned language operation service."""

from __future__ import annotations

import base64

import pytest


def _execute(operation: str, payload: dict[str, object]) -> dict[str, object]:
    from general_ludd.language.operations import execute_language_operation

    return execute_language_operation(operation, payload)


def test_language_detect_translate_and_transliterate_preserve_public_schemas() -> None:
    detected = _execute("language_detect", {"input_text": "the quick brown fox and the dog"})
    assert {"language", "iso_639_1", "confidence", "script"} <= detected.keys()

    translated = _execute(
        "translate",
        {"input_text": "hello", "source_language": "en", "target_language": "de"},
    )
    assert translated["translated_text"] == "hallo"

    transliterated = _execute(
        "transliterate",
        {"input_text": "Москва", "target_script": "Latin", "scheme": ""},
    )
    assert transliterated["transliterated_text"] == "Moskva"


def test_bom_and_encoding_accept_managed_host_slurp_payloads() -> None:
    encoded = base64.b64encode(b"\xef\xbb\xbfHello").decode("ascii")
    bom = _execute("bom_detect", {"input_b64": encoded, "strip_bom": True})
    assert bom["bom_detected"] is True
    assert bom["encoding"] == "UTF-8"
    assert "stripped_preview" in bom

    encoding = _execute("encoding_detect", {"input_b64": encoded})
    assert encoding["byte_length"] == 8
    assert "detected_encoding" in encoding
    assert "supported_encodings" in encoding


def test_homoglyph_unicode_locale_and_phonetic_results_remain_structured() -> None:
    homoglyph = _execute(
        "homoglyph_scan",
        {"input_text": "paypa" + chr(0x0430) + "l.com"},
    )
    assert homoglyph["safe"] is False
    assert int(homoglyph["total_findings"]) > 0

    unicode_result = _execute("unicode_analyze", {"input_text": "A😀"})
    assert unicode_result["input_length"] == 2
    assert unicode_result["plane_distribution"] == {"BMP": 1, "SMP": 1}
    assert len(unicode_result["codepoints"]) == 2

    locale = _execute(
        "locale_format",
        {"locale": "en-US", "value": "1234.5", "value_type": "number"},
    )
    assert locale["locale"] == "en-US"
    assert locale["formatted_value"]

    phonetic = _execute(
        "phonetic_transcribe",
        {"input_text": "hello world", "method": "ipa"},
    )
    assert phonetic["ipa"]


def test_unicode_analysis_decodes_managed_host_slurp_payload() -> None:
    encoded = base64.b64encode("A😀".encode()).decode("ascii")

    result = _execute("unicode_analyze", {"input_b64": encoded})

    assert result["input_length"] == 2
    assert result["plane_distribution"] == {"BMP": 1, "SMP": 1}


def test_unicode_analysis_rejects_non_utf8_managed_host_input() -> None:
    encoded = base64.b64encode(b"\xff\xfe").decode("ascii")

    with pytest.raises(ValueError, match="UTF-8"):
        _execute("unicode_analyze", {"input_b64": encoded})


def test_operation_service_rejects_unknown_or_unbounded_inputs() -> None:
    with pytest.raises(ValueError, match="unsupported language operation"):
        _execute("shell", {})
    with pytest.raises(ValueError, match="payload"):
        _execute("language_detect", {"input_text": "x" * 1_000_001})
