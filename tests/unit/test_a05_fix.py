"""TDD proof for A-05 fix: gateway _is_retryable kind-aware retry cap.

BUG: gateway.py _is_retryable used a blanket ``max_retries=3`` hard cap for ALL
retry kinds, including overload kinds (PROVIDER_ERROR, RATE_LIMITED) which have
a dedicated ``overload_max_retries=10`` budget in TimeoutRetryPolicy. The
blanket cap defeated the overload budget: overload errors stopped retrying at
attempt 4 (after max_retries=3) instead of attempt 10, never giving an
overloaded provider time to recover.

FIX: the hard cap is now kind-aware. For overload kinds the cap is
``policy._overload_max_retries`` (default 10); for transient kinds the cap
stays ``max_retries`` (default 3).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _build_gateway_with_always_failing_primary(
    provider_exc: BaseException,
) -> tuple[object, list[int]]:
    """Return (gateway, call_counter).

    The gateway has a single primary profile with NO fallback. Its provider
    always raises ``provider_exc``. ``call_counter[0]`` tracks how many times
    the provider class was instantiated (= number of primary attempts).
    """
    from general_ludd.models.gateway import ModelGateway, ModelProfile
    from general_ludd.models.timeout_detector import ModelHealthTracker

    tracker = ModelHealthTracker()
    primary = ModelProfile(
        model_profile_id="primary",
        provider="openai",
        model_name="m1",
        enabled=True,
    )
    gateway = ModelGateway(profiles=[primary], health_tracker=tracker)

    call_count: list[int] = [0]

    def get_provider(_name: str) -> MagicMock:
        call_count[0] += 1
        m = MagicMock()
        m.return_value.invoke.side_effect = provider_exc
        return m

    gateway._registry = MagicMock(
        is_installed=MagicMock(return_value=True),
        get_provider_class=get_provider,
    )
    return gateway, call_count


class TestOverloadKindAwareCap:
    """PROVIDER_ERROR / RATE_LIMITED must use overload_max_retries (10), not
    max_retries (3)."""

    def test_provider_error_retries_through_10_attempts(self) -> None:
        """503 PROVIDER_ERROR retries through 10 attempts (overload budget),
        not 4 (buggy blanket max_retries cap)."""
        gateway, call_count = _build_gateway_with_always_failing_primary(
            httpx.HTTPStatusError(
                "503",
                request=MagicMock(),
                response=MagicMock(status_code=503),
            ),
        )

        with patch("time.sleep"), pytest.raises(httpx.HTTPStatusError):
            cast(Any, gateway).call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert call_count[0] == 10, (
            f"PROVIDER_ERROR must retry through 10 attempts (overload_max_retries=10), "
            f"got {call_count[0]}. The kind-aware cap is missing or broken."
        )

    def test_rate_limited_retries_through_10_attempts(self) -> None:
        """429 RATE_LIMITED retries through 10 attempts (overload budget)."""
        gateway, call_count = _build_gateway_with_always_failing_primary(
            httpx.HTTPStatusError(
                "429",
                request=MagicMock(),
                response=MagicMock(status_code=429),
            ),
        )

        with patch("time.sleep"), pytest.raises(httpx.HTTPStatusError):
            cast(Any, gateway).call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert call_count[0] == 10, (
            f"RATE_LIMITED must retry through 10 attempts (overload_max_retries=10), "
            f"got {call_count[0]}."
        )


class TestTransientKindStillCappedAtMaxRetries:
    """Transient kinds (CONNECTION_TIMEOUT, READ_TIMEOUT) must STILL cap at
    failover_after_retries (3). The kind-aware fix must not loosen the
    fast-failover path."""

    def test_connection_timeout_caps_at_failover_after(self) -> None:
        gateway, call_count = _build_gateway_with_always_failing_primary(
            httpx.ConnectTimeout("connect timeout"),
        )

        with patch("time.sleep"), pytest.raises(httpx.ConnectTimeout):
            cast(Any, gateway).call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert call_count[0] == 3, (
            f"CONNECTION_TIMEOUT must still cap at 3 attempts (failover_after_retries=3), "
            f"got {call_count[0]}. The transient path was loosened."
        )

    def test_read_timeout_caps_at_failover_after(self) -> None:
        gateway, call_count = _build_gateway_with_always_failing_primary(
            httpx.ReadTimeout("read timeout"),
        )

        with patch("time.sleep"), pytest.raises(httpx.ReadTimeout):
            cast(Any, gateway).call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert call_count[0] == 3, (
            f"READ_TIMEOUT must still cap at 3 attempts (failover_after_retries=3), "
            f"got {call_count[0]}."
        )
