"""TUI verbose logger — logs all user actions to file and database."""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from typing import Any

import httpx


class TUILogger:
    def __init__(
        self,
        log_dir: str = "",
        daemon_url: str = "",
        verbose: bool = True,
    ) -> None:
        self._log_dir = log_dir
        self._daemon_url = daemon_url
        self.verbose = verbose
        self._session_id = uuid.uuid4().hex[:12]
        self._log_path = os.path.join(log_dir, "tui.log") if log_dir else ""
        self._entries: list[dict[str, Any]] = []
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    def _write(self, entry: dict[str, Any]) -> None:
        entry["session_id"] = self._session_id
        entry["timestamp"] = time.time()
        self._entries.append(entry)
        if self._log_path:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

    def log_key_press(self, view: str, key: str) -> None:
        if not self.verbose:
            return
        self._write({"event": "key_press", "view": view, "key": key})

    def log_view_change(self, from_view: str, to_view: str) -> None:
        self._write({"event": "view_change", "from_view": from_view, "to_view": to_view})

    def log_daemon_action(self, action: str, details: dict[str, Any] | None = None) -> None:
        self._write({"event": "daemon_action", "action": action, "details": details or {}})

    def log_selection(self, view: str, index: int, item_id: str) -> None:
        self._write({"event": "selection_change", "view": view, "index": index, "item_id": item_id})

    def log_status_msg(self, message: str) -> None:
        self._write({"event": "status_msg", "message": message})

    def toggle_verbose(self) -> None:
        self.verbose = not self.verbose

    def flush_to_database(self) -> None:
        if not self._daemon_url or not self._entries:
            return
        with contextlib.suppress(Exception):
            httpx.post(
                f"{self._daemon_url}/admin/tui-log",
                json={"entries": self._entries[-50:]},
                timeout=5.0,
            )

    def close(self) -> None:
        if self._entries:
            self.flush_to_database()
