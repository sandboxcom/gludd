"""Unit tests for the self-contained Graylog logs connector.

Transport is fully MOCKED — no socket is ever opened. A tiny ``_FakeResponse``
plus a closure-based fake transport stands in for httpx, exercising the public
contract: normalization, syslog level-int -> name mapping, timestamp parsing,
401 -> health not-ok, and internal-base_url SSRF rejection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from general_ludd.connectors.graylog import GraylogSource

# --------------------------------------------------------------------------- #
# Test doubles                                                                 #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Minimal stand-in for httpx.Response (status_code + json())."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _make_transport(
    *,
    status_code: int = 200,
    payload: Any = None,
    raise_exc: Exception | None = None,
    capture: list[dict[str, Any]] | None = None,
):
    """Build a fake transport callable matching the connector's signature."""

    def _transport(
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None,
        timeout: float,
    ) -> _FakeResponse:
        if capture is not None:
            capture.append(
                {"url": url, "headers": dict(headers), "params": dict(params or {}), "timeout": timeout}
            )
        if raise_exc is not None:
            raise raise_exc
        return _FakeResponse(status_code, payload)

    return _transport


# Canned Graylog universal-search payload (two messages spanning two syslog
# levels, one with an unknown numeric level, one with a malformed timestamp).
CANNED_SEARCH_PAYLOAD: dict[str, Any] = {
    "total_results": 2,
    "messages": [
        {
            "message": {
                "timestamp": "2026-06-12T18:30:00.000Z",
                "level": 3,  # syslog "error"
                "message": "disk usage critical on /var",
                "source": "web-01",
                "facility": "kern",
                "streams": ["stream-aaa", "stream-bbb"],
                "_id": "msg-1",
            }
        },
        {
            "message": {
                "timestamp": "2026-06-12T18:31:15.500Z",
                "level": 6,  # syslog "info"
                "message": "request served",
                "source": "web-02",
                "facility": "user",
                "streams": ["stream-ccc"],
            }
        },
    ],
}


