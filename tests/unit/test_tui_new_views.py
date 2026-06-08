"""Tests for new TUI table builders and view wiring for CLI parity."""

from __future__ import annotations

from typing import Any

import pytest


def _import_builder(name: str) -> Any:
    from general_ludd.cli import (
        _build_compute_table,
        _build_deployments_table,
        _build_filestore_table,
        _build_leaderboard_table,
        _build_mcp_table,
        _build_playbooks_table,
        _build_quantization_table,
        _build_scores_table,
        _build_skills_table,
        _build_templates_table,
    )

    return {
        "mcp": _build_mcp_table,
        "skills": _build_skills_table,
        "compute": _build_compute_table,
        "scores": _build_scores_table,
        "leaderboard": _build_leaderboard_table,
        "templates": _build_templates_table,
        "playbooks": _build_playbooks_table,
        "quantization": _build_quantization_table,
        "filestore": _build_filestore_table,
        "deployments": _build_deployments_table,
    }[name]


class TestBuildMCPTable:
    def test_empty(self) -> None:
        fn = _import_builder("mcp")
        t = fn([])
        assert t is not None
        assert t.title == "MCP Servers"

    def test_with_servers(self) -> None:
        fn = _import_builder("mcp")
        servers = [
            {"name": "github", "transport": "stdio", "status": "active"},
            {"name": "slack", "transport": "sse", "status": "inactive"},
        ]
        t = fn(servers)
        assert t is not None
        assert t.row_count == 2

    def test_adaptive_width(self) -> None:
        fn = _import_builder("mcp")
        t_narrow = fn([], term_width=60)
        t_wide = fn([], term_width=160)
        assert t_narrow is not None
        assert t_wide is not None


class TestBuildSkillsTable:
    def test_empty(self) -> None:
        fn = _import_builder("skills")
        t = fn([])
        assert t is not None
        assert t.title == "Skills"

    def test_with_skills(self) -> None:
        fn = _import_builder("skills")
        skills = [
            {"name": "tdd-discipline", "category": "testing", "installed": True},
            {"name": "security-first", "category": "security", "installed": False},
        ]
        t = fn(skills)
        assert t.row_count == 2


class TestBuildComputeTable:
    def test_empty(self) -> None:
        fn = _import_builder("compute")
        t = fn([])
        assert t is not None
        assert t.title == "Compute Endpoints"

    def test_with_endpoints(self) -> None:
        fn = _import_builder("compute")
        endpoints = [
            {"endpoint_id": "gpu-01", "provider": "aws", "status": "active"},
        ]
        t = fn(endpoints)
        assert t.row_count == 1


class TestBuildScoresTable:
    def test_empty(self) -> None:
        fn = _import_builder("scores")
        t = fn([])
        assert t is not None
        assert t.title == "Benchmark Scores"

    def test_with_scores(self) -> None:
        fn = _import_builder("scores")
        scores = [
            {
                "prompt_profile": "default",
                "model_profile": "gpt-4",
                "task_type": "bug_fix",
                "composite_score": 0.85,
            },
        ]
        t = fn(scores)
        assert t.row_count == 1


class TestBuildLeaderboardTable:
    def test_empty(self) -> None:
        fn = _import_builder("leaderboard")
        t = fn([])
        assert t is not None
        assert t.title == "Leaderboard"

    def test_with_entries(self) -> None:
        fn = _import_builder("leaderboard")
        entries = [
            {"rank": 1, "prompt": "default", "model": "gpt-4", "score": 0.92},
            {"rank": 2, "prompt": "concise", "model": "claude-3", "score": 0.88},
        ]
        t = fn(entries)
        assert t.row_count == 2


class TestBuildTemplatesTable:
    def test_empty(self) -> None:
        fn = _import_builder("templates")
        t = fn([])
        assert t is not None
        assert t.title == "Templates"

    def test_with_templates(self) -> None:
        fn = _import_builder("templates")
        templates = [
            {"name": "default", "task_types": ["bug_fix", "feature"], "source": "builtin"},
        ]
        t = fn(templates)
        assert t.row_count == 1


class TestBuildPlaybooksTable:
    def test_empty(self) -> None:
        fn = _import_builder("playbooks")
        t = fn([])
        assert t is not None
        assert t.title == "Playbooks"

    def test_with_playbooks(self) -> None:
        fn = _import_builder("playbooks")
        playbooks = [
            {"name": "run_tests", "tasks": 5, "status": "ready"},
        ]
        t = fn(playbooks)
        assert t.row_count == 1


