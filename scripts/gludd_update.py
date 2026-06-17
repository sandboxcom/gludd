#!/usr/bin/env python3
"""gludd_update — operator-facing CLI that turns an "update gludd: <text>"
request into a PRIORITIZED todo spec.

This is part 3 of issue #81 (the *operator surface*). Parts 1/2 — the
``UpdateRequestRouter`` and the ``applier`` that actually mutate the agent's own
source — live in :mod:`general_ludd.self_update` and are owned by other work.
This CLI deliberately does **not** import them at module load time: it performs a
single *lazy, guarded* import inside :func:`load_router` so that

* the script always imports cleanly (and so its unit tests run) even when the
  router module is absent or broken, and
* a missing router degrades to a clear ``router unavailable`` notice and a
  non-zero exit, **never** an unhandled exception.

Flow
----
1. Parse ``"update gludd: <free text>"`` into the request text.
2. Lazily route the text through ``UpdateRequestRouter`` to get an
   ``UpdatePlan`` (subsystem, target kind/paths, capability_required, risk).
3. Derive a ``priority`` from the plan's target kind:

   ===================  ==========  =============
   target kind          priority    needs_review
   ===================  ==========  =============
   ``config`` / ``yaml``  ``high``    no   (fast-track)
   ``role``               ``medium``  no
   ``code`` (anything     ``low``     yes  (code change → human review)
   else / unknown)
   ===================  ==========  =============

4. Emit a *todo spec* — a JSON object carrying ``title``, the full ``plan``, the
   derived ``priority`` and ``needs_review`` flag — to stdout, OR hand it to an
   injected ``todo_creator`` callable (used by tests and by an in-process caller
   that wants to create the todo directly rather than shell out).

The script creates **nothing** by itself: it only *emits a spec*. The Ansible
``gludd_update`` role (and, later, the daemon ``/api`` endpoint) is what turns
the spec into an actual todo via ``gludd_db todo_create``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

#: Prefix that marks an operator update request. Matched case-insensitively with
#: optional surrounding whitespace, e.g. ``"  Update Gludd:  add a knob "``.
REQUEST_PREFIX = "update gludd:"

#: Fully-qualified location of the (separately-owned) router. Imported lazily so
#: this module never hard-depends on it.
ROUTER_MODULE = "general_ludd.self_update.router"
ROUTER_ATTR = "UpdateRequestRouter"

#: Message printed (to stderr) when the router cannot be imported.
ROUTER_UNAVAILABLE_NOTICE = (
    "router unavailable: could not import "
    f"{ROUTER_MODULE}.{ROUTER_ATTR} — the self-update router is not installed "
    "in this build, so 'update gludd:' requests cannot be routed here. "
    "(This is expected in environments that ship the operator CLI without the "
    "self-update subsystem.)"
)

# Priority ladder. config/yaml is fast-tracked; role is mid; code is lowest and
# additionally flagged for human review.
PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"


class RequestParseError(ValueError):
    """Raised when the argument is not a well-formed ``update gludd:`` request."""


def parse_request(raw: str) -> str:
    """Extract the free-text body from an ``"update gludd: <text>"`` request.

    The prefix match is case-insensitive and tolerant of surrounding
    whitespace. Returns the stripped body. Raises :class:`RequestParseError`
    if the prefix is missing or the body is empty.
    """
    if not isinstance(raw, str):
        raise RequestParseError("request must be a string")
    stripped = raw.strip()
    if not stripped.lower().startswith(REQUEST_PREFIX):
        raise RequestParseError(
            f"request must start with {REQUEST_PREFIX!r} (got {raw!r})"
        )
    body = stripped[len(REQUEST_PREFIX):].strip()
    if not body:
        raise RequestParseError("request body after the prefix is empty")
    return body


def load_router() -> type | None:
    """Lazily, guardedly import the ``UpdateRequestRouter`` class.

    Returns the class on success, or ``None`` if the module/attribute is absent
    or import raises for any reason. **Never** propagates an exception — a
    missing router is a normal, handled state for this CLI.
    """
    try:
        import importlib

        module = importlib.import_module(ROUTER_MODULE)
        router_cls = getattr(module, ROUTER_ATTR, None)
        if router_cls is None or not isinstance(router_cls, type):
            return None
        return router_cls
    except Exception:
        # Any import-time failure (ModuleNotFoundError, a broken transitive
        # import, etc.) is treated as "router unavailable" — fail-closed but
        # never raise out of the CLI.
        return None


def _plan_attr(plan: Any, name: str, default: Any = None) -> Any:
    """Read an attribute from an UpdatePlan, tolerating dataclass *or* dict.

    The plan type is owned elsewhere; this CLI only consumes it, so it reads
    fields defensively (attribute first, then mapping key) and never assumes a
    concrete class.
    """
    if isinstance(plan, dict):
        return plan.get(name, default)
    return getattr(plan, name, default)


def _target_kind(plan: Any) -> str:
    """Return the plan's target kind as a lowercase string ("" if absent)."""
    # Accept several plausible field names the router might expose.
    for field in ("target_kind", "kind", "target_type"):
        value = _plan_attr(plan, field)
        if value:
            return str(value).strip().lower()
    return ""


