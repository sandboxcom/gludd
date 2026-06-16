"""Recurrence guard: every gludd_db caller must declare its db-op grants.

Background (#44 regression)
---------------------------
``gludd_db`` is gated by a per-role, default-DENY capability policy
(``capability_policy.py``).  A task that invokes ``general_ludd.agent.gludd_db``
WITHOUT passing a ``role:`` whose grant set includes the ``op:`` it performs is
refused at runtime with::

    db op denied by capability policy: role '<unknown>' may not perform
    db op 'todo_get' (default-deny; allowed ops: [])

#44 wired the policy in but did NOT pass ``role:`` from the roles / molecule
scenarios that call ``gludd_db``, so every real caller broke and
``molecule-test-all`` went red.

This test statically (no daemon, no molecule, no podman) walks EVERY
``gludd_db`` invocation in:

* every collection role's ``tasks/main.yml``
* every molecule scenario play (``converge.yml`` / ``verify.yml`` / ...)

and asserts that, for each one, a ``role:`` is supplied AND the resolved
capability identity grants the ``op:`` being performed (via the REAL
``capability_policy.for_role(...).check_db_op(op)``).  If a future role adds a
``gludd_db`` op without granting it, this test fails — the ``<unknown>``-role
default-deny breakage can never silently return.

``role:`` values that are Jinja (``"{{ capability_role }}"``) are resolved
against the role's ``defaults/main.yml`` / the scenario play ``vars:``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

# tests/security/test_gludd_db_capability_wiring.py -> repo root is three up.
ROOT = Path(__file__).parent.parent.parent
COLLECTION = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
)
ROLES_DIR = COLLECTION / "roles"
MODULE_UTILS_DIR = COLLECTION / "plugins" / "module_utils"
POLICY_PATH = MODULE_UTILS_DIR / "capability_policy.py"
MOLECULE_PLAYBOOKS = ROOT / "molecule" / "playbooks"

# The action name(s) that resolve to the gludd_db module.
GLUDD_DB_KEYS = {"general_ludd.agent.gludd_db", "gludd_db"}


def _load_capability_module():
    """Import capability_policy by path (no collection install / daemon)."""
    assert POLICY_PATH.is_file(), f"missing module_util: {POLICY_PATH}"
    if str(MODULE_UTILS_DIR) not in sys.path:
        sys.path.insert(0, str(MODULE_UTILS_DIR))
    spec = importlib.util.spec_from_file_location(
        "capability_policy", str(POLICY_PATH)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capability_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


cap_mod = _load_capability_module()
for_role = cap_mod.for_role
CapabilityError = cap_mod.CapabilityError


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path):
    """Load a YAML doc, tolerating Ansible-only tags by returning [] on error."""
    try:
        return yaml.safe_load(path.read_text()) or []
    except yaml.YAMLError:
        return []


def _iter_tasks(node):
    """Yield every mapping that looks like a task from an arbitrary YAML tree.

    Walks lists and the task-bearing keys of plays/blocks (``tasks``, ``pre_tasks``,
    ``post_tasks``, ``handlers``, ``block``, ``rescue``, ``always``) so a
    ``gludd_db`` call nested inside a block/rescue/always is still found.
    """
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
        return
    if not isinstance(node, dict):
        return
    # This mapping may itself be a task.
    yield node
    for key in (
        "tasks",
        "pre_tasks",
        "post_tasks",
        "handlers",
        "block",
        "rescue",
        "always",
    ):
        if key in node:
            yield from _iter_tasks(node[key])


def _strip_jinja(value: str) -> str:
    """Return the bare variable name inside a ``{{ ... }}`` (else the value)."""
    v = value.strip()
    if v.startswith("{{") and v.endswith("}}"):
        return v[2:-2].strip()
    return v


def _role_defaults(role_dir: Path) -> dict:
    """Load a role's ``defaults/main.yml`` as a dict (empty if absent)."""
    d = role_dir / "defaults" / "main.yml"
    data = _load_yaml(d) if d.is_file() else {}
    return data if isinstance(data, dict) else {}


def _collect_role_invocations():
    """(label, op, resolved_role) for every gludd_db task in a role's tasks/main.yml."""
    out = []
    for role_dir in sorted(p for p in ROLES_DIR.iterdir() if p.is_dir()):
        tasks_main = role_dir / "tasks" / "main.yml"
        if not tasks_main.is_file():
            continue
        defaults = _role_defaults(role_dir)
        tree = _load_yaml(tasks_main)
        for task in _iter_tasks(tree):
            for key in GLUDD_DB_KEYS & set(task.keys()):
                args = task[key] or {}
                if not isinstance(args, dict):
                    continue
                op = args.get("op")
                role_arg = args.get("role")
                label = f"role:{role_dir.name}:{task.get('name', '?')}"
                resolved = _resolve_role(role_arg, defaults)
                out.append((label, op, role_arg, resolved))
    return out


