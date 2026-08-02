"""Unit tests for language detection module."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class TestDetectLanguage:
    def test_english_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("The quick brown fox jumps over the lazy dog. This is a simple English sentence.")
        assert result["language"] == "en"
        assert result["language_name"] == "English"
        assert result["confidence"] > 0.0
        assert result["script"] == "Latin"

    def test_french_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "Le gouvernement français a annoncé une nouvelle politique pour la protection de l'environnement."
        )
        assert result["language"] in ("en", "fr")
        assert result["script"] == "Latin"

    def test_german_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "Die Bundesregierung hat beschlossen, dass alle Bürger "
            "und Unternehmen von der neuen Regelung profitieren werden."
        )
        assert result["script"] == "Latin"

    def test_spanish_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "El presidente ha declarado que la economía está mejorando y que todos los ciudadanos se beneficiarán."
        )
        assert result["script"] == "Latin"

    def test_russian_cyrillic_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "Президент Российской Федерации подписал новый указ о развитии экономики и социальной сферы."
        )
        assert result["language"] == "ru"
        assert result["language_name"] == "Russian"
        assert result["script"] == "Cyrillic"

    def test_ukrainian_cyrillic_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("Президент України підписав новий закон про освіту та розвиток науки в країні.")
        assert result["script"] == "Cyrillic"

    def test_japanese_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "日本の政府は新しい政策を発表しました。この政策は経済の発展に大きな影響を与えるでしょう。"
        )
        assert result["language"] == "ja"
        assert result["language_name"] == "Japanese"

    def test_korean_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "한국 정부는 새로운 경제 정책을 발표했습니다. 이 정책은 모든 시민들에게 혜택을 줄 것입니다."
        )
        assert result["language"] == "ko"
        assert result["language_name"] == "Korean"

    def test_arabic_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language(
            "أعلنت الحكومة السعودية عن خطة جديدة للتنمية الاقتصادية والاجتماعية في جميع مناطق المملكة."
        )
        assert result["language"] == "ar"
        assert result["language_name"] == "Arabic"

    def test_chinese_detected(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("中国政府宣布了新的经济政策。这项政策将促进全国的经济发展。")
        assert result["language"] == "zh"
        assert result["language_name"] == "Chinese"

    def test_empty_text_returns_unknown(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("")
        assert result["language"] == "unknown"
        assert result["confidence"] == 0.0

    def test_whitespace_text_returns_unknown(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("   \n\t   ")
        assert result["language"] == "unknown"
        assert result["confidence"] == 0.0

    def test_result_has_required_keys(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("Hello world. This is English text.")
        for key in ("language", "language_name", "confidence", "script", "iso_639_1", "alternative", "method"):
            assert key in result, f"Missing key: {key}"

    def test_alternatives_provided(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("The weather is beautiful today and I am very happy about it.")
        assert isinstance(result.get("alternative"), list)


class TestLanguageNames:
    def test_all_languages_have_names(self) -> None:
        from general_ludd.language.detection import LANGUAGE_NAMES

        assert "en" in LANGUAGE_NAMES
        assert LANGUAGE_NAMES["en"] == "English"
        assert "ru" in LANGUAGE_NAMES
        assert LANGUAGE_NAMES["ru"] == "Russian"
        assert "ja" in LANGUAGE_NAMES
        assert "ko" in LANGUAGE_NAMES
        assert "ar" in LANGUAGE_NAMES
        assert "zh" in LANGUAGE_NAMES


class TestScriptToLanguages:
    def test_script_to_langs_has_keys(self) -> None:
        from general_ludd.language.detection import _SCRIPT_TO_LANGS

        assert "Latin" in _SCRIPT_TO_LANGS
        assert "Cyrillic" in _SCRIPT_TO_LANGS
        assert "Han" in _SCRIPT_TO_LANGS
        assert "Arabic" in _SCRIPT_TO_LANGS
        assert "Devanagari" in _SCRIPT_TO_LANGS
        assert "Hangul" in _SCRIPT_TO_LANGS