def _cfg(**over: Any) -> dict[str, Any]:
    base = {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN", "name": "gl-prod"}
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# Construction / contract surface                                             #
# --------------------------------------------------------------------------- #


class TestContractSurface:
    def test_kind_is_logs(self) -> None:
        assert GraylogSource.KIND == "logs"
        src = GraylogSource(_cfg(), transport=_make_transport(), env={"GL_TOKEN": "t"})
        assert src.kind == "logs"

    def test_name_attribute(self) -> None:
        src = GraylogSource(_cfg(name="my-graylog"), transport=_make_transport(), env={})
        assert src.name == "my-graylog"

    def test_name_defaults_to_graylog(self) -> None:
        cfg = {"base_url": "https://graylog.example.com", "token_env": "GL_TOKEN"}
        src = GraylogSource(cfg, transport=_make_transport(), env={})
        assert src.name == "graylog"

    def test_missing_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            GraylogSource({"token_env": "GL_TOKEN"}, transport=_make_transport(), env={})

    def test_missing_token_env_raises(self) -> None:
        with pytest.raises(ValueError, match="token_env"):
            GraylogSource({"base_url": "https://graylog.example.com"}, transport=_make_transport(), env={})

    def test_no_imports_from_general_ludd_base(self) -> None:
        """No base-class / __init__ / sibling-connector imports.

        The connector stays self-contained EXCEPT for the one canonical shared
        SSRF guard (``general_ludd.security.ssrf``) — the single source of truth
        every guard must funnel through so blocklists cannot drift. Any other
        ``general_ludd`` import (a base class or a sibling connector) is still
        forbidden.
        """
        import general_ludd.connectors.graylog as mod

        src = mod.__file__ or ""
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import general_ludd", "from general_ludd")):
                assert (
                    "general_ludd.security.ssrf" in stripped
                    or "general_ludd.connectors._protocols" in stripped
                ), (
                    f"unexpected general_ludd import: {stripped!r}"
                )


# --------------------------------------------------------------------------- #
# query() — normalization                                                     #
# --------------------------------------------------------------------------- #


class TestQueryNormalization:
    def _src(self, capture: list[dict[str, Any]] | None = None) -> GraylogSource:
        transport = _make_transport(payload=CANNED_SEARCH_PAYLOAD, capture=capture)
        return GraylogSource(_cfg(), transport=transport, env={"GL_TOKEN": "secret-token"})

    def test_returns_one_record_per_message(self) -> None:
        records = self._src().query({"query": "*", "range": 300, "limit": 10})
        assert len(records) == 2

    def test_record_has_all_contract_keys(self) -> None:
        rec = self._src().query()[0]
        assert set(rec) == {
            "ts",
            "source",
            "kind",
            "level_or_status",
            "message",
            "value",
            "labels",
            "raw",
        }

    def test_kind_on_each_record_is_logs(self) -> None:
        for rec in self._src().query():
            assert rec["kind"] == "logs"

    def test_message_and_source_extracted(self) -> None:
        rec = self._src().query()[0]
        assert rec["message"] == "disk usage critical on /var"
        assert rec["source"] == "web-01"

    def test_syslog_level_int_mapped_to_name(self) -> None:
        records = self._src().query()
        assert records[0]["level_or_status"] == "error"  # level 3
        assert records[1]["level_or_status"] == "info"  # level 6

    def test_timestamp_parsed_to_utc_iso(self) -> None:
        rec = self._src().query()[0]
        # Normalized to explicit +00:00 offset.
        assert rec["ts"] == "2026-06-12T18:30:00+00:00"

    def test_labels_carry_source_facility_stream(self) -> None:
        rec = self._src().query()[0]
        assert rec["labels"] == {
            "source": "web-01",
            "facility": "kern",
            "stream": "stream-aaa",  # first stream id
        }

    def test_raw_preserves_original_message(self) -> None:
        rec = self._src().query()[0]
        assert rec["raw"]["_id"] == "msg-1"
        assert rec["raw"]["level"] == 3

    def test_relative_endpoint_and_params(self) -> None:
        capture: list[dict[str, Any]] = []
        self._src(capture).query({"query": "level:3", "range": 600, "limit": 25})
        call = capture[0]
        assert call["url"].endswith("/api/search/universal/relative")
        assert call["params"] == {"query": "level:3", "range": 600, "limit": 25}

    def test_absolute_endpoint_when_from_to_given(self) -> None:
        capture: list[dict[str, Any]] = []
        self._src(capture).query(
            {"query": "*", "from": "2026-06-12T00:00:00Z", "to": "2026-06-12T01:00:00Z"}
        )
        call = capture[0]
        assert call["url"].endswith("/api/search/universal/absolute")
        assert call["params"]["from"] == "2026-06-12T00:00:00Z"
        assert call["params"]["to"] == "2026-06-12T01:00:00Z"

    def test_basic_auth_header_uses_token_password_literal(self) -> None:
        import base64

        capture: list[dict[str, Any]] = []
        self._src(capture).query()
        auth = capture[0]["headers"]["Authorization"]
        assert auth.startswith("Basic ")
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
        assert decoded == "secret-token:token"

    def test_request_is_time_bounded(self) -> None:
        capture: list[dict[str, Any]] = []
        transport = _make_transport(payload=CANNED_SEARCH_PAYLOAD, capture=capture)
        GraylogSource(_cfg(timeout=7.5), transport=transport, env={"GL_TOKEN": "t"}).query()
        assert capture[0]["timeout"] == 7.5


class TestLevelMappingEdgeCases:
    def _query_one(self, level: Any) -> dict[str, Any]:
        msg = {"timestamp": "2026-06-12T00:00:00Z", "level": level, "message": "x", "source": "s"}
        payload = {"messages": [{"message": msg}]}
        src = GraylogSource(_cfg(), transport=_make_transport(payload=payload), env={"GL_TOKEN": "t"})
        return src.query()[0]

    @pytest.mark.parametrize(
        ("level", "name"),
        [
            (0, "emergency"),
            (1, "alert"),
            (2, "critical"),
            (3, "error"),
            (4, "warning"),
            (5, "notice"),
            (6, "info"),
            (7, "debug"),
        ],
    )
    def test_all_syslog_levels(self, level: int, name: str) -> None:
        assert self._query_one(level)["level_or_status"] == name

    def test_unknown_numeric_level_passes_through_as_string(self) -> None:
        assert self._query_one(99)["level_or_status"] == "99"

    def test_missing_level_is_none(self) -> None:
        assert self._query_one(None)["level_or_status"] is None

    def test_string_level_passes_through(self) -> None:
        assert self._query_one("ERROR")["level_or_status"] == "ERROR"


class TestQueryFailSoft:
    def test_non_200_returns_empty(self) -> None:
        src = GraylogSource(_cfg(), transport=_make_transport(status_code=500, payload={}), env={"GL_TOKEN": "t"})
        assert src.query() == []

    def test_transport_exception_returns_empty(self) -> None:
        src = GraylogSource(_cfg(), transport=_make_transport(raise_exc=RuntimeError("boom")), env={"GL_TOKEN": "t"})
        assert src.query() == []

    def test_payload_without_messages_returns_empty(self) -> None:
        src = GraylogSource(_cfg(), transport=_make_transport(payload={"total_results": 0}), env={"GL_TOKEN": "t"})
        assert src.query() == []

    def test_malformed_message_entry_skipped(self) -> None:
        good = {"message": "ok", "source": "s", "timestamp": "2026-06-12T00:00:00Z", "level": 6}
        payload = {"messages": [{"not_message": {}}, {"message": good}]}
        src = GraylogSource(_cfg(), transport=_make_transport(payload=payload), env={"GL_TOKEN": "t"})
        records = src.query()
        assert len(records) == 1
        assert records[0]["message"] == "ok"

    def test_bad_timestamp_falls_back_to_raw_string(self) -> None:
        payload = {"messages": [{"message": {"timestamp": "not-a-date", "level": 6, "message": "m", "source": "s"}}]}
        src = GraylogSource(_cfg(), transport=_make_transport(payload=payload), env={"GL_TOKEN": "t"})
        assert src.query()[0]["ts"] == "not-a-date"


# --------------------------------------------------------------------------- #
# health()                                                                    #
# --------------------------------------------------------------------------- #


class TestHealth:
    def test_health_ok_on_200(self) -> None:
        transport = _make_transport(status_code=200, payload={"status": "alive"})
        src = GraylogSource(_cfg(), transport=transport, env={"GL_TOKEN": "t"})
        h = src.health()
        assert h["ok"] is True
        assert h["status"] == 200
        assert h["name"] == "gl-prod"
        assert h["kind"] == "logs"

    def test_health_not_ok_on_401(self) -> None:
        src = GraylogSource(_cfg(), transport=_make_transport(status_code=401, payload={}), env={"GL_TOKEN": "bad"})
        h = src.health()
        assert h["ok"] is False
        assert h["status"] == 401
        assert h["error"] == "unauthorized"

    def test_health_not_ok_on_503(self) -> None:
        src = GraylogSource(_cfg(), transport=_make_transport(status_code=503, payload={}), env={"GL_TOKEN": "t"})
        h = src.health()
        assert h["ok"] is False
        assert h["status"] == 503

    def test_health_never_raises_on_transport_error(self) -> None:
        transport = _make_transport(raise_exc=ConnectionError("refused"))
        src = GraylogSource(_cfg(), transport=transport, env={"GL_TOKEN": "t"})
        h = src.health()  # must not raise
        assert h["ok"] is False
        assert "error" in h

    def test_health_hits_lbstatus_endpoint(self) -> None:
        capture: list[dict[str, Any]] = []
        transport = _make_transport(status_code=200, payload={}, capture=capture)
        GraylogSource(_cfg(), transport=transport, env={"GL_TOKEN": "t"}).health()
        assert capture[0]["url"].endswith("/api/system/lbstatus")


# --------------------------------------------------------------------------- #
# SSRF — literal-host block (no DNS)                                          #
# --------------------------------------------------------------------------- #


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:9000",
            "http://localhost:9000",
            "http://[::1]:9000",
            "http://10.0.0.5",
            "http://172.16.0.1",
            "http://192.168.1.10",
            "http://169.254.169.254",  # cloud metadata IP
            "http://169.254.0.1",  # link-local
            "http://0.0.0.0",
            # Metadata NAME coverage added by the canonical shared guard.
            "http://metadata.google.internal/",
            "http://metadata/",
        ],
    )
    def test_internal_base_url_rejected(self, base_url: str) -> None:
        with pytest.raises(ValueError, match=r"internal|SSRF|localhost"):
            GraylogSource({"base_url": base_url, "token_env": "GL_TOKEN"}, transport=_make_transport(), env={})

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://graylog.example.com",
            "https://logs.corp.internal-name.io",  # public-looking hostname, not an IP literal
            "http://8.8.8.8",  # public IP literal
        ],
    )
    def test_public_base_url_allowed(self, base_url: str) -> None:
        src = GraylogSource({"base_url": base_url, "token_env": "GL_TOKEN"}, transport=_make_transport(), env={})
        assert src.base_url == base_url.rstrip("/")
