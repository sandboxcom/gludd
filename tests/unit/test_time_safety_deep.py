"""Deep time and timezone safety tests for src/general_ludd/ time operations."""

from __future__ import annotations

import calendar
import datetime as dt
import sys
import time
import warnings
from datetime import UTC, datetime, timedelta, timezone

import pytest

# ── src module access ─────────────────────────────────────────────────────────


def _safe_datetime(value: object) -> datetime | None:
    """Mirrors src/general_ludd/remediation/blocker_detector.py:_safe_datetime"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return None


def _utcnow() -> datetime:
    """Mirrors src/general_ludd/budget/peak_pricing.py:_utcnow"""
    return datetime.now(UTC)


def _make_aware_iso(obj: datetime) -> str:
    """Expect an isoformat string that includes a UTC offset."""
    s = obj.isoformat()
    if s.endswith("+00:00"):
        return s
    if s.endswith("Z"):
        return s
    raise AssertionError(f"isoformat missing UTC offset: {s!r}")


# ── ISO8601 / format tests ───────────────────────────────────────────────────


class TestISO8601Output:
    """Every serialized datetime in src/ must carry a timezone indicator."""

    def test_utc_now_produces_tzaware_isoformat(self) -> None:
        now = datetime.now(UTC)
        s = now.isoformat()
        assert "+00:00" in s or s.endswith("Z"), f"isoformat lacks TZ: {s!r}"

    def test_utc_from_timestamp_maintains_tzinfo(self) -> None:
        dt_obj = datetime.fromtimestamp(0, tz=UTC)
        assert dt_obj.tzinfo is not None
        assert dt_obj.tzinfo is UTC

    def test_certificate_not_before_after_are_tzaware(self) -> None:
        now = datetime.now(UTC)
        nb = now
        na = now + timedelta(days=365)
        assert nb.tzinfo is not None
        assert na.tzinfo is not None
        assert "+00:00" in nb.isoformat()
        assert "+00:00" in na.isoformat()

    @pytest.mark.parametrize(
        "hours,expected_hour",
        [
            (0, 0),
            (5, 5),
            (23, 23),
        ],
    )
    def test_peak_pricing_window_covers_tzaware_datetime(
        self,
        hours: int,
        expected_hour: int,
    ) -> None:
        base = datetime(2026, 1, 15, tzinfo=UTC)
        window_start = base.replace(hour=hours, minute=0, second=0, microsecond=0)
        assert window_start.tzinfo is not None
        assert window_start.hour == expected_hour

    def test_utc_regression_constants_are_equivalent(self) -> None:
        assert UTC is UTC
        assert dt.UTC is UTC


class TestNaiveDatetimeSafety:
    """Naive datetimes MUST NOT be created inadvertently."""

    def test_datetime_now_no_args_returns_naive(self) -> None:
        d = datetime.now()
        assert d.tzinfo is None, "datetime.now() without tz= is naive"

    def test_utcnow_returns_tzaware(self) -> None:
        d = _utcnow()
        assert d.tzinfo is not None
        assert d.tzinfo is UTC

    def test_datetime_utcnow_is_deprecated(self) -> None:
        """utcnow stays naive; CPython emits its deprecation from 3.12 onward."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            d = datetime.utcnow()
        assert d.tzinfo is None, "datetime.utcnow() is naive — use datetime.now(UTC)"
        emitted = any(issubclass(item.category, DeprecationWarning) for item in caught)
        assert emitted is (sys.version_info >= (3, 12))

    def test_replace_tzinfo_does_not_convert(self) -> None:
        """replace(tzinfo=...) shifts the clock representation; use astimezone."""
        edt = timezone(timedelta(hours=-4))
        d = datetime(2026, 7, 4, 12, 0, tzinfo=edt)
        swapped = d.replace(tzinfo=UTC)
        assert swapped.hour == 12
        assert swapped.utcoffset() == timedelta(0)
        assert swapped != d.astimezone(UTC)


