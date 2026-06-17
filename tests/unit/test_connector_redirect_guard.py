"""Tests: SSRF redirect guard — pagerduty, opsgenie, monday transports.

Verifies that:
* PagerDuty and Opsgenie _DefaultTransport.get passes follow_redirects=False
  to httpx.get, so the library never silently follows a 3xx redirect.
* Monday _default_transport raises urllib.error.HTTPError on a 302 instead of
  following it (via _NoRedirectHandler).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------


def test_pagerduty_transport_passes_follow_redirects_false() -> None:
    """_DefaultTransport.get must forward follow_redirects=False to httpx.get."""
    from general_ludd.connectors.pagerduty import _DefaultTransport

    captured: dict[str, Any] = {}

    def fake_httpx_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        resp = SimpleNamespace(status_code=200)
        resp.json = lambda: {}  # type: ignore[assignment]
        return resp  # type: ignore[return-value]

    transport = _DefaultTransport()
    with patch("httpx.get", side_effect=fake_httpx_get):
        transport.get(
            "https://api.pagerduty.com/incidents",
            headers={"Authorization": "Token tok"},
            params={"limit": 100},
            timeout=10.0,
        )

    assert "follow_redirects" in captured, (
        "httpx.get was not called with follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


# ---------------------------------------------------------------------------
# Opsgenie
# ---------------------------------------------------------------------------


def test_opsgenie_transport_passes_follow_redirects_false() -> None:
    """_DefaultTransport.get must forward follow_redirects=False to httpx.get."""
    from general_ludd.connectors.opsgenie import _DefaultTransport

    captured: dict[str, Any] = {}

    def fake_httpx_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        resp = SimpleNamespace(status_code=200)
        resp.json = lambda: {}  # type: ignore[assignment]
        return resp  # type: ignore[return-value]

    transport = _DefaultTransport()
    with patch("httpx.get", side_effect=fake_httpx_get):
        transport.get(
            "https://api.opsgenie.com/v2/alerts",
            headers={"Authorization": "GenieKey key"},
            params={"limit": 100},
            timeout=10.0,
        )

    assert "follow_redirects" in captured, (
        "httpx.get was not called with follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


# ---------------------------------------------------------------------------
# Monday
# ---------------------------------------------------------------------------


def test_monday_no_redirect_handler_raises_on_302() -> None:
    """_NoRedirectHandler must raise HTTPError instead of following a 302."""
    from general_ludd.issue_sources.monday import _NoRedirectHandler

    handler = _NoRedirectHandler()
    fake_headers = MagicMock()
    fake_req = urllib.request.Request("https://api.monday.com/v2")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(
            req=fake_req,
            fp=None,
            code=302,
            msg="Found",
            headers=fake_headers,
            newurl="https://internal.corp/secret",
        )

    assert exc_info.value.code == 302
    assert "redirect blocked" in str(exc_info.value.reason or exc_info.value.msg)


def test_monday_default_transport_uses_no_redirect_opener() -> None:
    """_default_transport must pass _NoRedirectHandler to build_opener.

    We patch urllib.request.build_opener to capture what handler class is
    passed; the result of build_opener returns a successful fake response so
    the rest of the function completes normally.
    """
    from general_ludd.issue_sources.monday import (
        _default_transport,
        _NoRedirectHandler,
    )

    handler_classes_seen: list[type] = []
    fake_body = json.dumps({"data": {"boards": []}}).encode()

    class _FakeResp:
        status = 200

        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return fake_body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class _FakeOpener:
        def open(self, req: Any, timeout: Any = None) -> _FakeResp:
            return _FakeResp()

    def capturing_build_opener(*handlers: Any) -> _FakeOpener:
        for h in handlers:
            handler_classes_seen.append(type(h))
        return _FakeOpener()

    with patch("urllib.request.build_opener", side_effect=capturing_build_opener):
        status, _payload = _default_transport(
            method="POST",
            url="https://api.monday.com/v2",
            headers={"Content-Type": "application/json"},
            json_body={"query": "{ boards { id } }"},
            timeout=10.0,
        )

    assert _NoRedirectHandler in handler_classes_seen, (
        f"_NoRedirectHandler not found in handlers passed to build_opener; "
        f"got: {handler_classes_seen}"
    )
    assert status == 200
