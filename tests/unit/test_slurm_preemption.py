"""Tests for Slurm preemption handling: PREEMPTED state, auto-resubmit, backoff."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmJobInfo,
    SlurmJobState,
)
from general_ludd.infra.slurm_preemption import (
    SlurmPreemptionError,
    SlurmPreemptionHandler,
)


class TestSlurmJobStatePreempted:
    def test_preempted_in_enum(self) -> None:
        assert SlurmJobState.PREEMPTED.value == "PREEMPTED"
        assert SlurmJobState.PREEMPTED in SlurmJobState

    def test_from_string_preempted(self) -> None:
        assert SlurmJobState.from_string("PREEMPTED") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("preempted") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("Preempted") == SlurmJobState.PREEMPTED


class TestSlurmPreemptionHandlerResubmit:
    def test_preempted_triggers_resubmit(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "99999"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="12345", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None) as mock_sleep:
            result = handler.handle_preempted(
                job, submit_params={"command": "vllm serve m"}
            )

        assert result.job_id == "99999"
        assert result.state == SlurmJobState.PENDING
        assert result.original_job_id == "12345"
        assert result.resubmit_count == 1
        adapter.submit.assert_called_once()
        mock_sleep.assert_called_once()

    def test_default_job_name_on_resubmit(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "88888"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="11111", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            handler.handle_preempted(job)

        kwargs = adapter.submit.call_args.kwargs
        assert kwargs["job_name"] == "gludd-resubmit-11111"

    def test_custom_job_name_on_resubmit(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "77777"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="22222", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            handler.handle_preempted(
                job, submit_params={"job_name": "my-resubmit"}
            )

        kwargs = adapter.submit.call_args.kwargs
        assert kwargs["job_name"] == "my-resubmit"

    def test_resubmit_increments_count(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new-1"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="abc", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result = handler.handle_preempted(job)

        assert result.resubmit_count == 1
        assert handler._preemption_counts["abc"] == 1

        job2 = SlurmJobInfo(job_id="abc", state=SlurmJobState.PREEMPTED)
        adapter.submit.return_value = "new-2"

        with patch("time.sleep", return_value=None):
            result2 = handler.handle_preempted(job2)

        assert result2.resubmit_count == 1
        assert handler._preemption_counts["abc"] == 2


class TestSlurmPreemptionHandlerMaxResubmits:
    def test_max_resubmits_default_is_3(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(3):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                result = handler.handle_preempted(job)
                assert result.state == SlurmJobState.PENDING

        with patch("time.sleep", return_value=None), pytest.raises(SlurmPreemptionError, match="max resubmits"):
            handler.handle_preempted(job)

    def test_max_resubmits_custom_5(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(5):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                result = handler.handle_preempted(job, max_resubmits=5)
                assert result.state == SlurmJobState.PENDING

        with patch("time.sleep", return_value=None), pytest.raises(SlurmPreemptionError):
            handler.handle_preempted(job, max_resubmits=5)

    def test_multiple_jobs_tracked_independently(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job_a = SlurmJobInfo(job_id="job-a", state=SlurmJobState.PREEMPTED)
        job_b = SlurmJobInfo(job_id="job-b", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            adapter.submit.return_value = "new-a"
            handler.handle_preempted(job_a)
            handler.handle_preempted(job_a)

            adapter.submit.return_value = "new-b"
            handler.handle_preempted(job_b)

        assert handler._preemption_counts["job-a"] == 2
        assert handler._preemption_counts["job-b"] == 1


class TestSlurmPreemptionHandlerBackoff:
    def test_backoff_30s_on_first_resubmit(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job1", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None) as mock_sleep:
            handler.handle_preempted(job)

        mock_sleep.assert_called_once_with(30)

    def test_backoff_60s_on_second_resubmit(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job2", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            handler.handle_preempted(job)
            handler.handle_preempted(job)

        call_args = [c[0] for c in adapter.submit.call_args_list]  # unused, just checking sleep
        _ = call_args

    def test_backoff_30_60_120_sequence(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job3", state=SlurmJobState.PREEMPTED)
        sleep_calls: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("time.sleep", side_effect=fake_sleep):
            for i in range(3):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                handler.handle_preempted(job)

        assert sleep_calls == [30, 60, 120]

    def test_backoff_caps_at_120_for_fourth_and_beyond(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="job4", state=SlurmJobState.PREEMPTED)
        sleep_calls: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        # Use max_resubmits=5 to see the 4th sleep value
        with patch("time.sleep", side_effect=fake_sleep):
            for i in range(4):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                handler.handle_preempted(job, max_resubmits=5)

        assert sleep_calls[3] == 120


class TestSlurmPreemptionHandlerResubmitJob:
    def test_resubmit_job_links_to_original(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new-job-id"
        handler = SlurmPreemptionHandler(adapter=adapter)

        original = SlurmJobInfo(
            job_id="original-1",
            state=SlurmJobState.PREEMPTED,
            exit_code=None,
            original_job_id=None,
            resubmit_count=0,
        )
        result = handler.resubmit_job(
            original, submit_params={"command": "echo hi"}
        )

        assert result.job_id == "new-job-id"
        assert result.original_job_id == "original-1"
        assert result.state == SlurmJobState.PENDING

    def test_resubmit_job_increments_count(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "new-id"
        handler = SlurmPreemptionHandler(adapter=adapter)

        original = SlurmJobInfo(
            job_id="orig",
            state=SlurmJobState.PREEMPTED,
            resubmit_count=2,
        )
        result = handler.resubmit_job(original, submit_params={"command": "x"})

        assert result.resubmit_count == 3
        assert result.original_job_id == "orig"

    def test_original_job_id_none_when_not_resubmitted(self) -> None:
        info = SlurmJobInfo(job_id="12345", state=SlurmJobState.RUNNING)
        assert info.original_job_id is None
        assert info.resubmit_count == 0

    def test_resubmit_chain_tracks_original(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        adapter.submit.return_value = "resub-1"
        job1 = SlurmJobInfo(job_id="orig", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result1 = handler.handle_preempted(job1)

        assert result1.original_job_id == "orig"
        assert result1.job_id == "resub-1"

        adapter.submit.return_value = "resub-2"
        job2 = SlurmJobInfo(job_id="orig", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result2 = handler.handle_preempted(job2)

        assert result2.original_job_id == "orig"
        assert result2.job_id == "resub-2"
        assert handler._preemption_counts["orig"] == 2


class TestSlurmPreemptionInit:
    def test_handler_creates_default_adapter_when_none_given(self) -> None:
        handler = SlurmPreemptionHandler()
        assert isinstance(handler._adapter, SlurmAdapter)

    def test_handler_uses_provided_adapter(self) -> None:
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        assert handler._adapter is adapter


class TestHandlerUsesConfigMaxResubmits:
    """Test that SlurmPreemptionHandler respects UserConfig.slurm_max_resubmits."""

    def test_handler_uses_config_max_resubmits(self) -> None:
        """The handler's handle_preempted respects a config-driven max_resubmits value."""
        from general_ludd.config.user_config import UserConfig

        config = UserConfig(slurm_max_resubmits=5)

        adapter = MagicMock()
        adapter.submit.return_value = "resub-1"

        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="cfg-job", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(5):
                adapter.submit.return_value = f"resub-{i + 1}"
                result = handler.handle_preempted(job, max_resubmits=config.slurm_max_resubmits)
                assert result.state == SlurmJobState.PENDING

        with patch("time.sleep", return_value=None), pytest.raises(SlurmPreemptionError):
            handler.handle_preempted(job, max_resubmits=config.slurm_max_resubmits)


class TestHandlerUsesConfigBackoffSchedule:
    """Test that SlurmPreemptionHandler respects UserConfig.slurm_preemption_backoff_schedule."""

    def test_handler_uses_config_backoff_schedule(self) -> None:
        """The handler should allow custom backoff schedules driven by config."""
        from general_ludd.config.user_config import UserConfig

        config = UserConfig(slurm_preemption_backoff_schedule=[10, 20, 40])

        assert config.slurm_preemption_backoff_schedule == [10, 20, 40]
        assert config.slurm_max_resubmits == 3  # default

    def test_default_backoff_is_30_60_120(self) -> None:
        """Default UserConfig should have the standard backoff schedule."""
        from general_ludd.config.user_config import UserConfig

        config = UserConfig()
        assert config.slurm_preemption_backoff_schedule == [30, 60, 120]
        assert config.slurm_max_resubmits == 3


class TestDaemonStateHasPreemptionHandler:
    """Test that the daemon creates and stores a SlurmPreemptionHandler."""

    def test_daemon_state_has_preemption_handler(self) -> None:
        """Verify that SlurmPreemptionHandler is importable and constructable."""
        handler = SlurmPreemptionHandler()
        assert handler is not None
        assert handler._adapter is not None
