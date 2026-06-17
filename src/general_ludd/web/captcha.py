"""Captcha / bot-block DETECTION + a pluggable operator solver hook.

This module DETECTS a challenge and emits a clear structured signal; it NEVER
implements a protection bypass.  An operator may wire :class:`CaptchaSolver` to a
LICENSED solving service; the default :class:`UnconfiguredSolver` declines.

Detection is heuristic and therefore ADVISORY — it both false-positives (a
legitimate 403/429/503) and false-negatives (a new/obfuscated challenge), so the
caller ALWAYS retains the partial body and the marker set is operator-extendable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from general_ludd.web.results import CaptchaSignal, RawFetchResult

#: (vendor, lowercased body/header marker).  Operator-extendable.
_BODY_MARKERS: tuple[tuple[str, str], ...] = (
    ("cloudflare", "/cdn-cgi/challenge-platform"),
    ("cloudflare", "attention required! | cloudflare"),
    ("cloudflare", "just a moment..."),
    ("cloudflare", "cf-mitigated"),
    ("recaptcha", "g-recaptcha"),
    ("recaptcha", "recaptcha/api.js"),
    ("hcaptcha", "h-captcha"),
    ("hcaptcha", "hcaptcha.com"),
    ("turnstile", "challenges.cloudflare.com/turnstile"),
    ("turnstile", "cf-turnstile"),
    ("perimeterx", "px-captcha"),
    ("perimeterx", "_pxhd"),
    ("datadome", "datadome"),
    ("incapsula", "_incapsula_resource"),
    ("incapsula", "incident id"),
)

#: Header-name -> marker fragment (lowercased).  Presence flags a vendor.
_HEADER_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("cloudflare", "cf-mitigated", ""),
    ("cloudflare", "server", "cloudflare"),
    ("datadome", "x-datadome", ""),
    ("perimeterx", "x-px", ""),
)

#: Statuses that, on their own, RAISE SUSPICION (but are not proof).
_SUSPICIOUS_STATUSES = frozenset({403, 429, 503})


def detect_captcha(
    status: int | None,
    body: str,
    headers: dict[str, str],
) -> CaptchaSignal:
    """Return an advisory :class:`CaptchaSignal` for a fetched response.

    A vendor marker in the body/headers is a strong signal.  A suspicious status
    WITH a marker is flagged with the vendor; a marker alone (any status) is also
    flagged.  A bare suspicious status with NO marker is reported as a weak signal
    (``detected=True, vendor=None``) so the operator can decide — it never drops
    the body.
    """
    lower_headers = {k.lower(): (v or "").lower() for k, v in (headers or {}).items()}
    body_l = (body or "").lower()

    for vendor, marker in _BODY_MARKERS:
        if marker in body_l:
            return CaptchaSignal(
                detected=True, vendor=vendor, status=status,
                reason=f"body marker {marker!r}",
            )

    for vendor, hname, frag in _HEADER_MARKERS:
        if hname in lower_headers and (not frag or frag in lower_headers[hname]):
            return CaptchaSignal(
                detected=True, vendor=vendor, status=status,
                reason=f"header {hname!r}",
            )

    if status in _SUSPICIOUS_STATUSES:
        # set-cookie __cf_bm is a Cloudflare bot-management fingerprint.
        sc = lower_headers.get("set-cookie", "")
        if "__cf_bm" in sc:
            return CaptchaSignal(
                detected=True, vendor="cloudflare", status=status,
                reason="set-cookie __cf_bm",
            )
        return CaptchaSignal(
            detected=True, vendor=None, status=status,
            reason=f"suspicious status {status} (weak signal)",
        )

    return CaptchaSignal(detected=False, status=status)


@runtime_checkable
class CaptchaSolver(Protocol):
    """An OPERATOR-wired hook to a LICENSED solving service.

    Returns a solved token (or ``None`` when it cannot/declines).  The toolkit
    ships NO built-in solver — this is purely an integration seam.
    """

    def solve(
        self, signal: CaptchaSignal, page: RawFetchResult
    ) -> SolveOutcome | None:
        ...


class SolveOutcome:
    """The result of an operator solver invocation."""

    __slots__ = ("reason", "solved", "token")

    def __init__(self, solved: bool, token: str | None = None, reason: str = "") -> None:
        self.solved = solved
        self.token = token
        self.reason = reason


class UnconfiguredSolver:
    """Default solver: always declines (no built-in bypass)."""

    def solve(
        self, signal: CaptchaSignal, page: RawFetchResult
    ) -> SolveOutcome | None:
        return SolveOutcome(solved=False, reason="no-solver-configured")
