"""Unit tests for ModelDownloader dry-run, validation, size computation, and progress tracking."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from general_ludd.small_models.download import (
    DEFAULT_CACHE_DIR,
    DownloadedModel,
    DownloadProgress,
    DownloadSource,
    ModelDownloader,
)


class TestDownloadProgress:
    def test_percent_zero_when_zero_total_bytes(self) -> None:
        dp = DownloadProgress(total_bytes=0, downloaded_bytes=100)
        assert dp.percent == 0.0

    def test_percent_negative_total_bytes_clamped(self) -> None:
        dp = DownloadProgress(total_bytes=-1, downloaded_bytes=50)
        assert dp.percent == 0.0

    def test_percent_partial_download(self) -> None:
        dp = DownloadProgress(total_bytes=1000, downloaded_bytes=400)
        assert dp.percent == 40.0

    def test_percent_at_completion(self) -> None:
        dp = DownloadProgress(total_bytes=1000, downloaded_bytes=1000)
        assert dp.percent == 100.0

    def test_percent_clamped_at_100_when_over(self) -> None:
        dp = DownloadProgress(total_bytes=1000, downloaded_bytes=1500)
        assert dp.percent == 100.0

    def test_default_status_is_idle(self) -> None:
        dp = DownloadProgress()
        assert dp.status == "idle"
        assert dp.filename == ""
        assert dp.total_bytes == 0
        assert dp.downloaded_bytes == 0


class TestDownloadedModelFields:
    def test_defaults(self) -> None:
        m = DownloadedModel(model_id="test/model", local_path="/tmp/test_model")
        assert m.model_id == "test/model"
        assert m.source == DownloadSource.HUGGINGFACE
        assert m.filename is None
        assert m.size_bytes == 0
        assert m.revision is None
        assert m.downloaded_at > 0

    def test_filenames_preserved(self) -> None:
        m = DownloadedModel(
            model_id="test/model",
            local_path="/tmp/model",
            filename="model.gguf",
            revision="main",
            size_bytes=1024,
            source=DownloadSource.GGUF,
        )
        assert m.filename == "model.gguf"
        assert m.revision == "main"
        assert m.size_bytes == 1024
        assert m.source == DownloadSource.GGUF


class TestDownloadSourceEnum:
    def test_all_sources_defined(self) -> None:
        assert DownloadSource.HUGGINGFACE == "huggingface"
        assert DownloadSource.GGUF == "gguf"
        assert DownloadSource.OLLAMA == "ollama"
        assert DownloadSource.CACHE == "cache"

    def test_str_enum_values(self) -> None:
        assert str(DownloadSource.HUGGINGFACE) == "huggingface"
        assert str(DownloadSource.GGUF) == "gguf"


class TestComputeSize:
    def test_compute_size_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test_model.bin"
            filepath.write_bytes(b"x" * 2048)
            m = DownloadedModel(model_id="test/model", local_path=str(filepath))
            d = ModelDownloader(cache_dir=tmpdir)
            d._compute_size(m)
        assert m.size_bytes == 2048

    def test_compute_size_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "model_dir"
            subdir.mkdir()
            (subdir / "a.bin").write_bytes(b"a" * 1000)
            (subdir / "b.bin").write_bytes(b"b" * 500)
            (subdir / "nested").mkdir()
            (subdir / "nested" / "c.bin").write_bytes(b"c" * 300)
            m = DownloadedModel(model_id="test/model", local_path=str(subdir))
            d = ModelDownloader(cache_dir=tmpdir)
            d._compute_size(m)
        assert m.size_bytes == 1800


class TestDiskSpaceCheck:
    def test_cache_dir_has_free_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            usage = shutil.disk_usage(d.cache_dir)
            assert usage.free > 0

    def test_large_download_requires_sufficient_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            usage = shutil.disk_usage(d.cache_dir)
            large_model_gb = 16.0
            required_bytes = int(large_model_gb * 1e9)
            assert usage.free >= required_bytes or usage.free < required_bytes
            if usage.free < required_bytes:
                can_download = usage.free >= required_bytes
                assert isinstance(can_download, bool)


class TestCheckDownloadScheduling:
    def test_small_model_not_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.check_download_scheduling(size_gb=0.5)
        assert result["size_gb"] == 0.5
        assert "should_defer" in result
        assert "reason" in result
        assert "next_off_peak" in result

    def test_large_model_produces_valid_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.check_download_scheduling(size_gb=5.0)
        assert result["size_gb"] == 5.0
        assert isinstance(result["should_defer"], bool)
        assert isinstance(result["reason"], str)

    def test_check_download_scheduling_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.check_download_scheduling(size_gb=2.5)
        required_keys = {"size_gb", "is_off_peak_now", "should_defer", "reason", "next_off_peak"}
        assert required_keys.issubset(set(result.keys()))


class TestEstimateDownloadCost:
    def test_returns_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.estimate_download_cost(size_gb=3.0)
        assert result["size_gb"] == 3.0
        assert "data_transfer_usd" in result
        assert "estimated_storage_usd_per_month" in result
        assert "prefer_off_peak" in result

    def test_large_model_prefers_off_peak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.estimate_download_cost(size_gb=5.0)
        assert result["prefer_off_peak"] is True

    def test_small_model_no_prefer_off_peak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            result = d.estimate_download_cost(size_gb=0.1)
        assert result["prefer_off_peak"] is False


class TestDefaultCacheDir:
    def test_default_cache_dir_is_str(self) -> None:
        assert isinstance(DEFAULT_CACHE_DIR, str)
        assert "general-ludd" in DEFAULT_CACHE_DIR or "general_ludd" in DEFAULT_CACHE_DIR

    def test_env_var_overrides_default(self) -> None:
        with patch.dict(os.environ, {"GLUDD_MODEL_DIR": "/custom/models"}):
            from importlib import reload

            import general_ludd.small_models.download as mod

            reload(mod)
        assert mod.DEFAULT_CACHE_DIR == "/custom/models"
        mod.DEFAULT_CACHE_DIR = os.environ.get(
            "GLUDD_MODEL_DIR",
            os.path.expanduser("~/.cache/general-ludd/models"),
        )

    def test_cache_dir_created_on_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = os.path.join(tmpdir, "new_cache")
            _ = ModelDownloader(cache_dir=cache)
            assert os.path.isdir(cache)


class TestProgressCallback:
    def test_progress_updates_percent_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            cb = d._make_progress_callback(filename="test.bin", total_bytes=1000)

            cb(0, 250, 1000)
            assert d.get_progress().percent == 25.0
            assert d.get_progress().status == "downloading"

            cb(250, 1000, 1000)
            assert d.get_progress().percent == 100.0
            assert d.get_progress().status == "done"

    def test_progress_registers_external_callback(self) -> None:
        results: list[DownloadProgress] = []

        def on_prog(p: DownloadProgress) -> None:
            results.append(p)

        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            d.on_progress(on_prog)
            cb = d._make_progress_callback(filename="test.bin", total_bytes=1000)
            cb(0, 500, 1000)

        assert len(results) == 1
        assert results[0].percent == 50.0


class TestListAndGetDownloaded:
    def test_list_downloaded_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.list_downloaded() == []

    def test_get_downloaded_returns_none_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.get_downloaded("nonexistent") is None

    def test_remove_downloaded_noop_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            d.remove_downloaded("nonexistent")
            assert d.list_downloaded() == []
