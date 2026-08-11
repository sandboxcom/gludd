"""Deep behavioral tests for routers/schedule.py — Scheduler batch-planning endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestScheduleModuleShape:
    def test_register_is_callable(self) -> None:
        from general_ludd.routers.schedule import register

        assert callable(register)

    def test_register_adds_expected_path(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/schedule" in paths

    def test_register_returns_none(self) -> None:
        from general_ludd.routers.schedule import register

        result = register(FastAPI(), {})
        assert result is None


class TestScheduleRequestValidation:
    def test_empty_items_list_accepted(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={"items": []})
        assert resp.status_code == 200
        assert "batches" in resp.json()

    def test_missing_items_field_rejected(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={})
        assert resp.status_code == 422

    def test_item_id_min_length_enforced(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={"items": [{"id": ""}]})
        assert resp.status_code == 422

    def test_item_without_id_rejected(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={"items": [{}]})
        assert resp.status_code == 422

    def test_single_item_no_deps(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={"items": [{"id": "w1"}]})
        assert resp.status_code == 200
        data = resp.json()
        assert "batches" in data
        assert len(data["batches"]) == 1
        assert data["batches"][0] == ["w1"]

    def test_two_independent_items(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/api/schedule", json={"items": [{"id": "w1"}, {"id": "w2"}]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["batches"]) == 1
        assert sorted(data["batches"][0]) == ["w1", "w2"]

    def test_two_items_with_dependency(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A"}, {"id": "B", "depends_on": ["A"]}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["batches"]) == 2
        assert data["batches"][0] == ["A"]
        assert data["batches"][1] == ["B"]

    def test_greenfield_item_not_serialized(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A"}, {"id": "G1", "is_greenfield": True}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["batches"]) == 1
        assert sorted(data["batches"][0]) == ["A", "G1"]

    def test_resource_conflict_serializes(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "X", "resources": ["lock"]},
                    {"id": "Y", "resources": ["lock"]},
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["batches"]) == 2

    def test_unknown_dependency_raises(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A", "depends_on": ["Z"]}]},
        )
        assert resp.status_code == 422


class TestScheduleCycleDetection:
    def test_two_item_cycle_returns_409(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "A", "depends_on": ["B"]},
                    {"id": "B", "depends_on": ["A"]},
                ]
            },
        )
        assert resp.status_code == 409
        assert "dependency_cycle" in resp.json()["error"]

    def test_self_cycle_returns_409(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A", "depends_on": ["A"]}]},
        )
        assert resp.status_code == 409


class TestScheduleStateStorage:
    def test_last_plan_stored_after_successful_schedule(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A"}, {"id": "B", "depends_on": ["A"]}]},
        )
        assert resp.status_code == 200
        plan = app.state._schedule_last_plan
        assert plan is not None
        assert plan["batches"] == [["A"], ["B"]]
        assert len(plan["items"]) == 2

    def test_last_plan_not_updated_on_cycle(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        client.post(
            "/api/schedule",
            json={
                "items": [
                    {"id": "A", "depends_on": ["B"]},
                    {"id": "B", "depends_on": ["A"]},
                ]
            },
        )
        plan = getattr(app.state, "_schedule_last_plan", None)
        assert plan is None


class TestScheduleMaxLimits:
    def test_resources_max_length_enforced(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        too_many = [f"res_{i}" for i in range(101)]
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A", "resources": too_many}]},
        )
        assert resp.status_code == 422

    def test_depends_on_max_length_enforced(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        too_many = [f"dep_{i}" for i in range(101)]
        resp = client.post(
            "/api/schedule",
            json={"items": [{"id": "A", "depends_on": too_many}]},
        )
        assert resp.status_code == 422

    def test_items_list_max_length_enforced(self) -> None:
        from general_ludd.routers.schedule import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        too_many = [{"id": f"w{i}"} for i in range(1001)]
        resp = client.post("/api/schedule", json={"items": too_many})
        assert resp.status_code == 422
