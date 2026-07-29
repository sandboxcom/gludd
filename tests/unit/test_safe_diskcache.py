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


def test_safe_cache_directories_are_owner_only(tmp_path: Path) -> None:
    from general_ludd.security.safe_diskcache import open_safe_diskcache

    base = tmp_path / "cache"
    cache = open_safe_diskcache(base)
    try:
        assert stat.S_IMODE(os.stat(base).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(cache.directory).st_mode) == 0o700
    finally:
        cache.close()


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
