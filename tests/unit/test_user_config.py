from __future__ import annotations

import yaml

from general_ludd.config.loader import (
    build_config_layer,
    load_agent_config,
    load_user_config,
    save_agent_config,
)
from general_ludd.config.model_routing import ModelRoutingConfig
from general_ludd.config.user_config import AgentConfig, ConfigLayer, UserConfig


class TestUserConfigDefaults:
    def test_model_routing_is_none(self):
        cfg = UserConfig()
        assert cfg.model_routing is None

    def test_model_profiles_empty(self):
        cfg = UserConfig()
        assert cfg.model_profiles == {}

    def test_agents_empty(self):
        cfg = UserConfig()
        assert cfg.agents == {}

    def test_process_isolation_empty(self):
        cfg = UserConfig()
        assert cfg.process_isolation == {}

    def test_budget_empty(self):
        cfg = UserConfig()
        assert cfg.budget == {}

    def test_database_empty(self):
        cfg = UserConfig()
        assert cfg.database == {}

    def test_database_accepts_url(self):
        cfg = UserConfig(database={"url": "postgresql://localhost/gludd"})
        assert cfg.database == {"url": "postgresql://localhost/gludd"}

    def test_database_accepts_components(self):
        cfg = UserConfig(
            database={
                "host": "localhost",
                "port": 5432,
                "name": "gludd",
                "user": "gludd",
                "password": "secret",
            }
        )
        assert cfg.database["host"] == "localhost"
        assert cfg.database["port"] == 5432


class TestAgentConfigDefaults:
    def test_model_routing_is_none(self):
        cfg = AgentConfig()
        assert cfg.model_routing is None

    def test_active_model_profile_is_none(self):
        cfg = AgentConfig()
        assert cfg.active_model_profile is None

    def test_preferred_agents_empty(self):
        cfg = AgentConfig()
        assert cfg.preferred_agents == {}

    def test_task_preferences_empty(self):
        cfg = AgentConfig()
        assert cfg.task_preferences == {}

    def test_session_notes_empty(self):
        cfg = AgentConfig()
        assert cfg.session_notes == ""


class TestConfigLayerResolve:
    def test_user_override_takes_precedence(self):
        user = UserConfig(budget={"max_usd": 100})
        agent = AgentConfig(task_preferences={"budget": 50})
        layer = ConfigLayer(user=user, agent=agent, defaults={"budget": {"max_usd": 10}})
        assert layer.resolve("budget") == {"max_usd": 100}

    def test_agent_config_when_no_user(self):
        agent = AgentConfig(task_preferences={"budget": 50})
        layer = ConfigLayer(user=UserConfig(), agent=agent, defaults={"task_preferences": {}})
        assert layer.resolve("task_preferences") == {"budget": 50}

    def test_defaults_when_no_user_or_agent(self):
        layer = ConfigLayer(
            user=UserConfig(),
            agent=AgentConfig(),
            defaults={"some_key": "default_val"},
        )
        assert layer.resolve("some_key") == "default_val"

    def test_resolve_missing_key_returns_none(self):
        layer = ConfigLayer(user=UserConfig(), agent=AgentConfig(), defaults={})
        assert layer.resolve("nonexistent") is None

    def test_user_model_profiles_overrides_all(self):
        user = UserConfig(model_profiles={"primary": "gpt4"})
        agent = AgentConfig(preferred_agents={"model_profiles": {"primary": "haiku"}})
        layer = ConfigLayer(
            user=user,
            agent=agent,
            defaults={"model_profiles": {"primary": "fallback"}},
        )
        assert layer.resolve("model_profiles") == {"primary": "gpt4"}


class TestConfigLayerResolveModelRouting:
    def test_returns_user_config_when_present(self):
        user_routing = ModelRoutingConfig(default_profile="user_prof")
        user = UserConfig(model_routing=user_routing)
        agent_routing = ModelRoutingConfig(default_profile="agent_prof")
        agent = AgentConfig(model_routing=agent_routing)
        layer = ConfigLayer(user=user, agent=agent)
        result = layer.resolve_model_routing()
        assert result.default_profile == "user_prof"

    def test_falls_through_to_agent_config(self):
        agent_routing = ModelRoutingConfig(default_profile="agent_prof")
        agent = AgentConfig(model_routing=agent_routing)
        layer = ConfigLayer(user=UserConfig(), agent=agent)
        result = layer.resolve_model_routing()
        assert result.default_profile == "agent_prof"

    def test_returns_defaults_when_neither_set(self):
        layer = ConfigLayer(user=UserConfig(), agent=AgentConfig())
        result = layer.resolve_model_routing()
        assert result == ModelRoutingConfig()


