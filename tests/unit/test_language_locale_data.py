"""Deep tests for src/general_ludd/language/locale_data.py."""

from __future__ import annotations

from general_ludd.language.locale_data import (
    _PLURAL_GRAMMARS,
    CLDR_FIRST_DAY_OF_WEEK,
    CLDR_MEASUREMENT_SYSTEMS,
    COMMON_CURRENCIES,
    ISO_639_1_TO_NAME,
    ISO_3166_TO_NAME,
    ISO_15924_TO_NAME,
    LOCALE_FORMATS,
    RTL_LANGUAGES,
    RTL_SCRIPTS,
    _apply_grouping,
    evaluate_plural,
    format_currency,
    format_number,
    get_locale_data,
    negotiate_locale,
    parse_bcp47,
)


class TestParseBcp47:
    def test_empty_tag_returns_empty(self):
        result = parse_bcp47("")
        assert result["language"] == ""
        assert result["script"] == ""
        assert result["territory"] == ""
        assert result["codeset"] == ""
        assert result["canonical"] == ""

    def test_simple_language(self):
        result = parse_bcp47("en")
        assert result["language"] == "en"
        assert result["script"] == ""
        assert result["territory"] == ""
        assert result["canonical"] == "en"

    def test_language_territory_hyphen(self):
        result = parse_bcp47("en-US")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["canonical"] == "en-US"

    def test_language_territory_underscore(self):
        result = parse_bcp47("en_US")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["canonical"] == "en-US"

    def test_language_script_territory(self):
        result = parse_bcp47("zh-Hans-CN")
        assert result["language"] == "zh"
        assert result["script"] == "Hans"
        assert result["territory"] == "CN"
        assert result["canonical"] == "zh-Hans-CN"

    def test_with_codeset(self):
        result = parse_bcp47("en_US.UTF-8")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["codeset"] == "UTF-8"
        assert result["canonical"] == "en-US"

    def test_with_at_modifier_stripped(self):
        result = parse_bcp47("de-DE@euro")
        assert result["language"] == "de"
        assert result["territory"] == "DE"
        assert result["canonical"] == "de-DE"

    def test_two_char_script_interpreted_as_territory(self):
        result = parse_bcp47("sr-Latn")
        assert result["language"] == "sr"
        assert result["script"] == "Latn"
        assert result["territory"] == ""

    def test_three_digit_territory(self):
        result = parse_bcp47("es-419")
        assert result["language"] == "es"
        assert result["territory"] == "419"
        assert result["canonical"] == "es-419"

    def test_whitespace_trimmed(self):
        result = parse_bcp47("  fr-FR  ")
        assert result["language"] == "fr"
        assert result["territory"] == "FR"


class TestGetLocaleData:
    def test_exact_match(self):
        data = get_locale_data("en-US")
        assert data is not None
        assert data["bcp47"] == "en-US"
        assert data["is_rtl"] is False

    def test_underscore_variant(self):
        data = get_locale_data("en_US")
        assert data is not None
        assert data["bcp47"] == "en-US"

    def test_language_fallback_first_match(self):
        data = get_locale_data("en-AU")
        assert data is not None
        assert data["bcp47"] == "en-US"

    def test_language_fallback_german_dialect(self):
        data = get_locale_data("de-AT")
        assert data is not None
        assert data["bcp47"] == "de-DE"

    def test_none_for_empty_tag(self):
        assert get_locale_data("") is None

    def test_none_for_unknown_language(self):
        assert get_locale_data("zz-ZZ") is None

    def test_rtl_locale(self):
        data = get_locale_data("ar-SA")
        assert data is not None
        assert data["is_rtl"] is True


class TestNegotiateLocale:
    def test_exact_match(self):
        result = negotiate_locale("en-US", ["en-US", "de-DE", "fr-FR"])
        assert result == "en-US"

    def test_language_prefix_fallback(self):
        result = negotiate_locale("en-GB", ["de-DE", "en-US", "fr-FR"])
        assert result == "en-US"

    def test_q_value_ordering(self):
        result = negotiate_locale(
            "fr-FR;q=0.5, de-DE;q=0.8, en-US;q=1.0",
            ["fr-FR", "de-DE", "en-US"],
        )
        assert result == "en-US"

    def test_wildcard_returns_first_available(self):
        result = negotiate_locale("*", ["de-DE", "en-US"])
        assert result == "de-DE"

    def test_q_zero_skipped(self):
        result = negotiate_locale(
            "fr-FR;q=0, en-US;q=1.0",
            ["fr-FR", "en-US"],
        )
        assert result == "en-US"

    def test_underscore_in_available_normalized(self):
        result = negotiate_locale("en_US", ["en_US", "de_DE"])
        assert result == "en_US"

    def test_no_match_returns_default(self):
        result = negotiate_locale("fr-FR", ["de-DE", "zh-CN"], default="en-US")
        assert result == "en-US"

    def test_default_none_when_no_default(self):
        result = negotiate_locale("fr-FR", ["de-DE", "zh-CN"])
        assert result is None

    def test_empty_accept_returns_default(self):
        result = negotiate_locale("", ["en-US"])
        assert result is None

    def test_empty_available_returns_default(self):
        result = negotiate_locale("en-US", [], default="fr-FR")
        assert result == "fr-FR"

    def test_q_value_zero_bounds(self):
        result = negotiate_locale("en;q=0.0", ["en-US"])
        assert result is None

    def test_invalid_q_value(self):
        result = negotiate_locale("fr-FR;q=invalid, en-US;q=1.0", ["fr-FR", "en-US"])
        assert result == "en-US"


