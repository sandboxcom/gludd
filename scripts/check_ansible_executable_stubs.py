#!/usr/bin/env python3
"""Find definite executable stubs and false-success paths in collections.

The checker is deliberately conservative.  It reports only executable code,
skips tests and Molecule fixtures, and excludes every path owned by Searx.
Generic words such as ``placeholder`` in documentation are not findings.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SKIP_PARTS = {"docs", "molecule", "tests", "__pycache__"}
_PASS_ALLOWLIST = {
    "__init__.py",
    "protocols.py",
    "typing.py",
    "type_defs.py",
    "_types.py",
}
_TASK_RESERVED_KEY_GROUPS = (
    ("always", "block", "name", "rescue", "vars"),
    (
        "become", "become_exe", "become_flags", "become_method",
        "become_user", "collections", "connection", "environment",
    ),
    (
        "any_errors_fatal", "check_mode", "changed_when", "debugger", "delay",
        "delegate_facts", "delegate_to", "diff", "failed_when", "ignore_errors",
        "ignore_unreachable", "loop", "loop_control", "no_log", "notify",
        "poll", "register", "retries", "run_once", "tags", "throttle",
        "timeout", "until", "when",
    ),
)
_TASK_RESERVED_KEYS = frozenset().union(*_TASK_RESERVED_KEY_GROUPS)
_INERT_ROLE_ACTIONS = {
    "ansible.builtin.debug",
    "ansible.builtin.include_vars",
    "debug",
    "include_vars",
}
_ARTIFACT_ONLY_ACTIONS = {
    "ansible.builtin.assert",
    "ansible.builtin.copy",
    "ansible.builtin.file",
    "ansible.builtin.include_vars",
    "ansible.builtin.set_fact",
    "assert",
    "copy",
    "debug",
    "file",
    "include_vars",
    "set_fact",
    "ansible.builtin.debug",
}
_NOT_IMPLEMENTED_TRUE = re.compile(
    r"(?i)[\"']?not_implemented[\"']?\s*[:=]\s*true\b"
)
_SERVICE_REFERENCE = re.compile(r"(?i)routes\s+via\s+the\s+.+?\s+service\s+API")
_GREEN_STATUS = re.compile(
    r"(?i)[\"']status[\"']\s*:\s*[\"'](?:completed|success)[\"']"
)
_PLAYBOOK_MUTATION_ACTIONS = {
    "ansible.builtin.command",
    "ansible.builtin.dnf",
    "ansible.builtin.package",
    "ansible.builtin.pip",
    "ansible.builtin.uri",
    "ansible.builtin.yum",
    "command",
    "dnf",
    "package",
    "pip",
    "uri",
    "yum",
}
_DOGFOOD_PLAYBOOKS = {
    "dependency_update.yml",
    "gap_analysis.yml",
    "log_audit.yml",
    "molecule_test.yml",
    "noop.yml",
    "pip_install_bundle.yml",
    "quality_gate_validate.yml",
    "release_artifacts_validate.yml",
    "reload_harness.yml",
    "runtime_validate.yml",
    "self_improve_harness.yml",
    "slim_agent_container_build.yml",
    "validate_task.yml",
}


@dataclass(frozen=True, order=True)
class Finding:
    """One deterministic executable-stub finding."""

    path: str
    line: int
    rule: str
    detail: str

    def render(self) -> str:
        """Render a stable diagnostic line."""
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _is_searx_owned(path: Path) -> bool:
    return any("searx" in part.casefold() for part in path.parts)


def _is_executable(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in _SKIP_PARTS for part in relative.parts) or _is_searx_owned(relative):
        return False
    if path.suffix == ".py":
        return "plugins" in relative.parts or "files" in relative.parts
    if path.suffix in {".yml", ".yaml"}:
        return "roles" in relative.parts and "tasks" in relative.parts
    if path.suffix == ".j2":
        return "roles" in relative.parts and "templates" in relative.parts
    return False


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(candidate, ast.Name):
            names.add(candidate.id)
        elif isinstance(candidate, ast.Attribute):
            names.add(candidate.attr)
    return names


def _pass_only_findings(path: Path, relative: str, source: str) -> list[Finding]:
    if path.name in _PASS_ALLOWLIST:
        return []
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _decorator_names(node) & {"abstractmethod", "overload"}:
            continue
        body = node.body
        is_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
        is_ellipsis = (
            len(body) == 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis
        )
        if is_pass or is_ellipsis:
            findings.append(
                Finding(
                    path=relative,
                    line=node.lineno,
                    rule="pass-only-callable",
                    detail=f"{node.name} has no executable implementation",
                )
            )
    return findings


def _walk_tasks(value: Any) -> Iterator[dict[str, Any]]:
    """Yield task dictionaries recursively through Ansible task containers."""
    if not isinstance(value, list):
        return
    for task in value:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("always", "block", "rescue"):
            yield from _walk_tasks(task.get(key))


def _task_actions(value: Any) -> list[str]:
    actions: list[str] = []
    for task in _walk_tasks(value):
        for key in task:
            if key not in _TASK_RESERVED_KEYS:
                actions.append(str(key))
    return actions


def _placeholder_task_findings(
    value: Any,
    relative: str,
    source: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for task in _walk_tasks(value):
        name = task.get("name")
        direct_actions = [
            str(key)
            for key in task
            if key not in _TASK_RESERVED_KEYS
        ]
        if (
            isinstance(name, str)
            and direct_actions
            and set(direct_actions) <= {"ansible.builtin.debug", "debug"}
            and re.search(
                r"(?i)\b(?:placeholder|stub)\b",
                f"{name} {task.get(direct_actions[0], '')}",
            )
        ):
            offset = source.find(name)
            findings.append(
                Finding(
                    path=relative,
                    line=_line_number(source, max(0, offset)),
                    rule="placeholder-task",
                    detail="task is explicitly labelled as a placeholder or stub",
                )
            )
    return findings


def _playbook_tasks(value: Any) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return tasks
    for play in value:
        if not isinstance(play, dict):
            continue
        for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
            candidate = play.get(key)
            if isinstance(candidate, list):
                tasks.extend(item for item in candidate if isinstance(item, dict))
    return tasks


def _suppressed_playbook_findings(
    value: Any,
    relative: str,
    source: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for task in _walk_tasks(value):
        name = task.get("name", "unnamed task")
        actions = {
            str(key)
            for key in task
            if key not in _TASK_RESERVED_KEYS
        }
        variables = task.get("vars")
        allow_failure = (
            isinstance(variables, dict)
            and variables.get("allow_failure") is True
        )
        if (
            task.get("ignore_errors") is True or task.get("failed_when") is False
            or allow_failure
        ) and actions & {
            "ansible.builtin.command",
            "ansible.builtin.include_role",
            "ansible.builtin.shell",
            "command",
            "include_role",
            "shell",
        }:
            offset = source.find(str(name))
            findings.append(
                Finding(
                    path=relative,
                    line=_line_number(source, max(0, offset)),
                    rule="suppressed-playbook-failure",
                    detail="executable validation or test work suppresses failure",
                )
            )
    return findings


def _playbook_findings(relative: str, source: str) -> list[Finding]:
    try:
        loaded = yaml.safe_load(source)
    except yaml.YAMLError:
        return []
    tasks = _playbook_tasks(loaded)
    findings = _placeholder_task_findings(tasks, relative, source)
    findings.extend(_suppressed_playbook_findings(tasks, relative, source))
    actions = _task_actions(tasks)
    if actions and set(actions) <= _ARTIFACT_ONLY_ACTIONS and _GREEN_STATUS.search(source):
        match = _GREEN_STATUS.search(source)
        assert match is not None
        findings.append(
            Finding(
                path=relative,
                line=_line_number(source, match.start()),
                rule="artifact-only-success-playbook",
                detail="playbook writes a green artifact without executing its named work",
            )
        )
    for task in tasks:
        name = str(task.get("name", ""))
        if (
            "fallback" in name.casefold()
            and _GREEN_STATUS.search(str(task))
        ):
            offset = source.find(name)
            findings.append(
                Finding(
                    path=relative,
                    line=_line_number(source, max(0, offset)),
                    rule="fallback-success-playbook",
                    detail="fallback reports success without executing requested work",
                )
            )
    if (
        Path(relative).name == "dependency_update.yml"
        and _GREEN_STATUS.search(source)
        and not (set(actions) & _PLAYBOOK_MUTATION_ACTIONS)
    ):
        findings.append(
            Finding(
                path=relative,
                line=1,
                rule="operation-without-implementation",
                detail="dependency update playbook never performs an update",
            )
        )
    return findings


def _suppressed_module_utility_findings(
    value: Any,
    relative: str,
    source: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for task in _walk_tasks(value):
        name = task.get("name")
        direct_actions = {
            str(key)
            for key in task
            if key not in _TASK_RESERVED_KEYS
        }
        suppresses_failure = task.get("failed_when") is False or task.get("ignore_errors") is True
        if (
            isinstance(name, str)
            and "module_util" in name.casefold()
            and direct_actions & {"ansible.builtin.command", "ansible.builtin.shell", "command", "shell"}
            and suppresses_failure
        ):
            offset = source.find(name)
            findings.append(
                Finding(
                    path=relative,
                    line=_line_number(source, max(0, offset)),
                    rule="suppressed-module-utility-failure",
                    detail="module utility execution suppresses a nonzero result",
                )
            )
    return findings


def _yaml_findings(relative: str, source: str) -> list[Finding]:
    try:
        loaded = yaml.safe_load(source)
    except yaml.YAMLError:
        return []
    findings = _placeholder_task_findings(loaded, relative, source)
    findings.extend(_suppressed_module_utility_findings(loaded, relative, source))
    actions = _task_actions(loaded)
    if (
        "module_util:" in source
        and re.search(r"(?i)\bverdict\b", source)
        and actions
        and set(actions) <= _ARTIFACT_ONLY_ACTIONS
    ):
        offset = source.index("module_util:")
        findings.append(
            Finding(
                path=relative,
                line=_line_number(source, offset),
                rule="input-only-verdict",
                detail="role labels copied inputs as a verdict without executing domain logic",
            )
        )
    if _SERVICE_REFERENCE.search(source) is None:
        return findings
    if actions and set(actions) <= _INERT_ROLE_ACTIONS:
        match = _SERVICE_REFERENCE.search(source)
        assert match is not None
        findings.append(
            Finding(
                path=relative,
                line=_line_number(source, match.start()),
                rule="debug-only-service-role",
                detail="role advertises a service route but only loads variables and logs",
            )
        )
    return findings


def _source_findings(path: Path, relative: str, source: str) -> list[Finding]:
    findings: list[Finding] = []
    false_success = _NOT_IMPLEMENTED_TRUE.search(source)
    if false_success is not None and ("exit_json" in source or "ok_result" in source):
        findings.append(
            Finding(
                path=relative,
                line=_line_number(source, false_success.start()),
                rule="false-success-placeholder",
                detail="executable reports success while declaring itself unimplemented",
            )
        )
    if "stub — auto-registered connector has no real backend" in source and "success=True" in source:
        offset = source.index("stub — auto-registered connector has no real backend")
        findings.append(
            Finding(
                path=relative,
                line=_line_number(source, offset),
                rule="false-success-placeholder",
                detail="generated connector reports success without a backend",
            )
        )
    if path.suffix == ".py":
        findings.extend(_pass_only_findings(path, relative, source))
    elif path.suffix in {".yml", ".yaml"}:
        findings.extend(_yaml_findings(relative, source))
    return findings


def scan_collection_tree(root: Path) -> list[Finding]:
    """Return deterministic definite findings under a collection tree."""
    if not root.is_dir():
        raise FileNotFoundError(f"collection root does not exist: {root}")
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _is_executable(path, root):
            continue
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"non-UTF-8 executable collection source: {relative}") from exc
        findings.extend(_source_findings(path, relative, source))
    return sorted(set(findings))


def scan_playbook_tree(root: Path) -> list[Finding]:
    """Return definite false-success findings under a playbook directory."""
    if not root.is_dir():
        raise FileNotFoundError(f"playbook root does not exist: {root}")
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.y*ml")):
        relative_path = path.relative_to(root)
        if (
            path.name not in _DOGFOOD_PLAYBOOKS
            or _is_searx_owned(relative_path)
            or path.name == "noop.yml"
        ):
            continue
        relative = relative_path.as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"non-UTF-8 executable playbook source: {relative}"
            ) from exc
        findings.extend(_playbook_findings(relative, source))
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    """Run the checker CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("collections"),
        help="collection tree to scan (default: collections)",
    )
    parser.add_argument(
        "--playbooks-root",
        type=Path,
        default=Path("playbooks"),
        help="playbook tree to scan (default: playbooks)",
    )
    args = parser.parse_args(argv)
    try:
        findings = scan_collection_tree(args.root)
        findings.extend(scan_playbook_tree(args.playbooks_root))
    except (FileNotFoundError, ValueError) as exc:
        print(f"ANSIBLE_EXECUTABLE_STUB_SCAN_ERROR {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        print(f"ANSIBLE_EXECUTABLE_STUB_SCAN_FAIL findings={len(findings)}", file=sys.stderr)
        return 1
    print("ANSIBLE_EXECUTABLE_STUB_SCAN_PASS findings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
