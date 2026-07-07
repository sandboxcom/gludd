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
import sys
from pathlib import Path
from typing import Any

from general_ludd.ansible.runner import AnsibleRunnerAdapter

AUDIT_PLAYBOOK = "audit_plugins.yml"
DEFAULT_ARTIFACT_DIR = "/tmp/gludd-plugin-audit"


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


def _cmd_audit_plugins(args: argparse.Namespace) -> None:
    project = getattr(args, "project", None)
    limit = getattr(args, "limit", None)
    daemon_url = getattr(args, "daemon_url", "http://localhost:8000")
    enforce_disengage = bool(getattr(args, "enforce_disengage", False))

    project_root = project if project else str(Path.cwd())

    extra_vars: dict[str, Any] = {
        "project_name": project,
        "project_root": project_root,
        "daemon_url": daemon_url,
        "artifact_dir": DEFAULT_ARTIFACT_DIR,
        "audit_plugins_run_enforce_disengage": enforce_disengage,
    }
    if limit:
        extra_vars["audit_limit"] = limit

    result = _invoke_audit_playbook(extra_vars)

    rc = int(result.get("rc", 1))
    status = str(result.get("status", "failed"))

    print(f"audit-plugins playbook finished: status={status} rc={rc}")
    print(f"artifact_dir={DEFAULT_ARTIFACT_DIR}")

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
        help="Project name or path (defaults to current working directory).",
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
