"""Failure hypothesis development for spec MATE-001 section 3
(``failure_analyze`` role) and section 4.7.

The :class:`FailureAnalyzer` enumerates *competing* failure hypotheses from a
load case and a material property record, then prescribes a combined
nondestructive + destructive test plan to confirm or refute each one. It is
deliberately conservative about causality: every hypothesis carries an explicit
``confidence_state`` in ``{candidate, ruled_out, insufficient_data}`` — never
``confirmed`` — because root-cause attribution requires physical evidence the
analyzer does not possess (MATE-SAFE-003: no fabricated precision).

Hypotheses are screened using the closed-form checks in
:mod:`general_ludd.materials.strength` where applicable. Where a load case or
material property is missing the analyzer returns ``insufficient_data`` rather
than guessing.
"""

from __future__ import annotations

from typing import Any

from general_ludd.materials import strength

# Allowed confidence states. ``confirmed`` is intentionally absent — root cause
# requires physical evidence, not an analytical model.
CONFIDENCE_STATES: frozenset[str] = frozenset({"candidate", "ruled_out", "insufficient_data"})

CANDIDATE = "candidate"
RULED_OUT = "ruled_out"
INSUFFICIENT = "insufficient_data"

# Thresholds for elevated-temperature creep consideration (steel-like baseline).
CREEP_THRESHOLD_K = 0.4  # homologous: T/T_melt above this flags creep risk
STEEL_MELT_K = 1810.0  # approximate; only used as a fallback baseline


def _as_prop(value_MPa: float | None) -> dict[str, Any] | None:
    """Wrap a MPa capacity into the property-record shape strength.py expects."""
    if value_MPa is None:
        return None
    return {"value": float(value_MPa), "unit": "MPa", "uncertainty": 0.0}


def _extract_stress(load_case: dict[str, Any]) -> float | None:
    """Pull a representative stress magnitude out of a load case dict."""
    for key in ("max_stress_MPa", "stress_MPa", "applied_stress_MPa"):
        v = load_case.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


