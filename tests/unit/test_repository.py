from __future__ import annotations

from general_ludd.db.repository import BenchmarkRepository


def test_benchmark_repository_equivalence():
    assert BenchmarkRepository is not None
