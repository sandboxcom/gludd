"""VM sandbox lifecycle manager — P3 daemon-dispatch wiring + observability.

Coordinates the boot/dispatch/verify/release cycle for Firecracker and gVisor
backends. Tracks per-VM lifecycle state, metrics (boot time, dispatch count,
verify findings), and emits structured events for the daemon's observability
surface.

This module is the daemon-facing layer: ``boot()`` selects a backend, applies
its sandbox, and records the instance; ``dispatch()`` records execution against
the instance; ``verify()`` records divergence findings; ``release()`` tears
down. ``observe()`` returns aggregate metrics for daemon endpoints
(``/metrics/sandboxes``, ``/admin/vms``).

See ``docs/specs/FEATURE_UNIKERNEL_SANDBOX.md`` §4 P3.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)

logger = logging.getLogger(__name__)


class VMLifecycleState(StrEnum):
    """State machine for a single VM sandbox instance.

    PENDING → BOOTING → RUNNING → EXECUTING (transient per dispatch) → RUNNING
    → STOPPED on release. FAILED is a terminal-from-anywhere state used when
    backend apply fails or verify finds a critical divergence.
    """

    PENDING = "pending"
    BOOTING = "booting"
    RUNNING = "running"
    EXECUTING = "executing"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class VMMetrics:
    """Per-instance runtime metrics.

    Boot time is measured around ``backend.apply``; dispatch time around
    ``AgentExecutor.receive_and_execute``; verify findings is the count of
    findings the most recent ``backend.verify`` returned.
    """

    boot_ms: float = 0.0
    dispatch_count: int = 0
    peak_rss_kb: int = 0
    last_verify_findings: int = 0
    total_dispatch_ms: float = 0.0


@dataclass
class VMInstance:
    """A single booted VM sandbox instance and its accumulated state."""

    instance_id: str
    backend_name: str
    spec: PermissionSpec
    handle: SandboxHandle
    state: VMLifecycleState = VMLifecycleState.PENDING
    started_at: float = field(default_factory=time.time)
    stopped_at: float = 0.0
    metrics: VMMetrics = field(default_factory=VMMetrics)
    image_path: Path | None = None


def _resolve_backend(name: str) -> Any:
    """Lazy-import the backend class by canonical name."""
    if name == "firecracker":
        from general_ludd.security.sandboxes.vm.firecracker_backend import (
            FirecrackerBackend,
        )
        return FirecrackerBackend
    if name == "gvisor":
        from general_ludd.security.sandboxes.vm.gvisor_backend import GvisorBackend
        return GvisorBackend
    raise ValueError(
        f"Unknown backend {name!r} (expected 'firecracker' or 'gvisor')"
    )


class VMSandboxManager:
    """Daemon-facing facade over the VM sandbox backends.

    Holds the live instance registry, emits observability events, and computes
    aggregate metrics. Thread-unsafe by design — the daemon serialises
    mutations through the event loop. Concurrent instance IDs are isolated by
    uuid4 so parallel dispatches cannot collide.
    """

    def __init__(self) -> None:
        self.instances: dict[str, VMInstance] = {}
        self.events: list[dict[str, Any]] = []

    def boot(
        self,
        backend_name: str,
        spec: PermissionSpec,
        target: SandboxTarget,
        image_path: str | Path | None = None,
    ) -> VMInstance:
        """Resolve the backend, apply the sandbox, and register the instance.

        Records boot time and a 'booted' event on success, or transitions the
        instance to FAILED (still registered) when the backend reports
        unavailable or apply fails-open.
        """
        backend = _resolve_backend(backend_name)
        instance_id = f"vm-{uuid.uuid4().hex[:12]}"
        resolved_image = Path(image_path) if image_path is not None else None

        if not backend.available():
            logger.warning(
                "VMSandboxManager.boot: backend %s unavailable — recording FAILED",
                backend_name,
            )
            instance = VMInstance(
                instance_id=instance_id,
                backend_name=backend_name,
                spec=spec,
                handle=SandboxHandle(
                    backend=backend_name,
                    token=f"gludd-{spec.agent_type}",
                    applied=False,
                    extra={"reason": f"{backend_name} unavailable"},
                ),
                state=VMLifecycleState.FAILED,
                image_path=resolved_image,
            )
            self.instances[instance_id] = instance
            self._emit("boot_failed", instance, reason="backend unavailable")
            return instance

        start = time.monotonic()
        handle = backend.apply(spec, target)
        boot_ms = (time.monotonic() - start) * 1000.0

        if not handle.applied:
            state = VMLifecycleState.FAILED
            self._emit("boot_failed", _placeholder(instance_id, backend_name, spec, handle))
        else:
            state = VMLifecycleState.RUNNING

        instance = VMInstance(
            instance_id=instance_id,
            backend_name=backend_name,
            spec=spec,
            handle=handle,
            state=state,
            image_path=resolved_image,
        )
        instance.metrics.boot_ms = boot_ms
        self.instances[instance_id] = instance
        if state is VMLifecycleState.RUNNING:
            self._emit("booted", instance, boot_ms=boot_ms)
        return instance

    def dispatch(
        self,
        instance_id: str,
        target: SandboxTarget,
        command: Any | None = None,
    ) -> dict[str, Any]:
        """Execute a SandboxTarget inside a previously booted VM.

        Records wall-clock time and increments the dispatch counter. The VM
        transitions to EXECUTING during the call and back to RUNNING when it
        returns (in real usage the underlying virtio-vsock call is blocking;
        here it is delegated to AgentExecutor).

        ``command`` is an optional :class:`AgentCommand` (P4) — when supplied,
        the executor runs a real ``subprocess.run`` against it; when ``None``,
        the legacy stub path is preserved for backward compatibility.
        """
        instance = self._require(instance_id)
        if instance.state not in (VMLifecycleState.RUNNING, VMLifecycleState.EXECUTING):
            raise RuntimeError(
                f"VM {instance_id} is {instance.state.value!r} (not running)"
            )

        from general_ludd.security.sandboxes.vm.agent_executor import AgentExecutor

        instance.state = VMLifecycleState.EXECUTING
        start = time.monotonic()
        try:
            result = AgentExecutor.receive_and_execute(target, command=command)
        finally:
            instance.state = VMLifecycleState.RUNNING
        elapsed_ms = (time.monotonic() - start) * 1000.0
        instance.metrics.dispatch_count += 1
        instance.metrics.total_dispatch_ms += elapsed_ms

        self._emit("dispatched", instance, wall_ms=elapsed_ms)
        return {"status": "executed", "result": result, "instance_id": instance_id}

    def verify(self, instance_id: str) -> list[Finding]:
        """Re-read OS state via the backend's verifier and record findings."""
        instance = self._require(instance_id)
        backend = _resolve_backend(instance.backend_name)
        findings: list[Finding] = list(backend.verify(instance.spec, instance.handle))
        instance.metrics.last_verify_findings = len(findings)
        self._emit("verified", instance, findings=len(findings))
        return findings

    def release(self, instance_id: str) -> dict[str, Any]:
        """Tear down the VM sandbox. Idempotent on already-stopped instances."""
        instance = self._require(instance_id)
        if instance.state is VMLifecycleState.STOPPED:
            return {"state": instance.state.value, "instance_id": instance_id}

        backend = _resolve_backend(instance.backend_name)
        if instance.handle.applied:
            backend.release(instance.handle)

        instance.state = VMLifecycleState.STOPPED
        instance.stopped_at = time.time()
        self._emit("released", instance)
        return {"state": instance.state.value, "instance_id": instance_id}

    def list_instances(self) -> list[VMInstance]:
        """Return every registered instance (any state)."""
        return list(self.instances.values())

    def observe(self) -> dict[str, Any]:
        """Return aggregate metrics for the observability surface.

        Used by daemon endpoints to surface VM sandbox state without exposing
        per-instance detail. State breakdown is keyed on the
        :class:`VMLifecycleState` value string.
        """
        total_dispatches = sum(i.metrics.dispatch_count for i in self.instances.values())
        total_findings = sum(i.metrics.last_verify_findings for i in self.instances.values())
        boots = [i.metrics.boot_ms for i in self.instances.values() if i.metrics.boot_ms > 0]
        avg_boot_ms = sum(boots) / len(boots) if boots else 0.0
        running = sum(
            1 for i in self.instances.values()
            if i.state in (VMLifecycleState.RUNNING, VMLifecycleState.EXECUTING)
        )

        breakdown: dict[str, int] = {}
        for inst in self.instances.values():
            key = inst.state.value
            breakdown[key] = breakdown.get(key, 0) + 1

        return {
            "total_instances": len(self.instances),
            "running_instances": running,
            "total_dispatches": total_dispatches,
            "total_verify_findings": total_findings,
            "avg_boot_ms": avg_boot_ms,
            "events_emitted": len(self.events),
            "state_breakdown": breakdown,
        }

    def _require(self, instance_id: str) -> VMInstance:
        instance = self.instances.get(instance_id)
        if instance is None:
            raise KeyError(f"VM instance {instance_id!r} not found")
        return instance

    def _emit(
        self,
        event: str,
        instance: VMInstance | None,
        **fields: Any,
    ) -> None:
        """Append an observability event. Instance may be None for early failures."""
        entry: dict[str, Any] = {
            "event": event,
            "instance_id": instance.instance_id if instance else None,
            "backend": instance.backend_name if instance else None,
            "ts": time.time(),
            "spec": instance.spec.agent_type if instance else None,
        }
        entry.update(fields)
        self.events.append(entry)


def _placeholder(
    instance_id: str,
    backend_name: str,
    spec: PermissionSpec,
    handle: SandboxHandle,
) -> VMInstance:
    """Build a transient VMInstance for events emitted before registration."""
    return VMInstance(
        instance_id=instance_id,
        backend_name=backend_name,
        spec=spec,
        handle=handle,
    )


__all__ = [
    "VMInstance",
    "VMLifecycleState",
    "VMMetrics",
    "VMSandboxManager",
]