class FailureAnalyzer:
    """Develops competing failure hypotheses and a test plan from a load case.

    The analyzer is read-only: it never mutates its inputs and never claims
    confirmation. It ranks hypotheses by plausibility given the supplied
    numbers and flags the ones the evidence cannot yet resolve.
    """

    def develop_hypotheses(
        self,
        load_case: dict[str, Any],
        material: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return a list of competing failure hypotheses.

        Each hypothesis is a dict with:

          - ``failure_mode``: ``yield`` | ``fracture`` | ``fatigue`` | ``creep``
            | ``buckling``
          - ``confidence_state``: ``candidate`` | ``ruled_out`` |
            ``insufficient_data`` (never ``confirmed``)
          - ``rationale``: short string citing the screening check
          - ``inputs``: the numbers used (with units)
        """
        hyps: list[dict[str, Any]] = []
        stress_MPa = _extract_stress(load_case)
        load_type = str(load_case.get("type", "static")).lower()
        cycles = load_case.get("cycles")
        temperature_K = load_case.get("temperature_K")
        is_cyclic = "cycl" in load_type or "fatigue" in load_type or "alternating" in load_type

        # ── yield ──────────────────────────────────────────────────────────
        yield_cap = _as_prop(material.get("yield_MPa") or material.get("yield_strength_MPa"))
        if yield_cap is not None and stress_MPa is not None:
            check = strength.check_tension(yield_cap, stress_MPa)
            state = CANDIDATE if check["state"] == strength.STATE_FAIL else RULED_OUT
            rationale = (
                f"applied {stress_MPa:.1f} MPa vs yield {yield_cap['value']:.1f} MPa → margin {check['margin']:.3f}"
            )
        else:
            state = INSUFFICIENT
            rationale = "missing yield capacity or applied stress"
        hyps.append(
            {
                "failure_mode": "yield",
                "confidence_state": state,
                "rationale": rationale,
                "inputs": {"applied_stress_MPa": stress_MPa, "yield_MPa": yield_cap["value"] if yield_cap else None},
            }
        )

        # ── fracture (ultimate) ────────────────────────────────────────────
        ult_cap = _as_prop(material.get("ultimate_MPa") or material.get("ultimate_strength_MPa"))
        if ult_cap is not None and stress_MPa is not None:
            check = strength.check_tension(ult_cap, stress_MPa)
            state = CANDIDATE if check["state"] == strength.STATE_FAIL else RULED_OUT
            rationale = (
                f"applied {stress_MPa:.1f} MPa vs ultimate {ult_cap['value']:.1f} MPa → margin {check['margin']:.3f}"
            )
        else:
            state = INSUFFICIENT
            rationale = "missing ultimate capacity or applied stress"
        hyps.append(
            {
                "failure_mode": "fracture",
                "confidence_state": state,
                "rationale": rationale,
                "inputs": {"applied_stress_MPa": stress_MPa, "ultimate_MPa": ult_cap["value"] if ult_cap else None},
            }
        )

        # ── fatigue ────────────────────────────────────────────────────────
        endurance = material.get("endurance_MPa") or material.get("endurance_limit_MPa")
        if is_cyclic:
            if endurance is not None and stress_MPa is not None and isinstance(cycles, int) and cycles > 0:
                check = strength.check_fatigue_sn(
                    S_ut_MPa=ult_cap["value"] if ult_cap else endurance,
                    applied_amplitude_MPa=stress_MPa,
                    cycles=cycles,
                    S_e_MPa=float(endurance),
                )
                state = CANDIDATE if check["state"] == strength.STATE_FAIL else RULED_OUT
                rationale = (
                    f"cyclic {stress_MPa:.1f} MPa at N={cycles} vs allowable "
                    f"{check['capacity']:.1f} MPa → margin {check['margin']:.3f}"
                )
            else:
                state = INSUFFICIENT
                rationale = "cyclic load but missing endurance limit, amplitude, or cycle count"
        else:
            state = RULED_OUT
            rationale = "load case is not cyclic"
        hyps.append(
            {
                "failure_mode": "fatigue",
                "confidence_state": state,
                "rationale": rationale,
                "inputs": {
                    "applied_amplitude_MPa": stress_MPa if is_cyclic else None,
                    "endurance_MPa": float(endurance) if endurance is not None else None,
                    "cycles": cycles,
                },
            }
        )

        # ── creep ──────────────────────────────────────────────────────────
        melt_K = material.get("melt_K") or STEEL_MELT_K
        if isinstance(temperature_K, (int, float)) and temperature_K > 0:
            homologous = temperature_K / melt_K
            if homologous >= CREEP_THRESHOLD_K:
                state = CANDIDATE
                rationale = (
                    f"T/Tm = {temperature_K:.0f}/{melt_K:.0f} = {homologous:.2f} >= {CREEP_THRESHOLD_K} → creep risk"
                )
            else:
                state = RULED_OUT
                rationale = f"T/Tm = {homologous:.2f} < {CREEP_THRESHOLD_K} → creep unlikely"
        else:
            state = INSUFFICIENT
            rationale = "service temperature not provided"
        hyps.append(
            {
                "failure_mode": "creep",
                "confidence_state": state,
                "rationale": rationale,
                "inputs": {"temperature_K": temperature_K, "melt_K": melt_K},
            }
        )

        # ── buckling ───────────────────────────────────────────────────────
        # Buckling requires a slender geometry under compression; without
        # geometry inputs we cannot screen it, so it stays insufficient_data.
        has_geometry = all(load_case.get(k) is not None for k in ("E_MPa", "I_mm4", "L_mm"))
        is_compressive = "compress" in load_type or load_case.get("compressive_force_N")
        if has_geometry and is_compressive:
            check = strength.check_buckling_euler(
                E_MPa=float(load_case["E_MPa"]),
                I_mm4=float(load_case["I_mm4"]),
                L_mm=float(load_case["L_mm"]),
                K=float(load_case.get("K", 1.0)),
                applied_force_N=float(load_case.get("compressive_force_N", 0.0)),
            )
            state = CANDIDATE if check["state"] == strength.STATE_FAIL else RULED_OUT
            rationale = f"Euler P_cr={check['capacity']:.1f} N vs applied"
        elif is_compressive and not has_geometry:
            state = INSUFFICIENT
            rationale = "compressive load present but geometry (E/I/L) not provided"
        else:
            state = RULED_OUT
            rationale = "no compressive loading in load case"
        hyps.append(
            {
                "failure_mode": "buckling",
                "confidence_state": state,
                "rationale": rationale,
                "inputs": {
                    "E_MPa": load_case.get("E_MPa"),
                    "I_mm4": load_case.get("I_mm4"),
                    "L_mm": load_case.get("L_mm"),
                    "compressive_force_N": load_case.get("compressive_force_N"),
                },
            }
        )

        return hyps

    def prescribe_tests(self, hypotheses: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a combined NDT + destructive test plan to confirm or refute
        each *candidate* hypothesis.

        Returns ``{"tests": [...], "limitations": [...]}``. Each test cites the
        ``targets_hypothesis`` it is designed to resolve. Ruled-out and
        insufficient-data hypotheses are surfaced in ``limitations`` so the
        caller knows which evidence gaps remain.
        """
        tests: list[dict[str, Any]] = []
        limitations: list[str] = []

        candidates = [h for h in hypotheses if h["confidence_state"] == CANDIDATE]
        if not candidates:
            limitations.append(
                "no candidate failure mode identified; prescribe baseline incoming inspection per MATE-DEC-002 step 6"
            )

        for h in candidates:
            mode = h["failure_mode"]
            if mode == "yield":
                tests.append(
                    {
                        "method": "tensile_test",
                        "category": "destructive",
                        "specimen": "representative coupon per ASTM E8",
                        "purpose": "measure yield strength vs applied stress",
                        "targets_hypothesis": mode,
                    }
                )
            elif mode == "fracture":
                tests.append(
                    {
                        "method": "SEM_fractography",
                        "category": "destructive",
                        "specimen": "failed fracture surface or coupon",
                        "purpose": "distinguish brittle/ductile overload fracture",
                        "targets_hypothesis": mode,
                    }
                )
                tests.append(
                    {
                        "method": "charpy_impact",
                        "category": "destructive",
                        "specimen": "V-notch bars per ASTM E23",
                        "purpose": "measure toughness at service temperature",
                        "targets_hypothesis": mode,
                    }
                )
            elif mode == "fatigue":
                tests.append(
                    {
                        "method": "fluorescent_penetrant",
                        "category": "nondestructive",
                        "specimen": "in-service part or service-equivalent coupon",
                        "purpose": "detect fatigue crack initiation sites",
                        "targets_hypothesis": mode,
                    }
                )
                tests.append(
                    {
                        "method": "S-N_fatigue_test",
                        "category": "destructive",
                        "specimen": "coupon at service load spectrum per ASTM E466",
                        "purpose": "establish finite-life margin under service spectrum",
                        "targets_hypothesis": mode,
                    }
                )
            elif mode == "creep":
                tests.append(
                    {
                        "method": "metallography_sectioning",
                        "category": "destructive",
                        "specimen": "high-temperature region coupon",
                        "purpose": "detect creep cavitation / grain-boundary voids",
                        "targets_hypothesis": mode,
                    }
                )
                tests.append(
                    {
                        "method": "creep_rupture_test",
                        "category": "destructive",
                        "specimen": "coupon at service T and stress per ASTM E139",
                        "purpose": "measure steady-state creep rate and rupture life",
                        "targets_hypothesis": mode,
                    }
                )
            elif mode == "buckling":
                tests.append(
                    {
                        "method": "dimensional_inspection",
                        "category": "nondestructive",
                        "specimen": "as-built column geometry",
                        "purpose": "verify straightness and section properties vs design",
                        "targets_hypothesis": mode,
                    }
                )

        for h in hypotheses:
            if h["confidence_state"] == INSUFFICIENT:
                limitations.append(f"hypothesis '{h['failure_mode']}' unresolved: {h['rationale']}")

        if not any(t["category"] == "nondestructive" for t in tests):
            tests.append(
                {
                    "method": "visual_and_dimensional",
                    "category": "nondestructive",
                    "specimen": "as-received part",
                    "purpose": "baseline incoming inspection per MATE-DEC-002",
                    "targets_hypothesis": "baseline",
                }
            )

        return {
            "tests": tests,
            "limitations": limitations,
            "causality_note": (
                "Hypotheses are analytical candidates only; root-cause "
                "attribution requires the physical evidence prescribed here "
                "(MATE-SAFE-003)."
            ),
        }


__all__ = [
    "CANDIDATE",
    "CONFIDENCE_STATES",
    "INSUFFICIENT",
    "RULED_OUT",
    "FailureAnalyzer",
]
