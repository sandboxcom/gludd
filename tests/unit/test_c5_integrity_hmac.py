"""C5 — Integrity store hardening (canonical JSON HMAC).

Issues addressed:
1. ``integrity_db.json`` baseline unsigned — HMAC-sidecar now covers canonical JSON.
2. Corrupt store silently re-baselines (fail-open) — fail-closed on tampered store.
3. Non-canonical HMAC payload allows field-injection — sorted keys + compact
   separators before MAC.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


class TestC5IntegrityHmac:
    """P1 security: integrity store hardening."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "c5-fixed-test-key-01234")
        import general_ludd.integrity.scanner as _m

        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def _scanner(self, tmp):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        store = Path(tmp) / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    # ------------------------------------------------------------------
    # C5-1: stored baseline includes HMAC
    # ------------------------------------------------------------------

    def test_baseline_is_hmac_signed(self):
        """Stored baseline is HMAC signed with a PSK-derived key."""
        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            hashes = {"/a": "hash-a", "/b": "hash-b"}
            scanner._save_hashes(hashes)

            mac_path = store / "integrity_db.mac"
            hwm_path = store / "integrity_db.hwm"
            assert mac_path.exists(), "MAC sidecar must be written"
            assert hwm_path.exists(), "HWM sidecar must be written"

            mac_data = json.loads(mac_path.read_text())
            assert "mac" in mac_data, "MAC sidecar must contain 'mac'"
            assert "counter" in mac_data, "MAC sidecar must contain 'counter'"
            assert mac_data["counter"] >= 1

            hwm_data = json.loads(hwm_path.read_text())
            assert hwm_data["hwm"] >= 1
            assert "mac" in hwm_data, "HWM must be itself signed"

    def test_save_uses_canonical_json_sort_keys(self):
        """_save_hashes MUST canonicalize: sort_keys=True, separators=(',', ':')."""
        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            hashes = {"/z": "last", "/a": "first", "/m": "middle"}
            scanner._save_hashes(hashes)

            raw = (store / "integrity_db.json").read_text()
            assert raw == '{"/a":"first","/m":"middle","/z":"last"}', (
                f"Store must be canonical JSON with sorted keys, got: {raw!r}"
            )

    def test_canonical_json_prevents_field_injection(self):
        """Same data always serializes to identical bytes (sorted keys + compact).

        Field-injection: if keys are not sorted, the same dict can serialize to
        different byte strings, each with a different MAC.  An attacker could
        reorder keys to produce a valid-looking but different payload whose MAC
        still verifies — but with sorted keys + compact separators, the
        canonical form is deterministic.
        """
        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)

            data1 = {"/c": "v3", "/a": "v1", "/b": "v2"}
            data2 = {"/b": "v2", "/a": "v1", "/c": "v3"}  # different insertion order

            scanner._save_hashes(data1)
            canon1 = (store / "integrity_db.json").read_text()

            scanner._save_hashes(data2)
            canon2 = (store / "integrity_db.json").read_text()

            assert canon1 == canon2, (
                "Same logical data with different insertion order "
                "must produce identical canonical bytes"
            )

    def test_valid_hmac_verifies(self):
        """Good HMAC passes verification (round-trip)."""
        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)
            hashes = {"/etc/hostname": "host-hash", "/etc/motd": "motd-hash"}
            scanner._save_hashes(hashes)
            loaded = scanner._load_hashes()
            assert loaded == hashes, "Valid HMAC must pass verification"

    # ------------------------------------------------------------------
    # C5-2: corrupt store fails CLOSED (NOT silent re-baseline)
    # ------------------------------------------------------------------

    def test_corrupt_store_fails_closed(self):
        """Tampered file → IntegrityStoreError, NOT silent re-baseline."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "good-hash"})

            db = store / "integrity_db.json"
            db.write_text("this is not json at all")

            with pytest.raises(IntegrityStoreError, match="unparseable"):
                scanner._load_hashes()

    def test_removed_mac_with_key_fails_closed(self):
        """Store exists but .mac deleted + key configured → error."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h"})
            (store / "integrity_db.mac").unlink()

            with pytest.raises(IntegrityStoreError, match="missing signature"):
                scanner._load_hashes()

    def test_wrong_mac_fails_closed(self):
        """Store OK but .mac tampered → error."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h"})
            (store / "integrity_db.mac").write_text(
                json.dumps({"mac": "bad" * 16, "counter": 1})
            )

            with pytest.raises(IntegrityStoreError, match="tampered signature"):
                scanner._load_hashes()

    def test_rebaseline_is_explicit_not_automatic(self):
        """After tamper, rebaseline() must be called explicitly — load doesn't auto-heal."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/x": "y"})

            db = store / "integrity_db.json"
            db.write_text("garbage")

            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

            # Even a second load must still fail — never auto-heal.
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    # ------------------------------------------------------------------
    # C5-3: rebaseline writes canonical + re-signs
    # ------------------------------------------------------------------

    def test_rebaseline_writes_canonical_json(self):
        """rebaseline() re-writes the store in canonical JSON and re-signs."""
        import general_ludd.integrity.scanner as _m

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)

            monkeypatch_ = pytest.MonkeyPatch()
            monkeypatch_.setenv("GL_INTEGRITY_KEY", "rebase-key-A")
            monkeypatch_.setattr(_m, "_INTEGRITY_KEY", None)

            scanner._save_hashes({"/b": "second", "/a": "first"})
            canon_before = (store / "integrity_db.json").read_text()

            monkeypatch_.setenv("GL_INTEGRITY_KEY", "rebase-key-B")
            monkeypatch_.setattr(_m, "_INTEGRITY_KEY", None)

            recovered = scanner.rebaseline()
            canon_after = (store / "integrity_db.json").read_text()

            assert recovered == {"/a": "first", "/b": "second"}
            assert canon_after == canon_before, (
                "rebaseline must NOT change the store bytes "
                "(it re-signs the existing on-disk bytes, preserving them)"
            )
            assert scanner._load_hashes() == {"/a": "first", "/b": "second"}

            monkeypatch_.undo()

    def test_rebaseline_on_canonical_store_survives_round_trip(self):
        """After rebaseline on canonical store, load still works."""
        import general_ludd.integrity.scanner as _m

        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)

            monkeypatch_ = pytest.MonkeyPatch()
            monkeypatch_.setenv("GL_INTEGRITY_KEY", "roundtrip-A")
            monkeypatch_.setattr(_m, "_INTEGRITY_KEY", None)
            scanner._save_hashes({"/k": "v"})

            monkeypatch_.setenv("GL_INTEGRITY_KEY", "roundtrip-B")
            monkeypatch_.setattr(_m, "_INTEGRITY_KEY", None)
            scanner.rebaseline()

            assert scanner._load_hashes() == {"/k": "v"}

            monkeypatch_.setenv("GL_INTEGRITY_KEY", "roundtrip-B")
            monkeypatch_.setattr(_m, "_INTEGRITY_KEY", None)
            scanner._save_hashes({"/k": "v2"})
            assert scanner._load_hashes() == {"/k": "v2"}

            monkeypatch_.undo()
