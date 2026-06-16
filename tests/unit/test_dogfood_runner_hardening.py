"""Subprocess-hardening regression tests for DogfoodRunner.run_smoke_task.

``run_smoke_task(task_name)`` interpolates the caller-supplied ``task_name``
into the playbook path ``playbooks/<task_name>.yml`` and hands it to
``ansible-playbook`` as an argv token.  Because ``task_name`` is caller-derived,
it must be confined so it cannot:

  * escape the ``playbooks/`` directory via path separators / ``..`` traversal,
  * inject an option via a leading dash, or
  * carry shell metacharacters.

These tests pin the safe contract: the argv is list-form (never a string),
``shell=True`` is never used, and unsafe task names are rejected before any
subprocess is spawned.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.dogfood.runner import (
    DogfoodConfig,
    DogfoodRunner,
    _validate_task_name,
)


def _make_runner() -> DogfoodRunner:
    cfg = DogfoodConfig(
        repo_root="/tmp/repo",
        target_repo="/tmp/target",
        runtime_profile="local",
        model_profile="cheap",
    )
    return DogfoodRunner(cfg)


class _SubprocessSpy:
    """Records the arguments handed to subprocess.run without executing."""

    def __init__(self) -> None:
        self.called = False
        self.args: tuple[Any, ...] = ()
        self.kwargs: dict[str, Any] = {}

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.called = True
        self.args = args
        self.kwargs = kwargs

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()


# ---------------------------------------------------------------------------
# Safe contract: argv list-form, no shell=True
# ---------------------------------------------------------------------------

def test_run_smoke_task_uses_argv_list_not_shell(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    import general_ludd.dogfood.runner as runner_mod

    monkeypatch.setattr(runner_mod.subprocess, "run", spy)  # type: ignore[attr-defined]
    _make_runner().run_smoke_task("ping")

    assert spy.called
    argv = spy.args[0]
    # argv MUST be a list (not a shell string).
    assert isinstance(argv, list)
    assert all(isinstance(tok, str) for tok in argv)
    assert argv[0] == "ansible-playbook"
    assert argv[1] == "--syntax-check"
    assert argv[2] == "playbooks/ping.yml"
    # shell=True must NEVER be set.
    assert spy.kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# Validation: unsafe task names are rejected BEFORE spawning a subprocess
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "../../etc/passwd",       # traversal escaping playbooks/
        "..",                     # bare traversal
        "foo/bar",                # path separator
        "foo\\bar",               # windows-style separator
        "-rf",                    # leading dash -> option injection
        "--syntax-check",         # leading dash -> option injection
        "a;rm -rf /",             # shell metachars
        "a$(whoami)",             # command substitution
        "a`whoami`",              # backtick substitution
        "a|b",                    # pipe
        "a b",                    # whitespace
        "a&b",                    # background
        "",                       # empty
    ],
)
def test_validate_task_name_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ValueError):
        _validate_task_name(bad)


@pytest.mark.parametrize(
    "good",
    ["ping", "smoke_test", "role-deploy", "task.v2", "a1"],
)
def test_validate_task_name_accepts_safe(good: str) -> None:
    assert _validate_task_name(good) == good


def test_run_smoke_task_does_not_spawn_for_unsafe_name(monkeypatch: Any) -> None:
    spy = _SubprocessSpy()
    import general_ludd.dogfood.runner as runner_mod

    monkeypatch.setattr(runner_mod.subprocess, "run", spy)  # type: ignore[attr-defined]
    result = _make_runner().run_smoke_task("../../etc/passwd")

    # No subprocess may be spawned for an unsafe task name.
    assert spy.called is False
    assert result.success is False


def test_run_smoke_task_traversal_cannot_escape_playbooks(monkeypatch: Any) -> None:
    """A traversal task name must not produce a playbook path outside playbooks/."""
    spy = _SubprocessSpy()
    import general_ludd.dogfood.runner as runner_mod

    monkeypatch.setattr(runner_mod.subprocess, "run", spy)  # type: ignore[attr-defined]
    _make_runner().run_smoke_task("../secret")

    assert spy.called is False
