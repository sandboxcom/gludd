"""Tests for the connector-registry module-import allowlist (P1 security fix)
and the _SourceLike preflight check (P2).

P1 — arbitrary-code-exec via config:
  The ``class`` and ``module`` config selectors previously called
  importlib.import_module on operator-controlled strings with no allowlist.
  A hostile operator could set ``"module": "os"`` or ``"class": "os:system"``
  to import arbitrary stdlib/site-packages code.

  Fix: _check_module_allowlist() is called BEFORE any import_module; it raises
  ValueError (caught into _errors) if the resolved module path does not start
  with ``general_ludd.connectors``.

P2 — _SourceLike preflight:
  After construction, the registry verifies the returned object satisfies the
  _SourceLike structural protocol (name, KIND, health, query).  Non-conforming
  objects are rejected into _errors rather than stored in _sources.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.connectors.registry import ConnectorRegistry, _check_module_allowlist

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _GoodSource:
    """Minimal _SourceLike-conformant double."""

    KIND = "metrics"

    def __init__(self, config: dict[str, Any]) -> None:
        self.name = str(config.get("name") or "good")

    def health(self) -> dict[str, Any]:
        return {"ok": True}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _NotSourceLike:
    """Object that does NOT satisfy _SourceLike (missing name/KIND/health/query)."""

    def __init__(self, config: dict[str, Any]) -> None:
        pass  # no name, KIND, health, or query


# --------------------------------------------------------------------------- #
# P1 — _check_module_allowlist unit tests
# --------------------------------------------------------------------------- #


class TestCheckModuleAllowlist:
    """Direct unit tests for the allowlist helper (no I/O)."""

    # ---- selector="module" -------------------------------------------------

    def test_module_bare_name_with_prefix_passes(self) -> None:
        """A bare name that was already expanded to general_ludd.connectors.X passes."""
        # This mirrors what _resolve_factory does for a bare module name.
        _check_module_allowlist("general_ludd.connectors.prometheus", selector="module")

    def test_module_connectors_pkg_itself_denied(self) -> None:
        """The bare general_ludd.connectors package is NOT an importable
        connector — only concrete submodules are allowed (D-30 fix)."""
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("general_ludd.connectors", selector="module")

    def test_module_os_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os", selector="module")

    def test_module_os_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.path", selector="module")

    def test_module_os_system_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.system", selector="module")

    def test_module_subprocess_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("subprocess", selector="module")

    def test_module_general_ludd_non_connector_rejected(self) -> None:
        """Modules elsewhere in the package (not under connectors.) are rejected."""
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("general_ludd.gateway", selector="module")

    def test_module_prefix_lookalike_rejected(self) -> None:
        """A module that starts with a prefix lookalike but isn't the package."""
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("general_ludd.connectors_evil", selector="module")

    # ---- selector="class" --------------------------------------------------

    def test_class_colon_form_valid_prefix_passes(self) -> None:
        _check_module_allowlist(
            "general_ludd.connectors.prometheus:PrometheusSource", selector="class"
        )

    def test_class_dot_form_valid_prefix_passes(self) -> None:
        _check_module_allowlist(
            "general_ludd.connectors.datadog.DatadogSource", selector="class"
        )

    def test_class_os_colon_system_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os:system", selector="class")

    def test_class_os_system_dot_form_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("os.system", selector="class")

    def test_class_subprocess_popen_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("subprocess:Popen", selector="class")

    def test_class_shutil_colon_rmtree_rejected(self) -> None:
        with pytest.raises(ValueError, match="module import denied"):
            _check_module_allowlist("shutil:rmtree", selector="class")


# --------------------------------------------------------------------------- #
# P1 — from_config integration: hostile module/class values are blocked
# --------------------------------------------------------------------------- #