class TestEvaluatePlural:
    def test_english_one(self):
        assert evaluate_plural("en-US", 1) == "one"

    def test_english_zero(self):
        assert evaluate_plural("en-US", 0) == "other"

    def test_english_many(self):
        assert evaluate_plural("en-US", 5) == "other"

    def test_french_one_range(self):
        assert evaluate_plural("fr-FR", 0) == "one"
        assert evaluate_plural("fr-FR", 1) == "one"

    def test_french_many(self):
        assert evaluate_plural("fr-FR", 2) == "other"

    def test_russian_one(self):
        assert evaluate_plural("ru-RU", 1) == "one"

    def test_russian_one_21(self):
        assert evaluate_plural("ru-RU", 21) == "one"

    def test_russian_few(self):
        assert evaluate_plural("ru-RU", 2) == "few"
        assert evaluate_plural("ru-RU", 3) == "few"
        assert evaluate_plural("ru-RU", 4) == "few"

    def test_russian_few_excludes_12_14(self):
        assert evaluate_plural("ru-RU", 12) == "many"
        assert evaluate_plural("ru-RU", 13) == "many"
        assert evaluate_plural("ru-RU", 14) == "many"

    def test_russian_many(self):
        assert evaluate_plural("ru-RU", 0) == "many"
        assert evaluate_plural("ru-RU", 5) == "many"
        assert evaluate_plural("ru-RU", 11) == "many"

    def test_arabic_zero(self):
        assert evaluate_plural("ar-SA", 0) == "zero"

    def test_arabic_one(self):
        assert evaluate_plural("ar-SA", 1) == "one"

    def test_arabic_two(self):
        assert evaluate_plural("ar-SA", 2) == "two"

    def test_arabic_few(self):
        assert evaluate_plural("ar-SA", 3) == "few"
        assert evaluate_plural("ar-SA", 10) == "few"
        assert evaluate_plural("ar-SA", 103) == "few"

    def test_arabic_many(self):
        assert evaluate_plural("ar-SA", 11) == "many"
        assert evaluate_plural("ar-SA", 99) == "many"
        assert evaluate_plural("ar-SA", 111) == "many"

    def test_japanese_always_other(self):
        assert evaluate_plural("ja-JP", 0) == "other"
        assert evaluate_plural("ja-JP", 1) == "other"
        assert evaluate_plural("ja-JP", 1000) == "other"

    def test_chinese_always_other(self):
        assert evaluate_plural("zh-CN", 1) == "other"
        assert evaluate_plural("zh-CN", 5) == "other"

    def test_unknown_language_defaults_other(self):
        assert evaluate_plural("zz-ZZ", 1) == "other"


class TestApplyGrouping:
    def test_standard_threes(self):
        result = _apply_grouping("1234567", ",", [3])
        assert result == "1,234,567"

    def test_short_number_no_grouping(self):
        result = _apply_grouping("12", ",", [3])
        assert result == "12"

    def test_exact_triple(self):
        result = _apply_grouping("123", ",", [3])
        assert result == "123"

    def test_indian_grouping(self):
        result = _apply_grouping("1234567", ",", [3, 2])
        assert result == "12,34,567"

    def test_empty_pattern_returns_input(self):
        result = _apply_grouping("1234", ",", [])
        assert result == "1234"

    def test_empty_int_part(self):
        result = _apply_grouping("", ",", [3])
        assert result == ""


