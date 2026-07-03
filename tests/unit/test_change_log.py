"""Tests for the durable, signed, per-file change-record log (change_log.py)."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from general_ludd.integrity import scanner as scanner_mod
from general_ludd.integrity.change_log import ChangeRecordStore


@pytest.fixture(autouse=True)
def _reset_integrity_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scanner module caches GL_INTEGRITY_KEY in a global; reset it around
    each test so one test's key (or absence) cannot leak into another.

    Individual tests set/delete GL_INTEGRITY_KEY via monkeypatch themselves.
    """
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# record → list round-trip
# --------------------------------------------------------------------------

def test_record_then_list_round_trips(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))

    rec = store.record(
        "src/app.py",
        "modified",
        "agent tidied imports",
        old_content="import os\n",
        new_content="import os\nimport sys\n",
        signer="agent-7",
    )

    assert rec.id == 0
    assert rec.file_path == "src/app.py"
    assert rec.change_type == "modified"
    assert rec.reason == "agent tidied imports"
    assert rec.signer == "agent-7"
    assert rec.old_hash == _sha("import os\n")
    assert rec.new_hash == _sha("import os\nimport sys\n")
    assert rec.detected_at  # stamped
    assert rec.old_content == "import os\n"

    records = store.list_records()
    assert len(records) == 1
    got = records[0]
    assert got.file_path == "src/app.py"
    assert got.reason == "agent tidied imports"
    assert got.old_hash == _sha("import os\n")
    assert got.new_hash == _sha("import os\nimport sys\n")
    assert got.old_content == "import os\n"


def test_ids_increment_across_records(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    r0 = store.record("a.py", "new", "add a")
    r1 = store.record("b.py", "new", "add b")
    r2 = store.record("c.py", "new", "add c")
    assert [r0.id, r1.id, r2.id] == [0, 1, 2]
    assert [r.id for r in store.list_records()] == [0, 1, 2]


# --------------------------------------------------------------------------
# old_content snapshot enables a later diff
# --------------------------------------------------------------------------

def test_old_content_snapshot_enables_later_diff(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    old = "line 1\nline 2\nline 3\n"
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("doc.txt", "modified", "edit line 2", old_content=old)

    # A brand-new store instance reading the same dir still recovers the
    # snapshot — a diff can be produced without the source file.
    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    got = store2.list_records()[0]
    assert got.old_content == old


# --------------------------------------------------------------------------
# signing: fail-closed on tamper, round-trip when valid, empty on first run
# --------------------------------------------------------------------------

def test_signed_log_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-key-123")
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)

    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "new", "created x", new_content="print(1)\n")

    # Sidecar MAC written.
    assert (tmp_path / "change_log.mac").exists()

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    records = store2.list_records()
    assert len(records) == 1
    assert records[0].file_path == "x.py"


def test_missing_log_returns_empty_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-key-123")
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    assert store.list_records() == []


def test_tampered_log_body_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-key-123")
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)

    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "modified", "legit change", new_content="a\n")

    log_path = tmp_path / "change_log.json"
    data = json.loads(log_path.read_text())
    data[0]["reason"] = "attacker rewrote this"
    log_path.write_text(json.dumps(data, indent=2))

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    with pytest.raises(scanner_mod.IntegrityStoreError):
        store2.list_records()


def test_missing_mac_with_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-key-123")
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)

    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "new", "created x")

    # Downgrade attack: delete the signature but keep the (valid) body.
    (tmp_path / "change_log.mac").unlink()

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    with pytest.raises(scanner_mod.IntegrityStoreError):
        store2.list_records()


def test_bad_mac_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INTEGRITY_KEY", "secret-key-123")
    monkeypatch.setattr(scanner_mod, "_INTEGRITY_KEY", None, raising=False)

    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "new", "created x")

    (tmp_path / "change_log.mac").write_text("0" * 64)

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    with pytest.raises(scanner_mod.IntegrityStoreError):
        store2.list_records()


# --------------------------------------------------------------------------
# no key configured → operates without signing (no crash)
# --------------------------------------------------------------------------

def test_malformed_record_fails_closed_as_typed_error(tmp_path, monkeypatch):
    # Even without a key, a corrupt record body (invalid base64 old_content)
    # surfaces as a typed IntegrityStoreError, not a raw binascii/ValueError.
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "modified", "legit", old_content="hello\n")

    log_path = tmp_path / "change_log.json"
    data = json.loads(log_path.read_text())
    data[0]["old_content"] = "!!!not base64!!!"
    log_path.write_text(json.dumps(data, indent=2))

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    with pytest.raises(scanner_mod.IntegrityStoreError):
        store2.list_records()


