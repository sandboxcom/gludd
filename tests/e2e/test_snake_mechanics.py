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


def test_tick_before_start_is_a_noop() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    before = game.render_state()
    assert game.tick() is True
    assert game.state == "ready"
    assert game.render_state() == before


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


def test_start_accepts_menu_state_but_does_not_restart_active_game() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    game.state = "menu"
    game.start()
    assert game.state == "playing"
    game.input("up")
    game.start()
    assert game.state == "playing"
    assert game.direction == "up"


def test_restart_resets_terminal_and_progress_state() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    game.start()
    game.input("up")
    game.snake = [[0, 0]]
    game.direction = "left"
    game.game_over = True
    game.state = "game_over"
    game.score = 7

    game.restart()

    assert game.state == "ready"
    assert game.game_over is False
    assert game.score == 0
    assert game.direction == "right"
    assert game.snake == [[4, 4]]
    assert len(game.food) == 1


def test_invalid_input_does_not_change_direction() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    game.start()
    game.input("teleport")
    assert game.direction == "right"


def test_self_collision_is_terminal() -> None:
    game = _snake_class()(grid_w=8, grid_h=8)
    game.start()
    game.snake = [[2, 2], [2, 1], [1, 1], [1, 2]]
    game.food = []
    game.input("up")

    assert game.tick() is False
    assert game.state == "game_over"
    assert game.game_over is True


def test_full_board_has_no_food_cells() -> None:
    game = _snake_class()(grid_w=1, grid_h=1)
    assert game.snake == [[0, 0]]
    assert game.food == []