class TestLoadUserConfig:
    def test_load_from_file(self, tmp_path):
        data = {
            "model_routing": {"default_profile": "user_default"},
            "model_profiles": {"primary": "gpt4"},
            "budget": {"max_usd": 50},
        }
        yml = tmp_path / "user.yml"
        yml.write_text(yaml.dump(data))
        cfg = load_user_config(yml)
        assert cfg.model_routing is not None
        assert cfg.model_routing.default_profile == "user_default"
        assert cfg.model_profiles == {"primary": "gpt4"}
        assert cfg.budget == {"max_usd": 50}

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = load_user_config(tmp_path / "missing.yml")
        assert cfg == UserConfig()

    def test_load_empty_file_returns_defaults(self, tmp_path):
        yml = tmp_path / "empty.yml"
        yml.write_text("")
        cfg = load_user_config(yml)
        assert cfg == UserConfig()


class TestLoadAgentConfig:
    def test_load_from_file(self, tmp_path):
        data = {
            "active_model_profile": "zai_coder",
            "session_notes": "working on feature X",
            "task_preferences": {"prefer_fast": True},
        }
        yml = tmp_path / "agent_config.yml"
        yml.write_text(yaml.dump(data))
        cfg = load_agent_config(yml)
        assert cfg.active_model_profile == "zai_coder"
        assert cfg.session_notes == "working on feature X"
        assert cfg.task_preferences == {"prefer_fast": True}

    def test_load_missing_file_returns_defaults(self, tmp_path):
        cfg = load_agent_config(tmp_path / "missing.yml")
        assert cfg == AgentConfig()

    def test_load_empty_file_returns_defaults(self, tmp_path):
        yml = tmp_path / "empty.yml"
        yml.write_text("")
        cfg = load_agent_config(yml)
        assert cfg == AgentConfig()


class TestSaveAgentConfig:
    def test_writes_yaml(self, tmp_path):
        cfg = AgentConfig(
            active_model_profile="zai_coder",
            session_notes="test session",
        )
        out_path = tmp_path / "agent_config.yml"
        save_agent_config(cfg, out_path)
        assert out_path.exists()
        loaded = yaml.safe_load(out_path.read_text())
        assert loaded["active_model_profile"] == "zai_coder"
        assert loaded["session_notes"] == "test session"

    def test_round_trip(self, tmp_path):
        original = AgentConfig(
            active_model_profile="prof",
            preferred_agents={"coder": "agent_a"},
            task_preferences={"mode": "fast"},
            session_notes="notes",
        )
        out_path = tmp_path / "agent_config.yml"
        save_agent_config(original, out_path)
        loaded = load_agent_config(out_path)
        assert loaded.active_model_profile == "prof"
        assert loaded.preferred_agents == {"coder": "agent_a"}
        assert loaded.task_preferences == {"mode": "fast"}
        assert loaded.session_notes == "notes"


class TestUserConfigNotWritableByAgent:
    def test_user_config_has_no_save_function(self):
        assert not hasattr(UserConfig, "save")
        assert not callable(getattr(UserConfig, "save", None))

    def test_only_agent_config_has_save(self):
        from general_ludd.config import loader

        assert hasattr(loader, "save_agent_config")
        assert not hasattr(loader, "save_user_config")


class TestBuildConfigLayer:
    def test_full_layer_with_all_three(self, tmp_path):
        user_data = {
            "model_routing": {"default_profile": "user_prof"},
            "budget": {"max_usd": 100},
        }
        agent_data = {
            "active_model_profile": "agent_prof",
            "session_notes": "agent notes",
        }
        user_yml = tmp_path / "user.yml"
        user_yml.write_text(yaml.dump(user_data))
        agent_yml = tmp_path / "agent_config.yml"
        agent_yml.write_text(yaml.dump(agent_data))

        layer = build_config_layer(
            user_path=user_yml,
            agent_path=agent_yml,
            defaults={"budget": {"max_usd": 10}, "extra_key": "val"},
        )
        assert layer.user.model_routing is not None
        assert layer.user.model_routing.default_profile == "user_prof"
        assert layer.user.budget == {"max_usd": 100}
        assert layer.agent.active_model_profile == "agent_prof"
        assert layer.defaults["extra_key"] == "val"

    def test_layer_with_no_files(self, tmp_path):
        layer = build_config_layer(
            user_path=tmp_path / "missing_user.yml",
            agent_path=tmp_path / "missing_agent.yml",
            defaults={"key": "val"},
        )
        assert layer.user == UserConfig()
        assert layer.agent == AgentConfig()
        assert layer.defaults == {"key": "val"}

    def test_layer_with_none_paths(self):
        layer = build_config_layer(defaults={"key": "val"})
        assert layer.user == UserConfig()
        assert layer.agent == AgentConfig()
        assert layer.defaults == {"key": "val"}

    def test_precedence_user_over_agent_over_defaults(self, tmp_path):
        user_data = {"budget": {"max_usd": 100}}
        agent_data = {"task_preferences": {"budget": 50}}
        user_yml = tmp_path / "user.yml"
        user_yml.write_text(yaml.dump(user_data))
        agent_yml = tmp_path / "agent_config.yml"
        agent_yml.write_text(yaml.dump(agent_data))

        layer = build_config_layer(
            user_path=user_yml,
            agent_path=agent_yml,
            defaults={"budget": {"max_usd": 10}},
        )
        assert layer.resolve("budget") == {"max_usd": 100}
        assert layer.resolve("task_preferences") == {"budget": 50}
