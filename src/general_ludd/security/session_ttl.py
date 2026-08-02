"""D-16: Session token timeout enforcement.

SessionManager tracks sessions with absolute TTL, idle TTL, rotation,
revocation, and audience validation. Shared state is file-based so
multiple Gunicorn workers on the same machine share session state.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

_DEFAULT_ABSOLUTE_TTL = 3600
_DEFAULT_IDLE_TTL = 900


class SessionValidation(Enum):
    VALID = auto()
    EXPIRED = auto()
    REVOKED = auto()
    WRONG_AUDIENCE = auto()
    UNKNOWN = auto()


@dataclass
class SessionRecord:
    session_id: str
    audience: str
    created_at: float
    last_access: float
    absolute_ttl_seconds: int
    idle_ttl_seconds: int
    revoked: bool = False
    parent_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "audience": self.audience,
            "created_at": self.created_at,
            "last_access": self.last_access,
            "absolute_ttl_seconds": self.absolute_ttl_seconds,
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "revoked": self.revoked,
            "parent_session_id": self.parent_session_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        return cls(
            session_id=data["session_id"],
            audience=data["audience"],
            created_at=float(data["created_at"]),
            last_access=float(data["last_access"]),
            absolute_ttl_seconds=int(data["absolute_ttl_seconds"]),
            idle_ttl_seconds=int(data["idle_ttl_seconds"]),
            revoked=bool(data.get("revoked", False)),
            parent_session_id=data.get("parent_session_id"),
        )


@dataclass
class SessionCreateResult:
    session_id: str
    token: str


class SessionManager:
    def __init__(self, state_dir: Path | str | None = None) -> None:
        if state_dir is None:
            xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            state_dir = Path(xdg) / "general-ludd" / "sessions"
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._state_dir, 0o700)
        self._absolute_ttl = _read_env_int("GLUDD_SESSION_ABSOLUTE_TTL", _DEFAULT_ABSOLUTE_TTL)
        self._idle_ttl = _read_env_int("GLUDD_SESSION_IDLE_TTL", _DEFAULT_IDLE_TTL)

    def create_session(
        self,
        audience: str,
        absolute_ttl_seconds: int | None = None,
        idle_ttl_seconds: int | None = None,
    ) -> SessionCreateResult:
        session_id = secrets.token_urlsafe(32)
        token = secrets.token_urlsafe(32)
        now = time.time()
        record = SessionRecord(
            session_id=session_id,
            audience=audience,
            created_at=now,
            last_access=now,
            absolute_ttl_seconds=absolute_ttl_seconds if absolute_ttl_seconds is not None else self._absolute_ttl,
            idle_ttl_seconds=idle_ttl_seconds if idle_ttl_seconds is not None else self._idle_ttl,
        )
        self._write_record(record)
        return SessionCreateResult(session_id=session_id, token=token)

    def validate_session(self, session_id: str, audience: str | None = None) -> SessionValidation:
        record = self._load_record(session_id)
        if record is None:
            return SessionValidation.UNKNOWN
        if record.revoked:
            return SessionValidation.REVOKED
        if audience is not None and record.audience != audience:
            return SessionValidation.WRONG_AUDIENCE
        now = time.time()
        age = now - record.created_at
        idle = now - record.last_access
        if age > record.absolute_ttl_seconds or idle > record.idle_ttl_seconds:
            return SessionValidation.EXPIRED
        return SessionValidation.VALID

    def touch_session(self, session_id: str) -> bool:
        record = self._load_record(session_id)
        if record is None or record.revoked:
            return False
        record.last_access = time.time()
        self._write_record(record)
        return True

    def revoke_session(self, session_id: str) -> bool:
        record = self._load_record(session_id)
        if record is None:
            return False
        record.revoked = True
        self._write_record(record)
        return True

    def rotate_session(self, session_id: str) -> SessionCreateResult | None:
        record = self._load_record(session_id)
        if record is None or record.revoked:
            return None
        record.revoked = True
        self._write_record(record)
        new_result = self.create_session(
            audience=record.audience,
            absolute_ttl_seconds=record.absolute_ttl_seconds,
            idle_ttl_seconds=record.idle_ttl_seconds,
        )
        new_record = self._load_record(new_result.session_id)
        if new_record is not None:
            new_record.parent_session_id = session_id
            self._write_record(new_record)
        return new_result

    def cleanup_expired(self) -> int:
        removed = 0
        now = time.time()
        try:
            for entry in sorted(self._state_dir.iterdir()):
                if not entry.name.endswith(".json"):
                    continue
                record = self._load_record(entry.stem)
                if record is None:
                    entry.unlink(missing_ok=True)
                    removed += 1
                    continue
                if record.revoked:
                    entry.unlink(missing_ok=True)
                    removed += 1
                    continue
                age = now - record.created_at
                idle = now - record.last_access
                if age > record.absolute_ttl_seconds or idle > record.idle_ttl_seconds:
                    entry.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            pass
        return removed

    def _session_path(self, session_id: str) -> Path:
        return self._state_dir / f"{session_id}.json"

    def _load_record(self, session_id: str) -> SessionRecord | None:
        path = self._session_path(session_id)
        try:
            raw = json.loads(path.read_text())
            return SessionRecord.from_dict(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def _write_record(self, record: SessionRecord) -> None:
        path = self._session_path(record.session_id)
        data = json.dumps(record.to_dict())
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data.encode("utf-8"))
        finally:
            os.close(fd)


def _read_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default
