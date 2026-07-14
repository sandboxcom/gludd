"""Tests for SearX-based model discovery and indexing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.infra.model_search import (
    ModelIndex,
    ModelSearchResult,
    SearXModelSearch,
)


class TestModelSearchResult:
    def test_defaults(self):
        r = ModelSearchResult(name="test-model")
        assert r.name == "test-model"
        assert r.source_url == ""
        assert r.download_urls == []
        assert r.params_count == 0.0
        assert r.quantizations_available == []
        assert r.license == ""
        assert r.description == ""

    def test_full_fields(self):
        r = ModelSearchResult(
            name="llama-3-8b",
            source_url="https://huggingface.co/meta-llama/Meta-Llama-3-8B",
            download_urls=["https://huggingface.co/user/llama-3-8b.Q4_K_M.gguf"],
            params_count=8.0,
            quantizations_available=["q4_k_m", "q8_0", "fp16"],
            license="llama3",
            description="Meta Llama 3 8B",
        )
        assert r.params_count == 8.0
        assert "q4_k_m" in r.quantizations_available
        assert r.license == "llama3"


class TestSearXModelSearchExtract:
    def test_extract_model_name_from_hf_url(self):
        url = "https://huggingface.co/meta-llama/Meta-Llama-3-8B"
        name = SearXModelSearch._extract_model_name(url, "")
        assert name == "meta-llama__Meta-Llama-3-8B"

    def test_extract_model_name_from_title_slash(self):
        name = SearXModelSearch._extract_model_name("", "author/model-name")
        assert name == "author__model-name"

    def test_extract_model_name_with_dot_replacement(self):
        name = SearXModelSearch._extract_model_name(
            "", "meta-llama/Meta-Llama-3-8B text"
        )
        assert name == "meta-llama__Meta-Llama-3-8B"

    def test_extract_param_count_b_suffix(self):
        assert SearXModelSearch._extract_param_count("Llama 3 8B model with 8B params") == 8.0

    def test_extract_param_count_billion(self):
        assert SearXModelSearch._extract_param_count("70 billion parameter model") == 70.0

    def test_extract_param_count_none(self):
        assert SearXModelSearch._extract_param_count("no params here") == 0.0

    def test_extract_license_known(self):
        assert SearXModelSearch._extract_license("licensed under apache-2.0") == "apache-2.0"

    def test_extract_license_unknown(self):
        assert SearXModelSearch._extract_license("no license info") == ""

    def test_detect_quants(self):
        text = "available in q4_k_m, q8_0, and fp16 quantizations"
        quants = SearXModelSearch._detect_quants(text)
        assert "q4_k_m" in quants
        assert "q8_0" in quants
        assert "fp16" in quants

    def test_extract_download_urls(self):
        text = "Download at https://huggingface.co/user/model/resolve/main/model-q4_k_m.gguf"
        urls = SearXModelSearch._extract_download_urls(text)
        assert len(urls) == 1
        assert ".gguf" in urls[0]


class TestSearXModelSearchHTTP:
    def test_search_models_empty(self):
        searcher = SearXModelSearch(base_url="http://localhost:9999")
        results = searcher.search_models("nonexistent-model-xyz")
        assert results == []

    def test_find_model_no_results(self):
        searcher = SearXModelSearch(base_url="http://localhost:9999")
        result = searcher.find_model("nonexistent-model-xyz")
        assert result is None

    def test_search_models_with_mock_response(self):
        mock_response = {
            "query": "test",
            "results": [
                {
                    "url": "https://huggingface.co/test-org/test-model",
                    "title": "test-org/test-model · Hugging Face",
                    "content": "A 7B parameter model.",
                    "engine": "huggingface",
                }
            ],
        }
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            searcher = SearXModelSearch()
            results = searcher.search_models("test-model")
            assert len(results) == 1
            assert results[0].name == "test-org__test-model"
            assert "huggingface.co" in results[0].source_url

    def test_find_model_with_mock_response(self):
        mock_response = {
            "query": "llama",
            "results": [
                {
                    "url": "https://huggingface.co/meta-llama/Llama-3-8B",
                    "title": "meta-llama/Llama-3-8B · Hugging Face",
                    "content": "8B model q4_k_m GGUF llama3",
                }
            ],
        }
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            searcher = SearXModelSearch()
            result = searcher.find_model("Llama-3-8B")
            assert result is not None
            assert result.params_count == 8.0
            assert "q4_k_m" in result.quantizations_available
            assert result.license == "llama3"

    def test_search_models_skips_non_hf_urls(self):
        mock_response = {
            "query": "test",
            "results": [
                {
                    "url": "https://example.com/something",
                    "title": "Not HF",
                    "content": "generic content",
                },
                {
                    "url": "https://huggingface.co/org/model",
                    "title": "org/model",
                    "content": "HF model",
                },
            ],
        }
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            searcher = SearXModelSearch()
            results = searcher.search_models("test")
            assert len(results) == 1
            assert results[0].name == "org__model"


class TestModelIndex:
    def test_put_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            r = ModelSearchResult(name="test-model", params_count=7.0)
            index.put(r)
            retrieved = index.get("test-model")
            assert retrieved is not None
            assert retrieved.params_count == 7.0

    def test_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            index.put(ModelSearchResult(name="llama-3-8b", description="Meta Llama 3 8B"))
            index.put(ModelSearchResult(name="mistral-7b", description="Mistral 7B model"))
            results = index.search("llama")
            assert len(results) == 1
            assert results[0].name == "llama-3-8b"

    def test_list_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            index.put(ModelSearchResult(name="a"))
            index.put(ModelSearchResult(name="b"))
            assert index.size() == 2
            assert len(index.list_all()) == 2

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            index.put(ModelSearchResult(name="persisted-model"))
            assert index.size() == 1

            index2 = ModelIndex(cache_dir=tmpdir)
            assert index2.size() == 1
            assert index2.get("persisted-model") is not None

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            assert index.get("nonexistent") is None

    def test_search_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            index.put(ModelSearchResult(name="DeepSeek-V3", description="DeepSeek V3 model"))
            results = index.search("deepseek")
            assert len(results) == 1

    def test_corrupt_index_handled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            idx_path = Path(tmpdir) / "index.json"
            idx_path.write_text("{bad json")
            index = ModelIndex(cache_dir=tmpdir)
            assert index.size() == 0

    def test_empty_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index = ModelIndex(cache_dir=tmpdir)
            assert index.size() == 0
            assert index.list_all() == []
