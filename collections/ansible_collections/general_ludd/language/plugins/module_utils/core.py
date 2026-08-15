"""Core language utilities imported from ``src/general_ludd/language/``.

Wraps the authoritative in-process implementations for Ansible module consumption.
"""

from __future__ import annotations

import glob as glob_mod
import sys
from pathlib import Path
from typing import Any


def _find_src_root() -> Path:
    """Walk upward from this file to the repo root (pyproject.toml marker)."""
    current = Path(__file__).resolve().parent
    for _ in range(32):
        if (current / "pyproject.toml").is_file():
            return current / "src"
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parents[3] / "src"


_SRC = _find_src_root()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from general_ludd.language.charset_map import (
    ALL_ENCODINGS,
    BOM_OPTIONAL_BY_RFC,
    BOM_REQUIRED_BY_RFC,
    BOM_SIGNATURES,
    BOM_SIZE,
    CHARDET_CONFIDENCE_THRESHOLDS,
    MOJIBAKE_SIGNATURES,
)
from general_ludd.language.cross_patterns import (
    detect_cross_language_imports,
    detect_ffi_patterns,
    detect_polyglot_builds,
    detect_script_invocations,
)
from general_ludd.language.detection import detect_language
from general_ludd.language.font_data import (
    FONT_FORMAT_SPECS,
    OPENTYPE_OPTIONAL_TABLES,
    OPENTYPE_REQUIRED_TABLES,
    VARIABLE_FONT_AXES,
    get_font_metrics,
    has_kerning,
    has_variable_axes,
    identify_font_format,
    is_web_font_format,
    list_font_tables,
)
from general_ludd.language.homoglyph_data import (
    ATTACK_VECTORS,
    HOMOGLYPH_GROUPS,
    INVISIBLE_CHARACTERS,
    detect_bidi_overrides,
    detect_confusables,
    detect_invisible_chars,
    detect_mixed_script,
    is_suspicious,
)
from general_ludd.language.i18n_data import (
    extract_icu_placeholders,
    find_untranslated_strings,
    parse_po,
    pseudolocalize,
    serialize_po,
)
from general_ludd.language.locale_data import (
    CLDR_FIRST_DAY_OF_WEEK,
    CLDR_MEASUREMENT_SYSTEMS,
    COMMON_CURRENCIES,
    ISO_639_1_TO_NAME,
    LOCALE_FORMATS,
    RTL_LANGUAGES,
    format_currency,
    format_number,
    parse_bcp47,
)
from general_ludd.language.phonetic_data import (
    ARPABET_STRESS,
    ARPABET_TO_IPA,
    CMU_DICT_SUBSET,
    DOUBLE_METAPHONE,
    IPA_CONSONANTS,
    IPA_VOWELS,
    METAPHONE_EXCEPTIONS,
    SOUNDEX_IGNORE,
    SOUNDEX_MAPPING,
    SOUNDEX_VOWELS,
    compute_double_metaphone,
    compute_metaphone,
    compute_soundex,
    transcribe_to_arpabet,
    transcribe_to_ipa,
)
from general_ludd.language.translation import translate
from general_ludd.language.transliteration import list_schemes, transliterate
from general_ludd.language.unicode_data import (
    UNICODE_BLOCK_NAMES,
    UNICODE_CATEGORY_NAMES,
    UNICODE_VERSION_HISTORY,
    is_high_surrogate,
    is_low_surrogate,
    plane_of,
    surrogates_to_codepoint,
)


def get_charset_map() -> dict[str, Any]:
    return {
        "all_encodings": ALL_ENCODINGS,
        "bom_signatures": BOM_SIGNATURES,
        "bom_size": BOM_SIZE,
        "bom_required": list(BOM_REQUIRED_BY_RFC),
        "bom_optional": list(BOM_OPTIONAL_BY_RFC),
        "chardet_thresholds": CHARDET_CONFIDENCE_THRESHOLDS,
        "mojibake_signatures": MOJIBAKE_SIGNATURES,
    }


def get_encoding_data() -> dict[str, Any]:
    return {
        "all_encodings": ALL_ENCODINGS,
        "chardet_thresholds": CHARDET_CONFIDENCE_THRESHOLDS,
        "mojibake_signatures": MOJIBAKE_SIGNATURES,
    }


