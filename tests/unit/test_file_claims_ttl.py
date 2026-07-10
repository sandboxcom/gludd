"""Unit tests for FileClaimRegistry TTL / reap behavior (#53).

A worker that crashes without calling ``release`` must not poison a file
path forever. Claims carry a TTL; stale claims (past TTL, not refreshed by
a re-claim/heartbeat) are treated as absent by ``overlaps``/``all_claims``/
``merge_plan``/``claims_with_age`` and are actively removed by those calls
and by ``reap_stale``.

Covers:
- claim() stamps a timestamp visible via claims_with_age()
- stale claims disappear from overlaps()/all_claims()
- TTL boundary: exactly at ttl_seconds is NOT stale; just past it IS
- re-claiming (heartbeat) refreshes the timestamp and prevents reaping
- reap_stale() return value + removal + idempotent no-op when nothing stale
- live claims well under TTL are unaffected
- default constructor (no ttl_seconds/clock args) still works
- claims_with_age() shape
"""

from __future__ import annotations

from general_ludd.coordination.file_claims import FileClaimRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """Injectable monotonic-style clock for deterministic TTL tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_registry(ttl_seconds: float = 5.0, clock: FakeClock | None = None) -> tuple[FileClaimRegistry, FakeClock]:
    fake_clock = clock if clock is not None else FakeClock()
    reg = FileClaimRegistry(ttl_seconds=ttl_seconds, clock=fake_clock)
    return reg, fake_clock


# ---------------------------------------------------------------------------
# claim() stamps a timestamp
# ---------------------------------------------------------------------------


class TestClaimTimestamp:
    def test_claim_stamps_claimed_at(self) -> None:
        reg, clock = make_registry()
        clock.t = 100.0
        reg.claim("w1", ["a.py"])
        info = reg.claims_with_age()["w1"]
        assert info["claimed_at"] == 100.0
        assert info["age_seconds"] == 0.0

    def test_claims_with_age_files_sorted(self) -> None:
        reg, _clock = make_registry()
        reg.claim("w1", ["c.py", "a.py", "b.py"])
        info = reg.claims_with_age()["w1"]
        assert info["files"] == ["a.py", "b.py", "c.py"]

    def test_claims_with_age_age_seconds_advances(self) -> None:
        reg, clock = make_registry()
        reg.claim("w1", ["a.py"])
        clock.advance(2.0)
        info = reg.claims_with_age()["w1"]
        assert info["age_seconds"] == 2.0
        clock.advance(1.5)
        info = reg.claims_with_age()["w1"]
        assert info["age_seconds"] == 3.5


# ---------------------------------------------------------------------------
# Staleness / TTL expiry
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_stale_claim_invisible_to_overlaps(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["shared.py"])
        clock.advance(5.01)
        reg.claim("w2", ["shared.py"])
        # w1 is stale by the time w2 claims/queries; w2 should see no overlap.
        assert reg.overlaps("w2") == {}

    def test_stale_claim_invisible_to_all_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.01)
        assert reg.all_claims() == {}

    def test_stale_claim_invisible_to_merge_plan(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        clock.advance(5.01)
        # Both are stale now (neither refreshed) -> no contested files remain.
        assert reg.merge_plan() == {}

    def test_stale_claim_invisible_to_claims_with_age(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.01)
        assert reg.claims_with_age() == {}

    def test_exactly_at_ttl_boundary_not_stale(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.0)
        # now - claimed_at == ttl_seconds exactly -> NOT stale yet.
        assert reg.all_claims() == {"a.py": ["w1"]}

    def test_just_past_ttl_boundary_is_stale(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.000001)
        assert reg.all_claims() == {}

    def test_just_under_ttl_is_live(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(4.99)
        assert reg.all_claims() == {"a.py": ["w1"]}


# ---------------------------------------------------------------------------
# Re-claim (heartbeat) refreshes the timestamp
# ---------------------------------------------------------------------------


class TestHeartbeatRefresh:
    def test_reclaim_refreshes_timestamp_prevents_reap(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(3.0)
        # Heartbeat before going stale (would have been stale at +5.01 total
        # from the original claim, but the refresh resets the clock).
        reg.claim("w1", ["a.py"])
        clock.advance(3.0)  # total elapsed since original claim: 6.0, but only 3.0 since refresh
        assert reg.all_claims() == {"a.py": ["w1"]}

    def test_reclaim_with_new_file_set_also_refreshes(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(3.0)
        reg.claim("w1", ["b.py"])
        clock.advance(3.0)
        claims = reg.all_claims()
        assert "a.py" not in claims
        assert claims.get("b.py") == ["w1"]

    def test_without_heartbeat_claim_goes_stale(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(6.0)
        assert reg.all_claims() == {}


# ---------------------------------------------------------------------------
# reap_stale()
# ---------------------------------------------------------------------------


class TestReapStale:
    def test_reap_stale_returns_and_removes_reaped_workers(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        clock.advance(5.01)
        reaped = reg.reap_stale()
        assert reaped == ["w1", "w2"]
        assert reg.all_claims() == {}
        assert reg.claims_with_age() == {}

    def test_reap_stale_no_op_when_nothing_stale(self) -> None:
        reg, _clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        assert reg.reap_stale() == []
        assert reg.all_claims() == {"a.py": ["w1"]}

    def test_reap_stale_called_twice_second_call_empty(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.01)
        assert reg.reap_stale() == ["w1"]
        assert reg.reap_stale() == []

    def test_reap_stale_only_removes_stale_not_live(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(5.01)
        reg.claim("w2", ["b.py"])  # claimed fresh, at t=5.01
        reaped = reg.reap_stale()
        assert reaped == ["w1"]
        assert reg.all_claims() == {"b.py": ["w2"]}

    def test_reap_stale_explicit_now_argument(self) -> None:
        reg, _clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        # Pass an explicit `now` far in the future rather than advancing the clock.
        reaped = reg.reap_stale(now=100.0)
        assert reaped == ["w1"]


# ---------------------------------------------------------------------------
# Live claims under TTL are unaffected (no false positives)
# ---------------------------------------------------------------------------


class TestLiveClaimsUnaffected:
    def test_overlaps_unaffected_for_live_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        clock.advance(2.0)
        overlaps = reg.overlaps("w1")
        assert "w2" in overlaps.get("shared.py", [])

    def test_all_claims_unaffected_for_live_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["b.py"])
        clock.advance(4.0)
        claims = reg.all_claims()
        assert claims == {"a.py": ["w1"], "b.py": ["w2"]}

    def test_merge_plan_unaffected_for_live_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        clock.advance(4.0)
        assert reg.merge_plan() == {"shared.py": "union"}

    def test_should_wait_unaffected_for_live_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["shared.py"])
        reg.claim("w2", ["shared.py"])
        clock.advance(4.0)
        assert reg.should_wait("w1") == ["w2"]

    def test_reap_stale_does_not_touch_live_claims(self) -> None:
        reg, clock = make_registry(ttl_seconds=5.0)
        reg.claim("w1", ["a.py"])
        clock.advance(4.0)
        assert reg.reap_stale() == []
        assert reg.all_claims() == {"a.py": ["w1"]}


# ---------------------------------------------------------------------------
# Backward compatibility: default constructor
# ---------------------------------------------------------------------------


class TestDefaultConstructor:
    def test_default_constructor_basic_smoke(self) -> None:
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        reg.claim("w2", ["a.py"])
        overlaps = reg.overlaps("w1")
        assert "w2" in overlaps.get("a.py", [])
        assert reg.all_claims() == {"a.py": ["w1", "w2"]}
        reg.release("w1")
        assert reg.all_claims() == {"a.py": ["w2"]}

    def test_default_constructor_has_generous_ttl(self) -> None:
        """Default TTL (900s) means a claim made "just now" is never stale
        for any realistic test execution time."""
        reg = FileClaimRegistry()
        reg.claim("w1", ["a.py"])
        assert reg.all_claims() == {"a.py": ["w1"]}
        assert reg.reap_stale() == []
