"""ConnectorRegistry — make the ~50 connectors in this package reachable.

The package already ships ~50 self-contained connectors (``prometheus.py``,
``datadog.py``, ``okta.py``, ...), each exposing the ``base.Source`` contract
(a ``KIND`` class attr, a config-driven ``__init__`` that reads secrets only
from ``*_env`` env-var NAMES, ``health()`` and ``query(spec)``). Until now there
was no single place that an operator's config list could be turned into a set of
*live, named* sources the daemon can fan queries across — so the connectors were
built but unreachable (#72/#73).

:class:`ConnectorRegistry` is that wiring point. Given an operator-supplied
config list it discovers + instantiates connectors, groups them by ``KIND``, and
offers ``list_sources()`` / ``get()`` / ``by_kind()`` / ``health_all()`` /
``query(name, spec)``.

Security posture (least-privilege + SSRF-safe)
----------------------------------------------
- **Operator-registered sources only.** Every reachable source comes from the
  operator's config list. A caller addresses a source by its *registered name*;
  there is no code path that takes a raw URL from a request and turns it into an
  egress target. ``query()``'s signature is ``(name, spec)`` — deliberately
  URL-free — so a request can never steer the daemon at an arbitrary host. The
  per-connector ``is_safe_endpoint`` / literal-host SSRF guards still apply to
  the *configured* backend URLs; this layer simply guarantees no *new* targets
  can be introduced at query time.
- **No raw secrets, ever.** Config entries carry only ``*_env`` env-var NAMES.
  Secret VALUES are resolved by each connector from ``os.environ`` at call time,
  never stored on the registry and never surfaced by :meth:`list_sources`.
- **Best-effort + total build.** A malformed/unconstructable entry is recorded
  in :meth:`errors` and skipped; it never aborts the whole build. ``health_all``
  and ``query`` never propagate a connector exception — failures become error
  records / ``{"ok": False, ...}`` dicts.

Discovery
---------
Each config entry selects a connector class one of three ways (first wins):

1. ``factory``: a key into an explicit ``factories`` map passed to
   :meth:`from_config` (used by tests and by callers that pre-import classes);
2. ``class``: a fully-qualified ``"module.path:ClassName"`` (or
   ``"module.path.ClassName"``) dotted path, imported lazily;
3. ``module`` (+ optional ``class_name``): a module under
   ``general_ludd.connectors`` whose single ``*Source`` class is used.

The chosen class is called as ``Class(config)`` — matching every connector's
``__init__(self, config, ...)`` signature (transport/http args default to a real
implementation or are injectable per-connector for tests).
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging
import pkgutil
import re
from typing import Any, Protocol, runtime_checkable

import general_ludd.connectors as _connectors_pkg
from general_ludd.connectors.normalize import auth_family

logger = logging.getLogger(__name__)

_CONNECTORS_PKG = "general_ludd.connectors"
_ALLOWED_MODULE_PREFIXES = ("general_ludd.connectors.",)


def _assert_allowed_module(mod_path: str) -> None:
    """Guard against arbitrary module imports (RCE prevention).

    Only modules under ``general_ludd.connectors.*`` are allowed.
    """
    if not any(mod_path.startswith(p) for p in _ALLOWED_MODULE_PREFIXES):
        raise ImportError(
            f"Module '{mod_path}' not in allowed prefixes {_ALLOWED_MODULE_PREFIXES}"
        )


@runtime_checkable
class _SourceLike(Protocol):
    """Structural view of a connector instance (mirrors base.Source)."""

    name: str
    KIND: str

    def health(self) -> dict[str, Any]: ...

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]: ...


# A class (or any callable) that builds a source from a config dict.
SourceFactory = Any


class ConnectorRegistry:
    """A name -> live connector map built from an operator config list."""

    @staticmethod
    def source_module_paths() -> tuple[str, ...]:
        """Return the stable, operator-selectable connector module inventory."""
        return tuple(sorted(_ALLOWED_CONNECTOR_MODULES))

    def __init__(self) -> None:
        """Create an empty, unsealed connector registry."""
        self._sources: dict[str, _SourceLike] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._errors: list[dict[str, Any]] = []

    # -- construction ------------------------------------------------------ #
    @classmethod
    def from_config(
        cls,
        configs: list[dict[str, Any]] | None,
        *,
        factories: dict[str, SourceFactory] | None = None,
    ) -> ConnectorRegistry:
        """Build a registry from a list of connector config dicts.

        Each ``config`` MUST carry a ``name`` and ``kind`` and a selector
        (``factory`` / ``class`` / ``module``). It carries connector settings
        including ``*_env`` secret NAMES — never raw secret values. A bad entry
        is recorded in :meth:`errors` and skipped.
        """
        reg = cls()
        factories = factories or {}
        for config in configs or []:
            reg._build_one(config, factories)
        return reg

    def _build_one(
        self, config: dict[str, Any], factories: dict[str, SourceFactory]
    ) -> None:
        if not isinstance(config, dict):
            self._errors.append({"name": None, "error": "config is not a dict"})
            return
        name = str(config.get("name") or "").strip()
        if not name:
            self._errors.append({"name": None, "error": "config missing 'name'"})
            return
        try:
            factory = self._resolve_factory(config, factories)
            # Class-level interface preflight: reject a factory whose class is
            # structurally incapable of satisfying the Source contract BEFORE we
            # invoke its __init__.  A malformed connector's constructor may
            # itself incur network/secret side effects; rejecting it here keeps
            # those side effects from firing just for the instance to be
            # discarded by the post-construction _SourceLike check below.
            # Explicit factory maps may contain either Source classes or
            # factory functions. Classes are safe to preflight structurally;
            # functions must be invoked before their return interface is known.
            # Non-callables are always rejected as discovery failures.
            if inspect.isclass(factory) or not callable(factory):
                _validate_source_class(factory)
        except Exception as exc:  # discovery failure — skip, never abort
            self._errors.append({"name": name, "error": f"discovery: {exc}"})
            return
        try:
            source = factory(config)
        except Exception as exc:  # construction failure — skip, never abort
            self._errors.append({"name": name, "error": f"construct: {exc}"})
            return

        # P2 preflight: the constructed object must satisfy the _SourceLike
        # structural protocol (name, KIND, health(), query()).  Reject silently
        # broken connector implementations before they pollute the registry.
        if not isinstance(source, _SourceLike):
            self._errors.append(
                {
                    "name": name,
                    "error": (
                        f"construct: factory returned an object that does not "
                        f"satisfy _SourceLike (missing name/KIND/health/query): "
                        f"{type(source)!r}"
                    ),
                }
            )
            return

        kind = str(getattr(source, "KIND", config.get("kind") or "unknown"))
        # Operator config's name is authoritative for addressing the source.
        with contextlib.suppress(Exception):  # pragma: no cover - read-only name
            source.name = name
        self._sources[name] = source
        self._meta[name] = {
            "name": name,
            "kind": kind,
            "family": _family_for(name, config),
        }

    @staticmethod
    def _resolve_factory(
        config: dict[str, Any], factories: dict[str, SourceFactory]
    ) -> SourceFactory:
        """Pick the connector class for one config entry (factory/class/module)."""
        key = config.get("factory")
        if key is not None:
            if key not in factories:
                raise KeyError(f"unknown factory {key!r}")
            return factories[key]

        dotted = config.get("class")
        if isinstance(dotted, str) and dotted:
            # Allowlist: the module portion of a dotted class path must start
            # with general_ludd.connectors. to prevent arbitrary-code-exec via
            # operator-controlled config (e.g. "class": "os:system").
            _check_module_allowlist(dotted, selector="class")
            return _import_dotted(dotted)

        module = config.get("module")
        if isinstance(module, str) and module:
            mod_path = module if "." in module else f"{_CONNECTORS_PKG}.{module}"
            _check_module_allowlist(mod_path, selector="module")
            mod = importlib.import_module(mod_path)
            class_name = config.get("class_name")
            if isinstance(class_name, str) and class_name:
                _validate_class_name(class_name)
                return getattr(mod, class_name)
            return _single_source_class(mod, mod_path)

        raise ValueError("config has no 'factory', 'class', or 'module' selector")

    # -- read surface ------------------------------------------------------ #
    def list_sources(self) -> list[dict[str, Any]]:
        """Return per-source metadata ``{name, kind, family}`` — NEVER secrets."""
        return [dict(self._meta[name]) for name in self._sources]

    def get(self, name: str) -> _SourceLike | None:
        """Return the live source registered under ``name``, or ``None``."""
        return self._sources.get(name)

    def names(self) -> list[str]:
        """Return every registered source name (registration order)."""
        return list(self._sources)

    def by_kind(self) -> dict[str, list[str]]:
        """Return ``{kind: [name, ...]}`` grouping of registered sources."""
        grouped: dict[str, list[str]] = {}
        for name, meta in self._meta.items():
            grouped.setdefault(str(meta["kind"]), []).append(name)
        return grouped

    def errors(self) -> list[dict[str, Any]]:
        """Return the list of build errors (entries that were skipped)."""
        return list(self._errors)

    # -- health ------------------------------------------------------------ #
    def health_all(self) -> dict[str, dict[str, Any]]:
        """Probe ``health()`` on every source. Never raises.

        A source whose ``health()`` itself raises is reported as
        ``{"ok": False, "error": ...}`` rather than aborting the sweep.
        """
        out: dict[str, dict[str, Any]] = {}
        for name, source in self._sources.items():
            try:
                result = source.health()
                if not isinstance(result, dict):
                    result = {"ok": bool(result)}
            except Exception:  # health must never abort the sweep
                # Don't leak str(exc) (can embed a source's DSN/credentials) into
                # the health manifest; log it for operators, return generic text.
                logger.warning("health check failed for source %s", name, exc_info=True)
                result = {"ok": False, "source": name, "error": "health check failed"}
            out[name] = result
        return out

    # -- teardown ---------------------------------------------------------- #
    def close(self) -> None:
        """Close every source without propagating teardown failures.

        Calls ``disconnect()``/``close()`` on any source
        that exposes one (e.g. a buffered source running a background thread such
        as ``MqttSource``), so rebuilding the registry on reload doesn't leak
        threads/connections. Never raises.
        """
        for name, source in self._sources.items():
            closer = getattr(source, "disconnect", None) or getattr(source, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # teardown must never abort the sweep
                    logger.warning("close failed for source %s", name, exc_info=True)

    # -- query ------------------------------------------------------------- #
    def query(self, name: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run ``query(spec)`` on the OPERATOR-REGISTERED source ``name``.

        ``name`` MUST be a source the operator registered via config — there is
        no URL parameter, so a caller can never steer this at an arbitrary host
        (the SSRF firewall). An unknown name raises :class:`KeyError`. A
        connector exception is captured as a single error record, never raised.
        """
        source = self._sources.get(name)
        if source is None:
            raise KeyError(f"no registered source named {name!r}")
        spec = spec if isinstance(spec, dict) else {}
        try:
            records = source.query(spec)
        except Exception:  # surface as a record, never raise
            # Don't leak str(exc) (can embed a source's DSN/host/credentials)
            # into the returned record; log it for operators and return generic
            # text. Mirrors the health_all() treatment above.
            logger.warning("query failed for source %s", name, exc_info=True)
            return [
                {
                    "ts": None,
                    "source": name,
                    "kind": str(getattr(source, "KIND", "unknown")),
                    "level_or_status": "error",
                    "message": "query failed",
                    "value": None,
                    "labels": {},
                    "raw": "query failed",
                }
            ]
        return list(records) if records is not None else []


