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

import json
import os
import re
import tomllib
from typing import TypeAlias, TypeGuard

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCENARIO_DIR = os.path.join(_ROOT, "molecule", "playbooks", "binary_smoke_linux")
_MAKEFILE = os.path.join(_ROOT, "Makefile")
_PYPROJECT = os.path.join(_ROOT, "pyproject.toml")
_MAKE_TARGET_CONTRACT = os.path.join(_ROOT, "config", "make_target_contract.json")
_LIMA_LIFECYCLE_DOC = os.path.join(_ROOT, "docs", "features", "LIMA_DOCKER_LIFECYCLE.md")

YamlScalar: TypeAlias = str | int | float | bool | None
YamlValue: TypeAlias = YamlScalar | list["YamlValue"] | dict[str, "YamlValue"]


def _load(rel: str) -> str:
    path = os.path.join(_SCENARIO_DIR, rel)
    assert os.path.isfile(path), f"missing scenario file: {rel}"
    with open(path) as fh:
        return fh.read()


def _is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_str_object_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _validated_yaml(value: object, context: str) -> YamlValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if _is_object_list(value):
        return [
            _validated_yaml(item, f"{context}[{index}]")
            for index, item in enumerate(value)
        ]
    if _is_str_object_dict(value):
        return {
            key: _validated_yaml(item, f"{context}.{key}")
            for key, item in value.items()
        }
    raise AssertionError(f"{context} contains unsupported YAML value {value!r}")


def _load_yaml(rel: str) -> YamlValue:
    raw: object = yaml.safe_load(_load(rel))
    return _validated_yaml(raw, rel)


def _require_mapping(value: YamlValue, context: str) -> dict[str, YamlValue]:
    assert isinstance(value, dict), f"{context} must be a mapping"
    return value


def _require_sequence(value: YamlValue, context: str) -> list[YamlValue]:
    assert isinstance(value, list), f"{context} must be a sequence"
    return value


def _require_string(value: YamlValue, context: str) -> str:
    assert isinstance(value, str), f"{context} must be a string"
    return value


def _load_yaml_mapping(rel: str) -> dict[str, YamlValue]:
    return _require_mapping(_load_yaml(rel), rel)


def _load_yaml_sequence(rel: str) -> list[YamlValue]:
    return _require_sequence(_load_yaml(rel), rel)


def _mapping_key(
    mapping: dict[str, YamlValue], key: str, context: str
) -> dict[str, YamlValue]:
    return _require_mapping(mapping.get(key), f"{context}.{key}")


def _sequence_key(
    mapping: dict[str, YamlValue], key: str, context: str
) -> list[YamlValue]:
    return _require_sequence(mapping.get(key), f"{context}.{key}")


def _string_key(mapping: dict[str, YamlValue], key: str, context: str) -> str:
    return _require_string(mapping.get(key), f"{context}.{key}")


def _mapping_sequence_key(
    mapping: dict[str, YamlValue], key: str, context: str
) -> list[dict[str, YamlValue]]:
    return [
        _require_mapping(value, f"{context}.{key}[{index}]")
        for index, value in enumerate(_sequence_key(mapping, key, context))
    ]


def _play(rel: str, index: int) -> dict[str, YamlValue]:
    plays = _load_yaml_sequence(rel)
    assert index < len(plays), f"{rel} missing play at index {index}"
    return _require_mapping(plays[index], f"{rel}[{index}]")


def _task_map(rel: str) -> dict[str, dict[str, YamlValue]]:
    tasks: dict[str, dict[str, YamlValue]] = {}
    for index, value in enumerate(_load_yaml_sequence(rel)):
        task = _require_mapping(value, f"{rel}[{index}]")
        name = _require_string(task.get("name"), f"{rel}[{index}].name")
        tasks[name] = task
    return tasks


def _play_tasks(rel: str, play_index: int) -> list[dict[str, YamlValue]]:
    return _mapping_sequence_key(
        _play(rel, play_index), "tasks", f"{rel}[{play_index}]"
    )


# ---------------------------------------------------------------------------
# Scenario exists + uses the container (docker) driver
# ---------------------------------------------------------------------------


