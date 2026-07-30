"""CHEM-019 result-validation framework (Phase D).

Implements CHEM-019 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §10.
The spec mandates that "every workflow declares validators before execution"
and that "Validation status is ``validated``, ``provisional``, ``invalid``, or
``not_applicable``; only ``validated`` results may support execution-facing
artifacts."

This module provides:

* :class:`ValidationStatus` — the four status strings.
* :func:`supports_execution` — gate that only ``validated`` passes (per §10
  last sentence).
* :func:`validate_result` — runs the declared checks against a result dict and
  returns the aggregate validation status plus a per-check ``verification``
  list. Supported check kinds (per §10 "numerical work" + "experiments" +
  "computation" categories):

    - ``mass_conservation``    — ``mass_in`` ≈ ``mass_out`` (within tolerance)
    - ``charge_conservation``  — ``charge_in`` ≈ ``charge_out``
    - ``energy_conservation``  — ``energy_in`` ≈ ``energy_out``
    - ``atom_conservation``    — ``atoms_in`` ≈ ``atoms_out`` (dict per element)
    - ``unit_consistency``     — every value in ``values`` carries a unit
    - ``convergence``          — ``converged`` is True
    - ``limiting_case``        — ``input_zero`` ⇒ ``output_zero``
    - ``sensitivity``          — ``sensitivity`` float below declared threshold

  Each per-check record carries ``check``, ``status`` (``pass`` / ``fail`` /
  ``warn``), ``detail``, and any ``tolerance`` applied.

The aggregate status algorithm:

* any check ``fail`` ⇒ status = ``invalid``
* else any check ``warn`` ⇒ status = ``provisional``
* else status = ``validated``
"""

from __future__ import annotations

import uuid
from typing import Any

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-validation@0.1.0"


class ValidationStatus:
    """Validation status string constants (CHEM §10)."""

    VALIDATED = "validated"
    PROVISIONAL = "provisional"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"


def _new_id() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def supports_execution(status: str) -> bool:
    """Only ``validated`` results may support execution-facing artifacts."""
    return status == ValidationStatus.VALIDATED


# ---------------------------------------------------------------------------
# Per-check implementations
# ---------------------------------------------------------------------------


def _check_conservation(
    result: dict[str, Any],
    in_key: str,
    out_key: str,
    name: str,
    tol_pct: float,
) -> dict[str, Any]:
    """Generic conservation check on a scalar ``in``/``out`` pair."""
    if in_key not in result or out_key not in result:
        return {
            "check": name,
            "status": "warn",
            "detail": f"missing '{in_key}' or '{out_key}' field",
            "tolerance_pct": tol_pct,
        }
    in_v = float(result[in_key])
    out_v = float(result[out_key])
    ref = abs(in_v) if abs(in_v) > 0.0 else max(abs(in_v), abs(out_v), 1.0)
    rel_err = abs(in_v - out_v) / ref * 100.0 if ref > 0.0 else 0.0
    status = "pass" if rel_err <= tol_pct else "fail"
    return {
        "check": name,
        "status": status,
        "detail": f"{in_key}={in_v:g}, {out_key}={out_v:g}, rel_err={rel_err:.4g}%",
        "tolerance_pct": tol_pct,
        "absolute_delta": in_v - out_v,
        "relative_error_pct": rel_err,
    }


