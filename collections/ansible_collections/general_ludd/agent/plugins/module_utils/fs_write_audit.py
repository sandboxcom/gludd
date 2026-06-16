"""
FIM-on-write audit log for general_ludd.agent write-capable modules.

This module_util layers **file-integrity monitoring (FIM)** on top of the
existing :class:`fs_write_policy.WritePolicy`.  Where ``WritePolicy`` answers
*"is this path allowed to be written?"*, :class:`WriteAuditLog` answers the
follow-on questions:

* **Auditability** — every write to an allowed path records a manifest entry
  (resolved path, sha256 of the bytes that were written, ISO timestamp) so the
  set of writes a module performed is reconstructable after the fact.
* **Tamper detection** — before a *subsequent* write to a path we have already
  recorded, the file's current on-disk hash is compared against the post-hash
  we recorded for the previous write.  A mismatch means the file changed
  out-of-band between our writes (an unexpected external/adversarial
  modification) and is surfaced as :class:`IntegrityViolation`.

Composition with the policy (fail-closed)
------------------------------------------
:class:`WriteAuditLog` *wraps* a :class:`WritePolicy` and never bypasses it:

* The **audited path** is always run through ``policy.check()`` first, so every
  injection / encoding / NUL / ``..`` traversal / symlink-escape / sensitive
  target vector the policy rejects is still rejected here — FIM cannot be used
  to smuggle a write past the allow/deny policy.
* The **manifest file itself** is a write target, so it too is run through
  ``policy.check()``.  The manifest can never be parked outside the allowed
  roots (e.g. ``/etc/cron.d/manifest``), even though the audit layer is the one
  writing it.

Hashing parity
--------------
The post-hash is ``hashlib.sha256(<bytes on disk>).hexdigest()`` — byte-for-byte
the same construction the daemon-side
``general_ludd.integrity.scanner.FileIntegrityScanner._hash_file`` uses — so a
manifest written here is directly comparable to a baseline produced by the
daemon's scanner.  We deliberately re-implement the one-line hash with the
stdlib only (rather than importing ``general_ludd``) because this util runs
inside Ansible module execution on a managed node where the daemon package is
not guaranteed to be importable, matching the constraint in ``gludd.py`` /
``fs_write_policy.py``.

Usage in a module
-----------------
    from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_audit import (
        WriteAuditLog,
        IntegrityViolation,
    )

    audit = WriteAuditLog(policy=policy, manifest_path=manifest)
    try:
        audit.pre_write_check(target)      # fail-closed: policy + tamper
        # ... module performs the actual write to `target` ...
        audit.record_write(target)         # hash + manifest the result
    except (WritePolicyError, IntegrityViolation) as exc:
        module.fail_json(**error_result(f"audited write denied: {exc}"))
        return
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

# Import the policy via the collection path when available, else by sibling
# filename — mirrors the dual-path import the modules themselves use so this
# util works both inside a real collection and under importlib-by-path loading
# in tests.
try:  # pragma: no cover - exercised by both import shapes across environments
    from ansible_collections.general_ludd.agent.plugins.module_utils.fs_write_policy import (  # type: ignore[import]
        WritePolicy,
        WritePolicyError,
    )
except ImportError:  # pragma: no cover
    from fs_write_policy import WritePolicy, WritePolicyError  # type: ignore[import,no-redef]


class IntegrityViolation(Exception):
    """Raised (fail-closed) when a path changed out-of-band since we recorded it.

    Distinct from :class:`fs_write_policy.WritePolicyError`: the policy governs
    *whether* a path may be written; this governs *whether the bytes already on
    disk match what we last wrote there*.  An ``IntegrityViolation`` means an
    unexpected external modification happened between two audited writes.
    """


def hash_bytes_on_disk(path: str) -> str:
    """Return the sha256 hex digest of the file's current contents.

    Empty string if the file does not exist or cannot be read — the same
    fail-soft behaviour as ``FileIntegrityScanner._hash_file`` so a missing
    file is treated as "no recorded bytes" rather than an error.  Callers that
    need a present file must check existence themselves.
    """
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


@dataclass
class AuditEntry:
    """One recorded write: the resolved path, its post-write hash, when."""

    path: str
    sha256: str
    recorded_at: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256, "recorded_at": self.recorded_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        return cls(
            path=str(data.get("path", "")),
            sha256=str(data.get("sha256", "")),
            recorded_at=str(data.get("recorded_at", "")),
        )


@dataclass
class WriteAuditLog:
    """FIM-on-write audit log composed with a :class:`WritePolicy`.

    Parameters
    ----------
    policy:
        The allow/deny policy.  Every audited path *and* the manifest path are
        run through ``policy.check()`` — the audit layer never bypasses it.
    manifest_path:
        Where the JSON manifest is persisted.  Must itself resolve inside an
        allowed root (it is policy-checked on first write).  If empty, the log
        is in-memory only (still tamper-checks, just not durable).
    """

    policy: WritePolicy
    manifest_path: str = ""
    _entries: dict[str, AuditEntry] = field(default_factory=dict, init=False)
    _manifest_checked: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Load any pre-existing manifest so tamper detection survives across
        # separate module invocations that share a manifest path.
        if self.manifest_path and os.path.isfile(self.manifest_path):
            self._load_manifest()

    # -- manifest persistence ----------------------------------------------

    def _check_manifest_target(self) -> str:
        """Run the manifest path itself through the write policy (fail-closed).

        The manifest is a write target like any other; it must land inside an
        allowed root.  Cached after the first successful check so we do not
        re-resolve on every record.
        """
        resolved = self.policy.check(self.manifest_path)
        self._manifest_checked = True
        return resolved

    def _load_manifest(self) -> None:
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        for item in entries:
            if isinstance(item, dict):
                entry = AuditEntry.from_dict(item)
                if entry.path:
                    self._entries[entry.path] = entry

    def _save_manifest(self) -> None:
        if not self.manifest_path:
            return
        # Fail-closed: never write the manifest outside an allowed root.
        self._check_manifest_target()
        payload = {
            "version": 1,
            "entries": [e.to_dict() for e in self._entries.values()],
        }
        parent = os.path.dirname(self.manifest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.manifest_path)

    # -- public API ---------------------------------------------------------

    def pre_write_check(self, path: str) -> str:
        """Validate ``path`` before a write; return its resolved location.

        Two fail-closed gates, in order:

        1. ``policy.check(path)`` — every allow/deny + injection/symlink/NUL/
           traversal/sensitive vector. Raises :class:`WritePolicyError`.
        2. Tamper check — if we have a recorded post-hash for this resolved
           path AND the file currently exists, the on-disk bytes must still
           hash to the recorded value.  A mismatch means the file was modified
           out-of-band since our last write and raises
           :class:`IntegrityViolation`.

        A first write to a not-yet-recorded path passes the tamper gate (there
        is no prior hash to violate).
        """
        resolved = self.policy.check(path)
        recorded = self._entries.get(resolved)
        if recorded is not None and os.path.exists(resolved):
            current = hash_bytes_on_disk(resolved)
            if current != recorded.sha256:
                raise IntegrityViolation(
                    f"out-of-band modification detected for {resolved!r}: "
                    f"recorded post-hash {recorded.sha256[:12]}…, "
                    f"current on-disk hash {current[:12]}… — refusing to "
                    f"overwrite an externally-tampered file"
                )
        return resolved

    def record_write(self, path: str) -> AuditEntry:
        """Hash the file's current bytes and record a manifest entry.

        Call this immediately AFTER the module performs the real write.  The
        recorded hash becomes the baseline that the next ``pre_write_check`` of
        the same path compares against.  The path is re-checked through the
        policy (cheap, and keeps record_write safe even if called directly).
        """
        resolved = self.policy.check(path)
        digest = hash_bytes_on_disk(resolved)
        entry = AuditEntry(
            path=resolved,
            sha256=digest,
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._entries[resolved] = entry
        self._save_manifest()
        return entry

    def audited_write(self, path: str, data: bytes) -> AuditEntry:
        """Convenience: pre-check, write ``data``, then record — one call.

        Composes the full fail-closed sequence so a module can replace a raw
        ``open(path, "wb").write(data)`` with a single auditable call.  Any
        :class:`WritePolicyError` / :class:`IntegrityViolation` propagates and
        no bytes are written when the pre-check fails.
        """
        resolved = self.pre_write_check(path)
        with open(resolved, "wb") as fh:
            fh.write(data)
        return self.record_write(resolved)

    def entry_for(self, path: str) -> AuditEntry | None:
        """Return the recorded entry for ``path`` (resolved), or None."""
        try:
            resolved = self.policy.check(path)
        except WritePolicyError:
            return None
        return self._entries.get(resolved)

    def recorded_paths(self) -> list[str]:
        """Resolved paths that currently have a manifest entry."""
        return list(self._entries.keys())
