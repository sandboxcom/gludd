"""Structural and behavioral tests for sandbox state-dir Makefile targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _content() -> str:
    assert MAKEFILE.exists(), "Makefile must exist"
    return MAKEFILE.read_text()


def _recipe(target: str) -> str:
    content = _content()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    end = content.index("\n\n", start) if "\n\n" in content[start:] else len(content)
    return content[start:end].strip()


# ---------------- Structural Tests ----------------


def test_sandbox_state_dir_target_exists():
    _recipe("sandbox-state-dir")


def test_sandbox_state_dir_target_is_phony():
    content = _content()
    phony_section = content[content.index(".PHONY:") :]
    phony_end = phony_section.index("\n\n") if "\n\n" in phony_section else len(phony_section)
    assert "sandbox-state-dir" in phony_section[:phony_end], "sandbox-state-dir must be .PHONY"


def test_sandbox_state_list_target_exists():
    _recipe("sandbox-state-list")


def test_sandbox_state_clean_target_exists():
    _recipe("sandbox-state-clean")


def test_sandbox_state_dir_target_uses_sandbox_state():
    recipe = _recipe("sandbox-state-dir")
    assert "SandboxState" in recipe or "sandboxes.state" in recipe, "sandbox-state-dir must reference SandboxState"


# ---------------- Behavioral Tests ----------------


def test_sandbox_state_dir_printer_emits_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "sandbox-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    result = subprocess.run(
        ["make", "sandbox-state-dir"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout.strip()
    assert output, "sandbox-state-dir must emit a path"
    assert output.startswith("/"), f"Expected absolute path, got: {output}"


def test_sandbox_state_list_shows_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    base = tmp_path / "sandbox-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    state = SandboxState.discover(project_root=project)
    gvisor = state.directory("gvisor", "run-01")
    (gvisor / "state.json").write_text("{}")

    result = subprocess.run(
        ["make", "sandbox-state-list"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "gvisor" in result.stdout, "sandbox-state-list should show 'gvisor' backend"


def test_sandbox_state_clean_removes_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    base = tmp_path / "sandbox-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    state = SandboxState.discover(project_root=project)
    assert state.project_dir.exists()

    result = subprocess.run(
        ["make", "sandbox-state-clean"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Removed" in result.stdout or "removed" in result.stdout.lower()
    assert not state.project_dir.exists(), "project_dir must be removed after clean"


def test_sandbox_state_clean_preserves_configured_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security.sandboxes.state import SandboxState

    base = tmp_path / "sandbox-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    SandboxState.discover(project_root=project)

    result = subprocess.run(
        ["make", "sandbox-state-clean"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)},
    )
    assert result.returncode == 0
    assert base.exists(), "base_dir must survive sandbox-state-clean"


def test_sandbox_state_dir_respects_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "operator-chosen-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))
    result = subprocess.run(
        ["make", "sandbox-state-dir"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)},
    )
    assert result.returncode == 0
    assert str(base) in result.stdout, f"sandbox-state-dir output must include the configured base: {result.stdout}"


def test_sandbox_state_clean_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "sandbox-state"
    project = tmp_path / "test-project"
    project.mkdir()
    (project / ".gludd").mkdir()
    monkeypatch.setenv("GLUDD_SANDBOX_STATE_DIR", str(base))
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(project))

    env = {**os.environ, "GLUDD_SANDBOX_STATE_DIR": str(base), "GLUDD_PROJECT_ROOT": str(project)}
    subprocess.run(["make", "sandbox-state-clean"], capture_output=True, text=True, cwd=ROOT, env=env)
    result2 = subprocess.run(["make", "sandbox-state-clean"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert result2.returncode == 0, f"Second clean must succeed (idempotent): {result2.stderr}"
