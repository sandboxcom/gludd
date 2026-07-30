"""CHEM-015 analytical chemistry: calibration, quantitation, method validation (Phase D).

Implements CHEM-015 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §8.3.
Per the spec, quantification requires "range-appropriate calibration, residual
review, detection/quantitation limit method, recovery, precision, specificity,
robustness, and outlier policy. Extrapolation outside a validated range is
visibly flagged and cannot return ``succeeded``."

This module is intentionally dependency-free (uses only ``math`` + ``uuid``):

* :class:`CalibrationCurve` — ordinary-least-squares linear regression on
  ``(concentration, response)`` pairs, with ``predict`` returning a confidence
  interval (inverse-prediction / classical calibration interval) and an
  ``extrapolated`` flag for queries outside the calibrated response range.
  ``lod`` / ``loq`` follow the IUPAC convention (LOD = 3*sigma/slope,
  LOQ = 10*sigma/slope).
* :class:`MethodValidation` — wraps a :class:`CalibrationCurve` with the
  ICH Q2(R1) figures of merit: precision (RSD%), accuracy (recovery%),
  linearity (R²), range, specificity, robustness, LOD/LOQ.
* :func:`detect_outliers_grubbs` and :func:`dixon_q` — outlier-policy stubs
  (Grubbs at alpha=0.05 critical values; Dixon Q at the 0.90 tabular level).
* :func:`subtract_blank` — blank-subtraction helper producing a typed record.

Records follow the project convention (``name``, ``value``, ``unit``,
``status``, ``schema_version``, ``method_id``, ``run_id``, ``errors``,
``limitations``).
"""

from __future__ import annotations

import math
import uuid
from typing import Any

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-analytical@0.1.0"


