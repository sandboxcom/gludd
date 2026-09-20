"""Contracts for the provider-neutral model-worker attestation role."""

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
    / "attest_model_worker"
)
SCENARIO = ROOT / "molecule" / "playbooks" / "role_attest_model_worker"


def _read(relative: str) -> str:
    return (ROLE / relative).read_text(encoding="utf-8")


def test_role_has_complete_collection_structure() -> None:
    for relative in ("defaults/main.yml", "meta/main.yml", "tasks/main.yml", "README.md"):
        assert (ROLE / relative).is_file(), relative


def test_role_derives_attestation_request_from_universal_launch_plan() -> None:
    tasks = cast(list[dict[str, Any]], yaml.safe_load(_read("tasks/main.yml")))
    text = _read("tasks/main.yml")
    names = [str(task["name"]) for task in tasks]

    assert names == [
        "Validate universal model worker attestation inputs",
        "Observe model worker driver topology and runtime",
        "Publish content-free model worker attestation",
        "Require complete model worker attestation",
    ]
    assert "general_ludd.agent.gludd_model_worker_attest" in text
    for field in ("runner_id", "source_revision", "devices_per_replica"):
        assert f"gludd_model_worker_plan.{field}" in text
    assert names.index("Publish content-free model worker attestation") < names.index(
        "Require complete model worker attestation"
    )


def test_role_is_provider_and_workload_neutral_and_read_only() -> None:
    runtime = "\n".join(_read(path) for path in ("defaults/main.yml", "tasks/main.yml")).casefold()

    for forbidden in ("azure", "aws", "ec2", "self_improve", "self-improve", "standard_nc"):
        assert forbidden not in runtime
    for forbidden in ("ansible.builtin.shell", "ansible.builtin.command", "ansible.builtin.package"):
        assert forbidden not in runtime


def test_role_requires_all_content_free_evidence_before_launch() -> None:
    text = _read("tasks/main.yml")
    for field in (
        "facts_attested",
        "driver_ready",
        "runtime_ready",
        "topology_ready",
        "observed_inventory_digest",
        "driver_version_digest",
        "runtime_version_digest",
        "reason_codes",
    ):
        assert f"gludd_model_worker_attestation.{field}" in text


def test_documentation_records_shared_consumers_zdd_and_vendor_sources() -> None:
    text = _read("README.md")

    for phrase in (
        "provider-neutral",
        "self-improvement",
        "chemistry",
        "coding",
        "ZDD",
        "nvidia-ml-py",
        "AMD SMI",
        "OpenBao",
    ):
        assert phrase in text


def test_molecule_scenario_proves_missing_backend_fails_closed() -> None:
    for relative in (
        "molecule.yml",
        "default/prepare.yml",
        "default/converge.yml",
        "default/verify.yml",
        "default/cleanup.yml",
    ):
        assert (SCENARIO / relative).is_file(), relative
    converge = (SCENARIO / "default" / "converge.yml").read_text(encoding="utf-8")
    molecule = (SCENARIO / "molecule.yml").read_text(encoding="utf-8")

    assert "general_ludd.agent.attest_model_worker" in converge
    assert "molecule-missing" in converge
    assert "Require complete model worker attestation" in converge
    assert "probe_unavailable" in converge
    assert "idempotence" in molecule
