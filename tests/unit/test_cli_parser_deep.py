"""Deep argument-parser structural tests for every CLI subcommand.

Covers: subcommand registration, argument types, default values, choices constraints,
required arguments, positional vs optional, nested subparsers, deprecated aliases.
"""

from __future__ import annotations

import argparse

import pytest


def _build() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    from general_ludd.cli import build_parser

    return build_parser()


def _top_choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    try:
        return parser._subparsers._group_actions[0].choices
    except (AttributeError, IndexError):
        for action in parser._actions:
            if hasattr(action, "choices") and action.dest == "command":
                return action.choices
        return {}


def _sub_choices(subparser: argparse.ArgumentParser, dest: str) -> dict[str, argparse.ArgumentParser]:
    for action in subparser._actions:
        if hasattr(action, "choices") and action.dest == dest:
            return action.choices
    return {}


def _get_defaults(parser: argparse.ArgumentParser, args: list[str]) -> argparse.Namespace:
    return parser.parse_args(args)


# ---------------------------------------------------------------------------
# Top-level subcommand registrations
# ---------------------------------------------------------------------------

EXPECTED_TOP = {
    "daemon",
    "add",
    "status",
    "list",
    "log-level",
    "deployments",
    "version",
    "health",
    "selftest",
    "smoke",
    "models",
    "local-serve",
    "worktree",
    "project",
    "config",
    "mcp",
    "skills",
    "compute",
    "scores",
    "leaderboard",
    "chat",
    "help",
    "filestore",
    "preflight",
    "tui",
    "audit-plugins",
    "collection",
    "integrity",
    "ansible",
    "hooks",
    "workers",
    "agents",
    "metrics",
    "reload",
    "templates",
    "playbooks",
    "code",
    "slurm",
    "connectors",
    "login",
    "onboard",
    "perm",
    "payment",
    "human-todo",
    "model",
    "self-improve",
    "remediation",
    "ornith",
    "searx",
    "service",
    "deploy-check",
    "core-changes",
    "spec-quality",
    "make",
    "cloud",
    "test",
    "pause",
    "resume",
}


def test_build_parser_returns_tuple() -> None:
    parser, sub_map = _build()
    assert isinstance(parser, argparse.ArgumentParser)
    assert isinstance(sub_map, dict)


def test_all_top_level_subcommands_registered() -> None:
    parser, _sub_map = _build()
    registered = set(_top_choices(parser).keys())
    missing = EXPECTED_TOP - registered
    extra = registered - EXPECTED_TOP
    assert not missing, f"Missing top-level subcommands: {missing}"
    assert not extra, f"Unexpected top-level subcommands: {extra}"


def test_subcommand_map_has_nested_subparsers_registered() -> None:
    parser, sub_map = _build()
    assert len(sub_map) > 20, f"subcommand_map should have 20+ entries, got {len(sub_map)}"
    top_level = set(_top_choices(parser).keys())
    map_only = set(sub_map.keys()) - top_level
    assert "test-bg" in map_only or not map_only, (
        f"subcommand_map keys not in top-level (expected only 'test-bg'): {map_only}"
    )


# ---------------------------------------------------------------------------
# daemon
# ---------------------------------------------------------------------------


