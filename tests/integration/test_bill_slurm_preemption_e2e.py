"""End-to-end tests: Slurm preemption handling — PREEMPTED state, auto-resubmit, backoff, chain tracking."""

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


class TestSlurmPreemptionE2E:
    def test_preempted_detection_and_resubmit_flow(self):
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "resub-001"
        handler = SlurmPreemptionHandler(adapter=adapter)

        preempted_job = SlurmJobInfo(job_id="orig-42", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result = handler.handle_preempted(
                preempted_job,
                submit_params={
                    "command": "vllm serve model",
                    "job_name": "resubmit-orig-42",
                    "account": "billing-dept",
                },
            )

        assert result.job_id == "resub-001"
        assert result.state == SlurmJobState.PENDING
        assert result.original_job_id == "orig-42"
        assert result.resubmit_count == 1

        call_kwargs = adapter.submit.call_args.kwargs
        assert call_kwargs["account"] == "billing-dept"
        assert call_kwargs["job_name"] == "resubmit-orig-42"

    def test_resubmit_chain_preserves_original_job_id(self):
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        adapter.submit.return_value = "resub-1"
        job1 = SlurmJobInfo(job_id="chain-1", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            r1 = handler.handle_preempted(job1)
        assert r1.original_job_id == "chain-1"
        assert r1.job_id == "resub-1"

        adapter.submit.return_value = "resub-2"
        with patch("time.sleep", return_value=None):
            r2 = handler.handle_preempted(job1)
        assert r2.original_job_id == "chain-1"
        assert r2.job_id == "resub-2"
        assert handler._preemption_counts["chain-1"] == 2

    def test_max_resubmits_default_3_triggers_error(self):
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo(job_id="max-3", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(3):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                result = handler.handle_preempted(job)
                assert result.state == SlurmJobState.PENDING

        with patch("time.sleep", return_value=None), \
                pytest.raises(SlurmPreemptionError, match="max resubmits"):
            handler.handle_preempted(job)

    def test_custom_max_resubmits_cap(self):
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo(job_id="custom-max", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            for i in range(7):
                adapter.submit.return_value = f"resub-{i + 1}"
                handler.handle_preempted(job, max_resubmits=7)

        with patch("time.sleep", return_value=None), \
                pytest.raises(SlurmPreemptionError):
            handler.handle_preempted(job, max_resubmits=7)

    def test_backoff_sequence_30_60_120_120(self):
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)
        job = SlurmJobInfo(job_id="backoff", state=SlurmJobState.PREEMPTED)

        sleep_calls: list[float] = []

        def record_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("time.sleep", side_effect=record_sleep):
            for i in range(4):
                adapter.submit.return_value = f"resubmit-{i + 1}"
                handler.handle_preempted(job, max_resubmits=5)

        assert sleep_calls == [30, 60, 120, 120]

    def test_multiple_jobs_tracked_independently(self):
        adapter = MagicMock(spec=SlurmAdapter)
        handler = SlurmPreemptionHandler(adapter=adapter)

        job_a = SlurmJobInfo(job_id="a", state=SlurmJobState.PREEMPTED)
        job_b = SlurmJobInfo(job_id="b", state=SlurmJobState.PREEMPTED)
        job_c = SlurmJobInfo(job_id="c", state=SlurmJobState.PREEMPTED)

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

    def test_resubmit_job_increments_original_count(self):
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

    def test_default_job_name_on_resubmit(self):
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "88888"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="11111", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            handler.handle_preempted(job)

        kwargs = adapter.submit.call_args.kwargs
        assert kwargs["job_name"] == "gludd-resubmit-11111"

    def test_preempted_state_in_enum_and_case_insensitive(self):
        assert SlurmJobState.PREEMPTED.value == "PREEMPTED"
        assert SlurmJobState.from_string("preempted") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("Preempted") == SlurmJobState.PREEMPTED
        assert SlurmJobState.from_string("PREEMPTED") == SlurmJobState.PREEMPTED

    def test_original_job_id_none_for_new_jobs(self):
        info = SlurmJobInfo(job_id="fresh", state=SlurmJobState.PENDING)
        assert info.original_job_id is None
        assert info.resubmit_count == 0

    def test_handler_creates_default_adapter(self):
        handler = SlurmPreemptionHandler()
        assert isinstance(handler._adapter, SlurmAdapter)

    def test_preemption_handler_with_custom_submit_params(self):
        adapter = MagicMock(spec=SlurmAdapter)
        adapter.submit.return_value = "custom-submit"
        handler = SlurmPreemptionHandler(adapter=adapter)

        job = SlurmJobInfo(job_id="custom-1", state=SlurmJobState.PREEMPTED)

        with patch("time.sleep", return_value=None):
            result = handler.handle_preempted(
                job,
                submit_params={
                    "command": "python train.py",
                    "account": "research-gpu",
                    "qos": "high",
                    "time_limit": "04:00:00",
                    "gpus": 4,
                },
            )

        assert result.job_id == "custom-submit"
        call_kwargs = adapter.submit.call_args.kwargs
        assert call_kwargs["account"] == "research-gpu"
        assert call_kwargs["qos"] == "high"
        assert call_kwargs["time_limit"] == "04:00:00"
        assert call_kwargs["gpus"] == 4
