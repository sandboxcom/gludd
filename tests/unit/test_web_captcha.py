"""Offline tests for captcha/bot-block detection + the solver-hook seam."""

from __future__ import annotations

from general_ludd.web.captcha import NullSolver, detect_block
from general_ludd.web.types import BlockSignal


def test_cloudflare_challenge_detected() -> None:
    sig = detect_block(403, {"server": "cloudflare", "cf-ray": "x"},
                       "<html>Just a moment...</html>")
    assert sig is not None
    assert sig.vendor == "cloudflare"
    assert sig.kind == "bot_block"


def test_recaptcha_detected() -> None:
    sig = detect_block(403, {}, '<div class="g-recaptcha"></div>')
    assert sig is not None
    assert sig.vendor == "recaptcha"


def test_datadome_via_header() -> None:
    sig = detect_block(403, {"set-cookie": "datadome=abc"}, "")
    assert sig is not None
    assert sig.vendor == "datadome"


def test_200_never_a_block() -> None:
    assert detect_block(200, {}, "captcha mentioned but page is fine") is None


def test_plain_429_without_marker_not_a_block() -> None:
    # A transient-load 429 with no challenge marker is NOT flagged (so it can be
    # retried normally rather than treated as a persistent challenge).
    assert detect_block(429, {"retry-after": "5"}, "rate limited, try later") is None


def test_429_with_marker_is_block_with_retry_after() -> None:
    sig = detect_block(429, {"retry-after": "30"}, "verify you are human")
    assert sig is not None
    assert sig.retry_after == 30.0


def test_null_solver_declines() -> None:
    solver = NullSolver()
    sig = BlockSignal(vendor="x", kind="captcha", status=403, evidence="e")
    assert solver.solve(sig, "https://e.com/") is None


def test_huge_body_scan_capped() -> None:
    # Marker beyond the 64KB cap must not be found (scan is bounded).
    body = ("a" * (64 * 1024 + 10)) + "g-recaptcha"
    assert detect_block(403, {}, body) is None
