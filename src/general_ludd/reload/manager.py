"""Reload manager — handles live reload of harness components with rollback."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import cast


class ReloadType(Enum):
    CONFIG = "config"
    PROMPTS = "prompts"
    RULES = "rules"
    WORKER_CODE = "worker_code"
    EVENT_LOOP_CODE = "event_loop_code"
    SCHEMA_MIGRATION = "schema_migration"


@dataclass
class ReloadResult:
    reload_id: str
    reload_type: ReloadType
    status: str
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class ReloadStatus:
    reload_id: str
    type: ReloadType
    status: str
    started_at: str
    completed_at: str | None = None


class ReloadManager:
    def __init__(self) -> None:
        self._reload_store: dict[str, dict[str, object]] = {}

    def request_reload(
        self, reload_type: ReloadType, config: dict[str, object] | None = None
    ) -> ReloadResult:
        reload_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        self._reload_store[reload_id] = {
            "reload_type": reload_type,
            "status": "pending",
            "config": config,
            "started_at": now,
            "completed_at": None,
            "message": "",
        }
        return ReloadResult(
            reload_id=reload_id,
            reload_type=reload_type,
            status="pending",
            message="Reload requested",
            timestamp=now,
        )

    def execute_reload(self, reload_id: str) -> ReloadResult:
        entry = self._reload_store.get(reload_id)
        if entry is None:
            return ReloadResult(
                reload_id=reload_id,
                reload_type=ReloadType.CONFIG,
                status="failed",
                message="Unknown reload_id",
            )

        # FAIL-OPEN FIX (BUG#2): this manager performs NO real reload — it only
        # mutates in-memory bookkeeping. Reporting "success" for a reload that
        # ran, validated, and touched nothing lets a self-improvement be marked
        # "applied" with zero verification. Report an honest, non-success status
        # so nothing downstream can treat this no-op as a verified live swap. A
        # genuine code reload goes through HotReloader.reload_code_module, which
        # requires a passing health gate before it reports success.
        now = datetime.now(UTC).isoformat()
        entry["status"] = "no_op"
        entry["completed_at"] = now
        entry["message"] = (
            f"No-op: {cast(ReloadType, entry['reload_type']).value} reload not performed — "
            "no real reload/validation implemented for this target"
        )
        return ReloadResult(
            reload_id=reload_id,
            reload_type=cast(ReloadType, entry["reload_type"]),
            status="no_op",
            message=cast(str, entry["message"]),
            timestamp=now,
        )

    def rollback(self, reload_id: str) -> ReloadResult:
        entry = self._reload_store.get(reload_id)
        if entry is None:
            return ReloadResult(
                reload_id=reload_id,
                reload_type=ReloadType.CONFIG,
                status="failed",
                message="Unknown reload_id",
            )

        now = datetime.now(UTC).isoformat()
        entry["status"] = "rolled_back"
        entry["completed_at"] = now
        entry["message"] = f"Rolled back {cast(ReloadType, entry['reload_type']).value}"
        return ReloadResult(
            reload_id=reload_id,
            reload_type=cast(ReloadType, entry["reload_type"]),
            status="rolled_back",
            message=cast(str, entry["message"]),
            timestamp=now,
        )

    def get_reload_status(self, reload_id: str) -> ReloadStatus:
        entry = self._reload_store.get(reload_id)
        if entry is None:
            now = datetime.now(UTC).isoformat()
            return ReloadStatus(
                reload_id=reload_id,
                type=ReloadType.CONFIG,
                status="unknown",
                started_at=now,
            )
        return ReloadStatus(
            reload_id=reload_id,
            type=cast(ReloadType, entry["reload_type"]),
            status=cast(str, entry["status"]),
            started_at=cast(str, entry["started_at"]),
            completed_at=cast(str | None, entry["completed_at"]),
        )
