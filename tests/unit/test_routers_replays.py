"""Versioned replay router contracts and legacy-response compatibility."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from general_ludd.replay.service import (
    ReplayAccessDeniedError,
    ReplayAttachmentMetadata,
    ReplayCatalogError,
    ReplayCursorError,
    ReplayDetail,
    ReplayEventMetadata,
    ReplayExportError,
    ReplayManifestMetadata,
    ReplayNotFoundError,
    ReplayPage,
    ReplaySummary,
    ReplayVerification,
)
from general_ludd.routers.replays import register

_NOW = datetime(2026, 10, 6, 3, tzinfo=UTC)
_VERIFIED = ReplayVerification(
    valid=True,
    complete=True,
    status="completed",
    event_count=1,
    integrity="signed",
    signing_key_id="replay-integrity-v1",
    reason="verified",
)


class _Recorder:
    def __init__(self, run_ids: list[str]) -> None:
        self._run_ids = run_ids

    def list_runs(self) -> list[str]:
        return list(self._run_ids)


class _FakeReplayService:
    """Small policy-aware service double that records calls and audit actions."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.audits: list[tuple[str, str | None]] = []

    @staticmethod
    def _raise_for(run_id: str) -> None:
        if run_id in {"missing", "cross-project"}:
            raise ReplayNotFoundError("private existence detail")
        if run_id == "unauthorized":
            raise ReplayAccessDeniedError("private capability detail")
        if run_id == "corrupt-export":
            raise ReplayExportError("private integrity detail")
        if run_id == "catalog-down":
            raise ReplayCatalogError("private catalog detail")

    def list_runs(
        self,
        *,
        project_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> ReplayPage:
        self.calls.append(("list", project_id, limit, cursor))
        if cursor == "invalid-cursor":
            raise ReplayCursorError("private cursor detail")
        if cursor == "unauthorized-cursor":
            raise ReplayAccessDeniedError("private capability detail")
        if cursor == "invalid-value":
            raise ValueError("private validation detail")
        if cursor == "catalog-unavailable":
            raise ReplayCatalogError("private catalog detail")
        if cursor == "unexpected-failure":
            raise RuntimeError("private implementation detail")
        self.audits.append(("list", project_id))
        return ReplayPage(
            items=(
                ReplaySummary(
                    run_id="run-a",
                    project_id=project_id,
                    created_at=_NOW,
                    verification=_VERIFIED,
                ),
            ),
            next_cursor="opaque-next-page",
        )

    def show(self, run_id: str, *, project_id: str | None) -> ReplayDetail:
        self.calls.append(("show", run_id, project_id))
        self._raise_for(run_id)
        self.audits.append(("show", project_id))
        return ReplayDetail(
            run_id=run_id,
            project_id=project_id,
            verification=_VERIFIED,
            manifest=ReplayManifestMetadata(
                run_id=run_id,
                parent_run_id=None,
                operation="record",
                created_at=_NOW,
                finalized_at=_NOW,
                status="completed",
                project_id=project_id,
                event_count=1,
                events_sha256="sha256:" + "1" * 64,
                source_repository_sha256="sha256:" + "2" * 64,
                source_commit_sha="3" * 40,
                source_tree_sha="4" * 40,
                runtime_config_sha256="sha256:" + "5" * 64,
                model_provider="openai",
                model_profile="default",
                model_name="gpt-6",
                expected_stage_count=2,
                observed_stage_count=2,
                recorder_error_count=0,
                missing_range_count=0,
            ),
            events=(
                ReplayEventMetadata(
                    sequence=0,
                    event_id="event-0",
                    event_type="tool.responded",
                    occurred_at=_NOW,
                    recorded_at=_NOW,
                    project_id=project_id,
                    digest="sha256:" + "6" * 64,
                    redaction_count=1,
                ),
            ),
            attachments=(
                ReplayAttachmentMetadata(
                    digest="sha256:" + "7" * 64,
                    original_bytes=1024,
                    stored_bytes=128,
                    media_type="application/octet-stream",
                    encoding="gzip",
                    redaction_count=2,
                    truncated=True,
                ),
            ),
        )

    def verify(self, run_id: str, *, project_id: str | None) -> ReplayVerification:
        self.calls.append(("verify", run_id, project_id))
        self._raise_for(run_id)
        self.audits.append(("verify", project_id))
        return _VERIFIED

    def stream_export(
        self,
        run_id: str,
        *,
        project_id: str | None,
        chunk_size: int = 65_536,
    ) -> Iterator[bytes]:
        self.calls.append(("export", run_id, project_id, chunk_size))
        self._raise_for(run_id)

        def chunks() -> Iterator[bytes]:
            self.audits.append(("export", project_id))
            yield b"PK\x03\x04first"
            yield b"-second"

        return chunks()


def _app(
    service: _FakeReplayService | None,
    *,
    project_id: object | None = "project-a",
    recorder: _Recorder | None = None,
) -> FastAPI:
    app = FastAPI()
    if service is not None:
        app.state._replay_service = service
    if recorder is not None:
        app.state._run_recorder = recorder
    register(app, {})

    @app.middleware("http")
    async def daemon_auth_scope(request: Request, call_next: Any) -> Any:
        if project_id is not None:
            request.state.project_id = project_id
        request.state.auth_spec = object()
        return await call_next(request)

    return app


def test_legacy_list_response_remains_byte_shape_compatible() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service, recorder=_Recorder(["run-a", "run-b"]))) as client:
        response = client.get("/api/replays")
    assert response.status_code == 200
    assert response.content == b'["run-a","run-b"]'
    assert service.calls == []

    with TestClient(_app(service)) as client:
        empty = client.get("/api/replays")
    assert empty.content == b"[]"


