"""LangGraph checkpoint persistence for EventLoop tick state.

Wraps langgraph.checkpoint.memory.InMemorySaver (or SqliteSaver when a DB URI
is available) so tick state survives crashes. Checkpoints are keyed by tick_id
and store the phase state dict serialised as JSON.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Any, cast

logger = logging.getLogger(__name__)

_IMPORT_ERROR: str | None = None
try:
    from langgraph.checkpoint.memory import InMemorySaver
    _IMPORT_ERROR = None
except ImportError as exc:
    _IMPORT_ERROR = str(exc)

_HAS_SQLITE_SAVER = False
if _IMPORT_ERROR is None:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore[import-not-found]
        _HAS_SQLITE_SAVER = True
    except ImportError:
        _HAS_SQLITE_SAVER = False


def _tick_config(tick_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"tick:{tick_id}", "checkpoint_ns": "gludd/tick"}}


class TickCheckpointer:
    """Wraps a LangGraph BaseCheckpointSaver for tick-state persistence.

    When the underlying saver is None (graceful degradation), all operations
    are no-ops that return safe defaults.
    """

    def __init__(self, saver: Any | None = None) -> None:
        self._saver = saver
        self._ephemeral: dict[str, dict[str, Any]] = {}
        self._tick_timestamps: dict[str, float] = {}

    @property
    def available(self) -> bool:
        return self._saver is not None

    def put(self, tick_id: str, state: dict[str, Any]) -> None:
        if self._saver is not None:
            import uuid

            config = _tick_config(tick_id)
            ts = time.time()
            cid = str(uuid.uuid4())
            payload = {
                "tick_id": tick_id,
                "state": json.dumps(state),
                "ts": ts,
            }
            checkpoint = {
                "id": cid,
                "channel_values": {"gludd_state": payload},
                "channel_versions": {"gludd_state": cid},
            }
            self._saver.put(config, checkpoint, metadata={}, new_versions={"gludd_state": cid})
            self._tick_timestamps[tick_id] = ts
        elif self._ephemeral is not None:
            self._ephemeral[tick_id] = state

    def get(self, tick_id: str) -> dict[str, Any] | None:
        if self._saver is not None:
            config = _tick_config(tick_id)
            result = self._saver.get_tuple(config)
            if result is None:
                return None
            checkpoint = result.checkpoint if hasattr(result, "checkpoint") else {}
            if not isinstance(checkpoint, dict):
                return None
            channel_values = checkpoint.get("channel_values", {})
            if not isinstance(channel_values, dict):
                return None
            payload = channel_values.get("gludd_state", {})
            if not isinstance(payload, dict) or not payload:
                return None
            state_raw = payload.get("state", "{}")
            if not isinstance(state_raw, str):
                return None
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                return cast(dict[str, Any], json.loads(state_raw))
            return None
        if self._ephemeral is not None and tick_id in self._ephemeral:
            return self._ephemeral[tick_id]
        return None

    def list(self, tick_id: str) -> list[dict[str, Any]]:
        if self._saver is not None:
            config = _tick_config(tick_id)
            results = self._saver.list(config)
            checkpoints: list[dict[str, Any]] = []
            for item in results:
                cp = item.checkpoint if hasattr(item, "checkpoint") else {}
                if not isinstance(cp, dict):
                    continue
                channel_values = cp.get("channel_values", {})
                if not isinstance(channel_values, dict):
                    continue
                payload = channel_values.get("gludd_state", {})
                if not isinstance(payload, dict):
                    continue
                raw = payload.get("state", "{}")
                if not isinstance(raw, str):
                    continue
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    checkpoints.append(json.loads(raw))
            return checkpoints
        if self._ephemeral is not None and tick_id in self._ephemeral:
            return [self._ephemeral[tick_id]]
        return []

    def delete_thread(self, tick_id: str) -> None:
        if self._saver is not None and hasattr(self._saver, "delete_thread"):
            config = _tick_config(tick_id)
            self._saver.delete_thread(config.get("configurable", {}).get("thread_id", ""))
        if self._ephemeral is not None:
            self._ephemeral.pop(tick_id, None)

    def prune(self, max_age_hours: float = 24.0) -> int:
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        pruned = 0
        to_delete = [
            tid for tid, ts in self._tick_timestamps.items() if ts < cutoff
        ]
        for tid in to_delete:
            self.delete_thread(tid)
            self._tick_timestamps.pop(tid, None)
            pruned += 1
        if self._ephemeral is not None:
            old_keys = [k for k in self._ephemeral if k not in self._tick_timestamps]
            for k in old_keys:
                self._ephemeral.pop(k, None)
            self._ephemeral.clear()
        return pruned


def get_checkpointer(db_url: str | None = None) -> TickCheckpointer:
    """Factory: returns a TickCheckpointer backed by SqliteSaver or InMemorySaver.

    When ``db_url`` starts with ``sqlite://`` AND ``SqliteSaver`` is importable,
    the checkpointer is persisted to that file. Otherwise an in-memory saver
    is used (no persistence across process restarts). When langgraph is not
    installed at all, returns a degraded checkpointer backed by an ephemeral
    in-process dict so callers can operate without modification.

    Args:
        db_url: Optional SQLite database URL (e.g. ``sqlite:///path/to/checkpoints.db``).
    """
    if _IMPORT_ERROR is not None:
        logger.warning("langgraph not available: %s — using ephemeral dict", _IMPORT_ERROR)
        return TickCheckpointer(saver=None)

    if db_url and db_url.startswith("sqlite:///") and _HAS_SQLITE_SAVER:
        db_path = db_url[len("sqlite:///") :]
        try:
            saver = SqliteSaver.from_conn_string(db_path)
            logger.info("SqliteSaver created at %s", db_path)
            return TickCheckpointer(saver=saver)
        except Exception as exc:
            logger.warning("SqliteSaver init failed (%s), falling back to InMemorySaver", exc)

    saver = InMemorySaver()
    logger.info("InMemorySaver created")
    return TickCheckpointer(saver=saver)
