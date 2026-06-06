"""OpenRouter model discovery — scrapes free models from OpenRouter API."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class OpenRouterScraper:
    """Fetches and parses the list of free models available via OpenRouter."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0.0

    async def fetch_models(self) -> list[dict[str, Any]]:
        """Fetch the list of models from OpenRouter API. Returns empty on failure."""
        if self._api_key is None:
            logger.debug("No OpenRouter API key configured, skipping model fetch")
            return []

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/anomalyco/general-ludd-agent",
            "X-Title": "General Ludd Agent",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(OPENROUTER_MODELS_URL, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    models = self._parse_models_response(data)
                    self._cache = models
                    self._cache_timestamp = time.time()
                    logger.info("Fetched %d models from OpenRouter", len(models))
                    return models
                else:
                    logger.warning(
                        "OpenRouter models fetch failed: HTTP %d — %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return []
        except Exception as exc:
            logger.warning("OpenRouter models fetch error: %s", exc)
            return []

    def _parse_models_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse the OpenRouter API response into a list of model dicts."""
        raw_models = data.get("data", [])
        if not isinstance(raw_models, list):
            return []

        models: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for m in raw_models:
            if not isinstance(m, dict):
                continue
            model_id = m.get("id", "")
            if not model_id or model_id in seen_ids:
                continue
            seen_ids.add(model_id)

            pricing = m.get("pricing", {}) or {}
            top_provider = m.get("top_provider", {}) or {}

            models.append({
                "id": model_id,
                "name": m.get("name", model_id),
                "description": m.get("description", ""),
                "context_length": m.get("context_length", 8192),
                "pricing": {
                    "prompt": str(pricing.get("prompt", "0")),
                    "completion": str(pricing.get("completion", "0")),
                },
                "is_moderated": bool(top_provider.get("is_moderated", False)),
                "max_completion_tokens": top_provider.get("max_completion_tokens"),
                "created": m.get("created", 0),
            })
        return models

    @property
    def cached_models(self) -> list[dict[str, Any]] | None:
        return self._cache

    def invalidate_cache(self) -> None:
        self._cache = None
        self._cache_timestamp = 0.0
