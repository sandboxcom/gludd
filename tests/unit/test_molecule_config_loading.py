"""Structural pin for the ``config_loading`` molecule scenario.

This scenario verifies the gludd binary can correctly load ALL bundled config
files at runtime. The scenario lives under
``molecule/playbooks/config_loading/``. These tests assert the scenario's
*shape* (files exist, playbooks reference the right commands, verify.yml
asserts the documented invariants) so a regression that deletes or gut-punches
the scenario is caught at gate time.

The runtime behavior is exercised by ``make molecule-test-all`` (or the
sharded CI variants); this file is the structural canary.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO_ROOT = _REPO_ROOT / "molecule" / "playbooks" / "config_loading"

_CONVERGE = _SCENARIO_ROOT / "default" / "converge.yml"
_VERIFY = _SCENARIO_ROOT / "default" / "verify.yml"
_PREPARE = _SCENARIO_ROOT / "default" / "prepare.yml"
_MOLECULE = _SCENARIO_ROOT / "molecule.yml"


def _load(path: Path) -> str:
    assert path.is_file(), f"missing scenario file: {path}"
    return path.read_text()


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(_load(path))


# ---------------------------------------------------------------------------
# Scenario file presence
# ---------------------------------------------------------------------------


class TestScenarioFiles:
    def test_converge_yml_exists(self) -> None:
        assert _CONVERGE.is_file()

    def test_verify_yml_exists(self) -> None:
        assert _VERIFY.is_file()

    def test_prepare_yml_exists(self) -> None:
        assert _PREPARE.is_file()

    def test_molecule_yml_exists(self) -> None:
        assert _MOLECULE.is_file()

    def test_molecule_yml_uses_default_driver(self) -> None:
        doc = _load_yaml(_MOLECULE)
        assert isinstance(doc, dict)
        assert doc["driver"]["name"] == "default"

    def test_molecule_yml_wires_prepare_converge_verify(self) -> None:
        doc = _load_yaml(_MOLECULE)
        playbooks = doc["provisioner"]["playbooks"]
        for key in ("prepare", "converge", "verify"):
            assert key in playbooks, f"molecule.yml missing playbook: {key}"

    def test_molecule_yml_has_test_sequence(self) -> None:
        doc = _load_yaml(_MOLECULE)
        seq = doc["scenario"]["test_sequence"]
        # Converge persists failure-mode evidence for the verifier.
        assert "converge" in seq and "verify" in seq

    def test_molecule_yml_only_runs_implemented_phases(self) -> None:
        doc = _load_yaml(_MOLECULE)
        assert doc["scenario"]["test_sequence"] == [
            "syntax",
            "prepare",
            "converge",
            "verify",
        ], "missing phase playbooks produce Molecule warnings"

    def test_molecule_yml_declares_localhost_inventory(self) -> None:
        doc = _load_yaml(_MOLECULE)
        localhost = doc["provisioner"]["inventory"]["hosts"]["all"]["hosts"][
            "localhost"
        ]
        assert localhost["ansible_connection"] == "local"


# ---------------------------------------------------------------------------
# converge.yml references every config-loading path
# ---------------------------------------------------------------------------


class TestConvergeExercisesConfigLoading:
    def test_scenario_materializes_and_uses_all_three_collection_tiers(
        self,
    ) -> None:
        prepare = _load(_PREPARE)
        converge = _load(_CONVERGE)

        assert ".gludd/collections" in prepare, (
            "prepare must materialize the project collection tier"
        )
        assert "xdg/gludd/collections" in prepare, (
            "prepare must materialize the user collection tier"
        )
        assert 'project paths "{{ path_project_dir }}" --json' in converge, (
            "converge must pass the materialized project root explicitly"
        )
        assert "XDG_CONFIG_HOME: \"{{ path_xdg_dir }}\"" in converge, (
            "converge must point path resolution at the materialized user tier"
        )

    def test_converge_runs_project_paths(self) -> None:
        text = _load(_CONVERGE)
        assert "project paths --json" in text, (
            "converge must run `gludd project paths` to prove path-config loads"
        )

    def test_converge_parses_every_bundled_config(self) -> None:
        text = _load(_CONVERGE)
        assert "yaml.safe_load" in text, (
            "converge must parse bundled config YAML ('config validate' equivalent)"
        )
        assert "config" in text, "converge must target the config/ tree"

    def test_converge_boots_daemon(self) -> None:
        text = _load(_CONVERGE)
        assert re.search(r"\bgludd\b.*\bdaemon\b", text) or "daemon" in text
        assert "daemon.pid" in text, "converge must boot the daemon in the background"

    def test_converge_probes_config_derived_endpoints(self) -> None:
        text = _load(_CONVERGE)
        assert "/healthz" in text
        assert "/api/status" in text, (
            "converge must probe a config-derived endpoint to prove the loaded config is live"
        )

    def test_converge_tests_env_var_override(self) -> None:
        text = _load(_CONVERGE)
        assert "GLUDD_NETWORK__PORT" in text, (
            "converge must test the GLUDD_NETWORK__PORT env-var override"
        )

    def test_converge_tests_failure_modes(self) -> None:
        text = _load(_CONVERGE)
        for needle in ("invalid YAML", "missing optional config", "corrupt"):
            assert needle in text, f"converge missing failure-mode test: {needle}"

    def test_yaml_failure_checks_accept_specific_yaml_error_subclasses(
        self,
    ) -> None:
        converge = _load(_CONVERGE)
        verify = _load(_VERIFY)

        assert converge.count('"is_yaml_error"') >= 2, (
            "failure probes must record isinstance(exc, yaml.YAMLError)"
        )
        assert "inv_json.is_yaml_error" in verify
        assert "corrupt_json.is_yaml_error" in verify

    def test_converge_persists_evidence_for_verify(self) -> None:
        text = _load(_CONVERGE)
        # Every phase must write evidence to {{ work_dir }} so verify can assert.
        for artifact in (
            "project_paths.json",
            "config_parse.json",
            "agents.json",
            "model_profiles.json",
            "daemon.log",
            "failure_invalid_yaml.json",
            "failure_missing_config.json",
            "failure_corrupt_yaml.json",
        ):
            assert artifact in text, f"converge does not persist evidence: {artifact}"


# ---------------------------------------------------------------------------
# verify.yml asserts the documented invariants
# ---------------------------------------------------------------------------


class TestVerifyAssertsConfigInvariants:
    def test_verify_asserts_path_entry_count(self) -> None:
        text = _load(_VERIFY)
        assert "length >= 3" in text, (
            "verify must assert project paths returns >=3 entries (PROJECT/USER/BUNDLED)"
        )

    def test_verify_asserts_all_configs_parse(self) -> None:
        text = _load(_VERIFY)
        assert "bad_configs" in text
        assert "length == 0" in text, (
            "verify must assert every bundled config file parsed cleanly"
        )

    def test_verify_asserts_required_agents(self) -> None:
        text = _load(_VERIFY)
        for agent in ("build", "plan", "explore"):
            assert f"'{agent}' in agents_json.names" in text, (
                f"verify must assert the '{agent}' agent is configured"
            )

    def test_verify_asserts_model_profiles_present(self) -> None:
        text = _load(_VERIFY)
        assert "profiles_json" in text
        assert "length >= 1" in text, (
            "verify must assert model profiles were discovered"
        )

    def test_verify_blocks_config_error_signatures_in_daemon_log(self) -> None:
        text = _load(_VERIFY)
        for sig in ("KeyError", "ConfigParser", "YAMLError"):
            assert sig in text, (
                f"verify must flag '{sig}' as a config-loading failure in the daemon log"
            )

    def test_verify_asserts_failure_modes_handled(self) -> None:
        text = _load(_VERIFY)
        assert "inv_json.handled" in text
        assert "corrupt_json.handled" in text
        assert "miss_json" in text

    def test_verify_asserts_missing_config_falls_back_to_defaults(self) -> None:
        text = _load(_VERIFY)
        assert "missing optional config" in text or "bundled defaults" in text


# ---------------------------------------------------------------------------
# Scenario is discoverable by the molecule coverage tooling
# ---------------------------------------------------------------------------


class TestScenarioDiscoverability:
    def test_scenario_under_playbooks_root(self) -> None:
        # molecule/playbooks/<name>/molecule.yml is the discovery contract.
        assert _MOLECULE.is_file(), (
            "molecule.yml must live at molecule/playbooks/config_loading/molecule.yml"
        )

    def test_scenario_has_default_subdir(self) -> None:
        assert (_SCENARIO_ROOT / "default").is_dir()
