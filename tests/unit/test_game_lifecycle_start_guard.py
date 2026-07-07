"""Unit tests for the start()-before-tick guard in tests.e2e._game_lifecycle.

Regression coverage for the bug where the lifecycle harness ticked games N
times to verify state changes but never called ``start()`` first.  Games
implementing the new prompt spec correctly start in a 'ready' state and
``tick()`` short-circuits until ``start()`` transitions to 'playing', so
the harness reported false failures ("state did not change across N ticks").
"""

from __future__ import annotations

from tests.e2e._game_lifecycle import (
    _check_lifecycle_game_over,
    _check_lifecycle_score_increments,
    _invoke_start_method,
    run_lifecycle_checks,
)


class _ReadyStateSnake:
    """Mock game that follows the new prompt spec lifecycle.

    state transitions: ready -> start() -> playing -> tick() advances ->
    eventually game_over.  Mirrors the generated snake that previously
    triggered the false 'state did not change across 8 ticks' failure.
    """

    def __init__(self) -> None:
        self.state: str = "ready"
        self.score: int = 0
        self.game_over: bool = False
        self.body: list[list[int]] = [[5, 5], [4, 5], [3, 5]]
        self.grid_w: int = 20
        self.grid_h: int = 20
        self._ticks = 0

    def start(self) -> None:
        if self.state == "ready":
            self.state = "playing"

    def tick(self) -> bool:
        if self.state != "playing" or self.game_over:
            return False
        head = self.body[0]
        # React to out-of-bounds head (matches real generated snake behavior;
        # _force_lose_snake teleports head past grid_w/grid_h to force terminal).
        if head[0] < 0 or head[0] >= self.grid_w or head[1] < 0 or head[1] >= self.grid_h:
            self.game_over = True
            return False
        new_head = [head[0] + 1, head[1]]
        if new_head[0] >= self.grid_w:
            self.game_over = True
            return False
        self.body.insert(0, new_head)
        self.body.pop()
        self._ticks += 1
        self.score += 1
        if self._ticks >= 3:
            self.game_over = True
        return True

    def restart(self) -> None:
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self._ticks = 0
        self.body = [[5, 5], [4, 5], [3, 5]]


class _AutoStartSnake:
    """Mock game with no start() — tick() auto-transitions on first call."""

    def __init__(self) -> None:
        self.state: str = "ready"
        self.score: int = 0
        self.game_over: bool = False
        self.body: list[list[int]] = [[1, 1]]
        self._ticks = 0

    def tick(self) -> bool:
        if self.state == "ready":
            self.state = "playing"
        if self.state != "playing" or self.game_over:
            return False
        self.body[0] = [self.body[0][0] + 1, self.body[0][1]]
        self._ticks += 1
        self.score += 1
        if self._ticks >= 2:
            self.game_over = True
        return True

    def restart(self) -> None:
        self.state = "ready"
        self.score = 0
        self.game_over = False
        self._ticks = 0


class _BrokenStartSnake:
    """Mock game whose start() exists but does not transition state."""

    def __init__(self) -> None:
        self.state: str = "ready"
        self.score: int = 0
        self.game_over: bool = False

    def start(self) -> None:
        pass  # broken: leaves state in 'ready'

    def tick(self) -> bool:
        return False  # short-circuits like a correct new-spec game

    def restart(self) -> None:
        self.state = "ready"
        self.score = 0
        self.game_over = False


def _force_lose_snake(instance: object) -> str | None:
    """Drive _ReadyStateSnake to game_over by ticking until terminal."""
    for _ in range(10):
        tick = getattr(instance, "tick", None)
        if callable(tick):
            tick()
        if getattr(instance, "game_over", False):
            return None
    return None


def test_invoke_start_method_transitions_ready_to_playing() -> None:
    """_invoke_start_method calls start() and transitions ready -> playing."""
    snake = _ReadyStateSnake()
    assert snake.state == "ready"
    result = _invoke_start_method(snake)
    assert result is None
    assert snake.state == "playing"


def test_invoke_start_method_idempotent_when_already_playing() -> None:
    """Calling _invoke_start_method after start() is a no-op."""
    snake = _ReadyStateSnake()
    snake.start()
    assert snake.state == "playing"
    result = _invoke_start_method(snake)
    assert result is None
    assert snake.state == "playing"
    assert snake.score == 0  # no ticks fired


def test_invoke_start_method_falls_back_to_tick_autostart() -> None:
    """When no start() exists, _invoke_start_method ticks once to auto-start."""
    snake = _AutoStartSnake()
    assert snake.state == "ready"
    result = _invoke_start_method(snake)
    assert result is None
    assert snake.state == "playing"


def test_invoke_start_method_reports_broken_start() -> None:
    """When start() exists but leaves state in 'ready', helper reports it."""
    snake = _BrokenStartSnake()
    result = _invoke_start_method(snake)
    assert result is not None
    assert "stayed at 'ready'" in result


def test_invoke_start_method_noop_without_state_attr() -> None:
    """Games without a state attribute are tolerated (no-op)."""

    class NoState:
        def start(self) -> None:
            pass

    result = _invoke_start_method(NoState())
    assert result is None


def test_check_lifecycle_score_increments_calls_start_first() -> None:
    """Regression: score_increments must call start() before its tick loop.

    Previously this helper ticked 20 times without calling start(), and
    a correct new-spec game (tick short-circuits until start) would fail
    with 'score did not increment'.  It must now pass.
    """
    snake = _ReadyStateSnake()
    result = _check_lifecycle_score_increments(snake, n_ticks=10)
    assert result is None
    assert snake.score > 0


def test_check_lifecycle_game_over_calls_start_first() -> None:
    """Regression: game_over check must call start() before force_lose.

    force_lose strategies tick the game; without start(), those ticks
    short-circuit and the terminal flag never sets.
    """
    snake = _ReadyStateSnake()
    result = _check_lifecycle_game_over(snake, _force_lose_snake)
    assert result is None
    assert snake.game_over is True


def test_run_lifecycle_checks_passes_correct_new_spec_game() -> None:
    """End-to-end: run_lifecycle_checks must PASS a correct new-spec game.

    Builds a fake module containing _ReadyStateSnake and runs the full
    7-check lifecycle.  All checks must pass (empty failure list).
    """
    import types
    mod = types.ModuleType("fake_snake_module")
    # _discover_game_class filters by __module__ == mod.__name__, so the
    # mock class must report its module as the fake module's name.
    _ReadyStateSnake.__module__ = "fake_snake_module"
    mod.Snake = _ReadyStateSnake  # type: ignore[attr-defined]
    failures = run_lifecycle_checks("snake", mod)
    assert failures == [], f"unexpected lifecycle failures: {failures}"
