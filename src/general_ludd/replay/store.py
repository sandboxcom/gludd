"""Atomic, integrity-verifiable storage for ``gludd.run-bundle/v1``.

The finalized manifest is the bundle's commit marker. Events are published with
same-directory temporary files under a per-run cross-process lock. Finalization
then authenticates the canonical ordered event index and publishes the manifest
last, so a crash cannot make an unfinished bundle appear complete.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import BaseModel

from general_ludd.replay.schema import (
    EVENT_SCHEMA_V1,
    BundleManifestV1,
    EventEnvelopeV1,
    canonical_replay_json,
    parse_bundle_manifest,
    parse_event_envelope,
    validate_run_id,
)
from general_ludd.replay.telemetry import ReplayTelemetry

_EVENT_FILENAME = re.compile(r"[0-9]{12}\.json\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAC = re.compile(r"[0-9a-f]{64}\Z")
_STORE_MANAGED_EVENT_FIELDS = frozenset({"schema", "sequence", "digest"})
_MANIFEST_HMAC_CONTEXT = b"general_ludd.replay.manifest.v1\x00"


class ReplayStoreError(RuntimeError):
    """Base error for v1 replay persistence failures."""


class ReplayPathError(ReplayStoreError, ValueError):
    """Raised when a replay path is unsafe, escaped, or a symbolic link."""


class ReplayStateError(ReplayStoreError):
    """Raised when an append or finalization violates bundle lifecycle state."""


class ReplayIntegrityError(ReplayStoreError):
    """Raised when a caller requests evidence that does not verify."""


@dataclass(frozen=True, slots=True)
class BundleVerification:
    """Fail-closed verification verdict for one v1 replay bundle."""

    run_id: str
    valid: bool
    complete: bool
    status: str
    event_count: int
    signing_key_id: str | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedBundle:
    """A manifest and event sequence returned only after full verification."""

    manifest: BundleManifestV1
    events: tuple[EventEnvelopeV1, ...]


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Bounded retention outcome without exposing bundle payload content."""

    scanned: int
    planned: tuple[str, ...]
    deleted: tuple[str, ...]
    skipped_locked: tuple[str, ...]
    skipped_protected: tuple[str, ...]
    skipped_incomplete: tuple[str, ...]
    reclaimed_bytes: int
    remaining_bytes: int
    quota_satisfied: bool


@dataclass(frozen=True, slots=True)
class _RetentionCandidate:
    run_id: str
    path: Path
    created_at: datetime
    expired: bool
    size: int


