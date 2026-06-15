"""Workload specs for the A/B subprocess runner.

A workload is a small JSON-serializable dict describing what the child should
do against the candidate code. It must be serializable because it crosses the
process boundary as a CLI argument to the fresh-interpreter child — we never
ship a Python callable/pickle across the boundary (that would risk importing
candidate code in the parent and undermine isolation).

The child (``_child.py``) interprets the spec and runs it. Each workload kind
is responsible for asserting its own success conditions; the child only prints
the ``RESULT_OK`` sentinel when the workload returns without raising.
"""

from __future__ import annotations

from typing import Any

Workload = dict[str, Any]

KIND_IMPORT_MODULE = "import_module"

# The literal sentinel line the child prints AFTER a workload's assertions pass.
# Lives here (not in _child) so the parent runner can import it without loading
# the child entrypoint module — which would trigger a runpy double-import
# RuntimeWarning when the child is later launched via ``python -m``.
RESULT_SENTINEL = "RESULT_OK"


def import_module_workload(
    module: str, expect_attr: str | None = None
) -> Workload:
    """A workload that imports ``module`` from the candidate's ``src`` and,
    optionally, asserts that ``expect_attr`` is present on it.

    Importing the target module is the minimal, honest exercise of a code
    variant: if the variant crashes at import (raise, ``os._exit``, segfault),
    the child process dies and the parent observes a non-OK result without ever
    importing the module itself.
    """
    spec: Workload = {"kind": KIND_IMPORT_MODULE, "module": module}
    if expect_attr is not None:
        spec["expect_attr"] = expect_attr
    return spec
