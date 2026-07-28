"""Unit tests for the file-integrity scanner (C5 coverage gap).

The scanner was previously exercised ONLY via the ``/admin/selftest`` E2E and
the router's ``/admin/integrity/*`` endpoints. These tests drive the scanner's
public surface DIRECTLY (no running daemon, no FastAPI TestClient) so the core
logic — SHA-256 hashing, before/after change detection, HMAC sign/verify, and
the GL_INTEGRITY_KEY fail-closed behaviour — is covered in isolation.

Path confinement lives in ``general_ludd.security.sanitize.is_path_within``
(the router calls it via ``_confine_scan_paths``); it is exercised here as the
unit that rejects an escape path, since the scanner itself only walks the paths
it is handed.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from general_ludd.integrity import scanner as scanner_mod
from general_ludd.integrity.scanner import (
    ChangeRecord,
    FileIntegrityScanner,
    IntegrityKeyError,
    _get_integrity_key,
    sign_change,
    sign_change_openbao,
    verify_openbao_signature,
    verify_signature,
)
from general_ludd.security.sanitize import is_path_within


@pytest.fixture(autouse=True)
def _reset_integrity_key():
    """The module caches GL_INTEGRITY_KEY in a global; reset it around each test
    so one test's key (or absence) cannot leak into another."""
    saved = scanner_mod._INTEGRITY_KEY
    scanner_mod._INTEGRITY_KEY = None
    yield
    scanner_mod._INTEGRITY_KEY = saved


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------
class TestHashFile:
    def test_hash_matches_known_sha256(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        f = tmp_path / "known.txt"
        payload = b"the quick brown fox\n"
        f.write_bytes(payload)

        expected = hashlib.sha256(payload).hexdigest()
        assert scanner._hash_file(str(f)) == expected

    def test_hash_empty_file_is_sha256_of_empty(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert scanner._hash_file(str(f)) == hashlib.sha256(b"").hexdigest()

    def test_hash_missing_file_returns_empty_string(self, tmp_path):
        # _hash_file swallows read errors and returns "" so the scan loop skips
        # the file rather than crashing.
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        assert scanner._hash_file(str(tmp_path / "does-not-exist.txt")) == ""


# ---------------------------------------------------------------------------
# scan(): baseline, change detection, persistence
# ---------------------------------------------------------------------------
class TestScanChangeDetection:
    def _scanner(self, tmp_path):
        # store_dir is the persisted-hash DB location; keep it OUT of the watched
        # tree so the integrity_db.json itself is never hashed/reported.
        store = tmp_path / "store"
        store.mkdir()
        watch = tmp_path / "watch"
        watch.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), watch

    def test_first_scan_reports_all_as_new(self, tmp_path):
        scanner, watch = self._scanner(tmp_path)
        (watch / "a.txt").write_text("alpha")
        (watch / "b.txt").write_text("beta")

        result = scanner.scan([str(watch)])

        assert result["scanned"] == 2
        types = {c["type"] for c in result["changes"]}
        assert types == {"new"}
        files = {c["file"] for c in result["changes"]}
        assert files == {str(watch / "a.txt"), str(watch / "b.txt")}

    def test_unchanged_file_not_reported_on_rescan(self, tmp_path):
        scanner, watch = self._scanner(tmp_path)
        (watch / "a.txt").write_text("alpha")
        scanner.scan([str(watch)])  # baseline

        result = scanner.scan([str(watch)])  # nothing changed
        assert result["changes"] == []

    def test_modification_detected_with_old_and_new_hash(self, tmp_path):
        scanner, watch = self._scanner(tmp_path)
        target = watch / "a.txt"
        target.write_text("before")
        scanner.scan([str(watch)])  # baseline records "before"

        old_hash = hashlib.sha256(b"before").hexdigest()
        new_hash = hashlib.sha256(b"after-changed").hexdigest()
        target.write_text("after-changed")

        result = scanner.scan([str(watch)])
        mods = [c for c in result["changes"] if c["type"] == "modified"]
        assert len(mods) == 1
        assert mods[0]["file"] == str(target)
        assert mods[0]["old_hash"] == old_hash
        assert mods[0]["new_hash"] == new_hash
        assert mods[0]["old_hash"] != mods[0]["new_hash"]

    def test_removal_detected(self, tmp_path):
        scanner, watch = self._scanner(tmp_path)
        target = watch / "gone.txt"
        target.write_text("temporary")
        scanner.scan([str(watch)])  # baseline

        target.unlink()
        result = scanner.scan([str(watch)])
        removed = [c for c in result["changes"] if c["type"] == "removed"]
        assert len(removed) == 1
        assert removed[0]["file"] == str(target)
        assert removed[0]["new_hash"] is None

    def test_exclude_pattern_skips_matching_files(self, tmp_path):
        scanner, watch = self._scanner(tmp_path)
        (watch / "keep.txt").write_text("keep")
        (watch / "skip.pyc").write_text("skip-me")

        result = scanner.scan([str(watch)], exclude_patterns=[r"\.pyc$"])
        files = set(result["files"])
        assert str(watch / "keep.txt") in files
        assert str(watch / "skip.pyc") not in files

    def test_nonexistent_watch_path_is_skipped(self, tmp_path):
        scanner, _watch = self._scanner(tmp_path)
        result = scanner.scan([str(tmp_path / "no-such-dir")])
        assert result["scanned"] == 0
        assert result["changes"] == []

    def test_baseline_persisted_to_store_json(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        watch = tmp_path / "watch"
        watch.mkdir()
        (watch / "a.txt").write_text("alpha")

        scanner = FileIntegrityScanner(store_dir=str(store))
        scanner.scan([str(watch)])

        db = store / "integrity_db.json"
        assert db.exists()
        data = json.loads(db.read_text())
        assert str(watch / "a.txt") in data
        assert data[str(watch / "a.txt")] == hashlib.sha256(b"alpha").hexdigest()


# ---------------------------------------------------------------------------
# Path confinement (router's is_path_within guard)
# ---------------------------------------------------------------------------
class TestPathConfinement:
    def test_path_inside_root_accepted(self, tmp_path):
        inside = tmp_path / "sub" / "file.txt"
        inside.parent.mkdir(parents=True)
        inside.write_text("x")
        assert is_path_within(str(inside), str(tmp_path)) is True

    def test_dotdot_escape_rejected(self, tmp_path):
        root = tmp_path / "jail"
        root.mkdir()
        escape = str(root / ".." / "outside.txt")
        assert is_path_within(escape, str(root)) is False

    def test_absolute_etc_path_rejected(self, tmp_path):
        root = tmp_path / "jail"
        root.mkdir()
        assert is_path_within("/etc/passwd", str(root)) is False

    def test_empty_candidate_fails_closed(self, tmp_path):
        assert is_path_within("", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# GL_INTEGRITY_KEY fail-closed behaviour
# ---------------------------------------------------------------------------
class TestIntegrityKey:
    def test_missing_key_raises_integrity_key_error(self, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        with pytest.raises(IntegrityKeyError, match="GL_INTEGRITY_KEY is not set"):
            _get_integrity_key()

    def test_present_key_returned_and_cached(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-123")
        assert _get_integrity_key() == "secret-123"
        # Cached: a later delenv must not change the answer within the process.
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        assert _get_integrity_key() == "secret-123"


# ---------------------------------------------------------------------------
# HMAC sign / verify round-trips (both the ChangeRecord and openbao paths)
# ---------------------------------------------------------------------------
class TestSignVerify:
    def test_sign_change_then_verify_true(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "key-abc")
        change = ChangeRecord(
            file_path="/tmp/x",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        signed = sign_change(change, reason="ok", signer="admin")
        assert signed["approved"] is True
        assert signed["signature"]
        assert verify_signature(signed) is True

    def test_verify_detects_tamper(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "key-abc")
        change = ChangeRecord(
            file_path="/tmp/x",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        signed = sign_change(change, reason="ok", signer="admin")
        signed["new_hash"] = "tampered"  # payload no longer matches signature
        assert verify_signature(signed) is False

    def test_sign_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        change = ChangeRecord(file_path="/tmp/x", change_type="new")
        with pytest.raises(IntegrityKeyError):
            sign_change(change, reason="r", signer="s")

    def test_verify_without_key_returns_false_not_raises(self, monkeypatch):
        # verify_signature mirrors verify_openbao: a missing key means "cannot
        # verify" → tamper-equivalent False, NOT an exception.
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        assert verify_signature({"signature": "anything"}) is False

    def test_openbao_sign_then_verify_true(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "key-xyz")
        signed = sign_change_openbao(
            path="/tmp/file",
            signer="admin",
            reason="approved",
            old_hash="old",
            new_hash="new",
        )
        assert signed["signature"]
        assert signed["backend"] == "local-hmac"
        assert verify_openbao_signature(signed) is True

    def test_openbao_verify_detects_path_tamper(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "key-xyz")
        signed = sign_change_openbao(
            path="/tmp/file",
            signer="admin",
            reason="approved",
            old_hash="old",
            new_hash="new",
        )
        signed["path"] = "/tmp/evil"
        assert verify_openbao_signature(signed) is False

    def test_openbao_sign_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        with pytest.raises(IntegrityKeyError):
            sign_change_openbao(path="/tmp/x", signer="s", reason="r")


# ---------------------------------------------------------------------------
# C-INTEGRITY: pipe-join field-boundary-shift forgery
# ---------------------------------------------------------------------------
class TestPipeCollisionForgery:
    """The four sign/verify functions used ``"|".join(...)`` without escaping,
    length-prefix, or scheme tags, so an attacker can shift field boundaries
    (e.g. ``file_path="config", change_type="app|modified"`` vs
    ``file_path="config|app", change_type="modified"`` produce identical HMAC
    payloads).  These tests prove the collision EXISTS before the fix and is
    ELIMINATED after it."""

    def _setup_key(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "forgery-test-key")

    def test_sign_change_pipe_collision_no_longer_forges(self, monkeypatch):
        """After the JSON fix two different field arrangements must NOT produce
        the same HMAC signature."""
        self._setup_key(monkeypatch)
        c1 = ChangeRecord(
            file_path="config",
            change_type="app|modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        c2 = ChangeRecord(
            file_path="config|app",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        s1 = sign_change(c1, reason="ok", signer="admin")
        s2 = sign_change(c2, reason="ok", signer="admin")
        assert s1["signature"] != s2["signature"], (
            "Pipe-join collision: different field arrangements produced identical HMAC — the fix is not active"
        )

    def test_pipe_collision_reproduces_identical_hmac(self, monkeypatch):
        """Document the bug: under raw pipe-join, the two arrangements ARE
        identical because serialization discards field boundaries."""
        self._setup_key(monkeypatch)
        parts_a = ["config", "app|modified", "aaa", "bbb", "2026-01-01T00:00:00"]
        parts_b = ["config|app", "modified", "aaa", "bbb", "2026-01-01T00:00:00"]
        joined_a = "|".join(parts_a)
        joined_b = "|".join(parts_b)
        assert joined_a == joined_b, (
            "Sanity check: pipe-join on these inputs should produce identical payloads — the collision is real"
        )

    def test_openbao_pipe_collision_no_longer_forges(self, monkeypatch):
        """After the fix, sign_change_openbao must use distinct JSON payloads
        for different field arrangements."""
        self._setup_key(monkeypatch)
        s1 = sign_change_openbao(
            path="a|b",
            signer="c",
            reason="approved",
            old_hash="old",
            new_hash="new",
        )
        s2 = sign_change_openbao(
            path="a",
            signer="b|c",
            reason="approved",
            old_hash="old",
            new_hash="new",
        )
        assert s1["signature"] != s2["signature"], (
            "OpenBao pipe-join collision: different field arrangements produced identical HMAC — the fix is not active"
        )

    def test_scheme_tag_blocks_cross_scheme_replay(self, monkeypatch):
        """A signature produced by sign_change MUST NOT verify under
        verify_openbao_signature (and vice-versa), because each scheme now
        tags its payload with a distinct scheme identifier."""
        self._setup_key(monkeypatch)
        change = ChangeRecord(
            file_path="/tmp/x",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        signed = sign_change(change, reason="ok", signer="admin")
        assert signed["signature"]
        # sign_change → verify_openbao_signature must be rejected
        assert verify_openbao_signature(signed) is False, (
            "Cross-scheme replay: sign_change sig verified by verify_openbao_signature"
        )
        # sign_change_openbao → verify_signature must be rejected
        openbao_signed = sign_change_openbao(
            path="/tmp/x",
            signer="admin",
            reason="ok",
            old_hash="aaa",
            new_hash="new",
        )
        assert openbao_signed["signature"]
        assert verify_signature(openbao_signed) is False, (
            "Cross-scheme replay: sign_change_openbao sig verified by verify_signature"
        )

    def test_verify_signature_roundtrip_still_works(self, monkeypatch):
        """Sanity: the fix must not break self-consistent round-trips."""
        self._setup_key(monkeypatch)
        change = ChangeRecord(
            file_path="/tmp/x",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
            detected_at="2026-01-01T00:00:00",
        )
        signed = sign_change(change, reason="ok", signer="admin")
        assert verify_signature(signed) is True

    def test_verify_openbao_signature_roundtrip_still_works(self, monkeypatch):
        """Sanity: the fix must not break self-consistent round-trips."""
        self._setup_key(monkeypatch)
        signed = sign_change_openbao(
            path="/tmp/x",
            signer="admin",
            reason="approved",
            old_hash="old",
            new_hash="new",
        )
        assert verify_openbao_signature(signed) is True
