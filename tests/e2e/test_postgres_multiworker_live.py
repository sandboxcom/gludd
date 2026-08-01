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
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.migrations import get_alembic_config
from general_ludd.db.models import AuditEventModel, ProjectModel, TodoModel, TodoStatus
from general_ludd.db.repository import TodoRepository
from general_ludd.events import CustomEvent, EventBus
from general_ludd.infra.deployment_events import TerraformEventBridge

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


def _claim_process(url: str, project_id: str, start, results) -> None:
    try:
        if not start.wait(timeout=20):
            raise TimeoutError("claim start barrier timed out")
        results.put({"claimed": asyncio.run(_claim(url, project_id))})
    except BaseException as exc:
        results.put({"error": f"{type(exc).__name__}: {exc}"})


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


def test_two_worker_gunicorn_boots_and_serves_health() -> None:
    port = _free_port()
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": POSTGRES_URL,
            "GLUDD_ALLOW_UNCONFIGURED_MODEL": "true",
            "GLUDD_PSK": "postgres-e2e-psk",
            "GLUDD_SEARX_AUTOSTART": "false",
            "GLUDD_SERVICE_DISCOVERY_ENABLED": "false",
            "GLUDD_WORKER_ID": "postgres-e2e",
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
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)
        reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()

    assert process.returncode == 0
    output = "".join(lines)
    for unexpected in (
        "SearXNG process exited prematurely",
        "searxng installed but still not importable",
        "No model_profiles loaded",
        "_adaptive_router was still _STARTUP_UNSET",
    ):
        assert unexpected not in output
