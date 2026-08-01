"""Behavioral endpoint tests for the domain-expert HTTP router."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.ai_ml.schemas import ExpertRequest
from general_ludd.routers.experts import register


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register(app, {})
    return TestClient(app)


def test_materials_select_forwards_typed_body(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.materials as materials

    calls: list[tuple[dict[str, object], list[str] | None]] = []

    def select(requirements: dict[str, object], candidates: list[str] | None) -> dict[str, object]:
        calls.append((requirements, candidates))
        return {"status": "ranked", "count": len(candidates or [])}

    monkeypatch.setattr(materials, "select_materials", select)

    response = client.post(
        "/api/materials/select",
        json={"requirements": {"temperature_c": 400}, "candidates": ["steel", "nickel"]},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ranked", "count": 2}
    assert calls == [({"temperature_c": 400}, ["steel", "nickel"])]


def test_materials_select_uses_safe_defaults(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import general_ludd.materials as materials

    observed: list[tuple[dict[str, object], list[str] | None]] = []

    def select(requirements: dict[str, object], candidates: list[str] | None) -> dict[str, bool]:
        observed.append((requirements, candidates))
        return {"ok": True}

    monkeypatch.setattr(materials, "select_materials", select)

    response = client.post("/api/materials/select", json={})

    assert response.status_code == 200
    assert observed == [({}, None)]


def test_materials_failure_is_redacted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import general_ludd.materials as materials

    def fail(*_args: object) -> dict[str, object]:
        raise RuntimeError("supplier-secret")

    monkeypatch.setattr(materials, "select_materials", fail)

    response = client.post("/api/materials/select", json={})

    assert response.status_code == 500
    assert response.json() == {"detail": "materials select failed"}
    assert "supplier-secret" not in response.text


def test_chemistry_resolve_forwards_request(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import general_ludd.chemistry as chemistry

    requests: list[dict[str, object]] = []

    def resolve(request: dict[str, object]) -> dict[str, str]:
        requests.append(request)
        return {"workflow": "analytical", "risk": "low"}

    monkeypatch.setattr(chemistry, "route_chemistry_task", resolve)

    response = client.post(
        "/api/chemistry/resolve",
        json={"request": {"task": "quantify", "entities": ["sample-a"]}},
    )

    assert response.status_code == 200
    assert response.json() == {"workflow": "analytical", "risk": "low"}
    assert requests == [{"task": "quantify", "entities": ["sample-a"]}]


def test_chemistry_failure_is_redacted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import general_ludd.chemistry as chemistry

    def fail(_request: dict[str, object]) -> dict[str, object]:
        raise ValueError("formula-secret")

    monkeypatch.setattr(chemistry, "route_chemistry_task", fail)

    response = client.post("/api/chemistry/resolve", json={"request": {"task": "resolve"}})

    assert response.status_code == 500
    assert response.json() == {"detail": "chemistry resolve failed"}
    assert "formula-secret" not in response.text


def test_ai_query_builds_constraints_and_serializes_decision(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.ai_ml.router as ai_router

    routed: list[ExpertRequest] = []

    class Router:
        def route(self, request: ExpertRequest) -> SimpleNamespace:
            routed.append(request)
            return SimpleNamespace(
                request_id="request-1",
                matched_roles=("reasoning", "research"),
                refusal_reason=None,
            )

    monkeypatch.setattr(ai_router, "ExpertRouter", Router)

    response = client.post(
        "/api/ai_ml/query",
        json={
            "request_id": "request-1",
            "tenant_id": "tenant-a",
            "task": "question",
            "query": "Which model fits?",
            "approval_token": "approval-1",
            "offline": True,
            "deadline_s": 45,
            "budget_usd": 1.25,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": "request-1",
        "matched_roles": ["reasoning", "research"],
        "refusal_reason": None,
    }
    request = routed[0]
    assert request.request_id == "request-1"
    assert request.constraints.offline is True
    assert request.constraints.deadline_s == 45
    assert request.constraints.budget_usd == 1.25


def test_ai_query_rejects_invalid_task(client: TestClient) -> None:
    response = client.post(
        "/api/ai_ml/query",
        json={
            "request_id": "request-1",
            "tenant_id": "tenant-a",
            "task": "not-a-task",
            "query": "hello",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid task: 'not-a-task'"


def test_ai_query_validates_required_and_bounded_fields(client: TestClient) -> None:
    missing = client.post("/api/ai_ml/query", json={})
    negative = client.post(
        "/api/ai_ml/query",
        json={
            "request_id": "r",
            "tenant_id": "t",
            "query": "q",
            "deadline_s": 0,
            "budget_usd": -1,
        },
    )

    assert missing.status_code == 422
    assert negative.status_code == 422


def test_ai_router_failure_is_redacted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import general_ludd.ai_ml.router as ai_router

    class BrokenRouter:
        def route(self, _request: object) -> None:
            raise RuntimeError("model-routing-secret")

    monkeypatch.setattr(ai_router, "ExpertRouter", BrokenRouter)

    response = client.post(
        "/api/ai_ml/query",
        json={"request_id": "r", "tenant_id": "t", "query": "q"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "ai_ml query failed"}
    assert "model-routing-secret" not in response.text


def test_git_release_assess_serializes_evidence(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.git_release as git_release

    monkeypatch.setattr(
        git_release,
        "collect_repo_evidence",
        lambda path: SimpleNamespace(
            path=path,
            head_sha="abc123f",
            branch="development",
            is_dirty=False,
            is_detached=False,
        ),
    )

    response = client.get("/api/git_release/assess", params={"path": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {
        "path": str(tmp_path),
        "head_sha": "abc123f",
        "branch": "development",
        "is_dirty": False,
        "is_detached": False,
    }


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (FileNotFoundError("missing repo"), 404),
        (NotADirectoryError("not a directory"), 422),
        (RuntimeError("git unavailable"), 422),
    ],
)
def test_git_release_assess_maps_expected_failures(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    import general_ludd.git_release as git_release

    def fail(_path: str) -> None:
        raise error

    monkeypatch.setattr(git_release, "collect_repo_evidence", fail)

    response = client.get("/api/git_release/assess", params={"path": "/repo"})

    assert response.status_code == status
    assert error.args[0] in response.json()["detail"]


def test_git_release_assess_redacts_unexpected_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.git_release as git_release

    def fail(_path: str) -> None:
        raise ValueError("credential-secret")

    monkeypatch.setattr(git_release, "collect_repo_evidence", fail)

    response = client.get("/api/git_release/assess", params={"path": "/repo"})

    assert response.status_code == 500
    assert response.json() == {"detail": "repo assess failed"}
    assert "credential-secret" not in response.text


def test_git_release_assess_requires_path(client: TestClient) -> None:
    assert client.get("/api/git_release/assess").status_code == 422
