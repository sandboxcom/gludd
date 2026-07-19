#!/usr/bin/env python3
"""Check molecule playbooks and role tasks for common YAML/ansible CI failure patterns.

Patterns checked:
  1. gather_facts: true on localhost playbooks (should be false)
  2. ansible.builtin.script used with cmd: param (should be ansible.builtin.shell/command)
  3. failed_when with quoted strings that look like raw shell commands
  4. molecule.yml missing required playbook references
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_gather_facts(path: Path, content: str) -> list[str]:
    """Check for gather_facts: true on localhost playbooks."""
    issues = []
    lines = content.split("\n")
    in_play = False
    has_localhost = False
    gather_facts_true_line = -1

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == "---" or stripped.startswith("- name:"):
            if in_play and has_localhost and gather_facts_true_line > 0:
                issues.append(f"{path}:{gather_facts_true_line}: gather_facts: true on localhost playbook")
            in_play = False
            has_localhost = False
            gather_facts_true_line = -1

        if stripped.startswith("hosts:"):
            in_play = True
            if "localhost" in stripped:
                has_localhost = True

        if in_play and re.match(r"gather_facts:\s*true", stripped):
            gather_facts_true_line = i

    # Check last play
    if in_play and has_localhost and gather_facts_true_line > 0:
        issues.append(f"{path}:{gather_facts_true_line}: gather_facts: true on localhost playbook")
    return issues


def check_script_module(path: Path, content: str) -> list[str]:
    """Check for ansible.builtin.script used with cmd: (should use shell/command)."""
    issues = []
    lines = content.split("\n")
    in_script = False
    script_start = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r"ansible\.builtin\.script:\s*$", stripped):
            in_script = True
            script_start = i
            continue

        if in_script and re.match(r"\s+cmd:\s*", stripped):
            issues.append(
                f"{path}:{script_start}: ansible.builtin.script with cmd: param "
                f"(should use ansible.builtin.shell or ansible.builtin.command; "
                f"script module expects a local script path, not inline commands)"
            )
            in_script = False

        if in_script and stripped and not stripped.startswith(" "):
            in_script = False

    return issues


def check_failed_when_strings(path: Path, content: str) -> list[str]:
    """Check for failed_when with quoted shell-command-like strings."""
    issues = []
    for i, line in enumerate(content.split("\n"), 1):
        m = re.match(r'\s+failed_when:\s*["\'](.+)["\']', line)
        if m:
            val = m.group(1)
            if val.startswith("not ") or val.startswith("not("):
                continue
            if "|" in val or "!" in val or "rc" in val:
                issues.append(
                    f"{path}:{i}: failed_when with quoted string '{val}' "
                    f"(bare Jinja2 expression preferred; quoted strings may behave differently)"
                )
    return issues


def check_playbook_shell_cmd_passthrough(path: Path, content: str) -> list[str]:
    """Check molecule playbooks for shell: with cmd: that passes through raw args.

    Note: This is for `ansible.builtin.shell:` with `cmd: >-` (folded block scalar)
    which IS valid. This check is informational only for malformed patterns.
    """
    issues = []
    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        # Check for script module used with free_form that contains shell metachar
        if re.match(r"ansible\.builtin\.script:\s*$", line.strip()):
            # Check if next line is a free_form (not indented with `cmd:`)
            if i < len(lines):
                next_line = lines[i].strip()
                if next_line and not next_line.startswith("cmd:") and not next_line.startswith("executable:"):
                    # This is free_form argument to script module
                    issues.append(
                        f"{path}:{i}: ansible.builtin.script with free_form arg: "
                        f"'{next_line[:80]}' (ensure script path is relative to role files/)"
                    )
    return issues


def check_molecule_config(path: Path, content: str) -> list[str]:
    """Check molecule.yml for missing playbook references."""
    issues = []
    has_prepare = "prepare:" in content
    has_converge = "converge:" in content
    has_verify = "verify:" in content

    if not has_converge:
        issues.append(f"{path}: missing converge playbook reference")
    if not has_verify:
        issues.append(f"{path}: missing verify playbook reference")
    if not has_prepare:
        issues.append(f"{path}: missing prepare playbook reference (may be intentional)")
    return issues


def main():
    all_issues = []
    molecule_dirs = list(ROOT.glob("molecule/playbooks/*/")) + list(ROOT.glob("molecule/*/"))
    collection_molecule = list(ROOT.glob("collections/**/molecule/default/"))
    collection_roles = list(ROOT.glob("collections/**/roles/*/tasks/"))

    # Check molecule playbooks
    for mol_dir in molecule_dirs:
        mol_dir = mol_dir.resolve()
        for yml_file in sorted(mol_dir.glob("**/*.yml")):
            rel = str(yml_file.relative_to(ROOT))
            try:
                content = yml_file.read_text()
            except Exception:
                continue
            all_issues.extend(check_gather_facts(Path(rel), content))
            all_issues.extend(check_script_module(Path(rel), content))
            all_issues.extend(check_failed_when_strings(Path(rel), content))
            all_issues.extend(check_playbook_shell_cmd_passthrough(Path(rel), content))

        # Check molecule.yml
        mol_yml = mol_dir / "molecule.yml"
        if mol_yml.exists():
            rel = str(mol_yml.relative_to(ROOT))
            try:
                content = mol_yml.read_text()
            except Exception:
                continue
            all_issues.extend(check_molecule_config(Path(rel), content))

    # Check collection role tasks
    for tasks_dir in collection_roles:
        for yml_file in sorted(tasks_dir.glob("*.yml")):
            rel = str(yml_file.relative_to(ROOT))
            try:
                content = yml_file.read_text()
            except Exception:
                continue
            all_issues.extend(check_script_module(Path(rel), content))
            all_issues.extend(check_failed_when_strings(Path(rel), content))
            all_issues.extend(check_playbook_shell_cmd_passthrough(Path(rel), content))

    # Check collection molecule directories
    for mol_dir in collection_molecule:
        for yml_file in sorted(mol_dir.glob("*.yml")):
            rel = str(yml_file.relative_to(ROOT))
            try:
                content = yml_file.read_text()
            except Exception:
                continue
            all_issues.extend(check_gather_facts(Path(rel), content))
            all_issues.extend(check_script_module(Path(rel), content))
            all_issues.extend(check_failed_when_strings(Path(rel), content))
            all_issues.extend(check_playbook_shell_cmd_passthrough(Path(rel), content))

    if all_issues:
        print(f"Found {len(all_issues)} molecule YAML issues:")
        for issue in sorted(all_issues):
            print(f"  {issue}")
        return 1
    else:
        print("No molecule YAML issues found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
