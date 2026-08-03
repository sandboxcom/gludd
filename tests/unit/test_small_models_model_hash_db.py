"""Unit tests for ModelHashDB, KnownModels, FileHash, and ModelIntegrityError."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.model_hash_db import (
    FileHash,
    KnownModels,
    ModelHashDB,
    ModelIntegrityError,
    _sha256_file,
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
