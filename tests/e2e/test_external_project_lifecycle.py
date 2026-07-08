"""WP-E3: end-to-end proof that gludd runs a non-make external project lifecycle.

Exercises the full detection -> profile -> runner pipeline against a fixture
project that ships NO ``project.yml`` and NO ``Makefile`` -- only a
``pyproject.toml``. The ``ToolchainDetector`` synthesizes a Python profile, the
``ProjectCommandRunner`` runs ``pytest -q`` in the fixture workspace, and the
fixture's single test passes -- proving gludd can operate on an external
polyglot project without a Makefile.

Regression guard: gludd's OWN workspace (which has ``project.yml``) still
resolves to ``make test`` -- so self-hosting is unaffected.

Run:  make test-iso TESTFILE='tests/e2e/test_external_project_lifecycle.py'
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from general_ludd.project_runner.profile import load_project_profile
from general_ludd.project_runner.runner import ProjectCommandRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "external_pyproject"


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the external_pyproject fixture into ``tmp_path`` and return the ws path."""
    workspace = tmp_path / "external-ws"
    shutil.copytree(FIXTURE_ROOT, workspace)
    return workspace


def test_external_pyproject_detected_and_tests_run(tmp_path: Path) -> None:
    """A pyproject.toml-only project is detected and its pytest suite runs green."""
    workspace = _copy_fixture(tmp_path)
    profile = load_project_profile(workspace)
    assert profile.has("test")
    assert "pytest" in profile.commands["test"]

    runner = ProjectCommandRunner(workspace, profile, default_timeout_s=120)
    result = runner.run("test")
    assert result.exit_code == 0, (
        f"test failed (exit={result.exit_code}):\n"
        f"stdout: {result.stdout_tail}\nstderr: {result.stderr_tail}"
    )
    assert result.passed
    assert "1 passed" in result.stdout_tail, (
        f"expected '1 passed' in output, got: {result.stdout_tail}"
    )


def test_external_project_no_make_binary_invoked(
    tmp_path: Path, monkeypatch
) -> None:
    """The detected profile runs ``pytest`` directly -- never ``make``.

    Proves the non-make toolchain end-to-end by spying on ``subprocess.Popen``
    and asserting no invocation's argv[0] resolves to ``make``.
    """
    workspace = _copy_fixture(tmp_path)
    profile = load_project_profile(workspace)
    assert not profile.commands["test"].startswith("make"), (
        f"detected test command should not be make-based: {profile.commands['test']}"
    )

    runner = ProjectCommandRunner(workspace, profile, default_timeout_s=120)

    invoked_argv: list[list[str]] = []
    import general_ludd.project_runner.runner as runner_mod

    orig_popen = runner_mod.subprocess.Popen

    def capture_popen(args, *a, **kw):
        invoked_argv.append(list(args))
        return orig_popen(args, *a, **kw)

    monkeypatch.setattr(runner_mod.subprocess, "Popen", capture_popen)

    result = runner.run("test")
    assert result.exit_code == 0
    assert invoked_argv, "subprocess.Popen was never called"
    for argv in invoked_argv:
        exe = os.path.basename(str(argv[0]))
        assert exe != "make", f"runner invoked make binary: {argv}"


def test_external_project_lint_detected(tmp_path: Path) -> None:
    """The detected Python profile declares a lint command (ruff)."""
    workspace = _copy_fixture(tmp_path)
    profile = load_project_profile(workspace)
    assert profile.has("lint")
    assert "ruff" in profile.commands["lint"]


def test_gludd_workspace_still_uses_make() -> None:
    """Regression guard: gludd's own workspace resolves to ``make test`` via
    ``project.yml`` -- self-hosting is unaffected by the detection fallback."""
    profile = load_project_profile(REPO_ROOT)
    assert profile.commands["test"] == "make test"
    argv = profile.resolve_argv("test")
    assert os.path.basename(argv[0]) == "make"
