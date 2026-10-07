#!/usr/bin/env python3
"""Probe the public presentation revision without returning page content."""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CANONICAL_ORIGIN = "sandboxcom.github.io"
CANONICAL_PATH = "/gludd/"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REVISION_PATTERNS = (
    re.compile(r"github\.com/sandboxcom/gludd/blob/([0-9a-f]{40})/"),
    re.compile(r"(?:presentation\.(?:js|css)[^\"']*[?&]v=)([0-9a-f]{40})"),
)


@dataclass(frozen=True)
class ProbePlan:
    """Validated, serializable public Pages probe configuration."""

    url: str
    request_url: str
    expected_sha: str
    timeout_seconds: int


def build_plan(*, url: str, expected_sha: str, timeout_seconds: int, nonce: str) -> ProbePlan:
    """Validate the fixed public boundary and add a harmless cache buster."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != CANONICAL_ORIGIN
        or parsed.path != CANONICAL_PATH
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("URL must be the canonical Gludd Pages presentation")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("expected SHA must be 40 lowercase hex characters")
    if not 1 <= timeout_seconds <= 60:
        raise ValueError("probe timeout must be between 1 and 60 seconds")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", nonce):
        raise ValueError("probe nonce is invalid")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("gludd_probe", nonce))
    request_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    return ProbePlan(
        url=url,
        request_url=request_url,
        expected_sha=expected_sha,
        timeout_seconds=timeout_seconds,
    )


def extract_revision(html: str) -> str:
    """Extract one exact build SHA from immutable links and cache keys."""
    revisions = {
        match.group(1)
        for pattern in REVISION_PATTERNS
        for match in pattern.finditer(html)
    }
    if not revisions:
        raise ValueError("published revision marker is missing")
    if len(revisions) != 1:
        raise ValueError("published artifact has conflicting revision markers")
    return revisions.pop()


def extract_display_revision(html_text: str) -> str:
    """Extract the legacy seven-character footer revision when necessary."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", html_text))
    match = re.search(r"\bcommit\s+([0-9a-f]{7})\b", text, re.IGNORECASE)
    if not match:
        raise ValueError("published display revision is missing")
    return match.group(1).lower()


def probe_pages(
    plan: ProbePlan,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, object]:
    """Fetch one cache-busted artifact and return content-free revision evidence."""
    request = urllib.request.Request(
        plan.request_url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "gludd-presentation-revision-probe/1",
        },
    )
    with opener(request, timeout=plan.timeout_seconds) as response:
        status = int(response.status)
        content_type = response.headers.get_content_type()
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if status != 200:
        raise RuntimeError(f"Pages returned HTTP {status}")
    if content_type != "text/html":
        raise RuntimeError("Pages did not return HTML")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Pages response exceeded the bounded size")
    try:
        html_text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeError("Pages response was not UTF-8") from exc
    try:
        published_sha = extract_revision(html_text)
        precision = "full"
    except ValueError as exc:
        if "missing" not in str(exc):
            raise
        published_sha = extract_display_revision(html_text)
        precision = "short"
    return {
        "expected_sha": plan.expected_sha,
        "matches": precision == "full" and published_sha == plan.expected_sha,
        "published_sha": published_sha,
        "revision_precision": precision,
        "status": status,
        "url": plan.url,
    }


def main() -> None:
    """Validate the plan or execute one bounded public revision probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://sandboxcom.github.io/gludd/")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    plan = build_plan(
        url=args.url,
        expected_sha=args.expected_sha,
        timeout_seconds=args.timeout_seconds,
        nonce=str(time.time_ns()),
    )
    if args.validate_only:
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return
    try:
        result = probe_pages(plan)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"category": str(exc), "error": type(exc).__name__, "url": plan.url},
                sort_keys=True,
            )
        )
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["matches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
