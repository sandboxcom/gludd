"""Regression: BudgetManager.record_spend must be thread-safe (no lost updates).

record_spend does a read-modify-write of both _todo_spend and _daily_spend; it is
shared across worker threads, so the body must be serialized by a lock.
"""
from __future__ import annotations

import threading

from general_ludd.controllers.budget_manager import BudgetManager


def test_record_spend_thread_safe() -> None:
    mgr = BudgetManager(daily_limit_usd=float("inf"))

    num_threads = 10
    calls_per_thread = 100
    expected = float(num_threads * calls_per_thread)  # 1000.0

    def worker() -> None:
        for _ in range(calls_per_thread):
            mgr.record_spend("t", 1.0)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    status = mgr.get_status()
    assert status["daily_spend"] == expected


def test_record_spend_single_thread_baseline() -> None:
    mgr = BudgetManager(daily_limit_usd=float("inf"))
    for _ in range(1000):
        mgr.record_spend("single", 1.0)
    assert mgr.get_status()["daily_spend"] == 1000.0