class RunBundleStore:
    """Store and verify immutable v1 replay bundles under one configured root."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        verification_keys: Mapping[str, bytes] | None = None,
        active_key_id: str | None = None,
        lock_timeout: float = 10.0,
        telemetry: ReplayTelemetry | None = None,
    ) -> None:
        """Bind a safe root and an optional versioned HMAC key ring."""
        raw_root = Path(root)
        if raw_root.is_symlink():
            raise ReplayPathError("replay root must not be a symlink")
        if raw_root.exists() and not raw_root.is_dir():
            raise ReplayPathError("replay root must be a directory")
        raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root = raw_root.resolve(strict=True)
        self._runs_root = self._root / "runs-v1"
        self._locks_root = self._root / ".run-locks"
        self._ensure_directory(self._runs_root)
        self._ensure_directory(self._locks_root)

        if (
            not isinstance(lock_timeout, (int, float))
            or isinstance(lock_timeout, bool)
            or lock_timeout <= 0
        ):
            raise ValueError("lock_timeout must be a positive number")
        self._lock_timeout = float(lock_timeout)

        keys: dict[str, bytes] = {}
        for key_id, key in (verification_keys or {}).items():
            self._validate_key_id(key_id)
            if not isinstance(key, bytes) or len(key) < 16:
                raise ValueError("replay verification keys must contain at least 16 bytes")
            keys[key_id] = key
        if active_key_id is not None:
            self._validate_key_id(active_key_id)
            if active_key_id not in keys:
                raise ValueError("active_key_id must identify a configured verification key")
        self._verification_keys = keys
        self._active_key_id = active_key_id
        self._telemetry = telemetry if telemetry is not None else ReplayTelemetry()

    @staticmethod
    def _failure_reason(exc: Exception) -> str:
        """Collapse internal exceptions into the bounded telemetry vocabulary."""
        if isinstance(exc, Timeout):
            return "concurrency"
        if isinstance(exc, ReplayIntegrityError):
            return "integrity"
        if isinstance(exc, (TypeError, ValueError, ReplayStateError)):
            return "validation"
        if isinstance(exc, (OSError, ReplayPathError)):
            return "storage"
        return "internal"

    @staticmethod
    def _validate_key_id(key_id: str) -> str:
        if not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None:
            raise ValueError("replay key ID must be a bounded safe identifier")
        return key_id

    def _assert_safe_path(self, path: Path) -> None:
        try:
            relative = path.absolute().relative_to(self._root)
        except ValueError as exc:
            raise ReplayPathError("replay path escapes the configured root") from exc

        current = self._root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ReplayPathError(f"replay path contains symlink: {current.name}")
        try:
            path.resolve(strict=False).relative_to(self._root)
        except ValueError as exc:
            raise ReplayPathError("replay path resolves outside the configured root") from exc

    def _ensure_directory(self, path: Path) -> None:
        self._assert_safe_path(path)
        if path.exists() and not path.is_dir():
            raise ReplayPathError(f"replay directory path is not a directory: {path.name}")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._assert_safe_path(path)

    def bundle_path(self, run_id: str) -> Path:
        """Return the safe v1 bundle path for *run_id*."""
        safe_run_id = validate_run_id(run_id)
        path = self._runs_root / safe_run_id
        self._assert_safe_path(path)
        return path

    def run_lock(self, run_id: str, *, timeout: float | None = None) -> FileLock:
        """Return the maintained cross-process lock for one safe run ID."""
        safe_run_id = validate_run_id(run_id)
        self._ensure_directory(self._locks_root)
        path = self._locks_root / f"{safe_run_id}.lock"
        self._assert_safe_path(path)
        selected_timeout = self._lock_timeout if timeout is None else timeout
        return FileLock(str(path), timeout=selected_timeout, mode=0o600)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        self._assert_safe_path(path)
        self._ensure_directory(path.parent)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        self._assert_safe_path(temporary)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()

    @staticmethod
    def _sha256(payload: bytes) -> str:
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"

    @classmethod
    def _event_digest(cls, event: EventEnvelopeV1) -> str:
        payload = event.model_dump(mode="json", by_alias=True)
        payload.pop("digest", None)
        return cls._sha256(canonical_replay_json(payload).encode("utf-8"))

    @classmethod
    def _event_index_digest(cls, events: tuple[EventEnvelopeV1, ...]) -> str:
        index = [[event.sequence, event.digest] for event in events]
        return cls._sha256(canonical_replay_json(index).encode("utf-8"))

    @staticmethod
    def _manifest_payload(
        manifest: BundleManifestV1 | Mapping[str, object],
    ) -> dict[str, object]:
        if isinstance(manifest, BaseModel):
            return manifest.model_dump(mode="json", by_alias=True)
        return dict(manifest)

    def _manifest_mac(self, manifest: BundleManifestV1, key: bytes) -> str:
        payload = canonical_replay_json(manifest).encode("utf-8")
        return hmac.new(
            key,
            _MANIFEST_HMAC_CONTEXT + payload,
            hashlib.sha256,
        ).hexdigest()

    def _clean_unpublished_event_temps(self, events_dir: Path) -> None:
        if not events_dir.exists():
            return
        for entry in events_dir.iterdir():
            self._assert_safe_path(entry)
            if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                if not entry.is_file():
                    raise ReplayPathError("unpublished event temporary is not a file")
                entry.unlink()

    def _event_paths(self, events_dir: Path) -> tuple[Path, ...]:
        if not events_dir.exists():
            return ()
        self._assert_safe_path(events_dir)
        if not events_dir.is_dir():
            raise ReplayIntegrityError("events path is not a directory")
        paths: list[Path] = []
        for entry in events_dir.iterdir():
            self._assert_safe_path(entry)
            if not entry.is_file() or _EVENT_FILENAME.fullmatch(entry.name) is None:
                raise ReplayIntegrityError(f"unexpected event entry: {entry.name}")
            paths.append(entry)
        return tuple(sorted(paths, key=lambda item: item.name))

    def _read_events(self, events_dir: Path) -> tuple[EventEnvelopeV1, ...]:
        paths = self._event_paths(events_dir)
        expected_names = [f"{sequence:012d}.json" for sequence in range(len(paths))]
        if [path.name for path in paths] != expected_names:
            raise ReplayIntegrityError("event filenames are missing, extra, or non-contiguous")

        events: list[EventEnvelopeV1] = []
        event_ids: set[str] = set()
        for sequence, path in enumerate(paths):
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                event = parse_event_envelope(text)
            except (OSError, UnicodeError, TypeError, ValueError) as exc:
                raise ReplayIntegrityError(
                    f"event {path.name} is corrupt or invalid: {exc}"
                ) from exc
            if canonical_replay_json(event).encode("utf-8") != raw:
                raise ReplayIntegrityError(f"event {path.name} is not canonical JSON")
            if event.sequence != sequence:
                raise ReplayIntegrityError(
                    f"event {path.name} sequence does not match its ordered index"
                )
            if event.event_id in event_ids:
                raise ReplayIntegrityError("event IDs must be unique within a bundle")
            event_ids.add(event.event_id)
            expected_digest = self._event_digest(event)
            if not hmac.compare_digest(expected_digest, event.digest):
                raise ReplayIntegrityError(f"event {path.name} digest mismatch")
            events.append(event)
        return tuple(events)

    def append_event(
        self,
        run_id: str,
        event: Mapping[str, object],
    ) -> EventEnvelopeV1:
        """Allocate, validate, and atomically publish one typed event envelope."""
        started = time.perf_counter()
        try:
            envelope = self._append_event(run_id, event)
        except Exception as exc:
            self._telemetry.record_failure(self._failure_reason(exc))
            self._telemetry.operation("record", "failure")
            self._telemetry.record_seconds(None, time.perf_counter() - started)
            raise
        self._telemetry.events_recorded(EVENT_SCHEMA_V1, envelope.type)
        for kind in envelope.redaction.kinds:
            self._telemetry.redactions(kind)
        self._telemetry.operation("record", "success")
        self._telemetry.record_seconds(envelope.type, time.perf_counter() - started)
        return envelope

    def _append_event(
        self,
        run_id: str,
        event: Mapping[str, object],
    ) -> EventEnvelopeV1:
        """Perform one append without emitting an outcome before publication."""
        safe_run_id = validate_run_id(run_id)
        supplied = dict(event)
        reserved = sorted(_STORE_MANAGED_EVENT_FIELDS.intersection(supplied))
        if reserved:
            raise ValueError(
                "event contains store-managed field(s): " + ", ".join(reserved)
            )

        with self.run_lock(safe_run_id):
            bundle = self.bundle_path(safe_run_id)
            self._ensure_directory(bundle)
            manifest_path = bundle / "manifest.json"
            self._assert_safe_path(manifest_path)
            if manifest_path.exists():
                raise ReplayStateError("cannot append to a finalized replay bundle")
            events_dir = bundle / "events"
            self._ensure_directory(events_dir)
            self._clean_unpublished_event_temps(events_dir)
            existing = self._read_events(events_dir)
            sequence = len(existing)
            unsigned_payload: dict[str, object] = {
                "schema": EVENT_SCHEMA_V1,
                "sequence": sequence,
                **supplied,
                "digest": "sha256:" + "0" * 64,
            }
            unsigned = parse_event_envelope(unsigned_payload)
            final_payload = unsigned.model_dump(mode="json", by_alias=True)
            final_payload["digest"] = self._event_digest(unsigned)
            envelope = parse_event_envelope(final_payload)
            event_path = events_dir / f"{sequence:012d}.json"
            self._atomic_write(
                event_path,
                canonical_replay_json(envelope).encode("utf-8"),
            )
            return envelope

    def finalize(
        self,
        run_id: str,
        manifest: BundleManifestV1 | Mapping[str, object],
    ) -> BundleManifestV1:
        """Atomically publish a signed or explicitly unsigned final manifest."""
        try:
            finalized = self._finalize(run_id, manifest)
        except Exception as exc:
            self._telemetry.record_failure(self._failure_reason(exc))
            self._telemetry.operation("finalize", "failure")
            raise
        self._telemetry.bundle_finalized(finalized.schema_version, finalized.status)
        for attachment in finalized.attachments:
            if attachment.truncated:
                self._telemetry.truncations("attachment")
        try:
            _, _, bundle_size = self._collect_tree(self.bundle_path(finalized.run_id))
        except Exception:
            pass
        else:
            self._telemetry.bundle_bytes(finalized.schema_version, bundle_size)
        self._telemetry.operation("finalize", "success")
        return finalized

    def _finalize(
        self,
        run_id: str,
        manifest: BundleManifestV1 | Mapping[str, object],
    ) -> BundleManifestV1:
        """Perform finalization without announcing success before commit."""
        safe_run_id = validate_run_id(run_id)
        supplied = self._manifest_payload(manifest)
        if supplied.get("run_id") != safe_run_id:
            raise ReplayStateError("manifest run_id must match the finalized bundle")
        if supplied.get("status") == "running":
            raise ReplayStateError("a running replay bundle cannot be finalized")

        with self.run_lock(safe_run_id):
            bundle = self.bundle_path(safe_run_id)
            self._ensure_directory(bundle)
            events_dir = bundle / "events"
            self._ensure_directory(events_dir)
            manifest_path = bundle / "manifest.json"
            hmac_path = bundle / "manifest.hmac"
            self._assert_safe_path(manifest_path)
            self._assert_safe_path(hmac_path)
            if manifest_path.exists():
                raise ReplayStateError("replay bundle is already finalized")
            self._clean_unpublished_event_temps(events_dir)
            events = self._read_events(events_dir)

            supplied["event_count"] = len(events)
            supplied["events_sha256"] = self._event_index_digest(events)
            if self._active_key_id is None:
                supplied["integrity"] = "unsigned"
                supplied["signing_key_id"] = None
            else:
                supplied["integrity"] = "signed"
                supplied["signing_key_id"] = self._active_key_id
            finalized = parse_bundle_manifest(supplied)

            if finalized.integrity == "signed":
                key_id = finalized.signing_key_id
                if key_id is None:
                    raise ReplayStateError("signed manifest has no signing key ID")
                mac = self._manifest_mac(finalized, self._verification_keys[key_id])
                self._atomic_write(hmac_path, mac.encode("ascii"))
            else:
                with contextlib.suppress(FileNotFoundError):
                    hmac_path.unlink()
            self._atomic_write(
                manifest_path,
                canonical_replay_json(finalized).encode("utf-8"),
            )
            return finalized

    def _verify_attachments(
        self,
        bundle: Path,
        manifest: BundleManifestV1,
    ) -> tuple[str, ...]:
        attachments_dir = bundle / "attachments"
        expected_names: set[str] = set()
        errors: list[str] = []
        for attachment in manifest.attachments:
            digest_hex = attachment.digest.removeprefix("sha256:")
            name = f"sha256-{digest_hex}"
            expected_names.add(name)
            path = attachments_dir / name
            try:
                self._assert_safe_path(path)
                if not path.is_file():
                    errors.append(f"attachment missing: {name}")
                    continue
                payload = path.read_bytes()
            except (OSError, ReplayPathError) as exc:
                errors.append(f"attachment unreadable: {name}: {exc}")
                continue
            if len(payload) != attachment.stored_bytes:
                errors.append(f"attachment size mismatch: {name}")
            if not hmac.compare_digest(self._sha256(payload), attachment.digest):
                errors.append(f"attachment digest mismatch: {name}")

        if attachments_dir.exists():
            try:
                self._assert_safe_path(attachments_dir)
                if not attachments_dir.is_dir():
                    return (*errors, "attachments path is not a directory")
                actual_names: set[str] = set()
                for entry in attachments_dir.iterdir():
                    self._assert_safe_path(entry)
                    if not entry.is_file():
                        errors.append(f"unexpected attachment entry: {entry.name}")
                    actual_names.add(entry.name)
                for extra in sorted(actual_names - expected_names):
                    errors.append(f"unexpected attachment: {extra}")
            except (OSError, ReplayPathError) as exc:
                errors.append(f"attachments directory is unsafe: {exc}")
        elif expected_names:
            errors.append("attachments directory missing")
        return tuple(errors)

    def _verify_unlocked(
        self,
        safe_run_id: str,
    ) -> tuple[BundleVerification, BundleManifestV1 | None, tuple[EventEnvelopeV1, ...]]:
        try:
            bundle = self.bundle_path(safe_run_id)
        except (TypeError, ValueError) as exc:
            verdict = BundleVerification(
                run_id=safe_run_id,
                valid=False,
                complete=False,
                status="incomplete",
                event_count=0,
                signing_key_id=None,
                errors=(f"bundle path is unsafe: {exc}",),
            )
            return verdict, None, ()
        manifest_path = bundle / "manifest.json"
        if not manifest_path.exists():
            verdict = BundleVerification(
                run_id=safe_run_id,
                valid=False,
                complete=False,
                status="incomplete",
                event_count=0,
                signing_key_id=None,
                errors=("finalized manifest is missing",),
            )
            return verdict, None, ()

        errors: list[str] = []
        try:
            self._assert_safe_path(manifest_path)
            raw_manifest = manifest_path.read_bytes()
            manifest_text = raw_manifest.decode("utf-8")
            manifest = parse_bundle_manifest(manifest_text)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            verdict = BundleVerification(
                run_id=safe_run_id,
                valid=False,
                complete=False,
                status="incomplete",
                event_count=0,
                signing_key_id=None,
                errors=(f"manifest is corrupt or invalid: {exc}",),
            )
            return verdict, None, ()

        canonical_manifest = canonical_replay_json(manifest).encode("utf-8")
        if canonical_manifest != raw_manifest:
            errors.append("manifest is not canonical JSON")
        if manifest.run_id != safe_run_id:
            errors.append("manifest run_id does not match bundle path")

        hmac_path = bundle / "manifest.hmac"
        if manifest.integrity == "signed":
            key_id = manifest.signing_key_id
            key = self._verification_keys.get(key_id or "")
            if key is None:
                errors.append(f"HMAC verification key is unavailable: {key_id}")
            else:
                try:
                    self._assert_safe_path(hmac_path)
                    stored_mac = hmac_path.read_text(encoding="ascii")
                except (OSError, UnicodeError, ReplayPathError) as exc:
                    errors.append(f"manifest HMAC is missing or unreadable: {exc}")
                else:
                    if _MAC.fullmatch(stored_mac) is None:
                        errors.append("manifest HMAC has an invalid encoding")
                    expected_mac = self._manifest_mac(manifest, key)
                    if not hmac.compare_digest(expected_mac, stored_mac):
                        errors.append("manifest HMAC mismatch")
        elif hmac_path.exists():
            errors.append("unsigned manifest has an unexpected HMAC sidecar")

        try:
            events = self._read_events(bundle / "events")
        except (OSError, ReplayIntegrityError, ReplayPathError) as exc:
            errors.append(str(exc))
            events = ()
        if len(events) != manifest.event_count:
            errors.append("manifest event_count does not match stored events")
        if not hmac.compare_digest(
            self._event_index_digest(events), manifest.events_sha256
        ):
            errors.append("ordered event index digest mismatch")
        errors.extend(self._verify_attachments(bundle, manifest))

        try:
            allowed = {"manifest.json", "events", "attachments"}
            if manifest.integrity == "signed":
                allowed.add("manifest.hmac")
            for entry in bundle.iterdir():
                self._assert_safe_path(entry)
                if entry.name not in allowed:
                    errors.append(f"unexpected bundle entry: {entry.name}")
        except (OSError, ReplayPathError) as exc:
            errors.append(f"bundle directory is unsafe: {exc}")

        valid = not errors
        complete = (
            valid
            and manifest.finalized_at is not None
            and manifest.status in {"completed", "failed", "cancelled"}
        )
        verdict = BundleVerification(
            run_id=safe_run_id,
            valid=valid,
            complete=complete,
            status=manifest.status if valid else "incomplete",
            event_count=len(events),
            signing_key_id=manifest.signing_key_id,
            errors=tuple(errors),
        )
        return verdict, manifest, events

    @staticmethod
    def _verification_reason(verdict: BundleVerification) -> tuple[str, str]:
        """Map a detailed verdict to bounded outcome and reason classes."""
        if verdict.valid and verdict.complete:
            return "valid", "ok"
        errors = " ".join(verdict.errors).lower()
        if "unsupported" in errors and "schema" in errors:
            return "unsupported", "schema"
        if "missing" in errors:
            return "invalid", "missing"
        if "verification key" in errors:
            return "invalid", "key"
        if "hmac" in errors or "signature" in errors:
            return "invalid", "signature"
        if "digest" in errors:
            return "invalid", "digest"
        if "sequence" in errors or "event_count" in errors or "non-contiguous" in errors:
            return "invalid", "sequence"
        if "unexpected" in errors or "extra" in errors:
            return "invalid", "extra"
        if "locked" in errors:
            return "invalid", "storage"
        return "invalid", "corrupt"

    def verify(self, run_id: str) -> BundleVerification:
        """Verify a bundle and emit one bounded verdict for this public call."""
        try:
            verdict = self._verify(run_id)
        except Exception as exc:
            reason = "storage" if isinstance(exc, (OSError, ReplayPathError)) else "corrupt"
            self._telemetry.verification("v1", "error", reason)
            raise
        outcome, reason = self._verification_reason(verdict)
        self._telemetry.verification("v1", outcome, reason)
        return verdict

    def _verify(self, run_id: str) -> BundleVerification:
        """Verify without duplicating metrics for internal integrity checks."""
        safe_run_id = validate_run_id(run_id)
        try:
            with self.run_lock(safe_run_id):
                verdict, _, _ = self._verify_unlocked(safe_run_id)
                return verdict
        except Timeout:
            return BundleVerification(
                run_id=safe_run_id,
                valid=False,
                complete=False,
                status="incomplete",
                event_count=0,
                signing_key_id=None,
                errors=("replay bundle is locked",),
            )

    def read_verified(self, run_id: str) -> VerifiedBundle:
        """Return a bundle only after all integrity and completeness checks pass."""
        safe_run_id = validate_run_id(run_id)
        with self.run_lock(safe_run_id):
            verdict, manifest, events = self._verify_unlocked(safe_run_id)
            if not verdict.valid or not verdict.complete or manifest is None:
                details = "; ".join(verdict.errors) or "bundle is incomplete"
                raise ReplayIntegrityError(
                    f"replay bundle {safe_run_id!r} did not verify: {details}"
                )
            return VerifiedBundle(manifest=manifest, events=events)

    def _collect_tree(self, root: Path) -> tuple[list[Path], list[Path], int]:
        self._assert_safe_path(root)
        if not root.is_dir():
            raise ReplayPathError("bundle retention target is not a directory")
        files: list[Path] = []
        directories: list[Path] = [root]
        size = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            self._assert_safe_path(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    self._assert_safe_path(path)
                    if entry.is_symlink():
                        raise ReplayPathError("retention refuses symbolic links")
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(path)
                        size += entry.stat(follow_symlinks=False).st_size
                    else:
                        raise ReplayPathError("retention refuses special files")
        return files, directories, size

    def _delete_tree(self, root: Path) -> int:
        files, directories, size = self._collect_tree(root)
        for path in files:
            path.unlink()
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            path.rmdir()
        self._fsync_directory(self._runs_root)
        return size

    @staticmethod
    def _validate_retention_bound(value: int, name: str, *, allow_zero: bool) -> int:
        minimum = 0 if allow_zero else 1
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{name} must be a {qualifier} integer")
        return value

    def enforce_retention(
        self,
        *,
        now: datetime,
        max_total_bytes: int,
        max_deletions: int = 100,
        scan_limit: int = 1_000,
        dry_run: bool = False,
    ) -> RetentionResult:
        """Delete a bounded set of oldest eligible finalized bundles.

        Expired bundles are considered first. Byte-pressure cleanup then removes
        the oldest unpinned, unheld finalized bundles. Locked, incomplete,
        corrupt, symlinked, pinned, and held evidence is never deleted.
        """
        self._validate_retention_bound(
            max_total_bytes, "max_total_bytes", allow_zero=True
        )
        self._validate_retention_bound(max_deletions, "max_deletions", allow_zero=False)
        self._validate_retention_bound(scan_limit, "scan_limit", allow_zero=False)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("retention now must be timezone-aware")
        now_utc = now.astimezone(UTC)

        candidates: list[_RetentionCandidate] = []
        skipped_protected: list[str] = []
        skipped_incomplete: list[str] = []
        total_bytes = 0
        scanned = 0
        truncated_scan = False
        store_size_complete = True
        self._ensure_directory(self._runs_root)
        for entry in sorted(self._runs_root.iterdir(), key=lambda item: item.name):
            if scanned >= scan_limit:
                truncated_scan = True
                break
            scanned += 1
            size_counted = False
            try:
                run_id = validate_run_id(entry.name)
                self._assert_safe_path(entry)
                _, _, size = self._collect_tree(entry)
                total_bytes += size
                size_counted = True
                manifest_path = entry / "manifest.json"
                if not manifest_path.is_file():
                    skipped_incomplete.append(entry.name)
                    continue
                manifest = parse_bundle_manifest(manifest_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, ReplayPathError):
                if not size_counted:
                    store_size_complete = False
                skipped_incomplete.append(entry.name)
                continue
            if manifest.run_id != run_id:
                skipped_incomplete.append(run_id)
                continue
            if manifest.retention.pinned or manifest.retention.hold_reason is not None:
                skipped_protected.append(run_id)
                continue
            expires_at = manifest.retention.expires_at
            expired = expires_at is not None and expires_at <= now_utc
            candidates.append(
                _RetentionCandidate(
                    run_id=run_id,
                    path=entry,
                    created_at=manifest.created_at,
                    expired=expired,
                    size=size,
                )
            )

        candidates.sort(key=lambda item: (not item.expired, item.created_at, item.run_id))
        planned: list[str] = []
        deleted: list[str] = []
        skipped_locked: list[str] = []
        reclaimed = 0
        projected_total = total_bytes
        for candidate in candidates:
            if len(planned) >= max_deletions:
                break
            if not candidate.expired and projected_total <= max_total_bytes:
                continue
            try:
                with self.run_lock(candidate.run_id, timeout=0):
                    verdict, verified_manifest, _ = self._verify_unlocked(
                        candidate.run_id
                    )
                    if not verdict.valid or verified_manifest is None:
                        skipped_incomplete.append(candidate.run_id)
                        continue
                    if (
                        verified_manifest.retention.pinned
                        or verified_manifest.retention.hold_reason is not None
                    ):
                        skipped_protected.append(candidate.run_id)
                        continue
                    planned.append(candidate.run_id)
                    projected_total -= candidate.size
                    if not dry_run:
                        reclaimed += self._delete_tree(candidate.path)
                        deleted.append(candidate.run_id)
            except Timeout:
                skipped_locked.append(candidate.run_id)
            except (OSError, ReplayPathError):
                skipped_incomplete.append(candidate.run_id)

        remaining = total_bytes - reclaimed
        return RetentionResult(
            scanned=scanned,
            planned=tuple(planned),
            deleted=tuple(deleted),
            skipped_locked=tuple(sorted(set(skipped_locked))),
            skipped_protected=tuple(sorted(set(skipped_protected))),
            skipped_incomplete=tuple(sorted(set(skipped_incomplete))),
            reclaimed_bytes=reclaimed,
            remaining_bytes=remaining,
            quota_satisfied=(
                not truncated_scan
                and store_size_complete
                and (projected_total if dry_run else remaining) <= max_total_bytes
            ),
        )


__all__ = [
    "BundleVerification",
    "ReplayIntegrityError",
    "ReplayPathError",
    "ReplayStateError",
    "ReplayStoreError",
    "RetentionResult",
    "RunBundleStore",
    "VerifiedBundle",
]
