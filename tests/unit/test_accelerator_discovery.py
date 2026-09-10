from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from general_ludd.hardware.accelerator_discovery import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
    DiscoveryEvent,
    DiscoveryTrace,
    HardwareDiscovery,
    parse_slurm_nodes,
    probe_intel_xpu_gpus,
)
from general_ludd.hardware.survey import GpuInfo


class _Survey:
    def __init__(self, gpus: list[GpuInfo] | None = None) -> None:
        self._gpus = gpus or []

    def probe_gpus(self) -> list[GpuInfo]:
        return list(self._gpus)


class _Slurm:
    def __init__(self, nodes: list[dict[str, object]]) -> None:
        self._nodes = nodes

    def list_nodes(self) -> list[dict[str, object]]:
        return list(self._nodes)


def _missing_modules(_name: str) -> object:
    raise ModuleNotFoundError


def test_local_apple_gpu_is_normalized_without_a_hardware_model_key() -> None:
    discovery = HardwareDiscovery(
        survey=_Survey([GpuInfo("Apple M4 Max", 64.0, backend="metal")]),
        module_loader=_missing_modules,
    )

    inventory = discovery.discover_local()

    assert inventory.resources == (
        AcceleratorResource(
            kind=AcceleratorKind.GPU,
            location=AcceleratorLocation.LOCAL,
            backend="metal",
            model="Apple M4 Max",
            vendor="apple",
            resource_key="local:metal:0",
            total_count=1,
            available_count=1,
            memory_gb=64.0,
            source="system_profiler",
        ),
    )


def test_local_intel_xpu_uses_torch_runtime_properties() -> None:
    properties = SimpleNamespace(
        name="Intel Arc Pro",
        vendor="Intel Corporation",
        total_memory=16 * 1024**3,
    )
    xpu = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 2,
        get_device_properties=lambda _index: properties,
    )

    def loader(name: str) -> object:
        if name == "torch":
            return SimpleNamespace(xpu=xpu)
        raise ModuleNotFoundError

    inventory = HardwareDiscovery(survey=_Survey(), module_loader=loader).discover_local()

    assert [resource.resource_key for resource in inventory.resources] == [
        "local:xpu:0",
        "local:xpu:1",
    ]
    assert all(resource.vendor == "Intel Corporation" for resource in inventory.resources)
    assert all(resource.memory_gb == 16.0 for resource in inventory.resources)


def test_local_intel_xpu_absence_is_a_normal_empty_probe() -> None:
    torch = SimpleNamespace(xpu=SimpleNamespace(is_available=lambda: False))

    def loader(name: str) -> object:
        if name == "torch":
            return torch
        raise ModuleNotFoundError

    inventory = HardwareDiscovery(survey=_Survey(), module_loader=loader).discover_local()
    assert inventory.resources == ()


def test_local_discovery_reuses_supplied_survey_and_does_not_reprobe_xpu() -> None:
    survey = MagicMock()
    loaded_modules: list[str] = []

    def loader(name: str) -> object:
        loaded_modules.append(name)
        raise ModuleNotFoundError

    inventory = HardwareDiscovery(
        survey=survey,
        surveyed_gpus=(GpuInfo("Intel Arc", 16.0, backend="xpu", vendor="intel"),),
        module_loader=loader,
    ).discover_local()

    survey.probe_gpus.assert_not_called()
    assert loaded_modules == ["jax"]
    assert [resource.resource_key for resource in inventory.resources] == ["local:xpu:0"]


def test_local_tpu_uses_jax_device_metadata_and_memory_limit() -> None:
    tpu = SimpleNamespace(
        platform="tpu",
        device_kind="TPU v5e",
        id=3,
        process_index=1,
        memory_stats=lambda: {"bytes_limit": 16 * 1024**3},
    )
    cpu = SimpleNamespace(platform="cpu", device_kind="cpu", id=0, process_index=0)

    def loader(name: str) -> object:
        if name == "jax":
            return SimpleNamespace(devices=lambda: [cpu, tpu])
        raise ModuleNotFoundError

    inventory = HardwareDiscovery(survey=_Survey(), module_loader=loader).discover_local()

    assert len(inventory.resources) == 1
    resource = inventory.resources[0]
    assert resource.kind is AcceleratorKind.TPU
    assert resource.model == "TPU v5e"
    assert resource.backend == "jax-tpu"
    assert resource.memory_gb == 16.0
    assert resource.resource_key == "local:jax-tpu:1:3"


