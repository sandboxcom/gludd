"""Focused branch coverage for fail-closed execution-engine guard helpers."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from general_ludd.execution.engine import ExecutionEngine


def _engine(tmp_path: Path, **kwargs: object) -> ExecutionEngine:
    return ExecutionEngine(workspace_path=str(tmp_path), **kwargs)


def test_sandbox_verification_confines_workspace(tmp_path: Path) -> None:
    confined = tmp_path / "confined"
    confined.mkdir()
    enforcer = MagicMock()
    enforcer.confine_path.return_value = confined

    engine = _engine(tmp_path, sandbox_enforcer=enforcer)

    assert engine._verify_sandbox() is None
    assert engine.workspace_path == str(confined)
    assert engine._sandbox_verified is True


@pytest.mark.parametrize("failure_point", ["verify_ready", "confine_path"])
def test_sandbox_verification_fails_closed(tmp_path: Path, failure_point: str) -> None:
    enforcer = MagicMock()
    getattr(enforcer, failure_point).side_effect = RuntimeError("unavailable")
    engine = _engine(tmp_path, sandbox_enforcer=enforcer)

    denial = engine._verify_sandbox()

    assert denial is not None
    assert "Sandbox enforcement failed" in denial
    assert engine._sandbox_verified is False


def test_spend_reserve_fails_closed_when_cap_state_raises(tmp_path: Path) -> None:
    limiter = MagicMock()
    type(limiter).cap_configured = PropertyMock(side_effect=RuntimeError("state"))
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    assert engine._spend_reserve(0.1) == (None, "spend limiter state unavailable")


def test_spend_reserve_allows_uncapped_limiter(tmp_path: Path) -> None:
    limiter = MagicMock(cap_configured=False)
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    assert engine._spend_reserve(None) == (None, None)
    limiter.reserve.assert_not_called()


def test_spend_reserve_reports_reservation_and_remaining_failures(tmp_path: Path) -> None:
    limiter = MagicMock(cap_configured=True)
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    limiter.reserve.side_effect = RuntimeError("reserve")
    assert engine._spend_reserve(0.1) == (None, "spend reservation failed")

    limiter.reserve.side_effect = None
    limiter.reserve.return_value = None
    limiter.remaining.side_effect = RuntimeError("remaining")
    assert engine._spend_reserve(0.1) == (None, "spend limit exceeded")

    limiter.remaining.side_effect = None
    limiter.remaining.return_value = 0.25
    assert engine._spend_reserve(0.1) == (
        None,
        "spend limit exceeded: remaining=$0.250000",
    )


def test_actual_spend_rejects_invalid_text_without_projection(tmp_path: Path) -> None:
    limiter = MagicMock()
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    engine._spend_record_actual(
        "not-a-number",
        reservation_token=None,
        projected_cost_usd=None,
        model="m",
        project_id="p",
    )

    limiter.record.assert_not_called()
    limiter.commit.assert_not_called()


def test_actual_spend_commit_failures_retain_reservation(tmp_path: Path) -> None:
    limiter = MagicMock()
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    limiter.commit.side_effect = RuntimeError("commit")
    engine._spend_record_actual(
        0.2,
        reservation_token="token-1",
        projected_cost_usd=0.3,
        model="m",
        project_id="p",
    )
    limiter.record.assert_not_called()

    limiter.commit.side_effect = None
    limiter.commit.return_value = False
    engine._spend_record_actual(
        0.2,
        reservation_token="token-2",
        projected_cost_usd=0.3,
        model="m",
        project_id="p",
    )
    assert limiter.commit.call_count == 2


def test_actual_spend_record_failure_is_contained(tmp_path: Path) -> None:
    limiter = MagicMock()
    limiter.record.side_effect = RuntimeError("record")
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    engine._spend_record_actual(
        "0.2",
        reservation_token=None,
        projected_cost_usd=None,
        model="m",
        project_id="p",
    )

    limiter.record.assert_called_once()


def test_spend_release_contains_rejections_and_errors(tmp_path: Path) -> None:
    limiter = MagicMock()
    limiter.release.return_value = False
    engine = _engine(tmp_path)
    engine._spend_limiter = limiter

    engine._spend_release("token-1")
    limiter.release.side_effect = RuntimeError("release")
    engine._spend_release("token-2")

    assert limiter.release.call_count == 2


@pytest.mark.parametrize(
    ("guard", "expected"),
    [
        (object(), "budget guard has unknown interface"),
        (
            type("NonDictCheck", (), {"check_all_limits": lambda self, **kwargs: None})(),
            "budget check returned non-dict",
        ),
        (
            type("DeniedCheck", (), {"check_all_limits": lambda self, **kwargs: {"allowed": False}})(),
            "budget exhausted",
        ),
        (
            type("AllowedCharge", (), {"try_charge": lambda self, **kwargs: {"allowed": True}})(),
            None,
        ),
        (
            type("NonDictCharge", (), {"try_charge": lambda self, **kwargs: None})(),
            "budget check returned non-dict",
        ),
        (
            type("DeniedCharge", (), {"try_charge": lambda self, **kwargs: {"allowed": False}})(),
            "budget exhausted",
        ),
    ],
)
def test_budget_guard_interface_contracts(
    tmp_path: Path,
    guard: object,
    expected: str | None,
) -> None:
    assert _engine(tmp_path)._budget_pre_check(guard, projected_cost=0.1) == expected


def test_budget_guard_exceptions_are_denials(tmp_path: Path) -> None:
    class RaisingCheck:
        def check_all_limits(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("check")

    class RaisingCharge:
        def try_charge(self, **kwargs: object) -> dict[str, object]:
            raise RuntimeError("charge")

    engine = _engine(tmp_path)

    assert engine._budget_pre_check(RaisingCheck(), 0.1) == "budget check raised: check"
    assert engine._budget_pre_check(RaisingCharge(), 0.1) == "budget check raised: charge"
