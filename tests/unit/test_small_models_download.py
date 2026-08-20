"""Unit tests for ModelDownloader — multi-source download integration."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.cloud.model_sources import DownloadedFile, ModelSource
from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadSource,
    ModelDownloader,
)


class TestDownloadMultiSource:
    def _downloader(self) -> ModelDownloader:
        return ModelDownloader(cache_dir="/tmp/test-cache")

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_returns_result_when_model_in_registry(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
            size_bytes=1024,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        assert result.model_id == "qwen-0.5b"
        assert result.local_path == "/tmp/model.gguf"
        assert result.source == DownloadSource.HUGGINGFACE
        assert result.size_bytes == 1024

    def test_returns_none_for_unknown_model(self):
        dl = self._downloader()
        result = dl.download_multi_source(model_id="nonexistent-model-999")
        assert result is None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_propagates_filename_to_config(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
            size_bytes=2048,
        )
        result = dl.download_multi_source(
            model_id="qwen-0.5b",
            filename="custom.gguf",
        )
        assert result is not None
        assert result.filename == "custom.gguf"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_converts_string_order_to_enum(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.OLLAMA,
        )
        result = dl.download_multi_source(
            model_id="qwen-0.5b",
            order=["ollama", "huggingface"],
        )
        assert result is not None
        assert result.source == DownloadSource.OLLAMA

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_ignores_unknown_order_values(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
        )
        result = dl.download_multi_source(
            model_id="qwen-0.5b",
            order=["huggingface", "bogus_source", "ollama"],
        )
        assert result is not None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_returns_none_on_exception(self, mock_dl):
        dl = self._downloader()
        mock_dl.side_effect = RuntimeError("network down")
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_stores_result_in_downloaded(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        stored = dl.get_downloaded("qwen-0.5b")
        assert stored is not None
        assert stored.local_path == "/tmp/model.gguf"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_maps_ollama_source_correctly(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/ollama.gguf",
            source=ModelSource.OLLAMA,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        assert result.source == DownloadSource.OLLAMA

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_maps_local_path_source_to_cache(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/local.gguf",
            source=ModelSource.LOCAL_PATH,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        assert result.source == DownloadSource.CACHE


class TestDownloadFallback:
    def _downloader(self) -> ModelDownloader:
        return ModelDownloader(cache_dir="/tmp/test-cache")

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch("general_ludd.small_models.download.ModelDownloader.download_huggingface")
    def test_falls_back_to_legacy_when_unknown_model(self, mock_hf, mock_ms):
        dl = self._downloader()
        mock_ms.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
        )
        mock_hf.return_value = DownloadedModel(
            model_id="some-legacy-model",
            local_path="/tmp/legacy",
            source=DownloadSource.HUGGINGFACE,
        )

        result = dl.download(model_id="some-legacy-model")
        assert result is not None
        mock_hf.assert_called_once()

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_uses_multi_source_when_model_in_registry(self, mock_ms):
        dl = self._downloader()
        mock_ms.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
            size_bytes=4096,
        )

        result = dl.download(model_id="qwen-0.5b")
        assert result is not None
        assert result.source == DownloadSource.HUGGINGFACE

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_passes_order_to_multi_source(self, mock_ms):
        dl = self._downloader()
        mock_ms.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.OLLAMA,
        )
        result = dl.download(model_id="qwen-0.5b", order=["ollama"])
        assert result is not None
        assert result.source == DownloadSource.OLLAMA

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_return_none_on_multi_source_failure_falls_back(self, mock_ms):
        dl = self._downloader()
        mock_ms.side_effect = RuntimeError("multi-source failure")

        with patch.object(dl, "download_huggingface") as mock_hf:
            mock_hf.return_value = DownloadedModel(
                model_id="qwen-0.5b",
                local_path="/tmp/fallback",
                source=DownloadSource.HUGGINGFACE,
            )
            result = dl.download(model_id="qwen-0.5b")
            assert result is not None
            assert result.local_path == "/tmp/fallback"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch.object(ModelDownloader, "download_gguf")
    def test_falls_back_to_gguf_for_gguf_filenames(self, mock_gguf, mock_ms):
        dl = self._downloader()
        mock_ms.side_effect = RuntimeError("multi-source failure")
        mock_gguf.return_value = DownloadedModel(
            model_id="some-model",
            local_path="/tmp/model.gguf",
            source=DownloadSource.GGUF,
        )
        result = dl.download(
            model_id="some-model",
            filename="model.Q4_K_M.gguf",
        )
        assert result is not None
        assert result.source == DownloadSource.GGUF


class TestDownloadPeakScheduling:
    def test_unknown_size_skips_peak_scheduling(self) -> None:
        dl = ModelDownloader(cache_dir="/tmp/test-cache")
        downloaded = DownloadedModel(model_id="org/unknown", local_path="/tmp/unknown")

        with (
            patch("general_ludd.small_models.cost.should_defer_download") as mock_defer,
            patch.object(dl, "download_multi_source", return_value=None),
            patch.object(dl, "download_huggingface", return_value=downloaded),
        ):
            result = dl.download("org/unknown", verify_hash=False)

        assert result is downloaded
        mock_defer.assert_not_called()

    def test_known_size_checks_peak_scheduling(self) -> None:
        dl = ModelDownloader(cache_dir="/tmp/test-cache")
        downloaded = DownloadedModel(model_id="org/known", local_path="/tmp/known")

        with (
            patch(
                "general_ludd.small_models.cost.should_defer_download",
                return_value={"defer": False},
            ) as mock_defer,
            patch.object(dl, "download_multi_source", return_value=None),
            patch.object(dl, "download_huggingface", return_value=downloaded),
        ):
            result = dl.download(
                "org/known",
                verify_hash=False,
                estimated_size_gb=2.5,
            )

        assert result is downloaded
        mock_defer.assert_called_once_with(2.5, threshold_gb=1.0)


class TestDownloadMultiSourceEdgeCases:
    def _downloader(self) -> ModelDownloader:
        return ModelDownloader(cache_dir="/tmp/test-cache")

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_s3_mirror_maps_to_huggingface_source(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/s3.gguf",
            source=ModelSource.S3_MIRROR,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b", order=["s3_mirror"])
        assert result is not None
        assert result.source == DownloadSource.HUGGINGFACE

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_direct_url_maps_to_huggingface_source(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/direct.gguf",
            source=ModelSource.DIRECT_URL,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b", order=["direct_url"])
        assert result is not None
        assert result.source == DownloadSource.HUGGINGFACE

    def test_model_not_in_local_configs_returns_none(self):
        dl = self._downloader()
        result = dl.download_multi_source(model_id="model-not-in-any-registry-xyz")
        assert result is None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_caches_downloaded_model(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
        )
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        assert len(dl.list_downloaded()) == 1
        assert dl.list_downloaded()[0].model_id == "qwen-0.5b"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_remove_cached_model_clears_entry(self, mock_dl):
        dl = self._downloader()
        mock_dl.return_value = DownloadedFile(
            local_path="/tmp/model.gguf",
            source=ModelSource.HUGGINGFACE,
        )
        dl.download_multi_source(model_id="qwen-0.5b")
        dl.remove_downloaded("qwen-0.5b")
        assert dl.get_downloaded("qwen-0.5b") is None


class TestDownloadSourceMapping:
    def test_all_model_source_values_mapped(self):
        mapper = {
            ModelSource.HUGGINGFACE: DownloadSource.HUGGINGFACE,
            ModelSource.OLLAMA: DownloadSource.OLLAMA,
            ModelSource.DIRECT_URL: DownloadSource.HUGGINGFACE,
            ModelSource.LOCAL_PATH: DownloadSource.CACHE,
            ModelSource.S3_MIRROR: DownloadSource.HUGGINGFACE,
        }
        for ms in ModelSource:
            assert ms in mapper, f"ModelSource.{ms.value} not mapped to DownloadSource"
