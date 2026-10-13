"""Native Molecule configuration and Ansible correctness validation replace regex lint."""

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

SCRIPT = Path(checker.__file__).resolve()
CONFIG = """driver:
  name: default
platforms: []
provisioner:
  name: ansible
  playbooks:
    prepare: prepare.yml
    converge: converge.yml
    verify: verify.yml
verifier:
  name: ansible
scenario:
  test_sequence: [converge, verify]
"""
PLAYBOOK = """---
- name: Validate a local input without executing it
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Validate a built-in task
      ansible.builtin.debug:
        msg: valid
"""
BOOTSTRAP = (
    "import runpy, sys; from pathlib import Path; "
    "namespace = runpy.run_path(sys.argv[1]); main = namespace['main']; "
    "main.__globals__['ROOT'] = Path(sys.argv[2]); raise SystemExit(main())"
)


def _project(root: Path) -> Path:
    scenario = root / "molecule" / "playbooks" / "sample"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text(CONFIG)
    for name in ("prepare.yml", "converge.yml", "verify.yml"):
        (scenario / name).write_text(PLAYBOOK)
    return scenario


def _invoke(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP, str(SCRIPT), str(root)],
        cwd=root, capture_output=True, text=True, check=False, timeout=60,
    )


@pytest.mark.parametrize("case", ("facts", "quoted-expression", "script-cmd", "limited-sequence", "empty-sequence"))
def test_valid_native_semantics_are_not_rejected(tmp_path: Path, case: str) -> None:
    scenario = _project(tmp_path)
    converge = scenario / "converge.yml"
    if case == "facts":
        converge.write_text(PLAYBOOK.replace("gather_facts: false", "gather_facts: true"))
    elif case == "quoted-expression":
        converge.write_text(PLAYBOOK + '      failed_when: "result.rc != 0"\n')
    elif case == "script-cmd":
        (scenario / "fixture.sh").write_text("#!/bin/sh\nexit 0\n")
        converge.write_text(
            PLAYBOOK.replace(
                "ansible.builtin.debug:\n        msg: valid",
                "ansible.builtin.script:\n        cmd: fixture.sh",
            )
        )
    else:
        sequence = "[syntax, converge]" if case == "limited-sequence" else "[]"
        (scenario / "molecule.yml").write_text(
            "driver: {name: default}\nplatforms: []\nprovisioner: {name: ansible}\n"
            f"scenario: {{test_sequence: {sequence}}}\n"
        )
        if case == "empty-sequence":
            for name in ("prepare.yml", "converge.yml", "verify.yml"):
                (scenario / name).unlink()
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_documented_action_failures_are_reported_for_every_input_in_one_run(tmp_path: Path) -> None:
    scenario = _project(tmp_path)
    for playbook, name in (("converge.yml", "invalid_converge"), ("verify.yml", "invalid_verify")):
        task = f"ansible.builtin.assert:\n        that: [true]\n        {name}: true"
        (scenario / playbook).write_text(
            PLAYBOOK.replace("ansible.builtin.debug:\n        msg: valid", task)
        )
    result = _invoke(tmp_path)
    assert result.returncode != 0
    for name in ("invalid_converge", "invalid_verify"):
        assert name in result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative",
    (
        "agent/roles/openbao_break_glass_backup/tasks/main.yml",
        "security/roles/hsm_operations/tasks/detect_hsm.yml",
        "security/roles/ssl_cert/tasks/hsm.yml",
    ),
)
def test_repository_builtin_tasks_use_supported_native_options(
    relative: str, tmp_path: Path,
) -> None:
    _project(tmp_path)
    path = checker.ROOT / "collections/ansible_collections/general_ludd" / relative
    fixture = tmp_path / "collections/ansible_collections/general_ludd" / relative
    fixture.parent.mkdir(parents=True)
    fixture.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    # Retain real native dependencies: copying only the caller task previously
    # accepted a zero-exit missing-module warning, not a valid module contract.
    plugins = Path("collections/ansible_collections/general_ludd/agent/plugins")
    for dependency in (
        "modules/gludd_break_glass.py", "modules/gludd_model_call.py", "module_utils/gludd.py",
    ):
        source = checker.ROOT / plugins / dependency
        target = tmp_path / plugins / dependency
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        assert target.read_bytes() == source.read_bytes()
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "case", ("malformed-yaml", "unknown-module", "module-arguments", "invalid-expression",
             "missing-converge", "missing-verify", "invalid-schema", "unreadable-input"),
)
def test_real_invalid_inputs_are_fail_closed(tmp_path: Path, case: str) -> None:
    scenario = _project(tmp_path)
    converge = scenario / "converge.yml"
    if case == "malformed-yaml":
        converge.write_text("tasks: [\n")
    elif case == "unknown-module":
        converge.write_text(PLAYBOOK.replace("ansible.builtin.debug", "ansible.builtin.not_a_real_module"))
    elif case == "module-arguments":
        converge.write_text(
            PLAYBOOK.replace(
                "ansible.builtin.debug:\n        msg: valid",
                "ansible.builtin.copy:\n        content: valid\n        dest: /tmp/never-executed\n"
                "        definitely_invalid_argument: true",
            )
        )
    elif case == "invalid-expression":
        converge.write_text(PLAYBOOK + '      failed_when: "value ! incomplete"\n')
    elif case == "missing-converge":
        converge.unlink()
    elif case == "missing-verify":
        (scenario / "verify.yml").unlink()
    elif case == "invalid-schema":
        (scenario / "molecule.yml").write_text(CONFIG.replace("[converge, verify]", "not-a-sequence"))
    else:
        converge.write_bytes(b"\xff\xfe\n")
    result = _invoke(tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr


def test_native_optional_verify_is_explicitly_skipped_not_claimed(tmp_path: Path) -> None:
    scenario = _project(tmp_path)
    (scenario / "molecule.yml").write_text(
        "driver: {name: default}\nplatforms: []\n"
        "provisioner: {name: ansible, playbooks: {converge: converge.yml}}\n"
        "verifier: {name: ansible}\n"
    )
    (scenario / "verify.yml").unlink()
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MOLECULE_VALIDATION_VERIFY_SKIPPED" in result.stdout
    assert "CI GREEN" not in result.stdout
    assert "verification completed" not in result.stdout.lower()


def test_native_scenarios_have_isolated_ephemeral_configuration_state(tmp_path: Path) -> None:
    first = _project(tmp_path)
    second = first.parent / "second"
    second.mkdir()
    (second / "molecule.yml").write_text(CONFIG.replace("[converge, verify]", "[syntax, converge]"))
    for name in ("prepare.yml", "converge.yml", "verify.yml"):
        (second / name).write_text(PLAYBOOK)
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "has been modified since the scenario was created" not in result.stdout + result.stderr


def test_discovery_deduplicates_overlapping_scenarios_and_collection_tasks(tmp_path: Path) -> None:
    scenario = _project(tmp_path)
    tasks = tmp_path / "collections/ansible_collections/example/sample/roles/demo/tasks"
    tasks.mkdir(parents=True)
    task = tasks / "main.yml"
    task.write_text("- name: Native role task\n  ansible.builtin.debug:\n    msg: valid\n")
    discover = getattr(checker, "discover_inputs", None)
    assert callable(discover), "the retained input owner must deduplicate its existing validation scope"
    files = discover(tmp_path)
    assert len(files) == len(set(files))
    assert set(files) == {*scenario.glob("*.yml"), task}


def test_repository_scenarios_use_the_supported_builtin_driver_name() -> None:
    from molecule.util import safe_load_file

    obsolete = []
    for path in checker.discover_inputs(checker.ROOT):
        if path.name not in {"molecule.yml", "molecule.yaml"}:
            continue
        data = safe_load_file(path)
        if data.get("driver", {}).get("name") == "delegated":
            obsolete.append(str(path.relative_to(checker.ROOT)))
    assert not obsolete, "removed builtin driver declared: " + ", ".join(obsolete)


def test_native_configuration_errors_are_all_reported_with_source_identity(tmp_path: Path) -> None:
    scenario = _project(tmp_path)
    for name in ("bad_alpha", "bad_beta"):
        directory = scenario.parent / name
        directory.mkdir()
        (directory / "molecule.yml").write_text(CONFIG.replace("[converge, verify]", "invalid-sequence"))
    result = _invoke(tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    for name in ("bad_alpha", "bad_beta"):
        assert f"{name}/molecule.yml" in result.stdout + result.stderr


@pytest.mark.parametrize("case", ("copy", "copy-alias", "copy-template", "assert-alias"))
def test_documented_action_options_and_aliases_are_accepted_without_execution(
    tmp_path: Path, case: str,
) -> None:
    scenario = _project(tmp_path)
    destination = tmp_path / "must-not-be-created"
    if case == "assert-alias":
        task = "ansible.builtin.assert:\n        that: [true]\n        msg: documented alias"
    else:
        task = f"ansible.builtin.copy:\n        content: valid\n        dest: {destination}"
        if case == "copy-alias":
            task += '\n        attr: "{{ runtime_attributes }}"'
        elif case == "copy-template":
            task += '\n        owner: "{{ runtime_owner }}"\n        mode: "{{ runtime_mode }}"'
    (scenario / "converge.yml").write_text(
        PLAYBOOK.replace("ansible.builtin.debug:\n        msg: valid", task)
    )
    result = _invoke(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not destination.exists(), "validation must never execute the action or module"


@pytest.mark.parametrize("action", ("copy", "assert", "include_vars"))
def test_unknown_documented_action_option_is_refused(tmp_path: Path, action: str) -> None:
    scenario = _project(tmp_path)
    if action == "copy":
        task = "ansible.builtin.copy:\n        content: valid\n        dest: /tmp/never-executed"
    elif action == "assert":
        task = "ansible.builtin.assert:\n        that: [true]"
    else:
        task = 'ansible.builtin.include_vars:\n        file: "{{ runtime_file }}"'
    task += "\n        definitely_invalid_argument: true"
    (scenario / "converge.yml").write_text(
        PLAYBOOK.replace("ansible.builtin.debug:\n        msg: valid", task)
    )
    result = _invoke(tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "definitely_invalid_argument" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "action",
    (
        "ansible.builtin.set_fact:\n        _ar_snapshots: []\n        cacheable: true\n"
        '        dynamic_value: "{{ runtime_value }}"',
        'ansible.builtin.include_vars: "{{ runtime_file }}"',
        'ansible.builtin.include_vars:\n        file: "{{ runtime_file }}"\n        name: runtime_vars',
    ),
)
def test_documented_dynamic_action_domain_is_preserved_without_execution(
    tmp_path: Path, action: str,
) -> None:
    scenario = tmp_path / "molecule/default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text(
        "driver: {name: default}\nplatforms: []\nprovisioner: {name: ansible}\n"
        "scenario: {test_sequence: [syntax, converge]}\n"
    )
    (scenario / "converge.yml").write_text(
        "---\n- name: Validate dynamic action metadata without execution\n"
        "  hosts: localhost\n  gather_facts: false\n  tasks:\n"
        f"    - name: Native documented dynamic action\n      {action}\n"
    )
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", BOOTSTRAP, str(checker.__file__), str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
