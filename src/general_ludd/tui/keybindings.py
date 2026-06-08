"""TUI keybinding handler for view switching, input modes, and daemon actions."""

from __future__ import annotations

from typing import Any

import httpx

DISPATCH_MODES = ["active", "passive_external", "worktree_monitor"]

_TOGGLE_VIEWS: dict[str, tuple[str, str]] = {
    "u": ("mcp", "MCP Servers — [s]earch  [u] exit"),
    "j": ("skills", "Skills — [s]earch  [i]nstall  [j] exit"),
    "e": ("compute", "Compute — [a]dd endpoint  [e] exit"),
    "b": ("scores", "Scores — [b] exit"),
    "l": ("templates", "Templates — [r]efresh  [l] exit"),
    "n": ("quantization", "Quantization — [d]etect  [n] exit"),
    "f": ("filestore", "Filestore — [f] exit"),
    "z": ("deployments", "Deployments — [z] exit"),
}


def _handle_text_input(state: dict[str, Any], ch: str) -> bool:
    if ch == "\x1b":
        state["input_mode"] = None
        state["input_buffer"] = ""
        state["status_msg"] = "Cancelled"
        return True
    if ch == "\x7f":
        state["input_buffer"] = state["input_buffer"][:-1]
        return True
    return False


def _submit_text_input(state: dict[str, Any]) -> None:
    state["input_mode"] = None
    state["input_buffer"] = ""


