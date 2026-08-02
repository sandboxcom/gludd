"""Integration tests for general_ludd.language collection.

Tests cross-module workflows spanning all 5 knowledge modules and 8 roles:
- BOM detection + encoding verification (charset_map + unicode_data)
- Mojibake detection pipeline (charset_map)
- Homoglyph domain spoofing (homoglyph_data)
- Phonetic cross-method consistency (phonetic_data)
- Locale format cross-referencing (locale_data)
- Unicode normalization + block/plane identification (unicode_data + stdlib unicodedata)
- Role task file loadability (all 8 roles)
- Knowledge module import validation (all 5 modules)
"""

from __future__ import annotations

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_COLL = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "collections",
    "ansible_collections",
    "general_ludd",
    "language",
)


class TestBomEncodingWorkflow:
    """BOM detection, stripping, and encoding verification."""

    def test_detect_utf8_bom_then_verify_content(self) -> None:
        from general_ludd.language.charset_map import BOM_BY_SEQUENCE, BOM_SIGNATURES
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES

        data = BOM_SIGNATURES["UTF-8"] + "Caf\u00e9".encode("utf-8")
        encoding = None
        for sig in sorted(BOM_SIGNATURES.values(), key=len, reverse=True):
            if data.startswith(sig):
                encoding = BOM_BY_SEQUENCE[sig]
                break
        assert encoding == "UTF-8"
        stripped = data[len(BOM_SIGNATURES[encoding]) :]
        text = stripped.decode("utf-8")
        assert text == "Caf\u00e9"
        assert ord("\u00e9") == 0x00E9
        assert UTF8_HEADER_BYTES.get(0xC3) == 2

    def test_detect_utf16_le_bom_then_decode(self) -> None:
        from general_ludd.language.charset_map import BOM_BY_SEQUENCE, BOM_SIGNATURES

        data = BOM_SIGNATURES["UTF-16-LE"] + "Hello".encode("utf-16-le")
        encoding = None
        for sig in sorted(BOM_SIGNATURES.values(), key=len, reverse=True):
            if data.startswith(sig):
                encoding = BOM_BY_SEQUENCE[sig]
                break
        assert encoding == "UTF-16-LE"
        stripped = data[len(BOM_SIGNATURES[encoding]) :]
        assert stripped.decode("utf-16-le") == "Hello"

    def test_no_bom_ascii_passes_through(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES

        data = b"Plain ASCII"
        found = any(data.startswith(sig) for sig in BOM_SIGNATURES.values())
        assert not found
        assert data.decode("ascii") == "Plain ASCII"


class TestMojibakeDetection:
    """Encoding mismatch detection and chardet threshold pipeline."""

    def test_utf8_viewed_as_latin1_produces_mojibake(self) -> None:
        from general_ludd.language.charset_map import MOJIBAKE_SIGNATURES

        utf8_bytes = "caf\u00e9".encode("utf-8")
        misdecoded = utf8_bytes.decode("iso-8859-1")
        sigs = MOJIBAKE_SIGNATURES.get("UTF-8 viewed as ISO-8859-1", [])
        assert any(sig in misdecoded for sig in sigs), f"No mojibake pattern matched '{misdecoded}'"

    def test_latin1_as_utf8_causes_decode_error(self) -> None:
        from general_ludd.language.charset_map import MOJIBAKE_SIGNATURES

        assert len(MOJIBAKE_SIGNATURES) >= 4
        latin1_bytes = "caf\u00e9".encode("iso-8859-1")
        try:
            latin1_bytes.decode("utf-8")
            raise AssertionError("Latin-1 bytes should not decode as UTF-8")
        except UnicodeDecodeError:
            pass

    def test_chardet_threshold_pipeline_simulated(self) -> None:
        from general_ludd.language.charset_map import (
            ALL_ENCODINGS,
            CHARDET_CONFIDENCE_THRESHOLDS,
        )

        assert CHARDET_CONFIDENCE_THRESHOLDS["entry"] < CHARDET_CONFIDENCE_THRESHOLDS["usable"]
        assert CHARDET_CONFIDENCE_THRESHOLDS["usable"] < CHARDET_CONFIDENCE_THRESHOLDS["reliable"]
        assert CHARDET_CONFIDENCE_THRESHOLDS["reliable"] < CHARDET_CONFIDENCE_THRESHOLDS["trusted"]
        names = {e["name"] for e in ALL_ENCODINGS}
        assert "UTF-8" in names or "utf-8" in names or "utf_8" in names
        confidence = 0.72
        level = (
            "reliable"
            if confidence >= CHARDET_CONFIDENCE_THRESHOLDS["reliable"]
            else "usable"
            if confidence >= CHARDET_CONFIDENCE_THRESHOLDS["usable"]
            else "entry"
        )
        assert level == "usable"


class TestHomoglyphDomainSpoofing:
    """Confusable character detection for domain-spoofing scenarios."""

    def test_cyrillic_a_in_apple_detected(self) -> None:
        from general_ludd.language.homoglyph_data import (
            _INVISIBLE_SET,
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )

        spoofed = chr(0x0430) + "pple.com"
        confusable_found = any(_codepoint_in_group(ord(ch), HOMOGLYPH_GROUPS) and ord(ch) > 0x007F for ch in spoofed)
        assert confusable_found
        assert 0x200B in _INVISIBLE_SET

    def test_russian_o_all_confusable(self) -> None:
        from general_ludd.language.homoglyph_data import (
            _INVISIBLE_SET,
            HOMOGLYPH_GROUPS,
            _codepoint_in_group,
        )

        russian_goog = "".join(chr(cp) for cp in [0x043E, 0x043E, 0x043E, 0x043E, 0x043E])
        assert all(_codepoint_in_group(ord(c), HOMOGLYPH_GROUPS) for c in russian_goog)
        assert 0x00AD in _INVISIBLE_SET

    def test_invisible_characters_in_url(self) -> None:
        from general_ludd.language.homoglyph_data import _INVISIBLE_SET

        clean = "https://example.com"
        assert not any(ord(c) in _INVISIBLE_SET for c in clean)

        poisoned = f"https://evil{chr(0x200B)}.com"
        assert any(ord(c) in _INVISIBLE_SET for c in poisoned)


class TestPhoneticCrossMethod:
    """Multi-method phonetic transcription consistency."""

    def test_soundex_and_cmu_integration(self) -> None:
        from general_ludd.language.phonetic_data import (
            CMU_DICT_SUBSET,
            SOUNDEX_MAPPING,
        )

        assert "HELLO" in CMU_DICT_SUBSET
        assert "HH" in CMU_DICT_SUBSET["HELLO"][0]

        def _soundex(word: str) -> str:
            enc = word[0].upper()
            prev = ""
            for ch in word.lower()[1:]:
                code = SOUNDEX_MAPPING.get(ch, "")
                if code and code != prev:
                    enc += code
                    prev = code
            return (enc + "000")[:4]

        assert _soundex("Robert") == _soundex("Rupert")

    def test_arpabet_to_ipa_roundtrip_for_vowels(self) -> None:
        from general_ludd.language.phonetic_data import (
            ARPABET_TO_IPA,
            IPA_TO_ARPABET,
            IPA_VOWELS,
        )

        for entry in IPA_VOWELS:
            arp = entry.get("arpabet")
            ipa = entry.get("ipa")
            if arp and ipa:
                mapped_ipa = ARPABET_TO_IPA.get(arp)
                if mapped_ipa:
                    if mapped_ipa == ipa or mapped_ipa in ipa:
                        continue
                    reversed_arp = IPA_TO_ARPABET.get(ipa)
                    if reversed_arp:
                        assert reversed_arp == arp, f"ARPABET {arp} -> IPA mismatch: {mapped_ipa} vs {ipa}"

    def test_cmu_dict_words_all_have_stress(self) -> None:
        from general_ludd.language.phonetic_data import CMU_DICT_SUBSET

        for word, pronunciations in CMU_DICT_SUBSET.items():
            for pron in pronunciations:
                phonemes = pron.split()
                assert any(ph[-1] in "012" for ph in phonemes if len(ph) == 3), (
                    f"CMU word {word} has no stress marker: {pron}"
                )


class TestLocaleFormatIntegration:
    """Locale data cross-referencing consistency."""

    def test_locale_format_complete_cross_reference(self) -> None:
        from general_ludd.language.locale_data import (
            ISO_639_1_TO_NAME,
            ISO_3166_TO_NAME,
            ISO_15924_TO_NAME,
            LOCALE_FORMATS,
            RTL_LANGUAGES,
            RTL_SCRIPTS,
        )

        for key, locale in LOCALE_FORMATS.items():
            lang_code, territory = key.split("-", 1)
            assert lang_code in ISO_639_1_TO_NAME, f"Unknown lang: {lang_code}"
            assert territory in ISO_3166_TO_NAME, f"Unknown territory: {territory}"
            assert locale["script"] in ISO_15924_TO_NAME, f"Unknown script: {locale['script']}"
            if locale["is_rtl"]:
                assert lang_code in RTL_LANGUAGES, f"RTL lang missing: {lang_code}"
                assert locale["script"] in RTL_SCRIPTS, f"RTL script missing: {locale['script']}"
            assert locale["bcp47"] == key, f"bcp47 mismatch: {locale['bcp47']} != {key}"

    def test_currency_integration_with_locale(self) -> None:
        from general_ludd.language.locale_data import COMMON_CURRENCIES, LOCALE_FORMATS

        assert COMMON_CURRENCIES["USD"]["symbol"] == "$"
        assert COMMON_CURRENCIES["EUR"]["symbol"] == "\u20ac"
        assert COMMON_CURRENCIES["JPY"]["decimal_digits"] == 0
        assert not LOCALE_FORMATS["en-US"]["is_rtl"]

    def test_cldr_first_day_consistency(self) -> None:
        from general_ludd.language.locale_data import CLDR_FIRST_DAY_OF_WEEK, LOCALE_FORMATS

        for locale in LOCALE_FORMATS.values():
            territory = locale["territory"]
            if territory in CLDR_FIRST_DAY_OF_WEEK:
                assert 0 <= CLDR_FIRST_DAY_OF_WEEK[territory] <= 6

    def test_number_format_separators_differ(self) -> None:
        from general_ludd.language.locale_data import LOCALE_FORMATS

        for key, locale in LOCALE_FORMATS.items():
            nf = locale["number_format"]
            assert nf["decimal_separator"] != nf["grouping_separator"], (
                f"{key}: decimal and grouping separators must differ"
            )
            assert nf["infinity"] is not None
            assert nf["nan"] is not None


class TestUnicodeNormalizationIntegration:
    """Unicode normalization, plane identification, and version data."""

    def test_cafe_normalization_forms(self) -> None:
        import unicodedata

        from general_ludd.language.unicode_data import UNICODE_BLOCK_NAMES, plane_of

        composed = "caf\u00e9"
        decomposed = unicodedata.normalize("NFD", composed)
        assert len(decomposed) > len(composed)
        recomposed = unicodedata.normalize("NFC", decomposed)
        assert recomposed == composed
        assert plane_of(ord("\u00e9")) == "BMP"
        assert (0x0080, 0x00FF) in UNICODE_BLOCK_NAMES
        assert UNICODE_BLOCK_NAMES[(0x0080, 0x00FF)] == "Latin-1 Supplement"

    def test_emoji_plane_and_multibyte_encoding(self) -> None:
        from general_ludd.language.charset_map import BOM_SIGNATURES
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES, plane_of

        emoji = "\U0001f600"
        assert plane_of(ord(emoji)) == "SMP"
        utf8_bytes = emoji.encode("utf-8")
        assert len(utf8_bytes) == 4
        assert UTF8_HEADER_BYTES.get(utf8_bytes[0]) == 4
        assert emoji.encode("utf-16-be")[:2] == b"\xd8\x3d"
        assert len(BOM_SIGNATURES) >= 5

    def test_utf8_overlong_sequences_detected(self) -> None:
        from general_ludd.language.unicode_data import UTF8_HEADER_BYTES

        overlong = bytes([0xC0, 0xAF])
        assert UTF8_HEADER_BYTES.get(overlong[0]) == 2
        try:
            overlong.decode("utf-8")
            raise AssertionError("Overlong sequence should not decode")
        except UnicodeDecodeError:
            pass

    def test_unicode_version_history_span(self) -> None:
        from general_ludd.language.unicode_data import UNICODE_VERSION_HISTORY

        years = [int(e["year"]) for e in UNICODE_VERSION_HISTORY]
        assert years == sorted(years)
        versions = [e["version"] for e in UNICODE_VERSION_HISTORY]
        assert len(versions) == len(set(versions))
        assert int(UNICODE_VERSION_HISTORY[-1]["characters"]) > 140000


class TestLanguageRoleFileIntegration:
    """Role task file loadability and knowledge module import validation."""

    def test_all_roles_have_task_files_with_content(self) -> None:
        roles = [
            "bom_detect",
            "encoding_detect",
            "font_analyze",
            "homoglyph_scan",
            "i18n_extract",
            "locale_format",
            "phonetic_transcribe",
            "unicode_analyze",
        ]
        for role in roles:
            tasks_file = os.path.join(_COLL, "roles", role, "tasks", "main.yml")
            assert os.path.isfile(tasks_file), f"Missing {tasks_file}"
            with open(tasks_file) as f:
                content = f.read()
            assert len(content) > 20, f"{role} tasks/main.yml too short"
            assert "name:" in content, f"{role} tasks/main.yml missing 'name:'"

    def test_all_knowledge_modules_importable(self) -> None:
        import general_ludd.language.charset_map as cm
        import general_ludd.language.homoglyph_data as hd
        import general_ludd.language.locale_data as ld
        import general_ludd.language.phonetic_data as pd
        import general_ludd.language.unicode_data as ud

        assert hasattr(ud, "UNICODE_VERSION_HISTORY")
        assert hasattr(ud, "plane_of")
        assert hasattr(cm, "BOM_SIGNATURES")
        assert hasattr(cm, "ALL_ENCODINGS")
        assert hasattr(ld, "LOCALE_FORMATS")
        assert hasattr(ld, "RTL_LANGUAGES")
        assert hasattr(pd, "CMU_DICT_SUBSET")
        assert hasattr(pd, "SOUNDEX_MAPPING")
        assert hasattr(hd, "HOMOGLYPH_GROUPS")
        assert hasattr(hd, "_INVISIBLE_SET")

    def test_new_knowledge_modules_importable(self) -> None:
        import general_ludd.language.detection as det
        import general_ludd.language.translation as trans
        import general_ludd.language.transliteration as translit

        assert hasattr(det, "detect_language")
        assert hasattr(det, "LANGUAGE_NAMES")
        assert hasattr(trans, "translate")
        assert hasattr(trans, "_DICTIONARY")
        assert hasattr(translit, "transliterate")
        assert hasattr(translit, "list_schemes")
