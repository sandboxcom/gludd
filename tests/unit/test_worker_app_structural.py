"""Structural tests for worker/app.py — FastAPI worker application."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.worker.app import (
    _GENERATION_WORK_TYPES,
    _UNSET,
    _redact_secrets,
    _resolve_compaction_config,
    build_dispatcher_from_config,
    build_gateway_from_config,
    create_app,
    get_playbook_registry,
    get_runner,
    is_generation_work_type,
)


class TestGenerationWorkTypes:
    def test_is_frozenset(self):
        assert isinstance(_GENERATION_WORK_TYPES, frozenset)

    def test_contains_expected_types(self):
        assert "code" in _GENERATION_WORK_TYPES
        assert "bug_fix" in _GENERATION_WORK_TYPES

    def test_not_empty(self):
        assert len(_GENERATION_WORK_TYPES) > 0


class TestIsGenerationWorkType:
    def test_code_is_generation(self):
        assert is_generation_work_type("code") is True

    def test_non_generation_returns_false(self):
        assert is_generation_work_type("image_inspect") is False

    def test_none_returns_false(self):
        assert is_generation_work_type(None) is False


class TestUnset:
    def test_unset_is_not_none(self):
        assert _UNSET is not None


class TestRedactSecrets:
    def test_no_refs_unchanged(self):
        assert _redact_secrets("hello world", []) == "hello world"

    def test_single_ref_redacted(self):
        result = _redact_secrets("token abc123 here", ["abc123"])
        assert "abc123" not in result
        assert "REDACTED" in result

    def test_multiple_refs_redacted(self):
        result = _redact_secrets("a: x y: z", ["x", "z"])
        assert "x" not in result
        assert "z" not in result


class TestGetRunner:
    def test_returns_ansible_runner(self):
        runner = get_runner()
        assert runner is not None

    def test_returns_singleton(self):
        r1 = get_runner()
        r2 = get_runner()
        assert r1 is r2


class TestGetPlaybookRegistry:
    def test_returns_set(self):
        reg = get_playbook_registry()
        assert isinstance(reg, set)


class TestBuildGatewayFromConfig:
    def test_returns_none_or_gateway(self):
        result = build_gateway_from_config()
        assert result is None or hasattr(result, "list_model_profiles")


class TestBuildDispatcherFromConfig:
    def test_returns_any(self):
        result = build_dispatcher_from_config()
        assert result is None or hasattr(result, "dispatch_all")


class TestResolveCompactionConfig:
    def test_returns_tuple(self):
        enabled, _level = _resolve_compaction_config()
        assert isinstance(enabled, bool)
        assert enabled is False  # default is disabled


class TestCreateApp:
    def test_returns_fastapi_app(self):
        from fastapi import FastAPI
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_healthz_route_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/healthz" in routes

    def test_ping_route_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/ping" in routes

    def test_jobs_execute_route_registered(self):
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/jobs/execute" in routes

    def test_has_gateway_on_state(self):
        app = create_app()
        assert hasattr(app.state, "gateway")

    @pytest.mark.asyncio
    async def test_shutdown_drains_tasks_before_closing_owned_gateway(self) -> None:
        gateway = MagicMock()
        drain = AsyncMock()
        app = create_app(gateway=gateway, dispatcher=None)

        with patch(
            "general_ludd.models.job_invocation.drain_background_tasks",
            drain,
        ):
            await app.router.on_shutdown[-1]()

        drain.assert_awaited_once_with()
        gateway.close.assert_called_once_with()
