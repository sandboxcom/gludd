"""Load balancer algorithms: round-robin, least-conn, consistent hash, weighted, random.

Each algorithm supports health checks and optional sticky sessions.
"""

from __future__ import annotations

import hashlib
import random
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
V = TypeVar("V")


@dataclass
class Backend(Generic[T]):
    """Describe a registered backend with health and load state."""

    key: T
    healthy: bool = True
    weight: int = 1
    connections: int = 0


def _fmt_key(key: object) -> str:
    return str(key)


class RoundRobinBalancer(Generic[T]):
    """Select backends in round-robin order."""

    def __init__(self, backends: Sequence[T] = ()) -> None:
        """Initialize the balancer with optional backends."""
        self._backends: OrderedDict[T, Backend[T]] = OrderedDict()
        self._order: list[T] = []
        self._cursor: int = 0
        self._lock = threading.Lock()
        for b in backends:
            self.add(b)

    def add(self, key: T, weight: int = 1) -> None:
        """Add a backend key to the round-robin order."""
        with self._lock:
            if key not in self._backends:
                self._backends[key] = Backend(key=key, weight=weight)
                self._order.append(key)

    def remove(self, key: T) -> None:
        """Remove a backend."""
        with self._lock:
            self._backends.pop(key, None)
            if key in self._order:
                self._order.remove(key)
            if self._cursor >= len(self._order):
                self._cursor = 0

    def healthy(self, key: T) -> None:
        """Mark a backend healthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = True

    def unhealthy(self, key: T) -> None:
        """Mark a backend unhealthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = False

    def next(self) -> T | None:
        """Return the next healthy backend."""
        with self._lock:
            healthy = [k for k in self._order if self._backends[k].healthy]
            if not healthy:
                return None
            self._cursor = (self._cursor + 1) % len(healthy)
            return healthy[self._cursor - 1]

    @property
    def backend_count(self) -> int:
        """Return the number of backends."""
        return len(self._backends)

    @property
    def healthy_count(self) -> int:
        """Return the number of healthy backends."""
        return sum(1 for b in self._backends.values() if b.healthy)

    def __iter__(self) -> Iterator[T]:
        """Iterate over backend keys."""
        return iter(list(self._order))

    def __len__(self) -> int:
        """Return the number of backends."""
        return len(self._backends)


class LeastConnectionsBalancer(Generic[T]):
    """Select the healthy backend with the fewest connections."""

    def __init__(self, backends: Sequence[T] = ()) -> None:
        """Initialize the balancer with optional backends."""
        self._backends: dict[T, Backend[T]] = {}
        self._lock = threading.Lock()
        for b in backends:
            self.add(b)

    def add(self, key: T, weight: int = 1) -> None:
        """Add a backend with the given weight."""
        with self._lock:
            if key not in self._backends:
                self._backends[key] = Backend(key=key, weight=weight)

    def remove(self, key: T) -> None:
        """Remove a backend."""
        with self._lock:
            self._backends.pop(key, None)

    def healthy(self, key: T) -> None:
        """Mark a backend healthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = True

    def unhealthy(self, key: T) -> None:
        """Mark a backend unhealthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = False

    def acquire(self, key: T) -> None:
        """Increment the connection count of a backend."""
        with self._lock:
            if key in self._backends:
                self._backends[key].connections += 1

    def release(self, key: T) -> None:
        """Decrement the connection count of a backend."""
        with self._lock:
            if key in self._backends and self._backends[key].connections > 0:
                self._backends[key].connections -= 1

    def next(self) -> T | None:
        """Return the healthy backend with the fewest connections."""
        with self._lock:
            candidates = [b for b in self._backends.values() if b.healthy]
            if not candidates:
                return None
            candidates.sort(key=lambda b: (b.connections, b.key))
            return candidates[0].key

    @property
    def backend_count(self) -> int:
        """Return the number of backends."""
        return len(self._backends)

    @property
    def healthy_count(self) -> int:
        """Return the number of healthy backends."""
        return sum(1 for b in self._backends.values() if b.healthy)

    def connection_count(self, key: T) -> int:
        """Return the connection count of a backend."""
        with self._lock:
            b = self._backends.get(key)
            return b.connections if b else 0

    def __len__(self) -> int:
        """Return the number of backends."""
        return len(self._backends)


@dataclass
class HashRingNode(Generic[T]):
    """Represent a virtual node on the consistent hash ring."""

    key: T
    position: int
    healthy: bool = True
    weight: int = 1