def test_local_tpu_without_reported_capacity_remains_unknown() -> None:
    tpu = SimpleNamespace(
        platform="tpu",
        device_kind="TPU v4",
        id=0,
        process_index=0,
        memory_stats=lambda: None,
    )

    def loader(name: str) -> object:
        if name == "jax":
            return SimpleNamespace(devices=lambda: [tpu])
        raise ModuleNotFoundError

    resource = HardwareDiscovery(survey=_Survey(), module_loader=loader).discover_local().resources[0]
    assert resource.memory_gb is None


def test_local_runtime_probe_failure_is_visible_but_does_not_hide_other_hardware() -> None:
    events = []

    def loader(_name: str) -> object:
        raise RuntimeError("runtime initialization failed")

    inventory = HardwareDiscovery(
        survey=_Survey([GpuInfo("Apple M3", 24.0, backend="metal")]),
        module_loader=loader,
        trace_sink=events.append,
    ).discover_local()

    assert len(inventory.resources) == 1
    assert [event.event for event in events].count(DiscoveryEvent.SOURCE_UNAVAILABLE) == 2
    assert events[0].event is DiscoveryEvent.DISCOVERY_STARTED
    assert events[-1].event is DiscoveryEvent.DISCOVERY_COMPLETED


def test_trace_sink_failure_fails_closed() -> None:
    def broken_sink(_event: object) -> None:
        raise RuntimeError("trace unavailable")

    discovery = HardwareDiscovery(
        survey=_Survey(),
        module_loader=_missing_modules,
        trace_sink=broken_sink,
    )
    with pytest.raises(RuntimeError, match="trace unavailable"):
        discovery.discover_local()


def test_slurm_gres_inventory_reports_allocatable_gpu_and_tpu_counts() -> None:
    resources = parse_slurm_nodes(
        [
            {
                "name": "accelerator-01",
                "state": ["IDLE"],
                "partitions": ["accelerated"],
                "gres": "gpu:a100:4(S:0-3),tpu:v4:8",
                "gres_used": "gpu:a100:1,tpu:v4:2",
            }
        ]
    )

    assert [(item.kind, item.model, item.total_count, item.available_count) for item in resources] == [
        (AcceleratorKind.GPU, "a100", 4, 3),
        (AcceleratorKind.TPU, "v4", 8, 6),
    ]
    assert all(item.location is AcceleratorLocation.SLURM for item in resources)
    assert all(item.partitions == ("accelerated",) for item in resources)


@pytest.mark.parametrize("state", ["DOWN", "DRAIN", ["IDLE", "MAINT"], "NO_RESPOND"])
def test_slurm_unavailable_node_never_advertises_free_accelerators(state: object) -> None:
    resource = parse_slurm_nodes(
        [{"name": "node-01", "state": state, "gres": "gpu:future-device:2"}]
    )[0]
    assert resource.total_count == 2
    assert resource.available_count == 0


def test_slurm_generic_gpu_and_mig_memory_are_parsed_without_a_sku_table() -> None:
    resources = parse_slurm_nodes(
        [
            {
                "name": "node-01",
                "state": "IDLE",
                "gres": ["gpu:2", "gpu:1g.5gb:3(S:0-2)"],
            }
        ]
    )
    by_model = {resource.model: resource for resource in resources}
    assert by_model["unspecified"].memory_gb is None
    assert by_model["1g.5gb"].memory_gb == 5.0


