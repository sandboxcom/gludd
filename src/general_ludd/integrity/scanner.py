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
from typing import Any, Protocol

from watchdog.events import (
    FileSystemEvent,
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
        #
        # The sidecar has two on-disk shapes:
        #   * legacy  — a bare hex HMAC over the serialized store (no counter);
        #   * versioned — a JSON object ``{"mac": <hex>, "counter": <int>}`` whose
        #     HMAC covers ``counter|serialized`` so the signed baseline is bound
        #     to a MONOTONIC counter (anti-rollback, gap B).
        # A legacy sidecar is accepted once, then upgraded to the versioned shape
        # on the next save so existing deployments do not hard-break.
        self._mac_path = self._store.with_suffix(".mac")
        # High-water-mark sidecar: the highest counter ever signed, itself HMAC
        # protected.  Persisted SEPARATELY from the (store, .mac) pair so that
        # restoring an old, legitimately-signed pair (whose counter is below the
        # high-water-mark) is rejected as a rollback rather than re-validating.
        self._hwm_path = self._store.with_suffix(".hwm")

    def _hash_file(self, path: str) -> str:
        try:
            data = Path(path).read_bytes()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _store_mac(serialized: str, key: str) -> str:
        """HMAC-SHA256 over the exact serialized store bytes (legacy, counterless)."""
        return hmac.new(key.encode(), serialized.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _store_mac_versioned(serialized: str, counter: int, key: str) -> str:
        """HMAC-SHA256 binding the store bytes to a monotonic *counter*.

        The counter is signed alongside the store so an attacker cannot swap in
        an older validly-signed store without also forging the MAC (which they
        cannot without the key).  The counter is prefixed (``counter|serialized``)
        so the signed message is an unambiguous encoding of both.
        """
        msg = f"{counter}|{serialized}"
        return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _hwm_mac(counter: int, key: str) -> str:
        """HMAC over the high-water-mark value so it cannot be edited downward."""
        return hmac.new(key.encode(), f"hwm:{counter}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _parse_mac_sidecar(sidecar: str) -> tuple[int | None, str]:
        """Return ``(counter, mac)`` for a versioned sidecar, else ``(None, raw)``.

        A versioned sidecar is JSON ``{"mac": <hex>, "counter": <int>}``.  A
        legacy sidecar is a bare hex string; we return ``(None, <the raw hex>)``
        so the caller can verify it with the counterless MAC and upgrade it on
        the next save.
        """
        try:
            obj = json.loads(sidecar)
        except (json.JSONDecodeError, ValueError):
            return None, sidecar
        if isinstance(obj, dict) and "mac" in obj and "counter" in obj:
            try:
                return int(obj["counter"]), str(obj["mac"])
            except (TypeError, ValueError):
                # Present-but-malformed versioned sidecar → treat as tamper by
                # returning an impossible counter/mac that will fail the compare.
                return None, ""
        # Parsed JSON but not our shape → not a valid legacy hex either.
        return None, ""

    def _write_mac_and_hwm(self, serialized: str, counter: int, key: str) -> None:
        """Persist the versioned MAC sidecar and the signed high-water-mark."""
        mac = self._store_mac_versioned(serialized, counter, key)
        self._mac_path.write_text(json.dumps({"mac": mac, "counter": counter}))
        self._hwm_path.write_text(json.dumps({"hwm": counter, "mac": self._hwm_mac(counter, key)}))

    def _read_current_counter_best_effort(self, key: str) -> int:
        """Counter recorded in the on-disk MAC sidecar, or 0 if absent/legacy.

        Best-effort (never raises): used only to pick the next counter on save,
        where the authoritative rollback check is the high-water-mark.
        """
        if not self._mac_path.exists():
            return 0
        try:
            counter, _mac = self._parse_mac_sidecar(self._mac_path.read_text().strip())
        except Exception:
            return 0
        return counter if counter is not None else 0

    def _read_hwm_value(self, key: str) -> int | None:
        """Verified high-water-mark, or ``None`` if no HWM sidecar exists.

        Raises :class:`IntegrityStoreError` if the HWM sidecar is present but
        unparseable or fails its own HMAC — a tampered HWM is itself an attack.
        """
        if not self._hwm_path.exists():
            return None
        try:
            obj = json.loads(self._hwm_path.read_text())
            value = int(obj["hwm"])
            mac = str(obj["mac"])
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            raise IntegrityStoreError(
                f"integrity high-water-mark sidecar is unparseable ({self._hwm_path}); "
                "refusing to load — the rollback guard was tampered with"
            ) from exc
        if not hmac.compare_digest(mac, self._hwm_mac(value, key)):
            raise IntegrityStoreError(
                f"integrity high-water-mark signature is invalid ({self._hwm_path}); "
                "the anti-rollback counter was modified out of band"
            )
        return value

    def _read_hwm_value_best_effort(self, key: str) -> int:
        try:
            value = self._read_hwm_value(key)
        except IntegrityStoreError:
            return 0
        return value if value is not None else 0

    def _verify_mac_and_get_counter(self, raw: str, key: str) -> int:
        """Verify the MAC sidecar over *raw* and return its counter.

        Accepts both the legacy (counterless, counter reported as 0) and the
        versioned sidecar shapes.  Raises :class:`IntegrityStoreError` on a
        missing, malformed, or non-matching MAC (fail closed).
        """
        if not self._mac_path.exists():
            raise IntegrityStoreError(
                "integrity hash store failed HMAC verification "
                f"(missing signature: {self._mac_path}). Refusing to rebaseline — "
                "an absent signature with a key configured is a downgrade attack, "
                "or GL_INTEGRITY_KEY changed (use rebaseline() to recover)."
            )
        sidecar = self._mac_path.read_text().strip()
        counter, mac = self._parse_mac_sidecar(sidecar)
        if counter is None:
            # Legacy (counterless) sidecar: verify with the old MAC and treat the
            # counter as 0 so the next save upgrades it to the versioned shape.
            expected = self._store_mac(raw, key)
            if not mac or not hmac.compare_digest(mac, expected):
                raise IntegrityStoreError(
                    "integrity hash store failed HMAC verification "
                    f"(tampered signature: {self._mac_path}). Refusing to "
                    "rebaseline — the store or its signature was modified out of "
                    "band, or GL_INTEGRITY_KEY changed (use rebaseline() to recover)."
                )
            return 0
        expected = self._store_mac_versioned(raw, counter, key)
        if not hmac.compare_digest(mac, expected):
            raise IntegrityStoreError(
                "integrity hash store failed HMAC verification "
                f"(tampered signature: {self._mac_path}). Refusing to rebaseline — "
                "the store or its signature was modified out of band, or "
                "GL_INTEGRITY_KEY changed (use rebaseline() to recover)."
            )
        return counter

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
                f"integrity hash store has an unexpected shape ({self._store}); expected a JSON object of path->hash"
            )
        return {str(k): str(v) for k, v in data.items()}

    def _load_hashes(self) -> dict[str, str]:
        store_exists = self._store.exists()

        # (A) DELETED-STORE rebaseline defense.  A true first run has NEITHER the
        # store NOR a signature.  If the store is gone but a .mac sidecar (or the
        # high-water-mark) survives and a key is configured, that is a deletion/
        # downgrade attack — a signature without its store — NOT a first run, so
        # fail closed instead of silently returning an empty baseline.
        if not store_exists:
            if self._mac_path.exists() or self._hwm_path.exists():
                try:
                    _get_integrity_key()
                except IntegrityKeyError:
                    # No key → no integrity guarantee anyway; mirror the tolerant
                    # no-key mode rather than crash normal operation.
                    return {}
                raise IntegrityStoreError(
                    f"integrity hash store is missing ({self._store}) but its "
                    f"signature sidecar survives ({self._mac_path} / "
                    f"{self._hwm_path}). A signature without its store is a "
                    "deletion/rollback attack, not a first run — refusing to "
                    "rebaseline. Use rebaseline() to intentionally re-establish "
                    "a baseline."
                )
            # Neither file present → legitimate empty baseline (first run).
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

        # Key configured → verification is MANDATORY.  (c-ii) BAD or ABSENT MAC
        # → fail closed (raises inside the helper); returns the signed counter.
        counter = self._verify_mac_and_get_counter(raw, key)

        # (B) ANTI-ROLLBACK: reject a store whose signed counter is BELOW the
        # persisted high-water-mark.  Restoring an old, legitimately-signed
        # (store, .mac) pair re-validates its MAC, but its counter is stale, so
        # the separately-persisted high-water-mark catches the rollback.  We
        # accept counter >= hwm (not ==) so a crash between the MAC write and the
        # HWM advance is forward-safe: a higher counter still needs a valid MAC,
        # which an attacker cannot forge.
        hwm = self._read_hwm_value(key)
        if hwm is None:
            if counter >= 1:
                # A VERSIONED store (counter >= 1) with NO high-water-mark means
                # the rollback guard was deleted out of band — fail closed rather
                # than silently drop anti-rollback protection.  (A counterless
                # legacy store, counter == 0, is still accepted once and upgraded
                # on the next save.)  Recover intentionally via rebaseline().
                raise IntegrityStoreError(
                    f"integrity hash store is versioned (counter {counter}) but "
                    f"its high-water-mark sidecar is missing ({self._hwm_path}) — "
                    "the anti-rollback guard was deleted. Refusing to load. Use "
                    "rebaseline() to intentionally re-establish a baseline."
                )
        elif counter < hwm:
            raise IntegrityStoreError(
                f"integrity hash store counter ({counter}) is below the "
                f"high-water-mark ({hwm}) — the store was rolled back to an "
                "earlier signed state. Refusing to load. Use rebaseline() to "
                "intentionally re-establish a baseline."
            )
        # Present + VALID MAC + non-stale counter → trusted baseline.
        return hashes

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
        self._store.write_text(serialized)
        try:
            key = _get_integrity_key()
        except IntegrityKeyError:
            # No key: cannot sign.  Do not mint an ephemeral key (that would
            # make cross-process verification always fail).  Drop any stale
            # sidecars so a later key-configured load fails closed instead of
            # trusting an outdated signature or high-water-mark.
            self._mac_path.unlink(missing_ok=True)
            self._hwm_path.unlink(missing_ok=True)
            return
        # (B) Increment the monotonic counter above both the previous store
        # counter AND the high-water-mark, then persist the versioned MAC and a
        # fresh high-water-mark.  ``max(...)`` defends against a counter that was
        # reset downward out of band (the higher of the two still wins).
        prev = max(
            self._read_current_counter_best_effort(key),
            self._read_hwm_value_best_effort(key),
        )
        self._write_mac_and_hwm(serialized, prev + 1, key)

    def rebaseline(self) -> dict[str, str]:
        """Explicit operator recovery after a deliberate GL_INTEGRITY_KEY rotation.

        Provisioning or rotating ``GL_INTEGRITY_KEY`` against an existing store
        makes every subsequent load fail closed (the old MAC no longer verifies).
        That is intentional — a silent auto-rebaseline on a bad MAC would be a
        hole an attacker could drive a truck through.  Recovery is therefore an
        EXPLICIT, LOGGED operator action: this method re-signs the CURRENT
        on-disk store with the currently-configured key and bumps the monotonic
        counter above the high-water-mark, so a legitimate operator can recover
        while an attacker (who lacks the new key) still cannot.

        It is never invoked automatically by :meth:`_load_hashes`; the bad-MAC
        path stays fail-closed.  Requires a key (raises :class:`IntegrityKeyError`
        if unset) and a well-formed store on disk.

        Returns the re-signed hashes.
        """
        # Drop the memoized key so a key ROTATED in the environment after this
        # process first cached the old key is actually picked up — otherwise
        # rebaseline would re-sign with the stale key and never recover.
        global _INTEGRITY_KEY
        _INTEGRITY_KEY = None
        key = _get_integrity_key()  # rebaseline requires a key; fail closed if unset
        if not self._store.exists():
            raise IntegrityStoreError(f"cannot rebaseline: no integrity hash store on disk ({self._store})")
        raw = self._store.read_text()
        hashes = self._parse_store(raw)  # still refuse to re-sign a corrupt store
        prev = max(
            self._read_current_counter_best_effort(key),
            self._read_hwm_value_best_effort(key),
        )
        counter = prev + 1
        # Sign over the EXACT on-disk bytes (raw) so the MAC matches what
        # _load_hashes reads back; do not re-serialize.
        self._write_mac_and_hwm(raw, counter, key)
        logger.warning(
            "integrity hash store REBASELINED under the current GL_INTEGRITY_KEY "
            "(explicit operator action): store=%s counter=%d entries=%d",
            self._store,
            counter,
            len(hashes),
        )
        return hashes

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
    ) -> dict[str, object]:
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
        changes: list[dict[str, object]] = []

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
            changes.append(
                {
                    "type": "new",
                    "file": fp,
                    "new_hash": new_hashes.get(fp, ""),
                    "old_hash": None,
                    "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "approved": False,
                }
            )
        for fp in scanned & previously:
            if old_hashes[fp] != new_hashes.get(fp, ""):
                changes.append(
                    {
                        "type": "modified",
                        "file": fp,
                        "old_hash": old_hashes[fp],
                        "new_hash": new_hashes.get(fp, ""),
                        "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "approved": False,
                    }
                )
        for fp in previously - scanned:
            changes.append(
                {
                    "type": "removed",
                    "file": fp,
                    "old_hash": old_hashes[fp],
                    "new_hash": None,
                    "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "approved": False,
                }
            )

        self._save_hashes(new_hashes)
        return {"scanned": len(files), "files": files, "changes": changes}


class _IntegrityEventHandler(FileSystemEventHandler):
    """Watchdog event handler that collects filesystem change events."""

    def __init__(self, changes: list[dict[str, object]], lock: threading.Lock) -> None:
        super().__init__()
        self._changes = changes
        self._lock = lock

    def _record(self, event_type: str, src: str, dest: str | None = None) -> None:
        entry: dict[str, object] = {
            "type": event_type,
            "file": src,
            "detected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if dest:
            entry["dest"] = dest
        with self._lock:
            self._changes.append(entry)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record("new", str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record("modified", str(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record("removed", str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
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
        self._changes: list[dict[str, object]] = []
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

    def get_changes(self) -> list[dict[str, object]]:
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


def sign_change(change: ChangeRecord, reason: str, signer: str) -> dict[str, object]:
    payload = json.dumps(
        {
            "scheme": "gl-integrity-v1",
            "file_path": change.file_path,
            "change_type": change.change_type,
            "old_hash": str(change.old_hash),
            "new_hash": str(change.new_hash),
            "detected_at": change.detected_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result = asdict(change)
    result["approved"] = True
    result["reason"] = reason
    result["signer"] = signer
    result["signature"] = sig
    return result


def verify_signature(signed: dict[str, object]) -> bool:
    payload = json.dumps(
        {
            "scheme": "gl-integrity-v1",
            "file_path": str(signed.get("file_path", "")),
            "change_type": str(signed.get("change_type", "")),
            "old_hash": str(signed.get("old_hash", "")),
            "new_hash": str(signed.get("new_hash", "")),
            "detected_at": str(signed.get("detected_at", "")),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
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


class SecretsWriter(Protocol):
    """Structural type for a secrets resolver that can persist a signed secret."""

    def write_secret(self, path: str, value: dict[str, object]) -> None: ...


def sign_change_openbao(
    path: str,
    signer: str,
    reason: str,
    old_hash: str | None = None,
    new_hash: str | None = None,
    secrets_resolver: SecretsWriter | None = None,
) -> dict[str, object]:
    """Sign an integrity approval; hashes are included in the HMAC payload.

    ``old_hash`` and ``new_hash`` bind the signature to the specific scanned
    change so that the approval cannot be replayed against a different version
    of the file.  Both must match the values recorded during the scan; the
    router is responsible for enforcing that match before calling this function.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = json.dumps(
        {
            "scheme": "gl-integrity-openbao-v1",
            "path": path,
            "signer": signer,
            "reason": reason,
            "old_hash": str(old_hash),
            "new_hash": str(new_hash),
            "timestamp": ts,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    key = _get_integrity_key()
    sig = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    result: dict[str, object] = {
        "path": path,
        "signer": signer,
        "reason": reason,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "timestamp": ts,
        "signature": sig,
        "backend": "openbao" if secrets_resolver else "local-hmac",
    }
    if secrets_resolver is not None and hasattr(secrets_resolver, "write_secret"):
        try:
            secrets_resolver.write_secret(
                f"integrity/{path.replace('/', '_')}",
                {"signature": sig, "reason": reason, "old_hash": old_hash, "new_hash": new_hash},
            )
            result["backend"] = "openbao"
        except Exception:
            result["backend"] = "openbao-unavailable-fallback-hmac"
    return result


def verify_openbao_signature(signed: dict[str, object]) -> bool:
    """Verify a signature produced by :func:`sign_change_openbao`.

    Returns ``False`` (not an exception) on any mismatch so callers can
    distinguish tamper-detected from signing errors.
    """
    payload = json.dumps(
        {
            "scheme": "gl-integrity-openbao-v1",
            "path": str(signed.get("path", "")),
            "signer": str(signed.get("signer", "")),
            "reason": str(signed.get("reason", "")),
            "old_hash": str(signed.get("old_hash")),
            "new_hash": str(signed.get("new_hash")),
            "timestamp": str(signed.get("timestamp", "")),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
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
