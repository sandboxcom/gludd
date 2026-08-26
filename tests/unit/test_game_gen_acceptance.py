"""Commit-gate-compliant smoke suite for the generated-code acceptance engine.

``src/general_ludd/game_gen/acceptance.py`` pairs with this file under the TDD
commit gate's candidate test naming. The canonical, exhaustive acceptance suite
lives in ``tests/unit/test_generated_code_acceptance.py``.
"""

from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path

import pytest

from general_ludd.game_gen import acceptance
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


def _runtime_game(**overrides: str) -> str:
    bodies = {
        "__init__": "pass",
        "start": "pass",
        "tick": "pass",
        "score": "return 0",
        "is_game_over": "return False",
        "restart": "pass",
    }
    bodies.update(overrides)
    return "\n".join(
        [
            "class Game:",
            f"    def __init__(self): {bodies['__init__']}",
            f"    def start(self): {bodies['start']}",
            f"    def tick(self, direction): {bodies['tick']}",
            f"    def score(self): {bodies['score']}",
            f"    def is_game_over(self): {bodies['is_game_over']}",
            f"    def restart(self): {bodies['restart']}",
        ]
    )


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


def test_main_uses_the_isolated_subprocess_acceptance_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must not execute generated code in the daemon/test process."""
    game = tmp_path / "game.py"
    game.write_text(MINIMAL_GAME)
    calls: list[tuple[str, float, str | None]] = []

    def _accept(
        path: str,
        timeout_seconds: float = 10.0,
        module_name: str | None = None,
    ) -> AcceptanceResult:
        calls.append((path, timeout_seconds, module_name))
        return AcceptanceResult(accepted=True, game_class_name="Snake")

    def _reject_in_process(*_args: object, **_kwargs: object) -> AcceptanceResult:
        raise AssertionError("CLI attempted in-process generated-code execution")

    monkeypatch.setattr(acceptance, "accept_generated_code", _accept)
    monkeypatch.setattr(acceptance, "check_file", _reject_in_process)

    assert acceptance.main([str(game), "--timeout", "2.5", "--module-name", "snake_custom"]) == 0
    assert calls == [(str(game), 2.5, "snake_custom")]


def test_subprocess_probe_confines_side_effect_audit_to_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository size and coverage files must not consume the game budget."""
    game = tmp_path / "game.py"
    game.write_text(MINIMAL_GAME)
    observed_cwd: list[Path | None] = []

    def _run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout, check
        observed_cwd.append(cwd)
        Path(args[-2]).write_text(json.dumps({"failure": None, "class_name": "Snake", "output": ""}))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _run)

    failure, class_name, output = acceptance._subprocess_probe(game, 1.0)

    assert (failure, class_name, output) == (None, "Snake", "")
    assert observed_cwd == [tmp_path]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"__init__": "raise RuntimeError('init')"}, "instantiation raised"),
        ({"start": "raise RuntimeError('start')"}, "start() raised"),
        ({"score": "raise RuntimeError('score')"}, "score() raised"),
        ({"score": "return True"}, "expected int"),
        ({"is_game_over": "raise RuntimeError('over')"}, "is_game_over() raised"),
        ({"is_game_over": "return 1"}, "expected bool"),
        ({"tick": "raise RuntimeError('tick')"}, "tick() raised"),
        ({"restart": "raise RuntimeError('restart')"}, "restart() raised"),
        ({"start": "delattr(type(self), 'score')"}, "score() missing"),
    ],
)
def test_check_source_reports_runtime_contract_failures(
    overrides: dict[str, str],
    expected: str,
) -> None:
    result = check_source(_runtime_game(**overrides))
    assert result.accepted is False
    assert any(expected in reason for reason in result.reasons)


def test_check_source_accepts_game_over_before_tick() -> None:
    result = check_source(_runtime_game(is_game_over="return True"))
    assert result.accepted is True


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", "empty source"),
        ("class Broken(", "syntax error"),
        ("from os import getcwd\nclass Game: pass", "forbidden import"),
        ("value = __builtins__['open']('x')\nclass Game: pass", "forbidden builtins access"),
    ],
)
def test_check_source_rejects_static_boundary_failures(source: str, expected: str) -> None:
    result = check_source(source)
    assert result.accepted is False
    assert any(expected in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "prefix",
    [
        "print('x' * 5000)\n",
        "print(chr(233) * 100)\n",
    ],
)
def test_check_source_rejects_hostile_runtime_output(prefix: str) -> None:
    result = check_source(prefix + _runtime_game())
    assert result.accepted is False
    assert result.stdout_snippet


def test_check_source_rejects_import_time_filesystem_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = "from pathlib import Path\nPath('dropped.txt').write_text('x')\n" + _runtime_game()
    result = check_source(source)
    assert result.accepted is False
    assert any("filesystem side effect" in reason for reason in result.reasons)
    assert not (tmp_path / "dropped.txt").exists()


def test_check_source_fails_closed_when_watchdog_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unavailable(_which: int, _seconds: float, _interval: float = 0.0) -> tuple[float, float]:
        raise ValueError("not main thread")

    monkeypatch.setattr(signal, "setitimer", _unavailable)
    result = check_source(_runtime_game())
    assert result.accepted is False
    assert result.reasons == ["runtime watchdog unavailable in this thread"]


def test_check_file_rejects_missing_and_non_utf8_inputs(tmp_path: Path) -> None:
    missing = check_file(tmp_path / "missing.py")
    invalid = tmp_path / "invalid.py"
    invalid.write_bytes(b"\xff\xfe")
    non_utf8 = check_file(invalid)
    assert missing.accepted is False
    assert "cannot read file" in missing.reasons[0]
    assert non_utf8.reasons == ["file is not valid UTF-8 text"]


def test_subprocess_probe_reports_start_and_verdict_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = tmp_path / "game.py"
    game.write_text(MINIMAL_GAME)

    def _cannot_start(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("spawn denied")

    monkeypatch.setattr(subprocess, "run", _cannot_start)
    failure, _, _ = acceptance._subprocess_probe(game, 1.0)
    assert failure == "runtime probe could not start: spawn denied"

    def _no_verdict(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "missing verdict")

    monkeypatch.setattr(subprocess, "run", _no_verdict)
    failure, _, _ = acceptance._subprocess_probe(game, 1.0)
    assert failure == "runtime probe produced no verdict (probe stderr: missing verdict)"


def test_subprocess_probe_reports_nonzero_child_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = tmp_path / "game.py"
    game.write_text(MINIMAL_GAME)

    def _nonzero(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        Path(args[-2]).write_text(json.dumps({"failure": None, "class_name": "Snake", "output": "partial"}))
        return subprocess.CompletedProcess(args, 7, "", "child failed")

    monkeypatch.setattr(subprocess, "run", _nonzero)
    failure, class_name, output = acceptance._subprocess_probe(game, 1.0)
    assert failure == "runtime probe exited 7: child failed"
    assert (class_name, output) == ("Snake", "partial")
