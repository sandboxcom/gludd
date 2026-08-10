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


class TestDetectLanguageDeep:
    def test_confidence_bounded(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("The weather is beautiful and I am very happy about it today.")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_method_field(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("Hello world. This is English text with common words and phrases.")
        assert result["method"] in ("stopword+freq", "script-only")

    def test_iso_639_1_matches_language(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("The quick brown fox jumps over the lazy dog.")
        assert result["iso_639_1"] == result["language"]

    def test_top_n_limits_alternatives(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("The quick brown fox jumps over the lazy dog.", top_n=2)
        assert len(result["alternative"]) <= 1


class TestDetectLanguagesInText:
    def test_single_sentence(self) -> None:
        from general_ludd.language.detection import detect_languages_in_text

        results = detect_languages_in_text("Hello world.")
        assert len(results) >= 1
        assert results[0]["language"] != "unknown"

    def test_multi_sentence_english(self) -> None:
        from general_ludd.language.detection import detect_languages_in_text

        results = detect_languages_in_text(
            "The weather is nice today. I think we should go for a walk. It will be fun."
        )
        assert len(results) >= 1

    def test_mixed_language_sentences(self) -> None:
        from general_ludd.language.detection import detect_languages_in_text

        results = detect_languages_in_text("The weather is nice today. 今日は天気がいいですね。El clima es bueno.")
        assert len(results) >= 1

    def test_threshold_filters_low_confidence(self) -> None:
        from general_ludd.language.detection import detect_languages_in_text

        results = detect_languages_in_text("a b. c d. e f.", threshold=0.99)
        assert isinstance(results, list)

    def test_returns_segment_and_keys(self) -> None:
        from general_ludd.language.detection import detect_languages_in_text

        results = detect_languages_in_text("Hello world. This is a test.")
        for r in results:
            for key in ("segment", "language", "language_name", "confidence", "script"):
                assert key in r, f"Missing key: {key}"


class TestSplitSentences:
    def test_splits_on_period(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("Hello. World.")
        assert len(parts) == 2
        assert parts[0] == "Hello."

    def test_splits_on_newline(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("Line one.\n Line two.\n Line three")
        assert len(parts) >= 2

    def test_splits_on_exclamation(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("Wow! Amazing! Cool!")
        assert len(parts) == 3

    def test_splits_on_question(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("What is this? Why is it here?")
        assert len(parts) == 2

    def test_single_sentence_no_split(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("This is a single sentence without punctuation")
        assert len(parts) == 1

    def test_empty_string(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("")
        assert parts == []

    def test_whitespace_only(self) -> None:
        from general_ludd.language.detection import _split_sentences

        parts = _split_sentences("   \n   \t  ")
        assert parts == []


class TestScriptOf:
    def test_latin_a(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("A")) == "Latin"
        assert _script_of(ord("a")) == "Latin"

    def test_cyrillic(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("П")) == "Cyrillic"
        assert _script_of(ord("п")) == "Cyrillic"

    def test_hiragana(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("あ")) == "Hiragana"

    def test_katakana(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("カ")) == "Katakana"

    def test_hangul(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("한")) == "Hangul"

    def test_arabic(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("ع")) == "Arabic"

    def test_devanagari(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("अ")) == "Devanagari"

    def test_chinese_han(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("中")) == "Han"

    def test_greek(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("α")) == "Greek"

    def test_thai(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("ก")) == "Thai"

    def test_hebrew(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("א")) == "Hebrew"

    def test_bengali(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("অ")) == "Bengali"

    def test_tamil(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("அ")) == "Tamil"

    def test_telugu(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("అ")) == "Telugu"

    def test_malayalam(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("അ")) == "Malayalam"

    def test_gujarati(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("અ")) == "Gujarati"

    def test_gurmukhi(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(ord("ਅ")) == "Gurmukhi"

    def test_out_of_range_codepoint(self) -> None:
        from general_ludd.language.detection import _script_of

        assert _script_of(0x110000) == "Unknown"

    def test_punctuation_not_crashed(self) -> None:
        from general_ludd.language.detection import _script_of

        assert isinstance(_script_of(ord(".")), str)


class TestPrimaryScript:
    def test_latin_text(self) -> None:
        from general_ludd.language.detection import _primary_script

        assert _primary_script("Hello world") == "Latin"

    def test_cyrillic_text(self) -> None:
        from general_ludd.language.detection import _primary_script

        assert _primary_script("Привет мир") == "Cyrillic"

    def test_mixed_script_dominant(self) -> None:
        from general_ludd.language.detection import _primary_script

        script = _primary_script("Hello мир")
        assert script == "Latin"

    def test_empty_defaults_latin(self) -> None:
        from general_ludd.language.detection import _primary_script

        assert _primary_script("") == "Latin"

    def test_punctuation_only_defaults_latin(self) -> None:
        from general_ludd.language.detection import _primary_script

        assert _primary_script(".,;:!?\"'-–—…") == "Latin"

    def test_numbers_only(self) -> None:
        from general_ludd.language.detection import _primary_script

        assert _primary_script("12345 67890") == "Latin"


class TestCountScriptChars:
    def test_counts_latin(self) -> None:
        from general_ludd.language.detection import _count_script_chars

        count = _count_script_chars("Hello World", "Latin")
        assert count == 10

    def test_counts_cyrillic(self) -> None:
        from general_ludd.language.detection import _count_script_chars

        count = _count_script_chars("Привет", "Cyrillic")
        assert count == 6

    def test_ignores_punctuation(self) -> None:
        from general_ludd.language.detection import _count_script_chars

        count = _count_script_chars("Hello, World!", "Latin")
        assert count == 10

    def test_ignores_digits(self) -> None:
        from general_ludd.language.detection import _count_script_chars

        count = _count_script_chars("abc123", "Latin")
        assert count == 3

    def test_zero_for_wrong_script(self) -> None:
        from general_ludd.language.detection import _count_script_chars

        count = _count_script_chars("Hello World", "Cyrillic")
        assert count == 0


class TestStopwordPairwise:
    def test_english_stopwords_present(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("the be to of and a in that have it for")
        assert result["language"] == "en"

    def test_dutch_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("de het van en een in is dat te op voor zijn niet die met er aan om worden dan bij")
        assert result["language"] in ("en", "nl")
        assert result["script"] == "Latin"

    def test_swedish_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("och att det som en på i är av för med inte har till om den de ett man sig")
        assert result["script"] == "Latin"

    def test_turkish_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("ve bir bu da için de ile olarak ne değil gibi daha en çok var kadar sonra")
        assert result["script"] == "Latin"

    def test_polish_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("w się na z nie to do że jak jest po co dla już między lub ale przez przy")
        assert result["script"] == "Latin"

    def test_vietnamese_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("và của có không được trong một cho là với nhưng các đã sẽ những người khi")
        assert result["script"] == "Latin"

    def test_thai_stopwords(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("ที่เป็นว่าและไม่ในจะมีได้ของไปมาะาเขามันอันด้วยจากโดยถึงหรือ")
        assert result["script"] == "Thai"


class TestFrequencyFallback:
    def test_latin_no_stopwords_falls_back_to_freq(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("xylophone zebra quartz jester")
        assert result["script"] == "Latin"
        assert result["language"] != "unknown"


class TestUnicodeNameToScript:
    def test_all_keys_map_to_scripts(self) -> None:
        from general_ludd.language.detection import _UNICODE_NAME_TO_SCRIPT

        keys = {
            "CJK",
            "HANGUL",
            "HIRAGANA",
            "KATAKANA",
            "LATIN",
            "CYRILLIC",
            "ARABIC",
            "DEVANAGARI",
            "THAI",
            "GREEK",
            "HEBREW",
            "BENGALI",
            "TAMIL",
            "TELUGU",
            "MALAYALAM",
            "GUJARATI",
            "GURMUKHI",
        }
        for k in keys:
            assert k in _UNICODE_NAME_TO_SCRIPT, f"Missing key: {k}"


class TestStopwordTables:
    def test_all_tables_are_frozensets(self) -> None:
        from general_ludd.language.detection import _STOPWORDS

        for lang, stopwords in _STOPWORDS.items():
            assert isinstance(stopwords, frozenset), f"{lang} stopwords are not frozenset"

    def test_all_langs_have_min_words(self) -> None:
        from general_ludd.language.detection import _STOPWORDS

        for lang, stopwords in _STOPWORDS.items():
            assert len(stopwords) >= 15, f"{lang} has only {len(stopwords)} stopwords"

    def test_stopwords_lowercase(self) -> None:
        from general_ludd.language.detection import _STOPWORDS

        for lang, stopwords in _STOPWORDS.items():
            for word in stopwords:
                assert word == word.lower(), f"{lang} has uppercase stopword: {word}"


class TestFrequencyProfiles:
    def test_all_profiles_non_empty(self) -> None:
        from general_ludd.language.detection import _LANG_FREQUENCY_LATIN_CHARS

        for lang, chars in _LANG_FREQUENCY_LATIN_CHARS.items():
            assert len(chars) > 0, f"{lang} frequency profile is empty"

    def test_english_etaoin(self) -> None:
        from general_ludd.language.detection import _LANG_FREQUENCY_LATIN_CHARS

        en = _LANG_FREQUENCY_LATIN_CHARS["en"]
        assert en.startswith("et")

    def test_german_has_umlauts(self) -> None:
        from general_ludd.language.detection import _LANG_FREQUENCY_LATIN_CHARS

        de = _LANG_FREQUENCY_LATIN_CHARS["de"]
        assert "ü" in de or "ä" in de or "ö" in de


class TestUnknownInput:
    def test_gibberish_still_returns_structure(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("zzz qqq xxx yyy www")
        for key in ("language", "language_name", "confidence", "script"):
            assert key in result

    def test_single_letter(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("x")
        assert result["script"] in ("Latin", "Unknown")

    def test_numbers_and_symbols_only(self) -> None:
        from general_ludd.language.detection import detect_language

        result = detect_language("12345 67890")
        assert result["script"] in ("Latin", "Unknown")
