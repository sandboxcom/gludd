"""E2E tests: CLI, TUI, Ansible runner, Infrastructure subsystems.

Covers:
  1. CLI command parsing — subcommands, flags, args, help output
  2. TUI key handlers, view transitions, state management, spawn validation
  3. Ansible runner — paths, adapter lifecycle, role conversion
  4. Terraform operations — HCL generation, tfvars, state backends
  5. Infrastructure compute — config validation, cost tracking, pricing
  6. Provider enumeration — registry, filtering, pricing queries
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from general_ludd.cli import build_parser

# ═══════════════════════════════════════════════════════════════════════════════
# 1. CLI COMMAND PARSING
# ═══════════════════════════════════════════════════════════════════════════════


class TestCliCommandParsing:
    """Verify all subcommands parse correctly with their flags and args."""

    @staticmethod
    def _parse(args: list[str]):
        parser, _ = build_parser()
        return parser.parse_args(args)

    # ── top-level leaf commands ──

    def test_daemon_all_flags(self):
        args = self._parse(
            [
                "daemon",
                "--host",
                "0.0.0.0",
                "--port",
                "9090",
                "--log-level",
                "debug",
                "--tick-interval",
                "3.0",
                "--workers",
                "4",
                "--project",
                "proj-1",
            ]
        )
        assert args.command == "daemon"
        assert args.host == "0.0.0.0"
        assert args.port == 9090
        assert args.log_level == "debug"
        assert args.tick_interval == 3.0
        assert args.workers == 4
        assert args.project == "proj-1"

    def test_daemon_log_level_choices(self):
        for lvl in ("debug", "info", "warning", "error"):
            args = self._parse(["daemon", "--log-level", lvl])
            assert args.log_level == lvl

    def test_add_positional_title(self):
        args = self._parse(["add", "Fix login bug"])
        assert args.command == "add"
        assert args.title == "Fix login bug"
        assert args.queue == "core"
        assert args.priority == "medium"

    def test_add_all_flags(self):
        args = self._parse(
            [
                "add",
                "Deploy model",
                "--queue",
                "gpu",
                "--priority",
                "high",
                "--work-type",
                "deploy",
                "--description",
                "Deploy llama to AWS",
                "--project",
                "p42",
                "--daemon-url",
                "http://0.0.0.0:9999",
            ]
        )
        assert args.title == "Deploy model"
        assert args.queue == "gpu"
        assert args.priority == "high"
        assert args.work_type == "deploy"
        assert args.description == "Deploy llama to AWS"
        assert args.project == "p42"
        assert args.daemon_url == "http://0.0.0.0:9999"

    def test_status_optional_todo_id(self):
        a1 = self._parse(["status"])
        assert a1.todo_id is None
        a2 = self._parse(["status", "abc-123"])
        assert a2.todo_id == "abc-123"
        a3 = self._parse(["status", "--project", "p1"])
        assert a3.todo_id is None
        assert a3.project == "p1"

    def test_list_filter_flags(self):
        args = self._parse(
            ["list", "--queue", "core", "--status", "pending", "--project", "p1", "--daemon-url", "http://h:1"]
        )
        assert args.queue == "core"
        assert args.status == "pending"
        assert args.project == "p1"
        assert args.daemon_url == "http://h:1"

    def test_log_level_choices(self):
        for lvl in ("debug", "info", "warning", "error"):
            args = self._parse(["log-level", lvl])
            assert args.level == lvl

    def test_deployments(self):
        args = self._parse(["deployments", "--daemon-url", "http://h:1"])
        assert args.command == "deployments"

    def test_version(self):
        args = self._parse(["version"])
        assert args.command == "version"

    def test_health(self):
        args = self._parse(["health", "--daemon-url", "http://h:1"])
        assert args.command == "health"

    # ── models subcommand ──

    def test_models_search(self):
        args = self._parse(["models", "search", "llama", "--limit", "10"])
        assert args.models_command == "search"
        assert args.query == "llama"
        assert args.limit == 10

    def test_models_searx_search(self):
        args = self._parse(["models", "searx-search", "gpt", "--source", "github", "--searx-url", "http://s:8080"])
        assert args.models_command == "searx-search"
        assert args.query == "gpt"
        assert args.source == "github"
        assert args.searx_url == "http://s:8080"

    def test_models_deploy(self):
        args = self._parse(
            [
                "models",
                "deploy",
                "llama-7b",
                "--provider",
                "gcp",
                "--engine",
                "llamacpp",
                "--workload-type",
                "fine_tuning",
                "--region",
                "us-west1",
                "--gpu-count",
                "4",
                "--max-cost",
                "50.0",
            ]
        )
        assert args.models_command == "deploy"
        assert args.name == "llama-7b"
        assert args.provider == "gcp"
        assert args.engine == "llamacpp"
        assert args.workload_type == "fine_tuning"
        assert args.region == "us-west1"
        assert args.gpu_count == 4
        assert args.max_cost == 50.0

    def test_models_discover(self):
        args = self._parse(["models", "discover", "--provider", "openrouter"])
        assert args.provider == "openrouter"

    def test_models_discovered(self):
        args = self._parse(["models", "discovered"])
        assert args.models_command == "discovered"

    def test_models_list(self):
        args = self._parse(["models", "list"])
        assert args.models_command == "list"

    def test_models_add(self):
        args = self._parse(
            ["models", "add", "--model-id", "gpt-4o", "--provider", "openai", "--model", "gpt-4o-2024-08-06"]
        )
        assert args.model_id == "gpt-4o"
        assert args.provider == "openai"
        assert args.model == "gpt-4o-2024-08-06"

    def test_models_remove(self):
        args = self._parse(["models", "remove", "m123", "--daemon-url", "http://h:1"])
        assert args.model_id == "m123"

    def test_models_performance(self):
        args = self._parse(["models", "performance", "--service", "openai", "--task-type", "code"])
        assert args.service == "openai"
        assert args.task_type == "code"

    def test_models_ranking(self):
        args = self._parse(["models", "ranking", "--task-type", "code", "--strategy", "cheapest"])
        assert args.task_type == "code"
        assert args.strategy == "cheapest"

    def test_models_router_status(self):
        args = self._parse(["models", "router-status"])
        assert args.models_command == "router-status"

    def test_models_router_set(self):
        args = self._parse(["models", "router-set", "--task-type", "code", "--strategy", "fastest"])
        assert args.task_type == "code"
        assert args.strategy == "fastest"

    # ── local-serve ──

    def test_local_serve(self):
        args = self._parse(
            [
                "local-serve",
                "--model",
                "llama-7b",
                "--engine",
                "llamacpp",
                "--port",
                "9999",
                "--gpu-layers",
                "32",
                "--context-size",
                "8192",
            ]
        )
        assert args.command == "local-serve"
        assert args.model == "llama-7b"
        assert args.engine == "llamacpp"
        assert args.port == 9999
        assert args.gpu_layers == 32
        assert args.context_size == 8192

    # ── worktree subcommands ──

    def test_worktree_scan(self):
        args = self._parse(["worktree", "scan", "--path", "/a,/b"])
        assert args.worktree_command == "scan"
        assert args.path == "/a,/b"

    def test_worktree_status(self):
        args = self._parse(["worktree", "status"])
        assert args.worktree_command == "status"

    # ── project subcommands ──

    def test_project_add(self):
        args = self._parse(
            [
                "project",
                "add",
                "myproj",
                "--repo-url",
                "https://g",
                "--workspace-path",
                "/ws",
                "--weight",
                "50.0",
                "--dispatch-mode",
                "passive_external",
            ]
        )
        assert args.project_command == "add"
        assert args.name == "myproj"
        assert args.repo_url == "https://g"
        assert args.workspace_path == "/ws"
        assert args.weight == 50.0
        assert args.dispatch_mode == "passive_external"

    def test_project_list(self):
        args = self._parse(["project", "list"])
        assert args.project_command == "list"

    def test_project_remove(self):
        args = self._parse(["project", "remove", "p1"])
        assert args.project_command == "remove"
        assert args.project_id == "p1"

    # ── compute subcommands ──

    def test_compute_endpoints(self):
        args = self._parse(["compute", "endpoints"])
        assert args.compute_command == "endpoints"

    def test_compute_register(self):
        args = self._parse(
            [
                "compute",
                "register",
                "--id",
                "ep1",
                "--url",
                "http://ep:8000",
                "--model",
                "llama",
                "--max-concurrent",
                "4",
            ]
        )
        assert args.id == "ep1"
        assert args.url == "http://ep:8000"
        assert args.model == "llama"
        assert args.max_concurrent == 4

    def test_compute_unregister(self):
        args = self._parse(["compute", "unregister", "ep1"])
        assert args.endpoint_id == "ep1"

    def test_compute_launch(self):
        args = self._parse(
            [
                "compute",
                "launch",
                "--provider",
                "aws",
                "--gpu",
                "a100_80",
                "--model",
                "llama-70b",
                "--region",
                "us-east-1",
                "--gpu-count",
                "2",
                "--max-cost",
                "25.0",
                "--no-spot",
                "--engine",
                "vllm",
                "--workload-type",
                "batch_inference",
                "--deploy-type",
                "containerapp",
            ]
        )
        assert args.provider == "aws"
        assert args.gpu == "a100_80"
        assert args.model == "llama-70b"
        assert args.region == "us-east-1"
        assert args.gpu_count == 2
        assert args.max_cost == 25.0
        assert args.no_spot is True
        assert args.engine == "vllm"
        assert args.workload_type == "batch_inference"
        assert args.deploy_type == "containerapp"

    def test_compute_destroy(self):
        args = self._parse(["compute", "destroy", "inst-1"])
        assert args.instance_id == "inst-1"

    # ── mcp subcommands ──

    def test_mcp_search(self):
        args = self._parse(["mcp", "search", "github"])
        assert args.mcp_command == "search"
        assert args.query == "github"

    def test_mcp_list(self):
        args = self._parse(["mcp", "list"])
        assert args.mcp_command == "list"

    def test_mcp_info(self):
        args = self._parse(["mcp", "info", "github-mcp"])
        assert args.name == "github-mcp"

    # ── skills subcommands ──

    def test_skills_search(self):
        args = self._parse(["skills", "search", "coding"])
        assert args.skills_command == "search"
        assert args.query == "coding"

    def test_skills_list(self):
        args = self._parse(["skills", "list"])
        assert args.skills_command == "list"

    def test_skills_install(self):
        args = self._parse(["skills", "install", "type-safety"])
        assert args.name == "type-safety"

    # ── other commands ──

    def test_scores(self):
        args = self._parse(["scores", "--task-type", "code"])
        assert args.task_type == "code"

    def test_leaderboard(self):
        args = self._parse(["leaderboard", "--task-type", "review"])
        assert args.task_type == "review"

    def test_preflight(self):
        args = self._parse(["preflight", "--strict-terraform-import"])
        assert args.strict_terraform_import is True

    def test_tui(self):
        args = self._parse(["tui", "--daemon-url", "http://h:9"])
        assert args.command == "tui"
        assert args.daemon_url == "http://h:9"

    def test_reload(self):
        args = self._parse(["reload", "--scope", "config"])
        assert args.scope == "config"

    def test_chat_all_flags(self):
        args = self._parse(
            [
                "chat",
                "--model",
                "openai/gpt-4o",
                "--system-prompt",
                "You are helpful",
                "--history",
                "/tmp/h.json",
                "--resume",
                "--save-interval",
                "10",
                "--stream",
                "--max-context",
                "32768",
                "--export",
                "md",
                "--export-output",
                "/tmp/out.md",
            ]
        )
        assert args.model == "openai/gpt-4o"
        assert args.system_prompt == "You are helpful"
        assert args.history == "/tmp/h.json"
        assert args.resume is True
        assert args.save_interval == 10
        assert args.stream is True
        assert args.max_context == 32768
        assert args.export == "md"
        assert args.export_output == "/tmp/out.md"

    def test_help(self):
        args = self._parse(["help"])
        assert args.command == "help"

    # ── ansible subcommands ──

    def test_ansible_search(self):
        args = self._parse(["ansible", "search", "nginx", "--type", "collection"])
        assert args.ansible_command == "search"
        assert args.query == "nginx"
        assert args.type == "collection"

    def test_ansible_install(self):
        args = self._parse(["ansible", "install", "geerlingguy.nginx", "--type", "role"])
        assert args.name == "geerlingguy.nginx"
        assert args.type == "role"

    def test_ansible_builtins(self):
        args = self._parse(["ansible", "builtins"])
        assert args.ansible_command == "builtins"

    # ── integrity subcommands ──

    def test_integrity_scan(self):
        args = self._parse(["integrity", "scan", "--paths", "a", "b"])
        assert args.integrity_command == "scan"
        assert args.paths == ["a", "b"]

    def test_integrity_report(self):
        args = self._parse(["integrity", "report"])
        assert args.integrity_command == "report"

    def test_integrity_approve(self):
        args = self._parse(["integrity", "approve", "/etc/hosts", "--reason", "expected", "--signer", "ops"])
        assert args.change_id == "/etc/hosts"
        assert args.reason == "expected"
        assert args.signer == "ops"

    def test_integrity_reject(self):
        args = self._parse(["integrity", "reject", "/etc/shadow", "--reason", "unauthorized"])
        assert args.change_id == "/etc/shadow"
        assert args.reason == "unauthorized"

    def test_integrity_log(self):
        args = self._parse(["integrity", "log"])
        assert args.integrity_command == "log"

    # ── hooks commands ──

    def test_hooks_list(self):
        args = self._parse(["hooks", "list"])
        assert args.hooks_command == "list"

    def test_hooks_register(self):
        args = self._parse(["hooks", "register", "--event", "todo.created", "--handler", "slack.notify"])
        assert args.event == "todo.created"
        assert args.handler == "slack.notify"

    def test_hooks_delete(self):
        args = self._parse(["hooks", "delete", "h1"])
        assert args.hook_id == "h1"

    # ── workers / agents / metrics ──

    def test_workers_list(self):
        args = self._parse(["workers", "list"])
        assert args.workers_command == "list"

    def test_workers_ping(self):
        args = self._parse(["workers", "ping"])
        assert args.workers_command == "ping"

    def test_agents_list(self):
        args = self._parse(["agents", "list"])
        assert args.agents_command == "list"

    def test_metrics_cost(self):
        args = self._parse(["metrics", "cost"])
        assert args.metrics_command == "cost"

    def test_metrics_report(self):
        args = self._parse(["metrics", "report"])
        assert args.metrics_command == "report"

    # ── templates / playbooks / code ──

    def test_templates_list(self):
        args = self._parse(["templates", "list"])
        assert args.templates_command == "list"

    def test_templates_refresh(self):
        args = self._parse(["templates", "refresh"])
        assert args.templates_command == "refresh"

    def test_playbooks_list(self):
        args = self._parse(["playbooks", "list"])
        assert args.playbooks_command == "list"

    def test_playbooks_refresh(self):
        args = self._parse(["playbooks", "refresh"])
        assert args.playbooks_command == "refresh"

    def test_code_graph(self):
        args = self._parse(["code", "graph", "--source", "cli.py", "--language", "python"])
        assert args.source == "cli.py"
        assert args.language == "python"

    def test_code_search(self):
        args = self._parse(["code", "search", "build_parser", "--language", "python"])
        assert args.query == "build_parser"
        assert args.language == "python"

    # ── slurm ──

    def test_slurm_status(self):
        args = self._parse(["slurm", "status"])
        assert args.slurm_command == "status"

    def test_slurm_submit(self):
        args = self._parse(
            [
                "slurm",
                "submit",
                "--command",
                "python train.py",
                "--job-name",
                "train1",
                "--partition",
                "gpu",
                "--cpus-per-task",
                "8",
                "--gpus",
                "2",
                "--memory",
                "32G",
                "--time-limit",
                "04:00:00",
            ]
        )
        assert args.command == "python train.py"
        assert args.job_name == "train1"
        assert args.partition == "gpu"
        assert args.cpus_per_task == 8
        assert args.gpus == "2"
        assert args.memory == "32G"
        assert args.time_limit == "04:00:00"

    def test_slurm_job(self):
        args = self._parse(["slurm", "job", "12345"])
        assert args.job_id == "12345"

    def test_slurm_cancel(self):
        args = self._parse(["slurm", "cancel", "12345"])
        assert args.job_id == "12345"

    def test_slurm_list(self):
        args = self._parse(["slurm", "list"])
        assert args.slurm_command == "list"

    # ── connectors ──

    def test_connectors_list(self):
        args = self._parse(["connectors", "list"])
        assert args.connectors_command == "list"

    def test_connectors_health(self):
        args = self._parse(["connectors", "health"])
        assert args.connectors_command == "health"

    def test_connectors_query(self):
        args = self._parse(["connectors", "query", "prometheus", "--spec", '{"query": "up"}'])
        assert args.source == "prometheus"
        assert args.spec == '{"query": "up"}'

    # ── login / onboard ──

    def test_login_list(self):
        args = self._parse(["login", "--list"])
        assert args.list is True

    def test_login_service(self):
        args = self._parse(["login", "github", "--store", "openbao", "--timeout", "60"])
        assert args.service == "github"
        assert args.store == "openbao"
        assert args.timeout == 60

    def test_onboard(self):
        args = self._parse(["onboard", "aws", "--dry-run", "--region", "us-east-1", "--role-arn", "arn:aws:iam::x"])
        assert args.provider == "aws"
        assert args.dry_run is True
        assert args.region == "us-east-1"
        assert args.role_arn == "arn:aws:iam::x"

    # ── make command ──

    def test_make(self):
        args = self._parse(["make", "lint", "--cwd", "/tmp", "--timeout", "30", "--env", "KEY=V", "--stream"])
        assert args.target == "lint"
        assert args.cwd == "/tmp"
        assert args.timeout == 30
        assert args.env == ["KEY=V"]
        assert args.stream is True

    # ── test subcommands ──

    def test_test_background_launch(self):
        args = self._parse(["test", "background", "launch", "test_x.py", "--wait"])
        assert args.testbg_command == "launch"
        assert args.testfile == "test_x.py"
        assert args.wait is True

    def test_test_background_status(self):
        args = self._parse(["test", "background", "status", "test_x.py"])
        assert args.testfile == "test_x.py"

    def test_test_background_poll_all(self):
        args = self._parse(["test", "background", "poll-all"])
        assert args.testbg_command == "poll-all"

    def test_test_background_kill(self):
        args = self._parse(["test", "background", "kill", "test_x.py", "--force"])
        assert args.testfile == "test_x.py"
        assert args.force is True

    def test_test_background_results(self):
        args = self._parse(["test", "background", "results", "test_x.py"])
        assert args.testfile == "test_x.py"

    def test_test_self(self):
        args = self._parse(["test", "self", "--daemon-url", "http://h:1"])
        assert args.test_command == "self"

    def test_test_smoke(self):
        args = self._parse(["test", "smoke", "aws", "ec2-a100", "--live", "--json", "--timeout", "5.0"])
        assert args.provider == "aws"
        assert args.test == "ec2-a100"
        assert args.live is True
        assert args.json is True
        assert args.timeout == 5.0

    # ── config terraform ──

    def test_config_terraform_get(self):
        args = self._parse(["config", "terraform", "get", "--field", "region"])
        assert args.terraform_command == "get"
        assert args.field == "region"

    def test_config_terraform_set(self):
        args = self._parse(["config", "terraform", "set", "region", "eu-west-1"])
        assert args.terraform_command == "set"
        assert args.field == "region"
        assert args.value == "eu-west-1"


class TestCliHelpOutput:
    """Verify --help works for all parsers."""

    @staticmethod
    def _help_output(sub_args: list[str]) -> str:
        old_stdout = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            with pytest.raises(SystemExit) as exc:
                parser, _ = build_parser()
                parser.parse_args([*sub_args, "--help"])
            assert exc.value.code == 0
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    def test_top_level_help(self):
        out = self._help_output([])
        assert "General Ludd Agent" in out or "gludd" in out

    def test_daemon_help(self):
        out = self._help_output(["daemon"])
        assert "daemon" in out.lower() or "--host" in out

    def test_add_help(self):
        out = self._help_output(["add"])
        assert "title" in out.lower()

    def test_models_help(self):
        out = self._help_output(["models"])
        assert "models" in out.lower()

    def test_compute_help(self):
        out = self._help_output(["compute"])
        assert "launch" in out.lower() or "endpoints" in out.lower()

    def test_project_help(self):
        out = self._help_output(["project"])
        assert "add" in out.lower() or "project" in out.lower()

    def test_worktree_help(self):
        out = self._help_output(["worktree"])
        assert "scan" in out.lower() or "worktree" in out.lower()


class TestCliSubcommandRouting:
    """Verify func dispatch and subcommand_map are wired correctly."""

    def test_all_top_level_leaf_commands_registered(self):
        parser, _ = build_parser()
        subparsers_action = parser._subparsers._group_actions[0]
        registered = set(subparsers_action.choices.keys())
        expected_leaves = {
            "daemon",
            "add",
            "status",
            "list",
            "log-level",
            "deployments",
            "version",
            "health",
            "local-serve",
            "scores",
            "leaderboard",
            "preflight",
            "tui",
            "reload",
            "chat",
            "help",
            "login",
            "onboard",
            "make",
        }
        missing = expected_leaves - registered
        assert not missing, f"Leaf commands missing from parser choices: {missing}"

    def test_all_group_commands_in_subcommand_map(self):
        _, sub_map = build_parser()
        expected_groups = {
            "models",
            "worktree",
            "project",
            "config",
            "mcp",
            "skills",
            "compute",
            "hooks",
            "workers",
            "agents",
            "metrics",
            "templates",
            "playbooks",
            "code",
            "slurm",
            "connectors",
            "test",
        }
        registered = set(sub_map.keys())
        missing = expected_groups - registered
        assert not missing, f"Group commands missing from subcommand_map: {missing}"

    def test_subcommand_map_values_are_parsers(self):
        _, sub_map = build_parser()
        assert len(sub_map) >= 15
        for name, subparser in sub_map.items():
            assert subparser is not None, f"{name} has None subparser"
            assert hasattr(subparser, "print_help"), f"{name} missing print_help"

    def test_build_parser_returns_tuple(self):
        parser, sub_map = build_parser()
        import argparse as _argparse

        assert isinstance(parser, _argparse.ArgumentParser)
        assert isinstance(sub_map, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TUI KEY HANDLERS, VIEW TRANSITIONS, SPAWN VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestTUIKeyHandler:
    """Verify TUI key handlers, view transitions, and state management."""

    @staticmethod
    def _tui_state(view="main"):
        return {
            "current_view": view,
            "daemon_running": False,
            "status_msg": "",
            "daemon_url": "http://localhost:8000",
            "input_mode": None,
            "input_buffer": "",
            "input_field_index": 0,
            "input_fields": [],
            "dispatch_mode": "active",
            "ansible_search_results": [],
            "verbose_logging": False,
        }

    def test_handler_creation(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        handler = TUIKeyHandler(state)
        assert handler._state is state
        assert handler._state["current_view"] == "main"

    def test_escape_returns_to_main(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state(view="models")
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b")
        assert result is True
        assert state["current_view"] == "main"

    def test_escape_cancels_input_mode(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        state["input_mode"] = "models_search"
        state["input_buffer"] = "gpt"
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b")
        assert result is True
        assert state["input_mode"] is None
        assert state["input_buffer"] == ""

    def test_tab_toggles_panel_focus(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        handler = TUIKeyHandler(state)
        handler.handle_key("\t")
        assert state["panel_focus"] == "right"
        handler.handle_key("\t")
        assert state["panel_focus"] == "left"

    def test_arrow_down_handled(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b[B")
        assert result is True

    def test_arrow_up_handled(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b[A")
        assert result is True

    def test_left_arrow_back_to_main(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state(view="skills")
        handler = TUIKeyHandler(state)
        result = handler.handle_key("\x1b[D")
        assert result is True
        assert state["current_view"] == "main"

    def test_view_toggle_keys(self):
        from general_ludd.tui.keybindings import _TOGGLE_VIEWS

        assert len(_TOGGLE_VIEWS) >= 20
        for key, (view_name, _status) in _TOGGLE_VIEWS.items():
            assert isinstance(key, str) and len(key) == 1
            assert isinstance(view_name, str)
            assert view_name != ""

    def test_main_menu_items(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        handler = TUIKeyHandler(self._tui_state())
        items = handler.get_main_menu_items()
        assert len(items) >= 20
        for key, label, target in items:
            assert isinstance(key, str) and len(key) == 1
            assert isinstance(label, str) and label != ""
            assert isinstance(target, str) and target != ""

    def test_text_input_backspace(self):
        from general_ludd.tui.keybindings import TUIKeyHandler

        state = self._tui_state()
        state["input_mode"] = "models_search"
        state["input_buffer"] = "hello"
        handler = TUIKeyHandler(state)
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "hell"
        handler.handle_key("\x7f")
        assert state["input_buffer"] == "hel"

    def test_dispatch_modes_constant(self):
        from general_ludd.tui.keybindings import DISPATCH_MODES

        assert "active" in DISPATCH_MODES
        assert "passive_external" in DISPATCH_MODES
        assert "worktree_monitor" in DISPATCH_MODES
        assert len(DISPATCH_MODES) == 3


class TestTUIGunicornSpawnArgs:
    """Validate daemon spawn arg validation hardening."""

    def test_valid_hosts(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for host in ("127.0.0.1", "localhost", "::1", "10.0.0.1"):
            validate_gunicorn_spawn_args(host=host, port=8000)

    def test_invalid_hosts(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for bad in ("", "host with spaces", "bad;inject", "host\nnewline"):
            with pytest.raises(ValueError):
                validate_gunicorn_spawn_args(host=bad, port=8000)

    def test_valid_ports(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for port in (1, 80, 443, 8000, 65535):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=port)

    def test_invalid_ports(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for bad in (0, -1, 65536, 99999):
            with pytest.raises(ValueError):
                validate_gunicorn_spawn_args(host="127.0.0.1", port=bad)

    def test_invalid_port_type(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for bad in ("8000", True, None, 1.5):
            with pytest.raises(ValueError):
                validate_gunicorn_spawn_args(host="127.0.0.1", port=bad)

    def test_valid_log_levels(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for lvl in ("debug", "info", "warning", "error", "warn", "critical"):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, log_level=lvl)

    def test_invalid_log_levels(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        for bad in ("verbose", "trace", ""):
            with pytest.raises(ValueError):
                validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, log_level=bad)
        # None passes through (log_level is optional, checked `is not None`)
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, log_level=None)

    def test_worker_range(self):
        from general_ludd.tui.keybindings import validate_gunicorn_spawn_args

        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=1)
        validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=4096)
        with pytest.raises(ValueError):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=0)
        with pytest.raises(ValueError):
            validate_gunicorn_spawn_args(host="127.0.0.1", port=8000, workers=4097)

    def test_build_gunicorn_cmd(self):
        from general_ludd.tui.keybindings import build_gunicorn_cmd

        cmd = build_gunicorn_cmd(host="127.0.0.1", port=8000, workers=2, log_level="debug")
        assert cmd == [
            "gunicorn",
            "general_ludd.daemon:create_daemon_app()",
            "--worker-class",
            "uvicorn_worker.UvicornWorker",
            "--workers",
            "2",
            "--bind",
            "127.0.0.1:8000",
            "--log-level",
            "debug",
        ]

    def test_build_gunicorn_cmd_minimal(self):
        from general_ludd.tui.keybindings import build_gunicorn_cmd

        cmd = build_gunicorn_cmd(host="localhost", port=8080)
        assert "gunicorn" in cmd[0]
        assert "--bind" in cmd
        assert "localhost:8080" in cmd
        assert "--log-level" not in cmd


class TestTUIBreadcrumb:
    """Verify TUI breadcrumb navigation."""

    def test_push_and_pop(self):
        from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb

        state: dict = {"breadcrumb": ["main"]}
        push_breadcrumb(state, "models")
        assert state["breadcrumb"] == ["main", "models"]
        popped = pop_breadcrumb(state)
        assert popped == "main"
        assert state["breadcrumb"] == ["main"]

    def test_render_breadcrumb(self):
        from general_ludd.tui.breadcrumb import render_breadcrumb

        result = render_breadcrumb(["home", "settings", "profile"])
        assert result == "home > settings > profile"


class TestTUITables:
    """Verify TUI table factory."""

    def test_make_table_returns_rich_table(self):
        from rich.table import Table

        from general_ludd.tui.tables import _make_table

        columns = [("Name", "cyan", 1, 10), ("Value", "green", 2, 20)]
        t = _make_table("Test", columns, rows=[("k1", "v1"), ("k2", "v2")])
        assert isinstance(t, Table)
        assert t.row_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ANSIBLE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnsibleRunnerPaths:
    """Verify the 3-tier collections path resolution."""

    def test_resolve_collections_paths_default(self):
        from general_ludd.ansible.paths import resolve_collections_paths

        entries = resolve_collections_paths()
        assert len(entries) >= 1
        sources = {e.source for e in entries}
        assert "bundled" in sources

    def test_resolve_collections_paths_with_project(self, tmp_path):
        from general_ludd.ansible.paths import resolve_collections_paths

        proj_coll = tmp_path / ".gludd" / "collections"
        proj_coll.mkdir(parents=True)
        entries = resolve_collections_paths(project_root=tmp_path)
        sources = {e.source for e in entries}
        assert "project" in sources
        assert "bundled" in sources

    def test_to_ansible_env(self, tmp_path):
        from general_ludd.ansible.paths import (
            CollectionsPathEntry,
            to_ansible_env,
        )

        e1 = CollectionsPathEntry(source="project", path=tmp_path / "a", precedence=0)
        e2 = CollectionsPathEntry(source="bundled", path=tmp_path / "b", precedence=1)
        env = to_ansible_env([e1, e2])
        assert "ANSIBLE_COLLECTIONS_PATH" in env
        paths = env["ANSIBLE_COLLECTIONS_PATH"].split(":")
        assert len(paths) == 2

    def test_collections_path_entry_frozen(self):
        from general_ludd.ansible.paths import CollectionsPathEntry

        e = CollectionsPathEntry(source="test", path=Path("/tmp"), precedence=0)
        with pytest.raises(FrozenInstanceError):
            e.source = "other"

    def test_activate_collection_version(self, tmp_path):
        import general_ludd.ansible.paths as ap

        base = tmp_path / "col"
        base.mkdir()
        ns_dir = base / "ansible_collections" / "general_ludd@latest" / "agent"
        ns_dir.mkdir(parents=True)
        root, cleanup = ap.activate_collection_version(
            base, namespace="general_ludd", collection="agent", version="latest"
        )
        assert root.exists()
        assert cleanup is not None
        link = root / "ansible_collections" / "general_ludd" / "agent"
        assert link.is_symlink()

    def test_version_symlink_activation(self, tmp_path):
        import general_ludd.ansible.paths as ap

        base = tmp_path / "col"
        base.mkdir()
        ver_dir = base / "ansible_collections" / "general_ludd@0.1.0" / "agent"
        (ver_dir / "roles").mkdir(parents=True)
        root, cleanup = ap.activate_collection_version(
            base,
            namespace="general_ludd",
            collection="agent",
            version="0.1.0",
        )
        assert root.exists()
        assert cleanup is not None
        assert cleanup.exists()


class TestAnsibleRunnerAdapter:
    """Verify the adapter's lifecycle, job dirs, var writing, playbook resolution."""

    def test_adapter_creation_default(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        assert adapter.private_data_dir is not None
        assert os.path.isdir(adapter.private_data_dir)
        assert "noop.yml" in adapter.registry

    def test_adapter_custom_registry(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        pb = tmp_path / "playbook.yml"
        pb.write_text("---\n- hosts: localhost\n")
        adapter = AnsibleRunnerAdapter(registry={"custom.yml": str(pb)})
        assert adapter.registry["custom.yml"] == str(pb)

    def test_resolve_playbook_known(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        pb = tmp_path / "pb.yml"
        pb.write_text("---\n- hosts: localhost\n")
        adapter = AnsibleRunnerAdapter(registry={"my.yml": str(pb)})
        resolved = adapter.resolve_playbook("my.yml")
        assert resolved == str(pb)

    def test_resolve_playbook_unknown(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            adapter.resolve_playbook("nonexistent.yml")

    def test_prepare_job_dirs(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        dirs = adapter.prepare_job_dirs("TEST_JOB_001")
        assert os.path.isdir(dirs["root"])
        assert os.path.isdir(dirs["env"])
        assert os.path.isdir(dirs["project"])
        assert os.path.isdir(dirs["inventory"])
        assert os.path.isdir(dirs["artifacts"])

    def test_prepare_job_dirs_duplicate(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.prepare_job_dirs("DUP_JOB")
        with pytest.raises(FileExistsError, match="already exists"):
            adapter.prepare_job_dirs("DUP_JOB")

    def test_prepare_job_dirs_bad_id(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="Invalid job_id"):
            adapter.prepare_job_dirs("")

    def test_write_vars(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.prepare_job_dirs("WRITE_JOB")
        path = adapter.write_vars("WRITE_JOB", {"key": "val"}, {"shared": 42})
        assert os.path.isfile(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["job_vars"]["key"] == "val"
        assert data["shared_vars"]["shared"] == 42

    def test_write_vars_no_shared(self):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        adapter.prepare_job_dirs("NOSHARE")
        path = adapter.write_vars("NOSHARE", {"a": 1})
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["job_vars"]["a"] == 1
        assert "shared_vars" not in data

    def test_set_project_root(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter()
        assert adapter._project_root is None
        adapter.set_project_root(tmp_path)
        assert adapter._project_root == tmp_path
        adapter.set_project_root(None)
        assert adapter._project_root is None

    def test_activate_collection(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        base = tmp_path / ".gludd" / "collections"
        base.mkdir(parents=True)
        ns_dir = base / "ansible_collections" / "general_ludd@latest" / "agent"
        (ns_dir / "roles").mkdir(parents=True)
        adapter = AnsibleRunnerAdapter(project_root=tmp_path)
        root = adapter.activate_collection("general_ludd", "agent", "latest")
        assert root.exists()

    def test_clear_collection_versions(self, tmp_path):
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        base = tmp_path / ".gludd" / "collections"
        base.mkdir(parents=True)
        ns_dir = base / "ansible_collections" / "general_ludd@0.2.0" / "agent"
        (ns_dir / "roles").mkdir(parents=True)
        adapter = AnsibleRunnerAdapter(project_root=tmp_path)
        adapter.activate_collection("general_ludd", "agent", "0.2.0")
        assert len(adapter._version_activation_roots) >= 1
        adapter.clear_collection_versions()
        assert adapter._version_activation_roots == []


class TestAnsibleCoreRunner:
    """Verify CoreAnsibleRunner instantiation and basic configuration."""

    def test_runner_instantiation_default(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pd = str(tmp_path / "private")
        os.makedirs(pd, exist_ok=True)
        runner = CoreAnsibleRunner(private_data_dir=pd)
        assert runner._private_data_dir == pd
        assert runner._process_isolation is None

    def test_runner_with_isolation(self, tmp_path):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        pd = str(tmp_path / "private")
        os.makedirs(pd, exist_ok=True)
        iso = ProcessIsolationConfig(enabled=False)
        runner = CoreAnsibleRunner(private_data_dir=pd, process_isolation=iso)
        assert runner._process_isolation is iso

    def test_runner_collected_events(self):
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        assert runner._collected_events == []


class TestAnsibleConversion:
    """Verify role argument conversion and output normalization."""

    def test_convert_role_args_bom_detect(self):
        from general_ludd.ansible.runner import _convert_role_args

        result = _convert_role_args("bom_detect", {"file_path": "/x.txt"})
        assert "--input-file" in result
        assert "/x.txt" in result

    def test_convert_role_args_font_analyze(self):
        from general_ludd.ansible.runner import _convert_role_args

        result = _convert_role_args("font_analyze", {"file_path": "/f.ttf"})
        assert "--input" in result
        assert "/f.ttf" in result

    def test_convert_role_args_homoglyph_scan(self):
        from general_ludd.ansible.runner import _convert_role_args

        result = _convert_role_args("homoglyph_scan", {"text": "abc"})
        assert "--input" in result
        assert "abc" in result

    def test_convert_role_args_i18n_extract_namespaces_output(self):
        from general_ludd.ansible.runner import _convert_role_args

        with patch("tempfile.gettempdir", return_value="/tmp"):
            defaulted = _convert_role_args(
                "i18n_extract",
                {"directory": "/work/source"},
            )
        assert defaulted[:2] == ["--source-dir", "/work/source"]
        assert defaulted[2] == "--output-dir"
        assert defaulted[3].startswith("/tmp/gludd-i18n-extract-")
        assert _convert_role_args(
            "i18n_extract",
            {"directory": "/work/source", "output_dir": "/work/output"},
        ) == [
            "--source-dir",
            "/work/source",
            "--output-dir",
            "/work/output",
        ]

    def test_convert_role_args_unknown_role(self):
        from general_ludd.ansible.runner import _convert_role_args

        result = _convert_role_args("unknown_role", {"a": 1})
        assert result == []

    def test_normalize_role_output_bom_detect(self):
        from general_ludd.ansible.runner import _normalize_role_output

        raw = _normalize_role_output("bom_detect", {"bom_detected": True, "encoding": "UTF-8"})
        assert raw["has_bom"] is True
        assert raw["encoding"] == "utf-8"

    def test_normalize_role_output_unicode_analyze(self):
        from general_ludd.ansible.runner import _normalize_role_output

        raw = _normalize_role_output(
            "unicode_analyze",
            {
                "input_length": 5,
                "codepoints": [{"codepoint": "U+0041", "name": "LATIN A", "category": "Lu"}],
            },
        )
        assert raw["character_count"] == 5
        assert raw["codepoint"] == "U+0041"

    def test_normalize_role_output_font_analyze_invalid(self):
        from general_ludd.ansible.runner import _normalize_role_output

        raw = _normalize_role_output("font_analyze", {"format": "xyz", "file": ""})
        assert "error" in raw

    def test_normalize_role_output_phonetic(self):
        from general_ludd.ansible.runner import _normalize_role_output

        raw = _normalize_role_output(
            "phonetic_transcribe",
            {
                "words": [{"transcription": "helou"}],
            },
        )
        assert "helou" in raw["ipa"]

    def test_normalize_role_output_locale(self):
        from general_ludd.ansible.runner import _normalize_role_output

        raw = _normalize_role_output("locale_format", {"locale": "fr_FR.UTF-8"})
        assert raw["language"] == "fr"
        assert raw["territory"] == "FR"
        assert raw["codeset"] == "UTF-8"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TERRAFORM OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestTerraformGenerator:
    """Verify Terraform HCL generation for all provider types."""

    @staticmethod
    def _make_config(provider="aws", gpu="a100_80", engine="vllm", **kwargs):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine

        kw = {
            "provider": ComputeProvider(provider),
            "gpu_type": GPUType(gpu),
            "engine": InferenceEngine(engine),
            "model_name": kwargs.pop("model_name", "meta-llama/Llama-3.2-1B"),
            "region": kwargs.pop("region", "us-east-1"),
            "gpu_count": kwargs.pop("gpu_count", 1),
            "max_cost_usd": kwargs.pop("max_cost_usd", 10.0),
            **kwargs,
        }
        return ComputeConfig(**kw)

    def test_generate_aws_module_style(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("aws", "a100_80")
        hcl = gen.generate(cfg)
        assert "required_providers" in hcl
        assert "hashicorp/aws" in hcl
        assert 'module "vllm_server"' in hcl
        assert "us-east-1" in hcl

    def test_generate_gcp_module_style(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("gcp", "h100", region="us-central1")
        hcl = gen.generate(cfg)
        assert "hashicorp/google" in hcl
        assert 'module "vllm_server"' in hcl
        assert "us-central1" in hcl

    def test_generate_azure_module_style(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("azure", "t4", region="eastus")
        hcl = gen.generate(cfg)
        assert "hashicorp/azurerm" in hcl
        assert 'module "vllm_server"' in hcl

    def test_generate_runpod(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("runpod", "l4")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_vast_ai(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("vast_ai", "rtx_4090")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_lambda_labs(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("lambda_labs", "a100_80")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_modal(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("modal", "t4")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_coreweave(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("coreweave", "l40s")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_digital_ocean(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("digital_ocean", "rtx_6000_ada")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_oracle(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("oracle", "a10")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_vsphere(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("vsphere", "a100_80")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_vmware(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("vmware", "a100_80")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_kubernetes(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("kubernetes", "t4")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_llamacpp_engine(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("gcp", "t4", engine="llamacpp")
        hcl = gen.generate(cfg)
        assert 'module "vllm_server"' in hcl

    def test_azure_containerapp_deploy_type(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config("azure", "t4", deploy_type="containerapp")
        hcl = gen.generate(cfg)
        assert len(hcl) > 0

    def test_generate_with_state_backend(self):
        from general_ludd.infra.terraform import TerraformGenerator
        from general_ludd.infra.terraform_state import StateBackendSelector

        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(health_check=lambda: True),
        )
        gen = TerraformGenerator(state_backend_selector=selector)
        cfg = self._make_config("aws", "a100_80", max_cost_usd=100.0)
        hcl = gen.generate(cfg)
        assert "backend" in hcl or "module" in hcl


class TestTerraformTfvars:
    """Verify tfvars generation from ComputeConfig."""

    def _make_config(self, **kwargs):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine

        kw = {
            "provider": ComputeProvider(kwargs.pop("provider", "aws")),
            "gpu_type": GPUType(kwargs.pop("gpu", "a100_80")),
            "engine": InferenceEngine(kwargs.pop("engine", "vllm")),
            "model_name": kwargs.pop("model_name", "meta-llama/Llama-3.2-1B"),
            "region": kwargs.pop("region", "us-east-1"),
            "gpu_count": kwargs.pop("gpu_count", 1),
            "max_cost_usd": kwargs.pop("max_cost_usd", 10.0),
            **kwargs,
        }
        return ComputeConfig(**kw)

    def test_build_tfvars_basic(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config()
        tvars = gen.build_tfvars(cfg)
        assert "provider" in tvars
        assert '"aws"' in tvars
        assert "gpu_type" in tvars
        assert '"a100_80"' in tvars
        assert "model_name" in tvars
        assert "gpu_count" in tvars
        assert "max_cost_usd" in tvars
        assert "region" in tvars
        assert "engine" in tvars

    def test_build_tfvars_with_workload(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config(workload_type="realtime_api")
        tvars = gen.build_tfvars(cfg)
        assert "realtime_api" in tvars

    def test_build_tfvars_with_deployment_profile(self):
        from general_ludd.infra.terraform import TerraformGenerator

        gen = TerraformGenerator()
        cfg = self._make_config(
            deployment_profile={
                "tensor_parallel": 4,
                "enforce_eager": True,
                "quantization": "awq",
            }
        )
        tvars = gen.build_tfvars(cfg)
        assert "tensor_parallel" in tvars
        assert "enforce_eager" in tvars

    def test_escape_tfvar_value_escaping(self):
        from general_ludd.infra.terraform import escape_tfvar_value

        assert escape_tfvar_value("hello") == '"hello"'
        assert escape_tfvar_value('a"b') == '"a\\"b"'
        assert escape_tfvar_value("a\\b") == '"a\\\\b"'
        assert escape_tfvar_value("${var}") == '"\\${var}"'
        assert escape_tfvar_value("a\nb") == '"a\\nb"'

    def test_engine_serve_cmd_vllm(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
        from general_ludd.infra.terraform import _engine_serve_cmd

        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            engine=InferenceEngine.VLLM,
            model_name="meta-llama/Llama-3.2-1B",
            region="us-east-1",
        )
        cmd = _engine_serve_cmd(cfg)
        assert "docker" in cmd
        assert "Llama-3.2-1B" in cmd

    def test_engine_serve_cmd_llamacpp(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
        from general_ludd.infra.terraform import _engine_serve_cmd

        cfg = ComputeConfig(
            provider=ComputeProvider.GCP,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="llama-7b",
            region="us-central1",
        )
        cmd = _engine_serve_cmd(cfg)
        assert "docker" in cmd
        assert "llama-7b" in cmd
        assert "-m" in cmd

    def test_user_data_script(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
        from general_ludd.infra.terraform import _user_data_script

        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            engine=InferenceEngine.VLLM,
            model_name="model-x",
            max_cost_usd=25.0,
            timeout_minutes=120,
            workload_type="batch_inference",
        )
        script = _user_data_script(cfg)
        assert "#!/bin/bash" in script
        assert "MAX_COST=25.0" in script
        assert "TIMEOUT_MIN=120" in script
        assert "WORKLOAD_TYPE=batch_inference" in script


class TestTerraformStateBackend:
    """Verify state backend selection."""

    def test_local_backend_default(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
        from general_ludd.infra.terraform_state import StateBackendSelector

        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(health_check=lambda: False),
        )
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            engine=InferenceEngine.VLLM,
            model_name="x",
        )
        backend = selector.select(cfg)
        assert backend.kind == "local"

    def test_remote_backend_above_threshold(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
        from general_ludd.infra.terraform_state import StateBackendSelector

        selector = StateBackendSelector(
            openbao_client=MagicMock(),
            secrets_manager=MagicMock(health_check=lambda: True),
        )
        selector.cost_threshold_usd = 10.0
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            engine=InferenceEngine.VLLM,
            model_name="x",
            max_cost_usd=100.0,
        )
        backend = selector.select(cfg, deployment_id="DEP_1")
        assert backend.kind in ("openbao_kv", "http")

    def test_state_backend_config_frozen(self):
        from general_ludd.infra.terraform_state import StateBackendConfig

        cfg = StateBackendConfig(kind="local", path="/tmp/state")
        assert cfg.kind == "local"
        assert cfg.path == "/tmp/state"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INFRASTRUCTURE COMPUTE
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeConfig:
    """Verify ComputeConfig model validation and defaults."""

    def test_valid_config(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine

        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            engine=InferenceEngine.VLLM,
            model_name="meta-llama/Llama-3.2-1B",
        )
        assert cfg.provider == ComputeProvider.AWS
        assert cfg.gpu_type == GPUType.A100_80
        assert cfg.gpu_count == 1
        assert cfg.spot is True
        assert cfg.max_cost_usd == 10.0

    def test_gpu_count_minimum(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, gpu_count=0, model_name="x")

    def test_max_cost_must_be_positive(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, max_cost_usd=0, model_name="x")

    def test_timeout_must_be_positive(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, timeout_minutes=0, model_name="x")

    def test_disk_size_minimum(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, disk_size_gb=0, model_name="x")

    def test_model_name_validation(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        valid = ["meta-llama/Llama-3.2-1B", "ghcr.io/org/repo:tag", "ghcr.io/org/repo@sha256:abc123", "llama-7b"]
        for name in valid:
            cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name=name)
            assert cfg.model_name == name

        invalid = ["bad$name", "name with spaces", 'name"quote']
        for name in invalid:
            with pytest.raises(ValueError):
                ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name=name)

    def test_region_validation(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        valid = ["us-east-1", "eu-west-2", "us-central1", "eastus"]
        for r in valid:
            cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, region=r, model_name="x")
            assert cfg.region == r

        invalid = ["bad region", "us east 1"]
        for r in invalid:
            with pytest.raises(ValueError):
                ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, region=r, model_name="x")

    def test_workload_type_validation(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        valid = ["batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation", ""]
        for wt in valid:
            cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, workload_type=wt, model_name="x")
            assert cfg.workload_type == wt

        with pytest.raises(ValueError):
            ComputeConfig(
                provider=ComputeProvider.AWS, gpu_type=GPUType.T4, workload_type="invalid_type", model_name="x"
            )

    def test_all_providers_enum(self):
        from general_ludd.infra.compute import ComputeProvider

        providers = list(ComputeProvider)
        assert len(providers) >= 17
        for p in providers:
            assert isinstance(p.value, str)
            assert len(p.value) > 0

    def test_all_gpu_types_enum(self):
        from general_ludd.infra.compute import GPUType

        gpus = list(GPUType)
        assert len(gpus) >= 12
        for g in gpus:
            assert isinstance(g.value, str)

    def test_all_engines_enum(self):
        from general_ludd.infra.compute import InferenceEngine

        engines = list(InferenceEngine)
        assert InferenceEngine.VLLM in engines
        assert InferenceEngine.LLAMACPP in engines

    def test_compute_instance_model(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        inst = ComputeInstance(
            instance_id="i-abc123",
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
        )
        assert inst.instance_id == "i-abc123"
        assert inst.status == "pending"
        assert inst.port == 8000
        assert inst.cost_incurred == 0.0

    def test_compute_instance_port_validation(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeInstance(instance_id="x", provider=ComputeProvider.AWS, gpu_type=GPUType.T4, port=70000)

    def test_compute_instance_cost_non_negative(self):
        from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType

        with pytest.raises(ValueError):
            ComputeInstance(instance_id="x", provider=ComputeProvider.AWS, gpu_type=GPUType.T4, cost_incurred=-1.0)

    def test_allowed_cidr_validation(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        valid = ["127.0.0.1/32", "0.0.0.0/0", "10.0.0.0/16", "::1/128"]
        for cidr in valid:
            cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, allowed_cidr=cidr, model_name="x")
            assert cfg.allowed_cidr == cidr

    def test_guided_decoding_backend_validation(self):
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType

        for backend in ("outlines", "xgrammar", "lm-format-enforcer", ""):
            cfg = ComputeConfig(
                provider=ComputeProvider.AWS, gpu_type=GPUType.T4, guided_decoding_backend=backend, model_name="x"
            )
            assert cfg.guided_decoding_backend == backend

        with pytest.raises(ValueError):
            ComputeConfig(
                provider=ComputeProvider.AWS, gpu_type=GPUType.T4, guided_decoding_backend="bad_backend", model_name="x"
            )


class TestInfraCostTracker:
    """Verify infrastructure cost tracking with actual API."""

    def test_cost_record_creation(self):
        from general_ludd.infra.cost_tracker import InfraCostRecord

        r = InfraCostRecord(
            provider="aws",
            resource_type="gpu_instance",
            resource_id="i-123",
            cost_usd=5.25,
            gpu_type="a100_80",
            gpu_count=2,
            region="us-east-1",
            spot=True,
        )
        assert r.provider == "aws"
        assert r.cost_usd == 5.25
        assert r.gpu_type == "a100_80"
        assert r.gpu_count == 2

    def test_cost_tracker_instantiation(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        assert tracker is not None
        snap = tracker.snapshot()
        assert snap["total_cost"] == 0.0
        assert snap["record_count"] == 0

    def test_cost_tracker_record_and_snapshot(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "i-1", 10.0, gpu_type="a100_80", gpu_count=1)
        tracker.record("aws", "gpu_instance", "i-2", 15.0, gpu_type="a100_80", gpu_count=1)
        snap = tracker.snapshot()
        assert snap["by_provider"]["aws"] == 25.0
        assert snap["total_cost"] == 25.0
        assert snap["record_count"] == 2

    def test_cost_tracker_multi_provider(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "aw1", 10.0)
        tracker.record("gcp", "gpu_instance", "gc1", 20.0)
        snap = tracker.snapshot()
        assert snap["by_provider"]["aws"] == 10.0
        assert snap["by_provider"]["gcp"] == 20.0

    def test_cost_tracker_records_list(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "r1", 50.0)
        recs = tracker.records()
        assert len(recs) == 1
        assert recs[0].cost_usd == 50.0

    def test_cost_tracker_provider_breakdown(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        tracker.record("aws", "gpu_instance", "r1", 5.0)
        tracker.record("aws", "cpu_instance", "r2", 3.0)
        bd = tracker.provider_breakdown("aws")
        assert bd["gpu_instance"] == 5.0
        assert bd["cpu_instance"] == 3.0

    def test_cost_tracker_negative_cost_rejected(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        with pytest.raises(ValueError, match="cost_usd must be finite and >= 0"):
            tracker.record("aws", "gpu_instance", "r1", -1.0)

    def test_hourly_rate_defaults(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("aws", "p4d.24xlarge")
        assert rate > 0

    def test_hourly_rate_unknown_fallback(self):
        from general_ludd.infra.cost_tracker import InfraCostTracker

        tracker = InfraCostTracker()
        rate = tracker.hourly_rate_usd("unknown", "unknown-sku")
        assert rate > 0


class TestPricing:
    """Verify pricing tables and cost helpers."""

    def test_pricing_has_known_models(self):
        from general_ludd.infra.pricing import PRICING

        assert "claude-3-5-sonnet-20241022" in PRICING
        assert "gpt-4o" in PRICING
        assert "gpt-4o-mini" in PRICING
        assert "__default__" in PRICING

    def test_pricing_structure(self):
        from general_ludd.infra.pricing import PRICING

        for _model, rates in PRICING.items():
            assert isinstance(rates, tuple)
            assert len(rates) == 2
            input_rate, output_rate = rates
            assert input_rate >= 0
            assert output_rate >= 0

    def test_infra_pricing_has_keys(self):
        from general_ludd.infra.pricing import INFRA_PRICING

        assert "gpu_second" in INFRA_PRICING
        assert "cpu_second" in INFRA_PRICING
        assert "__default__" in INFRA_PRICING

    def test_infra_pricing_values_positive(self):
        from general_ludd.infra.pricing import INFRA_PRICING

        for _kind, rate in INFRA_PRICING.items():
            assert rate >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PROVIDER ENUMERATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderRegistry:
    """Verify provider registry, filtering, and pricing queries."""

    def test_builtin_providers_count(self):
        from general_ludd.infra.providers import _BUILTIN_PROVIDERS

        assert len(_BUILTIN_PROVIDERS) >= 16

    def test_provider_info_model(self):
        from general_ludd.infra.compute import ComputeProvider, GPUType
        from general_ludd.infra.providers import ProviderInfo

        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Amazon Web Services",
            terraform_provider="hashicorp/aws",
            supports_spot=True,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
            pricing={"t4": 0.20},
        )
        assert info.display_name == "Amazon Web Services"
        assert info.supports_spot is True
        assert info.terraform_provider == "hashicorp/aws"

    def test_provider_info_display_name_validation(self):
        from general_ludd.infra.compute import ComputeProvider, GPUType
        from general_ludd.infra.providers import ProviderInfo

        with pytest.raises(ValueError):
            ProviderInfo(
                provider=ComputeProvider.AWS,
                display_name="",
                terraform_provider="hashicorp/aws",
                supports_spot=False,
                sub_hour_billing=False,
                min_gpu=GPUType.T4,
                max_gpu=GPUType.T4,
            )

    def test_registry_get(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.AWS)
        assert info.provider == ComputeProvider.AWS
        assert info.display_name == "Amazon Web Services"

    def test_registry_get_all_providers(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        for provider in ComputeProvider:
            try:
                info = reg.get(provider)
                assert info.display_name != ""
                assert info.terraform_provider != ""
            except KeyError:
                pass

    def test_list_providers(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        providers = reg.list_providers()
        assert len(providers) >= 16
        names = {p.display_name for p in providers}
        assert "Amazon Web Services" in names
        assert "Google Cloud Platform" in names
        assert "Microsoft Azure" in names

    def test_get_cheapest_for_gpu(self):
        from general_ludd.infra.compute import GPUType
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        info = reg.get_cheapest_for_gpu(GPUType.A100_80)
        assert info is not None
        assert "a100_80" in info.pricing
        assert info.pricing["a100_80"] > 0

    def test_get_cheapest_for_gpu_unknown(self):
        from general_ludd.infra.compute import GPUType
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="No provider supports"):
            reg.get_cheapest_for_gpu(GPUType.AMD_MI250)

    def test_list_by_price(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        priced = reg.list_by_price()
        assert len(priced) > 0
        for i in range(len(priced) - 1):
            assert priced[i][1] <= priced[i + 1][1]

    def test_filter_providers_by_spot(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        spot_providers = [p for p in reg.list_providers() if p.supports_spot]
        assert len(spot_providers) >= 5
        for p in spot_providers:
            assert p.supports_spot is True

    def test_filter_providers_by_sub_hour(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        sub_hour = [p for p in reg.list_providers() if p.sub_hour_billing]
        assert len(sub_hour) >= 5

    def test_auth_env_providers(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        vmware_info = reg.get(ComputeProvider.VMWARE)
        assert len(vmware_info.auth_env) >= 1
        together_info = reg.get(ComputeProvider.TOGETHER_AI)
        assert "TOGETHER_API_KEY" in together_info.auth_env
        fireworks_info = reg.get(ComputeProvider.FIREWORKS_AI)
        assert "FIREWORKS_API_KEY" in fireworks_info.auth_env

    def test_api_only_providers(self):
        from general_ludd.infra.compute import ComputeProvider
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        for prov in (
            ComputeProvider.TOGETHER_AI,
            ComputeProvider.FIREWORKS_AI,
            ComputeProvider.HUGGINGFACE,
            ComputeProvider.REPLICATE,
        ):
            info = reg.get(prov)
            assert "API-only" in info.terraform_provider

    def test_registry_pricing_positive(self):
        from general_ludd.infra.providers import ProviderRegistry

        reg = ProviderRegistry()
        for info in reg.list_providers():
            for gpu, price in info.pricing.items():
                assert price >= 0, f"{info.display_name} {gpu} has negative price"
