"""Live PostgreSQL + two-worker Gunicorn acceptance tests."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.azure_cost_repository import (
    AzureCostLeaseClaim,
    AzureCostReconciliationRepository,
    StaleAzureCostLeaseError,
)
from general_ludd.db.deployment_repository import DeploymentRegistryRepository
from general_ludd.db.migrations import get_alembic_config
from general_ludd.db.models import (
    AuditEventModel,
    AzureCostOutboxEventModel,
    DeploymentRecordModel,
    ProjectModel,
    TodoModel,
    TodoStatus,
)
from general_ludd.db.repository import TodoRepository
from general_ludd.events import CustomEvent, EventBus
from general_ludd.infra.azure_cost_reconciliation import (
    AzureCostLedgerState,
    AzureCostPrediction,
)
from general_ludd.infra.deployment_events import TerraformEventBridge
from general_ludd.schemas.deployment import DeploymentRecord

POSTGRES_URL = os.environ.get("POSTGRES_E2E_URL", "")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="POSTGRES_E2E_URL is required; run make test-e2e-postgres-multiworker",
)


@pytest.fixture(scope="session", autouse=True)
def migrated_postgres() -> Iterator[None]:
    command.upgrade(get_alembic_config(POSTGRES_URL), "head")
    yield


async def _claim(url: str, project_id: str) -> list[str]:
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            claimed = await TodoRepository(session, project_id=project_id).claim_runnable(
                limit=6,
                project_id=project_id,
            )
            await session.commit()
            return [todo.todo_id for todo in claimed]
    finally:
        await engine.dispose()


async def _claim_cost(
    url: str,
    prediction_id: str,
    owner: str,
    now: datetime,
) -> list[dict[str, object]]:
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            claims = await AzureCostReconciliationRepository(session).claim_due(
                owner=owner,
                now=now,
                lease_duration=timedelta(minutes=1),
                limit=1,
                prediction_id=prediction_id,
            )
            await session.commit()
            return [
                {
                    "prediction_id": claim.prediction_id,
                    "prediction_version": claim.prediction_version,
                    "owner": claim.owner,
                    "fencing_token": claim.fencing_token,
                    "expires_at": claim.expires_at.isoformat(),
                }
                for claim in claims
            ]
    finally:
        await engine.dispose()


def _claim_cost_process(
    url: str,
    prediction_id: str,
    owner: str,
    now_iso: str,
    start,
    results,
) -> None:
    try:
        if not start.wait(timeout=20):
            raise TimeoutError("cost claim start barrier timed out")
        results.put(
            {
                "owner": owner,
                "claims": asyncio.run(
                    _claim_cost(
                        url,
                        prediction_id,
                        owner,
                        datetime.fromisoformat(now_iso),
                    )
                ),
            }
        )
    except BaseException as exc:
        results.put({"owner": owner, "error": f"{type(exc).__name__}: {exc}"})


def _claim_process(url: str, project_id: str, start, results) -> None:
    try:
        if not start.wait(timeout=20):
            raise TimeoutError("claim start barrier timed out")
        results.put({"claimed": asyncio.run(_claim(url, project_id))})
    except BaseException as exc:
        results.put({"error": f"{type(exc).__name__}: {exc}"})


async def _persist_deployment(url: str, instance_id: str, working_dir: str) -> None:
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            await DeploymentRegistryRepository(session).upsert(
                DeploymentRecord(
                    instance_id=instance_id,
                    working_dir=working_dir,
                    provider="azure",
                    model_name="postgres-multiworker",
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def _persist_deployment_process(
    url: str,
    instance_id: str,
    working_dir: str,
    start,
    results,
) -> None:
    try:
        if not start.wait(timeout=20):
            raise TimeoutError("deployment persist start barrier timed out")
        asyncio.run(_persist_deployment(url, instance_id, working_dir))
        results.put({"instance_id": instance_id})
    except BaseException as exc:
        results.put({"instance_id": instance_id, "error": f"{type(exc).__name__}: {exc}"})


@pytest.mark.asyncio
async def test_postgres_fences_claims_across_worker_processes() -> None:
    project_id = "pg-e2e-claims"
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(ProjectModel(project_id=project_id, name="Postgres claims E2E"))
        session.add_all(
            TodoModel(
                todo_id=f"PG-E2E-{index:02d}",
                project_id=project_id,
                title=f"claim {index}",
                status=TodoStatus.QUEUED.value,
                priority=100,
            )
            for index in range(12)
        )
        await session.commit()

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_claim_process, args=(POSTGRES_URL, project_id, start, results))
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    payloads = [results.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("error" not in payload for payload in payloads), payloads
    claimed_sets = [set(payload["claimed"]) for payload in payloads]
    assert [len(items) for items in claimed_sets] == [6, 6]
    assert claimed_sets[0].isdisjoint(claimed_sets[1])
    assert len(claimed_sets[0] | claimed_sets[1]) == 12
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_fences_cost_reconciliation_across_worker_processes() -> None:
    prediction_id = f"pg-cost-{uuid.uuid4().hex}"
    now = datetime.now(UTC).replace(microsecond=0)
    resource_id = (
        "/subscriptions/sub-1/resourceGroups/rg-1/providers/"
        "Microsoft.App/containerApps/app-1"
    )
    prediction = AzureCostPrediction(
        prediction_id=prediction_id,
        prediction_version=1,
        todo_id=f"TODO-{uuid.uuid4().hex[:12]}",
        subscription_id="sub-1",
        resource_group="rg-1",
        resource_ids=(resource_id,),
        meter_ids=("gpu-meter",),
        region="eastus",
        sku="Consumption-GPU-NC8as-T4",
        workload="postgres-multiworker",
        predicted_cost_usd=1.0,
        conservative_ceiling_usd=1.5,
        usage_started_at=now - timedelta(hours=2),
        usage_ended_at=now - timedelta(hours=1),
    )
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await AzureCostReconciliationRepository(session).persist_prediction(
            prediction,
            not_before=now,
            now=now,
        )
        await session.commit()

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_claim_cost_process,
            args=(
                POSTGRES_URL,
                prediction_id,
                f"cost-worker-{index}",
                now.isoformat(),
                start,
                results,
            ),
        )
        for index in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    payloads = [results.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("error" not in payload for payload in payloads), payloads
    claims = [claim for payload in payloads for claim in payload["claims"]]
    assert len(claims) == 1
    assert claims[0]["fencing_token"] == 1

    stale_claim = AzureCostLeaseClaim(
        prediction_id=str(claims[0]["prediction_id"]),
        prediction_version=int(claims[0]["prediction_version"]),
        owner=str(claims[0]["owner"]),
        fencing_token=int(claims[0]["fencing_token"]),
        expires_at=datetime.fromisoformat(str(claims[0]["expires_at"])),
    )
    takeover_at = now + timedelta(minutes=2)
    async with sessions() as session:
        repository = AzureCostReconciliationRepository(session)
        takeover = (
            await repository.claim_due(
                owner="cost-worker-takeover",
                now=takeover_at,
                lease_duration=timedelta(minutes=1),
                limit=1,
                prediction_id=prediction_id,
            )
        )[0]
        await session.commit()
    assert takeover.fencing_token == 2

    async with sessions() as session:
        repository = AzureCostReconciliationRepository(session)
        with pytest.raises(StaleAzureCostLeaseError):
            await repository.advance_state(
                stale_claim,
                AzureCostLedgerState.QUERY_DUE,
                now=takeover_at,
            )
        await session.rollback()

    async with sessions() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.advance_state(
            takeover,
            AzureCostLedgerState.QUERY_DUE,
            now=takeover_at,
        )
        await session.commit()
        event_count = await session.scalar(
            select(func.count())
            .select_from(AzureCostOutboxEventModel)
            .where(
                AzureCostOutboxEventModel.prediction_id == prediction_id,
                AzureCostOutboxEventModel.event_type
                == "COST_RECONCILIATION_QUERY_DUE",
            )
        )
        assert event_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_registry_preserves_concurrent_worker_writes() -> None:
    suffix = uuid.uuid4().hex[:12]
    instance_ids = [f"pg-deploy-a-{suffix}", f"pg-deploy-b-{suffix}"]
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(
            target=_persist_deployment_process,
            args=(POSTGRES_URL, instance_id, f"/tmp/{instance_id}", start, results),
        )
        for instance_id in instance_ids
    ]
    for worker in workers:
        worker.start()
    start.set()
    payloads = [results.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=10)

    assert all(worker.exitcode == 0 for worker in workers)
    assert all("error" not in payload for payload in payloads), payloads
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        rows = list(
            (
                await session.execute(
                    select(DeploymentRecordModel).where(
                        DeploymentRecordModel.instance_id.in_(instance_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {row.instance_id for row in rows} == set(instance_ids)
    await engine.dispose()


@pytest.mark.asyncio
async def test_terraform_event_is_visible_from_another_database_connection() -> None:
    writer_engine = create_async_engine(POSTGRES_URL)
    reader_engine = create_async_engine(POSTGRES_URL)
    writer_sessions = async_sessionmaker(writer_engine, expire_on_commit=False)
    reader_sessions = async_sessionmaker(reader_engine, expire_on_commit=False)
    bus = EventBus()
    bridge = TerraformEventBridge(
        event_bus=bus,
        session_factory=writer_sessions,
        worker_id="postgres-writer-a",
    )
    bridge.start()
    event = CustomEvent(
        name="terraform_deploy_completed",
        payload={"deployment_id": "pg-e2e-deploy", "instance_id": "gpu-ready"},
        source="terraform_deployment",
    )
    bus.publish(event)
    await bus.drain()

    async with reader_sessions() as session:
        row = (
            await session.execute(
                select(AuditEventModel).where(AuditEventModel.entity_id == event.event_id)
            )
        ).scalar_one()
    assert row.event_type == "terraform_deploy_completed"
    assert json.loads(row.details)["instance_id"] == "gpu-ready"
    bridge.close()
    await writer_engine.dispose()
    await reader_engine.dispose()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _publish_terminal_event(deployment_id: str) -> int:
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bus = EventBus()
    bridge = TerraformEventBridge(
        event_bus=bus,
        session_factory=sessions,
        worker_id="postgres-e2e-publisher",
    )
    bridge.start()
    event = CustomEvent(
        name="terraform_deploy_completed",
        payload={"deployment_id": deployment_id, "instance_id": f"gpu-{deployment_id}"},
        source="terraform_deployment",
    )
    bus.publish(event)
    await bus.drain()
    async with sessions() as session:
        audit_id = await session.scalar(
            select(AuditEventModel.id).where(AuditEventModel.entity_id == event.event_id)
        )
    await bridge.aclose()
    await engine.dispose()
    assert audit_id is not None
    return int(audit_id)


async def _terminate_wakeup_connections() -> int:
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        terminated = await session.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name LIKE 'gludd-wakeup:%' "
                "AND pid <> pg_backend_pid()"
            )
        )
        await session.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name LIKE 'gludd-wakeup:%' "
                "AND pid <> pg_backend_pid()"
            )
        )
        await session.commit()
    await engine.dispose()
    return int(terminated or 0)


def test_two_worker_gunicorn_boots_and_serves_health() -> None:
    port = _free_port()
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": POSTGRES_URL,
            "GLUDD_ALLOW_UNCONFIGURED_MODEL": "true",
            "GLUDD_AUTH_PSK": "postgres-e2e-psk",
            "GLUDD_SEARX_AUTOSTART": "false",
            "GLUDD_SERVICE_DISCOVERY_ENABLED": "false",
            "GLUDD_WORKER_ID": "postgres-e2e",
            "GLUDD_PG_WAKE_RECONNECT_SECONDS": "1.0",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunicorn",
            "general_ludd.daemon:create_daemon_app()",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--workers",
            "2",
            "--bind",
            f"127.0.0.1:{port}",
            "--access-logfile",
            "-",
            "--error-logfile",
            "-",
        ],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    lines: list[str] = []

    def stream_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            print(f"GUNICORN_E2E {line}", end="", flush=True)

    reader = threading.Thread(target=stream_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + 60
    healthy = False
    try:
        while time.monotonic() < deadline and process.poll() is None:
            worker_pids = {
                match.group(1)
                for line in lines
                if (match := re.search(r"Booting worker with pid: (\d+)", line))
            }
            if len(worker_pids) >= 2:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/healthz", timeout=1
                    ) as response:
                        healthy = response.status == 200
                except urllib.error.HTTPError as exc:
                    exc.close()
                    healthy = False
                except OSError:
                    healthy = False
                if healthy:
                    break
            time.sleep(0.25)
        assert process.poll() is None, "Gunicorn exited early:\n" + "".join(lines[-40:])
        assert healthy, "two-worker daemon never became healthy:\n" + "".join(lines[-40:])

        ready_deadline = time.monotonic() + 15
        ready_pids: set[str] = set()
        while time.monotonic() < ready_deadline:
            ready_pids = {
                match.group(1)
                for line in lines
                if (
                    match := re.search(
                        r"Terraform PostgreSQL wake listener ready .* pid=(\d+)",
                        line,
                    )
                )
            }
            if len(ready_pids) >= 2:
                break
            time.sleep(0.05)
        assert len(ready_pids) == 2, "both worker listeners were not ready:\n" + "".join(lines[-60:])

        published_at = time.monotonic()
        audit_id = asyncio.run(_publish_terminal_event(f"gunicorn-notify-{uuid.uuid4().hex}"))
        notify_deadline = time.monotonic() + 5
        notified_pids: set[str] = set()
        while time.monotonic() < notify_deadline:
            notified_pids = {
                match.group(1)
                for line in lines
                if (
                    match := re.search(
                        rf"wake notification received .* pid=(\d+) audit_event_id={audit_id}\b",
                        line,
                    )
                )
            }
            if notified_pids == ready_pids:
                break
            time.sleep(0.05)
        assert notified_pids == ready_pids, "event did not promptly wake both workers:\n" + "".join(lines[-80:])
        assert time.monotonic() - published_at < 5

        reconnect_marker = len(lines)
        assert asyncio.run(_terminate_wakeup_connections()) == 2
        reconnect_deadline = time.monotonic() + 5
        reconnect_pids: set[str] = set()
        while time.monotonic() < reconnect_deadline:
            reconnect_pids = {
                match.group(1)
                for line in lines[reconnect_marker:]
                if (
                    match := re.search(
                        r"wake listener reconnecting .* pid=(\d+)",
                        line,
                    )
                )
            }
            if reconnect_pids == ready_pids:
                break
            time.sleep(0.05)
        assert reconnect_pids == ready_pids, "listeners did not detect disconnect:\n" + "".join(lines[-80:])

        missed_audit_id = asyncio.run(
            _publish_terminal_event(f"gunicorn-catchup-{uuid.uuid4().hex}")
        )
        catchup_deadline = time.monotonic() + 8
        caught_up_pids: set[str] = set()
        while time.monotonic() < catchup_deadline:
            caught_up_pids = {
                match.group(1)
                for line in lines[reconnect_marker:]
                if (
                    match := re.search(
                        rf"wake catch-up .* pid=(\d+) .* latest={missed_audit_id}\b",
                        line,
                    )
                )
            }
            if caught_up_pids == ready_pids:
                break
            time.sleep(0.05)
        assert caught_up_pids == ready_pids, "missed event was not caught up:\n" + "".join(lines[-100:])
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)
        reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()

    assert process.returncode == 0
    output = "".join(lines)
    closed_pids = {
        match.group(1)
        for line in lines
        if (
            match := re.search(
                r"Terraform PostgreSQL wake listener closed .* pid=(\d+)",
                line,
            )
        )
    }
    assert closed_pids == ready_pids
    for unexpected in (
        "SearXNG process exited prematurely",
        "searxng installed but still not importable",
        "No model_profiles loaded",
        "_adaptive_router was still _STARTUP_UNSET",
    ):
        assert unexpected not in output