# --------------------------------------------------------------------------- #
# Discovery helpers
# --------------------------------------------------------------------------- #
def _family_for(name: str, config: dict[str, Any]) -> str:
    """Classify a source's auth family from its name, then config selectors.

    Tries the registered ``name`` first (an operator often names a source after
    its backend, e.g. ``prod-datadog``), then the ``factory`` / ``module`` /
    ``class`` selector token. Returns ``"unknown"`` when nothing matches.
    """
    family = auth_family(name)
    if family != "unknown":
        return family
    for field in ("factory", "module", "class", "class_name", "kind"):
        value = config.get(field)
        if isinstance(value, str) and value:
            inferred = auth_family(value)
            if inferred != "unknown":
                return inferred
    return "unknown"


_MODULE_ALLOWLIST_PREFIX = _CONNECTORS_PKG  # "general_ludd.connectors"

# Package helpers share the connector namespace but are not operator-selectable
# Source implementations.  Keep this production-owned inventory as the single
# source of truth for both config validation and contract tests: filesystem
# presence alone is not proof that a module is a connector plugin.
_CONNECTOR_INFRASTRUCTURE_MODULE_NAMES = frozenset(
    {
        "_errors",
        "_protocols",
        "_util",
        "base",
        "cursor_adapter",
        "exc_sanitizer",
        "ingest",
        "ingest_formats",
        "normalize",
        "registry",
    }
)

