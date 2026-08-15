"""Router tests for the push-side receiver: auth, size cap, rate limit, backpressure.

Follows the router test convention (bare FastAPI app + register(app, {}) + TestClient).
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.receiver import router as receiver_router
from general_ludd.receiver.buffer import OverflowPolicy, ReceiverBuffer

TOKEN = "test-ingest-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _set_ingest_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(receiver_router.INGEST_TOKEN_ENV, TOKEN)


def _make_client(
    *,
    buffer: ReceiverBuffer | None = None,
    rate_per_sec: float = 1000.0,
    rate_burst: float = 1000.0,
) -> tuple[TestClient, dict]:
    app = FastAPI()
    state: dict = {}
    if buffer is not None:
        state["receiver_buffer"] = buffer
    receiver_router.register(
        app, state, rate_per_sec=rate_per_sec, rate_burst=rate_burst
    )
    return TestClient(app, raise_server_exceptions=False), state


def _webhook_body() -> bytes:
    return json.dumps({"message": "hi", "level": "error", "host": "web-01"}).encode()


class TestAuth:
    def test_missing_token_is_401(self) -> None:
        client, _ = _make_client()
        resp = client.post("/ingest/webhook", content=_webhook_body())
        assert resp.status_code == 401

    def test_wrong_token_is_401(self) -> None:
        client, _ = _make_client()
        resp = client.post(
            "/ingest/webhook",
            content=_webhook_body(),
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_valid_token_accepted(self) -> None:
        client, state = _make_client()
        resp = client.post("/ingest/webhook", content=_webhook_body(), headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1
        # The record landed in the buffer, normalized.
        rec = state["receiver_buffer"].drain()[0]
        assert rec["join"]["host"] == "web-01"
        assert rec["join"]["severity"] == "error"

    def test_x_ingest_token_header_accepted(self) -> None:
        client, _ = _make_client()
        resp = client.post(
            "/ingest/webhook",
            content=_webhook_body(),
            headers={"X-Ingest-Token": TOKEN},
        )
        assert resp.status_code == 202

    def test_fail_closed_when_no_token_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(receiver_router.INGEST_TOKEN_ENV, raising=False)
        client, _ = _make_client()
        resp = client.post("/ingest/webhook", content=_webhook_body(), headers=AUTH)
        assert resp.status_code == 503
        assert resp.json()["error"] == "ingest_disabled"

    def test_blank_token_env_is_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(receiver_router.INGEST_TOKEN_ENV, "   ")
        client, _ = _make_client()
        resp = client.post("/ingest/webhook", content=_webhook_body(), headers=AUTH)
        assert resp.status_code == 503

    def test_admin_psk_does_not_grant_ingest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Setting GLUDD_AUTH_PSK (admin) must NOT authenticate against the ingest token.
        monkeypatch.setenv("GLUDD_AUTH_PSK", "admin-psk-value")
        client, _ = _make_client()
        resp = client.post(
            "/ingest/webhook",
            content=_webhook_body(),
            headers={"Authorization": "Bearer admin-psk-value"},
        )
        assert resp.status_code == 401


class TestSizeCap:
    def test_content_length_over_cap_is_413(self) -> None:
        client, _ = _make_client()
        headers = dict(AUTH)
        headers["Content-Length"] = str(receiver_router.MAX_BODY_BYTES + 1)
        resp = client.post(
            "/ingest/webhook",
            content=b"{}",
            headers=headers,
        )
        assert resp.status_code == 413

    def test_streamed_body_over_cap_is_413(self) -> None:
        client, _ = _make_client()
        # A body that exceeds the cap (no Content-Length override).
        big = b"[" + b'{"message":"x"},' * 1 + b"]"
        # Force oversize via a real large payload.
        oversize = b'{"message":"' + b"a" * (receiver_router.MAX_BODY_BYTES + 10) + b'"}'
        resp = client.post("/ingest/webhook", content=oversize, headers=AUTH)
        assert resp.status_code == 413
        assert big  # silence unused

    def test_bad_content_length_is_400(self) -> None:
        client, _ = _make_client()
        headers = dict(AUTH)
        headers["Content-Length"] = "not-a-number"
        # Starlette would normally reject, so send via a value the app parses.
        resp = client.post("/ingest/webhook", content=b"{}", headers=headers)
        # Either the app's 400 or transport-level rejection; assert not accepted.
        assert resp.status_code in (400, 413, 422)


class TestRateLimit:
    def test_rate_limit_returns_429(self) -> None:
        client, _ = _make_client(rate_per_sec=1.0, rate_burst=2.0)
        codes = [
            client.post("/ingest/webhook", content=_webhook_body(), headers=AUTH).status_code
            for _ in range(6)
        ]
        # The burst of 2 is accepted, the rest are rate-limited.
        assert 202 in codes
        assert 429 in codes


class TestBackpressure:
    def test_full_reject_buffer_returns_503(self) -> None:
        buf = ReceiverBuffer(maxlen=1, overflow=OverflowPolicy.REJECT)
        buf.offer({"message": "prefill"})  # fill it
        client, _ = _make_client(buffer=buf)
        resp = client.post("/ingest/webhook", content=_webhook_body(), headers=AUTH)
        assert resp.status_code == 503
        assert resp.json()["error"] == "buffer_full"


class TestOTLPEndpoints:
    def test_otlp_logs_endpoint(self) -> None:
        client, _state = _make_client()
        doc = {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {"body": {"stringValue": "hello"}, "severityText": "INFO"}
                            ]
                        }
                    ]
                }
            ]
        }
        resp = client.post(
            "/v1/logs",
            content=json.dumps(doc).encode(),
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1

    def test_otlp_metrics_endpoint(self) -> None:
        client, _ = _make_client()
        doc = {
            "resourceMetrics": [
                {
                    "scopeMetrics": [
                        {"metrics": [{"name": "m", "gauge": {"dataPoints": [{"asDouble": 1.0}]}}]}
                    ]
                }
            ]
        }
        resp = client.post("/v1/metrics", content=json.dumps(doc).encode(), headers=AUTH)
        assert resp.status_code == 202

    def test_otlp_traces_endpoint(self) -> None:
        client, _ = _make_client()
        doc = {
            "resourceSpans": [
                {"scopeSpans": [{"spans": [{"name": "s", "traceId": "t"}]}]}
            ]
        }
        resp = client.post("/v1/traces", content=json.dumps(doc).encode(), headers=AUTH)
        assert resp.status_code == 202

    def test_malformed_otlp_accepts_zero(self) -> None:
        client, _ = _make_client()
        resp = client.post("/v1/logs", content=b"not json", headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 0


class TestShipperEndpoints:
    def test_gelf_endpoint(self) -> None:
        client, _ = _make_client()
        body = json.dumps({"version": "1.1", "host": "h", "short_message": "boom"}).encode()
        resp = client.post("/ingest/gelf", content=body, headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1

    def test_fluent_endpoint(self) -> None:
        client, _ = _make_client()
        body = json.dumps(["mytag", [[1700000000, {"message": "hi"}]]]).encode()
        resp = client.post("/ingest/fluent", content=body, headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1

    def test_beats_endpoint(self) -> None:
        client, _ = _make_client()
        body = json.dumps([{"@timestamp": "t", "message": "m", "host": {"name": "h"}}]).encode()
        resp = client.post("/ingest/beats", content=body, headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["accepted"] == 1
