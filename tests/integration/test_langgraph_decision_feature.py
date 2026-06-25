"""Feature-presence + correctness tests for the langchain/langgraph Ansible feature.

This feature adds:
  - 3 Ansible modules under the general_ludd.agent collection:
      gludd_langchain_generate, gludd_langgraph_workflow, gludd_langgraph_decision
  - a role `langgraph_decision` (tasks/defaults/meta)
  - playbooks `playbooks/langgraph_decide.yml` + `playbooks/langchain_generate.yml`
  - a daemon endpoint POST /admin/models/workflow

CRITICAL CORRECTNESS PROPERTY guarded here
-------------------------------------------
On the in-process daemon runner path the playbook RESULT is discarded
(`event_loop/loop.py` returns without reading it), and the worker path
``rmtree``s the run artifacts. Therefore a role's decision can ONLY reach gludd
via a daemon API call (the `gludd_db` module → daemon DB), NOT via artifacts or
runner events. `test_decision_surfaced_via_daemon_api` is a regression guard for
exactly that: it asserts the role contains a `general_ludd.agent.gludd_db` task.

These tests are written to run *before* the feature files exist (other agents may
be creating them concurrently). Missing feature files produce a clear
``pytest.skip`` with a reason (when the whole feature dir/files are absent) rather
than a collection error; once the files are present the real assertions run.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

# Project root: tests/integration/<file> -> parents[2]
ROOT = Path(__file__).resolve().parents[2]

COLLECTION_DIR = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
)
MODULES_DIR = COLLECTION_DIR / "plugins" / "modules"
ROLE_DIR = COLLECTION_DIR / "roles" / "langgraph_decision"
PLAYBOOKS_DIR = ROOT / "playbooks"

# The three new modules this feature introduces.
NEW_MODULES = [
    "gludd_langchain_generate",
    "gludd_langgraph_workflow",
    "gludd_langgraph_decision",
]

# The new playbooks (file name -> required-ness for the "feature present" probe).
NEW_PLAYBOOKS = [
    "langgraph_decide.yml",
    "langchain_generate.yml",
]


def _feature_present() -> bool:
    """A coarse probe: is *any* of the feature surface on disk yet?

    Used to decide skip-vs-assert at the suite level so that a wholly-unbuilt
    feature skips cleanly while a partially/fully-built one is asserted against.
    """
    if ROLE_DIR.is_dir():
        return True
    if any((MODULES_DIR / f"{m}.py").is_file() for m in NEW_MODULES):
        return True
    return bool(any((PLAYBOOKS_DIR / p).is_file() for p in NEW_PLAYBOOKS))


# Suite-level guard: if nothing has landed yet, skip everything with a reason.
pytestmark = pytest.mark.skipif(
    not _feature_present(),
    reason=(
        "langchain/langgraph Ansible feature not built yet "
        "(no role dir, modules, or playbooks present) — "
        "tests will run once the feature files land"
    ),
)


def _read_text_or_skip(path: Path, what: str) -> str:
    if not path.is_file():
        pytest.skip(f"{what} not present yet: {path}")
    return path.read_text()


class TestNewModulesExist:
    """a. The 3 new module files exist and carry DOCUMENTATION blocks."""

    @pytest.mark.parametrize("module_name", NEW_MODULES)
    def test_module_file_exists(self, module_name: str):
        path = MODULES_DIR / f"{module_name}.py"
        assert path.is_file(), (
            f"new module {module_name}.py missing from "
            f"{MODULES_DIR} — expected for the langchain/langgraph feature"
        )

    @pytest.mark.parametrize("module_name", NEW_MODULES)
    def test_module_has_documentation_block(self, module_name: str):
        path = MODULES_DIR / f"{module_name}.py"
        content = _read_text_or_skip(path, f"module {module_name}")
        assert "DOCUMENTATION" in content, (
            f"{module_name}: missing DOCUMENTATION block"
        )

    @pytest.mark.parametrize("module_name", NEW_MODULES)
    def test_module_parses_as_python(self, module_name: str):
        """The module body must at least be syntactically valid Python.

        We compile the source rather than importing it (importing would run the
        AnsibleModule machinery / try to load ansible.module_utils). A syntax
        error here means the module file is broken.
        """
        import ast

        path = MODULES_DIR / f"{module_name}.py"
        content = _read_text_or_skip(path, f"module {module_name}")
        ast.parse(content)  # raises SyntaxError -> test failure

    @pytest.mark.parametrize("module_name", NEW_MODULES)
    def test_module_declares_main(self, module_name: str):
        path = MODULES_DIR / f"{module_name}.py"
        content = _read_text_or_skip(path, f"module {module_name}")
        assert "def main(" in content, f"{module_name}: missing main() entrypoint"


class TestNewRoleExists:
    """b. The langgraph_decision role has valid tasks/defaults/meta."""

    def test_role_dir_exists(self):
        assert ROLE_DIR.is_dir(), (
            f"role dir missing: {ROLE_DIR} — expected for langgraph_decision"
        )

    @pytest.mark.parametrize(
        "rel_path",
        ["tasks/main.yml", "defaults/main.yml", "meta/main.yml"],
    )
    def test_role_file_exists(self, rel_path: str):
        path = ROLE_DIR / rel_path
        assert path.is_file(), f"langgraph_decision role missing {rel_path}"

    @pytest.mark.parametrize(
        "rel_path",
        ["tasks/main.yml", "defaults/main.yml", "meta/main.yml"],
    )
    def test_role_file_is_valid_yaml(self, rel_path: str):
        path = ROLE_DIR / rel_path
        content = _read_text_or_skip(path, f"role file {rel_path}")
        parsed = yaml.safe_load(content)
        assert parsed is not None, f"{rel_path}: parsed to empty/None"

    def test_tasks_main_is_task_list(self):
        path = ROLE_DIR / "tasks" / "main.yml"
        content = _read_text_or_skip(path, "role tasks/main.yml")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list) and len(parsed) > 0, (
            "tasks/main.yml must be a non-empty list of tasks"
        )

    def test_meta_has_galaxy_info(self):
        path = ROLE_DIR / "meta" / "main.yml"
        content = _read_text_or_skip(path, "role meta/main.yml")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict) and "galaxy_info" in parsed, (
            "meta/main.yml must define galaxy_info"
        )


class TestNewPlaybooksRegistered:
    """c. The new playbooks exist, parse as YAML, and are play lists."""

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_playbook_exists(self, playbook_name: str):
        path = PLAYBOOKS_DIR / playbook_name
        assert path.is_file(), (
            f"playbook {playbook_name} missing from {PLAYBOOKS_DIR}"
        )

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    def test_playbook_is_play_list(self, playbook_name: str):
        path = PLAYBOOKS_DIR / playbook_name
        content = _read_text_or_skip(path, f"playbook {playbook_name}")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list) and len(parsed) > 0, (
            f"{playbook_name}: top-level must be a non-empty list of plays"
        )
        for i, play in enumerate(parsed):
            assert isinstance(play, dict), f"{playbook_name} play[{i}] not a mapping"
            assert "hosts" in play, f"{playbook_name} play[{i}] missing 'hosts'"


# --------------------------------------------------------------------------- #
# Helpers for the YAML-walking correctness tests.
# --------------------------------------------------------------------------- #
def _iter_tasks(node):
    """Yield every task mapping, recursing into block/rescue/always."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("block", "rescue", "always"):
        if isinstance(node.get(key), list):
            yield from _iter_tasks(node[key])


