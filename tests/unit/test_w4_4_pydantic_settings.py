"""TDD proof: W4.4 — UserConfig uses pydantic-settings with env var override.

Proves:
1. GLUDD_ env vars override YAML file values in UserConfig.
2. load_user_config still works from a YAML file.
3. Env var takes precedence over YAML.
4. All existing UserConfig consumers work unchanged.
"""

from __future__ import annotations

from pathlib import Path


class TestPydanticSettingsEnvOverride:
    def test_pydantic_settings_available(self) -> None:
        """pydantic-settings is importable."""
        import pydantic_settings
        assert pydantic_settings is not None

    def test_load_user_config_from_yaml(self, tmp_path: Path) -> None:
        """load_user_config reads YAML and returns UserConfig."""
        from general_ludd.config.loader import load_user_config

        cfg_file = tmp_path / "user.yml"
        cfg_file.write_text("agents:\n  timeout: 30\n")

        config = load_user_config(cfg_file)
        assert config is not None
        assert hasattr(config, "agents")

    def test_env_var_overrides_yaml_value(self, tmp_path: Path, monkeypatch) -> None:
        """GLUDD_ env vars override YAML file values."""
        from general_ludd.config.loader import load_user_config

        cfg_file = tmp_path / "user.yml"
        cfg_file.write_text("agents:\n  timeout: 30\n")

        # Set env var to override the 'agents' key (as a JSON string for dict fields).
        env_key = "GLUDD_AGENTS"
        env_val = '{"timeout": 99}'
        monkeypatch.setenv(env_key, env_val)
        config = load_user_config(cfg_file)
        # agents should now come from env var
        assert config.agents == {"timeout": 99}, (
            f"Expected env override to produce {{timeout: 99}}, got {config.agents}"
        )

    def test_yaml_used_when_no_env_override(self, tmp_path: Path, monkeypatch) -> None:
        """Without env override, YAML value is used."""
        from general_ludd.config.loader import load_user_config

        cfg_file = tmp_path / "user.yml"
        cfg_file.write_text("agents:\n  timeout: 42\n")

        monkeypatch.delenv("GLUDD_AGENTS", raising=False)
        config = load_user_config(cfg_file)
        assert config.agents == {"timeout": 42}

    def test_existing_consumers_unchanged(self) -> None:
        """UserConfig can be instantiated directly (existing callers unaffected)."""
        from general_ludd.config.user_config import UserConfig

        config = UserConfig()
        assert hasattr(config, "agents")
        assert hasattr(config, "model_profiles")
        assert hasattr(config, "database")
        assert hasattr(config, "budget")
