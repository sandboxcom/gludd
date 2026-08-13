"""SSRF regression tests for G12 WebRetriever.fetch_web_page.

Confirms the guard added to `general_ludd.retrieval.web.WebRetriever.
fetch_web_page` rejects loopback / link-local / RFC-1918 / cloud-metadata
targets (the CONFIRMED HIGH SSRF hole: no scheme/host check before
`urllib.request.urlopen`) while leaving ordinary public URLs unaffected, AND
that a same-origin-looking public URL cannot pivot to an internal host via an
HTTP redirect (the guard only inspects the ORIGINAL url; `urllib.request`'s
default opener auto-follows 3xx redirects, so without a no-follow redirect
handler a public URL that later 302s to e.g. `http://169.254.169.254/` would
bypass the guard entirely).

Mocks `urllib.request.build_opener` throughout (the retriever builds a fresh
opener with the no-follow redirect handler for every request rather than
calling the bare `urllib.request.urlopen`) so no test touches the network —
the guard must reject the dangerous cases WITHOUT ever reaching the mocked
fetch, and must NOT reject a normal public URL.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.retrieval.web import WebRetriever, _NoRedirectHandler
from general_ludd.security.safe_diskcache import open_safe_diskcache


class TestWebRetrieverSSRFGuard:
    def test_rejects_cloud_metadata_ip(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("http://169.254.169.254/latest/meta-data/")
        mock_build_opener.assert_not_called()

    def test_rejects_loopback_ip(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("http://127.0.0.1/")
        mock_build_opener.assert_not_called()

    def test_rejects_rfc1918_private_ip(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("http://10.0.0.5/")
        mock_build_opener.assert_not_called()

    def test_rejects_localhost_name(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("http://localhost:8080/admin")
        mock_build_opener.assert_not_called()

    def test_rejects_link_local_non_metadata(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("http://169.254.1.1/")
        mock_build_opener.assert_not_called()

    def test_rejects_disallowed_scheme(self) -> None:
        retriever = WebRetriever()
        with (
            patch("urllib.request.build_opener") as mock_build_opener,
            pytest.raises(ValueError, match="SSRF guard"),
        ):
            retriever.fetch_web_page("file:///etc/passwd")
        mock_build_opener.assert_not_called()

    def test_public_https_url_not_rejected_by_guard(self) -> None:
        html = "<html><head><title>OK</title></head><body>hi</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}
        url = "https://example.com/ssrf-guard-public-check"

        retriever = WebRetriever()
        # The disk cache backing WebRetriever persists across test runs (it is
        # a real diskcache directory, not an in-memory fixture); evict any
        # stale entry from a prior run so this test genuinely exercises the
        # fetch path (and therefore the guard) rather than a cache hit.
        with open_safe_diskcache(retriever._cache_path) as cache:
            cache.delete(url)
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            result = retriever.fetch_web_page(url)
        mock_build_opener.assert_called_once()
        mock_build_opener.return_value.open.assert_called_once()
        assert result.status_code == 200
        assert result.title == "OK"


class TestWebRetrieverRedirectGuard:
    """Confirms a 3xx redirect can never pivot a public fetch to an internal
    host: `fetch_web_page` must build its opener with `_NoRedirectHandler`
    (mirroring `general_ludd.issue_sources.monday._NoRedirectHandler`), and
    that handler must raise instead of following the redirect."""

    def test_no_redirect_handler_raises_on_302_to_metadata_ip(self) -> None:
        """`_NoRedirectHandler` must raise HTTPError instead of following a
        302 that points at the cloud-metadata IP — the actual SSRF-bypass
        vector this fix closes."""
        handler = _NoRedirectHandler()
        fake_headers = MagicMock()
        fake_req = urllib.request.Request("https://example.com/redirector")

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(
                req=fake_req,
                fp=None,
                code=302,
                msg="Found",
                headers=fake_headers,
                newurl="http://169.254.169.254/latest/meta-data/",
            )

        try:
            assert exc_info.value.code == 302
            assert "redirect blocked" in str(
                exc_info.value.reason or exc_info.value.msg
            )
        finally:
            exc_info.value.close()

    def test_fetch_web_page_uses_no_redirect_opener(self) -> None:
        """`fetch_web_page` must pass `_NoRedirectHandler` to
        `urllib.request.build_opener` on every request. We patch
        `build_opener` to capture the handler classes it was called with and
        return a fake opener producing a normal 200 response, so the rest of
        the fetch completes and only the *wiring* is under test — the
        handler's own raise-on-redirect behavior is covered separately by
        `test_no_redirect_handler_raises_on_302_to_metadata_ip`."""
        handler_classes_seen: list[type] = []
        html = "<html><head><title>Wired</title></head><body>ok</body></html>"

        class _FakeResp(io.BytesIO):
            status = 200
            headers: ClassVar[dict[str, str]] = {}

        def capturing_build_opener(*handlers: object) -> MagicMock:
            for h in handlers:
                handler_classes_seen.append(type(h))
            opener = MagicMock()
            opener.open.return_value = _FakeResp(html.encode("utf-8"))
            return opener

        url = "https://example.com/redirect-wiring-check"
        retriever = WebRetriever()
        with open_safe_diskcache(retriever._cache_path) as cache:
            cache.delete(url)
        with patch("urllib.request.build_opener", side_effect=capturing_build_opener):
            result = retriever.fetch_web_page(url)

        assert _NoRedirectHandler in handler_classes_seen, (
            f"_NoRedirectHandler not found in handlers passed to build_opener; "
            f"got: {handler_classes_seen}"
        )
        assert result.status_code == 200
        assert result.title == "Wired"

    def test_redirect_to_metadata_ip_is_not_followed_end_to_end(self) -> None:
        """End-to-end: when the (mocked) opener's `.open()` raises the
        HTTPError that `_NoRedirectHandler` would raise for a redirect
        targeting the metadata IP, `fetch_web_page` must surface it as a
        normal (non-2xx) result rather than making any further request to
        the internal host — proving the redirect is never followed and the
        internal URL is never fetched."""
        internal_url = "http://169.254.169.254/latest/meta-data/"
        redirect_error = urllib.error.HTTPError(
            url=internal_url,
            code=302,
            msg="redirect blocked (SSRF guard): Found",
            hdrs={"Location": internal_url},
            fp=None,
        )
        url = "https://example.com/redirects-to-metadata"
        retriever = WebRetriever()
        with open_safe_diskcache(retriever._cache_path) as cache:
            cache.delete(url)

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = redirect_error
            result = retriever.fetch_web_page(url)

        # The opener was invoked exactly once, for the ORIGINAL public url —
        # no second call was ever made to fetch the internal host.
        mock_build_opener.return_value.open.assert_called_once()
        called_req = mock_build_opener.return_value.open.call_args[0][0]
        assert called_req.full_url == url
        assert result.status_code == 302
        assert result.content == ""
