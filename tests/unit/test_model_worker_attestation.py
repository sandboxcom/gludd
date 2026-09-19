"""Provider-neutral model-worker driver, topology, and runtime attestation."""

from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.hardware.model_worker_attestation import (
    AcceleratorDeviceObservation,
    AcceleratorSnapshot,
    AmdSmiProbe,
    CommandObservation,
    ModelWorkerAttestationRequest,
    ModelWorkerAttestor,
    ModelWorkerTopology,
    NvidiaNvmlProbe,
    TopologyDeviceProfile,
    version_digest,
)

ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_RUNTIME = version_digest("vllm 0.10.2\n")


class _Memory:
    def __init__(self, total: int) -> None:
        self.total = total


class _Pci:
    def __init__(self, bus_id: str) -> None:
        self.busId = bus_id.encode("ascii")


class _FakeNvml:
    def __init__(self, *, count: int = 2, memory_mib: int = 81_920) -> None:
        self.count = count
        self.memory_mib = memory_mib
        self.initialized = False
        self.shutdown = False

    def nvmlInit(self) -> None:
        self.initialized = True

    def nvmlShutdown(self) -> None:
        self.shutdown = True

    def nvmlSystemGetDriverVersion(self) -> bytes:
        return b"580.82.07"

    def nvmlDeviceGetCount(self) -> int:
        return self.count

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return index

    def nvmlDeviceGetUUID(self, handle: int) -> bytes:
        return f"GPU-private-{handle}".encode()

    def nvmlDeviceGetName(self, handle: int) -> bytes:
        del handle
        return b"NVIDIA H100 80GB HBM3"

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> _Memory:
        del handle
        return _Memory(self.memory_mib * 1024 * 1024)

    def nvmlDeviceGetPciInfo(self, handle: int) -> _Pci:
        return _Pci(f"0000:{handle + 1:02x}:00.0")


class _FakeAmdSmi:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.initialized = False
        self.shutdown = False

    def amdsmi_init(self) -> None:
        self.initialized = True

    def amdsmi_shut_down(self) -> None:
        self.shutdown = True

    def amdsmi_get_processor_handles(self) -> list[str]:
        if self.fail:
            raise RuntimeError("private driver failure")
        return ["private-handle-0", "private-handle-1"]

    def amdsmi_get_gpu_device_uuid(self, handle: str) -> str:
        return f"UUID-{handle}"

    def amdsmi_get_gpu_device_bdf(self, handle: str) -> str:
        return "0000:01:00.0" if handle.endswith("0") else "0000:02:00.0"

    def amdsmi_get_gpu_asic_info(self, handle: str) -> dict[str, object]:
        del handle
        return {"market_name": "AMD Instinct MI300X"}

    def amdsmi_get_gpu_vram_info(self, handle: str) -> dict[str, object]:
        del handle
        return {"vram_size": 196_608}

    def amdsmi_get_gpu_driver_info(self, handle: str) -> dict[str, object]:
        del handle
        return {"driver_version": "6.10.5"}

    def amdsmi_get_xgmi_info(self, handle: str) -> dict[str, object]:
        return {
            "xgmi_lanes": 8,
            "xgmi_hive_id": 42,
            "xgmi_node_id": 0 if handle.endswith("0") else 1,
            "index": 0 if handle.endswith("0") else 1,
        }


def _topology(
    *,
    backend: str = "nvidia",
    count: int = 2,
    memory_mib: int = 81_920,
    interconnect: str = "nvlink",
) -> ModelWorkerTopology:
    model = "NVIDIA H100 80GB HBM3" if backend == "nvidia" else "AMD Instinct MI300X"
    return ModelWorkerTopology(
        backend=backend,
        devices=tuple(
            TopologyDeviceProfile(model=model, memory_mib=memory_mib)
            for _index in range(count)
        ),
        interconnect=interconnect,
    )


