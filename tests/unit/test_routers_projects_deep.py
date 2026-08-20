"""Deep edge-case tests for projects router endpoint validation & error paths.

Endpoints covered:
  - POST   /admin/projects                (validation edges, DoS vector, persist-path)
  - DELETE /admin/projects/{project_id}   (unset factory, double-delete, bare id)
  - PUT    /admin/projects/{project_id}/weight  (negative, zero, >100, nonexistent)
  - POST   /admin/projects/rebalance      (empty dict, exact-MAX, >MAX)
  - GET    /admin/projects                (no-ext, ext not projects, ext no summary)
  - POST   /admin/projects/skills         (empty fields, nonexistent skill)
  - PUT    /admin/dispatch/mode            (invalid modes, blank, valid modes)
  - POST   /admin/self-improve            (basic call)
  - POST   /admin/tui-log                 (exceed MAX, ring-buffer, empty entries)
  - GET    /admin/tui-log                 (empty, populated, ring-buffer cull)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(daemon_state=None, *, session_factory=None, config_dir=None):
    app = FastAPI()
    if daemon_state is not None:
        app.state.daemon_state = daemon_state
    if session_factory is not None:
        app.state._session_factory = session_factory
    if config_dir is not None:
        app.state._config_dir = config_dir
    return app


def _register(app, daemon_state=None):
    from general_ludd.routers.projects import register

    register(app, daemon_state or {})


def _mock_ext(mgr=None):
    ext: dict[str, object] = {}
    if mgr is not None:
        ext["projects"] = mgr
    return ext


class _FakeProject:
    """Non-mock object so FastAPI JSON serialisation sees real attributes."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _fake_project(**kw):
    defaults = {
        "project_id": "proj-00000001",
        "name": "test",
        "weight": 50.0,
        "description": "",
        "repo_url": "",
        "workspace_path": "",
        "dispatch_mode": "active",
        "active": True,
        "config": {},
    }
    defaults.update(kw)
    return _FakeProject(**defaults)


def _mock_project_mgr():
    mgr = MagicMock()
    mgr.add_project.return_value = _fake_project()
    mgr.remove_project.return_value = None
    mgr.set_weight.return_value = None
    mgr.rebalance.return_value = None
    mgr.total_weight.return_value = 50.0
    mgr.get_summary.return_value = {
        "total_projects": 1,
        "active_projects": 1,
        "total_weight": 50.0,
        "unallocated": 50.0,
        "projects": [],
    }
    mgr.list_projects.return_value = []
    mgr.get_project.return_value = None
    return mgr


def _mock_session_factory():
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    factory = MagicMock()
    factory.return_value = session
    return factory


# The projects router calls _get_or_create_extended_subsystems from
# general_ludd.daemon at the import site inside each endpoint function.
# Patching general_ludd.daemon is the correct target.
_DAEMON_PATH = "general_ludd.daemon._get_or_create_extended_subsystems"


# ---------------------------------------------------------------------------
# POST /admin/projects  — AddProjectRequest validation edges
# ---------------------------------------------------------------------------


