"""Unit tests for the generated-code acceptance engine.

Pins that model-generated code is REJECTED for every observable failure
mode — syntax errors, missing game class, missing required methods,
dangerous imports, import-time hangs, and junk output — and ACCEPTED for a
valid snake-like game class.  The rejection event is what the game-gen role
consumes to trigger corrective-prompt retry and model fallback.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from general_ludd.game_gen.acceptance import AcceptanceResult, accept_generated_code

_VALID_GAME = textwrap.dedent(
    """
    class SnakeGame:
        def __init__(self):
            self.score_value = 0
            self.over = False

        def start(self):
            self.score_value = 0
            self.over = False

        def score(self) -> int:
            return self.score_value

        def is_game_over(self) -> bool:
            return self.over

        def tick(self, direction: str) -> None:
            self.score_value += 1

        def restart(self) -> None:
            self.start()
    """
)


@pytest.fixture
def code_file(tmp_path: Path):
    def _write(source: str) -> Path:
        p = tmp_path / "generated_game.py"
        p.write_text(textwrap.dedent(source))
        return p

    return _write


def test_valid_game_class_is_accepted(code_file) -> None:
    result = accept_generated_code(str(code_file(_VALID_GAME)))
    assert isinstance(result, AcceptanceResult)
    assert result.accepted is True
    assert result.reasons == []


def test_syntax_error_is_rejected(code_file) -> None:
    result = accept_generated_code(str(code_file("class SnakeGame:\n    def __init__(self:\n")))
    assert result.accepted is False
    assert any("syntax" in r for r in result.reasons)


def test_no_class_is_rejected(code_file) -> None:
    result = accept_generated_code(str(code_file("print('hello world')\nx = 1 + 1\n")))
    assert result.accepted is False
    assert any("class" in r for r in result.reasons)


def test_missing_required_methods_is_rejected(code_file) -> None:
    result = accept_generated_code(str(code_file("class Game:\n    def render(self):\n        pass\n")))
    assert result.accepted is False
    assert any("method" in r for r in result.reasons)


@pytest.mark.parametrize(
    "bad_import",
    [
        "import os\nos.system('rm -rf /')\nclass G:\n    def start(self): pass\n",
        "import subprocess\nsubprocess.run(['ls'])\nclass G:\n    def start(self): pass\n",
        "import socket\ns = socket.socket()\nclass G:\n    def start(self): pass\n",
        "class G:\n    def start(self):\n        eval('1+1')\n",
        "class G:\n    def start(self):\n        exec('print(1)')\n",
    ],
)
def test_dangerous_import_is_rejected(code_file, bad_import: str) -> None:
    result = accept_generated_code(str(code_file(bad_import)))
    assert result.accepted is False
    assert any("forbidden" in r for r in result.reasons)


def test_import_time_hang_is_rejected_with_timeout(code_file) -> None:
    hanging_game = textwrap.dedent(
        """
        class G:
            def __init__(self):
                self.v = 0

            def start(self):
                while True:
                    self.v += 1

            def tick(self, direction):
                pass

            def score(self):
                return self.v

            def is_game_over(self):
                return False

            def restart(self):
                pass
        """
    )
    result = accept_generated_code(
        str(code_file(hanging_game)),
        timeout_seconds=1.0,
    )
    assert result.accepted is False
    assert any("budget" in r or "timeout" in r or "timed" in r for r in result.reasons)


def test_junk_output_is_rejected(code_file) -> None:
    result = accept_generated_code(str(code_file("asdf jkl; \u00a1\u00bf\u00a1\u00bf\n")))
    assert result.accepted is False
    assert any("junk" in r or "syntax" in r for r in result.reasons)


def test_rejection_carries_all_reasons(code_file) -> None:
    result = accept_generated_code(str(code_file("import os\nprint('x')\n")))
    assert result.accepted is False
    assert any("forbidden" in r for r in result.reasons)
    assert any("class" in r for r in result.reasons)
