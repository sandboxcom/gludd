"""DiskCache adapter that never invokes Python pickle deserialization."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Never,
    Protocol,
    Self,
    cast,
)

import diskcache
from diskcache.core import MODE_PICKLE, UNKNOWN

SAFE_CACHE_NAMESPACE = "msgpack-v1"


class SafeCache(Protocol):
    """Typed subset of the DiskCache API used by Gludd."""

    directory: str
    disk: object

    def set(
        self,
        key: object,
        value: object,
        expire: float | None = None,
        *,
        read: bool = False,
        tag: str | None = None,
        retry: bool = False,
    ) -> bool: ...

    def get(
        self,
        key: object,
        default: object = None,
        *,
        read: bool = False,
        expire_time: bool = False,
        tag: bool = False,
        retry: bool = False,
    ) -> object: ...

    def delete(self, key: object, retry: bool = False) -> bool: ...

    def clear(self, retry: bool = False) -> int: ...

    def close(self) -> None: ...

    def iterkeys(self, reverse: bool = False) -> Iterator[object]: ...

    def __iter__(self) -> Iterator[object]: ...

    def __contains__(self, key: object) -> bool: ...

    def __getitem__(self, key: object) -> object: ...

    def __setitem__(self, key: object, value: object) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _MsgpackModule(Protocol):
    def packb(
        self,
        value: object,
        *,
        use_bin_type: bool,
        strict_types: bool,
    ) -> bytes: ...

    def unpackb(
        self,
        value: bytes,
        *,
        raw: bool,
        strict_map_key: bool,
        ext_hook: Callable[[int, bytes], object],
    ) -> object: ...


_msgpack = cast(_MsgpackModule, importlib.import_module("msgpack"))


class _TypedDiskBase:
    """Static type surface for DiskCache's untyped ``Disk`` base class."""

    def __init__(
        self,
        directory: str,
        min_file_size: int = 0,
        pickle_protocol: int = 0,
    ) -> None:
        raise NotImplementedError

    def put(self, key: object) -> tuple[object, bool]:
        raise NotImplementedError

    def get(self, key: object, raw: bool) -> object:
        raise NotImplementedError

    def store(
        self,
        value: object,
        read: bool,
        key: object = UNKNOWN,
    ) -> tuple[int, int, str | None, object]:
        raise NotImplementedError

    def fetch(
        self,
        mode: int,
        filename: str | None,
        value: object,
        read: bool,
    ) -> object:
        raise NotImplementedError


_DiskBase = _TypedDiskBase
if not TYPE_CHECKING:
    _DiskBase = diskcache.Disk


class UnsafeLegacyCacheError(ValueError):
    """Raised when a cache row requires executable legacy deserialization."""


def _pack(value: object) -> bytes:
    try:
        return _msgpack.packb(value, use_bin_type=True, strict_types=True)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TypeError(
            f"unsupported safe-cache value type: {type(value).__name__}"
        ) from exc


def _reject_extension(code: int, data: bytes) -> Never:
    del data
    raise ValueError(f"MessagePack extension type {code} is not permitted")


def _unpack(value: bytes) -> object:
    return _msgpack.unpackb(
        value,
        raw=False,
        strict_map_key=True,
        ext_hook=_reject_extension,
    )


class SafeMsgpackDisk(_DiskBase):
    """Serialize keys and values as non-executable MessagePack data."""

    def put(self, key: object) -> tuple[object, bool]:
        return super().put(_pack(key))

    def get(self, key: object, raw: bool) -> object:
        if not raw:
            raise UnsafeLegacyCacheError(
                "refusing to deserialize a legacy pickled cache key"
            )
        value = super().get(key, raw)
        if not isinstance(value, bytes):
            raise UnsafeLegacyCacheError(
                "refusing a cache key outside the safe MessagePack namespace"
            )
        return _unpack(value)

    def store(
        self,
        value: object,
        read: bool,
        key: object = UNKNOWN,
    ) -> tuple[int, int, str | None, object]:
        if read:
            raise TypeError("safe cache does not accept file-like values")
        return super().store(_pack(value), False, key=key)

    def fetch(
        self,
        mode: int,
        filename: str | None,
        value: object,
        read: bool,
    ) -> object:
        if mode == MODE_PICKLE:
            raise UnsafeLegacyCacheError(
                "refusing to deserialize a legacy pickled cache value"
            )
        if read:
            raise TypeError("safe cache does not return file-like values")
        packed = super().fetch(mode, filename, value, False)
        if not isinstance(packed, bytes):
            raise UnsafeLegacyCacheError(
                "refusing a cache value outside the safe MessagePack namespace"
            )
        return _unpack(packed)


def open_safe_diskcache(
    cache_dir: str | os.PathLike[str],
    **settings: object,
) -> SafeCache:
    """Open an owner-only, versioned cache that cannot read legacy pickles."""

    safe_dir = prepare_safe_cache_directory(cache_dir)
    return cast(
        SafeCache,
        diskcache.Cache(
            str(safe_dir),
            disk=SafeMsgpackDisk,
            **settings,
        ),
    )


def prepare_safe_cache_directory(
    cache_dir: str | os.PathLike[str],
) -> Path:
    """Create the owner-only base and safe namespace without opening SQLite."""

    expanded = os.path.expandvars(os.path.expanduser(os.fspath(cache_dir)))
    base = Path(expanded)
    safe_dir = base / SAFE_CACHE_NAMESPACE
    for directory in (base, safe_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
    return safe_dir