class TestDaemonParser:
    def test_defaults(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon"])
        assert args.host == "127.0.0.1"
        assert args.port == 8000
        assert args.log_level == "info"
        assert args.tick_interval == 1.0
        assert args.workers == 1

    def test_port_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon", "--port", "8080"])
        assert args.port == 8080
        assert isinstance(args.port, int)

    def test_tick_interval_is_float(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon", "--tick-interval", "2.5"])
        assert args.tick_interval == 2.5
        assert isinstance(args.tick_interval, float)

    def test_workers_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon", "--workers", "4"])
        assert args.workers == 4
        assert isinstance(args.workers, int)

    def test_log_level_choices_valid(self) -> None:
        parser, _ = _build()
        for level in ("debug", "info", "warning", "error"):
            args = parser.parse_args(["daemon", "--log-level", level])
            assert args.log_level == level

    def test_log_level_choice_invalid_rejected(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["daemon", "--log-level", "invalid"])

    def test_config_dir_optional_string(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon", "--config-dir", "/etc/gludd"])
        assert args.config_dir == "/etc/gludd"

    def test_func_set(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["daemon"])
        assert args.func is not None


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestAddParser:
    def test_required_title_positional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["add", "Fix login bug"])
        assert args.title == "Fix login bug"

    def test_missing_title_rejected(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["add"])

    def test_default_defaults(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["add", "Test todo"])
        assert args.queue == "core"
        assert args.priority == "medium"
        assert args.work_type == "code"
        assert args.description == ""
        assert args.daemon_url == "http://localhost:8000"

    def test_project_option(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["add", "Task", "--project", "my-proj"])
        assert args.project == "my-proj"


# ---------------------------------------------------------------------------
# version / health / deployments / log-level
# ---------------------------------------------------------------------------


class TestSimpleCommands:
    def test_version_no_args(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["version"])
        assert args.func is not None

    def test_version_flag_global(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])

    def test_health_daemon_url_default(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["health"])
        assert args.daemon_url == "http://localhost:8000"

    def test_deployments_daemon_url_default(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["deployments"])
        assert args.daemon_url == "http://localhost:8000"

    def test_log_level_choices(self) -> None:
        parser, _ = _build()
        for level in ("debug", "info", "warning", "error"):
            args = parser.parse_args(["log-level", level])
            assert args.level == level

    def test_log_level_invalid_rejected(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["log-level", "trace"])


# ---------------------------------------------------------------------------
# models (nested subparsers)
# ---------------------------------------------------------------------------


class TestModelsParser:
    def test_models_subcommands_present(self) -> None:
        parser, _ = _build()
        models = _top_choices(parser)["models"]
        subs = _sub_choices(models, "models_command")
        expected = {
            "search",
            "searx-search",
            "deploy",
            "downloaded",
            "discover",
            "discovered",
            "list",
            "add",
            "remove",
            "performance",
            "ranking",
            "router-status",
            "router-set",
        }
        assert set(subs.keys()) == expected

    def test_models_search_defaults(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["models", "search"])
        assert args.query == ""
        assert args.limit == 20
        assert args.daemon_url == "http://localhost:8000"

    def test_models_search_limit_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["models", "search", "--limit", "50"])
        assert args.limit == 50
        assert isinstance(args.limit, int)

    def test_models_searx_search_source_choices(self) -> None:
        parser, _ = _build()
        for src in ("huggingface", "github", "web"):
            args = parser.parse_args(["models", "searx-search", "--source", src])
            assert args.source == src

    def test_models_deploy_arg_types(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["models", "deploy", "my-model", "--gpu-count", "4", "--max-cost", "25.5"])
        assert args.gpu_count == 4
        assert isinstance(args.gpu_count, int)
        assert args.max_cost == 25.5
        assert isinstance(args.max_cost, float)

    def test_models_deploy_engine_choices(self) -> None:
        parser, _ = _build()
        for engine in ("vllm", "llamacpp"):
            args = parser.parse_args(["models", "deploy", "m", "--engine", engine])
            assert args.engine == engine

    def test_models_deploy_workload_type_choices(self) -> None:
        parser, _ = _build()
        valid = {"batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation"}
        for wt in valid:
            args = parser.parse_args(["models", "deploy", "m", "--workload-type", wt])
            assert args.workload_type == wt

    def test_models_ranking_strategy_choices(self) -> None:
        parser, _ = _build()
        from general_ludd.models.performance_router import DEFAULT_STRATEGIES

        for strat in DEFAULT_STRATEGIES:
            args = parser.parse_args(["models", "ranking", "--task-type", "code", "--strategy", strat])
            assert args.strategy == strat

    def test_models_router_set_required_args(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["models", "router-set"])
        with pytest.raises(SystemExit):
            parser.parse_args(["models", "router-set", "--task-type", "code"])
        args = parser.parse_args(["models", "router-set", "--task-type", "code", "--strategy", "cheapest"])
        assert args.task_type == "code"
        assert args.strategy == "cheapest"


# ---------------------------------------------------------------------------
# compute
# ---------------------------------------------------------------------------


