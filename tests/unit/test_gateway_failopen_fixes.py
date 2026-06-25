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
        provider="openai",
        model_name="gpt-4",
    )
    fallback = ModelProfile(
        model_profile_id="fallback",
        enabled=True,
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
        with patch.object(
            gw,
            "call_model",
            side_effect=SSRFRejectionError("SSRF guard: refusing blocked URL"),
        ):
            with pytest.raises(SSRFRejectionError):
                gw._try_call_model("primary", [{"role": "user", "content": "hi"}])

    def test_try_call_model_still_swallows_plain_value_error(self):
        # The generic ValueError fail-soft path is preserved: a non-SSRF
        # ValueError still returns None (routes to a fallback).
        gw, _ = _make_gateway()
        with patch.object(gw, "call_model", side_effect=ValueError("boom")):
            assert (
                gw._try_call_model("primary", [{"role": "user", "content": "hi"}])
                is None
            )


class TestWalkFallbacksBudgetPropagates:
    def test_walk_fallbacks_propagates_budget_exceeded(self):
        # F-F: BudgetExceededError on the fallback path MUST propagate, not be
        # swallowed by the bare ``except Exception`` and continued past (which
        # would spend beyond the per-profile budget ceiling).
        gw, _ = _make_gateway()
        with patch.object(
            gw,
            "_call_fallback",
            side_effect=BudgetExceededError("over budget"),
        ):
            with pytest.raises(BudgetExceededError):
                gw._walk_fallbacks(
                    ["fallback"], [{"role": "user", "content": "hi"}]
                )

    def test_walk_fallbacks_still_swallows_generic_exception(self):
        # Non-budget failures still fall through to the next fallback / return
        # (None, last_exc); the budget re-raise must not break that behavior.
        gw, _ = _make_gateway()
        err = RuntimeError("transient")
        with patch.object(gw, "_call_fallback", side_effect=err):
            resp, last_exc = gw._walk_fallbacks(
                ["fallback"], [{"role": "user", "content": "hi"}]
            )
        assert resp is None
        assert last_exc is err
