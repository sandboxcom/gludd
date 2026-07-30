"""CHEM-014 spectroscopy analyzer (Phase D).

Implements CHEM-014 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §8.3.
Supports NMR (chemical shift, ppm), IR (wavenumber, cm^-1), and MS (m/z) at the
basic level: peak detection by local-maximum + threshold, trapezoidal
integration, reference-table assignment, and cosine-similarity spectrum
matching. Parsers declare supported families and fail explicitly on unknown
kinds per CHEM-AT-015 ("each spectroscopy parser round-trips its supported open
fixture and explicitly rejects unsupported versions").

This is intentionally a small, dependency-free surface. Vendor parsers (JCAMP,
mzML, Bruker fid) live in adapter modules under
``collections/ansible_collections/general_ludd/chemistry/``; this module
operates on already-parsed ``(xs, ys)`` numeric arrays.
"""

from __future__ import annotations

import math
from typing import Any

SCHEMA_VERSION = "1.0"
METHOD_ID = "chemistry-spectroscopy@0.1.0"

SUPPORTED_KINDS: frozenset[str] = frozenset({"NMR", "IR", "MS", "Raman", "UV-Vis", "generic"})

_X_UNIT_LABELS: dict[str, str] = {
    "NMR": "ppm",
    "IR": "cm^-1",
    "MS": "m/z",
    "Raman": "cm^-1",
    "UV-Vis": "nm",
    "generic": "x",
}


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


