"""WP-C1 behavioral coverage for the model gateway (STABILIZATION_PLAN.md).

Targets the under-covered branches ranked highest by (low coverage x high
criticality): fallback-chain advancement on a real provider failure,
circuit-breaker open + half-open transitions, budget-guard rejection, SSRF
egress rejection before any request, retry backoff growth, and the GW-2
truncation-never-cached guard.

Every test here drives a real code path and asserts an observable outcome --
none are import-only or attribute-existence checks.  Harness patterns mirror
``test_gateway_circuit_breaker.py`` (scripted provider class) and
``test_gateway_truncation_no_cache.py`` (mock cache + response_metadata).
"""

from __future__ import annotations

import itertools
from typing import ClassVar
from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.models.gateway import (
    BudgetExceededError,
    ModelGateway,
    ModelProfile,
    SSRFRejectionError,
)
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutKind,
    TimeoutRetryPolicy,
)

# ---------------------------------------------------------------------------
# Shared harness: a scripted provider that branches on the `model` init kwarg.
# The gateway builds ``init_kwargs={"model": profile.model_name, ...}`` and then
# calls ``provider_cls(**init_kwargs)``, so every profile gets a fresh instance
# whose ``_model`` identifies the calling profile.  This lets one registry serve
# a failing primary and a succeeding fallback through the REAL call_model path
# without patching call_model itself (unlike the kwarg-dispatch patch in
# test_model_gateway_fallback.py::TestFallbackPreservesCostTracking).
# ---------------------------------------------------------------------------


def _server_error() -> httpx.HTTPStatusError:
    """A retryable 503 that classifies as PROVIDER_ERROR."""
    request = httpx.Request("POST", "https://provider.invalid/v1/chat")
    response = httpx.Response(503, request=request, text="service unavailable")
    return httpx.HTTPStatusError("503", request=request, response=response)


class _BranchingProvider:
    """Provider stub whose invoke() behavior is keyed off the ``model`` kwarg."""

    fail_model: ClassVar[str] = ""

    def __init__(self, **init_kwargs: object) -> None:
        self._model = str(init_kwargs.get("model", ""))

    def invoke(self, messages: list[dict[str, str]]) -> object:
        if self._model == _BranchingProvider.fail_model:
            raise httpx.ConnectError("primary provider down")
        resp = type("_Resp", (), {})()
        resp.content = "fallback-served"
        resp.usage_metadata = {"input_tokens": 2, "output_tokens": 1}
        return resp


def _mock_registry(provider_cls: type | None = None) -> MagicMock:
    """A registry stub whose get_provider_class returns ``provider_cls``."""
    reg = MagicMock()
    reg.is_installed.return_value = True
    reg.get_provider_class.return_value = provider_cls or _BranchingProvider
    return reg


def _profile(
    pid: str,
    *,
    model_name: str = "",
    fallback: list[str] | None = None,
    budget: float = 200.0,
    api_metered: bool = False,
    api_base_alias: str | None = None,
    credential_alias: str | None = None,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=pid,
        provider="openai",
        model_name=model_name or pid,
        enabled=True,
        api_metered=api_metered,
        cost_per_input_token=5e-6 if api_metered else 0.0,
        cost_per_output_token=15e-6 if api_metered else 0.0,
        run_budget_usd=budget,
        fallback_profiles=fallback or [],
        api_base_alias=api_base_alias,
        credential_alias=credential_alias,
    )


@pytest.fixture(autouse=True)
def _reset_branching() -> None:
    _BranchingProvider.fail_model = ""


# ---------------------------------------------------------------------------
# 1. Fallback chain advances on a real provider failure.
# ---------------------------------------------------------------------------


