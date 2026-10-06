"""Host-wide reader/writer lease for Gludd's shared uv cache.

Long-running Python commands may still depend on artifacts materialized by the
``uv`` process that launched them even when no executable named ``uv`` remains
in the process table.  Shard runners therefore hold a shared kernel lease while
cache cleanup must acquire the exclusive side of the same lock.  The lock is
outside the cache so ``uv cache clean`` cannot delete its own authority record.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.resource_arbiter import project_namespace
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from resource_arbiter import project_namespace

_MAX_OWNER_RECEIPTS = 64


@dataclass(frozen=True)
class UvCacheLeasePaths:
    """Stable lock and owner-receipt paths for one exact cache root."""

    cache_root: Path
    lock_file: Path
    owners_dir: Path


@dataclass(frozen=True)
class UvCacheLease:
    """Result yielded while a cache lease attempt remains open."""

    acquired: bool
    mode: str
    lock_path: Path
    receipt_path: Path | None
    owners: tuple[dict[str, object], ...] = ()


def uv_cache_lease_paths(cache_root: Path) -> UvCacheLeasePaths:
    """Return cache-external lease paths after rejecting ambiguous roots."""
    expanded = cache_root.expanduser()
    if not expanded.is_absolute() or expanded == Path("/"):
        raise ValueError("cache root must be a non-root absolute path")
    resolved = expanded.resolve(strict=False)
    stem = f".{resolved.name}.gludd-lease"
    return UvCacheLeasePaths(
        cache_root=resolved,
        lock_file=resolved.parent / f"{stem}.lock",
        owners_dir=resolved.parent / f"{stem}.owners",
    )


def _read_owner_receipts(paths: UvCacheLeasePaths) -> tuple[dict[str, object], ...]:
    """Read a bounded, exact-cache set of diagnostic owner receipts."""
    owners: list[dict[str, object]] = []
    try:
        candidates = sorted(paths.owners_dir.glob("*.json"))[:_MAX_OWNER_RECEIPTS]
    except OSError:
        return ()
    for receipt in candidates:
        try:
            value: object = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and value.get("cache_root") == str(paths.cache_root)
            and isinstance(value.get("owner_root"), str)
            and isinstance(value.get("owner_namespace"), str)
            and isinstance(value.get("token"), str)
        ):
            owners.append(value)
    return tuple(owners)


def _write_owner_receipt(
    paths: UvCacheLeasePaths,
    *,
    owner_root: Path,
    mode: str,
    token: str,
) -> Path:
    """Atomically publish the root and namespace holding one kernel lease."""
    root = owner_root.expanduser()
    if not root.is_absolute() or root == Path("/"):
        raise ValueError("owner root must be a non-root absolute path")
    resolved_root = root.resolve(strict=False)
    namespace = project_namespace(resolved_root)
    paths.owners_dir.mkdir(parents=True, exist_ok=True)
    receipt = paths.owners_dir / f"{namespace}-{os.getpid()}-{token}.json"
    temporary = paths.owners_dir / f".{receipt.name}.tmp"
    payload = {
        "schema_version": 1,
        "mode": mode,
        "token": token,
        "pid": os.getpid(),
        "owner_root": str(resolved_root),
        "owner_namespace": namespace,
        "cache_root": str(paths.cache_root),
        "acquired_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, receipt)
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def _remove_owned_receipt(receipt: Path | None, token: str) -> None:
    """Remove only the receipt whose unguessable token this holder published."""
    if receipt is None:
        return
    try:
        value: object = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(value, dict) and value.get("token") == token:
        receipt.unlink(missing_ok=True)


def _remove_stale_receipts(paths: UvCacheLeasePaths) -> None:
    """Clear receipts only while an exclusive lock proves no shared owner lives."""
    try:
        candidates = tuple(paths.owners_dir.glob("*.json"))[:_MAX_OWNER_RECEIPTS]
    except OSError:
        return
    for receipt in candidates:
        receipt.unlink(missing_ok=True)


@contextlib.contextmanager
def _uv_cache_lease(
    cache_root: Path,
    *,
    owner_root: Path,
    mode: str,
    wait_seconds: float,
    heartbeat_seconds: float,
) -> Iterator[UvCacheLease]:
    """Acquire one shared/exclusive lease with a bounded observable wait."""
    if mode not in {"shared", "exclusive"}:
        raise ValueError("lease mode must be shared or exclusive")
    if wait_seconds < 0 or heartbeat_seconds <= 0:
        raise ValueError("lease wait must be nonnegative and heartbeat positive")
    paths = uv_cache_lease_paths(cache_root)
    paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(paths.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    receipt: Path | None = None
    token = uuid.uuid4().hex
    operation = fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX
    started = time.monotonic()
    deadline = started + wait_seconds
    next_heartbeat = started + heartbeat_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                now = time.monotonic()
                if now >= deadline:
                    yield UvCacheLease(
                        acquired=False,
                        mode=mode,
                        lock_path=paths.lock_file,
                        receipt_path=None,
                        owners=_read_owner_receipts(paths),
                    )
                    return
                if now >= next_heartbeat:
                    print(
                        "UV-CACHE-LEASE-WAIT "
                        f"mode={mode} elapsed={now - started:.0f}s "
                        f"path={paths.lock_file}",
                        flush=True,
                    )
                    next_heartbeat = now + heartbeat_seconds
                time.sleep(min(0.1, max(0.0, deadline - now)))
                continue
            acquired = True
            break

        if mode == "exclusive":
            _remove_stale_receipts(paths)
        receipt = _write_owner_receipt(
            paths,
            owner_root=owner_root,
            mode=mode,
            token=token,
        )
        yield UvCacheLease(
            acquired=True,
            mode=mode,
            lock_path=paths.lock_file,
            receipt_path=receipt,
        )
    finally:
        _remove_owned_receipt(receipt, token)
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def shared_uv_cache_lease(
    cache_root: Path,
    *,
    owner_root: Path,
    wait_seconds: float = 130.0,
    heartbeat_seconds: float = 10.0,
) -> contextlib.AbstractContextManager[UvCacheLease]:
    """Hold a shared lease for the lifetime of one cache consumer."""
    return _uv_cache_lease(
        cache_root,
        owner_root=owner_root,
        mode="shared",
        wait_seconds=wait_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )


def exclusive_uv_cache_lease(
    cache_root: Path,
    *,
    owner_root: Path,
    wait_seconds: float = 0.0,
    heartbeat_seconds: float = 10.0,
) -> contextlib.AbstractContextManager[UvCacheLease]:
    """Attempt the exclusive cleanup lease without bypassing active consumers."""
    return _uv_cache_lease(
        cache_root,
        owner_root=owner_root,
        mode="exclusive",
        wait_seconds=wait_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
