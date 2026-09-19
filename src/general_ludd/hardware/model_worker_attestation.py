"""Content-free guest attestation for universal accelerator model workers.

The controller supplies immutable runner and topology expectations.  A worker
uses maintained vendor interfaces to observe its devices, hashes raw identity
and version material locally, and returns only bounded facts suitable for a
zero-downtime admission decision.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

_MAX_TEXT = 1_024
_MAX_OUTPUT = 1_048_576
_MAX_DEVICES = 1_024
_MAX_PROBE_TOKENS = 64
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_VERSION_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_NVIDIA_ROW_RE = re.compile(r"GPU[0-9]+")
_NVIDIA_LINK_RE = re.compile(r"(?:X|SYS|NODE|PHB|PXB|PIX|NV[0-9]+)")


def _bounded_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ValueError(f"{field_name} must be bounded non-empty text")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    return value


def _identifier(value: object, field_name: str) -> str:
    text = _bounded_text(value, field_name)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a portable identifier")
    return text


def _positive_int(value: object, field_name: str, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be a bounded positive integer")
    return value


def _decoded_text(value: object, field_name: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field_name} must be UTF-8") from exc
    return _bounded_text(value, field_name)


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def version_digest(value: str) -> str:
    """Hash one bounded version-probe response without exporting its content."""
    text = _bounded_text(value, "version output")
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CommandObservation:
    """Bounded result from one tokenized, shell-free command probe."""

    returncode: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        """Reject mutable-looking codes and unbounded command output."""
        if type(self.returncode) is not int:
            raise ValueError("returncode must be an integer")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise ValueError("command output must be text")
        size = len(self.stdout.encode("utf-8")) + len(self.stderr.encode("utf-8"))
        if size > _MAX_OUTPUT:
            raise ValueError("command output exceeds bounded size")
        if "\x00" in self.stdout or "\x00" in self.stderr:
            raise ValueError("command output must not contain NUL")


CommandRunner = Callable[[tuple[str, ...], int], CommandObservation]
ModuleLoader = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class TopologyDeviceProfile:
    """Non-secret device shape used by both planned and observed topology."""

    model: str
    memory_mib: int

    def __post_init__(self) -> None:
        """Require a bounded observed model and positive device memory."""
        _bounded_text(self.model, "model")
        _positive_int(self.memory_mib, "memory_mib", maximum=1_000_000_000)

    def payload(self) -> dict[str, object]:
        """Return the stable public device-shape coordinates."""
        return {"memory_mib": self.memory_mib, "model": self.model}


@dataclass(frozen=True, slots=True)
class ModelWorkerTopology:
    """Provider-neutral device shape expected on one worker host."""

    backend: str
    devices: tuple[TopologyDeviceProfile, ...]
    interconnect: str

    def __post_init__(self) -> None:
        """Reject empty, mutable, or unbounded topology descriptions."""
        _identifier(self.backend, "backend")
        _identifier(self.interconnect, "interconnect")
        if (
            not isinstance(self.devices, tuple)
            or not 1 <= len(self.devices) <= _MAX_DEVICES
            or any(not isinstance(device, TopologyDeviceProfile) for device in self.devices)
        ):
            raise ValueError("devices must be a bounded TopologyDeviceProfile tuple")

    @property
    def digest(self) -> str:
        """Bind the backend, device shapes, and interconnect independent of order."""
        devices = [
            device.payload()
            for device in sorted(
                self.devices,
                key=lambda item: (item.model, item.memory_mib),
            )
        ]
        return _canonical_digest(
            {
                "backend": self.backend,
                "devices": devices,
                "interconnect": self.interconnect,
                "protocol": "gludd-model-worker-topology-v1",
            }
        )


@dataclass(frozen=True, slots=True)
class AcceleratorDeviceObservation:
    """One raw guest device observation retained only long enough to hash."""

    identity: str
    model: str
    memory_mib: int
    bus_address: str

    def __post_init__(self) -> None:
        """Bound the raw fields before any canonical hashing occurs."""
        for field_name in ("identity", "model", "bus_address"):
            _bounded_text(getattr(self, field_name), field_name)
        _positive_int(self.memory_mib, "memory_mib", maximum=1_000_000_000)

    def private_payload(self) -> dict[str, object]:
        """Return the in-memory identity payload used only for a local digest."""
        return {
            "bus_address": self.bus_address,
            "identity": self.identity,
            "memory_mib": self.memory_mib,
            "model": self.model,
        }

    def topology_profile(self) -> TopologyDeviceProfile:
        """Drop private identity fields from the shared topology shape."""
        return TopologyDeviceProfile(model=self.model, memory_mib=self.memory_mib)


@dataclass(frozen=True, slots=True)
class AcceleratorSnapshot:
    """Raw vendor probe snapshot with content-free derived coordinates."""

    backend: str
    devices: tuple[AcceleratorDeviceObservation, ...]
    driver_version: str
    interconnect: str

    def __post_init__(self) -> None:
        """Require one internally coherent, bounded vendor snapshot."""
        _identifier(self.backend, "backend")
        _identifier(self.interconnect, "interconnect")
        _bounded_text(self.driver_version, "driver_version")
        if (
            not isinstance(self.devices, tuple)
            or not 1 <= len(self.devices) <= _MAX_DEVICES
            or any(
                not isinstance(device, AcceleratorDeviceObservation)
                for device in self.devices
            )
        ):
            raise ValueError("devices must be a bounded AcceleratorDeviceObservation tuple")
        if len({device.identity for device in self.devices}) != len(self.devices):
            raise ValueError("device identities must be unique")

    @property
    def topology(self) -> ModelWorkerTopology:
        """Return the content-safe topology shape observed by the guest."""
        return ModelWorkerTopology(
            backend=self.backend,
            devices=tuple(device.topology_profile() for device in self.devices),
            interconnect=self.interconnect,
        )

    @property
    def inventory_digest(self) -> str:
        """Hash private device identity without exporting its raw values."""
        devices = sorted(
            (device.private_payload() for device in self.devices),
            key=lambda item: (str(item["identity"]), str(item["bus_address"])),
        )
        return _canonical_digest(
            {
                "backend": self.backend,
                "devices": devices,
                "protocol": "gludd-model-worker-inventory-v1",
            }
        )

    @property
    def driver_version_digest(self) -> str:
        """Return a content-free digest of the loaded driver version."""
        return version_digest(self.driver_version)


@dataclass(frozen=True, slots=True)
class ModelWorkerAttestationRequest:
    """Exact desired evidence boundary for one planned runner candidate."""

    runner_id: str
    source_revision: str
    backend: str
    minimum_device_count: int
    minimum_memory_mib: int
    required_interconnect: str
    expected_topology_digest: str
    runtime_probe: tuple[str, ...]
    expected_runtime_version_digest: str
    timeout_seconds: int = 15

    def __post_init__(self) -> None:
        """Reject ambiguous desired state before any guest command executes."""
        _bounded_text(self.runner_id, "runner_id")
        _bounded_text(self.source_revision, "source_revision")
        _identifier(self.backend, "backend")
        _identifier(self.required_interconnect, "required_interconnect")
        _positive_int(
            self.minimum_device_count,
            "minimum_device_count",
            maximum=_MAX_DEVICES,
        )
        _positive_int(
            self.minimum_memory_mib,
            "minimum_memory_mib",
            maximum=1_000_000_000,
        )
        if _DIGEST_RE.fullmatch(self.expected_topology_digest) is None:
            raise ValueError("expected_topology_digest must be a SHA-256 digest")
        if (
            not isinstance(self.runtime_probe, tuple)
            or not 1 <= len(self.runtime_probe) <= _MAX_PROBE_TOKENS
        ):
            raise ValueError("runtime_probe must be a bounded token tuple")
        for token in self.runtime_probe:
            text = _bounded_text(token, "runtime_probe")
            if any(delimiter in text for delimiter in "\r\n"):
                raise ValueError("runtime_probe tokens must not contain line breaks")
        if _VERSION_DIGEST_RE.fullmatch(self.expected_runtime_version_digest) is None:
            raise ValueError("expected_runtime_version_digest must be a SHA-256 digest")
        _positive_int(self.timeout_seconds, "timeout_seconds", maximum=300)


@dataclass(frozen=True, slots=True)
class ModelWorkerAttestationResult:
    """Content-free admission facts consumed by the universal worker role."""

    facts_attested: bool
    driver_ready: bool
    runtime_ready: bool
    topology_ready: bool
    device_count: int
    source_revision: str
    topology_digest: str
    observed_inventory_digest: str
    backend: str
    interconnect: str
    driver_version_digest: str
    runtime_version_digest: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize only bounded non-secret evidence for Ansible and APIs."""
        return {
            "schema_version": 1,
            "facts_attested": self.facts_attested,
            "driver_ready": self.driver_ready,
            "runtime_ready": self.runtime_ready,
            "topology_ready": self.topology_ready,
            "device_count": self.device_count,
            "source_revision": self.source_revision,
            "topology_digest": self.topology_digest,
            "observed_inventory_digest": self.observed_inventory_digest,
            "backend": self.backend,
            "interconnect": self.interconnect,
            "driver_version_digest": self.driver_version_digest,
            "runtime_version_digest": self.runtime_version_digest,
            "reason_codes": list(self.reason_codes),
        }


