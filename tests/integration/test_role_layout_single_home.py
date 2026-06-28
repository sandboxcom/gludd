"""Guardrails for the single-home Ansible role layout.

Historical context: the repo started with a root-level ``roles/`` directory
containing 5 legacy stub roles. After the W6/W8 migration to the
``general_ludd.agent`` + ``general_ludd.formal`` Ansible collections, the
collection became the single canonical home for all roles and modules. The
root ``roles/`` dir was left behind because two playbooks still used
bare-name ``include_role:`` (resolved via ``ansible.cfg: roles_path``),
which only works inside this repo — not for an external contributor running
Ansible without the repo's ``ansible.cfg``.

This test codifies the finished migration so the root ``roles/`` directory
cannot come back and so every playbook invokes roles by collection FQCN
(``general_ludd.<collection>.<role>``), which is portable to any contributor.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
ROOT_ROLES = ROOT / "roles"
ANSIBLE_CFG = ROOT / "ansible.cfg"
PLAYBOOKS_DIR = ROOT / "playbooks"
COLLECTION_ROLES = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent" / "roles"
)


class TestSingleRoleHome:
    def test_no_root_roles_directory(self):
        """The legacy root ``roles/`` directory must not exist. The collection
        is the single canonical home; leaving a parallel root ``roles/`` was
        the source of the 'two locations' contributor confusion.
        """
        assert not ROOT_ROLES.exists(), (
            f"root {ROOT_ROLES} must not exist — all roles belong to the "
            "general_ludd.agent / general_ludd.formal collections. Re-add a "
            "role there by FQCN, not under a top-level roles/ dir."
        )

    def test_ansible_cfg_has_no_roles_path(self):
        """``ansible.cfg`` must NOT set ``roles_path``. That setting existed
        solely to resolve the legacy root ``roles/`` bare-name lookups; with
        the collection as the single home, role resolution goes through
        ``collections_path`` + FQCN. Leaving ``roles_path`` set invites a
        parallel root roles/ dir to silently come back.
        """
        if not ANSIBLE_CFG.exists():
            return
        text = ANSIBLE_CFG.read_text()
        assert re.search(r"^\s*roles_path\s*=", text, re.MULTILINE) is None, (
            "ansible.cfg still sets roles_path — remove it. Role resolution "
            "must use collections_path + FQCN only."
        )


class TestPlaybooksUseFqcn:
    def test_no_bare_name_include_role(self):
        """Every ``include_role`` in ``playbooks/*.yml`` must name a
        collection-qualified role (``general_ludd.<collection>.<role>``).
        Bare-name lookups (``name: run_tests``) only resolve via the repo's
        ``ansible.cfg`` and break for external contributors.
        """
        if not PLAYBOOKS_DIR.exists():
            return
        offenders: list[str] = []
        for yml in sorted(PLAYBOOKS_DIR.glob("*.yml")):
            try:
                doc = yaml.safe_load(yml.read_text())
            except yaml.YAMLError:
                continue
            if not isinstance(doc, list):
                continue
            for play in doc:
                if not isinstance(play, dict):
                    continue
            for task in _walk_tasks(play):
                if not isinstance(task, dict):
                    continue
                inc = task.get("include_role")
                if inc is None:
                    inc = task.get("ansible.builtin.include_role")
                if inc is None:
                    continue
                name = inc["name"] if isinstance(inc, dict) else inc
                if not isinstance(name, str):
                    continue
                if "." not in name:
                    offenders.append(f"{yml.name}: include_role name={name!r}")
        assert not offenders, (
            "These playbooks use bare-name include_role (no collection FQCN). "
            "They only resolve via ansible.cfg roles_path and break for "
            "external contributors:\n  " + "\n  ".join(offenders)
        )


class TestLegacyRolesPortedToCollection:
    """The two legacy roles still referenced by playbooks (``run_tests``,
    ``lint_and_check``) must exist in the collection so the FQCN conversion
    doesn't lose functionality.
    """

    def test_run_tests_role_in_collection(self):
        assert (COLLECTION_ROLES / "run_tests" / "tasks" / "main.yml").is_file(), (
            "run_tests role must be ported to the collection at "
            "collections/.../agent/roles/run_tasks/ before the legacy root "
            "roles/ dir is deleted."
        )

    def test_lint_and_check_role_in_collection(self):
        assert (COLLECTION_ROLES / "lint_and_check" / "tasks" / "main.yml").is_file(), (
            "lint_and_check role must be ported to the collection at "
            "collections/.../agent/roles/lint_and_check/ before the legacy "
            "root roles/ dir is deleted."
        )


def _walk_tasks(play: dict):
    """Yield every task-like dict in a play, descending into ``tasks``,
    ``pre_tasks``, ``post_tasks``, ``handlers``, and block constructs.
    """
    for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
        node = play.get(key)
        if isinstance(node, list):
            yield from _iter_task_list(node)


def _iter_task_list(items):
    for item in items:
        if isinstance(item, dict):
            yield item
            if "block" in item and isinstance(item["block"], list):
                yield from _iter_task_list(item["block"])
