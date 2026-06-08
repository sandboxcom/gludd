"""Tests for TUI project management — cursor selection, add input, delete selected."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from general_ludd.tui.keybindings import TUIKeyHandler


def _state(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "current_view": "projects",
        "daemon_url": "http://localhost:8000",
        "status_msg": "",
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "selected_project_idx": 0,
        "projects_data": [],
    }
    defaults.update(overrides)
    return defaults


class TestProjectCursorSelection:
    def test_arrow_down_moves_cursor(self):
        state = _state(
            selected_project_idx=0,
            projects_data=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key_down()
        assert state["selected_project_idx"] == 1

    def test_arrow_down_wraps_to_zero(self):
        state = _state(
            selected_project_idx=2,
            projects_data=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key_down()
        assert state["selected_project_idx"] == 0

    def test_arrow_up_moves_cursor_back(self):
        state = _state(
            selected_project_idx=2,
            projects_data=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key_up()
        assert state["selected_project_idx"] == 1

    def test_arrow_up_wraps_to_end(self):
        state = _state(
            selected_project_idx=0,
            projects_data=[{"name": "a"}, {"name": "b"}, {"name": "c"}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key_up()
        assert state["selected_project_idx"] == 2

    def test_arrow_down_empty_list_stays_zero(self):
        state = _state(selected_project_idx=0, projects_data=[])
        handler = TUIKeyHandler(state)
        handler.handle_key_down()
        assert state["selected_project_idx"] == 0


class TestProjectAddInputMode:
    def test_press_a_enters_add_mode(self):
        state = _state()
        handler = TUIKeyHandler(state)
        handler.handle_key("a")
        assert state["input_mode"] == "projects_add"
        assert state["input_field_index"] == 0
        assert state["status_msg"] == "Add project — enter name"

    def test_typing_appends_to_buffer(self):
        state = _state(input_mode="projects_add", input_buffer="")
        handler = TUIKeyHandler(state)
        handler.handle_key("t")
        handler.handle_key("e")
        handler.handle_key("s")
        handler.handle_key("t")
        assert state["input_buffer"] == "test"

    def test_backspace_removes_char(self):
        state = _state(input_mode="projects_add", input_buffer="ab")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "a"

    def test_enter_saves_field_and_moves_to_weight(self):
        state = _state(
            input_mode="projects_add",
            input_buffer="myproj",
            input_field_index=0,
            input_fields=[{"label": "name", "value": ""}, {"label": "weight", "value": ""}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key("\r")
        assert state["input_fields"][0]["value"] == "myproj"
        assert state["input_field_index"] == 1
        assert state["input_buffer"] == ""
        assert "weight" in state["status_msg"]

    def test_enter_on_last_field_submits(self):
        state = _state(
            input_mode="projects_add",
            input_buffer="20",
            input_field_index=1,
            input_fields=[{"label": "name", "value": "myproj"}, {"label": "weight", "value": ""}],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"project_id": "p1", "name": "myproj"}
        with patch("httpx.post", return_value=mock_resp):
            handler = TUIKeyHandler(state)
            handler.handle_key("\r")
        assert state["input_fields"][1]["value"] == "20"
        assert state["input_mode"] is None
        assert "p1" in state["status_msg"]

    def test_escape_cancels_add(self):
        state = _state(input_mode="projects_add", input_buffer="test")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b")
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""
        assert "cancel" in state["status_msg"].lower()

    def test_submit_sends_correct_payload(self):
        state = _state(
            input_mode="projects_add",
            input_buffer="30",
            input_field_index=1,
            input_fields=[{"label": "name", "value": "myproj"}, {"label": "weight", "value": ""}],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"project_id": "p-new"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            handler = TUIKeyHandler(state)
            handler.handle_key("\r")
        call_kwargs = mock_post.call_args
        body = call_kwargs[1]["json"]
        assert body["name"] == "myproj"
        assert body["weight"] == 30

    def test_submit_error_shows_message(self):
        state = _state(
            input_mode="projects_add",
            input_buffer="10",
            input_field_index=1,
            input_fields=[{"label": "name", "value": "fail"}, {"label": "weight", "value": ""}],
        )
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            handler = TUIKeyHandler(state)
            handler.handle_key("\r")
        assert state["input_mode"] is None
        assert "error" in state["status_msg"].lower()


class TestProjectDeleteSelected:
    def test_delete_uses_selected_index(self):
        state = _state(
            selected_project_idx=1,
            projects_data=[
                {"project_id": "p1", "name": "first"},
                {"project_id": "p2", "name": "second"},
            ],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp) as mock_del:
            handler = TUIKeyHandler(state)
            handler.delete_selected_project()
        url = mock_del.call_args[0][0]
        assert "p2" in url

    def test_delete_empty_list_shows_message(self):
        state = _state(selected_project_idx=0, projects_data=[])
        handler = TUIKeyHandler(state)
        handler.delete_selected_project()
        assert "no projects" in state["status_msg"].lower()

    def test_delete_out_of_range_clamps(self):
        state = _state(
            selected_project_idx=5,
            projects_data=[{"project_id": "p1", "name": "only"}],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.delete", return_value=mock_resp) as mock_del:
            handler = TUIKeyHandler(state)
            handler.delete_selected_project()
        url = mock_del.call_args[0][0]
        assert "p1" in url

    def test_delete_error_shows_status(self):
        state = _state(
            selected_project_idx=0,
            projects_data=[{"project_id": "p1", "name": "fail"}],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.delete", return_value=mock_resp):
            handler = TUIKeyHandler(state)
            handler.delete_selected_project()
        assert "404" in state["status_msg"]


class TestProjectWeightEdit:
    def test_press_w_enters_weight_mode(self):
        state = _state(
            selected_project_idx=0,
            projects_data=[{"project_id": "p1", "name": "proj", "weight": 50}],
        )
        handler = TUIKeyHandler(state)
        handler.handle_key("w")
        assert state["input_mode"] == "projects_set_weight"
        assert "weight" in state["status_msg"].lower()

    def test_weight_submit_sends_put(self):
        state = _state(
            input_mode="projects_set_weight",
            input_buffer="75",
            selected_project_idx=0,
            projects_data=[{"project_id": "p1", "name": "proj", "weight": 50}],
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.put", return_value=mock_resp) as mock_put:
            handler = TUIKeyHandler(state)
            handler.handle_key("\r")
        url = mock_put.call_args[0][0]
        assert "p1/weight" in url
        body = mock_put.call_args[1]["json"]
        assert body["weight"] == 75
        assert state["input_mode"] is None

    def test_weight_escape_cancels(self):
        state = _state(input_mode="projects_set_weight", input_buffer="50")
        handler = TUIKeyHandler(state)
        handler.handle_key("\x1b")
        assert state["input_mode"] is None
