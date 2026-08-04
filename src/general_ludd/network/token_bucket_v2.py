"""Token bucket v2 rate limiter with multi-bucket, hierarchical groups,
burst allowance, and smooth sub-second refill.

Core types:
  BucketConfig   — capacity, rate, burst multiplier, parent reference
  BucketState    — tokens, last_refill timestamp
  Bucket         — live bucket: config + state + refill logic
  BucketGroup    — hierarchical grouping with parent→child overflow
  LimiterV2      — top-level coordinator for multi-bucket enforcement
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

# ── monotonic clock abstraction (injectable) ────────────────────────────────


def _monotonic_now() -> float:
    return time.monotonic()


# ── configuration & state ────────────────────────────────────────────────────


@dataclass
class BucketConfig:
    capacity: float
    rate: float
    burst_multiplier: float = 1.0
    parent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.rate < 0:
            raise ValueError("rate must be >= 0")
        if self.burst_multiplier < 1.0:
            raise ValueError("burst_multiplier must be >= 1.0")


@dataclass
class BucketState:
    tokens: float
    last_refill: float = field(default_factory=_monotonic_now)

    def __deepcopy__(self, memo: Any) -> BucketState:
        return BucketState(copy.deepcopy(self.tokens, memo), self.last_refill)


class Bucket:
    """A single token bucket with smooth refill and burst support."""

    def __init__(
        self,
        name: str,
        config: BucketConfig,
        state: BucketState | None = None,
        *,
        clock: Callable[[], float] = _monotonic_now,
    ) -> None:
        self.name = name
        self.config = config
        self._clock = clock
        self._state = state or BucketState(tokens=config.capacity, last_refill=clock())
        self._lock = threading.Lock()

    # -- read-only helpers ----------------------------------------------------

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._state.tokens

    @property
    def last_refill(self) -> float:
        with self._lock:
            return self._state.last_refill

    @property
    def capacity(self) -> float:
        return self.config.capacity

    @property
    def rate(self) -> float:
        return self.config.rate

    @property
    def effective_capacity(self) -> float:
        return self.config.capacity * self.config.burst_multiplier

    # -- core operations ------------------------------------------------------

    def refill(self) -> float:
        """Refill tokens based on elapsed time; return new token count."""
        now = self._clock()
        with self._lock:
            elapsed = now - self._state.last_refill
            if elapsed <= 0:
                return self._state.tokens
            added = elapsed * self.config.rate
            eff_cap = self.effective_capacity
            self._state.tokens = min(eff_cap, self._state.tokens + added)
            self._state.last_refill = now
            return self._state.tokens

    def consume(self, tokens: float, *, auto_refill: bool = True) -> bool:
        """Attempt to consume *tokens*.  Return True if allowed."""
        if tokens < 0:
            raise ValueError("tokens must be >= 0")
        if auto_refill:
            self.refill()
        with self._lock:
            if self._state.tokens >= tokens:
                self._state.tokens -= tokens
                return True
            return False

    def try_consume(self, tokens: float, *, auto_refill: bool = True) -> bool:
        """Alias for consume."""
        return self.consume(tokens, auto_refill=auto_refill)

    def reset(self) -> None:
        """Reset to full capacity."""
        with self._lock:
            self._state.tokens = self.config.capacity
            self._state.last_refill = self._clock()

    def snapshot(self) -> BucketState:
        """Return a deep-copy of current state."""
        with self._lock:
            return copy.deepcopy(self._state)


# ── hierarchical grouping ────────────────────────────────────────────────────


class BucketGroup:
    """Ordered group of buckets with optional parent→child overflow.

    When *overflow* is True, tokens are consumed from each bucket
    in order.  The first bucket that has sufficient tokens is charged;
    subsequent buckets are NOT charged.

    When *overflow* is False, ALL buckets in the group must have
    sufficient tokens for a consume to succeed (AND gating).
    """

    def __init__(self, name: str, buckets: Iterable[Bucket], *, overflow: bool = False) -> None:
        self.name = name
        self._buckets: list[Bucket] = list(buckets)
        self._overflow = overflow

    def __iter__(self) -> Iterator[Bucket]:
        return iter(self._buckets)

    def __len__(self) -> int:
        return len(self._buckets)

    def __getitem__(self, index: int) -> Bucket:
        return self._buckets[index]

    @property
    def overflow(self) -> bool:
        return self._overflow

    def add(self, bucket: Bucket) -> None:
        self._buckets.append(bucket)

    def consume(self, tokens: float, *, auto_refill: bool = True) -> bool:
        if self._overflow:
            return self._consume_overflow(tokens, auto_refill=auto_refill)
        return self._consume_all(tokens, auto_refill=auto_refill)

    def _consume_overflow(self, tokens: float, *, auto_refill: bool) -> bool:
        for b in self._buckets:
            if b.tokens >= tokens or (auto_refill and b.refill() >= tokens):
                return b.consume(tokens, auto_refill=False)
        return False

    def _consume_all(self, tokens: float, *, auto_refill: bool) -> bool:
        snapshots = [b.snapshot() for b in self._buckets]
        for b in self._buckets:
            if auto_refill:
                b.refill()
            if b.tokens < tokens:
                self._rollback(snapshots)
                return False
        for b in self._buckets:
            b.consume(tokens, auto_refill=False)
        return True

    def _rollback(self, snapshots: list[BucketState]) -> None:
        for b, s in zip(self._buckets, snapshots, strict=False):
            with b._lock:
                b._state.tokens = s.tokens
                b._state.last_refill = s.last_refill

    def refill_all(self) -> None:
        for b in self._buckets:
            b.refill()

    def reset_all(self) -> None:
        for b in self._buckets:
            b.reset()


# ── top-level limiter ────────────────────────────────────────────────────────


@dataclass
class LimiterV2:
    """Multi-bucket rate limiter coordinating named buckets and groups."""

    buckets: dict[str, Bucket] = field(default_factory=dict)
    groups: dict[str, BucketGroup] = field(default_factory=dict)
    default_group: str | None = None

    def register(self, name: str, config: BucketConfig) -> Bucket:
        if name in self.buckets:
            raise KeyError(f"bucket {name!r} already registered")
        b = Bucket(name, config)
        self.buckets[name] = b
        return b

    def create_group(self, name: str, bucket_names: Iterable[str], *, overflow: bool = False) -> BucketGroup:
        group = BucketGroup(name, [self.buckets[n] for n in bucket_names], overflow=overflow)
        self.groups[name] = group
        return group

    def allow(self, tokens: float, *, group: str | None = None, auto_refill: bool = True) -> bool:
        g = self._resolve_group(group)
        if g is not None:
            return g.consume(tokens, auto_refill=auto_refill)
        return all(b.consume(tokens, auto_refill=auto_refill) for b in self.buckets.values())

    def refill_all(self) -> None:
        for b in self.buckets.values():
            b.refill()

    def reset_all(self) -> None:
        for b in self.buckets.values():
            b.reset()

    def snapshot(self) -> dict[str, BucketState]:
        return {name: b.snapshot() for name, b in self.buckets.items()}

    def _resolve_group(self, group: str | None) -> BucketGroup | None:
        if group is not None:
            return self.groups[group]
        if self.default_group is not None:
            return self.groups[self.default_group]
        return None