class SnapshotProbe(Protocol):
    """Minimum extension contract for one accelerator backend."""

    def snapshot(self) -> AcceleratorSnapshot:
        """Return a bounded live device snapshot or raise on probe failure."""
        ...


class _Nvml(Protocol):
    def nvmlInit(self) -> None: ...

    def nvmlShutdown(self) -> None: ...

    def nvmlSystemGetDriverVersion(self) -> object: ...

    def nvmlDeviceGetCount(self) -> int: ...

    def nvmlDeviceGetHandleByIndex(self, index: int) -> object: ...

    def nvmlDeviceGetUUID(self, handle: object) -> object: ...

    def nvmlDeviceGetName(self, handle: object) -> object: ...

    def nvmlDeviceGetMemoryInfo(self, handle: object) -> object: ...

    def nvmlDeviceGetPciInfo(self, handle: object) -> object: ...


def _nvidia_interconnect(matrix: str, expected_devices: int) -> str:
    rows = []
    for line in matrix.splitlines():
        fields = line.split()
        if len(fields) >= expected_devices and all(
            _NVIDIA_ROW_RE.fullmatch(field) for field in fields[:expected_devices]
        ):
            continue
        if fields and _NVIDIA_ROW_RE.fullmatch(fields[0]):
            links = fields[1 : expected_devices + 1]
            if len(links) != expected_devices or any(
                _NVIDIA_LINK_RE.fullmatch(link) is None for link in links
            ):
                raise ValueError("NVIDIA topology matrix is malformed")
            rows.append(links)
    if len(rows) != expected_devices:
        raise ValueError("NVIDIA topology matrix device count drifted")
    non_self = [link for row in rows for link in row if link != "X"]
    return "nvlink" if any(link.startswith("NV") for link in non_self) else "pcie"