def test_v1_list_is_bounded_and_uses_auth_derived_project_scope() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service, project_id="project-a")) as client:
        response = client.get(
            "/api/v1/replays",
            params={
                "project_id": "attacker-project",
                "limit": "200",
                "cursor": "opaque-current-page",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] == "opaque-next-page"
    assert body["items"][0]["run_id"] == "run-a"
    assert body["items"][0]["project_id"] == "project-a"
    assert "opaque-current-page" not in response.text
    assert service.calls == [
        ("list", "project-a", 200, "opaque-current-page"),
    ]
    assert service.audits == [("list", "project-a")]


@pytest.mark.parametrize("limit", ["0", "201", "true", "1.5"])
def test_v1_list_rejects_invalid_limits_before_service_call(limit: str) -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        response = client.get("/api/v1/replays", params={"limit": limit})
    assert response.status_code == 422
    assert service.calls == []


def test_v1_list_maps_bad_cursor_to_bounded_content_free_error() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        response = client.get(
            "/api/v1/replays",
            params={"cursor": "invalid-cursor"},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid replay request"}
    assert "private" not in response.text


def test_v1_list_maps_policy_validation_and_backend_failures_content_free() -> None:
    service = _FakeReplayService()
    cases = (
        ("unauthorized-cursor", 404, "replay not found"),
        ("invalid-value", 400, "invalid replay request"),
        ("catalog-unavailable", 503, "replay unavailable"),
        ("unexpected-failure", 503, "replay unavailable"),
    )
    with TestClient(_app(service)) as client:
        responses = [
            client.get("/api/v1/replays", params={"cursor": cursor})
            for cursor, _, _ in cases
        ]
    assert [response.status_code for response in responses] == [
        status for _, status, _ in cases
    ]
    assert [response.json()["detail"] for response in responses] == [
        detail for _, _, detail in cases
    ]
    assert all("private" not in response.text for response in responses)


def test_v1_list_uses_query_scope_only_without_an_authenticated_claim() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service, project_id=None)) as client:
        response = client.get(
            "/api/v1/replays",
            params={"project_id": "query-project"},
        )
    assert response.status_code == 200
    assert service.calls == [("list", "query-project", 50, None)]

    malformed_claim_service = _FakeReplayService()
    with TestClient(_app(malformed_claim_service, project_id=object())) as client:
        malformed = client.get("/api/v1/replays")
    assert malformed.status_code == 404
    assert malformed.json() == {"detail": "replay not found"}
    assert malformed_claim_service.calls == []


