"""File integrity monitoring — hash recording, change detection, OpenBao signing.

Change detection: watchdog Observer drives event collection (W4.3).
The `FileWatcher` class uses watchdog's `Observer` for real-time event-based
change detection. The `FileIntegrityScanner.scan()` method keeps its existing
os.walk baseline-scan API; `FileWatcher` is the incremental change path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_INTEGRITY_KEY: str | None = None


class IntegrityKeyError(RuntimeError):
    """Raised when GL_INTEGRITY_KEY is absent and signing is attempted.

    Fail-closed: a random ephemeral key silently makes cross-process
    verification impossible (approve on process A, verify on process B → always
    fails).  We surface the misconfiguration early rather than letting bad
    signatures through.

    Resolution: set ``GL_INTEGRITY_KEY`` to a stable secret before starting the
    daemon.  The signing endpoints (/admin/integrity/approve) return HTTP 503
    until the key is provisioned.
    """


class IntegrityStoreError(RuntimeError):
    """Raised when the baseline hash store fails integrity verification.

    Fail-closed: the on-disk hash store (``integrity_db.json``) is protected by
    a sidecar HMAC-SHA256 signature (``integrity_db.mac``) keyed with the same
    ``GL_INTEGRITY_KEY`` that signs ChangeRecord approvals.  If a key is
    configured and the store is unparseable, or its signature is missing or
    does not match, that means the store was truncated, corrupted, or tampered
    with — e.g. an attacker who edited a monitored file also rewrote its stored
    hash to hide the change.  We RAISE rather than silently returning an empty
    baseline, because an empty baseline would rebaseline the tampered files and
    permanently mask the intrusion (every file would look "new", then trusted).
    """


def _get_integrity_key() -> str:
    global _INTEGRITY_KEY
    if _INTEGRITY_KEY is not None:
        return _INTEGRITY_KEY
    key = os.environ.get("GL_INTEGRITY_KEY")
    if key:
        _INTEGRITY_KEY = key
        return key
    raise IntegrityKeyError(
        "GL_INTEGRITY_KEY is not set. "
        "Set this environment variable to a stable secret before starting the "
        "integrity service. Refusing to sign with an ephemeral key because "
        "cross-process verification would always fail (fail-open)."
    )


@dataclass
class ChangeRecord:
    file_path: str
    change_type: str
    old_hash: str | None = None
    new_hash: str | None = None
    detected_at: str = ""
    approved: bool = False
    reason: str = ""
    signer: str = ""
    signature: str | None = None


class FileIntegrityScanner:
    def __init__(self, store_dir: str = "") -> None:
        if store_dir:
            self._store = Path(store_dir) / "integrity_db.json"
        else:
            home = os.path.expanduser("~")
            base = Path(home) / ".local" / "share" / "general-ludd" / "integrity"
            base.mkdir(parents=True, exist_ok=True)
            self._store = base / "integrity_db.json"
        # Sidecar HMAC signature over the serialized store bytes.  Kept in a
        # separate file so the store itself stays a plain ``{path: hash}`` JSON
        # object (backward compatible) while the MAC provides tamper detection.
        self._mac_path = self._store.with_suffix(".mac")

    def _hash_file(self, path: str) -> str:
        try:
            data = Path(path).read_bytes()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _store_mac(serialized: str, key: str) -> str:
        """HMAC-SHA256 over the exact serialized store bytes."""
        return hmac.new(key.encode(), serialized.encode(), hashlib.sha256).hexdigest()

    def _parse_store(self, raw: str) -> dict[str, str]:
        """Parse the store JSON, failing CLOSED on corruption.

        A store this class wrote is always a valid JSON object, so an
        unparseable or wrong-shaped store means truncation/corruption/tampering.
        We raise instead of the old ``except Exception: return {}`` which
        silently REBASELINED a tampered store.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IntegrityStoreError(
                f"integrity hash store is unparseable ({self._store}); refusing "
                "to silently rebaseline a corrupt or truncated store"
            ) from exc
        if not isinstance(data, dict):
            raise IntegrityStoreError(
                f"integrity hash store has an unexpected shape ({self._store}); "
                "expected a JSON object of path->hash"
            )
        return {str(k): str(v) for k, v in data.items()}

    def _load_hashes(self) -> dict[str, str]:
        # (a) Store MISSING → legitimate empty baseline (first run).
        if not self._store.exists():
            return {}

        raw = self._store.read_text()
        # (c-i) UNPARSEABLE store → fail closed regardless of key: a store we
        # wrote is always valid JSON, so this is corruption/tampering.
        hashes = self._parse_store(raw)

        try:
            key = _get_integrity_key()
        except IntegrityKeyError:
            # No key configured → we cannot verify authenticity.  Mirror the
            # ChangeRecord verify path (which returns "cannot verify" rather
            # than raising) so normal operation is not crashed for an operator
            # who never provisioned a key.  There is no integrity guarantee in
            # this mode — that is exactly why GL_INTEGRITY_KEY must be set.
            return hashes

        # Key configured → verification is MANDATORY.
        expected = self._store_mac(raw, key)
        stored_mac = ""
        if self._mac_path.exists():
            stored_mac = self._mac_path.read_text().strip()
        # (c-ii) BAD or ABSENT MAC → fail closed.  An absent MAC with a key
        # configured is a downgrade attack (attacker deletes the signature and
        # rewrites the store); a bad MAC means the store or signature was
        # edited out of band.  Constant-time compare (see verify_signature).
        if not stored_mac or not hmac.compare_digest(stored_mac, expected):
            raise IntegrityStoreError(
                "integrity hash store failed HMAC verification "
                f"(missing or tampered signature: {self._mac_path}). Refusing "
                "to rebaseline — the store or its signature was modified out of "
                "band, or GL_INTEGRITY_KEY changed."
            )
        # (b) Present + VALID MAC → trusted baseline.
        return hashes

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(hashes, indent=2)
        self._store.write_text(serialized)
        try:
            key = _get_integrity_key()
        except IntegrityKeyError:
            # No key: cannot sign.  Do not mint an ephemeral key (that would
            # make cross-process verification always fail).  Drop any stale
            # sidecar so a later key-configured load fails closed instead of
            # trusting an outdated signature.
            self._mac_path.unlink(missing_ok=True)
            return
        self._mac_path.write_text(self._store_mac(serialized, key))

    def _is_vc_controlled(self, path: str) -> bool:
        current = Path(path).resolve()
        while current != current.parent:
            if (current / ".git").is_dir() or (current / ".svn").is_dir():
                return True
            current = current.parent
        return False

    def scan(
        self,
        watch_paths: list[str],
        exclude_patterns: list[str] | None = None,
        skip_vc_controlled: bool = False,
    ) -> dict[str, Any]:
        """Scan *watch_paths* and return a change-detection report.

        Args:
            watch_paths: Directories to walk and hash.
            exclude_patterns: Regex strings; any file whose path matches is
                skipped.  The ``.git/`` / ``.svn/`` metadata directories are
                always excluded via these patterns — callers should include
                ``r'[\\/]\\.git[\\/]'`` (or the default patterns) to avoid
                hashing VCS internals.
            skip_vc_controlled: When *True*, also skip every file that lives
                anywhere inside a version-controlled working tree (i.e. any
                ancestor directory contains a ``.git`` or ``.svn`` entry).
                **Defaults to False** so that tracked source files are hashed
                by default — the monitor would otherwise be blind to tampering
                of all files in a git repository.
        """
        exclude = [re.compile(p) for p in (exclude_patterns or [])]
        old_hashes = self._load_hashes()
        new_hashes: dict[str, str] = {}
        files: list[str] = []
        changes: list[dict[str, Any]] = []

        for wp in watch_paths:
            root = Path(wp).expanduser().resolve()
            if not root.exists():
                continue
            for dirpath, _dirnames, filenames in os.walk(str(root)):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    if any(e.search(fp) for e in exclude):
                        continue
                    if skip_vc_controlled and self._is_vc_controlled(fp):
                        continue
                    new_hash = self._hash_file(fp)
                    if new_hash:
                        new_hashes[fp] = new_hash
                        files.append(fp)

        scanned = set(files)
        previously = set(old_hashes.keys())

        for fp in scanned - previously:
            changes.append({
                "type": "new",
                "file": fp,
                "new_hash": new_hashes.get(fp, ""),
                "old_hash": None,
                "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "approved": False,
            })
        for fp in scanned & previously:
            if old_hashes[fp] != new_hashes.get(fp, ""):
                changes.append({
                    "type": "modified",
                    "file": fp,
                    "old_hash": old_hashes[fp],
                    "new_hash": new_hashes.get(fp, ""),
                    "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "approved": False,
                })
        for fp in previously - scanned:
            changes.append({
                "type": "removed",
                "file": fp,
                "old_hash": old_hashes[fp],
                "new_hash": None,
                "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "approved": False,
            })

        self._save_hashes(new_hashes)
        return {"scanned": len(files), "files": files, "changes": changes}


