"""Subprocess-hardening regression tests for the quality runners.

Two call sites spawn subprocesses:

* ``quality/preflight.py`` — ``check_lint`` / ``check_mypy`` run
  ``uv run ruff …`` / ``uv run mypy src``.  Their argv is built entirely from
  internal constants (no caller/config interpolation), so they are ALREADY
  SAFE; these tests pin that contract so a future edit can't silently swap to a
  shell string or interpolate a tainted value.

* ``quality/feature_verifier.py`` — ``_default_runner(node_id)`` runs
  ``uv run pytest <node_id> …`` where ``node_id`` comes from feature-database
  evidence strings (``test:<pytest-node-id>``) and is therefore caller/config
  derived.  A ``node_id`` beginning with ``-`` would be parsed by pytest as an
  option (argument injection), so it must be rejected.  These tests prove the
  argv is list-form, ``shell=True`` is never used, and tainted node ids are
  refused before any subprocess runs.
"""

from __future__ import annotations

from typing import Any

import pytest

import general_ludd.quality.feature_verifier as fv_mod
import general_ludd.quality.preflight as preflight_mod
from general_ludd.quality.feature_verifier import _default_runner, _validate_node_id


class _SubprocessSpy:
    """Records arguments handed to subprocess.run without executing a process."""

    def __init__(self, returncode: int = 0) -> None:
        self.called = False
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}
        self._rc = returncode

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        self.args = args
        self.kwargs = kwargs

        rc = self._rc

        class _Result:
            returncode = rc
            stdout = ""
            stderr = ""

        return _Result()


# ---------------------------------------------------------------------------
# preflight.check_lint / check_mypy — already safe: internal-constant argv
# ---------------------------------------------------------------------------

def test_check_lint_uses_constant_argv_list_no_shell(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    monkeypatch.setattr(preflight_mod.subprocess, "run", spy)
    preflight_mod.check_lint()

    assert spy.called
    argv = spy.args[0]
    assert isinstance(argv, list)
    assert argv == ["uv", "run", "ruff", "check", "src", "tests"]
    assert all(isinstance(tok, str) for tok in argv)
    assert spy.kwargs.get("shell", False) is False


def test_check_mypy_uses_constant_argv_list_no_shell(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    monkeypatch.setattr(preflight_mod.subprocess, "run", spy)
    preflight_mod.check_mypy()

    assert spy.called
    argv = spy.args[0]
    assert isinstance(argv, list)
    assert argv == ["uv", "run", "mypy", "src"]
    assert all(isinstance(tok, str) for tok in argv)
    assert spy.kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# feature_verifier._default_runner — node_id is caller-derived: must validate
# ---------------------------------------------------------------------------

def test_default_runner_uses_argv_list_no_shell(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    monkeypatch.setattr(fv_mod.subprocess, "run", spy)
    _default_runner("tests/unit/test_x.py::test_ok")

    assert spy.called
    argv = spy.args[0]
    assert isinstance(argv, list)
    assert argv[0:3] == ["uv", "run", "pytest"]
    assert "tests/unit/test_x.py::test_ok" in argv
    assert argv[-4:] == ["-n", "2", "--maxprocesses", "2"]
    assert all(isinstance(tok, str) for tok in argv)
    assert spy.kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    "bad",
    [
        "-p no:cacheprovider",   # leading dash -> option injection
        "--collect-only",        # leading dash -> option injection
        "-x",                    # leading dash
        "a;rm -rf /",            # shell metachars
        "a$(whoami)",            # command substitution
        "a`whoami`",             # backtick
        "a|b",                   # pipe
        "a&b",                   # background
        "a b c",                 # whitespace (would split into extra argv tokens via shell)
        "",                      # empty
    ],
)
def test_validate_node_id_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ValueError):
        _validate_node_id(bad)


@pytest.mark.parametrize(
    "good",
    [
        "tests/unit/test_x.py",
        "tests/unit/test_x.py::test_ok",
        "tests/unit/test_x.py::TestClass::test_method",
        "test_module.py::test_a",
    ],
)
def test_validate_node_id_accepts_safe(good: str) -> None:
    assert _validate_node_id(good) == good


def test_default_runner_does_not_spawn_for_unsafe_node(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    monkeypatch.setattr(fv_mod.subprocess, "run", spy)
    rc = _default_runner("--collect-only")

    # No subprocess may run for an injection-shaped node id; failure rc returned.
    assert spy.called is False
    assert rc != 0
