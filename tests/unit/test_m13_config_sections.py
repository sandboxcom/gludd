"""Tests for M13: config sections consumed or deleted from shipped YAML.

Verifies that the general-ludd.yml shipped config only contains sections
that have actual consumers. Dead sections (documentation fiction) are removed.
"""

from __future__ import annotations

import yaml

CONSUMED_KEYS = {
    "model_routing",
    "database",
    "agents",
    "process_isolation",
    "budget",
}

OPTIONAL_KEYS_WITH_DEFAULTS = {
    "observability",
    "model_profiles",
}


class TestM13ShippedConfigOnlyConsumedSections:
    def test_shipped_config_has_no_dead_sections(self):
        import os

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "general-ludd.yml"
        )
        config_path = os.path.normpath(config_path)

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        top_level_keys = {k for k in data if not k.startswith("_")}
        allowed = CONSUMED_KEYS | OPTIONAL_KEYS_WITH_DEFAULTS
        dead_keys = top_level_keys - allowed

        assert dead_keys == set(), (
            f"Dead config sections found in general-ludd.yml: {dead_keys}. "
            f"Each must have a consumer in the code or be removed."
        )

    def test_all_consumed_keys_present(self):
        import os

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "general-ludd.yml"
        )
        config_path = os.path.normpath(config_path)

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        missing = CONSUMED_KEYS - set(data.keys())
        assert missing == set(), f"Consumed config keys missing: {missing}"

    def test_user_config_parses_shipped_config(self):
        import os

        from general_ludd.config.user_config import UserConfig

        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "general-ludd.yml"
        )
        config_path = os.path.normpath(config_path)

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        uc = UserConfig(**data)
        assert uc is not None
        assert uc.database is not None
        assert isinstance(uc.database, dict)
