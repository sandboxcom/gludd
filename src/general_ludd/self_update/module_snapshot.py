"""Module snapshot and rollback for hot-reloaded modules.

Saves pre-reload ``sys.modules`` state so old module versions can be restored
if the new version fails its health gate or raises at import time.
"""

from __future__ import annotations

import gc
import sys
import threading
import time
from types import ModuleType

from general_ludd.self_update.module_snapshot_types import ModuleSnapshot as ModuleSnapshot

_EXTENSION_SUFFIXES: frozenset[str] = frozenset(
    {".so", ".pyd", ".dylib", ".dll"}
)

_SINGLETON_LIKE_NAMES: frozenset[str] = frozenset(
    {
        "pool", "client", "connection", "_pool", "_client", "_connection",
        "session", "_session", "engine", "_engine", "cache", "_cache",
        "registry", "_registry", "manager", "repo", "repository",
    }
)


_lock = threading.Lock()


def snapshot_modules(module_names: list[str]) -> ModuleSnapshot:
    """Take a backup of *module_names* from ``sys.modules``.

    Thread-safe: acquires a lock so the snapshot is consistent with respect to
    any concurrent ``restore_modules`` call.

    C extension modules (``.so``, ``.pyd``, ``.dylib``, ``.dll``) cannot be
    deep-copied or reloaded safely — they are skipped with a warning.
    Module-level singletons (global connection pools, caches, etc.) are
    detected heuristically and logged as warnings; the snapshot still proceeds
    but the caller is informed that stale state may persist.
    """
    snapshot = ModuleSnapshot()
    with _lock:
        snapshot.snapshot_at = time.monotonic()
        for name in module_names:
            module = sys.modules.get(name)
            if module is None:
                continue

            if _is_extension_module(module):
                snapshot.warnings.append(
                    f"{name}: C extension module — cannot snapshot, skip"
                )
                continue

            snapshot.modules[name] = module
            snapshot.namespaces[name] = dict(module.__dict__)
            _warn_singletons(name, module, snapshot.warnings)

    return snapshot


def restore_modules(snapshot: ModuleSnapshot) -> list[str]:
    """Restore module objects and namespaces from *snapshot*.

    ``importlib.reload`` is deliberately not used here: it executes the live
    source into the existing module namespace and creates new class objects.
    Consumers that imported a class before the reload then fail identity checks
    even though both classes have the same qualified name.  Restoring the
    captured namespace preserves the pre-reload functions/classes and makes
    rollback a real in-memory rollback rather than another forward reload.

    Thread-safe: acquires the same lock as :func:`snapshot_modules` so
    snapshot and restore cannot interleave.

    Returns the list of module names that were successfully restored.
    """
    restored: list[str] = []
    with _lock:
        for name, old_module in snapshot.modules.items():
            old_namespace = snapshot.namespaces.get(name)
            if old_namespace is not None:
                namespace = old_module.__dict__
                namespace.clear()
                namespace.update(old_namespace)
            sys.modules[name] = old_module
            parent_name, separator, child_name = name.rpartition(".")
            if separator:
                parent = sys.modules.get(parent_name)
                if parent is not None:
                    setattr(parent, child_name, old_module)
            restored.append(name)
    return restored


def find_live_references(module_name: str) -> list[str]:
    """Discover objects holding references to the current module.

    Use ``gc.get_referrers()`` on ``sys.modules[module_name]``.
    Returns a list of ``"<type_name> at <id>"`` strings. Call after a reload
    to audit whether stale references to the old module still exist in live
    objects (which would prevent the old module from being garbage collected
    and may hold stale closure state).
    """
    module = sys.modules.get(module_name)
    if module is None:
        return []

    refs: list[str] = []
    seen_ids: set[int] = set()
    for ref in gc.get_referrers(module):
        rid = id(ref)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)

        if isinstance(ref, dict):
            for k, v in ref.items():
                if v is module:
                    refs.append(f"{type(ref).__name__}[{k!r}] at {rid}")
                    break
            else:
                refs.append(f"{type(ref).__name__}(...) at {rid}")
        elif isinstance(ref, ModuleType):
            refs.append(f"module {getattr(ref, '__name__', '?')} at {rid}")
        else:
            typ = type(ref).__qualname__
            refs.append(f"{typ} at {rid}")

    return refs


def _is_extension_module(module: ModuleType) -> bool:
    """Return True if *module* is a C extension module.

    Extension modules cannot be reloaded reliably and should not be snapshotted.
    """
    file_path = getattr(module, "__file__", None)
    if file_path is not None:
        for suffix in _EXTENSION_SUFFIXES:
            if file_path.endswith(suffix):
                return True

    loader = getattr(module, "__loader__", None)
    if loader is not None:
        loader_name = type(loader).__qualname__.lower()
        if "extension" in loader_name or "ext" in loader_name:
            return True

    return False


def _warn_singletons(
    module_name: str, module: ModuleType, warnings: list[str]
) -> None:
    """Detect module-level singleton state.

    Log a warning so the caller is aware that globals, connection pools, or
    similar singleton state may persist after a rollback.
    """
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        lower = attr_name.lower()
        for needle in _SINGLETON_LIKE_NAMES:
            if needle in lower:
                try:
                    obj = getattr(module, attr_name)
                except Exception:
                    continue
                if obj is not None and not callable(obj):
                    warnings.append(
                        f"{module_name}.{attr_name}: module-level singleton "
                        f"({type(obj).__qualname__}) — stale state may "
                        f"persist after rollback"
                    )
                    break