# Strict allowlist of every importable connector module path under
# ``general_ludd.connectors``. Built once at import time by scanning the package
# directory (``pkgutil.iter_modules``), so it auto-maintains as connectors are
# added/removed — but it NEVER contains anything outside this package.  This is
# the D-30 fix: operator-controlled ``module``/``class`` config values are
# hard-rejected unless they resolve to a path in this frozenset, closing the
# arbitrary-code-execution hole where ``"module": "os"`` would have imported an
# arbitrary stdlib/third-party module.
_ALLOWED_CONNECTOR_MODULES: frozenset[str] = frozenset(
    f"{_CONNECTORS_PKG}.{name}"
    for _finder, name, _ispkg in pkgutil.iter_modules(_connectors_pkg.__path__)
    if name not in _CONNECTOR_INFRASTRUCTURE_MODULE_NAMES
)


# Methods every connector class MUST expose (class-level, never dynamic).
# ``KIND`` and ``name`` are intentionally NOT checked here — they are
# legitimately set per-instance in ``__init__`` (see webhook_buffer.py /
# the _FakeSource test double), so the instance-level ``_SourceLike`` check
# remains the authority for those attributes.
_REQUIRED_SOURCE_METHODS = ("health", "query")


def _validate_source_class(factory: Any) -> None:
    """Reject a factory that cannot satisfy the :class:`Source` interface.

    Called AFTER resolution but BEFORE construction, so a malformed connector
    class never has its ``__init__`` invoked. Raises :class:`TypeError` with a
    message naming every gap, so a single bad registration surfaces all of its
    interface problems at once (rather than one-per-iteration).

    Two checks:

    1. ``factory`` must be callable (a class or zero-or-more-arg callable that
       builds a source). A non-callable (e.g. a bare instance smuggled in via
       the ``factories`` map) is a discovery error, not a construct error.
    2. The class must expose callable ``health`` and ``query`` attributes.
       These are always defined on the class (inherited or direct), unlike
       ``KIND``/``name`` which connectors may legitimately assign in
       ``__init__`` and which are therefore validated post-construction by the
       ``_SourceLike`` structural check.
    """
    if not callable(factory):
        raise TypeError(
            f"connector factory {factory!r} is not callable; "
            f"expected a Source class (with health()/query())"
        )

    missing: list[str] = []
    for method_name in _REQUIRED_SOURCE_METHODS:
        attr = getattr(factory, method_name, None)
        if not callable(attr):
            missing.append(method_name)
    if missing:
        raise TypeError(
            f"connector class {_qualname(factory)} is missing required "
            f"Source method(s) and cannot satisfy _SourceLike: {', '.join(missing)}"
        )