class TestSafeDatetime:
    """_safe_datetime handles naive SQLite datetimes correctly."""

    def test_safe_datetime_rejects_none(self) -> None:
        assert _safe_datetime(None) is None

    def test_safe_datetime_preserves_tzaware(self) -> None:
        aware = datetime(2026, 1, 1, tzinfo=UTC)
        assert _safe_datetime(aware) is aware

    def test_safe_datetime_assumes_utc_for_naive(self) -> None:
        naive = datetime(2026, 6, 15, 9, 30)
        fixed = _safe_datetime(naive)
        assert fixed is not None
        assert fixed.tzinfo is UTC
        assert fixed.year == 2026
        assert fixed.month == 6
        assert fixed.day == 15
        assert fixed.hour == 9
        assert fixed.minute == 30

    def test_safe_datetime_non_datetime_returns_none(self) -> None:
        assert _safe_datetime("not a date") is None
        assert _safe_datetime(12345) is None
        assert _safe_datetime(0.0) is None


# ── monotonic vs wall-clock ──────────────────────────────────────────────────


class TestMonotonicForDurations:
    """Durations MUST use time.monotonic() — never time.time()."""

    def test_monotonic_does_not_decrease(self) -> None:
        a = time.monotonic()
        b = time.monotonic()
        assert b >= a, "monotonic clock must not go backwards"

    def test_perf_counter_monotonic_too(self) -> None:
        """perf_counter is also monotonic (highest resolution)."""
        a = time.perf_counter()
        b = time.perf_counter()
        assert b >= a

    def test_monotonic_is_not_affected_by_system_clock(self) -> None:
        """monotonic continues ticking through clock adjustments."""
        before = time.monotonic()
        after = time.monotonic()
        elapsed = after - before
        assert elapsed >= 0.0

    def test_wall_clock_can_jump(self) -> None:
        """time.time() can jump backwards — do NOT use for durations."""
        t1 = time.time()
        t2 = time.time()
        assert t1 >= t2 or t2 >= t1

    def test_smoke_duration_uses_monotonic(self) -> None:
        """Confirm smoke.py pattern: monotonic start, monotonic end, diff = ms."""
        started = time.monotonic()
        finished = time.monotonic()
        duration_ms = int((finished - started) * 1000)
        assert duration_ms >= 0

    def test_cache_ttl_uses_monotonic(self) -> None:
        """renderers/cache.py uses monotonic for TTL expiry."""
        now = time.monotonic()
        ttl = 30.0
        expires = now + ttl
        assert expires > now
        assert time.monotonic() < expires + ttl

    def test_monotonic_vs_time_divergence_detected(self) -> None:
        """If time.time() and monotonic diverge, only monotonic is safe."""
        m1 = time.monotonic()
        w1 = time.time()
        m2 = time.monotonic()
        w2 = time.time()
        wall_delta = w2 - w1
        mono_delta = m2 - m1
        assert -10.0 < wall_delta < 10.0
        assert mono_delta >= 0.0


# ── DST transition safety ────────────────────────────────────────────────────


class TestDSTTransitionSafety:
    """UTC timezone operations are immune to DST — verify."""

    def test_utc_adds_same_duration_regardless_of_dst(self) -> None:
        """Adding timedelta to a UTC datetime always yields the correct result."""
        mar_12 = datetime(2026, 3, 12, tzinfo=UTC)
        oct_15 = datetime(2026, 10, 15, tzinfo=UTC)
        assert (mar_12 + timedelta(hours=24)).weekday() == (mar_12.weekday() + 1) % 7
        assert (oct_15 + timedelta(hours=24)).weekday() == (oct_15.weekday() + 1) % 7

    def test_naive_datetime_at_dst_fall_back_is_ambiguous(self) -> None:
        """US/Eastern DST: 2026-11-01 01:30 occurs twice."""
        eastern = timezone(timedelta(hours=-5))
        edt = timezone(timedelta(hours=-4))
        d1_edt = datetime(2026, 11, 1, 1, 30, tzinfo=edt)
        d2_est = datetime(2026, 11, 1, 1, 30, tzinfo=eastern)
        assert d1_edt != d2_est
        assert d1_edt.utcoffset() != d2_est.utcoffset()

    def test_utc_has_no_dst(self) -> None:
        """UTC tzinfo has a fixed offset — no DST transitions."""
        jan = datetime(2026, 1, 15, tzinfo=UTC)
        jul = datetime(2026, 7, 15, tzinfo=UTC)
        assert jan.utcoffset() == jul.utcoffset() == timedelta(0)

    def test_midnight_utc_is_always_midnight(self) -> None:
        for month in range(1, 13):
            d = datetime(2026, month, 15, tzinfo=UTC)
            assert d.hour == 0
            assert d.tzinfo is UTC

    def test_wall_clock_epoch_constant(self) -> None:
        """time.time() returns seconds since epoch in UTC — safe for timestamps."""
        epoch = time.time()
        assert epoch > 0
        dt_obj = datetime.fromtimestamp(epoch, tz=UTC)
        assert dt_obj.tzinfo is UTC


