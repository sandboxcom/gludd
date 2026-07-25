"""Structural pin for the ``binary_smoke_linux`` molecule scenario.

This scenario smoke-tests the BUILT gludd binary (a PyInstaller single-file
bundle produced by ``make build-executable``) inside a Linux container
(ubuntu:24.04 via the ``docker``/``podman`` driver). It exercises:

  * binary boot (no import / missing-file errors),
  * ``gludd version``           -> SemVer string,
  * ``gludd --help``            -> full subcommand listing,
  * ``gludd project paths``     -> bundled config/templates/playbooks resolution,
  * ``gludd daemon``            -> start + ``/healthz`` 200,
  * ``POST /api/todos``         -> daemon accepts + queues a trivial job,
  * error paths:
      - ``gludd --invalid-flag`` exits non-zero with a clean argparse error,
      - a second ``gludd daemon`` on an occupied port fails gracefully.

Why a *static* unit test (not a runtime one): the molecule scenario requires a
container runtime + the built binary; neither is available in the pytest
environment. So this test validates the scenario's STRUCTURE and coverage
declaratively — it parses the YAML and asserts the scenario covers every
behavior listed above. The runtime verification lives in the scenario's own
``verify.yml`` (which runs inside the container).

This mirrors the pattern in ``test_molecule_parallel.py`` (which statically
pins the CI molecule job's matrix shape).
"""

from __future__ import annotations

import os
import re

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCENARIO_DIR = os.path.join(_ROOT, "molecule", "playbooks", "binary_smoke_linux")


def _load(rel: str) -> str:
    path = os.path.join(_SCENARIO_DIR, rel)
    assert os.path.isfile(path), f"missing scenario file: {rel}"
    with open(path) as fh:
        return fh.read()


def _load_yaml(rel: str) -> object:
    return yaml.safe_load(_load(rel))


# ---------------------------------------------------------------------------
# Scenario exists + uses the container (docker) driver
# ---------------------------------------------------------------------------


class TestScenarioShape:
    def test_scenario_directory_exists(self):
        assert os.path.isdir(_SCENARIO_DIR), "binary_smoke_linux scenario missing"

    def test_molecule_yml_present(self):
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "molecule.yml"))

    def test_converge_yml_present(self):
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "converge.yml"))

    def test_verify_yml_present(self):
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "verify.yml"))

    def test_prepare_yml_present(self):
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "prepare.yml"))

    def test_molecule_uses_container_driver(self):
        data = _load_yaml("molecule.yml")
        assert isinstance(data, dict)
        driver = data.get("driver", {})
        assert driver.get("name") in {"docker", "podman"}, (
            "scenario must use a container driver (docker/podman) so the "
            f"binary is exercised on Linux; got driver={driver.get('name')!r}"
        )

    def test_molecule_declares_ubuntu_platform(self):
        data = _load_yaml("molecule.yml")
        platforms = data.get("platforms", [])
        assert isinstance(platforms, list) and platforms, "platforms list is empty"
        names = [str(p.get("image", "")) for p in platforms]
        assert any("ubuntu" in n for n in names), (
            "scenario must target an ubuntu image to exercise the Linux build; "
            f"got images={names}"
        )

    def test_molecule_uses_ansible_provisioner_and_verifier(self):
        data = _load_yaml("molecule.yml")
        assert data.get("provisioner", {}).get("name") == "ansible"
        assert data.get("verifier", {}).get("name") == "ansible"

    def test_molecule_wires_default_playbooks(self):
        data = _load_yaml("molecule.yml")
        playbooks = data.get("provisioner", {}).get("playbooks", {})
        for key in ("prepare", "converge", "verify"):
            assert key in playbooks, (
                f"provisioner.playbooks missing '{key}' reference"
            )


# ---------------------------------------------------------------------------
# converge.yml covers every required behavior
# ---------------------------------------------------------------------------


