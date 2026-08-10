from __future__ import annotations

from dataclasses import fields
from typing import ClassVar

import pytest

from general_ludd.local_model import (
    LocalModelConfig,
    get_model,
    list_models,
)
from general_ludd.local_model._local_model_configs import _LOCAL_MODELS


class TestListModels:
    def test_returns_all_configured_models(self):
        result = list_models()
        assert len(result) == len(_LOCAL_MODELS)
        assert len(result) > 0

    def test_every_item_is_local_model_config(self):
        result = list_models()
        for model in result:
            assert isinstance(model, LocalModelConfig)

    def test_filter_by_category_coding(self):
        result = list_models(category="coding")
        assert len(result) > 0
        for model in result:
            assert model.category == "coding"

    def test_filter_by_category_general(self):
        result = list_models(category="general")
        assert len(result) > 0
        for model in result:
            assert model.category == "general"

    def test_filter_ci_safe_only(self):
        result = list_models(ci_safe_only=True)
        assert len(result) > 0
        for model in result:
            assert model.ci_safe is True

    def test_ci_safe_subset_of_all(self):
        all_models = list_models()
        ci_safe = list_models(ci_safe_only=True)
        assert len(ci_safe) <= len(all_models)
        assert all(m.ci_safe for m in ci_safe)

    def test_combined_filters(self):
        result = list_models(category="general", ci_safe_only=True)
        for model in result:
            assert model.category == "general"
            assert model.ci_safe is True


class TestGetModel:
    def test_resolves_by_exact_name(self):
        cfg = get_model("phi-2")
        assert cfg is not None
        assert cfg.name == "phi-2"
        assert isinstance(cfg, LocalModelConfig)

    def test_resolves_by_alias(self):
        cfg = get_model("phi2")
        assert cfg is not None
        assert cfg.name == "phi-2"

    def test_resolves_by_ollama_tag(self):
        cfg = get_model("phi:2.7b")
        assert cfg is not None
        assert cfg.name == "phi-2"

    def test_resolves_by_module_identifier_name(self):
        cfg = get_model("phi_2")
        assert cfg is not None
        assert cfg.name == "phi-2"

    def test_all_names_directly_resolvable(self):
        for expected in _LOCAL_MODELS:
            cfg = get_model(expected.name)
            assert cfg is not None, f"get_model({expected.name!r}) returned None"
            assert cfg.name == expected.name

    def test_all_ollama_tags_resolvable_when_present(self):
        for expected in _LOCAL_MODELS:
            if expected.ollama_tag:
                cfg = get_model(expected.ollama_tag)
                assert cfg is not None, f"get_model({expected.ollama_tag!r}) returned None"
                assert cfg.name == expected.name

    def test_missing_model_returns_none(self):
        cfg = get_model("nonexistent-model-xyz")
        assert cfg is None


class TestModelFields:
    REQUIRED_FIELDS: ClassVar[set[str]] = {
        "name",
        "repo",
        "filename",
        "context_size",
        "huggingface_url",
        "ollama_tag",
        "quant_level",
        "size_mb",
        "category",
        "ci_safe",
        "aliases",
    }

    def test_all_fields_present_on_returned_models(self):
        result = list_models()
        for model in result:
            missing = self.REQUIRED_FIELDS - {f.name for f in fields(LocalModelConfig)}
            assert not missing, f"Model {model.name} missing fields: {missing}"

    def test_name_is_non_empty_string(self):
        for model in list_models():
            assert isinstance(model.name, str)
            assert model.name

    def test_repo_is_non_empty_string(self):
        for model in list_models():
            assert isinstance(model.repo, str)
            assert model.repo

    def test_filename_is_non_empty_string(self):
        for model in list_models():
            assert isinstance(model.filename, str)
            assert model.filename

    def test_context_size_positive_int(self):
        for model in list_models():
            assert isinstance(model.context_size, int)
            assert model.context_size > 0

    def test_huggingface_url_is_set(self):
        for model in list_models():
            assert isinstance(model.huggingface_url, str)
            assert model.huggingface_url.startswith("https://huggingface.co/")

    def test_size_mb_is_non_negative_int(self):
        for model in list_models():
            assert isinstance(model.size_mb, int)
            assert model.size_mb >= 0

    def test_category_is_coding_or_general(self):
        for model in list_models():
            assert model.category in ("coding", "general")

    def test_ci_safe_is_boolean(self):
        for model in list_models():
            assert isinstance(model.ci_safe, bool)

    def test_aliases_is_tuple_of_strings(self):
        for model in list_models():
            assert isinstance(model.aliases, tuple)
            for alias in model.aliases:
                assert isinstance(alias, str)

    def test_quant_level_non_empty(self):
        for model in list_models():
            assert isinstance(model.quant_level, str)
            assert model.quant_level

    def test_get_model_returns_identical_object_for_same_name(self):
        cfg1 = get_model("phi-2")
        cfg2 = get_model("phi-2")
        assert cfg1 is cfg2

    def test_frozen_dataclass_cannot_be_mutated(self):
        cfg = get_model("phi-2")
        assert cfg is not None
        with pytest.raises(AttributeError):
            cfg.name = "modified"  # type: ignore[misc]

    def test_count_matches_config(self):
        assert len(list_models()) == 24

    def test_coding_count_matches_config(self):
        assert len(list_models(category="coding")) == 8

    def test_general_count_matches_config(self):
        assert len(list_models(category="general")) == 16
