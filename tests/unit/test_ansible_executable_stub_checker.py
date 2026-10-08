"""Tests for the fail-closed Ansible executable-stub checker."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_ansible_executable_stubs import (
    main,
    scan_collection_tree,
    scan_playbook_tree,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scanner_finds_false_success_and_debug_only_service_roles(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/demo/plugins/modules/fake.py",
        "module.exit_json(changed=False, failed=False, not_implemented=True)\n",
    )
    _write(
        root / "ansible_collections/acme/demo/roles/plan/tasks/main.yml",
        """---
- name: Load defaults
  ansible.builtin.include_vars:
    file: defaults.yml
- name: Reference service API
  ansible.builtin.debug:
    msg: "plan routes via the demo service API (/api/demo/plan)."
""",
    )

    findings = scan_collection_tree(root)

    assert {finding.rule for finding in findings} == {
        "debug-only-service-role",
        "false-success-placeholder",
    }


def test_scanner_finds_executable_pass_but_not_abstract_or_test_code(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/demo/plugins/module_utils/runtime.py",
        "def execute():\n    pass\n",
    )
    _write(
        root / "ansible_collections/acme/demo/plugins/module_utils/protocols.py",
        "from abc import abstractmethod\n@abstractmethod\ndef execute():\n    pass\n",
    )
    _write(
        root / "ansible_collections/acme/demo/tests/unit/test_runtime.py",
        "def test_placeholder():\n    pass\n",
    )

    findings = scan_collection_tree(root)

    assert [(finding.path, finding.rule) for finding in findings] == [
        (
            "ansible_collections/acme/demo/plugins/module_utils/runtime.py",
            "pass-only-callable",
        )
    ]


def test_scanner_excludes_every_searx_owned_path(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/searxng/plugins/modules/fake.py",
        "module.exit_json(changed=False, failed=False, not_implemented=True)\n",
    )
    _write(
        root / "ansible_collections/acme/demo/roles/searx_proxy/tasks/main.yml",
        "- name: Placeholder\n  ansible.builtin.debug:\n    msg: placeholder\n",
    )

    assert scan_collection_tree(root) == []


def test_scanner_finds_input_only_verdict_and_suppressed_module_utility(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/demo/roles/fake/tasks/main.yml",
        """---
- name: Write fake verdict
  ansible.builtin.copy:
    content: "{{ {'input': fake_input} | to_json }}"
    dest: /tmp/fake.json
- name: Publish fake verdict
  ansible.builtin.set_fact:
    fake_verdict:
      module_util: fake.py
      output: /tmp/fake.json
""",
    )
    _write(
        root / "ansible_collections/acme/demo/roles/suppressed/tasks/main.yml",
        """---
- name: Run facts module_util
  ansible.builtin.command: python3 facts.py
  changed_when: false
  failed_when: false
""",
    )

    findings = scan_collection_tree(root)

    assert {finding.rule for finding in findings} == {
        "input-only-verdict",
        "suppressed-module-utility-failure",
    }


def test_scanner_finds_executable_playbook_placeholders_and_false_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / "playbooks"
    _write(
        root / "quality_gate_validate.yml",
        """---
- name: Quality gate validation
  hosts: localhost
  tasks:
    - name: Validate quality gates
      ansible.builtin.debug:
        msg: Quality gate validation placeholder
""",
    )
    _write(
        root / "gap_analysis.yml",
        """---
- name: Gap analysis
  hosts: localhost
  tasks:
    - name: Write green artifact
      ansible.builtin.copy:
        content: '{"status": "completed"}'
        dest: /tmp/gap.json
""",
    )
    _write(
        root / "validate_task.yml",
        """---
- name: Validation
  hosts: localhost
  tasks:
    - name: Run validation command
      ansible.builtin.command: make test-count
      ignore_errors: true
""",
    )
    _write(
        root / "noop.yml",
        """---
- name: Intentional no-op smoke fixture
  hosts: localhost
  tasks:
    - ansible.builtin.copy:
        content: '{"status": "success"}'
        dest: /tmp/noop.json