def _qualname(obj: Any) -> str:
    """Best-effort ``Module.QualName`` for error messages; falls back to repr."""
    name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
    module = getattr(obj, "__module__", None)
    if name and module:
        return f"{module}.{name}"
    return repr(obj)


def _check_module_allowlist(path: str, *, selector: str) -> None:
    """Raise ValueError if *path* does not start with the connectors package.

    For the ``class`` selector *path* is the full ``module.path:ClassName`` or
    ``module.path.ClassName`` string — we extract the module portion first.
    For the ``module`` selector *path* is already the resolved module path.

    This is a hard-reject: it is called BEFORE any ``importlib.import_module``
    so a hostile config value like ``"os"`` or ``"os.system"`` never results in
    an import.
    """
    if selector == "class":
        # Extract the module portion from "mod.path:ClassName" or
        # "mod.path.ClassName" — same logic as _import_dotted.
        if ":" in path:
            mod_portion, _, _ = path.partition(":")
        else:
            mod_portion, _, _ = path.rpartition(".")
    else:
        mod_portion = path

    if not (
        mod_portion == _MODULE_ALLOWLIST_PREFIX
        or mod_portion.startswith(_MODULE_ALLOWLIST_PREFIX + ".")
    ):
        raise ValueError(
            f"module import denied: {path!r} (selector={selector!r}) does not "
            f"start with the required prefix {_MODULE_ALLOWLIST_PREFIX!r}. "
            f"Only connectors under general_ludd.connectors may be loaded from "
            f"operator config."
        )

    # D-30: strict allowlist — even within the connectors package, only a
    # module that actually EXISTS as a connector submodule may be imported
    # from operator config.  Blocks both arbitrary stdlib modules and
    # hypothetical not-yet-existing paths inside the package namespace.
    if mod_portion not in _ALLOWED_CONNECTOR_MODULES:
        raise ValueError(
            f"module import denied: {path!r} (selector={selector!r}) resolves "
            f"to {mod_portion!r}, which is not in the connector allowlist "
            f"({len(_ALLOWED_CONNECTOR_MODULES)} known connector modules under "
            f"{_MODULE_ALLOWLIST_PREFIX!r}). Only concrete connector submodules "
            f"may be loaded from operator config."
        )


