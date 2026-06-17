"""SSRF guard tests for the GitHub Issues source and the IssueSource base.

These are security-focused: a ``base_url`` pointing at an internal/loopback/
link-local host (notably the cloud metadata endpoint 169.254.169.254) must be
rejected at construction, and a post-construction mutation of ``base_url`` to
such a host must be blocked at request time (defense-in-depth). A public host
(``api.github.com``) must be allowed and must actually reach the transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.issue_sources.base import IssueSource
from general_ludd.issue_sources.github_issues import GitHubIssuesSource


class RecordingTransport:
    """Transport double that records calls and returns a canned response.

    Conforms to the injectable transport protocol:
    ``(method, url, headers, body) -> (status, payload)``.
    """

    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else []
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any | None,
    ) -> tuple[int, Any]:
        self.calls.append((method, url, headers, body))
        return self.status, self.payload


# Internal / loopback / metadata hosts that must never be reachable.
INTERNAL_BASE_URLS = [
    "http://169.254.169.254",            # AWS/GCP/Azure IMDS metadata endpoint
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost",
    "http://127.0.0.1",
    "http://127.0.0.1:8080",
    "http://[::1]",                       # IPv6 loopback
    "http://10.0.0.5",                    # RFC1918 private
    "http://192.168.1.10",                # RFC1918 private
    "http://172.16.0.1",                  # RFC1918 private
    "http://0.0.0.0",                     # unspecified
]


# ---------------------------------------------------------------------------
# Construction-time rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", INTERNAL_BASE_URLS)
def test_internal_base_url_rejected_at_construction(base_url: str) -> None:
    transport = RecordingTransport()
    with pytest.raises(ValueError, match="internal base_url host"):
        GitHubIssuesSource({"repo": "owner/name", "base_url": base_url}, transport=transport)
    # The guard fires before any request is attempted.
    assert transport.calls == []


def test_imds_metadata_endpoint_rejected() -> None:
    """The cloud metadata endpoint is the canonical SSRF target."""
    with pytest.raises(ValueError, match=r"169\.254\.169\.254"):
        GitHubIssuesSource(
            {"repo": "owner/name", "base_url": "http://169.254.169.254/latest/meta-data/"}
        )


# ---------------------------------------------------------------------------
# Public host allowed and reaches the transport
# ---------------------------------------------------------------------------


def test_public_github_base_url_allowed_and_reaches_transport() -> None:
    transport = RecordingTransport(status=200, payload=[])
    src = GitHubIssuesSource(
        {"repo": "owner/name", "base_url": "https://api.github.com"},
        transport=transport,
    )
    assert src.base_url == "https://api.github.com"
    # fetch() should drive a real request through the recording transport.
    src.fetch()
    assert len(transport.calls) == 1
    method, url, _headers, _body = transport.calls[0]
    assert method == "GET"
    assert url == "https://api.github.com/repos/owner/name/issues"


def test_default_base_url_allowed() -> None:
    """No base_url configured falls back to the public default (allowed)."""
    transport = RecordingTransport()
    src = GitHubIssuesSource({"repo": "owner/name"}, transport=transport)
    assert src.base_url == "https://api.github.com"
    src.fetch()
    assert transport.calls and transport.calls[0][1].startswith("https://api.github.com")


# ---------------------------------------------------------------------------
# Post-construction mutation blocked per-request (defense-in-depth)
# ---------------------------------------------------------------------------


def test_post_construction_mutation_to_imds_blocked_in_call() -> None:
    transport = RecordingTransport()
    src = GitHubIssuesSource(
        {"repo": "owner/name", "base_url": "https://api.github.com"},
        transport=transport,
    )
    # Attacker mutates base_url after construction passed the guard.
    src.base_url = "http://169.254.169.254"
    with pytest.raises(ValueError, match="internal base_url host"):
        src.fetch()
    # No request ever left the building.
    assert transport.calls == []


def test_post_construction_mutation_to_loopback_blocked_in_call() -> None:
    transport = RecordingTransport()
    src = GitHubIssuesSource(
        {"repo": "owner/name", "base_url": "https://api.github.com"},
        transport=transport,
    )
    src.base_url = "http://127.0.0.1:9999"
    with pytest.raises(ValueError, match="internal base_url host"):
        src._call("GET", "/repos/owner/name/issues")
    assert transport.calls == []


def test_post_construction_mutation_blocked_in_write_back() -> None:
    transport = RecordingTransport()
    src = GitHubIssuesSource(
        {"repo": "owner/name", "base_url": "https://api.github.com"},
        transport=transport,
    )
    from general_ludd.issue_sources.base import Transition

    src.base_url = "http://localhost"
    with pytest.raises(ValueError, match="internal base_url host"):
        src.write_back("1", Transition.DONE)
    assert transport.calls == []


# ---------------------------------------------------------------------------
# Base IssueSource: guard runs even when require_base_url=False
# ---------------------------------------------------------------------------


def test_base_issue_source_guards_when_require_base_url_false() -> None:
    """A present internal base_url is rejected even with require_base_url=False."""
    with pytest.raises(ValueError, match="internal base_url host"):
        IssueSource(
            {"base_url": "http://169.254.169.254"},
            require_base_url=False,
        )


def test_base_issue_source_missing_url_allowed_when_not_required() -> None:
    """No base_url + require_base_url=False constructs cleanly (guard is a no-op)."""
    src = IssueSource({}, require_base_url=False)
    assert src.base_url is None


def test_base_issue_source_public_url_allowed() -> None:
    src = IssueSource({"base_url": "https://example.com"}, require_base_url=False)
    assert src.base_url == "https://example.com"


def test_base_issue_source_missing_url_required_raises() -> None:
    with pytest.raises(ValueError, match=r"base_url.* is required"):
        IssueSource({}, require_base_url=True)