def _check_atom_conservation(result: dict[str, Any], tol_pct: float) -> dict[str, Any]:
    """Atom-count conservation: dict per element compared across in/out."""
    in_atoms = result.get("atoms_in", {})
    out_atoms = result.get("atoms_out", {})
    if not in_atoms and not out_atoms:
        return {
            "check": "atom_conservation",
            "status": "warn",
            "detail": "missing 'atoms_in' or 'atoms_out' field",
            "tolerance_pct": tol_pct,
        }
    elements = set(in_atoms) | set(out_atoms)
    worst: dict[str, Any] | None = None
    for el in elements:
        in_c = float(in_atoms.get(el, 0.0))
        out_c = float(out_atoms.get(el, 0.0))
        ref = max(abs(in_c), abs(out_c), 1.0)
        rel = abs(in_c - out_c) / ref * 100.0
        if worst is None or rel > worst["relative_error_pct"]:
            worst = {
                "element": el,
                "in": in_c,
                "out": out_c,
                "relative_error_pct": rel,
            }
    if worst is None:
        return {
            "check": "atom_conservation",
            "status": "pass",
            "detail": "no elements to compare",
            "tolerance_pct": tol_pct,
            "relative_error_pct": 0.0,
        }
    status = "pass" if worst["relative_error_pct"] <= tol_pct else "fail"
    return {
        "check": "atom_conservation",
        "status": status,
        "detail": f"worst element {worst['element']}: in={worst['in']:g}, "
        f"out={worst['out']:g}, rel_err={worst['relative_error_pct']:.4g}%",
        "tolerance_pct": tol_pct,
        "relative_error_pct": worst["relative_error_pct"],
        "worst_element": worst["element"],
    }


def _check_unit_consistency(result: dict[str, Any]) -> dict[str, Any]:
    """Every entry in ``values`` must carry a unit, and all units must agree.

    "Consistency" has two facets: (1) every value declares a non-empty unit,
    and (2) when multiple values are present they all share the same unit so a
    downstream numeric combination is well-defined. Mismatched units (e.g.
    ``mg/L`` vs ``mol/L``) fail the check.
    """
    values = result.get("values", [])
    missing = []
    units: list[str] = []
    for v in values:
        if not isinstance(v, dict):
            continue
        unit = v.get("unit")
        if not unit:
            missing.append(v.get("name", "<unnamed>"))
        else:
            units.append(unit)
    if missing:
        return {
            "check": "unit_consistency",
            "status": "fail",
            "detail": f"{len(missing)} value(s) missing unit: {missing}",
            "missing": missing,
        }
    distinct = set(units)
    if len(distinct) > 1:
        return {
            "check": "unit_consistency",
            "status": "fail",
            "detail": f"inconsistent units across {len(units)} value(s): {sorted(distinct)}",
            "units": sorted(distinct),
        }
    return {
        "check": "unit_consistency",
        "status": "pass",
        "detail": f"{len(values)} value(s) all carry unit '{units[0] if units else 'n/a'}'",
        "n_values": len(values),
        "unit": units[0] if units else None,
    }


def _check_convergence(result: dict[str, Any]) -> dict[str, Any]:
    """``converged`` field must be True. ``iterations`` recorded when present."""
    converged = bool(result.get("converged", False))
    iterations = result.get("iterations")
    warnings = result.get("warnings", []) or []
    status = "pass" if converged else "fail"
    detail = f"converged={converged}" + (f", iterations={iterations}" if iterations is not None else "")
    if converged and warnings:
        # Converged but warnings present -> demote to warn (provisional).
        status = "warn"
        detail += f"; warnings={list(warnings)}"
    return {
        "check": "convergence",
        "status": status,
        "detail": detail,
        "converged": converged,
        "iterations": iterations,
    }


def _check_limiting_case(result: dict[str, Any]) -> dict[str, Any]:
    """Zero input must produce zero output."""
    input_zero = bool(result.get("input_zero", False))
    output_zero = bool(result.get("output_zero", False))
    case = result.get("limiting_case", "unspecified")
    if not input_zero:
        return {
            "check": "limiting_case",
            "status": "warn",
            "detail": f"limiting_case '{case}' did not exercise input_zero",
            "limiting_case": case,
        }
    status = "pass" if output_zero else "fail"
    return {
        "check": "limiting_case",
        "status": status,
        "detail": f"case '{case}': input_zero=True, output_zero={output_zero}",
        "limiting_case": case,
        "input_zero": input_zero,
        "output_zero": output_zero,
    }


