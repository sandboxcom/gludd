"""Unit tests for config/prompt_profiles/*.yml.

Verifies that the default prompt profile and its documented fields exist,
have valid types, reference an existing template, and use valid model-hint
routing keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "config" / "prompt_profiles"
TEMPLATES_DIR = REPO_ROOT / "templates"

# Routing keys allowed in model_hints — must be valid TaskRole names.
VALID_TASK_ROLES = frozenset({
    "planner", "coder", "reviewer", "editor", "compactor", "enumerator",
})

# Top-level required fields for every prompt profile.
REQUIRED_FIELDS = frozenset({
    "profile_id", "description", "system_prompt_template",
    "behavior", "skills", "model_hints", "token_budget",
})

# Required keys under behavior.
REQUIRED_BEHAVIOR_KEYS = frozenset({
    "session_persistence", "verbose_output", "auto_commit",
    "max_reasoning_steps", "require_evidence",
})

# Required keys under token_budget.
REQUIRED_TOKEN_BUDGET_KEYS = frozenset({
    "max_input_tokens", "max_output_tokens", "reserve_for_tools",
})


def _load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.yml"
    assert path.is_file(), f"prompt profile missing: {path}"
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{name}.yml must parse to a mapping"
    return data


class TestDefaultProfile:
    """The built-in default profile is the contract every agent relies on."""

    def test_default_yml_exists(self) -> None:
        assert (PROFILES_DIR / "default.yml").is_file()

    def test_default_has_all_required_fields(self) -> None:
        data = _load_profile("default")
        missing = REQUIRED_FIELDS - set(data)
        assert not missing, f"default.yml missing fields: {missing}"

    def test_default_profile_id_is_default(self) -> None:
        data = _load_profile("default")
        assert data["profile_id"] == "default"

    def test_default_description_is_nonempty_str(self) -> None:
        data = _load_profile("default")
        desc = data["description"]
        assert isinstance(desc, str) and desc.strip()

    def test_default_system_template_exists(self) -> None:
        data = _load_profile("default")
        tmpl_rel = data["system_prompt_template"]
        assert isinstance(tmpl_rel, str) and tmpl_rel
        tmpl_path = TEMPLATES_DIR / tmpl_rel
        assert tmpl_path.is_file(), (
            f"system_prompt_template references missing file: {tmpl_path}"
        )

    def test_default_behavior_has_all_keys_with_valid_types(self) -> None:
        data = _load_profile("default")
        behavior = data["behavior"]
        assert isinstance(behavior, dict)
        missing = REQUIRED_BEHAVIOR_KEYS - set(behavior)
        assert not missing, f"default behavior missing keys: {missing}"
        assert isinstance(behavior["session_persistence"], bool)
        assert isinstance(behavior["verbose_output"], bool)
        assert isinstance(behavior["auto_commit"], bool)
        assert isinstance(behavior["max_reasoning_steps"], int)
        assert behavior["max_reasoning_steps"] >= 1
        assert isinstance(behavior["require_evidence"], bool)

    def test_default_skills_is_list_of_nonempty_strings(self) -> None:
        data = _load_profile("default")
        skills = data["skills"]
        assert isinstance(skills, list)
        for s in skills:
            assert isinstance(s, str) and s.strip(), f"bad skill entry: {s!r}"

    def test_default_skills_reference_existing_files(self) -> None:
        data = _load_profile("default")
        for skill in data["skills"]:
            skill_path = REPO_ROOT / "config" / "skills" / f"{skill}.md"
            assert skill_path.is_file(), (
                f"skill {skill!r} has no matching file at {skill_path}"
            )

    def test_default_model_hints_are_valid_routing_keys(self) -> None:
        data = _load_profile("default")
        hints = data["model_hints"]
        assert isinstance(hints, dict) and hints
        for task_type, role in hints.items():
            assert isinstance(task_type, str) and task_type.strip()
            assert role in VALID_TASK_ROLES, (
                f"model_hints[{task_type!r}]={role!r} not a valid TaskRole; "
                f"expected one of {sorted(VALID_TASK_ROLES)}"
            )

    def test_default_token_budget_has_all_keys_with_valid_types(self) -> None:
        data = _load_profile("default")
        budget = data["token_budget"]
        assert isinstance(budget, dict)
        missing = REQUIRED_TOKEN_BUDGET_KEYS - set(budget)
        assert not missing, f"default token_budget missing keys: {missing}"
        for key in REQUIRED_TOKEN_BUDGET_KEYS:
            val = budget[key]
            assert isinstance(val, int) and val > 0, (
                f"token_budget.{key} must be a positive int, got {val!r}"
            )


class TestExampleProfiles:
    """terse_example and thorough_example must satisfy the same contract."""

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_has_all_required_fields(self, name: str) -> None:
        data = _load_profile(name)
        missing = REQUIRED_FIELDS - set(data)
        assert not missing, f"{name}.yml missing fields: {missing}"

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_profile_id_matches_filename(self, name: str) -> None:
        data = _load_profile(name)
        assert data["profile_id"] == name

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_system_template_exists(self, name: str) -> None:
        data = _load_profile(name)
        tmpl_path = TEMPLATES_DIR / data["system_prompt_template"]
        assert tmpl_path.is_file(), (
            f"{name} system_prompt_template missing: {tmpl_path}"
        )

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_behavior_valid_types(self, name: str) -> None:
        data = _load_profile(name)
        behavior = data["behavior"]
        assert isinstance(behavior, dict)
        for key in REQUIRED_BEHAVIOR_KEYS:
            assert key in behavior, f"{name} behavior missing {key}"
        assert isinstance(behavior["session_persistence"], bool)
        assert isinstance(behavior["verbose_output"], bool)
        assert isinstance(behavior["auto_commit"], bool)
        assert isinstance(behavior["max_reasoning_steps"], int)
        assert behavior["max_reasoning_steps"] >= 1
        assert isinstance(behavior["require_evidence"], bool)

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_skills_valid(self, name: str) -> None:
        data = _load_profile(name)
        skills = data["skills"]
        assert isinstance(skills, list)
        for s in skills:
            assert isinstance(s, str) and s.strip()

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_model_hints_valid(self, name: str) -> None:
        data = _load_profile(name)
        hints = data["model_hints"]
        assert isinstance(hints, dict)
        for role in hints.values():
            assert role in VALID_TASK_ROLES, (
                f"{name} model_hints has invalid role: {role!r}"
            )

    @pytest.mark.parametrize("name", ["terse_example", "thorough_example"])
    def test_example_token_budget_valid(self, name: str) -> None:
        data = _load_profile(name)
        budget = data["token_budget"]
        assert isinstance(budget, dict)
        for key in REQUIRED_TOKEN_BUDGET_KEYS:
            assert key in budget, f"{name} token_budget missing {key}"
            assert isinstance(budget[key], int) and budget[key] > 0

    def test_thorough_has_more_reasoning_steps_than_terse(self) -> None:
        """thorough_example must allow deeper reasoning than terse_example."""
        thorough = _load_profile("thorough_example")
        terse = _load_profile("terse_example")
        assert (
            thorough["behavior"]["max_reasoning_steps"]
            > terse["behavior"]["max_reasoning_steps"]
        )

    def test_thorough_has_larger_token_budget_than_terse(self) -> None:
        """thorough_example must have a larger input budget than terse_example."""
        thorough = _load_profile("thorough_example")
        terse = _load_profile("terse_example")
        assert (
            thorough["token_budget"]["max_input_tokens"]
            > terse["token_budget"]["max_input_tokens"]
        )


class TestProfileDirectoryContract:
    """Structural pins on the prompt_profiles directory itself."""

    def test_profiles_dir_exists(self) -> None:
        assert PROFILES_DIR.is_dir()

    def test_default_yml_is_present(self) -> None:
        files = {p.name for p in PROFILES_DIR.glob("*.yml")}
        assert "default.yml" in files, (
            f"default.yml missing from {PROFILES_DIR}; found {sorted(files)}"
        )

    def test_all_yml_files_satisfy_required_field_contract(self) -> None:
        """Every .yml in the directory must carry the full field set."""
        yml_files = sorted(PROFILES_DIR.glob("*.yml"))
        assert yml_files, "no prompt profile YAML files found"
        for path in yml_files:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            assert isinstance(data, dict), f"{path.name} must be a mapping"
            missing = REQUIRED_FIELDS - set(data)
            assert not missing, (
                f"{path.name} missing required fields: {missing}"
            )

    def test_all_yml_templates_resolve(self) -> None:
        for path in PROFILES_DIR.glob("*.yml"):
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            tmpl = data["system_prompt_template"]
            assert (TEMPLATES_DIR / tmpl).is_file(), (
                f"{path.name}: template not found: {tmpl}"
            )

    def test_all_model_hints_use_valid_roles(self) -> None:
        for path in PROFILES_DIR.glob("*.yml"):
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            for role in data["model_hints"].values():
                assert role in VALID_TASK_ROLES, (
                    f"{path.name}: invalid model_hint role {role!r}"
                )