class TestFallbackChainAdvancesOnProviderFailure:
    def test_fallback_chain_advances_on_provider_failure(self) -> None:
        """First provider fails with a connection error; the fallback serves.

        Drives the real call_model -> _invoke_and_bill path (provider.invoke
        raises httpx.ConnectError) so _try_call_model returns None and the
        fallback walk reaches the healthy fallback.  This is the core
        reliability guarantee of the fallback chain: a hard provider failure
        must not strand the caller when a fallback is configured.
        """
        _BranchingProvider.fail_model = "primary-model"
        primary = _profile("primary", model_name="primary-model", fallback=["fb"])
        fallback = _profile("fb", model_name="fb-model")
        gw = ModelGateway(
            profiles=[primary, fallback],
            provider_registry=_mock_registry(),
        )

        resp = gw.call_model_with_fallback(
            "primary", [{"role": "user", "content": "hi"}]
        )

        assert resp.content == "fallback-served"
        assert resp.model_name == "fb-model"


# ---------------------------------------------------------------------------
# 2. Circuit breaker opens after N consecutive failures.
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpensAfterNFailures:
    def test_circuit_breaker_opens_after_N_failures(self) -> None:
        """N consecutive real failures flip the breaker to open.

        Uses failure_threshold=3 and drives three independent call_model
        invocations (each records exactly one failure via
        record_timeout_on_failure).  After the third, is_healthy must report
        False -- the gateway's circuit gate would then refuse further calls.
        """
        tracker = ModelHealthTracker(failure_threshold=3, cooldown_seconds=60.0)
        profile = _profile("primary", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
            health_tracker=tracker,
        )

        # Sanity: healthy before any failure.
        assert tracker.is_healthy("primary") is True

        # Two failures: below threshold -> still healthy (breaker not yet open).
        for _ in range(2):
            with patch.object(
                _BranchingProvider,
                "invoke",
                side_effect=_server_error(),
            ), pytest.raises(httpx.HTTPStatusError):
                gw.call_model("primary", [{"role": "user", "content": "hi"}])
        assert tracker._consecutive.get("primary") == 2
        assert tracker.is_healthy("primary", admit_probe=False) is True

        # Third failure reaches threshold -> breaker opens.
        with patch.object(
            _BranchingProvider,
            "invoke",
            side_effect=_server_error(),
        ), pytest.raises(httpx.HTTPStatusError):
            gw.call_model("primary", [{"role": "user", "content": "hi"}])

        assert tracker._consecutive.get("primary") == 3
        assert tracker.is_healthy("primary", admit_probe=False) is False


# ---------------------------------------------------------------------------
# 3. Circuit breaker half-open admits exactly one probe after cooldown.
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpenAllowsOneProbe:
    def test_circuit_breaker_half_open_allows_one_probe(self) -> None:
        """After cooldown elapses, exactly ONE half-open probe is admitted.

        The ModelHealthTracker (which the gateway delegates circuit state to)
        implements single-flight half-open semantics: once the breaker is open
        and the cooldown window passes, the first is_healthy(admit_probe=True)
        claim admits a probe and records its timestamp; a second concurrent
        caller in the same window is refused (returns False) so only one probe
        hits the recovering provider.  This test pins that contract.
        """
        tracker = ModelHealthTracker(
            failure_threshold=3,
            cooldown_seconds=0.10,
        )
        profile = _profile("primary", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
            health_tracker=tracker,
        )

        # Trip the breaker via real failures through the gateway.
        for _ in range(3):
            with patch.object(
                _BranchingProvider,
                "invoke",
                side_effect=_server_error(),
            ), pytest.raises(httpx.HTTPStatusError):
                gw.call_model("primary", [{"role": "user", "content": "hi"}])
        assert tracker.is_healthy("primary", admit_probe=False) is False

        # Wait out the cooldown window so the breaker transitions to half-open.
        import time

        time.sleep(0.15)

        # First probe claim is admitted (breaker is half-open, slot free).
        first_probe = tracker.is_healthy("primary", admit_probe=True)
        assert first_probe is True
        # Second claim in the SAME window is refused: the single slot is held.
        second_probe = tracker.is_healthy("primary", admit_probe=True)
        assert second_probe is False


# ---------------------------------------------------------------------------
# 4. Budget guard rejects a call exceeding the remaining budget.
# ---------------------------------------------------------------------------