def test_no_key_operates_without_signing(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("x.py", "new", "created x", new_content="hello\n")

    # No sidecar signature is written when there is no key.
    assert not (tmp_path / "change_log.mac").exists()

    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    records = store2.list_records()
    assert len(records) == 1
    assert records[0].file_path == "x.py"


# --------------------------------------------------------------------------
# prune / mark_committed filtering
# --------------------------------------------------------------------------

def test_mark_committed_excludes_from_unpruned(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    r0 = store.record("a.py", "new", "add a")
    store.record("b.py", "new", "add b")

    assert store.mark_committed(r0.id) is True

    unpruned = store.list_records(unpruned_only=True)
    assert [r.file_path for r in unpruned] == ["b.py"]
    # Full list still contains everything.
    assert len(store.list_records()) == 2


def test_prune_excludes_from_unpruned(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("a.py", "new", "add a")
    r1 = store.record("b.py", "new", "add b")

    assert store.prune(r1.id) is True

    # Flag persists to a fresh store instance.
    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    unpruned = store2.list_records(unpruned_only=True)
    assert [r.file_path for r in unpruned] == ["a.py"]


def test_mark_committed_unknown_id_returns_false(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("a.py", "new", "add a")
    assert store.mark_committed(999) is False


# --------------------------------------------------------------------------
# concurrency: concurrent record() calls do not lose entries
# --------------------------------------------------------------------------

def test_concurrent_records_do_not_lose_entries(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))

    n = 24
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait()
            store.record(f"file_{i}.py", "new", f"reason {i}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    records = store.list_records()
    assert len(records) == n
    # ids are unique and contiguous.
    assert sorted(r.id for r in records) == list(range(n))
    assert len({r.file_path for r in records}) == n


def test_cross_instance_records_serialize_via_file_lock(tmp_path, monkeypatch):
    # Two SEPARATE ChangeRecordStore instances over the same dir hold two
    # different threading.Locks, so only the OS-level file lock (flock) can
    # serialize their interleaved load-modify-save.  Assert no lost appends and
    # no duplicate ids — the multi-process race the file lock defends.
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store_a = ChangeRecordStore(store_dir=str(tmp_path))
    store_b = ChangeRecordStore(store_dir=str(tmp_path))

    per_store = 20
    total = per_store * 2
    barrier = threading.Barrier(total)
    errors: list[BaseException] = []

    def worker(store: ChangeRecordStore, tag: str, i: int) -> None:
        try:
            barrier.wait()
            store.record(f"{tag}_{i}.py", "new", f"reason {tag} {i}")
        except BaseException as exc:
            errors.append(exc)

    threads = []
    for i in range(per_store):
        threads.append(threading.Thread(target=worker, args=(store_a, "a", i)))
        threads.append(threading.Thread(target=worker, args=(store_b, "b", i)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    records = ChangeRecordStore(store_dir=str(tmp_path)).list_records()
    # No lost appends.
    assert len(records) == total
    # No duplicate ids (contiguous 0..total-1) despite two competing instances.
    assert sorted(r.id for r in records) == list(range(total))
    assert len({r.file_path for r in records}) == total


# --------------------------------------------------------------------------
# binary-safe old_content snapshot (lossless round-trip)
# --------------------------------------------------------------------------

def test_binary_old_content_round_trips_losslessly(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    blob = b"\xff\xfe\x00\x01\x80\x7fPNG\x89binary\x00data"
    store = ChangeRecordStore(store_dir=str(tmp_path))
    rec = store.record("image.bin", "modified", "replaced asset", old_content=blob)

    # old_hash is the sha256 of the ORIGINAL raw bytes.
    assert rec.old_hash == hashlib.sha256(blob).hexdigest()

    # A fresh instance recovers the EXACT original bytes (not U+FFFD-corrupted),
    # and the recovered snapshot hashes back to old_hash.
    store2 = ChangeRecordStore(store_dir=str(tmp_path))
    got = store2.list_records()[0]
    assert isinstance(got.old_content, bytes)
    assert got.old_content == blob
    assert hashlib.sha256(got.old_content).hexdigest() == got.old_hash


def test_text_old_content_still_round_trips_as_str(tmp_path, monkeypatch):
    # Regression guard: a str snapshot must come back as str (not bytes) so the
    # binary-safety change does not alter the text path.
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    text = "héllo wörld\n"  # non-ASCII but valid UTF-8
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record("notes.txt", "modified", "edit", old_content=text)

    got = ChangeRecordStore(store_dir=str(tmp_path)).list_records()[0]
    assert isinstance(got.old_content, str)
    assert got.old_content == text
