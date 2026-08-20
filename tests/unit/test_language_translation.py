"""Unit tests for language translation module."""

from __future__ import annotations


class TestTranslate:
    def test_translate_english_to_german(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "de")
        assert result["source_language"] == "en"
        assert result["target_language"] == "de"
        assert result["translated_text"] == "hallo"
        assert result["confidence"] == 1.0
        assert result["engine"] == "dictionary"

    def test_translate_english_to_french(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("thank you", "en", "fr")
        assert result["translated_text"] == "merci"
        assert result["confidence"] > 0.0

    def test_translate_english_to_spanish(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("good morning", "en", "es")
        assert result["translated_text"] == "buenos días"

    def test_translate_english_to_italian(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "it")
        assert result["translated_text"] == "ciao"

    def test_translate_english_to_portuguese(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "pt")
        assert result["translated_text"] == "olá"

    def test_translate_english_to_russian(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("thank you", "en", "ru")
        assert result["translated_text"] == "спасибо"

    def test_translate_english_to_japanese(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "ja")
        assert result["translated_text"] == "こんにちは"

    def test_translate_english_to_korean(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("thank you", "en", "ko")
        assert result["translated_text"] == "감사합니다"

    def test_translate_english_to_chinese(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "zh")
        assert result["translated_text"] == "你好"

    def test_translate_english_to_arabic(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("welcome", "en", "ar")
        assert result["translated_text"] == "أهلا بك"

    def test_same_language_identity(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "en")
        assert result["translated_text"] == "hello"
        assert result["confidence"] == 1.0
        assert result["engine"] == "identity"

    def test_empty_text(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("", "en", "de")
        assert result["translated_text"] == ""
        assert result["confidence"] == 0.0

    def test_unknown_word_passthrough(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("xylophone", "en", "de")
        assert result["source_language"] == "en"
        assert result["target_language"] == "de"
        assert "engine" in result

    def test_result_has_required_keys(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello", "en", "de")
        for key in (
            "source_language",
            "source_text",
            "target_language",
            "translated_text",
            "confidence",
            "engine",
            "alternative",
            "error",
        ):
            assert key in result, f"Missing key: {key}"


class TestMultiWordTranslation:
    def test_multi_word_dictionary(self) -> None:
        from general_ludd.language.translation import translate

        result = translate("hello friend", "en", "de")
        assert result["engine"] == "dictionary"
        assert "hallo" in result["translated_text"]


class TestDictionaryData:
    def test_dictionary_has_required_languages(self) -> None:
        from general_ludd.language.translation import _DICTIONARY

        assert "en" in _DICTIONARY
        assert "de" in _DICTIONARY["en"]
        assert "fr" in _DICTIONARY["en"]
        assert "es" in _DICTIONARY["en"]
        assert "it" in _DICTIONARY["en"]
        assert "pt" in _DICTIONARY["en"]
        assert "ru" in _DICTIONARY["en"]
        assert "ja" in _DICTIONARY["en"]
        assert "ko" in _DICTIONARY["en"]
        assert "zh" in _DICTIONARY["en"]
        assert "ar" in _DICTIONARY["en"]