def derive_priority(plan: Any) -> tuple[str, bool]:
    """Derive ``(priority, needs_review)`` from an ``UpdatePlan``.

    * ``config`` / ``yaml`` → fast-track ``high``; no review.
    * ``role``              → ``medium``; no review.
    * anything else (``code`` or unknown) → ``low`` + ``needs_review=True``.

    Code changes mutate the agent's own behaviour, so they are deliberately the
    lowest priority *and* carry a human-review flag — the operator surface never
    silently fast-tracks a code self-modification.
    """
    kind = _target_kind(plan)
    if kind in ("config", "yaml", "yml"):
        return PRIORITY_HIGH, False
    if kind == "role":
        return PRIORITY_MEDIUM, False
    # code, or unknown/unspecified — treat conservatively as a reviewable change.
    return PRIORITY_LOW, True


def _plan_to_dict(plan: Any) -> dict[str, Any]:
    """Serialise the carried UpdatePlan into a JSON-safe dict.

    Reads the fields the operator surface cares about (subsystem, target
    kind/paths, capability_required, risk) defensively, so an UpdatePlan with
    extra or missing fields still produces a stable spec shape.
    """
    paths = _plan_attr(plan, "target_paths")
    if paths is None:
        paths = _plan_attr(plan, "paths", [])
    if isinstance(paths, (list, tuple)):
        paths = [str(p) for p in paths]
    elif paths:
        paths = [str(paths)]
    else:
        paths = []

    return {
        "subsystem": _plan_attr(plan, "subsystem"),
        "target_kind": _target_kind(plan) or None,
        "target_paths": paths,
        "capability_required": _plan_attr(plan, "capability_required"),
        "risk": _plan_attr(plan, "risk"),
    }


def _derive_title(text: str) -> str:
    """Build a concise todo title from the request body."""
    one_line = " ".join(text.split())
    if len(one_line) > 80:
        one_line = one_line[:77].rstrip() + "..."
    return f"update gludd: {one_line}"


def build_todo_spec(text: str, plan: Any) -> dict[str, Any]:
    """Assemble the prioritized todo spec from request ``text`` and ``plan``."""
    priority, needs_review = derive_priority(plan)
    return {
        "title": _derive_title(text),
        "request_text": text,
        "plan": _plan_to_dict(plan),
        "priority": priority,
        "needs_review": needs_review,
        "work_type": "self_update",
    }


def route_request(
    text: str,
    router_cls: type,
) -> Any:
    """Instantiate the router and route ``text`` to an UpdatePlan.

    The router's construction/route entrypoint is discovered defensively so this
    CLI is not coupled to one exact signature: it tries ``route`` then
    ``route_request`` then ``__call__``.
    """
    router = router_cls()
    for method_name in ("route", "route_request", "plan"):
        method = getattr(router, method_name, None)
        if callable(method):
            return method(text)
    # Fall back to calling the instance directly.
    if callable(router):
        return router(text)
    raise RuntimeError(
        f"{router_cls.__name__} exposes no route()/route_request()/__call__ entrypoint"
    )


def run(
    raw_request: str,
    todo_creator: Callable[[dict[str, Any]], Any] | None = None,
    out: Any = None,
    err: Any = None,
) -> int:
    """Core entrypoint (testable). Returns a process exit code.

    Parameters
    ----------
    raw_request:
        The full ``"update gludd: <text>"`` string.
    todo_creator:
        Optional callable; if given, the spec is handed to it instead of being
        printed as JSON. Lets an in-process caller (or a test) create the todo
        directly.
    out / err:
        Streams for normal/diagnostic output (default ``sys.stdout`` /
        ``sys.stderr``); injectable for tests.

    Never raises for a missing router — that path prints the notice and returns
    a non-zero code.
    """
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    try:
        text = parse_request(raw_request)
    except RequestParseError as exc:
        print(f"error: {exc}", file=err)
        return 2

    router_cls = load_router()
    if router_cls is None:
        print(ROUTER_UNAVAILABLE_NOTICE, file=err)
        return 3

    try:
        plan = route_request(text, router_cls)
    except Exception as exc:  # router present but routing failed
        print(f"error: routing failed: {exc}", file=err)
        return 4

    spec = build_todo_spec(text, plan)

    if todo_creator is not None:
        todo_creator(spec)
    else:
        json.dump(spec, out, indent=2, sort_keys=True)
        out.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """argparse front-end. Returns an exit code (does not call sys.exit)."""
    parser = argparse.ArgumentParser(
        prog="gludd_update",
        description=(
            "Turn an 'update gludd: <text>' request into a prioritized todo "
            "spec (emitted as JSON)."
        ),
    )
    parser.add_argument(
        "request",
        help="the request, e.g. \"update gludd: raise the model timeout to 60s\"",
    )
    args = parser.parse_args(argv)
    return run(args.request)


if __name__ == "__main__":
    sys.exit(main())
