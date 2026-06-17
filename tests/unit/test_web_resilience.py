"""Offline tests for WebResilience — retry/backoff + per-host circuit breaker."""

from __future__ import annotations

import httpx
import pytest

from general_ludd.models.timeout_detector import TimeoutKind
from general_ludd.web.policy import WebPolicy
from general_ludd.web.resilience import (
    CircuitOpenError,
    RetryableStatusError,
    WebResilience,
    classify_exception,
    is_retryable,
    web_error_for,
)
from general_ludd.web.safe_fetch import SafeFetchError
from general_ludd.web.types import WebError


def _no_sleep(res: WebResilience) -> WebResilience:
    res._sleep = lambda _s: None
    return res


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.com/")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("err", request=req, response=resp)


def test_retryable_classification() -> None:
    assert is_retryable(httpx.ConnectError("down"))
    assert is_retryable(httpx.ReadTimeout("slow"))
    assert is_retryable(_status_error(503))
    assert is_retryable(_status_error(429))
    assert not is_retryable(_status_error(403))
    assert not is_retryable(_status_error(404))
    assert not is_retryable(SafeFetchError(WebError.SSRF_BLOCKED, "x", "u"))


def test_retries_then_succeeds() -> None:
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=3)))
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("transient")
        return "ok"

    assert res.run("example.com", fn) == "ok"
    assert calls["n"] == 3


def test_retry_exhaustion_reraises() -> None:
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=2)))

    def fn() -> str:
        raise httpx.ReadTimeout("always slow")

    with pytest.raises(httpx.ReadTimeout):
        res.run("example.com", fn)


def test_non_retryable_surfaced_immediately() -> None:
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=3)))
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise _status_error(403)

    with pytest.raises(httpx.HTTPStatusError):
        res.run("example.com", fn)
    assert calls["n"] == 1  # NOT retried


def test_breaker_opens_after_threshold() -> None:
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=1, breaker_failure_threshold=3)))

    def boom() -> str:
        raise httpx.ConnectError("down")

    # Three consecutive failed single-attempt runs trip the breaker.
    for _ in range(3):
        with pytest.raises(httpx.ConnectError):
            res.run("dead.example.com", boom)

    # The next call short-circuits with CircuitOpenError WITHOUT calling fn.
    called = {"n": 0}

    def tracked() -> str:
        called["n"] += 1
        return "should-not-run"

    with pytest.raises(CircuitOpenError):
        res.run("dead.example.com", tracked)
    assert called["n"] == 0


def test_web_error_mapping() -> None:
    assert web_error_for(CircuitOpenError("h")) == WebError.CIRCUIT_OPEN
    assert web_error_for(httpx.ConnectError("x")) == WebError.OFFLINE
    assert web_error_for(SafeFetchError(WebError.REDIRECT_LIMIT, "x", "u")) == WebError.REDIRECT_LIMIT
    assert web_error_for(_status_error(503)) == WebError.HTTP_5XX


# -- RetryableStatusError: the seam that closes the "retry transient 5xx/429"
#    contract that a bare structured-return silently violated ------------------
def test_retryable_status_error_is_retryable_and_classified() -> None:
    assert is_retryable(RetryableStatusError(503))
    assert is_retryable(RetryableStatusError(429))
    assert classify_exception(RetryableStatusError(503)) == TimeoutKind.PROVIDER_ERROR
    assert classify_exception(RetryableStatusError(429)) == TimeoutKind.RATE_LIMITED
    assert web_error_for(RetryableStatusError(503)) == WebError.HTTP_5XX
    assert web_error_for(RetryableStatusError(429)) == WebError.HTTP_4XX


def test_retryable_status_retried_then_reraised() -> None:
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=3)))
    calls = {"n": 0}

    def fn() -> str:
        calls["n"] += 1
        raise RetryableStatusError(503)

    with pytest.raises(RetryableStatusError):
        res.run("up-but-5xx.example.com", fn)
    assert calls["n"] == 3  # retried up to max_attempts


def test_constant_5xx_host_trips_breaker() -> None:
    """A host that is UP but returns a constant 5xx must trip the per-host breaker
    — the gap that existed while 5xx never raised (so record_event never fired)."""
    res = _no_sleep(WebResilience(WebPolicy(max_attempts=1, breaker_failure_threshold=3)))

    def boom() -> str:
        raise RetryableStatusError(503)

    for _ in range(3):
        with pytest.raises(RetryableStatusError):
            res.run("dead-but-responding.example.com", boom)

    called = {"n": 0}

    def tracked() -> str:
        called["n"] += 1
        return "should-not-run"

    with pytest.raises(CircuitOpenError):
        res.run("dead-but-responding.example.com", tracked)
    assert called["n"] == 0  # breaker short-circuited before the call