def _load_role_tasks() -> list:
    path = ROLE_DIR / "tasks" / "main.yml"
    content = _read_text_or_skip(path, "role tasks/main.yml")
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, list), "tasks/main.yml must be a task list"
    return parsed


class TestDecisionSurfacedViaDaemonApi:
    """d. THE KEY TEST.

    On the in-process runner path the playbook result is discarded and the
    worker path rmtree's artifacts. So the only way a decision reaches gludd is
    a daemon API call: a ``general_ludd.agent.gludd_db`` task. This guards a
    regression where the role would rely solely on artifacts/runner events.
    """

    def test_role_contains_gludd_db_task(self):
        tasks = _load_role_tasks()
        db_module_keys = {"general_ludd.agent.gludd_db", "gludd_db"}
        found = []
        for task in _iter_tasks(tasks):
            for key in db_module_keys:
                if key in task:
                    found.append((task.get("name", "<unnamed>"), key))
        assert found, (
            "langgraph_decision/tasks/main.yml has NO gludd_db task — the "
            "decision cannot reach gludd. On the in-process runner path the "
            "playbook result is discarded (event_loop/loop.py) and the worker "
            "path rmtree's artifacts, so a daemon API call via gludd_db is the "
            "ONLY result-return channel. This is a correctness regression."
        )

    def test_gludd_db_task_uses_status_update_op(self):
        """The gludd_db result-return should write a todo status back.

        The decision is surfaced via op: todo_update_status (an op the gludd_db
        argspec / agent_task capability grants). Guard against a stub gludd_db
        task that names the module but performs no result-returning op.
        """
        tasks = _load_role_tasks()
        ops = []
        for task in _iter_tasks(tasks):
            for key in ("general_ludd.agent.gludd_db", "gludd_db"):
                args = task.get(key)
                if isinstance(args, dict) and "op" in args:
                    ops.append(args["op"])
        assert ops, "gludd_db task present but declares no `op:`"
        assert any("todo_update_status" in str(op) for op in ops), (
            f"gludd_db ops {ops} do not include todo_update_status — the "
            "decision is not surfaced back to gludd via the daemon API"
        )


