"""Module-boundary tests for non-blocking in-process review orchestration."""

from general_ludd.event_loop.review_compaction import update_compaction_accuracy
from general_ludd.event_loop.review_orchestration import (
    EventLoopReviewMixin,
    is_managed_self_improve_todo,
    safe_string_attribute,
)


def test_review_orchestration_exposes_small_reusable_boundaries() -> None:
    """Keep review behavior separate from the central event-loop scheduler."""
    assert hasattr(EventLoopReviewMixin, "_review_in_process")
    assert callable(is_managed_self_improve_todo)
    assert callable(safe_string_attribute)
    assert callable(update_compaction_accuracy)
