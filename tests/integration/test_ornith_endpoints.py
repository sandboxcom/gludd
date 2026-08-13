"""Integration tests for the Ornith training-data router endpoints.

Exercises the full daemon stack via FastAPI TestClient (which runs the
lifespan so the DB session factory is wired). Mirrors the pattern in
tests/integration/test_security_endpoints.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Exercise the public confinement setting: endpoint exports may write only
    # below the per-test root, including the explicit ``tmp_path`` used below.
    monkeypatch.setenv("ORNITH_EXPORT_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    app = create_daemon_app(config_dir=str(config_dir))
    with TestClient(app) as c:
        yield c


_RECORD_BODY = {
    "task_description": "Fix bug in module x",
    "target_files": ["src/x.py"],
    "scaffold_kind": "patch",
    "scaffold_content": "--- a/x.py\n+++ b/x.py\n",
    "agent_id": "agent-1",
    "iterations_used": 2,
    "tokens_consumed": 500,
    "model_sha": "ornith-9b-abc",
}


class TestOrnithEndpoints:
    def test_record_creates_pair(self, client):
        resp = client.post("/admin/ornith/record", json=_RECORD_BODY)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["id"].startswith("ORN-")
        assert data["outcome_status"] == "pending"
        assert data["scaffold_kind"] == "patch"
        assert data["scaffold_hash"]
        assert data["agent_id"] == "agent-1"

    def test_record_rejects_invalid_scaffold_kind(self, client):
        body = dict(_RECORD_BODY)
        body["scaffold_kind"] = "bogus"
        resp = client.post("/admin/ornith/record", json=body)
        assert resp.status_code == 422

    def test_set_outcome_updates_pair(self, client):
        rec = client.post("/admin/ornith/record", json=_RECORD_BODY)
        pair_id = rec.json()["id"]
        resp = client.patch(
            f"/admin/ornith/{pair_id}/outcome",
            json={"status": "succeeded", "details": {"gate_passed": True}},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["outcome_status"] == "succeeded"
        assert data["outcome_set_at"] is not None

    def test_set_outcome_unknown_pair_returns_404(self, client):
        resp = client.patch(
            "/admin/ornith/ORN-doesnotexist/outcome",
            json={"status": "succeeded"},
        )
        assert resp.status_code == 404

    def test_set_outcome_invalid_status_returns_422(self, client):
        rec = client.post("/admin/ornith/record", json=_RECORD_BODY)
        pair_id = rec.json()["id"]
        resp = client.patch(
            f"/admin/ornith/{pair_id}/outcome",
            json={"status": "bogus"},
        )
        assert resp.status_code == 422

    def test_pending_lists_pending_pairs(self, client):
        client.post("/admin/ornith/record", json=_RECORD_BODY)
        client.post("/admin/ornith/record", json=_RECORD_BODY)
        resp = client.get("/admin/ornith/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2
        assert len(data["pending"]) == data["count"]
        for p in data["pending"]:
            assert p["outcome_status"] == "pending"

    def test_export_returns_jsonl_path_and_count(self, client, tmp_path):
        out_path = tmp_path / "ds.jsonl"
        rec = client.post("/admin/ornith/record", json=_RECORD_BODY)
        pair_id = rec.json()["id"]
        client.patch(
            f"/admin/ornith/{pair_id}/outcome",
            json={"status": "succeeded"},
        )
        # A pending pair should be skipped.
        client.post("/admin/ornith/record", json=_RECORD_BODY)
        resp = client.get(
            "/admin/ornith/export", params={"out_path": str(out_path)}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["row_count"] == 1
        assert data["path"] == str(out_path)

    def test_export_rejects_path_outside_configured_root_without_path_disclosure(
        self, client, tmp_path
    ):
        out_path = tmp_path.parent / "outside-ornith-export.jsonl"

        resp = client.get(
            "/admin/ornith/export", params={"out_path": str(out_path)}
        )

        assert resp.status_code == 422
        assert resp.json() == {
            "detail": "out_path is outside the configured Ornith export root"
        }
        assert str(out_path) not in resp.text
        assert str(tmp_path) not in resp.text

    def test_stats_returns_counts(self, client):
        rec1 = client.post("/admin/ornith/record", json=_RECORD_BODY)
        rec2 = client.post("/admin/ornith/record", json=_RECORD_BODY)
        client.patch(
            f"/admin/ornith/{rec1.json()['id']}/outcome",
            json={"status": "succeeded"},
        )
        client.patch(
            f"/admin/ornith/{rec2.json()['id']}/outcome",
            json={"status": "rejected_by_gate"},
        )
        resp = client.get("/admin/ornith/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts_by_status"]["succeeded"] == 1
        assert data["counts_by_status"]["rejected_by_gate"] == 1
        assert data["total"] >= 2

    def test_pairs_filters_rejected_training_artifacts(self, client):
        rec = client.post("/admin/ornith/record", json=_RECORD_BODY)
        pair_id = rec.json()["id"]
        client.patch(
            f"/admin/ornith/{pair_id}/outcome",
            json={"status": "rejected_by_gate", "details": {"reason": "tests"}},
        )

        resp = client.get(
            "/admin/ornith/pairs",
            params={
                "status": "rejected_by_gate,reverted",
                "limit": 10,
                "lookback_days": 30,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["pairs"][0]["id"] == pair_id
        assert data["pairs"][0]["outcome_details"] == {"reason": "tests"}

    def test_status_history_and_config_endpoints_reflect_runtime_state(self, client):
        client.app.state._ornith_mcp_proc = object()
        client.app.state._ornith_status = {
            "version": "2.1",
            "model_sha": "sha-old",
            "total_calls": 4,
            "success_rate": 0.75,
            "sandbox_backend": "firecracker",
            "binary_path": "/opt/ornith",
        }
        client.app.state._ornith_history = [
            {"triggered_at": "2026-07-29T00:00:00Z", "result": {"ok": True}},
        ]

        status = client.get("/admin/ornith/status").json()
        assert status["installed"] is True
        assert status["version"] == "2.1"
        assert status["sandbox_backend"] == "firecracker"

        config = client.put(
            "/admin/ornith/config",
            json={"model_sha": "sha-new"},
        ).json()
        assert config["ornith_enabled"] is True
        assert config["model_sha"] == "sha-new"
        assert config["binary_path"] == "/opt/ornith"

        history = client.get("/admin/ornith/history", params={"limit": 1}).json()
        assert history["count"] == 1
        assert history["cycles"][0]["result"] == {"ok": True}
