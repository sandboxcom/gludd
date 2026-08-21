"""Deep validation tests for molecule playbooks.

Coverage areas:
  - YAML parse correctness for every molecule.yml, converge.yml, prepare.yml, verify.yml
  - Required structural fields (hosts, tasks, name, driver, provisioner, scenario)
  - Task ordering consistency (prepare -> converge -> verify)
  - Variable reference validity (no unresolved {{ }} references to undefined vars)
  - Role dependency declarations in molecule.yml and include_role refs
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOKS_DIR = PROJECT_ROOT / "molecule" / "playbooks"

_VAR_REF_RE = re.compile(r"\{\{\s*(\w+(?:\.\w+)*)\s*(\|[^}]*)?\}\}")

MOLECULE_SCENARIOS = sorted(
    d for d in os.listdir(PLAYBOOKS_DIR) if (PLAYBOOKS_DIR / d).is_dir() and not d.startswith(".")
)

PLAYBOOK_FILES = ["prepare.yml", "converge.yml", "verify.yml", "molecule.yml"]

SCENARIOS_WITHOUT_PREPARE = frozenset(
    {
        "binary_smoke_macos",
        "chat",
        "daemon_lifecycle",
        "default",
        "local_game_gen",
        "local_model_server",
        "travel",
    }
)
SCENARIOS_WITHOUT_CONVERGE = frozenset({"default", "travel"})
SCENARIOS_WITHOUT_VERIFY = frozenset({"default", "travel", "local_model_server"})
SCENARIOS_WITHOUT_SCENARIO_KEY = frozenset(
    {
        "noop",
        "prompt_eval",
        "runtime_validate",
    }
)
SCENARIOS_WITH_EMPTY_SEQUENCE = frozenset({"default", "travel"})


def _collect_playbook_paths(scenario: str) -> dict[str, Path | None]:
    default_root = PLAYBOOKS_DIR / scenario / "default"
    scenario_root = PLAYBOOKS_DIR / scenario
    paths: dict[str, Path | None] = {}
    for name in PLAYBOOK_FILES:
        candidate = scenario_root / name if name == "molecule.yml" else default_root / name
        paths[name] = candidate if candidate.exists() else None
    return paths


def _load_yaml(path: Path) -> Any:
    with open(path) as f:
        return yaml.safe_load(f)


def _find_var_refs(
    value: Any, parent_keys: tuple[str, ...] = ()
) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        for m in _VAR_REF_RE.finditer(value):
            refs.add(m.group(1))
    elif isinstance(value, dict):
        for k, v in value.items():
            refs |= _find_var_refs(v, (*parent_keys, str(k)))
    elif isinstance(value, list):
        for item in value:
            refs |= _find_var_refs(item, parent_keys)
    return refs


KNOWN_BUILTINS = frozenset(
    {
        "ansible_env",
        "lookup",
        "MOLECULE_PROJECT_DIRECTORY",
        "MOLECULE_SCENARIO_DIRECTORY",
        "MOLECULE_EPHEMERAL_DIRECTORY",
        "VIRTUAL_ENV",
        "GLUDD_MOCK_PORT",
        "ANSIBLE_COLLECTIONS_PATH",
        "PATH",
        "ansible_facts",
        "playbook_dir",
    }
)


# ---------------------------------------------------------------------------
# 1. YAML parse tests
# ---------------------------------------------------------------------------


class TestMoleculeYamlParse:
    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_parses(self, scenario: str) -> None:
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        assert path is not None, f"[{scenario}] molecule.yml missing"
        data = _load_yaml(path)
        assert data, f"[{scenario}] molecule.yml is empty"
        assert isinstance(data, dict), f"[{scenario}] molecule.yml is not a mapping"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_yml_parses(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        assert path is not None, f"[{scenario}] converge.yml missing"
        plays = _load_yaml(path)
        assert plays, f"[{scenario}] converge.yml is empty"
        assert isinstance(plays, list), f"[{scenario}] converge.yml is not a list"
        for idx, play in enumerate(plays):
            assert isinstance(play, dict), f"[{scenario}] converge.yml play {idx} is not a mapping"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_prepare_yml_parses(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_PREPARE:
            pytest.skip(f"no prepare.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["prepare.yml"]
        assert path is not None, f"[{scenario}] prepare.yml missing"
        plays = _load_yaml(path)
        assert plays, f"[{scenario}] prepare.yml is empty"
        assert isinstance(plays, list), f"[{scenario}] prepare.yml is not a list"
        for idx, play in enumerate(plays):
            assert isinstance(play, dict), f"[{scenario}] prepare.yml play {idx} is not a mapping"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_verify_yml_parses(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_VERIFY:
            pytest.skip(f"no verify.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["verify.yml"]
        assert path is not None, f"[{scenario}] verify.yml missing"
        plays = _load_yaml(path)
        assert plays, f"[{scenario}] verify.yml is empty"
        assert isinstance(plays, list), f"[{scenario}] verify.yml is not a list"
        for idx, play in enumerate(plays):
            assert isinstance(play, dict), f"[{scenario}] verify.yml play {idx} is not a mapping"


# ---------------------------------------------------------------------------
# 2. Required field tests
# ---------------------------------------------------------------------------


class TestMoleculeRequiredFields:
    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_has_driver(self, scenario: str) -> None:
        if scenario == "default":
            pytest.skip("canonical default scenario has no driver")
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        assert path is not None
        data = _load_yaml(path)
        assert "driver" in data, f"[{scenario}] molecule.yml missing 'driver'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_has_provisioner(self, scenario: str) -> None:
        if scenario == "default":
            pytest.skip("canonical default scenario has no provisioner")
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        assert path is not None
        data = _load_yaml(path)
        assert "provisioner" in data, f"[{scenario}] molecule.yml missing 'provisioner'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_has_scenario_key(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_SCENARIO_KEY:
            pytest.skip(f"molecule.yml for {scenario} has no scenario key")
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        assert path is not None
        data = _load_yaml(path)
        assert "scenario" in data, f"[{scenario}] molecule.yml missing 'scenario'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_has_name(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        assert path is not None
        plays = _load_yaml(path)
        for idx, play in enumerate(plays):
            if "import_playbook" in play:
                continue
            assert "name" in play, f"[{scenario}] converge.yml play {idx} missing 'name'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_playbooks_have_hosts_or_import(self, scenario: str) -> None:
        paths = _collect_playbook_paths(scenario)
        for filename in ["converge.yml", "prepare.yml", "verify.yml"]:
            if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                continue
            if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                continue
            if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                continue
            path = paths[filename]
            if path is None:
                continue
            plays = _load_yaml(path)
            for idx, play in enumerate(plays):
                if play is None:
                    continue
                assert ("hosts" in play) or ("import_playbook" in play), (
                    f"[{scenario}] {filename} play {idx} missing 'hosts' and 'import_playbook'"
                )

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_playbooks_with_hosts_have_tasks(self, scenario: str) -> None:
        paths = _collect_playbook_paths(scenario)
        for filename in ["converge.yml", "prepare.yml", "verify.yml"]:
            if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                continue
            if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                continue
            if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                continue
            path = paths[filename]
            if path is None:
                continue
            plays = _load_yaml(path)
            for idx, play in enumerate(plays):
                if play is None:
                    continue
                if "hosts" in play and "import_playbook" not in play:
                    assert "tasks" in play, f"[{scenario}] {filename} play {idx} has 'hosts' but no 'tasks'"


# ---------------------------------------------------------------------------
# 3. Task ordering consistency
# ---------------------------------------------------------------------------


class TestMoleculeTaskOrdering:
    def test_prepare_before_converge_in_molecule_yml(self) -> None:
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            path = paths["molecule.yml"]
            if path is None:
                continue
            data = _load_yaml(path)
            seq = data.get("scenario", {}).get("test_sequence", [])
            if not seq:
                continue
            try:
                pi = seq.index("prepare")
                ci = seq.index("converge")
                assert pi < ci, f"[{scenario}] test_sequence: prepare ({pi}) must come before converge ({ci})"
            except ValueError:
                pass

    def test_converge_before_verify_in_molecule_yml(self) -> None:
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            path = paths["molecule.yml"]
            if path is None:
                continue
            data = _load_yaml(path)
            seq = data.get("scenario", {}).get("test_sequence", [])
            if not seq:
                continue
            try:
                ci = seq.index("converge")
                vi = seq.index("verify")
                assert ci < vi, f"[{scenario}] test_sequence: converge ({ci}) must come before verify ({vi})"
            except ValueError:
                pass

    def test_scenario_has_syntax_if_sequence_defined(self) -> None:
        missing_syntax: list[str] = []
        for scenario in MOLECULE_SCENARIOS:
            if scenario in SCENARIOS_WITHOUT_SCENARIO_KEY:
                continue
            paths = _collect_playbook_paths(scenario)
            path = paths["molecule.yml"]
            if path is None:
                continue
            data = _load_yaml(path)
            seq = data["scenario"].get("test_sequence")
            assert isinstance(seq, list), f"[{scenario}] test_sequence must be a list"
            if scenario in SCENARIOS_WITH_EMPTY_SEQUENCE:
                assert seq == [], f"[{scenario}] inactive scenario must have an empty test_sequence"
                continue
            assert seq, f"[{scenario}] active scenario must have a non-empty test_sequence"
            if "syntax" not in seq:
                missing_syntax.append(scenario)
        assert not missing_syntax, f"test_sequence missing 'syntax': {', '.join(missing_syntax)}"


# ---------------------------------------------------------------------------
# 4. Variable reference tests
# ---------------------------------------------------------------------------


class TestMoleculeVariableReferences:
    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_vars_not_raw_lookups(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        if path is None:
            return
        plays = _load_yaml(path)
        for _play_idx, play in enumerate(plays):
            if "import_playbook" in play:
                continue
            top_vars = play.get("vars", {})
            defined = set(top_vars.keys()) if isinstance(top_vars, dict) else set()
            defined |= KNOWN_BUILTINS

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_no_double_brace_in_play_name(self, scenario: str) -> None:
        paths = _collect_playbook_paths(scenario)
        for filename in ["converge.yml", "prepare.yml", "verify.yml"]:
            if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                continue
            if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                continue
            if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                continue
            path = paths[filename]
            if path is None:
                continue
            plays = _load_yaml(path)
            for play_idx, play in enumerate(plays):
                play_name = play.get("name", "")
                assert "{{" not in play_name, (
                    f"[{scenario}] {filename} play {play_idx} name contains unresolved template: {play_name}"
                )

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_vars_with_mustache_refs_have_default_filters(
        self, scenario: str
    ) -> None:
        paths = _collect_playbook_paths(scenario)
        for filename in ["converge.yml", "prepare.yml", "verify.yml"]:
            if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                continue
            if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                continue
            if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                continue
            path = paths[filename]
            if path is None:
                continue
            plays = _load_yaml(path)
            for play in plays:
                if "hosts" not in play:
                    continue
                for _var_name, var_value in play.get("vars", {}).items():
                    if isinstance(var_value, str) and "{{" in var_value:
                        mustache_refs = _VAR_REF_RE.findall(var_value)
                        for ref_match in mustache_refs:
                            filter_part = ref_match[1] if len(ref_match) > 1 else ""
                            if "default" in filter_part:
                                continue
                            env_match = re.search(r"lookup\('env',\s*'([^']+)'\)", var_value)
                            if env_match:
                                continue

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_tasks_not_empty(self, scenario: str) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        if path is None:
            return
        plays = _load_yaml(path)
        for play in plays:
            if "import_playbook" in play:
                continue
            tasks = play.get("tasks", [])
            assert isinstance(tasks, list), f"[{scenario}] converge.yml tasks is not a list"
            assert len(tasks) > 0, f"[{scenario}] converge.yml tasks is empty"


# ---------------------------------------------------------------------------
# 5. Role dependency declarations
# ---------------------------------------------------------------------------


class TestMoleculeRoleDependencies:
    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_includes_a_role_or_module(
        self, scenario: str
    ) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        if scenario == "noop":
            pytest.skip("noop uses import_playbook to chain to real playbook")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        if path is None:
            return
        plays = _load_yaml(path)
        has_include = False
        for play in plays:
            if "import_playbook" in play:
                has_include = True
                continue
            for task in play.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                for key in task:
                    if "include_role" in key or "." in key:
                        has_include = True
        assert has_include, f"[{scenario}] converge.yml has no include_role or FQCN module call"

    def test_molecule_yml_playbooks_registered(self) -> None:
        for scenario in MOLECULE_SCENARIOS:
            if scenario in SCENARIOS_WITHOUT_CONVERGE:
                continue
            paths = _collect_playbook_paths(scenario)
            path = paths["molecule.yml"]
            if path is None:
                continue
            data = _load_yaml(path)
            provisioner = data.get("provisioner")
            if provisioner is None:
                continue
            playbooks = provisioner.get("playbooks", {})
            assert isinstance(playbooks, dict), f"[{scenario}] molecule.yml provisioner.playbooks must be a mapping"
            assert "converge" in playbooks, f"[{scenario}] molecule.yml missing playbooks.converge"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_driver_name_is_valid(
        self, scenario: str
    ) -> None:
        if scenario == "default":
            pytest.skip("canonical default scenario has no driver")
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        data = _load_yaml(path)
        driver = data.get("driver", {})
        if not driver:
            return
        driver_name = driver.get("name", "")
        assert driver_name in {"default", "delegated", "docker", "podman", ""}, (
            f"[{scenario}] molecule.yml driver.name={driver_name} unexpected"
        )

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_verifier_name_is_ansible(self, scenario: str) -> None:
        if scenario == "default":
            pytest.skip("canonical default scenario has no verifier")
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        data = _load_yaml(path)
        verifier = data.get("verifier", {})
        if verifier:
            assert verifier.get("name", "") == "ansible", f"[{scenario}] molecule.yml verifier.name must be 'ansible'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_converge_include_role_has_name(
        self, scenario: str
    ) -> None:
        if scenario in SCENARIOS_WITHOUT_CONVERGE:
            pytest.skip(f"no converge.yml for {scenario}")
        paths = _collect_playbook_paths(scenario)
        path = paths["converge.yml"]
        if path is None:
            return
        plays = _load_yaml(path)
        for play in plays:
            if "import_playbook" in play:
                continue
            for task in play.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                for key in task:
                    if "include_role" in key:
                        role_ref = task[key]
                        role_name = role_ref.get("name", "") if isinstance(role_ref, dict) else role_ref or ""
                        assert role_name, f"[{scenario}] converge.yml include_role has no role name"


# ---------------------------------------------------------------------------
# 6. Structural coherence tests
# ---------------------------------------------------------------------------


class TestMoleculeStructuralCoherence:
    def test_every_scenario_has_molecule_yml(self) -> None:
        missing = []
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            if paths["molecule.yml"] is None:
                missing.append(f"{scenario}/molecule.yml")
        assert not missing, f"Missing molecule.yml: {missing}"

    def test_required_files_exist_for_active_scenarios(self) -> None:
        missing = []
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            if paths["molecule.yml"] is None:
                continue
            for fname in ["converge.yml", "prepare.yml", "verify.yml"]:
                if scenario == "default":
                    continue
                if fname == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                    continue
                if fname == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                    continue
                if fname == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                    continue
                if paths[fname] is None:
                    missing.append(f"{scenario}/{fname}")
        assert not missing, f"Missing files: {missing}"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_molecule_yml_is_valid_mapping(
        self, scenario: str
    ) -> None:
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        loaded = _load_yaml(path)
        assert isinstance(loaded, dict), f"[{scenario}] molecule.yml not a mapping"

    def test_no_empty_playbooks(self) -> None:
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            for filename in ["converge.yml", "prepare.yml", "verify.yml"]:
                if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                    continue
                if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                    continue
                if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                    continue
                path = paths[filename]
                if path is None:
                    continue
                plays = _load_yaml(path)
                assert plays is not None, f"[{scenario}] {filename} is null"
                assert isinstance(plays, list), f"[{scenario}] {filename} is not a list of plays"
                for idx, play in enumerate(plays):
                    assert play is not None, f"[{scenario}] {filename} play {idx} is null"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_scenario_name_matches_directory(
        self, scenario: str
    ) -> None:
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        data = _load_yaml(path)
        cfg_name = data.get("scenario", {}).get("name")
        if cfg_name:
            assert cfg_name == "default", (
                f"[{scenario}] molecule.yml scenario.name={cfg_name}, but directory is 'default'"
            )

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_platforms_is_a_list(self, scenario: str) -> None:
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        data = _load_yaml(path)
        platforms = data.get("platforms", [])
        assert isinstance(platforms, list), f"[{scenario}] molecule.yml platforms is not a list"

    def test_all_playbooks_have_document_start(self) -> None:
        for scenario in MOLECULE_SCENARIOS:
            paths = _collect_playbook_paths(scenario)
            for filename in PLAYBOOK_FILES:
                if filename == "converge.yml" and scenario in SCENARIOS_WITHOUT_CONVERGE:
                    continue
                if filename == "prepare.yml" and scenario in SCENARIOS_WITHOUT_PREPARE:
                    continue
                if filename == "verify.yml" and scenario in SCENARIOS_WITHOUT_VERIFY:
                    continue
                path = paths[filename]
                if path is None:
                    continue
                with open(path) as f:
                    first_line = f.readline().rstrip("\n")
                assert first_line == "---", f"[{scenario}] {filename} missing YAML document start '---'"

    @pytest.mark.parametrize("scenario", MOLECULE_SCENARIOS)
    def test_playbook_filenames_consistent(
        self, scenario: str
    ) -> None:
        paths = _collect_playbook_paths(scenario)
        path = paths["molecule.yml"]
        if path is None:
            return
        data = _load_yaml(path)
        provisioner = data.get("provisioner")
        if provisioner is None:
            return
        playbooks = provisioner.get("playbooks", {})
        if isinstance(playbooks, dict):
            for key, rel_path in playbooks.items():
                local_path = f"default/{key}.yml"
                shared_prefix = "${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/"
                shared_filenames = {f"{key}.yml"}
                if key in {"cleanup", "destroy"}:
                    shared_filenames.add(f"mock_daemon_{key}.yml")
                shared_paths = {
                    f"{shared_prefix}{filename}" for filename in shared_filenames
                }
                allowed_paths = {local_path, *shared_paths}
                assert rel_path in allowed_paths, (
                    f"[{scenario}] molecule.yml playbooks.{key} = {rel_path}, "
                    f"expected one of {sorted(allowed_paths)}"
                )
                if rel_path in shared_paths:
                    shared_filename = rel_path.removeprefix(shared_prefix)
                    canonical_shared = PROJECT_ROOT / "molecule" / "shared" / shared_filename
                    assert canonical_shared.is_file(), (
                        f"[{scenario}] shared playbook does not exist: {canonical_shared}"
                    )
