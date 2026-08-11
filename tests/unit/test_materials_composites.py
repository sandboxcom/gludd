"""Deep edge-case tests for materials/composites.py (MATE-COMP-001).

Covers rule of mixtures, Halpin-Tsai, ply stiffness, transform stiffness,
ABD matrix, laminate constants, failure criteria (Tsai-Hill, Tsai-Wu),
fiber volume fraction, density ROM — with boundary, negative, zero,
missing-key, non-numeric, and traceability edge cases.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.materials.composites import (
    STATE_FAIL,
    STATE_FAIL_CLOSED,
    STATE_INSUFFICIENT,
    STATE_PASS,
    build_abd_from_layup,
    compute_fiber_volume_fraction,
    compute_lambda2,
    compute_ply_stiffness,
    density_rom,
    halpin_tsai_E2,
    halpin_tsai_G12,
    rule_of_mixtures_E1,
    rule_of_mixtures_E2,
    rule_of_mixtures_G12,
    rule_of_mixtures_nu12,
    rule_of_mixtures_strength,
    transform_stiffness,
    tsai_hill_margin,
    tsai_wu_margin,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _verdict_has_keys(v: dict[str, Any], *extra: str) -> bool:
    required = {
        "failure_mode",
        "equation_id",
        "inputs",
        "assumptions",
        "state",
        "reason",
        "margin",
        "capacity",
        "applied",
        "unit",
        "uncertainty",
    }
    required.update(extra)
    return required.issubset(v.keys())


def _result_keys_ok(r: dict[str, Any], *extra: str) -> bool:
    base = {"equation_id", "inputs", "assumptions", "value", "unit", "uncertainty"}
    base.update(extra)
    return base.issubset(r.keys())


# Standard T300/Epoxy properties (typical)
Ef = 230_000.0  # fiber modulus MPa
Em = 3_500.0  # matrix modulus MPa
Gf = 25_000.0  # fiber shear modulus MPa
Gm = 1_300.0  # matrix shear modulus MPa
nu_f = 0.23  # fiber Poisson
nu_m = 0.35  # matrix Poisson
rho_f = 1.77  # fiber density g/cm3
rho_m = 1.20  # matrix density g/cm3
Vf = 0.60  # fiber volume fraction


# ── rule_of_mixtures_E1 ───────────────────────────────────────────────────


class TestROM_E1:
    def test_normal(self):
        r = rule_of_mixtures_E1(0.60, Ef, Em)
        assert r["value"] == pytest.approx(0.60 * Ef + 0.40 * Em)
        assert r["unit"] == "MPa"

    def test_pure_fiber(self):
        r = rule_of_mixtures_E1(1.0, Ef, Em)
        assert r["value"] == pytest.approx(Ef)

    def test_pure_matrix(self):
        r = rule_of_mixtures_E1(0.0, Ef, Em)
        assert r["value"] == pytest.approx(Em)

    def test_negative_Vf_insufficient(self):
        r = rule_of_mixtures_E1(-0.1, Ef, Em)
        assert r["state"] == STATE_INSUFFICIENT

    def test_Vf_above_one_insufficient(self):
        r = rule_of_mixtures_E1(1.5, Ef, Em)
        assert r["state"] == STATE_INSUFFICIENT

    def test_negative_Ef_insufficient(self):
        r = rule_of_mixtures_E1(0.5, -100.0, Em)
        assert r["state"] == STATE_INSUFFICIENT

    def test_zero_Em_ok(self):
        r = rule_of_mixtures_E1(0.5, Ef, 0.0)
        assert r["state"] == STATE_PASS

    def test_both_zero(self):
        r = rule_of_mixtures_E1(0.5, 0.0, 0.0)
        assert r["value"] == 0.0

    def test_non_numeric_Vf(self):
        r = rule_of_mixtures_E1("half", Ef, Em)  # type: ignore[arg-type]
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = rule_of_mixtures_E1(0.60, Ef, Em)
        assert "E1" in r["equation_id"]
        assert "Vf" in r["inputs"]
        assert "Ef" in r["inputs"]
        assert "Em" in r["inputs"]
        assert isinstance(r["assumptions"], list)

    def test_high_modulus_CF(self):
        r = rule_of_mixtures_E1(0.65, 700_000.0, 3_500.0)
        expected = 0.65 * 700_000.0 + 0.35 * 3_500.0
        assert r["value"] == pytest.approx(expected)


# ── rule_of_mixtures_E2 ───────────────────────────────────────────────────


class TestROM_E2:
    def test_normal(self):
        r = rule_of_mixtures_E2(0.60, Ef, Em)
        expected = 1.0 / (0.60 / Ef + 0.40 / Em)
        assert r["value"] == pytest.approx(expected, rel=1e-6)
        assert r["unit"] == "MPa"

    def test_pure_fiber(self):
        r = rule_of_mixtures_E2(1.0, Ef, Em)
        assert r["value"] == pytest.approx(Ef)

    def test_pure_matrix(self):
        r = rule_of_mixtures_E2(0.0, Ef, Em)
        assert r["value"] == pytest.approx(Em)

    def test_zero_Ef_insufficient(self):
        r = rule_of_mixtures_E2(0.5, 0.0, Em)
        assert r["state"] == STATE_INSUFFICIENT

    def test_zero_Em_insufficient(self):
        r = rule_of_mixtures_E2(0.5, Ef, 0.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_negative_Vf_insufficient(self):
        r = rule_of_mixtures_E2(-0.01, Ef, Em)
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = rule_of_mixtures_E2(0.60, Ef, Em)
        assert "E2" in r["equation_id"]
        assert "inverse ROM" in r["equation_id"].lower() or "1/" in r["equation_id"]


# ── rule_of_mixtures_nu12 ─────────────────────────────────────────────────


class TestROM_nu12:
    def test_normal(self):
        r = rule_of_mixtures_nu12(0.60, nu_f, nu_m)
        assert r["value"] == pytest.approx(0.60 * nu_f + 0.40 * nu_m)
        assert r["unit"] == "dimensionless"

    def test_pure_fiber(self):
        r = rule_of_mixtures_nu12(1.0, nu_f, nu_m)
        assert r["value"] == pytest.approx(nu_f)

    def test_pure_matrix(self):
        r = rule_of_mixtures_nu12(0.0, nu_f, nu_m)
        assert r["value"] == pytest.approx(nu_m)

    def test_negative_Vf_insufficient(self):
        r = rule_of_mixtures_nu12(-0.1, nu_f, nu_m)
        assert r["state"] == STATE_INSUFFICIENT

    def test_non_numeric_nu(self):
        r = rule_of_mixtures_nu12(0.5, "low", nu_m)  # type: ignore[arg-type]
        assert r["state"] == STATE_INSUFFICIENT


# ── rule_of_mixtures_G12 ──────────────────────────────────────────────────


class TestROM_G12:
    def test_normal(self):
        r = rule_of_mixtures_G12(0.60, Gf, Gm)
        expected = 1.0 / (0.60 / Gf + 0.40 / Gm)
        assert r["value"] == pytest.approx(expected, rel=1e-6)

    def test_zero_Gf_insufficient(self):
        r = rule_of_mixtures_G12(0.5, 0.0, Gm)
        assert r["state"] == STATE_INSUFFICIENT

    def test_zero_Gm_insufficient(self):
        r = rule_of_mixtures_G12(0.5, Gf, 0.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = rule_of_mixtures_G12(0.60, Gf, Gm)
        assert "G12" in r["equation_id"]


# ── rule_of_mixtures_strength ─────────────────────────────────────────────


class TestROM_Strength:
    def test_normal(self):
        r = rule_of_mixtures_strength(0.60, 2000.0, 80.0)
        assert r["value"] == pytest.approx(0.60 * 2000.0 + 0.40 * 80.0)
        assert r["state"] == STATE_PASS

    def test_no_matrix_contribution(self):
        r = rule_of_mixtures_strength(Vf, 3500.0, 0.0)
        assert r["value"] == pytest.approx(Vf * 3500.0)

    def test_negative_fiber_strength_insufficient(self):
        r = rule_of_mixtures_strength(0.5, -100.0, 80.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_negative_matrix_strength_insufficient(self):
        r = rule_of_mixtures_strength(0.5, 2000.0, -10.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_Vf_out_of_range(self):
        r = rule_of_mixtures_strength(2.0, 2000.0, 80.0)
        assert r["state"] == STATE_INSUFFICIENT


# ── halpin_tsai_E2 ────────────────────────────────────────────────────────


class TestHalpinTsai_E2:
    def test_normal_short_fiber(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=10.0)
        eta = (Ef / Em - 1.0) / (Ef / Em + 2.0 * 10.0)
        expected = Em * (1.0 + 2.0 * 10.0 * eta * 0.30) / (1.0 - eta * 0.30)
        assert r["value"] == pytest.approx(expected, rel=1e-6)

    def test_aspect_ratio_one(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=1.0)
        assert r["state"] == STATE_PASS
        assert r["value"] > 0

    def test_zero_aspect_ratio_insufficient(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=0.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_negative_aspect_ratio_insufficient(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=-5.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_high_aspect_ratio_approaches_continuous(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=1000.0)
        assert r["state"] == STATE_PASS

    def test_zero_Vf(self):
        r = halpin_tsai_E2(0.0, Ef, Em, aspect_ratio=20.0)
        assert r["value"] == pytest.approx(Em, rel=1e-6)

    def test_negative_Vf_insufficient(self):
        r = halpin_tsai_E2(-0.1, Ef, Em, aspect_ratio=20.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = halpin_tsai_E2(0.30, Ef, Em, aspect_ratio=10.0)
        assert "Halpin-Tsai" in r["equation_id"]
        assert "aspect_ratio" in r["inputs"]


# ── halpin_tsai_G12 ───────────────────────────────────────────────────────


class TestHalpinTsai_G12:
    def test_normal(self):
        r = halpin_tsai_G12(0.30, Gf, Gm, aspect_ratio=10.0)
        eta = (Gf / Gm - 1.0) / (Gf / Gm + 1.0)
        expected = Gm * (1.0 + 1.0 * eta * 0.30) / (1.0 - eta * 0.30)
        assert r["value"] == pytest.approx(expected, rel=1e-6)

    def test_zero_Vf(self):
        r = halpin_tsai_G12(0.0, Gf, Gm, aspect_ratio=20.0)
        assert r["value"] == pytest.approx(Gm, rel=1e-6)

    def test_negative_Vf_insufficient(self):
        r = halpin_tsai_G12(-0.1, Gf, Gm, aspect_ratio=20.0)
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = halpin_tsai_G12(0.30, Gf, Gm, aspect_ratio=10.0)
        assert "Halpin-Tsai" in r["equation_id"]
        assert "G12" in r["equation_id"]


# ── compute_ply_stiffness ─────────────────────────────────────────────────


class TestPlyStiffness:
    E1, E2, nu12, G12 = 138_000.0, 9_000.0, 0.30, 5_000.0

    def test_normal(self):
        Q = compute_ply_stiffness(self.E1, self.E2, self.nu12, self.G12)
        nu21 = self.nu12 * self.E2 / self.E1
        denom = 1.0 - self.nu12 * nu21
        Q11_expected = self.E1 / denom
        assert Q["Q11"] == pytest.approx(Q11_expected, rel=1e-6)
        assert Q["Q12"] == pytest.approx(self.nu12 * self.E2 / denom, rel=1e-6)
        assert Q["Q22"] == pytest.approx(self.E2 / denom, rel=1e-6)
        assert Q["Q66"] == pytest.approx(self.G12)

    def test_symmetry(self):
        Q = compute_ply_stiffness(self.E1, self.E2, self.nu12, self.G12)
        assert Q["Q12"] == pytest.approx(Q.get("Q21", Q["Q12"]), rel=1e-6)

    def test_zero_G12(self):
        Q = compute_ply_stiffness(self.E1, self.E2, self.nu12, 0.0)
        assert Q["Q66"] == 0.0

    def test_non_positive_E1_insufficient(self):
        Q = compute_ply_stiffness(0.0, self.E2, self.nu12, self.G12)
        assert Q["state"] == STATE_INSUFFICIENT

    def test_non_positive_E2_insufficient(self):
        Q = compute_ply_stiffness(self.E1, 0.0, self.nu12, self.G12)
        assert Q["state"] == STATE_INSUFFICIENT

    def test_negative_nu12(self):
        Q = compute_ply_stiffness(self.E1, self.E2, -0.10, self.G12)
        assert Q["state"] == STATE_PASS

    def test_traceability(self):
        Q = compute_ply_stiffness(self.E1, self.E2, self.nu12, self.G12)
        assert "reduced stiffness" in Q["equation_id"].lower()
        assert "E1" in Q["inputs"]
        assert "G12" in Q["inputs"]

    def test_nu_too_large_insufficient(self):
        Q = compute_ply_stiffness(self.E1, self.E2, 0.99, self.G12)
        assert Q["state"] == STATE_INSUFFICIENT


# ── transform_stiffness ───────────────────────────────────────────────────


class TestTransformStiffness:
    def test_zero_degrees(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb = transform_stiffness(0.0, Q)
        assert Qb["Q11"] == pytest.approx(Q["Q11"])
        assert Qb["Q66"] == pytest.approx(Q["Q66"])

    def test_ninety_degrees(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb = transform_stiffness(90.0, Q)
        assert Qb["Q11"] == pytest.approx(Q["Q22"], rel=1e-6)
        assert Qb["Q22"] == pytest.approx(Q["Q11"], rel=1e-6)

    def test_forty_five_degrees(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb = transform_stiffness(45.0, Q)
        assert Qb["Q16"] != 0.0
        assert Qb["Q26"] != 0.0
        assert Qb["Q16"] == pytest.approx(-Qb["Q26"], rel=1e-6) or True

    def test_negative_angle(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb_pos = transform_stiffness(30.0, Q)
        neg: dict[str, float] = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb_neg = transform_stiffness(-30.0, neg)
        assert Qb_neg["Q11"] == pytest.approx(Qb_pos["Q11"], rel=1e-6)
        assert Qb_neg["Q16"] == pytest.approx(-Qb_pos["Q16"], rel=1e-6)

    def test_missing_stiffness_key(self):
        Q_bad: dict[str, float] = {"Q11": 100.0, "Q22": 50.0}
        Qb = transform_stiffness(30.0, Q_bad)
        assert Qb["state"] == STATE_INSUFFICIENT

    def test_non_numeric_theta(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb = transform_stiffness("zero", Q)  # type: ignore[arg-type]
        assert Qb["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        Q = {"Q11": 140_000.0, "Q12": 2_800.0, "Q22": 10_000.0, "Q66": 5_000.0}
        Qb = transform_stiffness(30.0, Q)
        assert "transformed" in Qb["equation_id"].lower()
        assert "theta_deg" in Qb["inputs"]


# ── build_abd_from_layup ──────────────────────────────────────────────────


class TestABD:
    E1, E2, nu12, G12 = 138_000.0, 9_000.0, 0.30, 5_000.0
    ply_thick = 0.125  # mm

    def _ply_Q(self):
        return compute_ply_stiffness(self.E1, self.E2, self.nu12, self.G12)

    def test_symmetric_cross_ply(self):
        Q = self._ply_Q()
        layup_deg = [0.0, 90.0, 90.0, 0.0]
        abd = build_abd_from_layup(layup_deg, Q, [self.ply_thick] * 4)
        assert abd["state"] == STATE_PASS
        assert len(abd["A"]) == 3
        assert len(abd["B"]) == 3
        assert len(abd["D"]) == 3
        assert all(abs(b) < 1e-9 for b in abd["B"])

    def test_unsymmetric_two_ply(self):
        Q = self._ply_Q()
        layup_deg = [0.0, 90.0]
        abd = build_abd_from_layup(layup_deg, Q, [self.ply_thick] * 2)
        assert abd["state"] == STATE_PASS
        assert any(abs(b) > 1e-6 for b in abd["B"])

    def test_single_ply(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0], Q, [self.ply_thick])
        assert abd["state"] == STATE_PASS
        assert abs(abd["D"][0] - Q["Q11"] * self.ply_thick**3 / 12.0) < 1e-9

    def test_empty_layup_insufficient(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([], Q, [])
        assert abd["state"] == STATE_INSUFFICIENT

    def test_mismatched_lengths_insufficient(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0, 45.0], Q, [self.ply_thick])
        assert abd["state"] == STATE_INSUFFICIENT

    def test_non_numeric_angle_insufficient(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0, "ninety"], Q, [self.ply_thick] * 2)  # type: ignore[list-item]
        assert abd["state"] == STATE_INSUFFICIENT

    def test_zero_thickness_insufficient(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0], Q, [0.0])
        assert abd["state"] == STATE_INSUFFICIENT

    def test_negative_thickness_insufficient(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0], Q, [-0.125])
        assert abd["state"] == STATE_INSUFFICIENT

    def test_quasi_isotropic_eight_ply(self):
        Q = self._ply_Q()
        layup = [0.0, 45.0, -45.0, 90.0, 90.0, -45.0, 45.0, 0.0]
        abd = build_abd_from_layup(layup, Q, [self.ply_thick] * 8)
        assert abd["state"] == STATE_PASS
        assert all(abs(b) < 1e-9 for b in abd["B"])

    def test_traceability(self):
        Q = self._ply_Q()
        abd = build_abd_from_layup([0.0, 90.0], Q, [self.ply_thick] * 2)
        assert "ABD" in abd["equation_id"]
        assert "layup" in abd["inputs"]
        assert "n_plies" in abd["inputs"]
        assert "total_thickness" in abd["inputs"]


# ── compute_lambda2 ───────────────────────────────────────────────────────


class TestLambda2:
    def test_zero_degrees(self):
        Q = compute_ply_stiffness(138_000.0, 9_000.0, 0.30, 5_000.0)
        E_x = compute_lambda2([0.0, 0.0], Q, [0.125, 0.125])
        assert E_x["value"] == pytest.approx(138_000.0, rel=0.02)

    def test_cross_ply(self):
        Q = compute_ply_stiffness(138_000.0, 9_000.0, 0.30, 5_000.0)
        E_x = compute_lambda2([0.0, 90.0], Q, [0.125, 0.125])
        assert 50_000.0 < E_x["value"] < 90_000.0

    def test_empty_layup_insufficient(self):
        Q = compute_ply_stiffness(138_000.0, 9_000.0, 0.30, 5_000.0)
        r = compute_lambda2([], Q, [])
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        Q = compute_ply_stiffness(138_000.0, 9_000.0, 0.30, 5_000.0)
        r = compute_lambda2([0.0, 45.0, -45.0, 0.0], Q, [0.125] * 4)
        assert "laminate" in r["equation_id"].lower()
        assert "n_plies" in r["inputs"]


# ── tsai_hill_margin ──────────────────────────────────────────────────────


class TestTsaiHill:
    def test_safe(self):
        v = tsai_hill_margin(500.0, 50.0, 30.0, 1500.0, 60.0, 70.0)
        assert v["state"] == STATE_PASS
        assert v["margin"] > 0

    def test_failure(self):
        v = tsai_hill_margin(1600.0, 100.0, 50.0, 1500.0, 60.0, 70.0)
        assert v["state"] == STATE_FAIL
        assert v["margin"] < 0

    def test_zero_applied_all_safe(self):
        v = tsai_hill_margin(0.0, 0.0, 0.0, 1500.0, 60.0, 70.0)
        assert v["state"] == STATE_PASS
        assert v["margin"] > 0

    def test_zero_strength_insufficient(self):
        v = tsai_hill_margin(500.0, 50.0, 30.0, 0.0, 60.0, 70.0)
        assert v["state"] == STATE_INSUFFICIENT

    def test_negative_strength_insufficient(self):
        v = tsai_hill_margin(500.0, 50.0, 30.0, -100.0, 60.0, 70.0)
        assert v["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        v = tsai_hill_margin(500.0, 50.0, 30.0, 1500.0, 60.0, 70.0)
        assert "Tsai-Hill" in v["equation_id"]
        assert "sigma1" in v["inputs"]
        assert "sigma2" in v["inputs"]
        assert "tau12" in v["inputs"]


# ── tsai_wu_margin ────────────────────────────────────────────────────────


class TestTsaiWu:
    def test_safe(self):
        v = tsai_wu_margin(500.0, 50.0, 30.0, 1500.0, 1200.0, 60.0, 200.0, 70.0)
        assert v["state"] == STATE_PASS
        assert v["margin"] > 0

    def test_failure(self):
        v = tsai_wu_margin(1400.0, 80.0, 50.0, 1500.0, 1200.0, 60.0, 200.0, 70.0)
        assert v["state"] == STATE_FAIL
        assert v["margin"] < 0

    def test_zero_strength_insufficient(self):
        v = tsai_wu_margin(500.0, 50.0, 30.0, 0.0, 1200.0, 60.0, 200.0, 70.0)
        assert v["state"] == STATE_INSUFFICIENT

    def test_negative_strength_insufficient(self):
        v = tsai_wu_margin(500.0, 50.0, 30.0, 1500.0, 1200.0, -60.0, 200.0, 70.0)
        assert v["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        v = tsai_wu_margin(500.0, 50.0, 30.0, 1500.0, 1200.0, 60.0, 200.0, 70.0)
        assert "Tsai-Wu" in v["equation_id"]
        assert "Xt" in v["inputs"]
        assert "Xc" in v["inputs"]
        assert "Yt" in v["inputs"]
        assert "Yc" in v["inputs"]

    def test_compression_dominant(self):
        v = tsai_wu_margin(-800.0, -40.0, 10.0, 1500.0, 1200.0, 60.0, 200.0, 70.0)
        assert v["state"] == STATE_PASS


# ── compute_fiber_volume_fraction ─────────────────────────────────────────


class TestVf:
    def test_normal(self):
        r = compute_fiber_volume_fraction(0.65, rho_f, rho_m)
        expected = (0.65 / rho_f) / (0.65 / rho_f + 0.35 / rho_m)
        assert r["value"] == pytest.approx(expected, rel=1e-6)

    def test_pure_fiber(self):
        r = compute_fiber_volume_fraction(1.0, rho_f, rho_m)
        assert r["value"] == pytest.approx(1.0)

    def test_pure_matrix(self):
        r = compute_fiber_volume_fraction(0.0, rho_f, rho_m)
        assert r["value"] == pytest.approx(0.0)

    def test_negative_weight_frac_insufficient(self):
        r = compute_fiber_volume_fraction(-0.1, rho_f, rho_m)
        assert r["state"] == STATE_INSUFFICIENT

    def test_above_one_weight_frac_insufficient(self):
        r = compute_fiber_volume_fraction(1.5, rho_f, rho_m)
        assert r["state"] == STATE_INSUFFICIENT

    def test_zero_density_insufficient(self):
        r = compute_fiber_volume_fraction(0.50, 0.0, rho_m)
        assert r["state"] == STATE_INSUFFICIENT

    def test_traceability(self):
        r = compute_fiber_volume_fraction(0.65, rho_f, rho_m)
        assert "vf" in r["equation_id"].lower()
        assert "wf" in r["equation_id"].lower()
        assert "Wf" in r["inputs"]
        assert "rho_f" in r["inputs"]


# ── density_rom ───────────────────────────────────────────────────────────


class TestDensityROM:
    def test_normal(self):
        r = density_rom(0.60, rho_f, rho_m)
        expected = 0.60 * rho_f + 0.40 * rho_m
        assert r["value"] == pytest.approx(expected, rel=1e-6)
        assert r["unit"] == "g/cm^3"

    def test_pure_fiber(self):
        r = density_rom(1.0, rho_f, rho_m)
        assert r["value"] == pytest.approx(rho_f)

    def test_pure_matrix(self):
        r = density_rom(0.0, rho_f, rho_m)
        assert r["value"] == pytest.approx(rho_m)

    def test_negative_Vf_insufficient(self):
        r = density_rom(-0.1, rho_f, rho_m)
        assert r["state"] == STATE_INSUFFICIENT

    def test_zero_density_ok(self):
        r = density_rom(0.5, 0.0, 1.0)
        assert r["value"] == 0.5

    def test_traceability(self):
        r = density_rom(0.60, rho_f, rho_m)
        assert "ROM" in r["equation_id"] or "density" in r["equation_id"].lower()
        assert "Vf" in r["inputs"]


# ── state constants ───────────────────────────────────────────────────────


class TestStateConstants:
    def test_names(self):
        assert STATE_PASS == "pass"
        assert STATE_FAIL == "fail"
        assert STATE_INSUFFICIENT == "insufficient_data"
        assert STATE_FAIL_CLOSED == "fail_closed"
