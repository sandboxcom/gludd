"""Composite materials engineering (spec MATE-COMP-001).

Provides analytical models for continuous- and short-fiber composite design:
  - Rule of Mixtures (ROM): longitudinal/transverse stiffness, Poisson, shear, strength
  - Halpin-Tsai: short/discontinuous-fiber transverse stiffness and shear modulus
  - Reduced stiffness (Q matrix) and in-plane rotation (Q_bar)
  - Classical Laminate Theory: ABD matrix from layup
  - Laminate engineering constant: equivalent E_x via lambda^2 method
  - Failure criteria: Tsai-Hill and Tsai-Wu with margins
  - Ancillary: fiber-volume fraction from weight fraction, density ROM

Every result carries an ``equation_id``, ``inputs`` dict, and ``assumptions``
list (MATE-DEC-004 calculation traceability). Numeric capacity is never
fabricated (MATE-SAFE-003); uncomputable results return
``insufficient_data`` or ``fail_closed``.
"""

from __future__ import annotations

import math
from typing import Any

STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_INSUFFICIENT = "insufficient_data"
STATE_FAIL_CLOSED = "fail_closed"

# ── helpers ────────────────────────────────────────────────────────────────


def _check_Vf(Vf: float) -> str | None:
    """Return a state string when Vf is out of [0, 1]."""
    if not isinstance(Vf, (int, float)) or not (0.0 <= Vf <= 1.0):
        return STATE_INSUFFICIENT
    return None


def _check_positive(v: float, label: str) -> str | None:
    """Return state when *v* is non-positive or non-numeric."""
    if not isinstance(v, (int, float)) or v <= 0:
        return STATE_INSUFFICIENT
    return None


def _result(
    value: float | None,
    unit: str,
    equation_id: str,
    inputs: dict[str, Any],
    assumptions: list[str] | None = None,
    state: str = STATE_PASS,
    uncertainty: float = 0.0,
    reason: str = "",
) -> dict[str, Any]:
    """Standard result dict."""
    return {
        "value": value,
        "unit": unit,
        "equation_id": equation_id,
        "inputs": inputs,
        "assumptions": assumptions or [],
        "state": state,
        "uncertainty": uncertainty,
        "reason": reason,
    }


