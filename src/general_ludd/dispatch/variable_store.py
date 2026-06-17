"""VariableStore: namespaced live variables for inter-turn template rendering.

A ``DispatchResult`` can be written into a namespace and the next turn's
Jinja2 template re-rendered with the updated variables so the model sees
fresh reality in its next prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Undefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from general_ludd.dispatch.dynamic_dispatcher import DispatchResult

logger = logging.getLogger(__name__)


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
        self._store: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Core set / get
    # ------------------------------------------------------------------

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Set ``namespace.key = value``."""
        self._store.setdefault(namespace, {})[key] = value

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Return ``namespace.key`` or ``default`` if absent."""
        return self._store.get(namespace, {}).get(key, default)

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        """Return a shallow copy of all variables in ``namespace``."""
        return dict(self._store.get(namespace, {}))

    def all_vars(self) -> dict[str, Any]:
        """Flatten the store into a single dict for template rendering.

        Keys are ``"namespace__key"`` to avoid collision between namespaces.
        Additionally the top-level namespace keys are aliased as bare names
        when there is no collision, but the ``__`` form always wins.
        """
        flat: dict[str, Any] = {}
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
        ctx: dict[str, Any] = {**self.all_vars(), **extra}
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
        safe_name = result.name.replace(".", "_DOT_").replace("-", "_DASH_")
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