def test_slurm_malformed_and_non_accelerator_gres_are_ignored() -> None:
    resources = parse_slurm_nodes(
        [
            {
                "name": "node-01",
                "state": "IDLE",
                "gres": "license:matlab:10,broken,gpu:future:two,tpu:v6e:4",
            }
        ]
    )
    assert len(resources) == 1
    assert resources[0].kind is AcceleratorKind.TPU


def test_slurm_used_count_is_bounded_at_zero_available() -> None:
    resource = parse_slurm_nodes(
        [
            {
                "name": "node-01",
                "state": "ALLOCATED",
                "gres": "gpu:intel:2",
                "gres_used": "gpu:intel:5",
            }
        ]
    )[0]
    assert resource.available_count == 0


def test_combined_discovery_merges_local_and_slurm_with_content_free_traces() -> None:
    events = []
    discovery = HardwareDiscovery(
        survey=_Survey([GpuInfo("Apple M2", 16.0, backend="metal")]),
        slurm=_Slurm(
            [{"name": "node-01", "state": "IDLE", "gres": "gpu:cluster-type:2"}]
        ),
        module_loader=_missing_modules,
        trace_sink=events.append,
    )

    inventory = discovery.discover()

    assert inventory.total_count == 3
    assert inventory.available_count == 3
    assert {resource.location for resource in inventory.resources} == {
        AcceleratorLocation.LOCAL,
        AcceleratorLocation.SLURM,
    }
    assert all(not hasattr(event, "model") for event in events)


def test_inventory_serializes_exact_generic_schema() -> None:
    inventory = HardwareDiscovery(
        survey=_Survey([GpuInfo("Apple M2", 16.0, backend="metal")]),
        module_loader=_missing_modules,
    ).discover_local()

    assert inventory.to_dict() == {
        "schema_version": 1,
        "total_count": 1,
        "available_count": 1,
        "resources": [
            {
                "kind": "gpu",
                "location": "local",
                "backend": "metal",
                "model": "Apple M2",
                "vendor": "apple",
                "resource_key": "local:metal:0",
                "total_count": 1,
                "available_count": 1,
                "memory_gb": 16.0,
                "source": "system_profiler",
                "node": None,
                "partitions": [],
            }
        ],
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"resource_key": ""}, "resource_key"),
        ({"total_count": 0}, "total_count"),
        ({"available_count": 2}, "available_count"),
        ({"memory_gb": 0.0}, "memory_gb"),
        ({"partitions": ["gpu"]}, "partitions"),
    ],
)
def test_accelerator_resource_rejects_ambiguous_or_unbounded_facts(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "kind": AcceleratorKind.GPU,
        "location": AcceleratorLocation.LOCAL,
        "backend": "metal",
        "model": "Apple",
        "vendor": "apple",
        "resource_key": "local:metal:0",
        "total_count": 1,
        "available_count": 1,
        "memory_gb": 8.0,
        "source": "system_profiler",
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        AcceleratorResource(**values)  # type: ignore[arg-type]


def test_slurm_discovery_failure_is_traced_and_propagated() -> None:
    slurm = MagicMock()
    slurm.list_nodes.side_effect = RuntimeError("controller unavailable")
    events = []
    discovery = HardwareDiscovery(
        survey=_Survey(),
        slurm=slurm,
        module_loader=_missing_modules,
        trace_sink=events.append,
    )

    with pytest.raises(RuntimeError, match="controller unavailable"):
        discovery.discover_slurm()
    assert events[-1].event is DiscoveryEvent.SOURCE_FAILED


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("wrong", "local", 0), "event"),
        ((DiscoveryEvent.DISCOVERY_STARTED, "", 0), "source"),
        ((DiscoveryEvent.DISCOVERY_STARTED, "local", True), "integer"),
        ((DiscoveryEvent.DISCOVERY_STARTED, "local", 100_001), "bounded"),
    ],
)
def test_discovery_trace_rejects_invalid_boundaries(
    arguments: tuple[object, object, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DiscoveryTrace(*arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "gpu"}, "kind"),
        ({"location": "local"}, "location"),
        ({"backend": "bad\nbackend"}, "control"),
        ({"total_count": True}, "integer"),
        ({"available_count": True}, "integer"),
        ({"memory_gb": "16"}, "number"),
        ({"node": "bad\nnode"}, "control"),
        ({"partitions": ("bad\npartition",)}, "control"),
    ],
)
def test_resource_additional_type_and_control_boundaries(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "kind": AcceleratorKind.GPU,
        "location": AcceleratorLocation.LOCAL,
        "backend": "metal",
        "model": "Apple",
        "vendor": "apple",
        "resource_key": "local:metal:0",
        "total_count": 1,
        "available_count": 1,
        "memory_gb": 8.0,
        "source": "system_profiler",
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        AcceleratorResource(**values)  # type: ignore[arg-type]


def test_inventory_rejects_mutable_invalid_and_duplicate_resources() -> None:
    resource = AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.LOCAL,
        backend="metal",
        model="Apple",
        vendor="apple",
        resource_key="local:metal:0",
        total_count=1,
        available_count=1,
        memory_gb=8.0,
        source="system_profiler",
    )
    from general_ludd.hardware.accelerator_discovery import AcceleratorInventory

    with pytest.raises(ValueError, match="tuple"):
        AcceleratorInventory([resource])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="AcceleratorResource"):
        AcceleratorInventory((object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique"):
        AcceleratorInventory((resource, resource))


def test_hardware_discovery_validates_callbacks() -> None:
    with pytest.raises(ValueError, match="module_loader"):
        HardwareDiscovery(module_loader=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="trace_sink"):
        HardwareDiscovery(trace_sink=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="surveyed_gpus"):
        HardwareDiscovery(surveyed_gpus=[object()])  # type: ignore[list-item]


def test_intel_probe_rejects_invalid_count_and_skips_unknown_memory() -> None:
    invalid = SimpleNamespace(
        xpu=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: True,
        )
    )
    with pytest.raises(ValueError, match="device count"):
        probe_intel_xpu_gpus(lambda _name: invalid)

    properties = SimpleNamespace(name=None, vendor=None, total_memory=0)
    no_memory = SimpleNamespace(
        xpu=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_properties=lambda _index: properties,
        )
    )
    assert probe_intel_xpu_gpus(lambda _name: no_memory) == ()


