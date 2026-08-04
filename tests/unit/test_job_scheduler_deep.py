"""Deep job scheduler tests — cron, interval, priority, concurrency, pause/resume.

Covers: CronSchedule parsing and matching, next_after computation, IntervalJob
scheduling, priority ordering, concurrency limiting, pause/resume gating,
job lifecycle (complete/fail/reset), error paths, and edge cases.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from general_ludd.scheduling.job_scheduler import (
    CronJob,
    CronSchedule,
    IntervalJob,
    JobScheduler,
    JobState,
)

# ---------------------------------------------------------------------------
# CronSchedule parsing
# ---------------------------------------------------------------------------


class TestCronScheduleParsing:
    def test_wildcard_all_fields(self):
        cs = CronSchedule.parse("* * * * *")
        assert cs.minute == "*"
        assert cs.hour == "*"
        assert cs.day_of_month == "*"
        assert cs.month == "*"
        assert cs.day_of_week == "*"

    def test_specific_values(self):
        cs = CronSchedule.parse("30 14 15 6 3")
        assert cs.minute == "30"
        assert cs.hour == "14"
        assert cs.day_of_month == "15"
        assert cs.month == "6"
        assert cs.day_of_week == "3"

    def test_ranges_and_lists(self):
        cs = CronSchedule.parse("0-30 8,12,16 1-15 */2 1-5")
        assert cs.minute == "0-30"
        assert cs.hour == "8,12,16"
        assert cs.day_of_month == "1-15"
        assert cs.month == "*/2"
        assert cs.day_of_week == "1-5"

    def test_too_few_fields_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            CronSchedule.parse("* * * *")

    def test_too_many_fields_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            CronSchedule.parse("* * * * * *")

    def test_step_syntax(self):
        cs = CronSchedule.parse("*/5 * * * *")
        assert cs.minute == "*/5"


# ---------------------------------------------------------------------------
# CronSchedule.matches
# ---------------------------------------------------------------------------


class TestCronScheduleMatches:
    def test_wildcard_matches_everything(self):
        cs = CronSchedule.parse("* * * * *")
        assert cs.matches(100000.0) is True
        assert cs.matches(9999999999.0) is True

    def test_exact_minute(self):
        from datetime import datetime

        cs = CronSchedule.parse("30 14 15 6 *")
        dt = datetime(2025, 6, 15, 14, 30, 0, tzinfo=UTC)
        ts = dt.timestamp()
        assert cs.matches(ts) is True

    def test_exact_minute_fails_wrong_minute(self):
        from datetime import datetime

        cs = CronSchedule.parse("30 14 15 6 *")
        dt = datetime(2025, 6, 15, 14, 31, 0, tzinfo=UTC)
        ts = dt.timestamp()
        assert cs.matches(ts) is False

    def test_range_matches(self):
        cs = CronSchedule.parse("0-15 * * * *")
        ts = 0.0  # epoch: 1970-01-01 00:00 UTC
        assert cs.matches(ts) is True
        ts_10min = 600.0
        assert cs.matches(ts_10min) is True
        ts_16min = 960.0
        assert cs.matches(ts_16min) is False

    def test_list_matches(self):
        cs = CronSchedule.parse("0,30 * * * *")
        ts_on_hour = 0.0
        ts_half_past = 1800.0
        ts_quarter = 900.0
        assert cs.matches(ts_on_hour) is True
        assert cs.matches(ts_half_past) is True
        assert cs.matches(ts_quarter) is False

    def test_step_matches(self):
        cs = CronSchedule.parse("*/15 * * * *")
        ts_0 = 0.0
        ts_15 = 900.0
        ts_30 = 1800.0
        ts_10 = 600.0
        assert cs.matches(ts_0) is True
        assert cs.matches(ts_15) is True
        assert cs.matches(ts_30) is True
        assert cs.matches(ts_10) is False

    def test_range_step_matches(self):
        cs = CronSchedule.parse("10-30/10 * * * *")
        ts_10 = 600.0
        ts_20 = 1200.0
        ts_30 = 1800.0
        ts_00 = 0.0
        ts_40 = 2400.0
        assert cs.matches(ts_10) is True
        assert cs.matches(ts_20) is True
        assert cs.matches(ts_30) is True
        assert cs.matches(ts_00) is False
        assert cs.matches(ts_40) is False


# ---------------------------------------------------------------------------
# CronSchedule.next_after
# ---------------------------------------------------------------------------


class TestCronScheduleNextAfter:
    def test_next_after_wildcard(self):
        cs = CronSchedule.parse("* * * * *")
        ts = 100000.0
        nxt = cs.next_after(ts)
        assert nxt > ts
        assert cs.matches(nxt) is True

    def test_next_after_specific_minute(self):
        cs = CronSchedule.parse("30 12 * * *")
        # exactly at 12:30 UTC on some day
        ts = 100000000.0
        nxt = cs.next_after(ts)
        assert nxt > ts
        assert cs.matches(nxt) is True

    def test_next_after_is_strictly_future(self):
        cs = CronSchedule.parse("* * * * *")
        ts = 100000.0
        nxt = cs.next_after(ts)
        assert nxt > ts


# ---------------------------------------------------------------------------
# CronSchedule._field_matches edge cases
# ---------------------------------------------------------------------------


class TestFieldMatches:
    def test_invalid_values_out_of_range(self):
        assert CronSchedule._field_matches("*", 60, 0, 59) is False
        assert CronSchedule._field_matches("*", -1, 0, 59) is False

    def test_exact_value_out_of_pattern_range(self):
        assert CronSchedule._field_matches("5", 6, 0, 59) is False

    def test_range_with_step(self):
        assert CronSchedule._field_matches("0-10/3", 0, 0, 59) is True
        assert CronSchedule._field_matches("0-10/3", 3, 0, 59) is True
        assert CronSchedule._field_matches("0-10/3", 6, 0, 59) is True
        assert CronSchedule._field_matches("0-10/3", 9, 0, 59) is True
        assert CronSchedule._field_matches("0-10/3", 1, 0, 59) is False
        assert CronSchedule._field_matches("0-10/3", 11, 0, 59) is False


# ---------------------------------------------------------------------------
# IntervalJob
# ---------------------------------------------------------------------------


class TestIntervalJob:
    def test_default_interval(self):
        job = IntervalJob(id="i1")
        assert job.interval_seconds == 60.0

    def test_custom_interval(self):
        job = IntervalJob(id="i2", interval_seconds=10.0)
        assert job.interval_seconds == 10.0

    def test_next_time_never_run(self):
        job = IntervalJob(id="i3", interval_seconds=30.0)
        now = 1000.0
        assert job.next_time(now=now) == pytest.approx(now + 30.0, rel=1e-9)

    def test_next_time_after_run(self):
        job = IntervalJob(id="i4", interval_seconds=30.0)
        job.last_run_at = 1000.0
        assert job.next_time() == pytest.approx(1030.0, rel=1e-9)

    def test_next_matches_never_run(self):
        job = IntervalJob(id="i5", interval_seconds=60.0)
        now = 30.0
        assert job.next_matches(now) is False
        assert job.next_matches(now + 60.0) is True
        assert job.next_matches(now + 120.0) is True

    def test_next_matches_after_run(self):
        job = IntervalJob(id="i6", interval_seconds=10.0)
        job.last_run_at = 100.0
        assert job.next_matches(109.9) is False
        assert job.next_matches(110.0) is True


# ---------------------------------------------------------------------------
# CronJob
# ---------------------------------------------------------------------------


class TestCronJob:
    def test_default_cron(self):
        job = CronJob(id="c1")
        assert job.cron_expr == "* * * * *"
        assert job.priority == 100

    def test_custom_cron(self):
        job = CronJob(id="c2", cron_expr="0 3 * * mon", priority=50)
        assert job.cron_expr == "0 3 * * mon"
        assert job.priority == 50

    def test_next_matches_delegates_to_schedule(self):
        job = CronJob(id="c3", cron_expr="* * * * *")
        assert job.next_matches(100000.0) is True

    def test_next_time_never_run(self):
        job = CronJob(id="c4", cron_expr="30 12 1 1 *")
        ts = 100000.0
        nxt = job.next_time()
        assert nxt > ts
        assert job.schedule.matches(nxt) is True


# ---------------------------------------------------------------------------
# JobScheduler — basic add/remove/get
# ---------------------------------------------------------------------------


class TestJobSchedulerBasic:
    def test_add_and_get(self):
        sched = JobScheduler()
        job = IntervalJob(id="a")
        sched.add(job)
        assert sched.get("a") is job

    def test_duplicate_add_raises(self):
        sched = JobScheduler()
        sched.add(IntervalJob(id="a"))
        with pytest.raises(ValueError, match="already registered"):
            sched.add(IntervalJob(id="a"))

    def test_remove(self):
        sched = JobScheduler()
        job = IntervalJob(id="a")
        sched.add(job)
        removed = sched.remove("a")
        assert removed is job
        with pytest.raises(KeyError):
            sched.get("a")

    def test_remove_missing_raises(self):
        sched = JobScheduler()
        with pytest.raises(KeyError, match="not found"):
            sched.remove("nonexistent")

    def test_jobs_returns_all(self):
        sched = JobScheduler()
        sched.add(IntervalJob(id="a"))
        sched.add(IntervalJob(id="b"))
        assert len(sched.jobs()) == 2

    def test_max_concurrent_default(self):
        sched = JobScheduler()
        assert sched.max_concurrent == 5

    def test_max_concurrent_custom(self):
        sched = JobScheduler(max_concurrent=3)
        assert sched.max_concurrent == 3

    def test_negative_max_concurrent_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            JobScheduler(max_concurrent=-1)


# ---------------------------------------------------------------------------
# JobScheduler — tick / interval scheduling
# ---------------------------------------------------------------------------


class TestJobSchedulerTickInterval:
    def test_interval_job_not_yet_due(self):
        sched = JobScheduler(max_concurrent=5)
        job = IntervalJob(id="a", interval_seconds=60)
        sched.add(job)
        dispatched = sched.tick(now=0.0)
        assert dispatched == []

    def test_interval_job_due(self):
        sched = JobScheduler(max_concurrent=5)
        job = IntervalJob(id="a", interval_seconds=60)
        sched.add(job)
        dispatched = sched.tick(now=60.0)
        assert len(dispatched) == 1
        assert dispatched[0].id == "a"
        assert job.state == JobState.RUNNING

    def test_interval_job_not_double_dispatched(self):
        sched = JobScheduler(max_concurrent=5)
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        d1 = sched.tick(now=20.0)
        assert len(d1) == 1
        d2 = sched.tick(now=30.0)
        assert d2 == []

    def test_interval_job_re_dispatched_after_complete(self):
        sched = JobScheduler(max_concurrent=5)
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        sched.tick(now=20.0)
        sched.complete("a", now=20.0)
        sched.reset("a")
        dispatched = sched.tick(now=35.0)
        assert len(dispatched) == 1
        assert dispatched[0].id == "a"

    def test_multiple_intervals_dispatched_by_priority(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(IntervalJob(id="low", interval_seconds=10, priority=99))
        sched.add(IntervalJob(id="high", interval_seconds=10, priority=1))
        sched.add(IntervalJob(id="mid", interval_seconds=10, priority=50))
        dispatched = sched.tick(now=20.0)
        assert [j.id for j in dispatched] == ["high", "mid", "low"]


# ---------------------------------------------------------------------------
# JobScheduler — tick / cron scheduling
# ---------------------------------------------------------------------------


class TestJobSchedulerTickCron:
    def test_cron_job_matches(self):
        sched = JobScheduler(max_concurrent=5)
        job = CronJob(id="c", cron_expr="* * * * *")
        sched.add(job)
        dispatched = sched.tick(now=0.0)
        assert len(dispatched) == 1
        assert dispatched[0].id == "c"

    def test_cron_job_not_matches(self):
        sched = JobScheduler(max_concurrent=5)
        job = CronJob(id="c", cron_expr="30 12 1 1 *")
        sched.add(job)
        dispatched = sched.tick(now=100000.0)
        assert dispatched == []

    def test_cron_job_priority_ordering(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(CronJob(id="c1", cron_expr="* * * * *", priority=50))
        sched.add(CronJob(id="c2", cron_expr="* * * * *", priority=10))
        sched.add(CronJob(id="c3", cron_expr="* * * * *", priority=30))
        dispatched = sched.tick(now=0.0)
        assert [j.id for j in dispatched] == ["c2", "c3", "c1"]


# ---------------------------------------------------------------------------
# JobScheduler — concurrency limiting
# ---------------------------------------------------------------------------


class TestJobSchedulerConcurrency:
    def test_respects_max_concurrent(self):
        sched = JobScheduler(max_concurrent=2)
        for i in range(5):
            sched.add(IntervalJob(id=str(i), interval_seconds=10))
        dispatched = sched.tick(now=20.0)
        assert len(dispatched) == 2

    def test_zero_concurrency_dispatches_none(self):
        sched = JobScheduler(max_concurrent=0)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        dispatched = sched.tick(now=20.0)
        assert dispatched == []

    def test_existing_running_reduces_available_slots(self):
        sched = JobScheduler(max_concurrent=3)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.add(IntervalJob(id="b", interval_seconds=10))
        sched.add(IntervalJob(id="c", interval_seconds=10))
        sched.add(IntervalJob(id="d", interval_seconds=10))
        d1 = sched.tick(now=20.0)
        assert len(d1) == 3
        assert sched.running_count == 3
        d2 = sched.tick(now=30.0)
        assert d2 == []

    def test_slot_freed_after_complete(self):
        sched = JobScheduler(max_concurrent=2)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.add(IntervalJob(id="b", interval_seconds=10))
        sched.add(IntervalJob(id="c", interval_seconds=10))
        sched.tick(now=20.0)
        assert sched.running_count == 2
        sched.complete("a", now=20.0)
        sched.reset("a")
        dispatched = sched.tick(now=35.0)
        assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# JobScheduler — pause / resume
# ---------------------------------------------------------------------------


class TestJobSchedulerPauseResume:
    def test_paused_initial_false(self):
        sched = JobScheduler()
        assert sched.paused is False

    def test_pause_stops_dispatch(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.pause()
        dispatched = sched.tick(now=20.0)
        assert dispatched == []

    def test_resume_allows_dispatch(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.pause()
        sched.resume()
        dispatched = sched.tick(now=20.0)
        assert len(dispatched) == 1

    def test_pause_persists_across_ticks(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.add(IntervalJob(id="b", interval_seconds=10))
        sched.pause()
        assert sched.tick(now=20.0) == []
        assert sched.tick(now=40.0) == []
        sched.resume()
        assert len(sched.tick(now=60.0)) == 2


# ---------------------------------------------------------------------------
# JobScheduler — complete / fail / reset
# ---------------------------------------------------------------------------


class TestJobSchedulerLifecycle:
    def test_complete_transitions_state(self):
        sched = JobScheduler()
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        sched.tick(now=20.0)
        sched.complete("a", result="ok")
        assert job.state == JobState.COMPLETED
        assert job.result == "ok"
        assert job.run_count == 1
        assert job.finished_at is not None
        assert job.last_run_at is not None

    def test_fail_transitions_state(self):
        sched = JobScheduler()
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        sched.tick(now=20.0)
        sched.fail("a", error="timeout")
        assert job.state == JobState.FAILED
        assert job.error == "timeout"
        assert job.run_count == 1

    def test_reset_clears_state(self):
        sched = JobScheduler()
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        sched.tick(now=20.0)
        sched.complete("a", result="ok")
        sched.reset("a")
        assert job.state == JobState.PENDING
        assert job.started_at is None
        assert job.finished_at is None
        assert job.result is None
        assert job.error is None

    def test_reset_all_clears_everything(self):
        sched = JobScheduler()
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.add(IntervalJob(id="b", interval_seconds=10))
        sched.tick(now=20.0)
        sched.complete("a")
        sched.fail("b", error="err")
        sched.reset_all()
        for job in sched.jobs():
            assert job.state == JobState.PENDING
            assert job.started_at is None
            assert job.finished_at is None

    def test_run_count_increments(self):
        sched = JobScheduler()
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        for _ in range(3):
            sched.tick(now=float(_ * 20 + 20))
            sched.complete("a")
            sched.reset("a")
        assert job.run_count == 3


# ---------------------------------------------------------------------------
# JobScheduler — edge cases
# ---------------------------------------------------------------------------


class TestJobSchedulerEdgeCases:
    def test_tick_empty_scheduler(self):
        sched = JobScheduler()
        assert sched.tick(now=0.0) == []

    def test_mixed_cron_and_interval_priority(self):
        sched = JobScheduler(max_concurrent=5)
        sched.add(IntervalJob(id="i1", interval_seconds=10, priority=50))
        sched.add(CronJob(id="c1", cron_expr="* * * * *", priority=10))
        sched.add(IntervalJob(id="i2", interval_seconds=10, priority=30))
        dispatched = sched.tick(now=20.0)
        assert dispatched[0].id == "c1"
        assert dispatched[1].id == "i2"
        assert dispatched[2].id == "i1"

    def test_job_not_dispatched_when_already_running(self):
        sched = JobScheduler(max_concurrent=5)
        job = IntervalJob(id="a", interval_seconds=10)
        sched.add(job)
        sched.tick(now=20.0)
        assert job.state == JobState.RUNNING
        dispatched = sched.tick(now=30.0)
        assert all(j.id != "a" for j in dispatched)

    def test_pending_and_running_counts(self):
        sched = JobScheduler(max_concurrent=2)
        sched.add(IntervalJob(id="a", interval_seconds=10))
        sched.add(IntervalJob(id="b", interval_seconds=10))
        sched.add(IntervalJob(id="c", interval_seconds=10))
        assert sched.pending_count == 3
        sched.tick(now=20.0)
        assert sched.running_count == 2
        assert sched.pending_count == 1


# ---------------------------------------------------------------------------
# JobState enum
# ---------------------------------------------------------------------------


class TestJobState:
    def test_enum_values(self):
        assert JobState.PENDING.value == 1
        assert JobState.RUNNING.value == 2
        assert JobState.COMPLETED.value == 3
        assert JobState.FAILED.value == 4

    def test_enum_is_sortable_by_value(self):
        states = sorted(JobState, key=lambda s: s.value)
        assert states == [
            JobState.PENDING,
            JobState.RUNNING,
            JobState.COMPLETED,
            JobState.FAILED,
        ]
