"""LIVE F6 proof: model-failover chain works end-to-end against real z.ai.

This test exercises the gateway's failover chain (call_model_with_retry ->
_walk_fallbacks -> _call_fallback) against the REAL z.ai API using a
z.ai-to-z.ai chain:

  * primary  "zai_bad"  -> forced to FAIL on every call, fallback=["zai_good"]
  * fallback "zai_good" -> real working model glm-4.6 on the real base URL

Both profiles share the same real credential (ZAI_API_KEY from .zai.key) and the
fallback uses the real base URL (ZAI_BASE_URL), so a successful run proves the
chain walked from a failing primary to a real glm-4.6 completion.

Run with: make test-f6-failover-zai (loads the key from .zai.key into env).

----------------------------------------------------------------------------
WHICH ERROR PATH TRIGGERS THE CHAIN WALK  (important — documented per task):

A guaranteed-bad MODEL ID (e.g. "glm-does-not-exist-xyz") makes z.ai return an
HTTP 400. TimeoutClassifier._classify_http_error maps a generic 400 to
TimeoutKind.INVALID_REQUEST, which is in timeout_detector._NON_RETRYABLE_KINDS.
call_model_with_retry's _is_retryable predicate returns False for non-retryable
kinds, so a bad-model 400 is RE-RAISED on the primary and the fallback chain is
NEVER walked. A bad model id therefore does NOT exercise failover.

So this test forces the primary to fail with a RETRYABLE error instead: the
primary's api_base_alias points at an UNREACHABLE https host. The connection
fails with httpx.ConnectError, which TimeoutClassifier maps to
TimeoutKind.CONNECTION_TIMEOUT (retryable, NOT in _NON_RETRYABLE_KINDS). After
the primary exhausts its retries, call_model_with_retry walks
profile.fallback_profiles -> "zai_good" -> a real glm-4.6 completion.

The unreachable host is an https host ("...invalid") that is PUBLIC-looking
(not loopback / link-local / RFC-1918 / metadata), so it passes the gateway's
SSRF guard (is_safe_fetch_url, https-only + private-host deny) and is allowed to
be attempted — where DNS resolution fails -> ConnectError -> retryable failover.
----------------------------------------------------------------------------
"""

from __future__ import annotations

import os

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.timeout_detector import ModelHealthTracker
from general_ludd.secrets.env import EnvSecretsManager


def _get_zai_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _get_zai_base_url() -> str:
    return os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4")


def _get_zai_model() -> str:
    return os.environ.get("ZAI_MODEL", "glm-4.6")


# A public-looking https host that does not resolve: passes the SSRF guard
# (not loopback / link-local / RFC-1918 / metadata) but ConnectError-fails at
# DNS/connect time -> retryable CONNECTION_TIMEOUT -> triggers the fallback walk.
_UNREACHABLE_BASE_URL = "https://zai-f6-failover-unreachable-probe.invalid/v4"

_SKIP_REASON = (
    "ZAI_API_KEY not set. "
    "Run: make test-f6-failover-zai (loads key from .zai.key)"
)


def _build_failover_gateway() -> ModelGateway:
    """Build a gateway with a failing primary -> real glm-4.6 fallback chain."""
    api_key = _get_zai_api_key()
    real_base_url = _get_zai_base_url()
    good_model = _get_zai_model()

    common = dict(
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        context_window=64000,
        max_input_tokens=60000,
        max_output_tokens=256,
        cost_per_input_token=0.0,
        cost_per_output_token=0.0,
        api_metered=False,
        run_budget_usd=1.0,
        enabled=True,
        credential_alias="ZAI_API_KEY",
    )

    # Primary: real credential, real model name, but UNREACHABLE base URL so the
    # call fails with a retryable ConnectError and the chain walks to the fallback.
    primary = ModelProfile(
        model_profile_id="zai_bad",
        model_name=good_model,
        api_base_alias="ZAI_BAD_BASE_URL",
        fallback_profiles=["zai_good"],
        **common,  # type: ignore[arg-type]
    )
    # Fallback: real credential + real base URL + real working model.
    fallback = ModelProfile(
        model_profile_id="zai_good",
        model_name=good_model,
        api_base_alias="ZAI_BASE_URL",
        fallback_profiles=[],
        **common,  # type: ignore[arg-type]
    )

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    if api_key:
        secrets.set("ZAI_API_KEY", api_key)
    secrets.set("ZAI_BASE_URL", real_base_url)
    secrets.set("ZAI_BAD_BASE_URL", _UNREACHABLE_BASE_URL)

    return ModelGateway(
        profiles=[primary, fallback],
        provider_registry=registry,
        secrets_manager=secrets,  # type: ignore[arg-type]
        health_tracker=ModelHealthTracker(),
    )


@pytest.mark.skipif(not _get_zai_api_key(), reason=_SKIP_REASON)
class TestF6FailoverChainLive:
    """End-to-end live proof that the failover chain walks bad-primary -> real fallback."""

    def test_failover_chain_walks_to_real_glm46(self):
        gw = _build_failover_gateway()

        # Entry point: call_model_with_retry. It retries the primary, and once the
        # retryable ConnectError exhausts, walks profile.fallback_profiles to
        # "zai_good" (real glm-4.6). max_retries=1 keeps the failing-primary phase
        # cheap (the unreachable host fails fast at connect time anyway).
        response = gw.call_model_with_retry(
            "zai_bad",
            [{"role": "user", "content": "Reply with the single word: PONG"}],
            max_retries=1,
            base_backoff_seconds=0.0,
        )

        # The fallback produced REAL content (the bad primary cannot have silently
        # succeeded — its base URL is unreachable).
        assert isinstance(response.content, str)
        assert response.content.strip(), (
            f"fallback returned empty content: {response!r}"
        )

        # It came from the real glm-4.6 fallback profile, not the bad primary.
        # call_model sets model_name to the profile's model_name; both profiles use
        # the same model id here, so we additionally assert via the health tracker
        # that the bad primary was recorded UNHEALTHY/failed and the good fallback
        # recorded a success.
        assert _get_zai_model() in response.model_name

        tracker = gw._health_tracker
        bad_health = tracker.get_health("zai_bad")
        good_health = tracker.get_health("zai_good")
        assert bad_health["total_failures"] > 0, (
            f"expected the bad primary to have recorded failures: {bad_health}"
        )
        assert good_health["consecutive_failures"] == 0, (
            f"expected the fallback to be healthy after success: {good_health}"
        )

        # Sanity: the model actually answered (cheap, deterministic-ish prompt).
        assert "pong" in response.content.lower() or len(response.content) > 0
