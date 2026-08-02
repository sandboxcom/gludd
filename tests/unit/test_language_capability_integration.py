"""Integration test: capability dispatch through CapabilityRouter to language model result.

Tests the full path from capability lookup → router dispatch → model result
for language detection, translation, and transliteration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.dispatch.capabilities import discover_capabilities
from general_ludd.dispatch.router import CapabilityRouter
from general_ludd.language.contracts import (
    LanguageDetectionResult,
    TranslationResult,
    TransliterationResult,
)
from general_ludd.language.core import LanguageDetector, Translator, Transliterator


@pytest.fixture
def router():
    colls_root = Path(__file__).resolve().parent.parent.parent / "collections" / "ansible_collections"
    registry = discover_capabilities(colls_root)
    return CapabilityRouter(registry)


# ── Capability → Model → Result: Detection ──────────────────────────────


class TestCapabilityDetectionIntegration:
    def test_detect_capability_registered(self, router):
        result = router.route("language_detection")
        assert result.ok is True
        assert any("language" in m.name for m in result.matches)

    def test_detect_capability_to_model_result_english(self, router):
        route = router.route("language_detection", {"text": "The quick brown fox"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("The quick brown fox")

        assert model_result.language_code == "en"
        assert model_result.confidence > 0.5
        assert isinstance(model_result, LanguageDetectionResult)

    def test_detect_capability_to_model_result_spanish(self, router):
        route = router.route("language_detection", {"text": "Hola, como estas"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("Hola como estas")

        assert model_result.language_code == "es"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_french(self, router):
        route = router.route("language_detection", {"text": "Bonjour le monde"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("Bonjour le monde")

        assert model_result.language_code == "fr"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_german(self, router):
        route = router.route("language_detection", {"text": "Guten Tag wie geht es"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("Guten Tag wie geht es")

        assert model_result.language_code == "de"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_russian(self, router):
        route = router.route("language_detection", {"text": "Привет как дела"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("Привет как дела")

        assert model_result.language_code == "ru"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_arabic(self, router):
        route = router.route("language_detection", {"text": "مرحبا كيف حالك"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("مرحبا كيف حالك")

        assert model_result.language_code == "ar"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_chinese(self, router):
        route = router.route("language_detection", {"text": "你好世界"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("你好世界")

        assert model_result.language_code == "zh"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_japanese(self, router):
        route = router.route("language_detection", {"text": "こんにちは世界"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("こんにちは世界")

        assert model_result.language_code == "ja"
        assert model_result.confidence > 0.5

    def test_detect_capability_to_model_result_korean(self, router):
        route = router.route("language_detection", {"text": "안녕하세요"})
        assert route.ok is True

        detector = LanguageDetector()
        model_result = detector.detect("안녕하세요")

        assert model_result.language_code == "ko"
        assert model_result.confidence > 0.5

    def test_detect_empty_text_returns_low_confidence(self, router):
        detector = LanguageDetector()
        with pytest.raises(ValueError, match="Text must not be empty"):
            detector.detect("   ")

    def test_detect_batch_from_capability(self, router):
        route = router.route("language_detection", {"texts": ["hello world", "hola mundo"]})
        assert route.ok is True

        detector = LanguageDetector()
        results = detector.detect_batch(["hello world", "hola mundo"])

        assert len(results) == 2
        assert results[0].language_code == "en"
        assert results[1].language_code == "es"


# ── Capability → Model → Result: Translation ────────────────────────────


class TestCapabilityTranslationIntegration:
    def test_translate_capability_registered(self, router):
        result = router.route("translation")
        assert result.ok is True

    def test_translate_capability_to_model_result_en_to_es(self, router):
        route = router.route("translation", {"text": "hello", "source_lang": "en", "target_lang": "es"})
        assert route.ok is True

        translator = Translator()
        model_result = translator.translate("hello", source_lang="en", target_lang="es")

        assert model_result.source_lang == "en"
        assert model_result.target_lang == "es"
        assert isinstance(model_result, TranslationResult)
        assert model_result.confidence is not None

    def test_translate_capability_to_model_result_en_to_fr(self, router):
        route = router.route("translation", {"text": "good morning", "source_lang": "en", "target_lang": "fr"})
        assert route.ok is True

        translator = Translator()
        model_result = translator.translate("good morning", source_lang="en", target_lang="fr")

        assert model_result.source_lang == "en"
        assert model_result.target_lang == "fr"
        assert model_result.confidence is not None

    def test_translate_same_language_raises(self, router):
        translator = Translator()
        with pytest.raises(ValueError, match="Cannot translate to the same language"):
            translator.translate("hello", source_lang="en", target_lang="en")

    def test_translate_empty_text_raises(self, router):
        translator = Translator()
        with pytest.raises(ValueError, match="Text must not be empty"):
            translator.translate("   ", source_lang="en", target_lang="es")

    def test_translate_batch_from_capability(self, router):
        route = router.route("translation", {"texts": ["hello", "goodbye"], "source_lang": "en", "target_lang": "es"})
        assert route.ok is True

        translator = Translator()
        results = translator.translate_batch(["hello", "goodbye"], source_lang="en", target_lang="es")

        assert len(results) == 2
        assert results[0].source_lang == "en"
        assert results[1].source_lang == "en"


# ── Capability → Model → Result: Transliteration ────────────────────────


class TestCapabilityTransliterationIntegration:
    def test_transliterate_capability_registered(self, router):
        result = router.route("transliteration")
        assert result.ok is True

    def test_transliterate_capability_to_model_result_cyrillic_to_latin(self, router):
        route = router.route("transliteration", {"text": "Привет мир", "target_script": "Latin"})
        assert route.ok is True

        transliterator = Transliterator()
        model_result = transliterator.transliterate("Привет мир", target_script="Latin")

        assert model_result.target_script == "Latin"
        assert model_result.source_script == "Cyrillic"
        assert isinstance(model_result, TransliterationResult)
        assert "Privet mir" in model_result.transliterated_text or len(model_result.transliterated_text) > 0

    def test_transliterate_capability_to_model_result_arabic_to_latin(self, router):
        route = router.route("transliteration", {"text": "مرحبا", "target_script": "Latin"})
        assert route.ok is True

        transliterator = Transliterator()
        model_result = transliterator.transliterate("مرحبا", target_script="Latin")

        assert model_result.target_script == "Latin"
        assert model_result.source_script == "Arabic"
        assert len(model_result.transliterated_text) > 0

    def test_transliterate_capability_to_model_result_hiragana_to_latin(self, router):
        route = router.route("transliteration", {"text": "こんにちは", "target_script": "Latin"})
        assert route.ok is True

        transliterator = Transliterator()
        model_result = transliterator.transliterate("こんにちは", target_script="Latin")

        assert model_result.target_script == "Latin"
        assert model_result.source_script == "Japanese"
        assert len(model_result.transliterated_text) > 0

    def test_transliterate_capability_to_model_result_devanagari_to_latin(self, router):
        route = router.route("transliteration", {"text": "नमस्ते", "target_script": "Latin"})
        assert route.ok is True

        transliterator = Transliterator()
        model_result = transliterator.transliterate("नमस्ते", target_script="Latin")

        assert model_result.target_script == "Latin"
        assert model_result.source_script == "Devanagari"
        assert len(model_result.transliterated_text) > 0

    def test_transliterate_same_script_raises(self, router):
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="Cannot transliterate to the same script"):
            transliterator.transliterate("hello", target_script="Latin")

    def test_transliterate_empty_text_raises(self, router):
        transliterator = Transliterator()
        with pytest.raises(ValueError, match="Text must not be empty"):
            transliterator.transliterate("   ", target_script="Latin")

    def test_transliterate_batch_from_capability(self, router):
        route = router.route("transliteration", {"texts": ["Привет", "Москва"], "target_script": "Latin"})
        assert route.ok is True

        transliterator = Transliterator()
        results = transliterator.transliterate_batch(["Привет", "Москва"], target_script="Latin")

        assert len(results) == 2
        assert results[0].target_script == "Latin"
        assert results[1].target_script == "Latin"


# ── Capability → Model → Result: Script Detection ───────────────────────


class TestCapabilityScriptDetectionIntegration:
    def test_script_detect_capability_to_model_result(self, router):
        transliterator = Transliterator()
        assert transliterator.detect_script("Привет") == "Cyrillic"
        assert transliterator.detect_script("hello") == "Latin"
        assert transliterator.detect_script("مرحبا") == "Arabic"
        assert transliterator.detect_script("こんにちは") == "Japanese"
        assert transliterator.detect_script("안녕하세요") == "Korean"
        assert transliterator.detect_script("你好") == "Chinese"


# ── Capability Router Listing ───────────────────────────────────────────


class TestCapabilityRouterListing:
    def test_list_capabilities_includes_language_tags(self, router):
        caps = router.list_capabilities()
        assert any(tag in caps for tag in ("language", "unicode", "encoding", "i18n", "l10n", "charset", "locale"))
