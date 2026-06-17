from __future__ import annotations

import yaml

from general_ludd.config.user_config import UserConfig


class TestUserConfigConnectorsDefault:
    def test_connectors_default_is_empty_list(self):
        cfg = UserConfig()
        assert cfg.connectors == []

    def test_connectors_type_is_list(self):
        cfg = UserConfig()
        assert isinstance(cfg.connectors, list)


class TestUserConfigConnectorsFromDict:
    def test_connectors_preserved_from_dict(self):
        entry = {"name": "prom", "kind": "prometheus", "module": "prom_mod", "url": "http://prom:9090"}
        cfg = UserConfig(connectors=[entry])
        assert len(cfg.connectors) == 1
        assert cfg.connectors[0]["name"] == "prom"
        assert cfg.connectors[0]["kind"] == "prometheus"
        assert cfg.connectors[0]["url"] == "http://prom:9090"

    def test_connectors_multiple_entries(self):
        entries = [
            {"name": "prom", "kind": "prometheus", "url": "http://prom:9090"},
            {"name": "loki", "kind": "loki", "url": "http://loki:3100"},
        ]
        cfg = UserConfig(connectors=entries)
        assert len(cfg.connectors) == 2
        assert cfg.connectors[1]["name"] == "loki"

    def test_connectors_not_dropped_with_extra_ignore(self):
        # extra="ignore" must NOT silently drop the known `connectors` field.
        cfg = UserConfig(connectors=[{"name": "x", "kind": "k"}], unknown_extra_key="dropped")
        assert len(cfg.connectors) == 1


class TestUserConfigConnectorsFromYaml:
    def test_connectors_loaded_from_yaml(self, tmp_path):
        data = {
            "connectors": [
                {"name": "prom", "kind": "prometheus", "module": "prom_mod", "url": "http://prom:9090"},
            ]
        }
        yml = tmp_path / "user.yml"
        yml.write_text(yaml.dump(data))
        cfg = UserConfig.from_yaml(yml)
        assert len(cfg.connectors) == 1
        assert cfg.connectors[0]["name"] == "prom"
        assert cfg.connectors[0]["kind"] == "prometheus"

    def test_connectors_default_when_yaml_omits_field(self, tmp_path):
        data = {"budget": {"max_usd": 10}}
        yml = tmp_path / "user.yml"
        yml.write_text(yaml.dump(data))
        cfg = UserConfig.from_yaml(yml)
        assert cfg.connectors == []

    def test_connectors_multiple_from_yaml(self, tmp_path):
        data = {
            "connectors": [
                {"name": "prom", "kind": "prometheus", "url": "http://prom:9090"},
                {"name": "loki", "kind": "loki", "url": "http://loki:3100"},
                {"name": "jaeger", "kind": "jaeger", "url": "http://jaeger:16686"},
            ]
        }
        yml = tmp_path / "user.yml"
        yml.write_text(yaml.dump(data))
        cfg = UserConfig.from_yaml(yml)
        assert len(cfg.connectors) == 3
        names = [c["name"] for c in cfg.connectors]
        assert names == ["prom", "loki", "jaeger"]