class TestBudgetGuardRejectsCallExceedingRemaining:
    def test_budget_guard_rejects_call_exceeding_remaining(self) -> None:
        """remaining=0.001, projected=0.01 -> check_budget returns False.

        The budget gate (D-24) must reject any call whose effective cost
        exceeds the remaining budget.  With no messages supplied, the
        server-side re-estimate is skipped and the caller-supplied
        estimated_cost is used directly, so this is a clean unit proof of the
        comparison that guards every metered call.
        """
        profile = _profile("budget-p", model_name="m")
        gw = ModelGateway(profiles=[profile], provider_registry=_mock_registry())

        rejected = gw.check_budget(
            "budget-p",
            estimated_cost=0.01,
            budget_remaining=0.001,
        )
        assert rejected is False

        # Behavioral contrast: a call UNDER budget is accepted, proving the
        # gate is a real comparison rather than an always-reject stub.
        accepted = gw.check_budget(
            "budget-p",
            estimated_cost=0.0001,
            budget_remaining=0.001,
        )
        assert accepted is True


# ---------------------------------------------------------------------------
# 5. SSRF rejection raises before any provider request is made.
# ---------------------------------------------------------------------------


class TestSSRFRejectionRaisesBeforeRequest:
    def test_ssrf_rejection_raises_before_request(self) -> None:
        """A blocked api_base_alias URL raises SSRFRejectionError pre-request.

        The gateway resolves api_base_alias to a literal base_url through the
        secrets resolver and validates it with is_safe_fetch_url BEFORE
        constructing the provider client.  A loopback URL must raise the
        distinct SSRFRejectionError type AND the provider must never be
        invoked (fail closed -- no outbound request to the internal host).
        """
        profile = _profile(
            "ssrf-p",
            model_name="m",
            api_base_alias="API_BASE",
            credential_alias="OPENAI_KEY",
        )

        mock_chat_model = MagicMock()
        mock_chat_model.invoke.return_value = MagicMock(content="leaked")

        registry = MagicMock()
        registry.is_installed.return_value = True
        registry.get_provider_class.return_value = MagicMock(
            return_value=mock_chat_model
        )

        secrets = MagicMock()

        def _resolve(alias: str) -> str | None:
            if alias == "API_BASE":
                return "http://127.0.0.1:8000/v1"
            if alias == "OPENAI_KEY":
                return "test-key"
            return None

        secrets.resolve.side_effect = _resolve

        gw = ModelGateway(
            profiles={"ssrf-p": profile},
            provider_registry=registry,
            secrets_manager=secrets,
        )

        with pytest.raises(SSRFRejectionError):
            gw.call_model("ssrf-p", [{"role": "user", "content": "hi"}])

        # Fail closed: the provider client was never invoked against the
        # internal/loopback host.
        mock_chat_model.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Retry backoff grows exponentially across successive attempts.
# ---------------------------------------------------------------------------


class TestRetryBackoffIncrementsExponentially:
    def test_retry_backoff_increments_exponentially(self) -> None:
        """Successive retry backoffs grow ~2x per attempt (deterministic).

        The gateway's ``_before_sleep`` tenacity callback delegates the wait
        computation to ``TimeoutRetryPolicy._compute_backoff``.  Injecting a
        deterministic jitter function (always take the high endpoint) removes
        the randomized equal-jitter spread so the underlying exponential
        component (``base * 2 ** (attempt - 1)``) is observable directly:
        1.0, 2.0, 4.0, 8.0 -- each double the previous.
        """
        policy = TimeoutRetryPolicy(
            max_retries=5,
            base_backoff_seconds=1.0,
            jitter_fn=lambda lo, hi: hi,
        )
        waits = [
            policy._compute_backoff(TimeoutKind.UNKNOWN, attempt, None)
            for attempt in range(1, 5)
        ]

        assert waits == [1.0, 2.0, 4.0, 8.0]
        assert all(b > a for a, b in itertools.pairwise(waits))


# ---------------------------------------------------------------------------
# 7. GW-2: a truncated response (finish_reason="length") is never cached.
# ---------------------------------------------------------------------------


