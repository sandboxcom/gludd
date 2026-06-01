"""Unit tests for EnvSecretsManager."""

from __future__ import annotations

import os

from general_ludd.secrets.env import EnvSecretsManager


class TestEnvSecretsManager:
    def test_resolve_from_override(self):
        mgr = EnvSecretsManager(overrides={"MY_KEY": "secret123"})
        assert mgr.resolve("MY_KEY") == "secret123"

    def test_resolve_from_environ(self):
        os.environ["_TEST_ENV_SECRET"] = "envval"
        try:
            mgr = EnvSecretsManager()
            assert mgr.resolve("_TEST_ENV_SECRET") == "envval"
        finally:
            del os.environ["_TEST_ENV_SECRET"]

    def test_override_takes_precedence_over_environ(self):
        os.environ["_TEST_ENV_SECRET2"] = "envval"
        try:
            mgr = EnvSecretsManager(overrides={"_TEST_ENV_SECRET2": "override"})
            assert mgr.resolve("_TEST_ENV_SECRET2") == "override"
        finally:
            del os.environ["_TEST_ENV_SECRET2"]

    def test_resolve_missing_returns_none(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("NONEXISTENT_KEY_XYZ") is None

    def test_set_adds_override(self):
        mgr = EnvSecretsManager()
        mgr.set("NEW_KEY", "val")
        assert mgr.resolve("NEW_KEY") == "val"

    def test_list_aliases(self):
        mgr = EnvSecretsManager(overrides={"b": "2", "a": "1"})
        assert mgr.list_aliases() == ["a", "b"]

    def test_empty_manager(self):
        mgr = EnvSecretsManager()
        assert mgr.list_aliases() == []
        assert mgr.resolve("anything") is None