class _IntegrityEventHandler(FileSystemEventHandler):
    """Watchdog event handler that collects filesystem change events."""

    def __init__(self, changes: list[dict[str, Any]], lock: threading.Lock) -> None:
        super().__init__()
        self._changes = changes
        self._lock = lock

    def _record(self, event_type: str, src: str, dest: str | None = None) -> None:
        entry: dict[str, Any] = {
            "type": event_type,
            "file": src,
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if dest:
            entry["dest"] = dest
        with self._lock:
            self._changes.append(entry)

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._record("new", str(event.src_path))

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._record("modified", str(event.src_path))

    def on_deleted(self, event: FileDeletedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._record("removed", str(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._record("moved", str(event.src_path), str(event.dest_path))


class FileWatcher:
    """Event-based file change detector using watchdog Observer (replaces os.walk polling).

    Usage::

        watcher = FileWatcher()
        watcher.start(["/path/to/watch"])
        # ... time passes ...
        changes = watcher.get_changes()  # returns and clears the event buffer
        watcher.stop()

    ``get_changes()`` consumes and clears the buffer, so each call returns only
    events since the previous call.
    """

    def __init__(self) -> None:
        self._observer: Any = None  # watchdog.observers.Observer, typed as Any (no stubs)
        self._changes: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def start(self, watch_paths: list[str]) -> None:
        """Start watching the given paths recursively."""
        self._observer = Observer()
        handler = _IntegrityEventHandler(self._changes, self._lock)
        for path in watch_paths:
            p = Path(path).expanduser().resolve()
            if p.exists():
                self._observer.schedule(handler, str(p), recursive=True)
        self._observer.start()

    def get_changes(self) -> list[dict[str, Any]]:
        """Return all collected change events and clear the internal buffer."""
        with self._lock:
            result = list(self._changes)
            self._changes.clear()
        return result

    def stop(self) -> None:
        """Stop the watchdog observer cleanly."""
        if self._observer is not None and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=2.0)
        self._observer = None


def sign_change(change: ChangeRecord, reason: str, signer: str) -> dict[str, Any]:
    parts = [change.file_path, change.change_type, str(change.old_hash), str(change.new_hash), change.detected_at]
    payload = "|".join(parts)
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result = asdict(change)
    result["approved"] = True
    result["reason"] = reason
    result["signer"] = signer
    result["signature"] = sig
    return result


def verify_signature(signed: dict[str, Any]) -> bool:
    parts = [
        signed.get("file_path", ""),
        signed.get("change_type", ""),
        signed.get("old_hash", ""),
        signed.get("new_hash", ""),
        signed.get("detected_at", ""),
    ]
    payload = "|".join(str(p) for p in parts)
    # Mirror verify_openbao_signature: a missing key means we cannot verify, so
    # return False (tamper-equivalent) rather than raising. GL_INTEGRITY_KEY must
    # be provisioned for a meaningful verify.
    try:
        key = _get_integrity_key()
    except IntegrityKeyError:
        return False
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    # Constant-time compare: a plain ``==`` on the HMAC hex digest leaks, via
    # early-exit timing, how many leading characters of a forged signature are
    # correct — enough to forge byte-by-byte. ``signed.get("signature")`` may be
    # None/non-str, so coerce to "" (a mismatch) before comparing; both operands
    # must be the same type for hmac.compare_digest.
    return hmac.compare_digest(str(signed.get("signature") or ""), expected)


def sign_change_openbao(
    path: str,
    signer: str,
    reason: str,
    old_hash: str | None = None,
    new_hash: str | None = None,
    secrets_resolver: Any | None = None,
) -> dict[str, Any]:
    """Sign an integrity approval; hashes are included in the HMAC payload.

    ``old_hash`` and ``new_hash`` bind the signature to the specific scanned
    change so that the approval cannot be replayed against a different version
    of the file.  Both must match the values recorded during the scan; the
    router is responsible for enforcing that match before calling this function.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = "|".join([path, signer, reason, str(old_hash), str(new_hash), ts])
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result: dict[str, Any] = {
        "path": path,
        "signer": signer,
        "reason": reason,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "timestamp": ts,
        "signature": sig,
        "backend": "openbao" if secrets_resolver else "local-hmac",
    }
    if secrets_resolver and hasattr(secrets_resolver, "write_secret"):
        try:
            secrets_resolver.write_secret(
                f"integrity/{path.replace('/', '_')}",
                {"signature": sig, "reason": reason, "old_hash": old_hash, "new_hash": new_hash},
            )
            result["backend"] = "openbao"
        except Exception:
            result["backend"] = "openbao-unavailable-fallback-hmac"
    return result


def verify_openbao_signature(signed: dict[str, Any]) -> bool:
    """Verify a signature produced by :func:`sign_change_openbao`.

    Returns ``False`` (not an exception) on any mismatch so callers can
    distinguish tamper-detected from signing errors.
    """
    parts = [
        signed.get("path", ""),
        signed.get("signer", ""),
        signed.get("reason", ""),
        str(signed.get("old_hash")),
        str(signed.get("new_hash")),
        signed.get("timestamp", ""),
    ]
    payload = "|".join(parts)
    try:
        key = _get_integrity_key()
    except IntegrityKeyError:
        return False
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    # Constant-time compare: a plain ``==`` on the HMAC hex digest leaks, via
    # early-exit timing, how many leading characters of a forged signature are
    # correct — enough to forge byte-by-byte. ``signed.get("signature")`` may be
    # None/non-str, so coerce to "" (a mismatch) before comparing; both operands
    # must be the same type for hmac.compare_digest.
    return hmac.compare_digest(str(signed.get("signature") or ""), expected)
