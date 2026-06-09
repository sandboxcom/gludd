from general_ludd.tui.keybindings import TUIKeyHandler


def _make_state(view: str = "main", **kw: object) -> dict:
    state: dict = {
        "current_view": view,
        "input_mode": None,
        "input_buffer": "",
        "status_msg": "",
        "breadcrumb": ["main"],
        "projects_data": [],
        "todos_data": [],
        "hooks_data": [],
        "workers_data": [],
        "models_data": [],
        "agents_data": [],
        "integrity_changes": [],
        "selected_project_idx": 0,
        "selected_todo_idx": 0,
        "selected_hook_idx": 0,
        "selected_worker_idx": 0,
        "selected_model_idx": 0,
        "selected_agent_idx": 0,
        "selected_integrity_idx": 0,
        "selected_main_idx": 0,
    }
    state.update(kw)
    return state


class TestLeftArrowGoesBack:
    def test_left_arrow_from_subview_returns_to_main(self):
        state = _make_state(view="projects")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["current_view"] == "main"

    def test_left_arrow_from_subview_pops_breadcrumb(self):
        state = _make_state(view="todos", breadcrumb=["main", "todos"])
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["breadcrumb"] == ["main"]

    def test_left_arrow_on_main_does_not_exit(self):
        state = _make_state(view="main")
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b[D")
        assert state["current_view"] == "main"
        assert result is True

    def test_left_arrow_cancels_input_mode(self):
        state = _make_state(view="models", input_mode="models_search", input_buffer="query")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""

    def test_left_arrow_cancels_projects_add(self):
        state = _make_state(view="projects", input_mode="projects_add", input_buffer="myproj")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_todos_add(self):
        state = _make_state(view="todos", input_mode="todos_add", input_buffer="my todo")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancles_hooks_register(self):
        state = _make_state(view="hooks", input_mode="hooks_register", input_buffer="evt")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_ansible_install(self):
        state = _make_state(view="ansible", input_mode="ansible_install", input_buffer="role")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_compute_register(self):
        state = _make_state(view="compute", input_mode="compute_register", input_buffer="url")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_models_add(self):
        state = _make_state(
            view="models", input_mode="models_add", input_buffer="gpt-4",
            input_field_index=0,
            input_fields=[{"label": "model_id", "value": ""}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None

    def test_left_arrow_cancels_projects_set_weight(self):
        state = _make_state(view="projects", input_mode="projects_set_weight", input_buffer="5")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[D")
        assert state["input_mode"] is None


class TestMainMenuArrowNavigation:
    def test_arrow_down_increments_main_menu_index(self):
        state = _make_state(view="main")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state["selected_main_idx"] == 1

    def test_arrow_up_wraps_to_bottom(self):
        state = _make_state(view="main", selected_main_idx=0)
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[A")
        assert state["selected_main_idx"] > 0

    def test_arrow_down_wraps_to_top(self):
        state = _make_state(view="main", selected_main_idx=99)
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b[B")
        assert state["selected_main_idx"] == 0

    def test_enter_activates_selected_main_menu_item(self):
        state = _make_state(view="main", selected_main_idx=6)
        handler = TUIKeyHandler(state)
        handler.handle_key("\r")
        assert state["current_view"] == "models"

    def test_space_activates_selected_main_menu_item(self):
        state = _make_state(view="main", selected_main_idx=6)
        handler = TUIKeyHandler(state)
        handler.handle_key(" ")
        assert state["current_view"] == "models"

    def test_main_menu_items_match_controls_table(self):
        state = _make_state(view="main")
        handler = TUIKeyHandler(state)
        items = handler.get_main_menu_items()
        keys = [item[0] for item in items]
        assert "s" in keys
        assert "m" in keys
        assert "p" in keys


class TestEscapeDuringInput:
    def test_escape_cancels_text_search(self):
        state = _make_state(view="models", input_mode="models_search", input_buffer="test")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b")
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""
        assert state["status_msg"] == "Cancelled"

    def test_escape_does_not_change_view_when_in_input(self):
        state = _make_state(view="models", input_mode="models_search", input_buffer="test")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b")
        assert state["current_view"] == "models"
