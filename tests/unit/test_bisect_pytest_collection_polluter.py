"""Tests for the collection-time side-effect minimizer."""

from __future__ import annotations

import pytest
from scripts.bisect_pytest_collection_polluter import (
    PolluterNotReproducedError,
    is_collection_pollution,
    minimize_polluting_set,
    slice_candidates,
)


def test_minimize_polluting_set_finds_single_file() -> None:
    candidates = ["a.py", "b.py", "c.py", "d.py"]

    result = minimize_polluting_set(
        candidates,
        lambda subset: "c.py" in subset,
    )

    assert result == ["c.py"]


def test_minimize_polluting_set_preserves_interacting_pair() -> None:
    candidates = ["a.py", "b.py", "c.py", "d.py"]

    result = minimize_polluting_set(
        candidates,
        lambda subset: {"b.py", "d.py"}.issubset(subset),
    )

    assert set(result) == {"b.py", "d.py"}


def test_minimize_polluting_set_rejects_non_reproducing_input() -> None:
    with pytest.raises(PolluterNotReproducedError):
        minimize_polluting_set(["a.py"], lambda _subset: False)


@pytest.mark.parametrize(
    "output",
    [
        "TypeError: 'Event' object can't be awaited",
        "NotImplementedError: Operator 'getitem' is not supported on this expression",
        "RecursionError: maximum recursion depth exceeded",
    ],
)
def test_known_python314_collection_corruption_is_classified(output: str) -> None:
    assert is_collection_pollution(output) is True


def test_unrelated_pytest_failure_is_not_collection_pollution() -> None:
    assert is_collection_pollution("AssertionError: expected 1, got 2") is False


def test_slice_candidates_resumes_prior_bisect_window() -> None:
    candidates = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    assert slice_candidates(candidates, start=2, limit=2) == ["c.py", "d.py"]
    assert slice_candidates(candidates, start=1, limit=0) == candidates[1:]
