"""Hosted test-shard environment contracts for exact-SHA evidence."""

from pathlib import Path
from typing import cast

import yaml

WORKFLOW = Path(".github/workflows/build.yml")
MOLECULE_WORKFLOW = Path(".github/workflows/molecule.yml")


def _mapping(value: object) -> dict[str, object]:
    """Narrow a parsed YAML object to a string-keyed mapping."""
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _test_shard_steps() -> list[dict[str, object]]:
    """Return typed steps from the hosted test-shard job."""
    loaded: object = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    workflow = _mapping(loaded)
    jobs = _mapping(workflow["jobs"])
    test_shard = _mapping(jobs["test-shard"])
    steps = test_shard["steps"]
    assert isinstance(steps, list)
    return [_mapping(step) for step in steps]


def _jobs(path: Path) -> dict[str, object]:
    """Return the jobs mapping for one hosted workflow."""
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _mapping(_mapping(loaded)["jobs"])


def test_test_shard_checkout_has_full_history_for_session_evidence() -> None:
    """Require hosted Git evidence to include the recorded session head."""
    checkout = next(
        step
        for step in _test_shard_steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    checkout_options = _mapping(checkout.get("with", {}))
    assert checkout_options.get("fetch-depth") == 0


def test_test_shard_resource_root_is_a_namespace_container() -> None:
    """Leave project-namespace construction to the resource arbiter."""
    test_step = next(
        step
        for step in _test_shard_steps()
        if str(step.get("name", "")).startswith("Test (shard")
    )

    environment = _mapping(test_step["env"])
    assert environment["GLUDD_RESOURCE_ROOT"] == "${{ runner.temp }}/gludd-resources"


def test_linux_jobs_pin_ubuntu_24_04_instead_of_migrating_latest() -> None:
    """Keep candidate evidence on one explicit hosted image generation."""
    for path in (WORKFLOW, MOLECULE_WORKFLOW):
        jobs = _jobs(path)
        linux_labels = [
            str(_mapping(job).get("runs-on", ""))
            for job in jobs.values()
            if str(_mapping(job).get("runs-on", "")).startswith("ubuntu-")
        ]
        assert linux_labels, f"{path} has no Linux hosted jobs"
        assert "ubuntu-latest" not in linux_labels
        assert all(
            label in {"ubuntu-24.04", "ubuntu-24.04-arm"} for label in linux_labels
        ), f"{path} has an unpinned Linux hosted label: {linux_labels}"


def test_startup_matrices_bound_hosted_runner_acquisition_burst() -> None:
    """Request at most four Linux runners while both push workflows start."""
    build_jobs = _jobs(WORKFLOW)
    gate = _mapping(build_jobs["gate"])
    gate_strategy = _mapping(gate["strategy"])
    assert gate_strategy["max-parallel"] == 1

    molecule_jobs = _jobs(MOLECULE_WORKFLOW)
    molecule = _mapping(molecule_jobs["molecule"])
    molecule_strategy = _mapping(molecule["strategy"])
    assert molecule_strategy["max-parallel"] == 3


def test_build_fanout_matrices_have_explicit_capacity_limits() -> None:
    """Bound every Linux matrix instead of launching fourteen legs together."""
    jobs = _jobs(WORKFLOW)
    expected = {
        "freellmapi-upstream-build": 1,
        "test-shard": 3,
        "molecule": 2,
    }
    for job_name, limit in expected.items():
        strategy = _mapping(_mapping(jobs[job_name])["strategy"])
        assert strategy["fail-fast"] is False
        assert strategy["max-parallel"] == limit
