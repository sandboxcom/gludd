"""HTTP router: language expert endpoints.

Surfaces language detection, translation, and transliteration over HTTP::

  - POST /api/language/detect      -- detect language of text
  - POST /api/language/translate   -- translate text between languages
  - POST /api/language/transliterate -- transliterate text between scripts
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DetectRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100000)


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    source: str = Field(default="auto", max_length=10)
    target: str = Field(default="en", max_length=10)


class TransliterateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)
    target_script: str = Field(default="Latin", max_length=20)
    scheme: str | None = Field(default=None, max_length=50)


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    @app.post("/api/language/detect")
    async def language_detect(req: DetectRequest) -> dict[str, object]:
        from general_ludd.language.detection import detect_language

        result = detect_language(req.text)
        return dict(result)

    @app.post("/api/language/translate")
    async def language_translate(req: TranslateRequest) -> dict[str, object]:
        from general_ludd.language.translation import translate

        result = translate(req.text, req.source, req.target)
        return dict(result)

    @app.post("/api/language/transliterate")
    async def language_transliterate(req: TransliterateRequest) -> dict[str, object]:
        from general_ludd.language.transliteration import transliterate

        result = transliterate(req.text, req.target_script, req.scheme)
        return dict(result)

    logger.info("Language expert router registered")
