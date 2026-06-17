"""Offline tests for fetch_parsed + the stdlib HTML extractor."""

from __future__ import annotations

import socket

import httpx
import pytest

from general_ludd.web import ssrf_client as sc
from general_ludd.web.parse import fetch_parsed, parse_html
from general_ludd.web.results import WebError

_PUBLIC_IP = "93.184.216.34"
_URL = "https://example.com/article"

_HTML = """
<html lang="en">
<head>
  <title>  Hello   World </title>
  <meta name="description" content="a test page">
  <meta property="og:title" content="OG Hello">
  <style>.x{color:red}</style>
</head>
<body>
  <h1>Main Heading</h1>
  <script>var hidden = 'no';</script>
  <p>Visible paragraph text.</p>
  <a href="/relative">rel</a>
  <a href="https://other.example.org/abs">abs</a>
  <a href="/relative">dup</a>
  <a href="javascript:void(0)">skip</a>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    def _fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


def test_parse_html_extracts_fields():
    ex = parse_html(_HTML, base_url=_URL)
    assert ex.normalized_title() == "Hello World"
    assert ex.lang == "en"
    assert "Visible paragraph text." in ex.text
    assert "var hidden" not in ex.text  # script skipped
    assert "color:red" not in ex.text  # style skipped
    assert "Main Heading" in ex.headings
    assert ex.meta["description"] == "a test page"
    assert ex.meta["og:title"] == "OG Hello"
    # absolute, deduped, javascript: dropped
    assert "https://example.com/relative" in ex.links
    assert "https://other.example.org/abs" in ex.links
    assert ex.links.count("https://example.com/relative") == 1
    assert all("javascript" not in link for link in ex.links)


def _transport(status=200, body=_HTML, ctype="text/html"):
    def handler(req):
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    return httpx.MockTransport(handler)


def test_fetch_parsed_ok():
    page = fetch_parsed(_URL, transport=_transport())
    assert page.ok is True
    assert page.title == "Hello World"
    assert page.links


def test_fetch_parsed_propagates_ssrf_error():
    page = fetch_parsed("https://127.0.0.1/x", transport=_transport())
    assert page.ok is False
    assert page.error == WebError.SSRF_BLOCKED


def test_fetch_parsed_non_html_returns_text():
    page = fetch_parsed(_URL, transport=_transport(body="plain data", ctype="text/plain"))
    assert page.ok is True
    assert page.text == "plain data"
    assert page.links == []


def test_fetch_parsed_4xx_propagates():
    page = fetch_parsed(_URL, transport=_transport(status=404))
    assert page.ok is False
    assert page.error == WebError.HTTP_4XX
