"""Deep watermark/checkpoint tests for SpendLimiter SPD-1 flush system.

Covers: low/high watermark triggers, checkpoint persistence, recovery from
checkpoint, sequence number tracking, and watermark monotonicity guarantees.
"""

from __future__ import annotations

from general_ludd.controllers.spend_limiter import SpendLimiter


class FakeClock:
    def __init__(self, start_time: float = 0.0) -> None:
        self.now = start_time

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


# -----------------------------------------------------------------------
# Low watermark — unflushed_records starts empty, grows, shrinks after flush
# -----------------------------------------------------------------------


class TestLowWatermark:
    def test_unflushed_empty_on_fresh_limiter(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        assert limiter.unflushed_records() == []

    def test_unflushed_grows_with_records(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=10.0)
        limiter.record(3.0, kind="infra", at=20.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 2
        assert unflushed[0][2] == 5.0
        assert unflushed[1][2] == 3.0

    def test_unflushed_returns_ascending_seq_order(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(1.0, kind="token", at=0.0)
        limiter.record(2.0, kind="token", at=0.0)
        limiter.record(3.0, kind="token", at=0.0)
        unflushed = limiter.unflushed_records()
        seqs = [rec[0] for rec in unflushed]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))

    def test_unflushed_readonly_no_side_effects(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=0.0)
        first = limiter.unflushed_records()
        second = limiter.unflushed_records()
        assert first == second


# -----------------------------------------------------------------------
# High watermark — mark_flushed advances the waterline
# -----------------------------------------------------------------------


class TestHighWatermark:
    def test_mark_flushed_advances_watermark(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=10.0)
        limiter.record(3.0, kind="infra", at=20.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 2
        last_seq = unflushed[-1][0]
        limiter.mark_flushed(last_seq)
        assert limiter.unflushed_records() == []

    def test_mark_flushed_monotonic_no_regress(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=10.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 1
        top_seq = unflushed[-1][0]
        limiter.mark_flushed(top_seq)
        limiter.mark_flushed(0)
        assert limiter.unflushed_records() == []

    def test_mark_flushed_with_future_seq_noop(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=10.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 1
        unflushed[-1][0]
        limiter.mark_flushed(999999)
        assert limiter.unflushed_records() == []

    def test_mark_flushed_partial_batch(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(1.0, kind="token", at=0.0)
        limiter.record(2.0, kind="token", at=0.0)
        limiter.record(3.0, kind="token", at=0.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 3
        middle_seq = unflushed[1][0]
        limiter.mark_flushed(middle_seq)
        remaining = limiter.unflushed_records()
        assert len(remaining) == 1
        assert remaining[0][2] == 3.0


# -----------------------------------------------------------------------
# Checkpoint persistence — snapshot + restore preserves watermark
# -----------------------------------------------------------------------


class TestCheckpointPersistence:
    def test_restore_seeds_watermark_past_all_records(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(10.0, kind="token", at=0.0)
        limiter1.record(20.0, kind="token", at=0.0)
        limiter1.mark_flushed(999)
        assert limiter1.unflushed_records() == []
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        assert limiter2.unflushed_records() == []

    def test_restore_watermark_prevents_reinsert(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(5.0, kind="token", at=0.0)
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        assert limiter2.unflushed_records() == []

    def test_restore_then_record_produces_unflushed(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(5.0, kind="token", at=0.0)
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        limiter2.record(3.0, kind="infra", at=0.0)
        unflushed = limiter2.unflushed_records()
        assert len(unflushed) == 1
        assert unflushed[0][2] == 3.0


# -----------------------------------------------------------------------
# Recovery from checkpoint — full flush cycle after restart
# -----------------------------------------------------------------------


class TestRecoveryFromCheckpoint:
    def test_full_flush_cycle_persistence(self) -> None:
        clock = FakeClock(0.0)
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        limiter.record(10.0, kind="token", at=0.0)
        limiter.record(5.0, kind="token", at=0.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 2
        limiter.mark_flushed(unflushed[-1][0])

        limiter.record(7.0, kind="infra", at=0.0)
        unflushed2 = limiter.unflushed_records()
        assert len(unflushed2) == 1
        assert unflushed2[0][2] == 7.0

    def test_recovery_after_restart_budget_preserved(self) -> None:
        limiter1 = SpendLimiter(limit_usd=50.0, window_seconds=10000.0, clock=FakeClock(0.0))
        limiter1.record(30.0, kind="token", at=0.0)
        limiter1.record(3.0, kind="token", at=0.0)
        limiter1.record(7.0, kind="token", at=0.0)
        unflushed = limiter1.unflushed_records()
        limiter1.mark_flushed(unflushed[-1][0])
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=50.0, window_seconds=10000.0, clock=FakeClock(100.0))
        limiter2.restore(snapshot)
        assert limiter2.window_spend(now=0.0) == 40.0
        assert limiter2.try_charge(15.0, kind="token", at=0.0) is False

    def test_recovery_no_duplicate_on_flush(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(5.0, kind="token", at=0.0)
        limiter1.record(3.0, kind="token", at=0.0)
        unflushed = limiter1.unflushed_records()
        limiter1.mark_flushed(unflushed[-1][0])
        assert limiter1.unflushed_records() == []
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        assert limiter2.unflushed_records() == []


# -----------------------------------------------------------------------
# Sequence number tracking — monotonicity, overflow edge, reset behavior
# -----------------------------------------------------------------------


class TestSequenceNumberTracking:
    def test_seq_monotonically_increasing(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        seqs: list[int] = []
        for i in range(10):
            limiter.record(float(i + 1), kind="token", at=0.0)
            unflushed = limiter.unflushed_records()
            seqs.append(unflushed[-1][0])
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))

    def test_seq_continues_after_mark_flushed(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(1.0, kind="token", at=0.0)
        seq_before = limiter.unflushed_records()[-1][0]
        limiter.mark_flushed(seq_before)
        limiter.record(2.0, kind="token", at=0.0)
        seq_after = limiter.unflushed_records()[-1][0]
        assert seq_after > seq_before

    def test_seq_continues_after_restore(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter1.record(1.0, kind="token", at=0.0)
        limiter1.record(2.0, kind="token", at=0.0)
        snapshot = limiter1.snapshot()

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        limiter2.record(3.0, kind="token", at=0.0)
        unflushed = limiter2.unflushed_records()
        assert len(unflushed) == 1
        assert unflushed[0][2] == 3.0

    def test_try_charge_increments_seq(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        assert limiter.try_charge(5.0, kind="token", at=0.0) is True
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 1
        seq1 = unflushed[0][0]
        assert limiter.try_charge(3.0, kind="token", at=0.0) is True
        unflushed2 = limiter.unflushed_records()
        assert len(unflushed2) == 2
        assert unflushed2[1][0] > seq1

    def test_reserve_reservation_seq_tracking(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        token = limiter.reserve(10.0)
        assert token is not None
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 1
        limiter.record(5.0, kind="token", at=0.0)
        unflushed2 = limiter.unflushed_records()
        assert len(unflushed2) == 2

    def test_commit_updates_reservation_at_same_seq(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        token = limiter.reserve(10.0)
        assert token is not None
        unflushed = limiter.unflushed_records()
        seq_reserved = unflushed[0][0]
        assert unflushed[0][2] == 10.0
        ok = limiter.commit(token, 7.5, kind="token", at=0.0)
        assert ok
        unflushed2 = limiter.unflushed_records()
        assert unflushed2[0][0] == seq_reserved
        assert unflushed2[0][2] == 7.5


# -----------------------------------------------------------------------
# Edge cases — empty records, zero cost, watermark at boundary
# -----------------------------------------------------------------------


class TestWatermarkEdgeCases:
    def test_unflushed_with_zero_cost_records(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(0.0, kind="token", at=0.0)
        unflushed = limiter.unflushed_records()
        assert len(unflushed) == 1
        assert unflushed[0][2] == 0.0

    def test_watermark_persists_across_empty_snapshot_roundtrip(self) -> None:
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        snapshot = limiter1.snapshot()
        assert snapshot == []
        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        assert limiter2.unflushed_records() == []

    def test_mark_flushed_idempotent_same_seq(self) -> None:
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter.record(5.0, kind="token", at=0.0)
        seq = limiter.unflushed_records()[-1][0]
        limiter.mark_flushed(seq)
        limiter.mark_flushed(seq)
        assert limiter.unflushed_records() == []

    def test_restore_double_count_prevention(self) -> None:
        clock = FakeClock(0.0)
        limiter = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        limiter.record(10.0, kind="token", at=0.0)
        snapshot = limiter.snapshot()
        limiter.restore(snapshot)
        assert limiter.window_spend(now=0.0) == 10.0

    def test_restore_unflushed_is_empty_for_persisted(self) -> None:
        clock = FakeClock(0.0)
        limiter1 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=clock)
        limiter1.record(5.0, kind="token", at=0.0)
        snapshot = limiter1.snapshot()
        limiter1.record(3.0, kind="token", at=0.0)
        unflushed_before = limiter1.unflushed_records()
        assert len(unflushed_before) == 2

        limiter2 = SpendLimiter(limit_usd=100.0, window_seconds=3600.0, clock=FakeClock(0.0))
        limiter2.restore(snapshot)
        assert limiter2.unflushed_records() == []