def test_v1_show_returns_metadata_without_payload_or_attachment_content() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        response = client.get("/api/v1/replays/run-a")

    assert response.status_code == 200
    body = response.json()
    assert body["manifest"]["run_id"] == "run-a"
    assert body["events"][0]["event_type"] == "tool.responded"
    assert body["attachments"][0]["stored_bytes"] == 128
    assert "payload" not in response.text
    assert "content" not in body["attachments"][0]
    assert service.audits == [("show", "project-a")]


def test_v1_unknown_cross_project_and_unauthorized_runs_are_same_404() -> None:
    service = _FakeReplayService()
    responses = []
    with TestClient(_app(service)) as client:
        for run_id in ("missing", "cross-project", "unauthorized", "unsafe..run"):
            responses.append(client.get(f"/api/v1/replays/{run_id}"))

    assert [response.status_code for response in responses] == [404, 404, 404, 404]
    assert {response.content for response in responses} == {
        b'{"detail":"replay not found"}',
    }
    assert all("private" not in response.text for response in responses)


def test_v1_verify_is_post_only_and_preserves_sanitized_verdict() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        response = client.post("/api/v1/replays/run-a/verify")
        wrong_method = client.get("/api/v1/replays/run-a/verify")

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "complete": True,
        "status": "completed",
        "event_count": 1,
        "integrity": "signed",
        "signing_key_id": "replay-integrity-v1",
        "reason": "verified",
    }
    assert wrong_method.status_code == 405
    assert service.audits == [("verify", "project-a")]


def test_v1_export_streams_fixed_attachment_without_output_path() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        response = client.get("/api/v1/replays/run-a/export")

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04first-second"
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="gludd-replay-run-a.zip"'
    )
    assert service.calls == [("export", "run-a", "project-a", 65_536)]
    assert service.audits == [("export", "project-a")]


def test_v1_export_and_backend_failures_are_content_free() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        unavailable = client.get("/api/v1/replays/corrupt-export/export")
        backend = client.get("/api/v1/replays/catalog-down")

    assert unavailable.status_code == 409
    assert unavailable.json() == {"detail": "replay export unavailable"}
    assert backend.status_code == 503
    assert backend.json() == {"detail": "replay unavailable"}
    assert "private" not in unavailable.text + backend.text


def test_v1_export_hides_unknown_cross_project_and_unauthorized_runs() -> None:
    service = _FakeReplayService()
    with TestClient(_app(service)) as client:
        responses = [
            client.get(f"/api/v1/replays/{run_id}/export")
            for run_id in ("missing", "cross-project", "unauthorized")
        ]
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.content for response in responses} == {
        b'{"detail":"replay not found"}',
    }


def test_v1_routes_fail_closed_when_service_is_not_wired() -> None:
    with TestClient(_app(None)) as client:
        responses = (
            client.get("/api/v1/replays"),
            client.get("/api/v1/replays/run-a"),
            client.post("/api/v1/replays/run-a/verify"),
            client.get("/api/v1/replays/run-a/export"),
        )
    assert [response.status_code for response in responses] == [503, 503, 503, 503]
    assert {response.content for response in responses} == {
        b'{"detail":"replay unavailable"}',
    }


def test_only_read_only_v1_routes_are_registered() -> None:
    app = _app(_FakeReplayService())
    route_methods = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
        if route.path.startswith("/api/v1/replays")
    }
    assert route_methods == {
        ("/api/v1/replays", "GET"),
        ("/api/v1/replays/{run_id}", "GET"),
        ("/api/v1/replays/{run_id}/verify", "POST"),
        ("/api/v1/replays/{run_id}/export", "GET"),
    }
    assert all("simulate" not in path and "reexecute" not in path for path, _ in route_methods)


def test_replay_routes_reuse_the_daemon_nonpublic_auth_boundary() -> None:
    daemon = importlib.import_module("general_ludd.daemon")
    is_public_path = daemon.is_public_path

    assert is_public_path("GET", "/api/replays") is False
    assert is_public_path("GET", "/api/v1/replays") is False
    assert is_public_path("GET", "/api/v1/replays/run-a") is False
    assert is_public_path("POST", "/api/v1/replays/run-a/verify") is False
    assert is_public_path("GET", "/api/v1/replays/run-a/export") is False
