"""Pydantic contracts for language detection, translation, and transliteration.

Provides validated input/output shapes for the language expert collection's
detection, translation, and transliteration operations. Follows the same
pattern as ``travel/plugins/module_utils/contracts.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class Direction(StrEnum):
    ltr = "ltr"
    rtl = "rtl"
    ttb = "ttb"


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------


class LanguageDetectionResult(BaseModel):
    """Result of detecting a language from a text sample."""

    model_config = ConfigDict(extra="forbid")

    language_code: str = Field(min_length=2)
    confidence: float = Field(ge=0.0, le=1.0)
    script: str | None = None
    region: str | None = None
    encoding: str | None = None
    detection_method: str | None = None


class DetectionBatchRequest(BaseModel):
    """Request to detect languages for multiple text samples."""

    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1)


class DetectionBatchResult(BaseModel):
    """Result of a batch language detection request."""

    model_config = ConfigDict(extra="forbid")

    request: DetectionBatchRequest
    results: list[LanguageDetectionResult]


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TranslationRequest(BaseModel):
    """Request to translate text from one language to another."""

    model_config = ConfigDict(extra="forbid")

    source_lang: str = Field(min_length=2)
    target_lang: str = Field(min_length=2)
    text: str = Field(min_length=1)
    formality: str = "neutral"


class TranslationResult(BaseModel):
    """Result of a translation operation."""

    model_config = ConfigDict(extra="forbid")

    translated_text: str
    source_lang: str
    target_lang: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TranslationBatchRequest(BaseModel):
    """Request to translate multiple texts in the same language pair."""

    model_config = ConfigDict(extra="forbid")

    source_lang: str = Field(min_length=2)
    target_lang: str = Field(min_length=2)
    texts: list[str] = Field(min_length=1)


class TranslationBatchResult(BaseModel):
    """Result of a batch translation request."""

    model_config = ConfigDict(extra="forbid")

    request: TranslationBatchRequest
    results: list[TranslationResult]


# ---------------------------------------------------------------------------
# Transliteration
# ---------------------------------------------------------------------------


class TransliterationRequest(BaseModel):
    """Request to transliterate text from one script to another."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    target_script: str = Field(min_length=3)
    source_script: str | None = None
    scheme: str | None = None


class TransliterationResult(BaseModel):
    """Result of a transliteration operation."""

    model_config = ConfigDict(extra="forbid")

    transliterated_text: str
    source_script: str
    target_script: str
    scheme: str | None = None


class TransliterationBatchRequest(BaseModel):
    """Request to transliterate multiple texts to the same target script."""

    model_config = ConfigDict(extra="forbid")

    target_script: str = Field(min_length=3)
    texts: list[str] = Field(min_length=1)
    source_script: str | None = None
    scheme: str | None = None


class TransliterationBatchResult(BaseModel):
    """Result of a batch transliteration request."""

    model_config = ConfigDict(extra="forbid")

    request: TransliterationBatchRequest
    results: list[TransliterationResult]


# ---------------------------------------------------------------------------
# Script Detection
# ---------------------------------------------------------------------------


class ScriptDetectionResult(BaseModel):
    """Result of detecting a writing script from text."""

    model_config = ConfigDict(extra="forbid")

    script_code: str = Field(min_length=3)
    script_name: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Language Profile
# ---------------------------------------------------------------------------


class LanguageProfile(BaseModel):
    """Metadata about a language: codes, scripts, direction, names."""

    model_config = ConfigDict(extra="forbid")

    iso_639_1: str = Field(min_length=2, max_length=2)
    iso_639_2: str = Field(min_length=3, max_length=3)
    iso_639_3: str | None = None
    name_en: str = Field(min_length=1)
    native_name: str = Field(min_length=1)
    scripts: list[str] = Field(default_factory=list)
    direction: Direction = Direction.ltr


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    "SCHEMA_VERSION",
    "DetectionBatchRequest",
    "DetectionBatchResult",
    "Direction",
    "LanguageDetectionResult",
    "LanguageProfile",
    "ScriptDetectionResult",
    "TranslationBatchRequest",
    "TranslationBatchResult",
    "TranslationRequest",
    "TranslationResult",
    "TransliterationBatchRequest",
    "TransliterationBatchResult",
    "TransliterationRequest",
    "TransliterationResult",
]
