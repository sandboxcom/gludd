"""Structural tests for routers/ornith.py — Ornith training-data collector endpoints."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.ornith

        assert general_ludd.routers.ornith is not None

    def test_register_is_callable(self):
        from general_ludd.routers.ornith import register

        assert callable(register)

    def test_logger_exists(self):
        from general_ludd.routers.ornith import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.routers.ornith"


class TestHelperFunctions:
    def test_get_session_factory_exists(self):
        from general_ludd.routers.ornith import _get_session_factory

        assert callable(_get_session_factory)

    def test_get_session_factory_returns_none_for_empty_state(self):
        from general_ludd.routers.ornith import _get_session_factory

        app = FastAPI()
        result = _get_session_factory(app)
        assert result is None

    def test_pair_to_dict_exists(self):
        from general_ludd.routers.ornith import _pair_to_dict

        assert callable(_pair_to_dict)


class TestPydanticModels:
    def test_record_pair_request_is_pydantic_base_model(self):
        from general_ludd.routers.ornith import RecordPairRequest

        assert issubclass(RecordPairRequest, BaseModel)

    def test_record_pair_request_has_required_fields(self):
        from general_ludd.routers.ornith import RecordPairRequest

        req = RecordPairRequest(
            task_description="test task",
            scaffold_kind="test",
            scaffold_content="content",
            agent_id="agent-1",
        )
        assert req.task_description == "test task"
        assert req.scaffold_kind == "test"
        assert req.scaffold_content == "content"
        assert req.agent_id == "agent-1"
        assert req.target_files == []
        assert req.iterations_used == 0
        assert req.tokens_consumed == 0

    def test_record_pair_request_task_description_min_length(self):
        from general_ludd.routers.ornith import RecordPairRequest

        req = RecordPairRequest(
            task_description="t",
            scaffold_kind="x",
            scaffold_content="c",
            agent_id="a",
        )
        assert req.task_description == "t"

    def test_set_outcome_request_is_pydantic_base_model(self):
        from general_ludd.routers.ornith import SetOutcomeRequest

        assert issubclass(SetOutcomeRequest, BaseModel)

    def test_set_outcome_request_defaults(self):
        from general_ludd.routers.ornith import SetOutcomeRequest

        req = SetOutcomeRequest(status="approved")
        assert req.status == "approved"
        assert req.details == {}

    def test_ornith_config_update_request_exists(self):
        from general_ludd.routers.ornith import OrnithConfigUpdateRequest

        assert issubclass(OrnithConfigUpdateRequest, BaseModel)


class TestRegister:
    def test_registers_ornith_routes(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/ornith/record" in routes
        assert "/admin/ornith/pending" in routes
        assert "/admin/ornith/export" in routes
        assert "/admin/ornith/stats" in routes
        assert "/admin/ornith/status" in routes
        assert "/admin/ornith/self-improve" in routes
        assert "/admin/ornith/history" in routes
        assert "/admin/ornith/config" in routes
        assert "/admin/ornith/pairs" in routes
        assert "/admin/ornith/{pair_id}/outcome" in routes

    def test_record_endpoint_post_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        record_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/record"), None
        )
        assert record_route is not None
        assert "POST" in record_route.methods

    def test_pending_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        pending_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/pending"), None
        )
        assert pending_route is not None
        assert "GET" in pending_route.methods

    def test_export_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        export_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/export"), None
        )
        assert export_route is not None
        assert "GET" in export_route.methods

    def test_stats_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        stats_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/stats"), None
        )
        assert stats_route is not None
        assert "GET" in stats_route.methods

    def test_status_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        status_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/status"), None
        )
        assert status_route is not None
        assert "GET" in status_route.methods

    def test_self_improve_endpoint_post_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        si_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/self-improve"), None
        )
        assert si_route is not None
        assert "POST" in si_route.methods

    def test_history_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        history_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/history"), None
        )
        assert history_route is not None
        assert "GET" in history_route.methods

    def test_config_endpoint_get_and_put_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        config_methods = set()
        for route in all_routes:
            if route.path == "/admin/ornith/config":
                config_methods.update(route.methods)
        assert "GET" in config_methods
        assert "PUT" in config_methods

    def test_pairs_endpoint_get_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        pairs_route = next(
            (r for r in all_routes if r.path == "/admin/ornith/pairs"), None
        )
        assert pairs_route is not None
        assert "GET" in pairs_route.methods

    def test_outcome_endpoint_patch_registered(self):
        from general_ludd.routers.ornith import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        outcome_route = next(
            (
                r
                for r in all_routes
                if r.path == "/admin/ornith/{pair_id}/outcome"
            ),
            None,
        )
        assert outcome_route is not None
        assert "PATCH" in outcome_route.methods
