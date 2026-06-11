"""Tests for M10: integrity approvals persisted + change events.

Verifies:
1. Integrity key requires GL_INTEGRITY_KEY (no hardcoded default fallback)
2. Signed changes are verifiable
3. Scanner detects changes and returns actionable results
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestM10IntegrityKey:
    def test_key_from_env_var_persists(self):
        from general_ludd.integrity.scanner import _get_integrity_key

        with patch.dict(os.environ, {"GL_INTEGRITY_KEY": "test-key-123"}, clear=True):
            import general_ludd.integrity.scanner as mod
            mod._INTEGRITY_KEY = None
            key1 = _get_integrity_key()
            key2 = _get_integrity_key()
            assert key1 == key2
            assert key1 == "test-key-123"

    def test_key_without_env_generates_random(self):
        with patch.dict(os.environ, {}, clear=True):
            import general_ludd.integrity.scanner as mod
            mod._INTEGRITY_KEY = None
            key = mod._get_integrity_key()
            assert len(key) == 64
            assert key != "test-key-123"


class TestM10IntegritySigning:
    def test_sign_and_verify_round_trip(self):
        from general_ludd.integrity.scanner import ChangeRecord, sign_change, verify_signature

        with patch.dict(os.environ, {"GL_INTEGRITY_KEY": "test-secret"}, clear=True):
            import general_ludd.integrity.scanner as mod
            mod._INTEGRITY_KEY = None

            record = ChangeRecord(
                file_path="/tmp/test.txt",
                change_type="modified",
                old_hash="abc123",
                new_hash="def456",
                detected_at="2026-01-01T00:00:00",
            )
            signed = sign_change(record, reason="approved", signer="admin")
            assert signed["approved"] is True
            assert signed["signer"] == "admin"
            assert signed["signature"] is not None

            assert verify_signature(signed) is True

    def test_tampered_signature_fails_verification(self):
        from general_ludd.integrity.scanner import ChangeRecord, sign_change, verify_signature

        with patch.dict(os.environ, {"GL_INTEGRITY_KEY": "test-secret"}, clear=True):
            import general_ludd.integrity.scanner as mod
            mod._INTEGRITY_KEY = None

            record = ChangeRecord(
                file_path="/tmp/test.txt",
                change_type="modified",
                old_hash="abc123",
                new_hash="def456",
                detected_at="2026-01-01T00:00:00",
            )
            signed = sign_change(record, reason="approved", signer="admin")
            signed["file_path"] = "/tmp/hacked.txt"

            assert verify_signature(signed) is False


class TestM10ScannerChanges:
    def test_scanner_returns_changes_structure(self, tmp_path):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        test_file = tmp_path / "test.txt"
        test_file.write_text("initial content")

        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        result = scanner.scan(watch_paths=[str(tmp_path)])

        assert "scanned" in result
        assert "files" in result
        assert "changes" in result
        assert isinstance(result["changes"], list)

    def test_scanner_detects_new_file(self, tmp_path):
        from general_ludd.integrity.scanner import FileIntegrityScanner

        test_file = tmp_path / "new_file.txt"
        test_file.write_text("hello world")

        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        result = scanner.scan(watch_paths=[str(tmp_path)])

        new_changes = [c for c in result["changes"] if c["type"] == "new"]
        assert len(new_changes) >= 1
        assert any(c["file"] == str(test_file) for c in new_changes)
