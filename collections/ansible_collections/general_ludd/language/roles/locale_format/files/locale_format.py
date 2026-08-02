#!/usr/bin/env python3
"""locale_format — Format dates, numbers, and currency per locale using CLDR data.

Produces JSON artifact with: formatted_value, locale, language_name, is_rtl,
number/date/currency format info, first_day_of_week, measurement_system.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def _add_src_to_path():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "..", "..", "..", "..", "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _format_date(dt: datetime.date, pattern: str) -> str:
    formatted = pattern
    formatted = formatted.replace("EEEE", WEEKDAYS[dt.weekday()])
    formatted = formatted.replace("MMMM", MONTHS[dt.month - 1])
    formatted = formatted.replace("MMM", MONTHS[dt.month - 1][:3])
    formatted = formatted.replace("yyyy", f"{dt.year:04d}")
    formatted = formatted.replace("dd", f"{dt.day:02d}")
    formatted = formatted.replace("d", str(dt.day))
    formatted = formatted.replace("MM", f"{dt.month:02d}")
    formatted = formatted.replace("M", str(dt.month))
    formatted = formatted.replace("y", str(dt.year))
    return formatted


def _format_number(val: float, nf: dict[str, str]) -> str:
    formatted = f"{val:,.2f}"
    formatted = (
        formatted.replace(",", "X")
        .replace(".", nf["decimal_separator"])
        .replace("X", nf["grouping_separator"])
    )
    return formatted


def _format_currency(amount: float, cf: dict[str, object]) -> str:
    dec = int(cf.get("decimal_digits", 2))
    dec_sep = str(cf.get("decimal_separator", "."))
    grp_sep = str(cf.get("grouping_separator", ","))
    formatted_amount = (
        f"{amount:,.{dec}f}".replace(",", "X")
        .replace(".", dec_sep)
        .replace("X", grp_sep)
    )
    placement = str(cf.get("placement", "before"))
    symbol = str(cf.get("symbol", ""))
    if placement == "before":
        return f"{symbol}{formatted_amount}"
    return f"{formatted_amount} {symbol}"


def format_locale(args: argparse.Namespace) -> dict[str, object]:
    _add_src_to_path()
    from general_ludd.language.locale_data import (  # type: ignore[import-not-at-top-of-file]
        CLDR_FIRST_DAY_OF_WEEK,
        CLDR_MEASUREMENT_SYSTEMS,
        COMMON_CURRENCIES,
        ISO_639_1_TO_NAME,
        LOCALE_FORMATS,
        RTL_LANGUAGES,
    )

    locale = args.locale
    value = args.value
    value_type = args.value_type

    result: dict[str, object] = {"locale": locale}

    lang_tag = locale.split("-")
    language_code = lang_tag[0] if lang_tag else ""
    territory = lang_tag[1] if len(lang_tag) > 1 else ""

    result["language_name"] = ISO_639_1_TO_NAME.get(language_code, "Unknown")
    result["script"] = ""
    result["territory"] = territory
    result["is_rtl"] = language_code in RTL_LANGUAGES

    locale_data = LOCALE_FORMATS.get(locale)
    locale_data_alt = LOCALE_FORMATS.get(locale.replace("-", "_"))

    ld = locale_data or locale_data_alt

    if ld:
        result["number_format"] = ld.get("number_format", {})
        result["date_format"] = ld.get("date_format", {})
        result["currency_format"] = ld.get("currency_format", {})
        result["plural_rules"] = {
            k: v for k, v in ld.get("plural_rules", {}).items() if v
        }

        if value_type == "date":
            try:
                dt = datetime.date.fromisoformat(value)
                df = ld.get("date_format", {})
                pat = df.get(args.date_length, df.get("medium", "yyyy-MM-dd"))
                result["formatted_value"] = _format_date(dt, str(pat))
            except ValueError:
                result["formatted_value"] = value
                result["format_error"] = "Could not parse date value"

        elif value_type == "number":
            try:
                num = float(value)
                result["formatted_value"] = _format_number(
                    num, ld.get("number_format", {})
                )
            except ValueError:
                result["formatted_value"] = value

        elif value_type == "currency":
            cf = dict(ld.get("currency_format", {}))
            if args.currency_code and args.currency_code in COMMON_CURRENCIES:
                cf = dict(COMMON_CURRENCIES[args.currency_code])
            try:
                amount = float(value)
                result["formatted_value"] = _format_currency(amount, cf)
            except ValueError:
                result["formatted_value"] = value
        else:
            result["formatted_value"] = value
    else:
        result["formatted_value"] = value
        result["warning"] = f"No CLDR data for locale {locale}; using raw value"

    result["first_day_of_week"] = CLDR_FIRST_DAY_OF_WEEK.get(territory, 1)
    result["measurement_system"] = CLDR_MEASUREMENT_SYSTEMS.get(territory, "metric")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Format values per locale using CLDR data")
    parser.add_argument("--locale", default="en-US", help="BCP 47 locale tag")
    parser.add_argument("--value", default="", help="Value to format")
    parser.add_argument("--value-type", default="date",
                        choices=["date", "number", "currency"])
    parser.add_argument("--output", default="-", help="Output JSON path (default: stdout)")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format")
    parser.add_argument("--date-length", default="medium",
                        choices=["full", "long", "medium", "short"])
    parser.add_argument("--currency-code", default="", help="Currency code override")

    args = parser.parse_args()

    try:
        result = format_locale(args)
    except Exception as exc:
        result = {"locale": args.locale, "error": str(exc)}

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output == "-":
        print(output)
    else:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output: {args.output}")

    sys.exit(0)


if __name__ == "__main__":
    main()
