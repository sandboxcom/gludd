"""Priority-aware job scheduler with cron, interval, concurrency, and pause/resume.

Schedules jobs with cron expressions or fixed intervals, respects priority
ordering and a configurable concurrency limit, and supports pause/resume.

Usage::

    from general_ludd.scheduling.job_scheduler import JobScheduler, IntervalJob, CronJob

    sched = JobScheduler(max_concurrent=3)
    sched.add(CronJob(id="cleanup", cron="0 3 * * *", priority=10, fn=cleanup_db))
    sched.add(IntervalJob(id="health", interval_seconds=30, priority=5, fn=health_check))
    sched.tick()  # -> list[Job] ready to run
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum, auto


class JobState(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class CronSchedule:
    minute: str = "*"
    hour: str = "*"
    day_of_month: str = "*"
    month: str = "*"
    day_of_week: str = "*"

    @staticmethod
    def parse(expr: str) -> CronSchedule:
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}: {expr!r}")
        return CronSchedule(
            minute=parts[0],
            hour=parts[1],
            day_of_month=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

    def matches(self, ts: float) -> bool:
        from datetime import datetime

        dt = datetime.fromtimestamp(ts, tz=UTC)
        return (
            self._field_matches(self.minute, dt.minute, 0, 59)
            and self._field_matches(self.hour, dt.hour, 0, 23)
            and self._field_matches(self.day_of_month, dt.day, 1, 31)
            and self._field_matches(self.month, dt.month, 1, 12)
            and self._field_matches(self.day_of_week, (dt.weekday() + 1) % 7, 0, 6)
        )

    def next_after(self, ts: float) -> float:
        from datetime import datetime, timedelta

        dt = datetime.fromtimestamp(ts, tz=UTC)
        for _ in range(366 * 24 * 60):
            dt += timedelta(minutes=1)
            candidate = dt.timestamp()
            if (
                candidate > ts
                and self._field_matches(self.minute, dt.minute, 0, 59)
                and self._field_matches(self.hour, dt.hour, 0, 23)
                and self._field_matches(self.day_of_month, dt.day, 1, 31)
                and self._field_matches(self.month, dt.month, 1, 12)
                and self._field_matches(self.day_of_week, (dt.weekday() + 1) % 7, 0, 6)
            ):
                return candidate
        raise RuntimeError("No next cron time found within 1 year")

    @staticmethod
    def _field_matches(pattern: str, value: int, lo: int, hi: int) -> bool:
        if pattern == "*":
            return lo <= value <= hi
        for token in pattern.split(","):
            token = token.strip()
            if "/" in token:
                base, step_str = token.split("/", 1)
                step = int(step_str)
                if base == "*":
                    base_lo, base_hi = lo, hi
                elif "-" in base:
                    base_lo, base_hi = _parse_range(base)
                else:
                    base_lo = base_hi = int(base)
                if base_lo <= value <= base_hi and (value - base_lo) % step == 0:
                    return True
            elif "-" in token:
                a, b = _parse_range(token)
                if a <= value <= b:
                    return True
            else:
                if int(token) == value:
                    return True
        return False


def _parse_range(token: str) -> tuple[int, int]:
    a_str, b_str = token.split("-", 1)
    return int(a_str), int(b_str)


@dataclass
class Job:
    id: str
    priority: int = 100
    fn: Callable[[], object] | None = None
    state: JobState = JobState.PENDING
    scheduled_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    last_run_at: float | None = None
    run_count: int = 0
    result: object = None
    error: str | None = None

    def next_time(self, now: float | None = None) -> float:
        raise NotImplementedError

    def next_matches(self, ts: float) -> bool:
        raise NotImplementedError


@dataclass
class CronJob(Job):
    cron_expr: str = "* * * * *"
    _schedule: CronSchedule | None = field(default=None, repr=False)

    def __post_init__(self):
        self._schedule = CronSchedule.parse(self.cron_expr)

    @property
    def schedule(self) -> CronSchedule:
        if self._schedule is None:
            self._schedule = CronSchedule.parse(self.cron_expr)
        return self._schedule

    def next_time(self, now: float | None = None) -> float:
        base = self.last_run_at if self.last_run_at is not None else (now if now is not None else _time.time())
        return self.schedule.next_after(base)

    def next_matches(self, ts: float) -> bool:
        return self.schedule.matches(ts)


@dataclass
class IntervalJob(Job):
    interval_seconds: float = 60.0

    def next_time(self, now: float | None = None) -> float:
        base = self.last_run_at if self.last_run_at is not None else (now if now is not None else _time.time())
        return base + self.interval_seconds

    def next_matches(self, ts: float) -> bool:
        base = self.last_run_at if self.last_run_at is not None else 0.0
        return ts >= base + self.interval_seconds


class JobScheduler:
    def __init__(self, max_concurrent: int = 5) -> None:
        if max_concurrent < 0:
            raise ValueError("max_concurrent must be >= 0")
        self._jobs: dict[str, Job] = {}
        self._max_concurrent = max_concurrent
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state == JobState.RUNNING)

    @property
    def pending_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state == JobState.PENDING)

    def add(self, job: Job) -> None:
        if job.id in self._jobs:
            raise ValueError(f"Job {job.id!r} already registered")
        self._jobs[job.id] = job

    def remove(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise KeyError(f"Job {job_id!r} not found")
        return self._jobs.pop(job_id)

    def get(self, job_id: str) -> Job:
        return self._jobs[job_id]

    def jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def tick(self, now: float | None = None, wall_clock: float | None = None) -> list[Job]:
        if now is None:
            now = _time.time()
        if wall_clock is None:
            wall_clock = now

        available: list[Job] = []
        for job in self._jobs.values():
            if job.state != JobState.PENDING:
                continue
            matches_cron = isinstance(job, CronJob) and job.next_matches(now)
            matches_interval = isinstance(job, IntervalJob) and job.next_matches(now)
            if matches_cron or matches_interval:
                available.append(job)

        if self._paused:
            return []

        available.sort(key=lambda j: j.priority)

        slots = self._max_concurrent - self.running_count
        if self._max_concurrent == 0:
            slots = 0
        if slots < 0:
            slots = 0

        dispatched: list[Job] = []
        for job in available:
            if len(dispatched) >= slots:
                break
            job.state = JobState.RUNNING
            job.started_at = now
            dispatched.append(job)

        return dispatched

    def complete(self, job_id: str, result: object = None, now: float | None = None) -> None:
        job = self._jobs[job_id]
        t = now if now is not None else _time.time()
        job.state = JobState.COMPLETED
        job.finished_at = t
        job.last_run_at = t
        job.run_count += 1
        job.result = result

    def fail(self, job_id: str, error: str, now: float | None = None) -> None:
        job = self._jobs[job_id]
        t = now if now is not None else _time.time()
        job.state = JobState.FAILED
        job.finished_at = t
        job.last_run_at = t
        job.run_count += 1
        job.error = error

    def reset(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.state = JobState.PENDING
        job.started_at = None
        job.finished_at = None
        job.result = None
        job.error = None

    def reset_all(self) -> None:
        for job in self._jobs.values():
            job.state = JobState.PENDING
            job.started_at = None
            job.finished_at = None
            job.result = None
            job.error = None
