"""Structural test for the expanded E2E model config registry."""

from __future__ import annotations

import pytest

from general_ludd.local_model._local_model_configs import (
    LocalModelConfig,
    get_e2e_models,
)
from tests.e2e._local_model_configs import (
    _MODELS,
    LOCAL_GGUF_MODELS,
    category_counts,
    get_all_configs,
    get_e2e_configs,
    get_models_by_role,
    list_models,
    model_count,
    select_models,
)


class TestModelRegistry:
    def test_runtime_registry_filter_and_quant_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("E2E_LOCAL_MODEL", "qwen-0.5b")
        assert [model.name for model in get_e2e_models()] == ["qwen-0.5b"]

        detected = LocalModelConfig(
            name="detected",
            repo="owner/model",
            filename="model-Q6_K.gguf",
            quant_level="",
        )
        assert detected.huggingface_url == "https://huggingface.co/owner/model"
        assert detected.quant_level == "Q6_K"

        unmatched = LocalModelConfig(
            name="unmatched",
            repo="",
            filename="model.gguf",
            quant_level="",
        )
        assert unmatched.huggingface_url == ""
        assert unmatched.quant_level == ""

    def test_runtime_registry_returns_copy_without_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("E2E_LOCAL_MODEL", raising=False)
        first = get_e2e_models()
        second = get_e2e_models()
        assert first == second
        assert first is not second

    def test_model_count_at_least_20(self) -> None:
        assert model_count() >= 20

    def test_category_counts(self) -> None:
        cc = category_counts()
        assert cc["total"] >= 20
        assert cc["coding"] >= 6
        assert cc["general"] >= 12
        assert cc["ci_safe"] >= 5

    def test_coding_models_exist(self) -> None:
        coding = list_models(category="coding")
        names = {m.name for m in coding}
        assert "Qwen2.5-Coder-0.5B" in names
        assert "Qwen2.5-Coder-1.5B" in names
        assert "Qwen2.5-Coder-3B" in names
        assert "DeepSeek-Coder-1.3B" in names
        assert "StarCoder2-3B" in names
        assert "CodeLlama-7B" in names
        assert "Phi-3-mini-4k" in names
        assert "SmolLM2-1.7B" in names

    def test_general_models_exist(self) -> None:
        general = list_models(category="general")
        names = {m.name for m in general}
        assert "Qwen2.5-0.5B" in names
        assert "Qwen2.5-1.5B" in names
        assert "Qwen2.5-3B" in names
        assert "Llama-3.2-1B" in names
        assert "Llama-3.2-3B" in names
        assert "Mistral-7B" in names
        assert "Gemma-2-2B" in names

    def test_ci_safe_filter(self) -> None:
        safe = list_models(ci_safe=True)
        for m in safe:
            assert m.size_mb < 500, f"{m.name} marked ci_safe but is {m.size_mb} MB"

    def test_get_e2e_configs_returns_all(self) -> None:
        configs = get_e2e_configs()
        assert len(configs) >= 20
        from general_ludd.local_model._local_model_configs import LocalModelConfig

        for c in configs:
            assert isinstance(c, LocalModelConfig)

    def test_get_e2e_configs_single_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("E2E_LOCAL_MODEL", "Qwen2.5-Coder-0.5B")
        configs = get_e2e_configs()
        assert len(configs) == 1
        assert configs[0].name == "Qwen2.5-Coder-0.5B"

    def test_get_all_configs(self) -> None:
        configs = get_all_configs()
        assert len(configs) >= 20

    def test_get_models_by_role(self) -> None:
        roles = get_models_by_role()
        assert set(roles.keys()) == {"PLANNER", "CODER", "REVIEWER"}
        assert len(roles["CODER"]) >= 6
        assert len(roles["PLANNER"]) >= 1
        assert len(roles["REVIEWER"]) >= 1

    def test_local_model_filter_coding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCAL_MODEL_FILTER", "coding")
        models = list_models()
        assert all(m.category == "coding" for m in models)

    def test_local_model_filter_ci_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCAL_MODEL_FILTER", "<500mb")
        models = list_models()
        assert all(m.ci_safe for m in models)

    def test_local_model_filter_specific_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCAL_MODEL_FILTER", "DeepSeek-Coder-1.3B")
        models = list_models()
        assert len(models) == 1
        assert models[0].name == "DeepSeek-Coder-1.3B"

    def test_local_gguf_models_backward_compat(self) -> None:
        assert "Qwen2.5-Coder-0.5B" in LOCAL_GGUF_MODELS
        assert LOCAL_GGUF_MODELS["Qwen2.5-Coder-0.5B"][2] == 312
        assert LOCAL_GGUF_MODELS["Mistral-7B"][2] == 4368

    def test_e2e_model_entry_to_local_config(self) -> None:
        entry = list_models(category="coding")[0]
        config = entry.to_local_model_config()
        assert config.name == entry.name
        assert config.repo == entry.repo
        assert config.filename == entry.filename
        assert config.context_size == entry.context_size

    def test_aliases_work_with_resolve(self) -> None:
        from tests.e2e._local_model_configs import _resolve

        assert _resolve("qwen-coder-0.5b") is not None
        assert _resolve("phi3-mini") is not None
        assert _resolve("gemma-2b") is not None
        assert _resolve("nonexistent-model") is None

    def test_selected_model_accepts_openai_server_identity(self) -> None:
        selected = select_models(ci_safe=True, target="Qwen2.5-0.5B-Instruct")

        assert [model.name for model in selected] == ["Qwen2.5-0.5B"]

    def test_selected_model_rejects_unknown_identity(self) -> None:
        with pytest.raises(ValueError, match="Unknown local model"):
            select_models(ci_safe=True, target="missing-model")

    def test_selected_model_rejects_ci_excluded_identity(self) -> None:
        with pytest.raises(ValueError, match="excluded by the active filters"):
            select_models(ci_safe=True, target="DeepSeek-Coder-1.3B")

    def test_all_names_unique(self) -> None:
        names = [m.name for m in _MODELS]
        assert len(names) == len(set(names)), f"Duplicate names: {[n for n in names if names.count(n) > 1]}"

    def test_context_size_positive(self) -> None:
        for m in _MODELS:
            assert m.context_size >= 2048, f"{m.name} has context_size {m.context_size}"

    def test_planner_excludes_too_small(self) -> None:
        roles = get_models_by_role()
        planner_names = {m.name for m in roles["PLANNER"]}
        assert "SmolLM2-135M" not in planner_names
