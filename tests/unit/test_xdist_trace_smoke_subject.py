from __future__ import annotations


def test_xdist_trace_smoke_subject_one() -> None:
    assert 1 + 1 == 2


def test_xdist_trace_smoke_subject_two() -> None:
    assert sorted(["b", "a"]) == ["a", "b"]
