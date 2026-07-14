"""C.5 Integrity store — HMAC canonical-JSON baseline, fail-closed on corrupt store.

A general-purpose integrity-checked key-value store. Every payload is serialized as
canonical (deterministically-key-ordered) JSON, signed with HMAC-SHA256, and
verified on load. Any integrity issue (tampered payload, missing/wrong MAC,
truncated data) raises :class:`IntegrityError` — fail-closed, no silent degradation.

When no key is provided the store operates in unkeyed mode without integrity
guarantees (for testing / bootstrapping scenarios).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path


class IntegrityError(RuntimeError):
    """Raised when on-disk data fails its MAC (tampered, truncated, or corrupted)."""


def canonical_json(data: object) -> str:
    """Serialize *data* as deterministically-key-ordered, compact JSON.

    Uses ``sort_keys=True`` (recursive key ordering) and ``separators=(",", ":")``
    (no whitespace) so the output is byte-for-byte identical for logically-equal
    input regardless of dict insertion order.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class IntegrityStore:
    """Durable, HMAC-signed key-value store with canonical-JSON serialization.

    Layout under *base_dir* (defaults to a ``.integrity`` directory in CWD)::

        <base>/<name>.json        — canonical JSON payload
        <base>/<name>.json.mac    — hex HMAC-SHA256 of the payload

    When *key* is ``None``, the store operates without signing/verification
    (degraded mode).  When *key* is provided, every ``save`` writes a MAC sidecar
    and every ``load`` verifies it, raising :class:`IntegrityError` on any mismatch.
    """

    _MAC_CONTEXT: bytes = b"general_ludd.integrity_store.v1\x00"

    def __init__(
        self,
        base_dir: str = "",
        key: bytes | None = None,
        domain: bytes = b"",
    ) -> None:
        self._key = key
        self._domain = domain
        if base_dir:
            self._base = Path(base_dir)
        else:
            self._base = Path(".integrity")
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def has_key(self) -> bool:
        return self._key is not None

    def sign(self, data: object) -> str:
        if self._key is None:
            raise IntegrityError("Cannot sign without a key.")
        payload = canonical_json(data)
        msg = self._MAC_CONTEXT + self._domain + payload.encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def verify(self, data: object, stored_mac: str) -> None:
        expected = self.sign(data)
        if not hmac.compare_digest(expected, stored_mac):
            raise IntegrityError(
                "integrity store MAC mismatch: data was tampered with or corrupted."
            )

    def _payload_path(self, name: str) -> Path:
        return self._base / f"{name}.json"

    def _mac_path(self, name: str) -> Path:
        return self._base / f"{name}.json.mac"

    def _atomic_write(self, path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    def save(self, name: str, data: object) -> str:
        payload = canonical_json(data)
        self._atomic_write(self._payload_path(name), payload)
        if self._key is not None:
            mac = self.sign(data)
            self._atomic_write(self._mac_path(name), mac)
        else:
            with contextlib.suppress(OSError):
                self._mac_path(name).unlink()
        return name

    def load(self, name: str) -> object:
        payload_path = self._payload_path(name)
        mac_path = self._mac_path(name)

        if not payload_path.exists():
            raise IntegrityError(
                f"integrity store payload missing ({payload_path}); cannot verify."
            )

        try:
            payload = payload_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IntegrityError(
                f"integrity store payload unreadable ({payload_path}): {exc}"
            ) from exc

        if self._key is not None:
            if not mac_path.exists():
                raise IntegrityError(
                    f"integrity store MAC sidecar missing ({mac_path}); "
                    "cannot verify payload (fail-closed)."
                )
            try:
                stored_mac = mac_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise IntegrityError(
                    f"integrity store MAC unreadable ({mac_path}): {exc}"
                ) from exc
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise IntegrityError(
                    f"integrity store payload is not valid JSON ({payload_path}): {exc}"
                ) from exc
            self.verify(data, stored_mac)

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IntegrityError(
                f"integrity store payload is not valid JSON ({payload_path}): {exc}"
            ) from exc