class TestVarsAreDefaultGuarded:
    """e. gludd-supplied vars are default()-guarded.

    Only ``model_response`` is guaranteed as a bare extravar; prompt_text,
    model_profile, and work_type must each be referenced through a
    ``| default(`` guard (in the role tasks or its defaults, and in the
    playbooks) so the play doesn't explode on an undefined var.
    """

    GUARDED_VARS: ClassVar[list[str]] = ["prompt_text", "model_profile", "work_type"]

    def _role_and_defaults_text(self) -> str:
        tasks_path = ROLE_DIR / "tasks" / "main.yml"
        defaults_path = ROLE_DIR / "defaults" / "main.yml"
        if not tasks_path.is_file():
            pytest.skip(f"role tasks/main.yml not present yet: {tasks_path}")
        text = tasks_path.read_text()
        if defaults_path.is_file():
            text += "\n" + defaults_path.read_text()
        return text

    @pytest.mark.parametrize("var", GUARDED_VARS)
    def test_role_references_var_with_default_guard(self, var: str):
        """If the role references {{ var }}, a `| default(` guard must exist.

        A var that is provided as a role default (defaults/main.yml) is itself a
        guarantee, so we accept either: a default() guard on the templated use,
        OR the var declared in defaults/main.yml.
        """
        tasks_path = ROLE_DIR / "tasks" / "main.yml"
        defaults_path = ROLE_DIR / "defaults" / "main.yml"
        tasks_text = _read_text_or_skip(tasks_path, "role tasks/main.yml")

        declared_in_defaults = False
        if defaults_path.is_file():
            defaults = yaml.safe_load(defaults_path.read_text()) or {}
            declared_in_defaults = isinstance(defaults, dict) and var in defaults

        references_var = f"{{{{ {var}" in tasks_text or var in tasks_text
        if not references_var:
            pytest.skip(f"role does not reference {var}")

        guarded = f"{var} | default(" in tasks_text or f"{var}|default(" in tasks_text
        assert guarded or declared_in_defaults, (
            f"var '{var}' is used in the role but is neither `| default(`-guarded "
            f"in tasks/main.yml nor declared in defaults/main.yml; only "
            f"model_response is a guaranteed bare extravar"
        )

    @pytest.mark.parametrize("playbook_name", NEW_PLAYBOOKS)
    @pytest.mark.parametrize("var", GUARDED_VARS)
    def test_playbook_default_guards_var_if_referenced(
        self, playbook_name: str, var: str
    ):
        path = PLAYBOOKS_DIR / playbook_name
        text = _read_text_or_skip(path, f"playbook {playbook_name}")
        if var not in text:
            pytest.skip(f"{playbook_name} does not reference {var}")
        guarded = f"{var} | default(" in text or f"{var}|default(" in text
        assert guarded, (
            f"{playbook_name}: var '{var}' referenced without a `| default(` "
            f"guard; only model_response is a guaranteed bare extravar"
        )
