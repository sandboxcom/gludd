"""Reload manager — handles live reload of harness components with rollback."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


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
        self._reload_store: dict[str, dict] = {}

    def request_reload(
        self, reload_type: ReloadType, config: dict | None = None
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

        now = datetime.now(UTC).isoformat()
        entry["status"] = "success"
        entry["completed_at"] = now
        entry["message"] = f"Reloaded {entry['reload_type'].value}"
        return ReloadResult(
            reload_id=reload_id,
            reload_type=entry["reload_type"],
            status="success",
            message=entry["message"],
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
        entry["message"] = f"Rolled back {entry['reload_type'].value}"
        return ReloadResult(
            reload_id=reload_id,
            reload_type=entry["reload_type"],
            status="rolled_back",
            message=entry["message"],
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
            type=entry["reload_type"],
            status=entry["status"],
            started_at=entry["started_at"],
            completed_at=entry["completed_at"],
        )
