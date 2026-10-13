"""Keep shared task lists and resource-free playbooks inside native validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from ansible.parsing.dataloader import DataLoader
from ansible.playbook import Playbook

if TYPE_CHECKING:
    import check_molecule_yaml as checker
else:
    from scripts import check_molecule_yaml as checker

BOOTSTRAP = (
    "import runpy, sys; from pathlib import Path; "
    "namespace = runpy.run_path(sys.argv[1]); main = namespace['main']; "
    "main.__globals__['ROOT'] = Path(sys.argv[2]); raise SystemExit(main())"
)


@pytest.mark.parametrize("invalid", (False, True))
def test_shared_tasks_keep_native_argument_validation(tmp_path: Path, invalid: bool) -> None:
    """Classify task-list content without excluding its native args diagnostics."""
    shared = tmp_path / "molecule/shared"
    shared.mkdir(parents=True)
    task = shared / "mock_daemon_start.yml"
    option = "gludd_invalid_parameter: true" if invalid else "state: absent"
    task.write_text(
        "---\n- name: Check native shared task options without execution\n"
        f"  ansible.builtin.file:\n    path: /never-executed\n    {option}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP, str(checker.__file__), str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60,
    )
    output = result.stdout + result.stderr
    if invalid:
        assert result.returncode != 0, output
        assert "args[module]" in output, output
        assert "gludd_invalid_parameter" in output, output
    else:
        assert result.returncode == 0, output
    assert "is not a valid attribute for a Play" not in output, output
    assert "MOLECULE_VALIDATION_LINT_START files=1" in output


@pytest.mark.parametrize("name", ("prepare.yml", "converge.yml", "verify.yml"))
def test_canonical_resource_free_phase_is_a_real_native_playbook(name: str) -> None:
    """A no-resource scenario still needs a valid play, not an empty YAML list."""
    loader = DataLoader()
    playbook = Playbook.load(str(checker.ROOT / "molecule/default" / name), loader=loader)
    plays = playbook.get_plays()
    assert len(plays) == 1
    assert plays[0].hosts == ["localhost"]
    assert plays[0].gather_facts is False
    assert not plays[0].get_roles()
    assert not plays[0].get_tasks()
