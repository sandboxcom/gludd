"""WP-E2: migrate ``execution.engine._run_tests`` to ProjectCommandRunner.

The hardcoded ``["make", "test"]`` invocation must be replaced by
``ProjectCommandRunner(workspace, profile).run("test")`` so a polyglot target
project runs its OWN declared test command (pytest / npm / cargo / go test…)
instead of assuming make. For gludd's own workspace (project.yml with
``test: make test``) the resolved argv is still ``['make', 'test']`` — zero
behavior change.

The ``tuple[int, str]`` return contract is preserved because both call sites
(``engine.py:555``, ``engine.py:699``) unpack ``test_exit_code, test_summary =
_run_tests(...)``. The 120s timeout + own-process-group kill are preserved by
forwarding ``timeout_s=120`` to the runner (which itself uses
``start_new_session`` + ``os.killpg``).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from unittest import mock

import pytest

from general_ludd.execution import engine
from general_ludd.project_runner import CheckResult, ProjectCommandRunner


@pytest.fixture
def make_workspace(tmp_path: Path):
    """Factory: create a workspace dir populated with optional files."""

    def _make(files: dict[str, str] | None = None) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir()
        for name, body in (files or {}).items():
            (ws / name).write_text(body, encoding="utf-8")
        return ws

    return _make


class _FakeRunner:
    """Stand-in for ProjectCommandRunner that captures the profile + records
    the call without spawning any process."""

    captured: ClassVar[list[_FakeRunner]] = []

    def __init__(self, workspace, profile, **kwargs):
        self.workspace = workspace
        self.profile = profile
        _FakeRunner.captured.append(self)

    def run(self, check, *, timeout_s=None):
        return CheckResult(
            name=check,
            exit_code=0,
            passed=True,
            duration_s=0.1,
            stdout_tail="ok",
        )


def _passing_result() -> CheckResult:
    return CheckResult(
        name="test", exit_code=0, passed=True, duration_s=0.1, stdout_tail="3 passed",
    )


def test_run_tests_uses_project_command_runner(make_workspace, monkeypatch):
    """_run_tests delegates to ProjectCommandRunner.run('test'), not raw make."""
    ws = make_workspace()
    runner_mock = mock.MagicMock(spec=ProjectCommandRunner)
    runner_mock.run.return_value = _passing_result()
    monkeypatch.setattr(engine, "ProjectCommandRunner", lambda *a, **kw: runner_mock)
    monkeypatch.setattr(engine, "load_project_profile", lambda workspace: mock.sentinel.profile)

    engine._run_tests(str(ws))

    runner_mock.run.assert_called_once()
    assert runner_mock.run.call_args.args[0] == "test"


def test_run_tests_passes_workspace_to_runner(make_workspace, monkeypatch):
    """The workspace path is forwarded to ProjectCommandRunner."""
    ws = make_workspace()
    captured: dict = {}
    runner_mock = mock.MagicMock(spec=ProjectCommandRunner)
    runner_mock.run.return_value = _passing_result()

    def capture(workspace, profile, **kw):
        captured["workspace"] = workspace
        captured["profile"] = profile
        return runner_mock

    monkeypatch.setattr(engine, "ProjectCommandRunner", capture)
    monkeypatch.setattr(engine, "load_project_profile", lambda workspace: mock.sentinel.profile)

    engine._run_tests(str(ws))

    assert Path(captured["workspace"]).resolve() == ws.resolve()


def test_run_tests_preserves_timeout_semantics(make_workspace, monkeypatch):
    """The 120s timeout is forwarded; a timed-out CheckResult maps to the
    timeout return contract."""
    ws = make_workspace()
    runner_mock = mock.MagicMock(spec=ProjectCommandRunner)
    runner_mock.run.return_value = CheckResult(
        name="test", exit_code=None, passed=False, duration_s=120.0,
        timed_out=True, stdout_tail="", stderr_tail="",
    )
    monkeypatch.setattr(engine, "ProjectCommandRunner", lambda *a, **kw: runner_mock)
    monkeypatch.setattr(engine, "load_project_profile", lambda workspace: mock.sentinel.profile)

    exit_code, summary = engine._run_tests(str(ws))

    assert runner_mock.run.call_args.kwargs.get("timeout_s") == 120
    assert exit_code == 1
    assert "timed out" in summary.lower()


def test_run_tests_returns_check_result_shape(make_workspace, monkeypatch):
    """Return type is tuple[int, str] — the existing contract call sites unpack."""
    ws = make_workspace()
    runner_mock = mock.MagicMock(spec=ProjectCommandRunner)
    runner_mock.run.return_value = _passing_result()
    monkeypatch.setattr(engine, "ProjectCommandRunner", lambda *a, **kw: runner_mock)
    monkeypatch.setattr(engine, "load_project_profile", lambda workspace: mock.sentinel.profile)

    result = engine._run_tests(str(ws))

    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], int)
    assert isinstance(result[1], str)


def test_run_tests_gludd_workspace_uses_make_test(make_workspace, monkeypatch):
    """For gludd's own workspace (project.yml with test: make test), _run_tests
    resolves the profile to ['make', 'test'] — zero behavior change."""
    ws = make_workspace({
        "project.yml": (
            "name: gludd\n"
            "allowed_exec: [make]\n"
            "env_passthrough: []\n"
            "commands:\n"
            "  test: make test\n"
        ),
    })
    _FakeRunner.captured = []
    monkeypatch.setattr(engine, "ProjectCommandRunner", _FakeRunner)

    engine._run_tests(str(ws))

    assert _FakeRunner.captured, "ProjectCommandRunner must be constructed"
    argv = _FakeRunner.captured[0].profile.resolve_argv("test")
    assert argv == ["make", "test"]


def test_run_tests_external_workspace_uses_detected_profile(make_workspace, monkeypatch):
    """For a workspace with pyproject.toml but no project.yml, _run_tests loads
    a detected profile whose test command resolves to pytest."""
    ws = make_workspace({"pyproject.toml": "[project]\nname = 'ext'\n"})
    _FakeRunner.captured = []
    monkeypatch.setattr(engine, "ProjectCommandRunner", _FakeRunner)

    engine._run_tests(str(ws))

    assert _FakeRunner.captured, "ProjectCommandRunner must be constructed"
    argv = _FakeRunner.captured[0].profile.resolve_argv("test")
    assert argv[0] == "pytest"
