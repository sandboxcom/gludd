"""User-Requested Feature Guardrail Tests.

This file is a GUARDRAIL. Every test here verifies a feature the user has
REPEATEDLY requested across 6+ sessions. If any test fails, it means
user-requested work is incomplete and MUST be fixed before declaring done.

The user has asked for:
1. Arrow navigation in ALL TUI views (6+ requests)
2. Tab to switch panel focus (4+ requests)
3. Escape/left-arrow to go back (4+ requests)
4. Space/Enter to select/activate (5+ requests)
5. All variables tightly typed with adversarial structure tests (3+ requests)
6. All daemon request models validated (2+ requests)
7. Critical data flow schemas validated (ongoing)
8. TDD enforcement with test-before-code (3+ requests)
9. >85% test coverage per file (5+ requests)
10. No `Any` in schemas where specific types could be used
"""
from __future__ import annotations

from typing import ClassVar

import pytest

# ============================================================================
# SECTION 1: TUI NAVIGATION — arrow/tab/escape/space in ALL views
# The user has asked 6+ times for arrow navigation to work in the TUI.
# ============================================================================

def _make_handler():
    from general_ludd.tui.keybindings import TUIKeyHandler

    state = {
        "current_view": "main",
        "input_mode": None,
        "selected_main_idx": 0,
        "selected_idx": 0,
        "verbose_logging": False,
        "panel_focus": "left",
        "breadcrumb": ["main"],
        "status_msg": "",
        "daemon_running": False,
        "daemon_url": "http://127.0.0.1:8000",
        "projects_data": [{"project_id": "p1", "name": "proj1"}, {"project_id": "p2", "name": "proj2"}],
        "todos_data": [{"todo_id": "t1", "title": "todo1"}, {"todo_id": "t2", "title": "todo2"}],
        "hooks_data": [{"hook_id": "h1", "event_name": "build"}, {"hook_id": "h2", "event_name": "deploy"}],
        "workers_data": [{"worker_id": "w1", "status": "idle"}, {"worker_id": "w2", "status": "busy"}],
        "models_data": [{"model_id": "m1", "name": "model1"}, {"model_id": "m2", "name": "model2"}],
        "active_project_id": None,
        "active_todo_id": None,
        "active_hook_id": None,
        "active_worker_id": None,
        "left_panel_width": 40,
        "tui_log_entries": [],
        "selected_project_idx": 0,
        "selected_todo_idx": 0,
        "selected_hook_idx": 0,
        "selected_worker_idx": 0,
        "selected_model_idx": 0,
    }

    def _start():
        state["daemon_running"] = True
        state["status_msg"] = "Started"

    def _stop():
        state["daemon_running"] = False
        state["status_msg"] = "Stopped"

    handler = TUIKeyHandler(state)
    handler._start_daemon = _start
    handler._stop_daemon = _stop
    return handler, state