def get_font_data() -> dict[str, Any]:
    return {
        "format_specs": FONT_FORMAT_SPECS,
        "required_tables": OPENTYPE_REQUIRED_TABLES,
        "optional_tables": OPENTYPE_OPTIONAL_TABLES,
        "variable_axes": VARIABLE_FONT_AXES,
        "identify_font_format": identify_font_format,
        "list_font_tables": list_font_tables,
        "get_font_metrics": get_font_metrics,
        "is_web_font_format": is_web_font_format,
        "has_variable_axes": has_variable_axes,
        "has_kerning": has_kerning,
    }


def get_homoglyph_data() -> dict[str, Any]:
    return {
        "attack_vectors": ATTACK_VECTORS,
        "homoglyph_groups": HOMOGLYPH_GROUPS,
        "invisible_characters": INVISIBLE_CHARACTERS,
    }


def get_i18n_data() -> dict[str, Any]:
    return {
        "pseudolocalize": pseudolocalize,
        "parse_po": parse_po,
        "serialize_po": serialize_po,
        "extract_icu_placeholders": extract_icu_placeholders,
        "find_untranslated_strings": find_untranslated_strings,
    }


def get_locale_data() -> dict[str, Any]:
    return {
        "first_day_of_week": CLDR_FIRST_DAY_OF_WEEK,
        "measurement_systems": CLDR_MEASUREMENT_SYSTEMS,
        "common_currencies": COMMON_CURRENCIES,
        "iso_639_1_to_name": ISO_639_1_TO_NAME,
        "locale_formats": LOCALE_FORMATS,
        "rtl_languages": RTL_LANGUAGES,
    }


def get_phonetic_data() -> dict[str, Any]:
    return {
        "arpabet_stress": ARPABET_STRESS,
        "arpabet_to_ipa": ARPABET_TO_IPA,
        "cmu_dict_subset": CMU_DICT_SUBSET,
        "double_metaphone": DOUBLE_METAPHONE,
        "ipa_consonants": IPA_CONSONANTS,
        "ipa_vowels": IPA_VOWELS,
        "metaphone_exceptions": METAPHONE_EXCEPTIONS,
        "soundex_ignore": SOUNDEX_IGNORE,
        "soundex_mapping": SOUNDEX_MAPPING,
        "soundex_vowels": SOUNDEX_VOWELS,
    }


def get_unicode_data() -> dict[str, Any]:
    return {
        "block_names": UNICODE_BLOCK_NAMES,
        "category_names": UNICODE_CATEGORY_NAMES,
        "version_history": UNICODE_VERSION_HISTORY,
        "plane_of": plane_of,
        "is_high_surrogate": is_high_surrogate,
        "is_low_surrogate": is_low_surrogate,
        "surrogates_to_codepoint": surrogates_to_codepoint,
    }


def _gather_files(source_dir: str, patterns: list[str]) -> list[str | Path]:
    matched: list[str | Path] = []
    for pat in patterns:
        for fpath in glob_mod.glob(f"{source_dir}/**/{pat}", recursive=True):
            matched.append(Path(fpath))
    return matched


def scan_cross_patterns(source_dir: str, patterns: list[str] | None = None) -> dict[str, Any]:
    default_patterns = patterns or ["*.py", "*.js", "*.ts", "*.rb", "*.go"]
    files = _gather_files(source_dir, default_patterns)
    return {
        "script_invocations": detect_script_invocations(files),
        "ffi_patterns": detect_ffi_patterns(files),
        "polyglot_builds": detect_polyglot_builds(source_dir),
        "cross_imports": detect_cross_language_imports(files),
    }


__all__ = [
    "compute_double_metaphone",
    "compute_metaphone",
    "compute_soundex",
    "detect_bidi_overrides",
    "detect_confusables",
    "detect_invisible_chars",
    "detect_language",
    "detect_mixed_script",
    "format_currency",
    "format_number",
    "get_charset_map",
    "get_encoding_data",
    "get_font_data",
    "get_homoglyph_data",
    "get_i18n_data",
    "get_locale_data",
    "get_phonetic_data",
    "get_unicode_data",
    "is_suspicious",
    "list_schemes",
    "parse_bcp47",
    "scan_cross_patterns",
    "transcribe_to_arpabet",
    "transcribe_to_ipa",
    "translate",
    "transliterate",
]
