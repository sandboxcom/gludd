"""Captcha / bot-block DETECTION + a pluggable operator solver-hook seam.

ETHICS BOUNDARY (documented, deliberate): gludd ships ONLY *detection* of a
captcha/bot-block challenge plus a structured signal and a hook the OPERATOR may
wire to a LICENSED solver service. It implements NO built-in protection bypass
and NO challenge-solving. The default :class:`NullSolver` returns ``None`` so the
toolkit falls back gracefully with a clear ``CAPTCHA_DETECTED`` signal.

Detection is signature-based and best-effort: it carries the raw body so callers
can judge, and it WILL drift as vendors change challenge HTML — treat it as a
signal, not a guarantee.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from general_ludd.web.types import BlockSignal

#: Statuses that, combined with a vendor marker, indicate a challenge/block.
_BLOCK_STATUSES = frozenset({401, 403, 429, 503})

#: Scan only the first ~64KB of a body so a hostile huge response can't stall the
#: substring scan.
_BODY_SCAN_CAP = 64 * 1024

#: vendor -> (kind, [case-insensitive substrings]). Centralized + test-pinned so
#: operators can tune and tests can assert against captured fixtures.
_MARKERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("cloudflare", "bot_block", (
        "cf-mitigated", "__cf_chl", "/cdn-cgi/challenge-platform",
        "attention required! | cloudflare", "just a moment...", "__cf_bm",
    )),
    ("datadome", "captcha", (
        "captcha-delivery", "geo.captcha-delivery.com", "datadome",
    )),
    ("recaptcha", "captcha", ("/recaptcha/", "g-recaptcha")),
    ("hcaptcha", "captcha", ("hcaptcha.com", "h-captcha")),
    ("perimeterx", "bot_block", ("_px", "px-captcha")),
    ("akamai", "bot_block", ("_abck", "ak_bmsc")),
    ("imperva", "bot_block", ("pardon our interruption",)),
    ("generic", "captcha", ("verify you are human", "captcha")),
)


@runtime_checkable
class SolverHook(Protocol):
    """Operator-pluggable captcha solver seam.

    An operator MAY inject an implementation backed by their own LICENSED
    anti-captcha contract. gludd never bundles one. ``solve`` returns a
    solver-defined token/result, or ``None`` to decline (the default).
    """

    def solve(self, challenge: BlockSignal, url: str) -> object | None:
        """Attempt to solve ``challenge`` for ``url``; ``None`` to decline."""
        ...


class NullSolver:
    """The default no-op solver — always declines, yielding a clean fallback."""

    def solve(self, challenge: BlockSignal, url: str) -> object | None:
        return None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def detect_block(
    status: int | None,
    headers: Mapping[str, str],
    body: str | None,
) -> BlockSignal | None:
    """Detect a captcha/bot-block challenge; ``None`` if none is recognized.

    Triggers only on a block-ish status combined with a known vendor marker in
    the headers or the (capped) body. A marker-bearing 429 is a *persistent*
    challenge (NOT auto-retried), distinguished from a transient-load 429 by the
    presence of a marker.
    """
    if status is None or status not in _BLOCK_STATUSES:
        return None

    hdr_blob = " ".join(f"{k}: {v}" for k, v in headers.items()).lower()
    body_blob = (body or "")[:_BODY_SCAN_CAP].lower()
    haystack = hdr_blob + "\n" + body_blob

    # Cloudflare also needs a Server: cloudflare / cf-ray corroboration to reduce
    # false positives, but a chl/cf_bm marker alone is strong enough.
    for vendor, kind, markers in _MARKERS:
        for marker in markers:
            if marker in haystack:
                effective_kind = kind
                if status == 429 and kind == "captcha" and vendor == "generic":
                    effective_kind = "rate_limited"
                return BlockSignal(
                    vendor=vendor,
                    kind=effective_kind,
                    status=status,
                    evidence=marker,
                    retry_after=_retry_after(headers),
                )
    return None
