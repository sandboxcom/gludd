"""Behavioral E2E checks for generated Snake game mechanics.

These assertions intentionally exercise game state transitions rather than
implementation names.  The source fixture is the same model response used by
``test_daemon_game_building`` so regressions in generated behavior are caught
before a pipeline result is accepted.
"""

from __future__ import annotations

import types

from tests.e2e.test_daemon_game_building import _SNAKE_MODULE


def _snake_class() -> type:
    source = _SNAKE_MODULE.removeprefix("```python\n").removesuffix("\n```")
    module = types.ModuleType("generated_snake_mechanics")
    exec(compile(source, "<generated-snake>", "exec"), module.__dict__)
    return module.Snake


def test_tick_advances_exactly_one_grid_cell() -> None:
    game = _snake_class()(grid_w=20, grid_h=20)
    game.start()
    before = list(game.snake[0])
    assert game.tick() is True
    after = game.snake[0]
    assert (after[0] - before[0], after[1] - before[1]) == (1, 0)


def test_reverse_input_is_rejected_and_perpendicular_turn_is_applied() -> None:
    game = _snake_class()(grid_w=20, grid_h=20)
    game.start()
    game.input("left")
    assert game.direction == "right"
    game.input("up")
    assert game.direction == "up"
    head = list(game.snake[0])
    assert game.tick() is True
    assert game.snake[0] == [head[0], head[1] - 1]


def test_wall_collision_is_terminal_and_tick_is_idempotent_after_game_over() -> None:
    game = _snake_class()(grid_w=2, grid_h=2)
    game.start()
    assert game.tick() is False
    assert game.game_over is True
    snapshot = game.render_state()
    assert game.tick() is False
    assert game.render_state() == snapshot


def test_food_consumption_grows_body_and_increments_score() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    game.start()
    head_x, head_y = game.snake[0]
    game.food = [[head_x + 1, head_y]]
    assert game.tick() is True
    assert game.score == 1
    assert len(game.snake) == 2
    assert game.snake[0] == [head_x + 1, head_y]
