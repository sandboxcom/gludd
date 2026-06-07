"""Tests for file integrity monitoring."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path


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


class TestIntegritySigning:
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