def test_runtime_property_failure_uses_safe_fallbacks() -> None:
    class BrokenProperties:
        @property
        def total_memory(self) -> int:
            raise RuntimeError("driver failure")

    runtime = SimpleNamespace(
        xpu=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_properties=lambda _index: BrokenProperties(),
        )
    )
    assert probe_intel_xpu_gpus(lambda _name: runtime) == ()


def test_absent_slurm_source_returns_traced_empty_inventory() -> None:
    events = []
    inventory = HardwareDiscovery(
        survey=_Survey(),
        module_loader=_missing_modules,
        trace_sink=events.append,
    ).discover_slurm()
    assert inventory.resources == ()
    assert [event.event for event in events] == [
        DiscoveryEvent.DISCOVERY_STARTED,
        DiscoveryEvent.SOURCE_UNAVAILABLE,
        DiscoveryEvent.DISCOVERY_COMPLETED,
    ]


def test_slurm_parser_handles_alternate_fields_and_fail_closed_shapes() -> None:
    resources = parse_slurm_nodes(
        [
            "not-a-node",  # type: ignore[list-item]
            {
                "hostname": "node-alt",
                "state": None,
                "partition": "b,a",
                "gres": {"unexpected": "shape"},
            },
            {
                "node_name": "node-two",
                "state": "IDLE",
                "partition": "b,a",
                "gres": "gpu:0,gpu:future_80gb:1",
            },
        ]
    )
    assert len(resources) == 1
    assert resources[0].node == "node-two"
    assert resources[0].partitions == ("a", "b")
    assert resources[0].memory_gb == 80.0
    with pytest.raises(ValueError, match="sequence"):
        parse_slurm_nodes("wrong")  # type: ignore[arg-type]
