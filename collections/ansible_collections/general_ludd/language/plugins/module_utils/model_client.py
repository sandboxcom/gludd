"""LLM-based language detection and translation via the reworked delegating wrapper.

Delegates all model transport to ``ModelClient`` from the agent collection.
Deterministic fallback work crosses the same authenticated daemon boundary via
``LanguageClient``; this collection never imports Gludd's core algorithms.
"""

from __future__ import annotations

import json
import logging
from typing import cast

from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    Message,
    ModelClient,
)
from ansible_collections.general_ludd.language.plugins.module_utils.core import (
    LanguageClient,
    LanguageServiceError,
)

logger = logging.getLogger(__name__)

_client = ModelClient("default")


def _call_llm(
    prompt: str,
    system_prompt: str = "",
    *,
    client: ModelClient | None = None,
) -> str | None:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append(Message("system", system_prompt))
    messages.append(Message("user", prompt))
    try:
        result = (client or _client).chat(messages)
        message = result.get("message")
        nested_content = message.get("content", "") if isinstance(message, dict) else ""
        content = result.get("text", "") or result.get("content", "") or nested_content
        return content if content else None
    except Exception:
        logger.debug("LLM daemon call failed", exc_info=True)
        return None


def detect_language_llm(
    text: str,
    *,
    candidates: list[str] | None = None,
    model_client: ModelClient | None = None,
    language_client: LanguageClient | None = None,
) -> dict[str, object]:
    """Detect language using an LLM for ambiguous or low-confidence cases.

    Falls back through an explicitly authenticated language service client if
    the model call fails or is unavailable.

    Returns a result dict with ``language_code``, ``confidence``, ``script``,
    and ``method``. When the LLM succeeds, ``method`` is ``"llm"``; an
    authenticated fallback reports ``"service"`` and a fail-closed result
    reports ``"unavailable"``.
    """
    if not text or not text.strip():
        return {
            "language_code": "und",
            "confidence": 0.0,
            "script": "",
            "method": "none",
        }

    candidate_hint = ""
    if candidates:
        candidate_hint = f" The text is one of: {', '.join(candidates)}."

    system = (
        "You are a language detection expert. Respond ONLY with a JSON object: "
        '{"language_code": "ISO 639-1", "confidence": 0.0-1.0, "script": "script name"}. '
        "No explanation, no markdown, just the JSON."
    )
    prompt = f"Detect the language of this text (return only JSON):{candidate_hint}\n\nText: {text[:500]}"

    llm_result = _call_llm(prompt, system_prompt=system, client=model_client)
    if llm_result:
        try:
            parsed = json.loads(llm_result.strip())
            return {
                "language_code": str(parsed.get("language_code", "und")),
                "confidence": float(parsed.get("confidence", 0.5)),
                "script": str(parsed.get("script", "")),
                "method": "llm",
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    if language_client is not None:
        try:
            service_result = language_client.execute(
                "language_detect",
                {"input_text": text},
            )
            return {
                "language_code": service_result.get("iso_639_1", "und"),
                "confidence": service_result.get("confidence", 0.5),
                "script": service_result.get("script", ""),
                "method": "service",
            }
        except LanguageServiceError:
            logger.debug("language service fallback failed", exc_info=True)
    return {
        "language_code": "und",
        "confidence": 0.0,
        "script": "",
        "method": "unavailable",
    }


def translate_llm(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "en",
    *,
    formality: str = "neutral",
    model_client: ModelClient | None = None,
    language_client: LanguageClient | None = None,
) -> dict[str, object]:
    """Translate text using an LLM for higher quality than the dictionary.

    Falls back through an explicitly authenticated language service client if
    the model call fails or is unavailable.

    Returns a result dict with ``translated_text``, ``source_language``,
    ``target_language``, ``confidence``, and ``engine``.
    """
    if not text or not text.strip():
        return {
            "source_language": source_lang,
            "source_text": text,
            "target_language": target_lang,
            "translated_text": "",
            "confidence": 1.0,
            "engine": "trivial",
            "alternative": [],
            "error": "",
        }

    source_hint = f"from {source_lang} " if source_lang != "auto" else ""
    formality_hint = f" Use {formality} formality." if formality != "neutral" else ""

    system = (
        "You are a professional translator. Respond ONLY with a JSON object: "
        '{"translated_text": "string", "confidence": 0.0-1.0, "detected_source": "ISO 639-1"}. '
        "No explanation, no markdown, just the JSON."
    )
    prompt = f"Translate the following text {source_hint}to {target_lang}.{formality_hint}\n\nText: {text[:1000]}"

    llm_result = _call_llm(prompt, system_prompt=system, client=model_client)
    if llm_result:
        try:
            parsed = json.loads(llm_result.strip())
            detected = parsed.get("detected_source", source_lang)
            return {
                "source_language": source_lang if source_lang != "auto" else str(detected),
                "source_text": text,
                "target_language": target_lang,
                "translated_text": str(parsed.get("translated_text", text)),
                "confidence": float(parsed.get("confidence", 0.8)),
                "engine": "llm",
                "alternative": [],
                "error": "",
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    if language_client is not None:
        try:
            return cast(
                dict[str, object],
                language_client.execute(
                    "translate",
                    {
                        "input_text": text,
                        "source_language": source_lang,
                        "target_language": target_lang,
                    },
                ),
            )
        except LanguageServiceError:
            logger.debug("language service fallback failed", exc_info=True)
    return {
        "source_language": source_lang,
        "source_text": text,
        "target_language": target_lang,
        "translated_text": text,
        "confidence": 0.0,
        "engine": "passthrough",
        "alternative": [],
        "error": "authenticated language services unavailable",
    }


__all__ = [
    "detect_language_llm",
    "translate_llm",
]
