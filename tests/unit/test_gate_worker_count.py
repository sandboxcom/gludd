"""
Unit tests for scripts/gate_worker_count.py::compute_workers.

Covers every path in the spec:
  - unset / empty GLUDD_XDIST → min(cpu//4, mem_cap)
  - 'auto'                    → same as unset
  - explicit numeric          → min(int, mem_cap)  (may only LOWER count)
  - never returns > mem_cap
  - never returns 0
"""

import os
import sys

import pytest

# Make scripts/ importable without installing.
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from gate_worker_count import _RAM_PER_WORKER, compute_workers  # noqa: E402,I001


_4GB = 4 * 1024 ** 3
_8GB = 8 * 1024 ** 3
_16GB = 16 * 1024 ** 3
_32GB = 32 * 1024 ** 3
_64GB = 64 * 1024 ** 3


class TestComputeWorkersAuto:
    """GLUDD_XDIST unset or 'auto' → min(cpu//4, mem_cap)."""

    def test_auto_clamps_to_mem_cap_when_mem_is_binding(self):
        # 16 cpus → cpu//4 = 4; 8 GB RAM → mem_cap = 8//4 = 2 → result = 2
        result = compute_workers("auto", cpu_count=16, total_ram_bytes=_8GB)
        assert result == 2, f"expected 2, got {result}"

    def test_auto_cpu_quarter_when_cpu_is_binding(self):
        # 8 cpus → cpu//4 = 2; 64 GB RAM → mem_cap = 16 → result = 2
        result = compute_workers("auto", cpu_count=8, total_ram_bytes=_64GB)
        assert result == 2

    def test_none_is_same_as_auto(self):
        r_none = compute_workers(None, cpu_count=16, total_ram_bytes=_8GB)
        r_auto = compute_workers("auto", cpu_count=16, total_ram_bytes=_8GB)
        assert r_none == r_auto

    def test_empty_string_is_same_as_auto(self):
        r_empty = compute_workers("", cpu_count=16, total_ram_bytes=_8GB)
        r_auto = compute_workers("auto", cpu_count=16, total_ram_bytes=_8GB)
        assert r_empty == r_auto

    def test_uppercase_AUTO_treated_as_auto(self):
        r = compute_workers("AUTO", cpu_count=16, total_ram_bytes=_8GB)
        assert r == 2

    def test_auto_large_cpu_large_ram(self):
        # 32 cpus → cpu//4 = 8; 64 GB RAM → mem_cap = 16 → result = 8 (cpu binding)
        result = compute_workers("auto", cpu_count=32, total_ram_bytes=_64GB)
        assert result == 8


class TestComputeWorkersExplicit:
    """GLUDD_XDIST=<int> → min(requested, mem_cap), floor 1."""

    def test_explicit_value_clamps_to_mem_cap(self):
        # 32 GB RAM → mem_cap = 8; explicit 32 → min(32, 8) = 8
        result = compute_workers("32", cpu_count=64, total_ram_bytes=_32GB)
        mem_cap = _32GB // _RAM_PER_WORKER
        assert result == mem_cap, f"expected {mem_cap}, got {result}"

    def test_explicit_value_below_mem_cap_is_honoured(self):
        # 64 GB RAM → mem_cap = 16; explicit 4 → min(4, 16) = 4
        result = compute_workers("4", cpu_count=32, total_ram_bytes=_64GB)
        assert result == 4

    def test_explicit_1_always_passes_through(self):
        result = compute_workers("1", cpu_count=16, total_ram_bytes=_8GB)
        assert result == 1

    def test_explicit_equals_mem_cap_is_allowed(self):
        # 8 GB → mem_cap = 2; explicit 2 → 2 (at the ceiling)
        result = compute_workers("2", cpu_count=16, total_ram_bytes=_8GB)
        assert result == 2

    def test_explicit_above_mem_cap_is_clamped(self):
        # 8 GB → mem_cap = 2; explicit 99 → 2
        result = compute_workers("99", cpu_count=64, total_ram_bytes=_8GB)
        assert result == 2


class TestComputeWorkersInvariants:
    """Global invariants: never > mem_cap, never < 1."""

    @pytest.mark.parametrize("xdist,cpu,ram", [
        (None,   1,  _4GB),
        ("auto", 2,  _8GB),
        ("4",    1,  _4GB),
        ("0",    4,  _8GB),   # explicit 0 → clamped to 1
        ("-1",   4,  _8GB),   # explicit negative → clamped to 1
        ("auto", 16, _4GB),   # 4 GB → mem_cap = 1, cpu//4 = 4 → result = 1
        ("auto", 1,  _4GB),   # very small cpu → floor 1
    ])
    def test_never_returns_zero(self, xdist, cpu, ram):
        result = compute_workers(xdist, cpu_count=cpu, total_ram_bytes=ram)
        assert result >= 1, f"got {result} for xdist={xdist!r} cpu={cpu} ram={ram}"

    @pytest.mark.parametrize("xdist,cpu,ram", [
        (None,   64, _8GB),
        ("auto", 64, _8GB),
        ("32",   64, _8GB),
        ("auto", 32, _4GB),
        ("16",   64, _4GB),
    ])
    def test_never_exceeds_mem_cap(self, xdist, cpu, ram):
        mem_cap = max(1, ram // _RAM_PER_WORKER)
        result = compute_workers(xdist, cpu_count=cpu, total_ram_bytes=ram)
        assert result <= mem_cap, (
            f"result {result} exceeds mem_cap {mem_cap} "
            f"for xdist={xdist!r} cpu={cpu} ram={ram}"
        )

    def test_4gb_ram_produces_mem_cap_1(self):
        # 4 GB / 4 GB per worker = 1; no matter how many CPUs.
        result = compute_workers("auto", cpu_count=128, total_ram_bytes=_4GB)
        assert result == 1

    def test_unparseable_xdist_falls_back_to_cpu_quarter_capped(self):
        # "abc" is unparseable → fall back to cpu//4=4, capped by mem_cap=8
        result = compute_workers("abc", cpu_count=16, total_ram_bytes=_32GB)
        assert result == 4


class TestExplicitMayOnlyLower:
    """An explicit override may only lower the count, never raise it above mem_cap."""

    def test_explicit_cannot_exceed_mem_cap(self):
        # mem_cap for 8 GB = 2; explicit 100 should still give 2
        mem_cap = max(1, _8GB // _RAM_PER_WORKER)
        result = compute_workers("100", cpu_count=4, total_ram_bytes=_8GB)
        assert result <= mem_cap
