"""Deep coverage tests for integrity scanner — MAC parsing, HWM edge cases,
_parse_store non-dict shapes, FileWatcher/event handler, sign_change_openbao
with SecretsWriter, rebaseline edge cases, multi-path scanning, and more.

These cover the gaps the existing three integrity test files do not exercise.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import cast

import pytest
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from general_ludd.integrity import scanner as scanner_mod
from general_ludd.integrity.scanner import (
    ChangeRecord,
    FileIntegrityScanner,
    FileWatcher,
    IntegrityKeyError,
    IntegrityStoreError,
    _IntegrityEventHandler,
    sign_change,
    sign_change_openbao,
    verify_openbao_signature,
    verify_signature,
)


@pytest.fixture(autouse=True)
def _reset_integrity_key():
    saved = scanner_mod._INTEGRITY_KEY
    scanner_mod._INTEGRITY_KEY = None
    yield
    scanner_mod._INTEGRITY_KEY = saved


# ---------------------------------------------------------------------------
# _store_mac / _store_mac_versioned / _hwm_mac static helper edge cases
# ---------------------------------------------------------------------------
class TestStoreMacHelpers:
    def test_store_mac_deterministic(self):
        result1 = FileIntegrityScanner._store_mac('{"a":1}', "k")
        result2 = FileIntegrityScanner._store_mac('{"a":1}', "k")
        assert result1 == result2
        assert len(result1) == 64

    def test_store_mac_different_key_different_output(self):
        r1 = FileIntegrityScanner._store_mac('{"a":1}', "key-a")
        r2 = FileIntegrityScanner._store_mac('{"a":1}', "key-b")
        assert r1 != r2

    def test_store_mac_versioned_counter_changes_mac(self):
        r1 = FileIntegrityScanner._store_mac_versioned('{"a":1}', 1, "k")
        r2 = FileIntegrityScanner._store_mac_versioned('{"a":1}', 2, "k")
        assert r1 != r2
        assert len(r1) == 64

    def test_store_mac_versioned_different_data(self):
        r1 = FileIntegrityScanner._store_mac_versioned('{"a":1}', 0, "k")
        r2 = FileIntegrityScanner._store_mac_versioned('{"b":2}', 0, "k")
        assert r1 != r2

    def test_hwm_mac_different_counter(self):
        r1 = FileIntegrityScanner._hwm_mac(0, "k")
        r2 = FileIntegrityScanner._hwm_mac(1, "k")
        assert r1 != r2
        assert len(r1) == 64

    def test_hwm_mac_different_key(self):
        r1 = FileIntegrityScanner._hwm_mac(5, "key-x")
        r2 = FileIntegrityScanner._hwm_mac(5, "key-y")
        assert r1 != r2


# ---------------------------------------------------------------------------
# _parse_mac_sidecar — legacy, versioned, malformed, missing fields
# ---------------------------------------------------------------------------
class TestParseMacSidecar:
    def test_legacy_bare_hex_returns_none_counter(self):
        counter, mac = FileIntegrityScanner._parse_mac_sidecar("a" * 64)
        assert counter is None
        assert mac == "a" * 64

    def test_versioned_json_returns_counter_and_mac(self):
        sidecar = json.dumps({"mac": "b" * 64, "counter": 7})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter == 7
        assert mac == "b" * 64

    def test_versioned_with_counter_zero(self):
        sidecar = json.dumps({"mac": "c" * 64, "counter": 0})
        counter, _mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter == 0

    def test_unparseable_json_treated_as_legacy(self):
        counter, mac = FileIntegrityScanner._parse_mac_sidecar("not-json-at-all")
        assert counter is None
        assert mac == "not-json-at-all"

    def test_json_not_dict_treated_as_tampered(self):
        counter, mac = FileIntegrityScanner._parse_mac_sidecar("[1,2,3]")
        assert counter is None
        assert mac == ""

    def test_json_dict_without_mac_key(self):
        sidecar = json.dumps({"counter": 5})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter is None
        assert mac == ""

    def test_json_dict_without_counter_key(self):
        sidecar = json.dumps({"mac": "d" * 64})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter is None
        assert mac == ""

    def test_json_dict_with_non_int_counter(self):
        sidecar = json.dumps({"mac": "e" * 64, "counter": "not-an-int"})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter is None
        assert mac == ""

    def test_json_dict_with_null_counter(self):
        sidecar = json.dumps({"mac": "f" * 64, "counter": None})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter is None
        assert mac == ""

    def test_json_with_extra_keys_still_parses(self):
        sidecar = json.dumps({"mac": "g" * 64, "counter": 3, "extra": "ignored"})
        counter, mac = FileIntegrityScanner._parse_mac_sidecar(sidecar)
        assert counter == 3
        assert mac == "g" * 64

    def test_empty_string(self):
        counter, mac = FileIntegrityScanner._parse_mac_sidecar("")
        assert counter is None
        assert mac == ""


# ---------------------------------------------------------------------------
# _write_mac_and_hwm — verify correct file contents written
# ---------------------------------------------------------------------------
class TestWriteMacAndHwm:
    def test_writes_both_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "write-hwm-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._write_mac_and_hwm('{"a":"h"}', 5, "write-hwm-key")
        assert scanner._mac_path.exists()
        assert scanner._hwm_path.exists()

    def test_mac_file_contains_versioned_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "mac-shape-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._write_mac_and_hwm('{"a":"h"}', 5, "mac-shape-key")
        data = json.loads(scanner._mac_path.read_text())
        assert "mac" in data
        assert data["counter"] == 5

    def test_hwm_file_contains_hwm_and_mac(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "hwm-shape-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._write_mac_and_hwm('{"a":"h"}', 3, "hwm-shape-key")
        data = json.loads(scanner._hwm_path.read_text())
        assert "hwm" in data
        assert data["hwm"] == 3
        assert "mac" in data


# ---------------------------------------------------------------------------
# _read_current_counter_best_effort — missing file, legacy, unparseable
# ---------------------------------------------------------------------------
class TestReadCurrentCounterBestEffort:
    def test_no_mac_file_returns_zero(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        assert scanner._read_current_counter_best_effort("k") == 0

    def test_legacy_bare_hex_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "counter-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        scanner._mac_path.write_text("a" * 64)
        assert scanner._read_current_counter_best_effort("k") == 0

    def test_versioned_sidecar_returns_counter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "counter-v-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h1"})
        scanner._save_hashes({"/a": "h2"})
        counter = scanner._read_current_counter_best_effort("counter-v-key")
        assert counter >= 2

    def test_unparseable_mac_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "unparse-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        scanner._mac_path.write_text("{{{broken")
        assert scanner._read_current_counter_best_effort("unparse-key") == 0


# ---------------------------------------------------------------------------
# _read_hwm_value_best_effort — error swallowing
# ---------------------------------------------------------------------------
class TestReadHwmValueBestEffort:
    def test_no_hwm_file_returns_zero(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        assert scanner._read_hwm_value_best_effort("k") == 0

    def test_tampered_hwm_returns_zero_not_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "hwm-best-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        scanner._hwm_path.write_text(json.dumps({"hwm": 0, "mac": "deadbeef" * 8}))
        assert scanner._read_hwm_value_best_effort("hwm-best-key") == 0


# ---------------------------------------------------------------------------
# _verify_mac_and_get_counter — missing mac_path, tampered versioned, empty sidecar
# ---------------------------------------------------------------------------
class TestVerifyMacAndGetCounter:
    def test_missing_mac_path_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "verify-mac-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        with pytest.raises(IntegrityStoreError, match="missing signature"):
            scanner._verify_mac_and_get_counter('{"a":1}', "verify-mac-key")

    def test_legacy_sidecar_correct_mac_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "legacy-v-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        raw = scanner._store.read_text()
        legacy_mac = FileIntegrityScanner._store_mac(raw, "legacy-v-key")
        scanner._mac_path.write_text(legacy_mac)
        counter = scanner._verify_mac_and_get_counter(raw, "legacy-v-key")
        assert counter == 0

    def test_legacy_tampered_mac_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "legacy-bad-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        raw = scanner._store.read_text()
        scanner._mac_path.write_text("b" * 64)
        with pytest.raises(IntegrityStoreError):
            scanner._verify_mac_and_get_counter(raw, "legacy-bad-key")

    def test_versioned_tampered_mac_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "ver-bad-key")
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        scanner._save_hashes({"/a": "h"})
        raw = scanner._store.read_text()
        scanner._mac_path.write_text(json.dumps({"mac": "b" * 64, "counter": 1}))
        with pytest.raises(IntegrityStoreError):
            scanner._verify_mac_and_get_counter(raw, "ver-bad-key")


# ---------------------------------------------------------------------------
# _parse_store — non-dict JSON, empty dict valid
# ---------------------------------------------------------------------------
class TestParseStore:
    def test_empty_dict_is_valid(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        result = scanner._parse_store("{}")
        assert result == {}

    def test_list_json_raises(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        with pytest.raises(IntegrityStoreError):
            scanner._parse_store("[1, 2, 3]")

    def test_string_json_raises(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        with pytest.raises(IntegrityStoreError):
            scanner._parse_store('"just a string"')

    def test_null_json_raises(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        with pytest.raises(IntegrityStoreError):
            scanner._parse_store("null")

    def test_int_json_raises(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        with pytest.raises(IntegrityStoreError):
            scanner._parse_store("42")


# ---------------------------------------------------------------------------
# _load_hashes — deleted store variants, no key vs key, hwm-only survivor
# ---------------------------------------------------------------------------
class TestLoadHashesEdgeCases:
    def _scanner(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_deleted_store_with_only_hwm_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "hwm-only-key")
        scanner, store = self._scanner(tmp_path)
        scanner._save_hashes({"/a": "h"})
        (store / "integrity_db.json").unlink()
        (store / "integrity_db.mac").unlink()
        # Only .hwm survives — it's a deletion attack
        with pytest.raises(IntegrityStoreError):
            scanner._load_hashes()

    def test_store_exists_no_key_returns_hashes_unverified(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        scanner, store = self._scanner(tmp_path)
        store_file = store / "integrity_db.json"
        store_file.write_text(json.dumps({"/x": "hash-x"}))
        assert scanner._load_hashes() == {"/x": "hash-x"}

    def test_store_exists_no_key_corrupt_store_still_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        scanner, store = self._scanner(tmp_path)
        (store / "integrity_db.json").write_text("{{{bad")
        with pytest.raises(IntegrityStoreError):
            scanner._load_hashes()


# ---------------------------------------------------------------------------
# _is_vc_controlled — .git and .svn, nested, non-VC
# ---------------------------------------------------------------------------
class TestIsVcControlled:
    def test_git_directory_detected(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        (tmp_path / ".git").mkdir()
        f = tmp_path / "src" / "file.py"
        f.parent.mkdir()
        f.write_text("pass")
        assert scanner._is_vc_controlled(str(f)) is True

    def test_svn_directory_detected(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        (tmp_path / ".svn").mkdir()
        f = tmp_path / "src" / "file.py"
        f.parent.mkdir()
        f.write_text("pass")
        assert scanner._is_vc_controlled(str(f)) is True

    def test_nested_vc_detection(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (tmp_path / "a" / ".git").mkdir()
        f = deep / "file.txt"
        f.write_text("deep")
        assert scanner._is_vc_controlled(str(f)) is True

    def test_non_vc_directory(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        d = tmp_path / "no-vc"
        d.mkdir()
        f = d / "file.txt"
        f.write_text("plain")
        assert scanner._is_vc_controlled(str(f)) is False

    def test_root_path_not_vc(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        assert scanner._is_vc_controlled(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# scan() — multiple watch_paths, subdirectories, tilde expansion, skip_vc with .svn
# ---------------------------------------------------------------------------
class TestScanMultiPaths:
    def _scanner(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store))

    def test_multiple_watch_paths_combined(self, tmp_path):
        scanner = self._scanner(tmp_path)
        a = tmp_path / "dir-a"
        b = tmp_path / "dir-b"
        a.mkdir()
        b.mkdir()
        (a / "a1.txt").write_text("alpha")
        (b / "b1.txt").write_text("beta")
        result = scanner.scan([str(a), str(b)])
        assert result["scanned"] == 2
        names = {str(a / "a1.txt"), str(b / "b1.txt")}
        assert set(cast("list[str]", result["files"])) == names

    def test_subdirectory_traversal(self, tmp_path):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "watch"
        watch.mkdir()
        sub = watch / "sub"
        sub.mkdir()
        (watch / "root.txt").write_text("r")
        (sub / "deep.txt").write_text("d")
        result = scanner.scan([str(watch)])
        assert result["scanned"] == 2

    def test_one_nonexistent_one_valid_watch_path(self, tmp_path):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "watch"
        watch.mkdir()
        (watch / "file.txt").write_text("data")
        result = scanner.scan([str(tmp_path / "gone"), str(watch)])
        assert result["scanned"] == 1

    def test_skip_vc_controlled_with_svn(self, tmp_path):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "repo"
        watch.mkdir()
        (watch / ".svn").mkdir()
        (watch / "tracked.py").write_text("svn-tracked")
        result = scanner.scan([str(watch)], exclude_patterns=[r"[\\/]\.svn[\\/]"], skip_vc_controlled=True)
        assert result["scanned"] == 0

    def test_tilde_expansion_in_path(self, tmp_path, monkeypatch):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "watch"
        watch.mkdir()
        (watch / "f.txt").write_text("tilde")
        monkeypatch.setenv("HOME", str(tmp_path))
        tilde_path = "~/watch"
        result = scanner.scan([tilde_path])
        assert result["scanned"] == 1

    def test_empty_directory_scan_zero(self, tmp_path):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "empty"
        watch.mkdir()
        result = scanner.scan([str(watch)])
        assert result["scanned"] == 0
        assert result["files"] == []
        assert result["changes"] == []

    def test_binary_file_hashed(self, tmp_path):
        scanner = self._scanner(tmp_path)
        watch = tmp_path / "watch"
        watch.mkdir()
        (watch / "bin.dat").write_bytes(b"\x00\x01\x02\xff")
        result = scanner.scan([str(watch)])
        assert result["scanned"] == 1


# ---------------------------------------------------------------------------
# FileWatcher lifecycle — start/stop/get_changes
# ---------------------------------------------------------------------------
class TestFileWatcherLifecycle:
    def test_get_changes_clears_buffer(self, tmp_path):
        watcher = FileWatcher()
        watcher._changes = [{"type": "new", "file": "/tmp/x"}]
        changes = watcher.get_changes()
        assert len(changes) == 1
        assert watcher.get_changes() == []

    def test_stop_before_start_no_error(self):
        watcher = FileWatcher()
        watcher.stop()

    def test_stop_when_not_alive_no_error(self):
        watcher = FileWatcher()
        watcher._observer = None
        watcher.stop()

    def test_start_watches_existing_directory(self, tmp_path):
        watcher = FileWatcher()
        watch = tmp_path / "watch"
        watch.mkdir()
        watcher.start([str(watch)])
        assert watcher._observer is not None
        watcher.stop()

    def test_start_skips_nonexistent_directories(self, tmp_path):
        watcher = FileWatcher()
        watcher.start([str(tmp_path / "does-not-exist")])
        watcher.stop()


# ---------------------------------------------------------------------------
# _IntegrityEventHandler — event types, directory filtering
# ---------------------------------------------------------------------------
class TestIntegrityEventHandler:
    def _handler(self):
        changes: list[dict[str, object]] = []
        lock = threading.Lock()
        return _IntegrityEventHandler(changes, lock), changes

    def test_on_created_file(self):
        handler, changes = self._handler()
        handler.on_created(FileCreatedEvent("/tmp/new.txt"))
        assert len(changes) == 1
        assert changes[0]["type"] == "new"
        assert changes[0]["file"] == "/tmp/new.txt"

    def test_on_created_directory_filtered(self):
        handler, changes = self._handler()
        handler.on_created(DirCreatedEvent("/tmp/new_dir"))
        assert len(changes) == 0

    def test_on_modified_file(self):
        handler, changes = self._handler()
        handler.on_modified(FileModifiedEvent("/tmp/mod.txt"))
        assert len(changes) == 1
        assert changes[0]["type"] == "modified"
        assert changes[0]["file"] == "/tmp/mod.txt"

    def test_on_modified_directory_filtered(self):
        handler, changes = self._handler()
        handler.on_modified(DirModifiedEvent("/tmp/mod_dir"))
        assert len(changes) == 0

    def test_on_deleted_file(self):
        handler, changes = self._handler()
        handler.on_deleted(FileDeletedEvent("/tmp/del.txt"))
        assert len(changes) == 1
        assert changes[0]["type"] == "removed"
        assert changes[0]["file"] == "/tmp/del.txt"

    def test_on_deleted_directory_filtered(self):
        handler, changes = self._handler()
        handler.on_deleted(DirDeletedEvent("/tmp/del_dir"))
        assert len(changes) == 0

    def test_on_moved_file(self):
        handler, changes = self._handler()
        handler.on_moved(FileMovedEvent("/tmp/src.txt", "/tmp/dst.txt"))
        assert len(changes) == 1
        assert changes[0]["type"] == "moved"
        assert changes[0]["file"] == "/tmp/src.txt"
        assert changes[0]["dest"] == "/tmp/dst.txt"

    def test_on_moved_directory_filtered(self):
        handler, changes = self._handler()
        handler.on_moved(DirMovedEvent("/tmp/src_dir", "/tmp/dst_dir"))
        assert len(changes) == 0


# ---------------------------------------------------------------------------
# sign_change_openbao — with a mock SecretsWriter
# ---------------------------------------------------------------------------
class TestSignChangeOpenbaoWriter:
    class _FakeSecretsWriter:
        def __init__(self):
            self.written: list[tuple[str, dict[str, object]]] = []

        def write_secret(self, path: str, value: dict[str, object]) -> None:
            self.written.append((path, value))

    def test_writes_to_secrets_resolver(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "bao-writer-key")
        writer = self._FakeSecretsWriter()
        result = sign_change_openbao(
            path="/etc/cfg.yml",
            signer="admin",
            reason="approved",
            old_hash="old-h",
            new_hash="new-h",
            secrets_resolver=writer,
        )
        assert result["backend"] == "openbao"
        assert result["signature"]
        assert len(writer.written) == 1
        path, value = writer.written[0]
        assert path.startswith("integrity/")
        assert "signature" in value

    def test_failing_writer_falls_back_to_hmac(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "bao-fail-key")

        class _FailingWriter:
            def write_secret(self, path, value):
                raise RuntimeError("vault down")

            def hasattr_self(self):
                pass

        writer = _FailingWriter()
        result = sign_change_openbao(
            path="/tmp/f",
            signer="s",
            reason="r",
            old_hash="o",
            new_hash="n",
            secrets_resolver=writer,
        )
        assert result["backend"] == "openbao-unavailable-fallback-hmac"
        assert result["signature"]


# ---------------------------------------------------------------------------
# verify_openbao_signature — edge cases
# ---------------------------------------------------------------------------
class TestVerifyOpenbaoSignatureEdges:
    def test_verify_with_none_signature_returns_false(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "vfy-none-key")
        assert verify_openbao_signature({"signature": None}) is False

    def test_verify_with_empty_signature_returns_false(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "vfy-empty-key")
        assert verify_openbao_signature({"signature": ""}) is False

    def test_verify_with_missing_signature_key_returns_false(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "vfy-miss-key")
        assert verify_openbao_signature({}) is False

    def test_verify_without_key_returns_false(self, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        assert verify_openbao_signature({"signature": "abc"}) is False


# ---------------------------------------------------------------------------
# rebaseline — store missing, requires key, counter bump
# ---------------------------------------------------------------------------
class TestRebaselineEdges:
    def _scanner(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store

    def test_rebaseline_no_store_on_disk_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-nostore")
        scanner, _store = self._scanner(tmp_path)
        with pytest.raises(IntegrityStoreError, match="no integrity hash store"):
            scanner.rebaseline()

    def test_rebaseline_requires_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-then-del")
        scanner, _store = self._scanner(tmp_path)
        scanner._save_hashes({"/a": "h"})
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        with pytest.raises(IntegrityKeyError):
            scanner.rebaseline()

    def test_rebaseline_counter_exceeds_prior_hwm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-cnt-key")
        scanner, store = self._scanner(tmp_path)
        scanner._save_hashes({"/a": "h1"})
        scanner._save_hashes({"/a": "h2"})
        hwm_before = json.loads((store / "integrity_db.hwm").read_text())
        monkeypatch.setenv("GL_INTEGRITY_KEY", "rebase-cnt-key2")
        scanner.rebaseline()
        hwm_after = json.loads((store / "integrity_db.hwm").read_text())
        assert hwm_after["hwm"] > hwm_before["hwm"]


# ---------------------------------------------------------------------------
# ChangeRecord dataclass defaults + sign_change round-trip with all fields
# ---------------------------------------------------------------------------
class TestChangeRecordDefaults:
    def test_default_values(self):
        cr = ChangeRecord(file_path="/x", change_type="new")
        assert cr.old_hash is None
        assert cr.new_hash is None
        assert cr.detected_at == ""
        assert cr.approved is False
        assert cr.reason == ""
        assert cr.signer == ""
        assert cr.signature is None

    def test_full_round_trip_with_all_fields_populated(self, monkeypatch):
        monkeypatch.setenv("GL_INTEGRITY_KEY", "round-full-key")
        cr = ChangeRecord(
            file_path="/a/b",
            change_type="modified",
            old_hash="old-hash",
            new_hash="new-hash",
            detected_at="2026-08-01T12:00:00",
            approved=False,
            reason="",
            signer="",
            signature=None,
        )
        signed = sign_change(cr, reason="legit change", signer="ops")
        assert signed["approved"] is True
        assert signed["reason"] == "legit change"
        assert signed["signer"] == "ops"
        assert verify_signature(signed) is True


# ---------------------------------------------------------------------------
# _save_hashes / _load_hashes: key configured mid-session (transition)
# ---------------------------------------------------------------------------
class TestKeyTransition:
    def test_first_save_without_key_then_save_with_key_upgrades(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
        scanner, store = self._scanner(tmp_path)
        scanner._save_hashes({"/a": "h"})
        assert not (store / "integrity_db.mac").exists()
        assert not (store / "integrity_db.hwm").exists()
        monkeypatch.setenv("GL_INTEGRITY_KEY", "upgrade-key")
        scanner._save_hashes({"/a": "h2"})
        assert (store / "integrity_db.mac").exists()
        assert (store / "integrity_db.hwm").exists()

    def _scanner(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        return FileIntegrityScanner(store_dir=str(store)), store


# ---------------------------------------------------------------------------
# scan(): binary file with null bytes handled by _hash_file
# ---------------------------------------------------------------------------
class TestHashFileEdges:
    def test_binary_file_returns_hex_digest(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        f = tmp_path / "bin"
        f.write_bytes(b"\x00\xff\xab\x12")
        result = scanner._hash_file(str(f))
        assert len(result) == 64
        assert result == hashlib.sha256(b"\x00\xff\xab\x12").hexdigest()

    def test_large_file_is_hashed(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        f = tmp_path / "large.bin"
        f.write_bytes(b"X" * 100000)
        result = scanner._hash_file(str(f))
        assert len(result) == 64
        assert result == hashlib.sha256(b"X" * 100000).hexdigest()

    def test_unreadable_file_returns_empty_string(self, tmp_path):
        scanner = FileIntegrityScanner(store_dir=str(tmp_path))
        assert scanner._hash_file(str(tmp_path / "nope.bin")) == ""