def _request(
    *,
    backend: str = "nvidia",
    topology: ModelWorkerTopology | None = None,
    required_interconnect: str = "nvlink",
    minimum_device_count: int = 2,
    minimum_memory_mib: int = 80_000,
    runtime_digest: str = _EXPECTED_RUNTIME,
) -> ModelWorkerAttestationRequest:
    expected = topology or _topology(backend=backend, interconnect=required_interconnect)
    return ModelWorkerAttestationRequest(
        runner_id="vllm-observed",
        source_revision="vllm-cli-observed-2026-09-19",
        backend=backend,
        minimum_device_count=minimum_device_count,
        minimum_memory_mib=minimum_memory_mib,
        required_interconnect=required_interconnect,
        expected_topology_digest=expected.digest,
        runtime_probe=("vllm", "--version"),
        expected_runtime_version_digest=runtime_digest,
        timeout_seconds=15,
    )


def _nvidia_runner(
    argv: tuple[str, ...],
    timeout_seconds: int,
) -> CommandObservation:
    assert timeout_seconds == 15
    if argv == ("nvidia-smi", "topo", "-m"):
        return CommandObservation(
            returncode=0,
            stdout=(
                "        GPU0    GPU1    CPU Affinity\n"
                "GPU0     X      NV4     0-31\n"
                "GPU1    NV4      X      0-31\n"
            ),
            stderr="",
        )
    assert argv == ("vllm", "--version")
    return CommandObservation(returncode=0, stdout="vllm 0.10.2\n", stderr="")


def test_topology_digest_is_canonical_bounded_and_immutable() -> None:
    first = _topology()
    reverse = ModelWorkerTopology(
        backend="nvidia",
        devices=tuple(reversed(first.devices)),
        interconnect="nvlink",
    )

    assert first.digest == reverse.digest
    assert len(first.digest) == 64
    with pytest.raises(FrozenInstanceError):
        first.backend = "amd"  # type: ignore[misc]
    with pytest.raises(ValueError, match="devices"):
        ModelWorkerTopology(backend="nvidia", devices=(), interconnect="single")
    with pytest.raises(ValueError, match="memory_mib"):
        TopologyDeviceProfile(model="GPU", memory_mib=True)


def test_nvidia_nvml_attestation_proves_multi_gpu_runtime_without_raw_identifiers() -> None:
    nvml = _FakeNvml()
    probe = NvidiaNvmlProbe(
        module_loader=lambda name: nvml if name == "pynvml" else None,
        command_runner=_nvidia_runner,
    )
    snapshot = probe.snapshot()
    assert snapshot.backend == "nvidia"
    assert snapshot.interconnect == "nvlink"
    attestor = ModelWorkerAttestor(
        probes={"nvidia": probe},
        command_runner=_nvidia_runner,
    )

    result = attestor.attest(_request())

    assert result.facts_attested is True
    assert result.driver_ready is True
    assert result.runtime_ready is True
    assert result.topology_ready is True
    assert result.device_count == 2
    assert result.backend == "nvidia"
    assert result.interconnect == "nvlink"
    assert result.topology_digest == _topology().digest
    assert result.runtime_version_digest == _EXPECTED_RUNTIME
    assert result.reason_codes == ()
    assert nvml.initialized is True
    assert nvml.shutdown is True
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert "GPU-private" not in serialized
    assert "0000:01:00.0" not in serialized
    assert "580.82.07" not in serialized
    assert "NVIDIA H100" not in serialized


def test_attestation_rejects_pcie_when_nvlink_is_required() -> None:
    def runner(argv: tuple[str, ...], timeout_seconds: int) -> CommandObservation:
        if argv == ("nvidia-smi", "topo", "-m"):
            return CommandObservation(
                0,
                "        GPU0 GPU1\nGPU0 X PHB\nGPU1 PHB X\n",
                "",
            )
        return _nvidia_runner(argv, timeout_seconds)

    attestor = ModelWorkerAttestor(
        probes={
            "nvidia": NvidiaNvmlProbe(
                module_loader=lambda _name: _FakeNvml(),
                command_runner=runner,
            )
        },
        command_runner=runner,
    )

    result = attestor.attest(_request())

    assert result.driver_ready is True
    assert result.topology_ready is False
    assert result.facts_attested is False
    assert "interconnect_mismatch" in result.reason_codes
    assert "topology_digest_mismatch" in result.reason_codes