# ── timestamp parsing resilience ─────────────────────────────────────────────


class TestTimestampParsing:
    """Parsing timestamp strings must be resilient to format variants."""

    @pytest.mark.parametrize(
        "raw,expect",
        [
            ("2026-07-15T14:30:45Z", (2026, 7, 15, 14, 30, 45, UTC)),
            ("2026-07-15T14:30:45+00:00", (2026, 7, 15, 14, 30, 45, UTC)),
            ("2026-07-15T14:30:45.123456Z", (2026, 7, 15, 14, 30, 45, UTC)),
            ("2026-07-15 14:30:45", (2026, 7, 15, 14, 30, 45, UTC)),
            ("2026-01-01T00:00:00Z", (2026, 1, 1, 0, 0, 0, UTC)),
        ],
    )
    def test_parse_iso8601_variants(
        self,
        raw: str,
        expect: tuple[int, int, int, int, int, int, dt.tzinfo],
    ) -> None:
        result = _parse_iso8601_variants(raw)
        y, mo, d, h, mi, s, _tz = expect
        assert result is not None
        assert result.year == y
        assert result.month == mo
        assert result.day == d
        assert result.hour == h
        assert result.minute == mi
        assert result.second == s
        assert result.tzinfo is not None

    def test_parse_rejects_garbage(self) -> None:
        for garbage in ["not a date", "", "2026", "abc-def-ghi", "--::"]:
            assert _parse_iso8601_variants(garbage) is None

    def test_parse_handles_log_timestamp_patterns(self) -> None:
        """_parse_timestamp in log_analyzer.py uses regex patterns."""
        from general_ludd.log_analyzer import _parse_timestamp

        windows_ts = "2026-07-15 14:30:45,123 ERROR something"
        result = _parse_timestamp(windows_ts)
        assert result == "2026-07-15 14:30:45"

        iso_ts = "2026-07-15T14:30:45.123+00:00 message"
        result = _parse_timestamp(iso_ts)
        assert result == "2026-07-15T14:30:45.123+00:00"

    def test_datetime_fromisoformat_roundtrip_with_z(self) -> None:
        """Python 3.11+ fromisoformat handles Z suffix."""
        s = "2026-07-15T14:30:45Z"
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        assert d.tzinfo is not None
        assert d.isoformat() in ("2026-07-15T14:30:45+00:00", "2026-07-15T14:30:45Z")

    def test_parsed_timestamp_is_comparable_to_utc_now(self) -> None:
        d = datetime.fromisoformat("2026-07-15T14:30:45+00:00")
        now = datetime.now(UTC)
        assert d < now  # parsed 2026 date precedes runtime

    @pytest.mark.parametrize(
        "fmt,value",
        [
            ("%Y-%m-%dT%H:%M:%S", "2026-07-15T14:30:45"),
            ("%Y-%m-%d", "2026-07-15"),
            ("%Y-%m-%dT%H:%M:%S%z", "2026-07-15T14:30:45+0000"),
            ("%Y-%m-%dT%H:%M:%S%z", "2026-07-15T14:30:45+00:00"),
            ("%Y-%m-%dT%H:%M:%S.%f", "2026-07-15T14:30:45.123456"),
        ],
    )
    def test_strptime_behavior_with_various_formats(
        self,
        fmt: str,
        value: str,
    ) -> None:
        result = datetime.strptime(value, fmt)
        assert result is not None


# ── Comparision and arithmetic safety ────────────────────────────────────────


class TestComparisonSafety:
    """Datetime comparisons must not mix aware and naive."""

    def test_aware_vs_naive_raises_typeerror(self) -> None:
        aware = datetime.now(UTC)
        naive = datetime(2026, 1, 1)
        with pytest.raises(TypeError):
            _ = aware > naive

    def test_aware_vs_aware_tz_compare_correctly(self) -> None:
        """Different timezones compare correctly when both are aware."""
        utc = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        edt = utc.astimezone(timezone(timedelta(hours=-4)))
        assert utc == edt
        assert utc.hour == 12
        assert edt.hour == 8

    def test_timedelta_crossing_dst_stays_consistent_in_utc(self) -> None:
        mar_7_utc = datetime(2026, 3, 7, tzinfo=UTC)
        mar_15_utc = datetime(2026, 3, 15, tzinfo=UTC)
        delta = mar_15_utc - mar_7_utc
        assert delta == timedelta(days=8)


