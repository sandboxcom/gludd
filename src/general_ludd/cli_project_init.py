"""CLI subcommand: ``gludd project init``.

Thin wrapper around the ``general_ludd.agent.project_init`` ansible role.
The role (under the bundled collection) is the single source of truth for the
scaffold shape; this module does ONLY arg parsing + role invocation. All file
I/O lives in the role.

Operators override the scaffold by placing a same-FQCN role at
``<project>/.gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/``;
the project-collection precedence system shadows the bundled copy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

DEFAULT_COLLECTION_NAME = "project"
PROJECT_INIT_PLAYBOOK = "project_init.yml"


def _cmd_project_init(args: argparse.Namespace) -> None:
    namespace = getattr(args, "namespace", None)
    collection = getattr(args, "collection", None) or DEFAULT_COLLECTION_NAME
    force = bool(getattr(args, "force", False))
    project_dir = Path(getattr(args, "project_dir", None) or os_cwd())

    if not namespace:
        print("Error: --namespace is required.", file=sys.stderr)
        sys.exit(2)

    extra_vars: dict[str, Any] = {
        "collection_namespace": namespace,
        "collection_name": collection,
        "project_dir": str(project_dir),
        "force": force,
    }

    result = _invoke_role(PROJECT_INIT_PLAYBOOK, extra_vars)

    rc = int(result.get("rc", 1))
    status = str(result.get("status", "failed"))

    summary = _extract_summary(result)
    if summary:
        print(summary)
    else:
        print(f"project_init role finished: status={status} rc={rc}")

    if status != "successful" or rc != 0:
        sys.exit(1 if rc == 0 else rc)


def _invoke_role(playbook: str, extra_vars: dict[str, Any]) -> dict[str, Any]:
    """Run the project_init playbook via AnsibleRunnerAdapter."""
    from general_ludd.ansible.runner import AnsibleRunnerAdapter

    adapter = AnsibleRunnerAdapter(project_root=extra_vars.get("project_dir"))
    if playbook not in adapter.list_playbooks():
        # Fall back to the absolute path under the repo playbooks/ dir.
        adapter.register_playbook(playbook, str(_resolve_playbook_path(playbook)))
    return adapter.run_playbook(playbook, extravars=extra_vars)


def _resolve_playbook_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    candidates = [here / "playbooks" / name, Path.cwd() / "playbooks" / name]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _extract_summary(result: dict[str, Any]) -> str:
    """Pull the role's debug summary message out of the runner result."""
    events = result.get("events") or []
    summaries: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_data = ev.get("event_data") or ev
        task = str(ev_data.get("task", "")).lower()
        if "report scaffold summary" in task or "summary" in task:
            msg = ev_data.get("res", {}).get("msg") if isinstance(ev_data.get("res"), dict) else None
            if msg:
                summaries.append(str(msg))
    return "\n".join(summaries)


def os_cwd() -> str:
    import os

    return os.getcwd()


def add_project_init_subparser(proj_sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = proj_sub.add_parser(
        "init",
        help=(
            "Scaffold a project-local ansible collection under "
            ".gludd/collections/ (namespace + collection skeleton)."
        ),
    )
    p.add_argument(
        "project_dir",
        nargs="?",
        default=None,
        help="Project directory (default: current working directory).",
    )
    p.add_argument(
        "--namespace",
        required=True,
        help="Galaxy namespace for the project collection (e.g. acme).",
    )
    p.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help=f"Collection name (default: {DEFAULT_COLLECTION_NAME}).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files if the scaffold already exists.",
    )
    p.set_defaults(func=_cmd_project_init)
