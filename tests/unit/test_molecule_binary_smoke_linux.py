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
import tomllib

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCENARIO_DIR = os.path.join(_ROOT, "molecule", "playbooks", "binary_smoke_linux")
_MAKEFILE = os.path.join(_ROOT, "Makefile")
_PYPROJECT = os.path.join(_ROOT, "pyproject.toml")


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

    def test_cleanup_yml_present(self):
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "cleanup.yml"))

    def test_dependency_manifests_are_complete(self):
        requirements = _load_yaml("requirements.yml")
        collections = _load_yaml("collections.yml")

        assert requirements == {"roles": []}
        assert isinstance(collections, dict)
        names = {
            dependency["name"]
            for dependency in collections.get("collections", [])
        }
        assert names == {"ansible.posix", "community.docker"}

    def test_molecule_uses_container_driver(self):
        data = _load_yaml("molecule.yml")
        assert isinstance(data, dict)
        driver = data.get("driver", {})
        assert driver.get("name") in {"docker", "podman"}, (
            "scenario must use a container driver (docker/podman) so the "
            f"binary is exercised on Linux; got driver={driver.get('name')!r}"
        )

    def test_docker_driver_dependency_is_declared(self):
        with open(_PYPROJECT, "rb") as fh:
            project = tomllib.load(fh)

        dependency_sets = (
            project["project"]["optional-dependencies"]["dev"],
            project["dependency-groups"]["dev"],
        )
        for dependencies in dependency_sets:
            assert any(
                dependency.startswith("molecule-plugins[docker]")
                for dependency in dependencies
            ), "molecule Docker scenarios require molecule-plugins[docker]"

    def test_make_target_routes_docker_sdk_to_podman_socket(self):
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        assert "LIMA_INSTANCE ?= gludd-docker" in makefile
        assert 'limactl list "$(LIMA_INSTANCE)"' in makefile
        assert "PODMAN_MACHINE ?= gludd" in makefile
        assert 'podman machine inspect "$(PODMAN_MACHINE)"' in makefile
        assert "DOCKER_HOST=" in makefile
        assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in makefile
        assert 'PROJECT_COLLECTIONS="$$(pwd)/collections"' in makefile
        assert (
            'export ANSIBLE_COLLECTIONS_PATH="$$PROJECT_COLLECTIONS:'
            '$$ANSIBLE_STATE_DIR/collections:'
        ) in makefile
        assert 'DOCKER_CONFIG_VALUE="$$ANSIBLE_STATE_DIR/docker"' in makefile
        assert 'export DOCKER_CONFIG="$$DOCKER_CONFIG_VALUE"' in makefile
        assert "$(UV) run molecule reset" not in makefile
        assert "lima-docker-status:" in makefile
        assert "docker ps --all" in makefile
        assert "docker images" in makefile
        assert "lima-docker-pull:" in makefile
        assert "LIMA_DOCKER_CONFIG ?= /tmp/gludd-lima-docker-config" in makefile
        assert 'DOCKER_CONFIG="$(LIMA_DOCKER_CONFIG)"' in makefile
        assert 'docker pull "$(LIMA_IMAGE)"' in makefile

    def test_legacy_default_machine_cleanup_is_bounded_and_opt_in(self):
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        assert "podman-legacy-default-delete:" in makefile
        assert "PODMAN_LEGACY_MACHINE ?= podman-machine-default" in makefile
        assert "PODMAN_LEGACY_DELETE_VALIDATE_ONLY ?= 1" in makefile
        assert "PODMAN_LEGACY_DELETE_TIMEOUT_SECS ?= 120" in makefile
        assert (
            'if [ "$(PODMAN_LEGACY_MACHINE)" != "podman-machine-default" ]; then'
            in makefile
        )
        assert 'podman machine rm -f "$(PODMAN_LEGACY_MACHINE)"' in makefile
        assert "PODMAN_LEGACY_DELETE_HEARTBEAT" in makefile

    def test_molecule_clean_removes_only_generated_dependency_namespaces(self):
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        section = makefile.split("molecule-clean:", 1)[1].split(
            "molecule-test:", 1
        )[0]
        assert 'git ls-files -- "$$d"' in section
        assert "Preserving tracked scenario" in section
        assert "collections/ansible_collections/ansible" in section
        assert "collections/ansible_collections/community" in section
        assert "general_ludd" not in section

    def test_molecule_test_uses_canonical_source_without_copying(self):
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        section = makefile.split("molecule-test:", 1)[1].split(
            "git-status:", 1
        )[0]
        assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in section
        assert 'rm -rf "$$RUNTIME_SCENARIO"' not in section
        assert 'cp "molecule/playbooks/$(SCENARIO)/molecule.yml"' not in section

    def test_molecule_declares_ubuntu_platform(self):
        data = _load_yaml("molecule.yml")
        platforms = data.get("platforms", [])
        assert isinstance(platforms, list) and platforms, "platforms list is empty"
        names = [str(p.get("image", "")) for p in platforms]
        assert any("ubuntu" in n for n in names), (
            "scenario must target an ubuntu image to exercise the Linux build; "
            f"got images={names}"
        )

    def test_molecule_uses_prebuilt_image_and_bootstraps_python(self):
        data = _load_yaml("molecule.yml")
        platforms = data.get("platforms", [])
        assert platforms[0].get("pre_build_image") is True

        converge = _load_yaml("default/converge.yml")
        bootstrap = converge[0]
        assert bootstrap.get("gather_facts") is False
        serialized = yaml.safe_dump(bootstrap)
        assert "ansible.builtin.raw" in serialized
        assert "apt-get install -y python3" in serialized
        assert converge[1].get("become") is False

    def test_molecule_uses_ansible_provisioner_and_verifier(self):
        data = _load_yaml("molecule.yml")
        assert data.get("provisioner", {}).get("name") == "ansible"
        assert data.get("verifier", {}).get("name") == "ansible"

    def test_molecule_wires_default_playbooks(self):
        data = _load_yaml("molecule.yml")
        playbooks = data.get("provisioner", {}).get("playbooks", {})
        for key in ("cleanup", "prepare", "converge", "verify"):
            assert key in playbooks, (
                f"provisioner.playbooks missing '{key}' reference"
            )

        sequence = data.get("scenario", {}).get("test_sequence", [])
        assert sequence[:3] == ["dependency", "cleanup", "destroy"]


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

    def test_converge_authenticates_job_submission(self):
        converge = _load_yaml("default/converge.yml")
        daemon_play = converge[1]
        daemon_psk = daemon_play["vars"].get("daemon_psk")
        assert daemon_psk, "the smoke daemon must exercise fail-closed PSK auth"

        tasks = daemon_play["tasks"]
        start = next(task for task in tasks if task["name"].startswith("Start the"))
        submit = next(task for task in tasks if task["name"].startswith("Submit a"))
        assert start["environment"]["GLUDD_AUTH_PSK"] == "{{ daemon_psk }}"
        assert submit["ansible.builtin.uri"]["headers"]["Authorization"] == (
            "Bearer {{ daemon_psk }}"
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

    def test_converge_persists_remote_port_clash_output(self):
        converge = _load_yaml("default/converge.yml")
        tasks = converge[1]["tasks"]
        persist = next(
            task for task in tasks if task["name"] == "Persist port-clash result"
        )
        content = persist["ansible.builtin.copy"]["content"]
        assert "lookup(" not in content
        assert "{{ port_clash.stdout }}" in content


# ---------------------------------------------------------------------------
# verify.yml asserts every required invariant
# ---------------------------------------------------------------------------


class TestVerifyAssertions:
    def test_verify_does_not_require_sudo_in_root_container(self):
        verify = _load_yaml("default/verify.yml")
        assert verify[0].get("become") is False

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
        assert "'(missing)' not in cli_outputs" in out
        assert "'=== project paths (rc=0) ===' in cli_outputs" in out

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

    def test_verify_uses_ansible_compatible_nonzero_regexes(self):
        out = _load("default/verify.yml")

        assert "regex_search('rc=[1-9][0-9]*')" in out
        assert "regex_search('EXIT_RC=[1-9][0-9]*')" in out
        assert "regex_search('rc=([0-9]+)'," not in out
        assert "regex_search('EXIT_RC=([0-9]+)'," not in out

    def test_verify_asserts_port_clash_handled_gracefully(self):
        out = _load("default/verify.yml")
        assert "port" in out.lower() or "clash" in out.lower(), (
            "verify must assert the occupied-port daemon failed gracefully"
        )


# ---------------------------------------------------------------------------
# prepare.yml ensures the binary is built before the container uses it
# ---------------------------------------------------------------------------


class TestPrepare:
    def test_make_target_builds_a_real_linux_binary_before_molecule(self):
        out = _load("default/prepare.yml")
        assert "dist/linux/gludd" in out
        assert "ansible.builtin.command" not in out

        with open(_MAKEFILE) as fh:
            makefile = fh.read()
        assert 'if [ "$(SCENARIO)" = "binary_smoke_linux" ]' in makefile
        assert "$(MAKE) --no-print-directory build-linux-executable" in makefile
        assert "build-linux-executable:" in makefile
        assert "UV_PROJECT_ENVIRONMENT=/tmp/gludd-linux-venv" in makefile
        assert "git archive HEAD" in makefile
        assert (
            "LINUX_BINARY_IMAGE ?= "
            "ghcr.io/astral-sh/uv:python3.12-bookworm-slim@"
            "sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261"
            "ba1b7147afa78e58"
        ) in makefile
        assert "--pull=always" in makefile
        assert "LINUX_BINARY_SCRATCH_ROOT ?= $(HOME)/tmp/gludd-linux-build" in makefile
        assert "DEBIAN_SNAPSHOT ?= 20260729T000000Z" in makefile
        assert "LINUX_BINUTILS_VERSION ?=" in makefile
        assert "LINUX_APT_UTILS_VERSION ?=" in makefile
        assert "snapshot.debian.org/archive/debian/" in makefile
        assert "snapshot.debian.org/archive/debian-security/" in makefile
        assert "Acquire::Check-Valid-Until" in makefile
        assert "APT::Update::Error-Mode=any update" in makefile
        assert (
            'sed -i "\\|/usr/share/man/|d" '
            "/etc/dpkg/dpkg.cfg.d/docker"
        ) in makefile
        assert 'mkdir -p /usr/share/man/man7' in makefile
        assert ': > /usr/share/man/man7/bash-builtins.7.gz' in makefile
        assert "update-alternatives --remove builtins.7.gz" in makefile
        assert 'rm -f /usr/share/man/man7/bash-builtins.7.gz' in makefile
        assert (
            'apt-get install -y --download-only --no-install-recommends '
            '"apt-utils=$(LINUX_APT_UTILS_VERSION)"'
        ) in makefile
        assert "dpkg -i /var/cache/apt/archives/apt-utils_" in makefile
        assert "apt-get -y --no-remove dist-upgrade" in makefile
        assert (
            'apt-get install -y --no-install-recommends '
            '"binutils=$(LINUX_BINUTILS_VERSION)"'
        ) in makefile
        assert "command -v objdump" in makefile
        assert "command -v objcopy" in makefile
        assert "dpkg-query -W binutils" in makefile
        assert "apt-get -s dist-upgrade" in makefile
        assert (
            "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded."
            in makefile
        )
        assert "audit_pyinstaller_warnings.py" in makefile
        assert "/tmp/gludd-pyinstaller-build/gludd/warn-gludd.txt" in makefile
        assert "--spec gludd.spec" in makefile
        assert ":/workspace:ro" in makefile
        assert '@set -e; if [ "$$(uname -s)" = "Linux" ]' in makefile
        assert "output_dir=$$(mktemp" not in makefile
        assert 'rm -rf "$$source_dir"' in makefile
        assert '-v "$$output_dir:/out"' not in makefile
        assert "cp /tmp/gludd-pyinstaller-build/gludd/warn-gludd.txt" in makefile
        assert "pyinstaller_status=0" in makefile
        assert "|| pyinstaller_status=$$?" in makefile
        assert 'test "$$pyinstaller_status" -eq 0' in makefile
        assert "build_status=0" in makefile
        assert "|| build_status=$$?" in makefile
        assert (
            'docker cp "$$container_name:/out/warn-gludd.txt" '
            '"$(dir $(LINUX_BINARY_OUTPUT))warn-gludd.txt"'
        ) in makefile
        status_check = makefile.index('if [ "$$build_status" -ne 0 ]')
        binary_copy = makefile.index(
            'docker cp "$$container_name:/out/gludd" "$(LINUX_BINARY_OUTPUT)"'
        )
        assert status_check < binary_copy
        assert 'exit "$$build_status"' in makefile
        assert "file \"$(LINUX_BINARY_OUTPUT)\"" in makefile
        assert "ELF" in makefile
