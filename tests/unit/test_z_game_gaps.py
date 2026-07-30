"""Unit tests for Z.1-Z.3 E2E game gap fixes.

Z.1: daemon pipeline claim_runnable returns 0 todos → regression coverage
Z.2: game_over/won flag mismatch — normalization in _check_lifecycle_game_over
Z.3: tetris gravity — board-diff fallback in _check_tetris_gravity
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import TodoStatus
from tests.e2e._game_lifecycle import (
    _check_lifecycle_game_over,
    _check_tetris_gravity,
)

_PROJECT_ID = "proj-z-regression"


@pytest.fixture
def _engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
async def _session_factory(_engine):
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(_engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await _engine.dispose()


def _make_loop(factory, runner):
    pm = MagicMock()
    pm.select_project.return_value = SimpleNamespace(project_id=_PROJECT_ID)
    loop = EventLoop(
        session=factory,
        runner=runner,
        task_return_repo=MagicMock(),
        config={"repo_root": "/tmp"},
        project_manager=pm,
    )
    loop._task_return_repo.claim_unreviewed = MagicMock(return_value=[])
    loop._runner = runner
    return loop


class TestZ1DaemonPipeline:
    """Z.1: claim_runnable returns todos and dispatch fires."""

    @pytest.mark.asyncio
    async def test_claim_returns_queued_todo_with_matching_project(self, _session_factory):
        async with _session_factory() as session:
            repo = TodoRepository(session)
            await repo.create(
                {
                    "todo_id": "Z1-001",
                    "title": "Z1 regression task",
                    "queue": "core",
                    "priority": 5,
                    "work_type": "code",
                    "status": TodoStatus.QUEUED.value,
                    "project_id": _PROJECT_ID,
                }
            )
            await session.commit()

        runner = MagicMock()
        runner.prepare_job_dirs = MagicMock(return_value={"root": "/tmp/z1"})
        runner.write_vars = MagicMock()
        runner.run_playbook = MagicMock()

        loop = _make_loop(_session_factory, runner)
        metrics = await loop.tick()
        claimed = loop._tick_state.get("claimed_todos", [])
        assert len(claimed) >= 1, "claim_runnable should return the queued todo"
        assert any(getattr(t, "todo_id", None) == "Z1-001" for t in claimed)
        assert metrics["phases_completed"] > 0


class _WonButNotOverGame:
    """Mock game: force_lose sets won=True but leaves game_over=False (Z.2 bug)."""

    def __init__(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self.won = False

    def start(self):
        if self.state == "ready":
            self.state = "playing"

    def tick(self):
        return not (self.state != "playing" or self.game_over)

    def restart(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self.won = False


def _force_lose_set_won_only(instance):
    """Sets won=True but deliberately does NOT set game_over=True."""
    instance.won = True
    return None


class TestZ2GameOverWonMismatch:
    """Z.2: _check_lifecycle_game_over normalizes won=True -> game_over=True."""

    def test_normalizes_game_over_when_won_is_true(self):
        game = _WonButNotOverGame()
        result = _check_lifecycle_game_over(game, _force_lose_set_won_only)
        assert result is None
        assert game.won is True
        assert game.game_over is True, "game_over should be normalized to True when won=True"

    def test_idempotent_check_sees_consistent_state(self):
        """After normalization, the idempotent check should see game_over=True."""
        from tests.e2e._game_lifecycle import _check_lifecycle_game_over_idempotent

        game = _WonButNotOverGame()
        _check_lifecycle_game_over(game, _force_lose_set_won_only)
        assert game.game_over is True
        result = _check_lifecycle_game_over_idempotent(game)
        assert result is None, f"idempotent check should pass after normalization: {result}"


class _NoGravityTetris:
    """Mock tetris: tick() does NOT move the piece (gravity bug Z.3)."""

    def __init__(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self.lines_cleared = 0
        self.grid = [[0] * 10 for _ in range(20)]
        self.grid[0][4] = 1
        self._active_row = 0

    def start(self):
        if self.state == "ready":
            self.state = "playing"

    def tick(self):
        return not (self.state != "playing" or self.game_over)

    def restart(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self.grid = [[0] * 10 for _ in range(20)]
        self.grid[0][4] = 1
        self._active_row = 0


class _GravityTetris:
    """Mock tetris: tick() moves the piece down one row (correct gravity)."""

    def __init__(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self.lines_cleared = 0
        self.grid = [[0] * 10 for _ in range(20)]
        self._active_row = 0
        self.grid[0][4] = 1

    def start(self):
        if self.state == "ready":
            self.state = "playing"

    def tick(self):
        if self.state != "playing" or self.game_over:
            return False
        self.grid[self._active_row][4] = 0
        self._active_row += 1
        if self._active_row >= len(self.grid):
            self.game_over = True
            return False
        self.grid[self._active_row][4] = 1
        return True

    def restart(self):
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self._active_row = 0
        self.grid = [[0] * 10 for _ in range(20)]
        self.grid[0][4] = 1


class TestZ3TetrisGravity:
    """Z.3: _check_tetris_gravity catches missing gravity via board-diff."""

    def test_detects_no_gravity(self):
        game = _NoGravityTetris()
        result = _check_tetris_gravity(game)
        assert result is not None, "should detect gravity not applied"
        assert "gravity" in result.lower()

    def test_passes_correct_gravity(self):
        game = _GravityTetris()
        result = _check_tetris_gravity(game)
        assert result is None, f"correct gravity should pass, got: {result}"

    def test_skips_when_no_grid(self):
        class _NoGrid:
            state = "playing"

            def tick(self):
                pass

        result = _check_tetris_gravity(_NoGrid())
        assert result is None
