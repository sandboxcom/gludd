"""Tests for converting FreeLLMAPI quirks into bounded Gludd failover hints.

Privacy and ownership contract: only content-free quirk slugs admitted by the
signed catalog may influence Gludd policy, and the resulting hints are bounded
numeric overrides that Gludd validates before applying to its own retry/failover
graph.  Catalog prose, endpoints, and credentials never cross this boundary.
"""

from __future__ import annotations

import pytest

from general_ludd.models.freellmapi_candidates import FreeModelCandidateSeed
from general_ludd.models.freellmapi_catalog import CatalogLimits
from general_ludd.models.freellmapi_failover_hints import (
    FailoverPolicyHints,
    failover_hints_from_candidate,
)
from general_ludd.models.timeout_detector import TimeoutRetryPolicy


def _seed(*quirks: str) -> FreeModelCandidateSeed:
    return FreeModelCandidateSeed(
        platform="groq",
        model_id="acme/coder-model",
        display_name="Acme Coder",
        intelligence_rank=4,
        speed_rank=2,
        size_label="70B",
        limits=CatalogLimits(rpm=10, rpd=100, tpm=2_000, tpd=20_000),
        monthly_token_budget="1M",
        context_window=32_768,
        supports_vision=False,
        supports_tools=True,
        catalog_version="2026.09.15",
        catalog_payload_sha256="a" * 64,
        quirk_slugs=quirks,
    )


def test_no_quirks_yields_empty_hints() -> None:
    hints = failover_hints_from_candidate(_seed())

    assert hints == FailoverPolicyHints()
    assert hints.max_retries is None
    assert hints.failover_after_retries is None
    assert hints.base_backoff_seconds is None


def test_fast_failover_quirk_lowers_failover_threshold() -> None:
    hints = failover_hints_from_candidate(_seed("fast-failover"))

    assert hints.failover_after_retries == 2
    assert hints.max_retries is None
    assert hints.base_backoff_seconds is None


def test_extra_retries_quirk_raises_retry_ceiling() -> None:
    hints = failover_hints_from_candidate(_seed("extra-retries"))

    assert hints.max_retries == 5
    assert hints.failover_after_retries is None
    assert hints.base_backoff_seconds is None


def test_long_backoff_quirk_increases_base_backoff() -> None:
    hints = failover_hints_from_candidate(_seed("long-backoff"))

    assert hints.base_backoff_seconds == 4.0
    assert hints.max_retries is None
    assert hints.failover_after_retries is None


def test_combined_quirks_merge_conservatively() -> None:
    hints = failover_hints_from_candidate(_seed("fast-failover", "extra-retries", "long-backoff"))

    assert hints.failover_after_retries == 2
    assert hints.max_retries == 5
    assert hints.base_backoff_seconds == 4.0


def test_unknown_quirks_are_ignored_and_cannot_invent_policy() -> None:
    hints = failover_hints_from_candidate(_seed("unknown-quirk", "also-unknown", "fast-failover"))

    assert hints.failover_after_retries == 2
    assert hints.max_retries is None
    assert hints.base_backoff_seconds is None


def test_hints_are_bounded_and_fail_closed_on_extreme_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.models.freellmapi_failover_hints as hints_module

    monkeypatch.setattr(hints_module, "_MAX_RETRIES", 3)
    hints = failover_hints_from_candidate(_seed("extra-retries"))

    assert hints.max_retries == 3


def test_hints_can_materialize_a_gludd_retry_policy() -> None:
    hints = failover_hints_from_candidate(_seed("fast-failover", "extra-retries", "long-backoff"))

    policy = TimeoutRetryPolicy(
        max_retries=hints.max_retries,
        failover_after_retries=hints.failover_after_retries,
        base_backoff_seconds=hints.base_backoff_seconds,
    )

    assert policy._max_retries == 5
    assert policy._failover_after == 2
    assert policy._base_backoff == 4.0


def test_input_is_typed_and_fails_closed() -> None:
    with pytest.raises(ValueError, match="candidate"):
        failover_hints_from_candidate(object())  # type: ignore[arg-type]


def test_only_content_free_slugs_influence_hints() -> None:
    base = _seed("fast-failover")
    different_metadata = FreeModelCandidateSeed(
        platform=base.platform,
        model_id=base.model_id,
        display_name=" Leaked endpoint https://secret.example token abc123 ",
        intelligence_rank=base.intelligence_rank,
        speed_rank=base.speed_rank,
        size_label=base.size_label,
        limits=base.limits,
        monthly_token_budget="leaked-token",
        context_window=base.context_window,
        supports_vision=base.supports_vision,
        supports_tools=base.supports_tools,
        catalog_version=base.catalog_version,
        catalog_payload_sha256=base.catalog_payload_sha256,
        quirk_slugs=base.quirk_slugs,
    )

    assert failover_hints_from_candidate(base) == failover_hints_from_candidate(different_metadata)