class TestFormatNumber:
    def test_en_us_integer(self):
        result = format_number(1234, "en-US")
        assert result == "1,234"

    def test_en_us_negative(self):
        result = format_number(-5678, "en-US")
        assert result == "-5,678"

    def test_en_us_decimal(self):
        result = format_number(1234.50, "en-US")
        assert result == "1,234.5"

    def test_de_de_decimal_comma(self):
        result = format_number(1234.50, "de-DE")
        assert result == "1.234,5"

    def test_fr_fr_narrow_no_break_space(self):
        result = format_number(1000, "fr-FR")
        assert result == "1\u202f000"

    def test_ar_sa_grouping(self):
        data = LOCALE_FORMATS.get("ar-SA")
        assert data is not None
        result = format_number(1234, "ar-SA")
        assert "\u066c" in result

    def test_unknown_locale_plain(self):
        result = format_number(1234.5, "zz-ZZ")
        assert result == "1234.5"


class TestFormatCurrency:
    def test_usd_amount(self):
        result = format_currency(42.50, "USD", "en-US")
        assert result == "$42.50"

    def test_eur_amount(self):
        result = format_currency(10.99, "EUR", "de-DE")
        assert "10,99" in result

    def test_jpy_no_decimals(self):
        result = format_currency(500, "JPY", "ja-JP")
        assert result == "\xa5500"

    def test_negative_amount(self):
        result = format_currency(-10.50, "USD", "en-US")
        assert result == "$-10.50"

    def test_unknown_currency_fallback(self):
        result = format_currency(100, "XYZ", "en-US")
        assert "XYZ" in result

    def test_ar_sa_rial_fallback_as_code(self):
        result = format_currency(100, "SAR", "ar-SA")
        assert "SAR" in result

    def test_rub_after(self):
        result = format_currency(500, "RUB", "ru-RU")
        assert result.endswith("\u20bd")


class TestRtlData:
    def test_arabic_script_is_rtl(self):
        assert "Arab" in RTL_SCRIPTS

    def test_hebrew_script_is_rtl(self):
        assert "Hebr" in RTL_SCRIPTS

    def test_arabic_language_is_rtl(self):
        assert "ar" in RTL_LANGUAGES

    def test_hebrew_language_is_rtl(self):
        assert "he" in RTL_LANGUAGES

    def test_english_not_rtl(self):
        assert "en" not in RTL_LANGUAGES


class TestCommonCurrencies:
    def test_usd(self):
        assert COMMON_CURRENCIES["USD"]["symbol"] == "$"
        assert COMMON_CURRENCIES["USD"]["decimal_digits"] == 2

    def test_jpy_zero_decimals(self):
        assert COMMON_CURRENCIES["JPY"]["decimal_digits"] == 0

    def test_eur_after(self):
        assert COMMON_CURRENCIES["EUR"]["placement"] == "after"


class TestCldrSupplemental:
    def test_us_first_day_sunday(self):
        assert CLDR_FIRST_DAY_OF_WEEK["US"] == 0

    def test_gb_first_day_monday(self):
        assert CLDR_FIRST_DAY_OF_WEEK["GB"] == 0

    def test_de_first_day_monday(self):
        assert CLDR_FIRST_DAY_OF_WEEK["DE"] == 1

    def test_sa_first_day_saturday(self):
        assert CLDR_FIRST_DAY_OF_WEEK["SA"] == 5

    def test_us_measurement_system(self):
        assert CLDR_MEASUREMENT_SYSTEMS["US"] == "US"

    def test_de_metric(self):
        assert CLDR_MEASUREMENT_SYSTEMS["DE"] == "metric"


class TestIsoData:
    def test_iso_639_en(self):
        assert ISO_639_1_TO_NAME["en"] == "English"

    def test_iso_639_zh(self):
        assert ISO_639_1_TO_NAME["zh"] == "Chinese"

    def test_iso_3166_us(self):
        assert ISO_3166_TO_NAME["US"] == "United States"

    def test_iso_15924_latn(self):
        assert ISO_15924_TO_NAME["Latn"] == "Latin"

    def test_iso_15924_arab(self):
        assert ISO_15924_TO_NAME["Arab"] == "Arabic"


class TestPluralGrammars:
    def test_english_grammar(self):
        assert "one" in _PLURAL_GRAMMARS["en"]
        assert "other" in _PLURAL_GRAMMARS["en"]

    def test_russian_grammar(self):
        assert _PLURAL_GRAMMARS["ru"]["one"]  # non-empty rule
        assert _PLURAL_GRAMMARS["ru"]["few"]
        assert _PLURAL_GRAMMARS["ru"]["many"]

    def test_arabic_grammar(self):
        assert _PLURAL_GRAMMARS["ar"]["zero"]
        assert _PLURAL_GRAMMARS["ar"]["one"]
        assert _PLURAL_GRAMMARS["ar"]["two"]
        assert _PLURAL_GRAMMARS["ar"]["few"]
        assert _PLURAL_GRAMMARS["ar"]["many"]
