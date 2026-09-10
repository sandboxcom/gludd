"""Tests for validated provider-neutral accelerator inventory types."""

import pytest

from general_ludd.hardware.accelerator_types import (
    AcceleratorInventory,
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
    DiscoveryEvent,
    DiscoveryTrace,
    safe_runtime_text,
)


def test_inventory_types_preserve_observed_resource_facts() -> None:
    """Inventory serialization must retain generic observed capacity exactly."""
    resource = AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend="metal",
        model="Apple M2",
        vendor="apple",
        resource_key="local:metal:0",
        total_count=1,
        available_count=1,
        memory_gb=5.36,
        source="system_profiler",
    )

    payload = AcceleratorInventory((resource,)).to_dict()

    assert payload["total_count"] == 1
    resources = payload["resources"]
    assert isinstance(resources, list)
    assert isinstance(resources[0], dict)
    assert resources[0]["model"] == "Apple M2"
    assert DiscoveryTrace(DiscoveryEvent.SOURCE_COMPLETED, "metal", 1).discovered_count == 1
    assert safe_runtime_text("  Intel Arc  ", "unknown") == "Intel Arc"


def test_inventory_types_reject_duplicate_resource_identity() -> None:
    """Routing must not double-count one observed resource key."""
    resource = AcceleratorResource(
        kind=AcceleratorKind.TPU,
        location=AcceleratorLocation.SLURM,
        backend="slurm-gres",
        model="unspecified",
        vendor="unspecified",
        resource_key="slurm:n1:tpu:unspecified",
        total_count=1,
        available_count=0,
        memory_gb=None,
        source="slurm-gres",
    )
    with pytest.raises(ValueError, match="unique"):
        AcceleratorInventory((resource, resource))
