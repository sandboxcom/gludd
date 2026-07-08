"""Writer subprocess package (B3.1.3 — WP-B1).

Extracts the daemon's writer path (event-loop claim/review/reconcile) into a
dedicated subprocess so DB-write responsibility is isolated from the gunicorn
HTTP workers. Slice 1 ships only the parent-side lifecycle
(:class:`WriterProcess`); the real EventLoop integration lands in Slice 3.

See ``docs/STABILIZATION_PLAN.md`` WP-B1 for the work-package spec.
"""

from __future__ import annotations

from general_ludd.writer.process import WriterProcess

__all__ = ["WriterProcess"]

