"""Pydantic contracts for the language collection.

Provides validated input/output shapes for detection, translation,
transliteration, homoglyph scanning, and phonetic transcription.

Follows the same pattern as ``travel/plugins/module_utils/contracts.py``.
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
    model_config = ConfigDict(extra="forbid")

    language_code: str = Field(min_length=2)
    confidence: float = Field(ge=0.0, le=1.0)
    script: str | None = None
    region: str | None = None
    encoding: str | None = None
    detection_method: str | None = None


class DetectionBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1)


class DetectionBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: DetectionBatchRequest
    results: list[LanguageDetectionResult]


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_lang: str = Field(min_length=2)
    target_lang: str = Field(min_length=2)
    text: str = Field(min_length=1)
    formality: str = "neutral"


class TranslationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    translated_text: str
    source_lang: str
    target_lang: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TranslationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_lang: str = Field(min_length=2)
    target_lang: str = Field(min_length=2)
    texts: list[str] = Field(min_length=1)


class TranslationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TranslationBatchRequest
    results: list[TranslationResult]


# ---------------------------------------------------------------------------
# Transliteration
# ---------------------------------------------------------------------------


class TransliterationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    target_script: str = Field(min_length=3)
    source_script: str | None = None
    scheme: str | None = None


class TransliterationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transliterated_text: str
    source_script: str
    target_script: str
    scheme: str | None = None


class TransliterationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_script: str = Field(min_length=3)
    texts: list[str] = Field(min_length=1)
    source_script: str | None = None
    scheme: str | None = None


class TransliterationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TransliterationBatchRequest
    results: list[TransliterationResult]


# ---------------------------------------------------------------------------
# Script Detection
# ---------------------------------------------------------------------------


class ScriptDetectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_code: str = Field(min_length=3)
    script_name: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Homoglyph Scanning
# ---------------------------------------------------------------------------


class HomoglyphMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    character: str = Field(min_length=1)
    homoglyph_of: str = Field(min_length=1)
    confusable_for: str = Field(min_length=1)


class HomoglyphScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_text: str = Field(min_length=1)
    matches: list[HomoglyphMatch] = Field(default_factory=list)
    has_confusables: bool = False
    risk_level: str = "none"


# ---------------------------------------------------------------------------
# Phonetic Transcription
# ---------------------------------------------------------------------------


class PhoneticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_text: str = Field(min_length=1)
    phonetic_text: str
    scheme: str = "IPA"
    language_code: str | None = None
    segments: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Language Profile
# ---------------------------------------------------------------------------


class LanguageProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso_639_1: str = Field(min_length=2, max_length=2)
    iso_639_2: str = Field(min_length=3, max_length=3)
    iso_639_3: str | None = None
    name_en: str = Field(min_length=1)
    native_name: str = Field(min_length=1)
    scripts: list[str] = Field(default_factory=list)
    direction: Direction = Direction.ltr


# ---------------------------------------------------------------------------
# Font Analysis
# ---------------------------------------------------------------------------


class FontGlyphCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_path: str = Field(min_length=1)
    total_glyphs: int = Field(ge=0)
    covered_scripts: list[str] = Field(default_factory=list)
    missing_scripts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    "SCHEMA_VERSION",
    "DetectionBatchRequest",
    "DetectionBatchResult",
    "Direction",
    "FontGlyphCoverage",
    "HomoglyphMatch",
    "HomoglyphScanResult",
    "LanguageDetectionResult",
    "LanguageProfile",
    "PhoneticResult",
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
