"""LLM-based language detection and translation via the reworked delegating wrapper.

Delegates all HTTP transport to ``ModelClient`` from the agent's shared
module_utils. Falls back to the offline implementations when the daemon
is unreachable.
"""

from __future__ import annotations

import json
import logging

from ansible_collections.general_ludd.agent.plugins.module_utils.model_client import (
    Message,
    ModelClient,
)

logger = logging.getLogger(__name__)

_client = ModelClient("default")


def _call_llm(prompt: str, system_prompt: str = "") -> str | None:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append(Message("system", system_prompt))
    messages.append(Message("user", prompt))
    try:
        result = _client.chat(messages)
        content = result.get("content", "") or result.get("message", {}).get("content", "")
        return content if content else None
    except Exception:
        logger.debug("LLM daemon call failed, falling back to offline", exc_info=True)
        return None


def detect_language_llm(text: str, *, candidates: list[str] | None = None) -> dict[str, object]:
    """Detect language using an LLM for ambiguous or low-confidence cases.

    Falls back to the statistical stopword-based detection if the LLM call
    fails or is unavailable.

    Returns a result dict with ``language_code``, ``confidence``, ``script``,
    and ``method``. When the LLM succeeds, ``method`` is ``"llm"``; otherwise
    ``"stopword"``.
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

    llm_result = _call_llm(prompt, system_prompt=system)
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

    try:
        from general_ludd.language.detection import detect_language as _offline_detect

        offline = _offline_detect(text)
        return {
            "language_code": offline.get("iso_639_1", "und"),
            "confidence": offline.get("confidence", 0.5),
            "script": offline.get("script", ""),
            "method": "stopword",
        }
    except Exception:
        return {
            "language_code": "en",
            "confidence": 0.1,
            "script": "Latin",
            "method": "fallback",
        }


def translate_llm(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "en",
    *,
    formality: str = "neutral",
) -> dict[str, object]:
    """Translate text using an LLM for higher quality than the dictionary.

    Falls back to the offline dictionary-based translation if the LLM call
    fails or is unavailable.

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

    llm_result = _call_llm(prompt, system_prompt=system)
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

    try:
        from general_ludd.language.translation import translate as _offline_translate

        offline_result: dict[str, object] = _offline_translate(text, source_lang, target_lang)  # type: ignore[assignment]
        return offline_result
    except Exception:
        return {
            "source_language": source_lang,
            "source_text": text,
            "target_language": target_lang,
            "translated_text": text,
            "confidence": 0.0,
            "engine": "passthrough",
            "alternative": [],
            "error": "LLM and offline translation both unavailable",
        }


__all__ = [
    "detect_language_llm",
    "translate_llm",
]