class TestBuildQuantizationTable:
    def test_empty(self) -> None:
        fn = _import_builder("quantization")
        t = fn([])
        assert t is not None
        assert t.title == "Quantization"

    def test_with_entries(self) -> None:
        fn = _import_builder("quantization")
        entries = [
            {
                "model_id": "llama-7b",
                "precision": "int4",
                "confidence": 0.92,
                "source": "self-probe",
            },
        ]
        t = fn(entries)
        assert t.row_count == 1


class TestBuildFilestoreTable:
    def test_empty(self) -> None:
        fn = _import_builder("filestore")
        t = fn([])
        assert t is not None
        assert t.title == "Filestore"

    def test_with_files(self) -> None:
        fn = _import_builder("filestore")
        files = [
            {"name": "ansible-runner", "size_bytes": 45000000, "type": "binary"},
            {"name": "config.yml", "size_bytes": 1024, "type": "config"},
        ]
        t = fn(files)
        assert t.row_count == 2


class TestBuildDeploymentsTable:
    def test_empty(self) -> None:
        fn = _import_builder("deployments")
        t = fn([])
        assert t is not None
        assert t.title == "Deployments"

    def test_with_deployments(self) -> None:
        fn = _import_builder("deployments")
        deployments = [
            {"name": "prod-us-east", "provider": "aws", "status": "running"},
            {"name": "staging", "provider": "azure", "status": "stopped"},
        ]
        t = fn(deployments)
        assert t.row_count == 2


class TestNewViewKeybindings:
    def _make_handler(self) -> tuple[Any, dict[str, Any]]:
        from general_ludd.tui.keybindings import TUIKeyHandler

        state: dict[str, Any] = {
            "current_view": "main",
            "daemon_running": False,
            "status_msg": "",
            "daemon_url": "http://localhost:8000",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
        }
        return TUIKeyHandler(state), state

    def test_mcp_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("u")
        assert state["current_view"] == "mcp"

    def test_mcp_view_exit(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "mcp"
        handler.handle_key("u")
        assert state["current_view"] == "main"

    def test_skills_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("j")
        assert state["current_view"] == "skills"

    def test_compute_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("e")
        assert state["current_view"] == "compute"

    def test_scores_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("b")
        assert state["current_view"] == "scores"

    def test_templates_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("l")
        assert state["current_view"] == "templates"

    def test_quantization_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("n")
        assert state["current_view"] == "quantization"

    def test_filestore_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("f")
        assert state["current_view"] == "filestore"

    def test_deployments_view_toggle(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("z")
        assert state["current_view"] == "deployments"

    def test_reload_from_main(self) -> None:
        handler, state = self._make_handler()
        handler.handle_key("R")
        assert "reload" in state.get("status_msg", "").lower() or state.get("last_reload") is not None

    def test_mcp_search_input_mode(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "mcp"
        handler.handle_key("s")
        assert state["input_mode"] == "mcp_search"

    def test_skills_search_input_mode(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "skills"
        handler.handle_key("s")
        assert state["input_mode"] == "skills_search"

    def test_compute_register_input_mode(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "compute"
        handler.handle_key("a")
        assert state["input_mode"] == "compute_register"

    def test_todos_add_input_mode(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "todos"
        handler.handle_key("a")
        assert state["input_mode"] == "todos_add"

    def test_workers_ping(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "workers"
        handler.handle_key("p")
        assert "ping" in state.get("status_msg", "").lower() or state.get("last_ping") is not None

    def test_models_search_input_mode(self) -> None:
        handler, state = self._make_handler()
        state["current_view"] = "models"
        handler.handle_key("s")
        assert state["input_mode"] == "models_search"


class TestAllNewTablesNoUnboundedColumns:
    @pytest.mark.parametrize("name", [
        "mcp", "skills", "compute", "scores", "leaderboard",
        "templates", "playbooks", "quantization", "filestore", "deployments",
    ])
    def test_no_unbounded_columns(self, name: str) -> None:
        fn = _import_builder(name)
        t = fn([])
        for col in t.columns:
            assert col.max_width is not None, f"{name}: column {col.header!r} has no max_width"
