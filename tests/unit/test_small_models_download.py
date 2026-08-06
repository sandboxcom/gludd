"""Unit tests for ModelDownloader."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import ANY, patch

from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadProgress,
    DownloadSource,
    ModelDownloader,
)


class TestDownloadedModel:
    def test_defaults(self):
        m = DownloadedModel(model_id="org/model", local_path="/tmp/model")
        assert m.model_id == "org/model"
        assert m.local_path == "/tmp/model"
        assert m.source == DownloadSource.HUGGINGFACE
        assert m.filename is None
        assert m.size_bytes == 0
        assert m.revision is None

    def test_custom_values(self):
        m = DownloadedModel(
            model_id="org/model",
            local_path="/tmp/model",
            source=DownloadSource.GGUF,
            filename="model.q4.gguf",
            size_bytes=1000000,
            revision="abc123",
        )
        assert m.source == DownloadSource.GGUF
        assert m.filename == "model.q4.gguf"
        assert m.size_bytes == 1000000
        assert m.revision == "abc123"


class TestDownloadProgress:
    def test_defaults(self):
        p = DownloadProgress()
        assert p.filename == ""
        assert p.total_bytes == 0
        assert p.downloaded_bytes == 0
        assert p.speed_bytes_per_sec == 0.0
        assert p.percent == 0.0
        assert p.status == "idle"

    def test_percent_calculation(self):
        p = DownloadProgress(total_bytes=1000, downloaded_bytes=500)
        assert p.percent == 50.0

    def test_percent_zero_total(self):
        p = DownloadProgress(total_bytes=0, downloaded_bytes=100)
        assert p.percent == 0.0


class TestModelDownloaderInit:
    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.cache_dir == tmpdir
            assert d.hf_token is None

    def test_creates_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = os.path.join(tmpdir, "models")
            ModelDownloader(cache_dir=cache)
            assert Path(cache).exists()

    def test_hf_token_from_env(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HF_TOKEN": "test-token"}):
            d = ModelDownloader(cache_dir=tmpdir)
            assert d.hf_token == "test-token"

    def test_explicit_token_overrides_env(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HF_TOKEN": "env-token"}):
            d = ModelDownloader(cache_dir=tmpdir, hf_token="explicit-token")
            assert d.hf_token == "explicit-token"


class TestModelDownloaderHuggingFace:
    def test_download_single_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.hf_hub_download") as mock_hf:
                mock_hf.return_value = os.path.join(tmpdir, "tokenizer.json")
                result = d.download_huggingface(model_id="org/model", filename="tokenizer.json", revision="v1.0")
                mock_hf.assert_called_once_with(
                    repo_id="org/model",
                    filename="tokenizer.json",
                    token="test",
                    revision="v1.0",
                    callback=mock_hf.call_args[1]["callback"],
                )
                # hf_hub_download callback is a function; verify it was passed
                assert callable(mock_hf.call_args[1]["callback"])
            assert result.model_id == "org/model"
            assert result.filename == "tokenizer.json"
            assert result.revision == "v1.0"
            assert result.source == DownloadSource.HUGGINGFACE

    def test_download_model_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.snapshot_download") as mock_snap:
                mock_snap.return_value = os.path.join(tmpdir, "org--model")
                result = d.download_huggingface(model_id="org/model")
                mock_snap.assert_called_once_with(
                    repo_id="org/model",
                    token="test",
                    revision=None,
                )
            assert result.model_id == "org/model"
            assert result.filename is None
            assert result.source == DownloadSource.HUGGINGFACE


class TestModelDownloaderGGUF:
    def test_download_gguf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.hf_hub_download") as mock_hf:
                mock_hf.return_value = os.path.join(tmpdir, "model.q4_k_m.gguf")
                result = d.download_gguf(model_id="org/model", filename="model.q4_k_m.gguf")
                mock_hf.assert_called_once()
            assert result.model_id == "org/model"
            assert result.filename == "model.q4_k_m.gguf"
            assert result.source == DownloadSource.GGUF

    def test_download_gguf_with_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.hf_hub_download") as mock_hf:
                mock_hf.return_value = os.path.join(tmpdir, "model.gguf")
                result = d.download_gguf(
                    model_id="org/model",
                    filename="model.gguf",
                    revision="v2.0",
                )
                mock_hf.assert_called_once_with(
                    repo_id="org/model",
                    filename="model.gguf",
                    token="test",
                    revision="v2.0",
                )
            assert result.revision == "v2.0"


class TestModelDownloaderOllama:
    def test_pull_equivalent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.snapshot_download") as mock_snap:
                mock_snap.return_value = os.path.join(tmpdir, "org--model")
                result = d.pull_ollama(model_id="org/model:latest", revision="v1.0")
            assert result.model_id == "org/model:latest"
            assert result.source == DownloadSource.OLLAMA
            assert result.revision == "v1.0"

    def test_pull_equivalent_strips_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.snapshot_download") as mock_snap:
                mock_snap.return_value = os.path.join(tmpdir, "model.gguf")
                result = d.pull_ollama(
                    model_id="org/model:q4_k_m",
                )
                mock_snap.assert_called_once_with(
                    repo_id="org/model",
                    token="test",
                    revision=None,
                )
            assert result.source == DownloadSource.OLLAMA


class TestModelDownloaderProgress:
    def test_progress_callback_updates_state(self):
        d = ModelDownloader(cache_dir="/tmp")
        cb = d._make_progress_callback("test.gguf", 5000)
        cb(2500, 5000)
        progress = d.get_progress()
        assert progress.filename == "test.gguf"
        assert progress.downloaded_bytes == 2500
        assert progress.total_bytes == 5000
        assert progress.percent == 50.0
        assert progress.status == "downloading"

    def test_progress_callback_resets_on_new_file(self):
        d = ModelDownloader(cache_dir="/tmp")
        cb1 = d._make_progress_callback("a.gguf", 3000)
        cb1(1500, 3000)
        cb2 = d._make_progress_callback("b.gguf", 2000)
        cb2(500, 2000)
        progress = d.get_progress()
        assert progress.filename == "b.gguf"
        assert progress.total_bytes == 2000
        assert progress.downloaded_bytes == 500

    def test_progress_last_bytes_reset_only_when_restart(self):
        d = ModelDownloader(cache_dir="/tmp")
        cb = d._make_progress_callback("test.gguf", 10000)
        cb(3000, 10000)
        cb(7000, 10000)
        progress = d.get_progress()
        assert progress.downloaded_bytes == 7000

    def test_progress_status_done(self):
        d = ModelDownloader(cache_dir="/tmp")
        cb = d._make_progress_callback("test.gguf", 5000)
        cb(5000, 5000)
        progress = d.get_progress()
        assert progress.percent == 100.0


class TestModelDownloaderListRemove:
    def test_list_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m = DownloadedModel(model_id="org/model", local_path="/tmp/model")
            d._downloaded[m.model_id] = m
            assert len(d.list_downloaded()) == 1

    def test_get_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m = DownloadedModel(model_id="org/model", local_path="/tmp/model")
            d._downloaded[m.model_id] = m
            assert d.get_downloaded("org/model") is m
            assert d.get_downloaded("nonexistent") is None

    def test_remove_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m = DownloadedModel(model_id="org/model", local_path="/tmp/model")
            d._downloaded[m.model_id] = m
            d.remove_downloaded("org/model")
            assert d.get_downloaded("org/model") is None

    def test_remove_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            d.remove_downloaded("nonexistent")


class TestModelDownloaderConvenience:
    def test_download_auto_gguf_by_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.hf_hub_download") as mock_hf:
                mock_hf.return_value = os.path.join(tmpdir, "model.gguf")
                result = d.download(model_id="org/model", filename="model.gguf")
            assert result.source == DownloadSource.GGUF

    def test_download_auto_huggingface(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir, hf_token="test")
            with patch("huggingface_hub.snapshot_download") as mock_snap:
                mock_snap.return_value = os.path.join(tmpdir, "org--model")
                result = d.download(model_id="org/model")
            assert result.source == DownloadSource.HUGGINGFACE