class TUIKeyHandler:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def handle_key(self, ch: str) -> bool:
        state = self._state
        view = state["current_view"]
        input_mode = state.get("input_mode")

        if input_mode == "models_add":
            return self._handle_models_add_input(ch)
        if input_mode == "models_search":
            return self._handle_text_search_input(ch, "models_search", "/admin/models/search", "models_search_results")
        if input_mode == "ansible_search":
            return self._handle_ansible_search_input(ch)
        if input_mode == "projects_add":
            return self._handle_projects_add_input(ch)
        if input_mode == "projects_set_weight":
            return self._handle_projects_set_weight_input(ch)
        if input_mode == "mcp_search":
            return self._handle_text_search_input(ch, "mcp_search", "/admin/mcp/search", "mcp_search_results")
        if input_mode == "skills_search":
            return self._handle_text_search_input(ch, "skills_search", "/admin/skills/search", "skills_search_results")
        if input_mode == "compute_register":
            return self._handle_compute_register_input(ch)
        if input_mode == "todos_add":
            return self._handle_todos_add_input(ch)

        if view == "todos" and ch == "a":
            state["input_mode"] = "todos_add"
            state["input_buffer"] = ""
            state["input_field_index"] = 0
            state["input_fields"] = [
                {"label": "title", "value": ""},
                {"label": "priority", "value": ""},
            ]
            state["status_msg"] = "Add todo — enter title"
            return True

        if view == "workers" and ch == "p":
            self._ping_workers()
            return True

        if view == "projects" and ch == "a":
            state["input_mode"] = "projects_add"
            state["input_buffer"] = ""
            state["input_field_index"] = 0
            state["input_fields"] = [
                {"label": "name", "value": ""},
                {"label": "weight", "value": ""},
            ]
            state["status_msg"] = "Add project — enter name"
            return True

        if view == "projects" and ch == "d":
            self.delete_selected_project()
            return True

        if view == "projects" and ch == "w":
            projects: list[dict[str, Any]] = state.get("projects_data", [])
            idx: int = state.get("selected_project_idx", 0)
            if idx < len(projects):
                state["input_mode"] = "projects_set_weight"
                state["input_buffer"] = ""
                state["status_msg"] = f"Set weight for {projects[idx].get('name', '?')} — enter weight"
            return True

        if view == "models" and ch == "a":
            state["input_mode"] = "models_add"
            state["input_buffer"] = ""
            state["input_field_index"] = 0
            state["input_fields"] = [
                {"label": "model_id", "value": ""},
                {"label": "provider", "value": ""},
                {"label": "api_base", "value": ""},
            ]
            state["status_msg"] = "Add model — enter model_id"
            return True

        if view == "models" and ch == "s":
            state["input_mode"] = "models_search"
            state["input_buffer"] = ""
            state["status_msg"] = "Search models — enter query"
            return True

        if view == "ansible" and ch == "s":
            state["input_mode"] = "ansible_search"
            state["input_buffer"] = ""
            state["status_msg"] = "Search Galaxy — enter query"
            return True

        if view == "mcp" and ch == "s":
            state["input_mode"] = "mcp_search"
            state["input_buffer"] = ""
            state["status_msg"] = "Search MCP servers — enter query"
            return True

        if view == "skills" and ch == "s":
            state["input_mode"] = "skills_search"
            state["input_buffer"] = ""
            state["status_msg"] = "Search skills — enter query"
            return True

        if view == "compute" and ch == "a":
            state["input_mode"] = "compute_register"
            state["input_buffer"] = ""
            state["input_field_index"] = 0
            state["input_fields"] = [
                {"label": "endpoint_url", "value": ""},
                {"label": "provider", "value": ""},
            ]
            state["status_msg"] = "Register endpoint — enter URL"
            return True

        if view == "main" and ch == "R":
            self._reload_daemon()
            return True

        if ch == "a" and view == "main":
            state["current_view"] = "ansible"
            state["status_msg"] = "Ansible Galaxy — [s]earch  [a] exit  [q] quit"
            return True

        if view == "ansible" and ch == "a":
            state["current_view"] = "main"
            state["status_msg"] = ""
            return True

        if view == "ansible" and ch == "\x1b":
            state["current_view"] = "main"
            state["status_msg"] = ""
            return True

        if view == "main" and ch == "d":
            return self._cycle_dispatch_mode()

        for key, (view_name, msg) in _TOGGLE_VIEWS.items():
            if ch == key:
                if view == view_name:
                    state["current_view"] = "main"
                    state["status_msg"] = ""
                else:
                    state["current_view"] = view_name
                    state["status_msg"] = msg
                return True

        return True

    def handle_key_down(self) -> None:
        state = self._state
        projects = state.get("projects_data", [])
        if not projects:
            return
        idx: int = state.get("selected_project_idx", 0)
        state["selected_project_idx"] = (idx + 1) % len(projects)

    def handle_key_up(self) -> None:
        state = self._state
        projects = state.get("projects_data", [])
        if not projects:
            return
        idx: int = state.get("selected_project_idx", 0)
        state["selected_project_idx"] = (idx - 1) % len(projects)

    def delete_selected_project(self) -> None:
        state = self._state
        projects: list[dict[str, Any]] = state.get("projects_data", [])
        idx: int = state.get("selected_project_idx", 0)
        if not projects:
            state["status_msg"] = "No projects to remove"
            return
        if idx >= len(projects):
            idx = len(projects) - 1
        pid = projects[idx].get("project_id", "")
        try:
            resp = httpx.delete(
                f"{state['daemon_url']}/admin/projects/{pid}",
                timeout=5.0,
            )
            if resp.status_code == 200:
                state["status_msg"] = f"Removed {pid}"
            else:
                state["status_msg"] = f"Remove failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Remove error: {exc}"

    def _ping_workers(self) -> None:
        state = self._state
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/workers/ping",
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                count = len(data.get("workers", []))
                state["status_msg"] = f"Ping OK: {count} workers responded"
            else:
                state["status_msg"] = f"Ping failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Ping error: {exc}"

    def _reload_daemon(self) -> None:
        state = self._state
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/reload",
                json={"scope": "all"},
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                scope = data.get("scope", "all")
                state["status_msg"] = f"Reloaded: {scope}"
                state["last_reload"] = True
            else:
                state["status_msg"] = f"Reload failed: {resp.status_code}"
                state["last_reload"] = False
        except Exception as exc:
            state["status_msg"] = f"Reload error: {exc}"
            state["last_reload"] = False

    def _handle_text_search_input(self, ch: str, mode: str, endpoint: str, result_key: str) -> bool:
        state = self._state
        if _handle_text_input(state, ch):
            return True
        if ch == "\r":
            query = state["input_buffer"]
            try:
                resp = httpx.get(
                    f"{state['daemon_url']}{endpoint}",
                    params={"query": query},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", data.get("servers", data.get("skills", [])))
                    state["status_msg"] = f"Found {len(results)} results for '{query}'"
                    state[result_key] = results
                else:
                    state["status_msg"] = f"Search failed: {resp.status_code}"
            except Exception as exc:
                state["status_msg"] = f"Search error: {exc}"
            _submit_text_input(state)
            return True
        state["input_buffer"] += ch
        return True

    def _handle_todos_add_input(self, ch: str) -> bool:
        state = self._state
        if _handle_text_input(state, ch):
            return True
        if ch == "\r":
            idx = state["input_field_index"]
            state["input_fields"][idx]["value"] = state["input_buffer"]
            state["input_buffer"] = ""
            if idx < len(state["input_fields"]) - 1:
                state["input_field_index"] = idx + 1
                next_label = state["input_fields"][idx + 1]["label"]
                state["status_msg"] = f"Add todo — enter {next_label}"
            else:
                self._submit_todos_add()
            return True
        state["input_buffer"] += ch
        return True

    def _submit_todos_add(self) -> None:
        state = self._state
        fields = state["input_fields"]
        title = fields[0]["value"]
        priority_raw = fields[1]["value"]
        try:
            priority = int(priority_raw) if priority_raw else 5
        except ValueError:
            priority = 5
        payload: dict[str, Any] = {"title": title, "priority": priority}
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/todos",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                tid = data.get("todo_id", "?")
                state["status_msg"] = f"Todo added: {tid}"
            else:
                state["status_msg"] = f"Add failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Add error: {exc}"
        _submit_text_input(state)

    def _handle_compute_register_input(self, ch: str) -> bool:
        state = self._state
        if _handle_text_input(state, ch):
            return True
        if ch == "\r":
            idx = state["input_field_index"]
            state["input_fields"][idx]["value"] = state["input_buffer"]
            state["input_buffer"] = ""
            if idx < len(state["input_fields"]) - 1:
                state["input_field_index"] = idx + 1
                next_label = state["input_fields"][idx + 1]["label"]
                state["status_msg"] = f"Register endpoint — enter {next_label}"
            else:
                self._submit_compute_register()
            return True
        state["input_buffer"] += ch
        return True

    def _submit_compute_register(self) -> None:
        state = self._state
        fields = state["input_fields"]
        payload = {
            "endpoint_url": fields[0]["value"],
            "provider": fields[1]["value"] or "custom",
        }
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/compute/endpoints",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                eid = data.get("endpoint_id", "?")
                state["status_msg"] = f"Endpoint registered: {eid}"
            else:
                state["status_msg"] = f"Register failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Register error: {exc}"
        _submit_text_input(state)

    def _handle_projects_add_input(self, ch: str) -> bool:
        state = self._state
        if ch == "\x1b":
            state["input_mode"] = None
            state["input_buffer"] = ""
            state["status_msg"] = "Add cancelled"
            return True
        if ch == "\x7f":
            state["input_buffer"] = state["input_buffer"][:-1]
            return True
        if ch == "\r":
            idx = state["input_field_index"]
            state["input_fields"][idx]["value"] = state["input_buffer"]
            state["input_buffer"] = ""
            if idx < len(state["input_fields"]) - 1:
                state["input_field_index"] = idx + 1
                next_label = state["input_fields"][idx + 1]["label"]
                state["status_msg"] = f"Add project — enter {next_label}"
            else:
                self._submit_projects_add()
            return True
        state["input_buffer"] += ch
        return True

    def _submit_projects_add(self) -> None:
        state = self._state
        fields = state["input_fields"]
        name = fields[0]["value"]
        weight_raw = fields[1]["value"]
        try:
            weight = float(weight_raw) if weight_raw else 10.0
        except ValueError:
            weight = 10.0
        payload: dict[str, Any] = {"name": name, "weight": weight}
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/projects",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                pid = data.get("project_id", "?")
                state["status_msg"] = f"Project added: {pid}"
            else:
                state["status_msg"] = f"Add failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Add error: {exc}"
        state["input_mode"] = None
        state["input_buffer"] = ""

    def _handle_projects_set_weight_input(self, ch: str) -> bool:
        state = self._state
        if ch == "\x1b":
            state["input_mode"] = None
            state["input_buffer"] = ""
            state["status_msg"] = "Weight edit cancelled"
            return True
        if ch == "\x7f":
            state["input_buffer"] = state["input_buffer"][:-1]
            return True
        if ch == "\r":
            raw = state["input_buffer"]
            try:
                weight = float(raw)
            except ValueError:
                state["status_msg"] = f"Invalid weight: {raw}"
                state["input_mode"] = None
                state["input_buffer"] = ""
                return True
            projects: list[dict[str, Any]] = state.get("projects_data", [])
            idx: int = state.get("selected_project_idx", 0)
            if idx < len(projects):
                pid = projects[idx].get("project_id", "")
                try:
                    resp = httpx.put(
                        f"{state['daemon_url']}/admin/projects/{pid}/weight",
                        json={"weight": weight},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        state["status_msg"] = f"Weight set to {weight}% for {pid}"
                    else:
                        state["status_msg"] = f"Weight change failed: {resp.status_code}"
                except Exception as exc:
                    state["status_msg"] = f"Weight error: {exc}"
            state["input_mode"] = None
            state["input_buffer"] = ""
            return True
        state["input_buffer"] += ch
        return True

    def _handle_models_add_input(self, ch: str) -> bool:
        state = self._state
        if ch == "\x1b":
            state["input_mode"] = None
            state["input_buffer"] = ""
            state["status_msg"] = "Add cancelled"
            return True
        if ch == "\x7f":
            state["input_buffer"] = state["input_buffer"][:-1]
            return True
        if ch == "\r":
            idx = state["input_field_index"]
            state["input_fields"][idx]["value"] = state["input_buffer"]
            state["input_buffer"] = ""
            if idx < len(state["input_fields"]) - 1:
                state["input_field_index"] = idx + 1
                next_label = state["input_fields"][idx + 1]["label"]
                state["status_msg"] = f"Add model — enter {next_label}"
            else:
                self._submit_models_add()
            return True
        state["input_buffer"] += ch
        return True

    def _submit_models_add(self) -> None:
        state = self._state
        fields = state["input_fields"]
        payload = {
            "model_id": fields[0]["value"],
            "provider": fields[1]["value"] or "openai",
            "model": fields[0]["value"],
            "api_base": fields[2]["value"],
        }
        try:
            resp = httpx.post(
                f"{state['daemon_url']}/admin/models",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                mid = data.get("model_id", payload["model_id"])
                state["status_msg"] = f"Model added: {mid}"
            else:
                state["status_msg"] = f"Add failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Add error: {exc}"
        state["input_mode"] = None
        state["input_buffer"] = ""

    def _handle_ansible_search_input(self, ch: str) -> bool:
        state = self._state
        if ch == "\x1b":
            state["input_mode"] = None
            state["input_buffer"] = ""
            state["status_msg"] = "Search cancelled"
            return True
        if ch == "\x7f":
            state["input_buffer"] = state["input_buffer"][:-1]
            return True
        if ch == "\r":
            query = state["input_buffer"]
            try:
                resp = httpx.get(
                    f"{state['daemon_url']}/admin/ansible/search",
                    params={"query": query},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    state["status_msg"] = f"Found {len(results)} results for '{query}'"
                    state["ansible_search_results"] = results
                else:
                    state["status_msg"] = f"Search failed: {resp.status_code}"
            except Exception as exc:
                state["status_msg"] = f"Search error: {exc}"
            state["input_mode"] = None
            state["input_buffer"] = ""
            return True
        state["input_buffer"] += ch
        return True

    def _cycle_dispatch_mode(self) -> bool:
        state = self._state
        current = state.get("dispatch_mode", "active")
        try:
            idx = DISPATCH_MODES.index(current)
            next_idx = (idx + 1) % len(DISPATCH_MODES)
            next_mode = DISPATCH_MODES[next_idx]
        except ValueError:
            next_mode = DISPATCH_MODES[0]
        try:
            resp = httpx.put(
                f"{state['daemon_url']}/admin/dispatch/mode",
                json={"mode": next_mode},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_mode = data.get("dispatch_mode", next_mode)
                state["dispatch_mode"] = new_mode
                state["status_msg"] = f"Dispatch mode: {new_mode}"
            else:
                state["status_msg"] = f"Dispatch change failed: {resp.status_code}"
        except Exception as exc:
            state["status_msg"] = f"Dispatch error: {exc}"
        return True
