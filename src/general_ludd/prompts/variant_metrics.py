"""G6 VariantMetrics — track A/B variant outcomes and auto-promote winners.

Records per-variant success/failure rates and latency for each prompt
template name. After enough samples (default 10 per variant), the winning
variant can be promoted so the selector switches from round-robin to
winner-only. Metrics persist to a JSON file for cross-session tracking.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any


class VariantMetrics:
    """Tracks per-template, per-variant outcome statistics.

    Usage::

        metrics = VariantMetrics()
        metrics.record_outcome("dispatch_started", "A", success=True, latency_ms=1234.5)
        winner = metrics.get_winner("dispatch_started")  # -> "A" or "B" or None
        metrics.promote_winner("dispatch_started")
    """

    MIN_SAMPLES_PER_VARIANT: int = 10

    def __init__(
        self,
        storage_path: str = ".gludd/variant_metrics.json",
        min_samples_per_variant: int = MIN_SAMPLES_PER_VARIANT,
    ) -> None:
        self._storage_path = storage_path
        self._min_samples = min_samples_per_variant
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        template_name: str,
        variant: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record a single dispatch outcome for *template_name* / *variant*."""
        with self._lock:
            entry = self._ensure_entry(template_name, variant)
            entry["total"] += 1
            if success:
                entry["successes"] += 1
            entry["latency_sum"] += latency_ms
            self._save()

    def get_winner(self, template_name: str) -> str | None:
        """Return the better-performing variant, or None if insufficient data.

        Winner is determined by highest success rate; ties broken by lowest
        average latency. Returns None when fewer than *min_samples* have been
        recorded for either variant.
        """
        with self._lock:
            stats = self._data.get(template_name, {})
            a = stats.get("A", {})
            b = stats.get("B", {})
            a_total = a.get("total", 0)
            b_total = b.get("total", 0)
            if a_total < self._min_samples or b_total < self._min_samples:
                return None

            a_rate = a.get("successes", 0) / a_total if a_total else 0.0
            b_rate = b.get("successes", 0) / b_total if b_total else 0.0
            if a_rate > b_rate:
                return "A"
            if b_rate > a_rate:
                return "B"

            a_lat = a.get("latency_sum", 0.0) / a_total if a_total else float("inf")
            b_lat = b.get("latency_sum", 0.0) / b_total if b_total else float("inf")
            if a_lat < b_lat:
                return "A"
            if b_lat < a_lat:
                return "B"
            return None

    def promote_winner(self, template_name: str) -> str | None:
        """Tag *template_name* for auto-promotion so select() always picks the
        stronger variant. Returns the promoted variant or None if no clear winner."""
        with self._lock:
            winner = self.get_winner(template_name)
            if winner is not None:
                self._data[template_name]["promoted"] = winner
                self._save()
            return winner

    def is_promoted(self, template_name: str) -> str | None:
        """Return the promoted variant for *template_name*, or None."""
        with self._lock:
            return self._data.get(template_name, {}).get("promoted")

    def stats(self, template_name: str) -> dict[str, Any]:
        """Return a snapshot of recorded statistics for *template_name*."""
        with self._lock:
            entry = self._data.get(template_name, {})
            return {
                k: v
                for k, v in entry.items()
                if k in ("A", "B", "promoted")
            }

    def total_samples(self, template_name: str, variant: str) -> int:
        """Total outcomes recorded for a specific variant."""
        with self._lock:
            return int(self._data.get(template_name, {}).get(variant, {}).get("total", 0))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_entry(self, template_name: str, variant: str) -> dict[str, Any]:
        if template_name not in self._data:
            self._data[template_name] = {}
        tmpl = self._data[template_name]
        if variant not in tmpl:
            tmpl[variant] = {"total": 0, "successes": 0, "latency_sum": 0.0}
        return tmpl[variant]  # type: ignore[no-any-return]

    def _load(self) -> dict[str, Any]:
        if os.path.isfile(self._storage_path):
            try:
                with open(self._storage_path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    return loaded
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._storage_path) or ".", exist_ok=True)
        tmp = self._storage_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._storage_path)
