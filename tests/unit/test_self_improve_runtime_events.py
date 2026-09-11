"""Regression tests for self-improvement model lifecycle event extraction."""

from __future__ import annotations

from general_ludd.self_improve import runtime_events
from general_ludd.self_improve.runtime import (
    _planned_artifact_identity,
    _report_model_acquisition_event,
    _report_model_release,
    _report_model_resolution_failure,
)


def test_runtime_reexports_the_canonical_model_event_boundary() -> None:
    """Existing integrations retain one implementation after module slimming."""
    assert _planned_artifact_identity is runtime_events.planned_artifact_identity
    assert _report_model_acquisition_event is runtime_events.report_model_acquisition_event
    assert _report_model_release is runtime_events.report_model_release
    assert _report_model_resolution_failure is runtime_events.report_model_resolution_failure
