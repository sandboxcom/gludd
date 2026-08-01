"""Production dispatch admission must be attested before agent execution."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.config.user_config import VmSandboxConfig
from general_ludd.db.models import Base, ProjectModel
from general_ludd.event_loop.loop import EventLoop
from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.policy.profiles import resolve_sandbox_profile
from general_ludd.security.sandboxes import SandboxHandle
from general_ludd.security.sandboxes.attestation import (
    AttestationIntegrityError,
    DurableSandboxAttestationStore,
    RuntimeSandboxObservation,
    SandboxAttestationEvent,
)
from general_ludd.security.sandboxes.dispatch import (
    DurableSandboxDispatchGuard,
    SandboxDispatchDenied,
    SandboxDispatchIdentity,
)


def _observation(
    backend: str = "firecracker",
    *,
    application_kernel: bool = True,
) -> RuntimeSandboxObservation:
    guarantees = {
        "filesystem-isolation",
        "network-policy",
        "no-new-privileges",
        "process-identity",
        "resource-limits",
        "syscall-filter",
    }
    if application_kernel:
        guarantees.add("application-kernel")
    return RuntimeSandboxObservation(
        applied=True,
        backend=backend,
        backend_version="test-runtime-1",
        image_digest="sha256:" + "b" * 64,
        guarantees=guarantees,
        namespaces={
            "user": "user:[11]",
            "mount": "mnt:[12]",
            "pid": "pid:[13]",
            "ipc": "ipc:[14]",
            "network": "net:[15]",
            "uts": "uts:[16]",
        },
        vm_identity="vm-test-1" if backend == "firecracker" else None,
        cgroup="/gludd/tenant-a/todo-1",
        filesystem_mounts=("source:ro", "workspace:rw"),
        network_policy="deny-all-v1",
        uid=100001,
        gid=100001,
        syscall_profile="untrusted-code-v1",
    )


def _backend(observation: RuntimeSandboxObservation) -> type:
    class ObservedBackend:
        name = observation.backend
        released: ClassVar[list[SandboxHandle]] = []

        @staticmethod
        def apply(spec: PermissionSpec, target: object) -> SandboxHandle:
            del spec, target
            return SandboxHandle(backend=observation.backend, token="sandbox-token")

        @staticmethod
        def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[object]:
            del spec, handle
            return []

        @staticmethod
        def observe_runtime(
            spec: PermissionSpec,
            handle: SandboxHandle,
            resolved: object,
        ) -> RuntimeSandboxObservation:
            del spec, handle, resolved
            return observation

        @classmethod
        def release(cls, handle: SandboxHandle) -> None:
            cls.released.append(handle)

    return ObservedBackend


async def _database():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="tenant-a", name="Tenant A"))
        await session.commit()
    return engine, sessions


def _todo(todo_id: str = "TODO-ATTEST") -> SimpleNamespace:
    return SimpleNamespace(
        todo_id=todo_id,
        project_id="tenant-a",
        assigned_agent="build-agent",
        queue="core",
        work_type="code",
    )


def _loop(
    sessions: async_sessionmaker,
    store: object,
    *,
    profile: str,
) -> tuple[EventLoop, MagicMock]:
    sandbox_executor = MagicMock()
    sandbox_executor.execute.return_value = subprocess.CompletedProcess(
        args=["dispatch"], returncode=0, stdout="", stderr=""
    )
    loop = EventLoop(
        session=sessions,
        sandbox_executor=sandbox_executor,
        sandbox_attestation_store=store,
        sandbox_profile=resolve_sandbox_profile(profile),
        config={"repo_root": "."},
    )
    loop._resolve_permission_spec = MagicMock(
        return_value=PermissionSpec(agent_type="build-agent")
    )
    return loop, sandbox_executor


@pytest.mark.asyncio
async def test_production_dispatch_persists_tenant_allow_before_execution() -> None:
    engine, sessions = await _database()
    writer = DurableSandboxAttestationStore(sessions)
    reader = DurableSandboxAttestationStore(sessions)
    loop, sandbox_executor = _loop(sessions, writer, profile="locked")
    visible_during_execution: list[object] = []

    async def execute_after_attestation(
        todo: SimpleNamespace, **kwargs: object
    ) -> None:
        del kwargs
        visible_during_execution.extend(
            await reader.list_events(
                project_id="tenant-a",
                tenant_id="tenant-a",
                work_item_id=todo.todo_id,
            )
        )

    loop._dispatch_execute_job = AsyncMock(side_effect=execute_after_attestation)
    backend = _backend(_observation())

    with patch("general_ludd.security.sandboxes.detect.auto", return_value=backend):
        await loop._dispatch_execute_job_isolated(_todo())

    assert [event.decision for event in visible_during_execution] == ["allow"]
    assert visible_during_execution[0].tenant_id == "tenant-a"
    assert visible_during_execution[0].sequence > 0
    sandbox_executor.execute.assert_called_once()
    assert len(backend.released) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_configurable_profile_changes_weak_backend_admission() -> None:
    engine, sessions = await _database()
    store = DurableSandboxAttestationStore(sessions)
    observation = _observation("bubblewrap", application_kernel=False)
    backend = _backend(observation)
    locked, locked_executor = _loop(sessions, store, profile="locked")
    standard, standard_executor = _loop(sessions, store, profile="standard")
    locked._dispatch_execute_job = AsyncMock()
    standard._dispatch_execute_job = AsyncMock()

    with patch("general_ludd.security.sandboxes.detect.auto", return_value=backend):
        with pytest.raises(SandboxDispatchDenied, match="application-kernel"):
            await locked._dispatch_execute_job_isolated(_todo("TODO-LOCKED"))
        await standard._dispatch_execute_job_isolated(_todo("TODO-STANDARD"))

    locked_events = await store.list_events(
        project_id="tenant-a", tenant_id="tenant-a", work_item_id="TODO-LOCKED"
    )
    standard_events = await store.list_events(
        project_id="tenant-a", tenant_id="tenant-a", work_item_id="TODO-STANDARD"
    )
    assert [(event.requested_profile, event.decision) for event in locked_events] == [
        ("locked", "deny")
    ]
    assert [(event.requested_profile, event.decision) for event in standard_events] == [
        ("standard", "allow")
    ]
    locked_executor.execute.assert_not_called()
    standard_executor.execute.assert_called_once()
    locked._dispatch_execute_job.assert_not_called()
    standard._dispatch_execute_job.assert_awaited_once()
    await engine.dispose()


@pytest.mark.asyncio
async def test_missing_backend_is_durably_denied_before_execution() -> None:
    engine, sessions = await _database()
    store = DurableSandboxAttestationStore(sessions)
    loop, sandbox_executor = _loop(sessions, store, profile="locked")
    loop._dispatch_execute_job = AsyncMock()

    with (
        patch("general_ludd.security.sandboxes.detect.auto", return_value=None),
        pytest.raises(SandboxDispatchDenied, match="backend-applied"),
    ):
        await loop._dispatch_execute_job_isolated(_todo("TODO-NO-BACKEND"))

    events = await store.list_events(
        project_id="tenant-a",
        tenant_id="tenant-a",
        work_item_id="TODO-NO-BACKEND",
    )
    assert [(event.decision, event.reason_code) for event in events] == [
        ("deny", "backend-not-applied")
    ]
    sandbox_executor.execute.assert_not_called()
    loop._dispatch_execute_job.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
async def test_backend_without_independent_observer_is_durably_denied() -> None:
    engine, sessions = await _database()
    store = DurableSandboxAttestationStore(sessions)
    loop, sandbox_executor = _loop(sessions, store, profile="standard")
    loop._dispatch_execute_job = AsyncMock()
    backend = _backend(_observation("bubblewrap", application_kernel=False))
    delattr(backend, "observe_runtime")

    with (
        patch("general_ludd.security.sandboxes.detect.auto", return_value=backend),
        pytest.raises(SandboxDispatchDenied, match="backend-applied"),
    ):
        await loop._dispatch_execute_job_isolated(_todo("TODO-NO-PROBE"))

    events = await store.list_events(
        project_id="tenant-a", tenant_id="tenant-a", work_item_id="TODO-NO-PROBE"
    )
    assert events[0].decision == "deny"
    assert len(backend.released) == 1
    sandbox_executor.execute.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
async def test_backend_verification_failure_overrides_claimed_allow() -> None:
    engine, sessions = await _database()
    store = DurableSandboxAttestationStore(sessions)
    loop, sandbox_executor = _loop(sessions, store, profile="locked")
    loop._dispatch_execute_job = AsyncMock()
    backend = _backend(_observation())
    backend.verify = staticmethod(
        lambda spec, handle: [SimpleNamespace(severity="fail", message="drift")]
    )

    with (
        patch("general_ludd.security.sandboxes.detect.auto", return_value=backend),
        pytest.raises(SandboxDispatchDenied, match="backend-applied"),
    ):
        await loop._dispatch_execute_job_isolated(_todo("TODO-VERIFY-FAIL"))

    events = await store.list_events(
        project_id="tenant-a",
        tenant_id="tenant-a",
        work_item_id="TODO-VERIFY-FAIL",
    )
    assert events[0].observation.applied is False
    assert len(backend.released) == 1
    sandbox_executor.execute.assert_not_called()
    await engine.dispose()


@pytest.mark.asyncio
async def test_attested_boundary_without_sandbox_executor_fails_closed() -> None:
    engine, sessions = await _database()
    store = DurableSandboxAttestationStore(sessions)
    loop = EventLoop(
        session=sessions,
        sandbox_executor=None,
        sandbox_attestation_store=store,
        sandbox_profile=resolve_sandbox_profile("locked"),
        config={"repo_root": "."},
    )
    loop._resolve_permission_spec = MagicMock(
        return_value=PermissionSpec(agent_type="build-agent")
    )
    loop._dispatch_execute_job = AsyncMock()
    backend = _backend(_observation())

    with (
        patch("general_ludd.security.sandboxes.detect.auto", return_value=backend),
        pytest.raises(SandboxDispatchDenied, match="backend-applied"),
    ):
        await loop._dispatch_execute_job_isolated(_todo("TODO-NO-EXECUTOR"))

    loop._dispatch_execute_job.assert_not_called()
    events = await store.list_events(
        project_id="tenant-a",
        tenant_id="tenant-a",
        work_item_id="TODO-NO-EXECUTOR",
    )
    assert events[0].decision == "deny"
    await engine.dispose()


@pytest.mark.asyncio
async def test_attestation_store_failure_fails_closed() -> None:
    class BrokenStore:
        async def append(self, event: object) -> object:
            del event
            raise RuntimeError("audit database unavailable")

    engine, sessions = await _database()
    loop, sandbox_executor = _loop(sessions, BrokenStore(), profile="standard")
    loop._dispatch_execute_job = AsyncMock()
    backend = _backend(_observation("bubblewrap", application_kernel=False))

    with (
        patch("general_ludd.security.sandboxes.detect.auto", return_value=backend),
        pytest.raises(RuntimeError, match="audit database unavailable"),
    ):
        await loop._dispatch_execute_job_isolated(_todo("TODO-STORE-DOWN"))

    sandbox_executor.execute.assert_not_called()
    loop._dispatch_execute_job.assert_not_called()
    assert len(backend.released) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_guard_rejects_store_that_did_not_durably_seal_event() -> None:
    class UnsealedStore:
        async def append(self, event: object) -> object:
            return event

    resolved = resolve_sandbox_profile("standard")
    guard = DurableSandboxDispatchGuard(
        resolved=resolved,
        store=UnsealedStore(),
    )
    identity = SandboxDispatchIdentity(
        project_id="tenant-a",
        work_item_id="TODO-UNSEALED",
        agent_id="event-loop",
        tenant_id="tenant-a",
        correlation_id="sandbox:TODO-UNSEALED",
    )

    assert guard.resolved is resolved
    with pytest.raises(AttestationIntegrityError, match="unsealed"):
        await guard.attest(
            identity,
            _observation("bubblewrap", application_kernel=False),
        )


@pytest.mark.asyncio
async def test_guard_rejects_integrity_valid_event_swapped_by_store() -> None:
    class SwappingStore:
        async def append(
            self,
            event: SandboxAttestationEvent,
        ) -> SandboxAttestationEvent:
            return event.model_copy(update={"work_item_id": "TODO-OTHER"}).seal(41)

    guard = DurableSandboxDispatchGuard(
        resolved=resolve_sandbox_profile("standard"),
        store=SwappingStore(),
    )
    identity = SandboxDispatchIdentity(
        project_id="tenant-a",
        work_item_id="TODO-EXPECTED",
        agent_id="event-loop",
        tenant_id="tenant-a",
        correlation_id="sandbox:TODO-EXPECTED",
    )

    with pytest.raises(AttestationIntegrityError, match="different attestation"):
        await guard.attest(
            identity,
            _observation("bubblewrap", application_kernel=False),
        )


def test_vm_sandbox_config_selects_only_valid_resolved_profiles() -> None:
    configured = VmSandboxConfig(profile="standard")

    assert resolve_sandbox_profile(configured.profile).requested_profile == "standard"
    with pytest.raises(ValidationError, match="profile"):
        VmSandboxConfig(profile="permissive")
