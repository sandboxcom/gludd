# Baseline — 2026-06-10

## Test Suite

| Metric | Count |
|--------|-------|
| Collected | 5,497 |
| Passed | 5,427 |
| Failed | 40 |
| Skipped | 30 |
| Warnings | 27 |
| Duration | 191s |

### 40 Failing Tests

**EventLoop / daemon startup (6):**
- test_tick_with_runner_dispatches_via_runner_not_http
- test_phase_order_completeness
- test_daemon_event_loop_runs_tick
- test_lifespan_creates_event_loop_and_task
- test_lifespan_stops_event_loop_on_shutdown
- test_event_loop_emits_tick_metrics

**AdaptiveRouter (5):**
- test_adaptive_router_fallback_uses_todo_defaults
- test_resolve_adaptive_prompt_unknown_work_type_defaults_feature
- test_route_no_repo_falls_back
- test_route_insufficient_data_falls_back
- test_route_min_samples_filters

**Model health (1):**
- test_all_unhealthy_returns_fallback

**TUI extracted builders (3):**
- test_calls_scanner_with_valid_paths
- test_no_valid_paths
- test_returns_nav_dict

**Guardrail self-tests (2):**
- test_make_test_passes
- test_make_lint_passes

**Secrets/OpenBao (7):**
- test_build_secrets_resolver_openbao_external
- test_openbao_connect_external
- test_connect_with_local_bootstrap_result
- test_start_local_container_success
- test_start_local_container_failure
- test_start_local_container_without_resolver_uses_default
- test_migrate_called_when_openbao_configured

**Runtime relative paths (4):**
- test_relative_container_path_rejected
- test_runtime_validator_container_relative_path
- test_data_source_mount_relative_container_path
- test_validate_profile_relative_container_path

**Bootstrap/filestore (3):**
- test_default_store_created
- test_filestore_creation
- test_filestore_exception

**CLI (2):**
- test_cli_module_has_no_top_level_daemon_imports
- test_daemon_calls_create_app_and_popen

**AddTodo (1):**
- test_add_todo_rejects_invalid_queue

**Worktree (1):**
- test_worktree_status_with_monitor

**Ansible endpoints (3):**
- test_ansible_search
- test_ansible_builtins
- test_ansible_search_empty_query

**Other (2):**
- test_local_inference_start
- test_pid_phase_handles_exception_gracefully

## Lint

| Metric | Count |
|--------|-------|
| Errors | 19 (18 fixable) |
| File | tests/unit/test_compute_launch_and_remote_slurm.py (18 issues), src/general_ludd/routers/compute.py (1 issue) |

## Typecheck (mypy strict)

| Metric | Count |
|--------|-------|
| Errors | 25 in 5 files |

**By file:**
- otel_bridge.py: 5 errors (missing opentelemetry stubs)
- planning/repo_map.py: 1 error (untyped def)
- db/session.py: 1 error (Any return)
- routers/__init__.py: 1 error (missing type arg)
- cli.py: 17 errors (build_parser return type, _make_table variance)

## Healthcheck

Worker app factory: OK
Event loop import: OK
