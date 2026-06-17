"""Tests for the OPTIONAL JS render path — lazy import + structured fallback.

Proves base import + collection NEVER break without the 'web' extra: importing
render and calling render_js without playwright returns a structured
renderer_unavailable WebResult rather than raising ImportError.
"""

from __future__ import annotations

import builtins

import httpx
import pytest

from general_ludd.web.policy import WebPolicy
from general_ludd.web.render import render_js
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.types import WebError


def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _internal_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def _fetcher(resolver=_public_resolver, **pol) -> SafeFetcher:
    policy = WebPolicy(allow_render=True, **pol)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          follow_redirects=False)
    return SafeFetcher(client=client, resolver=resolver, policy=policy)


def test_render_disabled_by_policy() -> None:
    # allow_render defaults to False.
    result = render_js("https://example.com/",
                       fetcher=SafeFetcher(
                           client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
                           resolver=_public_resolver,
                           policy=WebPolicy()))
    assert result.ok is False
    # DISTINCT from a missing dependency: a policy-disabled render reports
    # RENDER_DISABLED so the caller knows to flip the policy, not install the extra.
    assert result.error == WebError.RENDER_DISABLED
    assert "disabled by policy" in (result.detail or "")


def test_render_ssrf_blocked_before_launch() -> None:
    result = render_js("https://evil.example.com/",
                       policy=WebPolicy(allow_render=True),
                       fetcher=_fetcher(resolver=_internal_resolver))
    assert result.ok is False
    assert result.error == WebError.SSRF_BLOCKED


def test_render_unavailable_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = render_js("https://example.com/",
                       policy=WebPolicy(allow_render=True),
                       fetcher=_fetcher())
    assert result.ok is False
    assert result.error == WebError.RENDERER_UNAVAILABLE
    assert "web" in (result.detail or "")  # mentions the extra


def test_render_module_imports_without_extra() -> None:
    # The whole point: importing render must succeed with nothing extra installed.
    import general_ludd.web.render as r
    assert hasattr(r, "render_js")
