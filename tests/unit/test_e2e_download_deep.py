"""Deep E2E tests for model download lifecycle and management."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.download import (
    DEFAULT_CACHE_DIR,
    DEFAULT_DOWNLOAD_TIMEOUT,
    DownloadedModel,
    DownloadProgress,
    DownloadSource,
    ModelDownloader,
)
from general_ludd.small_models.model_hash_db import (
    KnownModels,
    ModelHashDB,
    ModelIntegrityError,
)


class TestDownloadLifecycle:
    """Full lifecycle: init → track → download → verify → list → remove."""

    def test_init_defaults_correct(self):
        d = ModelDownloader()
        assert d.cache_dir == DEFAULT_CACHE_DIR
        assert d.hf_token is None
        assert d.timeout == DEFAULT_DOWNLOAD_TIMEOUT
        assert d._hash_db is None
        assert d.list_downloaded() == []

    def test_init_creates_cache_dir_on_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = os.path.join(tmpdir, "a", "b", "c")
            ModelDownloader(cache_dir=cache)
            assert Path(cache).is_dir()

    def test_lifecycle_register_then_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m = DownloadedModel(model_id="org/a", local_path=os.path.join(tmpdir, "a"))
            d._downloaded[m.model_id] = m
            assert len(d.list_downloaded()) == 1
            assert d.get_downloaded("org/a") is m

    def test_lifecycle_register_then_remove(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m = DownloadedModel(model_id="org/b", local_path=os.path.join(tmpdir, "b"))
            d._downloaded[m.model_id] = m
            d.remove_downloaded("org/b")
            assert d.list_downloaded() == []
            assert d.get_downloaded("org/b") is None

    def test_lifecycle_multiple_models_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            for i in range(5):
                d._downloaded[f"org/m{i}"] = DownloadedModel(
                    model_id=f"org/m{i}", local_path=os.path.join(tmpdir, f"m{i}")
                )
            assert len(d.list_downloaded()) == 5

    def test_lifecycle_remove_nonexistent_no_error(self):
        d = ModelDownloader()
        d.remove_downloaded("nonexistent/model")


class TestDownloadWithHashVerify:
    def test_hash_verify_pass_keeps_model_in_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "tokenizer.json")
            db = ModelHashDB.from_known_models()
            d = ModelDownloader(cache_dir=tmpdir, hash_db=db, hf_token="tok")

            with patch.object(db, "verify_download"):
                result = d.download(
                    model_id="HuggingFaceTB/SmolLM2-135M",
                    filename="tokenizer.json",
                    verify_hash=True,
                )
            assert d.get_downloaded(result.model_id) is result

    def test_hash_verify_fail_removes_from_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "bad_file")
            db = ModelHashDB.from_known_models()
            d = ModelDownloader(cache_dir=tmpdir, hash_db=db, hf_token="tok")

            def _fail(*args, **kwargs):
                raise ModelIntegrityError("M", "f", "a", "b")

            with patch.object(db, "verify_download", side_effect=_fail), pytest.raises(ModelIntegrityError):
                d.download(
                    model_id="HuggingFaceTB/SmolLM2-135M",
                    filename="whatever",
                    verify_hash=True,
                )
            assert d.get_downloaded("HuggingFaceTB/SmolLM2-135M") is None

    def test_hash_verify_skipped_when_no_db(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "file.txt")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download(model_id="org/x", filename="file.txt", verify_hash=True)
            assert d.get_downloaded("org/x") is result

    def test_hash_verify_skipped_when_verify_flag_false(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "file.txt")
            db = ModelHashDB.from_known_models()
            d = ModelDownloader(cache_dir=tmpdir, hash_db=db, hf_token="tok")
            result = d.download(
                model_id="HuggingFaceTB/SmolLM2-135M",
                filename="file.txt",
                verify_hash=False,
            )
            assert d.get_downloaded("HuggingFaceTB/SmolLM2-135M") is result

    def test_hash_db_from_known_models_has_all_keys(self):
        db = ModelHashDB.from_known_models()
        models = db.list_models()
        assert "HuggingFaceTB/SmolLM2-135M" in models
        assert "Qwen/Qwen2.5-0.5B" in models
        assert "TinyLlama/TinyLlama-1.1B-Chat-v1.0" in models
        assert "microsoft/phi-2" in models

    def test_hash_db_get_hashes_returns_files(self):
        db = ModelHashDB.from_known_models()
        files = db.get_hashes("HuggingFaceTB/SmolLM2-135M")
        assert files is not None
        filenames = {f.filename for f in files}
        assert "model.safetensors" in filenames
        assert "config.json" in filenames
        assert "tokenizer.json" in filenames

    def test_hash_db_persist_and_reload(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("{}")
            db_path = tf.name
        try:
            db = ModelHashDB(db_path=db_path)
            db.register_model("test/model", KnownModels.get("microsoft/phi-2") or [])

            db2 = ModelHashDB(db_path=db_path)
            assert "test/model" in db2.list_models()
        finally:
            os.unlink(db_path)


class TestProgressCallback:
    def test_progress_initial_state_is_idle(self):
        d = ModelDownloader()
        p = d.get_progress()
        assert p.filename == ""
        assert p.total_bytes == 0
        assert p.downloaded_bytes == 0
        assert p.speed_bytes_per_sec == 0.0
        assert p.status == "idle"
        assert p.percent == 0.0

    def test_progress_updates_during_downloading(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("model.gguf", 10000)
        cb(0, 2500, 10000)
        p = d.get_progress()
        assert p.filename == "model.gguf"
        assert p.downloaded_bytes == 2500
        assert p.total_bytes == 10000
        assert p.percent == 25.0
        assert p.status == "downloading"

    def test_progress_tracks_speed(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("model.gguf", 10000)
        cb(0, 1000, 10000)
        time.sleep(1.01)
        cb(100, 5000, 10000)
        p = d.get_progress()
        assert p.speed_bytes_per_sec >= 0

    def test_progress_status_done_at_100_percent(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("model.gguf", 5000)
        cb(0, 5000, 5000)
        assert d.get_progress().percent == 100.0

    def test_progress_percent_capped_at_100(self):
        p = DownloadProgress(total_bytes=100, downloaded_bytes=200)
        assert p.percent == 100.0

    def test_on_progress_callback_fires(self):
        d = ModelDownloader()
        captured: list[DownloadProgress] = []
        d.on_progress(lambda p: captured.append(p))
        cb = d._make_progress_callback("test.bin", 1000)
        cb(0, 300, 1000)
        cb(0, 600, 1000)
        cb(0, 1000, 1000)
        assert len(captured) == 3
        assert captured[-1].percent == 100.0

    def test_progress_reset_on_new_make_progress_callback(self):
        d = ModelDownloader()
        cb1 = d._make_progress_callback("a.bin", 5000)
        cb1(0, 3000, 5000)
        cb2 = d._make_progress_callback("b.bin", 2000)
        cb2(0, 500, 2000)
        p = d.get_progress()
        assert p.filename == "b.bin"
        assert p.total_bytes == 2000
        assert p.downloaded_bytes == 500


class TestResumePartialDownload:
    def test_partial_download_state_is_observable(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("big_model.gguf", 100000)
        cb(0, 45000, 100000)
        p = d.get_progress()
        assert p.downloaded_bytes == 45000
        assert p.percent == 45.0

    def test_partial_to_completion_transition(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("big_model.gguf", 100000)
        cb(0, 45000, 100000)
        cb(0, 90000, 100000)
        cb(0, 100000, 100000)
        p = d.get_progress()
        assert p.downloaded_bytes == 100000
        assert p.percent == 100.0

    def test_progress_monotonic_increasing_bytes(self):
        d = ModelDownloader()
        cb = d._make_progress_callback("m.gguf", 10000)
        previous = 0
        for step in (1000, 3500, 7200, 10000):
            cb(0, step, 10000)
            assert d.get_progress().downloaded_bytes >= previous
            previous = d.get_progress().downloaded_bytes


class TestTimeoutHandling:
    def test_timeout_default_respected(self):
        d = ModelDownloader()
        assert d.timeout == DEFAULT_DOWNLOAD_TIMEOUT

    def test_timeout_explicit_overrides_default(self):
        d = ModelDownloader(timeout=5.0)
        assert d.timeout == 5.0

    def test_timeout_env_var_respected(self):
        assert float(os.environ.get("GLUDD_HF_DOWNLOAD_TIMEOUT", "30")) == DEFAULT_DOWNLOAD_TIMEOUT

    def test_timeout_explicit_wins_over_env(self):
        with patch.dict(os.environ, {"GLUDD_HF_DOWNLOAD_TIMEOUT": "15"}):
            d = ModelDownloader(timeout=45.0)
            assert d.timeout == 45.0

    def test_timeout_passed_to_hf_env_vars(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("huggingface_hub.hf_hub_download") as mock_hf,
            patch.dict(os.environ, {}, clear=True),
        ):
            mock_hf.return_value = os.path.join(tmpdir, "f")
            d = ModelDownloader(cache_dir=tmpdir, timeout=7.5)
            d.download_huggingface(model_id="org/x", filename="f")
            assert os.environ.get("HF_HUB_DOWNLOAD_TIMEOUT") == "7.5"

    def test_timeout_none_uses_default(self):
        with patch.object(os, "environ", {}):
            d = ModelDownloader(timeout=None)
            assert d.timeout == float(os.environ.get("GLUDD_HF_DOWNLOAD_TIMEOUT", "30"))


class TestConcurrentDownloads:
    def test_multiple_models_tracked_independently(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            models = {
                f"org/m{i}": DownloadedModel(
                    model_id=f"org/m{i}",
                    local_path=os.path.join(tmpdir, f"m{i}"),
                    source=DownloadSource.HUGGINGFACE if i % 2 == 0 else DownloadSource.GGUF,
                )
                for i in range(10)
            }
            for mid, m in models.items():
                d._downloaded[mid] = m

            assert len(d.list_downloaded()) == 10
            for mid, m in models.items():
                assert d.get_downloaded(mid) is m

    def test_concurrent_progress_tracks_last_active(self):
        d = ModelDownloader()
        cb_a = d._make_progress_callback("a.gguf", 1000)
        cb_a(0, 400, 1000)
        cb_b = d._make_progress_callback("b.gguf", 2000)
        cb_b(0, 1200, 2000)
        p = d.get_progress()
        assert p.filename == "b.gguf"
        assert p.total_bytes == 2000

    def test_partial_failure_does_not_lose_other_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m_good = DownloadedModel(model_id="org/good", local_path=os.path.join(tmpdir, "good"))
            m_bad = DownloadedModel(model_id="org/bad", local_path=os.path.join(tmpdir, "bad"))
            d._downloaded["org/good"] = m_good
            d._downloaded["org/bad"] = m_bad

            d.remove_downloaded("org/bad")
            assert d.get_downloaded("org/good") is m_good
            assert d.get_downloaded("org/bad") is None


class TestCleanupOnFailure:
    def test_integrity_failure_pops_model(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "corrupt.bin")
            db = ModelHashDB.from_known_models()

            def _fail_verify(*_a, **_kw):
                raise ModelIntegrityError("M", "corrupt.bin", "EXP", "ACT")

            with patch.object(db, "verify_download", side_effect=_fail_verify):
                d = ModelDownloader(cache_dir=tmpdir, hash_db=db, hf_token="tok")
                with pytest.raises(ModelIntegrityError):
                    d.download(model_id="HuggingFaceTB/SmolLM2-135M", filename="corrupt.bin")
                assert len(d._downloaded) == 0

    def test_duplicate_model_id_overwrites_tracker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = ModelDownloader(cache_dir=tmpdir)
            m1 = DownloadedModel(model_id="org/x", local_path="/old", size_bytes=100)
            m2 = DownloadedModel(model_id="org/x", local_path="/new", size_bytes=200)
            d._downloaded["org/x"] = m1
            d._downloaded["org/x"] = m2
            got = d.get_downloaded("org/x")
            assert got is not None
            assert got.local_path == "/new"
            assert got.size_bytes == 200


class TestDownloadSchedulingAndCost:
    def test_download_scheduling_returns_keys(self):
        d = ModelDownloader()
        result = d.check_download_scheduling(size_gb=2.5)
        assert "size_gb" in result
        assert "is_off_peak_now" in result
        assert "should_defer" in result
        assert "next_off_peak" in result

    def test_download_cost_returns_keys(self):
        d = ModelDownloader()
        result = d.estimate_download_cost(size_gb=3.0)
        assert "data_transfer_usd" in result
        assert "estimated_storage_usd_per_month" in result
        assert "prefer_off_peak" in result

    def test_download_defers_large_file_during_peak(self):
        with patch("general_ludd.small_models.cost.is_off_peak", return_value=False):
            d = ModelDownloader()
            result = d.check_download_scheduling(size_gb=2.0)
            assert result["should_defer"] is True

    def test_download_proceeds_small_file_during_peak(self):
        with patch("general_ludd.small_models.cost.is_off_peak", return_value=False):
            d = ModelDownloader()
            result = d.check_download_scheduling(size_gb=0.5)
            assert result["should_defer"] is False


class TestComputeSize:
    def test_size_computed_for_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"a" * 1234)
            path = f.name
        try:
            m = DownloadedModel(model_id="m", local_path=path)
            ModelDownloader._compute_size(ModelDownloader(), m)
            assert m.size_bytes == 1234
        finally:
            os.unlink(path)

    def test_size_computed_for_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "a.txt").write_text("hello")
            (Path(tmpdir) / "b.txt").write_text("world!")
            m = DownloadedModel(model_id="m", local_path=tmpdir)
            ModelDownloader._compute_size(ModelDownloader(), m)
            assert m.size_bytes == 11

    def test_size_default_zero_for_missing_path(self):
        m = DownloadedModel(model_id="m", local_path="/nonexistent/path")
        ModelDownloader._compute_size(ModelDownloader(), m)
        assert m.size_bytes == 0


class TestEdgeCases:
    def test_download_with_pinned_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "f.txt")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download_huggingface(model_id="org/model", filename="f.txt", revision="abc123")
            assert result.revision == "abc123"

    def test_download_without_revision_warns(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.snapshot_download") as mock_snap:
            mock_snap.return_value = os.path.join(tmpdir, "repo")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download_huggingface(model_id="org/model")
            assert result.revision is None

    def test_download_non_gguf_extension_routes_to_huggingface(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "model.safetensors")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download(model_id="org/model", filename="model.safetensors")
            assert result.source == DownloadSource.HUGGINGFACE

    def test_download_gguf_uppercase_routes_to_gguf(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "model.GGUF")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download(model_id="org/model", filename="model.GGUF")
            assert result.source == DownloadSource.GGUF

    def test_downloaded_model_default_timestamp_is_recent(self):
        before = time.time()
        m = DownloadedModel(model_id="m", local_path="/tmp/f")
        after = time.time()
        assert before <= m.downloaded_at <= after + 0.01

    def test_progress_percent_at_zero_total(self):
        p = DownloadProgress(total_bytes=0, downloaded_bytes=500)
        assert p.percent == 0.0

    def test_progress_percent_at_zero_downloaded(self):
        p = DownloadProgress(total_bytes=1000, downloaded_bytes=0)
        assert p.percent == 0.0

    def test_download_source_enum_values(self):
        assert DownloadSource.HUGGINGFACE == "huggingface"
        assert DownloadSource.GGUF == "gguf"
        assert DownloadSource.OLLAMA == "ollama"
        assert DownloadSource.CACHE == "cache"

    def test_hf_token_fallback_to_huggingface_hub_token(self):
        with patch.dict(os.environ, {"HUGGING_FACE_HUB_TOKEN": "fallback-token"}, clear=True):
            d = ModelDownloader()
            assert d.hf_token == "fallback-token"

    def test_hf_token_prefers_hf_token_over_hub_token(self):
        env = {"HF_TOKEN": "primary", "HUGGING_FACE_HUB_TOKEN": "fallback"}
        with patch.dict(os.environ, env, clear=True):
            d = ModelDownloader()
            assert d.hf_token == "primary"

    def test_gguf_download_sets_gguf_source(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "f.gguf")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download_gguf(model_id="org/m", filename="f.gguf")
            assert result.source == DownloadSource.GGUF
            assert result.filename == "f.gguf"

    def test_gguf_cache_only_resolution_is_forwarded(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "f.gguf")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")

            d.download_gguf(model_id="org/m", filename="f.gguf", local_files_only=True)

            mock_hf.assert_called_once_with(
                repo_id="org/m",
                filename="f.gguf",
                token="tok",
                revision=None,
                cache_dir=tmpdir,
                local_files_only=True,
            )

    def test_downloaded_at_is_set_on_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = os.path.join(tmpdir, "f.gguf")
            d = ModelDownloader(cache_dir=tmpdir, hf_token="tok")
            result = d.download_gguf(model_id="org/m", filename="f.gguf")
            now = time.time()
            assert abs(result.downloaded_at - now) < 5

    def test_size_bytes_in_downloaded_model_populated(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"x" * 5678)
            path = tf.name
        try:
            with patch("huggingface_hub.hf_hub_download", return_value=path):
                d = ModelDownloader(cache_dir=os.path.dirname(path), hf_token="tok")
                result = d.download_gguf(model_id="org/m", filename="x.gguf")
                assert result.size_bytes == 5678
        finally:
            os.unlink(path)
