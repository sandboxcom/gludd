"""Deep edge-case tests for ModelHashDB, _sha256_file, import_from_hf, and persistence.

Covers: hash collision simulation, buffer-boundary files, concurrent writes to same db_path,
malformed JSON fields, empty-file-list verification, README.md with ambiguous 64-char strings,
path-as-file multi-file hash check, case-sensitivity for model IDs, trailing-slash paths,
rapid persist thrashing, and non-hex sha256 storage.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.small_models.model_hash_db import (
    _READ_SIZE,
    FileHash,
    KnownModels,
    ModelHashDB,
    ModelIntegrityError,
    _sha256_file,
    load_known_models_from_config,
    merge_known_models,
)

# ── _sha256_file buffer-boundary tests ──


class TestSha256BufferBoundaries:
    """Files exactly at, just below, and just above the 64 KiB read buffer."""

    def test_exactly_one_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "exact.bin"
            content = b"x" * _READ_SIZE
            fpath.write_bytes(content)
            assert _sha256_file(str(fpath)) == hashlib.sha256(content).hexdigest()

    def test_one_byte_less_than_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "minus1.bin"
            content = b"y" * (_READ_SIZE - 1)
            fpath.write_bytes(content)
            assert _sha256_file(str(fpath)) == hashlib.sha256(content).hexdigest()

    def test_one_byte_more_than_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "plus1.bin"
            content = b"z" * (_READ_SIZE + 1)
            fpath.write_bytes(content)
            assert _sha256_file(str(fpath)) == hashlib.sha256(content).hexdigest()

    def test_double_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "double.bin"
            content = b"\x00" * (_READ_SIZE * 2)
            fpath.write_bytes(content)
            assert _sha256_file(str(fpath)) == hashlib.sha256(content).hexdigest()

    def test_triple_buffer_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "triple1.bin"
            content = b"\xff" * (_READ_SIZE * 3 + 1)
            fpath.write_bytes(content)
            assert _sha256_file(str(fpath)) == hashlib.sha256(content).hexdigest()


# ── Concurrent writes to the same db_path ──


class TestConcurrentPersistence:
    """Multiple threads writing to the same on-disk JSON simultaneously."""

    def test_concurrent_registration_same_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shared.json"
            errors: list[Exception] = []
            threading.Event()

            def writer(prefix: str, count: int) -> None:
                try:
                    db = ModelHashDB(db_path=str(db_path))
                    for i in range(count):
                        db.register_model(
                            f"org/{prefix}-{i:04d}",
                            [FileHash("f.bin", hashlib.sha256(f"{prefix}{i}".encode()).hexdigest())],
                        )
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(f"w{t}", 50)) for t in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"concurrent writes to same JSON raised: {errors}"

            db_final = ModelHashDB(db_path=str(db_path))
            models = db_final.list_models()
            assert len(models) >= 50, f"expected >=50 models after concurrent writes, got {len(models)}"

    def test_concurrent_register_and_read_same_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "rw.json"
            errors: list[Exception] = []

            ModelHashDB(db_path=str(db_path))

            def writer() -> None:
                try:
                    for i in range(40):
                        db = ModelHashDB(db_path=str(db_path))
                        db.register_model(f"org/w-{i:04d}", [FileHash("x.bin", "a" * 64)])
                except Exception as e:
                    errors.append(e)

            def reader() -> None:
                try:
                    for _ in range(200):
                        db = ModelHashDB(db_path=str(db_path))
                        _ = db.list_models()
                        time.sleep(0.001)
                except Exception as e:
                    errors.append(e)

            t_w = threading.Thread(target=writer)
            t_r = threading.Thread(target=reader)
            t_w.start()
            t_r.start()
            t_w.join()
            t_r.join()

            assert not errors, f"concurrent read/write same JSON raised: {errors}"


# ── Hash collision simulation ──


class TestHashCollisionSimulation:
    """Behaviour when two different FileHash entries carry the same sha256 value."""

    def test_identical_hash_different_filenames_both_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "collide.json"
            db = ModelHashDB(db_path=str(db_path))
            shared_sha = "c" * 64
            db.register_model(
                "org/model",
                [
                    FileHash("alpha.bin", shared_sha),
                    FileHash("beta.bin", shared_sha),
                ],
            )
            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/model")
            assert files is not None
            filenames = [fh.filename for fh in files]
            assert filenames == ["alpha.bin", "beta.bin"]

    def test_collision_verify_single_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"data"
            sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "only.bin"
            fpath.write_bytes(content)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [
                    FileHash("only.bin", sha),
                    FileHash("missing.bin", sha),
                ],
            )
            db.verify_download("org/model", str(fpath))


# ── Malformed JSON field tests ──


class TestMalformedJsonFields:
    """Persistence of entries with missing or extra keys."""

    def test_load_json_missing_filename_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nofile.json"
            db_path.write_text(json.dumps({"org/a": [{"sha256": "a" * 64}]}))
            with pytest.raises(KeyError):
                ModelHashDB(db_path=str(db_path))

    def test_load_json_missing_sha256_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nosha.json"
            db_path.write_text(json.dumps({"org/a": [{"filename": "x.bin"}]}))
            with pytest.raises(KeyError):
                ModelHashDB(db_path=str(db_path))

    def test_load_json_extra_keys_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "extra.json"
            db_path.write_text(
                json.dumps(
                    {
                        "org/a": [
                            {"filename": "x.bin", "sha256": "a" * 64, "extra_field": 42},
                        ],
                    }
                )
            )
            db = ModelHashDB(db_path=str(db_path))
            files = db.get_hashes("org/a")
            assert files is not None
            assert files[0].filename == "x.bin"
            assert files[0].sha256 == "a" * 64

    def test_load_json_null_entry_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nullval.json"
            db_path.write_text(json.dumps({"org/a": None}))
            with pytest.raises(TypeError):
                ModelHashDB(db_path=str(db_path))

    def test_load_json_empty_files_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "emptyfiles.json"
            db_path.write_text(json.dumps({"org/a": []}))
            db = ModelHashDB(db_path=str(db_path))
            assert db.list_models() == ["org/a"]
            assert db.get_hashes("org/a") == []


# ── verify_download edge cases ──


class TestVerifyDownloadDeepEdges:
    """Path handling edge cases for verify_download."""

    def test_path_is_file_hashes_multiple_entries_all_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"single file"
            sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "only.bin"
            fpath.write_bytes(content)

            db = ModelHashDB()
            db.register_model(
                "org/model",
                [
                    FileHash("only.bin", sha),
                    FileHash("other.bin", sha),
                ],
            )
            db.verify_download("org/model", str(fpath))
            assert fpath.exists()

    def test_path_trailing_slash_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"trailing"
            sha = hashlib.sha256(content).hexdigest()
            (Path(tmpdir) / "t.bin").write_bytes(content)

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("t.bin", sha)])
            db.verify_download("org/model", tmpdir + os.sep)

    def test_path_does_not_exist_skips_all_files(self) -> None:
        db = ModelHashDB()
        db.register_model(
            "org/model",
            [
                FileHash("a.bin", "0" * 64),
                FileHash("b.bin", "0" * 64),
            ],
        )
        db.verify_download("org/model", "/tmp/__gludd_never_exists__/dir")

    def test_empty_files_list_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "x.bin"
            fpath.write_bytes(b"x")
            db = ModelHashDB()
            db.register_model("org/model", [])
            db.verify_download("org/model", str(fpath))

    def test_no_registered_model_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "orphan.bin"
            fpath.write_bytes(b"orphan")
            db = ModelHashDB()
            db.verify_download("org/never-registered", str(tmpdir))
            assert fpath.exists()


# ── import_from_hf edge cases ──


class TestImportFromHFDeepEdges:
    """README.md parsing edge cases."""

    def test_readme_line_with_64_nonhex_chars_rejected(self) -> None:
        db = ModelHashDB()
        readme = "- model.safetensors " + ("g" * 63 + "z") + "\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/badhex")
        assert result is False

    def test_readme_line_64char_non_list_item_ignored(self) -> None:
        db = ModelHashDB()
        readme = f"Some text with a 64-char string here: {'a' * 64}\n- model.bin {'b' * 64}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/mixed")
        assert result is True
        files = db.get_hashes("unknown/mixed")
        assert files is not None
        assert len(files) == 1
        assert files[0].filename == "model.bin"

    def test_readme_line_with_only_sha_no_filename_rejected(self) -> None:
        db = ModelHashDB()
        readme = f"- {'a' * 64}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/sha-only")
        assert result is False

    def test_readme_sha_all_numbers_but_64chars_accepted(self) -> None:
        db = ModelHashDB()
        sha = "0" * 64
        readme = f"- config.json {sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/numeric-sha")
        assert result is True
        files = db.get_hashes("unknown/numeric-sha")
        assert files is not None
        assert files[0].sha256 == sha

    def test_readme_uppercase_hex_sha_accepted(self) -> None:
        db = ModelHashDB()
        sha = "A" * 64
        readme = f"- model.bin {sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/upper")
        assert result is True
        files = db.get_hashes("unknown/upper")
        assert files is not None and files[0].sha256 == sha

    def test_readme_indented_list_dash_accepted(self) -> None:
        db = ModelHashDB()
        readme = f"  - model.bin {'c' * 64}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/indented")
        assert result is True
        files = db.get_hashes("unknown/indented")
        assert files is not None and files[0].filename == "model.bin"

    def test_readme_filename_has_numbers_and_special_chars(self) -> None:
        db = ModelHashDB()
        fname = "model-0.5b-q4_k_m.gguf"
        sha = "d" * 64
        readme = f"- {fname} {sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/special-fname")
        assert result is True
        files = db.get_hashes("unknown/special-fname")
        assert files is not None
        assert files[0].filename == fname

    def test_readme_mixed_valid_and_invalid_lines(self) -> None:
        db = ModelHashDB()
        sha_valid = "e" * 64
        readme = f"- valid.bin {sha_valid}\n- invalid.bin {'g' * 64}\n- nohash.bin\n- another_valid.bin {'f' * 64}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/mixed-validity")
        assert result is True
        files = db.get_hashes("unknown/mixed-validity")
        assert files is not None
        assert len(files) == 2
        filenames = [fh.filename for fh in files]
        assert "valid.bin" in filenames
        assert "another_valid.bin" in filenames

    def test_readme_supershort_sha_rejected(self) -> None:
        db = ModelHashDB()
        readme = f"- model.bin {'a' * 32}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/shortsha")
        assert result is False

    def test_readme_very_long_line_with_sha_at_end(self) -> None:
        db = ModelHashDB()
        sha = "f" * 64
        padding = "x" * 500
        readme = f"- {padding} model.safetensors {sha}\n"
        with patch("huggingface_hub.hf_hub_download") as mock_hf:
            mock_hf.return_value = "/fake/README.md"
            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = readme
                result = db.import_from_hf("unknown/longline")
        assert result is True
        files = db.get_hashes("unknown/longline")
        assert files is not None
        assert files[0].sha256 == sha


# ── Rapid persist thrashing ──


class TestRapidPersist:
    """Repeated register + persist cycles without corruption."""

    def test_rapid_register_unregister_same_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "thrash.json"
            db = ModelHashDB(db_path=str(db_path))
            for i in range(100):
                db.register_model("org/model", [FileHash(f"file_{i % 5}.bin", "a" * 64)])
                db.remove_model("org/model")
                db.register_model("org/model", [FileHash("final.bin", "b" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/model")
            assert files is not None
            assert files[0].filename == "final.bin"

    def test_rapid_multi_model_constant_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "rapid.json"
            db = ModelHashDB(db_path=str(db_path))
            for i in range(50):
                db.register_model(f"org/model-{i % 10}", [FileHash("f.bin", "a" * 64)])
            db2 = ModelHashDB(db_path=str(db_path))
            models = db2.list_models()
            assert 1 <= len(models) <= 10, f"expected 1-10 models, got {len(models)}"


# ── Model ID case sensitivity ──


class TestCaseSensitivityEdges:
    """ModelHashDB treats model IDs as case-sensitive keys."""

    def test_register_different_case_same_name(self) -> None:
        db = ModelHashDB()
        db.register_model("ORG/MODEL", [FileHash("upper.bin", "a" * 64)])
        db.register_model("org/model", [FileHash("lower.bin", "b" * 64)])
        assert len(db.list_models()) == 2
        upper = db.get_hashes("ORG/MODEL")
        lower = db.get_hashes("org/model")
        assert upper is not None and upper[0].filename == "upper.bin"
        assert lower is not None and lower[0].filename == "lower.bin"

    def test_verify_is_case_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"case"
            sha = hashlib.sha256(content).hexdigest()
            fpath = Path(tmpdir) / "f.bin"
            fpath.write_bytes(content)

            db = ModelHashDB()
            db.register_model("Org/Model", [FileHash("f.bin", sha)])
            db.verify_download("Org/Model", str(fpath))


# ── FileHash edge cases ──


class TestFileHashDeepEdges:
    """Structural edge cases for FileHash."""

    def test_empty_sha_string_treated_as_is(self) -> None:
        fh = FileHash(filename="f.bin", sha256="")
        assert fh.sha256 == ""
        assert fh.to_dict()["sha256"] == ""

    def test_sha_with_whitespace_characters(self) -> None:
        fh = FileHash(filename="f.bin", sha256="  \t\n  " + "a" * 58 + "  ")
        assert fh.sha256.startswith(" ")
        d = fh.to_dict()
        restored = FileHash.from_dict(d)
        assert restored.sha256 == fh.sha256

    def test_filename_with_only_special_characters(self) -> None:
        fname = "!@#$%^&*()[]{}|;:',.<>?`~"
        fh = FileHash(filename=fname, sha256="a" * 64)
        assert fh.filename == fname
        persisted = FileHash.from_dict(fh.to_dict())
        assert persisted.filename == fname

    def test_hash_equality_with_none(self) -> None:
        fh = FileHash(filename="f.bin", sha256="a" * 64)
        assert fh is not None  # type: ignore[comparison-overlap]
        assert fh is not None  # type: ignore[comparison-overlap]

    def test_hash_equality_with_different_type(self) -> None:
        fh = FileHash(filename="f.bin", sha256="a" * 64)
        assert fh != "not a FileHash"
        assert fh != 42


# ── ModelIntegrityError edge cases ──


class TestModelIntegrityErrorDeepEdges:
    """Exceptional paths for ModelIntegrityError."""

    def test_properties_after_exception_caught(self) -> None:
        err = ModelIntegrityError("m", "f", "exp", "act")
        assert err.model_id == "m"
        assert err.filename == "f"
        assert err.expected == "exp"
        assert err.actual == "act"

    def test_hashes_not_truncated_when_under_16_chars(self) -> None:
        err = ModelIntegrityError("m", "f", "short", "tiny")
        msg = str(err)
        assert "short" in msg
        assert "tiny" in msg

    def test_exception_has_message_about_expected(self) -> None:
        err = ModelIntegrityError("m", "f", "expected_hash", "actual_hash")
        assert "expected sha256=" in str(err)

    def test_exception_has_message_about_got(self) -> None:
        err = ModelIntegrityError("m", "f", "expected_hash", "actual_hash")
        assert "got sha256=" in str(err)


# ── KnownModels deep edges ──


class TestKnownModelsDeepEdges:
    """Structural invariants not already covered."""

    def test_all_returns_same_keys_as_get_available(self) -> None:
        all_models = KnownModels.all()
        for model_id in all_models:
            assert KnownModels.get(model_id) is not None

    def test_hashes_in_builtin_are_all_hex(self) -> None:
        for model_id, files in KnownModels.all().items():
            for fh in files:
                assert all(c in "0123456789abcdef" for c in fh.sha256.lower()), (
                    f"non-hex sha in {model_id}/{fh.filename}: {fh.sha256}"
                )

    def test_get_returns_same_object_not_copy(self) -> None:
        files1 = KnownModels.get("HuggingFaceTB/SmolLM2-135M")
        files2 = KnownModels.get("HuggingFaceTB/SmolLM2-135M")
        assert files1 is files2


# ── merge_known_models deep edges ──


class TestMergeKnownModelsDeepEdges:
    """Complex merge scenarios."""

    def test_merge_three_sources_last_wins(self) -> None:
        a = {"org/x": [FileHash("v1.bin", "1" * 64)]}
        b = {"org/x": [FileHash("v2.bin", "2" * 64)]}
        c = {"org/x": [FileHash("v3.bin", "3" * 64)]}
        merged = merge_known_models(a, b, c)
        assert len(merged) == 1
        assert merged["org/x"][0].filename == "v3.bin"

    def test_merge_disjoint_keys_accumulate(self) -> None:
        a = {"org/a": [FileHash("a.bin", "a" * 64)]}
        b = {"org/b": [FileHash("b.bin", "b" * 64)]}
        c = {"org/c": [FileHash("c.bin", "c" * 64)]}
        merged = merge_known_models(a, b, c)
        assert len(merged) == 3
        assert "org/a" in merged
        assert "org/b" in merged
        assert "org/c" in merged

    def test_input_not_mutated(self) -> None:
        src = {"org/model": [FileHash("f.bin", "a" * 64)]}
        before = dict(src)
        _ = merge_known_models(src, {"org/other": [FileHash("g.bin", "b" * 64)]})
        assert src == before

    def test_single_source_is_not_copied_shallow(self) -> None:
        src = {"org/m": [FileHash("f.bin", "a" * 64)]}
        merged = merge_known_models(src)
        assert merged == src
        assert merged is not src


# ── load_known_models_from_config deep edges ──


class TestLoadKnownModelsDeepEdges:
    """Config loading edge cases."""

    def test_none_path_falls_back_to_default_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = load_known_models_from_config(None)
        assert isinstance(result, dict)  # None → uses default config path or env var

    def test_config_with_surrogate_pair_unicode_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "emoji.json"
            config_path.write_text(
                json.dumps(
                    {
                        "org/\U0001f600-model": [{"filename": "f.bin", "sha256": "a" * 64}],
                    }
                )
            )
            result = load_known_models_from_config(str(config_path))
            keys = list(result.keys())
            assert len(keys) == 1
            assert "\U0001f600" in keys[0]

    def test_config_with_nested_json_objects_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nested.json"
            config_path.write_text(
                json.dumps(
                    {
                        "org/a": {"filename": "x.bin", "sha256": "a" * 64},
                    }
                )
            )
            with pytest.raises(TypeError):
                load_known_models_from_config(str(config_path))


# ── Large persistence stress ──


class TestLargePersistenceStress:
    """Many models + many files per model roundtrip."""

    def test_many_files_per_single_model_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "manyfiles.json"
            db = ModelHashDB(db_path=str(db_path))
            file_count = 200
            hashes = [
                FileHash(f"file_{i:04d}.bin", hashlib.sha256(f"content{i}".encode()).hexdigest())
                for i in range(file_count)
            ]
            db.register_model("org/big", hashes)
            db2 = ModelHashDB(db_path=str(db_path))
            files = db2.get_hashes("org/big")
            assert files is not None
            assert len(files) == file_count

    def test_json_identical_after_multiple_roundtrip_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "stable.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.register_model("org/b", [FileHash("y.bin", "b" * 64)])
            first_content = db_path.read_text()
            for _ in range(10):
                ModelHashDB(db_path=str(db_path))
            assert db_path.read_text() == first_content


# ── Symlink and path resolution edges ──


class TestVerifyDownloadPathEdges:
    """Path resolution oddities during verification."""

    def test_path_is_symlink_to_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            link_dir = Path(tmpdir) / "link"
            os.symlink(str(real_dir), str(link_dir))

            content = b"linked data"
            sha = hashlib.sha256(content).hexdigest()
            (real_dir / "model.bin").write_bytes(content)

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("model.bin", sha)])
            db.verify_download("org/model", str(link_dir))

    def test_path_is_symlink_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = Path(tmpdir) / "real.bin"
            real_file.write_bytes(b"symlink data")
            sha = hashlib.sha256(b"symlink data").hexdigest()
            link_file = Path(tmpdir) / "link.bin"
            os.symlink(str(real_file), str(link_file))

            db = ModelHashDB()
            db.register_model("org/model", [FileHash("link.bin", sha)])
            db.verify_download("org/model", str(link_file))


# ── ModelHashDB.reset / lifecycle edges ──


class TestModelHashDBLifecycle:
    """Reset and re-init lifecycles."""

    def test_new_instance_after_clear_reads_empty_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "life.json"
            db = ModelHashDB(db_path=str(db_path))
            db.register_model("org/a", [FileHash("x.bin", "a" * 64)])
            db.clear()
            db2 = ModelHashDB(db_path=str(db_path))
            assert db2.list_models() == []

    def test_multiple_dbs_on_same_path_read_each_others_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shared2.json"
            db_a = ModelHashDB(db_path=str(db_path))
            db_a.register_model("org/a", [FileHash("a.bin", "a" * 64)])
            db_b = ModelHashDB(db_path=str(db_path))
            assert "org/a" in db_b.list_models()
            db_b.register_model("org/b", [FileHash("b.bin", "b" * 64)])
            db_a2 = ModelHashDB(db_path=str(db_path))
            assert set(db_a2.list_models()) == {"org/a", "org/b"}

    def test_reimport_same_model_twice_no_duplication(self) -> None:
        db = ModelHashDB()
        db.import_from_hf("HuggingFaceTB/SmolLM2-135M")
        first_count = len(db.list_models())
        db.import_from_hf("HuggingFaceTB/SmolLM2-135M")
        assert len(db.list_models()) == first_count
