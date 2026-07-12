"""Unit tests for budget_pre_check dispatcher in budget_guard_check.py."""

from __future__ import annotations

from general_ludd.budget_guard_check import budget_pre_check


class _FakeCheckAllLimits:
    def __init__(self, verdict: dict | object, raise_on_call: Exception | None = None):
        self._verdict = verdict
        self._raise = raise_on_call
        self.call_count = 0

    def check_all_limits(self, estimated_cost: float = 0.0) -> dict:
        self.call_count += 1
        if self._raise:
            raise self._raise
        if isinstance(self._verdict, dict):
            return self._verdict
        return self._verdict  # type: ignore[return-value]


class _FakeWouldExceed:
    def __init__(self, over: bool = False, remaining: float | None = None, raise_on_call: Exception | None = None):
        self._over = over
        self._remaining = remaining
        self._raise = raise_on_call
        self.call_count = 0

    def would_exceed(self, projected_usd: float) -> bool:
        self.call_count += 1
        if self._raise:
            raise self._raise
        return self._over

    def remaining(self, now: float | None = None) -> float:
        if self._remaining is not None:
            return self._remaining
        return 0.0


class _FakeUnknownGuard:
    pass


# --- None guard ---


def test_none_guard_allows() -> None:
    assert budget_pre_check(None) is None


def test_none_guard_with_projected_cost() -> None:
    assert budget_pre_check(None, projected_cost=500.0) is None


# --- check_all_limits guard ---


def test_check_all_limits_allowed() -> None:
    guard = _FakeCheckAllLimits({"allowed": True})
    assert budget_pre_check(guard) is None


def test_check_all_limits_allowed_with_projection() -> None:
    guard = _FakeCheckAllLimits({"allowed": True})
    assert budget_pre_check(guard, projected_cost=5.0) is None


def test_check_all_limits_denied_with_reason() -> None:
    guard = _FakeCheckAllLimits({"allowed": False, "reason": "budget exhausted"})
    result = budget_pre_check(guard)
    assert result == "budget exhausted"


def test_check_all_limits_denied_no_reason_defaults() -> None:
    guard = _FakeCheckAllLimits({"allowed": False})
    result = budget_pre_check(guard)
    assert result == "budget exhausted"


def test_check_all_limits_raises_exception() -> None:
    guard = _FakeCheckAllLimits({}, raise_on_call=ValueError("boom"))
    result = budget_pre_check(guard)
    assert "boom" in str(result)


def test_check_all_limits_returns_non_dict() -> None:
    guard = _FakeCheckAllLimits("not-a-dict")
    result = budget_pre_check(guard)
    assert "non-dict" in str(result)


def test_check_all_limits_returns_none() -> None:
    guard = _FakeCheckAllLimits(None)
    result = budget_pre_check(guard)
    assert "non-dict" in str(result)


# --- would_exceed guard ---


def test_would_exceed_false_allows() -> None:
    guard = _FakeWouldExceed(over=False)
    assert budget_pre_check(guard) is None


def test_would_exceed_false_with_projection() -> None:
    guard = _FakeWouldExceed(over=False)
    assert budget_pre_check(guard, projected_cost=10.0) is None


def test_would_exceed_true_with_remaining() -> None:
    guard = _FakeWouldExceed(over=True, remaining=3.14)
    result = budget_pre_check(guard)
    assert result == "spend limit exceeded: remaining=$3.140000"


def test_would_exceed_true_without_remaining() -> None:
    g = _FakeWouldExceed(over=True, remaining=None)
    g.remaining = None
    result = budget_pre_check(g)
    assert result == "spend limit exceeded"


def test_would_exceed_raises() -> None:
    guard = _FakeWouldExceed(raise_on_call=RuntimeError("cap"))
    result = budget_pre_check(guard)
    assert "cap" in str(result)


# --- unknown guard ---


def test_unknown_guard_fail_closed() -> None:
    guard = _FakeUnknownGuard()
    result = budget_pre_check(guard)
    assert "unknown interface" in str(result)


# --- projected_cost forwarding ---


def test_would_exceed_receives_projected_cost() -> None:
    guard = _FakeWouldExceed(over=False)
    budget_pre_check(guard, projected_cost=123.45)
    assert guard.call_count == 1


def test_check_all_limits_receives_projected_cost() -> None:
    guard = _FakeCheckAllLimits({"allowed": True})
    budget_pre_check(guard, projected_cost=50.0)
    assert guard.call_count == 1