def _check_sensitivity(result: dict[str, Any]) -> dict[str, Any]:
    """Reported ``sensitivity`` must be at or below ``sensitivity_threshold``."""
    s = result.get("sensitivity")
    thr = result.get("sensitivity_threshold")
    if s is None or thr is None:
        return {
            "check": "sensitivity",
            "status": "warn",
            "detail": "missing 'sensitivity' or 'sensitivity_threshold'",
        }
    s_v = float(s)
    thr_v = float(thr)
    status = "pass" if s_v <= thr_v else "fail"
    return {
        "check": "sensitivity",
        "status": status,
        "detail": f"sensitivity={s_v:g}, threshold={thr_v:g}",
        "sensitivity": s_v,
        "threshold": thr_v,
    }


_CHECK_DISPATCH: dict[str, Any] = {
    "mass_conservation": lambda r, t: _check_conservation(r, "mass_in", "mass_out", "mass_conservation", t),
    "charge_conservation": lambda r, t: _check_conservation(r, "charge_in", "charge_out", "charge_conservation", t),
    "energy_conservation": lambda r, t: _check_conservation(r, "energy_in", "energy_out", "energy_conservation", t),
    "atom_conservation": lambda r, t: _check_atom_conservation(r, t),
    "unit_consistency": lambda r, t: _check_unit_consistency(r),
    "convergence": lambda r, t: _check_convergence(r),
    "limiting_case": lambda r, t: _check_limiting_case(r),
    "sensitivity": lambda r, t: _check_sensitivity(r),
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    """Run the declared ``checks`` against ``result`` and return aggregate status.

    Parameters
    ----------
    result:
        Result dict. The following keys are recognized:

        * ``checks`` (list[str]) — which validators to run (required). Unknown
          check names are reported as ``warn``.
        * ``tolerance_pct`` (float) — per-check relative tolerance for
          conservation checks. Defaults to 0.5%.
        * per-check fields documented on the individual ``_check_*`` functions.

    Returns
    -------
    dict
        Record with keys ``schema_version``, ``method_id``, ``run_id``,
        ``name``, ``status`` (one of :class:`ValidationStatus`), ``checks_run``,
        ``verification`` (per-check records), ``errors``, ``limitations``.
    """
    declared = list(result.get("checks", []))
    tol_pct = float(result.get("tolerance_pct", 0.5))

    verification: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    limitations: list[str] = []

    has_fail = False
    has_warn = False

    for chk in declared:
        fn = _CHECK_DISPATCH.get(chk)
        if fn is None:
            verification.append(
                {
                    "check": chk,
                    "status": "warn",
                    "detail": f"unknown check '{chk}'",
                }
            )
            has_warn = True
            limitations.append(f"unknown check '{chk}' skipped")
            continue
        rec = fn(result, tol_pct)
        verification.append(rec)
        if rec["status"] == "fail":
            has_fail = True
        elif rec["status"] == "warn":
            has_warn = True

    if not declared:
        # No declared checks -> not_applicable (cannot claim validated).
        status = ValidationStatus.NOT_APPLICABLE
        limitations.append("no checks declared")
    elif has_fail:
        status = ValidationStatus.INVALID
        errors.append(_err("chem.validation.check_failed", "one or more declared checks failed"))
    elif has_warn:
        status = ValidationStatus.PROVISIONAL
        limitations.append("one or more checks produced warnings")
    else:
        status = ValidationStatus.VALIDATED

    return {
        "schema_version": SCHEMA_VERSION,
        "method_id": METHOD_ID,
        "run_id": _new_id(),
        "name": "validate_result",
        "status": status,
        "supports_execution": supports_execution(status),
        "checks_run": len(verification),
        "verification": verification,
        "tolerance_pct": tol_pct,
        "errors": errors,
        "limitations": limitations,
    }


__all__ = [
    "METHOD_ID",
    "SCHEMA_VERSION",
    "ValidationStatus",
    "supports_execution",
    "validate_result",
]
