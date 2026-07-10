"""Tests for record_job_benchmark (event_loop/benchmark.py).

Uses the project-wide asyncio_mode = "auto" (pyproject.toml) — no
pytest.mark.asyncio markers needed.

Run: make test-iso TESTFILE='tests/unit/test_event_loop_benchmark.py'
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.benchmark import record_job_benchmark


class TestRecordJobBenchmarkHappyPath:
    async def test_exact_record_result_payload(self) -> None:
        recorder = MagicMock()
        recorder._repo = MagicMock()
        recorder._repo.record_result = AsyncMock()

        await record_job_benchmark(
            recorder=recorder,
            model_profile="glm-4.6",
            prompt_profile="pp-1",
            work_type="bug_fix",
            success=True,
            input_tokens=2000,
            output_tokens=100,
            cost_usd=0.01,
        )

        recorder._repo.record_result.assert_awaited_once_with(
            data={
                "model_profile_id": "glm-4.6",
                "prompt_profile_id": "pp-1",
                "task_type": "bug_fix",
                "success": True,
                "completion_score": 1.0,
                "code_quality_score": 0.5,
                "instruction_adherence_score": 1.0,
                # min(1.0, 1000.0 / max(2000.0, 1.0)) == 0.5
                "token_efficiency_score": 0.5,
                "input_tokens": 2000,
                "output_tokens": 100,
                "cost_usd": 0.01,
                "time_seconds": 0.0,
                "error_message": "",
                "raw_output": "",
            }
        )


class TestRecordJobBenchmarkFailurePath:
    async def test_failure_unknown_profile_and_error_message(self) -> None:
        recorder = MagicMock()
        recorder._repo = MagicMock()
        recorder._repo.record_result = AsyncMock()

        await record_job_benchmark(
            recorder=recorder,
            model_profile=None,
            prompt_profile=None,
            work_type="code",
            success=False,
        )

        recorder._repo.record_result.assert_awaited_once_with(
            data={
                "model_profile_id": "unknown",
                "prompt_profile_id": None,
                "task_type": "code",
                "success": False,
                "completion_score": 0.0,
                "code_quality_score": 0.5,
                "instruction_adherence_score": 0.5,
                # input_tokens defaults to 0 -> max(0.0, 1.0) == 1.0 -> min(1.0, 1000.0) == 1.0
                "token_efficiency_score": 1.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "time_seconds": 0.0,
                "error_message": "Job failed",
                "raw_output": "",
            }
        )


class TestRecordJobBenchmarkNoops:
    async def test_recorder_none_is_a_noop(self) -> None:
        # Must not raise even though recorder._repo would normally be accessed.
        await record_job_benchmark(
            recorder=None,
            model_profile="m",
            prompt_profile="p",
            work_type="code",
            success=True,
        )

    async def test_repo_none_is_a_noop(self) -> None:
        recorder = MagicMock()
        recorder._repo = None

        await record_job_benchmark(
            recorder=recorder,
            model_profile="m",
            prompt_profile="p",
            work_type="code",
            success=True,
        )
        # No assertion possible on record_result since _repo is None; absence
        # of an exception (the _repo.record_result attribute access is never
        # reached) is the behavior under test.

    async def test_record_result_exception_is_suppressed(self) -> None:
        recorder = MagicMock()
        recorder._repo = MagicMock()
        recorder._repo.record_result = AsyncMock(side_effect=RuntimeError("db down"))

        # contextlib.suppress(Exception) wraps the whole record_result call,
        # so a raising repo must not propagate.
        await record_job_benchmark(
            recorder=recorder,
            model_profile="m",
            prompt_profile="p",
            work_type="code",
            success=True,
        )


class TestRecordJobBenchmarkFragileGuard:
    async def test_recorder_missing_repo_attr_raises_attributeerror(self) -> None:
        """DOCUMENTED FRAGILITY: the ``recorder is None or recorder._repo is None``
        guard in record_job_benchmark accesses ``recorder._repo`` OUTSIDE the
        ``contextlib.suppress(Exception)`` block that wraps the record_result
        call. A recorder object that lacks a ``_repo`` attribute entirely (as
        opposed to one whose ``_repo`` is explicitly ``None``) therefore raises
        an uncaught AttributeError instead of being treated as "no repo,
        no-op".

        If this guard is ever hardened (e.g. ``getattr(recorder, "_repo",
        None) is None``), flip this test to assert the call is a silent noop
        instead of asserting the raise.
        """

        class _RecorderWithoutRepo:
            pass

        recorder = _RecorderWithoutRepo()

        with pytest.raises(AttributeError):
            await record_job_benchmark(
                recorder=recorder,
                model_profile="m",
                prompt_profile="p",
                work_type="code",
                success=True,
            )
