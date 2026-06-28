"""Unit tests for Phase 2 of the project-local .gludd/ feature.

Tests cover:
- AnsibleRunnerAdapter default_env stored + merged (per-call env wins)
- PromptRegistry extra_template_dirs shadows default + default still reachable +
  backward-compat with no extras
- SkillRegistry project skill shadows same-named global + augments unique
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# AnsibleRunnerAdapter — default_env
# ---------------------------------------------------------------------------


class TestAnsibleRunnerAdapterDefaultEnv:
    """default_env is stored and merged with per-call env (per-call wins)."""

    def test_default_env_stored_on_instance(self) -> None:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter(default_env={"FOO": "bar"})
        assert adapter._default_env == {"FOO": "bar"}

    def test_no_default_env_stores_empty_dict(self) -> None:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        assert adapter._default_env == {}

    def test_none_default_env_stores_empty_dict(self) -> None:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter(default_env=None)
        assert adapter._default_env == {}

    def test_per_call_env_wins_over_default(self) -> None:
        """Per-call env values override default_env values on key collision."""
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter(default_env={"KEY": "default", "ONLY_DEF": "d"})

        captured: dict[str, Any] = {}

        def _fake_run_playbook(
            playbook_path: str,
            extravars: Any,
            timeout: Any,
            extra_env: Any,
        ) -> Any:
            captured["extra_env"] = extra_env
            result = MagicMock()
            result.model_dump.return_value = {"status": "ok", "rc": 0, "events": []}
            return result

        with patch.object(adapter._core_runner, "run_playbook", side_effect=_fake_run_playbook):
            adapter.run_playbook("noop.yml", env={"KEY": "per_call"})

        assert captured["extra_env"]["KEY"] == "per_call"    # per-call wins
        assert captured["extra_env"]["ONLY_DEF"] == "d"      # default still present

    def test_default_env_passed_when_no_per_call_env(self) -> None:
        """Default env is used in full when no per-call env is supplied."""
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter(default_env={"A": "1", "B": "2"})

        captured: dict[str, Any] = {}

        def _fake_run_playbook(
            playbook_path: str,
            extravars: Any,
            timeout: Any,
            extra_env: Any,
        ) -> Any:
            captured["extra_env"] = extra_env
            result = MagicMock()
            result.model_dump.return_value = {"status": "ok", "rc": 0, "events": []}
            return result

        with patch.object(adapter._core_runner, "run_playbook", side_effect=_fake_run_playbook):
            adapter.run_playbook("noop.yml")

        assert captured["extra_env"] == {"A": "1", "B": "2"}

    def test_backward_compat_no_env_args(self) -> None:
        """Existing callers that pass no env kwargs still get None extra_env."""
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()  # no default_env

        captured: dict[str, Any] = {}

        def _fake_run_playbook(
            playbook_path: str,
            extravars: Any,
            timeout: Any,
            extra_env: Any,
        ) -> Any:
            captured["extra_env"] = extra_env
            result = MagicMock()
            result.model_dump.return_value = {"status": "ok", "rc": 0, "events": []}
            return result

        with patch.object(adapter._core_runner, "run_playbook", side_effect=_fake_run_playbook):
            adapter.run_playbook("noop.yml")

        # No default_env + no per-call env → merged dict is empty → extra_env=None
        assert captured["extra_env"] is None


# ---------------------------------------------------------------------------
# PromptRegistry — extra_template_dirs
# ---------------------------------------------------------------------------


class TestPromptRegistryExtraTemplateDirs:
    """extra_template_dirs shadows the default dir; default still reachable."""

    def test_backward_compat_no_extras(self) -> None:
        """PromptRegistry with no extras behaves identically to the original."""
        from general_ludd.prompts.registry import PromptRegistry

        reg = PromptRegistry()
        assert reg._extra_template_dirs == []

    def test_extra_dirs_stored(self, tmp_path: Path) -> None:
        from general_ludd.prompts.registry import PromptRegistry

        extra = str(tmp_path / "extras")
        reg = PromptRegistry(extra_template_dirs=[extra])
        assert reg._extra_template_dirs == [extra]

    def test_extra_dir_shadows_default(self, tmp_path: Path) -> None:
        """A template with the same name in an extra dir shadows the default dir."""
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()

        (default_dir / "hello.md.j2").write_text("default: {{ name }}")
        (extra_dir / "hello.md.j2").write_text("extra: {{ name }}")

        from general_ludd.prompts.registry import PromptRegistry

        reg = PromptRegistry(
            template_dir=str(default_dir),
            extra_template_dirs=[str(extra_dir)],
        )
        reg.refresh()

        rendered = reg.render("hello.md.j2", name="world")
        assert rendered == "extra: world"  # extra dir wins

    def test_default_dir_still_reachable_for_unique_templates(
        self, tmp_path: Path
    ) -> None:
        """Templates that only exist in the default dir are still discoverable."""
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()

        (default_dir / "only_default.md.j2").write_text("default_only: {{ x }}")
        (extra_dir / "only_extra.md.j2").write_text("extra_only: {{ x }}")

        from general_ludd.prompts.registry import PromptRegistry

        reg = PromptRegistry(
            template_dir=str(default_dir),
            extra_template_dirs=[str(extra_dir)],
        )
        reg.refresh()

        assert reg.render("only_default.md.j2", x=1) == "default_only: 1"
        assert reg.render("only_extra.md.j2", x=2) == "extra_only: 2"

    def test_refresh_no_dirs_returns_refreshed_false(self) -> None:
        from general_ludd.prompts.registry import PromptRegistry

        reg = PromptRegistry()
        result = reg.refresh()
        assert result["refreshed"] is False

    def test_make_loader_extras_first(self, tmp_path: Path) -> None:
        """_make_loader puts extra dirs before the default dir."""
        from jinja2 import FileSystemLoader

        from general_ludd.prompts.registry import PromptRegistry

        extra = str(tmp_path / "extra")
        default = str(tmp_path / "default")
        reg = PromptRegistry(template_dir=default, extra_template_dirs=[extra])
        loader = reg._make_loader()
        assert isinstance(loader, FileSystemLoader)
        # searchpath: extras first, default last
        assert loader.searchpath[0] == extra
        assert loader.searchpath[-1] == default

    def test_extra_dir_none_equiv_to_empty(self) -> None:
        from general_ludd.prompts.registry import PromptRegistry

        reg = PromptRegistry(extra_template_dirs=None)
        assert reg._extra_template_dirs == []


# ---------------------------------------------------------------------------
# SkillRegistry — project skills
# ---------------------------------------------------------------------------


class TestSkillRegistryProjectSkills:
    """Project skills shadow same-named globals and augment the unique set."""

    def _make_skill_md(self, path: Path, name: str, description: str) -> None:
        """Write a minimal skill markdown file with YAML frontmatter.

        The skill parser (skills/loader.py) populates Skill.description ONLY
        from front-matter (a bare-markdown body leaves description=""), so a
        realistic fixture must declare name/description in the front-matter —
        mirroring real skills like config/skills/osquery_system_state.md.
        """
        path.write_text(
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"trigger_patterns:\n  - {name.lower()}\n"
            f"---\n\n"
            f"# {name}\n\n{description}\n"
        )

    def test_project_skill_shadows_global_same_name(self, tmp_path: Path) -> None:
        """A project skill with the same name as a global skill replaces it."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        self._make_skill_md(global_dir / "deploy.md", "deploy", "Global deploy skill")
        self._make_skill_md(project_dir / "deploy.md", "deploy", "Project deploy skill")

        from general_ludd.skills.loader import discover_skills
        from general_ludd.skills.registry import SkillRegistry

        registry = SkillRegistry()
        for skill in discover_skills(str(global_dir)):
            registry.register(skill)
        # Simulate Phase 2: refresh with project dir (last write wins)
        registry.refresh(search_paths=[str(project_dir)])

        skill = registry.get("deploy")
        assert skill is not None
        assert "Project" in skill.description

    def test_project_skill_augments_unique(self, tmp_path: Path) -> None:
        """Project skills that don't exist globally are added to the registry."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        self._make_skill_md(global_dir / "build.md", "build", "Global build skill")
        self._make_skill_md(project_dir / "custom.md", "custom", "Project custom skill")

        from general_ludd.skills.loader import discover_skills
        from general_ludd.skills.registry import SkillRegistry

        registry = SkillRegistry()
        for skill in discover_skills(str(global_dir)):
            registry.register(skill)
        registry.refresh(search_paths=[str(project_dir)])

        names = [s.name for s in registry.list_skills()]
        assert "build" in names
        assert "custom" in names

    def test_global_skill_retained_when_no_project_conflict(
        self, tmp_path: Path
    ) -> None:
        """Global skills that have no project counterpart are still available."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        self._make_skill_md(global_dir / "lint.md", "lint", "Global lint skill")
        self._make_skill_md(project_dir / "custom.md", "custom", "Project skill only")

        from general_ludd.skills.loader import discover_skills
        from general_ludd.skills.registry import SkillRegistry

        registry = SkillRegistry()
        for skill in discover_skills(str(global_dir)):
            registry.register(skill)
        registry.refresh(search_paths=[str(project_dir)])

        lint = registry.get("lint")
        assert lint is not None
        assert "Global" in lint.description
