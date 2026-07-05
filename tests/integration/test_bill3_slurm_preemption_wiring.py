"""Integration tests: bill-3 Slurm preemption daemon wiring.

Tests SlurmPreemptionHandler PREEMPTED state detection, resubmit with
exponential backoff, max_resubmits cap, and original_job_id chaining
through the slurm_deployment poll_until_servable integration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobInfo,
    SlurmJobState,
)
from general_ludd.infra.slurm_preemption import (
    SlurmPreemptionError,
    SlurmPreemptionHandler,
)


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    yield
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


def _make_db_config(tmp_path: pytest.Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


class TestSlurmPreemptionWiring:
    """Integration tests for SlurmPreemptionHandler wiring and lifecycle."""

    def test_preempted_state_detection_and_transition(self):
        """PREEMPTED state is detected and triggers resubmit to PENDING."""
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "resub-pending-001"
        handler = SlurmPreemptionHandler(adapter=adapter)

        preempted = SlurmJobInfo("orig-42", SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result = handler.handle_preempted(preempted)

        assert result.job_id == "resub-pending-001"
        assert result.state == SlurmJobState.PENDING
        assert result.original_job_id == "orig-42"
        assert result.resubmit_count == 1

    def test_resubmit_with_exponential_backoff_sequence(self):
        """Resubmits wait 30s, 60s, 120s, 120s between attempts."""
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo("backoff-job", SlurmJobState.PREEMPTED)

        sleep_calls: list[float] = []

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("time.sleep", side_effect=record_sleep):
            for i in range(4):
                adapter.submit.return_value = f"resub-{i + 1}"
                handler.handle_preempted(job, max_resubmits=5)

        assert sleep_calls == [30, 60, 120, 120]

    def test_max_resubmits_cap_stops_after_n(self):
        """After max_resubmits (default 3), SlurmPreemptionError is raised."""
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo("max-job", SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(3):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                result = handler.handle_preempted(job)
                assert result.state == SlurmJobState.PENDING

        with patch("time.sleep", return_value=None), \
                pytest.raises(SlurmPreemptionError, match="max resubmits"):
            handler.handle_preempted(job)

    def test_original_job_id_chaining_across_resubmits(self):
        """original_job_id stays set to the first job across all resubmits."""
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        adapter.submit.return_value = "chain-1"
        job = SlurmJobInfo("orig-chain", SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            r1 = handler.handle_preempted(job)
        assert r1.original_job_id == "orig-chain"
        assert r1.job_id == "chain-1"

        adapter.submit.return_value = "chain-2"
        with patch("time.sleep", return_value=None):
            r2 = handler.handle_preempted(job)
        assert r2.original_job_id == "orig-chain"
        assert r2.job_id == "chain-2"

        adapter.submit.return_value = "chain-3"
        with patch("time.sleep", return_value=None):
            r3 = handler.handle_preempted(job)
        assert r3.original_job_id == "orig-chain"
        assert r3.job_id == "chain-3"

        assert handler._preemption_counts["orig-chain"] == 3

    def test_multiple_independent_jobs_tracked_separately(self):
        """Each job has its own preemption count, independent of others."""
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job_a = SlurmJobInfo("a", SlurmJobState.PREEMPTED)
        job_b = SlurmJobInfo("b", SlurmJobState.PREEMPTED)
        job_c = SlurmJobInfo("c", SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            adapter.submit.return_value = "new-a"
            handler.handle_preempted(job_a)
            handler.handle_preempted(job_a)

            adapter.submit.return_value = "new-b"
            handler.handle_preempted(job_b)
            handler.handle_preempted(job_b)
            handler.handle_preempted(job_b)

            adapter.submit.return_value = "new-c"
            handler.handle_preempted(job_c)

        assert handler._preemption_counts["a"] == 2
        assert handler._preemption_counts["b"] == 3
        assert handler._preemption_counts["c"] == 1

    def test_custom_max_resubmits_cap(self):
        """Custom max_resubmits is respected (test with cap of 7)."""
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo("custom-cap", SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(7):
                adapter.submit.return_value = f"resub-{i + 1}"
                handler.handle_preempted(job, max_resubmits=7)

        with patch("time.sleep", return_value=None), \
                pytest.raises(SlurmPreemptionError):
            handler.handle_preempted(job, max_resubmits=7)

    def test_resubmit_increments_original_count(self):
        """resubmit_job increments resubmit_count on the result."""
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new-id"
        handler = SlurmPreemptionHandler(adapter=adapter)

        original = SlurmJobInfo(
            job_id="orig", state=SlurmJobState.PREEMPTED, resubmit_count=2
        )
        result = handler.resubmit_job(original, submit_params={"command": "x"})
        assert result.resubmit_count == 3
        assert result.original_job_id == "orig"

    def test_job_status_polling_endpoint_reflects_state(
        self, tmp_path: pytest.Path
    ):
        """GET /admin/slurm/jobs/{job_id} returns current state including PREEMPTED."""
        mock_adapter = MagicMock()
        mock_adapter.available.return_value = True
        mock_adapter.status.return_value = SlurmJobInfo(
            "job-preempted", SlurmJobState.PREEMPTED
        )

        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ), patch(
            "general_ludd.routers.slurm.SlurmAdapter",
            return_value=mock_adapter,
        ):
            app = create_daemon_app(
                tick_interval=300.0, config_dir=_make_db_config(tmp_path)
            )
            with TestClient(app) as client:
                resp = client.get("/admin/slurm/jobs/job-preempted")
                assert resp.status_code == 200
                data = resp.json()
                assert data["job_id"] == "job-preempted"
                assert data["state"] == "PREEMPTED"

    def test_preempted_state_case_insensitive_from_string(self):
        """SlurmJobState.from_string handles case-insensitive PREEMPTED."""
        assert SlurmJobState.from_string("preempted") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("Preempted") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("PREEMPTED") == SlurmJobState.PREEMPTED

    def test_preemption_handler_default_adapter(self):
        """Handler creates a default SlurmAdapter when none is provided."""
        handler = SlurmPreemptionHandler()
        assert isinstance(handler._adapter, SlurmAdapter)

    def test_original_job_id_none_for_new_jobs(self):
        """New jobs have original_job_id=None and resubmit_count=0."""
        info = SlurmJobInfo("fresh", SlurmJobState.PENDING)
        assert info.original_job_id is None
        assert info.resubmit_count == 0
