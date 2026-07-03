"""Tests for file integrity monitoring."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest


class TestFileIntegrityScanner:
    def test_scanner_computes_file_hash(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            tf = Path(tmp) / "test.txt"
            tf.write_text("hello world")
            scanner = FileIntegrityScanner(store_dir=tmp)
            h = scanner._hash_file(str(tf))
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert h == expected

    def test_scanner_scan_finds_files(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("a")
            (Path(tmp) / "b.txt").write_text("b")
            scanner = FileIntegrityScanner(store_dir=tmp)
            result = scanner.scan([tmp], exclude_patterns=[])
            assert len(result["files"]) == 2
            assert result["scanned"] == 2

    def test_scanner_detects_changed_files(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "integrity_store"
            store.mkdir()
            watch = Path(tmp) / "watch"
            watch.mkdir()
            tf = watch / "f.txt"
            tf.write_text("original")
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner.scan([str(watch)], exclude_patterns=[])
            tf.write_text("modified")
            result = scanner.scan([str(watch)], exclude_patterns=[])
            changes = [c for c in result["changes"] if c["type"] == "modified"]
            assert len(changes) == 1
            assert changes[0]["file"].endswith("f.txt")

    def test_scanner_detects_new_files(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "integrity_store"
            store.mkdir()
            watch = Path(tmp) / "watch"
            watch.mkdir()
            (watch / "old.txt").write_text("old")
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner.scan([str(watch)], exclude_patterns=[])
            (watch / "new.txt").write_text("new")
            result = scanner.scan([str(watch)], exclude_patterns=[])
            changes = [c for c in result["changes"] if c["type"] == "new"]
            assert len(changes) == 1

    def test_scanner_detects_removed_files(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "integrity_store"
            store.mkdir()
            watch = Path(tmp) / "watch"
            watch.mkdir()
            tf = watch / "del.txt"
            tf.write_text("will delete")
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner.scan([str(watch)], exclude_patterns=[])
            tf.unlink()
            result = scanner.scan([str(watch)], exclude_patterns=[])
            changes = [c for c in result["changes"] if c["type"] == "removed"]
            assert len(changes) == 1

    def test_scanner_excludes_patterns(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            watch = Path(tmp) / "watch"
            watch.mkdir()
            (watch / "include.txt").write_text("x")
            (watch / "exclude.pyc").write_text("x")
            scanner = FileIntegrityScanner(store_dir=tmp)
            result = scanner.scan([str(watch)], exclude_patterns=[r"\.pyc$"])
            names = [Path(p).name for p in result["files"]]
            assert "include.txt" in names
            assert "exclude.pyc" not in names

    def test_scanner_only_non_vc_files(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / "repo" / ".git"
            git_dir.mkdir(parents=True)
            watch = Path(tmp) / "watch"
            watch.mkdir()
            (watch / "tracked.txt").write_text("x")
            scanner = FileIntegrityScanner(store_dir=str(tmp))
            result = scanner.scan([str(watch)], exclude_patterns=[])
            names = [Path(p).name for p in result["files"]]
            assert "tracked.txt" in names  # watch dir is not under repo/.git

    # --- Regression tests for fix: VC-controlled files must be hashed by default ---

    def test_scanner_includes_vc_controlled_files_by_default(self):
        """Files inside a git working tree are scanned by default (skip_vc_controlled=False).

        Regression: the old code called _is_vc_controlled() unconditionally and
        skipped every file under any ancestor .git directory, making the monitor
        blind to tampering of tracked source files.
        """
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            # Simulate a git working tree: create a .git dir at the watch root.
            watch = Path(tmp) / "repo"
            watch.mkdir()
            (watch / ".git").mkdir()
            src_file = watch / "module.py"
            src_file.write_text("def hello(): pass\n")
            scanner = FileIntegrityScanner(store_dir=str(tmp))
            # Default scan — skip_vc_controlled is False, so module.py MUST appear.
            result = scanner.scan([str(watch)], exclude_patterns=[r"[\\/]\.git[\\/]"])
            names = [Path(p).name for p in result["files"]]
            assert "module.py" in names, (
                "scan() excluded a VC-controlled source file by default; "
                "skip_vc_controlled must default to False"
            )

    def test_scanner_detects_tampering_of_vc_controlled_file(self):
        """Modification of a tracked .py file is detected on the second scan."""
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            watch = Path(tmp) / "repo"
            watch.mkdir()
            (watch / ".git").mkdir()
            src_file = watch / "app.py"
            src_file.write_text("x = 1\n")
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner.scan([str(watch)], exclude_patterns=[r"[\\/]\.git[\\/]"])
            # Tamper with the file.
            src_file.write_text("x = 1  # tampered\n")
            result = scanner.scan([str(watch)], exclude_patterns=[r"[\\/]\.git[\\/]"])
            modified = [c for c in result["changes"] if c["type"] == "modified"]
            assert any(Path(c["file"]).name == "app.py" for c in modified), (
                "Tampering with a VC-controlled source file was not detected"
            )

    def test_scanner_skip_vc_controlled_opt_in_excludes_files(self):
        """When skip_vc_controlled=True the old behaviour is restored (opt-in)."""
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            watch = Path(tmp) / "repo"
            watch.mkdir()
            (watch / ".git").mkdir()
            (watch / "module.py").write_text("pass\n")
            scanner = FileIntegrityScanner(store_dir=str(tmp))
            result = scanner.scan(
                [str(watch)],
                exclude_patterns=[r"[\\/]\.git[\\/]"],
                skip_vc_controlled=True,
            )
            names = [Path(p).name for p in result["files"]]
            assert "module.py" not in names, (
                "skip_vc_controlled=True should exclude files under a .git tree"
            )


class TestIntegritySigning:
    @pytest.fixture(autouse=True)
    def _set_integrity_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "test-signing-key-fixedvalue")
        import general_ludd.integrity.scanner as _m
        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def test_sign_change_with_reason(self):
        import datetime

        from general_ludd.integrity.scanner import ChangeRecord, sign_change
        change = ChangeRecord(
            file_path="/tmp/test.txt",
            change_type="modified",
            old_hash="abc123",
            new_hash="def456",
            detected_at=datetime.datetime.now().isoformat(),
            approved=False,
        )
        signed = sign_change(change, reason="Approved config update", signer="admin")
        assert signed["signature"] is not None
        assert signed["reason"] == "Approved config update"
        assert signed["signer"] == "admin"
        assert signed["approved"] is True

    def test_verify_signature(self):
        import datetime

        from general_ludd.integrity.scanner import ChangeRecord, sign_change, verify_signature
        change = ChangeRecord(
            file_path="/tmp/test.txt",
            change_type="modified",
            old_hash="abc123",
            new_hash="def456",
            detected_at=datetime.datetime.now().isoformat(),
            approved=False,
        )
        signed = sign_change(change, reason="ok", signer="admin")
        assert verify_signature(signed) is True

    def test_verify_tampered_signature_fails(self):
        import datetime

        from general_ludd.integrity.scanner import ChangeRecord, sign_change, verify_signature
        change = ChangeRecord(
            file_path="/tmp/test.txt",
            change_type="modified",
            old_hash="abc123",
            new_hash="def456",
            detected_at=datetime.datetime.now().isoformat(),
            approved=False,
        )
        signed = sign_change(change, reason="ok", signer="admin")
        signed["new_hash"] = "tampered"
        assert verify_signature(signed) is False

    def test_sign_change_in_openbao(self):
        from general_ludd.integrity.scanner import sign_change_openbao

        result = sign_change_openbao(
            path="projects/test/config.yml",
            signer="admin",
            reason="Approved by admin",
            secrets_resolver=None,
        )
        assert "path" in result
        assert "signature" in result
        assert result["reason"] == "Approved by admin"


class TestIntegrityKeyFailClosed:
    """Regression: missing GL_INTEGRITY_KEY must fail CLOSED, not mint a random key."""

    def test_missing_key_raises_on_sign_change(self, monkeypatch):
        """sign_change raises IntegrityKeyError when GL_INTEGRITY_KEY is unset."""
        import datetime

        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import ChangeRecord, IntegrityKeyError, sign_change

        change = ChangeRecord(
            file_path="/tmp/test.txt",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at=datetime.datetime.now().isoformat(),
        )
        with pytest.raises(IntegrityKeyError):
            sign_change(change, reason="r", signer="s")

    def test_missing_key_raises_on_sign_change_openbao(self, monkeypatch):
        """sign_change_openbao raises IntegrityKeyError when GL_INTEGRITY_KEY is unset."""
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import IntegrityKeyError, sign_change_openbao

        with pytest.raises(IntegrityKeyError):
            sign_change_openbao(path="/tmp/f", signer="s", reason="r")

    def test_missing_key_verify_returns_false(self, monkeypatch):
        """verify_openbao_signature returns False (not raises) when key is absent."""
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import verify_openbao_signature

        # Should not raise — callers distinguish tamper from key error via bool.
        assert verify_openbao_signature({"signature": "any"}) is False

    def test_key_present_does_not_raise(self, monkeypatch):
        """When GL_INTEGRITY_KEY is set, signing succeeds without error."""
        import datetime

        monkeypatch.setenv("GL_INTEGRITY_KEY", "stable-test-key-abc123")
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import ChangeRecord, sign_change

        change = ChangeRecord(
            file_path="/tmp/ok.txt",
            change_type="new",
            old_hash=None,
            new_hash="deadbeef",
            detected_at=datetime.datetime.now().isoformat(),
        )
        result = sign_change(change, reason="ok", signer="admin")
        assert result["signature"] is not None


class TestSignatureIncludesFileHash:
    """Regression: approval signatures must bind to file hashes.

    Before this fix, sign_change_openbao signed path|signer|reason|timestamp
    with no hash, so an approval could be replayed against a different version
    of the file.
    """

    def test_openbao_signature_includes_hashes(self, monkeypatch):
        """Signed result carries old_hash and new_hash fields."""
        monkeypatch.setenv("GL_INTEGRITY_KEY", "test-key-hash-binding")
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import sign_change_openbao

        result = sign_change_openbao(
            path="/tmp/cfg.yml",
            signer="admin",
            reason="test",
            old_hash="oldhash123",
            new_hash="newhash456",
        )
        assert result.get("old_hash") == "oldhash123"
        assert result.get("new_hash") == "newhash456"
        assert result.get("signature") is not None

    def test_openbao_verify_round_trips(self, monkeypatch):
        """A freshly signed approval verifies correctly."""
        monkeypatch.setenv("GL_INTEGRITY_KEY", "test-key-verify-rt")
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import sign_change_openbao, verify_openbao_signature

        result = sign_change_openbao(
            path="/tmp/cfg.yml",
            signer="admin",
            reason="round-trip",
            old_hash="h1",
            new_hash="h2",
        )
        assert verify_openbao_signature(result) is True

    def test_tampered_file_hash_fails_verification(self, monkeypatch):
        """Changing new_hash after signing makes verify_openbao_signature return False."""
        monkeypatch.setenv("GL_INTEGRITY_KEY", "test-key-tamper-detect")
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import sign_change_openbao, verify_openbao_signature

        result = sign_change_openbao(
            path="/tmp/sec.cfg",
            signer="admin",
            reason="ok",
            old_hash="orig_old",
            new_hash="orig_new",
        )
        # Simulate post-approval tampering of the new file hash.
        tampered = dict(result)
        tampered["new_hash"] = "attacker_hash"
        assert verify_openbao_signature(tampered) is False

    def test_different_hashes_produce_different_signatures(self, monkeypatch):
        """Two approvals for the same path but different hashes yield different sigs."""
        monkeypatch.setenv("GL_INTEGRITY_KEY", "test-key-diff-sig")
        import general_ludd.integrity.scanner as _scanner_mod
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)

        from general_ludd.integrity.scanner import sign_change_openbao

        # Reset cached key so the env var is read fresh each call.
        r1 = sign_change_openbao("/p", "s", "r", old_hash="a", new_hash="b")
        monkeypatch.setattr(_scanner_mod, "_INTEGRITY_KEY", None)
        r2 = sign_change_openbao("/p", "s", "r", old_hash="a", new_hash="CHANGED")
        assert r1["signature"] != r2["signature"]


class TestHashStoreSigned:
    """Regression: the baseline hash store must be HMAC-signed and fail CLOSED.

    Before this fix, ``_save_hashes`` wrote ``json.dumps(hashes)`` with NO
    signature, so an attacker who edited a monitored file could also edit its
    stored hash and the next ``scan()`` reported "no change".  And
    ``_load_hashes`` swallowed ANY parse error and returned ``{}``, so a
    corrupt/truncated (tampered) store SILENTLY REBASELINED instead of failing
    closed.  The store is now protected by a sidecar HMAC (``integrity_db.mac``)
    keyed with GL_INTEGRITY_KEY.
    """

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "store-signing-key-fixed")
        import general_ludd.integrity.scanner as _m
        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def _scanner(self, tmp):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        store = Path(tmp) / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_save_then_load_round_trips_with_valid_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            hashes = {"/a": "hash-a", "/b": "hash-b"}
            scanner._save_hashes(hashes)
            # A sidecar MAC file must have been written.
            assert (store / "integrity_db.mac").exists()
            assert scanner._load_hashes() == hashes

    def test_missing_store_returns_empty_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)
            # No store on disk yet → legitimate empty baseline, no raise.
            assert scanner._load_hashes() == {}

    def test_tampered_store_hash_fails_closed(self):
        """Attacker edits a stored hash but cannot re-sign → load raises."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/etc/passwd": "good-hash"})  # pragma: allowlist secret
            db = store / "integrity_db.json"
            data = json.loads(db.read_text())
            data["/etc/passwd"] = "attacker-planted-hash"  # pragma: allowlist secret
            db.write_text(json.dumps(data, indent=2))
            # MUST NOT silently return {} and rebaseline.
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_tampered_mac_fails_closed(self):
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h"})
            mac = store / "integrity_db.mac"
            mac.write_text("deadbeef" * 8)  # wrong signature
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_deleted_mac_fails_closed_downgrade(self):
        """Absent signature with a key configured = downgrade attack → raise."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h"})
            (store / "integrity_db.mac").unlink()
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_truncated_store_fails_closed_not_rebaseline(self):
        """Corrupt/truncated store must raise, NOT return {} and rebaseline."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h"})
            (store / "integrity_db.json").write_text('{"a": "b"')  # truncated JSON
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_scan_tamper_of_store_is_detected_end_to_end(self):
        """Attacker edits a watched file AND its stored hash → next scan raises."""
        from general_ludd.integrity.scanner import (
            FileIntegrityScanner,
            IntegrityStoreError,
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            watch = Path(tmp) / "watch"
            watch.mkdir()
            target = watch / "f.txt"
            target.write_text("original")
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner.scan([str(watch)], exclude_patterns=[])

            # Attacker modifies the file and forges the stored hash to match,
            # but lacks GL_INTEGRITY_KEY so the sidecar MAC is now stale.
            target.write_text("malicious")
            new_hash = hashlib.sha256(b"malicious").hexdigest()
            db = store / "integrity_db.json"
            data = json.loads(db.read_text())
            data[str(target)] = new_hash
            db.write_text(json.dumps(data, indent=2))

            with pytest.raises(IntegrityStoreError):
                scanner.scan([str(watch)], exclude_patterns=[])


class TestHashStoreNoKey:
    """Without GL_INTEGRITY_KEY the store behaves like the ChangeRecord path:
    it does not crash normal operation (no integrity guarantee is possible)."""

    @pytest.fixture(autouse=True)
    def _clear_key(self, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        import general_ludd.integrity.scanner as _m
        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def test_round_trips_without_key_and_writes_no_sidecar(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            scanner = FileIntegrityScanner(store_dir=str(store))
            scanner._save_hashes({"/a": "h"})
            # No key → cannot sign, so no sidecar is written.
            assert not (store / "integrity_db.mac").exists()
            # Normal operation continues: load returns the hashes best-effort.
            assert scanner._load_hashes() == {"/a": "h"}

    def test_missing_store_without_key_returns_empty(self):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            scanner = FileIntegrityScanner(store_dir=str(store))
            assert scanner._load_hashes() == {}


class TestHashStoreDeletedStore:
    """Gap A: a surviving signature whose store was deleted is an attack, not a
    first run.  A true first run has NEITHER the store NOR a signature."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "deleted-store-key-fixed")
        import general_ludd.integrity.scanner as _m
        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def _scanner(self, tmp):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        store = Path(tmp) / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_deleted_store_with_surviving_mac_fails_closed(self):
        """rm integrity_db.json but leave a valid .mac + key set → raise."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/etc/passwd": "good-hash"})  # pragma: allowlist secret
            # Attacker deletes only the store, leaving the signed sidecar behind.
            (store / "integrity_db.json").unlink()
            assert (store / "integrity_db.mac").exists()
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_true_first_run_returns_empty(self):
        """Neither store nor .mac present → legitimate empty baseline (regression)."""
        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            assert not (store / "integrity_db.json").exists()
            assert not (store / "integrity_db.mac").exists()
            assert scanner._load_hashes() == {}


class TestHashStoreAntiRollback:
    """Gap B: an old, legitimately-signed (store, .mac) pair must not re-validate
    once a newer signed state exists (monotonic counter + high-water-mark)."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "anti-rollback-key-fixed")
        import general_ludd.integrity.scanner as _m
        monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)

    def _scanner(self, tmp):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        store = Path(tmp) / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_rollback_to_earlier_signed_pair_is_rejected(self):
        """Restore an earlier signed (store, .mac) pair → load fails closed."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            db = store / "integrity_db.json"
            mac = store / "integrity_db.mac"

            # v1: capture the store + its matching signature (lower counter).
            scanner._save_hashes({"/a": "v1-hash"})
            v1_store = db.read_text()
            v1_mac = mac.read_text()

            # v2: a newer signed state bumps the high-water-mark.
            scanner._save_hashes({"/a": "v2-hash"})
            assert scanner._load_hashes() == {"/a": "v2-hash"}

            # Attacker restores ONLY the old store + its old (valid) signature,
            # leaving the newer high-water-mark in place.
            db.write_text(v1_store)
            mac.write_text(v1_mac)
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_monotonic_save_load_save_round_trips(self):
        """Normal save→load→save must NOT be falsely rejected as a rollback."""
        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h1"})
            assert scanner._load_hashes() == {"/a": "h1"}
            scanner._save_hashes({"/a": "h2", "/b": "h3"})
            assert scanner._load_hashes() == {"/a": "h2", "/b": "h3"}
            scanner._save_hashes({"/a": "h4"})
            assert scanner._load_hashes() == {"/a": "h4"}

    def test_tampered_high_water_mark_fails_closed(self):
        """Editing the .hwm sidecar out of band is itself detected."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h1"})
            hwm = store / "integrity_db.hwm"
            hwm.write_text(json.dumps({"hwm": 0, "mac": "deadbeef" * 8}))
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

    def test_deleted_high_water_mark_fails_closed(self):
        """Deleting only the .hwm (versioned store survives) drops rollback
        protection → fail closed instead of silently trusting."""
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            scanner._save_hashes({"/a": "h1"})
            (store / "integrity_db.hwm").unlink()
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()


class TestHashStoreKeyRotationRebaseline:
    """Gap D: rotating GL_INTEGRITY_KEY hard-raises forever until the operator
    EXPLICITLY rebaselines — recovery must never be automatic on a bad MAC."""

    def _scanner(self, tmp):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        store = Path(tmp) / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_key_rotation_raises_then_explicit_rebaseline_recovers(self, monkeypatch):
        import general_ludd.integrity.scanner as _m
        from general_ludd.integrity.scanner import IntegrityStoreError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)

            # Baseline signed under the ORIGINAL key.
            monkeypatch.setenv("GL_INTEGRITY_KEY", "rotation-key-OLD")
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            scanner._save_hashes({"/a": "h1"})
            assert scanner._load_hashes() == {"/a": "h1"}

            # Operator rotates the key → old MAC no longer verifies → fail closed.
            monkeypatch.setenv("GL_INTEGRITY_KEY", "rotation-key-NEW")
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

            # A bad MAC must NOT silently self-heal: a second load still raises.
            with pytest.raises(IntegrityStoreError):
                scanner._load_hashes()

            # Explicit operator recovery re-signs the current store with the new
            # key; subsequent loads succeed and preserve the recorded hashes.
            recovered = scanner.rebaseline()
            assert recovered == {"/a": "h1"}
            assert scanner._load_hashes() == {"/a": "h1"}

    def test_rebaseline_requires_key(self, monkeypatch):
        import general_ludd.integrity.scanner as _m
        from general_ludd.integrity.scanner import IntegrityKeyError

        with tempfile.TemporaryDirectory() as tmp:
            scanner, _store = self._scanner(tmp)
            monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-needs-key")
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            scanner._save_hashes({"/a": "h1"})
            # Unset the key → rebaseline must fail closed rather than sign blindly.
            monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            with pytest.raises(IntegrityKeyError):
                scanner.rebaseline()

    def test_rebaseline_bumps_counter_above_high_water_mark(self, monkeypatch):
        """After rebaseline the counter must exceed the prior high-water-mark so
        the re-signed store is not itself seen as a rollback."""
        import general_ludd.integrity.scanner as _m

        with tempfile.TemporaryDirectory() as tmp:
            scanner, store = self._scanner(tmp)
            monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-counter-old")
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            scanner._save_hashes({"/a": "h1"})
            scanner._save_hashes({"/a": "h2"})  # counter now 2
            mac_before = json.loads((store / "integrity_db.mac").read_text())

            monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-counter-new")
            monkeypatch.setattr(_m, "_INTEGRITY_KEY", None)
            scanner.rebaseline()
            mac_after = json.loads((store / "integrity_db.mac").read_text())
            assert mac_after["counter"] > mac_before["counter"]
            # And the load path accepts the freshly rebaselined store.
            assert scanner._load_hashes() == {"/a": "h2"}
