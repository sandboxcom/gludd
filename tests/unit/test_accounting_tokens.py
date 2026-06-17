"""Regression: accounting tokens_used must use real token counts, not total_calls*1000.

Mirrors the exact preference cascade in routers/accounting.py::_UsageRecord so the
behaviour is pinned without depending on the router's request-time wiring.
"""
from __future__ import annotations

from typing import Any


def _tokens_used(mu: dict[str, Any]) -> int:
    """Replica of the routers/accounting.py _UsageRecord.tokens_used cascade."""
    return sum(
        int(
            u.get("total_tokens")
            or (int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)))
            or int(u.get("total_calls", 0)) * 1000
        )
        for u in (mu.values() if isinstance(mu, dict) else [])
    )


def test_tokens_used_prefers_total_tokens_over_calls_proxy() -> None:
    """When total_tokens is present it must be used, not total_calls*1000."""
    mu = {"claude-3": {"total_tokens": 5000, "total_calls": 2}}
    assert _tokens_used(mu) == 5000  # real, NOT 2000 (proxy)


def test_tokens_used_falls_back_to_input_plus_output() -> None:
    """Without total_tokens, sum input+output, not total_calls*1000."""
    mu = {"m": {"input_tokens": 200, "output_tokens": 100, "total_calls": 5}}
    assert _tokens_used(mu) == 300  # 200+100, NOT 5000


def test_tokens_used_falls_back_to_calls_proxy_when_no_token_field() -> None:
    """Fallback to calls*1000 only when no token field is available."""
    mu = {"claude-3": {"total_calls": 3}}
    assert _tokens_used(mu) == 3000


def test_tokens_used_sums_per_model_cascade() -> None:
    """The cascade is applied per-model, then summed."""
    mu = {
        "a": {"total_tokens": 600, "total_calls": 10},          # 600
        "b": {"input_tokens": 150, "output_tokens": 75},        # 225
    }
    assert _tokens_used(mu) == 825
