"""Adaptive compaction feedback from completed in-process reviews."""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.controllers.compaction_aggressiveness import AccuracySample
from general_ludd.schemas.task_decision import TaskDecision

logger = logging.getLogger(__name__)


def update_compaction_accuracy(loop: Any, decision: TaskDecision) -> None:
    """Feed one review outcome into the optional compaction controller."""
    if loop._compaction_controller is None:
        return
    success = decision.decision == "complete"
    loop._compaction_passed += 1 if success else 0
    loop._compaction_total += 1
    sample = AccuracySample(
        passed=loop._compaction_passed,
        total=loop._compaction_total,
    )
    if loop._compaction_level is None:
        config = loop.config.get("compaction", {}) if isinstance(loop.config, dict) else {}
        loop._compaction_level = config.get("level", 1) if config.get("enabled") else 0
    next_level = loop._compaction_controller.compute(loop._compaction_level, sample)
    if next_level != loop._compaction_level:
        logger.info(
            "Compaction level adjusted: %d -> %d (passed=%d total=%d)",
            loop._compaction_level,
            next_level,
            loop._compaction_passed,
            loop._compaction_total,
        )
        loop._compaction_level = next_level
    loop._compaction_disabled = loop._compaction_controller.disable_signaled(
        loop._compaction_level,
        sample,
    )
    if loop._compaction_disabled:
        logger.warning(
            "Compaction disabled by adaptive controller "
            "(level=%d, passed=%d total=%d, rate=%.2f)",
            loop._compaction_level,
            loop._compaction_passed,
            loop._compaction_total,
            sample.rate or 0.0,
        )


__all__ = ["update_compaction_accuracy"]
