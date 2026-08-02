"""TDD tests for D-16: Session token timeout enforcement.

SessionManager enforces absolute TTL, idle TTL, rotation, revocation,
and audience validation across workers via file-based shared state.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from general_ludd.security.session_ttl import (
    SessionCreateResult,
    SessionManager,
    SessionValidation,
)


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


@pytest.fixture
def mgr(tmp_state_dir: Path) -> SessionManager:
    return SessionManager(state_dir=tmp_state_dir)


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------


def test_create_session_returns_token(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    assert isinstance(result, SessionCreateResult)
    assert len(result.session_id) >= 43
    assert len(result.token) >= 43
    assert result.session_id != result.token


def test_create_session_different_ids(mgr: SessionManager) -> None:
    a = mgr.create_session(audience="api")
    b = mgr.create_session(audience="api")
    assert a.session_id != b.session_id
    assert a.token != b.token


def test_create_session_persists_to_disk(mgr: SessionManager, tmp_state_dir: Path) -> None:
    result = mgr.create_session(audience="api")
    session_file = tmp_state_dir / f"{result.session_id}.json"
    assert session_file.exists()
    raw = json.loads(session_file.read_text())
    assert raw["session_id"] == result.session_id
    assert raw["audience"] == "api"
    assert "created_at" in raw
    assert "last_access" in raw


def test_create_session_stores_audience(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="admin")
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.audience == "admin"


def test_create_session_records_timestamps(mgr: SessionManager) -> None:
    before = time.time()
    result = mgr.create_session(audience="api")
    after = time.time()
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert before - 2 <= record.created_at <= after + 2
    assert before - 2 <= record.last_access <= after + 2


# ---------------------------------------------------------------------------
# Session validation
# ---------------------------------------------------------------------------


def test_validate_fresh_session_is_valid(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


def test_validate_expired_absolute_ttl(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=1)
    time.sleep(1.1)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.EXPIRED


def test_validate_expired_idle_ttl(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", idle_ttl_seconds=1)
    time.sleep(1.1)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.EXPIRED


def test_validate_touch_resets_idle_timer(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", idle_ttl_seconds=2)
    time.sleep(0.2)
    mgr.touch_session(result.session_id)
    time.sleep(0.3)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


def test_validate_wrong_audience(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    validation = mgr.validate_session(result.session_id, audience="admin")
    assert validation is SessionValidation.WRONG_AUDIENCE


def test_validate_unknown_session(mgr: SessionManager) -> None:
    validation = mgr.validate_session("nonexistent_id_12345", audience="api")
    assert validation is SessionValidation.UNKNOWN


def test_validate_near_expiry_still_valid(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=1)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoke_session(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    assert mgr.revoke_session(result.session_id) is True
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.REVOKED


def test_revoke_nonexistent_session(mgr: SessionManager) -> None:
    assert mgr.revoke_session("nonexistent_id") is False


def test_revoke_idempotent(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    assert mgr.revoke_session(result.session_id) is True
    assert mgr.revoke_session(result.session_id) is True


def test_validate_revoked_supersedes_expired(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=0)
    mgr.revoke_session(result.session_id)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.REVOKED


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def test_rotate_session_creates_new_token(mgr: SessionManager) -> None:
    old = mgr.create_session(audience="api")
    new = mgr.rotate_session(old.session_id)
    assert new is not None
    assert new.session_id != old.session_id
    assert new.token != old.token


def test_rotate_session_revokes_old(mgr: SessionManager) -> None:
    old = mgr.create_session(audience="api")
    mgr.rotate_session(old.session_id)
    validation = mgr.validate_session(old.session_id, audience="api")
    assert validation is SessionValidation.REVOKED


def test_rotate_session_new_is_valid(mgr: SessionManager) -> None:
    old = mgr.create_session(audience="api")
    new = mgr.rotate_session(old.session_id)
    assert new is not None
    validation = mgr.validate_session(new.session_id, audience="api")
    assert validation is SessionValidation.VALID


def test_rotate_session_preserves_audience(mgr: SessionManager) -> None:
    old = mgr.create_session(audience="admin")
    new = mgr.rotate_session(old.session_id)
    assert new is not None
    record = mgr._load_record(new.session_id)
    assert record is not None
    assert record.audience == "admin"


def test_rotate_nonexistent_returns_none(mgr: SessionManager) -> None:
    assert mgr.rotate_session("nonexistent_id") is None


def test_rotate_already_revoked(mgr: SessionManager) -> None:
    old = mgr.create_session(audience="api")
    mgr.revoke_session(old.session_id)
    new = mgr.rotate_session(old.session_id)
    assert new is None


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_cleanup_removes_expired_sessions(mgr: SessionManager, tmp_state_dir: Path) -> None:
    r1 = mgr.create_session(audience="api", absolute_ttl_seconds=0)
    r2 = mgr.create_session(audience="api", absolute_ttl_seconds=3600)
    time.sleep(0.1)
    removed = mgr.cleanup_expired()
    assert removed >= 1
    assert not (tmp_state_dir / f"{r1.session_id}.json").exists()
    assert (tmp_state_dir / f"{r2.session_id}.json").exists()


def test_cleanup_removes_revoked(mgr: SessionManager, tmp_state_dir: Path) -> None:
    r1 = mgr.create_session(audience="api")
    mgr.revoke_session(r1.session_id)
    removed = mgr.cleanup_expired()
    assert removed >= 1
    assert not (tmp_state_dir / f"{r1.session_id}.json").exists()


def test_cleanup_empty_dir(mgr: SessionManager) -> None:
    assert mgr.cleanup_expired() == 0


def test_cleanup_keeps_active_sessions(mgr: SessionManager, tmp_state_dir: Path) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=3600)
    removed = mgr.cleanup_expired()
    assert removed == 0
    assert (tmp_state_dir / f"{result.session_id}.json").exists()


# ---------------------------------------------------------------------------
# Clock skew tolerance
# ---------------------------------------------------------------------------


def test_clock_skew_tolerance_within_bound(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=60)
    record = mgr._load_record(result.session_id)
    assert record is not None
    record.last_access = time.time() + 10
    mgr._write_record(record)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


def test_clock_skew_past_creation(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api", absolute_ttl_seconds=60)
    record = mgr._load_record(result.session_id)
    assert record is not None
    record.created_at = time.time() + 10
    mgr._write_record(record)
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


# ---------------------------------------------------------------------------
# Concurrency (cross-worker via file-based state)
# ---------------------------------------------------------------------------


def test_two_managers_share_state(tmp_state_dir: Path) -> None:
    mgr1 = SessionManager(state_dir=tmp_state_dir)
    mgr2 = SessionManager(state_dir=tmp_state_dir)
    result = mgr1.create_session(audience="api")
    validation = mgr2.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.VALID


def test_revoke_visible_across_managers(tmp_state_dir: Path) -> None:
    mgr1 = SessionManager(state_dir=tmp_state_dir)
    mgr2 = SessionManager(state_dir=tmp_state_dir)
    result = mgr1.create_session(audience="api")
    mgr2.revoke_session(result.session_id)
    validation = mgr1.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.REVOKED


# ---------------------------------------------------------------------------
# Defaults and configuration
# ---------------------------------------------------------------------------


def test_default_absolute_ttl(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.absolute_ttl_seconds == 3600


def test_default_idle_ttl(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.idle_ttl_seconds == 900


def test_env_var_overrides_default_ttl(tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_SESSION_ABSOLUTE_TTL", "7200")
    monkeypatch.setenv("GLUDD_SESSION_IDLE_TTL", "1800")
    mgr = SessionManager(state_dir=tmp_state_dir)
    result = mgr.create_session(audience="api")
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.absolute_ttl_seconds == 7200
    assert record.idle_ttl_seconds == 1800


def test_env_var_invalid_uses_default(tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_SESSION_ABSOLUTE_TTL", "not_a_number")
    mgr = SessionManager(state_dir=tmp_state_dir)
    result = mgr.create_session(audience="api")
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.absolute_ttl_seconds == 3600


# ---------------------------------------------------------------------------
# Record serialization round-trip
# ---------------------------------------------------------------------------


def test_record_to_from_dict(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="admin", absolute_ttl_seconds=1800, idle_ttl_seconds=600)
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert record.session_id == result.session_id
    assert record.audience == "admin"
    assert record.absolute_ttl_seconds == 1800
    assert record.idle_ttl_seconds == 600
    assert record.revoked is False


# ---------------------------------------------------------------------------
# File permission safety
# ---------------------------------------------------------------------------


def test_session_file_is_not_world_readable(mgr: SessionManager, tmp_state_dir: Path) -> None:
    result = mgr.create_session(audience="api")
    session_file = tmp_state_dir / f"{result.session_id}.json"
    mode = session_file.stat().st_mode
    assert mode & 0o077 == 0


def test_session_directory_is_restricted(mgr: SessionManager, tmp_state_dir: Path) -> None:
    mode = tmp_state_dir.stat().st_mode
    assert mode & 0o077 == 0


# ---------------------------------------------------------------------------
# Token format safety
# ---------------------------------------------------------------------------


def test_token_does_not_contain_session_id(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    assert result.session_id not in result.token


def test_session_id_is_url_safe(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    assert "/" not in result.session_id
    assert "+" not in result.session_id


# ---------------------------------------------------------------------------
# Audience as access boundary
# ---------------------------------------------------------------------------


def test_audience_null_matches(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api")
    # None audience = skip audience check (for internal use)
    validation = mgr.validate_session(result.session_id, audience=None)
    assert validation is SessionValidation.VALID


def test_audience_exact_match_required(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="api-v2")
    validation = mgr.validate_session(result.session_id, audience="api")
    assert validation is SessionValidation.WRONG_AUDIENCE


# ---------------------------------------------------------------------------
# SessionRecord dataclass
# ---------------------------------------------------------------------------


def test_session_record_fields(mgr: SessionManager) -> None:
    result = mgr.create_session(audience="web", absolute_ttl_seconds=500, idle_ttl_seconds=200)
    record = mgr._load_record(result.session_id)
    assert record is not None
    assert isinstance(record.session_id, str)
    assert isinstance(record.audience, str)
    assert isinstance(record.created_at, float)
    assert isinstance(record.last_access, float)
    assert isinstance(record.absolute_ttl_seconds, int)
    assert isinstance(record.idle_ttl_seconds, int)
    assert isinstance(record.revoked, bool)