class TestImportGuardIntegration:
    """End-to-end: hostile config values are rejected; no import occurs."""

    def test_module_os_bare_name_expanded_and_safe(self) -> None:
        """'module': 'os' (bare, no dots) is expanded to general_ludd.connectors.os
        by _resolve_factory BEFORE the allowlist check, so it passes the prefix
        guard.  This is the intended safe path: bare names are always scoped to
        the connectors package.  The import will fail (no such module) but the
        allowlist does NOT block it.  We assert that whatever import IS attempted
        targets the scoped path, never raw 'os'.
        """
        with patch("importlib.import_module") as mock_import:
            mock_import.side_effect = ModuleNotFoundError("mocked")
            reg = ConnectorRegistry.from_config(
                [{"name": "evil", "kind": "logs", "module": "os"}]
            )
        # The import was attempted, but only for the SCOPED path.
        if mock_import.called:
            called_path = mock_import.call_args[0][0]
            assert called_path == "general_ludd.connectors.os", (
                f"expected import of scoped path but got {called_path!r}"
            )
        # The source is not added (import or construction failed).
        assert reg.get("evil") is None
        assert "evil" not in reg.names()
        # An error is recorded.
        assert len(reg.errors()) == 1

    def test_module_os_system_dotted_not_imported(self) -> None:
        """'module': 'os.system' (dotted, bypasses bare-name prefix) is rejected."""
        with patch("importlib.import_module") as mock_import:
            reg = ConnectorRegistry.from_config(
                [{"name": "evil2", "kind": "logs", "module": "os.system"}]
            )
        mock_import.assert_not_called()
        assert reg.get("evil2") is None
        assert reg.errors()[0]["name"] == "evil2"

    def test_class_os_colon_system_not_imported(self) -> None:
        """'class': 'os:system' must be rejected before any importlib call."""
        with patch("importlib.import_module") as mock_import:
            reg = ConnectorRegistry.from_config(
                [{"name": "evil3", "kind": "logs", "class": "os:system"}]
            )
        mock_import.assert_not_called()
        assert reg.get("evil3") is None
        assert reg.errors()[0]["name"] == "evil3"

    def test_class_subprocess_popen_not_imported(self) -> None:
        """'class': 'subprocess:Popen' must be blocked."""
        with patch("importlib.import_module") as mock_import:
            reg = ConnectorRegistry.from_config(
                [{"name": "evil4", "kind": "logs", "class": "subprocess:Popen"}]
            )
        mock_import.assert_not_called()
        assert reg.get("evil4") is None

    def test_legit_module_bare_name_passes_allowlist_and_loads(self) -> None:
        """A bare connector module name (e.g. 'prometheus') expands to
        general_ludd.connectors.prometheus, passes the allowlist, and is
        imported (mocked here so no real network/disk side effects).
        """
        import types

        _MOD_PATH = "general_ludd.connectors.prometheus"

        # Build a real module object so vars() works correctly in
        # _single_source_class (MagicMock.__dict__ is not what vars() returns).
        mock_mod = types.ModuleType(_MOD_PATH)

        class PrometheusSource:
            __module__ = _MOD_PATH
            KIND = "metrics"

            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config.get("name") or "prom")

            def health(self) -> dict[str, Any]:
                return {"ok": True}

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        mock_mod.PrometheusSource = PrometheusSource  # type: ignore[attr-defined]

        with patch("importlib.import_module", return_value=mock_mod) as mock_import:
            reg = ConnectorRegistry.from_config(
                [{"name": "prom", "kind": "metrics", "module": "prometheus"}]
            )

        # import_module must have been called with the full path
        mock_import.assert_called_once_with(_MOD_PATH)
        # The source is registered
        assert reg.get("prom") is not None
        assert reg.errors() == []

    def test_legit_class_dotted_path_passes_allowlist_and_loads(self) -> None:
        """A 'class' selector rooted at general_ludd.connectors passes and imports."""
        import types

        _MOD_PATH = "general_ludd.connectors.datadog"
        mock_mod = types.ModuleType(_MOD_PATH)

        class DatadogSource:
            __module__ = _MOD_PATH
            KIND = "metrics"

            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config.get("name") or "dd")

            def health(self) -> dict[str, Any]:
                return {"ok": True}

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        mock_mod.DatadogSource = DatadogSource  # type: ignore[attr-defined]

        with patch("importlib.import_module", return_value=mock_mod) as mock_import:
            reg = ConnectorRegistry.from_config(
                [
                    {
                        "name": "dd",
                        "kind": "metrics",
                        "class": "general_ludd.connectors.datadog:DatadogSource",
                    }
                ]
            )

        mock_import.assert_called_once_with(_MOD_PATH)
        assert reg.get("dd") is not None
        assert reg.errors() == []

    def test_factory_selector_bypasses_import_allowlist(self) -> None:
        """A 'factory' key is looked up in the injected map, never imported.

        This is the safe pre-import path; no allowlist check is needed because
        no importlib call is made.
        """
        reg = ConnectorRegistry.from_config(
            [{"name": "safe", "kind": "metrics", "factory": "good"}],
            factories={"good": _GoodSource},
        )
        assert reg.get("safe") is not None
        assert reg.errors() == []


