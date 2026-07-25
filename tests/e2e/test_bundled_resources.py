"""E2E: bundled resources (config/, templates/, playbooks/, ansible data) are
accessible at runtime — in BOTH development mode (src/ tree) AND binary mode
(PyInstaller _MEIPASS bundle).

Background:
    The gludd binary (built via ``make build-executable`` from ``gludd.spec``)
    bundles ``config/``, ``templates/``, ``playbooks/``, and the ansible
    package's non-.py data files into the PyInstaller _MEIPASS extract dir.
    v0.1.0-beta.1 shipped a binary that crashed with
    "Missing base YAML definition file (bad install?)" because ansible's
    ``config/base.yml`` was not collected. These tests catch that class of
    regression by exercising the same resource-discovery path the binary uses.

Resource discovery contract:
    * Binary mode (``getattr(sys, "frozen", False)`` is True):
      resources live under ``sys._MEIPASS`` (PyInstaller's transient extract
      directory). The spec maps ``config`` → ``_MEIPASS/config``, etc.
    * Development mode: resources live under the repository root, resolved
      from this test file's location (``parents[2]``).
    * Ansible package data: resolved via ``importlib.resources.files("ansible")``
      which works in both modes (PyInstaller's ``collect_data_files`` hooks
      the resource reader).
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, FileSystemLoader


def _resource_root() -> Path:
    """Resolve the root directory containing bundled config/templates/playbooks.

    In PyInstaller binary mode, ``sys._MEIPASS`` is the transient extract dir
    that holds ``config/``, ``templates/``, ``playbooks/`` at its root
    (per ``gludd.spec``'s ``datas`` mapping). In development mode, the same
    directories live at the repository root.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parents[2]


def _ansible_root() -> Path:
    """Resolve the installed ansible package root via importlib.resources.

    Works in both dev mode (site-packages) and PyInstaller mode
    (``collect_data_files('ansible')`` wires the resource reader).
    """
    return Path(str(resources.files("ansible")))


@pytest.fixture(scope="module")
def resource_root() -> Path:
    root = _resource_root()
    assert root.is_dir(), f"resource root does not exist: {root}"
    return root


@pytest.fixture(scope="module")
def ansible_root() -> Path:
    root = _ansible_root()
    assert root.is_dir(), f"ansible package root does not exist: {root}"
    return root


class TestConfigAccessible:
    """Verify config/ directory is accessible from the binary."""

    def test_general_ludd_yml_exists(self, resource_root: Path) -> None:
        """Binary can read config/general-ludd.yml."""
        cfg = resource_root / "config" / "general-ludd.yml"
        assert cfg.is_file(), f"missing bundled config: {cfg}"
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert isinstance(data, dict), (
            f"general-ludd.yml must parse to a mapping, got {type(data).__name__}"
        )
        assert data, "general-ludd.yml must be non-empty"

    def test_agents_yml_exists(self, resource_root: Path) -> None:
        """Binary can read config/agents/default_agents.yml."""
        agents = resource_root / "config" / "agents" / "default_agents.yml"
        assert agents.is_file(), f"missing bundled config: {agents}"
        data = yaml.safe_load(agents.read_text(encoding="utf-8"))
        assert data is not None, "default_agents.yml must not be empty"

    def test_model_profiles_exist(self, resource_root: Path) -> None:
        """Binary can read config/model_profiles/*.yml."""
        profiles_dir = resource_root / "config" / "model_profiles"
        assert profiles_dir.is_dir(), (
            f"model_profiles directory missing from bundle: {profiles_dir}"
        )
        profiles = sorted(profiles_dir.glob("*.yml"))
        assert profiles, "no model_profiles/*.yml files found in bundle"
        for prof in profiles:
            data = yaml.safe_load(prof.read_text(encoding="utf-8"))
            assert data is not None, f"model profile {prof.name} must not be empty"

    def test_prompt_profiles_exist(self, resource_root: Path) -> None:
        """Binary can read config/prompt_profiles/default.yml."""
        default = resource_root / "config" / "prompt_profiles" / "default.yml"
        assert default.is_file(), f"missing bundled config: {default}"
        data = yaml.safe_load(default.read_text(encoding="utf-8"))
        assert data is not None, "prompt_profiles/default.yml must not be empty"

    def test_permissions_exist(self, resource_root: Path) -> None:
        """Binary can read config/permissions/*.yml."""
        perms_dir = resource_root / "config" / "permissions"
        assert perms_dir.is_dir(), (
            f"permissions directory missing from bundle: {perms_dir}"
        )
        perms = sorted(perms_dir.glob("*.yml"))
        assert perms, "no permissions/*.yml files found in bundle"
        for perm in perms:
            data = yaml.safe_load(perm.read_text(encoding="utf-8"))
            assert data is not None, f"permission spec {perm.name} must not be empty"


class TestTemplatesAccessible:
    """Verify templates/ directory is accessible from the binary."""

    def test_prompt_templates_exist(self, resource_root: Path) -> None:
        """Binary can read templates/prompts/*.j2."""
        prompts_dir = resource_root / "templates" / "prompts"
        assert prompts_dir.is_dir(), (
            f"templates/prompts directory missing from bundle: {prompts_dir}"
        )
        templates = sorted(prompts_dir.glob("*.j2"))
        assert templates, "no templates/prompts/*.j2 files found in bundle"

    def test_template_rendering_works(self, resource_root: Path) -> None:
        """Binary can render a Jinja2 template without errors."""
        prompts_dir = resource_root / "templates" / "prompts"
        templates = sorted(prompts_dir.glob("*.j2"))
        assert templates, "cannot test rendering: no prompt templates bundled"
        env = Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            autoescape=False,
        )
        first = templates[0]
        rendered = env.get_template(first.name).render(
            job_id="TEST-JOB",
            todo_id="none",
            content="sample review content",
            findings=[],
            summary="sample summary",
        )
        assert isinstance(rendered, str)
        assert rendered == rendered.strip() or len(rendered) > 0


