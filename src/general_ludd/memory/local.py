"""Local agent memory — diskcache-backed persistent key-value store.

Drop-in companion to MemoryRepository (SQL-backed) for local-only operation.
Same get/set/delete/list_by_namespace/purge_expired API without SQL dependency.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import diskcache

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = ".gludd/local_memory"


@dataclass
class MemoryRecord:
    agent_id: str
    key: str
    value: str
    namespace: str = "default"
    project_id: str | None = None
    ttl_seconds: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "key": self.key,
            "value": self.value,
            "namespace": self.namespace,
            "project_id": self.project_id,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> MemoryRecord:
        return MemoryRecord(
            agent_id=str(data.get("agent_id", "")),
            key=str(data.get("key", "")),
            value=str(data.get("value", "")),
            namespace=str(data.get("namespace", "default")),
            project_id=(
                str(d) if isinstance(d := data.get("project_id"), str) else None
            ),
            ttl_seconds=(
                int(d) if isinstance(d := data.get("ttl_seconds"), (int, float)) else None
            ),
            created_at=(
                float(d) if isinstance(d := data.get("created_at"), (int, float, str)) else 0.0
            ),
            updated_at=(
                float(d) if isinstance(d := data.get("updated_at"), (int, float, str)) else 0.0
            ),
        )



class LocalAgentMemory:
    """diskcache-backed local agent memory store.

    Compatible with the MemoryRepository get/set/delete/list_by_namespace API
    but stores data in a diskcache directory instead of SQL. Suitable for
    local agent operation without a running database.
    """

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR) -> None:
        path = os.path.expanduser(os.path.expandvars(str(cache_dir)))
        os.makedirs(path, mode=0o700, exist_ok=True)
        self._cache_dir = path
        self._cache: diskcache.Cache = diskcache.Cache(path)
        self._index_prefix = "idx"

    def _data_key(self, agent_id: str, key: str, namespace: str) -> str:
        return f"{namespace}:{agent_id}:{key}"

    def _index_key(self, agent_id: str, namespace: str) -> str:
        return f"{self._index_prefix}:{namespace}:{agent_id}"

    async def get(
        self, agent_id: str, key: str, namespace: str = "default"
    ) -> MemoryRecord | None:
        cache_key = self._data_key(agent_id, key, namespace)
        data = self._cache.get(cache_key, default=None)
        if data is None:
            return None
        record = MemoryRecord.from_dict(data)
        if self._is_expired(record):
            await self.delete(agent_id, key, namespace)
            return None
        return record

    async def set(
        self,
        agent_id: str,
        key: str,
        value: str,
        namespace: str = "default",
        ttl_seconds: int | None = None,
    ) -> MemoryRecord:
        now = time.time()
        record = MemoryRecord(
            agent_id=agent_id,
            key=key,
            value=value,
            namespace=namespace,
            ttl_seconds=ttl_seconds,
            created_at=now,
            updated_at=now,
        )
        cache_key = self._data_key(agent_id, key, namespace)
        idx_key = self._index_key(agent_id, namespace)
        expire = (now + ttl_seconds) if ttl_seconds else None
        self._cache.set(cache_key, record.as_dict(), expire=expire)
        keys = self._cache.get(idx_key, default=set())
        if not isinstance(keys, set):
            keys = set()
        keys.add(key)
        self._cache.set(idx_key, keys)
        return record

    async def delete(
        self, agent_id: str, key: str, namespace: str = "default"
    ) -> bool:
        cache_key = self._data_key(agent_id, key, namespace)
        idx_key = self._index_key(agent_id, namespace)
        existed = cache_key in self._cache
        self._cache.delete(cache_key)
        keys = self._cache.get(idx_key, default=set())
        if not isinstance(keys, set):
            keys = set()
        keys.discard(key)
        if keys:
            self._cache.set(idx_key, keys)
        else:
            self._cache.delete(idx_key)
        return existed

    async def list_by_namespace(
        self,
        agent_id: str,
        namespace: str = "default",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        idx_key = self._index_key(agent_id, namespace)
        keys: set[str] = self._cache.get(idx_key, default=set())
        if not isinstance(keys, set):
            keys = set()
        results: list[MemoryRecord] = []
        for key in sorted(keys):
            record = await self.get(agent_id, key, namespace)
            if record is not None:
                results.append(record)
            if len(results) >= limit:
                break
        return results

    async def purge_expired(self) -> int:
        purged = 0
        for cache_key_bytes in list(self._cache):
            cache_key = (
                cache_key_bytes if isinstance(cache_key_bytes, str)
                else cache_key_bytes.decode(errors="replace")
            )
            if cache_key.startswith(self._index_prefix):
                continue
            data = self._cache.get(cache_key, default=None, read=True)
            if data is None:
                self._cache.delete(cache_key)
                continue
            record = MemoryRecord.from_dict(data)
            if self._is_expired(record):
                await self.delete(record.agent_id, record.key, record.namespace)
                purged += 1
        return purged

    @staticmethod
    def _is_expired(record: MemoryRecord) -> bool:
        if record.ttl_seconds is None:
            return False
        elapsed = time.time() - record.created_at
        return elapsed > record.ttl_seconds

    def close(self) -> None:
        self._cache.close()

    @property
    def cache_dir(self) -> str:
        return self._cache_dir
