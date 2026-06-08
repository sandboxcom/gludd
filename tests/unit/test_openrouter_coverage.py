from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestParseModelsResponseEdgeCases:
    def test_data_not_list_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        result = scraper._parse_models_response({"data": "not-a-list"})
        assert result == []

    def test_data_is_dict_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        result = scraper._parse_models_response({"data": {"nested": True}})
        assert result == []

    def test_non_dict_model_entries_skipped(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        data = {"data": ["string-entry", 42, None, True, {"id": "valid/model", "name": "Valid"}]}
        result = scraper._parse_models_response(data)
        assert len(result) == 1
        assert result[0]["id"] == "valid/model"

    def test_duplicate_model_ids_deduplicated(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        data = {
            "data": [
                {"id": "dup/model", "name": "First"},
                {"id": "dup/model", "name": "Second"},
                {"id": "unique/model", "name": "Unique"},
            ]
        }
        result = scraper._parse_models_response(data)
        assert len(result) == 2
        assert result[0]["id"] == "dup/model"
        assert result[0]["name"] == "First"
        assert result[1]["id"] == "unique/model"

    def test_none_pricing_handled_gracefully(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        data = {
            "data": [
                {"id": "a/b", "name": "NoPricing", "pricing": None, "top_provider": None},
            ]
        }
        result = scraper._parse_models_response(data)
        assert len(result) == 1
        assert result[0]["pricing"]["prompt"] == "0"
        assert result[0]["pricing"]["completion"] == "0"
        assert result[0]["is_moderated"] is False

    def test_empty_model_id_skipped(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        data = {
            "data": [
                {"id": "", "name": "Empty"},
                {"id": "valid/model", "name": "Valid"},
            ]
        }
        result = scraper._parse_models_response(data)
        assert len(result) == 1
        assert result[0]["id"] == "valid/model"

    def test_missing_pricing_key_uses_defaults(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        data = {"data": [{"id": "x/y", "name": "NoKeys"}]}
        result = scraper._parse_models_response(data)
        assert len(result) == 1
        assert result[0]["pricing"]["prompt"] == "0"
        assert result[0]["max_completion_tokens"] is None


class TestCachedModelsProperty:
    def test_cached_models_before_fetch_returns_none(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        assert scraper.cached_models is None

    @pytest.mark.asyncio
    async def test_cached_models_after_fetch_returns_list(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        response = {"data": [{"id": "a/b", "name": "A"}]}
        mock_client = AsyncMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = response
        mock_client.__aenter__.return_value.get.return_value = mock_resp

        scraper = OpenRouterScraper(api_key="key")
        with patch("general_ludd.models.openrouter_discovery.httpx.AsyncClient", return_value=mock_client):
            await scraper.fetch_models()
        assert scraper.cached_models is not None
        assert len(scraper.cached_models) == 1
        assert scraper.cached_models[0]["id"] == "a/b"


class TestInvalidateCache:
    def test_invalidate_clears_cache_and_timestamp(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraper._cache = [{"id": "x/y"}]
        scraper._cache_timestamp = 1234.5
        scraper.invalidate_cache()
        assert scraper._cache is None
        assert scraper._cache_timestamp == 0.0

    def test_cached_models_returns_none_after_invalidate(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper()
        scraper._cache = [{"id": "x/y"}]
        assert scraper.cached_models is not None
        scraper.invalidate_cache()
        assert scraper.cached_models is None


class TestFetchModelsNoApiKey:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        from general_ludd.models.openrouter_discovery import OpenRouterScraper

        scraper = OpenRouterScraper(api_key=None)
        result = await scraper.fetch_models()
        assert result == []