class TestScenarioShape:
    def test_scenario_directory_exists(self) -> None:
        assert os.path.isdir(_SCENARIO_DIR), "binary_smoke_linux scenario missing"

    def test_molecule_yml_present(self) -> None:
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "molecule.yml"))

    def test_converge_yml_present(self) -> None:
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "converge.yml"))

    def test_verify_yml_present(self) -> None:
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "verify.yml"))

    def test_prepare_yml_present(self) -> None:
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "prepare.yml"))

    def test_cleanup_yml_present(self) -> None:
        assert os.path.isfile(os.path.join(_SCENARIO_DIR, "default", "cleanup.yml"))

    def test_dependency_manifests_are_complete(self) -> None:
        requirements = _load_yaml_mapping("requirements.yml")
        collections = _load_yaml_mapping("collections.yml")

        assert requirements == {"roles": []}
        names = {
            _string_key(dependency, "name", "collections.yml.collections")
            for dependency in _mapping_sequence_key(
                collections, "collections", "collections.yml"
            )
        }
        assert names == {"ansible.posix", "community.docker"}

    def test_molecule_uses_container_driver(self) -> None:
        data = _load_yaml_mapping("molecule.yml")
        driver = _mapping_key(data, "driver", "molecule.yml")
        driver_name = _string_key(driver, "name", "molecule.yml.driver")
        assert driver_name in {"docker", "podman"}, (
            "scenario must use a container driver (docker/podman) so the "
            f"binary is exercised on Linux; got driver={driver_name!r}"
        )

    def test_docker_driver_dependency_is_declared(self) -> None:
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

    def test_make_target_routes_docker_sdk_to_podman_socket(self) -> None:
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

    def test_lima_docker_start_is_bounded_namespaced_and_contracted(self) -> None:
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        assert "LIMA_DOCKER_START_TIMEOUT_SECS ?= 180" in makefile
        assert "lima-docker-start:" in makefile
        assert 'instance=$$(limactl list "$(LIMA_INSTANCE)"' in makefile
        assert 'if [ "$$instance" != "$(LIMA_INSTANCE)" ]; then' in makefile
        assert 'limactl start --timeout "$(LIMA_DOCKER_START_TIMEOUT_SECS)s" "$(LIMA_INSTANCE)"' in makefile
        assert 'DOCKER_HOST="unix://$$socket" docker info' in makefile
        assert "LIMA_DOCKER_START_READY" in makefile
        assert "limactl delete" not in makefile

        with open(_MAKE_TARGET_CONTRACT) as fh:
            contract = json.load(fh)
        target = next(
            entry for entry in contract["targets"] if entry["name"] == "lima-docker-start"
        )
        assert target["make_variables"] == [
            "LIMA_INSTANCE",
            "LIMA_DOCKER_CONFIG",
            "LIMA_DOCKER_START_TIMEOUT_SECS",
            "LIMA_DOCKER_VALIDATE_ONLY",
        ]
        assert target["behavior"].endswith("LIMA_DOCKER_VALIDATE_ONLY=1")

    def test_lima_docker_stop_is_bounded_idempotent_and_non_destructive(self) -> None:
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        assert "LIMA_DOCKER_STOP_TIMEOUT_SECS ?= 200" in makefile
        assert "LIMA_DOCKER_STOP_KILL_AFTER_SECS ?= 10" in makefile
        assert "lima-docker-stop:" in makefile
        stop_recipe = makefile.split("lima-docker-stop:", 1)[1].split(
            "\nlima-docker-status:", 1
        )[0]
        assert '*[!A-Za-z0-9._-]*|.|..)' in stop_recipe
        assert "gludd-*)" in stop_recipe
        assert 'record=$$(limactl list "$(LIMA_INSTANCE)"' in stop_recipe
        assert "LIMA_DOCKER_STOP_ALREADY_STOPPED" in stop_recipe
        assert "LIMA_DOCKER_STOP_BEGIN" in stop_recipe
        assert 'limactl --tty=false stop "$(LIMA_INSTANCE)"' in stop_recipe
        assert 'kill -0 "$$stop_pid"' in stop_recipe
        assert 'kill -TERM "$$stop_pid"' in stop_recipe
        assert 'kill -KILL "$$stop_pid"' in stop_recipe
        assert 'wait "$$stop_pid"' in stop_recipe
        assert "LIMA_DOCKER_STOP_TIMEOUT" in stop_recipe
        assert "LIMA_DOCKER_STOP_KILL" in stop_recipe
        assert "command -v gtimeout" not in stop_recipe
        assert "command -v timeout" not in stop_recipe
        assert "LIMA_DOCKER_STOP_READY" in stop_recipe
        assert "--force" not in stop_recipe
        assert "limactl delete" not in stop_recipe
        assert "docker rm" not in stop_recipe

        with open(_MAKE_TARGET_CONTRACT) as fh:
            contract = json.load(fh)
        target = next(
            entry for entry in contract["targets"] if entry["name"] == "lima-docker-stop"
        )
        assert target["make_variables"] == [
            "LIMA_INSTANCE",
            "LIMA_DOCKER_STOP_KILL_AFTER_SECS",
            "LIMA_DOCKER_STOP_TIMEOUT_SECS",
            "LIMA_DOCKER_VALIDATE_ONLY",
        ]
        assert target["behavior"].endswith("LIMA_DOCKER_VALIDATE_ONLY=1")

        with open(_LIMA_LIFECYCLE_DOC) as fh:
            lifecycle_doc = fh.read()
        assert "2026-08-20" in lifecycle_doc
        assert "https://lima-vm.io/docs/reference/limactl_stop/" in lifecycle_doc
        assert "https://github.com/lima-vm/lima/discussions/1666" in lifecycle_doc
        assert "ZDD" in lifecycle_doc
        assert "rollback" in lifecycle_doc.lower()

    def test_legacy_default_machine_cleanup_is_bounded_and_opt_in(self) -> None:
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

    def test_molecule_clean_removes_only_generated_dependency_namespaces(self) -> None:
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

    def test_molecule_test_uses_canonical_source_without_copying(self) -> None:
        with open(_MAKEFILE) as fh:
            makefile = fh.read()

        section = makefile.split("molecule-test:", 1)[1].split(
            "git-status:", 1
        )[0]
        assert 'MOLECULE_GLOB="molecule/playbooks/*/molecule.yml"' in section
        assert 'rm -rf "$$RUNTIME_SCENARIO"' not in section
        assert 'cp "molecule/playbooks/$(SCENARIO)/molecule.yml"' not in section

    def test_molecule_declares_ubuntu_platform(self) -> None:
        data = _load_yaml_mapping("molecule.yml")
        platforms = _mapping_sequence_key(data, "platforms", "molecule.yml")
        assert platforms, "platforms list is empty"
        names = [
            _string_key(platform, "image", "molecule.yml.platforms")
            for platform in platforms
        ]
        assert any("ubuntu" in n for n in names), (
            "scenario must target an ubuntu image to exercise the Linux build; "
            f"got images={names}"
        )

    def test_molecule_uses_prebuilt_image_and_bootstraps_python(self) -> None:
        data = _load_yaml_mapping("molecule.yml")
        platforms = _mapping_sequence_key(data, "platforms", "molecule.yml")
        assert platforms[0].get("pre_build_image") is True

        bootstrap = _play("default/converge.yml", 0)
        assert bootstrap.get("gather_facts") is False
        serialized = yaml.safe_dump(bootstrap)
        assert "ansible.builtin.raw" in serialized
        assert "apt-get install -y python3" in serialized
        assert _play("default/converge.yml", 1).get("become") is False

    def test_molecule_uses_ansible_provisioner_and_verifier(self) -> None:
        data = _load_yaml_mapping("molecule.yml")
        provisioner = _mapping_key(data, "provisioner", "molecule.yml")
        verifier = _mapping_key(data, "verifier", "molecule.yml")
        assert provisioner.get("name") == "ansible"
        assert verifier.get("name") == "ansible"

    def test_molecule_wires_default_playbooks(self) -> None:
        data = _load_yaml_mapping("molecule.yml")
        provisioner = _mapping_key(data, "provisioner", "molecule.yml")
        playbooks = _mapping_key(
            provisioner, "playbooks", "molecule.yml.provisioner"
        )
        for key in ("cleanup", "prepare", "converge", "verify"):
            assert key in playbooks, (
                f"provisioner.playbooks missing '{key}' reference"
            )

        scenario = _mapping_key(data, "scenario", "molecule.yml")
        sequence = _sequence_key(scenario, "test_sequence", "molecule.yml.scenario")
        assert sequence[:3] == ["dependency", "cleanup", "destroy"]


