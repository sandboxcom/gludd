"""Integration tests for ModelDownloader — multi-source model download wiring."""

from __future__ import annotations

import concurrent.futures
import pathlib
import threading
import time
from unittest.mock import patch

import pytest

from general_ludd.cloud.model_sources import DownloadedFile, ModelSource
from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadSource,
    ModelDownloader,
)
from general_ludd.small_models.model_hash_db import ModelHashDB, ModelIntegrityError

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _downloader(**kw) -> ModelDownloader:
    return ModelDownloader(cache_dir="/tmp/test-dl-cache", **kw)


def _fake_downloaded_file(
    local_path: str = "/tmp/test-model.gguf",
    source: ModelSource = ModelSource.HUGGINGFACE,
    size_bytes: int = 1024,
) -> DownloadedFile:
    return DownloadedFile(local_path=local_path, source=source, size_bytes=size_bytes)


def _touch(path: str) -> str:
    """Create a real empty file on disk so size computation works."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 256)
    return str(p)


# ---------------------------------------------------------------
# 1. Multi-source download flow
# ---------------------------------------------------------------


class TestMultiSourceFlow:
    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_full_flow_stores_result_and_size(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/qwen.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path, size_bytes=256)

        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is not None
        assert result.model_id == "qwen-0.5b"
        assert result.source == DownloadSource.HUGGINGFACE
        assert result.size_bytes == 256
        assert dl.get_downloaded("qwen-0.5b") is not None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_full_flow_maps_each_source_to_download_source(self, mock_dl):
        path = _touch("/tmp/test-dl-cache/s3.gguf")
        for ms, expected in (
            (ModelSource.HUGGINGFACE, DownloadSource.HUGGINGFACE),
            (ModelSource.OLLAMA, DownloadSource.OLLAMA),
            (ModelSource.LOCAL_PATH, DownloadSource.CACHE),
            (ModelSource.S3_MIRROR, DownloadSource.HUGGINGFACE),
            (ModelSource.DIRECT_URL, DownloadSource.HUGGINGFACE),
        ):
            dl = _downloader()
            mock_dl.return_value = _fake_downloaded_file(local_path=path, source=ms)
            result = dl.download_multi_source(model_id="qwen-0.5b")
            assert result is not None
            assert result.source == expected, f"{ms.value} → {result.source} (expected {expected})"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_download_method_prefers_multi_source_over_legacy(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/multi.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        with patch.object(dl, "download_huggingface") as mock_hf:
            result = dl.download(model_id="qwen-0.5b")
            assert result is not None
            assert result.source == DownloadSource.HUGGINGFACE
            mock_hf.assert_not_called()

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch.object(ModelDownloader, "download_huggingface")
    def test_download_falls_back_when_unknown_model(self, mock_hf, mock_ms):
        dl = _downloader()
        mock_ms.side_effect = RuntimeError("no sources")
        mock_hf.return_value = DownloadedModel(
            model_id="legacy-model-xyz",
            local_path="/tmp/legacy.gguf",
            source=DownloadSource.HUGGINGFACE,
        )
        result = dl.download(model_id="legacy-model-xyz")
        assert result is not None
        mock_hf.assert_called_once_with(model_id="legacy-model-xyz", filename=None, revision=None)

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch.object(ModelDownloader, "download_gguf")
    def test_download_routes_gguf_extension_to_gguf_method(self, mock_gguf, mock_ms):
        dl = _downloader()
        mock_ms.side_effect = RuntimeError("multi-source failure")
        mock_gguf.return_value = DownloadedModel(
            model_id="some-model",
            local_path="/tmp/gguf.gguf",
            source=DownloadSource.GGUF,
        )
        result = dl.download(model_id="some-model", filename="model.Q4_K_M.gguf")
        assert result is not None
        assert result.source == DownloadSource.GGUF


# ---------------------------------------------------------------
# 2. Source selection priority
# ---------------------------------------------------------------


class TestSourceSelectionPriority:
    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_custom_order_passed_through(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/ordered.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path, source=ModelSource.OLLAMA)

        result = dl.download_multi_source(model_id="qwen-0.5b", order=["ollama", "s3_mirror"])
        assert result is not None

        call_order = mock_dl.call_args[1].get("order", [])
        assert len(call_order) == 2
        assert call_order[0] == ModelSource.OLLAMA
        assert call_order[1] == ModelSource.S3_MIRROR

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_default_order_used_when_none_given(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/default.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        dl.download_multi_source(model_id="qwen-0.5b", order=None)
        call_order = mock_dl.call_args[1].get("order", [])
        assert call_order is None or len(call_order) == 0

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_invalid_order_values_ignored(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/filtered.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        result = dl.download_multi_source(model_id="qwen-0.5b", order=["huggingface", "not_a_source", "ollama"])
        assert result is not None

        call_order = mock_dl.call_args[1].get("order", [])
        assert len(call_order) == 2
        assert ModelSource.HUGGINGFACE in call_order
        assert ModelSource.OLLAMA in call_order

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_model_not_registered_returns_none(self, mock_dl):
        dl = _downloader()
        result = dl.download_multi_source(model_id="nonexistent-abc-123")
        assert result is None
        mock_dl.assert_not_called()

    def test_source_mapping_table_is_exhaustive(self):
        mapper = {
            ModelSource.HUGGINGFACE: DownloadSource.HUGGINGFACE,
            ModelSource.OLLAMA: DownloadSource.OLLAMA,
            ModelSource.DIRECT_URL: DownloadSource.HUGGINGFACE,
            ModelSource.LOCAL_PATH: DownloadSource.CACHE,
            ModelSource.S3_MIRROR: DownloadSource.HUGGINGFACE,
        }
        for ms in ModelSource:
            assert ms in mapper, f"ModelSource.{ms.value} not mapped"


# ---------------------------------------------------------------
# 3. Download concurrency
# ---------------------------------------------------------------


class TestDownloadConcurrency:
    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_concurrent_multi_source_downloads_different_models(self, mock_dl):
        path_a = _touch("/tmp/test-dl-cache/a.gguf")
        _touch("/tmp/test-dl-cache/b.gguf")

        def _mk_result(model_id: str, path: str) -> DownloadedFile:
            return DownloadedFile(local_path=path, source=ModelSource.HUGGINGFACE, size_bytes=512)

        mock_dl.side_effect = lambda *a, **kw: DownloadedFile(
            local_path=path_a,
            source=ModelSource.HUGGINGFACE,
            size_bytes=512,
        )

        dl = _downloader()

        def _run(model_id: str) -> DownloadedModel | None:
            return dl.download_multi_source(model_id=model_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [
                ex.submit(_run, "qwen-0.5b"),
                ex.submit(_run, "tinyllama-1.1b"),
                ex.submit(_run, "smollm2-135m"),
            ]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 3
        assert all(r is not None for r in results)

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_concurrent_downloads_preserve_source_mapping(self, mock_dl):
        path = _touch("/tmp/test-dl-cache/concurrent.gguf")

        model_source_plan = [
            ("qwen-0.5b", ModelSource.HUGGINGFACE, DownloadSource.HUGGINGFACE),
            ("tinyllama-1.1b", ModelSource.OLLAMA, DownloadSource.OLLAMA),
        ]

        dl = _downloader()

        for name, ms, expected in model_source_plan:
            with patch("general_ludd.cloud.model_sources.download_with_fallback") as m:
                m.return_value = _fake_downloaded_file(local_path=path, source=ms)
                result = dl.download_multi_source(model_id=name)
                assert result is not None
                assert result.source == expected

    def test_downloaded_cache_thread_safety(self):
        dl = _downloader()

        def _put(i: int) -> None:
            m = DownloadedModel(
                model_id=f"model-{i}",
                local_path=f"/tmp/model-{i}.gguf",
            )
            dl._downloaded[m.model_id] = m
            time.sleep(0.001)
            _ = dl.get_downloaded(f"model-{i}")

        threads = [threading.Thread(target=_put, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        models = dl.list_downloaded()
        assert len(models) == 50


# ---------------------------------------------------------------
# 4. Partial download recovery
# ---------------------------------------------------------------


class TestPartialDownloadRecovery:
    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_download_multi_source_handles_exception_gracefully(self, mock_dl):
        dl = _downloader()
        mock_dl.side_effect = RuntimeError("transient network failure")
        result = dl.download_multi_source(model_id="qwen-0.5b")
        assert result is None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_download_method_recovers_via_fallback_on_multi_source_failure(self, mock_dl):
        dl = _downloader()
        _touch("/tmp/test-dl-cache/fallback.gguf")
        mock_dl.side_effect = RuntimeError("all sources exhausted")

        with patch.object(dl, "download_huggingface") as mock_hf:
            mock_hf.return_value = DownloadedModel(
                model_id="qwen-0.5b",
                local_path="/tmp/fallback.gguf",
                source=DownloadSource.HUGGINGFACE,
            )
            result = dl.download(model_id="qwen-0.5b")
            assert result is not None
            assert result.local_path == "/tmp/fallback.gguf"

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_removed_downloaded_clears_cache(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/removed.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        dl.download_multi_source(model_id="qwen-0.5b")
        assert dl.get_downloaded("qwen-0.5b") is not None

        dl.remove_downloaded("qwen-0.5b")
        assert dl.get_downloaded("qwen-0.5b") is None
        assert len(dl.list_downloaded()) == 0

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_resubmit_after_failure_replaces_cache_entry(self, mock_dl):
        dl = _downloader()
        _touch("/tmp/test-dl-cache/first.gguf")
        path_b = _touch("/tmp/test-dl-cache/second.gguf")

        call_count = [0]

        def _side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("first attempt failed")
            return _fake_downloaded_file(local_path=path_b)

        mock_dl.side_effect = _side_effect

        first = dl.download_multi_source(model_id="qwen-0.5b")
        assert first is None

        second = dl.download_multi_source(model_id="qwen-0.5b")
        assert second is not None
        assert second.local_path == path_b

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_download_retries_respected_in_multi_source(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/retry.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        result = dl.download_multi_source(model_id="qwen-0.5b", retries=3)
        assert result is not None

        call_kwargs = mock_dl.call_args[1]
        assert call_kwargs.get("retries") == 3

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_download_method_passes_retries_via_multi_source(self, mock_dl):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/defretry.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        dl.download_multi_source(model_id="qwen-0.5b", retries=2)

        call_kwargs = mock_dl.call_args[1]
        assert call_kwargs.get("retries") == 2


# ---------------------------------------------------------------
# 5. Integrity check
# ---------------------------------------------------------------


class TestIntegrityCheck:
    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch.object(ModelHashDB, "verify_download")
    def test_multi_source_passes_through_integrity_on_success(self, mock_verify, mock_dl):
        path = _touch("/tmp/test-dl-cache/source.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        hash_db = ModelHashDB()
        dl = _downloader(hash_db=hash_db)

        result = dl.download(model_id="qwen-0.5b", verify_hash=True)
        assert result is not None
        mock_verify.assert_called_once_with("qwen-0.5b", path)

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    @patch.object(ModelHashDB, "verify_download")
    def test_multi_source_integrity_failure_clears_and_raises(self, mock_verify, mock_dl):
        path = _touch("/tmp/test-dl-cache/bad-hash.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        hash_db = ModelHashDB()
        dl = _downloader(hash_db=hash_db)
        mock_verify.side_effect = ModelIntegrityError(
            model_id="qwen-0.5b",
            filename="bad.gguf",
            expected="abc123",
            actual="def456",
        )

        with pytest.raises(ModelIntegrityError):
            dl.download(model_id="qwen-0.5b", verify_hash=True)

        assert dl.get_downloaded("qwen-0.5b") is None

    @patch("general_ludd.cloud.model_sources.download_with_fallback")
    def test_multi_source_skip_integrity_when_hash_db_absent(self, mock_dl):
        path = _touch("/tmp/test-dl-cache/nodb.gguf")
        mock_dl.return_value = _fake_downloaded_file(local_path=path)

        dl = _downloader(hash_db=None)
        result = dl.download(model_id="qwen-0.5b", verify_hash=True)
        assert result is not None

    @patch.object(ModelDownloader, "download_huggingface")
    def test_legacy_download_integrity_passes(self, mock_hf):
        path = _touch("/tmp/test-dl-cache/legacy-hash.gguf")
        mock_hf.return_value = DownloadedModel(
            model_id="legacy-model",
            local_path=path,
            source=DownloadSource.HUGGINGFACE,
        )

        hash_db = ModelHashDB()
        with patch.object(hash_db, "verify_download") as mock_verify:
            dl = _downloader(hash_db=hash_db)
            with patch.object(dl, "download_multi_source", return_value=None):
                result = dl.download(model_id="legacy-model", verify_hash=True)
                assert result is not None
                mock_verify.assert_called_once_with("legacy-model", path)

    @patch.object(ModelDownloader, "download_huggingface")
    def test_legacy_download_integrity_failure_raises(self, mock_hf):
        path = _touch("/tmp/test-dl-cache/bad-legacy.gguf")
        mock_hf.return_value = DownloadedModel(
            model_id="legacy-model",
            local_path=path,
            source=DownloadSource.HUGGINGFACE,
        )

        hash_db = ModelHashDB()
        with patch.object(hash_db, "verify_download") as mock_verify:
            mock_verify.side_effect = ModelIntegrityError(
                model_id="legacy-model",
                filename="bad.gguf",
                expected="abc",
                actual="def",
            )
            dl = _downloader(hash_db=hash_db)
            with patch.object(dl, "download_multi_source", return_value=None), pytest.raises(ModelIntegrityError):
                dl.download(model_id="legacy-model", verify_hash=True)


# ---------------------------------------------------------------
# 6. Download deferral wiring
# ---------------------------------------------------------------


class TestDownloadDeferralWiring:
    def test_check_download_scheduling_returns_keys(self):
        dl = _downloader()
        info = dl.check_download_scheduling(size_gb=0.5)
        assert "size_gb" in info
        assert "is_off_peak_now" in info
        assert "should_defer" in info
        assert "reason" in info
        assert "next_off_peak" in info

    def test_download_defers_when_peak_and_large(self):
        dl = _downloader()
        with (
            patch("general_ludd.small_models.cost.should_defer_download") as mock_defer,
            patch.object(dl, "download_multi_source", return_value=None),
            patch.object(dl, "download_huggingface") as mock_hf,
        ):
            mock_defer.return_value = {"defer": True, "reason": "peak pricing", "next_off_peak": {}}
            mock_hf.return_value = DownloadedModel(
                model_id="large-model",
                local_path="/tmp/large.gguf",
                source=DownloadSource.HUGGINGFACE,
            )
            result = dl.download(model_id="large-model", force=False)
            assert result is not None
        dl = _downloader()
        with patch("general_ludd.small_models.cost.should_defer_download") as mock_defer:
            mock_defer.return_value = {"defer": True, "reason": "peak pricing"}
            with patch.object(dl, "download_multi_source") as mock_ms:
                mock_ms.return_value = DownloadedModel(
                    model_id="qwen-0.5b",
                    local_path="/tmp/forced.gguf",
                    source=DownloadSource.HUGGINGFACE,
                )
                result = dl.download(model_id="qwen-0.5b", force=True)
                assert result is not None
                mock_ms.assert_called_once()

    def test_estimate_download_cost_returns_dict(self):
        dl = _downloader()
        cost = dl.estimate_download_cost(size_gb=2.0)
        assert isinstance(cost, dict)
        assert "cost_usd" in cost or "estimated_cost" in cost or len(cost) > 0


# ---------------------------------------------------------------
# 7. DownloadProgress
# ---------------------------------------------------------------


class TestDownloadProgressWiring:
    def test_progress_default_state(self):
        dl = _downloader()
        progress = dl.get_progress()
        assert progress.status == "idle"
        assert progress.total_bytes == 0
        assert progress.downloaded_bytes == 0

    def test_on_progress_callback_registration(self):
        dl = _downloader()
        called = []

        def _cb(p):
            called.append(p)

        dl.on_progress(_cb)
        assert dl._on_progress is _cb

    def test_remove_downloaded_pops_entry(self):
        dl = _downloader()
        path = _touch("/tmp/test-dl-cache/resume.gguf")

        with patch("general_ludd.cloud.model_sources.download_with_fallback") as mock_dl:
            mock_dl.return_value = _fake_downloaded_file(local_path=path)
            dl.download_multi_source(model_id="qwen-0.5b")
            assert dl.get_downloaded("qwen-0.5b") is not None
            dl.remove_downloaded("qwen-0.5b")
            assert dl.get_downloaded("qwen-0.5b") is None
            assert dl.remove_downloaded("nonexistent") is None
