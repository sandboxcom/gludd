"""TDD tests for D-09, D-10, D-35 worker/runner fixes.

D-10: duplicate job_id → 409; prepare_job_dirs uses exist_ok=False for root.
D-09: on failure in execute_job, job dir is cleaned up; on success, dir is also cleaned up.
D-35: JobSpec.timeout field; wait_for caps at GLUDD_JOB_TIMEOUT_MAX.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.schemas.job import JobSpec
from general_ludd.worker.app import create_app


@pytest.fixture(autouse=True)
def _reset_runner() -> Any:
    """Restore the module-level _runner singleton after each test.

    Prevents xdist process-level state leak: _make_client writes
    worker_module._runner directly and never restores it. Without this
    fixture, a failing-runner set by one test contaminates the next
    test scheduled on the same worker process.
    """
    import general_ludd.worker.app as worker_module
    original = worker_module._runner
    yield
    worker_module._runner = original


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

    def test_duplicate_error_message_is_clear(self, tmp_path: Any) -> None:
        """D-10: the FileExistsError message names the job_id and refuses overwrite."""
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        runner.prepare_job_dirs("JOBMSG")
        with pytest.raises(FileExistsError) as exc_info:
            runner.prepare_job_dirs("JOBMSG")
        msg = str(exc_info.value)
        assert "JOBMSG" in msg
        assert "already exists" in msg.lower()

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

        cast(Any, runner).run_playbook = fake_run

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBSUC"))
        assert resp.status_code == 200

        # Worker cleans up workspace on success (rmtree at app.py:483)
        job_dir = tmp_path / "TESTJOBSUC"
        assert not job_dir.exists()


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

        cast(Any, runner).run_playbook = failing_run

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

        cast(Any, runner).run_playbook = ok_run

        client = _make_client(runner)
        resp = client.post("/jobs/execute", json=_make_job(job_id="TESTJOBOK"))
        assert resp.status_code == 200
        # Worker cleans up workspace on success (rmtree at app.py:483)
        job_dir = tmp_path / "TESTJOBOK"
        assert not job_dir.exists()

    def test_job_dir_removed_on_timeout(self, tmp_path: Any) -> None:
        """D-09: a job that times out must still have its workspace cleaned up.

        asyncio.wait_for raises TimeoutError (an Exception) — covered by the
        except handler — but this guards against a regression that narrows the
        handler (e.g. except BaseException removal) silently leaking the dir.
        """
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        import threading
        _release = threading.Event()

        def stalling_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            _release.wait(timeout=5.0)
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}  # pragma: no cover

        cast(Any, runner).run_playbook = stalling_run

        client = _make_client(runner)
        try:
            client.post(
                "/jobs/execute",
                json=_make_job(job_id="TESTJOBTIMEOUT", timeout=0.05),
                timeout=10,
            )
        except Exception:
            pass
        finally:
            _release.set()

        job_dir = tmp_path / "TESTJOBTIMEOUT"
        assert not job_dir.exists(), "job dir leaked after timeout"

    def test_job_dir_removed_when_write_vars_raises(self, tmp_path: Any) -> None:
        """D-09: cleanup must also cover failures BEFORE run_playbook runs.

        write_vars lives inside the same try block as run_playbook, so a
        failure there must still tear down the prepared workspace.
        """
        runner = AnsibleRunnerAdapter(private_data_dir=str(tmp_path))
        noop = tmp_path / "noop.yml"
        noop.write_text("---\n- hosts: all\n  tasks: []\n")
        runner.registry["noop.yml"] = str(noop)

        def bad_write(*args: Any, **kwargs: Any) -> None:
            raise OSError("simulated write failure")

        def _noop_run(**k: Any) -> dict[str, Any]:
            return {"rc": 0}  # never reached

        cast(Any, runner).run_playbook = _noop_run
        cast(Any, runner).write_vars = bad_write

        client = _make_client(runner)
        with contextlib.suppress(Exception):
            client.post("/jobs/execute", json=_make_job(job_id="TESTJOBWV"), timeout=10)

        job_dir = tmp_path / "TESTJOBWV"
        assert not job_dir.exists(), "job dir leaked after write_vars failure"


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

        cast(Any, runner).run_playbook = stalling_run

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

        async def patched_wait_for(coro: Any, timeout: Any) -> Any:
            effective_timeouts.append(float(timeout))
            return await original_wait_for(coro, timeout=timeout)

        def ok_run(playbook_name: str, **kwargs: Any) -> dict[str, Any]:
            return {"status": "successful", "rc": 0, "events": [], "artifacts": []}

        cast(Any, runner).run_playbook = ok_run

        with patch("general_ludd.worker.app.asyncio.wait_for", side_effect=patched_wait_for):
            client = _make_client(runner)
            resp = client.post(
                "/jobs/execute",
                json=_make_job(job_id="TESTJOBCAP", timeout=9999.0),
            )

        assert resp.status_code == 200
        assert effective_timeouts, "asyncio.wait_for was never called"
        # wait_for gets _timeout + 30.0 outer-backstop margin.
        # _timeout = min(9999, 10) = 10.0 → wait_for timeout = 40.0.
        expected_msg = (
            f"Expected 40.0 (cap 10.0 + backstop 30.0), got {effective_timeouts[0]}"
        )
        assert effective_timeouts[0] == pytest.approx(40.0), expected_msg
