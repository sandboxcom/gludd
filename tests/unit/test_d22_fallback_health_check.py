"""D-22: ``call_model_with_fallback`` must honour ``is_healthy`` before each
fallback attempt and short-circuit when the entire chain is circuit-open.

The retry-storm amplifier: when a model fails, the fallback chain used to abort
on the first non-``ValueError``/``ImportError`` exception (via
``_try_call_model``'s narrow except), so the ``is_healthy`` gate on the *next*
fallback never ran. The caller then retried the whole chain, re-hitting the
same failing primary. These tests pin the fix:

1. A provider-level exception from one fallback does NOT abort the chain — the
   next healthy fallback is still attempted.
2. The ``is_healthy`` gate is consulted before each fallback attempt.
3. When every model in the chain is circuit-open, the gateway raises
   immediately rather than walking the loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.manager import SecretAlias, SecretsManager


def _make_profile(
    pid: str,
    fallback: list[str] | None = None,
    budget: float = 200.0,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        enabled=True,
        provider="openai",
        provider_package="langchain-openai",
        provider_class_hint="ChatOpenAI",
        model_name=f"model-{pid}",
        credential_alias="openai_key",
        run_budget_usd=budget,
        fallback_profiles=fallback or [],
    )


def _make_gateway(profiles: list[ModelProfile]) -> tuple[ModelGateway, ProviderRegistry]:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

    fake_secret_client = MagicMock()
    fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": "sk-test"}}
    }
    secrets = SecretsManager(
        client=fake_secret_client,
        aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
    )
    gw = ModelGateway(
        profiles=profiles,
        provider_registry=reg,
        secrets_manager=secrets,
    )
    return gw, reg


class _FakeHealthTracker:
    """Reports a fixed unhealthy set; ``is_healthy`` is the only consumed method."""

    def __init__(self, unhealthy: set[str]) -> None:
        self._unhealthy = unhealthy

    def is_healthy(self, profile_id: str) -> bool:
        return profile_id not in self._unhealthy

    def record_success(self, profile_id: str) -> None:
        self._unhealthy.discard(profile_id)

    def record_failure(self, profile_id: str, kind: object = None) -> None:
        self._unhealthy.add(profile_id)


def _fake_response(content: str) -> object:
    resp = MagicMock()
    resp.content = content
    return resp


class TestD22FallbackHealthCheck:
    def test_provider_exception_does_not_abort_fallback_chain(self):
        """D-22: a provider-level exception from the primary (or a fallback)
        must not abort the chain. The next healthy fallback must still be
        attempted — previously the narrow ``except (ValueError, ImportError)``
        in ``_try_call_model`` let provider errors propagate and skip every
        remaining fallback, amplifying retry storms.
        """
        primary = _make_profile("primary", fallback=["fb_a", "fb_b"])
        fb_a = _make_profile("fb_a")
        fb_b = _make_profile("fb_b")
        gw, reg = _make_gateway([primary, fb_a, fb_b])
        gw._health_tracker = None

        class _ProviderError(RuntimeError):
            pass

        ok_response = _fake_response("from fb_b")

        def fake_try(profile_id, _messages, **_kwargs):
            if profile_id == "fb_b":
                return ok_response
            raise _ProviderError(f"provider blew up on {profile_id}")

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(gw, "_try_call_model", side_effect=fake_try) as spy,
        ):
            resp = gw.call_model_with_fallback(
                "primary",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "from fb_b"
        attempted = [c.args[0] for c in spy.call_args_list]
        # primary and fb_a were attempted (and raised); fb_b rescued the call.
        assert "primary" in attempted
        assert "fb_a" in attempted
        assert "fb_b" in attempted

    def test_unhealthy_fallbacks_are_skipped(self):
        """D-22: ``is_healthy`` is consulted before each fallback attempt —
        circuit-open entries are skipped, not attempted.
        """
        primary = _make_profile("primary_open", fallback=["fb_sick", "fb_ok"])
        fb_sick = _make_profile("fb_sick")
        fb_ok = _make_profile("fb_ok")
        tracker = _FakeHealthTracker(unhealthy={"primary_open", "fb_sick"})
        gw, reg = _make_gateway([primary, fb_sick, fb_ok])
        gw._health_tracker = tracker

        FakeChatModel = MagicMock()
        instance = MagicMock()
        instance.invoke.return_value = _fake_response("from fb_ok")
        FakeChatModel.return_value = instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            patch.object(gw, "_try_call_model", wraps=gw._try_call_model) as spy,
        ):
            resp = gw.call_model_with_fallback(
                "primary_open",
                [{"role": "user", "content": "hi"}],
            )

        assert resp.content == "from fb_ok"
        attempted = [c.args[0] for c in spy.call_args_list]
        assert "primary_open" not in attempted
        assert "fb_sick" not in attempted
        assert "fb_ok" in attempted

    def test_all_unhealthy_raises_immediately(self):
        """D-22: when every model in the chain (primary + fallbacks) is
        circuit-open, raise immediately instead of walking the loop.
        """
        primary = _make_profile("primary_sick", fallback=["fb_sick_1", "fb_sick_2"])
        fb_1 = _make_profile("fb_sick_1")
        fb_2 = _make_profile("fb_sick_2")
        tracker = _FakeHealthTracker(
            unhealthy={"primary_sick", "fb_sick_1", "fb_sick_2"}
        )
        gw, reg = _make_gateway([primary, fb_1, fb_2])
        gw._health_tracker = tracker

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=MagicMock()),
            patch.object(gw, "_try_call_model") as spy,
            pytest.raises(ValueError, match="circuit-open"),
        ):
            gw.call_model_with_fallback(
                "primary_sick",
                [{"role": "user", "content": "hi"}],
            )

        # No model was attempted — the short-circuit fired before the loop.
        assert spy.call_count == 0
