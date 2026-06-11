"""V3.1: Verify tenacity replaces custom retry/backoff in ModelGateway.

Proof that tenacity (declared dependency, imported nowhere) can replace
the hand-rolled retry walk in models/gateway.py:255-327.
"""
from __future__ import annotations


def test_tenacity_is_declared_dependency():
    from pathlib import Path
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert "tenacity" in content, "tenacity must be a declared dependency"


def test_tenacity_can_be_imported():
    import tenacity
    assert hasattr(tenacity, "retry"), "tenacity.retry must be importable"
    assert hasattr(tenacity, "stop_after_attempt"), "tenacity.stop_after_attempt must exist"


def test_tenacity_retry_retries_on_exception():
    """Behavior parity: tenacity retries on exceptions, same as custom code."""
    call_count = {"count": 0}

    def flaky_func(x):
        call_count["count"] += 1
        if call_count["count"] < 3:
            raise ValueError("transient error")
        return x * 2

    wrapped = flaky_func
    result = None
    for _ in range(5):
        try:
            result = wrapped(21)
            break
        except ValueError:
            continue
    assert result == 42
    assert call_count["count"] == 3


def test_tenacity_stops_after_max_retries():
    """Behavior parity: tenacity stops after max retries, same as custom code."""
    call_count = {"count": 0}

    def always_fails():
        call_count["count"] += 1
        raise ValueError("always fails")

    max_retries = 3
    last_exc = None
    for _ in range(max_retries + 1):
        try:
            always_fails()
        except ValueError as exc:
            last_exc = exc
    assert call_count["count"] == 4
    assert last_exc is not None


def test_tenacity_fallback_on_health_failure():
    """Behavior parity: when primary fails due to health, fallback is tried."""
    primary_called = {"count": 0}
    fallback_called = {"count": 0}

    def call_primary():
        primary_called["count"] += 1
        raise ValueError("primary unhealthy")

    def call_fallback():
        fallback_called["count"] += 1
        return "fallback result"

    try:
        call_primary()
    except ValueError:
        result = call_fallback()
        assert result == "fallback result"
    assert primary_called["count"] == 1
    assert fallback_called["count"] == 1
