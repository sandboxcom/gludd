"""Chronic-blocker reporter — structured operator-facing report.

Thin wrapper around :meth:`BlockerDetector.chronic_blockers` that returns a
stable dict shape consumed by both:

  - ``GET /admin/remediation/chronic-blockers`` (PSK-gated daemon endpoint)
  - ``gludd remediation chronic-blockers [--json]`` CLI

The shape is intentionally additive: callers may add top-level keys but the
existing ones are part of the contract (tests + the CLI pretty-printer
depend on them).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from general_ludd.remediation.blocker_detector import BlockerDetector

logger = logging.getLogger(__name__)


async def chronic_blocker_report(
    detector: BlockerDetector,
    project_id: str | None = None,
    lookback_days: int | None = None,
) -> dict[str, Any]:
    """Return a structured chronic-blocker report.

    Returns a dict with::

        {
          "generated_at": "<iso utc>",
          "lookback_days": int,
          "project_id": str | null,
          "chronic_blockers": [
            {
              "task_type": str,
              "blocker_kind": str,
              "incident_count": int,
              "first_seen": "<iso>",
              "last_seen": "<iso>",
              "recent_todo_ids": [str, ...],
            },
            ...
          ],
          "total": int,
        }
    """
    blockers = await detector.chronic_blockers(lookback_days=lookback_days)
    generated_at = datetime.now(UTC).isoformat()
    return {
        "generated_at": generated_at,
        "lookback_days": lookback_days or detector.config.chronic_lookback_days,
        "project_id": project_id,
        "chronic_blockers": [
            {
                "task_type": b.task_type,
                "blocker_kind": b.blocker_kind,
                "incident_count": b.incident_count,
                "first_seen": b.first_seen.isoformat(),
                "last_seen": b.last_seen.isoformat(),
                "recent_todo_ids": list(b.recent_todo_ids),
            }
            for b in blockers
        ],
        "total": len(blockers),
    }