# ---------------------------------------------------------------------------
# converge.yml covers every required behavior
# ---------------------------------------------------------------------------


class TestConvergeCoverage:
    def test_converge_installs_minimal_deps(self) -> None:
        out = _load("default/converge.yml")
        assert "ca-certificates" in out and "curl" in out, (
            "converge must install ca-certificates + curl for the health poll"
        )

    def test_converge_copies_the_built_binary(self) -> None:
        out = _load("default/converge.yml")
        assert "dist/gludd" in out, "converge must copy the built binary (dist/gludd)"
        assert "chmod" in out.lower() or "mode: \"0755\"" in out or "mode: '0755'" in out, (
            "converge must make the binary executable"
        )

    def test_converge_runs_version_subcommand(self) -> None:
        out = _load("default/converge.yml")
        assert re.search(r"gludd[^\\\n]* version\b", out) or " version" in out, (
            "converge must run 'gludd version' (no --version flag exists)"
        )

    def test_converge_runs_help_flag(self) -> None:
        out = _load("default/converge.yml")
        assert "--help" in out, "converge must run 'gludd --help'"

    def test_converge_runs_project_paths(self) -> None:
        out = _load("default/converge.yml")
        assert "project paths" in out, (
            "converge must run 'gludd project paths' to exercise bundled-path resolution"
        )

    def test_converge_starts_daemon_backgrounded(self) -> None:
        out = _load("default/converge.yml")
        assert "daemon" in out, "converge must start the gludd daemon"
        assert "nohup" in out or "&" in out or "async" in out, (
            "daemon must be started in the background (nohup/&/async)"
        )

    def test_converge_polls_health_endpoint(self) -> None:
        out = _load("default/converge.yml")
        assert "healthz" in out, (
            "converge must poll /healthz (the canonical daemon health endpoint)"
        )
        assert "30" in out, "health poll must allow up to ~30s"

    def test_converge_requires_canonical_healthy_status(self) -> None:
        tasks = _play_tasks("default/converge.yml", 1)
        health = next(
            task
            for task in tasks
            if "/healthz"
            in _string_key(task, "name", "default/converge.yml[1].tasks")
        )

        failed_when = _string_key(
            health, "failed_when", "default/converge.yml health task"
        )
        assert "!= 'healthy'" in failed_when, (
            "the daemon's stable /healthz contract uses status='healthy'; "
            "the smoke scenario must reject degraded responses without "
            "expecting the unrelated literal 'ok'"
        )

    def test_converge_submits_job_via_daemon_api(self) -> None:
        out = _load("default/converge.yml")
        assert "/api/todos" in out, (
            "converge must submit a trivial job via POST /api/todos (the job-"
            "submission API; there is no /api/playbook/run endpoint)"
        )

    def test_converge_authenticates_job_submission(self) -> None:
        daemon_play = _play("default/converge.yml", 1)
        daemon_vars = _mapping_key(
            daemon_play, "vars", "default/converge.yml[1]"
        )
        daemon_psk = daemon_vars.get("daemon_psk")
        assert daemon_psk, "the smoke daemon must exercise fail-closed PSK auth"

        tasks = _mapping_sequence_key(
            daemon_play, "tasks", "default/converge.yml[1]"
        )
        start = next(
            task
            for task in tasks
            if _string_key(task, "name", "default/converge.yml[1].tasks").startswith(
                "Start the"
            )
        )
        submit = next(
            task
            for task in tasks
            if _string_key(task, "name", "default/converge.yml[1].tasks").startswith(
                "Submit a"
            )
        )
        environment = _mapping_key(
            start, "environment", "default/converge.yml start task"
        )
        uri = _mapping_key(
            submit, "ansible.builtin.uri", "default/converge.yml submit task"
        )
        headers = _mapping_key(
            uri, "headers", "default/converge.yml submit task URI"
        )
        assert environment["GLUDD_AUTH_PSK"] == "{{ daemon_psk }}"
        assert headers["Authorization"] == (
            "Bearer {{ daemon_psk }}"
        )

    def test_converge_covers_invalid_flag_error_path(self) -> None:
        out = _load("default/converge.yml")
        assert "--invalid-flag" in out, (
            "converge must run 'gludd --invalid-flag' to verify a clean argparse error"
        )

    def test_converge_covers_occupied_port_error_path(self) -> None:
        out = _load("default/converge.yml")
        # Second daemon invocation against the already-bound port.
        assert out.count("daemon") >= 2, (
            "converge must start a second daemon on the occupied port"
        )

    def test_converge_persists_remote_port_clash_output(self) -> None:
        tasks = _play_tasks("default/converge.yml", 1)
        persist = next(
            task
            for task in tasks
            if _string_key(task, "name", "default/converge.yml[1].tasks")
            == "Persist port-clash result"
        )
        copy = _mapping_key(
            persist, "ansible.builtin.copy", "default/converge.yml persist task"
        )
        content = _string_key(
            copy, "content", "default/converge.yml persist task copy"
        )
        assert "lookup(" not in content
        assert "{{ port_clash.stdout }}" in content

    def test_converge_pins_process_identity_for_teardown(self) -> None:
        out = _load("default/converge.yml")

        assert "/proc/{{ daemon_pid_record.pid }}/stat" in out
        assert "process_start_ticks" in out
        assert "executable" in out
        assert "'pid': daemon_pid_record.pid | int" in out


