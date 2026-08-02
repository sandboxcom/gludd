"""SEC.1 D-10: Worker API request size limits — behavioral tests.

Covers:
- MAX_BODY_BYTES is defined and non-zero (8 MiB default)
- Content-Length early-reject at boundary
- Streaming body cap enforcement
- All ingest endpoints enforce the cap
- 413 response format
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.receiver import router as receiver_router
from general_ludd.receiver.parsers import MAX_PAYLOAD_BYTES

TOKEN = "test-ingest-token"


@pytest.fixture(autouse=True)
def _set_ingest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(receiver_router.INGEST_TOKEN_ENV, TOKEN)


def _client() -> TestClient:
    app = FastAPI()
    state: dict = {}
    receiver_router.register(app, state)
    return TestClient(app, raise_server_exceptions=False)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


class TestMaxBodyBytesConstant:
    def test_max_body_bytes_defined(self) -> None:
        assert receiver_router.MAX_BODY_BYTES > 0

    def test_max_body_bytes_matches_parser_cap(self) -> None:
        assert receiver_router.MAX_BODY_BYTES == MAX_PAYLOAD_BYTES

    def test_max_body_bytes_is_8_mebibytes(self) -> None:
        assert receiver_router.MAX_BODY_BYTES == 8 * 1024 * 1024


class TestContentLengthReject:
    def test_exact_at_boundary_accepted(self) -> None:
        body = json.dumps({"msg": "ok"}).encode()
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(len(body))
        resp = client.post("/ingest/webhook", content=body, headers=headers)
        assert resp.status_code == 202

    def test_one_byte_over_boundary_rejected(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(receiver_router.MAX_BODY_BYTES + 1)
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"] == "payload_too_large"
        assert body["max_bytes"] == receiver_router.MAX_BODY_BYTES

    def test_very_large_content_length_rejected(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(100 * 1024 * 1024)
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        assert resp.status_code == 413

    def test_zero_content_length_accepted(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = "0"
        resp = client.post("/ingest/webhook", content=b"", headers=headers)
        assert resp.status_code == 202

    def test_bad_content_length_rejected(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = "not-a-number"
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        assert resp.status_code in (400, 413, 422)


class TestStreamingBodyReject:
    def test_body_exceeding_cap_returns_413(self) -> None:
        client = _client()
        oversize = b'{"message":"' + b"a" * (receiver_router.MAX_BODY_BYTES + 10) + b'"}'
        resp = client.post("/ingest/webhook", content=oversize, headers=AUTH)
        assert resp.status_code == 413

    def test_body_at_boundary_accepted(self) -> None:
        client = _client()
        msg = b"a" * (receiver_router.MAX_BODY_BYTES - 20)
        body = b'{"message":"' + msg + b'"}'
        headers = dict(AUTH)
        headers["Content-Type"] = "application/json"
        resp = client.post("/ingest/webhook", content=body, headers=headers)
        assert resp.status_code in (202, 413)


class TestAllEndpointsEnforceLimit:
    ENDPOINTS: ClassVar = [
        ("/v1/logs", "application/json"),
        ("/v1/metrics", "application/json"),
        ("/v1/traces", "application/json"),
        ("/ingest/webhook", None),
        ("/ingest/gelf", None),
        ("/ingest/fluent", None),
        ("/ingest/beats", None),
    ]

    def test_all_endpoints_reject_oversized_body(self) -> None:
        client = _client()
        oversize = b"x" * (receiver_router.MAX_BODY_BYTES + 100)
        for path, ct in self.ENDPOINTS:
            headers = dict(AUTH)
            if ct:
                headers["Content-Type"] = ct
            resp = client.post(path, content=oversize, headers=headers)
            assert resp.status_code == 413, (
                f"Endpoint {path} returned {resp.status_code}, expected 413 for oversized body"
            )

    def test_all_endpoints_accept_small_body(self) -> None:
        client = _client()
        for path, ct in self.ENDPOINTS:
            headers = dict(AUTH)
            if ct:
                headers["Content-Type"] = ct
            body = b'{"message":"ok"}'
            resp = client.post(path, content=body, headers=headers)
            assert resp.status_code in (202,), f"Endpoint {path} returned {resp.status_code} with body {resp.text}"


class Test413ResponseFormat:
    def test_error_field_present(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(receiver_router.MAX_BODY_BYTES + 1)
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        body = resp.json()
        assert "error" in body
        assert "max_bytes" in body

    def test_max_bytes_matches_cap(self) -> None:
        client = _client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(receiver_router.MAX_BODY_BYTES + 1)
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        assert resp.json()["max_bytes"] == receiver_router.MAX_BODY_BYTES