def test_attestation_reports_insufficient_devices_and_memory() -> None:
    nvml = _FakeNvml(count=1, memory_mib=40_000)
    single_runner_calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], timeout_seconds: int) -> CommandObservation:
        single_runner_calls.append(argv)
        if argv == ("vllm", "--version"):
            return _nvidia_runner(argv, timeout_seconds)
        raise AssertionError(f"single-device probe must not request topology: {argv}")

    attestor = ModelWorkerAttestor(
        probes={
            "nvidia": NvidiaNvmlProbe(
                module_loader=lambda _name: nvml,
                command_runner=runner,
            )
        },
        command_runner=runner,
    )

    result = attestor.attest(_request())

    assert result.device_count == 1
    assert result.driver_ready is False
    assert result.facts_attested is False
    assert result.reason_codes == (
        "insufficient_devices",
        "insufficient_memory",
        "interconnect_mismatch",
        "topology_digest_mismatch",
    )
    assert single_runner_calls == [("vllm", "--version")]


def test_runtime_version_must_match_the_attested_profile() -> None:
    def runner(argv: tuple[str, ...], timeout_seconds: int) -> CommandObservation:
        if argv == ("vllm", "--version"):
            return CommandObservation(0, "vllm changed", "")
        return _nvidia_runner(argv, timeout_seconds)

    result = ModelWorkerAttestor(
        probes={
            "nvidia": NvidiaNvmlProbe(
                module_loader=lambda _name: _FakeNvml(),
                command_runner=runner,
            )
        },
        command_runner=runner,
    ).attest(_request())

    assert result.driver_ready is True
    assert result.runtime_ready is False
    assert result.facts_attested is False
    assert result.reason_codes == ("runtime_version_mismatch",)
    assert result.runtime_version_digest == version_digest("vllm changed")


def test_missing_or_failed_probe_returns_only_bounded_failure_evidence() -> None:
    runtime_calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], _timeout: int) -> CommandObservation:
        runtime_calls.append(argv)
        return CommandObservation(0, "should not run", "")

    attestor = ModelWorkerAttestor(probes={}, command_runner=runner)
    result = attestor.attest(_request(backend="future-tpu"))

    assert result.to_dict() == {
        "schema_version": 1,
        "facts_attested": False,
        "driver_ready": False,
        "runtime_ready": False,
        "topology_ready": False,
        "device_count": 0,
        "source_revision": "vllm-cli-observed-2026-09-19",
        "topology_digest": "",
        "observed_inventory_digest": "",
        "backend": "future-tpu",
        "interconnect": "",
        "driver_version_digest": "",
        "runtime_version_digest": "",
        "reason_codes": ["probe_unavailable"],
    }
    assert runtime_calls == []


def test_amd_smi_attestation_uses_vendor_library_and_xgmi() -> None:
    amd = _FakeAmdSmi()
    topology = _topology(
        backend="amd",
        count=2,
        memory_mib=196_608,
        interconnect="xgmi",
    )
    attestor = ModelWorkerAttestor(
        probes={"amd": AmdSmiProbe(module_loader=lambda _name: amd)},
        command_runner=_nvidia_runner,
    )

    result = attestor.attest(
        _request(
            backend="amd",
            topology=topology,
            required_interconnect="xgmi",
            minimum_memory_mib=190_000,
        )
    )

    assert result.facts_attested is True
    assert result.topology_digest == topology.digest
    assert result.interconnect == "xgmi"
    assert amd.initialized is True
    assert amd.shutdown is True


