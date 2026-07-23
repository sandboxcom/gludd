"""Unit tests for PauseStore — durable signed pause-state persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from general_ludd.controllers.pause_store import IntegrityError, PauseStore, PauseStoreError, default_pause_dir


def _write_file(path: Path, content: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    return path


def _corrupt_mac(state_dir: Path) -> None:
    mac_path = state_dir / "pause_state.json.mac"
    if mac_path.exists():
        mac_path.write_text("deadbeef", encoding="utf-8")


# --- default_pause_dir ---


def test_default_pause_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_PAUSE_DIR", "/custom/pause")
    assert default_pause_dir() == Path("/custom/pause")


def test_default_pause_dir_from_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_PAUSE_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    assert default_pause_dir() == Path("/xdg/data/general-ludd/pause")


def test_default_pause_dir_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_PAUSE_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = default_pause_dir()
    assert "general-ludd" in str(result)
    assert result.name == "pause"


# --- save + load round-trip (with MAC) ---


def test_round_trip_basic(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    records = [{"id": "a", "paused": True}, {"id": "b"}]
    store.save(records)
    loaded = store.load()
    assert loaded == records


def test_round_trip_empty_list(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([])
    assert store.load() == []


def test_load_no_state_file(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    assert store.load() == []


def test_round_trip_multiple_saves(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"v": 1}])
    store.save([{"v": 2}, {"v": 3}])
    loaded = store.load()
    assert loaded == [{"v": 2}, {"v": 3}]


def test_mac_sidecar_created(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"x": 1}])
    assert (tmp_path / "pause_state.json").exists()
    assert (tmp_path / "pause_state.json.mac").exists()


def test_secrets_dir_created(tmp_path: Path) -> None:
    PauseStore(base_dir=tmp_path)
    assert (tmp_path / "secrets").is_dir()


def test_keyed_marker_created(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{}])
    assert (tmp_path / ".keyed").exists()
    assert (tmp_path / ".keyed").read_text() == "keyed\n"


# --- integrity: MAC enforcement ---


def test_integrity_error_on_mismatched_mac(tmp_path: Path) -> None:
    store_a = PauseStore(base_dir=tmp_path)
    store_a.save([{"id": 1}])
    _corrupt_mac(tmp_path)
    store_b = PauseStore(base_dir=tmp_path)
    with pytest.raises(IntegrityError, match="MAC mismatch"):
        store_b.load()


def test_integrity_error_on_missing_mac(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    (tmp_path / "pause_state.json.mac").unlink()
    store2 = PauseStore(base_dir=tmp_path)
    with pytest.raises(IntegrityError, match="no MAC sidecar"):
        store2.load()


def test_tampered_payload_detected(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    state = tmp_path / "pause_state.json"
    state.write_text('not valid json', encoding="utf-8")
    store2 = PauseStore(base_dir=tmp_path)
    with pytest.raises(IntegrityError, match="MAC mismatch"):
        store2.load()


# --- _decode + JSON validation ---


def test_decode_non_list(tmp_path: Path) -> None:
    PauseStore(base_dir=tmp_path)
    _write_file(tmp_path / "pause_state.json", '"just-a-string"')
    with pytest.raises(IntegrityError, match="not a JSON list"):
        PauseStore._decode('"just-a-string"')


def test_decode_valid_list(tmp_path: Path) -> None:
    result = PauseStore._decode(json.dumps([{"a": 1}, {"b": 2}]))
    assert result == [{"a": 1}, {"b": 2}]


def test_decode_non_dict_item(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError, match="not a JSON object"):
        PauseStore._decode(json.dumps(["string-item"]))


def test_decode_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError, match="not valid JSON"):
        PauseStore._decode("not json at all")


# --- oversized state file (DoS guard) ---


def test_oversized_state_rejected(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    state = tmp_path / "pause_state.json"
    big = "x" * (PauseStore._MAX_STATE_BYTES + 100)
    state.write_text(big, encoding="utf-8")
    with pytest.raises(IntegrityError, match="exceeding"):
        store.load()


# --- has_durable_key property ---


def test_has_durable_key_true(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    assert store.has_durable_key is True


def test_has_durable_key_false_on_unwritable_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o000)

    class _DeniedStore(PauseStore):
        def _mint_key(self) -> bytes:
            raise OSError("permission denied")

    try:
        store = _DeniedStore(base_dir=tmp_path)
        assert store.has_durable_key is False
    finally:
        os.chmod(secrets_dir, 0o700)


# --- base_dir property ---


def test_base_dir_property(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    assert store.base_dir == tmp_path.resolve()


# --- degraded mode: save clears stale MAC ---


def test_degraded_mode_removes_stale_mac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secrets_dir, 0o000)
    (tmp_path / "pause_state.json.mac").write_text("stale", encoding="utf-8")

    class _DegradedStore(PauseStore):
        def _prior_signing_evidence(self) -> bool:
            return False

        def _mint_key(self) -> bytes:
            raise OSError("permission denied")

    try:
        store = _DegradedStore(base_dir=tmp_path)
        store.save([{"id": 1}])
        loaded = store.load()
        assert loaded == [{"id": 1}]
        assert not (tmp_path / "pause_state.json.mac").exists()
    finally:
        os.chmod(secrets_dir, 0o700)


# --- atomic write produces valid state file ---


def test_atomic_write_permissions(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    state_path = tmp_path / "pause_state.json"
    mode = state_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_atomic_write_no_tmp_left(tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 0


# --- keyfile security: group/world-readable key denied ---


def test_readable_keyfile_rejected(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key_path = secrets_dir / "pause_mac.key"
    key_path.write_bytes(b"x" * 32)
    os.chmod(key_path, 0o644)
    (tmp_path / ".keyed").write_text("keyed\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="group/world accessible"):
        PauseStore(base_dir=tmp_path)


# --- keyfile: empty keyfile with prior signing evidence fail-closed ---


def test_empty_keyfile_with_prior_keyed_fails(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "pause_mac.key").write_bytes(b"")
    (tmp_path / ".keyed").write_text("keyed\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="empty"):
        PauseStore(base_dir=tmp_path)


# --- keyfile: missing key with prior signing evidence fail-closed ---


def test_missing_key_with_prior_mac_fails(tmp_path: Path) -> None:
    (tmp_path / "pause_state.json.mac").write_text("abc123", encoding="utf-8")
    with pytest.raises(IntegrityError, match="missing"):
        PauseStore(base_dir=tmp_path)


# --- pause_store_error base class ---


def test_pause_store_error_is_runtime_error() -> None:
    assert issubclass(PauseStoreError, RuntimeError)


def test_integrity_error_is_pause_store_error() -> None:
    assert issubclass(IntegrityError, PauseStoreError)


# --- state file unreadable during load ---


def test_load_unreadable_state_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = PauseStore(base_dir=tmp_path)
    store.save([{"id": 1}])
    os.chmod(tmp_path / "pause_state.json", 0o000)
    with pytest.raises(PauseStoreError, match="unreadable"):
        store.load()
    os.chmod(tmp_path / "pause_state.json", 0o600)
