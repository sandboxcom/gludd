"""Commit-gate-compliant smoke suite for the generated-code acceptance engine.

``src/general_ludd/game_gen/acceptance.py`` pairs with this file under the TDD
commit gate's candidate test naming. The canonical, exhaustive acceptance suite
lives in ``tests/unit/test_generated_code_acceptance.py``.
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.game_gen.acceptance import AcceptanceResult, check_file, check_source, main

MINIMAL_GAME = """
class Snake:
    def __init__(self):
        self._score = 0
        self._game_over = False

    def start(self):
        self.restart()

    def restart(self):
        self._score = 0
        self._game_over = False

    def tick(self, direction):
        if direction in ("up", "down", "left", "right"):
            self._score += 1

    def score(self) -> int:
        return self._score

    def is_game_over(self) -> bool:
        return self._game_over
"""


def test_check_source_accepts_well_formed_game() -> None:
    result = check_source(MINIMAL_GAME)
    assert isinstance(result, AcceptanceResult)
    assert result.accepted is True
    assert result.game_class_name == "Snake"


def test_check_source_rejects_source_without_class() -> None:
    result = check_source("print('no game here')")
    assert result.accepted is False
    assert any("game class" in reason for reason in result.reasons)


def test_check_file_rejects_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "broken.py"
    bad.write_text("class Snake\n    def __init__(self\n        pass\n")
    result = check_file(bad)
    assert result.accepted is False
    assert any("syntax" in reason for reason in result.reasons)


def test_main_exit_codes_follow_verdict(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text(MINIMAL_GAME)
    bad = tmp_path / "bad.py"
    bad.write_text("{{{ this is not python }}")
    assert main([str(good)]) == 0
    assert main([str(bad)]) == 1
