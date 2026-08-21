"""Unit tests for the connector healthcheck contract (base.py).

Covers:
- Every known connector Source module exports a ``health()`` method
- ``run_healthcheck()`` returns a standard :class:`HealthResult`
- Unconfigured connectors return ``degraded`` (not ``unhealthy``)
- Healthcheck timeout is enforced
- ``ConnectorRegistry.health_all()`` runs health on every registered source
- SourceRegistry lists all available connectors
"""

from __future__ import annotations

import time
from typing import Any, cast
from unittest.mock import patch

import pytest

from general_ludd.connectors.base import (
    HealthResult,
    SourceRegistry,
    _is_configured,
    classify_health,
    classify_health_for_source,
    run_healthcheck,
)
from general_ludd.connectors.registry import ConnectorRegistry


# --------------------------------------------------------------------------- #
# Discovery helpers
# --------------------------------------------------------------------------- #
def _connector_module_paths() -> list[str]:
    """Return the production allowlist of operator-selectable source modules."""
    return list(ConnectorRegistry.source_module_paths())


def _source_class_for(mod_path: str) -> type | None:
    """Return the ``*Source`` or ``*Client`` class from a connector module, or None."""
    import importlib

    mod = importlib.import_module(mod_path)
    for suffix in ("Source", "Client"):
        for attr, obj in vars(mod).items():
            if attr.endswith(suffix) and isinstance(obj, type) and getattr(obj, "__module__", None) == mod_path:
                return obj
    return None


