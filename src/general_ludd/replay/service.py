"""Read-only service core for inspecting and exporting v1 replay bundles."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from general_ludd.replay.schema import (
    AttachmentV1,
    BundleManifestV1,
    EventEnvelopeV1,
    canonical_replay_json,
    validate_run_id,
)
from general_ludd.replay.store import (
    BundleVerification,
    ReplayIntegrityError,
    RunBundleStore,
    VerifiedBundle,
)
from general_ludd.replay.telemetry import ReplayTelemetry

_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CURSOR_VERSION = 1
_CURSOR_CONTEXT = b"general_ludd.replay.cursor.v1\x00"
_CURSOR_PAYLOAD_BYTES = 17
_CURSOR_MAC_BYTES = 16
_MAX_CURSOR_TEXT = 128
_MAX_PAGE_SIZE = 200
_MIN_EXPORT_CHUNK = 4_096
_MAX_EXPORT_CHUNK = 1_048_576

ReplayCapability = Literal["replay:read", "replay:export"]
ReplayAction = Literal["list", "show", "verify", "export"]
ReplayOutcome = Literal["success", "failure"]
VerificationReason = Literal[
    "verified",
    "unsigned",
    "incomplete",
    "unsupported_schema",
    "integrity_failure",
]


class ReplayServiceError(RuntimeError):
    """Base error for the read-only replay service boundary."""


class ReplayAccessDeniedError(ReplayServiceError):
    """Raised for an unauthorized collection request."""


class ReplayNotFoundError(ReplayServiceError):
    """Raised for missing, cross-project, or unauthorized individual runs."""


class ReplayCursorError(ReplayServiceError, ValueError):
    """Raised when an opaque pagination cursor is malformed or out of scope."""


class ReplayCatalogError(ReplayServiceError):
    """Raised when the injected catalog violates its bounded contract."""


class ReplayAuditError(ReplayServiceError):
    """Raised when a required audit event cannot be emitted."""


class ReplayExportError(ReplayServiceError):
    """Raised when a bundle cannot be exported as verified evidence."""


@dataclass(frozen=True, slots=True)
class ReplayCatalogEntry:
    """Trusted content-free catalog metadata for one replay bundle."""

    run_id: str
    project_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        """Validate identifiers and normalize the catalog timestamp to UTC."""
        validate_run_id(self.run_id)
        _validate_project_id(self.project_id)
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("replay catalog created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))


class ReplayCatalog(Protocol):
    """Bounded trusted index used to avoid scanning replay storage."""

    def page(
        self,
        *,
        project_id: str | None,
        offset: int,
        limit: int,
    ) -> Sequence[ReplayCatalogEntry]:
        """Return no more than *limit* stable rows starting at *offset*."""

    def get(self, run_id: str) -> ReplayCatalogEntry | None:
        """Return trusted scope metadata for one safe run ID."""


class ReplayAuthorizer(Protocol):
    """Injected request-bound capability decision."""

    def __call__(
        self,
        capability: ReplayCapability,
        project_id: str | None,
    ) -> bool:
        """Return whether the current principal may act in the project scope."""


@dataclass(frozen=True, slots=True)
class ReplayAuditEvent:
    """Content-free audit record for one service operation."""

    action: ReplayAction
    outcome: ReplayOutcome
    reason: str
    run_id: str | None
    project_id: str | None


class ReplayAuditSink(Protocol):
    """Injected sink for content-free replay audit events."""

    def __call__(self, event: ReplayAuditEvent, /) -> None:
        """Persist one replay audit event or raise."""


@dataclass(frozen=True, slots=True)
class ReplayVerification:
    """Sanitized verification state with no raw parser or storage errors."""

    valid: bool
    complete: bool
    status: str
    event_count: int
    integrity: Literal["signed", "unsigned", "unverified"]
    signing_key_id: str | None
    reason: VerificationReason


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Content-free list entry for one project-scoped run."""

    run_id: str
    project_id: str | None
    created_at: datetime
    verification: ReplayVerification


