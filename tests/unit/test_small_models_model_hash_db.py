"""Unit tests for ModelHashDB, KnownModels, FileHash, and ModelIntegrityError."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.download import (
    DownloadedModel,
    DownloadSource,
    ModelDownloader,
)
from general_ludd.small_models.model_hash_db import (
    FileHash,
    KnownModels,
    ModelHashDB,
    ModelIntegrityError,
    _sha256_file,
    load_known_models_from_config,
    merge_known_models,
)


class TestFileHash:
    def test_construction(self):
        fh = FileHash(filename="model.safetensors", sha256="abc123")
        assert fh.filename == "model.safetensors"
        assert fh.sha256 == "abc123"

    def test_equality(self):
        a = FileHash(filename="f", sha256="a")
        b = FileHash(filename="f", sha256="a")
        c = FileHash(filename="f", sha256="b")
        assert a == b
        assert a != c

    def test_to_dict(self):
        fh = FileHash(filename="tokenizer.json", sha256="deadbeef")
        d = fh.to_dict()
        assert d == {"filename": "tokenizer.json", "sha256": "deadbeef"}

    def test_from_dict(self):
        d = {"filename": "config.json", "sha256": "cafe"}
        fh = FileHash.from_dict(d)
        assert fh.filename == "config.json"
        assert fh.sha256 == "cafe"


class TestModelIntegrityError:
    def test_message(self):
        err = ModelIntegrityError("org/model", "model.safetensors", "abc", "def")
        assert "org/model" in str(err)
        assert "model.safetensors" in str(err)
        assert "abc" in str(err)
        assert "def" in str(err)

    def test_is_exception(self):
        err = ModelIntegrityError("m", "f", "a", "b")
        assert isinstance(err, Exception)


class TestKnownModels:
    def test_has_entry_for_smollm2(self):
        files = KnownModels.get("HuggingFaceTB/SmolLM2-135M")
        assert files is not None
        assert isinstance(files, list)
        assert len(files) > 0

    def test_has_entry_for_qwen(self):
        files = KnownModels.get("Qwen/Qwen2.5-0.5B")
        assert files is not None

    def test_has_entry_for_tinyllama(self):
        files = KnownModels.get("TinyLlama/TinyLlama-1.1B-Chat-v1.0")
        assert files is not None

    def test_has_entry_for_phi2(self):
        files = KnownModels.get("microsoft/phi-2")
        assert files is not None

    def test_has_entry_for_qwen_gguf(self):
        files = KnownModels.get("Qwen/Qwen2.5-0.5B-GGUF")
        assert files is not None
        assert isinstance(files, list)
        assert len(files) == 3
        filenames = [fh.filename for fh in files]
        assert "qwen2.5-0.5b-q4_k_m.gguf" in filenames
        assert "config.json" in filenames
        assert "tokenizer.json" in filenames

    def test_missing_model_returns_none(self):
        assert KnownModels.get("nonexistent/model-v99") is None

    def test_all_models_are_list_of_filehash(self):
        for _model_id, files in KnownModels.all().items():
            assert isinstance(files, list)
            for fh in files:
                assert isinstance(fh, FileHash)
                assert fh.filename
                assert len(fh.sha256) == 64

    def test_stable_across_calls(self):
        a = KnownModels.get("HuggingFaceTB/SmolLM2-135M")
        b = KnownModels.get("HuggingFaceTB/SmolLM2-135M")
        assert a == b


class TestModelHashDB:
    def test_init_creates_empty_db(self):
        db = ModelHashDB()
        assert db.list_models() == []

    def test_register_and_list(self):
        db = ModelHashDB()
        db.register_model("org/model", [FileHash("a.bin", "sha256hex")])
        assert "org/model" in db.list_models()

    def test_register_overwrites(self):
        db = ModelHashDB()
        db.register_model("org/model", [FileHash("a.bin", "aaa")])
        db.register_model("org/model", [FileHash("b.bin", "bbb")])
        files = db.get_hashes("org/model")
        assert len(files) == 1
        assert files[0].filename == "b.bin"

    def test_get_hashes_missing(self):
        db = ModelHashDB()
        assert db.get_hashes("nonexistent") is None

    def test_verify_download_file_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"hello world"
            expected_sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(content)

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("model.bin", expected_sha)])
            db.verify_download("org/model", str(fpath))

    def test_verify_download_directory_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content_a = b"file a data"
            content_b = b"file b data"
            sha_a = hashlib.sha256(content_a).hexdigest()
            sha_b = hashlib.sha256(content_b).hexdigest()

            (Path(tmpdir) / "a.bin").write_bytes(content_a)
            (Path(tmpdir) / "b.bin").write_bytes(content_b)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [
                    FileHash("a.bin", sha_a),
                    FileHash("b.bin", sha_b),
                ],
            )
            db.verify_download("org/model", tmpdir)

    def test_verify_download_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(b"hello world")

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("model.bin", "0" * 64)])
            try:
                db.verify_download("org/model", str(fpath))
                raise AssertionError("expected ModelIntegrityError")
            except ModelIntegrityError as e:
                assert "org/model" in str(e)
                assert "model.bin" in str(e)

    def test_verify_download_deletes_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(b"hello world")

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("model.bin", "0" * 64)])
            with contextlib.suppress(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))
            assert not fpath.exists()

    def test_verify_download_unregistered_model_skips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(b"hello")
            db = ModelHashDB()
            db.verify_download("org/model", str(fpath))

    def test_verify_download_missing_registered_file_skips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("missing.bin", "0" * 64)])
            db.verify_download("org/model", tmpdir)

    def test_json_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hashes.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.register_model("org/b", [FileHash("y.bin", "b" * 64)])

            db2 = ModelHashDB(db_path=str(db_path))
            assert set(db2.list_models()) == {"org/a", "org/b"}
            files = db2.get_hashes("org/a")
            assert files is not None
            assert files[0].filename == "x.bin"
            assert files[0].sha256 == "a" * 64

    def test_json_persistence_empty_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hashes.json"
            ModelHashDB(db_path=str(db_path))
            db2 = ModelHashDB(db_path=str(db_path))
            assert db2.list_models() == []

    def test_import_from_hf_readme_metadata(self):
        readme = b"""---
