"""Acceptance and counterexamples for supported native syntax-check batching.

Run with the existing make test-files owner; no new validator or runner exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_molecule_yaml as checker
else:
    from scripts import check_molecule_yaml as checker

PLAYBOOK = """---
- name: Validate syntax without executing any task
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Native built-in action
      ansible.builtin.debug:
        msg: valid
"""

def _playbook(directory: Path, content: str = PLAYBOOK) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "converge.yml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "negative", ("syntax", "syntax-completeness", "args", "jinja", "schema", "module-isolation"),
)
def test_native_validator_preserves_late_input_negative_controls(
    tmp_path: Path, negative: str,
) -> None:
    """Require real native diagnostics from every input and isolated modules."""
    scenario = tmp_path / "molecule/default"
    config = (
        "driver: {name: default}\nplatforms: []\nprovisioner: {name: ansible}\n"
        "scenario: {test_sequence: [syntax, converge]}\n"
    )
    _playbook(scenario)
    (scenario / "molecule.yml").write_text(config, encoding="utf-8")
    late = tmp_path / "molecule/zzzz-late"
    _playbook(late)
    (late / "molecule.yml").write_text(config, encoding="utf-8")
    if negative in {"syntax", "syntax-completeness"}:
        content = PLAYBOOK.replace("ansible.builtin.debug", "ansible.builtin.gludd_late_missing")
        expected = "gludd_late_missing"
        if negative == "syntax-completeness":
            (scenario / "converge.yml").write_text(
                PLAYBOOK.replace("ansible.builtin.debug", "ansible.builtin.gludd_early_missing"),
                encoding="utf-8",
            )
    elif negative == "args":
        content = PLAYBOOK.replace("msg: valid", "gludd_late_invalid_argument: true")
        expected = "gludd_late_invalid_argument"
    elif negative == "jinja":
        content = PLAYBOOK + '      failed_when: "gludd_late_value ! incomplete"\n'
        expected = "jinja[invalid]"
    elif negative == "module-isolation":
        library = scenario / "library"
        library.mkdir()
        (library / "gludd_late_local_only.py").write_text(
            '"""Must remain invisible to a sibling playbook."""\n'
            'DOCUMENTATION = """\nmodule: gludd_late_local_only\n'
            'short_description: Test fixture\ndescription: Test fixture\n"""\n',
            encoding="utf-8",
        )
        content = PLAYBOOK.replace("ansible.builtin.debug", "gludd_late_local_only")
        expected = "gludd_late_local_only"
    else:
        content = PLAYBOOK
        (late / "molecule.yml").write_text(
            config.replace("[syntax, converge]", "gludd_late_invalid_sequence"),
            encoding="utf-8",
        )
        expected = "zzzz-late/molecule.yml"
    (late / "converge.yml").write_text(content, encoding="utf-8")
    bootstrap = (
        "import runpy, sys; from pathlib import Path; "
        "namespace = runpy.run_path(sys.argv[1]); main = namespace['main']; "
        "main.__globals__['ROOT'] = Path(sys.argv[2]); raise SystemExit(main())"
    )
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", bootstrap, str(checker.__file__), str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert expected in result.stdout + result.stderr
    if negative == "syntax-completeness":
        assert "gludd_early_missing" in result.stdout + result.stderr