def _collect_scenario_invocations():
    """(label, op, resolved_role) for every gludd_db task in molecule plays.

    Scenarios that drive gludd_db indirectly (via include_role) are covered by
    the role-invocation walk above, so only DIRECT module calls are checked here.
    """
    out = []
    if not MOLECULE_PLAYBOOKS.is_dir():
        return out
    for scenario in sorted(p for p in MOLECULE_PLAYBOOKS.iterdir() if p.is_dir()):
        play_dir = scenario / "default"
        if not play_dir.is_dir():
            continue
        for play_file in sorted(play_dir.glob("*.yml")):
            tree = _load_yaml(play_file)
            # Gather play-level vars so a {{ capability_role }} can be resolved.
            play_vars = {}
            if isinstance(tree, list):
                for play in tree:
                    if isinstance(play, dict) and isinstance(play.get("vars"), dict):
                        play_vars.update(play["vars"])
            for task in _iter_tasks(tree):
                for key in GLUDD_DB_KEYS & set(task.keys()):
                    args = task[key] or {}
                    if not isinstance(args, dict):
                        continue
                    op = args.get("op")
                    role_arg = args.get("role")
                    label = (
                        f"scenario:{scenario.name}:{play_file.name}:"
                        f"{task.get('name', '?')}"
                    )
                    resolved = _resolve_role(role_arg, play_vars)
                    out.append((label, op, role_arg, resolved))
    return out


def _resolve_role(role_arg, var_scope: dict):
    """Resolve a ``role:`` argument to a concrete role name.

    A literal string passes through.  A ``{{ var }}`` is looked up in
    ``var_scope`` (the role defaults or the play vars).  Returns ``None`` if no
    role arg was supplied at all (the failure mode we guard against).
    """
    if role_arg is None:
        return None
    if not isinstance(role_arg, str):
        return None
    bare = _strip_jinja(role_arg)
    if bare == role_arg.strip():
        # A literal (non-Jinja) role name.
        return bare
    # A Jinja var reference; resolve from the supplied scope.
    return var_scope.get(bare)


ROLE_INVOCATIONS = _collect_role_invocations()
SCENARIO_INVOCATIONS = _collect_scenario_invocations()
ALL_INVOCATIONS = ROLE_INVOCATIONS + SCENARIO_INVOCATIONS


def test_some_gludd_db_invocations_were_discovered():
    """Sanity: the static walker actually found gludd_db calls (no silent empty)."""
    assert ROLE_INVOCATIONS, "no gludd_db role invocations discovered — walker broken?"
    assert SCENARIO_INVOCATIONS, (
        "no gludd_db molecule-scenario invocations discovered — walker broken?"
    )


@pytest.mark.parametrize(
    "label,op,role_arg,resolved",
    ALL_INVOCATIONS,
    ids=[inv[0] for inv in ALL_INVOCATIONS],
)
def test_every_gludd_db_invocation_declares_a_role(label, op, role_arg, resolved):
    """Every gludd_db task must pass a (resolvable, non-empty) ``role:``."""
    assert role_arg is not None, (
        f"{label}: gludd_db task passes NO role: — under the default-DENY "
        f"capability policy this is refused as the '<unknown>' role. Pass "
        f"role: \"{{{{ capability_role }}}}\" (or a literal role name)."
    )
    assert resolved, (
        f"{label}: role: {role_arg!r} resolved to empty/None — the "
        f"capability_role var is undefined in this role's defaults / play vars."
    )


@pytest.mark.parametrize(
    "label,op,role_arg,resolved",
    [inv for inv in ALL_INVOCATIONS if inv[1] is not None and inv[3]],
    ids=[
        inv[0] for inv in ALL_INVOCATIONS if inv[1] is not None and inv[3]
    ],
)
def test_every_gludd_db_op_is_granted_to_its_role(label, op, role_arg, resolved):
    """The resolved role MUST grant the op (real check_db_op, not a stub)."""
    policy = for_role(resolved)
    try:
        policy.check_db_op(op)
    except CapabilityError as exc:  # pragma: no cover - failure path is the assert
        pytest.fail(
            f"{label}: role {resolved!r} is NOT granted db op {op!r} — this would "
            f"fail at runtime with default-DENY. Add {op!r} to the {resolved!r} "
            f"entry's db_ops in capability_policy.py (principle of least "
            f"privilege: grant ONLY the ops this caller uses). Detail: {exc}"
        )
