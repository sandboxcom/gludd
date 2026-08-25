"""Hosted test-shard environment contracts for exact-SHA evidence."""

from pathlib import Path
from typing import cast

import yaml

WORKFLOW = Path(".github/workflows/build.yml")


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