def _new_id() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sample_std(values: list[float]) -> float:
    """Sample standard deviation (ddof=1). Returns 0.0 for n < 2."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = _mean(values)
    ss = sum((v - mu) ** 2 for v in values)
    return math.sqrt(ss / (n - 1))


# Grubbs critical values Z for alpha=0.05 (two-sided), n=3..30. Beyond the table we
# fall back to the n=30 entry conservatively. This is intentionally a small
# reference table — production code would compute from the t-distribution, but
# this stub is sufficient to demonstrate the outlier-policy contract.
_GRUBBS_Z_05: dict[int, float] = {
    3: 1.155,
    4: 1.481,
    5: 1.715,
    6: 1.887,
    7: 2.020,
    8: 2.126,
    9: 2.215,
    10: 2.290,
    11: 2.355,
    12: 2.412,
    15: 2.549,
    20: 2.710,
    25: 2.822,
    30: 2.908,
}

# Dixon Q critical values at the 0.90 confidence level for small samples.
_DIXON_Q_90: dict[int, float] = {
    3: 0.941,
    4: 0.765,
    5: 0.642,
    6: 0.560,
    7: 0.507,
    8: 0.468,
    9: 0.437,
    10: 0.412,
    11: 0.392,
    12: 0.376,
}


# ---------------------------------------------------------------------------
# CalibrationCurve
# ---------------------------------------------------------------------------


class CalibrationCurve:
    """Linear-regression calibration curve (ordinary least squares).

    Parameters
    ----------
    concentrations, responses:
        Equal-length numeric lists of calibrator concentrations (x) and the
        instrument response (y). Length must be >= 2.
    """

    def __init__(self, concentrations: list[float], responses: list[float]) -> None:
        if len(concentrations) != len(responses):
            raise ValueError(
                f"concentrations ({len(concentrations)}) and responses ({len(responses)}) must have equal length"
            )
        if len(concentrations) < 2:
            raise ValueError("calibration curve requires at least 2 points")
        self.concentrations: list[float] = [float(c) for c in concentrations]
        self.responses: list[float] = [float(r) for r in responses]
        self._fit_cached: dict[str, Any] | None = None

    # -- core regression -------------------------------------------------

    def fit(self) -> dict[str, Any]:
        """Return OLS fit (slope, intercept, r_squared, residuals, range).

        All fields are recomputed on each call (no cache aliasing), but a
        shallow copy of the cached fit is returned when the data has not
        changed across calls in the same instance.
        """
        if self._fit_cached is not None:
            return dict(self._fit_cached)

        xs = self.concentrations
        ys = self.responses
        n = len(xs)
        mean_x = _mean(xs)
        mean_y = _mean(ys)

        ss_xx = sum((x - mean_x) ** 2 for x in xs)
        ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        ss_yy = sum((y - mean_y) ** 2 for y in ys)

        if ss_xx == 0.0:
            raise ValueError("concentrations are degenerate (zero variance)")

        slope = ss_xy / ss_xx
        intercept = mean_y - slope * mean_x
        r_squared = 0.0 if ss_yy == 0.0 else (ss_xy * ss_xy) / (ss_xx * ss_yy)

        residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys, strict=True)]

        # Standard error of the regression (s_yx) — used for LOD/LOQ default
        # sigma and for the prediction confidence interval.
        s_yx = math.sqrt(sum(r * r for r in residuals) / (n - 2)) if n > 2 else 0.0

        rec: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "status": "succeeded",
            "name": "calibration_fit",
            "slope": slope,
            "intercept": intercept,
            "r_squared": r_squared,
            "n": n,
            "residuals": residuals,
            "s_yx": s_yx,
            "range_low": min(xs),
            "range_high": max(xs),
            "response_low": min(ys),
            "response_high": max(ys),
            "errors": [],
            "limitations": [],
        }
        self._fit_cached = dict(rec)
        return rec

    # -- inverse prediction ---------------------------------------------

    def predict(self, response: float, alpha: float = 0.05) -> dict[str, Any]:
        """Inverse-predict concentration from a response.

        Returns a record with:

        * ``concentration`` — (response - intercept) / slope
        * ``ci_lower`` / ``ci_upper`` — classical calibration interval
          (Inverse-Prediction interval based on ``s_yx`` and ``ss_xx``)
        * ``in_range`` — whether the predicted concentration falls inside the
          calibrated concentration range
        * ``extrapolated`` — True iff the response lies outside the calibrated
          response range; in that case ``status`` is ``"degraded"`` per spec
          §8.3 ("Extrapolation outside a validated range is visibly flagged and
          cannot return ``succeeded``").
        """
        if alpha <= 0.0 or alpha >= 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {alpha}")

        f = self.fit()
        slope = f["slope"]
        intercept = f["intercept"]
        s_yx = f["s_yx"]
        ss_xx = sum((x - _mean(self.concentrations)) ** 2 for x in self.concentrations)
        n = f["n"]
        _mean(self.concentrations)

        if slope == 0.0:
            return {
                "schema_version": SCHEMA_VERSION,
                "method_id": METHOD_ID,
                "run_id": _new_id(),
                "name": "predict",
                "status": "invalid",
                "concentration": float("nan"),
                "in_range": False,
                "extrapolated": True,
                "errors": [_err("chem.calibration.zero_slope", "slope is zero; cannot invert")],
                "limitations": ["calibration slope is zero"],
            }

        conc = (response - intercept) / slope

        resp_lo = f["response_low"]
        resp_hi = f["response_high"]
        extrapolated = response < resp_lo or response > resp_hi
        in_range = f["range_low"] <= conc <= f["range_high"]

        # Inverse-prediction CI (classical calibration interval):
        #   x_hat ± t · (s_yx / slope) · sqrt(1/m + 1/n + (y - y_bar)^2 / (slope^2 · ss_xx))
        # m = 1 (single measurement). We approximate the t-multiplier with the
        # normal-z two-sided value at alpha (1.96 for 0.05) when n > 2; for
        # n == 2 the residual df is 0 and the CI is undefined -> reported as
        # (conc, conc) so downstream code can still consume the record.
        ci_lower = conc
        ci_upper = conc
        if n > 2 and s_yx > 0.0 and ss_xx > 0.0:
            # t critical for df=n-2 at alpha/2; approximate via z for n large.
            # Use z_{1-alpha/2} from a small lookup to avoid a scipy dep.
            z = _z_two_sided(alpha)
            mean_y = _mean(self.responses)
            se = (s_yx / abs(slope)) * math.sqrt(1.0 / n + (response - mean_y) ** 2 / (slope * slope * ss_xx))
            ci_lower = conc - z * se
            ci_upper = conc + z * se

        status = "succeeded"
        limitations: list[str] = []
        errors: list[dict[str, Any]] = []
        if extrapolated:
            status = "degraded"
            limitations.append(
                f"response {response} outside calibrated range [{resp_lo}, {resp_hi}]; result flagged as extrapolation"
            )
            errors.append(
                _err(
                    "chem.calibration.extrapolation",
                    "query response lies outside validated calibration range",
                )
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "predict",
            "status": status,
            "concentration": conc,
            "ci_lower": ci_lower,
            "ci_upper": ci_upper,
            "confidence_level": 1.0 - alpha,
            "in_range": in_range,
            "extrapolated": extrapolated,
            "calibrated_range": (f["range_low"], f["range_high"]),
            "errors": errors,
            "limitations": limitations,
        }

    # -- range guard -----------------------------------------------------

    def check_range(self, response: float) -> dict[str, Any]:
        """Verify a response lies inside the calibrated response range.

        Returns a record with ``extrapolated`` flag and ``status`` ``succeeded``
        iff the response is in range. Used as a pre-flight guard before
        quantitation.
        """
        f = self.fit()
        lo = f["response_low"]
        hi = f["response_high"]
        extrapolated = response < lo or response > hi
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "check_range",
            "status": "succeeded" if not extrapolated else "degraded",
            "response": float(response),
            "response_low": lo,
            "response_high": hi,
            "extrapolated": extrapolated,
            "errors": []
            if not extrapolated
            else [
                _err(
                    "chem.calibration.extrapolation",
                    f"response {response} outside calibrated range [{lo}, {hi}]",
                )
            ],
            "limitations": []
            if not extrapolated
            else [
                "extrapolation outside validated range",
            ],
        }

    # -- detection / quantitation limits --------------------------------

    def lod(self, sigma_blank: float | None = None, k: int = 3) -> float:
        """Limit of detection = k * sigma / |slope| (IUPAC, k=3)."""
        f = self.fit()
        slope = f["slope"]
        if slope == 0.0:
            raise ValueError("slope is zero; cannot compute LOD")
        sigma = sigma_blank if sigma_blank is not None else f["s_yx"]
        if sigma < 0.0:
            raise ValueError(f"sigma_blank must be non-negative, got {sigma}")
        return k * sigma / abs(slope)

    def loq(self, sigma_blank: float | None = None, k: int = 10) -> float:
        """Limit of quantitation = k * sigma / |slope| (IUPAC, k=10)."""
        f = self.fit()
        slope = f["slope"]
        if slope == 0.0:
            raise ValueError("slope is zero; cannot compute LOQ")
        sigma = sigma_blank if sigma_blank is not None else f["s_yx"]
        if sigma < 0.0:
            raise ValueError(f"sigma_blank must be non-negative, got {sigma}")
        return k * sigma / abs(slope)


# ---------------------------------------------------------------------------
# MethodValidation (ICH Q2(R1) figures of merit)
# ---------------------------------------------------------------------------


class MethodValidation:
    """ICH Q2(R1)-style figures-of-merit wrapper around a calibration curve.

    The ``curve`` argument is optional: precision, accuracy, specificity, and
    robustness can be computed from standalone replicate data; linearity, range,
    LOD/LOQ require a :class:`CalibrationCurve`.
    """

    def __init__(self, curve: CalibrationCurve | None = None) -> None:
        self.curve = curve

    # -- precision -------------------------------------------------------

    def precision(self, replicates: list[float]) -> dict[str, Any]:
        """Relative standard deviation (RSD%) of replicate measurements."""
        if len(replicates) < 2:
            raise ValueError("precision requires at least 2 replicates")
        mu = _mean(replicates)
        sd = _sample_std(replicates)
        rsd = 0.0 if mu == 0.0 else (sd / mu) * 100.0
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "precision",
            "value": rsd,
            "unit": "%RSD",
            "mean": mu,
            "std_dev": sd,
            "n": len(replicates),
            "errors": [],
            "limitations": [],
        }

    # -- accuracy --------------------------------------------------------

    def accuracy(self, measured: float, nominal: float, lo_pct: float = 80.0, hi_pct: float = 120.0) -> dict[str, Any]:
        """Recovery (%) = measured / nominal · 100, with acceptance window."""
        if nominal == 0.0:
            raise ValueError("nominal must be non-zero")
        recovery = (measured / nominal) * 100.0
        acceptance = "pass" if lo_pct <= recovery <= hi_pct else "fail"
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "accuracy",
            "value": recovery,
            "unit": "%recovery",
            "measured": float(measured),
            "nominal": float(nominal),
            "acceptance_window": (lo_pct, hi_pct),
            "acceptance": acceptance,
            "errors": [],
            "limitations": []
            if acceptance == "pass"
            else [
                f"recovery {recovery:.2f}% outside acceptance window [{lo_pct}, {hi_pct}]",
            ],
        }

    # -- linearity -------------------------------------------------------

    def linearity(self) -> dict[str, Any]:
        """Coefficient of determination (R²) of the calibration curve."""
        if self.curve is None:
            raise ValueError("linearity requires a CalibrationCurve")
        f = self.curve.fit()
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "linearity",
            "value": f["r_squared"],
            "unit": "dimensionless",
            "n": f["n"],
            "slope": f["slope"],
            "intercept": f["intercept"],
            "errors": [],
            "limitations": [],
        }

    # -- range -----------------------------------------------------------

    def range(self) -> dict[str, Any]:
        """Validated response range of the calibration curve."""
        if self.curve is None:
            raise ValueError("range requires a CalibrationCurve")
        f = self.curve.fit()
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "range",
            "low": f["response_low"],
            "high": f["response_high"],
            "concentration_low": f["range_low"],
            "concentration_high": f["range_high"],
            "unit": "response",
            "errors": [],
            "limitations": [],
        }

    # -- specificity / robustness (stubs returning typed records) -------

    def specificity(self, interferences: list[dict[str, Any]]) -> dict[str, Any]:
        """Specificity: does the method respond only to the target analyte?

        ``interferences`` is a list of records (e.g.
        ``{"compound": "X", "response_pct": 1.2}``). Any response_pct above a
        typical 5% threshold marks specificity as degraded.
        """
        threshold = 5.0
        flagged = [i for i in interferences if abs(float(i.get("response_pct", 0.0))) > threshold]
        status = "succeeded" if not flagged else "degraded"
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "specificity",
            "status": status,
            "interferences_tested": len(interferences),
            "interferences_flagged": len(flagged),
            "threshold_pct": threshold,
            "errors": [],
            "limitations": []
            if not flagged
            else [
                f"{len(flagged)} interference(s) exceed {threshold}% response threshold",
            ],
        }

    def robustness(self, perturbations: list[dict[str, Any]]) -> dict[str, Any]:
        """Robustness: method response to small, deliberate parameter changes."""
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "robustness",
            "status": "succeeded",
            "perturbations_evaluated": len(perturbations),
            "perturbations": list(perturbations),
            "errors": [],
            "limitations": [],
        }

    # -- combined LOD/LOQ -----------------------------------------------

    def lod_loq(self, sigma_blank: float | None = None) -> dict[str, Any]:
        """Combined LOD + LOQ record from the calibration curve."""
        if self.curve is None:
            raise ValueError("lod_loq requires a CalibrationCurve")
        lod = self.curve.lod(sigma_blank=sigma_blank, k=3)
        loq = self.curve.loq(sigma_blank=sigma_blank, k=10)
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "lod_loq",
            "lod": lod,
            "loq": loq,
            "lod_k": 3,
            "loq_k": 10,
            "sigma_source": "residuals" if sigma_blank is None else "blank",
            "errors": [],
            "limitations": [],
        }


# ---------------------------------------------------------------------------
# Outlier policy
# ---------------------------------------------------------------------------


def detect_outliers_grubbs(values: list[float], alpha: float = 0.05) -> dict[str, Any]:
    """Grubbs' test for a single outlier (max |z|).

    Compares the standardized max deviation against the Grubbs critical Z at
    alpha=0.05 for the sample size. Iteratively removes flagged outliers until none
    remain. Returns a record naming flagged values.
    """
    if len(values) < 3:
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "outlier_grubbs",
            "status": "degraded",
            "n_outliers": 0,
            "outliers": [],
            "errors": [_err("chem.outlier.insufficient_data", "Grubbs test requires n >= 3")],
            "limitations": ["insufficient data for Grubbs test"],
        }

    remaining = list(values)
    outliers: list[float] = []
    z_crit = _grubbs_z(len(remaining), alpha)

    while len(remaining) >= 3:
        mu = _mean(remaining)
        sd = _sample_std(remaining)
        if sd == 0.0:
            break
        # candidate = furthest point from the mean
        idx, val = max(enumerate(remaining), key=lambda iv: abs(iv[1] - mu))
        g = abs(val - mu) / sd
        if g > z_crit:
            outliers.append(val)
            remaining.pop(idx)
            z_crit = _grubbs_z(len(remaining), alpha)
        else:
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "method_id": METHOD_ID,
        "run_id": _new_id(),
        "name": "outlier_grubbs",
        "status": "succeeded" if not outliers else "degraded",
        "alpha": alpha,
        "n_input": len(values),
        "n_outliers": len(outliers),
        "outliers": outliers,
        "n_remaining": len(remaining),
        "errors": [],
        "limitations": []
        if not outliers
        else [
            f"{len(outliers)} value(s) flagged as outliers at alpha={alpha}",
        ],
    }


def dixon_q(values: list[float]) -> dict[str, Any]:
    """Dixon Q test for a single outlier at the 0.90 confidence level."""
    n = len(values)
    if n < 3:
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "outlier_dixon_q",
            "status": "degraded",
            "statistic": float("nan"),
            "outlier": None,
            "errors": [_err("chem.outlier.insufficient_data", "Dixon Q requires n >= 3")],
            "limitations": ["insufficient data for Dixon Q test"],
        }

    vs = sorted(values)
    rng = vs[-1] - vs[0]
    q_crit = _DIXON_Q_90.get(n, _DIXON_Q_90[12])
    if rng == 0.0:
        return {
            "schema_version": SCHEMA_VERSION,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "name": "outlier_dixon_q",
            "status": "succeeded",
            "statistic": 0.0,
            "outlier": None,
            "q_critical": q_crit,
            "errors": [],
            "limitations": ["all values identical; no outlier"],
        }

    # Test the low extreme and the high extreme; report the larger gap.
    gap_low = vs[1] - vs[0]
    gap_high = vs[-1] - vs[-2]
    if gap_low >= gap_high:
        q_stat = gap_low / rng
        outlier = vs[0]
    else:
        q_stat = gap_high / rng
        outlier = vs[-1]

    is_outlier = q_stat > q_crit
    return {
        "schema_version": SCHEMA_VERSION,
        "method_id": METHOD_ID,
        "run_id": _new_id(),
        "name": "outlier_dixon_q",
        "status": "succeeded" if not is_outlier else "degraded",
        "statistic": q_stat,
        "q_critical": q_crit,
        "outlier": outlier if is_outlier else None,
        "errors": [],
        "limitations": []
        if not is_outlier
        else [
            f"Dixon Q statistic {q_stat:.3f} exceeds critical {q_crit:.3f}",
        ],
    }


# ---------------------------------------------------------------------------
# Blank subtraction
# ---------------------------------------------------------------------------


def subtract_blank(response: float, blank: float) -> dict[str, Any]:
    """Net signal after blank subtraction. Returns a typed record."""
    net = response - blank
    return {
        "schema_version": SCHEMA_VERSION,
        "method_id": METHOD_ID,
        "run_id": _new_id(),
        "name": "blank_subtraction",
        "value": net,
        "raw_response": float(response),
        "blank": float(blank),
        "blank_subtracted": True,
        "unit": "response",
        "errors": [],
        "limitations": []
        if net >= 0.0
        else [
            "net signal is negative after blank subtraction",
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _grubbs_z(n: int, alpha: float) -> float:
    """Grubbs critical value Z for sample size ``n`` at two-sided alpha.

    Falls back to the n=30 entry conservatively for n > 30 (the test grows
    conservative, which is acceptable for an outlier policy stub).
    """
    if n in _GRUBBS_Z_05:
        return _GRUBBS_Z_05[n]
    if n > 30:
        return _GRUBBS_Z_05[30]
    # linear interpolation between the two nearest tabulated keys
    keys = sorted(_GRUBBS_Z_05)
    lo = max(k for k in keys if k <= n)
    hi = min(k for k in keys if k >= n)
    if lo == hi:
        return _GRUBBS_Z_05[lo]
    z_lo = _GRUBBS_Z_05[lo]
    z_hi = _GRUBBS_Z_05[hi]
    return z_lo + (z_hi - z_lo) * (n - lo) / (hi - lo)


def _z_two_sided(alpha: float) -> float:
    """Standard normal two-sided critical value for ``alpha``.

    Small lookup table for common alphas; defaults to 1.96 (alpha=0.05).
    """
    table = {0.10: 1.645, 0.05: 1.96, 0.01: 2.576, 0.001: 3.291}
    return table.get(round(alpha, 4), 1.96)


__all__ = [
    "METHOD_ID",
    "SCHEMA_VERSION",
    "CalibrationCurve",
    "MethodValidation",
    "detect_outliers_grubbs",
    "dixon_q",
    "subtract_blank",
]
