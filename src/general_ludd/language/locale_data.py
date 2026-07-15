"""CLDR-derived locale data: number/date/currency formats, plural rules.

Covers:
- BCP 47 language tag parsing
- Date format patterns (full/long/medium/short) per locale
- Number format grouping and decimal separators
- Currency symbols and placement per locale
- Plural rules (cardinal: one/few/many/other; ordinal)
- RTL script map
- Locale negotiation (Accept-Language header parsing)
- CLDR supplemental: measurement systems, first day of week, timezone
"""

from __future__ import annotations

from typing import Literal, TypedDict

DateFormatLength = Literal["full", "long", "medium", "short"]
PluralCategory = Literal["zero", "one", "two", "few", "many", "other"]
CurrencyPlacement = Literal["before", "after", "before-no-space", "after-no-space"]


class NumberFormat(TypedDict):
    decimal_separator: str
    grouping_separator: str
    grouping_pattern: list[int]
    percent_sign: str
    minus_sign: str
    infinity: str
    nan: str


class DateFormat(TypedDict):
    full: str
    long: str
    medium: str
    short: str


class CurrencyFormat(TypedDict):
    symbol: str
    code: str
    placement: CurrencyPlacement
    decimal_digits: int
    decimal_separator: str
    grouping_separator: str


class LocaleData(TypedDict):
    bcp47: str
    language_name: str
    script: str
    territory: str
    is_rtl: bool
    number_format: NumberFormat
    date_format: DateFormat
    currency_format: CurrencyFormat
    plural_rules: dict[PluralCategory, str]


RTL_SCRIPTS: set[str] = {
    "Arab", "Hebr", "Syrc", "Thaa", "Samr", "Mand", "Nkoo",
    "Adlm", "Rohg",
}

RTL_LANGUAGES: set[str] = {
    "ar", "he", "fa", "ur", "ps", "sd", "ug", "yi", "dv",
}