class ConsistentHashBalancer(Generic[T]):
    """Select backends using a consistent hash ring."""

    VIRTUAL_NODES_DEFAULT = 128

    def __init__(
        self,
        backends: Sequence[T] = (),
        virtual_nodes: int = VIRTUAL_NODES_DEFAULT,
        hash_fn: Callable[[str], int] | None = None,
        affinity_key: str | None = None,
    ) -> None:
        """Initialize the balancer with backends and ring options."""
        self._virtual_nodes = virtual_nodes
        self._hash_fn = hash_fn or self._default_hash
        self._affinity_key = affinity_key
        self._ring: list[HashRingNode[T]] = []
        self._backend_vnodes: dict[T, list[HashRingNode[T]]] = {}
        self._lock = threading.Lock()
        for b in backends:
            self.add(b)

    @staticmethod
    def _default_hash(key: str) -> int:
        h = hashlib.md5(key.encode(), usedforsecurity=False).digest()
        return int.from_bytes(h[:8], "big")

    def _vnodes(self, key: T) -> list[HashRingNode[T]]:
        return [
            HashRingNode(
                key=key,
                position=self._hash_fn(f"{_fmt_key(key)}-v{i}"),
                weight=1,
            )
            for i in range(self._virtual_nodes)
        ]

    def add(self, key: T, weight: int = 1) -> None:
        """Add a backend to the ring."""
        with self._lock:
            if key in self._backend_vnodes:
                return
            vnodes = self._vnodes(key)
            self._backend_vnodes[key] = vnodes
            self._ring.extend(vnodes)
            self._ring.sort(key=lambda n: n.position)

    def remove(self, key: T) -> None:
        """Remove a backend from the ring."""
        with self._lock:
            vnodes = self._backend_vnodes.pop(key, [])
            for vn in vnodes:
                self._ring.remove(vn)

    def healthy(self, key: T) -> None:
        """Mark all virtual nodes of a backend healthy."""
        with self._lock:
            for vn in self._backend_vnodes.get(key, []):
                vn.healthy = True

    def unhealthy(self, key: T) -> None:
        """Mark all virtual nodes of a backend unhealthy."""
        with self._lock:
            for vn in self._backend_vnodes.get(key, []):
                vn.healthy = False

    def next(self, request_key: str | None = None) -> T | None:
        """Return the backend for the request key."""
        with self._lock:
            healthy_vnodes = [n for n in self._ring if n.healthy]
            if not healthy_vnodes:
                return None
            affinity = (self._affinity_key or "") + (request_key or "")
            h = self._hash_fn(affinity) if affinity else 0
            for node in healthy_vnodes:
                if node.position >= h:
                    return node.key
            return healthy_vnodes[0].key

    @property
    def backend_count(self) -> int:
        """Return the number of backends."""
        return len(self._backend_vnodes)

    @property
    def healthy_count(self) -> int:
        """Return the number of backends with a healthy virtual node."""
        return sum(1 for vns in self._backend_vnodes.values() if any(n.healthy for n in vns))

    def get_node(self, request_key: str) -> T | None:
        """Return the backend for a request key."""
        return self.next(request_key)

    def __len__(self) -> int:
        """Return the number of backends."""
        return len(self._backend_vnodes)


class WeightedBalancer(Generic[T]):
    """Select backends randomly proportional to their weight."""

    def __init__(self, backends: Sequence[T] = ()) -> None:
        """Initialize the balancer with optional backends."""
        self._backends: dict[T, Backend[T]] = {}
        self._order: list[T] = []
        self._current_weight: int = 0
        self._lock = threading.Lock()
        for b in backends:
            self.add(b)

    def add(self, key: T, weight: int = 1) -> None:
        """Add a backend with the given weight."""
        with self._lock:
            if key not in self._backends:
                self._backends[key] = Backend(key=key, weight=weight)
                self._order.append(key)

    def remove(self, key: T) -> None:
        """Remove a backend from the weighted pool."""
        with self._lock:
            self._backends.pop(key, None)
            if key in self._order:
                self._order.remove(key)

    def set_weight(self, key: T, weight: int) -> None:
        """Set a backend's weight (clamped to at least 1)."""
        with self._lock:
            if key in self._backends:
                self._backends[key].weight = max(weight, 1)

    def healthy(self, key: T) -> None:
        """Mark a backend healthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = True

    def unhealthy(self, key: T) -> None:
        """Mark a backend unhealthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = False

    @property
    def backend_count(self) -> int:
        """Return the number of backends."""
        return len(self._backends)

    @property
    def healthy_count(self) -> int:
        """Return the number of healthy backends."""
        return sum(1 for b in self._backends.values() if b.healthy)

    def weight(self, key: T) -> int:
        """Return a backend's current weight (0 when absent)."""
        with self._lock:
            b = self._backends.get(key)
            return b.weight if b else 0

    def next(self) -> T | None:
        """Select a healthy backend by weighted random choice."""
        with self._lock:
            candidates = [(k, b.weight) for k, b in self._backends.items() if b.healthy]
            if not candidates:
                return None
            total = sum(w for _, w in candidates)
            if total == 0:
                return None
            r = random.randint(0, total - 1)
            cumulative = 0
            for k, w in candidates:
                cumulative += w
                if r < cumulative:
                    return k
            return candidates[-1][0]

    def __len__(self) -> int:
        """Return the number of backends."""
        return len(self._backends)


