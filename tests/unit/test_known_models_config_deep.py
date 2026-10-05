import json
import os
import tempfile

from general_ludd.small_models.model_hash_db import (
    FileHash,
    KnownModels,
    load_known_models_from_config,
    merge_known_models,
)


def _config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "..", "config", "known_models.json")


class TestConfigLoads:
    def test_config_file_exists(self):
        path = _config_path()
        assert os.path.isfile(path), f"Missing config at {path}"

    def test_config_is_valid_json(self):
        with open(_config_path()) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_load_known_models_returns_dict(self):
        result = load_known_models_from_config(_config_path())
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_load_models_are_filehash_instances(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            assert isinstance(model_id, str)
            assert isinstance(files, list)
            for fh in files:
                assert isinstance(fh, FileHash)

    def test_missing_file_returns_empty_dict(self):
        result = load_known_models_from_config("/nonexistent/path/models.json")
        assert result == {}


class TestRequiredFields:
    def test_all_entries_have_filename(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            for fh in files:
                assert fh.filename, f"Missing filename in {model_id}"
                assert isinstance(fh.filename, str)

    def test_all_entries_have_sha256(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            for fh in files:
                assert fh.sha256, f"Missing sha256 in {model_id}/{fh.filename}"
                assert isinstance(fh.sha256, str)

    def test_every_entry_has_exactly_two_fields(self):
        with open(_config_path()) as f:
            data = json.load(f)
        for model_id, files_list in data.items():
            for entry in files_list:
                assert set(entry.keys()) == {"filename", "sha256"}, (
                    f"Unexpected keys in {model_id}: {set(entry.keys())}"
                )


class TestHashValidity:
    def test_all_sha256_are_64_char_lowercase_hex(self):
        result = load_known_models_from_config(_config_path())
        hex_chars = set("0123456789abcdef")
        for model_id, files in result.items():
            for fh in files:
                assert len(fh.sha256) == 64, f"sha256 length {len(fh.sha256)} != 64 in {model_id}/{fh.filename}"
                assert set(fh.sha256) <= hex_chars, f"Non-hex sha256 in {model_id}/{fh.filename}: {fh.sha256}"

    def test_no_empty_sha256(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            for fh in files:
                assert fh.sha256 != "", f"Empty sha256 in {model_id}/{fh.filename}"

    def test_no_duplicate_sha256_within_same_model(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            hashes = [fh.sha256 for fh in files]
            assert len(hashes) == len(set(hashes)), f"Duplicate sha256 within {model_id}"


class TestNoDuplicateModelIDs:
    def test_no_duplicate_model_ids(self):
        with open(_config_path()) as f:
            data = json.load(f)
        model_ids = list(data.keys())
        assert len(model_ids) == len(set(model_ids)), "Duplicate model IDs found"


class TestFileIntegrity:
    def test_every_model_has_at_least_one_file(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            assert len(files) >= 1, f"Model {model_id} has no files"

    def test_no_duplicate_filenames_within_model(self):
        result = load_known_models_from_config(_config_path())
        for model_id, files in result.items():
            names = [fh.filename for fh in files]
            assert len(names) == len(set(names)), f"Duplicate filenames in {model_id}: {names}"

    def test_known_models_have_expected_members(self):
        result = load_known_models_from_config(_config_path())
        expected = {
            "HuggingFaceTB/SmolLM2-135M",
            "Qwen/Qwen2.5-0.5B",
            "Qwen/Qwen2.5-0.5B-GGUF",
            "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "microsoft/phi-2",
            "deepseek-ai/DeepSeek-Coder-1.3B",
            "meta-llama/Llama-3.2-1B",
            "microsoft/Phi-3-mini-4k-instruct",
        }
        assert set(result.keys()) == expected

    def test_config_consistent_with_builtin_knownmodels(self):
        config_result = load_known_models_from_config(_config_path())
        builtin = KnownModels.all()
        assert set(config_result.keys()) <= set(builtin.keys()), (
            f"Config has model IDs not in KnownModels: {set(config_result.keys()) - set(builtin.keys())}"
        )


class TestEnvVarOverride:
    def test_env_var_overrides_default_path(self, monkeypatch):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write(json.dumps({"test/model": [{"filename": "f.bin", "sha256": "a" * 64}]}))
            tmp_path = tf.name
        try:
            monkeypatch.setenv("GLUDD_KNOWN_MODELS_FILE", tmp_path)
            result = load_known_models_from_config()
            assert "test/model" in result
        finally:
            os.unlink(tmp_path)

    def test_explicit_path_bypasses_env_var(self, monkeypatch):
        monkeypatch.setenv("GLUDD_KNOWN_MODELS_FILE", "/nonexistent/path.json")
        result = load_known_models_from_config(_config_path())
        assert len(result) > 0


class TestMergeKnownModels:
    def test_merge_single_source_is_identity(self):
        source = {"a": [FileHash("f1.txt", "a" * 64)]}
        assert merge_known_models(source) == source

    def test_merge_two_disjoint_sources(self):
        a = {"A": [FileHash("f1.txt", "a" * 64)]}
        b = {"B": [FileHash("f2.txt", "b" * 64)]}
        merged = merge_known_models(a, b)
        assert set(merged.keys()) == {"A", "B"}

    def test_merge_later_wins_on_same_key(self):
        a = {"M": [FileHash("old.txt", "0" * 64)]}
        b = {"M": [FileHash("new.txt", "f" * 64)]}
        merged = merge_known_models(a, b)
        assert merged["M"][0].filename == "new.txt"

    def test_merge_empty_sources_returns_empty(self):
        assert merge_known_models() == {}
        assert merge_known_models({}, {}) == {}

    def test_merge_leaves_originals_unmodified(self):
        a = {"M": [FileHash("orig.txt", "c" * 64)]}
        b = {"N": [FileHash("other.txt", "d" * 64)]}
        merged = merge_known_models(a, b)
        merged["M"] = [FileHash("mutated.txt", "e" * 64)]
        assert a["M"][0].filename == "orig.txt"


class TestFileHashRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        fh = FileHash("model.safetensors", "a" * 64)
        assert FileHash.from_dict(fh.to_dict()) == fh