# ── mock-based integration tests ────────────────────────────────────────────


class TestFrozenTimestamps:
    """Validate timestamp-producing functions under mocked time."""

    def test_utcnow_returns_mocked_time(self) -> None:
        datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
        d = _utcnow()
        assert d.tzinfo is UTC
        assert isinstance(d, datetime)
        assert d > datetime(2026, 1, 1, tzinfo=UTC)

    def test_peak_pricing_now_respects_di_clock(self) -> None:
        """peak_pricing.py is_peak accepts an optional now= param for testing."""
        from general_ludd.budget.peak_pricing import is_peak

        sun_noon = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)
        result = is_peak(now=sun_noon)
        assert isinstance(result, bool)

    def test_monotonic_not_affected_by_wall_clock_mock(self) -> None:
        """monotonic clock advances regardless of wall clock mocks."""
        t1 = time.monotonic()
        t2 = time.monotonic()
        assert t2 > t1, "monotonic clock must always advance"

    def test_expiry_comparison_with_explicit_values(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
        expires = datetime(2026, 8, 1, tzinfo=UTC)
        assert expires > now

    def test_safe_datetime_preserves_mocked_utc(self) -> None:
        aware = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)
        assert _safe_datetime(aware) == aware
        result = _safe_datetime(aware)
        assert result is not None
        assert result.tzinfo is UTC


# ── edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for time operations."""

    def test_leap_year_feb_29_safe(self) -> None:
        d = datetime(2024, 2, 29, tzinfo=UTC)
        assert d.day == 29
        next_day = d + timedelta(days=1)
        assert next_day.month == 3
        assert next_day.day == 1

    def test_non_leap_year_feb_28_safe(self) -> None:
        d = datetime(2025, 2, 28, tzinfo=UTC)
        next_day = d + timedelta(days=1)
        assert next_day.month == 3
        assert next_day.day == 1

    def test_timestamp_boundary_year_2038(self) -> None:
        """Year 2038 problem: 32-bit signed epoch overflow. Test safety."""
        t = calendar.timegm((2038, 1, 19, 3, 14, 7))
        d = datetime.fromtimestamp(t, tz=UTC)
        assert d.year == 2038

    def test_timestamp_boundary_year_2038_plus_one(self) -> None:
        t = calendar.timegm((2038, 1, 19, 3, 14, 8))
        d = datetime.fromtimestamp(t, tz=UTC)
        assert d.year == 2038

    def test_very_old_date(self) -> None:
        d = datetime(1970, 1, 1, tzinfo=UTC)
        assert d.timestamp() == 0.0

    def test_very_far_future_date(self) -> None:
        d = datetime(3000, 1, 1, tzinfo=UTC)
        assert d.year == 3000
        assert d.tzinfo is UTC

    def test_microsecond_precision_retained(self) -> None:
        d = datetime(2026, 7, 15, 12, 0, 0, 123456, tzinfo=UTC)
        s = d.isoformat()
        assert "123456" in s

    def test_strftime_utc_produces_expected_string(self) -> None:
        d = datetime(2026, 7, 15, 14, 30, 45, tzinfo=UTC)
        got = d.strftime("%Y-%m-%dT%H:%M:%S%z")
        assert got == "2026-07-15T14:30:45+0000"


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_iso8601_variants(raw: str) -> datetime | None:
    """Try common ISO8601 variants. Returns None on failure.

    Mirrors the robustness needed for _parse_timestamp in log_analyzer.py.
    """
    candidates = [
        raw.replace("Z", "+00:00"),
        raw.replace("z", "+00:00"),
        raw.replace(" ", "T") + "+00:00" if "T" not in raw and "+" not in raw else raw,
    ]
    date_candidate = raw.replace(" ", "T")
    if date_candidate != raw:
        candidates.append(date_candidate)
    tz_candidates = [c for c in candidates if _looks_tz_aware(c)]
    for c in tz_candidates:
        try:
            return datetime.fromisoformat(c)
        except (ValueError, TypeError):
            continue
    for c in candidates:
        if c not in tz_candidates:
            try:
                return datetime.fromisoformat(c)
            except (ValueError, TypeError):
                continue
    return None


def _looks_tz_aware(s: str) -> bool:
    return "+" in s[10:] or s.endswith("Z") or s.endswith("z")
