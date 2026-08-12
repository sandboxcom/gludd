"""Peak/off-peak pricing schedules for model API providers.

Provides configurable time-window pricing so the system can route work to
cheaper off-peak windows (e.g. OpenAI's 50% off-peak discount).

Exports:
    RateTier:              a single rate window (provider, model, rate, days, hours).
    PeakPricingSchedule:   mutable collection of RateTier instances with lookups.
    get_current_rate:      current rate for a model/provider (peak or off-peak).
    list_rate_tiers:       all rate tiers for a model/provider.
    is_off_peak:           whether it is currently off-peak for a model/provider.
    next_off_peak_window:  datetime when the next off-peak window starts.
    default_schedule:      factory returning a schedule pre-loaded with builtins.
"""

from __future__ import annotations

import datetime
import threading as _th
from dataclasses import dataclass, field
from typing import ClassVar


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(frozen=True)
class RateTier:
    """One price point for one model/provider during a time window.

    Args:
        model_id:  provider's model identifier (e.g. ``"gpt-4o"``).
        provider:  provider slug (``"openai"``, ``"anthropic"``, ...).
        rate:      cost per 1M input tokens in USD.
        label:     human-readable tier name (``"peak"``, ``"off-peak"``).
        days:      frozenset of ISO day-of-week integers (0=Monday, 6=Sunday).
        start_hour: inclusive hour (0-23) when this rate takes effect.
        end_hour:   exclusive hour (0-23) when this rate ends.
                    An end_hour <= start_hour indicates an overnight window
                    (e.g. start=20, end=8 means 20:00-07:59).
    """

    model_id: str
    provider: str
    rate: float
    label: str
    days: frozenset[int]
    start_hour: int
    end_hour: int

    def __post_init__(self) -> None:
        if self.rate < 0:
            raise ValueError("rate must be non-negative")
        if self.start_hour < 0 or self.start_hour > 23:
            raise ValueError("start_hour must be 0-23")
        if self.end_hour < 0 or self.end_hour > 23:
            raise ValueError("end_hour must be 0-23")

    @property
    def _key(self) -> tuple[object, ...]:
        return (
            self.model_id,
            self.provider,
            self.label,
            self.start_hour,
            self.end_hour,
            self.rate,
        )

    def __hash__(self) -> int:
        return hash(self._key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RateTier):
            return self._key == other._key
        return NotImplemented

    def covers(self, dt: datetime.datetime) -> bool:
        hour = dt.hour
        if self.start_hour <= self.end_hour:
            return dt.weekday() in self.days and self.start_hour <= hour < self.end_hour
        if hour >= self.start_hour:
            return dt.weekday() in self.days
        if hour < self.end_hour:
            return (dt.weekday() - 1) % 7 in self.days
        return False


@dataclass
class PeakPricingSchedule:
    """Mutable collection of RateTier instances, keyed by (model_id, provider).

    Tiers are ordered: the *first* matching tier for a given time wins.
    Use ``add_tier`` / ``remove_tier`` to mutate.
    """

    _tiers: dict[tuple[str, str], list[RateTier]] = field(default_factory=dict)

    def add_tier(self, tier: RateTier) -> None:
        key = (tier.model_id, tier.provider)
        bucket = self._tiers.setdefault(key, [])
        if tier not in bucket:
            bucket.append(tier)

    def remove_tier(self, tier: RateTier) -> None:
        key = (tier.model_id, tier.provider)
        bucket = self._tiers.get(key)
        if bucket is not None and tier in bucket:
            bucket.remove(tier)
            if not bucket:
                del self._tiers[key]

    def clear(self) -> None:
        self._tiers.clear()

    def tiers_for(self, model_id: str, provider: str) -> list[RateTier]:
        return list(self._tiers.get((model_id, provider), []))

    def matching_tier(self, model_id: str, provider: str, dt: datetime.datetime) -> RateTier | None:
        tiers = self.tiers_for(model_id, provider)
        for tier in tiers:
            if tier.covers(dt):
                return tier
        for tier in tiers:
            if dt.weekday() in tier.days:
                return tier
        return None

    def all_providers(self) -> list[str]:
        seen: dict[str, None] = {}
        for _mid, prov in self._tiers:
            seen[prov] = None
        return list(seen)

    def all_model_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for mid, _prov in self._tiers:
            seen[mid] = None
        return list(seen)


def get_current_rate(
    schedule: PeakPricingSchedule,
    model_id: str,
    provider: str,
) -> float:
    tier = schedule.matching_tier(model_id, provider, _utcnow())
    return tier.rate if tier is not None else 0.0


def list_rate_tiers(
    schedule: PeakPricingSchedule,
    model_id: str,
    provider: str,
) -> list[RateTier]:
    return schedule.tiers_for(model_id, provider)


