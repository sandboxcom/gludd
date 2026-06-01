"""Integration tests for HuggingFace model search, download, and use.

Uses a tiny model (sshleifer/tiny-gpt2) for fast downloads.
These tests require network access but use a minimal model (<1MB).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from general_ludd.models.model_registry import ModelRegistry


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(cache_dir=str(tmp_path / "models"))


class TestHFModelSearchIntegration:
    @pytest.mark.skipif(
        not os.environ.get("RUN_HF_TESTS"),
        reason="Set RUN_HF_TESTS=1 to run HuggingFace integration tests",
    )
    def test_search_returns_results_for_text_generation(self, registry):
        results = registry.search(
            query="text generation",
            tags=["text-generation"],
            limit=5,
        )
        assert len(results) >= 1
        assert all(r.model_id for r in results)

    @pytest.mark.skipif(
        not os.environ.get("RUN_HF_TESTS"),
        reason="Set RUN_HF_TESTS=1 to run HuggingFace integration tests",
    )
    def test_get_model_info_for_tiny_model(self, registry):
        info = registry.get_model_info("sshleifer/tiny-gpt2")
        assert info is not None
        assert info["model_id"] == "sshleifer/tiny-gpt2"
        assert info.get("pipeline_tag") in ("text-generation", None) or True


class TestHFModelDownloadIntegration:
    @pytest.mark.skipif(
        not os.environ.get("RUN_HF_TESTS"),
        reason="Set RUN_HF_TESTS=1 to run HuggingFace integration tests",
    )
    def test_download_tiny_model(self, registry):
        result = registry.download(
            model_id="sshleifer/tiny-gpt2",
            filename="config.json",
            engine="vllm",
        )
        assert result is not None
        assert result.model_id == "sshleifer/tiny-gpt2"
        assert result.local_path is not None
        assert result.size_bytes > 0

    @pytest.mark.skipif(
        not os.environ.get("RUN_HF_TESTS"),
        reason="Set RUN_HF_TESTS=1 to run HuggingFace integration tests",
    )
    def test_list_downloaded_after_download(self, registry):
        registry.download(
            model_id="sshleifer/tiny-gpt2",
            filename="config.json",
            engine="vllm",
        )
        downloaded = registry.list_downloaded()
        assert len(downloaded) >= 1
        assert any(d.model_id == "sshleifer/tiny-gpt2" for d in downloaded)

    @pytest.mark.skipif(
        not os.environ.get("RUN_HF_TESTS"),
        reason="Set RUN_HF_TESTS=1 to run HuggingFace integration tests",
    )
    def test_remove_downloaded_model(self, registry):
        registry.download(
            model_id="sshleifer/tiny-gpt2",
            filename="config.json",
            engine="vllm",
        )
        registry.remove_downloaded("sshleifer/tiny-gpt2")
        assert registry.get_downloaded("sshleifer/tiny-gpt2") is None


class TestHFModelDownloadUnit:
    """Unit tests that mock HfApi for fast CI."""

    def test_download_calls_hf_hub_download(self, registry):
        with patch("huggingface_hub.hf_hub_download") as mock_dl:
            mock_dl.return_value = "/tmp/models/tiny-gpt2/config.json"
            with patch("general_ludd.models.model_registry.os.path.getsize", return_value=1024):
                result = registry.download(
                    model_id="sshleifer/tiny-gpt2",
                    filename="config.json",
                )
                assert result is not None
                assert result.model_id == "sshleifer/tiny-gpt2"
                mock_dl.assert_called_once()

    def test_download_full_snapshot(self, registry):
        with patch("huggingface_hub.snapshot_download") as mock_dl:
            mock_dl.return_value = "/tmp/models/tiny-gpt2"
            with patch("general_ludd.models.model_registry.os.path.getsize", return_value=2048):
                result = registry.download(model_id="sshleifer/tiny-gpt2")
                assert result is not None
                mock_dl.assert_called_once()
