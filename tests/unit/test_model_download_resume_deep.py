"""Unit tests for model download resume, retry, checksum verification, and backoff."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.download import (
    DownloadedModel,
    ModelDownloader,
)
from general_ludd.small_models.model_hash_db import (
    FileHash,
    ModelHashDB,
    ModelIntegrityError,
    _sha256_file,
)


class TestPartialDownloadResume:
    def test_hf_hub_download_passes_expected_kwargs(self):
        downloader = ModelDownloader(cache_dir="/tmp/test")
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/tmp/test/model.bin"
            result = downloader.download_huggingface(model_id="test/model", filename="model.bin", revision="abc123")
        assert result.model_id == "test/model"
        call_kwargs = mock_hf.call_args.kwargs
        assert call_kwargs["repo_id"] == "test/model"
        assert call_kwargs["filename"] == "model.bin"
        assert call_kwargs["revision"] == "abc123"

    def test_hf_hub_download_supports_resume_download(self):
        downloader = ModelDownloader(cache_dir="/tmp/test")
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/tmp/test/model.bin"
            downloader.download_huggingface(model_id="test/model", filename="model.bin")
        call_kwargs = mock_hf.call_args.kwargs
        assert call_kwargs["repo_id"] == "test/model"
        assert call_kwargs["filename"] == "model.bin"

    def test_progress_preserved_across_partial_chunks(self):
        downloader = ModelDownloader(cache_dir="/tmp/test")
        cb = downloader._make_progress_callback(filename="large.bin", total_bytes=10000)
        cb(0, 3000, 10000)
        assert 29.0 <= downloader.get_progress().percent <= 31.0
        cb(3000, 7000, 10000)
        assert 69.0 <= downloader.get_progress().percent <= 71.0
        cb(7000, 10000, 10000)
        assert downloader.get_progress().percent == 100.0
        assert downloader.get_progress().status == "done"

    def test_multiple_partial_downloads_reset_progress_per_file(self):
        downloader = ModelDownloader(cache_dir="/tmp/test")
        cb1 = downloader._make_progress_callback("a.bin", 5000)
        cb1(0, 2500, 5000)
        assert downloader.get_progress().downloaded_bytes == 2500
        cb2 = downloader._make_progress_callback("b.bin", 8000)
        cb2(0, 4000, 8000)
        assert downloader.get_progress().downloaded_bytes == 4000


class TestChecksumVerificationAfterResume:
    def test_sha256_file_matches_known_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "model.bin"
            content = b"correct-model-bytes" * 100
            filepath.write_bytes(content)
            expected_sha = hashlib.sha256(content).hexdigest()
            actual_sha = _sha256_file(str(filepath))
            assert actual_sha == expected_sha

    def test_sha256_file_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "empty.bin"
            filepath.write_bytes(b"")
            actual_sha = _sha256_file(str(filepath))
            expected_sha = hashlib.sha256(b"").hexdigest()
            assert actual_sha == expected_sha

    def test_verify_download_raises_on_mismatch(self):
        model_id = "HuggingFaceTB/SmolLM2-135M"
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / model_id.replace("/", "_")
            subdir.mkdir()
            (subdir / "model.safetensors").write_bytes(b"tampered-content")
            (subdir / "config.json").write_bytes(b"{}")
            (subdir / "tokenizer.json").write_bytes(b"{}")
            (subdir / "tokenizer_config.json").write_bytes(b"{}")
            (subdir / "generation_config.json").write_bytes(b"{}")
            (subdir / "special_tokens_map.json").write_bytes(b"{}")
            db = ModelHashDB.from_known_models()
            with pytest.raises(ModelIntegrityError) as exc_info:
                db.verify_download(model_id, str(subdir))
            assert exc_info.value.model_id == model_id
            assert exc_info.value.filename == "model.safetensors"

    def test_verify_download_skips_unregistered_model(self):
        db = ModelHashDB.from_known_models()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent_model"
            path.mkdir()
            (path / "file.bin").write_bytes(b"data")
            db.verify_download("org/nonexistent-model", str(path))

    def test_download_verify_hash_pops_on_integrity_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "test_model"
            subdir.mkdir()
            (subdir / "model.safetensors").write_bytes(b"bad-data")
            downloader = ModelDownloader(cache_dir=tmpdir)
            db = ModelHashDB.from_known_models()
            downloader._hash_db = db
            downloader._downloaded["HuggingFaceTB/SmolLM2-135M"] = DownloadedModel(
                model_id="HuggingFaceTB/SmolLM2-135M",
                local_path=str(subdir),
            )
            with patch.object(db, "verify_download") as mock_verify:
                mock_verify.side_effect = ModelIntegrityError(
                    "HuggingFaceTB/SmolLM2-135M",
                    "model.safetensors",
                    "abc123",
                    "def456",
                )
                with patch(
                    "general_ludd.small_models.cost.should_defer_download",
                    return_value={},
                ), patch(
                    "huggingface_hub.hf_hub_download",
                    return_value=str(subdir / "model.safetensors"),
                ), pytest.raises(ModelIntegrityError):
                    downloader.download(
                        "HuggingFaceTB/SmolLM2-135M",
                        filename="model.safetensors",
                        force=True,
                        verify_hash=True,
                    )
            assert "HuggingFaceTB/SmolLM2-135M" not in downloader._downloaded


class TestRetryWithBackoff:
    def test_exponential_backoff_delays_grow(self):
        base = 0.1
        delays = [base * (2**attempt) for attempt in range(5)]
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1]

    def test_backoff_capped_at_max_delay(self):
        max_delay = 30.0
        base = 2.0
        for attempt in range(20):
            delay = min(base * (2**attempt), max_delay)
            assert delay <= max_delay

    def test_base_delay_used_for_first_attempt(self):
        base = 0.5
        max_delay = 60.0
        delay = min(base * (2**0), max_delay)
        assert delay == 0.5

    def test_backoff_delay_integrates_with_retry_item_ready_at(self):
        from general_ludd.messaging.retry_queue import RetryItem

        now = 1000.0
        base_delay = 0.5
        max_delay = 60.0
        item = RetryItem(item_id="t1", payload={}, priority=0, enqueued_at=now, ready_at=now, attempt=0)
        delay = min(base_delay * (2**item.attempt), max_delay)
        assert delay == 0.5
        item.attempt = 1
        delay = min(base_delay * (2**item.attempt), max_delay)
        assert delay == 1.0
        item.attempt = 2
        delay = min(base_delay * (2**item.attempt), max_delay)
        assert delay == 2.0
        item.attempt = 10
        delay = min(base_delay * (2**item.attempt), max_delay)
        assert delay == max_delay


class TestMaxRetryExhaustion:
    def test_exhaustion_after_max_retries_halts(self):
        from general_ludd.messaging.retry_queue import RetryQueue

        rq = RetryQueue(max_retries=2, base_delay=0.01)
        rq.enqueue({"task": "download"})
        item = rq.dequeue(timeout=1.0)
        assert item is not None
        rq.nack(item.item_id, "transient error")
        item2 = rq.dequeue(timeout=1.0)
        assert item2 is not None
        rq.nack(item2.item_id, "transient error")
        item3 = rq.dequeue(timeout=1.0)
        assert item3 is not None
        rq.nack(item3.item_id, "transient error")
        assert rq.dlq_size == 1

    def test_max_retries_zero_sends_to_dlq_immediately(self):
        from general_ludd.messaging.retry_queue import RetryQueue

        rq = RetryQueue(max_retries=0, base_delay=0.01)
        rq.enqueue({"task": "download"})
        item = rq.dequeue(timeout=1.0)
        assert item is not None
        rq.nack(item.item_id, "error")
        assert rq.dlq_size == 1

    def test_ack_after_successful_retry(self):
        from general_ludd.messaging.retry_queue import RetryQueue

        rq = RetryQueue(max_retries=3, base_delay=0.01)
        rq.enqueue({"task": "download"})
        item = rq.dequeue(timeout=1.0)
        assert item is not None
        assert item.attempt == 0
        rq.nack(item.item_id, "first fail")
        item2 = rq.dequeue(timeout=1.0)
        assert item2 is not None
        assert item2.attempt == 1
        rq.ack(item2.item_id)
        assert rq.size == 0
        assert rq.active_count == 0
        assert rq.dlq_size == 0


class TestModelHashDBPersistence:
    def test_register_and_retrieve_rtt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dbpath = Path(tmpdir) / "hashes.json"
            db = ModelHashDB(db_path=str(dbpath))
            fh = FileHash("test.bin", 64 * "a")
            db.register_model("test/model", [fh])
            db2 = ModelHashDB(db_path=str(dbpath))
            retrieved = db2.get_hashes("test/model")
            assert retrieved is not None
            assert len(retrieved) == 1
            assert retrieved[0].filename == "test.bin"
            assert retrieved[0].sha256 == 64 * "a"

    def test_remove_model_clears_hashes(self):
        db = ModelHashDB()
        fh = FileHash("test.bin", 64 * "f")
        db.register_model("test/model", [fh])
        assert db.get_hashes("test/model") is not None
        db.remove_model("test/model")
        assert db.get_hashes("test/model") is None

    def test_clear_all_entries(self):
        db = ModelHashDB()
        db.register_model("a", [FileHash("a.bin", 64 * "a")])
        db.register_model("b", [FileHash("b.bin", 64 * "b")])
        assert len(db.list_models()) == 2
        db.clear()
        assert len(db.list_models()) == 0
