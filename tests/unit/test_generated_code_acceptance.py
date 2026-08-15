"""Acceptance tests for the generated-code rejection engine.

Pins the contract of ``general_ludd.game_gen.acceptance``: a single
``AcceptanceResult`` (accept/reject + reasons) produced from generated source
text, with a hard runtime budget. Every failure mode a misbehaving model can
produce must end in rejection:

* syntax errors
* no game class
* missing required methods
* dangerous imports (os, subprocess, socket) and calls (eval/exec/open)
* infinite loops (hard timeout)
* junk output instead of a class
* non-ASCII junk
* excessive or non-ASCII output even when the class itself is valid
"""

from __future__ import annotations

import inspect
from pathlib import Path

from general_ludd.game_gen.acceptance import (
    AcceptanceResult,
    accept_generated_code,
    check_file,
    check_source,
    main,
)

SNAKE_GAME_CODE = """
import random

class Snake:
    def __init__(self):
        self.grid_w = 20
        self.grid_h = 20
        self.restart()

    def start(self):
        self.restart()

    def restart(self):
        self.body = [(10, 10), (9, 10), (8, 10)]
        self.direction = "right"
        self._score = 0
        self._game_over = False
        self.food = self._place_food()

    def _place_food(self):
        while True:
            fx = random.randint(0, self.grid_w - 1)
            fy = random.randint(0, self.grid_h - 1)
            if (fx, fy) not in self.body:
                return (fx, fy)

    def tick(self, direction):
        if self._game_over:
            return
        dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        head = (self.body[0][0] + dx, self.body[0][1] + dy)
        if not (0 <= head[0] < self.grid_w and 0 <= head[1] < self.grid_h):
            self._game_over = True
            return
        if head in self.body:
            self._game_over = True
            return
        self.body.insert(0, head)
        if head == self.food:
            self._score += 1
            self.food = self._place_food()
        else:
            self.body.pop()

    def score(self) -> int:
        return self._score

    def is_game_over(self) -> bool:
        return self._game_over
"""

SYNTAX_ERROR_CODE = "class Snake\n    def __init__(self\n        pass\n"

NO_CLASS_CODE = "import random\nprint('the game is coming soon!')\n"

JUNK_PRINT_NO_CLASS_CODE = 'print("🎮🎮🎮 here is your game 🐍🐍🐍")\nprint("junk " * 100)\n'

NON_ASCII_JUNK_CODE = "🎮🐍🎮🐍🎮🐍" * 40 + "\n"

MISSING_METHODS_CODE = """
class Snake:
    def __init__(self):
        self._score = 0
    def start(self):
        pass
"""

OS_IMPORT_CODE = SNAKE_GAME_CODE + "\nimport os\n"

OS_FROM_IMPORT_CODE = SNAKE_GAME_CODE + "\nfrom os import system\n"

OS_ALIASED_IMPORT_CODE = SNAKE_GAME_CODE + "\nimport os as _o\n"

SUBPROCESS_IMPORT_CODE = SNAKE_GAME_CODE + "\nimport subprocess\n"

SOCKET_IMPORT_CODE = SNAKE_GAME_CODE + "\nimport socket\n"

EVAL_CALL_CODE = SNAKE_GAME_CODE + "\nSnake.tick = lambda self, direction: eval('print(1)')\n"

EXEC_CALL_CODE = SNAKE_GAME_CODE + "\nSnake.tick = lambda self, direction: exec('print(1)')\n"

OPEN_CALL_CODE = SNAKE_GAME_CODE + "\nSnake.tick = lambda self, direction: open('/etc/passwd').read()\n"

IO_OPEN_CALL_CODE = (
    SNAKE_GAME_CODE + "\nimport io\nSnake.tick = lambda self, direction: io.open('/etc/passwd').read()\n"
)

INFINITE_LOOP_CODE = (
    SNAKE_GAME_CODE + "\ndef _hang(self, direction):\n    while True:\n        pass\nSnake.tick = _hang\n"
)

EXCESSIVE_OUTPUT_CODE = 'print("x" * 8192)\n' + SNAKE_GAME_CODE

NON_ASCII_OUTPUT_CODE = 'print("🎮" * 500)\n' + SNAKE_GAME_CODE

OS_SYSTEM_CALL_CODE = SNAKE_GAME_CODE + "\nimport os\nos.system('echo pwned')\n"

SUBPROCESS_RUN_CALL_CODE = SNAKE_GAME_CODE + "\nimport subprocess\nsubprocess.run(['echo', 'pwned'])\n"

