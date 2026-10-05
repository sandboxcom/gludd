"""Unit tests for _local_model_configs."""



from general_ludd.local_model._local_model_configs import LocalModelConfig, get_e2e_models


class TestLocalModelConfig:
    def test_defaults(self):
        cfg = LocalModelConfig(name="x", repo="y", filename="z.gguf")
        assert cfg.context_size == 2048
        assert cfg.quant_level == "Q4_K_M"
        assert cfg.category == "general"
        assert cfg.ci_safe is False

    def test_huggingface_url_derived(self):
        cfg = LocalModelConfig(name="x", repo="y/z", filename="z.gguf")
        assert cfg.huggingface_url == "https://huggingface.co/y/z"

    def test_quant_level_from_filename(self):
        cfg = LocalModelConfig(name="x", repo="y", filename="model-Q8_0.gguf", quant_level="")
        assert cfg.quant_level == "Q8_0"

    def test_quant_level_unknown(self):
        cfg = LocalModelConfig(name="x", repo="y", filename="model.gguf", quant_level="")
        assert cfg.quant_level == ""


class TestGetE2eModels:
    def test_returns_all_by_default(self):
        models = get_e2e_models()
        assert len(models) > 10

    def test_filter_by_env(self, monkeypatch):
        monkeypatch.setenv("E2E_LOCAL_MODEL", "qwen-0.5b")
        models = get_e2e_models()
        assert len(models) == 1
        assert models[0].name == "qwen-0.5b"
        monkeypatch.delenv("E2E_LOCAL_MODEL")