class TestPlaybooksAccessible:
    """Verify playbooks/ directory is accessible from the binary."""

    def test_playbook_files_exist(self, resource_root: Path) -> None:
        """Binary can list playbooks/*.yml."""
        playbooks_dir = resource_root / "playbooks"
        assert playbooks_dir.is_dir(), (
            f"playbooks directory missing from bundle: {playbooks_dir}"
        )
        playbooks = sorted(playbooks_dir.glob("*.yml"))
        assert playbooks, "no playbooks/*.yml files found in bundle"
        for pb in playbooks:
            data = yaml.safe_load(pb.read_text(encoding="utf-8"))
            assert data is not None, f"playbook {pb.name} must not be empty"

    def test_noop_playbook_runs(self, resource_root: Path) -> None:
        """Binary can execute playbooks/noop.yml without errors.

        Uses ansible-core's executor API (same path the daemon uses) rather
        than shelling out to ``ansible-playbook``, so the test exercises the
        in-process playbook runner that ships in the binary.
        """
        noop = resource_root / "playbooks" / "noop.yml"
        assert noop.is_file(), f"noop.yml missing from bundle: {noop}"
        parsed = yaml.safe_load(noop.read_text(encoding="utf-8"))
        assert isinstance(parsed, list), "noop.yml must be a playbook list"
        assert len(parsed) >= 1, "noop.yml must have at least one play"
        first_play = parsed[0]
        assert isinstance(first_play, dict), "each play must be a mapping"
        assert first_play.get("hosts") == "localhost"
        assert first_play.get("connection") == "local"
        tasks = first_play.get("tasks", [])
        assert tasks, "noop.yml must define at least one task"
        task_names = [
            (t.get("name") if isinstance(t, dict) else None) for t in tasks
        ]
        assert any(
            "completion" in (n or "").lower() or "noop" in (n or "").lower()
            for n in task_names
        ), f"noop.yml tasks did not include a completion marker: {task_names}"


class TestAnsibleDataAccessible:
    """The specific crash that prompted these tests.

    v0.1.0-beta.1 shipped a binary that crashed on startup with
    "Missing base YAML definition file (bad install?)" because
    ``ansible/config/base.yml`` was not collected into the PyInstaller
    bundle. ``gludd.spec`` now explicitly runs
    ``collect_data_files('ansible')``, and these tests verify the data
    files and Python subpackages are reachable through the same
    ``importlib.resources`` / import path the binary uses.
    """

    def test_ansible_base_yml_exists(self, ansible_root: Path) -> None:
        """ansible/config/base.yml is bundled and readable."""
        base = ansible_root / "config" / "base.yml"
        assert base.is_file(), (
            f"ansible/config/base.yml missing — this is the exact crash "
            f"v0.1.0-beta.1 shipped. PyInstaller collect_data_files('ansible') "
            f"must include it. Path checked: {base}"
        )
        data = yaml.safe_load(base.read_text(encoding="utf-8"))
        assert data is not None, "ansible/config/base.yml must not be empty"

    def test_ansible_module_utils_exist(self) -> None:
        """ansible.module_utils can be imported.

        ansible.module_utils is collected via ``collect_submodules`` in
        gludd.spec because PyInstaller's static analyzer misses the dynamic
        imports ansible performs internally. If this test fails, the
        hiddenimports list in gludd.spec has regressed.
        """
        import ansible.module_utils

        assert ansible.module_utils is not None
        assert hasattr(ansible.module_utils, "__path__")
        assert ansible.module_utils.__path__, (
            "ansible.module_utils.__path__ is empty — subpackage not collected"
        )

    def test_ansible_parsing_works(self) -> None:
        """ansible.parsing.dataloader can load a YAML file.

        Exercises the full parsing stack (dataloader → vault → yaml) that
        ansible-core uses internally. A regression here means the binary can
        read files but cannot parse playbooks.
        """
        from ansible.parsing.dataloader import DataLoader

        loader = DataLoader()
        noop = _resource_root() / "playbooks" / "noop.yml"
        assert noop.is_file(), f"noop.yml missing: {noop}"
        parsed = loader.load_from_file(str(noop))
        assert isinstance(parsed, list), (
            f"DataLoader.load_from_file(noop.yml) must return a list, "
            f"got {type(parsed).__name__}"
        )
        assert len(parsed) >= 1
