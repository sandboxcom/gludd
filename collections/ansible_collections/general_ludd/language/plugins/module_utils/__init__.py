"""Language collection module_utils — language detection, translation, transliteration.

Imports core implementations from ``src/general_ludd/language/`` and wraps them
with LLM-based fallbacks via :mod:`model_client`. Uses :mod:`capability_router`
from ``general_ludd.dispatch`` for capability-based dispatch.
"""

from __future__ import annotations

from .capability_router import (
    LanguageRouter,
    RouteRequest,
)
from .core import (
    detect_language,
    get_charset_map,
    get_encoding_data,
    get_font_data,
    get_homoglyph_data,
    get_i18n_data,
    get_locale_data,
    get_phonetic_data,
    get_unicode_data,
    scan_cross_patterns,
    translate,
    transliterate,
)
from .model_client import (
    detect_language_llm,
    translate_llm,
)

__all__ = [
    "LanguageRouter",
    "RouteRequest",
    "detect_language",
    "detect_language_llm",
    "get_charset_map",
    "get_encoding_data",
    "get_font_data",
    "get_homoglyph_data",
    "get_i18n_data",
    "get_locale_data",
    "get_phonetic_data",
    "get_unicode_data",
    "scan_cross_patterns",
    "translate",
    "translate_llm",
    "transliterate",
]
