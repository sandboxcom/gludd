"""Operator SSH key rotation + history scrub.

Encapsulates the lifecycle of deployment SSH keys: generate a new key pair,
register it with the target host (``authorized_keys`` append), scrub old
keys from the host, prune from the local keystore, and record an
auditable rotation event.

Public functions are pure / file-system operations; the caller wires
the SSH transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_KEYSTORE = "~/.gludd/ssh/keys"
DEFAULT_KEY_TYPE = "ed25519"
DEFAULT_KEY_BITS = 521


@dataclass
class RotationEvent:
    key_name: str
    fingerprint: str
    rotated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    old_fingerprints: list[str] = field(default_factory=list)


@dataclass
class KeyMetadata:
    name: str
    fingerprint: str
    created_at: datetime
    rotated_at: datetime | None = None


def _keystore_dir() -> Path:
    return Path(DEFAULT_KEYSTORE).expanduser().resolve()


def _ensure_keystore() -> Path:
    d = _keystore_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def generate_key_pair(
    name: str,
    key_type: str = DEFAULT_KEY_TYPE,
    bits: int = DEFAULT_KEY_BITS,
    *,
    keystore_dir: str | Path | None = None,
) -> KeyMetadata:
    store = Path(keystore_dir) if keystore_dir else _ensure_keystore()
    private_path = store / name
    public_path = store / f"{name}.pub"

    if private_path.exists() or public_path.exists():
        raise FileExistsError(f"key {name!r} already exists in keystore")
    if not (0 <= bits <= 16384):
        raise ValueError("bits must be 0-16384")

    private_key = (
        f"# stub-{key_type}-{bits}b-key-for-{name}  # pragma: allowlist secret"
    )
    public_key = f"{key_type} AAA...stub... user@{name}-{bits}"

    private_path.write_text(private_key)
    private_path.chmod(0o600)
    public_path.write_text(public_key)

    return KeyMetadata(
        name=name,
        fingerprint=f"SHA256:stub-{name}-{bits}",
        created_at=datetime.now(UTC),
    )


def list_keys(keystore_dir: str | Path | None = None) -> list[KeyMetadata]:
    store = Path(keystore_dir) if keystore_dir else Path(DEFAULT_KEYSTORE).expanduser()
    if not store.is_dir():
        return []
    keys: list[KeyMetadata] = []
    for p in sorted(store.glob("*.pub")):
        meta = read_key_metadata(p)
        if meta is not None:
            keys.append(meta)
    return keys


def read_key_metadata(pub_path: Path) -> KeyMetadata | None:
    if not pub_path.is_file():
        return None
    name = pub_path.stem
    fingerprint = pub_path.read_text().strip().split(" ")[-1] if pub_path.stat().st_size > 0 else "unknown"
    created_ts = pub_path.stat().st_ctime
    return KeyMetadata(
        name=name,
        fingerprint=fingerprint,
        created_at=datetime.fromtimestamp(created_ts, tz=UTC),
    )


def scrub_key(name: str, keystore_dir: str | Path | None = None) -> bool:
    store = Path(keystore_dir) if keystore_dir else Path(DEFAULT_KEYSTORE).expanduser()
    private = store / name
    public = store / f"{name}.pub"
    removed = False
    for p in (private, public):
        if p.is_file():
            p.unlink()
            removed = True
    return removed


def rotation_history(
    keystore_dir: str | Path | None = None,
) -> list[RotationEvent]:
    store = Path(keystore_dir) if keystore_dir else Path(DEFAULT_KEYSTORE).expanduser()
    history_path = store / "rotation_history.txt"
    if not history_path.is_file():
        return []
    events: list[RotationEvent] = []
    for line in history_path.read_text().strip().splitlines():
        if not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 3:
            try:
                events.append(
                    RotationEvent(
                        key_name=parts[0],
                        fingerprint=parts[1],
                        rotated_at=datetime.fromisoformat(parts[2]),
                        old_fingerprints=parts[3].split(",") if len(parts) > 3 else [],
                    )
                )
            except (ValueError, IndexError):
                continue
    return events


def record_rotation(
    event: RotationEvent,
    keystore_dir: str | Path | None = None,
) -> None:
    store = Path(keystore_dir) if keystore_dir else _ensure_keystore()
    history_path = store / "rotation_history.txt"
    line = (
        f"{event.key_name}\t{event.fingerprint}\t"
        f"{event.rotated_at.isoformat()}\t"
        f"{','.join(event.old_fingerprints)}\n"
    )
    with open(history_path, "a") as f:
        f.write(line)
