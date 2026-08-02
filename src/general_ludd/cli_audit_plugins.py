"""CLI subcommand: ``gludd audit-plugins``.

Thin wrapper around the ``playbooks/audit_plugins.yml`` playbook. The
playbook orchestrates the plugin-health audit roles (agent floor check,
delegate discipline, task deadlines, optional deletion gate). This module
does ONLY arg parsing + playbook invocation via ``AnsibleRunnerAdapter`` —
it never shells out to ``ansible-playbook``.

Operators who want project-level audit behavior override the bundled roles
via the project-collection precedence system; this CLI respects whatever
collections path is resolved for the active project.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.security.sandboxes.state import _reject_symlink_components
from general_ludd.security.state import SecureStateError, project_state

AUDIT_PLAYBOOK = "audit_plugins.yml"
_LOGICAL_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def _resolve_playbook_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    candidates = [here / "playbooks" / name, Path.cwd() / "playbooks" / name]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _invoke_audit_playbook(extra_vars: dict[str, Any]) -> dict[str, Any]:
    """Run the audit playbook via ``AnsibleRunnerAdapter``.

    ``AnsibleRunnerAdapter`` is referenced at module level so unit tests can
    patch ``general_ludd.cli_audit_plugins.AnsibleRunnerAdapter``.
    """
    project_root = extra_vars.get("project_root")
    adapter = AnsibleRunnerAdapter(project_root=project_root)
    if AUDIT_PLAYBOOK not in adapter.list_playbooks():
        adapter.register_playbook(AUDIT_PLAYBOOK, str(_resolve_playbook_path(AUDIT_PLAYBOOK)))
    return adapter.run_playbook(AUDIT_PLAYBOOK, extravars=extra_vars)


def _looks_like_project_path(value: str) -> bool:
    """Return whether *value* explicitly selects filesystem path semantics."""
    return (
        Path(value).is_absolute()
        or value.startswith((".", "~"))
        or "/" in value
        or "\\" in value
    )


def _resolve_project_argument(project: str | None) -> tuple[str | None, Path]:
    """Resolve a project name/path without broadening the filesystem scope.

    A validated single-component name identifies the current project while
    remaining available to the audit playbook as ``project_name``. Filesystem
    selection must be explicit (for example ``./child`` or an absolute path),
    and it fails closed on traversal, symlinks, missing paths, and non-directories.
    """
    cwd = Path.cwd().resolve(strict=True)
    if project is None:
        return None, cwd
    if _LOGICAL_PROJECT_RE.fullmatch(project) and project not in {".", ".."}:
        return project, cwd
    if not _looks_like_project_path(project):
        raise SecureStateError(
            "logical project name must start with an alphanumeric character "
            "and contain only letters, digits, '.', '_', or '-'",
        )

    try:
        candidate = Path(project).expanduser()
    except (OSError, RuntimeError) as exc:
        raise SecureStateError(f"project path is unavailable: {project}") from exc
    path_parts = tuple(part for part in re.split(r"[\\/]", project) if part)
    if ".." in candidate.parts or ".." in path_parts:
        raise SecureStateError("project path must not contain '..'")
    if not candidate.is_absolute():
        candidate = cwd / candidate
    _reject_symlink_components(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise SecureStateError(f"project path is unavailable: {candidate}") from exc
    if resolved != candidate:
        raise SecureStateError(
            f"project path resolution changed its filesystem scope: {candidate}",
        )
    if not resolved.is_dir():
        raise SecureStateError(f"project path is not a directory: {candidate}")
    return project, resolved


def _cmd_audit_plugins(args: argparse.Namespace) -> None:
    project = getattr(args, "project", None)
    limit = getattr(args, "limit", None)
    daemon_url = getattr(args, "daemon_url", "http://localhost:8000")
    enforce_disengage = bool(getattr(args, "enforce_disengage", False))

    project_name, resolved_project_root = _resolve_project_argument(project)
    project_root = str(resolved_project_root)
    artifact_dir = str(
        project_state(project_root=project_root).directory("plugin-audit")
    )

    extra_vars: dict[str, Any] = {
        "project_name": project_name,
        "project_root": project_root,
        "daemon_url": daemon_url,
        "artifact_dir": artifact_dir,
        "audit_plugins_run_enforce_disengage": enforce_disengage,
    }
    if limit:
        extra_vars["audit_limit"] = limit

    result = _invoke_audit_playbook(extra_vars)

    rc = int(result.get("rc", 1))
    status = str(result.get("status", "failed"))

    print(f"audit-plugins playbook finished: status={status} rc={rc}")
    print(f"artifact_dir={artifact_dir}")

    events = result.get("events") or []
    if events:
        print(f"events={len(events)}")

    if status != "successful" or rc != 0:
        sys.exit(1 if rc == 0 else rc)


def add_audit_plugins_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "audit-plugins",
        help=(
            "Run the plugin-health audit playbook (agent floor, delegate "
            "discipline, task deadlines, optional deletion gate)."
        ),
    )
    p.add_argument(
        "--project",
        default=None,
        help=(
            "Logical project name or explicit path (relative paths must include "
            "./; defaults to the current working directory)."
        ),
    )
    p.add_argument(
        "--limit",
        default=None,
        help="Limit the audit to a specific role (e.g. agent_floor_check).",
    )
    p.add_argument(
        "--enforce-disengage",
        dest="enforce_disengage",
        action="store_true",
        default=False,
        help=(
            "Opt-in: run the destructive enforce_disengage role "
            "(writes the emergency disengage signal). Default is off."
        ),
    )
    p.add_argument(
        "--daemon-url",
        default="http://localhost:8000",
        help="Daemon URL passed to the audit roles (default: http://localhost:8000).",
    )
    p.set_defaults(func=_cmd_audit_plugins)
