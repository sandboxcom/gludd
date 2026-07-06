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
        self._lock = threading.RLock()
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

    def generate_variant_report(self) -> dict[str, Any]:
        """Aggregate outcomes by template_name and variant into a comparison report.

        Returns a structured dict with per-template variant comparisons:
        which variant is winning, by what margin, and sample counts.
        """
        with self._lock:
            templates: dict[str, Any] = {}
            for tmpl_name, variants in self._data.items():
                a = variants.get("A", {})
                b = variants.get("B", {})
                a_total = a.get("total", 0)
                b_total = b.get("total", 0)
                a_successes = a.get("successes", 0)
                b_successes = b.get("successes", 0)
                a_rate = a_successes / a_total if a_total else 0.0
                b_rate = b_successes / b_total if b_total else 0.0
                a_lat = a.get("latency_sum", 0.0) / a_total if a_total else float("inf")
                b_lat = b.get("latency_sum", 0.0) / b_total if b_total else float("inf")
                promoted = variants.get("promoted")

                winner = self.get_winner(tmpl_name)
                sufficient = (
                    a_total >= self._min_samples
                    and b_total >= self._min_samples
                )

                templates[tmpl_name] = {
                    "variants": {
                        "A": {
                            "samples": a_total,
                            "successes": a_successes,
                            "success_rate": round(a_rate, 4),
                            "avg_latency_ms": round(a_lat, 2) if a_lat != float("inf") else None,
                        },
                        "B": {
                            "samples": b_total,
                            "successes": b_successes,
                            "success_rate": round(b_rate, 4),
                            "avg_latency_ms": round(b_lat, 2) if b_lat != float("inf") else None,
                        },
                    },
                    "sufficient_data": sufficient,
                    "winner": winner,
                    "promoted": promoted,
                    "margin": self._compute_margin(a, b),
                }

            return {
                "templates": templates,
                "template_count": len(templates),
            }

    def _compute_margin(
        self, a: dict[str, Any], b: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute the margin between two variants in success rate and latency."""
        a_total = a.get("total", 0)
        b_total = b.get("total", 0)
        a_rate = a.get("successes", 0) / a_total if a_total else 0.0
        b_rate = b.get("successes", 0) / b_total if b_total else 0.0
        a_lat = a.get("latency_sum", 0.0) / a_total if a_total else float("inf")
        b_lat = b.get("latency_sum", 0.0) / b_total if b_total else float("inf")

        rate_delta = round(a_rate - b_rate, 4)
        if a_lat != float("inf") and b_lat != float("inf"):
            lat_delta_pct = round(
                ((b_lat - a_lat) / max(a_lat, b_lat)) * 100, 2
            )
        else:
            lat_delta_pct = None

        winning_metric = None
        if rate_delta != 0:
            winning_metric = "success_rate"
        elif lat_delta_pct is not None and lat_delta_pct != 0:
            winning_metric = "latency"

        return {
            "success_rate_delta": rate_delta,
            "latency_delta_pct": lat_delta_pct,
            "winning_metric": winning_metric,
            "description": (
                f"A leads B by {abs(rate_delta):.1%} success rate"
                if rate_delta > 0
                else f"B leads A by {abs(rate_delta):.1%} success rate"
                if rate_delta < 0
                else (
                    f"A faster by {abs(lat_delta_pct or 0):.1f}% latency"
                    if lat_delta_pct is not None and lat_delta_pct > 0
                    else f"B faster by {abs(lat_delta_pct or 0):.1f}% latency"
                    if lat_delta_pct is not None and lat_delta_pct < 0
                    else "equal"
                )
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_entry(self, template_name: str, variant: str) -> dict[str, Any]:
        if template_name not in self._data:
            self._data[template_name] = {}
        tmpl = self._data[template_name]
        if variant not in tmpl:
            tmpl[variant] = {"total": 0, "successes": 0, "latency_sum": 0.0}
        entry = tmpl[variant]
        return entry if isinstance(entry, dict) else {"total": 0, "successes": 0, "latency_sum": 0.0}

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
