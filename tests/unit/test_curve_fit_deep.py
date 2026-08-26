"""Deep curve-fitting tests: linear, polynomial, exponential, logarithmic regression.

Covers OLS linear regression, nth-degree polynomial fits, exponential (log-space
OLS), logarithmic (semi-log-space OLS), R-squared boundary cases, residual
analysis (normality, standardised residuals, RSS), and inverse-prediction
confidence intervals.  All regressions are implemented from first principles
(no numpy/scipy dependency) so the tests serve as the reference specification.

Reference: ``src/general_ludd/chemistry/analytical.py`` (``CalibrationCurve``).
"""

from __future__ import annotations

import math

from general_ludd.chemistry import analytical

# ---------------------------------------------------------------------------
# Pure-function regression primitives (spec — extracted to src/ later)
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _ss_xx(xs: list[float]) -> float:
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs)


def _ss_xy(xs: list[float], ys: list[float]) -> float:
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))


def _ss_yy(ys: list[float]) -> float:
    m = _mean(ys)
    return sum((y - m) ** 2 for y in ys)


def linear_fit(xs: list[float], ys: list[float]) -> dict:
    """OLS linear regression: y = a + b·x.  Returns {slope,intercept,r_squared,residuals}."""
    n = len(xs)
    if n < 2:
        raise ValueError("need at least 2 points")
    ssx = _ss_xx(xs)
    if ssx == 0.0:
        raise ValueError("degenerate x (zero variance)")
    ssy = _ss_yy(ys)
    sxy = _ss_xy(xs, ys)
    slope = sxy / ssx
    intercept = _mean(ys) - slope * _mean(xs)
    r_squared = (sxy * sxy) / (ssx * ssy) if ssy != 0.0 else 0.0
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys, strict=True)]
    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "residuals": residuals,
        "n": n,
    }


