"""Deep tests for closest-pair algorithms: brute force, divide-and-conquer, line sweep."""

from __future__ import annotations

import math
import random
from typing import cast

import pytest

from general_ludd.algorithms.closest_pair import (
    closest_pair_brute,
    closest_pair_dc,
    closest_pair_sweep,
)


def _brute_verified(points):
    best = math.inf
    pair = None
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1])
            if d < best:
                best = d
                pair = (i, j)
    return best, pair


def _make_random(n, seed=42, scale=1000.0):
    rng = random.Random(seed)
    return [(rng.uniform(-scale, scale), rng.uniform(-scale, scale)) for _ in range(n)]


# ── edge cases (all 3 impls) ──────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_empty(self, impl):
        d, p = impl([])
        assert math.isinf(d)
        assert p is None

    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_single_point(self, impl):
        d, p = impl([(0.0, 0.0)])
        assert math.isinf(d)
        assert p is None

    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_two_points(self, impl):
        d, p = impl([(0.0, 0.0), (3.0, 4.0)])
        assert d == pytest.approx(5.0)
        assert p == (0, 1)

    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_three_points(self, impl):
        pts = [(0.0, 0.0), (10.0, 0.0), (0.5, 0.0)]
        d, p = impl(pts)
        assert d == pytest.approx(0.5)
        assert p is not None
        assert set(cast(tuple[int, int], p)) == {0, 2}


# ── duplicates ────────────────────────────────────────────────────────


class TestDuplicates:
    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_duplicate_point(self, impl):
        pts = [(7.0, 3.0), (1.0, 1.0), (7.0, 3.0)]
        d, p = impl(pts)
        assert d == 0.0
        assert p is not None
        assert set(cast(tuple[int, int], p)) == {0, 2}

    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_all_duplicates(self, impl):
        pts = [(5.0, 5.0), (5.0, 5.0), (5.0, 5.0)]
        d, p = impl(pts)
        assert d == 0.0
        assert p is not None


# ── collinear ─────────────────────────────────────────────────────────


class TestCollinear:
    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_horizontal_line(self, impl):
        pts = [(0.0, 0.0), (3.0, 0.0), (1.0, 0.0), (7.0, 0.0)]
        d, p = impl(pts)
        assert d == pytest.approx(1.0)
        assert p is not None
        assert set(cast(tuple[int, int], p)) == {0, 2}

    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_vertical_line(self, impl):
        pts = [(0.0, 0.0), (0.0, 5.0), (0.0, 0.5), (0.0, 100.0)]
        d, _p = impl(pts)
        assert d == pytest.approx(0.5)


# ── negative coordinates ──────────────────────────────────────────────


class TestNegativeCoords:
    @pytest.mark.parametrize("impl", [closest_pair_brute, closest_pair_dc, closest_pair_sweep])
    def test_all_negative(self, impl):
        pts = [(-1.0, -1.0), (-10.0, -10.0), (-1.1, -1.1)]
        d, p = impl(pts)
        expected = math.hypot(0.1, 0.1)
        assert d == pytest.approx(expected)
        assert p is not None
        assert set(cast(tuple[int, int], p)) == {0, 2}

    @pytest.mark.parametrize("impl", [closest_pair_dc, closest_pair_sweep])
    def test_mixed_sign_quadrants(self, impl):
        pts = [(-5.0, 0.5), (5.0, 0.5), (0.0, 0.0)]
        d, p = impl(pts)
        assert p is not None
        assert d == pytest.approx(math.hypot(-5.0, 0.5))
        assert set(cast(tuple[int, int], p)) == {0, 2}


# ── random vs brute ───────────────────────────────────────────────────


class TestRandomVsBrute:
    @pytest.mark.parametrize("n", [4, 10, 50, 200])
    def test_dc_matches_brute(self, n):
        pts = _make_random(n, seed=n)
        bd, bp = _brute_verified(pts)
        dd, dp = closest_pair_dc(pts)
        assert dd == pytest.approx(bd)
        assert dp is not None and bp is not None
        assert set(dp) == set(bp)

    @pytest.mark.parametrize("n", [4, 10, 50, 200])
    def test_sweep_matches_brute(self, n):
        pts = _make_random(n, seed=n + 1000)
        bd, bp = _brute_verified(pts)
        sd, sp = closest_pair_sweep(pts)
        assert sd == pytest.approx(bd)
        assert sp is not None and bp is not None
        assert set(sp) == set(bp)

    def test_dc_vs_sweep_large(self):
        pts = _make_random(500, seed=99)
        dd, dp = closest_pair_dc(pts)
        sd, sp = closest_pair_sweep(pts)
        assert dd == pytest.approx(sd)
        assert dp is not None and sp is not None
        assert set(dp) == set(sp)


# ── cross-consistency ─────────────────────────────────────────────────


class TestCrossConsistency:
    @pytest.mark.parametrize("seed", [7, 13, 42, 101])
    def test_all_three_agree(self, seed):
        pts = _make_random(30, seed=seed)
        bd, bp = closest_pair_brute(pts)
        dd, dp = closest_pair_dc(pts)
        sd, sp = closest_pair_sweep(pts)
        assert bd == pytest.approx(dd)
        assert dd == pytest.approx(sd)
        assert bp is not None and dp is not None and sp is not None
        assert set(bp) == set(dp) == set(sp)

    def test_fixed_known_case(self):
        pts = [(0.0, 0.0), (7.0, 0.0), (3.0, 4.0), (9.0, 1.0), (10.0, 2.0), (1.0, 2.0)]
        bd, bp = closest_pair_brute(pts)
        dd, dp = closest_pair_dc(pts)
        sd, sp = closest_pair_sweep(pts)
        assert dd == pytest.approx(bd)
        assert sd == pytest.approx(bd)
        assert bp is not None and dp is not None and sp is not None
        assert set(bp) == set(dp) == set(sp)


# ── large-scale stability ─────────────────────────────────────────────


class TestLargeScale:
    def test_dc_does_not_recurse_too_deep(self):
        pts = _make_random(2000, seed=1)
        d, p = closest_pair_dc(pts)
        bd, bp = _brute_verified(pts)
        assert d == pytest.approx(bd)
        assert p is not None and bp is not None
        assert set(p) == set(bp)

    def test_sweep_does_not_recurse_too_deep(self):
        pts = _make_random(2000, seed=2)
        d, p = closest_pair_sweep(pts)
        bd, bp = _brute_verified(pts)
        assert d == pytest.approx(bd)
        assert p is not None and bp is not None
        assert set(p) == set(bp)
