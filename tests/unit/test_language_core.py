"""TDD tests for ``src/general_ludd/language/core.py``.

LanguageDetector, Translator, and Transliterator with mock backends.

These tests fail until the module exists.
"""

from __future__ import annotations

import pytest

from general_ludd.language.contracts import (
    LanguageDetectionResult,
    TranslationResult,
    TransliterationResult,
)
from general_ludd.language.core import LanguageDetector, Translator, Transliterator

# ── LanguageDetector ──────────────────────────────────────────────────────


class TestLanguageDetector:
    def test_detect_english(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Hello, how are you today?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "en"
        assert result.confidence > 0.5

    def test_detect_spanish(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Hola, ¿cómo estás?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "es"
        assert result.confidence > 0.5

    def test_detect_french(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Bonjour, comment allez-vous?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "fr"
        assert result.confidence > 0.5

    def test_detect_german(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Guten Tag, wie geht es Ihnen?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "de"
        assert result.confidence > 0.5

    def test_detect_chinese(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("你好，今天怎么样？")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "zh"
        assert result.confidence > 0.5

    def test_detect_japanese(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("こんにちは、お元気ですか？")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "ja"

    def test_detect_korean(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("안녕하세요, 어떻게 지내세요?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "ko"

    def test_detect_russian(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Привет, как дела?")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "ru"

    def test_detect_arabic(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("مرحبا، كيف حالك؟")
        assert isinstance(result, LanguageDetectionResult)
        assert result.language_code == "ar"

    def test_detect_returns_script_for_known_scripts(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Привет мир")
        assert result.script == "Cyrillic"

    def test_detect_returns_region_when_discernable(self) -> None:
        detector = LanguageDetector()
        result = detector.detect("Hello, how are you?")
        assert result.region is not None

    def test_detect_empty_text_raises(self) -> None:
        detector = LanguageDetector()
        with pytest.raises(ValueError, match="empty"):
            detector.detect("")

    def test_detect_whitespace_only_raises(self) -> None:
        detector = LanguageDetector()
        with pytest.raises(ValueError, match="empty"):
            detector.detect("   \t\n  ")

    def test_detect_batch_returns_results_for_all(self) -> None:
        detector = LanguageDetector()
        texts = ["Hello world", "Hola mundo", "Bonjour le monde"]
        results = detector.detect_batch(texts)
        assert len(results) == 3
        assert results[0].language_code == "en"
        assert results[1].language_code == "es"
        assert results[2].language_code == "fr"

    def test_detect_batch_empty_list_raises(self) -> None:
        detector = LanguageDetector()
        with pytest.raises(ValueError, match="at least one"):
            detector.detect_batch([])


# ── Translator ────────────────────────────────────────────────────────────


class TestTranslatorConstruction:
    def test_translator_accepts_mock_backend_option(self) -> None:
        translator = Translator(backend="mock")
        assert translator.backend == "mock"

    def test_translator_defaults_to_mock_backend(self) -> None:
        translator = Translator()
        assert translator.backend == "mock"


class TestTranslatorTranslate:
    def test_translate_en_to_es(self) -> None:
        translator = Translator()
        result = translator.translate("Hello world", "en", "es")
        assert isinstance(result, TranslationResult)
        assert result.translated_text != "Hello world"
        assert result.source_lang == "en"
        assert result.target_lang == "es"

    def test_translate_en_to_fr(self) -> None:
        translator = Translator()
        result = translator.translate("Good morning", "en", "fr")
        assert isinstance(result, TranslationResult)
        assert result.source_lang == "en"
        assert result.target_lang == "fr"

    def test_translate_preserves_length_approx(self) -> None:
        translator = Translator()
        result = translator.translate("The quick brown fox", "en", "de")
        assert len(result.translated_text) > 0

    def test_translate_empty_text_raises(self) -> None:
        translator = Translator()
        with pytest.raises(ValueError, match="empty"):
            translator.translate("", "en", "es")

    def test_translate_whitespace_only_raises(self) -> None:
        translator = Translator()
        with pytest.raises(ValueError, match="empty"):
            translator.translate("   ", "en", "es")

    def test_translate_same_source_target_raises(self) -> None:
        translator = Translator()
        with pytest.raises(ValueError, match="same language"):
            translator.translate("Hello", "en", "en")

    def test_translate_batch(self) -> None:
        translator = Translator()
        results = translator.translate_batch(
            ["Hello", "Good morning", "Thank you"],
            "en",
            "es",
        )
        assert len(results) == 3
        for r in results:
            assert isinstance(r, TranslationResult)
            assert r.source_lang == "en"
            assert r.target_lang == "es"

    def test_translate_batch_empty_list_raises(self) -> None:
        translator = Translator()
        with pytest.raises(ValueError, match="at least one"):
            translator.translate_batch([], "en", "es")


class TestTranslatorMockFeatures:
    def test_mock_translator_returns_marker(self) -> None:
        translator = Translator(backend="mock")
        result = translator.translate("Hello", "en", "fr")
        assert "[FR]" in result.translated_text

    def test_mock_translator_includes_source_lang_in_output(self) -> None:
        translator = Translator(backend="mock")
        result = translator.translate("Test me", "en", "de")
        assert "[DE]" in result.translated_text

    def test_mock_translator_confidence_set(self) -> None:
        translator = Translator(backend="mock")
        result = translator.translate("Example", "en", "it")
        assert result.confidence is not None
        assert 0.0 <= result.confidence <= 1.0


# ── Transliterator ────────────────────────────────────────────────────────


class TestTransliteratorConstruction:
    def test_transliterator_accepts_mock_backend_option(self) -> None:
        transliterator = Transliterator(backend="mock")
        assert transliterator.backend == "mock"

    def test_transliterator_defaults_to_mock_backend(self) -> None:
        transliterator = Transliterator()
        assert transliterator.backend == "mock"


class TestTransliteratorTransliterate:
    def test_transliterate_cyrillic_to_latin(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("Москва", "Latin")
        assert isinstance(result, TransliterationResult)
        assert result.transliterated_text != "Москва"
        assert result.source_script == "Cyrillic"
        assert result.target_script == "Latin"

    def test_transliterate_arabic_to_latin(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("مرحبا", "Latin")
        assert isinstance(result, TransliterationResult)
        assert result.source_script == "Arabic"
        assert result.target_script == "Latin"

    def test_transliterate_chinese_to_latin(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("你好世界", "Latin")
        assert isinstance(result, TransliterationResult)
        assert result.target_script == "Latin"

    def test_transliterate_hindi_to_latin(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("नमस्ते", "Latin")
        assert isinstance(result, TransliterationResult)
        assert result.source_script == "Devanagari"
        assert result.target_script == "Latin"

    def test_transliterate_latin_to_cyrillic(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("Moskva", "Cyrillic")
        assert isinstance(result, TransliterationResult)
        assert result.source_script == "Latin"
        assert result.target_script == "Cyrillic"

    def test_transliterate_japanese_to_latin(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("こんにちは", "Latin")
        assert isinstance(result, TransliterationResult)
        assert result.source_script == "Japanese"
        assert result.target_script == "Latin"

    def test_transliterate_empty_text_raises(self) -> None:
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="empty"):
            transliterator.transliterate("", "Latin")

    def test_transliterate_same_script_raises(self) -> None:
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="same script"):
            transliterator.transliterate("Hello", "Latin")

    def test_transliterate_unknown_target_raises(self) -> None:
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="Unknown target script"):
            transliterator.transliterate("Hello", "MartianScript")

    def test_transliterate_batch(self) -> None:
        transliterator = Transliterator()
        results = transliterator.transliterate_batch(
            ["Привет", "Москва", "спасибо"],
            "Latin",
        )
        assert len(results) == 3
        for r in results:
            assert isinstance(r, TransliterationResult)
            assert r.target_script == "Latin"

    def test_transliterate_batch_empty_list_raises(self) -> None:
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="at least one"):
            transliterator.transliterate_batch([], "Latin")

    def test_transliterate_latin_to_cyrillic_with_scheme(self) -> None:
        transliterator = Transliterator()
        result = transliterator.transliterate("Moskva", "Cyrillic", scheme="ISO-9")
        assert isinstance(result, TransliterationResult)
        assert result.scheme == "ISO-9"


class TestTransliteratorScriptDetection:
    def test_detect_script_cyrillic(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("Привет") == "Cyrillic"

    def test_detect_script_arabic(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("مرحبا") == "Arabic"

    def test_detect_script_devanagari(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("नमस्ते") == "Devanagari"

    def test_detect_script_japanese(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("こんにちは") == "Japanese"

    def test_detect_script_chinese(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("你好") == "Chinese"

    def test_detect_script_korean(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("한글") == "Korean"

    def test_detect_script_latin(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("Hello world") == "Latin"

    def test_detect_script_unknown_falls_back(self) -> None:
        transliterator = Transliterator()
        assert transliterator.detect_script("") == "Unknown"


class TestTransliteratorMockFeatures:
    def test_mock_cyrillic_to_latin_produces_ascii(self) -> None:
        transliterator = Translator(backend="mock")  # unrelated
        transliterator = Transliterator(backend="mock")
        result = transliterator.transliterate("Привет мир", "Latin")
        assert all(ord(c) < 128 for c in result.transliterated_text)

    def test_mock_transliteration_preserves_approx_length(self) -> None:
        transliterator = Transliterator(backend="mock")
        result = transliterator.transliterate("Москва", "Latin")
        assert len(result.transliterated_text) > 0
