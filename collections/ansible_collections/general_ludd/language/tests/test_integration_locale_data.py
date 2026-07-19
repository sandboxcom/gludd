import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../../src'))

from general_ludd.language.locale_data import (
    LOCALE_FORMATS,
    RTL_SCRIPTS,
    RTL_LANGUAGES,
    COMMON_CURRENCIES,
    ISO_639_1_TO_NAME,
    ISO_3166_TO_NAME,
    CLDR_FIRST_DAY_OF_WEEK,
    CLDR_MEASUREMENT_SYSTEMS,
)


def test_locale_us_exists():
    assert 'en-US' in LOCALE_FORMATS


def test_locale_de_exists():
    assert 'de-DE' in LOCALE_FORMATS


def test_locale_ar_rtl():
    assert LOCALE_FORMATS['ar-SA']['is_rtl'] is True


def test_locale_en_not_rtl():
    assert LOCALE_FORMATS['en-US']['is_rtl'] is False


def test_rtl_scripts():
    assert 'Arab' in RTL_SCRIPTS


def test_rtl_languages():
    assert 'ar' in RTL_LANGUAGES


def test_common_currencies():
    assert 'USD' in COMMON_CURRENCIES
    assert 'EUR' in COMMON_CURRENCIES


def test_iso_639_lookup():
    assert ISO_639_1_TO_NAME['en'] == 'English'


def test_iso_3166_lookup():
    assert ISO_3166_TO_NAME['US'] == 'United States'


def test_first_day_of_week():
    assert CLDR_FIRST_DAY_OF_WEEK['US'] == 0


def test_measurement_system():
    assert CLDR_MEASUREMENT_SYSTEMS['US'] == 'US'
