"""Phase E TDD tests: locale formatting helpers.

The ``gludd language`` CLI was moved to
collections/ansible_collections/general_ludd/language/.
Core retains locale_data formatting helpers.
"""

from __future__ import annotations


class TestFormatNumber:
    """format_number() formats a number per locale conventions."""

    def test_us_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number

        assert format_number(1234567.89, "en-US") == "1,234,567.89"

    def test_german_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number

        assert format_number(1234567.89, "de-DE") == "1.234.567,89"

    def test_japanese_no_grouping(self) -> None:
        from general_ludd.language.locale_data import format_number

        result = format_number(1234567, "ja-JP")
        assert "1" in result and "234" in result

    def test_unknown_locale_fallback(self) -> None:
        from general_ludd.language.locale_data import format_number

        result = format_number(1000, "xx-XX")
        assert "1000" in result


class TestFormatCurrency:
    """format_currency() formats an amount with currency symbol per locale."""

    def test_usd_us(self) -> None:
        from general_ludd.language.locale_data import format_currency

        result = format_currency(99.50, "USD", "en-US")
        assert "$" in result
        assert "99" in result

    def test_eur_de(self) -> None:
        from general_ludd.language.locale_data import format_currency

        result = format_currency(50.00, "EUR", "de-DE")
        assert "50" in result

    def test_unknown_currency(self) -> None:
        from general_ludd.language.locale_data import format_currency

        result = format_currency(10, "XYZ", "en-US")
        assert "10" in result