class RandomBalancer(Generic[T]):
    """Balance requests across backends by uniform random choice."""

    def __init__(self, backends: Sequence[T] = (), seed: int | None = None) -> None:
        """Initialize the random balancer with optional backends and seed."""
        self._backends: dict[T, Backend[T]] = {}
        self._order: list[T] = []
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        for b in backends:
            self.add(b)

    def add(self, key: T, weight: int = 1) -> None:
        """Add a backend key to the random pool."""
        with self._lock:
            if key not in self._backends:
                self._backends[key] = Backend(key=key, weight=weight)
                self._order.append(key)

    def remove(self, key: T) -> None:
        """Remove a backend from the random pool."""
        with self._lock:
            self._backends.pop(key, None)
            if key in self._order:
                self._order.remove(key)

    def healthy(self, key: T) -> None:
        """Mark a backend healthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = True

    def unhealthy(self, key: T) -> None:
        """Mark a backend unhealthy."""
        with self._lock:
            if key in self._backends:
                self._backends[key].healthy = False

    @property
    def backend_count(self) -> int:
        """Return the number of backends."""
        return len(self._backends)

    @property
    def healthy_count(self) -> int:
        """Return the number of healthy backends."""
        return sum(1 for b in self._backends.values() if b.healthy)

    def next(self) -> T | None:
        """Select a healthy backend uniformly at random."""
        with self._lock:
            healthy = [k for k in self._order if self._backends[k].healthy]
            if not healthy:
                return None
            return self._rng.choice(healthy)

    def distribution(self, samples: int = 10000) -> dict[T, int]:
        """Return a sampled distribution of selections for inspection."""
        counts: dict[T, int] = {}
        for _ in range(samples):
            k = self.next()
            if k is not None:
                counts[k] = counts.get(k, 0) + 1
        return counts

    def __len__(self) -> int:
        """Return the number of backends."""
        return len(self._backends)


@dataclass
class StickySession(Generic[T]):
    """A sticky session binding a session key to one backend."""

    backend: T
    session_key: str


class StickySessionStore(Generic[T]):
    """Thread-safe store mapping session keys to backend selections."""

    def __init__(self) -> None:
        """Initialize an empty session store."""
        self._sessions: dict[str, T] = {}
        self._lock = threading.Lock()

    def get(self, session_key: str) -> T | None:
        """Return the backend bound to a session key, if any."""
        with self._lock:
            return self._sessions.get(session_key)

    def set(self, session_key: str, backend: T) -> None:
        """Bind a session key to a backend."""
        with self._lock:
            self._sessions[session_key] = backend

    def remove(self, session_key: str) -> None:
        """Drop a session binding."""
        with self._lock:
            self._sessions.pop(session_key, None)

    def clear(self) -> None:
        """Drop every session binding."""
        with self._lock:
            self._sessions.clear()

    def __contains__(self, session_key: str) -> bool:
        """Return whether the session key is bound."""
        with self._lock:
            return session_key in self._sessions

    def __len__(self) -> int:
        """Return the number of bound sessions."""
        with self._lock:
            return len(self._sessions)


def sticky_dispatch(
    balancer: RoundRobinBalancer[T] | LeastConnectionsBalancer[T] | WeightedBalancer[T] | RandomBalancer[T],
    session_store: StickySessionStore[T],
    session_key: str,
) -> T | None:
    """Route a session key through its bound backend or a fresh selection."""
    existing = session_store.get(session_key)
    if existing is not None:
        healthy = hasattr(balancer, "_backends") and getattr(balancer._backends.get(existing, None), "healthy", True)
        if healthy:
            return existing
        session_store.remove(session_key)
    backend = balancer.next()
    if backend is not None:
        session_store.set(session_key, backend)
    return backend


def sticky_dispatch_ch(
    balancer: ConsistentHashBalancer[T],
    session_store: StickySessionStore[T],
    session_key: str,
) -> T | None:
    """Route a session key through its bound consistent-hash node."""
    existing = session_store.get(session_key)
    if existing is not None:
        return existing
    backend = balancer.next(session_key)
    if backend is not None:
        session_store.set(session_key, backend)
    return backend