class SpectraAnalyzer:
    """Stateless-ish analyzer for NMR/IR/MS/Raman/UV-Vis spectra.

    Construction parameters:

    * ``kind`` — one of :data:`SUPPORTED_KINDS`. Anything else raises
      ``ValueError`` so unsupported formats fail explicitly (CHEM-AT-015).
    * ``reference`` — optional ``{label: reference_x}`` table for
      :meth:`assign`. Tolerance is in the same x-unit as the reference.
    * ``tolerance`` — absolute window used when matching an observed x to the
      reference table.
    """

    def __init__(
        self,
        kind: str,
        reference: dict[str, float] | None = None,
        tolerance: float = 5.0,
    ) -> None:
        if kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported spectrum kind {kind!r}; supported: {sorted(SUPPORTED_KINDS)}")
        self.kind = kind
        self.reference = dict(reference) if reference else {}
        self.tolerance = float(tolerance)

    # ------------------------------------------------------------------
    # format support / rejection
    # ------------------------------------------------------------------

    def x_unit_labels(self) -> dict[str, str]:
        """Return the x-axis unit label for each supported kind."""
        return dict(_X_UNIT_LABELS)

    # ------------------------------------------------------------------
    # peak detection + integration
    # ------------------------------------------------------------------

    def detect_peaks(
        self,
        xs: list[float],
        ys: list[float],
        threshold: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Return local maxima above ``threshold``.

        A peak is a point whose y is greater than its immediate neighbors and
        greater than or equal to ``threshold``. Endpoints are never peaks.
        """
        if len(xs) != len(ys):
            raise ValueError("xs and ys must have equal length")
        peaks: list[dict[str, Any]] = []
        for i in range(1, len(ys) - 1):
            y = ys[i]
            if y < threshold:
                continue
            if y > ys[i - 1] and y > ys[i + 1]:
                peaks.append(
                    {
                        "x": float(xs[i]),
                        "height": float(y),
                        "index": i,
                        "kind": self.kind,
                    }
                )
        return peaks

    def integrate(
        self,
        xs: list[float],
        ys: list[float],
        x_min: float | None = None,
        x_max: float | None = None,
    ) -> float:
        """Trapezoidal integral of ``ys`` over ``xs`` within ``[x_min, x_max]``.

        Bounds default to the full x-range. Points outside the window are
        skipped. The xs must be sorted ascending.
        """
        if len(xs) != len(ys):
            raise ValueError("xs and ys must have equal length")
        lo = xs[0] if x_min is None else x_min
        hi = xs[-1] if x_max is None else x_max
        if hi < lo:
            raise ValueError("x_max must be >= x_min")

        area = 0.0
        for i in range(len(xs) - 1):
            x0, x1 = xs[i], xs[i + 1]
            y0, y1 = ys[i], ys[i + 1]
            if x1 < lo or x0 > hi:
                continue
            # clip to window for partial-width trapezoids
            cx0 = max(x0, lo)
            cx1 = min(x1, hi)
            if cx1 <= cx0:
                continue
            # linearly interpolate y at the clipped edges
            span = x1 - x0
            if span == 0:
                continue
            frac0 = (cx0 - x0) / span
            frac1 = (cx1 - x0) / span
            iy0 = y0 + frac0 * (y1 - y0)
            iy1 = y0 + frac1 * (y1 - y0)
            area += 0.5 * (iy0 + iy1) * (cx1 - cx0)
        return area

    # ------------------------------------------------------------------
    # reference assignment + matching
    # ------------------------------------------------------------------

    def assign(self, observed_x: float) -> dict[str, Any]:
        """Return the nearest reference entry within ``tolerance``."""
        if not self.reference:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "degraded",
                "assignment": None,
                "observed_x": float(observed_x),
                "limitations": ["no reference table loaded"],
                "errors": [_err("chem.spectra.no_reference", "reference table is empty")],
            }

        best_label: str | None = None
        best_delta = math.inf
        for label, ref_x in self.reference.items():
            delta = abs(float(ref_x) - float(observed_x))
            if delta < best_delta:
                best_delta = delta
                best_label = label

        if best_label is None or best_delta > self.tolerance:
            limitation_msg = (
                f"no reference within tolerance={self.tolerance}; nearest was {best_label} (delta={best_delta:.3g})"
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "degraded",
                "assignment": "unknown",
                "observed_x": float(observed_x),
                "nearest": best_label,
                "nearest_delta": best_delta,
                "tolerance": self.tolerance,
                "limitations": [limitation_msg],
                "errors": [],
            }

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "succeeded",
            "assignment": best_label,
            "observed_x": float(observed_x),
            "reference_x": float(self.reference[best_label]),
            "delta": best_delta,
            "tolerance": self.tolerance,
            "limitations": [],
            "errors": [],
        }

    def match_spectrum(
        self,
        xs_a: list[float],
        ys_a: list[float],
        xs_b: list[float],
        ys_b: list[float],
    ) -> dict[str, Any]:
        """Cosine similarity between two spectra on a shared x-grid.

        The two spectra must share the same ``xs`` grid; otherwise they are
        resampled onto the union grid by nearest-neighbor carry-forward. A
        perfect overlap gives ``similarity = 1.0``; orthogonal spectra give 0.
        """
        if len(xs_a) != len(ys_a) or len(xs_b) != len(ys_b):
            raise ValueError("xs and ys must have equal length within each spectrum")

        ya: list[float] = []
        yb: list[float] = []
        if xs_a == xs_b:
            grid = list(xs_a)
            ya = list(ys_a)
            yb = list(ys_b)
        else:
            # resample onto the union of xs, sorted ascending
            grid = sorted(set(xs_a) | set(xs_b))
            map_a = dict(zip(xs_a, ys_a, strict=True))
            map_b = dict(zip(xs_b, ys_b, strict=True))
            prev_a = 0.0
            prev_b = 0.0
            for x in grid:
                if x in map_a:
                    prev_a = float(map_a[x])
                if x in map_b:
                    prev_b = float(map_b[x])
                ya.append(prev_a)
                yb.append(prev_b)

        dot = sum(a * b for a, b in zip(ya, yb, strict=True))
        na = math.sqrt(sum(a * a for a in ya))
        nb = math.sqrt(sum(b * b for b in yb))
        similarity = 0.0 if (na == 0.0 or nb == 0.0) else dot / (na * nb)

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "succeeded",
            "similarity": similarity,
            "kind": self.kind,
            "method_id": METHOD_ID,
            "run_id": _new_id(),
            "grid_points": len(grid),
            "errors": [],
        }


__all__ = ["SUPPORTED_KINDS", "SpectraAnalyzer"]
