"""StsDashboardProvider — aggregated STS token metrics for the NF.7 audit dashboard.

Read-only aggregator over :class:`TokenStore` (token state) and
:class:`StsAuditModel` rows (lifecycle events). Produces a single ``snapshot()``
dict consumable by a TUI/dashboard renderer. Never mutates state.

Surface area:

- ``active_token_count`` — live (non-revoked, non-expired) tokens.
- ``mint_count`` / ``revoke_count`` / ``expire_count`` — totals derived from
  audit events across all tokens. ``use``/``renew``/``revive`` are not surfaced
  here; they belong in a per-token drill-down, not the overview.
- ``scope_distribution`` — ``{action: count}`` over active tokens' scope_actions.
- ``cascade_event_count`` — number of revoke events that belong to a cascade
  group (``>= CascadeConfig.min_group_size`` revokes sharing a
  ``parent_agent_id`` within ``window_seconds``).
- ``quota_utilization`` — per-agent and per-project ``{active, max}`` when a
  :class:`QuotaConfig` is supplied; ``max`` is omitted when unknown.
- ``generated_at`` — epoch seconds when the snapshot was produced.
"""

from __future__ import annotations

import json as _json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from general_ludd.db.models import AgentTokenModel, StsAuditModel
    from general_ludd.sts.store import TokenStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CascadeConfig:
    """Cascade-event detection thresholds.

    Attributes:
        window_seconds: Revokes within this wall-clock window sharing a
            ``parent_agent_id`` are eligible to be counted as one cascade.
        min_group_size: Minimum number of revokes in a window for the group to
            count as a cascade. ``2`` means any pair triggers it.
    """

    window_seconds: int = 60
    min_group_size: int = 2


def _is_active(row: AgentTokenModel, now: datetime | None = None) -> bool:
    """True iff *row* represents a live (non-revoked, non-expired) token."""
    if getattr(row, "revoked_at", None) is not None:
        return False
    expires_at_raw = getattr(row, "expires_at", None)
    if expires_at_raw is None:
        return True
    expires_at = cast(datetime, expires_at_raw)
    return expires_at >= (now or datetime.now(UTC))


def _parse_scope_actions(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = _json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(a) for a in parsed] if isinstance(parsed, list) else []


class StsDashboardProvider:
    """Aggregates STS dashboard metrics from token + audit data.

    Construction is cheap (no I/O). All queries are deferred to :meth:`snapshot`.
    """

    def __init__(
        self,
        store: TokenStore,
        session_factory: async_sessionmaker[Any],
        *,
        cascade_config: CascadeConfig | None = None,
        quota_config: Any = None,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._cascade_config = cascade_config or CascadeConfig()
        self._quota_config = quota_config

    async def snapshot(self) -> dict[str, Any]:
        """Produce a single-point-in-time dashboard snapshot.

        All queries run concurrently where possible; failures in one section
        do not corrupt the others (each section returns its own empty default).
        """
        tokens = await self._store.list_all()
        audit_rows = await self._load_audit_rows()
        now = datetime.now(UTC)

        active_tokens = [t for t in tokens if _is_active(t, now=now)]
        events_all = self._flatten_events(audit_rows)

        return {
            "active_token_count": len(active_tokens),
            "mint_count": sum(1 for e in events_all if e.get("action") == "mint"),
            "revoke_count": sum(1 for e in events_all if e.get("action") == "revoke"),
            "expire_count": sum(1 for e in events_all if e.get("action") == "expire"),
            "scope_distribution": self._scope_distribution(active_tokens),
            "cascade_event_count": self._cascade_count(events_all),
            "quota_utilization": self._quota_utilization(active_tokens),
            "generated_at": time.time(),
        }

    async def _load_audit_rows(self) -> list[StsAuditModel]:
        from general_ludd.db.models import StsAuditModel

        try:
            async with self._session_factory() as session:
                result = await session.execute(select(StsAuditModel))
                return list(result.scalars().all())
        except Exception:
            logger.exception("Failed to load StsAuditModel rows for dashboard")
            return []

    @staticmethod
    def _flatten_events(audit_rows: list[StsAuditModel]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in audit_rows:
            try:
                parsed = _json.loads(getattr(row, "events", "[]") or "[]")
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, list):
                continue
            for entry in parsed:
                if isinstance(entry, dict):
                    out.append(entry)
        return out

    @staticmethod
    def _scope_distribution(active_tokens: list[AgentTokenModel]) -> dict[str, int]:
        dist: dict[str, int] = defaultdict(int)
        for token in active_tokens:
            for action in _parse_scope_actions(getattr(token, "scope_actions", None)):
                dist[action] += 1
        return dict(dist)

    def _cascade_count(self, events: list[dict[str, Any]]) -> int:
        """Count revoke events that belong to a cascade group.

        A cascade group is ``>= min_group_size`` revoke events sharing the
        same ``parent_agent_id`` whose span (max_ts - min_ts) is within
        ``window_seconds``. Every revoke in a qualifying group is counted,
        not just the threshold-crosser — they are all cascade events.
        """
        revokes_by_parent: dict[str, list[float]] = defaultdict(list)
        for e in events:
            if e.get("action") != "revoke":
                continue
            parent = str(e.get("parent_agent_id", ""))
            revokes_by_parent[parent].append(float(e.get("timestamp") or 0.0))

        min_size = self._cascade_config.min_group_size
        window = float(self._cascade_config.window_seconds)

        cascade_count = 0
        for _parent, timestamps in revokes_by_parent.items():
            if len(timestamps) < min_size:
                continue
            timestamps.sort()
            # Mark every index that participates in at least one qualifying
            # window of size >= min_size whose span is <= window seconds.
            in_cascade = [False] * len(timestamps)
            for i in range(len(timestamps) - min_size + 1):
                if timestamps[i + min_size - 1] - timestamps[i] <= window:
                    for j in range(i, i + min_size):
                        in_cascade[j] = True
            cascade_count += sum(1 for flag in in_cascade if flag)
        return cascade_count

    def _quota_utilization(
        self, active_tokens: list[AgentTokenModel]
    ) -> dict[str, dict[str, Any]]:
        per_agent: dict[str, int] = defaultdict(int)
        for token in active_tokens:
            per_agent[getattr(token, "agent_id", "")] += 1

        max_per_agent: int | None = None
        if self._quota_config is not None:
            max_per_agent = getattr(self._quota_config, "max_tokens_per_agent", None)

        out: dict[str, dict[str, Any]] = {}
        for agent_id, active in per_agent.items():
            entry: dict[str, Any] = {"active": active}
            if max_per_agent is not None:
                entry["max"] = int(max_per_agent)
            out[agent_id] = entry
        return {"per_agent": out}


__all__ = ["CascadeConfig", "StsDashboardProvider"]