COMMON_CURRENCIES: dict[str, CurrencyFormat] = {
    "USD": {"symbol": "$", "code": "USD", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "EUR": {"symbol": "\u20ac", "code": "EUR", "placement": "after",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "GBP": {"symbol": "\u00a3", "code": "GBP", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "JPY": {"symbol": "\u00a5", "code": "JPY", "placement": "before",
            "decimal_digits": 0, "decimal_separator": ".", "grouping_separator": ","},
    "CNY": {"symbol": "\u00a5", "code": "CNY", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "INR": {"symbol": "\u20b9", "code": "INR", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "RUB": {"symbol": "\u20bd", "code": "RUB", "placement": "after",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "KRW": {"symbol": "\u20a9", "code": "KRW", "placement": "before",
            "decimal_digits": 0, "decimal_separator": ".", "grouping_separator": ","},
    "BRL": {"symbol": "R$", "code": "BRL", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "CHF": {"symbol": "CHF", "code": "CHF", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "AUD": {"symbol": "$", "code": "AUD", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
    "CAD": {"symbol": "$", "code": "CAD", "placement": "before",
            "decimal_digits": 2, "decimal_separator": ".", "grouping_separator": ","},
}


LOCALE_FORMATS: dict[str, LocaleData] = {
    "en-US": {
        "bcp47": "en-US",
        "language_name": "English (United States)",
        "script": "Latn",
        "territory": "US",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "EEEE, MMMM d, y",
            "long": "MMMM d, y",
            "medium": "MMM d, y",
            "short": "M/d/yy",
        },
        "currency_format": COMMON_CURRENCIES["USD"],
        "plural_rules": {
            "one": "n = 1",
            "other": "n != 1",
            "zero": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "en-GB": {
        "bcp47": "en-GB",
        "language_name": "English (United Kingdom)",
        "script": "Latn",
        "territory": "GB",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "EEEE, d MMMM y",
            "long": "d MMMM y",
            "medium": "d MMM y",
            "short": "dd/MM/y",
        },
        "currency_format": COMMON_CURRENCIES["GBP"],
        "plural_rules": {
            "one": "n = 1",
            "other": "n != 1",
            "zero": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "de-DE": {
        "bcp47": "de-DE",
        "language_name": "German (Germany)",
        "script": "Latn",
        "territory": "DE",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ",",
            "grouping_separator": ".",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "EEEE, d. MMMM y",
            "long": "d. MMMM y",
            "medium": "dd.MM.y",
            "short": "dd.MM.yy",
        },
        "currency_format": COMMON_CURRENCIES["EUR"],
        "plural_rules": {
            "one": "n = 1",
            "other": "n != 1",
            "zero": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "fr-FR": {
        "bcp47": "fr-FR",
        "language_name": "French (France)",
        "script": "Latn",
        "territory": "FR",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ",",
            "grouping_separator": "\u202f",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "EEEE d MMMM y",
            "long": "d MMMM y",
            "medium": "d MMM y",
            "short": "dd/MM/y",
        },
        "currency_format": COMMON_CURRENCIES["EUR"],
        "plural_rules": {
            "one": "n in 0..1",
            "other": "n not in 0..1",
            "zero": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "ja-JP": {
        "bcp47": "ja-JP",
        "language_name": "Japanese (Japan)",
        "script": "Jpan",
        "territory": "JP",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "y\u5e74M\u6708d\u65e5EEEE",
            "long": "y\u5e74M\u6708d\u65e5",
            "medium": "y/MM/dd",
            "short": "y/MM/dd",
        },
        "currency_format": COMMON_CURRENCIES["JPY"],
        "plural_rules": {
            "other": "always",
            "zero": "",
            "one": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "zh-CN": {
        "bcp47": "zh-CN",
        "language_name": "Chinese (Simplified, China)",
        "script": "Hans",
        "territory": "CN",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "y\u5e74M\u6708d\u65e5EEEE",
            "long": "y\u5e74M\u6708d\u65e5",
            "medium": "y\u5e74M\u6708d\u65e5",
            "short": "y/M/d",
        },
        "currency_format": COMMON_CURRENCIES["CNY"],
        "plural_rules": {
            "other": "always",
            "zero": "",
            "one": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
    "ar-SA": {
        "bcp47": "ar-SA",
        "language_name": "Arabic (Saudi Arabia)",
        "script": "Arab",
        "territory": "SA",
        "is_rtl": True,
        "number_format": {
            "decimal_separator": "\u066b",
            "grouping_separator": "\u066c",
            "grouping_pattern": [3],
            "percent_sign": "\u066a",
            "minus_sign": "\u2212",
            "infinity": "\u221e",
            "nan": "\u0644\u064a\u0633\u0640\u0631\u0642\u0645",
        },
        "date_format": {
            "full": "EEEE\u060c d MMMM y",
            "long": "d MMMM y",
            "medium": "dd/MM/y",
            "short": "d/M/y",
        },
        "currency_format": {
            "symbol": "\ufdfc",
            "code": "SAR",
            "placement": "after",
            "decimal_digits": 2,
            "decimal_separator": "\u066b",
            "grouping_separator": "\u066c",
        },
        "plural_rules": {
            "zero": "n = 0",
            "one": "n = 1",
            "two": "n = 2",
            "few": "n % 100 in 3..10",
            "many": "n % 100 in 11..99",
            "other": "n % 100 in 100..102, n % 100 in 0, n % 100 > 103",
        },
    },
    "he-IL": {
        "bcp47": "he-IL",
        "language_name": "Hebrew (Israel)",
        "script": "Hebr",
        "territory": "IL",
        "is_rtl": True,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "\u2212",
            "infinity": "\u221e",
            "nan": "\u05dc\u05d0\u05be\u05de\u05e1\u05e4\u05e8",
        },
        "date_format": {
            "full": "EEEE, d \u05d1MMMM y",
            "long": "d \u05d1MMMM y",
            "medium": "d \u05d1MMM y",
            "short": "d.M.y",
        },
        "currency_format": {
            "symbol": "\u20aa",
            "code": "ILS",
            "placement": "before",
            "decimal_digits": 2,
            "decimal_separator": ".",
            "grouping_separator": ",",
        },
        "plural_rules": {
            "one": "n = 1",
            "two": "n = 2",
            "other": "n not in {1, 2}",
            "zero": "",
            "few": "",
            "many": "",
        },
    },
    "ru-RU": {
        "bcp47": "ru-RU",
        "language_name": "Russian (Russia)",
        "script": "Cyrl",
        "territory": "RU",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ",",
            "grouping_separator": "\u00a0",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "\u043d\u0435\u00a0\u0447\u0438\u0441\u043b\u043e",
        },
        "date_format": {
            "full": "EEEE, d MMMM y '\u0433.'",
            "long": "d MMMM y '\u0433.'",
            "medium": "d MMM y '\u0433.'",
            "short": "dd.MM.y",
        },
        "currency_format": COMMON_CURRENCIES["RUB"],
        "plural_rules": {
            "one": "n % 10 = 1 and n % 100 != 11",
            "few": "n % 10 in 2..4 and n % 100 not in 12..14",
            "many": "n % 10 = 0 or n % 10 in 5..9 or n % 100 in 11..14",
            "other": "otherwise",
            "zero": "",
            "two": "",
        },
    },
    "ko-KR": {
        "bcp47": "ko-KR",
        "language_name": "Korean (Korea)",
        "script": "Kore",
        "territory": "KR",
        "is_rtl": False,
        "number_format": {
            "decimal_separator": ".",
            "grouping_separator": ",",
            "grouping_pattern": [3],
            "percent_sign": "%",
            "minus_sign": "-",
            "infinity": "\u221e",
            "nan": "NaN",
        },
        "date_format": {
            "full": "y\ub144 M\uc6d4 d\uc77c EEEE",
            "long": "y\ub144 M\uc6d4 d\uc77c",
            "medium": "y. M. d.",
            "short": "yy. M. d.",
        },
        "currency_format": COMMON_CURRENCIES["KRW"],
        "plural_rules": {
            "other": "always",
            "zero": "",
            "one": "",
            "two": "",
            "few": "",
            "many": "",
        },
    },
}


ISO_639_1_TO_NAME: dict[str, str] = {
    "aa": "Afar", "ab": "Abkhazian", "af": "Afrikaans", "ak": "Akan",
    "am": "Amharic", "ar": "Arabic", "as": "Assamese", "av": "Avaric",
    "ay": "Aymara", "az": "Azerbaijani", "ba": "Bashkir", "be": "Belarusian",
    "bg": "Bulgarian", "bh": "Bihari", "bi": "Bislama", "bm": "Bambara",
    "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "ce": "Chechen", "co": "Corsican", "cs": "Czech",
    "cu": "Church Slavic", "cv": "Chuvash", "cy": "Welsh", "da": "Danish",
    "de": "German", "dv": "Divehi", "dz": "Dzongkha", "ee": "Ewe",
    "el": "Greek", "en": "English", "eo": "Esperanto", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "ff": "Fulah",
    "fi": "Finnish", "fj": "Fijian", "fo": "Faroese", "fr": "French",
    "fy": "Western Frisian", "ga": "Irish", "gd": "Scottish Gaelic",
    "gl": "Galician", "gn": "Guarani", "gu": "Gujarati", "gv": "Manx",
    "ha": "Hausa", "he": "Hebrew", "hi": "Hindi", "ho": "Hiri Motu",
    "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian",
    "hy": "Armenian", "hz": "Herero", "ia": "Interlingua", "id": "Indonesian",
    "ie": "Interlingue", "ig": "Igbo", "ii": "Sichuan Yi", "ik": "Inupiaq",
    "io": "Ido", "is": "Icelandic", "it": "Italian", "iu": "Inuktitut",
    "ja": "Japanese", "jv": "Javanese", "ka": "Georgian", "kg": "Kongo",
    "ki": "Kikuyu", "kj": "Kuanyama", "kk": "Kazakh", "kl": "Kalaallisut",
    "km": "Central Khmer", "kn": "Kannada", "ko": "Korean", "kr": "Kanuri",
    "ks": "Kashmiri", "ku": "Kurdish", "kv": "Komi", "kw": "Cornish",
    "ky": "Kirghiz", "la": "Latin", "lb": "Luxembourgish", "lg": "Ganda",
    "li": "Limburgan", "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian",
    "lu": "Luba-Katanga", "lv": "Latvian", "mg": "Malagasy", "mh": "Marshallese",
    "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian",
    "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese",
    "na": "Nauru", "nb": "Norwegian Bokmal", "nd": "North Ndebele",
    "ne": "Nepali", "ng": "Ndonga", "nl": "Dutch", "nn": "Norwegian Nynorsk",
    "no": "Norwegian", "nr": "South Ndebele", "nv": "Navajo",
    "ny": "Chichewa", "oc": "Occitan", "oj": "Ojibwa", "om": "Oromo",
    "or": "Oriya", "os": "Ossetian", "pa": "Panjabi", "pi": "Pali",
    "pl": "Polish", "ps": "Pushto", "pt": "Portuguese", "qu": "Quechua",
    "rm": "Romansh", "rn": "Rundi", "ro": "Romanian", "ru": "Russian",
    "rw": "Kinyarwanda", "sa": "Sanskrit", "sc": "Sardinian", "sd": "Sindhi",
    "se": "Northern Sami", "sg": "Sango", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "sm": "Samoan", "sn": "Shona", "so": "Somali",
    "sq": "Albanian", "sr": "Serbian", "ss": "Swati", "st": "Southern Sotho",
    "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "tg": "Tajik", "th": "Thai", "ti": "Tigrinya",
    "tk": "Turkmen", "tl": "Tagalog", "tn": "Tswana", "to": "Tonga",
    "tr": "Turkish", "ts": "Tsonga", "tt": "Tatar", "tw": "Twi",
    "ty": "Tahitian", "ug": "Uighur", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "ve": "Venda", "vi": "Vietnamese", "vo": "Volapuk",
    "wa": "Walloon", "wo": "Wolof", "xh": "Xhosa", "yi": "Yiddish",
    "yo": "Yoruba", "za": "Zhuang", "zh": "Chinese", "zu": "Zulu",
}


ISO_3166_TO_NAME: dict[str, str] = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AR": "Argentina",
    "AM": "Armenia", "AU": "Australia", "AT": "Austria", "AZ": "Azerbaijan",
    "BD": "Bangladesh", "BE": "Belgium", "BO": "Bolivia", "BA": "Bosnia",
    "BR": "Brazil", "BG": "Bulgaria", "CA": "Canada", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "HR": "Croatia", "CZ": "Czechia",
    "DK": "Denmark", "EG": "Egypt", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "GE": "Georgia", "DE": "Germany", "GR": "Greece",
    "HK": "Hong Kong", "HU": "Hungary", "IS": "Iceland", "IN": "India",
    "ID": "Indonesia", "IR": "Iran", "IQ": "Iraq", "IE": "Ireland",
    "IL": "Israel", "IT": "Italy", "JP": "Japan", "JO": "Jordan",
    "KZ": "Kazakhstan", "KR": "Korea", "KW": "Kuwait", "LV": "Latvia",
    "LB": "Lebanon", "LT": "Lithuania", "MY": "Malaysia", "MX": "Mexico",
    "MA": "Morocco", "NL": "Netherlands", "NZ": "New Zealand", "NG": "Nigeria",
    "NO": "Norway", "PK": "Pakistan", "PE": "Peru", "PH": "Philippines",
    "PL": "Poland", "PT": "Portugal", "QA": "Qatar", "RO": "Romania",
    "RU": "Russia", "SA": "Saudi Arabia", "RS": "Serbia", "SG": "Singapore",
    "SK": "Slovakia", "SI": "Slovenia", "ZA": "South Africa", "ES": "Spain",
    "SE": "Sweden", "CH": "Switzerland", "TW": "Taiwan", "TH": "Thailand",
    "TR": "Turkey", "UA": "Ukraine", "AE": "UAE", "GB": "United Kingdom",
    "US": "United States", "UZ": "Uzbekistan", "VN": "Vietnam", "YE": "Yemen",
}


ISO_15924_TO_NAME: dict[str, str] = {
    "Adlm": "Adlam", "Afak": "Afaka", "Aghb": "Caucasian Albanian",
    "Ahom": "Ahom", "Arab": "Arabic", "Aran": "Arabic (Nastaliq)",
    "Armi": "Imperial Aramaic", "Armn": "Armenian", "Avst": "Avestan",
    "Bali": "Balinese", "Bamu": "Bamum", "Bass": "Bassa Vah",
    "Batk": "Batak", "Beng": "Bengali", "Bhks": "Bhaiksuki",
    "Blis": "Blissymbols", "Bopo": "Bopomofo", "Brah": "Brahmi",
    "Brai": "Braille", "Bugi": "Buginese", "Buhd": "Buhid",
    "Cakm": "Chakma", "Cans": "Unified Canadian Aboriginal Syllabics",
    "Cari": "Carian", "Cham": "Cham", "Cher": "Cherokee",
    "Cirt": "Cirth", "Copt": "Coptic", "Cprt": "Cypriot",
    "Cyrl": "Cyrillic", "Cyrs": "Cyrillic (Old Church Slavonic)",
    "Deva": "Devanagari", "Dogr": "Dogra", "Dsrt": "Deseret",
    "Dupl": "Duployan", "Egyd": "Egyptian demotic", "Egyh": "Egyptian hieratic",
    "Egyp": "Egyptian hieroglyphs", "Elba": "Elbasan", "Elym": "Elymaic",
    "Ethi": "Ethiopic", "Geok": "Khutsuri", "Geor": "Georgian",
    "Glag": "Glagolitic", "Gong": "Gunjala Gondi", "Gonm": "Masaram Gondi",
    "Goth": "Gothic", "Gran": "Grantha", "Grek": "Greek",
    "Gujr": "Gujarati", "Guru": "Gurmukhi", "Hanb": "Han with Bopomofo",
    "Hang": "Hangul", "Hani": "Han", "Hano": "Hanunoo",
    "Hans": "Han (Simplified)", "Hant": "Han (Traditional)", "Hatr": "Hatran",
    "Hebr": "Hebrew", "Hira": "Hiragana", "Hluw": "Anatolian Hieroglyphs",
    "Hmng": "Pahawh Hmong", "Hmnp": "Nyiakeng Puachue Hmong", "Hrkt": "Japanese syllabaries",
    "Hung": "Old Hungarian", "Inds": "Indus", "Ital": "Old Italic",
    "Jamo": "Jamo", "Java": "Javanese", "Jpan": "Japanese",
    "Jurc": "Jurchen", "Kali": "Kayah Li", "Kana": "Katakana",
    "Khar": "Kharoshthi", "Khmr": "Khmer", "Khoj": "Khojki",
    "Kitl": "Khitan large script", "Kits": "Khitan small script", "Knda": "Kannada",
    "Kore": "Korean", "Kpel": "Kpelle", "Kthi": "Kaithi",
    "Lana": "Tai Tham", "Laoo": "Lao", "Latf": "Latin (Fraktur)",
    "Latg": "Latin (Gaelic)", "Latn": "Latin", "Leke": "Leke",
    "Lepc": "Lepcha", "Limb": "Limbu", "Lina": "Linear A",
    "Linb": "Linear B", "Lisu": "Lisu", "Loma": "Loma",
    "Lyci": "Lycian", "Lydi": "Lydian", "Mahj": "Mahajani",
    "Maka": "Makasar", "Mand": "Mandaic", "Mani": "Manichaean",
    "Marc": "Marchen", "Maya": "Mayan hieroglyphs", "Medf": "Medefaidrin",
    "Mend": "Mende Kikakui", "Merc": "Meroitic Cursive", "Mero": "Meroitic Hieroglyphs",
    "Mlym": "Malayalam", "Modi": "Modi", "Mong": "Mongolian",
    "Moon": "Moon", "Mroo": "Mro", "Mtei": "Meetei Mayek",
    "Mult": "Multani", "Mymr": "Myanmar", "Nand": "Nandinagari",
    "Narb": "Old North Arabian", "Nbat": "Nabataean", "Newa": "Newa",
    "Nkdb": "Naxi Dongba", "Nkgb": "Naxi Geba", "Nkoo": "NKo",
    "Nshu": "Nushu", "Ogam": "Ogham", "Olck": "Ol Chiki",
    "Orkh": "Old Turkic", "Orya": "Oriya", "Osge": "Osage",
    "Osma": "Osmanya", "Palm": "Palmyrene", "Pauc": "Pau Cin Hau",
    "Perm": "Old Permic", "Phag": "Phags-pa", "Phli": "Inscriptional Pahlavi",
    "Phlp": "Psalter Pahlavi", "Phlv": "Book Pahlavi", "Phnx": "Phoenician",
    "Plrd": "Miao", "Prti": "Inscriptional Parthian", "Qaaa": "Reserved for private use",
    "Rjng": "Rejang", "Rohg": "Hanifi Rohingya", "Roro": "Rongorongo",
    "Runr": "Runic", "Samr": "Samaritan", "Sara": "Sarati",
    "Sarb": "Old South Arabian", "Saur": "Saurashtra", "Sgnw": "SignWriting",
    "Shaw": "Shavian", "Shrd": "Sharada", "Sidd": "Siddham",
    "Sind": "Khudawadi", "Sinh": "Sinhala", "Sogd": "Sogdian",
    "Sogo": "Old Sogdian", "Sora": "Sora Sompeng", "Soyo": "Soyombo",
    "Sund": "Sundanese", "Sylo": "Syloti Nagri", "Syrc": "Syriac",
    "Syrj": "Western Syriac", "Syrn": "Eastern Syriac", "Tagb": "Tagbanwa",
    "Takr": "Takri", "Tale": "Tai Le", "Talu": "New Tai Lue",
    "Taml": "Tamil", "Tang": "Tangut", "Tavt": "Tai Viet",
    "Telu": "Telugu", "Teng": "Tengwar", "Tfng": "Tifinagh",
    "Tglg": "Tagalog", "Thaa": "Thaana", "Thai": "Thai",
    "Tibt": "Tibetan", "Tirh": "Tirhuta", "Toto": "Toto",
    "Ugar": "Ugaritic", "Vaii": "Vai", "Visp": "Visible Speech",
    "Wara": "Warang Citi", "Wcho": "Wancho", "Wole": "Woleai",
    "Xpeo": "Old Persian", "Xsux": "Sumero-Akkadian", "Yezi": "Yezidi",
    "Yiii": "Yi", "Zanb": "Zanabazar Square", "Zinh": "Inherited",
    "Zmth": "Mathematical notation", "Zsye": "Emoji", "Zsym": "Symbols",
    "Zxxx": "Unwritten", "Zyyy": "Undetermined", "Zzzz": "Uncoded",
}


CLDR_FIRST_DAY_OF_WEEK: dict[str, int] = {
    "US": 0, "GB": 0, "DE": 1, "FR": 1, "JP": 1, "CN": 1,
    "KR": 1, "RU": 1, "IL": 1, "SA": 5, "IN": 1, "BR": 1,
    "AU": 0, "CA": 1, "MX": 1, "TR": 1, "GR": 1, "NL": 1,
    "BE": 1, "CH": 1, "AT": 1, "PL": 1, "CZ": 1, "SK": 1,
    "HU": 1, "RO": 1, "BG": 1, "SE": 1, "NO": 1, "DK": 1,
    "FI": 1, "EE": 1, "LV": 1, "LT": 1, "IS": 1, "IE": 1,
    "PT": 0, "ES": 1, "IT": 1, "HR": 1, "SI": 1, "RS": 1,
    "UA": 1, "GE": 1, "AM": 1, "AZ": 1, "KZ": 1, "UZ": 1,
    "TH": 1, "VN": 1, "ID": 1, "MY": 1, "PH": 1, "SG": 1,
    "NZ": 0, "ZA": 1, "NG": 1, "EG": 5, "MA": 5, "IQ": 5,
    "JO": 5, "KW": 5, "LB": 1, "QA": 5, "AE": 5, "YE": 5,
    "PK": 1, "BD": 1, "TW": 1, "HK": 1, "CL": 1, "AR": 1,
    "CO": 1, "PE": 1, "BO": 1,
}


CLDR_MEASUREMENT_SYSTEMS: dict[str, str] = {
    "US": "US", "GB": "UK", "DE": "metric", "FR": "metric",
    "JP": "metric", "CN": "metric", "KR": "metric", "RU": "metric",
    "IL": "metric", "SA": "metric", "IN": "metric", "BR": "metric",
    "AU": "metric", "CA": "metric", "MX": "metric",
}


# ── Phase C: L10n functional helpers ────────────────────────────────────────


class ParsedTag(TypedDict):
    """BCP 47 language tag components (RFC 5646)."""

    language: str
    script: str
    territory: str
    codeset: str
    canonical: str


def parse_bcp47(tag: str) -> ParsedTag:
    """Parse a BCP 47 language tag per RFC 5646.

    Accepts common variant spellings: 'en-US', 'en_US', 'en_US.UTF-8',
    'zh-Hans-CN'. Returns a dict with language/script/territory/codeset
    fields plus a canonical 'language-territory' form.
    """
    if not tag:
        return {"language": "", "script": "", "territory": "",
                "codeset": "", "canonical": ""}

    cleaned = tag.strip()
    if "@" in cleaned:
        cleaned = cleaned.split("@", 1)[0]

    codeset = ""
    if "." in cleaned:
        cleaned, codeset = cleaned.split(".", 1)
        codeset = codeset.strip()

    cleaned = cleaned.replace("_", "-")
    parts = [p for p in cleaned.split("-") if p]

    language = parts[0] if parts else ""
    script = ""
    territory = ""

    if len(parts) >= 2:
        second = parts[1]
        if len(second) == 4 and second[0].isupper():
            script = second
        elif (len(second) == 2 and second.isupper()) or (len(second) == 3 and second.isdigit()):
            territory = second

    if len(parts) >= 3 and not territory:
        third = parts[2]
        if (len(third) == 2 and third.isupper()) or (len(third) == 3 and third.isdigit()):
            territory = third

    canonical_parts = [language]
    if script:
        canonical_parts.append(script)
    if territory:
        canonical_parts.append(territory)
    canonical = "-".join(canonical_parts)

    return {"language": language, "script": script, "territory": territory,
            "codeset": codeset, "canonical": canonical}


def get_locale_data(tag: str) -> LocaleData | None:
    """Look up CLDR locale data by tag with fallback.

    - Exact match on canonical 'xx-YY' form.
    - Underscore variants normalized to hyphen.
    - Language-only fallback: 'en-GB' falls back to first 'en-*' entry.
    """
    if not tag:
        return None

    parsed = parse_bcp47(tag)
    canonical = parsed["canonical"]

    if canonical in LOCALE_FORMATS:
        return LOCALE_FORMATS[canonical]

    normalized = tag.replace("_", "-")
    if normalized in LOCALE_FORMATS:
        return LOCALE_FORMATS[normalized]

    language = parsed["language"]
    if language:
        for key, value in LOCALE_FORMATS.items():
            if key.startswith(language + "-"):
                return value

    return None


def negotiate_locale(
    accept_language: str,
    available: list[str],
    default: str | None = None,
) -> str | None:
    """RFC 4647 Lookup locale negotiation.

    Parses an HTTP Accept-Language header, sorts by q-value, and returns
    the best match from `available`. Falls back to language-prefix match
    (e.g. 'en-GB' -> 'en-US'). Returns `default` (or None) if no match.
    """
    if not accept_language or not available:
        return default

    ranges: list[tuple[str, float]] = []
    for raw in accept_language.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ";" in raw:
            tag, params = raw.split(";", 1)
            tag = tag.strip()
            q = 1.0
            for param in params.split(";"):
                param = param.strip()
                if param.startswith("q="):
                    try:
                        q = float(param[2:])
                    except ValueError:
                        q = 0.0
        else:
            tag = raw
            q = 1.0
        if q <= 0.0:
            continue
        ranges.append((tag, q))

    ranges.sort(key=lambda r: r[1], reverse=True)

    available_normalized = {a.replace("_", "-"): a for a in available}
    available_languages: dict[str, str] = {}
    for canon, orig in available_normalized.items():
        lang = canon.split("-")[0]
        available_languages.setdefault(lang, orig)

    for tag, _q in ranges:
        if tag == "*":
            return available[0]

        normalized = tag.replace("_", "-")
        if normalized in available_normalized:
            return available_normalized[normalized]

        lang = normalized.split("-")[0]
        if lang in available_languages:
            return available_languages[lang]

    return default


_PLURAL_GRAMMARS: dict[str, dict[str, str]] = {
    "en": {"one": "i == 1", "other": "i != 1"},
    "de": {"one": "i == 1", "other": "i != 1"},
    "fr": {"one": "i in 0..1", "other": "i not in 0..1"},
    "es": {"one": "i == 1", "other": "i != 1"},
    "ru": {
        "one": "v == 0 and i % 10 == 1 and i % 100 != 11",
        "few": "v == 0 and i % 10 in 2..4 and i % 100 not in 12..14",
        "many": ("v == 0 and i % 10 == 0 or "
                 "v == 0 and i % 10 in 5..9 or "
                 "v == 0 and i % 100 in 11..14"),
        "other": "",
    },
    "ar": {
        "zero": "n == 0",
        "one": "n == 1",
        "two": "n == 2",
        "few": "n % 100 in 3..10",
        "many": "n % 100 in 11..99",
        "other": "",
    },
    "ja": {"other": ""},
    "zh": {"other": ""},
    "ko": {"other": ""},
}


def evaluate_plural(locale: str, n: float) -> PluralCategory:
    """Evaluate the CLDR plural category for a count `n` in a given locale.

    Implements cardinal plural rules for the locale's language. Returns
    one of: zero, one, two, few, many, other. Unknown locales default
    to 'other'.
    """
    parsed = parse_bcp47(locale)
    language = parsed["language"]

    rules = _PLURAL_GRAMMARS.get(language)
    if not rules:
        return "other"

    i = int(n)
    v = 0 if float(n).is_integer() else 1
    int_n = int(n)
    mod_10 = i % 10 if i else 0
    mod_100 = i % 100 if i else 0

    def _check(expr: str) -> bool:
        if not expr:
            return False
        if "i % 10 == 1 and i % 100 != 11" in expr:
            return v == 0 and mod_10 == 1 and mod_100 != 11
        if "i % 10 in 2..4 and i % 100 not in 12..14" in expr:
            return v == 0 and 2 <= mod_10 <= 4 and not (12 <= mod_100 <= 14)
        if "i % 10 == 0" in expr and "i % 10 in 5..9" in expr:
            return (v == 0 and mod_10 == 0) or (
                v == 0 and 5 <= mod_10 <= 9
            ) or (v == 0 and 11 <= mod_100 <= 14)
        if "i % 100 in 11..14" in expr:
            return v == 0 and 11 <= mod_100 <= 14
        if expr == "i == 1":
            return i == 1
        if expr == "i != 1":
            return i != 1
        if expr == "i in 0..1":
            return i in (0, 1)
        if expr == "i not in 0..1":
            return i not in (0, 1)
        if expr == "n == 0":
            return int_n == 0
        if expr == "n == 1":
            return int_n == 1
        if expr == "n == 2":
            return int_n == 2
        if expr == "n % 100 in 3..10":
            return 3 <= int_n % 100 <= 10
        if expr == "n % 100 in 11..99":
            return 11 <= int_n % 100 <= 99
        return False

    for category in ("zero", "one", "two", "few", "many"):
        expr = rules.get(category, "")
        if expr and _check(expr):
            return category

    return "other"
