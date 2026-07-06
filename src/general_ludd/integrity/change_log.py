"""Durable, signed, per-file change-record log (FIM change-export core).

This module persists an APPEND-only log of :class:`ChangeRecord` entries so a
later slice can diff/export the changes an agent or operator made — without
needing the original source file, because each entry can capture an
``old_content`` snapshot.

Design mirrors :mod:`general_ludd.integrity.scanner`:

* The on-disk log (``change_log.json``) is a plain JSON array, protected by a
  sidecar HMAC-SHA256 signature (``change_log.mac``) keyed with the same
  ``GL_INTEGRITY_KEY`` that signs ChangeRecord approvals and the baseline hash
  store.  We reuse :func:`scanner._get_integrity_key`, the ``_store_mac``
  HMAC pattern, and the fail-closed load semantics.
* If a key IS configured, load verification is MANDATORY: an unparseable log, a
  missing signature, or a bad signature RAISES :class:`IntegrityStoreError`
  (fail closed — refuse to trust a tampered change log).
* If NO key is configured, the log operates without signing (mirroring the
  ChangeRecord "cannot verify" tolerance) so an operator who never provisioned
  a key is not crashed — there is simply no integrity guarantee in that mode.

The read-modify-write of the log is guarded by a :class:`threading.Lock` so
concurrent :meth:`ChangeRecordStore.record` calls never lose entries.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from general_ludd.integrity.scanner import (
    ChangeRecord,
    FileIntegrityScanner,
    IntegrityKeyError,
    IntegrityStoreError,
    _get_integrity_key,
)

# Platform-specific: fcntl is POSIX-only (Linux/macOS).
# contextlib.suppress handles the optional-stdlib-module idiom.
fcntl: Any = None
with contextlib.suppress(ImportError):
    fcntl = importlib.import_module("fcntl")

# Reuse the scanner's HMAC-SHA256-over-serialized-bytes helper verbatim so the
# change log is signed with the exact same construction as the baseline store.
_store_mac = FileIntegrityScanner._store_mac

__all__ = ("ChangeLogEntry", "ChangeRecordStore")


@dataclass
class ChangeLogEntry(ChangeRecord):
    """A :class:`ChangeRecord` augmented with change-log bookkeeping.

    Reuses every field of the base :class:`ChangeRecord`
    (``file_path``/``change_type``/``old_hash``/``new_hash``/``detected_at``/
    ``approved``/``reason``/``signer``/``signature``) and adds:

    * ``id`` — a stable, monotonically increasing integer identifier.
    * ``old_content`` — an OPTIONAL captured snapshot of the file's previous
      content, so a diff can be produced later without the source file.  Stored
      LOSSLESSLY: the RAW bytes are base64-encoded on disk (a ``str`` snapshot
      is UTF-8 encoded first), and decoded back to the EXACT original on load —
      a ``str`` round-trips to ``str``, ``bytes`` round-trips to ``bytes``.  No
      lossy UTF-8 decode happens at store time (that is deferred to
      diff-render time), so a binary/non-UTF-8 snapshot is preserved byte-for-
      byte and still hashes to ``old_hash``.
    * ``new_content`` — an OPTIONAL captured snapshot of the file's NEW content,
      stored with the exact same lossless base64 construction as
      ``old_content`` (round-trips ``str``→``str``, ``bytes``→``bytes``, hashes
      to ``new_hash``).  Capturing both sides lets a later slice render a full
      unified diff of an agentic change WITHOUT needing the source file for
      either side.
    * ``committed`` — set when the record has been upstreamed/committed so
      :meth:`ChangeRecordStore.list_records` can filter it out (this supports
      the later overlay-prune step).
    """

    id: int = -1
    old_content: str | bytes | None = None
    new_content: str | bytes | None = None
    committed: bool = False


def _hash_content(content: str | bytes | None) -> str | None:
    """SHA-256 hex digest over content bytes (mirrors scanner._hash_file)."""
    if content is None:
        return None
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


class ChangeRecordStore:
    """Append-only, HMAC-signed log of :class:`ChangeLogEntry` records."""

    def __init__(self, store_dir: str = "") -> None:
        if store_dir:
            base = Path(store_dir)
            base.mkdir(parents=True, exist_ok=True)
            self._store = base / "change_log.json"
        else:
            home = os.path.expanduser("~")
            base = Path(home) / ".local" / "share" / "general-ludd" / "integrity"
            base.mkdir(parents=True, exist_ok=True)
            self._store = base / "change_log.json"
        # Sidecar HMAC signature over the serialized log bytes — kept separate
        # so the log itself stays a plain JSON array.
        self._mac_path = self._store.with_suffix(".mac")
        # In-process mutual exclusion for one ChangeRecordStore instance.
        self._lock = threading.Lock()
        # CROSS-process mutual exclusion: the CLI and the daemon are separate
        # processes that both append to the same log, so a threading.Lock alone
        # would let two whole-file writes clobber each other (lost append +
        # duplicate id).  An OS advisory lock on this sidecar serializes the
        # load-modify-save critical section across processes (and across
        # separate instances in one process, since each acquisition opens a
        # fresh file description).
        self._lock_path = self._store.with_suffix(".lock")

    # -- locking ---------------------------------------------------------

    @contextlib.contextmanager
    def _interprocess_lock(self) -> Iterator[None]:
        """Hold an exclusive OS advisory lock over the critical section.

        Degrades gracefully to a no-op where ``fcntl`` is unavailable (e.g.
        Windows) — it never crashes, and the in-process :attr:`_lock` still
        provides the single-process guarantee.  A fresh ``open`` per call gives
        each acquirer its own open file description, so ``flock`` is mutually
        exclusive even between two instances inside one process.
        """
        if fcntl is None:  # pragma: no cover - non-POSIX platform
            yield
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # -- serialization ---------------------------------------------------

    @staticmethod
    def _entry_to_dict(entry: ChangeLogEntry) -> dict[str, Any]:
        data = asdict(entry)
        # Base64-encode the captured snapshot LOSSLESSLY: encode the RAW bytes
        # (a str snapshot is UTF-8 encoded first) with NO lossy decode, and
        # record whether the original was bytes so it can be recovered exactly.
        # The JSON field ``old_content`` therefore holds a base64 string on disk
        # and ``old_content_binary`` remembers the original Python type.
        oc = entry.old_content
        if oc is None:
            data["old_content"] = None
            data["old_content_binary"] = False
        else:
            raw = oc if isinstance(oc, bytes) else oc.encode("utf-8")
            data["old_content"] = base64.b64encode(raw).decode("ascii")
            data["old_content_binary"] = isinstance(oc, bytes)
        # Mirror the lossless snapshot construction for the NEW content so a
        # later slice can diff both sides without the source file.
        nc = entry.new_content
        if nc is None:
            data["new_content"] = None
            data["new_content_binary"] = False
        else:
            raw_new = nc if isinstance(nc, bytes) else nc.encode("utf-8")
            data["new_content"] = base64.b64encode(raw_new).decode("ascii")
            data["new_content_binary"] = isinstance(nc, bytes)
        return data

    @staticmethod
    def _entry_from_dict(data: dict[str, Any]) -> ChangeLogEntry:
        oc = data.get("old_content")
        old_content: str | bytes | None = None
        if oc is not None:
            raw = base64.b64decode(str(oc))
            # Recover the EXACT original: raw bytes for a binary snapshot, or a
            # lossless UTF-8 decode for a text snapshot (which we UTF-8 encoded,
            # so the decode cannot fail or lose data).
            old_content = (
                raw
                if bool(data.get("old_content_binary", False))
                else raw.decode("utf-8")
            )
        nc = data.get("new_content")
        new_content: str | bytes | None = None
        if nc is not None:
            raw_new = base64.b64decode(str(nc))
            new_content = (
                raw_new
                if bool(data.get("new_content_binary", False))
                else raw_new.decode("utf-8")
            )
        return ChangeLogEntry(
            file_path=str(data.get("file_path", "")),
            change_type=str(data.get("change_type", "")),
            old_hash=data.get("old_hash"),
            new_hash=data.get("new_hash"),
            detected_at=str(data.get("detected_at", "")),
            approved=bool(data.get("approved", False)),
            reason=str(data.get("reason", "")),
            signer=str(data.get("signer", "")),
            signature=data.get("signature"),
            id=int(data.get("id", -1)),
            old_content=old_content,
            new_content=new_content,
            committed=bool(data.get("committed", False)),
        )

    def _parse_log(self, raw: str) -> list[ChangeLogEntry]:
        """Parse the log JSON, failing CLOSED on corruption.

        A log this class wrote is always a JSON array, so an unparseable or
        wrong-shaped log means truncation/corruption/tampering — we raise
        instead of silently returning an empty log (which would hide the loss).
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IntegrityStoreError(
                f"change log is unparseable ({self._store}); refusing to "
                "silently drop a corrupt or truncated log"
            ) from exc
        if not isinstance(data, list):
            raise IntegrityStoreError(
                f"change log has an unexpected shape ({self._store}); expected "
                "a JSON array of change records"
            )
        # A record this class wrote always decodes cleanly (valid base64
        # ``old_content``, integer ``id``); a decode failure therefore means
        # corruption/tampering → fail closed with a typed error rather than
        # leaking a raw binascii.Error/ValueError to the caller.
        try:
            return [self._entry_from_dict(dict(d)) for d in data]
        except (ValueError, TypeError, binascii.Error) as exc:
            raise IntegrityStoreError(
                f"change log contains a malformed record ({self._store}); "
                "refusing to trust a corrupt or tampered log"
            ) from exc

    def _load(self) -> list[ChangeLogEntry]:
        # (a) Log MISSING → legitimate empty log (first run).
        if not self._store.exists():
            return []

        raw = self._store.read_text()

        try:
            key = _get_integrity_key()
        except IntegrityKeyError:
            # No key → cannot verify authenticity.  Mirror the ChangeRecord
            # verify path (return rather than raise) so an operator who never
            # provisioned a key is not crashed.  No integrity guarantee here.
            # (c-i) An UNPARSEABLE log still fails closed via _parse_log.
            return self._parse_log(raw)

        # Key configured → verification is MANDATORY, and it happens on the raw
        # bytes BEFORE decoding any record, so tamper is reported as a clean
        # HMAC failure rather than a downstream decode error.
        expected = _store_mac(raw, key)
        stored_mac = ""
        if self._mac_path.exists():
            stored_mac = self._mac_path.read_text().strip()
        # (c-ii) BAD or ABSENT MAC → fail closed.  An absent MAC with a key
        # configured is a downgrade attack; a bad MAC means the log or its
        # signature was edited out of band.  Constant-time compare.
        if not stored_mac or not hmac.compare_digest(stored_mac, expected):
            raise IntegrityStoreError(
                "change log failed HMAC verification (missing or tampered "
                f"signature: {self._mac_path}). Refusing to trust the log — it "
                "or its signature was modified out of band, or GL_INTEGRITY_KEY "
                "changed."
            )
        # (b) Present + VALID MAC → trusted log; now safe to decode records.
        return self._parse_log(raw)

    def _save(self, entries: list[ChangeLogEntry]) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(
            [self._entry_to_dict(e) for e in entries], indent=2
        )
        self._store.write_text(serialized)
        try:
            key = _get_integrity_key()
        except IntegrityKeyError:
            # No key: cannot sign.  Do NOT mint an ephemeral key.  Drop any
            # stale sidecar so a later key-configured load fails closed instead
            # of trusting an outdated signature.
            self._mac_path.unlink(missing_ok=True)
            return
        self._mac_path.write_text(_store_mac(serialized, key))

    # -- public API ------------------------------------------------------

    def record(
        self,
        file_path: str,
        change_type: str,
        reason: str,
        *,
        old_content: str | bytes | None = None,
        new_content: str | bytes | None = None,
        signer: str = "",
    ) -> ChangeLogEntry:
        """Append a change record to the log and return it (with its ``id``).

        ``old_hash``/``new_hash`` are computed from ``old_content``/
        ``new_content`` when provided.  BOTH ``old_content`` and ``new_content``
        are captured LOSSLESSLY (raw bytes, base64 on disk) so a full unified
        diff — textual or binary — can be produced later without the source
        file; each recovered snapshot always hashes back to its
        ``old_hash``/``new_hash``.  ``detected_at`` is stamped with the current
        time.
        """
        # Capture the snapshot RAW (no lossy decode) so binary/non-UTF-8
        # content survives byte-for-byte and hashes to old_hash.
        old_hash = _hash_content(old_content)
        new_hash = _hash_content(new_content)
        detected_at = time.strftime("%Y-%m-%dT%H:%M:%S")

        # threading.Lock (in-process) + flock (cross-process) together guard the
        # whole load→append→save so no concurrent writer can clobber an append
        # or reuse an id.
        with self._lock, self._interprocess_lock():
            entries = self._load()
            next_id = (max(e.id for e in entries) + 1) if entries else 0
            entry = ChangeLogEntry(
                file_path=file_path,
                change_type=change_type,
                old_hash=old_hash,
                new_hash=new_hash,
                detected_at=detected_at,
                reason=reason,
                signer=signer,
                id=next_id,
                old_content=old_content,
                new_content=new_content,
            )
            entries.append(entry)
            self._save(entries)
        return entry

    def list_records(self, *, unpruned_only: bool = False) -> list[ChangeLogEntry]:
        """Return the verified log; optionally excluding committed/pruned records.

        Takes the same cross-process lock as the writers so a reader never
        observes the body/MAC pair mid-write.
        """
        with self._lock, self._interprocess_lock():
            entries = self._load()
        if unpruned_only:
            return [e for e in entries if not e.committed]
        return entries

    def mark_committed(self, record_id: int) -> bool:
        """Mark a record as upstreamed/committed so ``list`` can filter it out.

        Returns ``True`` if a record with ``record_id`` was found and flagged.
        """
        with self._lock, self._interprocess_lock():
            entries = self._load()
            found = False
            for e in entries:
                if e.id == record_id:
                    e.committed = True
                    found = True
                    break
            if found:
                self._save(entries)
            return found

    def prune(self, record_id: int) -> bool:
        """Alias for :meth:`mark_committed` (marks a record upstreamed/pruned)."""
        return self.mark_committed(record_id)