class TestComputeParser:
    def test_compute_subcommands_present(self) -> None:
        parser, _ = _build()
        compute = _top_choices(parser)["compute"]
        subs = _sub_choices(compute, "compute_command")
        assert set(subs.keys()) == {"endpoints", "register", "unregister", "launch", "destroy"}

    def test_compute_register_required_args(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["compute", "register"])

    def test_compute_launch_required_args(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["compute", "launch"])
        with pytest.raises(SystemExit):
            parser.parse_args(["compute", "launch", "--provider", "aws"])

    def test_compute_launch_no_spot_is_store_true(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(
            [
                "compute",
                "launch",
                "--provider",
                "aws",
                "--gpu",
                "a100",
                "--model",
                "llama",
            ]
        )
        assert args.no_spot is False
        args2 = parser.parse_args(
            [
                "compute",
                "launch",
                "--provider",
                "aws",
                "--gpu",
                "a100",
                "--model",
                "llama",
                "--no-spot",
            ]
        )
        assert args2.no_spot is True

    def test_compute_launch_workload_type_choices(self) -> None:
        parser, _ = _build()
        valid = {"batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation"}
        for wt in valid:
            args = parser.parse_args(
                [
                    "compute",
                    "launch",
                    "--provider",
                    "aws",
                    "--gpu",
                    "t4",
                    "--model",
                    "test",
                    "--workload-type",
                    wt,
                ]
            )
            assert args.workload_type == wt


# ---------------------------------------------------------------------------
# local-serve
# ---------------------------------------------------------------------------


class TestLocalServeParser:
    def test_required_model(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["local-serve"])

    def test_defaults(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["local-serve", "--model", "llama2"])
        assert args.engine == "vllm"
        assert args.host == "localhost"
        assert args.port == 8001
        assert args.gpu_layers == -1
        assert args.context_size == 4096

    def test_engine_choices(self) -> None:
        parser, _ = _build()
        for engine in ("vllm", "llamacpp"):
            args = parser.parse_args(["local-serve", "--model", "m", "--engine", engine])
            assert args.engine == engine

    def test_port_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["local-serve", "--model", "m", "--port", "9000"])
        assert args.port == 9000
        assert isinstance(args.port, int)

    def test_gpu_layers_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["local-serve", "--model", "m", "--gpu-layers", "32"])
        assert args.gpu_layers == 32
        assert isinstance(args.gpu_layers, int)

    def test_context_size_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["local-serve", "--model", "m", "--context-size", "8192"])
        assert args.context_size == 8192
        assert isinstance(args.context_size, int)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLoginParser:
    def test_service_positional_optional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["login"])
        assert args.service is None

    def test_service_positional_set(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["login", "github"])
        assert args.service == "github"

    def test_list_flag_store_true(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["login", "--list"])
        assert args.list is True

    def test_timeout_is_float(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["login", "github", "--timeout", "300"])
        assert args.timeout == 300.0
        assert isinstance(args.timeout, float)

    def test_timeout_default(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["login", "github"])
        assert args.timeout == 120.0

    def test_store_choices(self) -> None:
        parser, _ = _build()
        for store in ("env", "openbao"):
            args = parser.parse_args(["login", "github", "--store", store])
            assert args.store == store

    def test_store_invalid_rejected(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["login", "github", "--store", "invalid"])


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class TestChatParser:
    def test_export_choices(self) -> None:
        parser, _ = _build()
        for fmt in ("md", "json", "html"):
            args = parser.parse_args(["chat", "--export", fmt])
            assert args.export == fmt

    def test_stream_store_true(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["chat"])
        assert args.stream is False
        args2 = parser.parse_args(["chat", "--stream"])
        assert args2.stream is True

    def test_save_interval_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["chat", "--save-interval", "10"])
        assert args.save_interval == 10
        assert isinstance(args.save_interval, int)

    def test_max_context_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["chat", "--max-context", "32768"])
        assert args.max_context == 32768
        assert isinstance(args.max_context, int)


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


class TestProjectParser:
    def test_project_subcommands_present(self) -> None:
        parser, _ = _build()
        proj = _top_choices(parser)["project"]
        subs = _sub_choices(proj, "project_command")
        assert {"add", "list", "remove", "init", "paths"} <= set(subs.keys())

    def test_project_add_choices(self) -> None:
        parser, _ = _build()
        for mode in ("active", "passive_external", "worktree_monitor"):
            args = parser.parse_args(["project", "add", "test", "--dispatch-mode", mode])
            assert args.dispatch_mode == mode


