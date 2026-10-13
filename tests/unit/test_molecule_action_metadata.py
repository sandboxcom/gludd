"""Native action metadata ownership, unsupported coverage, and refusal regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_molecule_yaml as checker
else:
    from scripts import check_molecule_yaml as checker

SCRIPT = Path(checker.__file__).resolve()
ACTION = "metadata_fixture.sample.probe"
BOOTSTRAP = (
    "import runpy, sys; from pathlib import Path; "
    "namespace = runpy.run_path(sys.argv[1]); "
    "namespace['validate_action_option_names']((Path(sys.argv[2]),))"
)
DOCUMENT = """module: probe
short_description: Native metadata fixture
options:
  allowed:
    type: str
    aliases: [alias]
"""


def _probe(
    root: Path, *, module: str, action: str = "", argument: str = "allowed",
    sidecar: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Each CLI owns one finder and reads metadata without importing plugin bodies."""
    collection = root / "collections/ansible_collections/metadata_fixture/sample"
    marker = root / "must-not-execute"
    sentinel = f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    for kind, contents in (("modules", module), ("action", action)):
        directory = collection / "plugins" / kind
        directory.mkdir(parents=True)
        (directory / "probe.py").write_text(contents + sentinel)
    if sidecar is not None:
        (collection / "plugins/modules/probe.yml").write_text(sidecar)
    playbook = root / "playbook.yml"
    playbook.write_text(
        "- name: Read native options without execution\n  hosts: localhost\n"
        "  gather_facts: false\n  tasks:\n    - name: Native action metadata\n"
        f'      {ACTION}:\n        {argument}: "{{{{ runtime_value }}}}"\n'
    )
    local = root / "ansible-local"
    local.mkdir()
    environment = dict(os.environ)
    environment.update(
        ANSIBLE_COLLECTIONS_PATH=str(root / "collections"),
        ANSIBLE_HOME=str(root / "ansible-home"),
        ANSIBLE_LOCAL_TEMP=str(local),
    )
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP, str(SCRIPT), str(playbook)],
        cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=20,
    )
    assert not marker.exists(), "metadata resolution must never import action or module bodies"
    return result


@pytest.mark.parametrize("module", ('"""DOCUMENTATION:\n  options:\n    allowed: {}\n"""',
                                     'DOCUMENTATION = "module: probe\\nshort_description: No native options"'))
def test_missing_native_options_are_unsupported_coverage_not_invalid_input(
    tmp_path: Path, module: str,
) -> None:
    result = _probe(tmp_path, module=module)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MOLECULE_VALIDATION_ACTION_OPTIONS_UNSUPPORTED" in result.stdout
    assert ACTION in result.stdout and "proof=none" in result.stdout
    assert "validated" not in result.stdout.lower()


@pytest.mark.parametrize("argument, valid", (("allowed", True), ("alias", True), ("bad_option", False)))
def test_native_action_options_are_used_when_module_options_are_unavailable(
    tmp_path: Path, argument: str, valid: bool,
) -> None:
    result = _probe(tmp_path, module="", action=f"DOCUMENTATION = {DOCUMENT!r}", argument=argument)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert "MOLECULE_VALIDATION_ACTION_OPTIONS_UNSUPPORTED" not in result.stdout
    if not valid:
        assert "bad_option" in result.stderr


@pytest.mark.parametrize("argument, valid", (("alias", True), ("bad_option", False)))
def test_native_adjacent_module_options_remain_authoritative(
    tmp_path: Path, argument: str, valid: bool,
) -> None:
    sidecar = "DOCUMENTATION:\n" + "".join(f"  {line}\n" for line in DOCUMENT.splitlines())
    result = _probe(tmp_path, module="", sidecar=sidecar, argument=argument)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert "MOLECULE_VALIDATION_ACTION_OPTIONS_UNSUPPORTED" not in result.stdout
    if not valid:
        assert "bad_option" in result.stderr


@pytest.mark.parametrize("document", ("options: [", "options: invalid"))
def test_malformed_native_documentation_is_never_unsupported_coverage(
    tmp_path: Path, document: str,
) -> None:
    result = _probe(
        tmp_path, module=f"DOCUMENTATION = {document!r}", action=f"DOCUMENTATION = {DOCUMENT!r}",
    )
    assert result.returncode != 0
    assert "MOLECULE_VALIDATION_ACTION_OPTIONS_UNSUPPORTED" not in result.stdout


def test_native_module_options_take_precedence_over_action_metadata(tmp_path: Path) -> None:
    document = DOCUMENT.replace("allowed", "module_only").replace("[alias]", "[module_alias]")
    result = _probe(
        tmp_path, module=f"DOCUMENTATION = {document!r}", action=f"DOCUMENTATION = {DOCUMENT!r}",
    )
    assert result.returncode != 0
    assert "allowed" in result.stderr
