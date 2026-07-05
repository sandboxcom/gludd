"""Tests for Slurm job lifecycle persistence (DB records) and shutdown scancel hook."""
from __future__ import annotations

import os as _os
from unittest.mock import patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import SlurmJobRepository
from general_ludd.db.session import run_wal_pragmas


@pytest_asyncio.fixture
async def session(tmp_path):
    db = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    run_wal_pragmas(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _job_data(**overrides):
    """Minimal valid job data for create()."""
    return {
        "job_id": "12345",
        "deployment_id": "deploy-01",
        "account": "myaccount",
        "qos": "normal",
        "partition": "gpu",
        "gpu_count": 1,
        "gpu_type": "a100",
        "max_hours": 2.0,
        "max_cost_usd": 10.0,
        "hourly_rate_usd": 5.0,
        "cost_incurred": 0.0,
        "status": "submitted",
        "daemon_pid": _os.getpid(),
        **overrides,
    }


class TestSlurmJobRepository:
    async def test_create_job_persists(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        row = await repo.create(_job_data())
        assert row.id is not None
        assert row.job_id == "12345"
        assert row.status == "submitted"

    async def test_get_by_job_id_found(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data())
        row = await repo.get_by_job_id("12345")
        assert row is not None
        assert row.job_id == "12345"

    async def test_get_by_job_id_not_found(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        row = await repo.get_by_job_id("nonexistent")
        assert row is None

    async def test_update_status_to_completed(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(status="running"))
        updated = await repo.update_status("12345", "completed", cost_incurred=8.50)
        assert updated is True
        row = await repo.get_by_job_id("12345")
        assert row is not None
        assert row.status == "completed"
        assert row.cost_incurred == 8.50
        assert row.completed_at is not None

    async def test_update_status_nonexistent(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        updated = await repo.update_status("no-such-job", "cancelled")
        assert updated is False

    async def test_update_status_sets_completed_at_for_terminal(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(status="running"))
        for status in ("completed", "failed", "cancelled"):
            await repo.create(_job_data(job_id=f"9{status}", status="running"))
            await repo.update_status(f"9{status}", status)
            row = await repo.get_by_job_id(f"9{status}")
            assert row is not None
            assert row.completed_at is not None

    async def test_list_active_returns_submitted_and_running(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(job_id="111", status="submitted"))
        await repo.create(_job_data(job_id="222", status="running"))
        await repo.create(_job_data(job_id="333", status="completed"))
        jobs = await repo.list_active()
        assert len(jobs) == 2
        ids = {j.job_id for j in jobs}
        assert ids == {"111", "222"}

    async def test_list_active_filters_by_daemon_pid(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(job_id="aaa", status="running", daemon_pid=42))
        await repo.create(_job_data(job_id="bbb", status="running", daemon_pid=_os.getpid()))
        jobs = await repo.list_active(daemon_pid=_os.getpid())
        assert len(jobs) == 1
        assert jobs[0].job_id == "bbb"

    async def test_list_by_deployment(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(job_id="d1", deployment_id="dep-a"))
        await repo.create(_job_data(job_id="d2", deployment_id="dep-b"))
        await repo.create(_job_data(job_id="d3", deployment_id="dep-a"))
        jobs = await repo.list_by_deployment("dep-a")
        assert len(jobs) == 2
        ids = {j.job_id for j in jobs}
        assert ids == {"d1", "d3"}

    async def test_list_orphans(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(job_id="mine", status="running", daemon_pid=_os.getpid()))
        await repo.create(_job_data(job_id="orphan", status="running", daemon_pid=99999))
        await repo.create(_job_data(job_id="completed-orphan", status="completed", daemon_pid=99999))
        orphans = await repo.list_orphans(_os.getpid())
        assert len(orphans) == 1
        assert orphans[0].job_id == "orphan"


class TestShutdownScancelHook:
    def test_adapter_cancel_calls_scancel(self):
        """SlurmAdapter.cancel() invokes subprocess.run with scancel."""
        mock_result = _make_mock_result(0, "")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            from general_ludd.infra.slurm import SlurmAdapter
            adapter = SlurmAdapter()
            adapter.cancel("42")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "scancel"

    async def test_list_active_filters_by_daemon_pid_for_shutdown(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        my_pid = _os.getpid()
        await repo.create(_job_data(job_id="keep", status="running", daemon_pid=my_pid))
        await repo.create(_job_data(job_id="other", status="running", daemon_pid=99999))
        await repo.create(_job_data(job_id="done", status="completed", daemon_pid=my_pid))
        mine = await repo.list_active(daemon_pid=my_pid)
        assert len(mine) == 1
        assert mine[0].job_id == "keep"

    def test_shutdown_status_update_to_cancelled(self):
        """update_status('cancelled') sets completed_at."""
        import asyncio as _asyncio

        async def _run():
            from general_ludd.db.session import ensure_tables, init_engine_from_config
            engine = init_engine_from_config({})
            await ensure_tables(engine)
            from general_ludd.db.session import create_async_session_factory
            sf = create_async_session_factory(engine)
            async with sf() as sess:
                repo = SlurmJobRepository(sess)
                await repo.create(_job_data(job_id="99", status="running"))
                await repo.update_status("99", "cancelled")
                row = await repo.get_by_job_id("99")
                assert row is not None
                assert row.status == "cancelled"
                assert row.completed_at is not None
            await engine.dispose()

        _asyncio.run(_run())


class TestOrphanDetection:
    async def test_orphan_detection_on_startup(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        my_pid = _os.getpid()
        await repo.create(_job_data(job_id="current", status="running", daemon_pid=my_pid))
        await repo.create(_job_data(job_id="from-before", status="running", daemon_pid=12345))
        orphans = await repo.list_orphans(my_pid)
        assert len(orphans) == 1
        assert orphans[0].job_id == "from-before"

    async def test_no_orphans_when_all_running_are_mine(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        await repo.create(_job_data(job_id="j1", status="running", daemon_pid=_os.getpid()))
        await repo.create(_job_data(job_id="j2", status="submitted", daemon_pid=_os.getpid()))
        orphans = await repo.list_orphans(_os.getpid())
        assert len(orphans) == 0

    async def test_empty_orphan_list_on_fresh_db(self, session: AsyncSession):
        repo = SlurmJobRepository(session)
        orphans = await repo.list_orphans(_os.getpid())
        assert orphans == []


def _make_mock_result(returncode, stdout):
    from unittest.mock import MagicMock
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    return mock_result
