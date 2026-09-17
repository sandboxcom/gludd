"""Durable JSON file-backed capability evidence store.

Persists CapabilityEvidence records across daemon restarts so that
evidence gathered by the local evaluation suite survives process
restarts and is available to the policy without re-evaluation.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from contextlib import suppress
from typing import Any

from general_ludd.routing_roles.small_model_policy import (
    _IDENTIFIER_RE,
    _TASK_KIND_RE,
    DEFAULT_TASK_CONTRACTS,
    CapabilityEvidence,
)
from general_ludd.schemas.benchmark import TaskRole, TaskType

_MAX_EVIDENCE_BYTES = 67_108_864


class CapabilityEvidenceStore:
    """JSON file-backed store for capability evaluation evidence.

    Thread-safe.  Each write atomically replaces the file so a crash
    mid-write leaves the prior state intact.  Records are stored as
    plain dicts with a ``registered_at`` epoch-float timestamp.
    """

    def __init__(self, path: str) -> None:
        """Open or initialize the evidence store at an absolute path."""
        self._path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        try:
            metadata = os.lstat(self._path)
        except FileNotFoundError:
            self._records = []
            self._save()
            return
        except OSError:
            raise ValueError("evidence store is unavailable") from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("evidence store must be a regular non-symlink file")
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._path, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (metadata.st_dev, metadata.st_ino)
                    or opened.st_size > _MAX_EVIDENCE_BYTES
                ):
                    raise ValueError("evidence store is not one bounded regular file")
                raw = os.read(descriptor, opened.st_size + 1).decode("utf-8")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            if not raw.strip():
                self._records = []
                self._save()
                return
            loaded = json.loads(raw)
            if not isinstance(loaded, list) or any(
                not isinstance(record, dict) for record in loaded
            ):
                raise ValueError
            self._records = loaded
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            raise ValueError("evidence store must contain a valid JSON list") from None

    def _save(self) -> None:
        directory = os.path.dirname(self._path)
        descriptor = -1
        temporary_path = ""
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                dir=directory,
                prefix=f".{os.path.basename(self._path)}.",
                suffix=".tmp",
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(self._records, stream, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                current = os.lstat(self._path)
            except FileNotFoundError:
                current = None
            if current is not None and (
                stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode)
            ):
                raise ValueError("evidence store must be a regular non-symlink file")
            os.replace(temporary_path, self._path)
            temporary_path = ""
        except (OSError, TypeError, ValueError):
            raise ValueError("evidence store could not be saved safely") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_path)

    # -- public API ---------------------------------------------------------

    def register_evidence(self, evidence: CapabilityEvidence | dict[str, Any]) -> int:
        """Store a capability-evidence record.  Returns the new record count."""
        if isinstance(evidence, CapabilityEvidence):
            record: dict[str, Any] = {
                "model_profile_id": evidence.model_profile_id,
                "model_identity_digest": evidence.model_identity_digest,
                "task_kind": evidence.task_kind,
                "role": evidence.role.value,
                "collection": evidence.collection,
                "suite_id": evidence.suite_id,
                "suite_revision": evidence.suite_revision,
                "acceptance_contract_digest": evidence.acceptance_contract_digest,
                "passed_cases": evidence.passed_cases,
                "total_cases": evidence.total_cases,
                "collection_ok": evidence.collection_ok,
                "local_only": evidence.local_only,
                "evidence_digest": evidence.evidence_digest,
            }
        else:
            record = dict(evidence)
        record.setdefault("registered_at", time.time())
        with self._lock:
            self._records.append(record)
            self._save()
            return len(self._records)

    def query_by_model(self, model_profile_id: str) -> list[dict[str, Any]]:
        """Return every record for *model_profile_id*."""
        if not isinstance(model_profile_id, str) or _IDENTIFIER_RE.fullmatch(model_profile_id) is None:
            raise ValueError("model_profile_id has an invalid format")
        with self._lock:
            return [dict(r) for r in self._records if r.get("model_profile_id") == model_profile_id]

    def query_by_task_kind(self, task_kind: str) -> list[dict[str, Any]]:
        """Return every record for *task_kind* across all models."""
        if not isinstance(task_kind, str) or _TASK_KIND_RE.fullmatch(task_kind) is None:
            raise ValueError("task_kind has an invalid format")
        with self._lock:
            return [dict(r) for r in self._records if r.get("task_kind") == task_kind]

    def query_by_task_shape(
        self,
        task_type: TaskType,
        task_kind: str,
        *,
        model_profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return evidence for one exact existing task-type/contract shape.

        Legacy records without task_type never match. This makes a success for
        feature coding evidence unavailable to bug-fix coding selection.
        """
        if not isinstance(task_type, TaskType):
            raise ValueError("task_type must be a TaskType value")
        if task_kind not in DEFAULT_TASK_CONTRACTS:
            raise ValueError("task_kind must name a default task contract")
        if (
            model_profile_id is not None
            and (
                not isinstance(model_profile_id, str)
                or _IDENTIFIER_RE.fullmatch(model_profile_id) is None
            )
        ):
            raise ValueError("model_profile_id has an invalid format")
        with self._lock:
            return [
                dict(record)
                for record in self._records
                if record.get("task_type") == task_type.value
                and record.get("task_kind") == task_kind
                and (
                    model_profile_id is None
                    or record.get("model_profile_id") == model_profile_id
                )
            ]

    def list_all(self) -> list[dict[str, Any]]:
        """Return a shallow copy of every stored record."""
        with self._lock:
            return [dict(r) for r in self._records]

    def expire_stale(self, max_age_seconds: float) -> tuple[int, int]:
        """Remove records older than *max_age_seconds*.

        Returns ``(remained, removed)``.
        """
        cutoff = time.time() - max_age_seconds
        with self._lock:
            kept = [r for r in self._records if r.get("registered_at", 0.0) >= cutoff]
            removed = len(self._records) - len(kept)
            self._records = kept
            if removed:
                self._save()
            return len(kept), removed

    def load_evidence_for_identity(self, model_profile_id: str, model_identity_digest: str) -> list[CapabilityEvidence]:
        """Hydrate stored dict records into ``CapabilityEvidence`` objects.

        Only returns evidence whose ``model_identity_digest`` matches
        the current identity fingerprint, so a weight artifact change
        invalidates stale cached evidence.
        """
        raw_records = self.query_by_model(model_profile_id)
        results: list[CapabilityEvidence] = []
        for rec in raw_records:
            if rec.get("model_identity_digest") != model_identity_digest:
                continue
            try:
                role = rec["role"]
                if isinstance(role, str):
                    role = TaskRole(role)
                results.append(
                    CapabilityEvidence(
                        model_profile_id=rec["model_profile_id"],
                        model_identity_digest=rec["model_identity_digest"],
                        task_kind=rec["task_kind"],
                        role=role,
                        collection=rec["collection"],
                        suite_id=rec["suite_id"],
                        suite_revision=rec["suite_revision"],
                        acceptance_contract_digest=rec["acceptance_contract_digest"],
                        passed_cases=rec["passed_cases"],
                        total_cases=rec["total_cases"],
                        collection_ok=rec["collection_ok"],
                        local_only=rec["local_only"],
                        evidence_digest=rec["evidence_digest"],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results


__all__ = ["CapabilityEvidenceStore"]