# ---------------------------------------------------------------------------
# integrity
# ---------------------------------------------------------------------------


class TestIntegrityParser:
    def test_integrity_subcommands_present(self) -> None:
        parser, _ = _build()
        integ = _top_choices(parser)["integrity"]
        subs = _sub_choices(integ, "integrity_command")
        assert set(subs.keys()) == {"scan", "report", "approve", "reject", "log"}

    def test_integrity_approve_required_reason(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["integrity", "approve", "config.yml"])

    def test_integrity_scan_paths_nargs(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["integrity", "scan", "--paths", "/a", "/b", "/c"])
        assert args.paths == ["/a", "/b", "/c"]


# ---------------------------------------------------------------------------
# selftest backward compatibility
# ---------------------------------------------------------------------------


class TestSelftestParser:
    def test_selftest_registered(self) -> None:
        parser, _ = _build()
        assert "selftest" in _top_choices(parser)

    def test_selftest_has_daemon_url(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["selftest"])
        assert args.daemon_url == "http://localhost:8000"


# ---------------------------------------------------------------------------
# test subcommand tree
# ---------------------------------------------------------------------------


class TestTestSubcommandTree:
    def test_test_background_subcommands(self) -> None:
        parser, _ = _build()
        test_p = _top_choices(parser)["test"]
        bg = _sub_choices(test_p, "test_command")["background"]
        subs = _sub_choices(bg, "testbg_command")
        assert set(subs.keys()) == {"launch", "status", "poll-all", "kill", "results"}

    def test_test_background_launch_required_testfile(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["test", "background", "launch"])

    def test_test_background_launch_wait_flag(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["test", "background", "launch", "test_file.py"])
        assert args.wait is False
        args2 = parser.parse_args(["test", "background", "launch", "test_file.py", "--wait"])
        assert args2.wait is True

    def test_test_self_parse(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["test", "self", "--daemon-url", "http://localhost:8888"])
        assert args.daemon_url == "http://localhost:8888"


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_pause_subcommands(self) -> None:
        parser, _ = _build()
        pause = _top_choices(parser)["pause"]
        subs = _sub_choices(pause, "pause_command")
        assert set(subs.keys()) == {"list", "project", "model"}

    def test_pause_project_requires_target(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["pause", "project"])

    def test_pause_model_reason_default(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["pause", "model", "gpt-4o"])
        assert args.reason == ""
        assert args.daemon_url == "http://localhost:8000"

    def test_resume_subcommands(self) -> None:
        parser, _ = _build()
        resume = _top_choices(parser)["resume"]
        subs = _sub_choices(resume, "resume_command")
        assert set(subs.keys()) == {"project", "model"}

    def test_resume_requires_target(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["resume", "project"])


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


class TestOnboardParser:
    def test_provider_positional_optional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["onboard"])
        assert args.provider is None

    def test_dry_run_store_true(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["onboard", "aws"])
        assert args.dry_run is False
        args2 = parser.parse_args(["onboard", "aws", "--dry-run"])
        assert args2.dry_run is True


# ---------------------------------------------------------------------------
# make
# ---------------------------------------------------------------------------


class TestMakeParser:
    def test_required_target(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["make"])

    def test_timeout_is_int(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["make", "test", "--timeout", "600"])
        assert args.timeout == 600
        assert isinstance(args.timeout, int)

    def test_env_nargs(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["make", "lint", "--env", "FOO=bar", "BAZ=qux"])
        assert args.env == ["FOO=bar", "BAZ=qux"]


# ---------------------------------------------------------------------------
# cloud
# ---------------------------------------------------------------------------


class TestCloudParser:
    def test_iam_generate_required_provider(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["cloud", "iam", "generate"])

    def test_iam_generate_provider_choices(self) -> None:
        parser, _ = _build()
        for prov in ("azure", "aws", "gcp"):
            args = parser.parse_args(["cloud", "iam", "generate", "--provider", prov])
            assert args.provider == prov

    def test_iam_generate_persona_choices(self) -> None:
        parser, _ = _build()
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            args = parser.parse_args(["cloud", "iam", "generate", "--provider", "aws", "--persona", persona])
            assert args.persona == persona

    def test_iam_validate_required_file(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["cloud", "iam", "validate", "--provider", "aws"])


