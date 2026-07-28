"""Regression tests for two gateway fail-open security fixes.

F-E (SSRF fail-open): ``_try_call_model`` previously caught the SSRF-guard
ValueError in its bare ``except (ValueError, ImportError): return None`` and
silently fell open to the next fallback profile, masking the egress block. The
SSRF guard now raises the distinct ``SSRFRejectionError`` and ``_try_call_model``
re-raises it instead of swallowing it.

F-F (budget bypass on fallback path): ``_walk_fallbacks`` previously caught
``BudgetExceededError`` in its bare ``except Exception`` and continued to the
next fallback, spending past the per-profile budget ceiling. It now re-raises
``BudgetExceededError`` so the budget gate is enforced on the fallback path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.models.gateway import (
    BudgetExceededError,
    ModelGateway,
    ModelProfile,
    SSRFRejectionError,
)
from general_ludd.models.provider_registry import ProviderRegistry


def _make_gateway() -> tuple[ModelGateway, ProviderRegistry]:
    reg = ProviderRegistry()
    reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
    primary = ModelProfile(
        model_profile_id="primary",
        enabled=True,
        api_metered=False,
        provider="openai",
        model_name="gpt-4",
    )
    fallback = ModelProfile(
        model_profile_id="fallback",
        enabled=True,
        api_metered=False,
        provider="openai",
        model_name="gpt-4-fb",
    )
    gw = ModelGateway(profiles=[primary, fallback], provider_registry=reg)
    return gw, reg


class TestSSRFRejectionPropagates:
    def test_ssrf_rejection_is_value_error_subclass(self):
        # Subclassing ValueError keeps existing ``except ValueError`` callers
        # working while allowing the distinct re-raise.
        assert issubclass(SSRFRejectionError, ValueError)

    def test_try_call_model_propagates_ssrf_rejection(self):
        # F-E: an SSRF egress rejection MUST propagate out of _try_call_model,
        # NOT be swallowed into a silent None (which would fall open to the next
        # fallback profile and mask the egress block).
        gw, _ = _make_gateway()
        with (
            patch.object(
                gw,
                "call_model",
                side_effect=SSRFRejectionError("SSRF guard: refusing blocked URL"),
            ),
            pytest.raises(SSRFRejectionError),
        ):
            gw._try_call_model("primary", [{"role": "user", "content": "hi"}])

    def test_try_call_model_still_swallows_plain_value_error(self):
        # The generic ValueError fail-soft path is preserved: a non-SSRF
        # ValueError still returns None (routes to a fallback).
        gw, _ = _make_gateway()
        with patch.object(gw, "call_model", side_effect=ValueError("boom")):
            assert gw._try_call_model("primary", [{"role": "user", "content": "hi"}]) is None


class TestWalkFallbacksBudgetPropagates:
    def test_walk_fallbacks_propagates_budget_exceeded(self):
        # F-F: BudgetExceededError on the fallback path MUST propagate, not be
        # swallowed by the bare ``except Exception`` and continued past (which
        # would spend beyond the per-profile budget ceiling).
        gw, _ = _make_gateway()
        with (
            patch.object(
                gw,
                "_call_fallback",
                side_effect=BudgetExceededError("over budget"),
            ),
            pytest.raises(BudgetExceededError),
        ):
            gw._walk_fallbacks(["fallback"], [{"role": "user", "content": "hi"}])

    def test_walk_fallbacks_still_swallows_generic_exception(self):
        # Non-budget failures still fall through to the next fallback / return
        # (None, last_exc, attempts); the budget re-raise must not break that
        # behavior. The attempts list records the failed hop (structured
        # all-providers-down error, docs/audit/FAILOVER_GAPS.md).
        gw, _ = _make_gateway()
        err = RuntimeError("transient")
        with patch.object(gw, "_call_fallback", side_effect=err):
            resp, last_exc, attempts = gw._walk_fallbacks(["fallback"], [{"role": "user", "content": "hi"}])
        assert resp is None
        assert last_exc is err
        assert attempts == [{"profile_id": "fallback", "reason": "transient"}]


_MSG = [{"role": "user", "content": "hi"}]


class TestWalkFallbacksSSRFPropagates:
    """Defense-in-depth: an SSRFRejectionError raised while walking the fallback
    chain MUST propagate and hard-stop the walk, NOT be re-swallowed by the bare
    ``except Exception`` and routed onward to the next profile (which would mask
    the egress block). Mirrors the BudgetExceededError re-raise at the same site.
    """

    def test_walk_fallbacks_propagates_ssrf_rejection(self):
        # Site: _walk_fallbacks. First fallback's call raises SSRF → propagate;
        # the SECOND fallback must never be attempted.
        gw, _ = _make_gateway()
        called: list[str] = []

        def fake_call_fallback(fb_id, messages, **kwargs):
            called.append(fb_id)
            if fb_id == "fb1":
                raise SSRFRejectionError("SSRF guard: refusing blocked URL")
            return object()

        with patch.object(gw, "_call_fallback", side_effect=fake_call_fallback), pytest.raises(SSRFRejectionError):
            gw._walk_fallbacks(["fb1", "fb2"], _MSG)

        assert called == ["fb1"]
        assert "fb2" not in called


class TestCallModelWithFallbackSSRFPropagates:
    """The two ``except Exception`` sites inside call_model_with_fallback (the
    primary attempt and the fallback loop) must NOT re-swallow an SSRF rejection
    re-raised by _try_call_model; it must hard-stop the chain.
    """

    def test_primary_ssrf_propagates_and_fallback_not_tried(self):
        # Site: call_model_with_fallback primary attempt. Primary raises SSRF →
        # propagate; the fallback profile's call is NEVER invoked.
        gw, _ = _make_gateway()
        called: list[str] = []

        def fake_call_model(pid, messages, **kwargs):
            called.append(pid)
            if pid == "primary":
                raise SSRFRejectionError("SSRF guard: refusing blocked URL")
            return object()

        with patch.object(gw, "call_model", side_effect=fake_call_model), pytest.raises(SSRFRejectionError):
            gw.call_model_with_fallback("primary", _MSG, fallback_profiles=["fallback"])

        assert called == ["primary"]
        assert "fallback" not in called

    def test_fallback_loop_ssrf_propagates_and_next_not_tried(self):
        # Site: call_model_with_fallback fallback loop. Primary fails soft
        # (plain ValueError → None), first fallback raises SSRF → propagate; the
        # SECOND fallback must never be attempted.
        gw, _ = _make_gateway()
        called: list[str] = []

        def fake_call_model(pid, messages, **kwargs):
            called.append(pid)
            if pid == "primary":
                raise ValueError("transient")  # fail-soft → _try returns None
            if pid == "fb1":
                raise SSRFRejectionError("SSRF guard: refusing blocked URL")
            return object()

        with patch.object(gw, "call_model", side_effect=fake_call_model), pytest.raises(SSRFRejectionError):
            gw.call_model_with_fallback("primary", _MSG, fallback_profiles=["fb1", "fb2"])

        assert called == ["primary", "fb1"]
        assert "fb2" not in called

    def test_generic_error_still_falls_through_to_fallback(self):
        # Regression: a non-security transient error on the primary MUST still
        # fall through to the next profile (swallow behavior preserved for
        # non-SSRF/non-budget errors).
        gw, _ = _make_gateway()
        sentinel = object()
        called: list[str] = []

        def fake_call_model(pid, messages, **kwargs):
            called.append(pid)
            if pid == "primary":
                raise RuntimeError("transient")
            return sentinel

        with patch.object(gw, "call_model", side_effect=fake_call_model):
            result = gw.call_model_with_fallback("primary", _MSG, fallback_profiles=["fallback"])

        assert result is sentinel
        assert called == ["primary", "fallback"]