# ---------------------------------------------------------------------------
# verify.yml asserts every required invariant
# ---------------------------------------------------------------------------


class TestVerifyAssertions:
    def test_verify_does_not_require_sudo_in_root_container(self) -> None:
        assert _play("default/verify.yml", 0).get("become") is False

    def test_verify_asserts_semver(self) -> None:
        out = _load("default/verify.yml")
        assert "regex" in out.lower() or re.search(r"\\d\+", out), (
            "verify must assert 'version' output is a SemVer (\\d+\\.\\d+\\.\\d+)"
        )

    def test_verify_asserts_subcommands_listed(self) -> None:
        out = _load("default/verify.yml")
        for sub in ("daemon", "project"):
            assert sub in out, f"verify must assert --help lists the '{sub}' subcommand"

    def test_verify_asserts_daemon_health_200(self) -> None:
        out = _load("default/verify.yml")
        assert "healthz" in out and "200" in out, (
            "verify must assert the daemon /healthz endpoint returns HTTP 200"
        )

    def test_verify_asserts_no_traceback(self) -> None:
        out = _load("default/verify.yml")
        assert "Traceback" in out, (
            "verify must assert no Python traceback appears in any output"
        )

    def test_verify_asserts_no_module_not_found(self) -> None:
        out = _load("default/verify.yml")
        assert "ModuleNotFoundError" in out, (
            "verify must assert no ModuleNotFoundError (binary bundling regression)"
        )

    def test_verify_asserts_no_missing_base_yaml(self) -> None:
        out = _load("default/verify.yml")
        assert "Missing base YAML definition file" in out, (
            "verify must assert the 'Missing base YAML definition file' error is absent"
        )

    def test_verify_asserts_external_collection_plane(self) -> None:
        out = _load("default/verify.yml")
        assert "Collection search path" in out or "project paths" in out, (
            "verify must exercise project-path discovery"
        )
        assert "'=== project paths (rc=0) ===' in cli_outputs" in out
        assert r"regex_findall('\\(missing\\)') | length == 1" in out
        assert r"BUNDLED.*collections.*\\(missing\\)" in out

    def test_teardown_uses_pinned_process_identity(self) -> None:
        out = _load("default/stop_daemon.yml")

        assert "process_start_ticks" in out
        assert "binary_daemon_manifest.executable == gludd_bin" in out
        assert "/proc/{{ _binary_daemon_pid_record.pid | string }}/stat" in out
        assert "__gludd_bundled_gunicorn__" not in out
        assert "'--bind'" not in out

    def test_teardown_rechecks_liveness_after_proc_identity_reads(self) -> None:
        """A PID exit between ``ps`` and ``/proc`` must not look foreign."""
        tasks = _task_map("default/stop_daemon.yml")
        names = list(tasks)

        initial_ps = "Inspect only the PID named by the packaged daemon"
        executable = "Inspect the pinned packaged daemon executable identity"
        final_ps = "Recheck the PID after packaged daemon identity inspection"
        establish = "Establish exact packaged daemon ownership"
        assert names.index(initial_ps) < names.index(executable)
        assert names.index(executable) < names.index(final_ps)
        assert names.index(final_ps) < names.index(establish)

        final_check = tasks[final_ps]
        assert final_check["failed_when"] == "_binary_daemon_final_ps.rc not in [0, 1]"

    def test_teardown_reports_each_identity_invariant_without_signalling_foreign_pid(
        self,
    ) -> None:
        """Live mismatches remain fail-closed and identify the changed field."""
        tasks = _task_map("default/stop_daemon.yml")

        establish = tasks["Establish exact packaged daemon ownership"]
        facts = _mapping_key(
            establish,
            "ansible.builtin.set_fact",
            "default/stop_daemon.yml establish task",
        )
        assert {
            "_binary_daemon_pid_live",
            "_binary_daemon_start_ticks_match",
            "_binary_daemon_executable_matches",
            "_binary_daemon_owned",
        } <= facts.keys()

        refusal = tasks["Refuse to signal a reused or foreign PID"]
        assertion = _mapping_key(
            refusal,
            "ansible.builtin.assert",
            "default/stop_daemon.yml refusal task",
        )
        conditions = _sequence_key(
            assertion, "that", "default/stop_daemon.yml refusal assertion"
        )
        condition = _require_string(
            conditions[0], "default/stop_daemon.yml refusal assertion condition"
        )
        assert "_binary_daemon_pid_live" in condition
        fail_msg = _string_key(
            assertion, "fail_msg", "default/stop_daemon.yml refusal assertion"
        )
        for field in (
            "pid_live",
            "expected_start_ticks",
            "observed_start_ticks",
            "expected_executable",
            "observed_executable",
        ):
            assert field in fail_msg

        stop_owned = tasks["Stop only the proven packaged daemon"]
        stop_conditions = _sequence_key(
            stop_owned, "when", "default/stop_daemon.yml stop task"
        )
        assert "_binary_daemon_owned | bool" in stop_conditions

    def test_teardown_waits_for_release_when_daemon_exits_during_inspection(
        self,
    ) -> None:
        """Success, failure, and cancellation all prove endpoint/PID release."""
        tasks = _task_map("default/stop_daemon.yml")
        release_guard = "not _binary_daemon_pid_live | bool or _binary_daemon_owned | bool"

        endpoint = tasks["Prove the packaged daemon endpoint is closed"]
        pid_record = tasks["Prove the daemon wrapper released its PID record"]
        endpoint_conditions = _sequence_key(
            endpoint, "when", "default/stop_daemon.yml endpoint task"
        )
        pid_record_conditions = _sequence_key(
            pid_record, "when", "default/stop_daemon.yml PID-record task"
        )
        assert release_guard in endpoint_conditions
        assert release_guard in pid_record_conditions
        endpoint_wait = _mapping_key(
            endpoint,
            "ansible.builtin.wait_for",
            "default/stop_daemon.yml endpoint task",
        )
        pid_record_wait = _mapping_key(
            pid_record,
            "ansible.builtin.wait_for",
            "default/stop_daemon.yml PID-record task",
        )
        assert endpoint_wait["state"] == "stopped"
        assert pid_record_wait["state"] == "absent"

    def test_verify_assertes_no_import_errors(self) -> None:
        out = _load("default/verify.yml")
        assert "ImportError" in out or "ModuleNotFoundError" in out, (
            "verify must assert no import errors"
        )

    def test_verify_asserts_job_processed(self) -> None:
        out = _load("default/verify.yml")
        assert "/api/todos" in out or "todo_id" in out, (
            "verify must assert the daemon accepted the submitted job"
        )

    def test_verify_asserts_invalid_flag_exits_nonzero(self) -> None:
        out = _load("default/verify.yml")
        assert "bad" in out.lower() or "invalid" in out.lower(), (
            "verify must assert the invalid-flag invocation exited non-zero"
        )

    def test_verify_uses_ansible_compatible_nonzero_regexes(self) -> None:
        out = _load("default/verify.yml")

        assert "regex_search('rc=[1-9][0-9]*')" in out
        assert "regex_search('EXIT_RC=[1-9][0-9]*')" in out
        assert "regex_search('rc=([0-9]+)'," not in out
        assert "regex_search('EXIT_RC=([0-9]+)'," not in out

    def test_verify_asserts_port_clash_handled_gracefully(self) -> None:
        out = _load("default/verify.yml")
        assert "port" in out.lower() or "clash" in out.lower(), (
            "verify must assert the occupied-port daemon failed gracefully"
        )


# ---------------------------------------------------------------------------
# prepare.yml ensures the binary is built before the container uses it
# ---------------------------------------------------------------------------


class TestPrepare:
    def test_make_target_builds_a_real_linux_binary_before_molecule(self) -> None:
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
