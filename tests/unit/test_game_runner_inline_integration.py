"""Current-base regression coverage for the inline game runner boundary."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from general_ludd.cloud import game_e2e
from general_ludd.cloud.game_e2e import GameInputEvent, GameRunner


def _pygame_double() -> MagicMock:
    pygame_double = MagicMock()
    surface = MagicMock()
    surface.copy.return_value = surface
    pygame_double.display.set_mode.return_value = surface
    pygame_double.display.flip = MagicMock()
    pygame_double.display.update = MagicMock()
    pygame_double.event.get = MagicMock(return_value=[])
    pygame_double.event.Event.side_effect = (
        lambda event_type, **fields: SimpleNamespace(type=event_type, **fields)
    )
    pygame_double.surfarray.array3d.return_value = np.zeros(
        (800, 600, 3),
        dtype=np.uint8,
    )
    pygame_double.K_w = 1
    pygame_double.K_a = 2
    pygame_double.K_s = 3
    pygame_double.K_d = 4
    pygame_double.K_SPACE = 5
    pygame_double.K_ESCAPE = 6
    pygame_double.K_RETURN = 7
    pygame_double.MOUSEMOTION = 8
    pygame_double.KEYDOWN = 9
    return pygame_double


def _write_inline_game(game_path: Path, statements: str) -> None:
    game_path.write_text(
        "from general_ludd.cloud import game_e2e\n" + statements,
        encoding="utf-8",
    )


def _isolate_dynamic_module(monkeypatch: pytest.MonkeyPatch, game_path: Path) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, game_path.stem, ModuleType(game_path.stem))


def test_inline_runner_captures_scripted_keyboard_and_mouse_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pygame_double = _pygame_double()
    original_flip = pygame_double.display.flip
    original_update = pygame_double.display.update
    original_event_get = pygame_double.event.get
    game_path = tmp_path / "scripted_inline_game.py"
    _write_inline_game(
        game_path,
        "game_e2e.pygame.event.get()\n"
        "game_e2e.pygame.display.flip()\n"
        "game_e2e.pygame.event.get()\n"
        "game_e2e.pygame.display.update()\n",
    )
    _isolate_dynamic_module(monkeypatch, game_path)
    monkeypatch.setattr(game_e2e, "pygame", pygame_double)

    runner = GameRunner()
    frames = runner.run_headless_inline(
        str(game_path),
        2,
        input_script=(
            GameInputEvent(frame=0, control="mouse"),
            GameInputEvent(frame=1, control="w"),
        ),
    )

    assert [frame.shape for frame in frames] == [(600, 800, 3), (600, 800, 3)]
    assert runner.last_injected_controls == {"mouse", "w"}
    assert pygame_double.event.Event.call_count == 2
    assert pygame_double.display.flip is original_flip
    assert pygame_double.display.update is original_update
    assert pygame_double.event.get is original_event_get
    original_flip.assert_called_once_with()
    original_update.assert_not_called()


def test_inline_runner_restores_hooks_after_invalid_scripted_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pygame_double = _pygame_double()
    original_flip = pygame_double.display.flip
    original_update = pygame_double.display.update
    original_event_get = pygame_double.event.get
    game_path = tmp_path / "invalid_inline_game.py"
    _write_inline_game(game_path, "game_e2e.pygame.event.get()\n")
    _isolate_dynamic_module(monkeypatch, game_path)
    monkeypatch.setattr(game_e2e, "pygame", pygame_double)

    with pytest.raises(ValueError, match="Unsupported scripted game control: jump"):
        GameRunner().run_headless_inline(
            str(game_path),
            1,
            input_script=(GameInputEvent(frame=0, control="jump"),),
        )

    assert pygame_double.display.flip is original_flip
    assert pygame_double.display.update is original_update
    assert pygame_double.event.get is original_event_get