def is_off_peak(
    schedule: PeakPricingSchedule,
    model_id: str,
    provider: str,
) -> bool:
    tier = schedule.matching_tier(model_id, provider, _utcnow())
    return tier is not None and tier.label == "off-peak"


def next_off_peak_window(
    schedule: PeakPricingSchedule,
    model_id: str,
    provider: str,
) -> datetime.datetime | None:
    tiers = schedule.tiers_for(model_id, provider)
    off_peak_tiers = [t for t in tiers if t.label == "off-peak"]
    if not off_peak_tiers:
        return None

    now = _utcnow()
    current = schedule.matching_tier(model_id, provider, now)

    if current is not None and current.label == "off-peak":
        return now.replace(minute=0, second=0, microsecond=0)

    for delta_hours in range(1, 168 + 1):
        candidate = now + datetime.timedelta(hours=delta_hours)
        for tier in off_peak_tiers:
            if tier.covers(candidate):
                return candidate.replace(minute=0, second=0, microsecond=0)

    return None


# -- Built-in schedule helpers -------------------------------------------------


def _otier(
    model_id: str,
    provider: str,
    peak_rate: float,
    off_peak_rate: float,
    peak_days: frozenset[int],
    peak_start: int,
    peak_end: int,
) -> tuple[RateTier, RateTier]:
    return (
        RateTier(
            model_id,
            provider,
            peak_rate,
            "peak",
            peak_days,
            peak_start,
            peak_end,
        ),
        RateTier(
            model_id,
            provider,
            off_peak_rate,
            "off-peak",
            frozenset(range(7)),
            peak_end,
            peak_start,
        ),
    )


_WEEKDAYS = frozenset(range(5))

_OPENAI_PEAK_TIERS: list[RateTier] = []
_ANTHROPIC_PEAK_TIERS: list[RateTier] = []
_GOOGLE_PEAK_TIERS: list[RateTier] = []
_DEEPSEEK_PEAK_TIERS: list[RateTier] = []
_OPENROUTER_PEAK_TIERS: list[RateTier] = []

# OpenAI
for oai_model in (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o4-mini",
    "o3",
    "o3-mini",
    "o1",
    "gpt-4.5-preview",
):
    peak, off = _otier(oai_model, "openai", 0.0, 0.0, _WEEKDAYS, 7, 19)
    _OPENAI_PEAK_TIERS.append(peak)
    _OPENAI_PEAK_TIERS.append(off)

_OPENAI_RATES: dict[str, float] = {
    "gpt-4o": 2.50,
    "gpt-4.1": 2.00,
    "gpt-4.1-mini": 0.40,
    "gpt-4.1-nano": 0.10,
    "o4-mini": 1.10,
    "o3": 10.00,
    "o3-mini": 1.10,
    "o1": 15.00,
    "gpt-4.5-preview": 75.00,
}
for tier in _OPENAI_PEAK_TIERS:
    base = _OPENAI_RATES.get(tier.model_id, 0.0)
    r = base * 0.5 if tier.label == "off-peak" else base
    object.__setattr__(tier, "rate", r)

# Anthropic
for anthropic_model in (
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-opus-4-20250514",
):
    peak, off = _otier(anthropic_model, "anthropic", 0.0, 0.0, _WEEKDAYS, 7, 19)
    _ANTHROPIC_PEAK_TIERS.append(peak)
    _ANTHROPIC_PEAK_TIERS.append(off)

_ANTHROPIC_RATES: dict[str, float] = {
    "claude-sonnet-4-20250514": 3.00,
    "claude-3-5-sonnet-20241022": 3.00,
    "claude-3-5-haiku-20241022": 0.80,
    "claude-opus-4-20250514": 15.00,
}
for tier in _ANTHROPIC_PEAK_TIERS:
    base = _ANTHROPIC_RATES.get(tier.model_id, 0.0)
    r = base * 0.5 if tier.label == "off-peak" else base
    object.__setattr__(tier, "rate", r)

# Google (flat rate)
for google_model in (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
):
    peak, off = _otier(google_model, "google", 0.0, 0.0, _WEEKDAYS, 7, 19)
    _GOOGLE_PEAK_TIERS.append(peak)
    _GOOGLE_PEAK_TIERS.append(off)

_GOOGLE_RATES: dict[str, float] = {
    "gemini-2.5-pro": 1.25,
    "gemini-2.5-flash": 0.15,
    "gemini-2.0-flash": 0.10,
    "gemini-2.0-flash-lite": 0.075,
}
for tier in _GOOGLE_PEAK_TIERS:
    object.__setattr__(tier, "rate", _GOOGLE_RATES.get(tier.model_id, 0.0))

