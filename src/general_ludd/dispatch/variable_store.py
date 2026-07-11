"""VariableStore: namespaced live variables for inter-turn template rendering.

A ``DispatchResult`` can be written into a namespace and the next turn's
Jinja2 template re-rendered with the updated variables so the model sees
fresh reality in its next prompt.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jinja2 import Undefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

logger = logging.getLogger(__name__)

#: C15 defect 4 (key injection): a VariableStore key is flattened into a Jinja2
#: template context name as ``namespace__key``, so a key carrying a path
#: separator, NUL byte, or traversal component could smuggle an unintended
#: template variable name or collide with reserved sentinel keys. Restrict keys
#: to the safe character class actually used by the flattening convention
#: (alphanumerics, underscore, dot, dash, colon — the same class MCP tool names
#: allow). Anything else is rejected fail-closed by ``VariableStore.set``.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

#: Reserved dispatch key STEMS that ``apply_results`` writes unconditionally for
#: the "last result" sentinel. A model-controlled dispatch ``name`` equal to one
#: of these would clobber the sentinel; ``_safe_dispatch_name`` escapes it.
_RESERVED_DISPATCH_NAMES: frozenset[str] = frozenset({"last"})

#: Suffix appended to a sanitized dispatch name that collides with a reserved
#: sentinel stem, so the escaped key can never equal ``last__*``.
_RESERVED_TOOLNAME_SUFFIX = "_TOOLNAME"


def _safe_dispatch_name(name: str) -> str:
    """Escape a model-controlled dispatch ``name`` for use as a store key.

    Dots and dashes are replaced with sentinels (preserving the existing
    ``test_apply_results_safe_name_replaces_dots_and_dashes`` contract), and a
    name that would collide with a reserved sentinel stem (e.g. ``last``) gets a
    suffix so it can never overwrite the ``dispatch__last__*`` keys.
    """
    safe = name.replace(".", "_DOT_").replace("-", "_DASH_")
    if safe in _RESERVED_DISPATCH_NAMES:
        safe = f"{safe}{_RESERVED_TOOLNAME_SUFFIX}"
    return safe



class VariableStore:
    """Namespaced key-value store with Jinja2 template rendering.

    Namespaces are plain string prefixes (e.g. ``"dispatch"``, ``"tool"``,
    ``"project.foo"``).  Variable names within a namespace are arbitrary
    strings.

    ``render(template, **extra)`` renders a Jinja2 template string against the
    flattened store plus any caller-supplied extras.  Missing variables in the
    template produce an empty string (``Undefined``) rather than raising, so
    partial templates stay useful.
    """

    def __init__(self) -> None:
        # {namespace: {key: value}}
        self._store: dict[str, dict[str, object]] = {}

    # ------------------------------------------------------------------
    # Core set / get
    # ------------------------------------------------------------------

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Set ``namespace.key = value``.

        C15 defect 4: ``key`` is rejected fail-closed unless it matches the safe
        key character class (``^[A-Za-z0-9_.:-]+$``). This blocks key-injection
        via path separators (``a/b``, ``a\\b``), NUL bytes, and traversal
        (``../../etc/passwd``) — none of which can appear in a legitimate
        flattened ``namespace__key`` template variable name.
        """
        if not isinstance(key, str) or not _SAFE_KEY_RE.match(key):
            raise ValueError(
                f"invalid VariableStore key {key!r}: must match "
                r"^[A-Za-z0-9_.:-]+$ (path separators, NUL bytes, and traversal "
                "components are not allowed)"
            )
        self._store.setdefault(namespace, {})[key] = value

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Return ``namespace.key`` or ``default`` if absent."""
        return self._store.get(namespace, {}).get(key, default)

    def get_namespace(self, namespace: str) -> dict[str, object]:
        """Return a shallow copy of all variables in ``namespace``."""
        return dict(self._store.get(namespace, {}))

    def all_vars(self) -> dict[str, object]:
        """Flatten the store into a single dict for template rendering.

        Keys are ``"namespace__key"`` to avoid collision between namespaces.
        Additionally the top-level namespace keys are aliased as bare names
        when there is no collision, but the ``__`` form always wins.
        """
        flat: dict[str, object] = {}
        for ns, kvs in self._store.items():
            for k, v in kvs.items():
                flat[f"{ns}__{k}"] = v
        return flat

    # ------------------------------------------------------------------
    # Template rendering
    # ------------------------------------------------------------------

    def render(self, template: str, **extra: Any) -> str:
        """Render a Jinja2 template string against the current store + extras.

        Missing variables resolve to an empty string (not a hard error) so
        partial templates remain useful when only some vars are populated yet.
        """
        # SandboxedEnvironment (not plain Environment): a dispatch result's
        # output can carry attacker-influenced text, and render() evaluates the
        # template against those vars. The sandbox blocks attribute access to
        # dunders / globals so a `{{ ().__class__.__mro__ }}`-style SSTI payload
        # cannot reach Python internals. Blocked access raises and is caught
        # below (fail-open returns the raw template, never the evaluated escape).
        env = SandboxedEnvironment(undefined=Undefined, autoescape=select_autoescape())
        ctx: dict[str, object] = {**self.all_vars(), **extra}
        try:
            tmpl = env.from_string(template)
            return tmpl.render(**ctx)
        except Exception as exc:
            logger.warning("VariableStore.render failed: %s", exc)
            return template  # fail-open: return the raw template on render error


# ---------------------------------------------------------------------------
# apply_results
# ---------------------------------------------------------------------------

def apply_results(store: VariableStore, results: list[DispatchResult]) -> None:
    """Merge a list of DispatchResults into the store under ``"dispatch"`` namespace.

    Each result is stored as::

        dispatch__<name>__ok        bool
        dispatch__<name>__output    Any
        dispatch__<name>__error     str | None

    Also writes the latest result under the key ``dispatch__last``.
    """
    for result in results:
        safe_name = _safe_dispatch_name(result.name)
        store.set("dispatch", f"{safe_name}__ok", result.ok)
        store.set("dispatch", f"{safe_name}__output", result.output)
        store.set("dispatch", f"{safe_name}__error", result.error)

    if results:
        last = results[-1]
        store.set("dispatch", "last__ok", last.ok)
        store.set("dispatch", "last__output", last.output)
        store.set("dispatch", "last__error", last.error)
        store.set("dispatch", "last__name", last.name)
        store.set("dispatch", "last__kind", last.kind)
