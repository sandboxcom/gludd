"""Contracts for the provider-neutral attested model-worker role."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "model_worker"
)
SCENARIO = ROOT / "molecule" / "playbooks" / "role_model_worker"


def _read(relative: str) -> str:
    return (ROLE / relative).read_text(encoding="utf-8")


def _yaml(relative: str) -> Any:
    return yaml.safe_load(_read(relative))


def test_role_has_complete_collection_structure() -> None:
    for relative in (
        "defaults/main.yml",
        "meta/main.yml",
        "tasks/main.yml",
        "tasks/retire.yml",
        "templates/gludd-model-worker.env.j2",
        "templates/gludd-model-worker.service.j2",
        "README.md",
    ):
        assert (ROLE / relative).is_file(), relative


def test_main_role_consumes_the_universal_runner_launch_plan() -> None:
    tasks = cast(list[dict[str, Any]], _yaml("tasks/main.yml"))
    text = _read("tasks/main.yml")

    assert tasks[0]["name"] == "Validate universal model worker inputs"
    for field in (
        "schema_version",
        "runner_id",
        "adapter_id",
        "source_revision",
        "command",
        "environment",
        "request_options",
        "replica_count",
        "devices_per_replica",
    ):
        assert f"gludd_model_worker_plan.{field}" in text


def test_role_requires_live_runtime_and_topology_attestation() -> None:
    text = _read("tasks/main.yml")

    for field in (
        "facts_attested",
        "driver_ready",
        "runtime_ready",
        "device_count",
        "source_revision",
        "topology_digest",
    ):
        assert f"gludd_model_worker_attestation.{field}" in text
    assert "gludd_model_worker_plan.devices_per_replica" in text
    assert "gludd_model_worker_expected_topology_digest" in text


def test_role_is_provider_task_and_workload_neutral() -> None:
    runtime_text = "\n".join(
        _read(relative)
        for relative in (
            "defaults/main.yml",
            "tasks/main.yml",
            "tasks/retire.yml",
            "templates/gludd-model-worker.service.j2",
        )
    ).casefold()
    for forbidden in (
        "azure",
        "aws",
        "ec2",
        "container app",
        "self_improve",
        "self-improve",
        "standard_nc",
        "standard_nd",
        "standard_nv",
    ):
        assert forbidden not in runtime_text


def test_role_executes_only_the_tokenized_attested_command() -> None:
    text = _read("tasks/main.yml")
    service = _read("templates/gludd-model-worker.service.j2")

    assert "ansible.builtin.shell" not in text
    assert "ansible.builtin.pip" not in text
    assert "ansible.builtin.package" not in text
    assert "curl " not in text
    assert "gludd_model_worker_plan.command | first" in text
    assert "gludd_model_worker_plan.command" in service
    assert "ExecStart=" in service
    assert "| quote" in service


def test_environment_rejects_secret_names_and_is_not_logged() -> None:
    tasks = _read("tasks/main.yml")
    environment = _read("templates/gludd-model-worker.env.j2")

    assert "gludd_model_worker_forbidden_environment_names" in tasks
    assert "no_log: true" in tasks
    assert "gludd_model_worker_plan.environment | dict2items" in environment
    assert "to_json" in environment


def test_candidate_becomes_healthy_before_attestation_is_published() -> None:
    tasks = cast(list[dict[str, Any]], _yaml("tasks/main.yml"))
    names = [str(task["name"]) for task in tasks]

    start_index = names.index("Start candidate model worker service")
    health_index = names.index("Wait for candidate model worker health")
    record_index = names.index("Record content-free candidate attestation")
    publish_index = names.index("Publish model worker candidate fact")
    assert start_index < health_index < record_index < publish_index


def test_main_role_never_retires_the_previous_service() -> None:
    main = _read("tasks/main.yml")
    retire = _read("tasks/retire.yml")

    assert "state: stopped" not in main
    assert "gludd_model_worker_retire_service" not in main
    assert "state: stopped" in retire
    assert "state: absent" in retire
    assert "daemon_reload: true" in retire
    assert "attestations" in retire
    assert "gludd_model_worker_retire_service" in retire


def test_service_is_restartable_and_systemd_hardened() -> None:
    service = _read("templates/gludd-model-worker.service.j2")

    for contract in (
        "Restart=on-failure",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ReadWritePaths=",
        "EnvironmentFile=",
    ):
        assert contract in service


def test_attestation_record_is_content_free() -> None:
    tasks = _read("tasks/main.yml")
    record = tasks.split("Record content-free candidate attestation", maxsplit=1)[1]
    record = record.split("Publish model worker candidate fact", maxsplit=1)[0]

    assert "source_revision" in record
    assert "topology_digest" in record
    assert "devices_per_replica" in record
    assert "model_id" not in record
    assert "environment" not in record
    assert "request_options" not in record


def test_defaults_are_bounded_and_contain_no_credentials() -> None:
    defaults = cast(dict[str, Any], _yaml("defaults/main.yml"))

    assert defaults["gludd_model_worker_health_retries"] > 0
    assert defaults["gludd_model_worker_health_delay"] > 0
    assert defaults["gludd_model_worker_service_user"] == "gludd"
    assert defaults["gludd_model_worker_state_dir"].startswith("/var/lib/gludd/")
    serialized = yaml.safe_dump(defaults).casefold()
    for forbidden in ("password", "api_key", "client_secret", "access_token"):
        assert forbidden not in serialized


def test_metadata_describes_all_supported_runner_families() -> None:
    meta = cast(dict[str, Any], _yaml("meta/main.yml"))["galaxy_info"]
    description = str(meta["description"]).casefold()

    assert meta["role_name"] == "model_worker"
    assert "vllm" in description
    assert "ollama" in description
    assert "llama.cpp" in description


def test_documentation_records_reuse_zdd_and_operator_reports() -> None:
    text = _read("README.md")

    assert "provider-neutral" in text
    assert "self-improvement" in text
    assert "Azure" in text
    assert "local" in text
    assert "Slurm" in text
    assert "RunnerLaunchPlan" in text
    assert "ZDD" in text
    assert "github.com/vllm-project/vllm/issues/33041" in text
    assert "github.com/ollama/ollama/issues/7047" in text


def test_molecule_scenario_exercises_guardrails_and_live_zdd_lifecycle() -> None:
    for relative in (
        "molecule.yml",
        "default/prepare.yml",
        "default/converge.yml",
        "default/verify.yml",
        "default/cleanup.yml",
    ):
        assert (SCENARIO / relative).is_file(), relative

    molecule = (SCENARIO / "molecule.yml").read_text(encoding="utf-8")
    converge = (SCENARIO / "default" / "converge.yml").read_text(encoding="utf-8")
    verify = (SCENARIO / "default" / "verify.yml").read_text(encoding="utf-8")

    assert "idempotence" in molecule
    assert "general_ludd.agent.model_worker" in converge
    assert "API_TOKEN" in converge
    assert "topology_guard_triggered" in converge
    assert "MOLECULE_LIVE_MODEL_WORKER" in converge
    assert "devices_per_replica: 2" in converge
    assert "gludd_model_worker_candidate" in converge
    assert "tasks_from: retire.yml" in verify
    assert "candidate artifacts were not fully retired" in verify
