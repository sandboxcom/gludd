"""Tests for SpendLimiter: rolling-window soft cap, clock injection, boundary math."""

from __future__ import annotations

import pytest

from general_ludd.controllers.spend_limiter import SpendLimiter


def _make_limiter(limit_usd: float, window_seconds: float) -> tuple[SpendLimiter, list[float]]:
    """Return a SpendLimiter and a mutable list acting as a fake monotonic clock."""
    clock_val: list[float] = [0.0]

    def fake_clock() -> float:
        return clock_val[0]

    limiter = SpendLimiter(limit_usd=limit_usd, window_seconds=window_seconds, clock=fake_clock)
    return limiter, clock_val


class TestWindowSpend:
    def test_empty_window_is_zero(self) -> None:
        sl, _ = _make_limiter(10.0, 3600.0)
        assert sl.window_spend() == pytest.approx(0.0)

    def test_single_record_within_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.5, kind="token")
        assert sl.window_spend() == pytest.approx(1.5)

    def test_multiple_records_within_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 100.0
        sl.record(1.0, kind="token")
        sl.record(2.0, kind="infra")
        assert sl.window_spend() == pytest.approx(3.0)

    def test_old_records_pruned_outside_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")  # at t=0, window is [t-3600, t]
        clock[0] = 3601.0             # now t=3601; the record at t=0 is outside [t-3600, t]
        assert sl.window_spend() == pytest.approx(0.0)

    def test_records_on_boundary_included(self) -> None:
        """A record recorded at exactly (now - window_seconds) must still be included."""
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(3.0, kind="token")
        clock[0] = 3600.0   # record is exactly at the boundary (t=0, window starts at t-3600=0)
        assert sl.window_spend() == pytest.approx(3.0)

    def test_records_just_after_boundary_excluded(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(3.0, kind="token")
        clock[0] = 3600.1   # just past boundary
        assert sl.window_spend() == pytest.approx(0.0)

    def test_mixed_window_keeps_only_recent(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 0.0
        sl.record(5.0, kind="token")   # old record
        clock[0] = 1800.0
        sl.record(2.0, kind="infra")   # still in window when clock=3601
        clock[0] = 3601.0
        # at t=3601: window is [1, 3601]; t=0 is excluded, t=1800 is included
        assert sl.window_spend() == pytest.approx(2.0)

    def test_record_with_explicit_at_parameter(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1000.0
        sl.record(4.0, kind="token", at=999.0)  # pass explicit timestamp
        assert sl.window_spend() == pytest.approx(4.0)


class TestRemaining:
    def test_remaining_starts_at_limit(self) -> None:
        sl, _ = _make_limiter(20.0, 3600.0)
        assert sl.remaining() == pytest.approx(20.0)

    def test_remaining_decreases_after_record(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.remaining() == pytest.approx(15.0)

    def test_remaining_never_negative(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")  # exceeds limit
        assert sl.remaining() == pytest.approx(0.0)

    def test_remaining_zero_when_fully_consumed(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.remaining() == pytest.approx(0.0)


class TestWouldExceed:
    def test_below_limit_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert not sl.would_exceed(10.0)  # 5 + 10 = 15 <= 20

    def test_exactly_at_limit_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")
        assert not sl.would_exceed(10.0)  # 10 + 10 = 20, exactly at limit -> not exceeded

    def test_above_limit_would_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(10.0, kind="token")
        assert sl.would_exceed(11.0)   # 10 + 11 = 21 > 20

    def test_at_zero_remaining_always_exceeds(self) -> None:
        sl, clock = _make_limiter(5.0, 3600.0)
        clock[0] = 1.0
        sl.record(5.0, kind="token")
        assert sl.would_exceed(0.01)   # even tiny projected cost exceeds

    def test_zero_projected_at_full_window_does_not_exceed(self) -> None:
        sl, clock = _make_limiter(20.0, 3600.0)
        clock[0] = 1.0
        sl.record(20.0, kind="token")  # exactly at limit
        assert not sl.would_exceed(0.0)

    def test_roughly_met_boundary(self) -> None:
        """Verify 'roughly met not exceeded': if remaining > 0, a dispatch that
        fits remaining is allowed; once remaining <= 0, any positive projected
        cost is deferred."""
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(9.99, kind="token")
        # remaining ~ 0.01; projected 0.01 fits
        assert not sl.would_exceed(0.01)
        # projected 0.02 would push over
        assert sl.would_exceed(0.02)


class TestMixedCostKinds:
    def test_token_and_infra_costs_sum_in_window(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(2.0, kind="token")
        sl.record(3.0, kind="infra")
        assert sl.window_spend() == pytest.approx(5.0)
        assert sl.remaining() == pytest.approx(5.0)

    def test_model_kwarg_accepted(self) -> None:
        sl, clock = _make_limiter(10.0, 3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token", model="claude-3-5-sonnet-20241022")
        assert sl.window_spend() == pytest.approx(1.0)


class TestDefaultClock:
    def test_default_clock_uses_monotonic(self) -> None:
        """SpendLimiter without injected clock must not raise on construction or use."""
        sl = SpendLimiter(limit_usd=5.0, window_seconds=60.0)
        sl.record(0.01, kind="token")
        assert sl.window_spend() > 0.0
        assert sl.remaining() < 5.0


class TestRestoreFutureTimestampClamp:
    """Security tests: restore() must clamp future timestamps to now.

    A hostile restore() payload with ts = now + 9999 creates a record that
    would NEVER expire from the rolling window (it is always within
    [now - window_seconds, now] since future ts > now > cutoff).  That lets
    an attacker peg the window spend artificially high indefinitely — a
    denial-of-service against legitimate dispatches.

    Fix: any ts > current_clock() must be clamped to current_clock() so the
    restored record ages out of the window on the normal schedule.
    """

    def test_restore_future_timestamp_expires_normally(self) -> None:
        """A record with a future timestamp must be clamped to now so it ages
        out of the window at the normal time rather than living forever."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=60.0)
        clock[0] = 100.0
        # Inject a record with timestamp 9999 seconds in the future.
        sl.restore([(100.0 + 9999.0, 5.0)])
        # At t=100, the record should be visible (it was clamped to ts=100).
        assert sl.window_spend(now=100.0) == pytest.approx(5.0)
        # At t=161 (100+61), the record at ts=100 should have expired from
        # the 60-second window (cutoff=101, ts=100 < 101 -> pruned).
        assert sl.window_spend(now=161.0) == pytest.approx(0.0)

    def test_restore_future_timestamp_does_not_inflate_forever(self) -> None:
        """Without clamping, a far-future timestamp record is never pruned.
        After clamping, it must age out normally."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1000.0
        # Far-future timestamp: now + 100000 seconds.
        sl.restore([(1000.0 + 100_000.0, 3.0)])
        # Advance clock well past one window (4601 s later — window = 3600 s).
        # If the ts were NOT clamped it would still be in the window.
        # After clamping to ts=1000, cutoff at t=4601 is 4601-3600=1001 > 1000 -> pruned.
        assert sl.window_spend(now=4601.0) == pytest.approx(0.0)

    def test_restore_past_timestamps_unchanged(self) -> None:
        """Timestamps legitimately in the past must not be altered."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1000.0
        # A record 100s in the past — still within the 3600s window.
        sl.restore([(900.0, 2.0)])
        assert sl.window_spend(now=1000.0) == pytest.approx(2.0)
        # It must expire at t=900+3600=4500, i.e. still present at t=4499,
        # absent at t=4501.
        assert sl.window_spend(now=4499.0) == pytest.approx(2.0)
        assert sl.window_spend(now=4501.0) == pytest.approx(0.0)


class TestRestoreValidation:
    """Security tests: restore() must drop invalid (negative / non-finite) cost records.

    A hostile restore() payload carrying cost=-1000.0 would deflate window_spend()
    and let an attacker evade the cap.  NaN/inf costs must also be rejected because
    NaN compares False against every limit and inf overflows the sum.
    """

    def test_restore_negative_cost_does_not_deflate_spend(self) -> None:
        """Negative cost in restore() must be silently dropped; the window spend
        must remain at its pre-restore level and the cap must still be enforced."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        # Record legitimate spend of 0.9 USD (near cap).
        sl.record(0.9, kind="token")
        # Attempt to deflate via a negative-cost restore record.
        sl.restore([(10.0, -1000.0)])
        # Window spend must not be deflated to -999.1.
        assert sl.window_spend() == pytest.approx(0.9)
        # Cap must still be enforced: 0.9 + 0.15 > 1.0.
        assert sl.would_exceed(0.15) is True

    def test_restore_nan_cost_is_dropped(self) -> None:
        """NaN cost in restore() must be silently dropped; window spend unchanged."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        sl.record(0.9, kind="token")
        sl.restore([(10.0, float("nan"))])
        assert sl.window_spend() == pytest.approx(0.9)
        assert sl.would_exceed(0.15) is True

    def test_restore_inf_cost_is_dropped(self) -> None:
        """Positive inf, negative inf costs in restore() must all be dropped."""
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=60.0)
        clock[0] = 10.0
        sl.record(0.9, kind="token")
        sl.restore([
            (10.0, float("inf")),
            (10.0, float("-inf")),
        ])
        assert sl.window_spend() == pytest.approx(0.9)
        assert sl.would_exceed(0.15) is True


class TestRecordGuard:
    """Security tests: record() must RAISE on negative / non-finite cost (live path).

    Unlike restore() which silently drops bad records (offline/deserialization path),
    record() is the LIVE path called by try_charge() on every dispatch.  A negative
    cost here would DEFLATE window_spend(), letting an attacker evade the cap by
    injecting a negative charge.  NaN/inf costs would poison sum() so would_exceed()
    returns False (unlimited spend).  We raise ValueError so the caller sees a hard
    error and cannot continue as if the charge were accepted.
    """

    def test_record_negative_cost_raises(self) -> None:
        """record() with a negative cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError, match="non-negative"):
            sl.record(-1.0, kind="token")

    def test_record_negative_cost_does_not_add_to_window(self) -> None:
        """After a failed record() call the window must be unchanged (no partial write)."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(2.0, kind="token")
        with pytest.raises(ValueError):
            sl.record(-1.0, kind="token")
        # Window must still be 2.0, not 1.0 (deflated) or anything else.
        assert sl.window_spend() == pytest.approx(2.0)

    def test_record_nan_cost_raises(self) -> None:
        """record() with NaN cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("nan"), kind="token")

    def test_record_nan_does_not_poison_window(self) -> None:
        """A failed record(NaN) must leave window_spend() unaffected (no NaN in sum)."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(3.0, kind="token")
        with pytest.raises(ValueError):
            sl.record(float("nan"), kind="token")
        assert sl.window_spend() == pytest.approx(3.0)
        # Cap enforcement must still work (not poisoned to always-False).
        assert sl.would_exceed(8.0) is True  # 3 + 8 = 11 > 10

    def test_record_inf_cost_raises(self) -> None:
        """record() with +inf cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("inf"), kind="token")

    def test_record_neg_inf_cost_raises(self) -> None:
        """record() with -inf cost must raise ValueError."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        with pytest.raises(ValueError):
            sl.record(float("-inf"), kind="token")

    def test_record_positive_cost_works(self) -> None:
        """The normal positive-cost path must still succeed after the guard is added."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(0.05, kind="token")
        assert sl.window_spend() == pytest.approx(0.05)

    def test_record_zero_cost_works(self) -> None:
        """Zero is a valid (non-negative, finite) cost and must be accepted."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(0.0, kind="infra")
        assert sl.window_spend() == pytest.approx(0.0)

    def test_try_charge_negative_cost_refused_no_headroom_created(self) -> None:
        """try_charge() with a negative cost must not create phantom headroom.

        A negative cost passed through try_charge → record() would reduce
        window_spend() and let subsequent charges bypass the cap.  The guard
        in record() must raise, which propagates out of try_charge() so the
        cap is never evaded.
        """
        sl, clock = _make_limiter(limit_usd=1.0, window_seconds=3600.0)
        clock[0] = 1.0
        # Fill the cap to within 0.05 USD.
        sl.record(0.95, kind="token")
        assert sl.remaining() == pytest.approx(0.05)
        # Attempt to inject negative cost via try_charge — must raise.
        with pytest.raises(ValueError):
            sl.try_charge(-1.0, kind="token")
        # Remaining must be unchanged (not inflated to 1.05).
        assert sl.remaining() == pytest.approx(0.05)

    def test_try_charge_positive_cost_still_recorded(self) -> None:
        """try_charge() normal positive path must still record the charge."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        accepted = sl.try_charge(2.0, kind="token")
        assert accepted is True
        assert sl.window_spend() == pytest.approx(2.0)


class TestFlushWatermark:
    """SPD-1: the limiter-side watermark API consumed by a periodic EventLoop
    flush phase (not implemented here) that persists in-memory records to the
    ``spend_records`` table.  These tests only exercise the limiter's own
    bookkeeping: unflushed_records() scoping, mark_flushed() monotonicity,
    restore()'s dedupe seeding, and thread-safety of the seq counter.
    """

    def test_unflushed_records_empty_when_nothing_recorded(self) -> None:
        sl, _ = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        assert sl.unflushed_records() == []

    def test_unflushed_records_returns_all_before_any_flush(self) -> None:
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token", project_id="proj-a")
        sl.record(2.0, kind="infra", project_id="proj-b")
        unflushed = sl.unflushed_records()
        assert len(unflushed) == 2
        # (seq, ts, cost, project_id)
        assert unflushed[0] == (1, 1.0, 1.0, "proj-a")
        assert unflushed[1] == (2, 1.0, 2.0, "proj-b")

    def test_mark_flushed_scopes_unflushed_records(self) -> None:
        """After mark_flushed(upto_seq), only records with seq > upto_seq remain."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.record(2.0, kind="token")  # seq=2
        sl.record(3.0, kind="token")  # seq=3
        assert len(sl.unflushed_records()) == 3
        sl.mark_flushed(2)
        remaining = sl.unflushed_records()
        assert len(remaining) == 1
        assert remaining[0][0] == 3
        assert remaining[0][2] == pytest.approx(3.0)

    def test_mark_flushed_full_drain(self) -> None:
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")
        sl.record(2.0, kind="token")
        sl.mark_flushed(2)
        assert sl.unflushed_records() == []

    def test_mark_flushed_is_monotonic_noop_on_lower_value(self) -> None:
        """A lower (or equal) mark_flushed() call must never move the
        watermark backwards — that would resurrect already-flushed records
        as unflushed and cause duplicate DB inserts."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.record(2.0, kind="token")  # seq=2
        sl.record(3.0, kind="token")  # seq=3
        sl.mark_flushed(3)
        assert sl.unflushed_records() == []
        # Regress with a lower value -> must be a no-op.
        sl.mark_flushed(1)
        assert sl.unflushed_records() == []
        # Equal value -> also a no-op (not an error).
        sl.mark_flushed(3)
        assert sl.unflushed_records() == []

    def test_mark_flushed_advances_only_forward(self) -> None:
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.record(2.0, kind="token")  # seq=2
        sl.mark_flushed(1)
        assert len(sl.unflushed_records()) == 1
        # Advancing forward from 1 -> 2 must further shrink the unflushed set.
        sl.mark_flushed(2)
        assert sl.unflushed_records() == []
        # Attempting to go back down to 1 must not resurrect seq=2 as flushed
        # again in a way that breaks anything — it's simply ignored.
        sl.mark_flushed(1)
        assert sl.unflushed_records() == []

    def test_restore_ingested_records_never_appear_unflushed(self) -> None:
        """The critical dedupe property: restore() must seed the watermark
        PAST every ingested record so a post-restart flush never re-INSERTs
        rows that were already persisted before the restart."""
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 100.0
        sl.restore([(90.0, 1.0, "proj-a"), (95.0, 2.0, None)])
        assert sl.unflushed_records() == []
        # window_spend must still reflect the restored records (restore()
        # ingesting them for the watermark must not affect cap math).
        assert sl.window_spend() == pytest.approx(3.0)

    def test_restore_then_new_charge_only_new_charge_is_unflushed(self) -> None:
        sl, clock = _make_limiter(limit_usd=10.0, window_seconds=3600.0)
        clock[0] = 100.0
        sl.restore([(90.0, 1.0, "proj-a")])
        assert sl.unflushed_records() == []
        sl.record(5.0, kind="token", project_id="proj-b")
        unflushed = sl.unflushed_records()
        assert len(unflushed) == 1
        assert unflushed[0][2] == pytest.approx(5.0)
        assert unflushed[0][3] == "proj-b"

    def test_seq_survives_interleaved_charges_and_flushes(self) -> None:
        """seq must keep incrementing monotonically across interleaved
        record/try_charge and mark_flushed calls — never reused, never reset."""
        sl, clock = _make_limiter(limit_usd=100.0, window_seconds=3600.0)
        clock[0] = 1.0
        sl.record(1.0, kind="token")  # seq=1
        sl.mark_flushed(1)
        sl.record(1.0, kind="token")  # seq=2
        sl.try_charge(1.0, kind="token")  # seq=3
        assert [rec[0] for rec in sl.unflushed_records()] == [2, 3]
        sl.mark_flushed(2)
        assert [rec[0] for rec in sl.unflushed_records()] == [3]
        sl.record(1.0, kind="token")  # seq=4
        assert [rec[0] for rec in sl.unflushed_records()] == [3, 4]
        sl.mark_flushed(4)
        assert sl.unflushed_records() == []
        # One more charge after full drain must continue from seq=5, not reset.
        sl.record(1.0, kind="token")  # seq=5
        assert [rec[0] for rec in sl.unflushed_records()] == [5]

    def test_thread_safety_smoke_charges_from_n_threads_while_flushing(self) -> None:
        """N threads charging concurrently while a flusher repeatedly reads
        unflushed_records() + mark_flushed() must never lose or duplicate a
        seq, and the final unflushed count must reconcile with total charges
        minus what was actually marked flushed."""
        import threading as _threading

        sl, clock = _make_limiter(limit_usd=1_000_000.0, window_seconds=3600.0)
        clock[0] = 1.0
        n_threads = 8
        charges_per_thread = 50
        stop = _threading.Event()
        flushed_seqs: list[int] = []
        flush_lock = _threading.Lock()

        def charger() -> None:
            for _ in range(charges_per_thread):
                sl.record(0.01, kind="token")

        def flusher() -> None:
            while not stop.is_set():
                pending = sl.unflushed_records()
                if pending:
                    upto = max(rec[0] for rec in pending)
                    sl.mark_flushed(upto)
                    with flush_lock:
                        flushed_seqs.append(upto)

        charger_threads = [_threading.Thread(target=charger) for _ in range(n_threads)]
        flusher_thread = _threading.Thread(target=flusher)
        flusher_thread.start()
        for t in charger_threads:
            t.start()
        for t in charger_threads:
            t.join()
        # Drain any remaining unflushed records after all charges landed.
        pending = sl.unflushed_records()
        if pending:
            upto = max(rec[0] for rec in pending)
            sl.mark_flushed(upto)
        stop.set()
        flusher_thread.join()

        total_charges = n_threads * charges_per_thread
        # Every seq from 1..total_charges must have been assigned exactly
        # once (no duplicate/lost seq under concurrent record() calls) and
        # everything must end up flushed.
        assert sl.unflushed_records() == []
        with sl._lock:  # test-only introspection of seq bookkeeping
            assert sl._seq == total_charges
            assert sl._last_flushed_seq == total_charges
        # snapshot() must still expose only the public 3-tuple shape (no seq leak).
        snap = sl.snapshot()
        assert len(snap) == total_charges
        assert all(len(rec) == 3 for rec in snap)
