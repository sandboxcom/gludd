"""Deep tests for the expert collection HTTP router.

Tests the endpoint handlers registered in
:func:`general_ludd.routers.experts.register`:

- ``POST /api/materials/select``   — MaterialsSelectRequest
- ``POST /api/chemistry/resolve``  — ChemistryResolveRequest
- ``POST /api/ai_ml/query``        — ExpertQueryRequest
- ``POST /api/language/execute``   — LanguageOperationRequest
- ``GET  /api/git_release/assess`` — git_release_assess

Covers request validation, success paths, error handling, and edge cases.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.routers.experts import (
    ChemistryResolveRequest,
    ExpertQueryRequest,
    LanguageOperationRequest,
    MaterialsSelectRequest,
    register,
)

# ---------------------------------------------------------------------------
# LanguageOperationRequest and POST /api/language/execute
# ---------------------------------------------------------------------------


class TestLanguageOperationRequest:
    def test_payload_defaults_to_empty_dict(self) -> None:
        request = LanguageOperationRequest(operation="language_detect")
        assert request.operation == "language_detect"
        assert request.payload == {}

    def test_empty_operation_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LanguageOperationRequest(operation="")

    def test_non_dict_payload_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LanguageOperationRequest(operation="language_detect", payload=[])  # type: ignore[arg-type]


_LANGUAGE_PATH = "general_ludd.language.operations.execute_language_operation"


class TestLanguageEndpoint:
    def test_execute_returns_wrapped_result(self) -> None:
        expected = {"language": "English", "confidence": 1.0}
        with patch(_LANGUAGE_PATH, return_value=expected) as mock_execute:
            client = _make_app()
            response = client.post(
                "/api/language/execute",
                json={"operation": "language_detect", "payload": {"input_text": "hello"}},
            )
        assert response.status_code == 200
        assert response.json() == {"result": expected}
        mock_execute.assert_called_once_with("language_detect", {"input_text": "hello"})

    def test_value_error_is_client_error(self) -> None:
        with patch(_LANGUAGE_PATH, side_effect=ValueError("unknown operation")):
            client = _make_app()
            response = client.post(
                "/api/language/execute",
                json={"operation": "not-real", "payload": {}},
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "unknown operation"

    def test_unexpected_error_is_fail_closed(self) -> None:
        with patch(_LANGUAGE_PATH, side_effect=RuntimeError("sensitive detail")):
            client = _make_app()
            response = client.post(
                "/api/language/execute",
                json={"operation": "language_detect", "payload": {}},
            )
        assert response.status_code == 500
        assert response.json()["detail"] == "language operation failed"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    register(app, {})
    return TestClient(app)


# ---------------------------------------------------------------------------
# MaterialsSelectRequest model validation
# ---------------------------------------------------------------------------


class TestMaterialsSelectRequest:
    def test_empty_body_uses_defaults(self) -> None:
        m = MaterialsSelectRequest()
        assert m.requirements == {}
        assert m.candidates is None

    def test_requirements_populated(self) -> None:
        m = MaterialsSelectRequest(
            requirements={"load": "10kN", "temperature": "300C"},
        )
        assert m.requirements["load"] == "10kN"
        assert m.candidates is None

    def test_candidates_list(self) -> None:
        m = MaterialsSelectRequest(
            candidates=["Ti-6Al-4V", "316L"],
        )
        assert m.candidates == ["Ti-6Al-4V", "316L"]
        assert m.requirements == {}

    def test_non_dict_requirements_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MaterialsSelectRequest(requirements="not-a-dict")  # type: ignore[arg-type]

    def test_empty_candidates_list(self) -> None:
        m = MaterialsSelectRequest(candidates=[])
        assert m.candidates == []


# ---------------------------------------------------------------------------
# POST /api/materials/select
# ---------------------------------------------------------------------------


_MATERIALS_PATH = "general_ludd.materials.select_materials"


class TestMaterialsEndpoint:
    def test_select_returns_dict(self) -> None:
        with patch(_MATERIALS_PATH, return_value={"ranking": [{"material": "Ti-6Al-4V", "score": 0.95}]}):
            client = _make_app()
            resp = client.post("/api/materials/select", json={"requirements": {"load": "10kN"}})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ranking"][0]["material"] == "Ti-6Al-4V"

    def test_select_with_candidates(self) -> None:
        with patch(_MATERIALS_PATH, return_value={"ranking": []}) as mock_select:
            client = _make_app()
            client.post(
                "/api/materials/select",
                json={
                    "requirements": {"load": "10kN"},
                    "candidates": ["Al-6061", "CFRP"],
                },
            )
        mock_select.assert_called_once()
        args, _ = mock_select.call_args
        assert args[0] == {"load": "10kN"}
        assert args[1] == ["Al-6061", "CFRP"]

    def test_select_internal_error_returns_500(self) -> None:
        with patch(_MATERIALS_PATH, side_effect=RuntimeError("simulation crashed")):
            client = _make_app()
            resp = client.post("/api/materials/select", json={})
        assert resp.status_code == 500
        assert "materials select failed" in resp.json()["detail"]

    def test_select_empty_body(self) -> None:
        with patch(_MATERIALS_PATH, return_value={"ranking": []}) as mock:
            client = _make_app()
            resp = client.post("/api/materials/select", json={})
        assert resp.status_code == 200
        mock.assert_called_once_with({}, None)

    def test_select_extra_fields_ignored(self) -> None:
        with patch(_MATERIALS_PATH, return_value={"ranking": []}) as mock:
            client = _make_app()
            resp = client.post(
                "/api/materials/select",
                json={"requirements": {}, "unknown_field": 42},
            )
        assert resp.status_code == 200
        mock.assert_called_once_with({}, None)


# ---------------------------------------------------------------------------
# ChemistryResolveRequest model validation
# ---------------------------------------------------------------------------


class TestChemistryResolveRequest:
    def test_empty_body_uses_defaults(self) -> None:
        c = ChemistryResolveRequest()
        assert c.request == {}

    def test_request_populated(self) -> None:
        c = ChemistryResolveRequest(
            request={"task": "identity", "entities": ["water"]},
        )
        assert c.request["task"] == "identity"
        assert c.request["entities"] == ["water"]

    def test_non_dict_request_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChemistryResolveRequest(request="not-a-dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# POST /api/chemistry/resolve
# ---------------------------------------------------------------------------


_CHEM_PATH = "general_ludd.chemistry.route_chemistry_task"


class TestChemistryEndpoint:
    def test_resolve_returns_dict(self) -> None:
        with patch(_CHEM_PATH, return_value={"risk_tier": "moderate", "workflow": "standard"}):
            client = _make_app()
            resp = client.post(
                "/api/chemistry/resolve",
                json={"request": {"task": "identity", "entities": ["water"]}},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["risk_tier"] == "moderate"
        assert body["workflow"] == "standard"

    def test_resolve_empty_body(self) -> None:
        with patch(_CHEM_PATH, return_value={"status": "ok"}) as mock:
            client = _make_app()
            resp = client.post("/api/chemistry/resolve", json={})
        assert resp.status_code == 200
        mock.assert_called_once_with({})

    def test_resolve_internal_error_returns_500(self) -> None:
        with patch(_CHEM_PATH, side_effect=ValueError("unknown compound")):
            client = _make_app()
            resp = client.post("/api/chemistry/resolve", json={})
        assert resp.status_code == 500
        assert "chemistry resolve failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# ExpertQueryRequest model validation (POST /api/ai_ml/query)
# ---------------------------------------------------------------------------


class TestExpertQueryRequest:
    def test_minimal_valid(self) -> None:
        q = ExpertQueryRequest(request_id="r1", tenant_id="t1", query="what is ML?")
        assert q.request_id == "r1"
        assert q.tenant_id == "t1"
        assert q.query == "what is ML?"
        assert q.task == "question"
        assert q.offline is False
        assert q.deadline_s == 300
        assert q.budget_usd == 0.0

    def test_missing_request_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(tenant_id="t1", query="q")  # type: ignore[arg-type]

    def test_empty_request_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(request_id="", tenant_id="t1", query="q")

    def test_empty_tenant_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(request_id="r1", tenant_id="", query="q")  # type: ignore[arg-type]

    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(request_id="r1", tenant_id="t1", query="")

    def test_negative_deadline_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(request_id="r1", tenant_id="t1", query="q", deadline_s=0)

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExpertQueryRequest(request_id="r1", tenant_id="t1", query="q", budget_usd=-1.0)

    def test_full_fields(self) -> None:
        q = ExpertQueryRequest(
            request_id="r-full",
            tenant_id="t-full",
            task="distill",
            query="distill model X",
            approval_token="tok-abc",
            offline=True,
            deadline_s=120,
            budget_usd=5.0,
        )
        assert q.approval_token == "tok-abc"
        assert q.offline is True
        assert q.deadline_s == 120
        assert q.budget_usd == 5.0

    def test_deadline_boundary_one(self) -> None:
        q = ExpertQueryRequest(request_id="r1", tenant_id="t1", query="q", deadline_s=1)
        assert q.deadline_s == 1

    def test_budget_boundary_zero(self) -> None:
        q = ExpertQueryRequest(request_id="r1", tenant_id="t1", query="q", budget_usd=0.0)
        assert q.budget_usd == 0.0


# ---------------------------------------------------------------------------
# POST /api/ai_ml/query
# ---------------------------------------------------------------------------


_AIML_ROUTER_PATH = "general_ludd.ai_ml.router.ExpertRouter"
_AIML_TASK_PATH = "general_ludd.ai_ml.schemas.ExpertTask"


class TestAiMlEndpoint:
    def test_query_returns_decision(self) -> None:
        mock_decision = MagicMock()
        mock_decision.request_id = "r1"
        mock_decision.matched_roles = {"chemist", "safety"}
        mock_decision.refusal_reason = None

        with patch(_AIML_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.route.return_value = mock_decision

            client = _make_app()
            resp = client.post(
                "/api/ai_ml/query",
                json={"request_id": "r1", "tenant_id": "t1", "query": "what is ML?"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "r1"
        assert set(body["matched_roles"]) == {"chemist", "safety"}
        assert body["refusal_reason"] is None

    def test_query_invalid_task_returns_422(self) -> None:
        with patch(_AIML_TASK_PATH, side_effect=ValueError("not a valid ExpertTask")):
            client = _make_app()
            resp = client.post(
                "/api/ai_ml/query",
                json={
                    "request_id": "r1",
                    "tenant_id": "t1",
                    "query": "do something",
                    "task": "INVALID_TASK",
                },
            )
        assert resp.status_code == 422
        assert "invalid task" in resp.json()["detail"].lower()

    def test_query_internal_error_returns_500(self) -> None:
        with patch(_AIML_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.route.side_effect = RuntimeError("router crashed")

            client = _make_app()
            resp = client.post(
                "/api/ai_ml/query",
                json={"request_id": "r1", "tenant_id": "t1", "query": "q"},
            )
        assert resp.status_code == 500
        assert "ai_ml query failed" in resp.json()["detail"]

    def test_query_with_refusal_reason(self) -> None:
        mock_decision = MagicMock()
        mock_decision.request_id = "r-refused"
        mock_decision.matched_roles = set()
        mock_decision.refusal_reason = "budget exceeded"

        with patch(_AIML_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.route.return_value = mock_decision

            client = _make_app()
            resp = client.post(
                "/api/ai_ml/query",
                json={
                    "request_id": "r-refused",
                    "tenant_id": "t1",
                    "query": "expensive operation",
                    "budget_usd": 0.01,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["refusal_reason"] == "budget exceeded"
        assert body["matched_roles"] == []

    def test_query_minimal_body(self) -> None:
        mock_decision = MagicMock()
        mock_decision.request_id = "r-min"
        mock_decision.matched_roles = set()
        mock_decision.refusal_reason = "no role matched"

        with patch(_AIML_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.route.return_value = mock_decision

            client = _make_app()
            resp = client.post(
                "/api/ai_ml/query",
                json={"request_id": "r-min", "tenant_id": "t-min", "query": "hi"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "r-min"

    def test_query_missing_request_id_400(self) -> None:
        client = _make_app()
        resp = client.post("/api/ai_ml/query", json={"tenant_id": "t1", "query": "q"})
        assert resp.status_code == 422

    def test_query_empty_request_id_400(self) -> None:
        client = _make_app()
        resp = client.post(
            "/api/ai_ml/query",
            json={"request_id": "", "tenant_id": "t1", "query": "q"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/git_release/assess
# ---------------------------------------------------------------------------


_GRC_EVIDENCE_PATH = "general_ludd.git_release.collect_repo_evidence"


class TestGitReleaseEndpoint:
    def test_assess_returns_evidence_for_valid_repo(self) -> None:
        evidence = MagicMock()
        evidence.path = "/tmp/fake-repo"
        evidence.head_sha = "a" * 40
        evidence.branch = "main"
        evidence.is_dirty = False
        evidence.is_detached = False

        with patch(_GRC_EVIDENCE_PATH, return_value=evidence):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/tmp/fake-repo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "/tmp/fake-repo"
        assert body["head_sha"] == "a" * 40
        assert body["branch"] == "main"
        assert body["is_dirty"] is False
        assert body["is_detached"] is False

    def test_assess_missing_path_returns_422(self) -> None:
        client = _make_app()
        resp = client.get("/api/git_release/assess")
        assert resp.status_code == 422

    def test_assess_file_not_found_returns_404(self) -> None:
        with patch(_GRC_EVIDENCE_PATH, side_effect=FileNotFoundError("no such repo")):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/nonexistent"})
        assert resp.status_code == 404
        assert "no such repo" in resp.json()["detail"]

    def test_assess_not_a_directory_returns_422(self) -> None:
        with patch(_GRC_EVIDENCE_PATH, side_effect=NotADirectoryError("not a git repo")):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/tmp/file.txt"})
        assert resp.status_code == 422
        assert "not a git repo" in resp.json()["detail"]

    def test_assess_runtime_error_returns_422(self) -> None:
        with patch(_GRC_EVIDENCE_PATH, side_effect=RuntimeError("git is broken")):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/tmp/repo"})
        assert resp.status_code == 422
        assert "git is broken" in resp.json()["detail"]

    def test_assess_internal_error_returns_500(self) -> None:
        with patch(_GRC_EVIDENCE_PATH, side_effect=OSError("disk full")):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/tmp/repo"})
        assert resp.status_code == 500
        assert "repo assess failed" in resp.json()["detail"]

    def test_assess_dirty_and_detached_repo(self) -> None:
        evidence = MagicMock()
        evidence.path = "/tmp/dirty-repo"
        evidence.head_sha = "b" * 40
        evidence.branch = "HEAD"
        evidence.is_dirty = True
        evidence.is_detached = True

        with patch(_GRC_EVIDENCE_PATH, return_value=evidence):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": "/tmp/dirty-repo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_dirty"] is True
        assert body["is_detached"] is True

    def test_assess_empty_path(self) -> None:
        with patch(_GRC_EVIDENCE_PATH, side_effect=FileNotFoundError("")):
            client = _make_app()
            resp = client.get("/api/git_release/assess", params={"path": ""})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# register() wiring — all routes reachable
# ---------------------------------------------------------------------------


class TestRegisterWiring:
    def test_all_routes_registered(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        routes = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/materials/select" in routes
        assert "/api/chemistry/resolve" in routes
        assert "/api/ai_ml/query" in routes
        assert "/api/language/execute" in routes
        assert "/api/git_release/assess" in routes

    def test_materials_select_method_is_post(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/api/materials/select":  # type: ignore[union-attr]
                assert "POST" in r.methods  # type: ignore[union-attr]
                break
        else:
            pytest.fail("route not found")

    def test_chemistry_resolve_method_is_post(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/api/chemistry/resolve":  # type: ignore[union-attr]
                assert "POST" in r.methods  # type: ignore[union-attr]
                break
        else:
            pytest.fail("route not found")

    def test_ai_ml_query_method_is_post(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/api/ai_ml/query":  # type: ignore[union-attr]
                assert "POST" in r.methods  # type: ignore[union-attr]
                break
        else:
            pytest.fail("route not found")

    def test_language_execute_method_is_post(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/api/language/execute":  # type: ignore[union-attr]
                assert "POST" in route.methods  # type: ignore[union-attr]
                break
        else:
            pytest.fail("route not found")

    def test_git_release_assess_method_is_get(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if hasattr(r, "path") and r.path == "/api/git_release/assess":  # type: ignore[union-attr]
                assert "GET" in r.methods  # type: ignore[union-attr]
                break
        else:
            pytest.fail("route not found")
