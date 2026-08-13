"""Regression tests for non-executable DiskCache serialization."""

from __future__ import annotations

import os
import pickle
import stat
from pathlib import Path

import diskcache
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _write_marker(path: str) -> None:
    Path(path).write_text("executed", encoding="utf-8")


class _PicklePayload:
    def __init__(self, marker: Path) -> None:
        self._marker = marker

    def __reduce__(self):
        return _write_marker, (str(self._marker),)


def test_safe_cache_round_trips_only_data_types(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import open_safe_diskcache

    cache = open_safe_diskcache(tmp_path / "cache")
    try:
        value = {
            "text": "hello",
            "number": 42,
            "ratio": 0.5,
            "enabled": True,
            "empty": None,
            "items": ["a", b"bytes"],
        }
        cache.set("key", value)
        assert cache.get("key") == value
        with pytest.raises(TypeError):
            cache.set("unsupported", {"set"})
    finally:
        cache.close()


def test_safe_cache_never_reads_legacy_pickle_cache(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import (
        SAFE_CACHE_NAMESPACE,
        open_safe_diskcache,
    )

    base = tmp_path / "cache"
    marker = tmp_path / "pickle-executed"
    legacy = diskcache.Cache(str(base))
    try:
        legacy.set("poison", _PicklePayload(marker))
    finally:
        legacy.close()

    cache = open_safe_diskcache(base)
    try:
        assert Path(cache.directory) == base / SAFE_CACHE_NAMESPACE
        assert cache.get("poison") is None
    finally:
        cache.close()
    assert not marker.exists()


def test_safe_disk_rejects_pickle_mode_without_deserializing(tmp_path: Path) -> None:
    from diskcache.core import MODE_PICKLE

    from general_ludd.security.safe_diskcache import (
        SafeMsgpackDisk,
        UnsafeLegacyCacheError,
    )

    marker = tmp_path / "pickle-executed"
    payload = pickle.dumps(_PicklePayload(marker))
    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(UnsafeLegacyCacheError):
        disk.fetch(MODE_PICKLE, None, payload, False)
    assert not marker.exists()


def test_safe_disk_rejects_legacy_key_without_deserializing(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import (
        SafeMsgpackDisk,
        UnsafeLegacyCacheError,
    )

    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(UnsafeLegacyCacheError, match="legacy pickled cache key"):
        disk.get(pickle.dumps(_PicklePayload(tmp_path / "marker")), False)
    assert not (tmp_path / "marker").exists()


def test_safe_disk_round_trips_msgpack_keys(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import SafeMsgpackDisk

    disk = SafeMsgpackDisk(str(tmp_path))
    stored, raw = disk.put({"key": ["value", 3]})
    assert disk.get(stored, raw) == {"key": ["value", 3]}


def test_safe_disk_rejects_non_bytes_key_namespace(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import (
        SafeMsgpackDisk,
        UnsafeLegacyCacheError,
    )

    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(UnsafeLegacyCacheError, match="outside the safe"):
        disk.get("not-packed", True)


def test_safe_disk_rejects_file_like_store_and_fetch(tmp_path: Path) -> None:
    from diskcache.core import MODE_RAW

    from general_ludd.security.safe_diskcache import SafeMsgpackDisk

    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(TypeError, match="does not accept file-like"):
        disk.store(b"value", True)
    with pytest.raises(TypeError, match="does not return file-like"):
        disk.fetch(MODE_RAW, None, b"value", True)


def test_safe_disk_rejects_non_bytes_value_namespace(tmp_path: Path) -> None:
    from diskcache.core import MODE_RAW

    from general_ludd.security.safe_diskcache import (
        SafeMsgpackDisk,
        UnsafeLegacyCacheError,
    )

    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(UnsafeLegacyCacheError, match="outside the safe"):
        disk.fetch(MODE_RAW, None, "not-packed", False)


def test_safe_disk_rejects_msgpack_extensions(tmp_path: Path) -> None:
    from diskcache.core import MODE_RAW

    from general_ludd.security.safe_diskcache import SafeMsgpackDisk

    packed = b"\xd4\x01x"  # MessagePack fixext1, extension code 1.
    disk = SafeMsgpackDisk(str(tmp_path))
    with pytest.raises(ValueError, match="extension type 1"):
        disk.fetch(MODE_RAW, None, packed, False)


def test_safe_cache_directories_are_owner_only(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import open_safe_diskcache

    base = tmp_path / "cache"
    cache = open_safe_diskcache(base)
    try:
        assert stat.S_IMODE(os.stat(base).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(cache.directory).st_mode) == 0o700
    finally:
        cache.close()


def test_safe_cache_protocol_uses_bound_magic_methods() -> None:
    """Operator and context-manager members must bind protocol self."""
    from general_ludd.security.safe_diskcache import SafeCache

    for name in (
        "__iter__",
        "__contains__",
        "__getitem__",
        "__setitem__",
        "__enter__",
        "__exit__",
    ):
        assert callable(SafeCache.__dict__.get(name)), name


def test_application_has_no_direct_diskcache_construction() -> None:
    safe_module = ROOT / "src/general_ludd/security/safe_diskcache.py"
    offenders: list[str] = []
    for path in (ROOT / "src/general_ludd").rglob("*.py"):
        if path == safe_module:
            continue
        text = path.read_text(encoding="utf-8")
        if "diskcache.Cache(" in text or "from diskcache import Cache" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
