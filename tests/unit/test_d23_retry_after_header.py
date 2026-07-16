"""TDD proof for D-23 fix: 429 Retry-After header threaded through retry sleep.

BUG: gateway.py _before_sleep callback computed backoff via
``policy._compute_backoff(kind, attempt, None, ...)`` — it ALWAYS passed
``None`` for ``retry_after``, so even though TimeoutRetryPolicy._compute_backoff
already knows how to honor a server-supplied Retry-After (returns
``max(retry_after, 1.0)`` for RATE_LIMITED), the gateway never extracted the
header from the 429 response and handed it to the policy. A provider telling
us "retry in 30s" was ignored in favor of the fixed exponential backoff.

FIX: parse the ``Retry-After`` header from the failing 429 response (httpx
HTTPStatusError.response.headers or openai APIStatusError.response.headers)
and pass it through to ``_compute_backoff`` so the server-directed wait is
respected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import asyncio

import httpx
import pytest

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway


def _build_gateway_with_always_failing_primary(
    provider_exc: BaseException,
) -> tuple[ModelGateway, list[int]]:
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


class TestRetryAfterHeaderHonored:
    """A 429 carrying ``Retry-After: <seconds>`` MUST cause the retry sleep to
    be at least that many seconds (per TimeoutRetryPolicy._compute_backoff
    returning ``max(retry_after, 1.0)`` for RATE_LIMITED)."""

    def test_helper_extracts_seconds_from_real_httpx_response(self) -> None:
        from general_ludd.models.gateway import _extract_retry_after_seconds

        response = httpx.Response(
            status_code=429,
            headers={"retry-after": "30"},
            request=httpx.Request("POST", "http://x/y"),
        )
        exc = httpx.HTTPStatusError(
            "429", request=response.request, response=response
        )
        assert _extract_retry_after_seconds(exc) == 30.0
    """A 429 carrying ``Retry-After: <seconds>`` MUST cause the retry sleep to
    be at least that many seconds (per TimeoutRetryPolicy._compute_backoff
    returning ``max(retry_after, 1.0)`` for RATE_LIMITED)."""

    def test_retry_after_seconds_header_threads_to_sleep(self) -> None:
        retry_after_seconds = 30
        response = httpx.Response(
            status_code=429,
            headers={"retry-after": str(retry_after_seconds)},
            request=httpx.Request("POST", "http://x/y"),
        )
        exc = httpx.HTTPStatusError("429", request=response.request, response=response)

        gateway, _call_count = _build_gateway_with_always_failing_primary(exc)

        sleeps: list[float] = []

        def _capture_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        with patch("time.sleep", side_effect=_capture_sleep), pytest.raises(
            httpx.HTTPStatusError
        ):
            asyncio.run(gateway.call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert sleeps, "time.sleep was never called by the retry path"
        # _compute_backoff returns max(retry_after, 1.0) for RATE_LIMITED when
        # retry_after is provided. Every non-zero sleep must be >=
        # retry_after_seconds; if the header is ignored the backoff collapses
        # to the jittered base (with base_backoff_seconds=0.0 that is ~0).
        # (A stray time.sleep(0) from unrelated infra may appear in the list;
        # filter zero-duration calls before asserting.)
        nonzero = [s for s in sleeps if s > 0]
        assert nonzero, f"all sleeps were 0; full list: {sleeps}"
        for s in nonzero:
            assert s >= retry_after_seconds, (
                f"Retry-After header ({retry_after_seconds}s) was not honored: "
                f"observed sleep={s}. The header is not threaded to _compute_backoff."
            )

    def test_no_retry_after_header_falls_back_to_backoff(self) -> None:
        """Without a Retry-After header, behavior is unchanged: the fixed
        exponential backoff runs (and with base_backoff_seconds=0.0 the sleep
        is small, definitely < the 30s a header would impose)."""
        response = httpx.Response(
            status_code=429,
            headers={},
            request=httpx.Request("POST", "http://x/y"),
        )
        exc = httpx.HTTPStatusError("429", request=response.request, response=response)

        gateway, _call_count = _build_gateway_with_always_failing_primary(exc)

        sleeps: list[float] = []

        def _capture_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        with patch("time.sleep", side_effect=_capture_sleep), pytest.raises(
            httpx.HTTPStatusError
        ):
            asyncio.run(cast(Any, gateway).call_model_with_retry(
                "primary",
                [{"role": "user", "content": "hi"}],
                base_backoff_seconds=0.0,
            )

        assert sleeps, "time.sleep was never called by the retry path"
        # Without a header we must NOT invent a 30s wait — backoff should be
        # the small jittered exponential, well under any plausible Retry-After.
        assert max(sleeps) < 5.0, (
            f"Without Retry-After header the backoff should be the small "
            f"jittered exponential, got max sleep={max(sleeps)}"
        )