class NvidiaNvmlProbe:
    """Observe NVIDIA devices through NVML and the documented topology CLI."""

    def __init__(
        self,
        *,
        module_loader: ModuleLoader = importlib.import_module,
        command_runner: CommandRunner,
    ) -> None:
        """Configure injectable maintained interfaces for deterministic tests."""
        if not callable(module_loader) or not callable(command_runner):
            raise ValueError("NVIDIA probe dependencies must be callable")
        self._module_loader = module_loader
        self._command_runner = command_runner

    def snapshot(self) -> AcceleratorSnapshot:
        """Return one NVML device snapshot and NVLink/PCIe classification."""
        nvml = cast(_Nvml, self._module_loader("pynvml"))
        initialized = False
        try:
            nvml.nvmlInit()
            initialized = True
            count = _positive_int(
                nvml.nvmlDeviceGetCount(),
                "NVIDIA device count",
                maximum=_MAX_DEVICES,
            )
            driver_version = _decoded_text(
                nvml.nvmlSystemGetDriverVersion(),
                "NVIDIA driver version",
            )
            devices: list[AcceleratorDeviceObservation] = []
            for index in range(count):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                memory_bytes = getattr(memory, "total", None)
                if type(memory_bytes) is not int or memory_bytes < 1024 * 1024:
                    raise ValueError("NVIDIA device memory is invalid")
                pci = nvml.nvmlDeviceGetPciInfo(handle)
                devices.append(
                    AcceleratorDeviceObservation(
                        identity=_decoded_text(
                            nvml.nvmlDeviceGetUUID(handle),
                            "NVIDIA device identity",
                        ),
                        model=_decoded_text(
                            nvml.nvmlDeviceGetName(handle),
                            "NVIDIA device model",
                        ),
                        memory_mib=memory_bytes // (1024 * 1024),
                        bus_address=_decoded_text(
                            getattr(pci, "busId", None),
                            "NVIDIA bus address",
                        ),
                    )
                )
            if count == 1:
                interconnect = "single"
            else:
                topology = self._command_runner(("nvidia-smi", "topo", "-m"), 15)
                if topology.returncode != 0:
                    raise ValueError("NVIDIA topology probe failed")
                interconnect = _nvidia_interconnect(topology.stdout, count)
            return AcceleratorSnapshot(
                backend="nvidia",
                devices=tuple(devices),
                driver_version=driver_version,
                interconnect=interconnect,
            )
        finally:
            if initialized:
                nvml.nvmlShutdown()


