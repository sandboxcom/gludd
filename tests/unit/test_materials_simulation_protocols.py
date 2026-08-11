"""Dedicated tests for simulation/protocols.py — ResourceBounds, DeterminismSpec,
CheckpointRestartSpec, ValidationCase, and the SolverAdapter Protocol.

These cover field-level validation, edge cases, and defaults that the existing
test_materials_simulation.py touches only indirectly via adapter fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from general_ludd.materials.simulation.protocols import (
    CheckpointRestartSpec,
    DeterminismSpec,
    ResourceBounds,
    SolverAdapter,
    ValidationCase,
)


class TestResourceBounds:
    def test_construct_with_valid_fields(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=8192, wall_time_s=3600, disk_gb=20.0)
        assert rb.cpu_cores == 4
        assert rb.memory_mb == 8192
        assert rb.wall_time_s == 3600
        assert rb.disk_gb == 20.0

    def test_cpu_cores_negative_rejected(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=-1, memory_mb=0, wall_time_s=0, disk_gb=0.0)

    def test_memory_mb_negative_rejected(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=0, memory_mb=-1, wall_time_s=0, disk_gb=0.0)

    def test_wall_time_s_negative_rejected(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=0, memory_mb=0, wall_time_s=-1, disk_gb=0.0)

    def test_disk_gb_negative_rejected(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=0, memory_mb=0, wall_time_s=0, disk_gb=-0.1)

    def test_zero_bounds_allowed(self):
        rb = ResourceBounds(cpu_cores=0, memory_mb=0, wall_time_s=0, disk_gb=0.0)
        assert rb.cpu_cores == 0
        assert rb.memory_mb == 0

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.0, extra="bad")

    def test_allows_all_within_bounds(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(2, 8192, 3600, 50.0) is True

    def test_allows_exceeds_cpu(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(8, 8192, 3600, 50.0) is False

    def test_allows_exceeds_memory(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(2, 32768, 3600, 50.0) is False

    def test_allows_exceeds_wall_time(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(2, 8192, 10800, 50.0) is False

    def test_allows_exceeds_disk(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(2, 8192, 3600, 200.0) is False

    def test_allows_exactly_at_bounds(self):
        rb = ResourceBounds(cpu_cores=4, memory_mb=16384, wall_time_s=7200, disk_gb=100.0)
        assert rb.allows(4, 16384, 7200, 100.0) is True

    def test_allows_zero_bounds_accepts_zero_request(self):
        rb = ResourceBounds(cpu_cores=0, memory_mb=0, wall_time_s=0, disk_gb=0.0)
        assert rb.allows(0, 0, 0, 0.0) is True

    def test_allows_zero_bounds_rejects_positive_request(self):
        rb = ResourceBounds(cpu_cores=0, memory_mb=0, wall_time_s=0, disk_gb=0.0)
        assert rb.allows(1, 0, 0, 0.0) is False

    def test_allows_float_disk_precision(self):
        rb = ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.5)
        assert rb.allows(1, 1, 1, 1.5) is True
        assert rb.allows(1, 1, 1, 1.5000001) is False

    def test_allows_rejects_multiple_violations(self):
        rb = ResourceBounds(cpu_cores=2, memory_mb=2, wall_time_s=2, disk_gb=2.0)
        assert rb.allows(4, 4, 4, 4.0) is False


class TestDeterminismSpec:
    def test_reproducible_true_seed_controlled_true(self):
        ds = DeterminismSpec(reproducible=True, seed_controlled=True, version_pinned=True)
        assert ds.reproducible is True
        assert ds.seed_controlled is True
        assert ds.version_pinned is True

    def test_reproducible_false_defaults(self):
        ds = DeterminismSpec(reproducible=False)
        assert ds.reproducible is False
        assert ds.seed_controlled is False
        assert ds.version_pinned is False

    def test_seed_controlled_default_false(self):
        ds = DeterminismSpec(reproducible=True, version_pinned=True)
        assert ds.seed_controlled is False

    def test_version_pinned_default_false(self):
        ds = DeterminismSpec(reproducible=True, seed_controlled=True)
        assert ds.version_pinned is False

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            DeterminismSpec(reproducible=True, extra="nope")

    def test_non_reproducible_can_still_control_seed(self):
        ds = DeterminismSpec(reproducible=False, seed_controlled=True)
        assert ds.reproducible is False
        assert ds.seed_controlled is True


class TestCheckpointRestartSpec:
    def test_supported_with_format_and_max(self):
        cr = CheckpointRestartSpec(supported=True, format="hdf5", max_checkpoints=4)
        assert cr.supported is True
        assert cr.format == "hdf5"
        assert cr.max_checkpoints == 4

    def test_supported_without_format(self):
        cr = CheckpointRestartSpec(supported=True, max_checkpoints=2)
        assert cr.supported is True
        assert cr.format is None

    def test_not_supported(self):
        cr = CheckpointRestartSpec(supported=False)
        assert cr.supported is False
        assert cr.format is None
        assert cr.max_checkpoints == 0

    def test_max_checkpoints_default_zero(self):
        cr = CheckpointRestartSpec(supported=True, format="restart")
        assert cr.max_checkpoints == 0

    def test_negative_max_checkpoints_rejected(self):
        with pytest.raises(ValidationError):
            CheckpointRestartSpec(supported=True, max_checkpoints=-1)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            CheckpointRestartSpec(supported=True, extra="bad")

    def test_format_without_support_still_valid(self):
        cr = CheckpointRestartSpec(supported=False, format="hdf5")
        assert cr.supported is False
        assert cr.format == "hdf5"


class TestValidationCase:
    def test_minimal_name_only(self):
        vc = ValidationCase(name="benchmark1")
        assert vc.name == "benchmark1"
        assert vc.benchmark_uri is None
        assert vc.tolerance == {}
        assert vc.status == "unverified"

    def test_full_fields(self):
        vc = ValidationCase(
            name="NAFEMS LE1",
            benchmark_uri="https://example/nafems-le1",
            tolerance={"rel": 0.02},
            status="passing",
        )
        assert vc.name == "NAFEMS LE1"
        assert vc.benchmark_uri == "https://example/nafems-le1"
        assert vc.tolerance == {"rel": 0.02}
        assert vc.status == "passing"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ValidationCase(name="")

    def test_name_min_length_one_satisfied(self):
        vc = ValidationCase(name="X")
        assert vc.name == "X"

    def test_default_status_is_unverified(self):
        vc = ValidationCase(name="test1")
        assert vc.status == "unverified"

    def test_default_tolerance_is_empty_dict(self):
        vc = ValidationCase(name="test1")
        assert vc.tolerance == {}

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ValidationCase(name="test1", extra="nope")

    def test_tolerance_accepts_multiple_keys(self):
        vc = ValidationCase(name="multi", tolerance={"abs": 0.01, "rel": 0.05})
        assert vc.tolerance["abs"] == 0.01
        assert vc.tolerance["rel"] == 0.05

    def test_benchmark_uri_none_by_default(self):
        vc = ValidationCase(name="test1", tolerance={"rel": 0.01})
        assert vc.benchmark_uri is None


class TestSolverAdapterProtocol:
    def test_protocol_is_runtime_checkable(self):
        @dataclass
        class Good:
            capability_id: str = "v1"
            solver_name: str = "S"
            version: str = "1.0"
            license: str = "MIT"
            supported_physics: list[str] = field(default_factory=lambda: ["static"])
            unit_conventions: dict[str, str] = field(default_factory=dict)
            determinism: DeterminismSpec = field(default_factory=lambda: DeterminismSpec(reproducible=True))
            resource_bounds: ResourceBounds = field(
                default_factory=lambda: ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.0)
            )
            checkpoint_restart: CheckpointRestartSpec = field(
                default_factory=lambda: CheckpointRestartSpec(supported=False)
            )
            input_schema: dict[str, object] = field(default_factory=dict)
            output_schema: dict[str, object] = field(default_factory=dict)
            validation_cases: list[ValidationCase] = field(default_factory=lambda: [ValidationCase(name="vc1")])
            known_limitations: list[str] = field(default_factory=list)

        assert isinstance(Good(), SolverAdapter)

    def test_missing_capability_id_fails(self):
        @dataclass
        class Bad:
            solver_name: str = "S"
            version: str = "1"

        assert not isinstance(Bad(), SolverAdapter)

    def test_missing_validation_cases_fails(self):
        @dataclass
        class Bad:
            capability_id: str = "v1"
            solver_name: str = "S"
            version: str = "1"
            license: str = "MIT"
            supported_physics: list[str] = field(default_factory=list)
            unit_conventions: dict[str, str] = field(default_factory=dict)
            determinism: DeterminismSpec = field(default_factory=lambda: DeterminismSpec(reproducible=True))
            resource_bounds: ResourceBounds = field(
                default_factory=lambda: ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.0)
            )
            checkpoint_restart: CheckpointRestartSpec = field(
                default_factory=lambda: CheckpointRestartSpec(supported=False)
            )
            input_schema: dict[str, object] = field(default_factory=dict)
            output_schema: dict[str, object] = field(default_factory=dict)
            known_limitations: list[str] = field(default_factory=list)

        assert not isinstance(Bad(), SolverAdapter)

    def test_empty_validation_cases_list_is_ok(self):
        @dataclass
        class Ok:
            capability_id: str = "v1"
            solver_name: str = "S"
            version: str = "1"
            license: str = "MIT"
            supported_physics: list[str] = field(default_factory=list)
            unit_conventions: dict[str, str] = field(default_factory=dict)
            determinism: DeterminismSpec = field(default_factory=lambda: DeterminismSpec(reproducible=True))
            resource_bounds: ResourceBounds = field(
                default_factory=lambda: ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.0)
            )
            checkpoint_restart: CheckpointRestartSpec = field(
                default_factory=lambda: CheckpointRestartSpec(supported=False)
            )
            input_schema: dict[str, object] = field(default_factory=dict)
            output_schema: dict[str, object] = field(default_factory=dict)
            validation_cases: list[ValidationCase] = field(default_factory=list)
            known_limitations: list[str] = field(default_factory=list)

        assert isinstance(Ok(), SolverAdapter)

    def test_non_dataclass_with_right_attrs_passes(self):
        class PlainAdapter:
            capability_id = "v1"
            solver_name = "S"
            version = "1.0"
            license = "MIT"

            def __init__(self):
                self.supported_physics = ["static"]
                self.unit_conventions = {"stress": "MPa"}
                self.determinism = DeterminismSpec(reproducible=True)
                self.resource_bounds = ResourceBounds(cpu_cores=1, memory_mb=1, wall_time_s=1, disk_gb=1.0)
                self.checkpoint_restart = CheckpointRestartSpec(supported=False)
                self.input_schema = {"type": "object"}
                self.output_schema = {"type": "object"}
                self.validation_cases = [ValidationCase(name="vc1")]
                self.known_limitations = ["none"]

        assert isinstance(PlainAdapter(), SolverAdapter)
