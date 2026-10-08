"""Behavioral endpoint tests for the domain-expert HTTP router."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.ai_ml.schemas import ExpertRequest
from general_ludd.routers.experts import (
    ChemistryRequestError,
    _dispatch_chemistry,
    register,
)


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


def test_materials_resolve_is_bounded_and_idempotent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.materials.operations as operations

    calls: list[tuple[str, dict[str, object]]] = []

    def dispatch(operation: str, request: dict[str, object]) -> dict[str, object]:
        calls.append((operation, request))
        return {"state": "candidate", "operation": operation}

    monkeypatch.setattr(operations, "dispatch_materials_operation", dispatch)
    body = {
        "operation": "machining_plan",
        "request": {"material_id": "abs", "process": "milling"},
        "timeout_seconds": 2.0,
        "idempotency_key": "materials-fixture",
    }

    first = client.post("/api/materials/resolve", json=body)
    replay = client.post("/api/materials/resolve", json=body)

    assert first.status_code == 200
    assert first.json() == {"state": "candidate", "operation": "machining_plan"}
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert calls == [("machining_plan", {"material_id": "abs", "process": "milling"})]


def test_materials_resolve_rejects_invalid_operation_input(client: TestClient) -> None:
    response = client.post(
        "/api/materials/resolve",
        json={"operation": "machining_plan", "request": {}},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "material_id must be a non-empty string of at most 256 characters"}


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


@pytest.mark.parametrize(
    ("operation", "payload", "expected_key"),
    [
        ("molar_mass", {"formula": "H2O"}, "value"),
        ("moles", {"mass_g": 18.0, "formula": "H2O"}, "value"),
        ("dilution", {"c1": 1.0, "v1": 2.0, "c2": 0.5, "v2": None}, "v2"),
        ("yield", {"actual_g": 8.0, "theoretical_g": 10.0}, "value"),
    ],
)
def test_chemistry_numeric_dispatches_use_typed_domain_functions(
    operation: str,
    payload: dict[str, object],
    expected_key: str,
) -> None:
    assert expected_key in _dispatch_chemistry(operation, payload)


@pytest.mark.parametrize(
    ("operation", "function_name"),
    [
        ("identity", "resolve_identity"),
        ("reaction", "analyze_reaction"),
        ("hazard", "screen_hazards"),
    ],
)
def test_chemistry_object_dispatches_forward_exact_request(
    operation: str,
    function_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.chemistry as chemistry

    observed: list[dict[str, object]] = []
    monkeypatch.setattr(
        chemistry,
        function_name,
        lambda request: observed.append(request) or {"kind": operation},
    )

    assert _dispatch_chemistry(operation, {"sample": "a"}) == {"kind": operation}
    assert observed == [{"sample": "a"}]


def test_chemistry_dispatch_fails_closed_for_bad_numeric_or_operation() -> None:
    with pytest.raises(ChemistryRequestError, match="invalid chemistry"):
        _dispatch_chemistry("moles", {"mass_g": "secret", "formula": "H2O"})
    with pytest.raises(ChemistryRequestError, match="unsupported"):
        _dispatch_chemistry("unknown", {})


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


def test_language_execute_forwards_typed_operation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.language.operations as language_operations

    monkeypatch.setattr(
        language_operations,
        "execute_language_operation",
        lambda operation, payload: {"operation": operation, "payload": payload},
    )

    response = client.post(
        "/api/language/execute",
        json={"operation": "inspect", "payload": {"text": "hello"}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "result": {"operation": "inspect", "payload": {"text": "hello"}}
    }


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("invalid language request"), 422, "invalid language request"),
        (RuntimeError("secret"), 500, "language operation failed"),
    ],
)
def test_language_execute_maps_failures(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    detail: str,
) -> None:
    import general_ludd.language.operations as language_operations

    def fail(_operation: str, _payload: dict[str, object]) -> None:
        raise error

    monkeypatch.setattr(language_operations, "execute_language_operation", fail)
    response = client.post(
        "/api/language/execute",
        json={"operation": "inspect", "payload": {}},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert "secret" not in response.text


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


def test_git_release_resolve_is_bounded_and_idempotent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import general_ludd.git_release.operations as operations

    calls: list[tuple[str, dict[str, object]]] = []

    def dispatch(operation: str, request: dict[str, object]) -> dict[str, object]:
        calls.append((operation, request))
        return {"state": "proposal", "operation": operation}

    monkeypatch.setattr(operations, "dispatch_git_release_operation", dispatch)
    body = {
        "operation": "release_plan",
        "request": {"path": "/repo"},
        "timeout_seconds": 2.0,
        "idempotency_key": "git-release-fixture",
    }

    first = client.post("/api/git_release/resolve", json=body)
    replay = client.post("/api/git_release/resolve", json=body)

    assert first.status_code == 200
    assert first.json() == {"state": "proposal", "operation": "release_plan"}
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert calls == [("release_plan", {"path": "/repo"})]


def test_git_release_resolve_rejects_invalid_input(client: TestClient) -> None:
    response = client.post(
        "/api/git_release/resolve",
        json={"operation": "release_plan", "request": {}},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "path must be a non-empty string"}