# DeepSeek (flat rate)
for ds_model in ("deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"):
    peak, off = _otier(ds_model, "deepseek", 0.0, 0.0, _WEEKDAYS, 7, 19)
    _DEEPSEEK_PEAK_TIERS.append(peak)
    _DEEPSEEK_PEAK_TIERS.append(off)

_DEEPSEEK_RATES: dict[str, float] = {
    "deepseek-chat": 0.27,
    "deepseek-reasoner": 0.55,
    "deepseek-v4-pro": 1.00,
}
for tier in _DEEPSEEK_PEAK_TIERS:
    object.__setattr__(tier, "rate", _DEEPSEEK_RATES.get(tier.model_id, 0.0))

# OpenRouter (flat rate)
for or_model in (
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4-20250514",
    "google/gemini-2.5-pro",
):
    peak, off = _otier(or_model, "openrouter", 0.0, 0.0, _WEEKDAYS, 7, 19)
    _OPENROUTER_PEAK_TIERS.append(peak)
    _OPENROUTER_PEAK_TIERS.append(off)

_OPENROUTER_RATES: dict[str, float] = {
    "openai/gpt-4o": 2.50,
    "anthropic/claude-sonnet-4-20250514": 3.00,
    "google/gemini-2.5-pro": 1.25,
}
for tier in _OPENROUTER_PEAK_TIERS:
    object.__setattr__(tier, "rate", _OPENROUTER_RATES.get(tier.model_id, 0.0))


BUILTIN_TIERS: dict[str, list[RateTier]] = {
    "openai": _OPENAI_PEAK_TIERS,
    "anthropic": _ANTHROPIC_PEAK_TIERS,
    "google": _GOOGLE_PEAK_TIERS,
    "deepseek": _DEEPSEEK_PEAK_TIERS,
    "openrouter": _OPENROUTER_PEAK_TIERS,
}


def apply_builtin_schedules(schedule: PeakPricingSchedule) -> None:
    for tiers in BUILTIN_TIERS.values():
        for tier in tiers:
            schedule.add_tier(tier)


def default_schedule() -> PeakPricingSchedule:
    schedule = PeakPricingSchedule()
    apply_builtin_schedules(schedule)
    return schedule


# -- Backward-compatible bridge to the old flat-rate API -----------------------

_DEFAULT_OFF_PEAK_DISCOUNT = 0.75
_DEFAULT_PEAK_START = 9
_DEFAULT_PEAK_END = 17


def current_rate_multiplier(
    now: datetime.datetime | None = None,
    *,
    off_peak_discount: float = _DEFAULT_OFF_PEAK_DISCOUNT,
) -> float:
    if now is None:
        now = _utcnow()
    if now.weekday() >= 5:
        return off_peak_discount
    if _DEFAULT_PEAK_START <= now.hour < _DEFAULT_PEAK_END:
        return 1.0
    return off_peak_discount


def is_peak(now: datetime.datetime | None = None) -> bool:
    if now is None:
        now = _utcnow()
    if now.weekday() >= 5:
        return False
    return _DEFAULT_PEAK_START <= now.hour < _DEFAULT_PEAK_END


def peak_rate_for_model(
    model_name: str,
    input_cost_per_token: float,
    output_cost_per_token: float,
    *,
    now: datetime.datetime | None = None,
) -> tuple[float, float]:
    m = current_rate_multiplier(now)
    return input_cost_per_token * m, output_cost_per_token * m


@dataclass
class PeakPricingTracker:
    """Thread-safe accumulator for off-peak savings (backward-compatible)."""

    _singleton: ClassVar[PeakPricingTracker | None] = None
    _singleton_lock: ClassVar[_th.Lock] = _th.Lock()

    _cumulative_full_cost: float = 0.0
    _cumulative_discounted_cost: float = 0.0
    _lock: _th.Lock = field(default_factory=_th.Lock, init=False, repr=False)

    @classmethod
    def singleton(cls) -> PeakPricingTracker:
        if cls._singleton is None:
            with cls._singleton_lock:
                if cls._singleton is None:
                    cls._singleton = cls()
        return cls._singleton

    def record_call(
        self,
        base_cost: float,
        effective_cost: float,
    ) -> None:
        if base_cost <= effective_cost:
            return
        with self._lock:
            self._cumulative_full_cost += base_cost
            self._cumulative_discounted_cost += effective_cost

    @property
    def cumulative_savings(self) -> float:
        with self._lock:
            return max(
                0.0,
                self._cumulative_full_cost - self._cumulative_discounted_cost,
            )

    @property
    def cumulative_full_cost(self) -> float:
        with self._lock:
            return self._cumulative_full_cost

    @property
    def cumulative_discounted_cost(self) -> float:
        with self._lock:
            return self._cumulative_discounted_cost
