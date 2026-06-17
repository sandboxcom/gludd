"""Offline tests for captcha/bot-block detection + the solver hook (no bypass)."""

from __future__ import annotations

from general_ludd.web.captcha import (
    SolveOutcome,
    UnconfiguredSolver,
    detect_captcha,
)
from general_ludd.web.results import RawFetchResult


def test_cloudflare_body_marker():
    sig = detect_captcha(403, "<html>Just a moment...</html>", {})
    assert sig.detected is True
    assert sig.vendor == "cloudflare"


def test_recaptcha_marker():
    sig = detect_captcha(200, "<div class='g-recaptcha'></div>", {})
    assert sig.detected is True
    assert sig.vendor == "recaptcha"


def test_hcaptcha_marker():
    sig = detect_captcha(200, "<div class='h-captcha'></div>", {})
    assert sig.detected is True
    assert sig.vendor == "hcaptcha"


def test_datadome_header():
    sig = detect_captcha(403, "blocked", {"x-datadome": "protected"})
    assert sig.detected is True
    assert sig.vendor == "datadome"


def test_cf_bm_cookie_on_suspicious_status():
    sig = detect_captcha(429, "rate", {"set-cookie": "__cf_bm=abc; Path=/"})
    assert sig.detected is True
    assert sig.vendor == "cloudflare"


def test_suspicious_status_weak_signal():
    sig = detect_captcha(503, "service unavailable", {})
    assert sig.detected is True
    assert sig.vendor is None
    assert "weak" in sig.reason


def test_clean_page_not_detected():
    sig = detect_captcha(200, "<html><body>normal</body></html>", {})
    assert sig.detected is False


def test_unconfigured_solver_declines():
    solver = UnconfiguredSolver()
    out = solver.solve(detect_captcha(403, "Just a moment...", {}),
                       RawFetchResult(ok=False))
    assert isinstance(out, SolveOutcome)
    assert out.solved is False
    assert out.reason == "no-solver-configured"
