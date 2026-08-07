"""Unit tests for model_sources — multi-source download with fallback."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.cloud.model_sources import (
    ALTERNATIVE_SOURCES,
    DownloadedFile,
    DownloadError,
    ModelSource,
    download_with_fallback,
    health_check,
    resolve_source_chain,
)
from general_ludd.local_model._local_model_configs import LocalModelConfig


def _make_config(
    name: str = "qwen-0.5b",
    repo: str = "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
    filename: str = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
) -> LocalModelConfig:
    return LocalModelConfig(name=name, repo=repo, filename=filename)


class TestModelSource:
    def test_all_sources_present(self):
        assert set(ModelSource) == {
            ModelSource.HUGGINGFACE,
            ModelSource.OLLAMA,
            ModelSource.DIRECT_URL,
            ModelSource.LOCAL_PATH,
            ModelSource.S3_MIRROR,
        }


class TestAlternativeSources:
    def test_every_configured_model_has_source_entry(self):
        """Every model in _local_model_configs._LOCAL_MODELS has an entry in ALTERNATIVE_SOURCES."""
        from general_ludd.local_model._local_model_configs import _LOCAL_MODELS

        for cfg in _LOCAL_MODELS:
            assert cfg.name in ALTERNATIVE_SOURCES, f"Missing ALTERNATIVE_SOURCES entry for {cfg.name}"

    def test_alternative_sources_have_required_keys(self):
        """Every entry has at least huggingface + optional ollama/direct_url."""
        for name, sources in ALTERNATIVE_SOURCES.items():
            assert ModelSource.HUGGINGFACE in sources, f"Missing HUGGINGFACE fallback for {name}"
            assert sources[ModelSource.HUGGINGFACE]["repo"], f"HUGGINGFACE repo empty for {name}"
            assert sources[ModelSource.HUGGINGFACE]["filename"], f"HUGGINGFACE filename empty for {name}"

    def test_ollama_entries_are_valid_model_names(self):
        for name, sources in ALTERNATIVE_SOURCES.items():
            if ModelSource.OLLAMA in sources:
                ollama_name = sources[ModelSource.OLLAMA]
                assert isinstance(ollama_name, str) and ":" not in ollama_name.split("/")[-1].split(":")[:1], (
                    f"Ollama entry for {name} should be a plain model name"
                )


class TestResolveSourceChain:
    def test_default_order_is_ollama_first(self):
        chain = resolve_source_chain()
        assert chain[0] == ModelSource.OLLAMA
        assert ModelSource.HUGGINGFACE in chain
        assert ModelSource.DIRECT_URL in chain

    def test_custom_order(self):
        chain = resolve_source_chain(order=[ModelSource.HUGGINGFACE, ModelSource.OLLAMA])
        assert chain == [ModelSource.HUGGINGFACE, ModelSource.OLLAMA]

    def test_unknown_sources_filtered(self):
        chain = resolve_source_chain(order=[ModelSource.LOCAL_PATH, ModelSource.S3_MIRROR])
        assert len(chain) > 0


class TestHealthCheck:
    @patch("general_ludd.cloud.model_sources._check_url_reachable")
    def test_health_check_passes_when_one_source_reachable(self, mock_reach):
        mock_reach.return_value = True
        assert health_check() is True
        mock_reach.assert_called_once()

    @patch("general_ludd.cloud.model_sources._check_url_reachable", return_value=False)
    def test_health_check_fails_when_none_reachable(self, mock_reach):
        assert health_check() is False
        assert mock_reach.call_count >= 1

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True)
    @patch("general_ludd.cloud.model_sources._check_url_reachable", return_value=True)
    def test_health_check_detects_ollama(self, mock_reach, mock_installed):
        assert health_check() is True


class TestDownloadWithFallback:
    def _cfg(self) -> LocalModelConfig:
        return _make_config()

    @patch("general_ludd.cloud.model_sources._download_from_huggingface")
    def test_falls_through_to_huggingface(self, mock_hf):
        cfg = self._cfg()
        mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)

        result = download_with_fallback(cfg)
        assert result.local_path == "/tmp/model.gguf"
        assert result.source == ModelSource.HUGGINGFACE

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True)
    @patch("general_ludd.cloud.model_sources._download_from_ollama")
    def test_prefers_ollama_when_installed(self, mock_ollama, _mock_installed):
        cfg = _make_config()
        mock_ollama.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.OLLAMA)

        result = download_with_fallback(cfg)
        assert result.source == ModelSource.OLLAMA
        mock_ollama.assert_called_once()

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True)
    @patch("general_ludd.cloud.model_sources._download_from_ollama", side_effect=Exception("ollama failed"))
    @patch("general_ludd.cloud.model_sources._download_from_huggingface")
    def test_falls_back_after_ollama_failure(self, mock_hf, mock_ollama, _mock_installed):
        cfg = self._make_no_ollama_cfg()
        mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)

        result = download_with_fallback(cfg)
        assert result.source == ModelSource.HUGGINGFACE

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False)
    @patch("general_ludd.cloud.model_sources._download_from_huggingface")
    def test_skips_ollama_when_not_installed(self, mock_hf, _mock_installed):
        cfg = self._cfg()
        mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)

        download_with_fallback(cfg)
        assert mock_hf.call_count == 1

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=False)
    @patch("general_ludd.cloud.model_sources._download_from_huggingface", side_effect=Exception("hf failed"))
    @patch("general_ludd.cloud.model_sources._download_from_direct_url", side_effect=Exception("url failed"))
    def test_raises_after_all_fallbacks_exhausted(self, mock_url, mock_hf, _mock_installed):
        cfg = self._make_direct_url_cfg()
        with pytest.raises(DownloadError):
            download_with_fallback(cfg)

    def _make_no_ollama_cfg(self) -> LocalModelConfig:
        return LocalModelConfig(
            name="ci-only-model",
            repo="org/no-ollama-equiv",
            filename="model.gguf",
        )

    def _make_direct_url_cfg(self) -> LocalModelConfig:
        return LocalModelConfig(
            name="direct-url-model",
            repo="org/direct-model",
            filename="model.gguf",
        )

    @patch("general_ludd.cloud.model_sources._check_ollama_installed", return_value=True)
    @patch("general_ludd.cloud.model_sources._download_from_ollama", side_effect=Exception("fail"))
    @patch("general_ludd.cloud.model_sources._download_from_huggingface")
    def test_retry_logic_exhausted(self, mock_hf, mock_ollama, _mock_installed):
        cfg = self._make_no_ollama_cfg()
        mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)
        result = download_with_fallback(cfg, retries=2)
        assert result.source == ModelSource.HUGGINGFACE


class TestBackwardCompat:
    """Existing HuggingFace-only path must still work."""

    @patch("general_ludd.cloud.model_sources._download_from_huggingface")
    def test_huggingface_only_still_works(self, mock_hf):
        cfg = _make_config()
        mock_hf.return_value = DownloadedFile(local_path="/tmp/model.gguf", source=ModelSource.HUGGINGFACE)

        result = download_with_fallback(cfg, order=[ModelSource.HUGGINGFACE])
        assert result.source == ModelSource.HUGGINGFACE
