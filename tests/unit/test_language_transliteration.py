"""Unit tests for language transliteration module."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class TestTransliterate:
    def test_cyrillic_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет мир")
        assert result["source_script"] == "Cyrillic"
        assert result["target_script"] == "Latin"
        assert len(result["transliterated_text"]) > 0
        assert result["scheme"] == "cyrillic-to-latin"

    def test_russian_hello(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет")
        assert result["transliterated_text"] == "Privet"

    def test_russian_moscow(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Москва")
        assert result["transliterated_text"] == "Moskva"

    def test_cyrillic_single_letter(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Я")
        assert result["transliterated_text"] == "Â"

    def test_greek_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Καλημέρα")
        assert result["source_script"] == "Greek"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "greek-to-latin"
        assert len(result["transliterated_text"]) > 0

    def test_arabic_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("سلام")
        assert result["source_script"] == "Arabic"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "arabic-to-latin"
        assert len(result["transliterated_text"]) > 0

    def test_devanagari_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("नमस्ते")
        assert result["source_script"] == "Devanagari"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "devanagari-to-latin"

    def test_hiragana_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("こんにちは")
        assert result["source_script"] == "Hiragana"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "hiragana-to-latin"
        assert len(result["transliterated_text"]) > 0

    def test_katakana_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("コンピュータ")
        assert result["source_script"] == "Katakana"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "katakana-to-latin"

    def test_korean_to_latin(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("안녕하세요")
        assert result["source_script"] == "Hangul"
        assert result["target_script"] == "Latin"
        assert result["scheme"] == "korean-to-latin"

    def test_latin_to_cyrillic(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Moskva", target_script="Cyrillic")
        assert result["source_script"] == "Latin"
        assert result["target_script"] == "Cyrillic"
        assert result["scheme"] == "latin-to-cyrillic"

    def test_latin_to_greek(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Kalimera", target_script="Greek")
        assert result["source_script"] == "Latin"
        assert result["target_script"] == "Greek"
        assert result["scheme"] == "latin-to-greek"

    def test_latin_identity(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Hello World", target_script="Latin")
        assert result["source_script"] == "Latin"
        assert result["transliterated_text"] == "Hello World"
        assert result["scheme"] == "identity"

    def test_empty_text(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("")
        assert result["transliterated_text"] == ""
        assert result["scheme"] == "none"

    def test_result_has_required_keys(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет")
        for key in ("source_text", "source_script", "target_script", "transliterated_text", "scheme", "reversible"):
            assert key in result, f"Missing key: {key}"

    def test_explicit_scheme(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет", scheme="cyrillic-to-latin")
        assert result["scheme"] == "cyrillic-to-latin"
        assert result["transliterated_text"] == "Privet"

    def test_cyrillic_reversible(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("Привет")
        assert result["reversible"] is True

    def test_arabic_not_reversible(self) -> None:
        from general_ludd.language.transliteration import transliterate

        result = transliterate("مرحبا")
        is_rev = bool(result["reversible"])
        assert is_rev is False


class TestListSchemes:
    def test_list_schemes_returns_all(self) -> None:
        from general_ludd.language.transliteration import list_schemes

        schemes = list_schemes()
        assert len(schemes) >= 9
        names = {s["scheme"] for s in schemes}
        assert "cyrillic-to-latin" in names
        assert "greek-to-latin" in names
        assert "arabic-to-latin" in names
        assert "devanagari-to-latin" in names
        assert "hiragana-to-latin" in names
        assert "katakana-to-latin" in names
        assert "korean-to-latin" in names

    def test_scheme_structure(self) -> None:
        from general_ludd.language.transliteration import list_schemes

        schemes = list_schemes()
        for scheme in schemes:
            assert "scheme" in scheme
            assert "source_script" in scheme
            assert "target_script" in scheme
            assert "reversible" in scheme


class TestTransliterationTables:
    def test_cyrillic_table_size(self) -> None:
        from general_ludd.language.transliteration import _CYRILLIC_TO_LATIN

        assert len(_CYRILLIC_TO_LATIN) >= 60

    def test_greek_table_size(self) -> None:
        from general_ludd.language.transliteration import _GREEK_TO_LATIN

        assert len(_GREEK_TO_LATIN) >= 40

    def test_hiragana_table_size(self) -> None:
        from general_ludd.language.transliteration import _HIRAGANA_TO_LATIN

        assert len(_HIRAGANA_TO_LATIN) >= 70

    def test_katakana_table_size(self) -> None:
        from general_ludd.language.transliteration import _KATAKANA_TO_LATIN

        assert len(_KATAKANA_TO_LATIN) >= 70
