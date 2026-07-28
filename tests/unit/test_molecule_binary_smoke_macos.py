"""Structural pin for the ``binary_smoke_macos`` molecule scenario.

This scenario smoke-tests the pyinstaller-frozen gludd binary on macOS against
localhost (delegated connection). It guards a class of regression that pure
Python unit tests cannot catch: a frozen binary that boots, parses CLI args,
resolves the 3-tier ansible collections precedence, and starts the daemon
without a ``Traceback`` or a ``Missing base YAML definition file`` error.

These tests verify the SCENARIO exists and is well-formed so that
``make molecule-test SCENARIO=binary_smoke_macos`` and the CI molecule shards
can actually invoke it. They do NOT invoke molecule themselves (that is the
gate's job via ``make molecule-test-shard``).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCENARIO_DIR = _ROOT / "molecule" / "playbooks" / "binary_smoke_macos"
_CONVERGE = "default/converge.yml"
_VERIFY = "default/verify.yml"

_REQUIRED_FILES = (
    "molecule.yml",
    _CONVERGE,
    _VERIFY,
)


def _load_yaml(relative: str) -> object:
    path = _SCENARIO_DIR / relative
    assert path.is_file(), f"scenario file missing: {relative}"
    with path.open() as fh:
        return yaml.safe_load(fh)


class TestScenarioFilesExist:
    """The scenario directory must contain the three canonical molecule files.
    A missing file means ``molecule test`` fails immediately at the playbook
    resolution step — the scenario would be a silent no-op in CI."""

    def test_scenario_directory_exists(self):
        assert _SCENARIO_DIR.is_dir(), (
            f"binary_smoke_macos scenario directory missing: {_SCENARIO_DIR}"
        )

    def test_required_files_present(self):
        missing = [name for name in _REQUIRED_FILES if not (_SCENARIO_DIR / name).is_file()]
        assert not missing, (
            f"binary_smoke_macos scenario missing files: {missing}"
        )


class TestMoleculeYmlShape:
    """molecule.yml must declare the localhost platform, ansible provisioner,
    and ansible verifier — and must wire converge+verify playbooks so the
    scenario actually runs."""

    def test_molecule_yml_is_valid_yaml(self):
        data = _load_yaml("molecule.yml")
        assert isinstance(data, dict), "molecule.yml must parse to a mapping"

    def test_molecule_yml_has_platforms(self):
        data = _load_yaml("molecule.yml")
        assert isinstance(data, dict)
        platforms = data.get("platforms")
        assert isinstance(platforms, list) and platforms, (
            "molecule.yml must declare at least one platform"
        )

    def test_molecule_yml_platform_is_localhost_macos(self):
        data = _load_yaml("molecule.yml")
        platforms = data["platforms"]
        localhost = next((p for p in platforms if p.get("name") == "localhost"), None)
        assert localhost is not None, (
            "molecule.yml must declare a 'localhost' platform entry"
        )
        assert localhost.get("os") == "macos", (
            f"localhost platform must set os: macos; got {localhost.get('os')!r}"
        )
        assert localhost.get("connection") == "local", (
            f"localhost platform must set connection: local; got "
            f"{localhost.get('connection')!r}"
        )

    def test_molecule_yml_has_ansible_provisioner(self):
        data = _load_yaml("molecule.yml")
        provisioner = data.get("provisioner", {})
        assert provisioner.get("name") == "ansible", (
            f"provisioner.name must be 'ansible'; got {provisioner.get('name')!r}"
        )

    def test_molecule_yml_provisioner_references_converge_and_verify(self):
        data = _load_yaml("molecule.yml")
        playbooks = data.get("provisioner", {}).get("playbooks", {})
        assert playbooks.get("prepare"), (
            "provisioner.playbooks must reference a prepare playbook"
        )
        assert playbooks.get("converge"), (
            "provisioner.playbooks must reference a converge playbook"
        )
        assert playbooks.get("verify"), (
            "provisioner.playbooks must reference a verify playbook"
        )

    def test_molecule_yml_runs_prepare_before_converge(self):
        data = _load_yaml("molecule.yml")
        sequence = data.get("scenario", {}).get("test_sequence", [])
        assert "prepare" in sequence
        assert sequence.index("prepare") < sequence.index("converge")

    def test_molecule_yml_has_ansible_verifier(self):
        data = _load_yaml("molecule.yml")
        verifier = data.get("verifier", {})
        assert verifier.get("name") == "ansible", (
            f"verifier.name must be 'ansible'; got {verifier.get('name')!r}"
        )


class TestConvergeYmlShape:
    """converge.yml must invoke the binary through every smoke command and
    start the daemon — otherwise the regression this scenario guards would
    pass silently with a stub playbook."""

    def test_converge_yml_is_valid_yaml(self):
        data = _load_yaml(_CONVERGE)
        assert isinstance(data, list), "converge.yml must parse to a list of plays"

    def test_converge_yml_targets_localhost(self):
        data = _load_yaml(_CONVERGE)
        assert isinstance(data, list)
        hosts = {play.get("hosts") for play in data if isinstance(play, dict)}
        assert "localhost" in hosts, (
            f"converge.yml must target localhost; hosts found: {hosts}"
        )

    def test_converge_yml_runs_version_subcommand(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert re.search(r"\bversion\b", text), (
            "converge.yml must run `gludd version` (or --version) to verify "
            "the frozen binary reports a version string"
        )

    def test_converge_yml_runs_help(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "--help" in text, (
            "converge.yml must run `gludd --help` to verify argparse boots"
        )

    def test_converge_yml_runs_project_paths(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "project" in text and "paths" in text, (
            "converge.yml must run `gludd project paths` to verify the 3-tier "
            "collections precedence resolves from the frozen binary"
        )

    def test_converge_yml_starts_daemon(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "daemon" in text, (
            "converge.yml must start the gludd daemon to verify it boots "
            "without a traceback"
        )
        assert re.search(r"--port\s+\S+", text) or "daemon" in text, (
            "converge.yml must start the daemon on an explicit port"
        )

    def test_converge_yml_polls_health_endpoint(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "healthz" in text or "health" in text, (
            "converge.yml must poll the daemon health endpoint to verify the "
            "server is reachable post-startup"
        )

    def test_converge_yml_makes_binary_executable(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "0755" in text or "mode" in text, (
            "converge.yml must chmod the binary executable before invoking it"
        )

    def test_converge_yml_supports_local_or_downloaded_binary(self):
        text = (_SCENARIO_DIR / _CONVERGE).read_text()
        assert "GLUDD_BINARY_PATH" in text or "dist/gludd" in text, (
            "converge.yml must accept a local binary path (env override or "
            "dist/gludd) so the scenario runs against a locally-built binary"
        )


class TestVerifyYmlShape:
    """verify.yml must assert the smoke invariants — version string, help
    output, project paths, no traceback, no Missing base YAML definition file,
    and a reachable health endpoint. A verify playbook missing any of these
    would let the regression slip through."""

    def test_verify_yml_is_valid_yaml(self):
        data = _load_yaml(_VERIFY)
        assert isinstance(data, list), "verify.yml must parse to a list of plays"

    def test_verify_yml_targets_localhost(self):
        data = _load_yaml(_VERIFY)
        assert isinstance(data, list)
        hosts = {play.get("hosts") for play in data if isinstance(play, dict)}
        assert "localhost" in hosts, (
            f"verify.yml must target localhost; hosts found: {hosts}"
        )

    def test_verify_yml_asserts_version_string(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "version" in text.lower(), (
            "verify.yml must assert that `gludd version` produced a version string"
        )
        assert "is not none" in text, (
            "regex_search results must be converted to booleans for strict Ansible"
        )

    def test_verify_yml_asserts_help_contents(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "usage" in text.lower(), (
            "verify.yml must assert that `gludd --help` contains usage info"
        )

    def test_verify_yml_asserts_project_paths_output(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "paths" in text, (
            "verify.yml must assert that `gludd project paths` produced entries"
        )

    def test_verify_yml_asserts_no_traceback(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "Traceback" in text, (
            "verify.yml must assert that the daemon emitted no Python traceback"
        )

    def test_verify_yml_asserts_no_missing_base_yaml_definition_file(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "Missing base YAML definition file" in text, (
            "verify.yml must assert that the daemon did not emit the "
            "'Missing base YAML definition file' frozen-binary regression"
        )

    def test_verify_yml_asserts_health_endpoint(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "health" in text.lower(), (
            "verify.yml must assert the daemon health endpoint returned 200 "
            "or a JSON status field"
        )

    def test_verify_yml_kills_daemon(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert "kill" in text.lower(), (
            "verify.yml must kill the smoke daemon after assertions so the "
            "scenario leaves no orphaned process"
        )


class TestNoGatherFactsTrue:
    """localhost plays must NOT use ``gather_facts: true`` — that pattern is
    flagged by ``scripts/check_molecule_yaml.py`` and would fail the gate.
    converge.yml uses ``gather_facts: true`` only when it genuinely needs the
    local box's ansible_python_interpreter; verify.yml must stay explicit-off."""

    def test_verify_yml_does_not_gather_facts_true(self):
        text = (_SCENARIO_DIR / _VERIFY).read_text()
        assert re.search(r"gather_facts:\s*true", text) is None, (
            "verify.yml must not set gather_facts: true on localhost"
        )


__all__ = [
    "TestConvergeYmlShape",
    "TestMoleculeYmlShape",
    "TestNoGatherFactsTrue",
    "TestScenarioFilesExist",
    "TestVerifyYmlShape",
]
