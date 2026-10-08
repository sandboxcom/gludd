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
_AUTO_STORE = object()

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

    def __init__(self, store: Any | None = _AUTO_STORE) -> None:
        """Select the supplied, automatic, or ephemeral backing store."""
        if store is not _AUTO_STORE:
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

    @staticmethod
    def _store_key(namespace: tuple[str, ...], key: str) -> str:
        return f"{':'.join(namespace)}:{key}"

    @staticmethod
    def _project_store_key(
        namespace: tuple[str, ...], key: str, project_id: str | None,
    ) -> str:
        base = CrossConversationStore._store_key(namespace, key)
        return base if project_id is None else f"{base}:project:{project_id}"

    @staticmethod
    def _normalise_namespace(namespace: str | tuple[str, ...]) -> tuple[str, ...]:
        return (namespace,) if isinstance(namespace, str) else namespace

    def put(
        self,
        key: str,
        value: dict[str, Any],
        namespace: str | tuple[str, ...] = ("default",),
        ttl: float | None = None,
        project_id: str | None = None,
    ) -> None:
        """Store a value with optional namespace, project, and TTL isolation."""
        ns = self._normalise_namespace(namespace)
        sk = self._project_store_key(ns, key, project_id)
        legacy_sk = self._store_key(ns, key)
        ts = _now()

        entry_value = dict(value)

        if self._store is not None:
            try:
                self._store.put(ns, sk, entry_value)
                if sk != key:
                    self._store.put(ns, key, entry_value)
            except NotImplementedError:
                self._ephemeral.pop(sk, None)

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

        if project_id is not None and legacy_sk not in self._ephemeral:
            self._ephemeral[legacy_sk] = {**self._ephemeral[sk], "_alias": True}

        if ttl is not None:
            self._ttl_registry[sk] = ts + ttl
        else:
            self._ttl_registry.pop(sk, None)

    def get(
        self,
        key: str,
        namespace: str | tuple[str, ...] = ("default",),
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an unexpired value visible to the requested project."""
        ns = self._normalise_namespace(namespace)
        sk = self._project_store_key(ns, key, project_id)
        global_sk = self._project_store_key(ns, key, None)

        if self._is_expired(sk):
            self._evict(sk, ns, key, project_id)
            return None

        entry = self._ephemeral.get(sk)
        if entry is not None and not entry.get("_alias"):
            return self._filter_by_project(dict(entry), project_id)

        if project_id is not None:
            global_entry = self._ephemeral.get(global_sk)
            if global_entry is not None and not global_entry.get("_alias"):
                return self._filter_by_project(dict(global_entry), project_id)
            return None

        if self._store is not None:
            item = self._store.get(ns, sk)
            if item is not None:
                return self._filter_by_project({
                    "key": key,
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

    def search(
        self,
        namespace_prefix: str | tuple[str, ...] = ("default",),
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search visible values under a namespace prefix."""
        nsp = self._normalise_namespace(namespace_prefix)

        if self._store is not None:
            store_limit = max(limit * 3, limit)
            store_results = self._store.search(
                nsp, query=query, filter=filter, limit=store_limit,
            )
            if store_results:
                results: list[dict[str, Any]] = []
                for item in store_results:
                    sk = str(item.key)
                    raw_alias_sk = self._store_key(tuple(item.namespace), sk)
                    if sk not in self._ephemeral and raw_alias_sk in self._ephemeral:
                        continue
                    if self._is_expired(sk):
                        continue
                    meta = self._ephemeral.get(sk, {})
                    if meta.get("_alias"):
                        continue
                    entry = {
                        "key": meta.get("key", item.key),
                        "value": item.value,
                        "namespace": list(item.namespace),
                        "project_id": meta.get("project_id"),
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                        "score": getattr(item, "score", None),
                    }
                    if project_id is not None and entry.get("project_id") != project_id:
                        continue
                    filtered = self._filter_by_project(entry, project_id)
                    if filtered is not None:
                        results.append(filtered)
                return results[:limit]

        matches: list[dict[str, Any]] = []
        nsp_str = ":".join(nsp)

        for sk, entry in self._ephemeral.items():
            if entry.get("_alias"):
                continue
            if self._is_expired(sk):
                continue
            stored_ns = entry.get("namespace", ())
            if isinstance(stored_ns, list):
                stored_ns = tuple(stored_ns)
            stored_ns_str = ":".join(stored_ns)

            if not stored_ns_str.startswith(nsp_str):
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
            if project_id is not None and entry_copy.get("project_id") != project_id:
                continue
            filtered = self._filter_by_project(entry_copy, project_id)
            if filtered is not None:
                matches.append(filtered)

        return matches[:limit]

    def delete(
        self,
        key: str,
        namespace: str | tuple[str, ...] = ("default",),
        project_id: str | None = None,
    ) -> bool:
        """Delete one project-scoped value and report whether it existed."""
        ns = self._normalise_namespace(namespace)
        sk = self._project_store_key(ns, key, project_id)
        raw_key = key

        entry = self._ephemeral.get(sk)
        if project_id is not None and entry is None:
            return False

        existed = sk in self._ephemeral or (
            self._store is not None and self._store.get(ns, sk) is not None
        )

        if self._store is not None:
            self._store.delete(ns, sk)
            if project_id is None and sk != raw_key:
                self._store.delete(ns, raw_key)
        self._ephemeral.pop(sk, None)
        self._ttl_registry.pop(sk, None)
        return existed

    def purge_expired(self) -> int:
        """Delete expired values and return the number purged."""
        purged = 0
        now = _now()
        expired_sks = [sk for sk, deadline in self._ttl_registry.items() if now >= deadline]
        for sk in expired_sks:
            entry = self._ephemeral.get(sk, {})
            ns = tuple(entry.get("namespace", ("default",)))
            key = str(entry.get("key", ""))
            if key:
                self.delete(key, ns, entry.get("project_id"))
            else:
                self._ephemeral.pop(sk, None)
                self._ttl_registry.pop(sk, None)
            purged += 1
        return purged

    @property
    def available(self) -> bool:
        """Return whether the facade is available, including fallback mode."""
        return self._store is not None or bool(self._ephemeral) is not None

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
        return deadline is not None and _now() >= deadline

    def _evict(
        self,
        stored_key: str,
        namespace: tuple[str, ...],
        key: str,
        project_id: str | None,
    ) -> None:
        self._ephemeral.pop(stored_key, None)
        self._ttl_registry.pop(stored_key, None)
        if self._store is not None:
            with contextlib.suppress(Exception):
                self._store.delete(namespace, stored_key)
            if project_id is None:
                with contextlib.suppress(Exception):
                    self._store.delete(namespace, key)