def polynomial_fit(xs: list[float], ys: list[float], degree: int) -> dict:
    """Nth-degree polynomial fit via normal-equation OLS.

    Returns {coefficients: [c0,...,c_degree] (lowest-order first), r_squared, residuals}.
    Uses the Vandermonde matrix X_{i,j} = x_i**j and solves (X^T·X)·c = X^T·y
    via Gaussian elimination (no numpy dependency).
    """
    n = len(xs)
    if n <= degree:
        raise ValueError(f"need >{degree} points for degree {degree} fit")
    m_cols = degree + 1
    # Build X^T·X and X^T·y
    ata = [[0.0] * m_cols for _ in range(m_cols)]
    aty = [0.0] * m_cols
    for i in range(n):
        x_pow = 1.0
        row_powers = []
        for _j in range(m_cols):
            row_powers.append(x_pow)
            x_pow *= xs[i]
        for j in range(m_cols):
            aty[j] += row_powers[j] * ys[i]
            for k in range(m_cols):
                ata[j][k] += row_powers[j] * row_powers[k]
    # Gaussian elimination with partial pivoting
    coeffs = _solve_symmetric(ata, aty)
    # Compute R-squared
    mean_y = _mean(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = 0.0
    residuals = []
    for i in range(n):
        y_pred = 0.0
        x_pow = 1.0
        for c in coeffs:
            y_pred += c * x_pow
            x_pow *= xs[i]
        r = ys[i] - y_pred
        residuals.append(r)
        ss_res += r * r
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    return {
        "coefficients": coeffs,
        "r_squared": r_squared,
        "residuals": residuals,
        "n": n,
        "degree": degree,
    }


def _solve_symmetric(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for symmetric positive-definite system A·x = b."""
    n = len(b)
    aug = [[*row[:], b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[pivot][col]):
                pivot = row
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        if abs(aug[col][col]) < 1e-14:
            raise ValueError("singular matrix")
        for row in range(col + 1, n):
            factor = aug[row][col] / aug[col][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = aug[i][n]
        for j in range(i + 1, n):
            s -= aug[i][j] * x[j]
        x[i] = s / aug[i][i]
    return x


def exponential_fit(xs: list[float], ys: list[float]) -> dict:
    """Exponential fit via log-space OLS: y = a·e^(b·x).

    Linearises ln(y)=ln(a)+b·x, solves OLS, then exponentiates intercept.
    All y-values must be positive.
    """
    if any(y <= 0.0 for y in ys):
        raise ValueError("exponential fit requires all y > 0")
    log_ys = [math.log(y) for y in ys]
    lin = linear_fit(xs, log_ys)
    a = math.exp(lin["intercept"])
    b = lin["slope"]
    # R-squared in original space
    mean_y = _mean(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - a * math.exp(b * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    residuals = [y - a * math.exp(b * x) for x, y in zip(xs, ys, strict=True)]
    return {"a": a, "b": b, "r_squared": r_squared, "residuals": residuals, "n": len(xs)}


def logarithmic_fit(xs: list[float], ys: list[float]) -> dict:
    """Logarithmic fit via semi-log OLS: y = a + b·ln(x).

    All x-values must be positive.
    """
    if any(x <= 0.0 for x in xs):
        raise ValueError("logarithmic fit requires all x > 0")
    log_xs = [math.log(x) for x in xs]
    lin = linear_fit(log_xs, ys)
    a = lin["intercept"]  # = a in y=a+b·ln(x)
    b = lin["slope"]  # = b in y=a+b·ln(x)
    mean_y = _mean(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (a + b * math.log(x))) ** 2 for x, y in zip(xs, ys, strict=True))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    residuals = [y - (a + b * math.log(x)) for x, y in zip(xs, ys, strict=True)]
    return {"a": a, "b": b, "r_squared": r_squared, "residuals": residuals, "n": len(xs)}


# ---------------------------------------------------------------------------
# Residual analysis helpers
# ---------------------------------------------------------------------------


def residual_sum_of_squares(residuals: list[float]) -> float:
    return sum(r * r for r in residuals)


def standardised_residuals(residuals: list[float]) -> list[float]:
    """Studentised-style residuals: r_i / (s * sqrt(1 - 1/n))."""
    n = len(residuals)
    if n < 3:
        return [0.0] * n
    rss = residual_sum_of_squares(residuals)
    sigma = math.sqrt(rss / (n - 2))
    if sigma == 0.0:
        return [0.0] * n
    div = sigma * math.sqrt(1.0 - 1.0 / n)
    return [r / div for r in residuals]


def rss(residuals: list[float]) -> float:
    return residual_sum_of_squares(residuals)


def tss(ys: list[float]) -> float:
    m = _mean(ys)
    return sum((y - m) ** 2 for y in ys)


def adjusted_r_squared(r2: float, n: int, p: int) -> float:
    """Adjusted R-squared: 1 - (1-R²)·(n-1)/(n-p-1)."""
    if n <= p + 1:
        return float("nan")
    return 1.0 - (1.0 - r2) * (n - 1) / (n - p - 1)


def durbin_watson(residuals: list[float]) -> float:
    """Durbin-Watson statistic for serial autocorrelation."""
    n = len(residuals)
    if n < 3:
        return float("nan")
    num = sum((residuals[i] - residuals[i - 1]) ** 2 for i in range(1, n))
    den = sum(r * r for r in residuals)
    return num / den if den > 0.0 else float("nan")


# ---------------------------------------------------------------------------
# Linear Regression — deep tests
# ---------------------------------------------------------------------------


class TestLinearRegression:
    def test_linear_perfect_fit_exact(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [5.0, 7.0, 9.0, 11.0, 13.0]  # y = 2x + 5
        r = linear_fit(xs, ys)
        assert math.isclose(r["slope"], 2.0, rel_tol=1e-9)
        assert math.isclose(r["intercept"], 5.0, rel_tol=1e-9)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)
        assert all(abs(v) < 1e-9 for v in r["residuals"])

    def test_linear_noisy_high_r2(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        ys = [3.01, 4.98, 7.02, 8.99, 11.03, 13.02, 15.01, 16.97]  # ≈ 2x+1
        r = linear_fit(xs, ys)
        assert 0.999 < r["r_squared"] < 1.0
        assert r["n"] == 8

    def test_linear_flat_line_r2_zero(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [10.0, 10.0, 10.0, 10.0]  # all y identical → no variance
        r = linear_fit(xs, ys)
        assert math.isclose(r["slope"], 0.0, abs_tol=1e-9)
        assert math.isclose(r["r_squared"], 0.0, abs_tol=1e-12)

    def test_linear_negative_slope(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [10.0, 8.0, 6.0, 4.0, 2.0]  # y = -2x + 12
        r = linear_fit(xs, ys)
        assert math.isclose(r["slope"], -2.0, rel_tol=1e-9)
        assert math.isclose(r["intercept"], 12.0, rel_tol=1e-9)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_linear_degenerate_x_raises(self):
        xs = [3.0, 3.0, 3.0]
        ys = [1.0, 2.0, 3.0]
        try:
            linear_fit(xs, ys)
        except ValueError as e:
            assert "zero variance" in str(e) or "degenerate" in str(e)
        else:
            raise AssertionError("expected ValueError for degenerate x")

    def test_linear_two_points_exact(self):
        xs = [2.0, 4.0]
        ys = [3.0, 7.0]  # slope=2, intercept=-1
        r = linear_fit(xs, ys)
        assert math.isclose(r["slope"], 2.0)
        assert math.isclose(r["intercept"], -1.0)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_linear_insufficient_points_raises(self):
        try:
            linear_fit([1.0], [1.0])
        except ValueError as e:
            assert "2 points" in str(e)
        else:
            raise AssertionError("expected ValueError for n<2")

    def test_linear_residuals_sum_to_zero(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.1, 4.3, 6.8, 8.2, 10.5]
        r = linear_fit(xs, ys)
        assert math.isclose(sum(r["residuals"]), 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Polynomial Regression — deep tests
# ---------------------------------------------------------------------------


class TestPolynomialRegression:
    def test_quadratic_perfect_fit(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [1.0, 2.0, 7.0, 16.0, 29.0]  # y = 2x² - x + 1
        r = polynomial_fit(xs, ys, 2)
        assert len(r["coefficients"]) == 3
        c0, c1, c2 = r["coefficients"]
        assert math.isclose(c0, 1.0, rel_tol=1e-8)
        assert math.isclose(c1, -1.0, rel_tol=1e-8)
        assert math.isclose(c2, 2.0, rel_tol=1e-8)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_cubic_fit_noisy(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        ys = [v**3 + 0.1 * v for v in xs]
        r = polynomial_fit(xs, ys, 3)
        assert len(r["coefficients"]) == 4
        assert r["r_squared"] > 0.9999
        assert math.isclose(r["coefficients"][3], 1.0, rel_tol=1e-3)

    def test_quadratic_insufficient_points_raises(self):
        try:
            polynomial_fit([0.0, 1.0], [0.0, 1.0], 2)
        except ValueError as e:
            assert ">" in str(e)
        else:
            raise AssertionError("expected ValueError for n<=degree")

    def test_quadratic_residuals(self):
        xs = [-2.0, -1.0, 0.0, 1.0, 2.0]
        ys = [4.0, 1.0, 0.0, 1.0, 4.0]  # y = x² (perfect)
        r = polynomial_fit(xs, ys, 2)
        assert all(abs(v) < 1e-8 for v in r["residuals"])


# ---------------------------------------------------------------------------
# Exponential Regression — deep tests
# ---------------------------------------------------------------------------


class TestExponentialRegression:
    def test_exponential_perfect_growth(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [3.0, 6.0, 12.0, 24.0, 48.0]  # y = 3·2^x → a=3, b=ln(2)
        r = exponential_fit(xs, ys)
        assert math.isclose(r["a"], 3.0, rel_tol=1e-8)
        assert math.isclose(r["b"], math.log(2.0), rel_tol=1e-8)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_exponential_decay(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [100.0, 50.0, 25.0, 12.5, 6.25]  # y = 100·0.5^x → b=ln(0.5)
        r = exponential_fit(xs, ys)
        assert math.isclose(r["a"], 100.0, rel_tol=1e-8)
        assert math.isclose(r["b"], math.log(0.5), rel_tol=1e-8)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_exponential_noisy_r2(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [math.exp(v * 0.5) + (0.03 if v % 2 == 0 else -0.02) for v in xs]
        r = exponential_fit(xs, ys)
        assert r["r_squared"] > 0.99

    def test_exponential_nonpositive_y_raises(self):
        try:
            exponential_fit([0.0, 1.0], [3.0, -0.1])
        except ValueError as e:
            assert "positive" in str(e).lower() or "> 0" in str(e)
        else:
            raise AssertionError("expected ValueError for non-positive y")


# ---------------------------------------------------------------------------
# Logarithmic Regression — deep tests
# ---------------------------------------------------------------------------


class TestLogarithmicRegression:
    def test_logarithmic_perfect_fit(self):
        xs = [1.0, 2.0, 4.0, 8.0]
        ys = [0.0, 1.0, 2.0, 3.0]  # y = log2(x) → y = ln(x)/ln(2)
        r = logarithmic_fit(xs, ys)
        assert math.isclose(r["b"], 1.0 / math.log(2.0), rel_tol=1e-8)
        assert math.isclose(r["a"], 0.0, abs_tol=1e-9)

    def test_logarithmic_with_intercept(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [5.0 + 10.0 * math.log(x) for x in xs]
        r = logarithmic_fit(xs, ys)
        assert math.isclose(r["a"], 5.0, rel_tol=1e-8)
        assert math.isclose(r["b"], 10.0, rel_tol=1e-8)

    def test_logarithmic_nonpositive_x_raises(self):
        try:
            logarithmic_fit([1.0, -0.5], [0.0, 1.0])
        except ValueError as e:
            assert "positive" in str(e).lower() or "> 0" in str(e)
        else:
            raise AssertionError("expected ValueError for non-positive x")


# ---------------------------------------------------------------------------
# R-Squared — boundary cases and adjusted R²
# ---------------------------------------------------------------------------


class TestRSquared:
    def test_r_squared_perfect_is_one(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [7.0, 9.0, 11.0, 13.0]
        r = linear_fit(xs, ys)
        assert math.isclose(r["r_squared"], 1.0, abs_tol=1e-12)

    def test_r_squared_zero_variance_y(self):
        xs = [1.0, 2.0, 3.0]
        ys = [5.0, 5.0, 5.0]
        r = linear_fit(xs, ys)
        assert math.isclose(r["r_squared"], 0.0, abs_tol=1e-12)

    def test_adjusted_r_squared_penalty(self):
        n, p = 10, 2
        r2 = 0.85
        adj = adjusted_r_squared(r2, n, p)
        assert adj < r2
        assert adj > 0.0

    def test_adjusted_r_squared_insufficient_df(self):
        adj = adjusted_r_squared(0.9, 3, 2)  # n=3, p=2 → df ≤ 0
        assert math.isnan(adj)

    def test_quadratic_r2_exceeds_linear_r2(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0]
        ys = [x**2 + 0.5 for x in xs]
        r_lin = linear_fit(xs, ys)
        r_quad = polynomial_fit(xs, ys, 2)
        assert r_quad["r_squared"] > r_lin["r_squared"]


# ---------------------------------------------------------------------------
# Residual Analysis — deep tests
# ---------------------------------------------------------------------------


class TestResidualAnalysis:
    def test_rss_perfect_fit_zero(self):
        xs = [1.0, 2.0, 3.0]
        ys = [2.0, 4.0, 6.0]
        r = linear_fit(xs, ys)
        assert math.isclose(rss(r["residuals"]), 0.0, abs_tol=1e-12)

    def test_rss_noisy_positive(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.1, 4.3, 6.1, 7.9, 10.2]
        r = linear_fit(xs, ys)
        s = rss(r["residuals"])
        assert s > 0.0

    def test_tss_equals_rss_plus_explained(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.2, 4.0, 6.1, 8.3, 9.7]
        r = linear_fit(xs, ys)
        s_tot = tss(ys)
        s_res = rss(r["residuals"])
        assert math.isclose(s_tot, s_res + r["r_squared"] * s_tot, rel_tol=1e-9)

    def test_standardised_residuals_mean_approx_zero(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        ys = [2.1, 4.3, 6.0, 8.2, 10.4, 12.7, 14.5]
        r = linear_fit(xs, ys)
        sr = standardised_residuals(r["residuals"])
        assert abs(_mean(sr)) < 1e-6

    def test_durbin_watson_near_two_no_autocorrelation(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        ys = [2.1, 4.0, 6.2, 7.8, 10.3, 12.1, 14.4, 16.3]
        r = linear_fit(xs, ys)
        dw = durbin_watson(r["residuals"])
        assert 0.5 < dw < 3.5  # no strong autocorrelation for random-ish noise

    def test_residual_sum_near_zero_for_ols(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.3, 4.6, 6.9, 9.2, 11.5]
        r = linear_fit(xs, ys)
        assert abs(sum(r["residuals"])) < 1e-9  # OLS residuals always sum to ~0


# ---------------------------------------------------------------------------
# CalibrationCurve — integration with existing analytical module
# ---------------------------------------------------------------------------


class TestCalibrationCurveDeep:
    def test_fit_slope_intercept_r_squared(self):
        concs = [0.0, 1.0, 2.0, 3.0, 4.0]
        resps = [1.0, 3.0, 5.0, 7.0, 9.0]
        curve = analytical.CalibrationCurve(concs, resps)
        rec = curve.fit()
        assert math.isclose(rec["slope"], 2.0, rel_tol=1e-9)
        assert math.isclose(rec["intercept"], 1.0, rel_tol=1e-9)
        assert math.isclose(rec["r_squared"], 1.0, abs_tol=1e-12)

    def test_predict_in_range_no_extrapolation(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
        rec = curve.predict(15.0)
        assert rec["status"] == "succeeded"
        assert not rec["extrapolated"]
        assert math.isclose(rec["concentration"], 1.5, rel_tol=1e-9)

    def test_predict_extrapolated_marks_degraded(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
        rec = curve.predict(25.0)
        assert rec["extrapolated"]
        assert rec["status"] == "degraded"

    def test_lod_returns_positive(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0, 3.0, 4.0], [0.1, 10.1, 20.0, 30.2, 40.1])
        lod_val = curve.lod()
        assert lod_val > 0.0

    def test_loq_returns_positive(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0, 3.0, 4.0], [0.1, 10.1, 20.0, 30.2, 40.1])
        loq_val = curve.loq()
        assert loq_val > 0.0

    def test_check_range_in_range(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
        rec = curve.check_range(10.0)
        assert rec["status"] == "succeeded"
        assert not rec["extrapolated"]

    def test_check_range_extrapolated(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
        rec = curve.check_range(30.0)
        assert rec["status"] == "degraded"
        assert rec["extrapolated"]

    def test_fit_cache_returns_same_result(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0], [0.0, 10.0, 20.0])
        r1 = curve.fit()
        r2 = curve.fit()
        assert r1 is not r2  # shallow copy, not the same dict
        assert r1["slope"] == r2["slope"]

    def test_s_yx_for_noisy_data_positive(self):
        curve = analytical.CalibrationCurve([0.0, 1.0, 2.0, 3.0, 4.0], [0.1, 9.9, 20.2, 30.0, 40.1])
        rec = curve.fit()
        assert rec["s_yx"] > 0.0


# ---------------------------------------------------------------------------
# Cross-fit comparison tests
# ---------------------------------------------------------------------------


class TestCrossFitComparison:
    """Tests comparing linear, polynomial, exponential, and logarithmic fits on the same data."""

    def test_exponential_data_best_fit_by_exponential(self):
        xs = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        ys = [2.0 * math.exp(0.8 * x) for x in xs]
        r_lin = linear_fit(xs, ys)
        r_quad = polynomial_fit(xs, ys, 2)
        r_exp = exponential_fit(xs, ys)
        assert r_exp["r_squared"] > r_lin["r_squared"]
        assert r_quad["r_squared"] > r_lin["r_squared"]
        assert math.isclose(r_exp["r_squared"], 1.0, abs_tol=1e-10)

    def test_polynomial_data_best_fit_by_polynomial(self):
        xs = [-1.0, 0.0, 1.0, 2.0, 3.0]
        ys = [x**2 + 0.5 * x + 3.0 for x in xs]
        r_lin = linear_fit(xs, ys)
        r_quad = polynomial_fit(xs, ys, 2)
        assert r_quad["r_squared"] > r_lin["r_squared"]
        assert math.isclose(r_quad["r_squared"], 1.0, abs_tol=1e-10)

    def test_logarithmic_data_best_fit_by_logarithmic(self):
        xs = [1.0, 10.0, 100.0, 1000.0]
        ys = [10.0 + 2.0 * math.log(x) for x in xs]
        r_lin = linear_fit(xs, ys)
        r_log = logarithmic_fit(xs, ys)
        assert r_log["r_squared"] > r_lin["r_squared"]
        assert math.isclose(r_log["r_squared"], 1.0, abs_tol=1e-10)

    def test_linear_data_linear_r2_not_worse(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        ys = [3.0 * x + 7.0 + 0.01 for x in xs]
        r_lin = linear_fit(xs, ys)
        r_quad = polynomial_fit(xs, ys, 2)
        exponential_fit(xs, ys)
        assert r_lin["r_squared"] > 0.99
        # Linear should be roughly as good as quadratic for linear data
        assert abs(r_lin["r_squared"] - r_quad["r_squared"]) < 0.01

    def test_higher_degree_polynomial_does_not_overfit(self):
        xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [3.0 * x + 2.0 + 0.05 for x in xs]
        r_deg2 = polynomial_fit(xs, ys, 2)
        r_deg4 = polynomial_fit(xs, ys, 4)
        assert r_deg2["r_squared"] > 0.99
        assert r_deg4["r_squared"] > 0.99
