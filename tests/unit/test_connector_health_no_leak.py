"""Regression guard: connector health probes must not leak exception detail.

graylog.health(), splunk.health(), and registry.health_all() previously put
`repr(exc)` / `str(exc)` straight into their returned dicts. An exception repr
can embed a source's base URL, token-env name, or a DSN with credentials, and
these health dicts are surfaced to callers (health endpoints / manifests). The
fix logs the real error (exc_info=True) and returns a static generic message.

These connector health methods construct injected transports/clients, so a
source-level guard is the most robust pin (and also catches syntax errors via
import). It asserts the leak shapes are gone and the generic messages present.
"""

from __future__ import annotations

import inspect
import re


def _src(modpath: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(modpath))


# The leak shapes we removed: error fields built from repr(exc)/str(exc).
_LEAK_RE = re.compile(r'"error":\s*(?:repr|str)\(exc\)|result\["error"\]\s*=\s*(?:repr|str)\(exc\)')


def test_graylog_health_no_exc_leak() -> None:
    src = _src("general_ludd.connectors.graylog")
    # The health except must no longer embed repr(exc) in the error field
    # (other repr(exc) uses elsewhere in the module are out of scope).
    assert '"error": repr(exc)' not in src
    assert '"error": "graylog health check failed"' in src
    assert 'logger.warning("graylog health check failed"' in src


def test_splunk_health_no_exc_leak() -> None:
    src = _src("general_ludd.connectors.splunk")
    assert 'result["error"] = repr(exc)' not in src
    assert '"splunk health check failed"' in src
    assert 'logger.warning("splunk health check failed"' in src


def test_registry_health_all_no_exc_leak() -> None:
    src = _src("general_ludd.connectors.registry")
    # health_all's except no longer embeds str(exc) in the error field.
    assert '"error": str(exc)' not in src
    assert '"error": "health check failed"' in src


def test_no_repr_or_str_exc_error_field_remains() -> None:
    for modpath in (
        "general_ludd.connectors.graylog",
        "general_ludd.connectors.splunk",
        "general_ludd.connectors.registry",
    ):
        src = _src(modpath)
        assert not _LEAK_RE.search(src), f"health-error exc leak shape remains in {modpath}"
