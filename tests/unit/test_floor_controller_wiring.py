"""Unit tests for FloorController wiring in the event loop and daemon."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from general_ludd.controllers.floor import FloorController


class TestFloorController:
    def test_default_floor_is_ten(self) -> None:
        fc = FloorController()
        assert fc.floor == 10

    def test_env_var_overrides_floor(self, monkeypatch) -> None:
        monkeypatch.setenv("FLOOR", "15")
        fc = FloorController()
        assert fc.floor == 15

    def test_explicit_floor_takes_precedence(self, monkeypatch) -> None:
        monkeypatch.setenv("FLOOR", "15")
        fc = FloorController(floor=8)
        assert fc.floor == 8

    def test_full_health_returns_floor(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(100.0)
        assert fc.get_max_active() == 10

    def test_health_below_50_halves_cap(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(30.0)
        assert fc.get_max_active() == 5

    def test_health_below_25_blocks_dispatch(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(10.0)
        assert fc.get_max_active() == 0

    def test_health_at_exactly_50_returns_full(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(50.0)
        assert fc.get_max_active() == 10

    def test_health_at_exactly_25_returns_half(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(25.0)
        assert fc.get_max_active() == 5

    def test_odd_floor_half_rounds_down(self) -> None:
        fc = FloorController(floor=11)
        fc.update_health(30.0)
        assert fc.get_max_active() == 5

    def test_health_floor_of_one_with_below_50_still_one(self) -> None:
        fc = FloorController(floor=1)
        fc.update_health(30.0)
        assert fc.get_max_active() == 1

    def test_get_max_active_returns_int(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(100.0)
        assert isinstance(fc.get_max_active(), int)

    def test_health_clamped_to_zero(self) -> None:
        fc = FloorController()
        fc.update_health(-5.0)
        assert fc.health == 0.0

    def test_health_clamped_to_hundred(self) -> None:
        fc = FloorController()
        fc.update_health(150.0)
        assert fc.health == 100.0


class FakeTodo:
    def __init__(self, todo_id: str, version: int = 1):
        self.todo_id = todo_id
        self.version = version
        self.queue = "core"


class TestEventLoopFloorCap:
    """Verify the claim phase applies the floor-cap and releases excess."""

    @patch("general_ludd.event_loop.lease.acquire_lease", new_callable=AsyncMock)
    async def test_floor_cap_releases_excess_todos(self, _mock_acquire_lease) -> None:
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.claim_runnable.return_value = [
            FakeTodo(f"todo-{i}") for i in range(10)
        ]
        todo_repo.transition = AsyncMock()

        fc = FloorController(floor=3)

        loop = EventLoop(
            todo_repo=todo_repo,
            session=AsyncMock(),
            floor_controller=fc,
        )
        loop._tick_project_id = "p1"
        loop._active_session = AsyncMock()

        await loop._phase_claim_runnable_todos()

        claimed = loop._tick_state["claimed_todos"]
        assert len(claimed) == 3
        assert todo_repo.transition.call_count == 7

    @patch("general_ludd.event_loop.lease.acquire_lease", new_callable=AsyncMock)
    async def test_no_floor_controller_claims_all(self, _mock_acquire_lease) -> None:
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.claim_runnable.return_value = [
            FakeTodo(f"todo-{i}") for i in range(10)
        ]

        loop = EventLoop(
            todo_repo=todo_repo,
            session=AsyncMock(),
        )
        loop._tick_project_id = "p1"
        loop._active_session = AsyncMock()

        await loop._phase_claim_runnable_todos()

        claimed = loop._tick_state["claimed_todos"]
        assert len(claimed) == 10
        todo_repo.transition.assert_not_called()

    @patch("general_ludd.event_loop.lease.acquire_lease", new_callable=AsyncMock)
    async def test_health_zero_blocks_all_claims(self, _mock_acquire_lease) -> None:
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.claim_runnable.return_value = [
            FakeTodo(f"todo-{i}") for i in range(5)
        ]
        todo_repo.transition = AsyncMock()

        fc = FloorController(floor=10)
        fc.update_health(0.0)

        loop = EventLoop(
            todo_repo=todo_repo,
            session=AsyncMock(),
            floor_controller=fc,
        )
        loop._tick_project_id = "p1"
        loop._active_session = AsyncMock()

        await loop._phase_claim_runnable_todos()

        claimed = loop._tick_state["claimed_todos"]
        assert len(claimed) == 0
        assert todo_repo.transition.call_count == 5

    @patch("general_ludd.event_loop.lease.acquire_lease", new_callable=AsyncMock)
    async def test_excess_transitioned_to_queued(self, _mock_acquire_lease) -> None:
        from general_ludd.event_loop.loop import EventLoop
        from general_ludd.schemas.todo import TodoStatus

        todo_repo = AsyncMock()
        todos = [FakeTodo(f"todo-{i}") for i in range(8)]
        todo_repo.claim_runnable.return_value = todos
        todo_repo.transition = AsyncMock()

        fc = FloorController(floor=4)

        loop = EventLoop(
            todo_repo=todo_repo,
            session=AsyncMock(),
            floor_controller=fc,
        )
        loop._tick_project_id = "p1"
        loop._active_session = AsyncMock()

        await loop._phase_claim_runnable_todos()

        claimed = loop._tick_state["claimed_todos"]
        assert len(claimed) == 4
        assert todo_repo.transition.call_count == 4
        for i in range(4):
            call_args = todo_repo.transition.call_args_list[i].args
            assert call_args[0] == f"todo-{i + 4}"
            assert call_args[1] == TodoStatus.QUEUED

    @patch("general_ludd.event_loop.lease.acquire_lease", new_callable=AsyncMock)
    async def test_health_modulates_cap(self, _mock_acquire_lease) -> None:
        from general_ludd.event_loop.loop import EventLoop

        todo_repo = AsyncMock()
        todo_repo.claim_runnable.return_value = [
            FakeTodo(f"todo-{i}") for i in range(20)
        ]
        todo_repo.transition = AsyncMock()

        fc = FloorController(floor=10)
        fc.update_health(30.0)  # half -> cap 5

        loop = EventLoop(
            todo_repo=todo_repo,
            session=AsyncMock(),
            floor_controller=fc,
        )
        loop._tick_project_id = "p1"
        loop._active_session = AsyncMock()

        await loop._phase_claim_runnable_todos()

        claimed = loop._tick_state["claimed_todos"]
        assert len(claimed) == 5
        assert todo_repo.transition.call_count == 15