@dataclass(frozen=True, slots=True)
class ReplayPage:
    """Bounded replay summaries and an opaque continuation cursor."""

    items: tuple[ReplaySummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ReplayManifestMetadata:
    """Safe manifest metadata; request parameters and captured content are omitted."""

    run_id: str
    parent_run_id: str | None
    operation: str
    created_at: datetime
    finalized_at: datetime | None
    status: str
    project_id: str | None
    event_count: int
    events_sha256: str
    source_repository_sha256: str
    source_commit_sha: str
    source_tree_sha: str
    runtime_config_sha256: str
    model_provider: str
    model_profile: str
    model_name: str
    expected_stage_count: int
    observed_stage_count: int
    recorder_error_count: int
    missing_range_count: int


@dataclass(frozen=True, slots=True)
class ReplayEventMetadata:
    """Event metadata without captured payload content."""

    sequence: int
    event_id: str
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    project_id: str | None
    digest: str
    redaction_count: int


@dataclass(frozen=True, slots=True)
class ReplayAttachmentMetadata:
    """Content-addressed attachment metadata without bytes."""

    digest: str
    original_bytes: int
    stored_bytes: int
    media_type: str
    encoding: str | None
    redaction_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ReplayDetail:
    """Read-only metadata view for a verified or visibly unverified bundle."""

    run_id: str
    project_id: str | None
    verification: ReplayVerification
    manifest: ReplayManifestMetadata | None
    events: tuple[ReplayEventMetadata, ...]
    attachments: tuple[ReplayAttachmentMetadata, ...]


def _noop_audit(event: ReplayAuditEvent) -> None:
    del event


def _validate_project_id(project_id: str | None) -> str | None:
    if project_id is None:
        return None
    if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
        raise ValueError("project_id must be a bounded safe identifier or null")
    return project_id


class ReplayService:
    """Read-only, project-scoped facade over :class:`RunBundleStore`."""

    def __init__(
        self,
        store: RunBundleStore,
        *,
        catalog: ReplayCatalog,
        authorize: ReplayAuthorizer,
        cursor_key: bytes,
        audit: ReplayAuditSink = _noop_audit,
        telemetry: ReplayTelemetry | None = None,
    ) -> None:
        """Bind storage and request-independent authorization/audit adapters."""
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 16:
            raise ValueError("cursor_key must contain at least 16 bytes")
        self._store = store
        self._catalog = catalog
        self._authorize = authorize
        self._audit = audit
        self._cursor_key = cursor_key
        self._telemetry = telemetry if telemetry is not None else ReplayTelemetry()

    def _is_authorized(
        self,
        capability: ReplayCapability,
        project_id: str | None,
    ) -> bool:
        try:
            return self._authorize(capability, project_id) is True
        except Exception as exc:
            raise ReplayAccessDeniedError("replay access denied") from exc

    def _emit_audit(
        self,
        action: ReplayAction,
        outcome: ReplayOutcome,
        reason: str,
        *,
        run_id: str | None,
        project_id: str | None,
    ) -> None:
        event = ReplayAuditEvent(
            action=action,
            outcome=outcome,
            reason=reason,
            run_id=run_id,
            project_id=project_id,
        )
        try:
            self._audit(event)
        except Exception as exc:
            raise ReplayAuditError("replay audit unavailable") from exc

    @staticmethod
    def _scope_digest(project_id: str | None) -> bytes:
        scope = b"\x00system" if project_id is None else b"\x01" + project_id.encode()
        return hashlib.sha256(scope).digest()[:8]

    def _encode_cursor(self, offset: int, project_id: str | None) -> str:
        payload = bytes([_CURSOR_VERSION]) + struct.pack(">Q", offset)
        payload += self._scope_digest(project_id)
        mac = hmac.new(
            self._cursor_key,
            _CURSOR_CONTEXT + payload,
            hashlib.sha256,
        ).digest()[:_CURSOR_MAC_BYTES]
        return base64.urlsafe_b64encode(payload + mac).rstrip(b"=").decode("ascii")

    def _decode_cursor(self, cursor: str | None, project_id: str | None) -> int:
        if cursor is None:
            return 0
        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > _MAX_CURSOR_TEXT
            or not cursor.isascii()
        ):
            raise ReplayCursorError("invalid replay cursor")
        padding = "=" * (-len(cursor) % 4)
        try:
            decoded = base64.b64decode(
                cursor + padding,
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise ReplayCursorError("invalid replay cursor") from exc
        expected_size = _CURSOR_PAYLOAD_BYTES + _CURSOR_MAC_BYTES
        if len(decoded) != expected_size:
            raise ReplayCursorError("invalid replay cursor")
        payload = decoded[:_CURSOR_PAYLOAD_BYTES]
        stored_mac = decoded[_CURSOR_PAYLOAD_BYTES:]
        expected_mac = hmac.new(
            self._cursor_key,
            _CURSOR_CONTEXT + payload,
            hashlib.sha256,
        ).digest()[:_CURSOR_MAC_BYTES]
        if not hmac.compare_digest(stored_mac, expected_mac):
            raise ReplayCursorError("invalid replay cursor")
        if payload[0] != _CURSOR_VERSION or not hmac.compare_digest(
            payload[9:], self._scope_digest(project_id)
        ):
            raise ReplayCursorError("invalid replay cursor")
        return int(struct.unpack(">Q", payload[1:9])[0])

    @staticmethod
    def _verification(verdict: BundleVerification) -> ReplayVerification:
        errors = " ".join(verdict.errors).lower()
        if verdict.valid and verdict.complete:
            reason: VerificationReason = (
                "verified" if verdict.signing_key_id is not None else "unsigned"
            )
        elif "unsupported replay bundle schema" in errors:
            reason = "unsupported_schema"
        elif "finalized manifest is missing" in errors:
            reason = "incomplete"
        else:
            reason = "integrity_failure"
        if verdict.valid:
            integrity: Literal["signed", "unsigned", "unverified"] = (
                "signed" if verdict.signing_key_id is not None else "unsigned"
            )
        else:
            integrity = "unverified"
        return ReplayVerification(
            valid=verdict.valid,
            complete=verdict.complete,
            status=verdict.status,
            event_count=verdict.event_count,
            integrity=integrity,
            signing_key_id=verdict.signing_key_id if verdict.valid else None,
            reason=reason,
        )

    def _summary(self, entry: ReplayCatalogEntry) -> ReplaySummary:
        try:
            verdict = self._store.verify(entry.run_id)
        except Exception:
            verification = ReplayVerification(
                valid=False,
                complete=False,
                status="incomplete",
                event_count=0,
                integrity="unverified",
                signing_key_id=None,
                reason="integrity_failure",
            )
        else:
            verification = self._verification(verdict)
        return ReplaySummary(
            run_id=entry.run_id,
            project_id=entry.project_id,
            created_at=entry.created_at,
            verification=verification,
        )

    def list_runs(
        self,
        *,
        project_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ReplayPage:
        """Return one authorized, bounded page using a tamper-evident cursor."""
        try:
            page = self._list_runs(project_id=project_id, limit=limit, cursor=cursor)
        except Exception:
            self._telemetry.operation("list", "failure")
            raise
        self._telemetry.operation("list", "success")
        return page

    def _list_runs(
        self,
        *,
        project_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> ReplayPage:
        """Build a complete authorized page before emitting its outcome."""
        safe_project_id = _validate_project_id(project_id)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_PAGE_SIZE
        ):
            raise ValueError(f"limit must be an integer between 1 and {_MAX_PAGE_SIZE}")
        if not self._is_authorized("replay:read", safe_project_id):
            raise ReplayAccessDeniedError("replay access denied")
        offset = self._decode_cursor(cursor, safe_project_id)
        requested = limit + 1
        try:
            rows = self._catalog.page(
                project_id=safe_project_id,
                offset=offset,
                limit=requested,
            )
        except Exception as exc:
            raise ReplayCatalogError("replay catalog unavailable") from exc
        if not isinstance(rows, Sequence) or len(rows) > requested:
            raise ReplayCatalogError("replay catalog unavailable")
        normalized = tuple(rows)
        run_ids: set[str] = set()
        for entry in normalized:
            if (
                not isinstance(entry, ReplayCatalogEntry)
                or entry.project_id != safe_project_id
                or entry.run_id in run_ids
            ):
                raise ReplayCatalogError("replay catalog unavailable")
            run_ids.add(entry.run_id)
        has_more = len(normalized) > limit
        visible = normalized[:limit]
        page = ReplayPage(
            items=tuple(self._summary(entry) for entry in visible),
            next_cursor=(
                self._encode_cursor(offset + len(visible), safe_project_id)
                if has_more
                else None
            ),
        )
        self._emit_audit(
            "list",
            "success",
            "listed",
            run_id=None,
            project_id=safe_project_id,
        )
        return page

    def _lookup(
        self,
        run_id: str,
        project_id: str | None,
        capability: ReplayCapability,
    ) -> ReplayCatalogEntry:
        safe_run_id = validate_run_id(run_id)
        safe_project_id = _validate_project_id(project_id)
        if not self._is_authorized(capability, safe_project_id):
            raise ReplayNotFoundError("replay not found")
        try:
            entry = self._catalog.get(safe_run_id)
        except Exception as exc:
            raise ReplayCatalogError("replay catalog unavailable") from exc
        if (
            entry is None
            or not isinstance(entry, ReplayCatalogEntry)
            or entry.project_id != safe_project_id
        ):
            raise ReplayNotFoundError("replay not found")
        return entry

    @staticmethod
    def _manifest_metadata(manifest: BundleManifestV1) -> ReplayManifestMetadata:
        return ReplayManifestMetadata(
            run_id=manifest.run_id,
            parent_run_id=manifest.parent_run_id,
            operation=manifest.operation,
            created_at=manifest.created_at,
            finalized_at=manifest.finalized_at,
            status=manifest.status,
            project_id=manifest.project_id,
            event_count=manifest.event_count,
            events_sha256=manifest.events_sha256,
            source_repository_sha256=manifest.source.repository_url_sha256,
            source_commit_sha=manifest.source.commit_sha,
            source_tree_sha=manifest.source.tree_sha,
            runtime_config_sha256=manifest.runtime.config_sha256,
            model_provider=manifest.model.provider,
            model_profile=manifest.model.profile,
            model_name=manifest.model.model,
            expected_stage_count=len(manifest.completeness.expected_stages),
            observed_stage_count=len(manifest.completeness.observed_stages),
            recorder_error_count=len(manifest.completeness.recorder_errors),
            missing_range_count=len(manifest.completeness.missing_ranges),
        )

    @staticmethod
    def _event_metadata(event: EventEnvelopeV1) -> ReplayEventMetadata:
        return ReplayEventMetadata(
            sequence=event.sequence,
            event_id=event.event_id,
            event_type=event.type,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            project_id=event.project_id,
            digest=event.digest,
            redaction_count=event.redaction.count,
        )

    @staticmethod
    def _attachment_metadata(attachment: AttachmentV1) -> ReplayAttachmentMetadata:
        return ReplayAttachmentMetadata(
            digest=attachment.digest,
            original_bytes=attachment.original_bytes,
            stored_bytes=attachment.stored_bytes,
            media_type=attachment.media_type,
            encoding=attachment.encoding,
            redaction_count=attachment.redaction_count,
            truncated=attachment.truncated,
        )

    def verify(self, run_id: str, *, project_id: str | None) -> ReplayVerification:
        """Return a sanitized integrity verdict for one authorized run."""
        try:
            verification = self._verify(run_id, project_id=project_id)
        except Exception:
            self._telemetry.operation("verify", "failure")
            raise
        self._telemetry.operation(
            "verify", "success" if verification.valid else "failure"
        )
        return verification

    def _verify(self, run_id: str, *, project_id: str | None) -> ReplayVerification:
        """Verify and audit without duplicating the public operation outcome."""
        entry = self._lookup(run_id, project_id, "replay:read")
        verification = self._verification(self._store.verify(entry.run_id))
        self._emit_audit(
            "verify",
            "success" if verification.valid else "failure",
            verification.reason,
            run_id=entry.run_id,
            project_id=entry.project_id,
        )
        return verification

    def show(self, run_id: str, *, project_id: str | None) -> ReplayDetail:
        """Return safe bundle metadata, never event payload or attachment bytes."""
        try:
            detail = self._show(run_id, project_id=project_id)
        except Exception:
            self._telemetry.operation("show", "failure")
            raise
        self._telemetry.operation(
            "show", "success" if detail.verification.valid else "failure"
        )
        return detail

    def _show(self, run_id: str, *, project_id: str | None) -> ReplayDetail:
        """Construct and audit a complete detail response before its outcome."""
        entry = self._lookup(run_id, project_id, "replay:read")
        verification = self._verification(self._store.verify(entry.run_id))
        verified_bundle: VerifiedBundle | None = None
        if verification.valid and verification.complete:
            try:
                verified_bundle = self._store.read_verified(entry.run_id)
            except ReplayIntegrityError:
                verification = self._verification(self._store.verify(entry.run_id))
        if (
            verified_bundle is not None
            and verified_bundle.manifest.project_id != entry.project_id
        ):
            raise ReplayNotFoundError("replay not found")

        if verified_bundle is None:
            detail = ReplayDetail(
                run_id=entry.run_id,
                project_id=entry.project_id,
                verification=verification,
                manifest=None,
                events=(),
                attachments=(),
            )
        else:
            manifest = verified_bundle.manifest
            detail = ReplayDetail(
                run_id=entry.run_id,
                project_id=entry.project_id,
                verification=verification,
                manifest=self._manifest_metadata(manifest),
                events=tuple(
                    self._event_metadata(event) for event in verified_bundle.events
                ),
                attachments=tuple(
                    self._attachment_metadata(attachment)
                    for attachment in manifest.attachments
                ),
            )
        self._emit_audit(
            "show",
            "success" if verification.valid else "failure",
            verification.reason,
            run_id=entry.run_id,
            project_id=entry.project_id,
        )
        return detail

    @staticmethod
    def _zip_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return info

    @staticmethod
    def _safe_export_path(bundle_path: Path, relative: Path) -> Path:
        bundle_root = bundle_path.resolve(strict=True)
        candidate = bundle_path / relative
        current = bundle_path
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ReplayExportError("replay export unavailable")
        try:
            candidate.resolve(strict=True).relative_to(bundle_root)
        except (FileNotFoundError, ValueError) as exc:
            raise ReplayExportError("replay export unavailable") from exc
        if not candidate.is_file():
            raise ReplayExportError("replay export unavailable")
        return candidate

    @classmethod
    def _write_attachment(
        cls,
        archive: zipfile.ZipFile,
        bundle_path: Path,
        attachment: AttachmentV1,
        *,
        chunk_size: int,
    ) -> None:
        digest_hex = attachment.digest.removeprefix("sha256:")
        name = f"attachments/sha256-{digest_hex}"
        source_path = cls._safe_export_path(bundle_path, Path(name))
        digest = hashlib.sha256()
        total = 0
        with source_path.open("rb") as source, archive.open(
            cls._zip_info(name), "w", force_zip64=True
        ) as target:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
                total += len(chunk)
                target.write(chunk)
        if total != attachment.stored_bytes or not hmac.compare_digest(
            f"sha256:{digest.hexdigest()}", attachment.digest
        ):
            raise ReplayExportError("replay export unavailable")

    @classmethod
    def _write_small_entry(
        cls,
        archive: zipfile.ZipFile,
        name: str,
        content: bytes,
    ) -> None:
        with archive.open(cls._zip_info(name), "w", force_zip64=True) as target:
            target.write(content)

    def _export_iterator(
        self,
        entry: ReplayCatalogEntry,
        bundle: VerifiedBundle,
        *,
        chunk_size: int,
    ) -> Iterator[bytes]:
        try:
            bundle_path = self._store.bundle_path(entry.run_id)
            with tempfile.TemporaryFile(mode="w+b") as spool:
                with zipfile.ZipFile(
                    spool,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    allowZip64=True,
                ) as archive:
                    for attachment in sorted(
                        bundle.manifest.attachments,
                        key=lambda item: item.digest,
                    ):
                        self._write_attachment(
                            archive,
                            bundle_path,
                            attachment,
                            chunk_size=chunk_size,
                        )
                    for event in bundle.events:
                        self._write_small_entry(
                            archive,
                            f"events/{event.sequence:012d}.json",
                            canonical_replay_json(event).encode("utf-8"),
                        )
                    if bundle.manifest.integrity == "signed":
                        hmac_path = self._safe_export_path(
                            bundle_path, Path("manifest.hmac")
                        )
                        manifest_hmac = hmac_path.read_bytes()
                        if len(manifest_hmac) != 64:
                            raise ReplayExportError("replay export unavailable")
                        self._write_small_entry(
                            archive,
                            "manifest.hmac",
                            manifest_hmac,
                        )
                    self._write_small_entry(
                        archive,
                        "manifest.json",
                        canonical_replay_json(bundle.manifest).encode("utf-8"),
                    )

                final_verdict = self._store.verify(entry.run_id)
                if not final_verdict.valid or not final_verdict.complete:
                    raise ReplayExportError("replay export unavailable")
                self._emit_audit(
                    "export",
                    "success",
                    "verified",
                    run_id=entry.run_id,
                    project_id=entry.project_id,
                )
                spool.seek(0)
                while chunk := spool.read(chunk_size):
                    yield chunk
        except ReplayAuditError:
            self._telemetry.operation("export", "failure")
            raise
        except Exception as exc:
            self._telemetry.operation("export", "failure")
            self._emit_audit(
                "export",
                "failure",
                "integrity_failure",
                run_id=entry.run_id,
                project_id=entry.project_id,
            )
            if isinstance(exc, ReplayExportError):
                raise
            raise ReplayExportError("replay export unavailable") from exc
        else:
            self._telemetry.operation("export", "success")

    def stream_export(
        self,
        run_id: str,
        *,
        project_id: str | None,
        chunk_size: int = 65_536,
    ) -> Iterator[bytes]:
        """Return a verified ZIP byte stream without accepting an output path."""
        try:
            return self._stream_export(
                run_id,
                project_id=project_id,
                chunk_size=chunk_size,
            )
        except Exception:
            self._telemetry.operation("export", "failure")
            raise

    def _stream_export(
        self,
        run_id: str,
        *,
        project_id: str | None,
        chunk_size: int,
    ) -> Iterator[bytes]:
        """Validate an export before handing outcome tracking to its iterator."""
        if (
            not isinstance(chunk_size, int)
            or isinstance(chunk_size, bool)
            or not _MIN_EXPORT_CHUNK <= chunk_size <= _MAX_EXPORT_CHUNK
        ):
            raise ValueError(
                f"chunk_size must be between {_MIN_EXPORT_CHUNK} and "
                f"{_MAX_EXPORT_CHUNK} bytes"
            )
        entry = self._lookup(run_id, project_id, "replay:export")
        try:
            bundle = self._store.read_verified(entry.run_id)
        except ReplayIntegrityError as exc:
            self._emit_audit(
                "export",
                "failure",
                "integrity_failure",
                run_id=entry.run_id,
                project_id=entry.project_id,
            )
            raise ReplayExportError("replay export unavailable") from exc
        if bundle.manifest.project_id != entry.project_id:
            raise ReplayNotFoundError("replay not found")
        return self._export_iterator(entry, bundle, chunk_size=chunk_size)


__all__ = [
    "ReplayAccessDeniedError",
    "ReplayAttachmentMetadata",
    "ReplayAuditError",
    "ReplayAuditEvent",
    "ReplayAuditSink",
    "ReplayAuthorizer",
    "ReplayCapability",
    "ReplayCatalog",
    "ReplayCatalogEntry",
    "ReplayCatalogError",
    "ReplayCursorError",
    "ReplayDetail",
    "ReplayEventMetadata",
    "ReplayExportError",
    "ReplayManifestMetadata",
    "ReplayNotFoundError",
    "ReplayPage",
    "ReplayService",
    "ReplayServiceError",
    "ReplaySummary",
    "ReplayVerification",
]
