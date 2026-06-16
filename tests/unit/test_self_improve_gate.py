"""Unit tests for SelfImproveGate (F8 — human gate + runaway cap)."""

from __future__ import annotations

import pytest

from general_ludd.controllers.self_improve_gate import (
    DEFAULT_MAX_OPEN,
    SelfImproveGate,
)
from general_ludd.schemas.todo import TodoStatus


def _proposals(n: int) -> list[dict[str, object]]:
    return [{"title": f"fix {i}", "work_type": "test"} for i in range(n)]


class TestInitialStatus:
    def test_default_gates_to_approval_required(self) -> None:
        gate = SelfImproveGate()  # auto_queue defaults to False
        assert gate.initial_status == TodoStatus.APPROVAL_REQUIRED.value

    def test_auto_queue_goes_straight_to_queued(self) -> None:
        gate = SelfImproveGate(auto_queue=True)
        assert gate.initial_status == TodoStatus.QUEUED.value

    def test_admitted_proposals_carry_the_initial_status(self) -> None:
        gate = SelfImproveGate(auto_queue=False, max_open=10)
        decision = gate.admit(_proposals(2), open_count=0)
        assert len(decision.admitted) == 2
        for todo in decision.admitted:
            assert todo["status"] == TodoStatus.APPROVAL_REQUIRED.value


class TestOpenCap:
    def test_cap_respected_partial_admit(self) -> None:
        # 8 already open, cap 10 -> only 2 of the 5 proposals fit.
        gate = SelfImproveGate(max_open=10)
        decision = gate.admit(_proposals(5), open_count=8)
        assert decision.headroom == 2
        assert len(decision.admitted) == 2
        assert len(decision.rejected) == 3

    def test_cap_reached_admits_nothing(self) -> None:
        gate = SelfImproveGate(max_open=10)
        decision = gate.admit(_proposals(3), open_count=10)
        assert decision.admitted == []
        assert len(decision.rejected) == 3

    def test_over_cap_still_admits_nothing(self) -> None:
        gate = SelfImproveGate(max_open=10)
        decision = gate.admit(_proposals(3), open_count=15)
        assert decision.headroom == 0
        assert decision.admitted == []

    def test_zero_cap_pauses_all(self) -> None:
        gate = SelfImproveGate(max_open=0)
        decision = gate.admit(_proposals(4), open_count=0)
        assert decision.admitted == []
        assert len(decision.rejected) == 4

    def test_default_cap_is_ten(self) -> None:
        gate = SelfImproveGate()
        assert gate.headroom(open_count=0) == DEFAULT_MAX_OPEN

    def test_negative_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            SelfImproveGate(max_open=-1)


class TestNonMutating:
    def test_admit_does_not_mutate_input_proposals(self) -> None:
        gate = SelfImproveGate(auto_queue=True)
        proposals = _proposals(1)
        gate.admit(proposals, open_count=0)
        assert "status" not in proposals[0]

    def test_order_preserved_in_partial_admit(self) -> None:
        gate = SelfImproveGate(max_open=3)
        decision = gate.admit(_proposals(5), open_count=1)  # headroom 2
        assert [p["title"] for p in decision.admitted] == ["fix 0", "fix 1"]
        assert [p["title"] for p in decision.rejected] == ["fix 2", "fix 3", "fix 4"]
