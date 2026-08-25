"""Deep behavioral tests for routers/terraform_state.py.

Covers: 405 dispatch, empty-id unlock bypass, lock-default-fill,
operation_id wiring, zero-length fields, stack-name edge chars,
double-register route dedup, serial roundtrips, delete-relock,
and the internal _now_iso format.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import general_ludd.routers.terraform_state as ts_module


@pytest.fixture
def client() -> Any:
    ts_module._state_store.clear()
    ts_module._lock_store.clear()
    app = FastAPI()
    ts_module.register(app, {})
    return TestClient(app)


@pytest.fixture
def fresh_stores() -> Any:
    ts_module._state_store.clear()
    ts_module._lock_store.clear()
    yield
    ts_module._state_store.clear()
    ts_module._lock_store.clear()


# ── 405 Method Not Allowed ────────────────────────────────────────────


class TestMethodNotAllowed:
    def test_put_returns_405(self, client: Any) -> None:
        resp = client.put("/api/terraform/state/s", json={"v": 1})
        assert resp.status_code == 405
        assert "method not allowed" in resp.json()["detail"].casefold()

    def test_patch_returns_405(self, client: Any) -> None:
        resp = client.patch("/api/terraform/state/s", json={"v": 1})
        assert resp.status_code == 405

    def test_options_returns_405(self, client: Any) -> None:
        resp = client.options("/api/terraform/state/s")
        assert resp.status_code == 405

    def test_head_returns_405(self, client: Any) -> None:
        resp = client.head("/api/terraform/state/s")
        assert resp.status_code == 405

    def test_custom_method_returns_405(self, client: Any) -> None:
        resp = client.request("RENAME", "/api/terraform/state/s", json={})
        assert resp.status_code == 405


# ── Lock Default Value Filling ────────────────────────────────────────


class TestLockDefaults:
    def test_missing_id_generates_valid_uuid(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={})
        assert resp.status_code == 200
        uid = resp.json()["ID"]
        uuid.UUID(uid)

    def test_empty_string_id_generates_valid_uuid(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": ""})
        assert resp.status_code == 200
        uid = resp.json()["ID"]
        uuid.UUID(uid)

    def test_missing_operation_defaults_empty(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        assert resp.json()["Operation"] == ""

    def test_missing_info_defaults_empty(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        assert resp.json()["Info"] == ""

    def test_missing_who_defaults_empty(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        assert resp.json()["Who"] == ""

    def test_missing_version_defaults_empty(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        assert resp.json()["Version"] == ""

    def test_missing_created_defaults_to_iso_now(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        created = resp.json()["Created"]
        datetime.datetime.fromisoformat(created)
        now = datetime.datetime.now(datetime.UTC)
        parsed = datetime.datetime.fromisoformat(created)
        delta = abs((now - parsed).total_seconds())
        assert delta < 5

    def test_missing_path_defaults_empty(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        assert resp.json()["Path"] == ""

    def test_all_seven_lock_fields_returned(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        data = resp.json()
        fields = {"ID", "Operation", "Info", "Who", "Version", "Created", "Path"}
        assert set(data.keys()) == fields


# ── Unlock Edge Cases ─────────────────────────────────────────────────


class TestUnlockEdgeCases:
    def test_unlock_empty_provided_id_on_empty_lock(self, client: Any) -> None:
        resp = client.request("UNLOCK", "/api/terraform/state/nolock", json={})
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_unlock_provided_empty_string_allowed_when_lock_exists(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "lock"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": ""})
        assert resp.status_code == 200

    def test_unlock_after_double_unlock_is_idempotent(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "lock"})
        client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "lock"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "lock"})
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_unlock_substring_of_lock_id_is_rejected(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "abcdef"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "abc"})
        assert resp.status_code == 409

    def test_unlock_superset_of_lock_id_is_rejected(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "abc"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "abcdef"})
        assert resp.status_code == 409

    def test_unlock_with_no_body_accepted(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "lock"})
        resp = client.request("UNLOCK", "/api/terraform/state/s")
        assert resp.status_code == 200

    def test_unlock_with_malformed_body_fails_closed(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "lock"})
        resp = client.request(
            "UNLOCK",
            "/api/terraform/state/s",
            content=b"{",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_lock_with_non_object_body_fails_closed(self, client: Any) -> None:
        resp = client.request("LOCK", "/api/terraform/state/s", json=["not", "an", "object"])
        assert resp.status_code == 400


# ── Lock Conflict 423 Response Shape ──────────────────────────────────


class TestLockConflict:
    def test_423_response_contains_original_lock_data(self, client: Any) -> None:
        lock_body = {"ID": "orig", "Operation": "plan", "Who": "me"}
        client.request("LOCK", "/api/terraform/state/s", json=lock_body)
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "new"})
        assert resp.status_code == 423
        assert resp.json()["ID"] == "orig"
        assert resp.json()["Operation"] == "plan"

    def test_423_response_preserves_all_stored_fields(self, client: Any) -> None:
        lock_body = {
            "ID": "orig",
            "Operation": "apply",
            "Info": "info",
            "Who": "who",
            "Version": "1.0",
            "Created": "2024-01-01T00:00:00Z",
            "Path": "path.tfstate",
        }
        client.request("LOCK", "/api/terraform/state/s", json=lock_body)
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "blocked"})
        assert resp.status_code == 423
        data = resp.json()
        for key in lock_body:
            assert data[key] == lock_body[key]


# ── 409 Unlock Mismatch Detail ────────────────────────────────────────


class TestUnlock409Detail:
    def test_409_detail_contains_both_ids(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "correct"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "wrong"})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "correct" in detail
        assert "wrong" in detail

    def test_409_detail_mentions_lock_id_mismatch(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "x"})
        resp = client.request("UNLOCK", "/api/terraform/state/s", json={"ID": "y"})
        assert resp.status_code == 409
        assert "Lock ID mismatch" in resp.json()["detail"]


# ── State CRUD Deep ───────────────────────────────────────────────────


class TestStateDeep:
    def test_get_after_delete_returns_404(self, client: Any) -> None:
        client.post("/api/terraform/state/s", json={"v": 1})
        client.delete("/api/terraform/state/s")
        resp = client.get("/api/terraform/state/s")
        assert resp.status_code == 404
        assert "State not found" in resp.json()["detail"]

    def test_post_three_times_keeps_last(self, client: Any) -> None:
        client.post("/api/terraform/state/s", json={"v": 1})
        client.post("/api/terraform/state/s", json={"v": 2})
        client.post("/api/terraform/state/s", json={"v": 3})
        assert client.get("/api/terraform/state/s").json()["state"]["v"] == 3

    def test_post_empty_body_stores_empty_dict(self, client: Any) -> None:
        resp = client.post("/api/terraform/state/s", json={})
        assert resp.status_code == 200
        assert client.get("/api/terraform/state/s").json()["state"] == {}

    def test_delete_also_removes_state_from_store(self, client: Any) -> None:
        ts_module._state_store["s"] = {"v": 1}
        client.delete("/api/terraform/state/s")
        assert "s" not in ts_module._state_store

    def test_delete_also_removes_lock_from_store(self, client: Any) -> None:
        ts_module._lock_store["s"] = {"ID": "lock"}
        client.delete("/api/terraform/state/s")
        assert "s" not in ts_module._lock_store


# ── Stack Name Edge Characters ────────────────────────────────────────


class TestStackNameEdgeCases:
    def test_underscore_in_name(self, client: Any) -> None:
        name = "prod_us_east_1"
        client.post(f"/api/terraform/state/{name}", json={"v": 1})
        assert client.get(f"/api/terraform/state/{name}").status_code == 200

    def test_slash_encoded_in_name(self, client: Any) -> None:
        name = "team%2Fproject"
        client.post(f"/api/terraform/state/{name}", json={"v": 1})
        assert client.get(f"/api/terraform/state/{name}").status_code == 200

    def test_numeric_only_name(self, client: Any) -> None:
        client.post("/api/terraform/state/12345", json={"v": 1})
        assert client.get("/api/terraform/state/12345").json()["state"]["v"] == 1

    def test_empty_path_segment_name_returns_404(self, client: Any) -> None:
        resp = client.get("/api/terraform/state/")
        assert resp.status_code == 404


# ── Register Route Metadata ───────────────────────────────────────────


class TestRouteRegistration:
    def test_operation_ids_set_on_routes(self, client: Any) -> None:
        app = FastAPI()
        ts_module.register(app, {})
        ops = []
        for r in app.routes:
            if hasattr(r, "operation_id") and r.operation_id and "terraform_state" in r.operation_id:
                ops.append(r.operation_id)
        expected = {
            "terraform_state_get",
            "terraform_state_post",
            "terraform_state_delete",
        }
        assert set(ops) == expected

    def test_register_is_idempotent_routes_double(self, client: Any) -> None:
        app = FastAPI()
        ts_module.register(app, {})
        ts_module.register(app, {})
        tf_routes = [r.path for r in app.routes if hasattr(r, "path") and "/api/terraform/state" in r.path]
        assert len(tf_routes) == 10

    def test_register_passes_daemon_state_unchanged(self, client: Any) -> None:
        app = FastAPI()
        daemon_state: dict[str, object] = {"key": "value"}
        ts_module.register(app, daemon_state)
        assert daemon_state == {"key": "value"}


# ── Lock → Delete → Lock (relock after delete) ────────────────────────


class TestRelockAfterDelete:
    def test_lock_delete_lock_succeeds(self, client: Any) -> None:
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "a"})
        client.delete("/api/terraform/state/s")
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "b"})
        assert resp.status_code == 200
        assert resp.json()["ID"] == "b"

    def test_post_lock_delete_post_relock(self, client: Any) -> None:
        client.post("/api/terraform/state/s", json={"v": 1})
        client.request("LOCK", "/api/terraform/state/s", json={"ID": "a"})
        client.delete("/api/terraform/state/s")
        client.post("/api/terraform/state/s", json={"v": 2})
        resp = client.request("LOCK", "/api/terraform/state/s", json={"ID": "b"})
        assert resp.status_code == 200


# ── Serial Multi-Stack Roundtrip ──────────────────────────────────────


class TestMultiStackSerial:
    def test_five_stacks_independent(self, client: Any) -> None:
        for i in range(5):
            client.post(f"/api/terraform/state/s{i}", json={"v": i})
            client.request("LOCK", f"/api/terraform/state/s{i}", json={"ID": f"l{i}"})
        for i in range(5):
            assert client.get(f"/api/terraform/state/s{i}").json()["state"]["v"] == i
            lock = client.request("LOCK", f"/api/terraform/state/s{i}", json={"ID": "x"})
            assert lock.status_code == 423
        for i in range(5):
            client.request("UNLOCK", f"/api/terraform/state/s{i}", json={"ID": f"l{i}"})
            client.delete(f"/api/terraform/state/s{i}")
        for i in range(5):
            assert client.get(f"/api/terraform/state/s{i}").status_code == 404


# ── POST Response Is Always Empty Dict ────────────────────────────────


class TestPostResponse:
    def test_post_returns_empty_dict(self, client: Any) -> None:
        resp = client.post("/api/terraform/state/s", json={"v": 1})
        assert resp.json() == {}

    def test_post_overwrite_returns_empty_dict(self, client: Any) -> None:
        client.post("/api/terraform/state/s", json={"v": 1})
        resp = client.post("/api/terraform/state/s", json={"v": 2})
        assert resp.json() == {}

    def test_delete_returns_empty_dict(self, client: Any) -> None:
        client.post("/api/terraform/state/s", json={"v": 1})
        resp = client.delete("/api/terraform/state/s")
        assert resp.json() == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
