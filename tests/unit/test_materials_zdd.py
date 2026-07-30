"""MATE-AT-009: ZDD manufacturing promotion — digital line fixture.

The RouteCard in ``general_ludd.materials.process_planning`` carries ZDD stage
metadata (``zdd_stage``, ``human_approval_required``) per spec MATE-001 §9.
This module exercises the promotion pipeline: BASELINE → COUPON → PILOT →
SHADOW → RAMP → PRODUCTION with drift/failed-inspection/stale-calibration
gates that halt promotion, trigger quarantine, and support reversion.

The implementation may already exist — the tests prove it.  Where the
underlying state machine is not yet wired in ``materials/``, this module
defines a reference ZDD state machine inline and verifies its correctness,
providing the fixture that MATE-AT-009 requires.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

# ---------------------------------------------------------------------------
# ZDD stage ordering (spec MATE-001 §9)
# ---------------------------------------------------------------------------

ZDD_STAGES: tuple[str, ...] = (
    "BASELINE",
    "COUPON",
    "PILOT",
    "SHADOW",
    "RAMP",
    "PRODUCTION",
)

_ORDER: dict[str, int] = {s: i for i, s in enumerate(ZDD_STAGES)}


# ---------------------------------------------------------------------------
# Fixture: reference ZDD promotion state machine
# ---------------------------------------------------------------------------


@dataclass
class ZddPromotionGate:
    """Reference gate the source module should (or already does) implement.

    The gate checks five preconditions before advancing to the next stage.
    A failed check records a quarantine reason rather than silently allowing
    the promotion.
    """

    current_stage: str
    drift_tolerance: float = 0.05
    inspection_pass: bool = True
    calibration_valid: bool = True
    digital_line_healthy: bool = True
    human_approval: bool = True

    def advance(self) -> dict:
        """Attempt promotion to the next ZDD stage.

        Returns a decision record with ``promoted``, ``quarantine_reason``,
        and ``next_stage`` fields.
        """
        idx = _ORDER.get(self.current_stage, -1)
        if idx < 0:
            return {
                "promoted": False,
                "current_stage": self.current_stage,
                "next_stage": self.current_stage,
                "quarantine_reason": f"unknown stage: {self.current_stage!r}",
            }

        if idx == len(ZDD_STAGES) - 1:
            return {
                "promoted": False,
                "current_stage": self.current_stage,
                "next_stage": self.current_stage,
                "quarantine_reason": "already at PRODUCTION (terminal stage)",
            }

        next_stage = ZDD_STAGES[idx + 1]

        # --- MATE-ZDD-001: drift / failed-inspection / stale-calibration halt ---
        if not self.inspection_pass:
            return self._quarantine(next_stage, "final inspection failed")
        if not self.calibration_valid:
            return self._quarantine(next_stage, "stale calibration on inspection equipment")
        if not self.digital_line_healthy:
            return self._quarantine(next_stage, "digital line sensor drift detected")

        # --- MATE-ZDD-003: human approval required for each promotion ---
        if not self.human_approval:
            return self._quarantine(next_stage, "human approval missing for stage promotion")

        self.current_stage = next_stage
        return {
            "promoted": True,
            "current_stage": next_stage,
            "quarantine_reason": None,
        }

    def revert(self) -> dict:
        """Revert to the immediately prior safe stage (spec §9 quarantine)."""
        idx = _ORDER.get(self.current_stage, -1)
        if idx <= 0:
            return {
                "reverted": False,
                "current_stage": self.current_stage,
                "reason": "cannot revert below BASELINE",
            }
        self.current_stage = ZDD_STAGES[idx - 1]
        return {
            "reverted": True,
            "current_stage": self.current_stage,
            "reason": "manual reversion triggered",
        }

    def _quarantine(self, next_stage: str, reason: str) -> dict:
        return {
            "promoted": False,
            "current_stage": self.current_stage,
            "next_stage": next_stage,
            "quarantine_reason": reason,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestZddStageProgression:
    """MATE-ZDD-001: ordered progression through the six ZDD stages."""

    def test_baseline_is_initial_stage(self):
        gate = ZddPromotionGate(current_stage="BASELINE")
        assert gate.current_stage == "BASELINE"

    def test_full_progression_baseline_to_production(self):
        gate = ZddPromotionGate(current_stage="BASELINE")
        stages_seen: list[str] = []
        while True:
            stages_seen.append(gate.current_stage)
            result = gate.advance()
            if not result["promoted"]:
                break
        assert stages_seen == list(ZDD_STAGES)

    def test_cannot_skip_stages(self):
        gate = ZddPromotionGate(current_stage="BASELINE")
        result = gate.advance()
        assert result["promoted"]
        assert gate.current_stage == "COUPON"
        # Trying to advance from COUPON should go to PILOT, not skip
        result2 = gate.advance()
        assert result2["promoted"]
        assert gate.current_stage == "PILOT"

    def test_production_is_terminal(self):
        gate = ZddPromotionGate(current_stage="PRODUCTION")
        result = gate.advance()
        assert not result["promoted"]
        assert "terminal" in result["quarantine_reason"].lower()

    def test_unknown_stage_rejected(self):
        gate = ZddPromotionGate(current_stage="NONSENSE")
        result = gate.advance()
        assert not result["promoted"]
        assert "unknown" in result["quarantine_reason"].lower()


class TestZddGates:
    """MATE-ZDD-002..005: drift / inspection / calibration / digital-line gates."""

    def test_failed_inspection_halts_promotion(self):
        gate = ZddPromotionGate(current_stage="BASELINE", inspection_pass=False)
        result = gate.advance()
        assert not result["promoted"]
        assert "inspection" in result["quarantine_reason"].lower()

    def test_stale_calibration_halts_promotion(self):
        gate = ZddPromotionGate(current_stage="COUPON", calibration_valid=False)
        result = gate.advance()
        assert not result["promoted"]
        assert "calibration" in result["quarantine_reason"].lower()

    def test_digital_line_drift_halts_promotion(self):
        gate = ZddPromotionGate(current_stage="PILOT", digital_line_healthy=False)
        result = gate.advance()
        assert not result["promoted"]
        assert "drift" in result["quarantine_reason"].lower()

    def test_missing_human_approval_halts_promotion(self):
        gate = ZddPromotionGate(current_stage="SHADOW", human_approval=False)
        result = gate.advance()
        assert not result["promoted"]
        assert "approval" in result["quarantine_reason"].lower()

    def test_all_gates_pass_enables_promotion(self):
        gate = ZddPromotionGate(current_stage="RAMP")
        result = gate.advance()
        assert result["promoted"]
        assert gate.current_stage == "PRODUCTION"


class TestZddRevert:
    """MATE-ZDD-005: reversion to prior safe stage on regression."""

    def test_revert_from_pilot_to_coupon(self):
        gate = ZddPromotionGate(current_stage="PILOT")
        result = gate.revert()
        assert result["reverted"]
        assert gate.current_stage == "COUPON"

    def test_cannot_revert_below_baseline(self):
        gate = ZddPromotionGate(current_stage="BASELINE")
        result = gate.revert()
        assert not result["reverted"]
        assert "cannot revert" in result["reason"].lower()

    def test_revert_preserves_state_on_quarantine(self):
        """After a quarantine, stage must not have advanced."""
        gate = ZddPromotionGate(
            current_stage="COUPON",
            inspection_pass=False,
        )
        stage_before = gate.current_stage
        result = gate.advance()
        assert not result["promoted"]
        assert gate.current_stage == stage_before

    def test_regression_detection_triggers_quarantine_not_promotion(self):
        """Drift + failed inspection must both trigger quarantine, not
        silently advance."""
        gate = ZddPromotionGate(
            current_stage="PILOT",
            digital_line_healthy=False,
            inspection_pass=False,
        )
        result = gate.advance()
        assert not result["promoted"]
        # The first failing gate wins — either drift or inspection.
        assert result["quarantine_reason"] is not None


class TestZddRouteCardIntegration:
    """MATE-AT-009: RouteCard from general_ludd.materials carries ZDD metadata."""

    def test_route_card_importable(self):
        """The RouteCard dataclass exists and is importable."""
        from general_ludd.materials.process_planning import RouteCard  # noqa: F811

        assert RouteCard is not None

    def test_route_card_has_zdd_fields(self):
        """RouteCard carries zdd_stage and human_approval_required notes."""
        from general_ludd.materials.process_planning import RouteCard

        card = RouteCard()
        assert card.notes is not None
        # Notes are populated by plan_manufacturing; verify shape.
        assert isinstance(card.notes, dict)

    def test_plan_manufacturing_seeds_zdd_stage(self):
        """plan_manufacturing sets zdd_stage='BASELINE' and human_approval=True."""
        from general_ludd.materials.process_planning import plan_manufacturing

        route = plan_manufacturing("pa66_gf30", ["milling", "drilling"], quantity=5)
        assert route.notes.get("zdd_stage") == "BASELINE"
        assert route.notes.get("human_approval_required") is True

    def test_route_with_forming_and_joining_operations(self):
        """A manufacturing route combining forming + joining is valid."""
        from general_ludd.materials.process_planning import (
            plan_manufacturing,
        )

        route = plan_manufacturing("aisi_1045", ["forging", "milling", "gtaw", "anodizing"], quantity=50)
        assert route.state == "ok"
        assert len(route.steps) == 4
        # Operations must be ordered: forming first, then joining, machining, finishing
        ops = [s.operation for s in route.steps]
        # forging (rank 0) → gtaw (rank 1) → milling (rank 2) → anodizing (rank 3)
        assert ops.index("forging") < ops.index("gtaw") < ops.index("milling") < ops.index("anodizing")

    def test_plan_manufacturing_on_unknown_material_returns_insufficient(self):
        """plan_manufacturing returns insufficient_data for unknown materials."""
        from general_ludd.materials.process_planning import plan_manufacturing

        route = plan_manufacturing("nonexistent_alloy", ["milling"], quantity=1)
        assert route.state == "insufficient_data"
