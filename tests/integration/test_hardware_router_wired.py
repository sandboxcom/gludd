"""Integration tests for the hardware router wiring.

Exercises the daemon stack to prove the /admin/hardware/* endpoints are
correctly wired:
  1. GET /admin/hardware/inventory returns a populated HardwareInventory.
  2. GET /admin/hardware/model-fit?model=llama3-8b returns a can_run result.
  3. Missing PSK returns 401.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state() -> None:
    original_state = daemon_mod._daemon_state

    def _reset() -> None:
        if daemon_mod._daemon_state is None:
            daemon_mod._daemon_state = {"todos": [], "tick_metrics": {}, "quality_gate": {}}
            return
        daemon_mod._daemon_state["todos"] = []
        daemon_mod._daemon_state["tick_metrics"] = {}
        daemon_mod._daemon_state.setdefault("quality_gate", {})

    _reset()
    yield
    _reset()
    if original_state is None:
        daemon_mod._daemon_state = None


def _make_db_config(tmp_path: pytest.Path) -> tuple[str, str]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n")
    return str(config_dir), str(db_path)


async def _seed_default_project(app: Any) -> None:
    from sqlalchemy import select

    from general_ludd.db.models import ProjectModel

    factory = app.state._session_factory
    async with factory() as session:
        existing = await session.execute(select(ProjectModel).where(ProjectModel.project_id == "default"))
        if existing.scalar_one_or_none() is None:
            session.add(ProjectModel(project_id="default", name="Default project"))
            await session.commit()


class TestHardwareRouterWired:
    """Verify /admin/hardware/* endpoints are wired through the daemon."""

    def test_inventory_returns_hardware_snapshot(self, tmp_path: pytest.Path) -> None:
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                asyncio.run(_seed_default_project(app))
                assert app.state._hardware_inventory is not None

                resp = client.get("/admin/hardware/inventory")
                assert resp.status_code == 200
                data = resp.json()
                assert "gpus" in data
                assert "total_ram_gb" in data
                assert "disk_free_gb" in data
                assert "cpu_cores" in data
                assert isinstance(data["gpus"], list)
                assert data["cpu_cores"] >= 1

    def test_model_fit_returns_can_run_result(self, tmp_path: pytest.Path) -> None:
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                asyncio.run(_seed_default_project(app))
                assert app.state._hardware_inventory is not None

                resp = client.get("/admin/hardware/model-fit?model=llama3-8b")
                assert resp.status_code == 200
                data = resp.json()
                assert data["model"] == "llama3-8b"
                assert "can_run" in data
                assert "required_vram_gb" in data
                assert "available_gpu_vram_gb" in data

    def test_unknown_model_returns_fit_result(self, tmp_path: pytest.Path) -> None:
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                asyncio.run(_seed_default_project(app))

                resp = client.get("/admin/hardware/model-fit?model=unknown-model-x")
                assert resp.status_code == 200
                data = resp.json()
                assert data["model"] == "unknown-model-x"
                assert "can_run" in data

    def test_missing_psk_returns_401(self, tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_AUTH_PSK", "test-secret-key")
        config_dir, _db_path = _make_db_config(tmp_path)
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(tick_interval=300.0, config_dir=config_dir)
            with TestClient(app) as client:
                resp = client.get("/admin/hardware/inventory")
                assert resp.status_code == 401