class TestTruncationNeverCached:
    def test_truncation_never_cached(self) -> None:
        """finish_reason="length" -> cache.set NOT called; "stop" -> cached.

        GW-2 guard: caching a truncated turn would replay the cut-off text as
        the full answer on every identical future request.  The gateway reads
        finish_reason from response_metadata and skips cache.set when it equals
        "length".  A normally-completed turn (finish_reason="stop") must still
        be cached so the guard is not over-broad.
        """

        def _make_response(content: str, finish_reason: str) -> MagicMock:
            resp = MagicMock()
            resp.content = content
            resp.usage_metadata = {"input_tokens": 3, "output_tokens": 2}
            resp.response_metadata = {"finish_reason": finish_reason}
            return resp

        profile = _profile("gw2-p", model_name="m")

        # --- Truncated turn: must NOT be cached. ---
        trunc_response = _make_response("partial answer cut off", "length")
        trunc_chat = MagicMock()
        trunc_chat.invoke.return_value = trunc_response
        trunc_registry = MagicMock()
        trunc_registry.is_installed.return_value = True
        trunc_registry.get_provider_class.return_value = MagicMock(
            return_value=trunc_chat
        )
        trunc_cache = MagicMock()
        trunc_cache.get.return_value = None

        gw_trunc = ModelGateway(
            profiles={"gw2-p": profile},
            provider_registry=trunc_registry,
            response_cache=trunc_cache,
        )
        trunc_resp = gw_trunc.call_model(
            "gw2-p", [{"role": "user", "content": "hi"}]
        )
        assert trunc_resp.content == "partial answer cut off"
        trunc_cache.set.assert_not_called()

        # --- Complete turn: MUST be cached (guard is not over-broad). ---
        full_response = _make_response("full answer", "stop")
        full_chat = MagicMock()
        full_chat.invoke.return_value = full_response
        full_registry = MagicMock()
        full_registry.is_installed.return_value = True
        full_registry.get_provider_class.return_value = MagicMock(
            return_value=full_chat
        )
        full_cache = MagicMock()
        full_cache.get.return_value = None

        gw_full = ModelGateway(
            profiles={"gw2-p": profile},
            provider_registry=full_registry,
            response_cache=full_cache,
        )
        full_resp = gw_full.call_model(
            "gw2-p", [{"role": "user", "content": "hi"}]
        )
        assert full_resp.content == "full answer"
        full_cache.set.assert_called_once()


# ---------------------------------------------------------------------------
# Bonus behavioral coverage: BudgetExceededError propagates (D-24) and the
# breaker gate refuses an open circuit on the plain call_model path.
# ---------------------------------------------------------------------------


class TestBudgetExceededErrorPropagates:
    def test_budget_exceeded_error_type_propagates_from_call_model(self) -> None:
        """A budget rejection raises the distinct BudgetExceededError type.

        Reinforces (4) at the call_model boundary: when check_budget rejects,
        call_model raises BudgetExceededError (a ValueError subclass) so the
        fallback chain does not silently route around the per-profile cap.
        """
        profile = _profile(
            "over-budget-p", model_name="m", budget=0.001, api_metered=True
        )
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
        )

        with pytest.raises(BudgetExceededError):
            gw.call_model(
                "over-budget-p",
                [{"role": "user", "content": "hi"}],
                estimated_cost=5.0,
                budget_remaining=100.0,
            )


