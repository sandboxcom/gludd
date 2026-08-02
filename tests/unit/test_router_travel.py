"""Unit tests for the travel HTTP router (routers/travel.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.travel import (
    TRAVEL_PLAN_PLAYBOOK,
    _resolve_playbook_path,
    _travel_result,
    register,
)


class TestTravelResult:
    def test_successful_result(self) -> None:
        result = _travel_result({"status": "successful", "rc": 0, "events": [{"task": "ok"}]})
        assert result["status"] == "successful"
        assert result["rc"] == 0
        assert len(result["events"]) == 1

    def test_failed_result(self) -> None:
        result = _travel_result({"status": "failed", "rc": 2})
        assert result["status"] == "failed"
        assert result["rc"] == 2
        assert result["events"] == []

    def test_missing_keys_default(self) -> None:
        result = _travel_result({})
        assert result["status"] == "unknown"
        assert result["rc"] == -1
        assert result["events"] == []


class TestResolvePlaybookPath:
    def test_resolves_from_repo_root(self, tmp_path: Path) -> None:
        playbook_dir = tmp_path / "playbooks"
        playbook_dir.mkdir()
        (playbook_dir / "travel_plan.yml").write_text("---")
        from general_ludd.routers import travel as travel_module

        with patch.object(
            travel_module.Path,
            "__file__",
            create=True,
            new_callable=lambda: str(tmp_path / "src" / "general_ludd" / "routers" / "travel.py"),
        ):
            path = _resolve_playbook_path("travel_plan.yml")
            assert path is not None

    def test_returns_first_candidate_when_none_exist(self) -> None:
        with patch.object(Path, "is_file", return_value=False):
            result = _resolve_playbook_path("nonexistent.yml")
            assert result.name == "nonexistent.yml"


class TestRouterRegistration:
    def _make_app_with_mock_runner(self) -> tuple[FastAPI, MagicMock, TestClient]:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        def _patch_runner():
            return mock_runner

        return app, mock_runner, TestClient(app)

    def test_register_adds_four_routes(self) -> None:
        app = FastAPI()
        register(app, {})
        routes = {getattr(r, "path", "") for r in app.routes}
        assert "/api/travel/plan" in routes
        assert "/api/travel/flights" in routes
        assert "/api/travel/hotels" in routes
        assert "/api/travel/event" in routes

    def test_plan_endpoint_dispatches_playbook(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_plan.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/plan",
                json={"origin": "JFK", "destination": "CDG", "budget": 2000.0},
            )
        assert response.status_code == 200
        mock_runner.register_playbook.assert_called_once()
        mock_runner.run_playbook.assert_called_once()
        called_name = mock_runner.run_playbook.call_args[0][0]
        assert called_name == TRAVEL_PLAN_PLAYBOOK
        extravars = mock_runner.run_playbook.call_args[1]["extravars"]
        assert extravars["origin"] == "JFK"
        assert extravars["budget"] == 2000.0

    def test_flights_endpoint_dispatches_playbook(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_flights.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/flights",
                json={"origin": "LAX", "destination": "HND", "passengers": 3},
            )
        assert response.status_code == 200
        extravars = mock_runner.run_playbook.call_args[1]["extravars"]
        assert extravars["passengers"] == 3

    def test_hotels_endpoint_dispatches_playbook(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_hotels.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/hotels",
                json={"destination": "Paris", "guests": 2},
            )
        assert response.status_code == 200
        extravars = mock_runner.run_playbook.call_args[1]["extravars"]
        assert extravars["destination"] == "Paris"
        assert extravars["guests"] == 2

    def test_event_endpoint_dispatches_playbook(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_event_plan.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/event",
                json={"destination": "Berlin", "event_type": "conference", "attendees": 200},
            )
        assert response.status_code == 200
        extravars = mock_runner.run_playbook.call_args[1]["extravars"]
        assert extravars["event_type"] == "conference"
        assert extravars["attendees"] == 200

    def test_plan_endpoint_default_values(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_plan.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post("/api/travel/plan", json={})
        assert response.status_code == 200
        extravars = mock_runner.run_playbook.call_args[1]["extravars"]
        assert extravars["origin"] == ""
        assert extravars["travelers"] == 1
        assert extravars["interests"] == []

    def test_plan_endpoint_returns_500_on_error(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.side_effect = RuntimeError("boom")

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_plan.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post("/api/travel/plan", json={})
        assert response.status_code == 500

    def test_skips_registration_when_already_registered(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = ["travel_plan.yml"]
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_plan.yml"),
            ),
        ):
            register(app, {})
            client = TestClient(app)
            client.post("/api/travel/plan", json={"origin": "JFK"})
        mock_runner.register_playbook.assert_not_called()
        mock_runner.run_playbook.assert_called_once()


class TestLiveSearxngIntegration:
    def test_flights_live_true_calls_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_flights.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            mock_searxng.return_value = {"results": [], "result_count": 0}
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/flights?live=true",
                json={"origin": "JFK", "destination": "CDG"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "live_data" in data
        mock_searxng.assert_called_once_with("flights from JFK to CDG on any date", "flights")

    def test_flights_live_false_omits_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_flights.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/flights",
                json={"origin": "JFK", "destination": "CDG"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "live_data" not in data
        mock_searxng.assert_not_called()

    def test_hotels_live_true_calls_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_hotels.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            mock_searxng.return_value = {"results": [], "result_count": 0}
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/hotels?live=true",
                json={"destination": "Paris", "guests": 2},
            )
        assert response.status_code == 200
        assert "live_data" in response.json()
        mock_searxng.assert_called_once_with("hotels in Paris for 2 guest(s)", "hotels")

    def test_hotels_live_false_omits_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_hotels.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/hotels",
                json={"destination": "Paris"},
            )
        assert response.status_code == 200
        assert "live_data" not in response.json()
        mock_searxng.assert_not_called()

    def test_event_live_true_calls_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_event_plan.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            mock_searxng.return_value = {"results": [], "result_count": 0}
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/event?live=true",
                json={"destination": "Berlin", "event_type": "conference"},
            )
        assert response.status_code == 200
        assert "live_data" in response.json()
        mock_searxng.assert_called_once_with("conference in Berlin on any date", "events")

    def test_event_live_false_omits_searxng(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_event_plan.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
            ) as mock_searxng,
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/event",
                json={"destination": "Berlin"},
            )
        assert response.status_code == 200
        assert "live_data" not in response.json()
        mock_searxng.assert_not_called()

    def test_live_data_structure_includes_keys(self) -> None:
        app = FastAPI()
        mock_runner = MagicMock()
        mock_runner.list_playbooks.return_value = []
        mock_runner.run_playbook.return_value = {"status": "successful", "rc": 0}

        fake_live = {
            "results": [{"title": "Cheap flight", "airline": "UA"}],
            "raw_results": [],
            "result_count": 1,
            "query": "flights",
            "category": "flights",
            "search_url": "http://localhost:8080/search?q=flights",
        }
        with (
            patch(
                "general_ludd.ansible.runner.AnsibleRunnerAdapter",
                return_value=mock_runner,
            ),
            patch(
                "general_ludd.routers.travel._resolve_playbook_path",
                return_value=Path("/fake/playbooks/travel_search_flights.yml"),
            ),
            patch(
                "general_ludd.routers.travel._call_searxng",
                return_value=fake_live,
            ),
        ):
            register(app, {})
            client = TestClient(app)
            response = client.post(
                "/api/travel/flights?live=true",
                json={"origin": "JFK", "destination": "CDG"},
            )
        assert response.status_code == 200
        live = response.json()["live_data"]
        assert live["result_count"] == 1
        assert live["category"] == "flights"