class _AmdSmi(Protocol):
    def amdsmi_init(self) -> None: ...

    def amdsmi_shut_down(self) -> None: ...

    def amdsmi_get_processor_handles(self) -> Sequence[object]: ...

    def amdsmi_get_gpu_device_uuid(self, handle: object) -> object: ...

    def amdsmi_get_gpu_device_bdf(self, handle: object) -> object: ...

    def amdsmi_get_gpu_asic_info(self, handle: object) -> Mapping[str, object]: ...

    def amdsmi_get_gpu_vram_info(self, handle: object) -> Mapping[str, object]: ...

    def amdsmi_get_gpu_driver_info(self, handle: object) -> Mapping[str, object]: ...

    def amdsmi_get_xgmi_info(self, handle: object) -> Mapping[str, object]: ...


def _mapping_text(
    value: Mapping[str, object],
    key: str,
    field_name: str,
) -> str:
    return _decoded_text(value.get(key), field_name)


def _xgmi_lanes_active(value: object) -> bool:
    if type(value) is int:
        return value > 0
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "n/a", "none"}
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return len(value) > 0
    return False


class AmdSmiProbe:
    """Observe AMD devices through the vendor-recommended AMD SMI Python API."""

    def __init__(self, *, module_loader: ModuleLoader = importlib.import_module) -> None:
        """Configure an injectable AMD SMI module loader."""
        if not callable(module_loader):
            raise ValueError("AMD module_loader must be callable")
        self._module_loader = module_loader

    def snapshot(self) -> AcceleratorSnapshot:
        """Return AMD device, driver, memory, and XGMI facts."""
        amd = cast(_AmdSmi, self._module_loader("amdsmi"))
        initialized = False
        try:
            amd.amdsmi_init()
            initialized = True
            handles = tuple(amd.amdsmi_get_processor_handles())
            if not 1 <= len(handles) <= _MAX_DEVICES:
                raise ValueError("AMD device count is invalid")
            devices: list[AcceleratorDeviceObservation] = []
            driver_versions: set[str] = set()
            xgmi_active: list[bool] = []
            xgmi_hives: set[str] = set()
            for handle in handles:
                asic = amd.amdsmi_get_gpu_asic_info(handle)
                memory = amd.amdsmi_get_gpu_vram_info(handle)
                driver = amd.amdsmi_get_gpu_driver_info(handle)
                memory_mib = memory.get("vram_size")
                if type(memory_mib) is not int:
                    raise ValueError("AMD device memory is invalid")
                devices.append(
                    AcceleratorDeviceObservation(
                        identity=_decoded_text(
                            amd.amdsmi_get_gpu_device_uuid(handle),
                            "AMD device identity",
                        ),
                        model=_mapping_text(asic, "market_name", "AMD device model"),
                        memory_mib=memory_mib,
                        bus_address=_decoded_text(
                            amd.amdsmi_get_gpu_device_bdf(handle),
                            "AMD bus address",
                        ),
                    )
                )
                driver_versions.add(
                    _mapping_text(driver, "driver_version", "AMD driver version")
                )
                try:
                    xgmi = amd.amdsmi_get_xgmi_info(handle)
                except Exception:
                    xgmi_active.append(False)
                else:
                    xgmi_active.append(_xgmi_lanes_active(xgmi.get("xgmi_lanes")))
                    xgmi_hives.add(str(xgmi.get("xgmi_hive_id", "")))
            if len(driver_versions) != 1:
                raise ValueError("AMD driver versions are inconsistent")
            if len(handles) == 1:
                interconnect = "single"
            elif all(xgmi_active) and len(xgmi_hives) == 1 and "" not in xgmi_hives:
                interconnect = "xgmi"
            else:
                interconnect = "pcie"
            return AcceleratorSnapshot(
                backend="amd",
                devices=tuple(devices),
                driver_version=next(iter(driver_versions)),
                interconnect=interconnect,
            )
        finally:
            if initialized:
                amd.amdsmi_shut_down()


def _failed_result(
    request: ModelWorkerAttestationRequest,
    reason_code: str,
) -> ModelWorkerAttestationResult:
    return ModelWorkerAttestationResult(
        facts_attested=False,
        driver_ready=False,
        runtime_ready=False,
        topology_ready=False,
        device_count=0,
        source_revision=request.source_revision,
        topology_digest="",
        observed_inventory_digest="",
        backend=request.backend,
        interconnect="",
        driver_version_digest="",
        runtime_version_digest="",
        reason_codes=(reason_code,),
    )


