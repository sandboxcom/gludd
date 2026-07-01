"""C3 coverage: ``worker.app.build_gateway_from_config``.

The worker is stateless; its model profiles come from the same user config the
daemon reads. ``build_gateway_from_config`` has three observable outcomes:

  * profiles configured -> a ``ModelGateway`` is constructed WITH a
    ``ProviderRegistry`` (the CI-1 fix so the worker can make live calls) and an
    ``EnvSecretsManager``;
  * NO profiles configured -> ``None`` (the worker degrades to no model calls,
    the playbook still runs) — no crash;
  * config load raises -> the E2 fallback: log a WARNING (with traceback) and
    return ``None`` rather than failing.

These were untested (POST_COMMIT_BACKLOG_2026-06-24.md C3). The function imports
``load_user_config`` / ``ProviderRegistry`` / ``EnvSecretsManager`` lazily INSIDE
the function body, so each is patched at its SOURCE module (where the lazy
import binds), mirroring the worker-test style.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.worker.app import build_gateway_from_config


def _profile(profile_id: str = "default") -> ModelProfile:
    return ModelProfile(model_profile_id=profile_id, model_name="glm-4.6")


class TestBuildGatewayWithProfiles:
    def test_constructs_gateway_with_provider_registry_and_secrets(self) -> None:
        """CI-1 path: profiles present -> ModelGateway wired with a
        ProviderRegistry (so live calls work) + an EnvSecretsManager."""
        profile = _profile()
        uc = SimpleNamespace(model_profiles={"default": profile})

        fake_registry = MagicMock(name="ProviderRegistry")
        registry_cls = MagicMock()
        registry_cls.from_profiles.return_value = fake_registry
        fake_secrets = MagicMock(name="EnvSecretsManager")

        with (
            patch(
                "general_ludd.config.loader.load_user_config",
                return_value=uc,
            ),
            patch(
                "general_ludd.models.provider_registry.ProviderRegistry",
                registry_cls,
            ),
            patch(
                "general_ludd.secrets.env.EnvSecretsManager",
                return_value=fake_secrets,
            ),
        ):
            gateway = build_gateway_from_config()

        assert isinstance(gateway, ModelGateway)
        # CI-1: a ProviderRegistry was built FROM the profiles and wired in
        # (omitting it left provider_registry=None -> "No provider registry
        # configured" on every live call).
        registry_cls.from_profiles.assert_called_once()
        assert gateway._registry is fake_registry
        # The EnvSecretsManager is wired so credential aliases resolve.
        assert gateway._secrets is fake_secrets
        # The profile flowed through, keyed by its id.
        assert "default" in gateway._profiles

    def test_dict_profile_is_coerced_to_model_profile(self) -> None:
        """A raw-dict profile (config-loaded JSON) is coerced into a
        ModelProfile and the dict key backfills model_profile_id."""
        uc = SimpleNamespace(model_profiles={"fast": {"model_name": "glm-4.5"}})

        with (
            patch(
                "general_ludd.config.loader.load_user_config",
                return_value=uc,
            ),
            patch(
                "general_ludd.models.provider_registry.ProviderRegistry",
            ),
            patch(
                "general_ludd.secrets.env.EnvSecretsManager",
            ),
        ):
            gateway = build_gateway_from_config()

        assert isinstance(gateway, ModelGateway)
        assert "fast" in gateway._profiles
        assert gateway._profiles["fast"].model_name == "glm-4.5"


class TestBuildGatewayNoProfiles:
    def test_no_profiles_returns_none(self) -> None:
        """No profiles configured -> None (worker does no model calls; no
        crash). ProviderRegistry must NOT even be consulted."""
        uc = SimpleNamespace(model_profiles={})
        with (
            patch(
                "general_ludd.config.loader.load_user_config",
                return_value=uc,
            ),
            patch(
                "general_ludd.models.provider_registry.ProviderRegistry"
            ) as registry_cls,
        ):
            gateway = build_gateway_from_config()

        assert gateway is None
        registry_cls.from_profiles.assert_not_called()

    def test_missing_model_profiles_attr_returns_none(self) -> None:
        """A config object with no ``model_profiles`` attribute at all degrades
        gracefully to None (getattr default), not an AttributeError crash."""
        uc = SimpleNamespace()  # no model_profiles attribute
        with patch(
            "general_ludd.config.loader.load_user_config",
            return_value=uc,
        ):
            gateway = build_gateway_from_config()

        assert gateway is None


class TestBuildGatewayConfigError:
    def test_config_load_error_logs_and_returns_none(self, caplog) -> None:
        """E2 fallback: a raising config load is caught -> WARNING logged (with
        traceback) and None returned; the worker never fails to start."""
        logging.getLogger("general_ludd.worker.app").propagate = True
        with (
            patch(
                "general_ludd.config.loader.load_user_config",
                side_effect=RuntimeError("config blew up"),
            ),
            caplog.at_level(logging.WARNING, logger="general_ludd.worker.app"),
        ):
            gateway = build_gateway_from_config()

        assert gateway is None
        # The failure is OBSERVABLE (not silently swallowed).
        assert any(
            "gateway construction failed" in rec.getMessage().lower()
            for rec in caplog.records
        )
        # exc_info=True attaches the traceback to the record.
        assert any(rec.exc_info is not None for rec in caplog.records)

    def test_invalid_profile_dict_falls_back_to_none(self) -> None:
        """A malformed profile dict (empty model_profile_id) raises inside the
        ModelProfile coercion; the E2 except catches it -> None, no crash."""
        # An empty-string key -> model_profile_id "" -> ModelProfile validator
        # raises ValueError, exercised inside the try/except.
        uc = SimpleNamespace(model_profiles={"": {"model_name": "x"}})
        with patch(
            "general_ludd.config.loader.load_user_config",
            return_value=uc,
        ):
            gateway = build_gateway_from_config()

        assert gateway is None
