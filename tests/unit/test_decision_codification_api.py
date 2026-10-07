"""Content-safe HTTP boundary for offline decision-log analysis."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from general_ludd.daemon import create_daemon_app
from general_ludd.decision_codification.service import (
    AnalysisRejectionReason,
    DecisionAnalysis,
    DecisionAnalysisError,
    DecisionCandidate,
)
from general_ludd.routers.decision_codification import (
    MAX_API_ANALYSIS_BODY_BYTES,
    MAX_API_ANALYSIS_RUN_IDS,
    DecisionAnalysisRequest,
    DecisionAnalysisResponse,
    _parse_request,
    register,
)

_NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
_SHA_A = "sha256:" + "a" * 64
_SHA_B = "sha256:" + "b" * 64
_SHA_C = "sha256:" + "c" * 64
_SHA_D = "sha256:" + "d" * 64
_SHA_E = "sha256:" + "e" * 64
_SHA_F = "sha256:" + "f" * 64
_PSK = "decision-analysis-test-psk"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": "project-a",
        "run_ids": ["run-a", "run-b"],
        "training_recipe_digest": _SHA_A,
        "dependency_lock_digest": _SHA_B,
        "created_at": _NOW.isoformat(),
        "expires_at": (_NOW + timedelta(days=7)).isoformat(),
        "maximum_use_count": 100,
        "estimated_tokens_per_call": 250,
    }
    payload.update(overrides)
    return payload


def _candidate() -> SimpleNamespace:
    return SimpleNamespace(
        cluster_digest=_SHA_C,
        evidence_count=8,
        bundle=SimpleNamespace(candidate_digest=_SHA_D),
        validation_report=SimpleNamespace(report_digest=_SHA_E),
        holdout_report=SimpleNamespace(report_digest=_SHA_F),
    )


class _Adapter:
    project_id = "project-a"

    def __init__(self, result: DecisionAnalysis | Exception) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def analyze(self, run_ids: tuple[str, ...], **kwargs: object) -> DecisionAnalysis:
        self.calls.append((tuple(run_ids), kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _analysis(
    *,
    candidates: tuple[object, ...] = (),
    rejections: tuple[tuple[AnalysisRejectionReason, int], ...] = (),
) -> DecisionAnalysis:
    return DecisionAnalysis(
        bundles_read=2,
        events_seen=11,
        events_eligible=8,
        candidates=cast("tuple[DecisionCandidate, ...]", candidates),
        rejection_counts=rejections,
    )


def _router_app(adapter: object | None, *, project_id: object | None = "project-a") -> FastAPI:
    app = FastAPI()
    app.state.decision_codification = adapter
    register(app, {})

    @app.middleware("http")
    async def daemon_scope(request: Request, call_next: Any) -> Any:
        if project_id is not None:
            request.state.project_id = project_id
        return await call_next(request)

    return app


def _raw_request(
    body: bytes,
    *,
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> Request:
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/decision-codification/analyze",
            "headers": list(headers),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_analysis_returns_only_bounded_digests_counts_and_rejection_enums() -> None:
    adapter = _Adapter(
        _analysis(
            candidates=(_candidate(),),
            rejections=((AnalysisRejectionReason.UNVERIFIED_BUNDLE, 2),),
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=_router_app(adapter)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "bundles_read": 2,
        "events_seen": 11,
        "events_eligible": 8,
        "candidate_count": 1,
        "candidates": [
            {
                "cluster_digest": _SHA_C,
                "evidence_count": 8,
                "candidate_digest": _SHA_D,
                "validation_report_digest": _SHA_E,
                "holdout_report_digest": _SHA_F,
            }
        ],
        "rejection_counts": [
            {"reason": "unverified_bundle", "count": 2},
        ],
    }
    assert "run-a" not in response.text
    assert "project-a" not in response.text
    assert "events" not in response.json()["candidates"][0]
    assert adapter.calls == [
        (
            ("run-a", "run-b"),
            {
                "training_recipe_digest": _SHA_A,
                "dependency_lock_digest": _SHA_B,
                "created_at": _NOW,
                "expires_at": _NOW + timedelta(days=7),
                "maximum_use_count": 100,
                "estimated_tokens_per_call": 250,
            },
        )
    ]


@pytest.mark.asyncio
async def test_no_adapter_scope_mismatch_and_malformed_scope_fail_closed() -> None:
    adapter = _Adapter(_analysis())
    cases = (
        (_router_app(None), 503, "decision analysis unavailable"),
        (_router_app(adapter, project_id="project-b"), 404, "decision analysis not found"),
        (_router_app(adapter, project_id=object()), 404, "decision analysis not found"),
    )
    for app, status, detail in cases:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/decision-codification/analyze",
                json=_payload(),
            )
        assert response.status_code == status
        assert response.json() == {"detail": detail}
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_request_bounds_and_forbidden_activation_or_approval_data_are_content_safe() -> None:
    secret = "operator-private-approval-key"
    adapter = _Adapter(_analysis())
    app = _router_app(adapter)
    run_ids = [f"run-{index}" for index in range(MAX_API_ANALYSIS_RUN_IDS + 1)]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        oversized_ids = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(run_ids=run_ids),
        )
        forbidden = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(activate=True, approval_key=secret),
        )
        oversized_body = await client.post(
            "/api/v1/decision-codification/analyze",
            content=(
                b'{"private":"'
                + secret.encode()
                + (b"x" * MAX_API_ANALYSIS_BODY_BYTES)
                + b'"}'
            ),
            headers={"content-type": "application/json"},
        )

    assert oversized_ids.status_code == 422
    assert oversized_ids.json() == {"detail": "invalid decision analysis request"}
    assert forbidden.status_code == 422
    assert forbidden.json() == {"detail": "invalid decision analysis request"}
    assert secret not in forbidden.text
    assert oversized_body.status_code == 413
    assert oversized_body.json() == {"detail": "decision analysis request too large"}
    assert secret not in oversized_body.text
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_malformed_time_identity_and_empty_requests_are_content_safe() -> None:
    adapter = _Adapter(_analysis())
    app = _router_app(adapter, project_id=None)
    invalid_payloads = (
        _payload(run_ids=["run-a", "run-a"]),
        _payload(created_at="not-a-timestamp"),
        _payload(created_at="2026-10-06T12:00:00"),
        _payload(expires_at=(_NOW - timedelta(seconds=1)).isoformat()),
        _payload(project_id="project-b"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = [
            await client.post(
                "/api/v1/decision-codification/analyze",
                json=payload,
            )
            for payload in invalid_payloads
        ]
        empty = await client.post(
            "/api/v1/decision-codification/analyze",
            content=b"",
            headers={"content-type": "application/json"},
        )
        malformed_length = await client.post(
            "/api/v1/decision-codification/analyze",
            content=b"{}",
            headers={"content-length": "not-an-integer"},
        )

    assert [response.status_code for response in responses] == [422, 422, 422, 422, 404]
    assert all(
        response.json()["detail"]
        in {"invalid decision analysis request", "decision analysis not found"}
        for response in responses
    )
    assert empty.status_code == 422
    assert empty.json() == {"detail": "invalid decision analysis request"}
    assert malformed_length.status_code == 422
    assert malformed_length.json() == {"detail": "invalid decision analysis request"}
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_request_models_cover_non_json_callers_and_stream_bounds() -> None:
    direct = DecisionAnalysisRequest.model_validate(
        _payload(
            run_ids=("run-direct",),
            created_at=_NOW,
            expires_at=_NOW + timedelta(days=1),
        ),
        strict=True,
    )
    assert direct.run_ids == ("run-direct",)

    for created_at, expires_at in (
        (_NOW.replace(tzinfo=None), _NOW + timedelta(days=1)),
        (_NOW, (_NOW + timedelta(days=1)).replace(tzinfo=None)),
    ):
        with pytest.raises(ValidationError):
            DecisionAnalysisRequest.model_validate(
                _payload(
                    run_ids=("run-direct",),
                    created_at=created_at,
                    expires_at=expires_at,
                ),
                strict=True,
            )

    valid_raw = await _parse_request(
        _raw_request(
            json.dumps(_payload()).encode(),
            headers=((b"content-type", b"application/json"),),
        )
    )
    assert valid_raw.project_id == "project-a"

    for request, expected_status in (
        (
            _raw_request(
                b"{}",
                headers=((b"content-length", b"-1"),),
            ),
            422,
        ),
        (
            _raw_request(b"x" * (MAX_API_ANALYSIS_BODY_BYTES + 1)),
            413,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _parse_request(request)
        assert exc_info.value.status_code == expected_status

    with pytest.raises(ValidationError):
        DecisionAnalysisResponse(
            bundles_read=0,
            events_seen=0,
            events_eligible=0,
            candidate_count=1,
            candidates=(),
            rejection_counts=(),
        )


@pytest.mark.asyncio
async def test_analysis_errors_and_rejected_evidence_never_leak_backend_content() -> None:
    private = "private replay-store path and event body"
    invalid_adapter = _Adapter(DecisionAnalysisError(private))
    unavailable_adapter = _Adapter(RuntimeError(private))
    rejected_adapter = _Adapter(
        _analysis(
            rejections=(
                (AnalysisRejectionReason.PROJECT_MISMATCH, 1),
                (AnalysisRejectionReason.UNVERIFIED_BUNDLE, 1),
            )
        )
    )
    cases = (
        (invalid_adapter, 422, "invalid decision analysis request"),
        (unavailable_adapter, 503, "decision analysis unavailable"),
    )
    for adapter, status, detail in cases:
        async with AsyncClient(
            transport=ASGITransport(app=_router_app(adapter)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/decision-codification/analyze",
                json=_payload(),
            )
        assert response.status_code == status
        assert response.json() == {"detail": detail}
        assert private not in response.text

    async with AsyncClient(
        transport=ASGITransport(app=_router_app(rejected_adapter)),
        base_url="http://test",
    ) as client:
        rejected = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
        )
    assert rejected.status_code == 200
    assert rejected.json()["candidate_count"] == 0
    assert rejected.json()["rejection_counts"] == [
        {"reason": "project_mismatch", "count": 1},
        {"reason": "unverified_bundle", "count": 1},
    ]
    assert private not in rejected.text

    too_many = _Adapter(
        _analysis(candidates=tuple(_candidate() for _ in range(MAX_API_ANALYSIS_RUN_IDS)))
    )
    missing_scope = SimpleNamespace(project_id=None, analyze=too_many.analyze)
    for bounded_adapter in (too_many, missing_scope):
        async with AsyncClient(
            transport=ASGITransport(app=_router_app(bounded_adapter)),
            base_url="http://test",
        ) as client:
            bounded = await client.post(
                "/api/v1/decision-codification/analyze",
                json=_payload(),
            )
        assert bounded.status_code == 503
        assert bounded.json() == {"detail": "decision analysis unavailable"}


@pytest.mark.asyncio
async def test_daemon_auth_runs_before_analysis_and_claim_scope_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_AUTH_PSK", _PSK)
    monkeypatch.delenv("GLUDD_PSK", raising=False)
    monkeypatch.delenv("GLUDD_PSK_DISABLE", raising=False)
    monkeypatch.delenv("GLUDD_ALLOW_NO_AUTH", raising=False)
    monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)
    adapter = _Adapter(_analysis())
    app = create_daemon_app(tick_interval=300.0)
    app.state.decision_codification = adapter

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
        )
        wrong = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
            headers={"Authorization": "Bearer wrong"},
        )
        wrong_scope = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
            headers={"Authorization": f"Bearer project-b:{_PSK}"},
        )
        accepted = await client.post(
            "/api/v1/decision-codification/analyze",
            json=_payload(),
            headers={"Authorization": f"Bearer project-a:{_PSK}"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert wrong_scope.status_code == 404
    assert wrong_scope.json() == {"detail": "decision analysis not found"}
    assert accepted.status_code == 200
    assert len(adapter.calls) == 1
