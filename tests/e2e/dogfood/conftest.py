"""Shared fixtures for the dogfood E2E harness.

All heavy gludd imports are DEFERRED (inside fixture bodies) so collection is
import-safe even when gludd has no DB, no secrets, and no running daemon.

See docs/e2e_harness/DESIGN_native_dogfood_harness.md §9 for the fixture table.
"""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: end-to-end test requiring a real environment")
    config.addinivalue_line("markers", "live_zai: requires a live z.ai key in .secrets/llm_keys.env")


# ---------------------------------------------------------------------------
# Repo root
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root (contains pyproject.toml)."""
    candidate = Path(__file__).resolve()
    for _ in range(10):
        candidate = candidate.parent
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


# ---------------------------------------------------------------------------
# Secrets + gateway mode
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def zai_creds(repo_root: Path) -> dict[str, str] | None:
    """Load ZAI credentials from .secrets/llm_keys.env.

    Returns None when the file is absent or ZAI_API_KEY is not set.
    Tests that need a live key should call:
        if zai_creds is None:
            pytest.skip("no ZAI_API_KEY — live z.ai tests skipped")
    """
    from tests.e2e.dogfood._secrets import load_llm_keys
    return load_llm_keys(repo_root)


@pytest.fixture(scope="session")
def gateway_mode(zai_creds: dict[str, str] | None) -> str:
    """'live' when a real key is available; 'mock' otherwise."""
    return "live" if zai_creds is not None else "mock"


# ---------------------------------------------------------------------------
# Temporary workspace
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_workspace() -> Any:
    """Yield a fresh temporary directory; auto-delete on teardown."""
    import shutil
    d = tempfile.mkdtemp(prefix="gludd-e2e-")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# In-process FastAPI + in-memory SQLite app
# ---------------------------------------------------------------------------

@pytest.fixture()
async def inproc_app() -> Any:
    """Yield (app, client, session_factory, engine); auto-dispose on teardown.

    Creates a real FastAPI app with the todos router and an in-memory SQLite
    database (no footprint on disk, no interference with the developer's real DB).
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from general_ludd.daemon import _daemon_state
    from general_ludd.db.models import Base
    from general_ludd.routers.todos import register as reg_todos

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    state: dict[str, Any] = {"todos": []}
    _daemon_state["todos"] = state["todos"]

    app = FastAPI()
    app.state._session_factory = factory
    app.state._config_dir = None
    app.state._startup_config = {}
    app.state.log_level = "info"
    app.state.tick_interval = 1.0
    app.state.event_loop = None
    app.state._templates_dir = None
    app.state._playbooks_dir = None
    reg_todos(app, _daemon_state)

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield app, client, factory, engine
    finally:
        await client.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Free port helper (reused from tests/e2e/conftest.py pattern)
# ---------------------------------------------------------------------------

@pytest.fixture()
def free_port() -> int:
    """Return an ephemeral free TCP port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Build-gateway factory
# ---------------------------------------------------------------------------

@pytest.fixture()
def build_gateway_fn(zai_creds: dict[str, str] | None) -> Any:
    """Return the build_gateway factory pre-bound to the session's zai_creds."""
    from tests.e2e.dogfood._gateway import build_gateway
    def _factory(mock_response: str = "") -> tuple[Any, str]:
        return build_gateway(zai_creds, mock_response=mock_response)
    return _factory
