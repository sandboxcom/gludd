"""TUI keybinding handler for models add, ansible search, and dispatch mode toggle."""

from __future__ import annotations

from typing import Any

import httpx

DISPATCH_MODES = ["active", "passive_external", "worktree_monitor"]


class TUIKeyHandler:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    def handle_key(self, ch: str) -> bool:
        state = self._state
        view = state["current_view"]
        input_mode = state.get("input_mode")

        if input_mode == "models_add":
            return self._handle_models_add_input(ch)
        if input_mode == "ansible_search":
            return self._handle_ansible_search_input(ch)

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

        if view == "ansible" and ch == "s":
            state["input_mode"] = "ansible_search"
            state["input_buffer"] = ""
            state["status_msg"] = "Search Galaxy — enter query"
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