# ---------------------------------------------------------------------------
# slurm
# ---------------------------------------------------------------------------


class TestSlurmParser:
    def test_slurm_subcommands_present(self) -> None:
        parser, _ = _build()
        slurm = _top_choices(parser)["slurm"]
        subs = _sub_choices(slurm, "slurm_command")
        assert set(subs.keys()) == {"status", "submit", "job", "cancel", "list"}

    def test_slurm_submit_requires_command(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["slurm", "submit"])

    def test_slurm_cancel_requires_job_id(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["slurm", "cancel"])


# ---------------------------------------------------------------------------
# connectors
# ---------------------------------------------------------------------------


class TestConnectorsParser:
    def test_connectors_subcommands_present(self) -> None:
        parser, _ = _build()
        conn = _top_choices(parser)["connectors"]
        subs = _sub_choices(conn, "connectors_command")
        assert set(subs.keys()) == {"list", "health", "query"}

    def test_connectors_query_requires_source(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["connectors", "query"])

    def test_connectors_query_spec_default(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["connectors", "query", "my-source"])
        assert args.spec == "{}"


# ---------------------------------------------------------------------------
# ansible
# ---------------------------------------------------------------------------


class TestAnsibleParser:
    def test_ansible_subcommands_present(self) -> None:
        parser, _ = _build()
        ansible = _top_choices(parser)["ansible"]
        subs = _sub_choices(ansible, "ansible_command")
        assert set(subs.keys()) == {"search", "install", "builtins"}

    def test_ansible_search_type_choices(self) -> None:
        parser, _ = _build()
        for typ in ("role", "collection"):
            args = parser.parse_args(["ansible", "search", "nginx", "--type", typ])
            assert args.type == typ


# ---------------------------------------------------------------------------
# hooks / workers / agents / metrics / reload / templates / playbooks / code
# ---------------------------------------------------------------------------


class TestAdminSubcommands:
    def test_hooks_subcommands(self) -> None:
        parser, _ = _build()
        hooks = _top_choices(parser)["hooks"]
        subs = _sub_choices(hooks, "hooks_command")
        assert set(subs.keys()) == {"list", "register", "delete"}

    def test_workers_subcommands(self) -> None:
        parser, _ = _build()
        workers = _top_choices(parser)["workers"]
        subs = _sub_choices(workers, "workers_command")
        assert set(subs.keys()) == {"list", "ping"}

    def test_agents_subcommands(self) -> None:
        parser, _ = _build()
        agents = _top_choices(parser)["agents"]
        subs = _sub_choices(agents, "agents_command")
        assert set(subs.keys()) == {"list"}

    def test_metrics_subcommands(self) -> None:
        parser, _ = _build()
        metrics = _top_choices(parser)["metrics"]
        subs = _sub_choices(metrics, "metrics_command")
        assert set(subs.keys()) == {"cost", "report"}

    def test_templates_subcommands(self) -> None:
        parser, _ = _build()
        tmpl = _top_choices(parser)["templates"]
        subs = _sub_choices(tmpl, "templates_command")
        assert set(subs.keys()) == {"list", "refresh"}

    def test_playbooks_subcommands(self) -> None:
        parser, _ = _build()
        pb = _top_choices(parser)["playbooks"]
        subs = _sub_choices(pb, "playbooks_command")
        assert set(subs.keys()) == {"list", "refresh"}

    def test_code_subcommands(self) -> None:
        parser, _ = _build()
        code = _top_choices(parser)["code"]
        subs = _sub_choices(code, "code_command")
        assert set(subs.keys()) == {"graph", "search"}


# ---------------------------------------------------------------------------
# filestore
# ---------------------------------------------------------------------------


