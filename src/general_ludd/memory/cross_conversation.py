"""Cross-conversation memory — LangGraph Store API for persistent cross-session state.

Provides put/get/search/delete over LangGraph's BaseStore with TTL-based
expiration and namespace isolation. Gracefully degrades to an ephemeral dict
when langgraph is not installed.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_STORE_IMPORT_ERROR: str | None = None
_InMemoryStore: type | None = None

try:
    from langgraph.store.memory import InMemoryStore as _InMem

    _InMemoryStore = _InMem
except ImportError as exc:
    _STORE_IMPORT_ERROR = str(exc)


def _now() -> float:
    return time.time()


class CrossConversationStore:
    """Wraps a LangGraph BaseStore for cross-conversation persistence.

    When the underlying store is not available (graceful degradation), all
    operations fall back to an ephemeral in-process dict.  TTL-based
    expiration is managed at the wrapper level since InMemoryStore does not
    support native TTL.
    """

    def __init__(self, store: Any | None = None) -> None:
        if store is not None:
            self._store: Any = store
        elif _InMemoryStore is not None:
            self._store = _InMemoryStore()
            logger.info("CrossConversationStore: using InMemoryStore")
        elif _STORE_IMPORT_ERROR is not None:
            logger.warning(
                "langgraph.store not available: %s — using ephemeral dict",
                _STORE_IMPORT_ERROR,
            )
            self._store = None
        else:
            logger.warning("CrossConversationStore: no store provided — using ephemeral dict")
            self._store = None
        self._ephemeral: dict[str, dict[str, Any]] = {}
        self._ttl_registry: dict[str, float] = {}

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _store_key(namespace: tuple[str, ...], key: str) -> str:
        return f"{':'.join(namespace)}:{key}"

    @staticmethod
    def _normalise_namespace(namespace: str | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(namespace, str):
            return (namespace,)
        return namespace

    # --------------------------------------------------------------------- put

    def put(
        self,
        key: str,
        value: dict[str, Any],
        namespace: str | tuple[str, ...] = ("default",),
        ttl: float | None = None,
        project_id: str | None = None,
    ) -> None:
        ns = self._normalise_namespace(namespace)
        sk = self._store_key(ns, key)
        ts = _now()

        entry_value = dict(value)

        if self._store is not None:
            try:
                self._store.put(ns, key, entry_value)
            except NotImplementedError:
                self._ephemeral[sk] = {
                    "key": key,
                    "value": entry_value,
                    "namespace": ns,
                    "project_id": project_id,
                    "created_at": ts,
                    "updated_at": ts,
                }

        if sk not in self._ephemeral:
            self._ephemeral[sk] = {
                "key": key,
                "value": entry_value,
                "namespace": ns,
                "project_id": project_id,
                "created_at": ts,
                "updated_at": ts,
            }
        self._ephemeral[sk]["updated_at"] = ts
        self._ephemeral[sk]["value"] = entry_value
        self._ephemeral[sk]["project_id"] = project_id

        if ttl is not None:
            self._ttl_registry[sk] = ts + ttl
        elif sk in self._ttl_registry:
            del self._ttl_registry[sk]

    # --------------------------------------------------------------------- get

    def get(
        self,
        key: str,
        namespace: str | tuple[str, ...] = ("default",),
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        ns = self._normalise_namespace(namespace)
        sk = self._store_key(ns, key)

        if self._is_expired(sk):
            self._evict(sk, ns, key)
            return None

        if self._store is not None:
            item = self._store.get(ns, key)
            if item is not None:
                return self._filter_by_project({
                    "key": item.key,
                    "value": item.value,
                    "namespace": list(item.namespace),
                    "project_id": None,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }, project_id)

        entry = self._ephemeral.get(sk)
        if entry is not None:
            return self._filter_by_project(dict(entry), project_id)
        return None

    # ------------------------------------------------------------------ search

    def search(
        self,
        namespace_prefix: str | tuple[str, ...] = ("default",),
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        nsp = self._normalise_namespace(namespace_prefix)

        if self._store is not None:
            store_results = self._store.search(nsp, query=query, filter=filter, limit=limit)
            if store_results:
                results: list[dict[str, Any]] = []
                for item in store_results:
                    sk = self._store_key(item.namespace, item.key)
                    if self._is_expired(sk):
                        continue
                    entry = {
                        "key": item.key,
                        "value": item.value,
                        "namespace": list(item.namespace),
                        "project_id": None,
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                        "score": getattr(item, "score", None),
                    }
                    filtered = self._filter_by_project(entry, project_id)
                    if filtered is not None:
                        results.append(filtered)
                return results[:limit]

        matches: list[dict[str, Any]] = []
        nsp_str = ":".join(nsp)

        for sk, entry in self._ephemeral.items():
            if self._is_expired(sk):
                continue
            stored_ns = entry.get("namespace", ())
            if isinstance(stored_ns, list):
                stored_ns = tuple(stored_ns)
            stored_ns_str = ":".join(stored_ns)

            if not stored_ns_str.startswith(nsp_str) and stored_ns_str != nsp_str:
                continue

            if filter:
                val = entry.get("value", {})
                match = all(
                    str(val.get(fk, "")) == str(fv) for fk, fv in filter.items()
                )
                if not match:
                    continue

            entry_copy = {
                "key": entry["key"],
                "value": entry["value"],
                "namespace": list(stored_ns),
                "project_id": entry.get("project_id"),
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
                "score": None,
            }
            filtered = self._filter_by_project(entry_copy, project_id)
            if filtered is not None:
                matches.append(filtered)

        return matches[:limit]

    # ------------------------------------------------------------------ delete

    def delete(
        self,
        key: str,
        namespace: str | tuple[str, ...] = ("default",),
        project_id: str | None = None,
    ) -> bool:
        ns = self._normalise_namespace(namespace)
        sk = self._store_key(ns, key)

        entry = self._ephemeral.get(sk)
        if project_id is not None and entry is not None:
            entry_pid = entry.get("project_id")
            if entry_pid is not None and entry_pid != project_id:
                return False

        existed = sk in self._ephemeral or (
            self._store is not None and self._store.get(ns, key) is not None
        )

        if self._store is not None:
            self._store.delete(ns, key)
        self._ephemeral.pop(sk, None)
        self._ttl_registry.pop(sk, None)
        return existed

    # ------------------------------------------------------------------- purge

    def purge_expired(self) -> int:
        purged = 0
        now = _now()
        expired_sks = [sk for sk, deadline in self._ttl_registry.items() if now >= deadline]
        for sk in expired_sks:
            entry = self._ephemeral.get(sk, {})
            ns = tuple(entry.get("namespace", ("default",)))
            key = str(entry.get("key", ""))
            if key:
                self.delete(key, ns)
            else:
                self._ephemeral.pop(sk, None)
                self._ttl_registry.pop(sk, None)
            purged += 1
        return purged

    @property
    def available(self) -> bool:
        return self._store is not None or bool(self._ephemeral) is not None

    # ------------------------------------------------------------ private impl

    @staticmethod
    def _filter_by_project(
        entry: dict[str, Any], project_id: str | None,
    ) -> dict[str, Any] | None:
        if project_id is None:
            return entry
        entry_pid = entry.get("project_id")
        if entry_pid is None:
            return entry
        return entry if entry_pid == project_id else None

    def _is_expired(self, stored_key: str) -> bool:
        deadline = self._ttl_registry.get(stored_key)
        if deadline is None:
            return False
        return _now() >= deadline

    def _evict(self, stored_key: str, namespace: tuple[str, ...], key: str) -> None:
        self._ephemeral.pop(stored_key, None)
        self._ttl_registry.pop(stored_key, None)
        if self._store is not None:
            with contextlib.suppress(Exception):
                self._store.delete(namespace, key)
