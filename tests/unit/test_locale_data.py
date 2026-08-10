"""TDD tests for ``src/general_ludd/language/locale_data.py``.

Covers: BCP 47 parsing, locale lookups, plural rule evaluation,
number/currency formatting, locale negotiation, and data constants.
"""

from __future__ import annotations

from general_ludd.language.locale_data import (
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

# ── BCP 47 parsing ─────────────────────────────────────────────────────────


class TestParseBcp47:
    def test_standard_en_us(self) -> None:
        result = parse_bcp47("en-US")
        assert result["language"] == "en"
        assert result["script"] == ""
        assert result["territory"] == "US"
        assert result["codeset"] == ""
        assert result["canonical"] == "en-US"

    def test_underscore_variant(self) -> None:
        result = parse_bcp47("en_US")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["canonical"] == "en-US"

    def test_with_codeset(self) -> None:
        result = parse_bcp47("en_US.UTF-8")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["codeset"] == "UTF-8"
        assert result["canonical"] == "en-US"

    def test_script_tag(self) -> None:
        result = parse_bcp47("zh-Hans-CN")
        assert result["language"] == "zh"
        assert result["script"] == "Hans"
        assert result["territory"] == "CN"
        assert result["canonical"] == "zh-Hans-CN"

    def test_script_only_no_territory(self) -> None:
        result = parse_bcp47("sr-Latn")
        assert result["language"] == "sr"
        assert result["script"] == "Latn"
        assert result["territory"] == ""
        assert result["canonical"] == "sr-Latn"

    def test_empty_tag(self) -> None:
        result = parse_bcp47("")
        assert result["language"] == ""
        assert result["script"] == ""
        assert result["territory"] == ""
        assert result["canonical"] == ""

    def test_language_only(self) -> None:
        result = parse_bcp47("fr")
        assert result["language"] == "fr"
        assert result["script"] == ""
        assert result["territory"] == ""
        assert result["codeset"] == ""
        assert result["canonical"] == "fr"

    def test_with_at_modifier_stripped(self) -> None:
        result = parse_bcp47("de-DE@euro")
        assert result["canonical"] == "de-DE"
        assert result["language"] == "de"
        assert result["territory"] == "DE"

    def test_numeric_region(self) -> None:
        result = parse_bcp47("es-419")
        assert result["language"] == "es"
        assert result["territory"] == "419"
        assert result["canonical"] == "es-419"

    def test_extra_segments_ignored(self) -> None:
        result = parse_bcp47("en-US-x-private")
        assert result["language"] == "en"
        assert result["territory"] == "US"
        assert result["canonical"] == "en-US"


# ── Locale data lookups ────────────────────────────────────────────────────


class TestGetLocaleData:
    def test_exact_match_en_us(self) -> None:
        data = get_locale_data("en-US")
        assert data is not None
        assert data["bcp47"] == "en-US"
        assert data["language_name"] == "English (United States)"
        assert data["territory"] == "US"
        assert data["is_rtl"] is False
        assert data["number_format"]["decimal_separator"] == "."

    def test_exact_match_ar_sa(self) -> None:
        data = get_locale_data("ar-SA")
        assert data is not None
        assert data["is_rtl"] is True
        assert data["script"] == "Arab"
        assert data["number_format"]["decimal_separator"] == "\u066b"

    def test_underscore_normalized(self) -> None:
        data = get_locale_data("en_GB")
        assert data is not None
        assert data["bcp47"] == "en-GB"
        assert data["currency_format"]["code"] == "GBP"

    def test_language_fallback(self) -> None:
        data = get_locale_data("en-AU")
        assert data is not None
        assert data["bcp47"] in ("en-US", "en-GB")

    def test_french_fallback(self) -> None:
        data = get_locale_data("fr-CA")
        assert data is not None
        assert data["language_name"] == "French (France)"

    def test_None_tag_returns_none(self) -> None:
        assert get_locale_data("") is None

    def test_unknown_locale(self) -> None:
        assert get_locale_data("xx-YY") is None

    def test_all_explicit_locales(self) -> None:
        for tag in LOCALE_FORMATS:
            assert get_locale_data(tag) is not None

    def test_de_de_number_format(self) -> None:
        data = get_locale_data("de-DE")
        assert data is not None
        assert data["number_format"]["decimal_separator"] == ","
        assert data["number_format"]["grouping_separator"] == "."

    def test_ru_ru_plural_rules(self) -> None:
        data = get_locale_data("ru-RU")
        assert data is not None
        assert "one" in data["plural_rules"]
        assert data["plural_rules"]["one"] != ""


# ── Locale negotiation ─────────────────────────────────────────────────────


class TestNegotiateLocale:
    def test_exact_match(self) -> None:
        result = negotiate_locale("en-US", ["en-US", "fr-FR"])
        assert result == "en-US"

    def test_fallback_to_language(self) -> None:
        result = negotiate_locale("en-GB", ["en-US", "fr-FR"])
        assert result == "en-US"

    def test_q_value_ordering(self) -> None:
        result = negotiate_locale(
            "fr-FR;q=0.9, de-DE;q=0.8, en-US",
            ["de-DE", "en-US", "fr-FR"],
        )
        assert result == "en-US"

    def test_q_zero_skipped(self) -> None:
        result = negotiate_locale(
            "fr-FR;q=1.0, en-US;q=0",
            ["en-US"],
        )
        assert result is None

    def test_wildcard_selects_first(self) -> None:
        result = negotiate_locale("*", ["de-DE", "ja-JP"])
        assert result == "de-DE"

    def test_default_returned(self) -> None:
        result = negotiate_locale("xx-YY", ["en-US", "fr-FR"], default="en-US")
        assert result == "en-US"

    def test_empty_accept_language(self) -> None:
        result = negotiate_locale("", ["en-US", "fr-FR"], default="en-US")
        assert result == "en-US"

    def test_empty_available(self) -> None:
        result = negotiate_locale("en-US", [], default="en-US")
        assert result == "en-US"

    def test_no_match_no_default(self) -> None:
        result = negotiate_locale("xx-YY", ["en-US"])
        assert result is None

    def test_underscore_normalized(self) -> None:
        result = negotiate_locale("en_GB", ["en-US", "en-GB"])
        assert result == "en-GB"

    def test_multiple_q_params(self) -> None:
        result = negotiate_locale(
            "fr-FR;q=0.5, de-DE;q=0.8, en-US;q=0.9",
            ["de-DE", "fr-FR", "en-US"],
        )
        assert result == "en-US"

    def test_invalid_q_value(self) -> None:
        result = negotiate_locale(
            "fr-FR;q=invalid, en-US",
            ["fr-FR", "en-US"],
        )
        assert result == "en-US"


# ── Plural rule evaluation ─────────────────────────────────────────────────


class TestEvaluatePlural:
    def test_english_one(self) -> None:
        assert evaluate_plural("en-US", 1) == "one"

    def test_english_other(self) -> None:
        assert evaluate_plural("en-US", 2) == "other"
        assert evaluate_plural("en-US", 0) == "other"
        assert evaluate_plural("en-US", 100) == "other"

    def test_french_one_zero(self) -> None:
        assert evaluate_plural("fr-FR", 0) == "one"
        assert evaluate_plural("fr-FR", 1) == "one"

    def test_french_other(self) -> None:
        assert evaluate_plural("fr-FR", 2) == "other"

    def test_russian_one(self) -> None:
        assert evaluate_plural("ru-RU", 1) == "one"
        assert evaluate_plural("ru-RU", 21) == "one"
        assert evaluate_plural("ru-RU", 31) == "one"

    def test_russian_few(self) -> None:
        assert evaluate_plural("ru-RU", 2) == "few"
        assert evaluate_plural("ru-RU", 3) == "few"
        assert evaluate_plural("ru-RU", 22) == "few"

    def test_russian_many(self) -> None:
        assert evaluate_plural("ru-RU", 0) == "many"
        assert evaluate_plural("ru-RU", 5) == "many"
        assert evaluate_plural("ru-RU", 11) == "many"
        assert evaluate_plural("ru-RU", 20) == "many"

    def test_arabic_zero(self) -> None:
        assert evaluate_plural("ar-SA", 0) == "zero"

    def test_arabic_one(self) -> None:
        assert evaluate_plural("ar-SA", 1) == "one"

    def test_arabic_two(self) -> None:
        assert evaluate_plural("ar-SA", 2) == "two"

    def test_arabic_few(self) -> None:
        assert evaluate_plural("ar-SA", 3) == "few"
        assert evaluate_plural("ar-SA", 10) == "few"

    def test_arabic_many(self) -> None:
        assert evaluate_plural("ar-SA", 11) == "many"
        assert evaluate_plural("ar-SA", 25) == "many"

    def test_arabic_other(self) -> None:
        assert evaluate_plural("ar-SA", 100) == "other"
        assert evaluate_plural("ar-SA", 200) == "other"

    def test_japanese_always_other(self) -> None:
        for n in (0, 1, 2, 5, 10, 100):
            assert evaluate_plural("ja-JP", n) == "other"

    def test_chinese_always_other(self) -> None:
        for n in (0, 1, 2, 5, 10, 100):
            assert evaluate_plural("zh-CN", n) == "other"

    def test_hebrew_always_other(self) -> None:
        for n in (0, 1, 2, 3, 5, 10, 100):
            assert evaluate_plural("he-IL", n) == "other"

    def test_unknown_locale_other(self) -> None:
        assert evaluate_plural("xx-YY", 5) == "other"

    def test_german_one_and_other(self) -> None:
        assert evaluate_plural("de-DE", 1) == "one"
        assert evaluate_plural("de-DE", 0) == "other"
        assert evaluate_plural("de-DE", 2) == "other"


# ── Number formatting ──────────────────────────────────────────────────────


class TestApplyGrouping:
    def test_no_pattern(self) -> None:
        assert _apply_grouping("1234567", ",", []) == "1234567"

    def test_empty_int_part(self) -> None:
        assert _apply_grouping("", ",", [3]) == ""

    def test_standard_3_group(self) -> None:
        assert _apply_grouping("1234567", ",", [3]) == "1,234,567"

    def test_variable_grouping(self) -> None:
        assert _apply_grouping("12345678", ",", [3, 2]) == "1,23,45,678"

    def test_small_number_no_grouping(self) -> None:
        assert _apply_grouping("123", ",", [3]) == "123"

    def test_zero_size_group(self) -> None:
        assert _apply_grouping("1234567", ",", [0]) == "1234567"


class TestFormatNumber:
    def test_en_us_integer(self) -> None:
        result = format_number(1234567, "en-US")
        assert result == "1,234,567"

    def test_en_us_decimal(self) -> None:
        result = format_number(1234567.89, "en-US")
        assert result == "1,234,567.89"

    def test_en_us_negative(self) -> None:
        result = format_number(-1234.5, "en-US")
        assert result == "-1,234.5"

    def test_de_de_different_separators(self) -> None:
        result = format_number(1234567.89, "de-DE")
        assert result == "1.234.567,89"

    def test_fr_fr_thin_space_separator(self) -> None:
        result = format_number(12345, "fr-FR")
        assert "12" in result
        assert "\u202f" in result

    def test_ar_sa_arabic_separators(self) -> None:
        result = format_number(1234567.89, "ar-SA")
        assert "\u066b" in result
        assert "\u066c" in result

    def test_unknown_locale_fallback(self) -> None:
        result = format_number(1234.5, "xx-YY")
        assert result == str(1234.5)

    def test_zero(self) -> None:
        result = format_number(0, "en-US")
        assert result == "0"

    def test_trailing_zeros_stripped(self) -> None:
        result = format_number(42.00, "en-US")
        assert result == "42"


# ── Currency formatting ────────────────────────────────────────────────────


class TestFormatCurrency:
    def test_usd_before(self) -> None:
        result = format_currency(1234.56, "USD", "en-US")
        assert result == "$1,234.56"

    def test_eur_after(self) -> None:
        result = format_currency(1234.56, "EUR", "de-DE")
        assert "1.234,56" in result
        assert "\u20ac" in result

    def test_jpy_zero_decimals(self) -> None:
        result = format_currency(1234, "JPY", "ja-JP")
        assert result == "\u00a51,234"

    def test_negative_amount(self) -> None:
        result = format_currency(-50.0, "USD", "en-US")
        assert result == "-$50.00" or result == "$-50.00"
        assert "50.00" in result
        assert "-" in result

    def test_unknown_currency_code(self) -> None:
        result = format_currency(100, "XXX", "en-US")
        assert "XXX" in result
        assert "100" in result

    def test_unknown_locale_fallback(self) -> None:
        result = format_currency(100.5, "USD", "xx-YY")
        assert "$" in result
        assert "100.50" in result

    def test_brl_symbol(self) -> None:
        result = format_currency(100, "BRL", "en-US")
        assert result == "R$100.00"

    def test_rub_after(self) -> None:
        result = format_currency(100.0, "RUB", "ru-RU")
        assert "\u20bd" in result

    def test_all_common_currencies(self) -> None:
        for code in COMMON_CURRENCIES:
            result = format_currency(1, code, "en-US")
            assert len(result) > 0


# ── Data constants ─────────────────────────────────────────────────────────


class TestRtlScripts:
    def test_arabic_in_rtl(self) -> None:
        assert "Arab" in RTL_SCRIPTS

    def test_hebrew_in_rtl(self) -> None:
        assert "Hebr" in RTL_SCRIPTS

    def test_rtl_languages_set(self) -> None:
        assert {"ar", "he", "fa", "ur", "ps", "sd", "ug", "yi", "dv"} == RTL_LANGUAGES


class TestIsoConstants:
    def test_has_common_languages(self) -> None:
        assert ISO_639_1_TO_NAME["en"] == "English"
        assert ISO_639_1_TO_NAME["fr"] == "French"
        assert ISO_639_1_TO_NAME["de"] == "German"
        assert ISO_639_1_TO_NAME["zh"] == "Chinese"
        assert ISO_639_1_TO_NAME["ja"] == "Japanese"

    def test_has_common_countries(self) -> None:
        assert ISO_3166_TO_NAME["US"] == "United States"
        assert ISO_3166_TO_NAME["GB"] == "United Kingdom"
        assert ISO_3166_TO_NAME["DE"] == "Germany"
        assert ISO_3166_TO_NAME["FR"] == "France"
        assert ISO_3166_TO_NAME["JP"] == "Japan"

    def test_has_common_scripts(self) -> None:
        assert ISO_15924_TO_NAME["Latn"] == "Latin"
        assert ISO_15924_TO_NAME["Cyrl"] == "Cyrillic"
        assert ISO_15924_TO_NAME["Arab"] == "Arabic"

    def test_iso_639_1_count(self) -> None:
        assert len(ISO_639_1_TO_NAME) > 100


class TestCldrSupplemental:
    def test_first_day_us_sunday(self) -> None:
        assert CLDR_FIRST_DAY_OF_WEEK["US"] == 0

    def test_first_day_gb_monday(self) -> None:
        assert CLDR_FIRST_DAY_OF_WEEK["GB"] == 0

    def test_first_day_de_monday(self) -> None:
        assert CLDR_FIRST_DAY_OF_WEEK["DE"] == 1

    def test_first_day_sa_saturday(self) -> None:
        assert CLDR_FIRST_DAY_OF_WEEK["SA"] == 5

    def test_measurement_us_imperial(self) -> None:
        assert CLDR_MEASUREMENT_SYSTEMS["US"] == "US"

    def test_measurement_gb_uk(self) -> None:
        assert CLDR_MEASUREMENT_SYSTEMS["GB"] == "UK"

    def test_measurement_de_metric(self) -> None:
        assert CLDR_MEASUREMENT_SYSTEMS["DE"] == "metric"


class TestCommonCurrencies:
    def test_has_12_currencies(self) -> None:
        assert len(COMMON_CURRENCIES) == 12

    def test_usd_placement_before(self) -> None:
        assert COMMON_CURRENCIES["USD"]["placement"] == "before"

    def test_eur_placement_after(self) -> None:
        assert COMMON_CURRENCIES["EUR"]["placement"] == "after"

    def test_jpy_zero_decimal_digits(self) -> None:
        assert COMMON_CURRENCIES["JPY"]["decimal_digits"] == 0

    def test_all_have_symbol(self) -> None:
        for code, fmt in COMMON_CURRENCIES.items():
            assert fmt["symbol"] != "", f"{code} missing symbol"
            assert fmt["code"] == code


class TestLocaleFormats:
    def test_10_explicit_locales(self) -> None:
        assert len(LOCALE_FORMATS) == 10

    def test_all_have_bcp47_key(self) -> None:
        for tag, data in LOCALE_FORMATS.items():
            assert data["bcp47"] == tag

    def test_all_have_four_date_formats(self) -> None:
        for data in LOCALE_FORMATS.values():
            for length in ("full", "long", "medium", "short"):
                assert length in data["date_format"]
                assert data["date_format"][length]

    def test_all_have_number_format(self) -> None:
        for data in LOCALE_FORMATS.values():
            nf = data["number_format"]
            assert nf["decimal_separator"]
            assert nf["grouping_separator"]
            assert len(nf["grouping_pattern"]) >= 1

    def test_all_have_plural_categories(self) -> None:
        expected_categories = {"zero", "one", "two", "few", "many", "other"}
        for data in LOCALE_FORMATS.values():
            assert set(data["plural_rules"].keys()) == expected_categories
