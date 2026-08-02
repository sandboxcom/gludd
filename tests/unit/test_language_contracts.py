"""TDD contracts for language detection, translation, and transliteration.

These tests define the expected shape before the implementation exists.
All tests should FAIL on first run (red phase), then PASS after implementation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestLanguageDetectionContracts:
    """Contracts for language detection input/output shapes."""

    def test_detection_result_requires_language_code_and_confidence(self):
        """LanguageDetectionResult must have language_code (str) and confidence (float)."""
        from src.general_ludd.language.contracts import LanguageDetectionResult

        result = LanguageDetectionResult(language_code="en", confidence=0.95)
        assert result.language_code == "en"
        assert result.confidence == 0.95

    def test_detection_result_confidence_bounded_0_1(self):
        """Confidence must be in [0.0, 1.0]."""
        from src.general_ludd.language.contracts import LanguageDetectionResult

        LanguageDetectionResult(language_code="en", confidence=0.0)
        LanguageDetectionResult(language_code="en", confidence=1.0)
        with pytest.raises(ValidationError):
            LanguageDetectionResult(language_code="en", confidence=-0.1)
        with pytest.raises(ValidationError):
            LanguageDetectionResult(language_code="en", confidence=1.1)

    def test_detection_result_optional_fields(self):
        """Optional fields: script, region, encoding."""
        from src.general_ludd.language.contracts import LanguageDetectionResult

        result = LanguageDetectionResult(
            language_code="zh",
            confidence=0.87,
            script="Hans",
            region="CN",
            encoding="UTF-8",
        )
        assert result.script == "Hans"
        assert result.region == "CN"
        assert result.encoding == "UTF-8"

    def test_detection_result_defaults(self):
        """Optional fields default to None."""
        from src.general_ludd.language.contracts import LanguageDetectionResult

        result = LanguageDetectionResult(language_code="en", confidence=0.90)
        assert result.script is None
        assert result.region is None
        assert result.encoding is None
        assert result.detection_method is None

    def test_detection_result_language_code_min_length(self):
        """language_code must be at least 2 characters (ISO 639-1)."""
        from src.general_ludd.language.contracts import LanguageDetectionResult

        with pytest.raises(ValidationError):
            LanguageDetectionResult(language_code="e", confidence=0.5)

    def test_detection_batch_request_requires_text_list(self):
        """DetectionBatchRequest must have a non-empty list of text strings."""
        from src.general_ludd.language.contracts import DetectionBatchRequest

        req = DetectionBatchRequest(texts=["hello", "bonjour"])
        assert len(req.texts) == 2

    def test_detection_batch_request_rejects_empty_list(self):
        """DetectionBatchRequest must reject empty text list."""
        from src.general_ludd.language.contracts import DetectionBatchRequest

        with pytest.raises(ValidationError):
            DetectionBatchRequest(texts=[])

    def test_detection_batch_result_contains_per_text_results(self):
        """DetectionBatchResult wraps a list of LanguageDetectionResult."""
        from src.general_ludd.language.contracts import (
            DetectionBatchRequest,
            DetectionBatchResult,
            LanguageDetectionResult,
        )

        req = DetectionBatchRequest(texts=["hello", "hola"])
        results = [
            LanguageDetectionResult(language_code="en", confidence=0.95),
            LanguageDetectionResult(language_code="es", confidence=0.88),
        ]
        batch = DetectionBatchResult(request=req, results=results)
        assert len(batch.results) == 2
        assert batch.results[0].language_code == "en"


class TestTranslationContracts:
    """Contracts for translation request/response shapes."""

    def test_translation_request_requires_source_target_and_text(self):
        """TranslationRequest must have source_lang, target_lang, and text."""
        from src.general_ludd.language.contracts import TranslationRequest

        req = TranslationRequest(source_lang="en", target_lang="es", text="Hello world")
        assert req.source_lang == "en"
        assert req.target_lang == "es"
        assert req.text == "Hello world"

    def test_translation_request_rejects_empty_text(self):
        """TranslationRequest must reject empty text."""
        from src.general_ludd.language.contracts import TranslationRequest

        with pytest.raises(ValidationError):
            TranslationRequest(source_lang="en", target_lang="es", text="")

    def test_translation_request_rejects_short_lang_codes(self):
        """Language codes must be at least 2 characters."""
        from src.general_ludd.language.contracts import TranslationRequest

        with pytest.raises(ValidationError):
            TranslationRequest(source_lang="e", target_lang="es", text="hi")

    def test_translation_request_optional_formality(self):
        """Optional formality field with default 'neutral'."""
        from src.general_ludd.language.contracts import TranslationRequest

        req = TranslationRequest(source_lang="en", target_lang="de", text="Hello", formality="formal")
        assert req.formality == "formal"

    def test_translation_result_requires_translated_text(self):
        """TranslationResult must have translated_text."""
        from src.general_ludd.language.contracts import TranslationResult

        result = TranslationResult(
            translated_text="Hola mundo",
            source_lang="en",
            target_lang="es",
        )
        assert result.translated_text == "Hola mundo"

    def test_translation_result_optional_confidence(self):
        """Optional confidence field."""
        from src.general_ludd.language.contracts import TranslationResult

        result = TranslationResult(
            translated_text="Bonjour le monde",
            source_lang="en",
            target_lang="fr",
            confidence=0.92,
        )
        assert result.confidence == 0.92

    def test_translation_result_confidence_bounded(self):
        """TranslationResult confidence must be in [0.0, 1.0]."""
        from src.general_ludd.language.contracts import TranslationResult

        with pytest.raises(ValidationError):
            TranslationResult(translated_text="x", source_lang="en", target_lang="es", confidence=2.0)

    def test_translation_batch_request(self):
        """TranslationBatchRequest handles multiple texts."""
        from src.general_ludd.language.contracts import TranslationBatchRequest

        req = TranslationBatchRequest(
            source_lang="en",
            target_lang="fr",
            texts=["Hello", "Goodbye", "Thank you"],
        )
        assert len(req.texts) == 3

    def test_translation_batch_request_rejects_empty(self):
        """TranslationBatchRequest must reject empty texts list."""
        from src.general_ludd.language.contracts import TranslationBatchRequest

        with pytest.raises(ValidationError):
            TranslationBatchRequest(source_lang="en", target_lang="fr", texts=[])

    def test_translation_batch_result(self):
        """TranslationBatchResult wraps a list of TranslationResult."""
        from src.general_ludd.language.contracts import (
            TranslationBatchRequest,
            TranslationBatchResult,
            TranslationResult,
        )

        req = TranslationBatchRequest(source_lang="en", target_lang="es", texts=["Hello", "Goodbye"])
        results = [
            TranslationResult(translated_text="Hola", source_lang="en", target_lang="es"),
            TranslationResult(translated_text="Adios", source_lang="en", target_lang="es"),
        ]
        batch = TranslationBatchResult(request=req, results=results)
        assert len(batch.results) == 2


class TestTransliterationContracts:
    """Contracts for transliteration request/response shapes."""

    def test_transliteration_request_requires_text_and_target_script(self):
        """TransliterationRequest must have text and target_script."""
        from src.general_ludd.language.contracts import TransliterationRequest

        req = TransliterationRequest(text="Москва", target_script="Latn")
        assert req.text == "Москва"
        assert req.target_script == "Latn"

    def test_transliteration_request_rejects_empty_text(self):
        """TransliterationRequest must reject empty text."""
        from src.general_ludd.language.contracts import TransliterationRequest

        with pytest.raises(ValidationError):
            TransliterationRequest(text="", target_script="Latn")

    def test_transliteration_request_target_script_min_length(self):
        """target_script must be at least 3 characters (ISO 15924)."""
        from src.general_ludd.language.contracts import TransliterationRequest

        with pytest.raises(ValidationError):
            TransliterationRequest(text="hello", target_script="AB")

    def test_transliteration_request_optional_source_script(self):
        """Optional source_script field."""
        from src.general_ludd.language.contracts import TransliterationRequest

        req = TransliterationRequest(text="Москва", target_script="Latn", source_script="Cyrl")
        assert req.source_script == "Cyrl"

    def test_transliteration_request_optional_scheme(self):
        """Optional scheme field for naming the transliteration standard."""
        from src.general_ludd.language.contracts import TransliterationRequest

        req = TransliterationRequest(text="Москва", target_script="Latn", scheme="ISO 9")
        assert req.scheme == "ISO 9"

    def test_transliteration_result_requires_transliterated_text(self):
        """TransliterationResult must have transliterated_text."""
        from src.general_ludd.language.contracts import TransliterationResult

        result = TransliterationResult(
            transliterated_text="Moskva",
            source_script="Cyrl",
            target_script="Latn",
        )
        assert result.transliterated_text == "Moskva"

    def test_transliteration_result_optional_scheme(self):
        """Optional scheme in result."""
        from src.general_ludd.language.contracts import TransliterationResult

        result = TransliterationResult(
            transliterated_text="Moskva",
            source_script="Cyrl",
            target_script="Latn",
            scheme="ISO 9",
        )
        assert result.scheme == "ISO 9"

    def test_transliteration_result_empty_text_allowed(self):
        """TransliterationResult may have empty transliterated_text (edge case)."""
        from src.general_ludd.language.contracts import TransliterationResult

        result = TransliterationResult(
            transliterated_text="",
            source_script="Cyrl",
            target_script="Latn",
        )
        assert result.transliterated_text == ""

    def test_transliteration_batch_request(self):
        """TransliterationBatchRequest handles multiple texts."""
        from src.general_ludd.language.contracts import TransliterationBatchRequest

        req = TransliterationBatchRequest(
            target_script="Latn",
            texts=["Москва", "東京"],
            source_script=None,
            scheme="ISO 9",
        )
        assert len(req.texts) == 2

    def test_transliteration_batch_result(self):
        """TransliterationBatchResult wraps a list of TransliterationResult."""
        from src.general_ludd.language.contracts import (
            TransliterationBatchRequest,
            TransliterationBatchResult,
            TransliterationResult,
        )

        req = TransliterationBatchRequest(target_script="Latn", texts=["Москва"])
        results = [
            TransliterationResult(
                transliterated_text="Moskva",
                source_script="Cyrl",
                target_script="Latn",
            )
        ]
        batch = TransliterationBatchResult(request=req, results=results)
        assert len(batch.results) == 1


class TestScriptDetectionContracts:
    """Contracts for script detection."""

    def test_script_detection_result(self):
        """ScriptDetectionResult identifies a script from text."""
        from src.general_ludd.language.contracts import ScriptDetectionResult

        result = ScriptDetectionResult(
            script_code="Cyrl",
            script_name="Cyrillic",
            confidence=0.99,
        )
        assert result.script_code == "Cyrl"
        assert result.script_name == "Cyrillic"

    def test_language_profile_contract(self):
        """LanguageProfile bundles metadata about a language."""
        from src.general_ludd.language.contracts import LanguageProfile

        profile = LanguageProfile(
            iso_639_1="en",
            iso_639_2="eng",
            iso_639_3="eng",
            name_en="English",
            native_name="English",
            scripts=["Latn"],
            direction="ltr",
        )
        assert profile.iso_639_1 == "en"
        assert profile.scripts == ["Latn"]
        assert profile.direction == "ltr"

    def test_language_profile_direction_enum(self):
        """direction must be 'ltr', 'rtl', or 'ttb'."""
        from src.general_ludd.language.contracts import LanguageProfile

        profile = LanguageProfile(
            iso_639_1="ar",
            iso_639_2="ara",
            name_en="Arabic",
            native_name="العربية",
            scripts=["Arab"],
            direction="rtl",
        )
        assert profile.direction == "rtl"

        with pytest.raises(ValidationError):
            LanguageProfile(
                iso_639_1="xx",
                iso_639_2="xxx",
                name_en="Bad",
                native_name="Bad",
                scripts=["Latn"],
                direction="diagonal",
            )


class TestContractExports:
    """Verify all contracts are exported from the module."""

    def test_contracts_module_exports_all_models(self):
        """All contract types are in __all__."""
        from src.general_ludd.language import contracts

        expected = {
            "LanguageDetectionResult",
            "DetectionBatchRequest",
            "DetectionBatchResult",
            "TranslationRequest",
            "TranslationResult",
            "TranslationBatchRequest",
            "TranslationBatchResult",
            "TransliterationRequest",
            "TransliterationResult",
            "TransliterationBatchRequest",
            "TransliterationBatchResult",
            "ScriptDetectionResult",
            "LanguageProfile",
            "SCHEMA_VERSION",
        }
        exported = set(contracts.__all__)
        missing = expected - exported
        assert not missing, f"Missing exports: {missing}"
