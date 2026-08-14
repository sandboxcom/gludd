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
    :class:`IntegrityError` rather than returning possibly-tampered state.  A
    ``.keyed`` marker (written the instant a key is first minted) records that
    the store was ever keyed: once it — or a signed state file — exists, a later
    inability to load the key (deleted/insecure/unreadable keyfile) raises
    :class:`IntegrityError` rather than silently degrading or re-minting a key
    over signed state.  Degraded (unverified) operation is therefore reachable
    ONLY on a genuinely fresh store that has never held a key.
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


class PauseStoreIntegrityError(PauseStoreError):
    """Raised when the on-disk pause state fails its MAC (tampered/corrupted)."""


# Compatibility alias retained for callers of the pre-beta public contract.
IntegrityError = PauseStoreIntegrityError


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
    # Sentinel proving this store was once keyed.  Written the moment a durable
    # key is first minted; its (or a MAC sidecar's) presence forbids a later
    # silent degrade / key re-mint over previously-signed state (fail closed).
    _MARKER_NAME = ".keyed"
    # Domain-separation label mixed into every signed message so a pause blob
    # can never be replayed as (or forged from) some other HMAC context.
    _MAC_CONTEXT = b"general_ludd.pause_store.v1\x00"
    # Hard cap on the state payload we will read before json.loads (DoS guard).
    # Pause records are a handful of small dicts; 1 MiB is already absurdly big.
    _MAX_STATE_BYTES = 1_048_576

    def __init__(self, base_dir: str | Path = "") -> None:
        """Open a pause-state store and establish its integrity key.

        Args:
            base_dir: Private state directory. An empty value selects the
                platform-specific default.

        Raises:
            PauseStoreIntegrityError: If prior signed state exists but its key
                cannot be loaded securely.
        """
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
        self._keyed_marker = self._base / self._MARKER_NAME
        self._lock = threading.Lock()
        # A durable key -> integrity is ENFORCED on load.  None (degraded mode,
        # e.g. an unwritable secrets dir) -> operate without verification.
        self._mac_key: bytes | None = self._load_or_create_key()

    @property
    def base_dir(self) -> Path:
        """Return the resolved directory containing pause state and MAC data."""
        return self._base

    @property
    def has_durable_key(self) -> bool:
        """True when a durable MAC key is established (integrity enforced)."""
        return self._mac_key is not None

    # ------------------------------------------------------------------
    # Durable MAC key
    # ------------------------------------------------------------------

    def _prior_signing_evidence(self) -> bool:
        """True when this store was PREVIOUSLY keyed / signed.

        The ``.keyed`` marker is written the instant a durable key is first
        minted; a MAC sidecar is a backstop signal that state was signed even if
        the marker was removed.  Unsigned degraded-mode state (no marker, no
        MAC) is deliberately *not* counted, so a store that has genuinely never
        held a key can still start cleanly in degraded mode.
        """
        return self._keyed_marker.exists() or self._mac_path.exists()

    def _assert_keyfile_secure(self) -> None:
        """Refuse a group/world-accessible or wrong-owner keyfile (M-a).

        A MAC key readable by other users is no longer a secret; loading state
        signed with it would be security theatre.  Fail closed instead.
        """
        st = os.stat(self._key_path)
        mode = st.st_mode & 0o777
        if mode & 0o077:
            raise IntegrityError(
                f"pause MAC keyfile {self._key_path} is group/world accessible "
                f"(mode {oct(mode)}); refusing to use it (fail closed)."
            )
        getuid = getattr(os, "getuid", None)
        if getuid is not None and st.st_uid != getuid():
            raise IntegrityError(
                f"pause MAC keyfile {self._key_path} is owned by uid "
                f"{st.st_uid}, not the current user {getuid()}; refusing "
                "(fail closed)."
            )

    def _read_keyfile_bytes(self) -> bytes:
        """Read the raw key bytes (a seam so tests can inject an OSError)."""
        return self._key_path.read_bytes()

    def _ensure_marker(self) -> None:
        """Best-effort create the ``.keyed`` sentinel (idempotent)."""
        if self._keyed_marker.exists():
            return
        with contextlib.suppress(OSError):
            tmp = self._keyed_marker.with_name(self._keyed_marker.name + ".tmp")
            tmp.write_text("keyed\n", encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            tmp.replace(self._keyed_marker)

    def _mint_key(self) -> bytes:
        """Mint, atomically persist (0o600), and mark a fresh durable key."""
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._secrets_dir, 0o700)
        key = secrets.token_bytes(32)
        tmp = self._key_path.with_name(self._key_path.name + ".tmp")
        tmp.write_bytes(key)
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(self._key_path)
        self._ensure_marker()
        return key

    def _load_or_create_key(self) -> bytes | None:
        """Read the durable MAC key, minting one only on a truly fresh store.

        Fail-closed rules (SLICE 1 audit H1/H2):

          * If the store was previously keyed/signed
            (:meth:`_prior_signing_evidence`) but the keyfile is missing, empty,
            insecure, or unreadable, raise :class:`IntegrityError` — NEVER
            silently degrade or mint a replacement key over signed state.  A
            silent re-mint would rotate the key underneath existing signatures
            (false-tamper) or launder attacker-supplied state on the next save.
          * Degraded mode (return ``None``, operate without verification) is
            reachable ONLY when there is no prior signing evidence, i.e. a store
            that has genuinely never held a key and whose ``secrets/`` dir
            cannot be established.
        """
        prior_signed = self._prior_signing_evidence()
        try:
            if self._key_path.exists():
                self._assert_keyfile_secure()  # M-a
                data = self._read_keyfile_bytes()
                if data:
                    self._ensure_marker()  # forward-fill for pre-marker stores
                    return data
                # An empty keyfile is corrupt.  Re-minting would invalidate any
                # state the real (now-lost) key signed -> fail closed.
                if prior_signed:
                    raise IntegrityError(
                        f"pause MAC keyfile {self._key_path} is empty/corrupt "
                        "but prior signed state exists; refusing to re-mint "
                        "(fail closed)."
                    )
                logger.warning(
                    "PauseStore: empty MAC keyfile at %s; regenerating.",
                    self._key_path,
                )
            elif prior_signed:
                # H2: keyfile gone but a keyed marker / MAC sidecar remains.
                raise IntegrityError(
                    f"pause MAC keyfile {self._key_path} is missing but a "
                    "prior keyed/signed state exists; refusing to mint a "
                    "replacement key (fail closed)."
                )
            # No prior signing evidence: a genuinely fresh store -> mint a key.
            return self._mint_key()
        except IntegrityError:
            raise
        except OSError as exc:
            # H1: an OSError establishing the key for a PREVIOUSLY-keyed store
            # must not silently degrade into unverified operation.
            if prior_signed:
                raise IntegrityError(
                    "PauseStore: could not load the durable MAC key for a "
                    f"previously-keyed store ({exc}); refusing to operate "
                    "without integrity verification (fail closed)."
                ) from exc
            logger.warning(
                "PauseStore: could not establish a durable MAC key (%s); "
                "operating in DEGRADED mode without integrity verification.",
                exc,
            )
            return None

    def _mac(self, payload: str) -> str:
        assert self._mac_key is not None  # only called when a key exists
        # Domain-separate the signed message (M-c) so a pause payload can never
        # be replayed as some other context's HMAC input.
        msg = self._MAC_CONTEXT + payload.encode("utf-8")
        return hmac.new(self._mac_key, msg, sha256).hexdigest()

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
            # M-b: bound the read BEFORE parsing so an oversized (possibly
            # hostile) file cannot exhaust memory in read_text / json.loads.
            try:
                size = self._state_path.stat().st_size
            except OSError as exc:
                raise PauseStoreError(f"pause state unreadable: {exc}") from exc
            if size > self._MAX_STATE_BYTES:
                raise IntegrityError(
                    f"pause state file is {size} bytes, exceeding the "
                    f"{self._MAX_STATE_BYTES}-byte cap; refusing to parse "
                    "(DoS guard)."
                )
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