# --------------------------------------------------------------------------- #
# P2 — _SourceLike preflight: non-conforming object is rejected
# --------------------------------------------------------------------------- #


class TestSourceLikePreflight:
    def test_non_sourcelike_object_rejected_not_stored(self) -> None:
        """A factory that returns an object missing _SourceLike members must
        be caught, produce an error entry, and NOT be stored in _sources.

        This may happen at either of two layers: the class-level preflight
        (rejects before ``__init__`` runs, when the class itself lacks
        ``health``/``query``) or the instance-level ``_SourceLike`` check
        (rejects after construction, when ``name``/``KIND`` are absent). Both
        are valid rejection points — the contract under test is that a
        non-conforming class is rejected with a clear, interface-naming error.
        """
        reg = ConnectorRegistry.from_config(
            [{"name": "broken", "kind": "logs", "factory": "bad"}],
            factories={"bad": _NotSourceLike},
        )
        assert reg.get("broken") is None
        assert "broken" not in reg.names()
        errors = reg.errors()
        assert len(errors) == 1
        err = errors[0]
        assert err["name"] == "broken"
        # Either layer's message clearly names the Source interface gap.
        assert (
            "_SourceLike" in err["error"]
            or "satisfy" in err["error"].lower()
            or "required Source method" in err["error"]
        )

    def test_sourcelike_conformant_object_stored(self) -> None:
        """A factory returning a _SourceLike-conformant object is accepted."""
        reg = ConnectorRegistry.from_config(
            [{"name": "ok", "kind": "metrics", "factory": "good"}],
            factories={"good": _GoodSource},
        )
        assert reg.get("ok") is not None
        assert reg.errors() == []

    def test_non_sourcelike_does_not_appear_in_list_sources(self) -> None:
        """Non-conforming sources never pollute list_sources()."""
        reg = ConnectorRegistry.from_config(
            [
                {"name": "bad", "kind": "logs", "factory": "bad"},
                {"name": "good", "kind": "metrics", "factory": "good"},
            ],
            factories={"bad": _NotSourceLike, "good": _GoodSource},
        )
        names = [s["name"] for s in reg.list_sources()]
        assert "bad" not in names
        assert "good" in names

    def test_non_sourcelike_does_not_appear_in_health_all(self) -> None:
        """health_all() must not include the rejected non-conforming source."""
        reg = ConnectorRegistry.from_config(
            [{"name": "broken", "kind": "logs", "factory": "bad"}],
            factories={"bad": _NotSourceLike},
        )
        assert reg.health_all() == {}

    def test_non_sourcelike_query_raises_keyerror(self) -> None:
        """query() for a rejected source raises KeyError (it was never stored)."""
        reg = ConnectorRegistry.from_config(
            [{"name": "broken", "kind": "logs", "factory": "bad"}],
            factories={"bad": _NotSourceLike},
        )
        with pytest.raises(KeyError):
            reg.query("broken", {})