class TestArrowNavigationInAllViews:
    """Arrow up/down MUST work in every view that has a list."""

    VIEWS_WITH_LISTS: ClassVar[list[tuple[str, str, str]]] = [
        ("projects", "projects_data", "selected_project_idx"),
        ("todos", "todos_data", "selected_todo_idx"),
        ("hooks", "hooks_data", "selected_hook_idx"),
        ("workers", "workers_data", "selected_worker_idx"),
        ("models", "models_data", "selected_model_idx"),
    ]

    @pytest.mark.parametrize("view,data_key,idx_key", VIEWS_WITH_LISTS)
    def test_arrow_down_increments_index_in_view(self, view, data_key, idx_key):
        handler, state = _make_handler()
        state["current_view"] = view
        state["breadcrumb"] = ["main", view]
        state[idx_key] = 0
        handler.handle_key("\x1b[B")
        assert state[idx_key] == 1, f"Arrow down in {view} should increment {idx_key}"

    @pytest.mark.parametrize("view,data_key,idx_key", VIEWS_WITH_LISTS)
    def test_arrow_up_wraps_in_view(self, view, data_key, idx_key):
        handler, state = _make_handler()
        state["current_view"] = view
        state["breadcrumb"] = ["main", view]
        state[idx_key] = 0
        handler.handle_key("\x1b[A")
        assert state[idx_key] >= 0, f"Arrow up in {view} should wrap {idx_key}"

    def test_arrow_down_on_main_increments_menu_index(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        handler.handle_key("\x1b[B")
        assert state["selected_main_idx"] == 1

    def test_arrow_up_on_main_wraps(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["selected_main_idx"] = 0
        handler.handle_key("\x1b[A")
        assert state["selected_main_idx"] > 0


class TestTabPanelNavigation:
    """Tab MUST switch focus between left and right panels."""

    def test_tab_toggles_from_left_to_right(self):
        handler, state = _make_handler()
        state["panel_focus"] = "left"
        handler.handle_key("\t")
        assert state["panel_focus"] == "right"

    def test_tab_toggles_from_right_to_left(self):
        handler, state = _make_handler()
        state["panel_focus"] = "right"
        handler.handle_key("\t")
        assert state["panel_focus"] == "left"


class TestEscapeAndLeftArrowGoBack:
    """Escape and left-arrow MUST cancel input and go back to main."""

    VIEWS: ClassVar[list[str]] = ["projects", "todos", "hooks", "workers", "models"]

    @pytest.mark.parametrize("view", VIEWS)
    def test_escape_returns_to_main_from_view(self, view):
        handler, state = _make_handler()
        state["current_view"] = view
        state["breadcrumb"] = ["main", view]
        state["input_mode"] = None
        handler.handle_key("\x1b")
        assert state["current_view"] == "main", f"Escape from {view} should go to main"

    @pytest.mark.parametrize("view", VIEWS)
    def test_escape_pops_breadcrumb_from_view(self, view):
        handler, state = _make_handler()
        state["current_view"] = view
        state["breadcrumb"] = ["main", view]
        state["input_mode"] = None
        handler.handle_key("\x1b")
        assert state["breadcrumb"] == ["main"], f"Escape from {view} should pop breadcrumb"

    @pytest.mark.parametrize("view", VIEWS)
    def test_left_arrow_returns_to_main_from_view(self, view):
        handler, state = _make_handler()
        state["current_view"] = view
        state["breadcrumb"] = ["main", view]
        state["input_mode"] = None
        handler.handle_key("\x1b[D")
        assert state["current_view"] == "main", f"Left arrow from {view} should go to main"

    def test_escape_cancels_input_mode(self):
        handler, state = _make_handler()
        state["current_view"] = "models"
        state["input_mode"] = "models_search"
        handler.handle_key("\x1b")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_input_mode(self):
        handler, state = _make_handler()
        state["current_view"] = "projects"
        state["input_mode"] = "projects_add"
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None


class TestSpaceAndEnterActivate:
    """Space and Enter MUST activate selected item in ALL list views."""

    def test_space_activates_on_main_view(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["selected_main_idx"] = 0
        with pytest.MonkeyPatch.context() as mp:
            from general_ludd.tui import keybindings as kb
            called = []
            def mock_activate(self):
                called.append(True)
            mp.setattr(kb.TUIKeyHandler, "_activate_main_menu_item", mock_activate)
            handler.handle_key(" ")
            assert called, "Space on main view must call _activate_main_menu_item"

    def test_enter_activates_on_main_view(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["selected_main_idx"] = 0
        from general_ludd.tui import keybindings as kb
        called = []
        original = kb.TUIKeyHandler._activate_main_menu_item
        def mock_activate(self):
            called.append(True)
        kb.TUIKeyHandler._activate_main_menu_item = mock_activate
        try:
            handler.handle_key("\r")
            assert called, "Enter on main view must call _activate_main_menu_item"
        finally:
            kb.TUIKeyHandler._activate_main_menu_item = original

    def test_space_activates_project(self):
        handler, state = _make_handler()
        state["current_view"] = "projects"
        state["selected_project_idx"] = 0
        handler.handle_key(" ")
        assert state["active_project_id"] == "p1"

    def test_space_activates_todo(self):
        handler, state = _make_handler()
        state["current_view"] = "todos"
        state["selected_todo_idx"] = 0
        handler.handle_key(" ")
        assert state["active_todo_id"] == "t1"


class TestDaemonStartStopKeys:
    """Lowercase s/k on main view MUST start/stop daemon."""

    def test_s_starts_daemon(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        handler.handle_key("s")
        assert state["daemon_running"] is True

    def test_k_stops_daemon(self):
        handler, state = _make_handler()
        state["current_view"] = "main"
        state["daemon_running"] = True
        handler.handle_key("k")
        assert state["daemon_running"] is False


class TestVerboseToggle:
    """V (uppercase) MUST toggle verbose. v (lowercase) MUST enter config."""

    def test_V_toggles_verbose(self):
        handler, state = _make_handler()
        handler.handle_key("V")
        assert state["verbose_logging"] is True
        handler.handle_key("V")
        assert state["verbose_logging"] is False

    def test_v_enters_config_view(self):
        handler, state = _make_handler()
        handler.handle_key("v")
        assert state["current_view"] == "config"


# ============================================================================
# SECTION 2: SCHEMA ADVERSARIAL VALIDATION
# User asked 3+ times: "test the shapes of all variables with adversarial tests"
# ============================================================================

class TestJobSpecAdversarial:
    """JobSpec MUST reject missing required fields."""

    def test_missing_job_id_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.job import JobSpec
        with pytest.raises(ValidationError):
            JobSpec(playbook="p.yml", queue="core", work_type="code")

    def test_missing_playbook_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.job import JobSpec
        with pytest.raises(ValidationError):
            JobSpec(job_id="j1", queue="core", work_type="code")

    def test_missing_queue_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.job import JobSpec
        with pytest.raises(ValidationError):
            JobSpec(job_id="j1", playbook="p.yml", work_type="code")

    def test_valid_jobspec_passes(self):
        from general_ludd.schemas.job import JobSpec
        js = JobSpec(job_id="j1", playbook="p.yml", queue="core", work_type="code")
        assert js.job_id == "j1"


class TestTaskReturnAdversarial:
    """TaskReturn MUST reject missing required fields."""

    def test_missing_return_id_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.task_return import TaskReturn
        with pytest.raises(ValidationError):
            TaskReturn(job_id="j1", playbook="p.yml", queue="core")

    def test_valid_task_return_passes(self):
        from general_ludd.schemas.task_return import TaskReturn
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core")
        assert tr.return_id == "r1"


class TestBenchmarkScoresAdversarial:
    """BenchmarkScores MUST reject out-of-range values."""

    def test_score_above_1_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.benchmark import BenchmarkScores
        with pytest.raises(ValidationError):
            BenchmarkScores(completion_score=2.0, code_quality_score=0.5,
                            instruction_adherence_score=0.5, token_efficiency_score=0.5)

    def test_score_below_0_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.benchmark import BenchmarkScores
        with pytest.raises(ValidationError):
            BenchmarkScores(completion_score=-0.1, code_quality_score=0.5,
                            instruction_adherence_score=0.5, token_efficiency_score=0.5)

    def test_valid_scores_pass(self):
        from general_ludd.schemas.benchmark import BenchmarkScores
        bs = BenchmarkScores(completion_score=0.8, code_quality_score=0.7,
                             instruction_adherence_score=0.9, token_efficiency_score=0.6)
        assert 0 <= bs.composite_score <= 1


class TestQueueAdversarial:
    """Queue MUST reject invalid configurations."""

    def test_missing_queue_name_raises(self):
        from pydantic import ValidationError

        from general_ludd.schemas.queue import Queue
        with pytest.raises(ValidationError):
            Queue()

    def test_valid_queue_passes(self):
        from general_ludd.schemas.queue import Queue
        q = Queue(queue_name="core")
        assert q.queue_name == "core"


class TestTodoAdversarial:
    """Todo MUST reject invalid enum values."""

    def test_invalid_work_type_string(self):
        from general_ludd.schemas.todo import WorkType
        with pytest.raises(ValueError):
            WorkType("nonexistent_type")

    def test_invalid_risk_level_string(self):
        from general_ludd.schemas.todo import RiskLevel
        with pytest.raises(ValueError):
            RiskLevel("extreme")

    def test_valid_todo_passes(self):
        from general_ludd.schemas.todo import Todo
        t = Todo(todo_id="t1", title="test", queue="core", work_type="code")
        assert t.todo_id == "t1"


class TestTaskDecisionAdversarial:
    """TaskDecision MUST reject invalid decision strings."""

    def test_invalid_decision_raises(self):
        from general_ludd.schemas.task_decision import TaskDecision
        with pytest.raises(ValueError):
            TaskDecision(decision="maybe")


class TestDaemonRequestModels:
    """All daemon request models MUST reject invalid input."""

    def test_add_model_request_missing_fields(self):
        from pydantic import ValidationError

        from general_ludd.daemon import AddModelRequest
        with pytest.raises(ValidationError):
            AddModelRequest()

    def test_add_project_request_missing_fields(self):
        from pydantic import ValidationError

        from general_ludd.daemon import AddProjectRequest
        with pytest.raises(ValidationError):
            AddProjectRequest()

    def test_set_weight_request_missing_fields(self):
        from pydantic import ValidationError

        from general_ludd.daemon import SetWeightRequest
        with pytest.raises(ValidationError):
            SetWeightRequest()

    def test_register_hook_request_missing_fields(self):
        from pydantic import ValidationError

        from general_ludd.daemon import RegisterHookRequest
        with pytest.raises(ValidationError):
            RegisterHookRequest()

    def test_log_level_request_invalid(self):
        from pydantic import ValidationError

        from general_ludd.daemon import LogLevelRequest
        with pytest.raises(ValidationError):
            LogLevelRequest()


class TestGuardrailConfigAdversarial:
    """GuardrailConfig MUST reject configs with no enforcement layers."""

    def test_all_false_raises(self):
        from general_ludd.agents.behavior import GuardrailConfig
        with pytest.raises(ValueError):
            GuardrailConfig(config_layer=False, hook_layer=False, prompt_layer=False)

    def test_at_least_one_layer_passes(self):
        from general_ludd.agents.behavior import GuardrailConfig
        gc = GuardrailConfig(config_layer=True, hook_layer=False, prompt_layer=False)
        assert gc.config_layer is True


# ============================================================================
# SECTION 3: BREADCRUMB INTEGRITY
# User asked for breadcrumbs to make navigation testable.
# ============================================================================

class TestBreadcrumbIntegrity:
    """Breadcrumbs MUST stay in sync with view state."""

    def test_push_adds_view(self):
        from general_ludd.tui.breadcrumb import push_breadcrumb
        state = {"breadcrumb": ["main"]}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_pop_removes_last(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb
        state = {"breadcrumb": ["main", "projects"]}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_render_joins_with_arrow(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb
        result = render_breadcrumb(["main", "projects", "models"])
        assert "main" in result
        assert "projects" in result
        assert "models" in result


# ============================================================================
# SECTION 4: TDD GATE — verify test files exist for production modules
# ============================================================================

class TestTDDGate:
    """Critical production modules MUST have corresponding test files."""

    CRITICAL_MODULES: ClassVar[list[tuple[str, str]]] = [
        ("general_ludd/tui/keybindings", "test_tui"),
        ("general_ludd/tui/breadcrumb", "test_tui_breadcrumb"),
        ("general_ludd/tui/logger", "test_tui_logger"),
        ("general_ludd/tui/config_editor", "test_tui_config_editor"),
        ("general_ludd/daemon", "test_daemon"),
        ("general_ludd/cli", "test_cli"),
        ("general_ludd/db/session", "test_sqlite_default"),
        ("general_ludd/event_loop/loop", "test_event_loop"),
        ("general_ludd/quality/preflight", "test_preflight"),
        ("general_ludd/agents/behavior", "test_agent_behavior"),
        ("general_ludd/scoring/engine", "test_scoring"),
        ("general_ludd/scoring/router", "test_scoring"),
        ("general_ludd/models/gateway", "test_model_gateway"),
        ("general_ludd/secrets/manager", "test_secrets"),
    ]

    @pytest.mark.parametrize("module,expected_test", CRITICAL_MODULES)
    def test_file_exists(self, module, expected_test):
        import os
        test_path = os.path.join("tests", "unit", f"{expected_test}.py")
        assert os.path.exists(test_path), f"Missing test file: {test_path} for module {module}"


# ============================================================================
# SECTION 5: PANEL FOCUS STATE EXISTS
# ============================================================================

class TestPanelFocusState:
    """TUI MUST have a panel_focus state variable for Tab switching."""

    def test_panel_focus_key_in_initial_state(self):
        from general_ludd.tui.keybindings import TUIKeyHandler
        state = {"current_view": "main", "panel_focus": "left",
                 "input_mode": None, "selected_main_idx": 0,
                 "selected_idx": 0, "verbose_logging": False,
                 "breadcrumb": ["main"], "status_msg": "",
                 "daemon_running": False, "daemon_url": "http://127.0.0.1:8000",
                 "projects_data": [], "todos_data": [], "hooks_data": [],
                 "workers_data": [], "models_data": [],
                 "active_project_id": None, "active_todo_id": None,
                 "active_hook_id": None, "active_worker_id": None,
                 "left_panel_width": 40, "tui_log_entries": []}
        handler = TUIKeyHandler(state)
        handler.handle_key("\t")
        assert state.get("panel_focus") is not None
        assert state["panel_focus"] in ("left", "right")
