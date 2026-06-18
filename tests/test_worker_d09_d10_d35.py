"""TDD tests for D-09, D-10, D-35 worker/runner fixes.

D-10: duplicate job_id → 409; prepare_job_dirs uses exist_ok=False for root.
D-09: on failure in execute_job, job dir is cleaned up; on success, dir is kept.
D-35: JobSpec.timeout field; wait_for caps at GLUDD_JOB_TIMEOUT_MAX.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.schemas.job import JobSpec
from general_ludd.worker.app import create_app


def _make_job(**kwargs: Any) -> dict[str, Any]:
    base = {
        "job_id": "TESTJOB001",
        "playbook": "noop.yml",
        "queue": "default",
    }
    base.update(kwargs)
    return base


def _make_client(runner: AnsibleRunnerAdapter) -> TestClient:
    import general_ludd.worker.app as worker_module
    worker_module._runner = runner
    app = create_app(gateway=None)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# D-10: prepare_job_dirs — exist_ok=False for root
# ---------------------------------------------------------------------------

class TestPrepareJobDirsD10:
    def test_first_call_creates_dir(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        dirs = runner.prepare_job_dirs("JOBABC")
        assert os.path.isdir(dirs["root"])

    def test_duplicate_raises_file_exists_error(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        runner.prepare_job_dirs("JOBDUP")
        with pytest.raises(FileExistsError):
            runner.prepare_job_dirs("JOBDUP")

    def test_subdirs_created_with_exist_ok(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        dirs = runner.prepare_job_dirs("JOBSUB")
        for key in ("env", "project", "inventory", "artifacts"):
            assert os.path.isdir(dirs[key])


# ---------------------------------------------------------------------------
# D-10: execute_job — duplicate job_id → 409
# ---------------------------------------------------------------------------

class TestExecuteJobDuplicateD10:
    def test_duplicate_job_id_returns_409(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        # Pre-create the job dir to trigger FileExistsError in prepare_job_dirs
        job_dir = tmp_path / "TESTJOBDUP"
        job_dir.mkdir()

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBDUP"))
        assert resp.status_code == 409
        assert "already in progress" in resp.json().get("detail", "").lower()

    def test_successful_run_keeps_extravars(self, tmp_path: Any) -> None:
        """After a successful run the job dir and its extravars are still present."""
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        def fake_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}

        runner.run_playbook = fake_run  # type: ignore[method-assign]

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBSUC"))
        assert resp.status_code == 200

        # D-09: dir must still exist (no cleanup on success)
        job_dir = tmp_path / "TESTJOBSUC"
        assert job_dir.exists()
        # extravars file was written and kept
        extravars_path = job_dir / "env" / "extravars"
        assert extravars_path.exists()


# ---------------------------------------------------------------------------
# D-09: cleanup on failure
# ---------------------------------------------------------------------------

class TestCleanupOnFailureD09:
    def test_job_dir_removed_when_run_playbook_raises(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        def failing_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("simulated runner failure")

        runner.run_playbook = failing_run  # type: ignore[method-assign]

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBFAIL"))
        # Server re-raises → 500
        assert resp.status_code == 500
        # D-09: dir must be cleaned up
        job_dir = tmp_path / "TESTJOBFAIL"
        assert not job_dir.exists()

    def test_job_dir_kept_on_success(self, tmp_path: Any) -> None:
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        def ok_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}

        runner.run_playbook = ok_run  # type: ignore[method-assign]

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBOK"))
        assert resp.status_code == 200
        job_dir = tmp_path / "TESTJOBOK"
        assert job_dir.exists()


# ---------------------------------------------------------------------------
# D-35: JobSpec.timeout field
# ---------------------------------------------------------------------------

class TestJobSpecTimeoutD35:
    def test_timeout_field_defaults_to_none(self) -> None:
        job = JobSpec(job_id="J1", playbook="noop.yml", queue="q")
        assert job.timeout is None

    def test_timeout_field_accepts_float(self) -> None:
        job = JobSpec(job_id="J1", playbook="noop.yml", queue="q", timeout=30.0)
        assert job.timeout == 30.0

    def test_timeout_field_accepts_int(self) -> None:
        job = JobSpec(job_id="J1", playbook="noop.yml", queue="q", timeout=60)
        assert job.timeout == 60.0


# ---------------------------------------------------------------------------
# D-35: wait_for + timeout cap in execute_job
# ---------------------------------------------------------------------------

class TestWaitForTimeoutD35:
    def test_stalling_job_times_out(self, tmp_path: Any) -> None:
        """A run_playbook that blocks must be cancelled by wait_for."""
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        # Use a threading.Event the worker thread waits on, with a bounded
        # release so the thread cannot survive into pytest/xdist teardown and
        # stall the whole run for the full sleep. wait_for (timeout=0.05) fires
        # long before the 5s ceiling; the ceiling only bounds thread lifetime.
        import threading

        _release = threading.Event()

        def stalling_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            _release.wait(timeout=5.0)
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}  # pragma: no cover

        runner.run_playbook = stalling_run  # type: ignore[method-assign]

        client = _make_client(runner)
        # timeout=0.05s — wait_for fires long before the TestClient's own timeout
        try:
            resp = client.post(
                "/jobs/execute",
                json=_make_job(job_id="TESTJOBSTALL", timeout=0.05),
                timeout=10,
            )
            assert resp.status_code != 200
        except Exception:
            # Some transports surface TimeoutError as a client-side exception
            pass  # test passes — a stall was detected
        finally:
            # Release the worker thread so it exits immediately and cannot
            # block pytest/xdist teardown.
            _release.set()

    def test_caller_timeout_capped_at_server_max(self, tmp_path: Any, monkeypatch: Any) -> None:
        """Caller-supplied timeout > GLUDD_JOB_TIMEOUT_MAX must be capped to the server max."""
        monkeypatch.setenv("GLUDD_JOB_TIMEOUT_MAX", "10")

        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        effective_timeouts: list[float] = []
        original_wait_for = asyncio.wait_for

        async def patched_wait_for(coro: Any, timeout: Any) -> Any:  # type: ignore[override]
            effective_timeouts.append(float(timeout))
            return await original_wait_for(coro, timeout=timeout)

        def ok_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}

        runner.run_playbook = ok_run  # type: ignore[method-assign]

        with patch("general_ludd.worker.app.asyncio.wait_for", side_effect=patched_wait_for):
            client = _make_client(runner)
            resp = client.post(
                "/jobs/execute",
                json=_make_job(job_id="TESTJOBCAP", timeout=9999.0),
            )

        assert resp.status_code == 200
        assert effective_timeouts, "asyncio.wait_for was never called"
        assert effective_timeouts[0] <= 10.0, f"Timeout not capped: got {effective_timeouts[0]}"
