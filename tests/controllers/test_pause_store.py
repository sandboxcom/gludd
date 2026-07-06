"""Task #60 hardening tests for :mod:`general_ludd.controllers.pause_store`.

These cover the latent HIGH/MED findings from the SLICE 1 audit that must be
fixed before the store is wired to a caller (#35 SLICE 2/3):

  * H1 — a previously-keyed store whose key becomes unreadable must fail closed,
    never silently degrade to unverified operation.
  * H2 — a signed store whose keyfile is deleted must fail closed, never
    silently mint a replacement key (forge / false-tamper enabler).
  * M-a — refuse a group/world-accessible keyfile.
  * M-b — reject an oversized state payload before json.loads (DoS guard).
  * M-c — MAC domain separation (a signed blob is bound to this context).

The restart-survival + tamper regressions live in ``test_pause_controller.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from general_ludd.controllers.pause_store import IntegrityError, PauseStore


def _base(tmp_path: Path) -> str:
    return str(tmp_path / "pause")


def _keyed_store(tmp_path: Path) -> PauseStore:
    """A store that has minted a durable key and signed one payload."""
    store = PauseStore(base_dir=_base(tmp_path))
    store.save([{"kind": "project", "target_id": "p1"}])
    assert store.has_durable_key is True
    return store


# ---------------------------------------------------------------------------
# H2 — keyfile deleted under signed state must NOT re-mint
# ---------------------------------------------------------------------------


def test_deleted_keyfile_over_signed_state_fails_closed(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    key_path = store.base_dir / "secrets" / "pause_mac.key"
    assert key_path.exists()
    key_path.unlink()  # keyfile gone, but state + .mac + .keyed marker remain

    # A fresh instance over the same base must REFUSE — not mint a new key.
    with pytest.raises(IntegrityError):
        PauseStore(base_dir=_base(tmp_path))

    # And it did not silently recreate the keyfile.
    assert not key_path.exists()


def test_deleted_keyfile_and_marker_still_fails_via_mac_sidecar(
    tmp_path: Path,
) -> None:
    store = _keyed_store(tmp_path)
    (store.base_dir / "secrets" / "pause_mac.key").unlink()
    (store.base_dir / ".keyed").unlink()  # marker gone too...

    # ...but the MAC sidecar still proves the state was signed -> refuse.
    with pytest.raises(IntegrityError):
        PauseStore(base_dir=_base(tmp_path))


# ---------------------------------------------------------------------------
# H1 — unreadable key on a previously-keyed store must NOT silently degrade
# ---------------------------------------------------------------------------


def test_unreadable_key_with_marker_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    _keyed_store(tmp_path)  # mints key + marker + signs state

    real_read = Path.read_bytes

    def boom(self: Path) -> bytes:
        if self.name == "pause_mac.key":
            raise OSError("simulated unreadable keyfile")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", boom)

    fresh = None
    with pytest.raises(IntegrityError):
        fresh = PauseStore(base_dir=_base(tmp_path))
    # Construction failed closed; no degraded instance escaped.
    assert fresh is None


def test_fresh_store_may_degrade_when_secrets_unwritable(
    tmp_path: Path, monkeypatch
) -> None:
    # No prior signing evidence: a genuinely fresh store whose secrets/ dir
    # cannot be created is permitted to degrade (return None) rather than crash.
    real_mint = PauseStore._mint_key

    def fail_mint(self: PauseStore) -> bytes:
        raise OSError("secrets dir unwritable")

    monkeypatch.setattr(PauseStore, "_mint_key", fail_mint)
    store = PauseStore(base_dir=_base(tmp_path))
    assert store.has_durable_key is False  # degraded, but did not raise

    monkeypatch.setattr(PauseStore, "_mint_key", real_mint)  # restore hygiene


# ---------------------------------------------------------------------------
# M-a — insecure keyfile permissions refused
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(os, "getuid"), reason="POSIX permission semantics required"
)
def test_world_readable_keyfile_refused(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    key_path = store.base_dir / "secrets" / "pause_mac.key"
    os.chmod(key_path, 0o644)  # group + world readable

    with pytest.raises(IntegrityError):
        PauseStore(base_dir=_base(tmp_path))


@pytest.mark.skipif(
    not hasattr(os, "getuid"), reason="POSIX permission semantics required"
)
def test_group_readable_keyfile_refused(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    key_path = store.base_dir / "secrets" / "pause_mac.key"
    os.chmod(key_path, 0o640)  # group readable

    with pytest.raises(IntegrityError):
        PauseStore(base_dir=_base(tmp_path))


def test_owner_only_keyfile_accepted(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    key_path = store.base_dir / "secrets" / "pause_mac.key"
    os.chmod(key_path, 0o600)  # the normal, minted mode

    reopened = PauseStore(base_dir=_base(tmp_path))
    assert reopened.has_durable_key is True
    assert reopened.load() == [{"kind": "project", "target_id": "p1"}]


# ---------------------------------------------------------------------------
# M-b — oversized payload rejected before parse
# ---------------------------------------------------------------------------


def test_oversized_state_rejected_before_parse(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    state_path = store.base_dir / "pause_state.json"
    # Well over the cap and NOT valid JSON: if the guard fires first we get an
    # IntegrityError about size, never a JSONDecodeError from parsing.
    state_path.write_text("x" * (PauseStore._MAX_STATE_BYTES + 1), encoding="utf-8")

    with pytest.raises(IntegrityError) as excinfo:
        store.load()
    assert "DoS guard" in str(excinfo.value)


# ---------------------------------------------------------------------------
# M-c — MAC domain separation
# ---------------------------------------------------------------------------


def test_mac_is_domain_separated(tmp_path: Path) -> None:
    store = _keyed_store(tmp_path)
    payload = '[{"kind":"project","target_id":"p1"}]'
    # The signed message is context-prefixed, so the MAC differs from a bare
    # HMAC of the same payload under the same key.
    import hmac
    from hashlib import sha256

    bare = hmac.new(
        store._mac_key, payload.encode("utf-8"), sha256  # type: ignore[arg-type]  # test stub
    ).hexdigest()
    assert store._mac(payload) != bare
    assert store._MAC_CONTEXT and store._MAC_CONTEXT not in payload.encode("utf-8")


# ---------------------------------------------------------------------------
# Marker mechanism
# ---------------------------------------------------------------------------


def test_minting_a_key_writes_the_marker(tmp_path: Path) -> None:
    store = PauseStore(base_dir=_base(tmp_path))
    assert store.has_durable_key is True
    assert (store.base_dir / ".keyed").exists()