class ModelWorkerAttestor:
    """Evaluate vendor snapshots and runner probes against desired evidence."""

    def __init__(
        self,
        *,
        probes: Mapping[str, SnapshotProbe] | None = None,
        command_runner: CommandRunner | None = None,
        module_loader: ModuleLoader = importlib.import_module,
    ) -> None:
        """Configure default vendor probes or an extension-provided registry."""
        runner = command_runner or self.run_command
        if not callable(runner) or not callable(module_loader):
            raise ValueError("attestor dependencies must be callable")
        if probes is None:
            configured: dict[str, SnapshotProbe] = {
                "nvidia": NvidiaNvmlProbe(
                    module_loader=module_loader,
                    command_runner=runner,
                ),
                "amd": AmdSmiProbe(module_loader=module_loader),
            }
        else:
            configured = dict(probes)
        for backend, probe in configured.items():
            _identifier(backend, "probe backend")
            if not callable(getattr(probe, "snapshot", None)):
                raise ValueError("probe must implement snapshot")
        self._probes = configured
        self._command_runner = runner

    @staticmethod
    def run_command(
        argv: tuple[str, ...],
        timeout_seconds: int,
    ) -> CommandObservation:
        """Run a bounded tokenized probe without a shell or inherited stdin."""
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return CommandObservation(127, "", "")
        except subprocess.TimeoutExpired:
            return CommandObservation(124, "", "")
        return CommandObservation(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    def attest(
        self,
        request: ModelWorkerAttestationRequest,
    ) -> ModelWorkerAttestationResult:
        """Return content-free evidence, failing closed on any unavailable probe."""
        if not isinstance(request, ModelWorkerAttestationRequest):
            raise ValueError("request must be a ModelWorkerAttestationRequest")
        probe = self._probes.get(request.backend)
        if probe is None:
            return _failed_result(request, "probe_unavailable")
        try:
            snapshot = probe.snapshot()
            if snapshot.backend != request.backend:
                raise ValueError("probe backend drifted")
        except Exception:
            return _failed_result(request, "driver_probe_failed")

        reasons: list[str] = []
        if len(snapshot.devices) < request.minimum_device_count:
            reasons.append("insufficient_devices")
        if any(
            device.memory_mib < request.minimum_memory_mib
            for device in snapshot.devices
        ):
            reasons.append("insufficient_memory")
        driver_ready = not reasons

        interconnect_ready = (
            request.required_interconnect == "any"
            or snapshot.interconnect == request.required_interconnect
        )
        if not interconnect_ready:
            reasons.append("interconnect_mismatch")
        topology_digest = snapshot.topology.digest
        digest_ready = topology_digest == request.expected_topology_digest
        if not digest_ready:
            reasons.append("topology_digest_mismatch")
        topology_ready = interconnect_ready and digest_ready

        runtime_version = ""
        runtime_ready = False
        try:
            runtime = self._command_runner(
                request.runtime_probe,
                request.timeout_seconds,
            )
            output = runtime.stdout if runtime.stdout else runtime.stderr
            if runtime.returncode != 0:
                reasons.append("runtime_probe_failed")
            elif not output:
                reasons.append("runtime_probe_empty")
            else:
                runtime_version = version_digest(output)
                runtime_ready = (
                    runtime_version == request.expected_runtime_version_digest
                )
                if not runtime_ready:
                    reasons.append("runtime_version_mismatch")
        except Exception:
            reasons.append("runtime_probe_failed")

        facts_attested = driver_ready and topology_ready and runtime_ready
        return ModelWorkerAttestationResult(
            facts_attested=facts_attested,
            driver_ready=driver_ready,
            runtime_ready=runtime_ready,
            topology_ready=topology_ready,
            device_count=len(snapshot.devices),
            source_revision=request.source_revision,
            topology_digest=topology_digest,
            observed_inventory_digest=snapshot.inventory_digest,
            backend=snapshot.backend,
            interconnect=snapshot.interconnect,
            driver_version_digest=snapshot.driver_version_digest,
            runtime_version_digest=runtime_version,
            reason_codes=tuple(reasons),
        )


__all__ = (
    "AcceleratorDeviceObservation",
    "AcceleratorSnapshot",
    "AmdSmiProbe",
    "CommandObservation",
    "ModelWorkerAttestationRequest",
    "ModelWorkerAttestationResult",
    "ModelWorkerAttestor",
    "ModelWorkerTopology",
    "NvidiaNvmlProbe",
    "SnapshotProbe",
    "TopologyDeviceProfile",
    "version_digest",
)