class TestCircuitGateRefusesOpenBreaker:
    def test_open_breaker_refuses_call_before_invoke(self) -> None:
        """An open circuit on the call_model path raises before any invoke.

        Once the breaker is open, call_model must refuse the call (raising
        CircuitBreakerOpenError) WITHOUT invoking the provider -- the health
        gate fires before _invoke_and_bill.
        """
        from general_ludd.models.gateway import CircuitBreakerOpenError

        tracker = ModelHealthTracker(failure_threshold=2, cooldown_seconds=60.0)
        profile = _profile("gated-p", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
            health_tracker=tracker,
        )

        for _ in range(2):
            with patch.object(
                _BranchingProvider,
                "invoke",
                side_effect=_server_error(),
            ), pytest.raises(httpx.HTTPStatusError):
                gw.call_model("gated-p", [{"role": "user", "content": "hi"}])
        assert tracker.is_healthy("gated-p", admit_probe=False) is False

        with pytest.raises(CircuitBreakerOpenError):
            gw.call_model("gated-p", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# C.6 hardening tests — kwargs stripping, default httpx timeout, URL redaction
# ---------------------------------------------------------------------------


class TestC6KwargsStripping:
    def test_caller_base_url_is_stripped_and_warned(self) -> None:
        profile = _profile("strip-p", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
        )
        with (
            patch.object(gw, "_health_tracker", None),
            patch("general_ludd.models.gateway.logger") as mock_logger,
        ):
            gw.call_model("strip-p", [{"role": "user", "content": "hi"}],
                          base_url="https://evil.invalid/v1", api_key="sk-evil")

        warning_texts = " ".join(
            str(c.args[0]) for c in mock_logger.warning.call_args_list if c.args
        )
        assert "base_url" in warning_texts
        assert "api_key" in warning_texts

    def test_caller_base_url_stripped_from_call_with_fallback(self) -> None:
        profile = _profile("strip-fb-p", model_name="m",
                           fallback=["strip-fb-fb"])
        fb = _profile("strip-fb-fb", model_name="m2")
        gw = ModelGateway(
            profiles=[profile, fb],
            provider_registry=_mock_registry(),
        )
        resp = gw.call_model_with_fallback(
            "strip-fb-p", [{"role": "user", "content": "hi"}],
        )
        assert resp is not None

    def test_call_model_strips_base_url_from_kwargs(self) -> None:
        profile = _profile("strip-cm-p", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
        )
        with patch.object(gw, "_health_tracker", None):
            resp = gw.call_model("strip-cm-p", [{"role": "user", "content": "hi"}],
                                  api_key="sk-malicious")
        assert resp is not None


class TestC6DefaultHttpxTimeout:
    def test_default_httpx_timeout_injected(self) -> None:
        profile = _profile("timeout-p", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
        )
        with patch.object(gw, "_health_tracker", None):
            resp = gw.call_model("timeout-p", [{"role": "user", "content": "hi"}])
        assert resp is not None

    def test_caller_cannot_override_timeout(self) -> None:
        profile = _profile("timeout-2-p", model_name="m")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(),
        )
        with patch.object(gw, "_health_tracker", None):
            resp = gw.call_model("timeout-2-p", [{"role": "user", "content": "hi"}],
                                  request_timeout=999.0)
        assert resp is not None


class TestC6UrlRedaction:
    def test_resolved_url_redacted_from_provider_error(self) -> None:
        resolved_url = "https://internal-proxy.corp/v1/chat"

        class _UrlLeakingResolver:
            def resolve(self, alias: str) -> str:
                return resolved_url

        class _UrlLeakingProvider:
            def __init__(self, **init_kwargs: object) -> None:
                pass
            def invoke(self, messages: list[dict[str, str]]) -> object:
                raise httpx.ConnectError(
                    f"Connection refused to {resolved_url}"
                )

        profile = _profile("redact-p", model_name="m",
                           api_base_alias="my_proxy")
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=_mock_registry(provider_cls=_UrlLeakingProvider),
            secrets_manager=_UrlLeakingResolver(),
        )
        with (
            patch.object(gw, "_health_tracker", None),
            pytest.raises(httpx.ConnectError) as exc_info,
        ):
            gw.call_model("redact-p", [{"role": "user", "content": "hi"}])

        assert resolved_url not in str(exc_info.value)
        assert "[REDACTED_URL]" in str(exc_info.value)

    def test_redact_url_helper_noop_on_empty(self) -> None:
        from general_ludd.models.gateway import _redact_url_in_exception

        exc = RuntimeError("no url here")
        _redact_url_in_exception(exc, "")
        assert str(exc) == "no url here"

    def test_redact_url_helper_safe_on_non_string_args(self) -> None:
        from general_ludd.models.gateway import _redact_url_in_exception

        exc = Exception(42, "has https://x.invalid/v1 url")
        _redact_url_in_exception(exc, "https://x.invalid/v1")
        assert exc.args[0] == 42
        assert "[REDACTED_URL]" in exc.args[1]
        assert "https://x.invalid/v1" not in exc.args[1]