SOCKET_CALL_CODE = SNAKE_GAME_CODE + "\nimport socket\nsock = socket.socket()\n"

IMPORT_LEVEL_HANG_CODE = SNAKE_GAME_CODE + "\nwhile True:\n    pass\n"


def _joined_reasons(result: AcceptanceResult) -> str:
    return " | ".join(result.reasons)


class TestGeneratedCodeAcceptance:
    """The acceptance engine must accept good code and reject all defect classes."""

    # ── Acceptance of valid code ────────────────────────────────────────────

    def test_valid_game_is_accepted(self) -> None:
        result = check_source(SNAKE_GAME_CODE)
        assert result.accepted is True
        assert result.game_class_name == "Snake"
        assert result.reasons == []

    def test_result_exposes_elapsed_seconds(self) -> None:
        result = check_source(SNAKE_GAME_CODE)
        assert result.elapsed_seconds >= 0.0

    # ── Syntax ──────────────────────────────────────────────────────────────

    def test_syntax_error_is_rejected(self) -> None:
        result = check_source(SYNTAX_ERROR_CODE)
        assert result.accepted is False
        assert "syntax" in _joined_reasons(result)

    # ── Missing game class ──────────────────────────────────────────────────

    def test_source_without_game_class_is_rejected(self) -> None:
        result = check_source(NO_CLASS_CODE)
        assert result.accepted is False
        assert "game class" in _joined_reasons(result)

    def test_junk_print_module_without_class_is_rejected(self) -> None:
        result = check_source(JUNK_PRINT_NO_CLASS_CODE)
        assert result.accepted is False
        assert "game class" in _joined_reasons(result)

    def test_non_ascii_junk_source_is_rejected(self) -> None:
        result = check_source(NON_ASCII_JUNK_CODE)
        assert result.accepted is False
        assert "non-ASCII" in _joined_reasons(result)

    # ── Missing required methods ────────────────────────────────────────────

    def test_class_missing_required_methods_is_rejected(self) -> None:
        result = check_source(MISSING_METHODS_CODE)
        assert result.accepted is False
        joined = _joined_reasons(result)
        assert "tick" in joined
        assert "score" in joined
        assert "is_game_over" in joined
        assert "restart" in joined

    # ── Forbidden imports ───────────────────────────────────────────────────

    def test_os_import_is_rejected(self) -> None:
        result = check_source(OS_IMPORT_CODE)
        assert result.accepted is False
        assert "os" in _joined_reasons(result)

    def test_os_system_from_import_is_rejected(self) -> None:
        result = check_source(OS_FROM_IMPORT_CODE)
        assert result.accepted is False
        assert "os" in _joined_reasons(result)

    def test_aliased_os_import_is_rejected(self) -> None:
        result = check_source(OS_ALIASED_IMPORT_CODE)
        assert result.accepted is False
        assert "os" in _joined_reasons(result)

    def test_subprocess_import_is_rejected(self) -> None:
        result = check_source(SUBPROCESS_IMPORT_CODE)
        assert result.accepted is False
        assert "subprocess" in _joined_reasons(result)

    def test_socket_import_is_rejected(self) -> None:
        result = check_source(SOCKET_IMPORT_CODE)
        assert result.accepted is False
        assert "socket" in _joined_reasons(result)

    # ── Forbidden calls ─────────────────────────────────────────────────────

    def test_eval_call_is_rejected(self) -> None:
        result = check_source(EVAL_CALL_CODE)
        assert result.accepted is False
        assert "eval" in _joined_reasons(result)

    def test_exec_call_is_rejected(self) -> None:
        result = check_source(EXEC_CALL_CODE)
        assert result.accepted is False
        assert "exec" in _joined_reasons(result)

    def test_open_call_is_rejected(self) -> None:
        result = check_source(OPEN_CALL_CODE)
        assert result.accepted is False
        assert "open" in _joined_reasons(result)

    def test_io_open_call_is_rejected(self) -> None:
        result = check_source(IO_OPEN_CALL_CODE)
        assert result.accepted is False
        assert "open" in _joined_reasons(result)

    # ── Hard runtime timeout ────────────────────────────────────────────────

    def test_infinite_loop_is_rejected_within_timeout_window(self) -> None:
        result = check_source(INFINITE_LOOP_CODE, timeout=0.5)
        assert result.accepted is False
        assert "runtime budget" in _joined_reasons(result)
        assert result.elapsed_seconds < 2.0

    # ── Junk output ─────────────────────────────────────────────────────────

    def test_valid_class_with_excessive_output_is_rejected(self) -> None:
        result = check_source(EXCESSIVE_OUTPUT_CODE)
        assert result.accepted is False
        assert "excessive output" in _joined_reasons(result)

    def test_valid_class_with_non_ascii_output_is_rejected(self) -> None:
        result = check_source(NON_ASCII_OUTPUT_CODE)
        assert result.accepted is False
        assert "non-ASCII" in _joined_reasons(result)

    # ── File and CLI entry points ───────────────────────────────────────────

    def test_check_file_accepts_valid_file(self, tmp_path: Path) -> None:
        game_path = tmp_path / "snake.py"
        game_path.write_text(SNAKE_GAME_CODE)
        result = check_file(game_path)
        assert result.accepted is True
        assert result.game_class_name == "Snake"

    def test_check_file_rejects_bad_file(self, tmp_path: Path) -> None:
        game_path = tmp_path / "bad.py"
        game_path.write_text(SYNTAX_ERROR_CODE)
        result = check_file(game_path)
        assert result.accepted is False
        assert "syntax" in _joined_reasons(result)

    def test_cli_exits_zero_on_accept(self, tmp_path: Path) -> None:
        game_path = tmp_path / "good.py"
        game_path.write_text(SNAKE_GAME_CODE)
        assert main([str(game_path)]) == 0

    def test_cli_exits_one_on_reject(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(SNAKE_GAME_CODE + "\nimport socket\n")
        assert main([str(game_path)]) == 1

    def test_cli_exits_one_on_timeout(self, tmp_path: Path) -> None:
        game_path = tmp_path / "hang.py"
        game_path.write_text(INFINITE_LOOP_CODE)
        assert main(["--timeout", "0.5", str(game_path)]) == 1


class TestAcceptGeneratedCodeApi:
    """``accept_generated_code`` — subprocess-isolated, bounded-runtime verdicts."""

    def test_signature_defaults_to_ten_second_budget(self) -> None:
        signature = inspect.signature(accept_generated_code)
        assert signature.parameters["timeout_seconds"].default == 10.0

    def test_valid_snake_game_is_accepted(self, tmp_path: Path) -> None:
        game_path = tmp_path / "snake.py"
        game_path.write_text(SNAKE_GAME_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is True
        assert result.game_class_name == "Snake"
        assert result.reasons == []

    def test_syntax_error_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "broken.py"
        game_path.write_text(SYNTAX_ERROR_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "syntax" in " | ".join(result.reasons)

    def test_source_without_class_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "noclass.py"
        game_path.write_text(NO_CLASS_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "game class" in " | ".join(result.reasons)

    def test_junk_output_without_class_definition_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "junk.py"
        game_path.write_text(JUNK_PRINT_NO_CLASS_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "game class" in " | ".join(result.reasons)

    def test_missing_required_methods_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "partial.py"
        game_path.write_text(MISSING_METHODS_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        joined = " | ".join(result.reasons)
        assert "tick" in joined
        assert "score" in joined
        assert "is_game_over" in joined
        assert "restart" in joined

    def test_os_system_call_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(OS_SYSTEM_CALL_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "os" in " | ".join(result.reasons)

    def test_subprocess_import_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(SUBPROCESS_RUN_CALL_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "subprocess" in " | ".join(result.reasons)

    def test_socket_import_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(SOCKET_CALL_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "socket" in " | ".join(result.reasons)

    def test_eval_call_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(EVAL_CALL_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "eval" in " | ".join(result.reasons)

    def test_exec_call_is_rejected(self, tmp_path: Path) -> None:
        game_path = tmp_path / "evil.py"
        game_path.write_text(EXEC_CALL_CODE)
        result = accept_generated_code(str(game_path))
        assert result.accepted is False
        assert "exec" in " | ".join(result.reasons)

    def test_infinite_loop_at_import_is_rejected_within_timeout_window(self, tmp_path: Path) -> None:
        game_path = tmp_path / "hang.py"
        game_path.write_text(IMPORT_LEVEL_HANG_CODE)
        result = accept_generated_code(str(game_path), timeout_seconds=0.5)
        assert result.accepted is False
        assert "runtime budget" in " | ".join(result.reasons)
        assert result.elapsed_seconds < 2.0

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        result = accept_generated_code(str(tmp_path / "does_not_exist.py"))
        assert result.accepted is False
        assert any("cannot read file" in reason for reason in result.reasons)
