"""Budget subsystem — combined model API + infrastructure cost tracking.

Exports:
    CombinedCostTracker: unified facade over SpendLimiter (model API) and
                         InfraCostTracker (cloud infrastructure).
    CreditTracker:       prepaid service credit / balance tracker for the
                         configured model providers (DeepSeek, OpenAI, Z.AI,
                         OpenRouter).
    PeakPricingSchedule: peak/off-peak pricing schedule per provider.
    RateTier:            a single rate window (provider, model, rate, days, hours).
    get_current_rate:    current rate for a model/provider (peak or off-peak).
    list_rate_tiers:     all rate tiers for a model/provider.
    is_off_peak:         whether it is currently off-peak for a model/provider.
    next_off_peak_window: datetime when the next off-peak window starts.
    default_schedule:    factory returning a schedule pre-loaded with builtins.
    OffPeakScheduler:    queue expensive tasks for execution during off-peak
                         hours to reduce cost.
    OffPeakTicket:       a deferred task waiting for its off-peak window.
    SavingsTracker:      accumulate lifetime savings from off-peak deferrals.
"""

from __future__ import annotations

from general_ludd.budget.combined_cost import CombinedCostTracker
from general_ludd.budget.credit_tracker import CreditTracker
from general_ludd.budget.off_peak_scheduler import (
    OffPeakScheduler,
    OffPeakTicket,
    SavingsTracker,
)
from general_ludd.budget.peak_pricing import (
    PeakPricingSchedule,
    PeakPricingTracker,
    RateTier,
    apply_builtin_schedules,
    current_rate_multiplier,
    default_schedule,
    get_current_rate,
    is_off_peak,
    is_peak,
    list_rate_tiers,
    next_off_peak_window,
    peak_rate_for_model,
)

__all__ = [
    "CombinedCostTracker",
    "CreditTracker",
    "OffPeakScheduler",
    "OffPeakTicket",
    "PeakPricingSchedule",
    "PeakPricingTracker",
    "RateTier",
    "SavingsTracker",
    "apply_builtin_schedules",
    "current_rate_multiplier",
    "default_schedule",
    "get_current_rate",
    "is_off_peak",
    "is_peak",
    "list_rate_tiers",
    "next_off_peak_window",
    "peak_rate_for_model",
]
