"""Controller-side clients and routing for the language collection."""

from __future__ import annotations

from .capability_router import (
    LanguageRouter,
    RouteRequest,
)
from .core import (
    LanguageClient,
    LanguageServiceError,
    detect_language,
    translate,
    transliterate,
)
from .model_client import (
    detect_language_llm,
    translate_llm,
)

__all__ = [
    "LanguageClient",
    "LanguageRouter",
    "LanguageServiceError",
    "RouteRequest",
    "detect_language",
    "detect_language_llm",
    "translate",
    "translate_llm",
    "transliterate",
]
