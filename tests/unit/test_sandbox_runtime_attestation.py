"""Runtime attestation must deny missing guarantees and persist before return."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import AuditEventModel, Base, ProjectModel
from general_ludd.security.policy.profiles import resolve_sandbox_profile
from general_ludd.security.sandboxes.attestation import (
    AttestationEventTooLargeError,
    AttestationIntegrityError,
    DurableSandboxAttestationStore,
    ResourceSnapshot,
    RuntimeSandboxObservation,
    SandboxAttestationEvent,
    evaluate_runtime_attestation,
)


def _observation(*, applied: bool = True, guarantees: set[str] | None = None):
    return RuntimeSandboxObservation(
        applied=applied,
        backend="firecracker",
        backend_version="1.12.0",
        image_digest="sha256:" + "a" * 64,
        guarantees=guarantees
        or {
            "application-kernel",
            "filesystem-isolation",
            "network-policy",
            "no-new-privileges",
            "process-identity",
            "resource-limits",
            "syscall-filter",
        },
        namespaces={"mount": "mnt:[4026533000]", "network": "net:[4026533001]"},
        vm_identity="vm-fc-01",
        cgroup="/gludd/tenant-a/work-1",
        filesystem_mounts=("source:ro", "workspace:rw"),
        network_policy="deny-all-v1",
        uid=100001,
        gid=100001,
        syscall_profile="untrusted-code-v1",
    )


def _event(*, tenant_id: str = "tenant-a", work_item_id: str = "work-1"):
    return evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(),
        observation=_observation(),
        project_id="proj-security",
        work_item_id=work_item_id,
        agent_id="agent-sandbox",
        tenant_id=tenant_id,
        correlation_id="corr-security",
    )


def test_complete_observed_state_allows_locked_workload() -> None:
    event = _event()

    assert event.decision == "allow"
    assert event.reason_code == "attestation-verified"
    assert event.missing_guarantees == ()
    assert event.policy_hash == resolve_sandbox_profile().policy_hash
    assert event.effective_backend == "firecracker"


def test_integrity_digest_is_canonical_across_guarantee_input_order() -> None:
    draft = _event(work_item_id="canonical-order")
    payload = draft.model_dump(mode="python")
    observation = payload["observation"]
    assert isinstance(observation, dict)
    observation["guarantees"] = list(reversed(sorted(draft.observation.guarantees)))
    reconstructed = SandboxAttestationEvent.model_validate(payload)

    original = draft.seal(17)
    reordered = reconstructed.seal(17)

    assert original.integrity_sha256 == reordered.integrity_sha256
    assert reordered.verify_integrity() is True


def test_missing_or_unapplied_guarantee_denies_before_dispatch() -> None:
    missing_network = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(),
        observation=_observation(
            guarantees={
                "application-kernel",
                "filesystem-isolation",
                "no-new-privileges",
                "process-identity",
                "resource-limits",
                "syscall-filter",
            }
        ),
        project_id="proj-security",
        work_item_id="work-1",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )
    unapplied = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(),
        observation=_observation(applied=False),
        project_id="proj-security",
        work_item_id="work-2",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )

    assert missing_network.decision == "deny"
    assert missing_network.missing_guarantees == ("network-policy",)
    assert unapplied.decision == "deny"
    assert "backend-applied" in unapplied.missing_guarantees


def test_unpreferred_backend_is_a_durable_denial_reason() -> None:
    observation = _observation().model_copy(update={"backend": "bubblewrap"})
    event = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(
            administrator={"backend": {"preference": ["firecracker"]}}
        ),
        observation=observation,
        project_id="proj-security",
        work_item_id="work-1",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )

    assert event.decision == "deny"
    assert event.reason_code == "backend-not-approved"
    assert event.missing_guarantees == ("approved-backend",)


@pytest.mark.asyncio
async def test_store_commits_monotonic_integrity_checked_events_for_other_workers() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="proj-security", name="security"))
        await session.commit()
    writer = DurableSandboxAttestationStore(sessions)
    other_worker = DurableSandboxAttestationStore(sessions)

    first = await writer.append(_event(work_item_id="work-live"))
    second = await writer.append(_event(work_item_id="work-live"))
    visible = await other_worker.list_events(
        project_id="proj-security",
        tenant_id="tenant-a",
        work_item_id="work-live",
        after_sequence=first.sequence,
    )

    assert first.sequence > 0
    assert second.sequence > first.sequence
    assert len(first.integrity_sha256) == 64
    assert visible == [second]
    async with sessions() as session:
        rows = list((await session.execute(select(AuditEventModel))).scalars())
    assert len(rows) == 2
    assert all(row.event_type == "sandbox_runtime_attestation" for row in rows)
    assert all("secret" not in row.details.casefold() for row in rows)
    await engine.dispose()


@pytest.mark.asyncio
async def test_store_partitions_queries_by_tenant_even_when_work_ids_collide() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="proj-security", name="security"))
        await session.commit()
    store = DurableSandboxAttestationStore(sessions)
    await store.append(_event(tenant_id="tenant-a", work_item_id="same-id"))
    await store.append(_event(tenant_id="tenant-b", work_item_id="same-id"))

    tenant_a = await store.list_events(
        project_id="proj-security", tenant_id="tenant-a", work_item_id="same-id"
    )

    assert [event.tenant_id for event in tenant_a] == ["tenant-a"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_store_detects_tampering_in_durable_payload() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="proj-security", name="security"))
        await session.commit()
    store = DurableSandboxAttestationStore(sessions)
    await store.append(_event(work_item_id="tampered"))
    async with sessions() as session:
        row = (
            await session.execute(
                select(AuditEventModel).where(
                    AuditEventModel.event_type == "sandbox_runtime_attestation"
                )
            )
        ).scalar_one()
        payload = json.loads(row.details)
        payload["decision"] = "deny"
        row.details = json.dumps(payload, sort_keys=True)
        await session.commit()

    with pytest.raises(AttestationIntegrityError):
        await store.list_events(
            project_id="proj-security", tenant_id="tenant-a", work_item_id="tampered"
        )
    await engine.dispose()


def test_attestation_models_reject_raw_fields_and_unbounded_resources() -> None:
    with pytest.raises(ValidationError):
        ResourceSnapshot(memory_bytes=-1)
    with pytest.raises(ValidationError, match="raw_prompt"):
        RuntimeSandboxObservation.model_validate(
            {**_observation().model_dump(), "raw_prompt": "do not persist me"}
        )
    with pytest.raises(ValidationError, match="duplicate"):
        RuntimeSandboxObservation.model_validate(
            {
                **_observation().model_dump(),
                "filesystem_mounts": ["source:ro", "source:ro"],
            }
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        SandboxAttestationEvent.model_validate(
            {**_event().model_dump(), "timestamp": datetime(2026, 8, 1)}
        )
    with pytest.raises(ValueError, match="positive"):
        _event().seal(0)


def test_backend_strength_virtual_machine_and_syscall_profile_are_observed() -> None:
    weak = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(
            administrator={"backend": {"preference": ["bubblewrap"]}}
        ),
        observation=_observation().model_copy(update={"backend": "bubblewrap"}),
        project_id="proj-security",
        work_item_id="weak",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )
    syscall_mismatch = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(),
        observation=_observation().model_copy(update={"syscall_profile": "other-v1"}),
        project_id="proj-security",
        work_item_id="syscall",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )
    virtual_machine = evaluate_runtime_attestation(
        resolved=resolve_sandbox_profile(
            administrator={"backend": {"minimum_strength": "virtual-machine"}}
        ),
        observation=_observation(),
        project_id="proj-security",
        work_item_id="virtual",
        agent_id="agent-sandbox",
        tenant_id="tenant-a",
        correlation_id="corr-security",
    )

    assert weak.reason_code == "backend-strength-insufficient"
    assert syscall_mismatch.reason_code == "syscall-profile-mismatch"
    assert "syscall-profile-match" in syscall_mismatch.missing_guarantees
    assert virtual_machine.missing_guarantees == ("virtual-machine",)


@pytest.mark.asyncio
async def test_store_rejects_replay_oversize_and_invalid_query_bounds() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="proj-security", name="security"))
        await session.commit()
    store = DurableSandboxAttestationStore(sessions)
    sealed = await store.append(_event(work_item_id="guards"))

    with pytest.raises(ValueError, match="already"):
        await store.append(sealed)
    with pytest.raises(ValueError, match="after_sequence"):
        await store.list_events(
            project_id="proj-security",
            tenant_id="tenant-a",
            work_item_id="guards",
            after_sequence=-1,
        )
    with pytest.raises(ValueError, match="limit"):
        await store.list_events(
            project_id="proj-security",
            tenant_id="tenant-a",
            work_item_id="guards",
            limit=0,
        )
    with pytest.raises(ValueError, match="database"):
        DurableSandboxAttestationStore(sessions, max_event_bytes=0)
    too_small = DurableSandboxAttestationStore(sessions, max_event_bytes=1)
    with pytest.raises(AttestationEventTooLargeError):
        await too_small.append(_event(work_item_id="oversize"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_store_rejects_malformed_durable_json() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id="proj-security", name="security"))
        await session.commit()
    store = DurableSandboxAttestationStore(sessions)
    await store.append(_event(work_item_id="malformed"))
    async with sessions() as session:
        row = (
            await session.execute(
                select(AuditEventModel).where(
                    AuditEventModel.event_type == "sandbox_runtime_attestation"
                )
            )
        ).scalar_one()
        row.details = "{"
        await session.commit()

    with pytest.raises(AttestationIntegrityError, match="not valid"):
        await store.list_events(
            project_id="proj-security", tenant_id="tenant-a", work_item_id="malformed"
        )
    await engine.dispose()
