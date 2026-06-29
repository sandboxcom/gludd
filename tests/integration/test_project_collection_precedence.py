"""End-to-end precedence proof: project-tier role shadows bundled at runtime.

This test does NOT stop at path resolution — it spawns a fresh Python
subprocess that imports ansible-core with the resolved 3-tier
``ANSIBLE_COLLECTIONS_PATH`` and runs a real playbook through
``ansible.executor.playbook_executor.PlaybookExecutor``. Asserting on the
runtime fact ``role_source`` reflects the WINNING tier, not just that the
path-resolution order is correct.

Why subprocess: ansible-core installs ``AnsibleCollectionFinder`` on
``sys.meta_path`` at first import using the env then-active, and the
finder is idempotent after that. A same-process test that swaps env vars
after import cannot reliably reconfigure the finder across fork() boundaries
(the timeout-bounded path). Spawning a fresh subprocess gives ansible a
clean import with the per-test env, which is exactly the production code
path (the daemon process starts fresh, ansible imports lazily, env is set
at startup).

Three scenarios:
  1. project + bundled both define ``test_ns.proj.my_role`` → project wins
     (``role_source == "project"``).
  2. only bundled defines it → bundled wins
     (``role_source == "bundled"``).
  3. project + user + bundled all define it → project wins over user, user
     wins over bundled when project is removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _materialize_collection(
    collections_root: Path,
    namespace: str,
    collection: str,
    *,
    role_source_value: str,
) -> Path:
    """Create a minimal ansible collection with one role that sets role_source."""
    col_root = (
        collections_root / "ansible_collections" / namespace / collection
    )
    (col_root / "roles" / "my_role" / "tasks").mkdir(parents=True, exist_ok=True)
    (col_root / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
    (col_root / "meta").mkdir(parents=True, exist_ok=True)

    (col_root / "galaxy.yml").write_text(
        f"namespace: {namespace}\nname: {collection}\nversion: 1.0.0\n"
    )
    (col_root / "roles" / "my_role" / "tasks" / "main.yml").write_text(
        "---\n"
        "- name: Set role source fact\n"
        "  ansible.builtin.set_fact:\n"
        f"    role_source: {role_source_value!r}\n"
        "- name: Stamp role source to a file for cross-process assertion\n"
        "  ansible.builtin.copy:\n"
        '    dest: "{{ lookup(\'env\', \'ROLE_SOURCE_OUTFILE\') }}"\n'
        '    content: "{{ role_source }}"\n'
    )
    return col_root


_RUNNER_SCRIPT = textwrap.dedent(
    """
    import os
    import sys

    from ansible import context
    from ansible.executor.playbook_executor import PlaybookExecutor
    from ansible.inventory.manager import InventoryManager
    from ansible.module_utils.common.collections import ImmutableDict
    from ansible.plugins.loader import init_plugin_loader
    from ansible.vars.manager import VariableManager
    from ansible.parsing.dataloader import DataLoader

    init_plugin_loader()

    playbook_path = sys.argv[1]

    loader = DataLoader()
    options = ImmutableDict(
        inventory=["localhost,"],
        extravars=None,
        verbosity=0,
        check=False,
        diff=False,
        forks=1,
        become=False,
        become_method="sudo",
        become_user="root",
        connection="local",
        module_path=None,
        tags=[],
        skip_tags=[],
        start_at_task=None,
        listhosts=False,
        listtasks=False,
        listtags=False,
        syntax=False,
        subset=None,
        private_key_file=None,
        ssh_common_args=None,
        ssh_extra_args=None,
        sftp_extra_args=None,
        scp_extra_args=None,
        ask_vault_pass=False,
        vault_password_files=None,
        vault_ids=None,
    )
    context.CLIARGS = options
    inv = InventoryManager(loader=loader, sources=["localhost,"])
    vm = VariableManager(loader=loader, inventory=inv)

    pb = PlaybookExecutor(
        playbooks=[playbook_path],
        inventory=inv,
        variable_manager=vm,
        loader=loader,
        passwords={},
    )
    rc = pb.run()
    print("playbook rc=", rc)
    outfile = os.environ.get("ROLE_SOURCE_OUTFILE", "")
    if not outfile or not os.path.exists(outfile):
        print("OUTFILE_MISSING path=", outfile)
        sys.exit(2)
    with open(outfile) as f:
        print("ROLE_SOURCE=" + f.read())
    """
)


def _write_playbook(playbook_path: Path, fqcn_role: str) -> None:
    playbook_path.write_text(
        "---\n"
        "- hosts: localhost\n"
        "  gather_facts: false\n"
        "  roles:\n"
        f"    - {fqcn_role}\n"
    )


def _run_playbook_subprocess(
    collections_path: str,
    playbook: Path,
    tmp_path: Path,
) -> str:
    """Invoke a fresh python subprocess that imports ansible with the env.

    Returns the role_source value the playbook stamped. Raises on subprocess
    failure with the captured stderr.
    """
    script_path = tmp_path / "_runner.py"
    script_path.write_text(_RUNNER_SCRIPT)
    outfile = tmp_path / "role_source_out.txt"

    env = os.environ.copy()
    env["ANSIBLE_COLLECTIONS_PATH"] = collections_path
    env["ANSIBLE_ROLES_PATH"] = collections_path
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ROLE_SOURCE_OUTFILE"] = str(outfile)
    env["PYTHONPATH"] = os.path.dirname(script_path) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )

    proc = subprocess.run(
        [sys.executable, str(script_path), str(playbook)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"subprocess failed (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )
    for line in proc.stdout.splitlines():
        if line.startswith("ROLE_SOURCE="):
            return line.split("=", 1)[1]
    raise AssertionError(
        f"ROLE_SOURCE not found in subprocess output:\n{proc.stdout}\n{proc.stderr}"
    )


@pytest.fixture
def tmp_bundled(tmp_path: Path) -> Path:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    return bundled


@pytest.fixture
def clean_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG_CONFIG_HOME at a tmp dir; not used by subprocess path (which
    only gets ANSIBLE_COLLECTIONS_PATH explicitly) but keeps the parent
    process's resolver deterministic."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg


class TestProjectCollectionPrecedence:
    """Runtime precedence assertions via fresh subprocess ansible invocation."""

    def test_project_shadows_bundled(
        self, tmp_path: Path, tmp_bundled: Path, clean_xdg: Path
    ):
        """Both project and bundled tiers define ``test_ns.proj.my_role``."""
        project = tmp_path / "proj"
        project_col = project / ".gludd" / "collections"
        project_col.mkdir(parents=True)
        _materialize_collection(
            project_col, "test_ns", "proj", role_source_value="project"
        )
        _materialize_collection(
            tmp_bundled, "test_ns", "proj", role_source_value="bundled"
        )

        playbook = tmp_path / "probe.yml"
        _write_playbook(playbook, "test_ns.proj.my_role")

        cp = os.pathsep.join([str(project_col), str(tmp_bundled)])
        role_source = _run_playbook_subprocess(cp, playbook, tmp_path)
        assert role_source == "project", (
            f"expected project-tier role to win; got {role_source!r}"
        )

    def test_bundled_used_when_no_project_collection(
        self, tmp_path: Path, tmp_bundled: Path, clean_xdg: Path
    ):
        """Only the bundled tier defines the role."""
        _materialize_collection(
            tmp_bundled, "test_ns", "proj", role_source_value="bundled"
        )

        playbook = tmp_path / "probe.yml"
        _write_playbook(playbook, "test_ns.proj.my_role")

        role_source = _run_playbook_subprocess(
            str(tmp_bundled), playbook, tmp_path
        )
        assert role_source == "bundled", (
            f"expected bundled-tier fallback; got {role_source!r}"
        )

    def test_user_tier_between_project_and_bundled(
        self, tmp_path: Path, tmp_bundled: Path, clean_xdg: Path
    ):
        """User tier is between project and bundled; project wins overall,
        then user wins when project is removed."""
        project = tmp_path / "proj"
        project_col = project / ".gludd" / "collections"
        project_col.mkdir(parents=True)
        _materialize_collection(
            project_col, "test_ns", "proj", role_source_value="project"
        )
        user_col = clean_xdg / "gludd" / "collections"
        user_col.mkdir(parents=True)
        _materialize_collection(
            user_col, "test_ns", "proj", role_source_value="user"
        )
        _materialize_collection(
            tmp_bundled, "test_ns", "proj", role_source_value="bundled"
        )

        playbook = tmp_path / "probe.yml"
        _write_playbook(playbook, "test_ns.proj.my_role")

        # 3a. project present → project wins (over user AND bundled).
        cp_all = os.pathsep.join([str(project_col), str(user_col), str(tmp_bundled)])
        role_source = _run_playbook_subprocess(cp_all, playbook, tmp_path)
        assert role_source == "project", (
            f"project should win over user+bundled; got {role_source!r}"
        )

        # 3b. project removed → user wins over bundled.
        cp_no_proj = os.pathsep.join([str(user_col), str(tmp_bundled)])
        role_source_no_proj = _run_playbook_subprocess(
            cp_no_proj, playbook, tmp_path
        )
        assert role_source_no_proj == "user", (
            f"user tier should win over bundled when project absent; "
            f"got {role_source_no_proj!r}"
        )

    def test_resolver_output_matches_subprocess_env(
        self, tmp_path: Path, tmp_bundled: Path, clean_xdg: Path, monkeypatch
    ):
        """The env produced by ``resolve_collections_paths`` + ``to_ansible_env``
        — when fed to a subprocess — yields the same precedence outcome as the
        hand-built env in the tests above. This pins the resolver → subprocess
        wiring: a future resolver regression that broke ordering would fail
        here even if the subprocess itself works."""
        from general_ludd.ansible.paths import (
            resolve_collections_paths,
            to_ansible_env,
        )

        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_bundled,
        )
        project = tmp_path / "proj"
        project_col = project / ".gludd" / "collections"
        project_col.mkdir(parents=True)
        _materialize_collection(
            project_col, "test_ns", "proj", role_source_value="project"
        )
        _materialize_collection(
            tmp_bundled, "test_ns", "proj", role_source_value="bundled"
        )

        playbook = tmp_path / "probe.yml"
        _write_playbook(playbook, "test_ns.proj.my_role")

        entries = resolve_collections_paths(project_root=project)
        env = to_ansible_env(entries)
        cp = env["ANSIBLE_COLLECTIONS_PATH"]
        # Project first.
        assert cp.split(os.pathsep)[0] == str(project_col)

        role_source = _run_playbook_subprocess(cp, playbook, tmp_path)
        assert role_source == "project", (
            f"resolver-driven env should resolve to project tier; "
            f"got {role_source!r}"
        )
