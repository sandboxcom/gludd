"""D-30: ``importlib.import_module`` on operator-controlled strings must be
gated by a strict allowlist so a hostile config value like ``"module": "os"``
cannot trigger arbitrary code execution at connector registration time.

Covers both the ``module`` selector and the ``class`` selector (dotted path),
and confirms that legitimate connector modules still load.
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.registry import (
    _ALLOWED_CONNECTOR_MODULES,
    ConnectorRegistry,
    _check_module_allowlist,
)


# -- allowlist surface ------------------------------------------------------- #
def test_allowlist_is_nonempty_frozenset_of_package_modules() -> None:
    assert isinstance(_ALLOWED_CONNECTOR_MODULES, frozenset)
    assert len(_ALLOWED_CONNECTOR_MODULES) > 10
    assert all(m.startswith("general_ludd.connectors.") for m in _ALLOWED_CONNECTOR_MODULES)


@pytest.mark.parametrize(
    "bad_module",
    [
        "os",
        "os.path",
        "subprocess",
        "socket",
        "general_ludd.daemon",  # inside the codebase but NOT a connector
        "general_ludd.connectors.definitely_not_real",
        "evil.attacker.module",
    ],
)
def test_disallowed_modules_rejected_at_allowlist_check(bad_module: str) -> None:
    with pytest.raises(ValueError, match=r"allowlist|denied"):
        _check_module_allowlist(bad_module, selector="module")


def test_allowed_module_passes_allowlist_check() -> None:
    sample = next(iter(_ALLOWED_CONNECTOR_MODULES))
    _check_module_allowlist(sample, selector="module")


# -- registry-level behavior ------------------------------------------------- #
def test_registry_rejects_hostile_module_selector() -> None:
    reg = ConnectorRegistry.from_config(
        [{"name": "pwn", "module": "os"}]
    )
    errors = reg.errors()
    assert len(errors) == 1
    assert errors[0]["name"] == "pwn"
    assert "discovery" in errors[0]["error"]
    assert reg.names() == []


def test_registry_rejects_hostile_class_selector() -> None:
    reg = ConnectorRegistry.from_config(
        [{"name": "pwn2", "class": "os:system"}]
    )
    errors = reg.errors()
    assert len(errors) == 1
    assert reg.names() == []


def test_registry_allows_known_connector_module() -> None:
    # prometheus is a canonical, always-present connector module.
    reg = ConnectorRegistry.from_config(
        [{"name": "prom", "module": "prometheus"}]
    )
    # Either it registered cleanly OR it failed at *construction* (missing
    # env/transport) — but it MUST NOT have been rejected at the allowlist
    # (discovery) stage, which is the D-30 bug.
    discovery_errors = [
        e for e in reg.errors() if "discovery" in e.get("error", "")
    ]
    assert discovery_errors == [], discovery_errors
