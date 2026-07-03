"""Tests proving the coordination router is wired and endpoints are reachable.

Verifies that a FastAPI app with coordination.register() successfully handles
the full file-claim lifecycle: claim, detect overlaps, release, and facet queries.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import coordination


def test_all_four_endpoints_reachable() -> None:
    """Prove POST /claim, POST /release, GET /overlaps, GET /claims are all reachable."""
    app = FastAPI()
    coordination.register(app, {})
    client = TestClient(app)

    assert client.post(
        "/api/coordination/claim",
        json={"worker_id": "w1", "files": ["a.py", "b.py"]},
    ).status_code == 201

    assert client.post(
        "/api/coordination/release", json={"worker_id": "w1"}
    ).status_code == 200

    assert client.get("/api/coordination/overlaps?worker_id=w1").status_code == 200

    assert client.get("/api/coordination/claims").status_code == 200


def test_file_claim_lifecycle_end_to_end() -> None:
    """Claim, detect overlap, release, verify claims cleared."""
    app = FastAPI()
    coordination.register(app, {})
    client = TestClient(app)

    client.post(
        "/api/coordination/claim",
        json={"worker_id": "agent-a", "files": ["src/app.py", "tests/test_app.py"]},
    )
    client.post(
        "/api/coordination/claim",
        json={"worker_id": "agent-b", "files": ["src/app.py", "src/models.py"]},
    )

    claims = client.get("/api/coordination/claims").json()
    assert "src/app.py" in claims["claims"]
    assert "agent-a" in claims["claims"]["src/app.py"]
    assert "agent-b" in claims["claims"]["src/app.py"]
    assert "src/app.py" in claims["merge_plan"]

    overlaps = client.get(
        "/api/coordination/overlaps?worker_id=agent-a"
    ).json()
    assert "src/app.py" in overlaps["overlaps"]
    assert "agent-b" in overlaps["overlaps"]["src/app.py"]
    assert "agent-b" in overlaps["should_wait"]

    client.post("/api/coordination/release", json={"worker_id": "agent-b"})
    claims_after = client.get("/api/coordination/claims").json()
    assert "agent-b" not in claims_after["claims"].get("src/app.py", [])
    assert claims_after.get("merge_plan", {}) == {}


def test_coordination_facet_wired_to_app_state() -> None:
    """Prove the _coordination_facet helper returns claims/merge_plan from app.state."""
    app = FastAPI()
    coordination.register(app, {})

    facet = coordination._coordination_facet(app)
    assert facet == {"claims": {}, "merge_plan": {}}

    client = TestClient(app)
    client.post(
        "/api/coordination/claim",
        json={"worker_id": "bob", "files": ["shared.py"]},
    )
    client.post(
        "/api/coordination/claim",
        json={"worker_id": "alice", "files": ["shared.py"]},
    )

    facet = coordination._coordination_facet(app)
    assert "shared.py" in facet["claims"]
    assert facet["merge_plan"]["shared.py"] == "union"