library_name: transformers
license: apache-2.0
base_model: HuggingFaceTB/SmolLM2-135M
---

# SmolLM2
"""
        db = ModelHashDB()
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("HuggingFaceTB/SmolLM2-135M")
        assert isinstance(result, bool)

    def test_import_from_hf_no_readme(self):
        db = ModelHashDB()
        with patch("huggingface_hub.hf_hub_download", side_effect=Exception("no readme")):
            result = db.import_from_hf("nonexistent/model")
        assert result is False

    def test_deduct_from_known_models(self):
        db = ModelHashDB()
        db.import_from_hf("HuggingFaceTB/SmolLM2-135M")
        files = db.get_hashes("HuggingFaceTB/SmolLM2-135M")
        assert files is not None
        assert len(files) > 0

    def test_remove_model(self):
        db = ModelHashDB()
        db.register_model("org/model", [FileHash("f.bin", "a" * 64)])
        assert "org/model" in db.list_models()
        db.remove_model("org/model")
        assert "org/model" not in db.list_models()

    def test_remove_nonexistent(self):
        db = ModelHashDB()
        db.remove_model("nonexistent")

    def test_clear(self):
        db = ModelHashDB()
        db.register_model("org/a", [FileHash("f.bin", "a" * 64)])
        db.register_model("org/b", [FileHash("f.bin", "b" * 64)])
        db.clear()
        assert db.list_models() == []

    def test_from_known_models_populates(self):
        db = ModelHashDB.from_known_models()
        models = db.list_models()
        assert "HuggingFaceTB/SmolLM2-135M" in models
        assert "Qwen/Qwen2.5-0.5B" in models
        assert "Qwen/Qwen2.5-0.5B-GGUF" in models
        assert "TinyLlama/TinyLlama-1.1B-Chat-v1.0" in models
        assert "microsoft/phi-2" in models


class TestModelDownloaderHashIntegration:
    def test_download_verifies_hash_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            dl._hash_db = ModelHashDB()
            dl._hash_db.register_model(
                "HuggingFaceTB/SmolLM2-135M",
                [FileHash("model.safetensors", "0" * 64)],
            )

            with patch.object(dl, "download_huggingface") as mock_dl:
                mock_dl.return_value.local_path = tmpdir
                with contextlib.suppress(ModelIntegrityError):
                    dl.download("HuggingFaceTB/SmolLM2-135M", verify_hash=True)

    def test_download_skips_hash_verify_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            dl._hash_db = ModelHashDB()

            with patch.object(dl, "download_huggingface") as mock_dl:
                mock_dl.return_value = DownloadedModel(
                    model_id="org/model",
                    local_path=tmpdir,
                )
                result = dl.download("org/model", verify_hash=False)
            assert result.model_id == "org/model"


class TestModelDownloaderResilience:
    def test_default_cache_falls_back_to_project_namespace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import general_ludd.small_models.download as download_module

        blocker = tmp_path / "not-a-directory"
        blocker.write_text("blocked")
        unavailable = blocker / "models"
        monkeypatch.setattr(download_module, "DEFAULT_CACHE_DIR", str(unavailable))
        monkeypatch.delenv("GLUDD_MODEL_DIR", raising=False)
        monkeypatch.delenv("GLUDD_MODELS_DIR", raising=False)
        monkeypatch.setenv("GLUDD_STATE_DIR", str(tmp_path / "state"))

        downloader = ModelDownloader()

        assert downloader.cache_dir != str(unavailable)
        assert Path(downloader.cache_dir).name == "models"
        assert Path(downloader.cache_dir).is_dir()

    def test_download_exception_from_hf_propagates(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = ModelDownloader(cache_dir=tmpdir)
            with (
                patch.object(dl, "download_huggingface", side_effect=ConnectionError("network down")),
                pytest.raises(ConnectionError),
            ):
                dl.download("org/model")
            assert "org/model" not in dl._downloaded

    def test_download_exception_preserves_prior_state(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = ModelDownloader(cache_dir=tmpdir)
            dl._downloaded["org/prior"] = DownloadedModel(
                model_id="org/prior",
                local_path=tmpdir,
            )
            with (
                patch.object(dl, "download_huggingface", side_effect=ConnectionError("transient")),
                pytest.raises(ConnectionError),
            ):
                dl.download("org/model")
            assert "org/prior" in dl._downloaded
            assert "org/model" not in dl._downloaded

    def test_download_retry_by_caller_after_failure(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = ModelDownloader(cache_dir=tmpdir)
            max_attempts = 3
            attempts = 0

            for _ in range(max_attempts):
                attempts += 1
                if attempts < max_attempts:
                    with patch.object(dl, "download_huggingface", side_effect=ConnectionError("transient")):
                        try:
                            dl.download("org/model")
                        except ConnectionError:
                            continue
                else:
                    fpath = Path(tmpdir) / "model.bin"
                    fpath.write_bytes(b"data")
                    with patch.object(dl, "download_huggingface") as mock_dl:
                        mock_dl.return_value = DownloadedModel(
                            model_id="org/model",
                            local_path=str(fpath),
                            source=DownloadSource.HUGGINGFACE,
                        )
                        result = dl.download("org/model")
                    assert result.model_id == "org/model"
                    break
            assert attempts == max_attempts

    def test_checksum_failure_removes_downloaded_model(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.safetensors"
            fpath.write_bytes(b"tampered content")

            dl = ModelDownloader(cache_dir=tmpdir)
            dl._hash_db = ModelHashDB()
            dl._hash_db.register_model(
                "org/model",
                [FileHash("model.safetensors", "0" * 64)],
            )

            fake_model = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
            )
            with patch.object(dl, "download_huggingface", return_value=fake_model), pytest.raises(ModelIntegrityError):
                dl.download("org/model", verify_hash=True)
            assert "org/model" not in dl._downloaded

    def test_checksum_failure_does_not_delete_valid_other_model(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = ModelDownloader(cache_dir=tmpdir)
            dl._downloaded["org/other"] = DownloadedModel(
                model_id="org/other",
                local_path=tmpdir,
            )
            fpath = Path(tmpdir) / "model.safetensors"
            fpath.write_bytes(b"tampered content")

            dl._hash_db = ModelHashDB()
            dl._hash_db.register_model("org/model", [FileHash("model.safetensors", "f" * 64)])
            fake_model = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
            )
            with patch.object(dl, "download_huggingface", return_value=fake_model), pytest.raises(ModelIntegrityError):
                dl.download("org/model", verify_hash=True)
            assert "org/other" in dl._downloaded
            assert "org/model" not in dl._downloaded

    def test_checksum_skipped_when_hash_db_is_none(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = ModelDownloader(cache_dir=tmpdir)
            assert dl._hash_db is None
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(b"data")
            mock_result = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
            )
            with patch.object(dl, "download_huggingface", return_value=mock_result):
                result = dl.download("org/model", verify_hash=True)
            assert result is mock_result
            assert result.model_id == "org/model"

    def test_partial_download_resume_from_existing_file(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.safetensors"
            fpath.write_bytes(b"partial-content-" + b"\x00" * 1024)

            dl = ModelDownloader(cache_dir=tmpdir)
            fake_model = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
                size_bytes=len(b"partial-content-" + b"\x00" * 1024),
            )
            with patch.object(dl, "download_huggingface", return_value=fake_model):
                result = dl.download("org/model", verify_hash=False)
            assert result.model_id == "org/model"
            assert result.local_path == str(fpath)

    def test_partial_download_cache_dir_preexists(self):

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / ".cache" / "general-ludd" / "models"
            cache_dir.mkdir(parents=True)
            (cache_dir / "stale.bin").write_bytes(b"stale")

            dl = ModelDownloader(cache_dir=str(cache_dir))
            assert cache_dir.exists()
            assert (cache_dir / "stale.bin").exists()

            fpath = Path(tmpdir) / "fresh.bin"
            fpath.write_bytes(b"fresh")
            mock_result = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
            )
            with patch.object(dl, "download_huggingface", return_value=mock_result):
                returned = dl.download("org/model", verify_hash=False)
            assert returned is mock_result
            assert returned.model_id == "org/model"

    def test_partial_download_progress_tracks_bytes(self):

        dl = ModelDownloader()
        progress = dl.get_progress()
        assert progress.status == "idle"
        assert progress.downloaded_bytes == 0

        cb = dl._make_progress_callback("test.bin", 1024)
        cb(0, 256, 1024)
        progress = dl.get_progress()
        assert progress.downloaded_bytes == 256
        assert progress.total_bytes == 1024
        assert 0.0 < progress.percent < 100.0

    def test_partial_download_progress_completion(self):

        dl = ModelDownloader()
        cb = dl._make_progress_callback("test.bin", 100)
        cb(0, 100, 100)
        progress = dl.get_progress()
        assert progress.status == "done"
        assert progress.percent == 100.0

    def test_concurrent_downloads_isolate_state(self):

        dl1 = ModelDownloader()
        dl2 = ModelDownloader()

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath1 = Path(tmpdir) / "model1.bin"
            fpath1.write_bytes(b"a")
            fpath2 = Path(tmpdir) / "model2.bin"
            fpath2.write_bytes(b"b")

            fake1 = DownloadedModel(
                model_id="org/model1",
                local_path=str(fpath1),
                source=DownloadSource.HUGGINGFACE,
            )
            fake2 = DownloadedModel(
                model_id="org/model2",
                local_path=str(fpath2),
                source=DownloadSource.HUGGINGFACE,
            )

            with (
                patch.object(dl1, "download_huggingface", return_value=fake1),
                patch.object(dl2, "download_huggingface", return_value=fake2),
            ):
                dl1.download("org/model1", verify_hash=False)
                dl2.download("org/model2", verify_hash=False)

            assert dl1.get_downloaded("org/model1") is not None
            assert dl1.get_downloaded("org/model2") is None
            assert dl2.get_downloaded("org/model2") is not None
            assert dl2.get_downloaded("org/model1") is None

    def test_concurrent_thread_register_and_list(self):
        import threading

        dl = ModelDownloader()
        errors: list[Exception] = []

        def register(prefix: str):
            try:
                for i in range(20):
                    dl._downloaded[f"org/{prefix}-{i}"] = DownloadedModel(
                        model_id=f"org/{prefix}-{i}",
                        local_path=f"/tmp/{prefix}-{i}",
                    )
                    dl.get_downloaded(f"org/{prefix}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register, args=(f"t{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        all_models = dl.list_downloaded()
        for t in range(4):
            for i in range(20):
                matching = [m for m in all_models if m.model_id == f"org/t{t}-{i}"]
                assert len(matching) == 1

    def test_concurrent_download_same_model_id_overwrites(self):

        dl = ModelDownloader()
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(b"v2")
            fake = DownloadedModel(
                model_id="org/model",
                local_path=str(fpath),
                source=DownloadSource.HUGGINGFACE,
            )
            dl._downloaded["org/model"] = DownloadedModel(
                model_id="org/model",
                local_path="/tmp/old",
            )
            with patch.object(dl, "download_huggingface", return_value=fake):
                dl.download("org/model", verify_hash=False)
            result = dl.get_downloaded("org/model")
            assert result is not None
            assert result.local_path == str(fpath)

    def test_disk_space_guard_defer_large_download_during_peak(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "models"
            cache_dir.mkdir(parents=True)

            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=str(cache_dir))

            _usage = shutil.disk_usage(str(cache_dir)).free
            scheduling = dl.check_download_scheduling(5.0)
            assert "size_gb" in scheduling
            assert "should_defer" in scheduling
            assert "next_off_peak" in scheduling
            assert scheduling["size_gb"] == 5.0

            scheduling_small = dl.check_download_scheduling(0.1)
            assert scheduling_small["size_gb"] == 0.1

    def test_download_force_overrides_defer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            with (
                patch("general_ludd.small_models.cost.should_defer_download") as mock_defer,
                patch.object(dl, "download_huggingface") as mock_dl,
            ):
                mock_dl.return_value = DownloadedModel(
                    model_id="org/model",
                    local_path=tmpdir,
                )
                result = dl.download("org/model", force=True, verify_hash=False)
            assert result.model_id == "org/model"
            mock_defer.assert_not_called()

    def test_download_respects_timeout_configuration(self):

        dl = ModelDownloader(timeout=15.0)
        assert dl.timeout == 15.0

        dl_default = ModelDownloader()
        assert isinstance(dl_default.timeout, float)
        assert dl_default.timeout > 0

    def test_download_gguf_route_from_filename_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            with patch.object(dl, "download_gguf") as mock_gguf:
                mock_gguf.return_value = DownloadedModel(
                    model_id="org/model",
                    local_path=tmpdir,
                    source=DownloadSource.GGUF,
                    filename="q4_k_m.gguf",
                )
                result = dl.download("org/model", filename="q4_k_m.gguf", verify_hash=False)
            assert result.source == DownloadSource.GGUF
            assert result.filename == "q4_k_m.gguf"

    def test_download_hf_route_from_non_gguf_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            with patch.object(dl, "download_huggingface") as mock_hf:
                mock_hf.return_value = DownloadedModel(
                    model_id="org/model",
                    local_path=tmpdir,
                    source=DownloadSource.HUGGINGFACE,
                    filename="model.safetensors",
                )
                result = dl.download("org/model", filename="model.safetensors", verify_hash=False)
            assert result.source == DownloadSource.HUGGINGFACE
            assert result.filename == "model.safetensors"


class TestFileHashDeep:
    def test_immutable_frozen_dataclass_cannot_set_attribute(self) -> None:
        fh = FileHash(filename="x.bin", sha256="a" * 64)
        with pytest.raises(dataclasses.FrozenInstanceError):
            fh.filename = "y.bin"  # type: ignore[misc]

    def test_hashable_works_in_set(self) -> None:
        a = FileHash(filename="a.bin", sha256="0" * 64)
        b = FileHash(filename="a.bin", sha256="0" * 64)
        c = FileHash(filename="b.bin", sha256="1" * 64)
        s = {a, b, c}
        assert len(s) == 2
        assert a in s
        assert c in s

    def test_hashable_works_as_dict_key(self) -> None:
        fh = FileHash(filename="k.bin", sha256="f" * 64)
        d: dict[FileHash, int] = {fh: 42}
        assert d[fh] == 42
        same = FileHash(filename="k.bin", sha256="f" * 64)
        assert d[same] == 42

    def test_to_dict_roundtrip_via_from_dict(self) -> None:
        original = FileHash(filename="roundtrip.bin", sha256="e" * 64)
        d = original.to_dict()
        restored = FileHash.from_dict(d)
        assert restored == original
        assert restored is not original

    def test_empty_filename_and_sha(self) -> None:
        fh = FileHash(filename="", sha256="")
        assert fh.filename == ""
        assert fh.sha256 == ""
        d = fh.to_dict()
        assert d == {"filename": "", "sha256": ""}


class TestModelIntegrityErrorDeep:
    def test_model_id_property(self) -> None:
        err = ModelIntegrityError("org/repo", "f.bin", "abc", "def")
        assert err.model_id == "org/repo"

    def test_filename_property(self) -> None:
        err = ModelIntegrityError("org/repo", "model.safetensors", "abc", "def")
        assert err.filename == "model.safetensors"

    def test_expected_property(self) -> None:
        err = ModelIntegrityError("org/repo", "f.bin", "sha_expected", "sha_actual")
        assert err.expected == "sha_expected"

    def test_actual_property(self) -> None:
        err = ModelIntegrityError("org/repo", "f.bin", "sha_expected", "sha_actual")
        assert err.actual == "sha_actual"

    def test_message_truncates_hashes_to_sixteen_chars(self) -> None:
        long_expected = "a" * 64
        long_actual = "b" * 64
        err = ModelIntegrityError("m", "f", long_expected, long_actual)
        msg = str(err)
        assert long_expected[:16] in msg
        assert long_actual[:16] in msg
        assert long_expected not in msg
        assert long_actual not in msg

    def test_message_contains_short_hash_unchanged(self) -> None:
        err = ModelIntegrityError("m", "f", "abc", "def")
        msg = str(err)
        assert "abc" in msg
        assert "def" in msg

    def test_exception_can_be_caught_and_reraised(self) -> None:
        err = ModelIntegrityError("m", "f", "a", "b")
        with pytest.raises(ModelIntegrityError) as exc_info:
            raise err
        assert exc_info.value is err


class TestKnownModelsDeep:
    def test_all_returns_copy_not_internal_reference(self) -> None:
        d1 = KnownModels.all()
        d2 = KnownModels.all()
        assert d1 is not d2
        assert d1 == d2

    def test_all_mutation_does_not_affect_internal_state(self) -> None:
        d = KnownModels.all()
        d["fake/model"] = [FileHash("f.bin", "0" * 64)]
        assert KnownModels.get("fake/model") is None

    def test_all_keys_are_nonempty_strings(self) -> None:
        for model_id in KnownModels.all():
            assert isinstance(model_id, str)
            assert model_id

    def test_file_hashes_in_all_have_unique_filenames(self) -> None:
        for model_id, files in KnownModels.all().items():
            filenames = [fh.filename for fh in files]
            assert len(filenames) == len(set(filenames)), f"{model_id} has duplicate filenames"

    def test_empty_string_model_id_returns_none(self) -> None:
        assert KnownModels.get("") is None

    def test_case_sensitivity(self) -> None:
        assert KnownModels.get("huggingfacetb/smollm2-135m") is None


class TestModelHashDBDeep:
    def test_register_empty_file_list_still_registers(self) -> None:
        db = ModelHashDB()
        db.register_model("org/model", [])
        assert "org/model" in db.list_models()
        assert db.get_hashes("org/model") == []

    def test_verify_download_with_regular_file_not_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"match content"
            sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "single.bin"
            fpath.write_bytes(content)

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("single.bin", sha)])
            db.verify_download("org/model", str(fpath))

    def test_verify_download_directory_partial_match_raises_on_bad(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            good = b"good data"
            bad = b"bad data"
            sha_good = hashlib.sha256(good).hexdigest()
            (Path(tmpdir) / "good.bin").write_bytes(good)
            (Path(tmpdir) / "bad.bin").write_bytes(bad)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [FileHash("good.bin", sha_good), FileHash("bad.bin", "0" * 64)],
            )
            with pytest.raises(ModelIntegrityError) as exc_info:
                db.verify_download("org/model", tmpdir)
            assert exc_info.value.filename == "bad.bin"

    def test_verify_download_directory_missing_file_skips_verifies_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"real data"
            sha = hashlib.sha256(content).hexdigest()
            (Path(tmpdir) / "real.bin").write_bytes(content)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [FileHash("missing.bin", "0" * 64), FileHash("real.bin", sha)],
            )
            db.verify_download("org/model", tmpdir)

    def test_verify_download_nonexistent_path_does_not_raise(self) -> None:
        db = ModelHashDB()
        db.register_model("org/model", [FileHash("ghost.bin", "0" * 64)])
        db.verify_download("org/model", "/nonexistent/path/12345")

    def test_verify_download_unregistered_model_returns_none_like(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "x.bin"
            fpath.write_bytes(b"data")
            db = ModelHashDB()
            result = db.verify_download("unknown/model", str(fpath))
            assert result is None

    def test_json_persistence_corrupted_file_silently_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "corrupt.json"
            db_path.write_text("{not valid json")

            db = ModelHashDB(db_path=str(db_path))
            assert db.list_models() == []

    def test_json_persistence_truncated_file_silently_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "truncated.json"
            db_path.write_text('{"org/a": [{"filename": "x.bin"')

            db = ModelHashDB(db_path=str(db_path))
            assert db.list_models() == []

    def test_json_persistence_writes_valid_json_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.register_model("org/b", [FileHash("y.bin", "b" * 64)])

            raw = json.loads(Path(db_path).read_text())
            assert set(raw.keys()) == {"org/a", "org/b"}
            assert raw["org/a"][0]["filename"] == "x.bin"
            assert raw["org/a"][0]["sha256"] == "a" * 64

    def test_no_persistence_when_db_path_is_none(self) -> None:
        db = ModelHashDB()
        db.register_model("org/model", [FileHash("f.bin", "a" * 64)])
        assert "org/model" in db.list_models()

    def test_remove_model_persists_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.register_model("org/b", [FileHash("y.bin", "b" * 64)])
            db.remove_model("org/a")

            db2 = ModelHashDB(db_path=str(db_path))
            assert db2.list_models() == ["org/b"]

    def test_clear_persists_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.clear()

            db2 = ModelHashDB(db_path=str(db_path))
            assert db2.list_models() == []

    def test_register_multiple_overwrites_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("v1.bin", "1" * 64)])
            db.register_model("org/a", [FileHash("v2.bin", "2" * 64)])

            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/a")
            assert files is not None
            assert files[0].filename == "v2.bin"

    def test_from_known_models_all_entries_nonempty(self) -> None:
        db = ModelHashDB.from_known_models()
        for model_id in db.list_models():
            files = db.get_hashes(model_id)
            assert files is not None
            assert len(files) > 0

    def test_from_known_models_is_independent_of_source_mutation(self) -> None:
        db = ModelHashDB.from_known_models()
        initial_count = len(db.list_models())
        db.register_model("custom/model", [FileHash("x.bin", "a" * 64)])
        assert "custom/model" in db.list_models()
        assert KnownModels.get("custom/model") is None
        assert len(db.list_models()) == initial_count + 1

    def test_list_models_returns_alphabetical_via_sort_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("z/model", [FileHash("z.bin", "z" * 64)])
            db.register_model("a/model", [FileHash("a.bin", "a" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            models = db2.list_models()
            assert models == sorted(models)

    def test_verify_download_corrupt_file_is_deleted_before_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "corrupt.bin"
            fpath.write_bytes(b"corrupt content")

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("corrupt.bin", "0" * 64)])
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))
            assert not fpath.exists()

    def test_verify_download_only_deletes_mismatched_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            good_content = b"good"
            bad_content = b"bad"
            sha_good = hashlib.sha256(good_content).hexdigest()
            (Path(tmpdir) / "good.bin").write_bytes(good_content)
            (Path(tmpdir) / "bad.bin").write_bytes(bad_content)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [
                    FileHash("good.bin", sha_good),
                    FileHash("bad.bin", "0" * 64),
                ],
            )
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", tmpdir)
            assert (Path(tmpdir) / "good.bin").exists()
            assert not (Path(tmpdir) / "bad.bin").exists()


class TestSha256File:
    def test_empty_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "empty.bin"
            fpath.write_bytes(b"")
            sha = _sha256_file(str(fpath))
            assert sha == hashlib.sha256(b"").hexdigest()

    def test_large_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "large.bin"
            content = b"x" * (256 * 1024)
            fpath.write_bytes(content)
            sha = _sha256_file(str(fpath))
            assert sha == hashlib.sha256(content).hexdigest()

    def test_binary_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "binary.bin"
            content = bytes(range(256))
            fpath.write_bytes(content)
            sha = _sha256_file(str(fpath))
            assert sha == hashlib.sha256(content).hexdigest()

    def test_single_byte_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "one.bin"
            fpath.write_bytes(b"\xff")
            sha = _sha256_file(str(fpath))
            assert sha == hashlib.sha256(b"\xff").hexdigest()


class TestImportFromHFDeep:
    def test_known_model_is_checked_first_no_hf_call(self) -> None:
        db = ModelHashDB()
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            result = db.import_from_hf("HuggingFaceTB/SmolLM2-135M")
            mock_hf.assert_not_called()
        assert result is True
        assert db.get_hashes("HuggingFaceTB/SmolLM2-135M") is not None

    def test_readme_with_valid_hash_lines_parses_correctly(self) -> None:
        db = ModelHashDB()
        readme_content = (
            "---\n"
            "library_name: transformers\n"
            "---\n"
            "\n"
            "# My Model\n"
            "- model.safetensors " + "a" * 64 + "\n"
            "- config.json " + "b" * 64 + "\n"
            "- tokenizer.json " + "c" * 64 + "\n"
        )
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme_content
                result = db.import_from_hf("unknown/but-has-readme")
        assert result is True
        files = db.get_hashes("unknown/but-has-readme")
        assert files is not None
        assert len(files) == 3

    def test_readme_with_no_list_items_returns_false(self) -> None:
        db = ModelHashDB()
        readme_content = "# Just a title\nNo list items here.\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme_content
                result = db.import_from_hf("unknown/no-hashes")
        assert result is False
        assert db.get_hashes("unknown/no-hashes") is None

    def test_readme_with_list_items_but_no_valid_sha_returns_false(self) -> None:
        db = ModelHashDB()
        readme_content = "- model.safetensors not-a-sha\n- config.json short\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme_content
                result = db.import_from_hf("unknown/no-valid-sha")
        assert result is False

    def test_readme_os_error_returns_false(self) -> None:
        db = ModelHashDB()
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True, side_effect=OSError("permission denied")):
                result = db.import_from_hf("unknown/os-error")
        assert result is False

    def test_readme_has_sha_with_filename_containing_spaces(self) -> None:
        db = ModelHashDB()
        sha = "d" * 64
        readme_content = f"- model with spaces.safetensors {sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme_content
                result = db.import_from_hf("unknown/space-model")
        assert result is True
        files = db.get_hashes("unknown/space-model")
        assert files is not None
        assert files[0].filename == "model with spaces.safetensors"

    def test_readme_has_non_hex_sha_rejected(self) -> None:
        db = ModelHashDB()
        g_hex_sha = "g" + "0" * 63
        readme_content = f"- bad.safetensors {g_hex_sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme_content
                result = db.import_from_hf("unknown/bad-sha")
        assert result is False

    def test_readme_hf_hub_download_exception_returns_false(self) -> None:
        db = ModelHashDB()
        with patch("huggingface_hub.hf_hub_download", side_effect=ConnectionError("no network")):
            result = db.import_from_hf("nonexistent/offline")
        assert result is False


# — edge-case and fuzzing tests —


class TestUnicodeFileHash:
    def test_filehash_unicode_filename_to_dict(self) -> None:
        fh = FileHash(filename="モデル.safetensors", sha256="a" * 64)
        d = fh.to_dict()
        assert d["filename"] == "モデル.safetensors"

    def test_filehash_unicode_filename_from_dict_roundtrip(self) -> None:
        original = FileHash(filename="über/tokenizer.json", sha256="b" * 64)
        restored = FileHash.from_dict(original.to_dict())
        assert restored == original
        assert restored.filename == "über/tokenizer.json"

    def test_json_persistence_with_unicode_model_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/über_model", [FileHash("файл.bin", "c" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/über_model")
            assert files is not None
            assert files[0].filename == "файл.bin"

    def test_verify_download_with_unicode_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"unicode test"
            sha = hashlib.sha256(content).hexdigest()
            fname = "テストデータ.bin"
            (Path(tmpdir) / fname).write_bytes(content)
            db = ModelHashDB()
            db.register_model("org/model", [FileHash(fname, sha)])
            db.verify_download("org/model", tmpdir)


class TestLongFilenames:
    def test_filehash_with_very_long_filename(self) -> None:
        fname = "x" * 500 + ".safetensors"
        assert len(fname) > 255
        fh = FileHash(filename=fname, sha256="d" * 64)
        assert fh.filename == fname
        assert len(fh.filename) == len(fname)

    def test_json_persistence_with_long_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            fname = "a" * 300 + ".bin"
            db.register_model("org/model", [FileHash(fname, "e" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/model")
            assert files is not None
            assert files[0].filename == fname

    def test_long_model_id_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            long_id = "org/" + "x" * 200
            db.register_model(long_id, [FileHash("f.bin", "a" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            assert db2.get_hashes(long_id) is not None


class TestSHA256CaseSensitivity:
    def test_verify_rejects_uppercase_sha_when_computed_is_lowercase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"case test"
            fpath = Path(tmpdir) / "model.bin"
            fpath.write_bytes(content)
            actual_lower = hashlib.sha256(content).hexdigest()
            uppercased = actual_lower.upper()
            assert uppercased != actual_lower
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("model.bin", uppercased)])
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))

    def test_filehash_stores_sha_as_is_no_normalization(self) -> None:
        fh = FileHash(filename="f.bin", sha256="A" * 64)
        assert fh.sha256 == "A" * 64
        assert fh.sha256 != "a" * 64

    def test_filehash_inequality_different_case(self) -> None:
        a = FileHash(filename="f.bin", sha256="A")
        b = FileHash(filename="f.bin", sha256="a")
        assert a != b


class TestEmptyFileVerification:
    def test_verify_empty_file_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "empty.bin"
            fpath.write_bytes(b"")
            sha_empty = hashlib.sha256(b"").hexdigest()
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("empty.bin", sha_empty)])
            db.verify_download("org/model", str(fpath))

    def test_verify_empty_file_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "empty.bin"
            fpath.write_bytes(b"")
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("empty.bin", "f" * 64)])
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))

    def test_empty_file_sha_known_constant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "e.bin"
            fpath.write_bytes(b"")
            assert _sha256_file(str(fpath)) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestOverlappingHashes:
    def test_same_file_same_hash_different_models(self) -> None:
        db = ModelHashDB()
        fh = FileHash("shared.bin", "a" * 64)
        db.register_model("org/model-a", [fh])
        db.register_model("org/model-b", [fh])
        assert "org/model-a" in db.list_models()
        assert "org/model-b" in db.list_models()

    def test_different_files_same_hash(self) -> None:
        db = ModelHashDB()
        db.register_model(
            "org/m",
            [
                FileHash("a.bin", "s" * 64),
                FileHash("b.bin", "s" * 64),
            ],
        )
        files = db.get_hashes("org/m")
        assert files is not None
        assert len(files) == 2
        assert files[0].sha256 == files[1].sha256

    def test_verify_same_hash_for_multiple_files_in_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"same"
            sha = hashlib.sha256(content).hexdigest()
            (Path(tmpdir) / "x.bin").write_bytes(content)
            (Path(tmpdir) / "y.bin").write_bytes(content)
            db = ModelHashDB()
            db.register_model(
                "org/model",
                [
                    FileHash("x.bin", sha),
                    FileHash("y.bin", sha),
                ],
            )
            db.verify_download("org/model", tmpdir)


class TestPermissionErrors:
    def test_persist_to_readonly_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "db.json"
            db_path.write_text("{}")
            db_path.chmod(0o444)
            db = ModelHashDB(db_path=str(db_path))
            with pytest.raises(PermissionError):
                db.register_model("org/model", [FileHash("f.bin", "a" * 64)])
            assert db_path.exists()  # file not deleted on persist failure

    def test_load_unreadable_then_writable_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "unreadable.json"
            db_path.write_text('{"org/a": [{"filename": "f.bin", "sha256": "a"}]}')
            db_path.chmod(0o000)
            try:
                with pytest.raises(PermissionError):
                    ModelHashDB(db_path=str(db_path))
            finally:
                db_path.chmod(0o644)
            db = ModelHashDB(db_path=str(db_path))
            models = db.list_models()
            assert set(models) == {"org/a"}

    def test_persist_to_nonexistent_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "newdir" / "sub" / "db.json"
            db = ModelHashDB(db_path=str(db_path))
            with pytest.raises(FileNotFoundError):
                db.register_model("org/model", [FileHash("f.bin", "a" * 64)])
            assert not db_path.exists()  # directory not auto-created


class TestFuzzingHashDB:
    def test_fuzz_random_bytes_roundtrip(self) -> None:
        import random

        random.seed(42)
        for _i in range(20):
            content = bytes(random.randint(0, 255) for _ in range(random.randint(1, 16384)))
            sha = hashlib.sha256(content).hexdigest()
            with tempfile.TemporaryDirectory() as tmpdir:
                fpath = Path(tmpdir) / "fuzz.bin"
                fpath.write_bytes(content)
                db = ModelHashDB()
                db.register_model("org/fuzz", [FileHash("fuzz.bin", sha)])
                db.verify_download("org/fuzz", str(fpath))
                should_fail_sha = hashlib.sha256(content + b"!").hexdigest()
                db2 = ModelHashDB()
                db2.register_model("org/fuzz", [FileHash("fuzz.bin", should_fail_sha)])
                with pytest.raises(ModelIntegrityError):
                    db2.verify_download("org/fuzz", str(fpath))

    def test_fuzz_model_ids(self) -> None:
        import random
        import string

        random.seed(99)
        db = ModelHashDB()
        for _i in range(50):
            chars = string.ascii_letters + string.digits + "/._-"
            model_id = "".join(random.choice(chars) for _ in range(random.randint(1, 120)))
            sha = hashlib.sha256(str(random.random()).encode()).hexdigest()
            db.register_model(model_id, [FileHash("f.bin", sha)])
            assert db.get_hashes(model_id) is not None
            db.remove_model(model_id)
            assert db.get_hashes(model_id) is None

    def test_fuzz_json_persistence_many_models(self) -> None:
        import random

        random.seed(7)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fuzz.json"
            db = ModelHashDB(db_path=str(db_path))
            registered: list[str] = []
            for i in range(40):
                model_id = f"org/model-{i:04d}"
                n_files = random.randint(1, 5)
                files = [FileHash(f"file_{j}.bin", hashlib.sha256(os.urandom(16)).hexdigest()) for j in range(n_files)]
                db.register_model(model_id, files)
                registered.append(model_id)
            db2 = ModelHashDB(db_path=str(db_path))
            for mid in registered:
                assert db2.get_hashes(mid) is not None

    def test_fuzz_sha256_boundary_hex_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"boundary"
            actual_sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "boundary.bin"
            fpath.write_bytes(content)
            all_zeros = "0" * 64
            all_f = "f" * 64
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("boundary.bin", actual_sha)])
            db.verify_download("org/model", str(fpath))
            assert fpath.exists()  # matching hash preserves file
            db.register_model("org/model", [FileHash("boundary.bin", all_zeros)])
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))
            assert not fpath.exists()  # mismatched file deleted
            fpath.write_bytes(content)
            db.register_model("org/model", [FileHash("boundary.bin", all_f)])
            with pytest.raises(ModelIntegrityError):
                db.verify_download("org/model", str(fpath))

    def test_fuzz_concurrent_registration(self) -> None:
        import threading

        errors: list[Exception] = []
        db = ModelHashDB()

        def register_batch(prefix: str) -> None:
            try:
                for i in range(30):
                    db.register_model(f"org/{prefix}-{i}", [FileHash("f.bin", "a" * 64)])
                for i in range(30):
                    _files = db.get_hashes(f"org/{prefix}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_batch, args=(f"thread-{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        all_models = db.list_models()
        for t in range(4):
            for i in range(30):
                assert f"org/thread-{t}-{i}" in all_models

    def test_fuzz_concurrent_verify_read_only(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"thread-verify-test-data"
            sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "shared.bin"
            fpath.write_bytes(content)
            db = ModelHashDB()
            db.register_model("org/model", [FileHash("shared.bin", sha)])
            errors: list[Exception] = []

            def verify() -> None:
                try:
                    for _ in range(50):
                        db.verify_download("org/model", str(fpath))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=verify) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors

    def test_fuzz_json_special_characters_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "special.json"
            db = ModelHashDB(db_path=str(db_path))
            special_ids = [
                "org/model with spaces",
                'org/"quoted"/model',
                "org/model\nnewline",
                "org/model\ttab",
                "org/model\\backslash",
                "org/model/slash",
                "org/😀/model",
            ]
            for mid in special_ids:
                db.register_model(mid, [FileHash("f.bin", "x" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            for mid in special_ids:
                assert db2.get_hashes(mid) is not None

    def test_fuzz_sha256_file_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            _sha256_file("/tmp/gludd-nonexistent-file-for-fuzz-test-12345.bin")

    def test_fuzz_sha256_file_large_random(self) -> None:
        import random

        random.seed(12345)
        size = 1024 * 1024 * 2
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "large_random.bin"
            with open(fpath, "wb") as f:
                remaining = size
                while remaining > 0:
                    chunk = bytes(random.randint(0, 255) for _ in range(min(65536, remaining)))
                    f.write(chunk)
                    remaining -= len(chunk)
            sha = _sha256_file(str(fpath))
            h = hashlib.sha256()
            with open(fpath, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            assert sha == h.hexdigest()

    def test_fuzz_filehash_to_dict_all_byte_values_in_filename(self) -> None:
        fname = "file_" + "".join(chr(b) for b in range(32, 127)) + ".bin"
        sha = "f" * 64
        fh = FileHash(filename=fname, sha256=sha)
        d = fh.to_dict()
        assert d["filename"] == fname
        assert d["sha256"] == sha

    def test_fuzz_filehash_very_long_sha256(self) -> None:
        sha = "0123456789abcdef" * 10
        assert len(sha) == 160
        fh = FileHash(filename="f.bin", sha256=sha)
        d = fh.to_dict()
        restored = FileHash.from_dict(d)
        assert restored.sha256 == sha


class TestLoadKnownModelsFromConfig:
    def test_loads_all_models_from_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "known_models.json"
            config_path.write_text(
                json.dumps(
                    {
                        "org/model-a": [
                            {"filename": "a.bin", "sha256": "a" * 64},
                        ],
                        "org/model-b": [
                            {"filename": "b.bin", "sha256": "b" * 64},
                            {"filename": "c.bin", "sha256": "c" * 64},
                        ],
                    }
                )
            )

            result = load_known_models_from_config(str(config_path))
            assert len(result) == 2
            assert "org/model-a" in result
            assert "org/model-b" in result
            files_a = result["org/model-a"]
            assert len(files_a) == 1
            assert files_a[0].filename == "a.bin"
            assert files_a[0].sha256 == "a" * 64
            files_b = result["org/model-b"]
            assert len(files_b) == 2

    def test_missing_file_returns_empty_dict(self) -> None:
        result = load_known_models_from_config("/nonexistent/path/known_models.json")
        assert result == {}

    def test_empty_json_file_returns_empty_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "empty.json"
            config_path.write_text("{}")
            result = load_known_models_from_config(str(config_path))
            assert result == {}

    def test_corrupt_json_file_returns_empty_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "corrupt.json"
            config_path.write_text("{not valid")
            with pytest.raises(json.JSONDecodeError):
                load_known_models_from_config(str(config_path))

    def test_respects_env_var_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom.json"
            config_path.write_text(
                json.dumps(
                    {
                        "org/custom": [{"filename": "x.bin", "sha256": "d" * 64}],
                    }
                )
            )
            with patch.dict(os.environ, {"GLUDD_KNOWN_MODELS_FILE": str(config_path)}):
                result = load_known_models_from_config()
            assert "org/custom" in result


class TestMergeKnownModels:
    def test_merges_multiple_sources(self) -> None:
        src_a = {
            "org/a": [FileHash("a.bin", "a" * 64)],
        }
        src_b = {
            "org/b": [FileHash("b.bin", "b" * 64)],
        }
        merged = merge_known_models(src_a, src_b)
        assert len(merged) == 2
        assert "org/a" in merged
        assert "org/b" in merged

    def test_later_source_overwrites_earlier(self) -> None:
        src_a = {
            "org/model": [FileHash("v1.bin", "1" * 64)],
        }
        src_b = {
            "org/model": [FileHash("v2.bin", "2" * 64)],
        }
        merged = merge_known_models(src_a, src_b)
        assert len(merged) == 1
        files = merged["org/model"]
        assert files[0].filename == "v2.bin"

    def test_empty_sources_yields_empty(self) -> None:
        merged = merge_known_models()
        assert merged == {}

    def test_does_not_mutate_sources(self) -> None:
        src = {"org/model": [FileHash("f.bin", "a" * 64)]}
        copy_src = dict(src)
        merge_known_models(src)
        assert src == copy_src
