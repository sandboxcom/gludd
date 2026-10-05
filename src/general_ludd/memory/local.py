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

from general_ludd.security.safe_diskcache import SafeCache, open_safe_diskcache

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = ".gludd/local_memory"


@dataclass
class MemoryRecord:
    """Represent one project-scoped local memory value and its expiry metadata."""

    agent_id: str
    key: str
    value: str
    namespace: str = "default"
    project_id: str | None = None
    ttl_seconds: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, object]:
        """Serialize the record into safe cache-compatible primitives."""
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
        """Build a record from validated cache-compatible primitives."""
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
        """Initialize the store without acquiring its SQLite-backed cache."""
        path = os.path.expanduser(os.path.expandvars(str(cache_dir)))
        os.makedirs(path, mode=0o700, exist_ok=True)
        self._cache_dir = path
        self._cache_instance: SafeCache | None = None
        self._index_prefix = "idx"

    @property
    def _cache(self) -> SafeCache:
        """Open the cache only when a data operation needs SQLite."""
        if self._cache_instance is None:
            self._cache_instance = open_safe_diskcache(self._cache_dir)
        return self._cache_instance

    @staticmethod
    def _project_key(project_id: str | None) -> str:
        return project_id or "__global__"

    def _data_key(self, agent_id: str, key: str, namespace: str, project_id: str | None = None) -> str:
        pk = self._project_key(project_id)
        return f"{namespace}:{pk}:{agent_id}:{key}"

    def _index_key(self, agent_id: str, namespace: str, project_id: str | None = None) -> str:
        pk = self._project_key(project_id)
        return f"{self._index_prefix}:{namespace}:{pk}:{agent_id}"

    def _index_prefix_key(self, namespace: str, project_id: str | None = None) -> str:
        pk = self._project_key(project_id)
        return f"{self._index_prefix}:{namespace}:{pk}"

    async def get(
        self, agent_id: str, key: str, namespace: str = "default", project_id: str | None = None,
    ) -> MemoryRecord | None:
        """Return one unexpired memory record, if present."""
        cache_key = self._data_key(agent_id, key, namespace, project_id)
        data = self._cache.get(cache_key, default=None)
        if data is None:
            return None
        if not isinstance(data, dict) or not all(
            isinstance(key, str) for key in data
        ):
            return None
        record_data: dict[str, object] = {
            key: value for key, value in data.items() if isinstance(key, str)
        }
        record = MemoryRecord.from_dict(record_data)
        if self._is_expired(record):
            await self.delete(agent_id, key, namespace, project_id)
            return None
        return record

    async def set(
        self,
        agent_id: str,
        key: str,
        value: str,
        namespace: str = "default",
        project_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> MemoryRecord:
        """Store and index one memory record."""
        now = time.time()
        record = MemoryRecord(
            agent_id=agent_id,
            key=key,
            value=value,
            namespace=namespace,
            project_id=project_id,
            ttl_seconds=ttl_seconds,
            created_at=now,
            updated_at=now,
        )
        cache_key = self._data_key(agent_id, key, namespace, project_id)
        idx_key = self._index_key(agent_id, namespace, project_id)
        expire = (now + ttl_seconds) if ttl_seconds else None
        self._cache.set(cache_key, record.as_dict(), expire=expire)
        stored_keys = self._cache.get(idx_key, default=[])
        keys = set(stored_keys) if isinstance(stored_keys, list) else set()
        keys.add(key)
        self._cache.set(idx_key, sorted(keys))
        return record

    async def delete(
        self, agent_id: str, key: str, namespace: str = "default", project_id: str | None = None,
    ) -> bool:
        """Delete one memory record and update its namespace index."""
        cache_key = self._data_key(agent_id, key, namespace, project_id)
        idx_key = self._index_key(agent_id, namespace, project_id)
        existed = cache_key in self._cache
        self._cache.delete(cache_key)
        stored_keys = self._cache.get(idx_key, default=[])
        keys = set(stored_keys) if isinstance(stored_keys, list) else set()
        keys.discard(key)
        if keys:
            self._cache.set(idx_key, sorted(keys))
        else:
            self._cache.delete(idx_key)
        return existed

    async def list_by_namespace(
        self,
        agent_id: str,
        namespace: str = "default",
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """List up to ``limit`` unexpired records in one namespace."""
        idx_key = self._index_key(agent_id, namespace, project_id)
        stored_keys = self._cache.get(idx_key, default=[])
        keys: set[str] = (
            set(stored_keys) if isinstance(stored_keys, list) else set()
        )
        results: list[MemoryRecord] = []
        for key in sorted(keys):
            record = await self.get(agent_id, key, namespace, project_id)
            if record is not None:
                results.append(record)
            if len(results) >= limit:
                break
        return results

    async def purge_expired(self) -> int:
        """Delete expired records and return the number removed."""
        purged = 0
        for cache_key_bytes in list(self._cache):
            if isinstance(cache_key_bytes, str):
                cache_key = cache_key_bytes
            elif isinstance(cache_key_bytes, bytes):
                cache_key = cache_key_bytes.decode(errors="replace")
            else:
                continue
            if cache_key.startswith(self._index_prefix):
                continue
            data = self._cache.get(cache_key, default=None)
            if data is None:
                self._cache.delete(cache_key)
                continue
            if not isinstance(data, dict) or not all(
                isinstance(key, str) for key in data
            ):
                continue
            record_data: dict[str, object] = {
                key: value for key, value in data.items() if isinstance(key, str)
            }
            record = MemoryRecord.from_dict(record_data)
            if self._is_expired(record):
                await self.delete(record.agent_id, record.key, record.namespace, record.project_id)
                purged += 1
        return purged

    @staticmethod
    def _is_expired(record: MemoryRecord) -> bool:
        if record.ttl_seconds is None:
            return False
        elapsed = time.time() - record.created_at
        return elapsed > record.ttl_seconds

    def close(self) -> None:
        """Close the owned cache if acquired and permit safe repeated calls."""
        cache = self._cache_instance
        if cache is not None:
            cache.close()
            self._cache_instance = None

    @property
    def cache_dir(self) -> str:
        """Return the expanded owner-only cache directory."""
        return self._cache_dir
