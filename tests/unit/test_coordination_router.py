"""Unit tests for the coordination router (TDD — written before the implementation).

Spins up a tiny FastAPI TestClient, calls coordination.register(app, {}),
and exercises all endpoints.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import coordination

# ---------------------------------------------------------------------------
# Fixture: bare app with coordination router registered
# ---------------------------------------------------------------------------

@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    coordination.register(_app, {})
    return _app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/coordination/claim
# ---------------------------------------------------------------------------

class TestClaimEndpoint:
    def test_claim_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/api/coordination/claim",
            json={"worker_id": "w1", "files": ["a.py", "b.py"]},
        )
        assert resp.status_code == 201

    def test_claim_response_shape(self, client: TestClient) -> None:
        resp = client.post(
            "/api/coordination/claim",
            json={"worker_id": "w1", "files": ["a.py"]},
        )
        data = resp.json()
        assert data["worker_id"] == "w1"
        assert "a.py" in data["files"]

    def test_claim_idempotent(self, client: TestClient) -> None:
        payload = {"worker_id": "w1", "files": ["a.py"]}
        r1 = client.post("/api/coordination/claim", json=payload)
        r2 = client.post("/api/coordination/claim", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201

    def test_claim_missing_worker_id_422(self, client: TestClient) -> None:
        resp = client.post("/api/coordination/claim", json={"files": ["a.py"]})
        assert resp.status_code == 422

    def test_claim_empty_files_list(self, client: TestClient) -> None:
        resp = client.post(
            "/api/coordination/claim",
            json={"worker_id": "w1", "files": []},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# POST /api/coordination/release
# ---------------------------------------------------------------------------

class TestReleaseEndpoint:
    def test_release_claimed_worker(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        resp = client.post("/api/coordination/release", json={"worker_id": "w1"})
        assert resp.status_code == 200
        assert resp.json()["released"] is True

    def test_release_unknown_worker_is_noop(self, client: TestClient) -> None:
        resp = client.post("/api/coordination/release", json={"worker_id": "ghost"})
        assert resp.status_code == 200
        assert resp.json()["released"] is True

    def test_release_clears_claims(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        client.post("/api/coordination/release", json={"worker_id": "w1"})
        data = client.get("/api/coordination/claims").json()
        assert "a.py" not in data["claims"]


# ---------------------------------------------------------------------------
# GET /api/coordination/overlaps
# ---------------------------------------------------------------------------

class TestOverlapsEndpoint:
    def test_overlaps_reports_shared_file(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["shared.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["shared.py"]})
        resp = client.get("/api/coordination/overlaps?worker_id=w1")
        assert resp.status_code == 200
        data = resp.json()
        assert "shared.py" in data["overlaps"]
        assert "w2" in data["overlaps"]["shared.py"]

    def test_overlaps_no_conflict(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["b.py"]})
        resp = client.get("/api/coordination/overlaps?worker_id=w1")
        assert resp.status_code == 200
        assert resp.json()["overlaps"] == {}

    def test_overlaps_should_wait_field(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["f.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["f.py"]})
        data = client.get("/api/coordination/overlaps?worker_id=w1").json()
        assert "w2" in data["should_wait"]

    def test_overlaps_missing_worker_id_422(self, client: TestClient) -> None:
        resp = client.get("/api/coordination/overlaps")
        assert resp.status_code == 422

    def test_overlaps_unknown_worker_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/coordination/overlaps?worker_id=nobody")
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlaps"] == {}
        assert data["should_wait"] == []


# ---------------------------------------------------------------------------
# GET /api/coordination/claims
# ---------------------------------------------------------------------------

class TestClaimsEndpoint:
    def test_claims_empty_initially(self, client: TestClient) -> None:
        resp = client.get("/api/coordination/claims")
        assert resp.status_code == 200
        data = resp.json()
        assert data["claims"] == {}
        assert data["merge_plan"] == {}

    def test_claims_shows_all_workers(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["b.py"]})
        data = client.get("/api/coordination/claims").json()
        assert "w1" in data["claims"]["a.py"]
        assert "w2" in data["claims"]["b.py"]

    def test_claims_merge_plan_on_conflict(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["shared.py"]})
        client.post("/api/coordination/claim", json={"worker_id": "w2", "files": ["shared.py"]})
        data = client.get("/api/coordination/claims").json()
        assert "shared.py" in data["merge_plan"]
        assert data["merge_plan"]["shared.py"] == "union"

    def test_claims_no_merge_plan_without_conflict(self, client: TestClient) -> None:
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["a.py"]})
        data = client.get("/api/coordination/claims").json()
        assert data["merge_plan"] == {}


# ---------------------------------------------------------------------------
# _coordination_facet helper
# ---------------------------------------------------------------------------

class TestCoordinationFacet:
    def test_facet_returns_dict_with_expected_keys(self, app: FastAPI) -> None:
        facet = coordination._coordination_facet(app)
        assert "claims" in facet
        assert "merge_plan" in facet

    def test_facet_reflects_current_claims(self, app: FastAPI) -> None:
        client = TestClient(app)
        client.post("/api/coordination/claim", json={"worker_id": "w1", "files": ["f.py"]})
        facet = coordination._coordination_facet(app)
        assert "f.py" in facet["claims"]