class TestFilestoreParser:
    def test_filestore_subcommands_present(self) -> None:
        parser, _ = _build()
        fs = _top_choices(parser)["filestore"]
        subs = _sub_choices(fs, "filestore_command")
        assert set(subs.keys()) == {"list", "cat", "bootstrap", "binaries"}

    def test_filestore_cat_requires_path(self) -> None:
        parser, _ = _build()
        with pytest.raises(SystemExit):
            parser.parse_args(["filestore", "cat"])

    def test_filestore_list_path_nargs_optional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["filestore", "list"])
        assert args.path == "/"
        args2 = parser.parse_args(["filestore", "list", "/bin"])
        assert args2.path == "/bin"


# ---------------------------------------------------------------------------
# worktree
# ---------------------------------------------------------------------------


class TestWorktreeParser:
    def test_worktree_subcommands_present(self) -> None:
        parser, _ = _build()
        wt = _top_choices(parser)["worktree"]
        subs = _sub_choices(wt, "worktree_command")
        assert set(subs.keys()) == {"scan", "status"}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class TestConfigParser:
    def test_config_terraform_subcommands(self) -> None:
        parser, _ = _build()
        cfg = _top_choices(parser)["config"]
        cfg_subs = _sub_choices(cfg, "config_command")
        tf = cfg_subs["terraform"]
        tf_subs = _sub_choices(tf, "terraform_command")
        assert set(tf_subs.keys()) == {"get", "set"}

    def test_config_terraform_set_positionals(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["config", "terraform", "set", "region", "us-west-2"])
        assert args.field == "region"
        assert args.value == "us-west-2"


# ---------------------------------------------------------------------------
# mcp / skills
# ---------------------------------------------------------------------------


class TestMcpSkillsSubcommands:
    def test_mcp_subcommands(self) -> None:
        parser, _ = _build()
        mcp = _top_choices(parser)["mcp"]
        subs = _sub_choices(mcp, "mcp_command")
        assert set(subs.keys()) == {"search", "list", "info"}

    def test_skills_subcommands(self) -> None:
        parser, _ = _build()
        skills = _top_choices(parser)["skills"]
        subs = _sub_choices(skills, "skills_command")
        assert set(subs.keys()) == {"search", "list", "install"}


# ---------------------------------------------------------------------------
# preflight / tui / help / collection / deploy-check / core-changes / spec-quality
# ---------------------------------------------------------------------------


class TestMiscSubcommands:
    def test_preflight_func_set(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["preflight"])
        assert args.func is not None

    def test_tui_daemon_url(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["tui"])
        assert args.daemon_url == "http://localhost:8000"

    def test_help_func_set(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["help"])
        assert args.func is not None

    def test_collection_registered(self) -> None:
        parser, _ = _build()
        assert "collection" in _top_choices(parser)

    def test_deploy_check_registered(self) -> None:
        parser, _ = _build()
        assert "deploy-check" in _top_choices(parser)

    def test_core_changes_registered(self) -> None:
        parser, _ = _build()
        assert "core-changes" in _top_choices(parser)

    def test_spec_quality_registered(self) -> None:
        parser, _ = _build()
        assert "spec-quality" in _top_choices(parser)


# ---------------------------------------------------------------------------
# status / list
# ---------------------------------------------------------------------------


class TestStatusList:
    def test_status_todo_id_nargs_optional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["status"])
        assert args.todo_id is None
        args2 = parser.parse_args(["status", "abc-123"])
        assert args2.todo_id == "abc-123"

    def test_list_all_optional(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["list"])
        assert args.queue is None
        assert args.status is None
        assert args.project is None


# ---------------------------------------------------------------------------
# smoke / test smoke arguments
# ---------------------------------------------------------------------------


class TestSmokeArguments:
    def test_smoke_timeout_float(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["test", "smoke", "--timeout", "5.5", "aws", "ec2"])
        assert args.timeout == 5.5

    def test_smoke_max_cost_usd_float(self) -> None:
        parser, _ = _build()
        args = parser.parse_args(["test", "smoke", "--max-cost-usd", "15.75", "aws", "ec2"])
        assert args.max_cost_usd == 15.75

    def test_smoke_engine_choices(self) -> None:
        parser, _ = _build()
        for engine in ("vllm", "llamacpp"):
            args = parser.parse_args(["test", "smoke", "--engine", engine, "aws", "ec2"])
            assert args.engine == engine
