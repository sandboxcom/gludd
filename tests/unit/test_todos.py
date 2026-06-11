"""Verify /api/status endpoint robustness — V0.2 smoke fix proof.

When the filestore subsystem is unavailable (e.g., disk full, permission denied),
the status endpoint must still return a valid JSON response — not an empty body
or a crash that takes the daemon down.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from fastapi import FastAPI

from general_ludd.routers.todos import register


class TestStatusEndpointFileStoreFailure:
    def test_status_returns_json_when_filestore_crashes(self):
        """Proof: /api/status returns valid JSON even when FileStore raises."""
        app = FastAPI()
        state: dict = {"todos": [], "tick_metrics": {}, "quality_gate": {}}

        register(app, state)

        with patch("general_ludd.routers.todos.FileStore", side_effect=OSError("disk full")):
            from fastapi.testclient import TestClient
            client = TestClient(app)
            resp = client.get("/api/status")
            assert resp.status_code == 200
            data = json.loads(resp.text)
            assert isinstance(data, dict)
            assert "version" in data
            assert data["filestore_root"] == ""