def test_amd_smi_shutdown_runs_when_driver_query_fails() -> None:
    amd = _FakeAmdSmi(fail=True)
    runtime_calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], _timeout: int) -> CommandObservation:
        runtime_calls.append(argv)
        return CommandObservation(0, "unexpected", "")

    result = ModelWorkerAttestor(
        probes={"amd": AmdSmiProbe(module_loader=lambda _name: amd)},
        command_runner=runner,
    ).attest(_request(backend="amd"))

    assert result.reason_codes == ("driver_probe_failed",)
    assert result.facts_attested is False
    assert amd.shutdown is True
    assert runtime_calls == []


def test_custom_future_accelerator_probe_uses_the_same_contract() -> None:
    class FutureProbe:
        def snapshot(self) -> AcceleratorSnapshot:
            return AcceleratorSnapshot(
                backend="future-tpu",
                devices=(
                    AcceleratorDeviceObservation(
                        identity="private-tpu-id",
                        model="Future TPU",
                        memory_mib=32_768,
                        bus_address="private-mesh-coordinate",
                    ),
                ),
                driver_version="future-driver-1",
                interconnect="tpu-mesh",
            )

    topology = ModelWorkerTopology(
        backend="future-tpu",
        devices=(TopologyDeviceProfile("Future TPU", 32_768),),
        interconnect="tpu-mesh",
    )
    attestor = ModelWorkerAttestor(
        probes={"future-tpu": FutureProbe()},
        command_runner=lambda _argv, _timeout: CommandObservation(
            0,
            "vllm 0.10.2\n",
            "",
        ),
    )

    result = attestor.attest(
        _request(
            backend="future-tpu",
            topology=topology,
            required_interconnect="tpu-mesh",
            minimum_device_count=1,
            minimum_memory_mib=32_000,
        )
    )

    assert result.facts_attested is True
    assert result.backend == "future-tpu"
    assert "private-tpu-id" not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    "mutation",
    [
        {"runner_id": ""},
        {"backend": "BAD backend"},
        {"minimum_device_count": 0},
        {"minimum_memory_mib": True},
        {"required_interconnect": ""},
        {"expected_topology_digest": "short"},
        {"runtime_probe": cast(tuple[str, ...], ["vllm", "--version"])},
        {"runtime_probe": ("vllm\n",)},
        {"expected_runtime_version_digest": "sha256:short"},
        {"timeout_seconds": 0},
    ],
)
def test_attestation_request_rejects_ambiguous_or_mutable_input(
    mutation: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        replace(_request(), **mutation)


def test_command_observation_and_snapshot_reject_unbounded_shapes() -> None:
    with pytest.raises(ValueError, match="returncode"):
        CommandObservation(returncode=True, stdout="", stderr="")
    with pytest.raises(ValueError, match="output"):
        CommandObservation(returncode=0, stdout="x" * 1_048_577, stderr="")
    with pytest.raises(ValueError, match="text"):
        CommandObservation(returncode=0, stdout=cast(Any, b"bytes"), stderr="")
    with pytest.raises(ValueError, match="NUL"):
        CommandObservation(returncode=0, stdout="bad\x00output", stderr="")
    with pytest.raises(ValueError, match="devices"):
        AcceleratorSnapshot(
            backend="nvidia",
            devices=cast(tuple[AcceleratorDeviceObservation, ...], []),
            driver_version="driver",
            interconnect="single",
        )

    duplicate = AcceleratorDeviceObservation("same", "GPU", 1, "bus")
    with pytest.raises(ValueError, match="unique"):
        AcceleratorSnapshot(
            backend="nvidia",
            devices=(duplicate, duplicate),
            driver_version="driver",
            interconnect="pcie",
        )


@pytest.mark.parametrize(
    ("observation", "error"),
    [
        (CommandObservation(1, "", "private"), "topology probe failed"),
        (CommandObservation(0, "GPU0 X BAD\nGPU1 PHB X\n", ""), "matrix is malformed"),
        (CommandObservation(0, "GPU0 X PHB\n", ""), "device count drifted"),
    ],
)
def test_nvidia_topology_probe_rejects_failed_or_malformed_matrix(
    observation: CommandObservation,
    error: str,
) -> None:
    probe = NvidiaNvmlProbe(
        module_loader=lambda _name: _FakeNvml(),
        command_runner=lambda _argv, _timeout: observation,
    )

    with pytest.raises(ValueError, match=error):
        probe.snapshot()


def test_nvidia_probe_rejects_invalid_memory_and_non_utf8_driver() -> None:
    bad_memory = _FakeNvml(memory_mib=0)
    with pytest.raises(ValueError, match="memory"):
        NvidiaNvmlProbe(
            module_loader=lambda _name: bad_memory,
            command_runner=_nvidia_runner,
        ).snapshot()
    assert bad_memory.shutdown is True

    class BadDriver(_FakeNvml):
        def nvmlSystemGetDriverVersion(self) -> bytes:
            return b"\xff"

    bad_driver = BadDriver()
    with pytest.raises(ValueError, match="UTF-8"):
        NvidiaNvmlProbe(
            module_loader=lambda _name: bad_driver,
            command_runner=_nvidia_runner,
        ).snapshot()
    assert bad_driver.shutdown is True


def test_amd_probe_handles_single_device_and_pcie_fallback() -> None:
    class SingleAmd(_FakeAmdSmi):
        def amdsmi_get_processor_handles(self) -> list[str]:
            return ["private-handle-0"]

    single = AmdSmiProbe(module_loader=lambda _name: SingleAmd()).snapshot()
    assert single.interconnect == "single"

    class NoXgmi(_FakeAmdSmi):
        def amdsmi_get_xgmi_info(self, handle: str) -> dict[str, object]:
            del handle
            raise RuntimeError("not supported")

    pcie = AmdSmiProbe(module_loader=lambda _name: NoXgmi()).snapshot()
    assert pcie.interconnect == "pcie"


@pytest.mark.parametrize("failure", ["count", "memory", "driver"])
def test_amd_probe_rejects_malformed_vendor_evidence(failure: str) -> None:
    class BrokenAmd(_FakeAmdSmi):
        def amdsmi_get_processor_handles(self) -> list[str]:
            if failure == "count":
                return []
            return super().amdsmi_get_processor_handles()

        def amdsmi_get_gpu_vram_info(self, handle: str) -> dict[str, object]:
            if failure == "memory":
                return {"vram_size": "unknown"}
            return super().amdsmi_get_gpu_vram_info(handle)

        def amdsmi_get_gpu_driver_info(self, handle: str) -> dict[str, object]:
            info = super().amdsmi_get_gpu_driver_info(handle)
            if failure == "driver" and handle.endswith("1"):
                info["driver_version"] = "different"
            return info

    amd = BrokenAmd()
    with pytest.raises(ValueError):
        AmdSmiProbe(module_loader=lambda _name: amd).snapshot()
    assert amd.shutdown is True


@pytest.mark.parametrize(
    ("runtime", "expected_reason", "expected_digest"),
    [
        (CommandObservation(1, "", "private"), "runtime_probe_failed", ""),
        (CommandObservation(0, "", ""), "runtime_probe_empty", ""),
        (
            CommandObservation(0, "", "vllm 0.10.2\n"),
            "",
            _EXPECTED_RUNTIME,
        ),
    ],
)
def test_runtime_probe_failure_empty_and_stderr_paths(
    runtime: CommandObservation,
    expected_reason: str,
    expected_digest: str,
) -> None:
    class FixedProbe:
        def snapshot(self) -> AcceleratorSnapshot:
            return AcceleratorSnapshot(
                backend="nvidia",
                devices=tuple(
                    AcceleratorDeviceObservation(
                        identity=f"id-{index}",
                        model="NVIDIA H100 80GB HBM3",
                        memory_mib=81_920,
                        bus_address=f"bus-{index}",
                    )
                    for index in range(2)
                ),
                driver_version="driver",
                interconnect="nvlink",
            )

    result = ModelWorkerAttestor(
        probes={"nvidia": FixedProbe()},
        command_runner=lambda _argv, _timeout: runtime,
    ).attest(_request())

    assert result.runtime_version_digest == expected_digest
    if expected_reason:
        assert expected_reason in result.reason_codes
        assert result.facts_attested is False
    else:
        assert result.reason_codes == ()
        assert result.facts_attested is True


def test_runtime_exception_backend_drift_and_wrong_request_fail_closed() -> None:
    class DriftedProbe:
        def snapshot(self) -> AcceleratorSnapshot:
            return AcceleratorSnapshot(
                backend="amd",
                devices=(AcceleratorDeviceObservation("id", "GPU", 81_920, "bus"),),
                driver_version="driver",
                interconnect="single",
            )

    drifted = ModelWorkerAttestor(
        probes={"nvidia": DriftedProbe()},
        command_runner=_nvidia_runner,
    ).attest(_request())
    assert drifted.reason_codes == ("driver_probe_failed",)

    class FixedProbe:
        def snapshot(self) -> AcceleratorSnapshot:
            return AcceleratorSnapshot(
                backend="nvidia",
                devices=tuple(
                    AcceleratorDeviceObservation(f"id-{i}", "NVIDIA H100 80GB HBM3", 81_920, f"bus-{i}")
                    for i in range(2)
                ),
                driver_version="driver",
                interconnect="nvlink",
            )

    failed_runtime = ModelWorkerAttestor(
        probes={"nvidia": FixedProbe()},
        command_runner=lambda _argv, _timeout: (_ for _ in ()).throw(RuntimeError()),
    ).attest(_request())
    assert failed_runtime.reason_codes == ("runtime_probe_failed",)
    with pytest.raises(ValueError, match="request"):
        ModelWorkerAttestor(probes={}).attest(cast(Any, object()))


def test_probe_dependency_and_registry_validation() -> None:
    with pytest.raises(ValueError, match="dependencies"):
        NvidiaNvmlProbe(
            module_loader=cast(Any, None),
            command_runner=_nvidia_runner,
        )
    with pytest.raises(ValueError, match="module_loader"):
        AmdSmiProbe(module_loader=cast(Any, None))
    with pytest.raises(ValueError, match="dependencies"):
        ModelWorkerAttestor(probes={}, command_runner=cast(Any, None), module_loader=cast(Any, None))
    with pytest.raises(ValueError, match="snapshot"):
        ModelWorkerAttestor(probes={"nvidia": cast(Any, object())})

    default = ModelWorkerAttestor(
        module_loader=lambda _name: (_ for _ in ()).throw(ImportError()),
        command_runner=_nvidia_runner,
    )
    assert default.attest(_request()).reason_codes == ("driver_probe_failed",)


def test_default_command_runner_fails_closed_on_missing_binary_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestor = ModelWorkerAttestor(probes={})

    def missing(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert attestor.run_command(("missing",), 1).returncode == 127

    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["slow"], timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert attestor.run_command(("slow",), 1).returncode == 124

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["runner"],
            returncode=0,
            stdout="version",
            stderr="",
        ),
    )
    assert attestor.run_command(("runner",), 1) == CommandObservation(0, "version", "")


def test_feature_document_records_vendor_sources_and_operator_failures() -> None:
    text = (ROOT / "docs" / "features" / "MODEL_WORKER_ATTESTATION.md").read_text()

    assert "provider-neutral" in text
    assert "self-improvement" in text
    assert "nvidia-ml-py" in text
    assert "AMD SMI" in text
    assert "docs.nvidia.com/deploy/nvml-api" in text
    assert "rocm.docs.amd.com/projects/amdsmi" in text
    assert "github.com/NVIDIA/nvidia-container-toolkit/issues/48" in text
    assert "github.com/vllm-project/vllm/issues/33041" in text
    assert "github.com/ollama/ollama/issues/7047" in text


def test_attestation_has_a_strict_focused_coverage_profile() -> None:
    profile = (ROOT / "config" / "coverage_model_worker_attestation.ini").read_text()

    assert "*/src/general_ludd/hardware/model_worker_attestation.py" in profile
    assert "fail_under = 85" in profile
