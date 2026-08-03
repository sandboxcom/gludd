"""Unit tests for ModelHashDB, KnownModels, FileHash, and ModelIntegrityError."""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from general_ludd.small_models.model_hash_db import (
    FileHash,
    KnownModels,
    ModelHashDB,
    ModelIntegrityError,
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

    def test_download_skips_hash_verify_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from general_ludd.small_models.download import ModelDownloader

            dl = ModelDownloader(cache_dir=tmpdir)
            dl._hash_db = ModelHashDB()

            with patch.object(dl, "download_huggingface") as mock_dl:
                mock_dl.return_value.local_path = tmpdir
                result = dl.download("org/model", verify_hash=False)
            assert result.model_id == "org/model"
