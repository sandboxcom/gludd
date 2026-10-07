"""Focused branches for the extracted daemon lifecycle component."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.config.user_config import UserConfig
from general_ludd.daemon import _lifespan, create_daemon_app
from tests.unit.test_daemon import _lifespan_patches


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "initialized", "started", "server_expected"),
    [
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (True, True, True, True),
    ],
)
async def test_searx_autostart_branches_survive_lifecycle_extraction(
    installed: bool,
    initialized: bool,
    started: bool,
    server_expected: bool,
) -> None:
    """Dynamic imports and shutdown ownership keep their original behavior."""
    mock_loop = MagicMock()
    mock_loop.run_forever = AsyncMock()
    server = MagicMock()
    server.ensure_started.return_value = started
    app = create_daemon_app(tick_interval=0.01)
    app.state._startup_config["user_config"] = UserConfig(searx_autostart=True)

    with (
        _lifespan_patches(mock_loop),
        patch(
            "general_ludd.searx.install.ensure_searx_installed",
            return_value=installed,
        ),
        patch(
            "general_ludd.searx.install.ensure_searx_initialized",
            return_value=initialized,
        ),
        patch("general_ludd.searx.server.SearXServer", return_value=server),
    ):
        async with _lifespan(app):
            if server_expected:
                assert app.state._searx_server is server
            else:
                assert getattr(app.state, "_searx_server", None) is None

    if server_expected:
        server.stop.assert_called_once_with()
    else:
        server.stop.assert_not_called()


@pytest.mark.asyncio
async def test_facade_patch_for_sts_reaper_failure_reaches_component() -> None:
    """The facade remains the patch-where-looked-up seam after extraction."""
    mock_loop = MagicMock()
    mock_loop.run_forever = AsyncMock()
    app = create_daemon_app(tick_interval=0.01)

    with (
        _lifespan_patches(mock_loop),
        patch(
            "general_ludd.daemon._build_sts_reaper",
            side_effect=RuntimeError("reaper unavailable"),
        ) as build_reaper,
    ):
        async with _lifespan(app):
            assert "_sts_reaper" not in app.state.daemon_state

    build_reaper.assert_called_once()


@pytest.mark.asyncio
async def test_project_templates_and_db_override_survive_extraction(tmp_path) -> None:
    """Project-scoped paths and the explicit DB override reach the component."""
    mock_loop = MagicMock()
    mock_loop.run_forever = AsyncMock()
    project_gludd = tmp_path / "project" / ".gludd"
    (project_gludd / "templates" / "log_output").mkdir(parents=True)
    override_path = tmp_path / "override.db"
    app = create_daemon_app(tick_interval=0.01)
    app.state._startup_config["project_gludd_dir"] = project_gludd
    app.state._db_path_override = str(override_path)
    engine = MagicMock()
    engine.dispose = AsyncMock()

    with (
        _lifespan_patches(mock_loop),
        patch(
            "general_ludd.daemon.init_engine_from_config",
            return_value=engine,
        ) as init_engine,
    ):
        async with _lifespan(app):
            assert app.state._prompt_registry is not None

    db_config = init_engine.call_args.args[0]
    assert db_config["url"] == f"sqlite+aiosqlite:///{override_path}"


@pytest.mark.asyncio
async def test_orphan_warning_and_sqlite_stamp_survive_extraction() -> None:
    """The extracted startup still probes orphans and stamps SQLite schemas."""
    mock_loop = MagicMock()
    mock_loop.run_forever = AsyncMock()
    orphan = MagicMock(job_id="job-from-prior-daemon")
    repository = MagicMock()
    repository.list_orphans = AsyncMock(return_value=[orphan])
    app = create_daemon_app(tick_interval=0.01)
    existing_deployment_manager = MagicMock()
    app.state._deployment_manager = existing_deployment_manager

    with (
        _lifespan_patches(mock_loop),
        patch(
            "general_ludd.daemon.SlurmJobRepository",
            return_value=repository,
        ),
        patch("general_ludd.daemon.is_sqlite_url", return_value=True),
        patch(
            "general_ludd.db.migrations.get_alembic_config",
            return_value=MagicMock(),
        ),
        patch("general_ludd.db.migrations.stamp_head") as stamp_head,
    ):
        async with _lifespan(app):
            assert app.state._deployment_manager is existing_deployment_manager

    repository.list_orphans.assert_awaited_once()
    stamp_head.assert_called_once()
