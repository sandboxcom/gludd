"""Durable, integrity-checked persistence for pause state.

:class:`PauseController` (see :mod:`general_ludd.controllers.pause_controller`)
needs its paused-project / paused-model set to SURVIVE a daemon restart — a
pause that silently forgets itself across a restart would let paused work
resume behind the operator's back.  This module is that persistence layer.

Storage model (mirrors ``agents/hibernation.HibernationStore`` and
``models/response_cache`` disk hygiene):

  * Records are a ``list[dict]`` serialized as pydantic-free JSON (the caller —
    :class:`PauseController` — validates them into models on load).  Never
    pickle: a tampered file must not execute code.
  * The write is atomic — temp file + :func:`os.replace` — so a crash mid-write
    can never leave a half-written state file.  The payload and its MAC sidecar
    are both written ``0o600`` (owner-only) before the swap.
  * Integrity is a **keyed** HMAC-SHA256 over the payload.  Unlike the
    hibernation store — whose MAC key is per-process ephemeral and therefore
    NOT portable across a restart — this store derives its key from a DURABLE
    keyfile (``<base>/secrets/pause_mac.key``, created ``0o600`` on first use).
    A durable key is what lets a *fresh* store instance re-verify state written
    by a previous process: restart survival with tamper detection intact.
  * Fail-closed posture (mirrors the secret scanner / hibernation hydrate): when
    a key exists, a missing or mismatching MAC on load raises
    :class:`IntegrityError` rather than returning possibly-tampered state.  Only
    when no durable key could be established (degraded mode) does the store
    operate without integrity verification, tolerating the loss the way
    hibernation tolerates an absent snapshot.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import secrets
import threading
from hashlib import sha256
from pathlib import Path

logger = logging.getLogger(__name__)


class PauseStoreError(RuntimeError):
    """Raised when pause state cannot be safely written or read."""


class IntegrityError(PauseStoreError):
    """Raised when the on-disk pause state fails its MAC (tampered/corrupted)."""


def default_pause_dir() -> Path:
    """Resolve the default pause-state directory (env -> XDG -> default).

    Mirrors ``agents.hibernation.default_hibernation_dir``: an explicit
    ``GLUDD_PAUSE_DIR`` wins, else ``$XDG_DATA_HOME`` (fallback
    ``~/.local/share``) ``/general-ludd/pause``.
    """
    override = os.environ.get("GLUDD_PAUSE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(xdg) / "general-ludd" / "pause"


class PauseStore:
    """Persist a list of pause records to a durably-signed JSON file.

    Layout under *base_dir* (defaults to :func:`default_pause_dir`):

        <base>/pause_state.json        — the JSON payload (list of record dicts)
        <base>/pause_state.json.mac    — hex HMAC-SHA256 of the payload
        <base>/secrets/pause_mac.key   — durable 32-byte MAC key (0o600)

    A read-modify-write from :class:`PauseController` is guarded by an internal
    :class:`threading.Lock` so concurrent saves/loads cannot interleave.
    """

    _STATE_NAME = "pause_state.json"
    _KEY_NAME = "pause_mac.key"

    def __init__(self, base_dir: str | Path = "") -> None:
        # An empty base_dir means "use the default"; ``Path("")`` would resolve
        # to the CWD, which is never what we want.
        self._base = (
            Path(base_dir).resolve() if base_dir else default_pause_dir().resolve()
        )
        self._base.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._base, 0o700)
        self._state_path = self._base / self._STATE_NAME
        self._mac_path = self._base / f"{self._STATE_NAME}.mac"
        self._secrets_dir = self._base / "secrets"
        self._key_path = self._secrets_dir / self._KEY_NAME
        self._lock = threading.Lock()
        # A durable key -> integrity is ENFORCED on load.  None (degraded mode,
        # e.g. an unwritable secrets dir) -> operate without verification.
        self._mac_key: bytes | None = self._load_or_create_key()

    @property
    def base_dir(self) -> Path:
        return self._base

    @property
    def has_durable_key(self) -> bool:
        """True when a durable MAC key is established (integrity enforced)."""
        return self._mac_key is not None

    # ------------------------------------------------------------------
    # Durable MAC key
    # ------------------------------------------------------------------

    def _load_or_create_key(self) -> bytes | None:
        """Read the durable MAC key, minting one on first use.

        The key is written atomically ``0o600`` under an owner-only ``secrets/``
        dir.  If the key can neither be read nor created (permission/OS error),
        return ``None`` so the store degrades to unverified operation rather
        than crashing — mirroring hibernation's tolerance of missing snapshots.
        """
        try:
            if self._key_path.exists():
                data = self._key_path.read_bytes()
                if data:
                    return data
                # An empty keyfile is corrupt; fall through to mint a new one.
                logger.warning(
                    "PauseStore: empty MAC keyfile at %s; regenerating.",
                    self._key_path,
                )
            self._secrets_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(self._secrets_dir, 0o700)
            key = secrets.token_bytes(32)
            tmp = self._key_path.with_name(self._key_path.name + ".tmp")
            tmp.write_bytes(key)
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            tmp.replace(self._key_path)
            return key
        except OSError as exc:
            logger.warning(
                "PauseStore: could not establish a durable MAC key (%s); "
                "operating in DEGRADED mode without integrity verification.",
                exc,
            )
            return None

    def _mac(self, payload: str) -> str:
        assert self._mac_key is not None  # only called when a key exists
        return hmac.new(self._mac_key, payload.encode("utf-8"), sha256).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write *text* to *path* atomically, owner-only (0o600)."""
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise PauseStoreError(f"failed to write {path.name}: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, records: list[dict[str, object]]) -> None:
        """Persist *records* as signed JSON (atomic, owner-only).

        The payload is written first, then its MAC sidecar — both via temp file
        + :func:`os.replace`.  In degraded mode (no durable key) any stale MAC
        sidecar is removed so a later key never validates against an old MAC.
        """
        payload = json.dumps(list(records), sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._atomic_write(self._state_path, payload)
            if self._mac_key is not None:
                self._atomic_write(self._mac_path, self._mac(payload))
            else:
                with contextlib.suppress(OSError):
                    self._mac_path.unlink()

    def load(self) -> list[dict[str, object]]:
        """Read and integrity-check the pause records.

        Returns ``[]`` when no state file exists yet.  When a durable key is
        established, an absent or mismatching MAC raises :class:`IntegrityError`
        (fail closed — the state may have been tampered with).  In degraded mode
        the payload is returned without verification.
        """
        with self._lock:
            if not self._state_path.exists():
                return []
            try:
                payload = self._state_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PauseStoreError(f"pause state unreadable: {exc}") from exc
            if self._mac_key is not None:
                self._verify(payload)
            return self._decode(payload)

    def _verify(self, payload: str) -> None:
        if not self._mac_path.exists():
            raise IntegrityError(
                "pause state has no MAC sidecar but a durable key is set — "
                "refusing possibly-tampered state (fail closed)."
            )
        try:
            stored = self._mac_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PauseStoreError(f"pause MAC unreadable: {exc}") from exc
        actual = self._mac(payload)
        # Constant-time compare: the MAC is keyed, so ``!=`` would leak the key
        # via a timing side channel.
        if not hmac.compare_digest(actual, stored):
            raise IntegrityError(
                "pause state MAC mismatch: file was tampered with or corrupted."
            )

    @staticmethod
    def _decode(payload: str) -> list[dict[str, object]]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IntegrityError("pause state is not valid JSON.") from exc
        if not isinstance(data, list):
            raise IntegrityError("pause state payload is not a JSON list.")
        out: list[dict[str, object]] = []
        for item in data:
            if not isinstance(item, dict):
                raise IntegrityError("pause state record is not a JSON object.")
            out.append(item)
        return out
