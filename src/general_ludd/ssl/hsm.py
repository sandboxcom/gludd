"""HSM (hardware security module) session abstraction and mock backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class HSMConfig:
    """Connection settings for a PKCS#11 HSM."""

    module_path: str
    slot_id: int
    pin: str | None = None
    label: str = ""
    token_label: str = ""


@dataclass
class HSMKey:
    """Metadata for a key held in the HSM."""

    key_id: str
    label: str
    key_type: str
    key_size: int
    algorithm: str
    capabilities: list[str] = field(default_factory=list)
    created_at: str | None = None


class HSMSession(Protocol):
    """Minimal HSM session surface shared by real and mock backends."""

    def list_keys(self) -> list[HSMKey]:
        """List keys available in the session."""

    def sign(self, key_id: str, data: bytes, mechanism: str = "SHA256-RSA-PKCS") -> bytes:
        """Sign data with the named key."""

    def close(self) -> None:
        """Close the HSM session."""


class _MockHSMSession:
    def __init__(self, config: HSMConfig) -> None:
        self._config = config
        self._keys: dict[str, HSMKey] = {}
        self._closed = False
        self._load_preloaded_keys()

    @property
    def closed(self) -> bool:
        return self._closed

    def _load_preloaded_keys(self) -> None:
        preloaded = [
            HSMKey(
                key_id="rsa-2048-001",
                label="RSA 2048 Signing Key",
                key_type="RSA",
                key_size=2048,
                algorithm="RSA-PKCS",
                capabilities=["sign", "verify"],
            ),
            HSMKey(
                key_id="ecdsa-p256-001",
                label="ECDSA P-256 Signing Key",
                key_type="EC",
                key_size=256,
                algorithm="ECDSA",
                capabilities=["sign"],
            ),
            HSMKey(
                key_id="ed25519-001",
                label="Ed25519 Signing Key",
                key_type="EC",
                key_size=256,
                algorithm="Ed25519",
                capabilities=["sign"],
            ),
        ]
        for key in preloaded:
            self._keys[key.key_id] = key

    def list_keys(self) -> list[HSMKey]:
        if self._closed:
            raise RuntimeError("HSM session is closed")
        return list(self._keys.values())

    def sign(self, key_id: str, data: bytes, mechanism: str = "SHA256-RSA-PKCS") -> bytes:
        if self._closed:
            raise RuntimeError("HSM session is closed")
        if key_id not in self._keys:
            raise KeyError(f"Key not found: {key_id!r}")
        key = self._keys[key_id]
        prefix = f"MOCK_SIG:{key_id}:{mechanism}:{key.key_type}:".encode()
        suffix = b"\xff" * max(key.key_size // 8, 32)
        return prefix + data[:8] + suffix

    def close(self) -> None:
        self._keys.clear()
        self._closed = True


def configure_pkcs11(module_path: str, slot_id: int, pin: str | None = None) -> HSMConfig:
    """Build an HSMConfig for a PKCS#11 module."""
    return HSMConfig(module_path=module_path, slot_id=slot_id, pin=pin)


def create_mock_session(config: HSMConfig) -> HSMSession:
    """Create a mock HSM session with preloaded test keys."""
    return _MockHSMSession(config)


def list_keys(session: HSMSession) -> list[HSMKey]:
    """List the keys available through a session."""
    return session.list_keys()


def sign_with_hsm_key(
    session: HSMSession,
    key_id: str,
    data: bytes,
    mechanism: str = "SHA256-RSA-PKCS",
) -> bytes:
    """Sign data through the session's named key.

    Backends may use ``KeyError`` internally for a missing key, but callers of
    this public facade receive one stable, backend-independent ``ValueError``.
    """
    try:
        return session.sign(key_id, data, mechanism)
    except KeyError as exc:
        raise ValueError(f"Unknown key: {key_id}") from exc


def import_key(session: HSMSession, key_pem: bytes, label: str) -> HSMKey | None:
    """Import a key into the mock session (returns None for real backends)."""
    if not isinstance(session, _MockHSMSession):
        return None
    key_id = f"imported-{label}"
    key = HSMKey(
        key_id=key_id,
        label=label,
        key_type="RSA",
        key_size=4096,
        algorithm="RSA-PKCS",
        capabilities=["sign"],
    )
    session._keys[key_id] = key
    return key