""",
    )

    findings = scan_playbook_tree(root)

    assert {finding.rule for finding in findings} == {
        "artifact-only-success-playbook",
        "placeholder-task",
        "suppressed-playbook-failure",
    }
    assert all(finding.path != "noop.yml" for finding in findings)


def test_repository_has_no_definite_executable_stubs() -> None:
    repository = Path(__file__).resolve().parents[2]

    findings = scan_collection_tree(repository / "collections")
    findings.extend(scan_playbook_tree(repository / "playbooks"))

    assert findings == [], "\n".join(finding.render() for finding in findings)


def test_scanner_finds_generated_connector_and_async_ellipsis(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/demo/plugins/modules/connector.py",
        "message = 'stub — auto-registered connector has no real backend'\n"
        "result = dict(success=True)\n",
    )
    _write(
        root / "ansible_collections/acme/demo/plugins/module_utils/runtime.py",
        "async def execute():\n    ...\n",
    )

    findings = scan_collection_tree(root)

    assert {finding.rule for finding in findings} == {
        "false-success-placeholder",
        "pass-only-callable",
    }


def test_scanner_ignores_invalid_source_instead_of_guessing(tmp_path: Path) -> None:
    root = tmp_path / "collections"
    _write(
        root / "ansible_collections/acme/demo/plugins/modules/invalid.py",
        "def broken(:\n    pass\n",
    )
    _write(
        root / "ansible_collections/acme/demo/roles/invalid/tasks/main.yml",
        "tasks: [unterminated\n",
    )

    assert scan_collection_tree(root) == []


def test_playbook_scanner_finds_fallback_and_missing_dependency_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "playbooks"
    _write(
        root / "self_improve_harness.yml",
        """---
- name: Improve
  hosts: localhost
  tasks:
    - name: Write fallback result
      ansible.builtin.set_fact:
        outcome:
          status: success
""",
    )
    _write(
        root / "dependency_update.yml",
        """---
- name: Dependencies
  hosts: localhost
  tasks:
    - name: Write update result
      ansible.builtin.copy:
        content: '{"status": "completed"}'
        dest: /tmp/result.json
""",
    )

    rules = {finding.rule for finding in scan_playbook_tree(root)}

    assert "fallback-success-playbook" in rules
    assert "operation-without-implementation" in rules
    assert "artifact-only-success-playbook" in rules


def test_scanners_fail_closed_for_missing_or_non_utf8_inputs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_collection_tree(tmp_path / "missing-collections")
    with pytest.raises(FileNotFoundError):
        scan_playbook_tree(tmp_path / "missing-playbooks")

    collections = tmp_path / "collections"
    source = collections / "ansible_collections/acme/demo/plugins/modules/binary.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="non-UTF-8 executable collection"):
        scan_collection_tree(collections)

    playbooks = tmp_path / "playbooks"
    playbooks.mkdir()
    (playbooks / "validate_task.yml").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="non-UTF-8 executable playbook"):
        scan_playbook_tree(playbooks)


def test_cli_reports_pass_findings_and_input_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    collections = tmp_path / "collections"
    playbooks = tmp_path / "playbooks"
    collections.mkdir()
    playbooks.mkdir()

    assert main(["--root", str(collections), "--playbooks-root", str(playbooks)]) == 0
    assert "SCAN_PASS findings=0" in capsys.readouterr().out

    _write(
        collections / "ansible_collections/acme/demo/plugins/modules/fake.py",
        "module.exit_json(changed=False, not_implemented=True)\n",
    )
    assert main(["--root", str(collections), "--playbooks-root", str(playbooks)]) == 1
    captured = capsys.readouterr()
    assert "false-success-placeholder" in captured.err
    assert "SCAN_FAIL findings=1" in captured.err

    assert main(
        [
            "--root",
            str(tmp_path / "absent"),
            "--playbooks-root",
            str(playbooks),
        ]
    ) == 2
    assert "SCAN_ERROR" in capsys.readouterr().err