# --------------------------------------------------------------------------- #
# Fake source doubles
# --------------------------------------------------------------------------- #
class _HealthySource:
    name = "healthy"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "all good"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _UnhealthySource:
    name = "unhealthy"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {"ok": False, "detail": "backend unreachable"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _DegradedSource:
    name = "degraded"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {"ok": False, "detail": "partial outage"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _UnconfiguredSource:
    name = "unconfigured"
    KIND = "logs"

    def __init__(self) -> None:
        self.api_key_env = "MISSING_ENV_VAR_UNLIKELY_TO_EXIST"  # pragma: allowlist secret

    def health(self) -> dict[str, Any]:
        return {"ok": False, "detail": "no credentials"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _SlowSource:
    name = "slow"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        time.sleep(5.0)
        return {"ok": True}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _BoomSource:
    name = "boom"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _NonDictSource:
    name = "nondict"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return cast(Any, None)

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class _EmptyDictSource:
    name = "emptydict"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return []


# --------------------------------------------------------------------------- #
# Module-level export: every Source has health()
# --------------------------------------------------------------------------- #
class TestEveryConnectorHasHealth:
    @pytest.mark.parametrize("mod_path", _connector_module_paths())
    def test_module_has_source_class_with_health(self, mod_path: str) -> None:
        cls = _source_class_for(mod_path)
        assert cls is not None, f"{mod_path} has no *Source class"
        assert hasattr(cls, "health"), f"{cls.__qualname__} in {mod_path} has no health()"
        assert callable(cls.health), (
            f"{cls.__qualname__}.health in {mod_path} is not callable"
        )


# --------------------------------------------------------------------------- #
# classify_health — status mapping
# --------------------------------------------------------------------------- #
class TestClassifyHealth:
    def test_healthy(self) -> None:
        result = classify_health({"ok": True, "detail": "ok"}, "src")
        assert result == HealthResult(status="healthy", detail="ok", source="src")

    def test_unhealthy(self) -> None:
        result = classify_health({"ok": False, "detail": "down"}, "src")
        assert result == HealthResult(status="unhealthy", detail="down", source="src")

    def test_unhealthy_uses_error_field(self) -> None:
        result = classify_health({"ok": False, "error": "crash"}, "src")
        assert result == HealthResult(
            status="unhealthy", detail="crash", source="src"
        )

    def test_missing_ok_key(self) -> None:
        result = classify_health({"detail": "?"}, "src")
        assert result["status"] == "unhealthy"
        assert "missing 'ok' key" in result["detail"]


# --------------------------------------------------------------------------- #
# classify_health_for_source — degraded vs unhealthy
# --------------------------------------------------------------------------- #
class TestClassifyHealthForSource:
    def test_configured_unhealthy_stays_unhealthy(self) -> None:
        src = _UnhealthySource()
        with patch("general_ludd.connectors.base._is_configured", return_value=True):
            result = classify_health_for_source(
                src, {"ok": False, "detail": "backend unreachable"}
            )
        assert result["status"] == "unhealthy"

    def test_unconfigured_unhealthy_becomes_degraded(self) -> None:
        src = _UnconfiguredSource()
        # _is_configured will naturally return False since env var is missing
        result = classify_health_for_source(
            src, {"ok": False, "detail": "no credentials"}
        )
        assert result["status"] == "degraded"
        assert "unconfigured" in result["detail"]


# --------------------------------------------------------------------------- #
# _is_configured helper
# --------------------------------------------------------------------------- #
class TestIsConfigured:
    def test_source_with_no_env_attrs_assumes_configured(self) -> None:
        src = _HealthySource()
        assert _is_configured(src) is True

    def test_source_with_env_var_set_but_missing_is_not_configured(self) -> None:
        src = _UnconfiguredSource()
        assert _is_configured(src) is False

    def test_source_with_env_var_present_is_configured(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret")
        src = _UnhealthySource()
        cast(Any, src).token_env = "MY_TOKEN"
        assert _is_configured(src) is True


# --------------------------------------------------------------------------- #
# run_healthcheck — timeout
# --------------------------------------------------------------------------- #
class TestRunHealthcheck:
    def test_healthy_source_returns_healthy(self) -> None:
        result = run_healthcheck(_HealthySource(), timeout=5.0)
        assert result["status"] == "healthy"
        assert result["source"] == "healthy"

    def test_unhealthy_source_returns_unhealthy(self) -> None:
        result = run_healthcheck(_UnhealthySource(), timeout=5.0)
        assert result["status"] == "unhealthy"
        assert result["source"] == "unhealthy"

    def test_unconfigured_source_returns_degraded(self) -> None:
        result = run_healthcheck(_UnconfiguredSource(), timeout=5.0)
        assert result["status"] == "degraded"

    def test_timeout_enforced(self) -> None:
        result = run_healthcheck(_SlowSource(), timeout=0.5)
        assert result["status"] == "unhealthy"
        assert "timeout" in result["detail"]
        assert result["source"] == "slow"

    def test_exception_becomes_unhealthy_not_leak_message(self) -> None:
        result = run_healthcheck(_BoomSource(), timeout=5.0)
        assert result["status"] == "unhealthy"
        assert "exception" in result["detail"]
        assert "kaboom" not in result["detail"]

    def test_non_dict_result(self) -> None:
        result = run_healthcheck(_NonDictSource(), timeout=5.0)
        assert result["status"] == "unhealthy"

    def test_empty_dict_result(self) -> None:
        result = run_healthcheck(_EmptyDictSource(), timeout=5.0)
        assert result["status"] == "unhealthy"


# --------------------------------------------------------------------------- #
# SourceRegistry — lists all available connectors
# --------------------------------------------------------------------------- #
class TestSourceRegistryListsAll:
    def test_registry_all_returns_registered_sources(self) -> None:
        reg = SourceRegistry()
        reg.register(_HealthySource())
        reg.register(_UnhealthySource())
        names = {s.name for s in reg.all()}
        assert "healthy" in names
        assert "unhealthy" in names

    def test_registry_empty_by_default(self) -> None:
        reg = SourceRegistry()
        assert reg.all() == []

    def test_register_overwrite_by_name(self) -> None:
        reg = SourceRegistry()
        s1 = _HealthySource()
        s1.name = "dup"
        s2 = _UnhealthySource()
        s2.name = "dup"
        reg.register(s1)
        reg.register(s2)
        sources = reg.all()
        assert len(sources) == 1
        # Last write wins — unhealthy replaced healthy under the same name.
        assert isinstance(sources[0], _UnhealthySource)


# --------------------------------------------------------------------------- #
# ConnectorRegistry.health_all() — full sweep
# --------------------------------------------------------------------------- #
class TestConnectorRegistryHealthAll:
    def test_health_all_runs_health_on_all_sources(self) -> None:
        class _NamedSource:
            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config["name"])
                self.KIND = str(config.get("kind", "logs"))

            def health(self) -> dict[str, Any]:
                return {"ok": True, "detail": f"ok from {self.name}"}

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        reg = ConnectorRegistry.from_config(
            [
                {"name": "a", "kind": "logs", "factory": "named"},
                {"name": "b", "kind": "metrics", "factory": "named"},
            ],
            factories={"named": _NamedSource},
        )
        result = reg.health_all()
        assert "a" in result
        assert "b" in result
        assert result["a"]["ok"] is True
        assert result["b"]["ok"] is True

    def test_health_all_captures_exceptions(self) -> None:
        class _FailingSource:
            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config["name"])
                self.KIND = "logs"

            def health(self) -> dict[str, Any]:
                raise RuntimeError("boom")

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        reg = ConnectorRegistry.from_config(
            [{"name": "fail", "kind": "logs", "factory": "failing"}],
            factories={"failing": _FailingSource},
        )
        result = reg.health_all()
        assert "fail" in result
        assert result["fail"]["ok"] is False
        assert result["fail"]["error"] == "health check failed"

    def test_health_all_non_dict_result(self) -> None:
        class _ListSource:
            def __init__(self, config: dict[str, Any]) -> None:
                self.name = str(config["name"])
                self.KIND = "logs"

            def health(self) -> dict[str, Any]:
                return cast(Any, ["not a dict"])

            def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
                return []

        reg = ConnectorRegistry.from_config(
            [{"name": "list", "kind": "logs", "factory": "list"}],
            factories={"list": _ListSource},
        )
        result = reg.health_all()
        assert "list" in result
        # registry wraps a non-dict health() return as {"ok": bool(result)};
        # a truthy list becomes ok=True.
        assert result["list"]["ok"] is True