_VALID_CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]*Source$")


def _validate_class_name(class_name: str) -> None:
    """Reject class_name values that are not valid connector Source class names.

    A valid class_name must:
    - Start with a letter (no underscore, so dunder/private attrs blocked)
    - Contain only ``[A-Za-z0-9]`` after the first character
    - End with ``Source``

    Raises ValueError for any name that could leak internal attributes via
    ``getattr(mod, class_name)`` — ``__subclasses__``, ``__init__``,
    ``__builtins__``, ``_private_attr``, ``os_systemSource``, etc.
    """
    if not _VALID_CLASS_NAME_RE.match(class_name):
        if class_name.startswith("_"):
            detail = "starts with '_' (private/dunder attrs blocked)"
        elif not class_name.endswith("Source"):
            detail = "must end with 'Source'"
        elif not class_name[0].isupper():
            detail = "must start with an uppercase letter (PascalCase class name)"
        else:
            detail = "contains invalid characters"
        raise ValueError(
            f"class_name {class_name!r} is not a valid connector class name: "
            f"{detail}"
        )


def _import_dotted(dotted: str) -> SourceFactory:
    """Import a ``module.path:ClassName`` or ``module.path.ClassName`` target."""
    if ":" in dotted:
        mod_path, _, class_name = dotted.partition(":")
    else:
        mod_path, _, class_name = dotted.rpartition(".")
    if not mod_path or not class_name:
        raise ValueError(f"malformed class path {dotted!r}")
    _assert_allowed_module(mod_path)
    mod = importlib.import_module(mod_path)
    _validate_class_name(class_name)
    return getattr(mod, class_name)


def _single_source_class(mod: Any, mod_path: str) -> SourceFactory:
    """Return the module's single ``*Source`` class, or raise if ambiguous."""
    candidates = [
        obj
        for attr, obj in vars(mod).items()
        if attr.endswith("Source")
        and isinstance(obj, type)
        and getattr(obj, "__module__", None) == mod_path
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"no *Source class in {mod_path!r}")
    raise ValueError(
        f"ambiguous: {mod_path!r} has {len(candidates)} *Source classes "
        f"(set 'class_name')"
    )
