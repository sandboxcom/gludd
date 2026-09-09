"""Behavioral tests for atomic execution-lease input validation."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from general_ludd.event_loop.lease_validation import validate_lease_input


def test_accepts_bounded_lease_inputs_and_optional_versions() -> None:
    validate_lease_input(["todo:a", "todo:b"], "worker-1", 300, None)
    validate_lease_input(
        ["todo:a", "todo:b"],
        "worker-1",
        86_400,
        {"todo:a": 1, "todo:b": 9},
    )


@pytest.mark.parametrize(
    ("bucket_keys", "holder_id", "ttl_seconds", "todo_versions", "message"),
    [
        (["todo:a"], "", 1, None, "holder_id"),
        (["todo:a"], "x" * 129, 1, None, "holder_id"),
        (["todo:a"], "worker", True, None, "positive integer"),
        (["todo:a"], "worker", 0, None, "between 1 and 86400"),
        (["todo:a"], "worker", 86_401, None, "between 1 and 86400"),
        (["todo:a", "todo:a"], "worker", 1, None, "duplicates"),
        ([""], "worker", 1, None, "bucket key"),
        (["x" * 257], "worker", 1, None, "bucket key"),
        (["todo:a"], "worker", 1, {"todo:b": 1}, "unknown bucket key"),
        (["todo:a"], "worker", 1, {"todo:a": True}, "positive integers"),
        (["todo:a"], "worker", 1, {"todo:a": 0}, "positive integers"),
    ],
)
def test_rejects_ambiguous_or_unbounded_lease_inputs(
    bucket_keys: list[str],
    holder_id: str,
    ttl_seconds: int,
    todo_versions: Mapping[str, int] | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_lease_input(bucket_keys, holder_id, ttl_seconds, todo_versions)