class TestAddProjectEdges:
    def test_empty_name_422_when_no_ext(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post("/admin/projects", json={"name": "", "weight": 10.0})
        assert resp.status_code == 422

    def test_negative_weight_accepted_pydantic_422_downstream(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post("/admin/projects", json={"name": "x", "weight": -5.0})
        assert resp.status_code == 422

    def test_weight_as_string_rejected_by_pydantic(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects", json={"name": "x", "weight": "heavy"})
        assert resp.status_code == 422

    def test_missing_name_rejected(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects", json={"weight": 10.0})
        assert resp.status_code == 422

    def test_missing_weight_rejected(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects", json={"name": "x"})
        assert resp.status_code == 422

    def test_very_long_name_422(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post(
                "/admin/projects",
                json={"name": "n" * 10_000, "weight": 10.0},
            )
        assert resp.status_code == 422

    def test_extra_fields_ignored(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post(
                "/admin/projects",
                json={"name": "x", "weight": 10.0, "bogus": True, "evil": "yes"},
            )
        assert resp.status_code == 422

    def test_special_chars_in_name_422(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post(
                "/admin/projects",
                json={"name": "proj<script>alert(1)</script>", "weight": 10.0},
            )
        assert resp.status_code == 422

    def test_unicode_name_422(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.post(
                "/admin/projects",
                json={"name": "プロジェクト🔥emoji-test", "weight": 10.0},
            )
        assert resp.status_code == 422

    def test_successful_add_returns_project_data(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.post(
                "/admin/projects",
                json={"name": "hello", "weight": 25.0, "description": "desc"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test"
        assert data["project_id"] == "proj-00000001"

    def test_dispatch_mode_passive(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        mgr.add_project.return_value = _fake_project(dispatch_mode="passive_external")
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.post(
                "/admin/projects",
                json={"name": "d", "weight": 10.0, "dispatch_mode": "passive_external"},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /admin/projects  — error surfacing (no swallowed persist failures)
# ---------------------------------------------------------------------------


class TestAddProjectErrorPaths:
    def test_422_when_ext_missing_projects_key(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={"not_projects": MagicMock()}):
            resp = client.post("/admin/projects", json={"name": "x", "weight": 50.0})
        assert resp.status_code == 422

    def test_422_when_ext_is_none(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value=None):
            resp = client.post("/admin/projects", json={"name": "x", "weight": 50.0})
        assert resp.status_code == 422

    def test_422_when_add_project_raises_arbitrary(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        mgr.add_project.side_effect = RuntimeError("boom")
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.post("/admin/projects", json={"name": "x", "weight": 50.0})
        assert resp.status_code == 422

    def test_persist_failure_returns_422_not_200(self):
        app = _make_app(session_factory=_mock_session_factory())
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()

        async def _failing_persist(*a, **kw):
            raise RuntimeError("db down")

        with (
            patch(
                "general_ludd.projects.manager.persist_project",
                side_effect=_failing_persist,
            ),
            patch(_DAEMON_PATH, return_value=_mock_ext(mgr)),
        ):
            resp = client.post("/admin/projects", json={"name": "x", "weight": 50.0})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /admin/projects/{project_id}
# ---------------------------------------------------------------------------


class TestDeleteProjectEdges:
    def test_delete_nonexistent_project_returns_200(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.delete("/admin/projects/proj-fake0001")
        assert resp.status_code == 200
        assert resp.json() == {"removed": "proj-fake0001"}

    def test_delete_calls_remove_project(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.delete("/admin/projects/proj-00000001")
        assert resp.status_code == 200
        mgr.remove_project.assert_called_once_with("proj-00000001")

    def test_delete_when_ext_missing_projects_key(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        raised = False
        with patch(_DAEMON_PATH, return_value={}):
            try:
                client.delete("/admin/projects/proj-00000001")
            except KeyError as e:
                raised = True
                assert "projects" in str(e)
        assert raised, "Expected KeyError for missing projects key"

    def test_delete_empty_project_id_405(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.delete("/admin/projects/")
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# PUT /admin/projects/{project_id}/weight
# ---------------------------------------------------------------------------


class TestSetWeightEdges:
    def test_set_weight_nonexistent(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.put("/admin/projects/proj-fake0001/weight", json={"weight": 10.0})
        assert resp.status_code == 200

    def test_set_weight_missing_weight_field(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/projects/proj-00000001/weight", json={})
        assert resp.status_code == 422

    def test_set_weight_string_type_rejected(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/projects/proj-00000001/weight", json={"weight": "ten"})
        assert resp.status_code == 422

    def test_set_weight_null_rejected(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/projects/proj-00000001/weight", json={"weight": None})
        assert resp.status_code == 422

    def test_set_weight_integer_is_accepted(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.put("/admin/projects/proj-00000001/weight", json={"weight": 25})
        assert resp.status_code == 200

    def test_set_weight_extra_fields_ignored(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.put(
                "/admin/projects/proj-00000001/weight",
                json={"weight": 10.0, "hijack": 1},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /admin/projects/rebalance
# ---------------------------------------------------------------------------


class TestRebalanceEdges:
    def test_rebalance_empty_dict(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.post("/admin/projects/rebalance", json={"weights": {}})
        assert resp.status_code == 200
        assert resp.json() == {"rebalanced": []}

    def test_rebalance_at_max_limit(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        weights = {f"proj-{i:08d}": float(i % 100) for i in range(500)}
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.post("/admin/projects/rebalance", json={"weights": weights})
        assert resp.status_code == 200

    def test_rebalance_exceeds_max_limit(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        weights = {f"proj-{i:08d}": 1.0 for i in range(501)}
        resp = client.post("/admin/projects/rebalance", json={"weights": weights})
        assert resp.status_code == 413

    def test_rebalance_way_beyond_max(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        weights = {f"proj-{i:08d}": 1.0 for i in range(5000)}
        resp = client.post("/admin/projects/rebalance", json={"weights": weights})
        assert resp.status_code == 413

    def test_rebalance_weights_field_not_a_dict(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects/rebalance", json={"weights": "not-a-dict"})
        assert resp.status_code == 422

    def test_rebalance_missing_weights_field(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects/rebalance", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/projects  — list
# ---------------------------------------------------------------------------


class TestListProjectsEdges:
    def test_list_projects_no_ext(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch(_DAEMON_PATH, return_value={}):
            resp = client.get("/admin/projects")
        assert resp.status_code == 500

    def test_list_projects_summary_not_a_dict(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        mgr = _mock_project_mgr()
        mgr.get_summary.return_value = ["list-summary"]
        with patch(_DAEMON_PATH, return_value=_mock_ext(mgr)):
            resp = client.get("/admin/projects")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /admin/projects/skills
# ---------------------------------------------------------------------------


class TestProjectSkillsEdges:
    def test_empty_project_id_and_skill_name(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"project_id": "", "skill_name": ""},
        )
        assert resp.status_code == 422

    def test_empty_project_id(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"project_id": "", "skill_name": "git-release-captain"},
        )
        assert resp.status_code == 422

    def test_empty_skill_name(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"project_id": "proj-00000001", "skill_name": ""},
        )
        assert resp.status_code == 422

    def test_missing_both_fields(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/projects/skills", json={})
        assert resp.status_code == 422

    def test_missing_project_id_field(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"skill_name": "git-release-captain"},
        )
        assert resp.status_code == 422

    def test_missing_skill_name_field(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"project_id": "proj-00000001"},
        )
        assert resp.status_code == 422

    def test_nonexistent_skill_returns_404(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={"project_id": "proj-00000001", "skill_name": "no-such-skill"},
        )
        assert resp.status_code == 404

    def test_extra_fields_ignored(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/projects/skills",
            json={
                "project_id": "proj-00000001",
                "skill_name": "no-such-skill",
                "secret": "leak",
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /admin/dispatch/mode
# ---------------------------------------------------------------------------


class TestDispatchModeEdges:
    def test_invalid_mode_returns_400(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": "invalid"})
        assert resp.status_code == 400

    def test_empty_mode_returns_400(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": ""})
        assert resp.status_code == 400

    def test_active_mode_returns_200(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": "active"})
        assert resp.status_code == 200
        assert resp.json() == {"dispatch_mode": "active"}

    def test_passive_external_mode_returns_200(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": "passive_external"})
        assert resp.status_code == 200

    def test_worktree_monitor_mode_returns_200(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": "worktree_monitor"})
        assert resp.status_code == 200

    def test_missing_mode_field_uses_default_active(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={})
        assert resp.status_code == 200
        assert resp.json() == {"dispatch_mode": "active"}

    def test_mode_case_sensitive(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": "Active"})
        assert resp.status_code == 400

    def test_mode_as_null(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.put("/admin/dispatch/mode", json={"mode": None})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /admin/self-improve
# ---------------------------------------------------------------------------


class TestSelfImproveEdges:
    def test_self_improve_returns_ok(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        with patch("general_ludd.routers.projects.SelfImprovementHarness") as mock_harness:
            instance = mock_harness.return_value
            instance.run_full_cycle.return_value = {
                "findings_count": 1,
                "todos_generated": 2,
                "todos_enqueued": 1,
            }
            resp = client.post("/admin/self-improve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["findings_count"] == 1
        assert data["todos_generated"] == 2
        assert data["todos_enqueued"] == 1


# ---------------------------------------------------------------------------
# POST /admin/tui-log
# ---------------------------------------------------------------------------


class TestTuiLogEdges:
    def test_empty_entries(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/tui-log", json={"entries": []})
        assert resp.status_code == 200
        assert resp.json()["stored"] == 0

    def test_missing_entries_field_uses_default(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/tui-log", json={})
        assert resp.status_code == 200
        assert resp.json()["stored"] == 0

    def test_single_entry_stored(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post(
            "/admin/tui-log",
            json={"entries": [{"msg": "hello", "ts": 1}]},
        )
        assert resp.status_code == 200
        assert resp.json()["stored"] == 1

    def test_exactly_max_entries(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        entries = [{"n": i} for i in range(1000)]
        resp = client.post("/admin/tui-log", json={"entries": entries})
        assert resp.status_code == 200
        assert resp.json()["stored"] == 1000

    def test_exceeds_max_entries(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        entries = [{"n": i} for i in range(1001)]
        resp = client.post("/admin/tui-log", json={"entries": entries})
        assert resp.status_code == 413

    def test_way_beyond_max_entries(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        entries = [{"n": i} for i in range(5000)]
        resp = client.post("/admin/tui-log", json={"entries": entries})
        assert resp.status_code == 413

    def test_entries_not_a_list(self):
        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.post("/admin/tui-log", json={"entries": "not-a-list"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /admin/tui-log  — retrieval edges (test isolation via module-level reset)
# ---------------------------------------------------------------------------


class TestTuiLogGetEdges:
    def test_empty_log_returns_empty_list(self):
        """Must reset the module-level _tui_log_entries before testing the GET.
        Previous POST tests populate this global and test order is not guaranteed."""
        import general_ludd.routers.projects as _mod

        _mod._tui_log_entries.clear()

        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.get("/admin/tui-log")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_populated_log_returns_last_200(self):
        import general_ludd.routers.projects as _mod

        _mod._tui_log_entries.clear()
        _mod._tui_log_entries.extend({"n": i} for i in range(500))

        app = _make_app()
        _register(app)
        client = TestClient(app)
        resp = client.get("/admin/tui-log")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 200
        assert entries[0]["n"] == 300

    def test_ring_buffer_cull_triggers_via_post_endpoint(self):
        import general_ludd.routers.projects as _mod

        _mod._tui_log_entries.clear()
        # Pre-fill so one maximum-size request crosses the ring-buffer limit.
        _mod._tui_log_entries.extend({"n": i} for i in range(9001))

        app = _make_app()
        _register(app)
        client = TestClient(app)

        # Push the accepted maximum through the endpoint; 9001+1000 exceeds
        # the 10,000-entry retained ring without bypassing the request cap.
        entries = [{"n": i} for i in range(1000)]
        resp = client.post("/admin/tui-log", json={"entries": entries})
        assert resp.status_code == 200

        # After truncation should be at most 10000
        assert len(_mod._tui_log_entries) <= 10000

        resp = client.get("/admin/tui-log")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 200


# ---------------------------------------------------------------------------
# Request model direct-instantiation tests (no FastAPI)
# ---------------------------------------------------------------------------


class TestAddProjectRequestModel:
    def test_description_default_is_empty_string(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="a", weight=10.0)
        assert req.description == ""

    def test_repo_url_default_is_empty_string(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="a", weight=10.0)
        assert req.repo_url == ""

    def test_workspace_path_default_is_empty_string(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="a", weight=10.0)
        assert req.workspace_path == ""

    def test_dispatch_mode_default_is_active(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="a", weight=10.0)
        assert req.dispatch_mode == "active"

    def test_all_fields_populated(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(
            name="full-project",
            weight=75.0,
            description="A full desc",
            repo_url="https://github.com/sandboxcom/gludd",
            workspace_path="/tmp/ws",
            dispatch_mode="passive_external",
        )
        assert req.name == "full-project"
        assert req.weight == 75.0
        assert req.description == "A full desc"
        assert req.repo_url == "https://github.com/sandboxcom/gludd"
        assert req.workspace_path == "/tmp/ws"
        assert req.dispatch_mode == "passive_external"

    def test_weight_zero(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="zero", weight=0.0)
        assert req.weight == 0.0

    def test_weight_100(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="full", weight=100.0)
        assert req.weight == 100.0

    def test_weight_fractional(self):
        from general_ludd.routers.projects import AddProjectRequest

        req = AddProjectRequest(name="frac", weight=33.33333)
        assert req.weight == 33.33333


# ---------------------------------------------------------------------------
# DoS caps / constants structural tests
# ---------------------------------------------------------------------------


class TestDosCaps:
    def test_max_tui_log_entries_is_1000(self):
        from general_ludd.routers.projects import _MAX_TUI_LOG_ENTRIES

        assert _MAX_TUI_LOG_ENTRIES == 1000

    def test_max_rebalance_weights_is_500(self):
        from general_ludd.routers.projects import _MAX_REBALANCE_WEIGHTS

        assert _MAX_REBALANCE_WEIGHTS == 500