def _verdict(
    failure_mode: str,
    equation_id: str,
    inputs: dict[str, Any],
    capacity: float | None,
    applied: float,
    margin: float | None,
    state: str,
    unit: str = "MPa",
    uncertainty: float = 0.0,
    assumptions: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Standard failure-verdict dict (compatible with strength.py shape)."""
    return {
        "failure_mode": failure_mode,
        "equation_id": equation_id,
        "inputs": inputs,
        "assumptions": assumptions or [],
        "state": state,
        "reason": reason,
        "margin": margin,
        "capacity": capacity,
        "applied": applied,
        "unit": unit,
        "uncertainty": uncertainty,
    }


# ── Rule of Mixtures ───────────────────────────────────────────────────────


def rule_of_mixtures_E1(Vf: float, Ef: float, Em: float) -> dict[str, Any]:
    """Longitudinal modulus (isostrain, upper bound): E1 = Vf*Ef + Vm*Em."""
    eq = "E1=Vf*Ef+(1-Vf)*Em  (ROM isostrain, Voigt bound)"
    inputs: dict[str, Any] = {"Vf": Vf, "Ef": {"value": Ef, "unit": "MPa"}, "Em": {"value": Em, "unit": "MPa"}}
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if not isinstance(Ef, (int, float)) or not isinstance(Em, (int, float)):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Ef and Em must be numeric")
    if Ef < 0 or Em < 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Ef and Em must be non-negative")
    value = Vf * Ef + (1.0 - Vf) * Em
    return _result(value, "MPa", eq, inputs, assumptions=["isostrain (fibers and matrix strain equal)"])


def rule_of_mixtures_E2(Vf: float, Ef: float, Em: float) -> dict[str, Any]:
    """Transverse modulus (isostress, lower bound): 1/E2 = Vf/Ef + Vm/Em."""
    eq = "1/E2=Vf/Ef+(1-Vf)/Em  (ROM isostress, Reuss bound)"
    inputs: dict[str, Any] = {"Vf": Vf, "Ef": {"value": Ef, "unit": "MPa"}, "Em": {"value": Em, "unit": "MPa"}}
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if Ef <= 0 or Em <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Ef and Em must be positive")
    value = 1.0 / (Vf / Ef + (1.0 - Vf) / Em)
    return _result(value, "MPa", eq, inputs, assumptions=["isostress (fibers and matrix stress equal)"])


def rule_of_mixtures_nu12(Vf: float, nu_f: float, nu_m: float) -> dict[str, Any]:
    """Major Poisson ratio: nu12 = Vf*nu_f + Vm*nu_m."""
    eq = "nu12=Vf*nu_f+(1-Vf)*nu_m  (ROM)"
    inputs: dict[str, Any] = {"Vf": Vf, "nu_f": nu_f, "nu_m": nu_m}
    if _check_Vf(Vf):
        return _result(None, "dimensionless", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if not isinstance(nu_f, (int, float)) or not isinstance(nu_m, (int, float)):
        return _result(
            None, "dimensionless", eq, inputs, state=STATE_INSUFFICIENT, reason="nu_f and nu_m must be numeric"
        )
    value = Vf * nu_f + (1.0 - Vf) * nu_m
    return _result(value, "dimensionless", eq, inputs)


def rule_of_mixtures_G12(Vf: float, Gf: float, Gm: float) -> dict[str, Any]:
    """In-plane shear modulus (inverse ROM): 1/G12 = Vf/Gf + Vm/Gm."""
    eq = "1/G12=Vf/Gf+(1-Vf)/Gm  (inverse ROM)"
    inputs: dict[str, Any] = {"Vf": Vf, "Gf": {"value": Gf, "unit": "MPa"}, "Gm": {"value": Gm, "unit": "MPa"}}
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if Gf <= 0 or Gm <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Gf and Gm must be positive")
    value = 1.0 / (Vf / Gf + (1.0 - Vf) / Gm)
    return _result(value, "MPa", eq, inputs, assumptions=["inverse ROM for shear modulus"])


def rule_of_mixtures_strength(Vf: float, sigma_fu: float, sigma_mu: float) -> dict[str, Any]:
    """Longitudinal tensile strength: sigma_1u = Vf*sigma_fu + Vm*sigma_mu."""
    eq = "sigma_1u=Vf*sigma_fu+(1-Vf)*sigma_mu  (ROM strength)"
    inputs: dict[str, Any] = {
        "Vf": Vf,
        "sigma_fu": {"value": sigma_fu, "unit": "MPa"},
        "sigma_mu": {"value": sigma_mu, "unit": "MPa"},
    }
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if not isinstance(sigma_fu, (int, float)) or not isinstance(sigma_mu, (int, float)):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="strengths must be numeric")
    if sigma_fu < 0 or sigma_mu < 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="strengths must be non-negative")
    value = Vf * sigma_fu + (1.0 - Vf) * sigma_mu
    return _result(value, "MPa", eq, inputs, assumptions=["ROM for longitudinal strength"])


# ── Halpin-Tsai ────────────────────────────────────────────────────────────


def halpin_tsai_E2(Vf: float, Ef: float, Em: float, aspect_ratio: float) -> dict[str, Any]:
    """Transverse modulus for short/discontinuous fibers via Halpin-Tsai.

    eta = (Ef/Em - 1) / (Ef/Em + 2*a)   where a = aspect_ratio (L/d)
    E2 = Em * (1 + 2*a*eta*Vf) / (1 - eta*Vf)
    """
    eq = "E2=Em*(1+2*a*eta*Vf)/(1-eta*Vf)  (Halpin-Tsai)"
    inputs: dict[str, Any] = {
        "Vf": Vf,
        "Ef": {"value": Ef, "unit": "MPa"},
        "Em": {"value": Em, "unit": "MPa"},
        "aspect_ratio": aspect_ratio,
    }
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if Ef <= 0 or Em <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Ef and Em must be positive")
    if not isinstance(aspect_ratio, (int, float)) or aspect_ratio <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="aspect_ratio must be positive")
    a = aspect_ratio
    eta = (Ef / Em - 1.0) / (Ef / Em + 2.0 * a)
    value = Em * (1.0 + 2.0 * a * eta * Vf) / (1.0 - eta * Vf)
    return _result(value, "MPa", eq, inputs, assumptions=["short-fiber Halpin-Tsai", "transversely isotropic"])


def halpin_tsai_G12(Vf: float, Gf: float, Gm: float, aspect_ratio: float) -> dict[str, Any]:
    """In-plane shear modulus for short fibers via Halpin-Tsai.

    Uses xi=1 for shear.
    eta = (Gf/Gm - 1) / (Gf/Gm + 1)
    G12 = Gm * (1 + xi*eta*Vf) / (1 - eta*Vf)
    """
    eq = "G12=Gm*(1+xi*eta*Vf)/(1-eta*Vf)  (Halpin-Tsai shear, xi=1)"
    inputs: dict[str, Any] = {
        "Vf": Vf,
        "Gf": {"value": Gf, "unit": "MPa"},
        "Gm": {"value": Gm, "unit": "MPa"},
        "aspect_ratio": aspect_ratio,
    }
    if _check_Vf(Vf):
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if Gf <= 0 or Gm <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="Gf and Gm must be positive")
    if not isinstance(aspect_ratio, (int, float)) or aspect_ratio <= 0:
        return _result(None, "MPa", eq, inputs, state=STATE_INSUFFICIENT, reason="aspect_ratio must be positive")
    eta = (Gf / Gm - 1.0) / (Gf / Gm + 1.0)
    value = Gm * (1.0 + 1.0 * eta * Vf) / (1.0 - eta * Vf)
    return _result(value, "MPa", eq, inputs, assumptions=["short-fiber Halpin-Tsai shear", "xi=1"])


# ── Ply stiffness ──────────────────────────────────────────────────────────


def compute_ply_stiffness(E1: float, E2: float, nu12: float, G12: float) -> dict[str, Any]:
    """Reduced stiffness matrix Q for an orthotropic lamina in principal axes.

    Q11 = E1 / (1 - nu12*nu21),  Q12 = nu12*E2 / (1 - nu12*nu21)
    Q22 = E2 / (1 - nu12*nu21),  Q66 = G12
    where nu21 = nu12 * E2 / E1.
    """
    eq = "Q_ij reduced stiffness (orthotropic lamina)"
    inputs: dict[str, Any] = {
        "E1": {"value": E1, "unit": "MPa"},
        "E2": {"value": E2, "unit": "MPa"},
        "nu12": nu12,
        "G12": {"value": G12, "unit": "MPa"},
    }

    if E1 <= 0 or E2 <= 0:
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": inputs,
            "assumptions": [],
            "reason": "E1 and E2 must be positive",
        }
    if not isinstance(nu12, (int, float)):
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": inputs,
            "assumptions": [],
            "reason": "nu12 must be numeric",
        }
    if abs(nu12) >= 0.5:
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": inputs,
            "assumptions": [],
            "reason": "|nu12| >= 0.5 unphysical for structural composites",
        }
    nu21 = nu12 * E2 / E1
    denom = 1.0 - nu12 * nu21
    if denom <= 0:
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": inputs,
            "assumptions": [],
            "reason": "nu12*nu21 >= 1 (unphysical)",
        }
    return {
        "Q11": E1 / denom,
        "Q12": nu12 * E2 / denom,
        "Q22": E2 / denom,
        "Q66": G12,
        "state": STATE_PASS,
        "equation_id": eq,
        "inputs": inputs,
        "assumptions": ["orthotropic", "plane stress"],
        "reason": "",
    }


# ── Transform stiffness (in-plane rotation) ────────────────────────────────


def transform_stiffness(theta_deg: float, Q: dict[str, float]) -> dict[str, Any]:
    """Rotate reduced stiffness matrix Q_bar(theta) for angle-ply lamina.

    Q_bar = T^{-1} * Q * T^{-T}  (in-plane rotation tensor transformation).

    Returns dict with Q11, Q12, Q22, Q66, Q16, Q26.
    """
    eq = "Q_bar ij = transformed stiffness (in-plane rotation)"
    if not isinstance(theta_deg, (int, float)):
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "Q16": None,
            "Q26": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": {"theta_deg": theta_deg},
            "assumptions": [],
            "reason": "theta_deg must be numeric",
        }
    required = {"Q11", "Q12", "Q22", "Q66"}
    if not required.issubset(Q.keys()):
        return {
            "Q11": None,
            "Q12": None,
            "Q22": None,
            "Q66": None,
            "Q16": None,
            "Q26": None,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": {"theta_deg": theta_deg, "Q": {k: Q.get(k) for k in required}},
            "assumptions": [],
            "reason": "Q missing required keys",
        }

    theta = math.radians(theta_deg)
    c = math.cos(theta)
    s = math.sin(theta)

    Q11, Q12, Q22, Q66 = Q["Q11"], Q["Q12"], Q["Q22"], Q["Q66"]

    c4 = c * c * c * c
    s4 = s * s * s * s
    c3s = c * c * c * s
    cs3 = c * s * s * s
    c2s2 = c * c * s * s

    Qb11 = Q11 * c4 + 2.0 * (Q12 + 2.0 * Q66) * c2s2 + Q22 * s4
    Qb12 = (Q11 + Q22 - 4.0 * Q66) * c2s2 + Q12 * (c4 + s4)
    Qb22 = Q11 * s4 + 2.0 * (Q12 + 2.0 * Q66) * c2s2 + Q22 * c4
    Qb66 = (Q11 + Q22 - 2.0 * Q12 - 2.0 * Q66) * c2s2 + Q66 * (c4 + s4)
    Qb16 = (Q11 - Q12 - 2.0 * Q66) * c3s + (Q12 - Q22 + 2.0 * Q66) * cs3
    Qb26 = (Q11 - Q12 - 2.0 * Q66) * cs3 + (Q12 - Q22 + 2.0 * Q66) * c3s

    return {
        "Q11": Qb11,
        "Q12": Qb12,
        "Q22": Qb22,
        "Q66": Qb66,
        "Q16": Qb16,
        "Q26": Qb26,
        "state": STATE_PASS,
        "equation_id": eq,
        "inputs": {"theta_deg": theta_deg},
        "assumptions": ["plane stress", "orthotropic in principal axes"],
        "reason": "",
    }


# ── ABD matrix ─────────────────────────────────────────────────────────────


def build_abd_from_layup(
    angles_deg: list[float],
    Q: dict[str, Any],
    thicknesses: list[float],
) -> dict[str, Any]:
    """Build [A] (extensional), [B] (coupling), [D] (bending) 3x3 matrices.

    Mid-plane is z=0; each ply k has centroid z_k and thickness t_k.
    A_ij = sum_k (Qbar_ij)_k * t_k
    B_ij = sum_k (Qbar_ij)_k * t_k * z_k
    D_ij = sum_k (Qbar_ij)_k * (t_k * z_k^2 + t_k^3 / 12)
    """
    eq = "ABD matrix from layup (Classical Laminate Theory)"
    n = len(angles_deg)
    if n == 0 or n != len(thicknesses):
        return {
            "A": [0.0] * 3,
            "B": [0.0] * 3,
            "D": [0.0] * 3,
            "state": STATE_INSUFFICIENT,
            "equation_id": eq,
            "inputs": {"layup": angles_deg, "thicknesses": thicknesses, "n_plies": n, "total_thickness": 0.0},
            "assumptions": [],
            "reason": "layup empty or thickness list mismatch",
        }

    for i, t in enumerate(thicknesses):
        if not isinstance(t, (int, float)) or t <= 0:
            return {
                "A": [0.0] * 3,
                "B": [0.0] * 3,
                "D": [0.0] * 3,
                "state": STATE_INSUFFICIENT,
                "equation_id": eq,
                "inputs": {
                    "layup": angles_deg,
                    "thicknesses": thicknesses,
                    "n_plies": n,
                    "total_thickness": sum(thicknesses),
                },
                "assumptions": [],
                "reason": f"ply {i + 1} thickness must be positive",
            }
        if not isinstance(angles_deg[i], (int, float)):
            return {
                "A": [0.0] * 3,
                "B": [0.0] * 3,
                "D": [0.0] * 3,
                "state": STATE_INSUFFICIENT,
                "equation_id": eq,
                "inputs": {
                    "layup": angles_deg,
                    "thicknesses": thicknesses,
                    "n_plies": n,
                    "total_thickness": sum(thicknesses),
                },
                "assumptions": [],
                "reason": f"ply {i + 1} angle must be numeric",
            }

    total_t = sum(thicknesses)
    z_bottom = -total_t / 2.0

    A = [0.0, 0.0, 0.0]
    B = [0.0, 0.0, 0.0]
    D = [0.0, 0.0, 0.0]

    z_k_low = z_bottom
    for _i, (theta, t) in enumerate(zip(angles_deg, thicknesses, strict=False)):
        z_k = z_k_low + t / 2.0

        if theta == 0.0 or theta == 90.0 or theta == -90.0:
            Qb11 = Q["Q11"] if abs(theta) < 1e-9 else Q["Q22"]
            Qb12, Qb66 = Q["Q12"], Q["Q66"]
        else:
            Qb = transform_stiffness(theta, Q)
            Qb11, Qb12, _, Qb66 = Qb["Q11"], Qb["Q12"], Qb["Q22"], Qb["Q66"]

        A[0] += Qb11 * t
        A[1] += Qb12 * t
        A[2] += Qb66 * t
        B[0] += Qb11 * t * z_k
        B[1] += Qb12 * t * z_k
        B[2] += Qb66 * t * z_k
        D[0] += Qb11 * (t * z_k * z_k + t**3 / 12.0)
        D[1] += Qb12 * (t * z_k * z_k + t**3 / 12.0)
        D[2] += Qb66 * (t * z_k * z_k + t**3 / 12.0)

        z_k_low += t

    return {
        "A": A,
        "B": B,
        "D": D,
        "state": STATE_PASS,
        "equation_id": eq,
        "inputs": {"layup": angles_deg, "thicknesses": thicknesses, "n_plies": n, "total_thickness": total_t},
        "assumptions": ["plane stress", "perfect bonding", "linear elastic", "thin laminate"],
        "reason": "",
    }


# ── Laminate engineering constant ──────────────────────────────────────────


def compute_lambda2(
    angles_deg: list[float],
    Q: dict[str, Any],
    thicknesses: list[float],
) -> dict[str, Any]:
    """Approximate laminate longitudinal modulus via λ² method.

    E_x ≈ (1 / total_thickness) * sum_k (Qbar_11)_k * t_k  [exact for balanced/symmetric].
    Returns the equivalent stiffness result dict.
    """
    eq = "E_x laminate engineering constant (lambda^2)"
    n = len(angles_deg)
    if n == 0 or n != len(thicknesses):
        return _result(None, "MPa", eq, {"n_plies": n}, state=STATE_INSUFFICIENT, reason="empty or mismatched layup")

    total_t = sum(thicknesses)
    if total_t <= 0:
        return _result(
            None,
            "MPa",
            eq,
            {"n_plies": n, "total_thickness": total_t},
            state=STATE_INSUFFICIENT,
            reason="total thickness must be positive",
        )

    ex_sum = 0.0
    for theta, t in zip(angles_deg, thicknesses, strict=False):
        if abs(theta) < 1e-9:
            ex_sum += Q["Q11"] * t
        elif abs(abs(theta) - 90.0) < 1e-9:
            ex_sum += Q["Q22"] * t
        else:
            Qb = transform_stiffness(theta, Q)
            ex_sum += Qb["Q11"] * t

    E_x = ex_sum / total_t
    return _result(
        E_x,
        "MPa",
        eq,
        {"layup": angles_deg, "thicknesses": thicknesses, "n_plies": n, "total_thickness": total_t},
        assumptions=["plane stress", "approximate laminate modulus (lambda^2)"],
    )


# ── Failure criteria ───────────────────────────────────────────────────────


def tsai_hill_margin(
    sigma1: float,
    sigma2: float,
    tau12: float,
    X: float,
    Y: float,
    S: float,
) -> dict[str, Any]:
    """Tsai-Hill failure index and margin.

    FI² = (sigma1/X)² + (sigma2/Y)² - sigma1*sigma2/X² + (tau12/S)²
    margin = 1/FI - 1  (positive = safe, negative = failed).
    """
    eq = "Tsai-Hill: FI^2=(s1/X)^2+(s2/Y)^2-s1*s2/X^2+(t12/S)^2"
    inputs: dict[str, Any] = {
        "sigma1": {"value": sigma1, "unit": "MPa"},
        "sigma2": {"value": sigma2, "unit": "MPa"},
        "tau12": {"value": tau12, "unit": "MPa"},
        "X": {"value": X, "unit": "MPa"},
        "Y": {"value": Y, "unit": "MPa"},
        "S": {"value": S, "unit": "MPa"},
    }
    assumptions = ["quadratic interactive", "plane stress"]

    if X <= 0 or Y <= 0 or S <= 0:
        return _verdict(
            "tsai_hill",
            eq,
            inputs,
            None,
            0.0,
            None,
            STATE_INSUFFICIENT,
            assumptions=assumptions,
            reason="X, Y, S must be positive",
        )

    term1 = (sigma1 / X) ** 2
    term2 = (sigma2 / Y) ** 2
    term3 = -(sigma1 * sigma2) / (X * X)
    term4 = (tau12 / S) ** 2
    fi_sq = term1 + term2 + term3 + term4

    fi = math.sqrt(max(fi_sq, 0.0))
    margin = 1e9 if fi <= 0 else 1.0 / fi - 1.0

    capacity = 1.0
    applied = fi_sq

    return _verdict(
        "tsai_hill",
        eq,
        inputs,
        capacity,
        applied,
        margin,
        STATE_PASS if margin > 0 else STATE_FAIL,
        uncertainty=0.05 * X,
        assumptions=assumptions,
    )


def tsai_wu_margin(
    sigma1: float,
    sigma2: float,
    tau12: float,
    Xt: float,
    Xc: float,
    Yt: float,
    Yc: float,
    S: float,
) -> dict[str, Any]:
    """Tsai-Wu quadratic failure criterion.

    F1*sigma1 + F2*sigma2 + F11*sigma1^2 + F22*sigma2^2 + 2*F12*sigma1*sigma2 + F66*tau12^2 = 1

    F1 = 1/Xt - 1/Xc,  F2 = 1/Yt - 1/Yc
    F11 = 1/(Xt*Xc),    F22 = 1/(Yt*Yc)
    F12 = -1/(2*sqrt(Xt*Xc*Yt*Yc))  (default interaction)
    F66 = 1/S^2

    margin = 1/FI - 1.
    """
    eq = "Tsai-Wu: F_i*sigma_i + F_ij*sigma_i*sigma_j = 1"
    inputs: dict[str, Any] = {
        "sigma1": {"value": sigma1, "unit": "MPa"},
        "sigma2": {"value": sigma2, "unit": "MPa"},
        "tau12": {"value": tau12, "unit": "MPa"},
        "Xt": {"value": Xt, "unit": "MPa"},
        "Xc": {"value": Xc, "unit": "MPa"},
        "Yt": {"value": Yt, "unit": "MPa"},
        "Yc": {"value": Yc, "unit": "MPa"},
        "S": {"value": S, "unit": "MPa"},
    }

    if Xt <= 0 or Xc <= 0 or Yt <= 0 or Yc <= 0 or S <= 0:
        return _verdict(
            "tsai_wu",
            eq,
            inputs,
            None,
            0.0,
            None,
            STATE_INSUFFICIENT,
            assumptions=["quadratic interactive", "plane stress"],
            reason="strength values must be positive",
        )

    F1 = 1.0 / Xt - 1.0 / Xc
    F2 = 1.0 / Yt - 1.0 / Yc
    F11 = 1.0 / (Xt * Xc)
    F22 = 1.0 / (Yt * Yc)
    F12 = -1.0 / (2.0 * math.sqrt(Xt * Xc * Yt * Yc))
    F66 = 1.0 / (S * S)

    fi = (
        F1 * sigma1
        + F2 * sigma2
        + F11 * sigma1 * sigma1
        + F22 * sigma2 * sigma2
        + 2.0 * F12 * sigma1 * sigma2
        + F66 * tau12 * tau12
    )

    margin = 1e9 if fi <= 0 else 1.0 / fi - 1.0

    capacity = 1.0
    applied = fi

    return _verdict(
        "tsai_wu",
        eq,
        inputs,
        capacity,
        applied,
        margin,
        STATE_PASS if margin > 0 else STATE_FAIL,
        unit="MPa",
        uncertainty=0.05 * Xt,
        assumptions=["quadratic interactive", "plane stress", "F12 default interaction"],
    )


# ── Fiber-volume fraction ──────────────────────────────────────────────────


def compute_fiber_volume_fraction(Wf: float, rho_f: float, rho_m: float) -> dict[str, Any]:
    """Convert weight fraction to volume fraction.

    Vf = (Wf/rho_f) / (Wf/rho_f + (1-Wf)/rho_m)
    """
    eq = "Vf=(Wf/rho_f)/(Wf/rho_f+(1-Wf)/rho_m)"
    inputs: dict[str, Any] = {
        "Wf": Wf,
        "rho_f": {"value": rho_f, "unit": "g/cm^3"},
        "rho_m": {"value": rho_m, "unit": "g/cm^3"},
    }
    if not isinstance(Wf, (int, float)) or not (0.0 <= Wf <= 1.0):
        return _result(None, "dimensionless", eq, inputs, state=STATE_INSUFFICIENT, reason="Wf must be in [0, 1]")
    if rho_f <= 0 or rho_m <= 0:
        return _result(None, "dimensionless", eq, inputs, state=STATE_INSUFFICIENT, reason="densities must be positive")
    value = (Wf / rho_f) / (Wf / rho_f + (1.0 - Wf) / rho_m)
    return _result(value, "dimensionless", eq, inputs)


def density_rom(Vf: float, rho_f: float, rho_m: float) -> dict[str, Any]:
    """Composite density via rule of mixtures: rho = Vf*rho_f + Vm*rho_m."""
    eq = "rho_c=Vf*rho_f+(1-Vf)*rho_m  (density ROM)"
    inputs: dict[str, Any] = {
        "Vf": Vf,
        "rho_f": {"value": rho_f, "unit": "g/cm^3"},
        "rho_m": {"value": rho_m, "unit": "g/cm^3"},
    }
    if _check_Vf(Vf):
        return _result(None, "g/cm^3", eq, inputs, state=STATE_INSUFFICIENT, reason="Vf must be in [0, 1]")
    if not isinstance(rho_f, (int, float)) or not isinstance(rho_m, (int, float)):
        return _result(None, "g/cm^3", eq, inputs, state=STATE_INSUFFICIENT, reason="densities must be numeric")
    value = Vf * rho_f + (1.0 - Vf) * rho_m
    return _result(value, "g/cm^3", eq, inputs)


__all__ = [
    "STATE_FAIL",
    "STATE_FAIL_CLOSED",
    "STATE_INSUFFICIENT",
    "STATE_PASS",
    "build_abd_from_layup",
    "compute_fiber_volume_fraction",
    "compute_lambda2",
    "compute_ply_stiffness",
    "density_rom",
    "halpin_tsai_E2",
    "halpin_tsai_G12",
    "rule_of_mixtures_E1",
    "rule_of_mixtures_E2",
    "rule_of_mixtures_G12",
    "rule_of_mixtures_nu12",
    "rule_of_mixtures_strength",
    "transform_stiffness",
    "tsai_hill_margin",
    "tsai_wu_margin",
]
