"""Vector clock / version vector: causality tracking across distributed nodes.

Implements immutable vector clocks with:
  - increment: produce a new clock with one counter advanced
  - merge: lattice join (entrywise max)
  - compare: happens-before (<) and concurrent detection
"""

from __future__ import annotations

from collections.abc import Iterator


class VectorClock:
    """An immutable vector clock (version vector) mapping node ids to
    logical counters.

    Counters must be non-negative integers.  Zero-count entries are
    considered absent — they are not iterated and ``key in vc`` is
    false.
    """

    __slots__ = ("_counters",)

    def __init__(self, counters: dict[str, int] | None = None) -> None:
        if counters is None:
            self._counters: dict[str, int] = {}
            return
        for k, v in counters.items():
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"Non-negative integer required for key {k!r}, got {v!r}")
        self._counters = {k: v for k, v in counters.items() if v > 0}

    # ── core operations ────────────────────────────────────────────────────

    def increment(self, node_id: str) -> VectorClock:
        """Return a **new** clock with ``node_id`` counter advanced by 1."""
        new_counters = dict(self._counters)
        new_counters[node_id] = new_counters.get(node_id, 0) + 1
        return VectorClock(new_counters)

    def merge(self, other: VectorClock) -> VectorClock:
        """Lattice join: entrywise maximum of both clocks."""
        keys = self._counters.keys() | other._counters.keys()
        merged = {k: max(self._counters.get(k, 0), other._counters.get(k, 0)) for k in keys}
        return VectorClock(merged)

    # ── comparison: happens-before / concurrent ────────────────────────────

    def __lt__(self, other: VectorClock) -> bool:
        """True iff ``self`` strictly happens-before ``other``.

        ``a < b``  ⇔  ∀k: a[k] ≤ b[k]  ∧  ∃k: a[k] < b[k]
        """
        if self is other:
            return False
        all_keys = self._counters.keys() | other._counters.keys()
        any_strict = False
        for k in all_keys:
            sv = self._counters.get(k, 0)
            ov = other._counters.get(k, 0)
            if sv > ov:
                return False
            if sv < ov:
                any_strict = True
        return any_strict

    def __le__(self, other: VectorClock) -> bool:
        all_keys = self._counters.keys() | other._counters.keys()
        return all(self._counters.get(k, 0) <= other._counters.get(k, 0) for k in all_keys)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VectorClock):
            return NotImplemented
        if self is other:
            return True
        return self._counters == other._counters

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._counters.items())))

    # ── dict-like read interface ───────────────────────────────────────────

    def __getitem__(self, node_id: str) -> int:
        return self._counters.get(node_id, 0)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._counters

    def __len__(self) -> int:
        return len(self._counters)

    def __bool__(self) -> bool:
        return bool(self._counters)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._counters))

    # ── repr / str ─────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"VectorClock({self._counters!r})"

    def keys(self) -> set[str]:
        return set(self._counters.keys())

    def __str__(self) -> str:
        return f"<VectorClock {self._counters}>"

    # ── dict-like materialisation ──────────────────────────────────────────

    def __or__(self, other: dict[str, int]) -> dict[str, int]:
        return dict(self._counters) | other

    def __ror__(self, other: dict[str, int]) -> dict[str, int]:
        return other | dict(self._counters)