class TestConvergeCoverage:
    def test_converge_installs_minimal_deps(self):
        out = _load("default/converge.yml")
        assert "ca-certificates" in out and "curl" in out, (
            "converge must install ca-certificates + curl for the health poll"
        )

    def test_converge_copies_the_built_binary(self):
        out = _load("default/converge.yml")
        assert "dist/gludd" in out, "converge must copy the built binary (dist/gludd)"
        assert "chmod" in out.lower() or "mode: \"0755\"" in out or "mode: '0755'" in out, (
            "converge must make the binary executable"
        )

    def test_converge_runs_version_subcommand(self):
        out = _load("default/converge.yml")
        assert re.search(r"gludd[^\\\n]* version\b", out) or " version" in out, (
            "converge must run 'gludd version' (no --version flag exists)"
        )

    def test_converge_runs_help_flag(self):
        out = _load("default/converge.yml")
        assert "--help" in out, "converge must run 'gludd --help'"

    def test_converge_runs_project_paths(self):
        out = _load("default/converge.yml")
        assert "project paths" in out, (
            "converge must run 'gludd project paths' to exercise bundled-path resolution"
        )

    def test_converge_starts_daemon_backgrounded(self):
        out = _load("default/converge.yml")
        assert "daemon" in out, "converge must start the gludd daemon"
        assert "nohup" in out or "&" in out or "async" in out, (
            "daemon must be started in the background (nohup/&/async)"
        )

    def test_converge_polls_health_endpoint(self):
        out = _load("default/converge.yml")
        assert "healthz" in out, (
            "converge must poll /healthz (the canonical daemon health endpoint)"
        )
        assert "30" in out, "health poll must allow up to ~30s"

    def test_converge_submits_job_via_daemon_api(self):
        out = _load("default/converge.yml")
        assert "/api/todos" in out, (
            "converge must submit a trivial job via POST /api/todos (the job-"
            "submission API; there is no /api/playbook/run endpoint)"
        )

    def test_converge_covers_invalid_flag_error_path(self):
        out = _load("default/converge.yml")
        assert "--invalid-flag" in out, (
            "converge must run 'gludd --invalid-flag' to verify a clean argparse error"
        )

    def test_converge_covers_occupied_port_error_path(self):
        out = _load("default/converge.yml")
        # Second daemon invocation against the already-bound port.
        assert out.count("daemon") >= 2, (
            "converge must start a second daemon on the occupied port"
        )


# ---------------------------------------------------------------------------
# verify.yml asserts every required invariant
# ---------------------------------------------------------------------------


class TestVerifyAssertions:
    def test_verify_asserts_semver(self):
        out = _load("default/verify.yml")
        assert "regex" in out.lower() or re.search(r"\\d\+", out), (
            "verify must assert 'version' output is a SemVer (\\d+\\.\\d+\\.\\d+)"
        )

    def test_verify_asserts_subcommands_listed(self):
        out = _load("default/verify.yml")
        for sub in ("daemon", "project"):
            assert sub in out, f"verify must assert --help lists the '{sub}' subcommand"

    def test_verify_asserts_daemon_health_200(self):
        out = _load("default/verify.yml")
        assert "healthz" in out and "200" in out, (
            "verify must assert the daemon /healthz endpoint returns HTTP 200"
        )

    def test_verify_asserts_no_traceback(self):
        out = _load("default/verify.yml")
        assert "Traceback" in out, (
            "verify must assert no Python traceback appears in any output"
        )

    def test_verify_asserts_no_module_not_found(self):
        out = _load("default/verify.yml")
        assert "ModuleNotFoundError" in out, (
            "verify must assert no ModuleNotFoundError (binary bundling regression)"
        )

    def test_verify_asserts_no_missing_base_yaml(self):
        out = _load("default/verify.yml")
        assert "Missing base YAML definition file" in out, (
            "verify must assert the 'Missing base YAML definition file' error is absent"
        )

    def test_verify_asserts_bundled_path_resolution(self):
        out = _load("default/verify.yml")
        assert "Collection search path" in out or "project paths" in out, (
            "verify must assert the binary locates bundled config/playbooks paths"
        )

    def test_verify_assertes_no_import_errors(self):
        out = _load("default/verify.yml")
        assert "ImportError" in out or "ModuleNotFoundError" in out, (
            "verify must assert no import errors"
        )

    def test_verify_asserts_job_processed(self):
        out = _load("default/verify.yml")
        assert "/api/todos" in out or "todo_id" in out, (
            "verify must assert the daemon accepted the submitted job"
        )

    def test_verify_asserts_invalid_flag_exits_nonzero(self):
        out = _load("default/verify.yml")
        assert "bad" in out.lower() or "invalid" in out.lower(), (
            "verify must assert the invalid-flag invocation exited non-zero"
        )

    def test_verify_asserts_port_clash_handled_gracefully(self):
        out = _load("default/verify.yml")
        assert "port" in out.lower() or "clash" in out.lower(), (
            "verify must assert the occupied-port daemon failed gracefully"
        )


# ---------------------------------------------------------------------------
# prepare.yml ensures the binary is built before the container uses it
# ---------------------------------------------------------------------------


class TestPrepare:
    def test_prepare_builds_or_locates_binary(self):
        out = _load("default/prepare.yml")
        assert "dist/gludd" in out or "build-executable" in out, (
            "prepare must ensure dist/gludd exists (build via make build-executable)"
        )
