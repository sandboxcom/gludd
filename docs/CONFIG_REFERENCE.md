# Configuration Reference (WP-F1)

**Project:** general-ludd-agent
**Version:** 0.1.0-beta.1 (`src/general_ludd/__init__.py`)
**Audience:** weaker-model AI executors and human operators. This doc is
self-sufficient — no other file is required to understand how to configure and
run the daemon end-to-end.

> This is the canonical single-source reference. `docs/quickstart.md` (fast path)
> and `docs/configuration.md` (narrative) cover the same ground from other
> angles; when they disagree, **this file is authoritative**.

---

## TL;DR — minimal path (3 commands)

```bash
make init                          # 1. install deps (uv sync) + create dirs
export ZAI_API_KEY=sk-...          # 2. configure ONE model provider
export GLUDD_CONFIG_DIR="$PWD/config"  # 3. REQUIRED from a repo checkout — see §2.0
gludd daemon                       # 4. start server (127.0.0.1:8000)
# in another shell:
gludd models router-status         # MUST list a profile — empty means step 3 was skipped
gludd add "Write a hello-world test" --work-type code
gludd status                       # watch the todo move to completed
```

That is the whole product spine. Everything below is detail.

> **Do not skip step 3.** The repo's `config/` directory is not on the config
> discovery path. Without `GLUDD_CONFIG_DIR`, no model profiles load and every
> agent silently "completes" with empty output. See §2.0.

---

## 1. Environment Variables

Env vars are the **highest-priority** config layer (override all YAML files).
All are optional unless marked **required**. Defaults are read from
`os.environ.get(...)` at the call sites cited.

### 1.1 `GLUDD_*` — daemon / runtime

| Name | Purpose | Default | Required | Source |
|---|---|---|---|---|
| `GLUDD_AUTH_PSK` | Pre-shared key (Bearer token) for daemon↔CLI/worker auth. Auto-generated and printed when binding a non-loopback interface. | `""` (auth disabled) | optional¹ | `daemon.py:2369`, `cli.py:1151` |
| `GLUDD_REQUIRE_AUTH` | Force auth on. Truthy values: `1`,`true`,`yes`,`on`. When set without `GLUDD_AUTH_PSK`, worker surface fails CLOSED (503). | `""` | optional | `daemon.py:2378` |
| `GLUDD_ALLOW_NO_AUTH` | Explicitly bypass auth (dev only). Truthy set as above. | `""` | optional | `daemon.py:2375` |
| `GLUDD_CONFIG_DIR` | Override the config directory. **Set this when running from a repo checkout** — the repo's own `config/` tree is NOT on the discovery path. See §2.0. | builtin | optional | `daemon.py:2313` |
| `GLUDD_TEMPLATES_DIR` | Override prompt-templates directory. | builtin | optional | `daemon.py:2315` |
| `GLUDD_PLAYBOOKS_DIR` | Override ansible playbooks directory. | builtin | optional | `daemon.py:2317` |
| `GLUDD_TICK_INTERVAL` | Event-loop tick interval in seconds. | `1.0` | optional | `daemon.py:2307` |
| `GLUDD_LOG_LEVEL` | Daemon log level: `debug`\|`info`\|`warning`\|`error`. | `info` | optional | `daemon.py:2309` |
| `GLUDD_WRITER_MODE` | DB writer path. **`inline` is the only working mode — do NOT set `subprocess`** (see §5, Experimental flags). | `inline` | optional | `daemon.py:898` |
| `GLUDD_DB_PATH` | SQLite database file path. | `$XDG_DATA_HOME/general-ludd/general-ludd.db` (→ `~/.local/share/general-ludd/general-ludd.db`) | optional | `db/session.py:25` |
| `GLUDD_DAEMON_URL` | Base URL the CLI / renderer use to reach the daemon. | `http://localhost:8000` | optional | `renderers/runner.py:230` |
| `GLUDD_WORKER_ID` | Worker identifier (multi-worker disambiguation; clamped to 1 on SQLite). | `worker` | optional | `worker/app.py:306` |
| `GLUDD_JOB_TIMEOUT_MAX` | Maximum job wall-clock seconds a worker will accept. | `600` | optional | `worker/app.py:465` |
| `GLUDD_PLAYBOOK_TIMEOUT` | Per-playbook ansible-runner timeout seconds. | none (runner default) | optional | `ansible/core_runner.py:64` |
| `GLUDD_RENDER_MAX_BYTES` | Cap on rendered prompt/output byte count. | builtin cap | optional | `renderers/runner.py:116` |
| `GLUDD_WORKER_ALLOWLIST` | Comma-separated worker IDs allowed to receive broadcasts. | all | optional | `reload/worker_broadcast.py:76` |
| `GLUDD_PERMITTED_MOUNTS` | Comma-separated OpenBao mount paths the secrets manager may read. | `secret,kv` | optional | `secrets/manager.py:25` |
| `GLUDD_PAUSE_DIR` | Override the pause-store directory. | builtin | optional | `controllers/pause_store.py:64` |
| `GLUDD_HIBERNATION_DIR` | Override the agent hibernation-store directory. | builtin | optional | `agents/hibernation.py:64` |
| `GLUDD_BACKUP_DIR` | Override the account-backup destination directory. | system temp | optional | `account/backup.py:324` |
| `GLUDD_PROJECT_DIR` | Override the active project working directory. | builtin | optional | `config/project_dir.py:31` |
| `GLUDD_PROJECT_ROOT` | Trusted explicit root for MCP builtin execution and enforcement-ledger discovery. It must name an existing directory; when unset or invalid, enforcement searches only `cwd` and its ancestors, then stays at `cwd`. | builtin | optional | `mcp/builtins.py:127`, `.opencode/lib/shared.ts:490` |
| `GLUDD_WORKSPACE_ROOT` | Workspace root (issue sources, model router, integrity router). | `""` | optional | `issue_sources/csv_excel.py:91` |
| `GLUDD_REPO_ROOT` | Repo root for maintenance router operations. | `.` | optional | `routers/maintenance.py:27` |
| `GLUDD_SELF_REPO_URL` | Override the git URL used for self-update. | builtin | optional | `projects/manager.py:70` |
| `GLUDD_SELF_UPDATE_APPROVAL_SECRET` | Secret required to approve a self-update. | `""` | optional | `self_update/apply.py:216` |
| `GLUDD_MCP_ALLOW_ANY_EXEC` | Allow MCP servers to exec arbitrary commands (UNSAFE). Truthy set. | `""` (off) | optional | `mcp/transport.py:117` |
| `GLUDD_WEB_FETCH_ALLOWED_DOMAINS` | Comma-separated domains the web-fetch tool may reach. | `""` (none) | optional | `retrieval/web.py:55` |
| `GLUDD_TERRAFORM_STACKS_DIR` | Directory holding terraform stack definitions. | builtin | optional | `daemon.py:1020` |

¹ `GLUDD_AUTH_PSK` becomes **required** the moment you bind the daemon to a
non-loopback interface (`--host` not `127.0.0.1`/`localhost`/`::1`): the CLI
auto-generates a 32-byte token, prints it once, and all clients must send
`Authorization: Bearer <psk>`.


### 1.1a Complete `GLUDD_*` index (machine-generated)

Rows below are generated from the live source scan so every env var read
in `src/` and `scripts/` is represented. The table above keeps the
hand-authored entries for the core runtime variables.

| Name | Purpose | Default | Required | Source |
|---|---|---|---|---|
| `GLUDD_ACTIVE_WORKSTREAM_REGISTRY` | Override the shared active-workstream registry path. See §1.1b. | `$TMPDIR/gludd-active-workstreams/<git-common-dir-hash>.json` | optional | `scripts/workstream_registry.py:28` |
| `GLUDD_ADAPTIVE_HEARTBEAT_SECS` | Auto-indexed (see source) | — | optional | `scripts/adaptive_test.py:240` |
| `GLUDD_ADAPTIVE_NO_PROGRESS_SECS` | Auto-indexed (see source) | — | optional | `scripts/adaptive_test.py:254` |
| `GLUDD_ADAPTIVE_PROGRESS_FILE` | Auto-indexed (see source) | — | optional | `scripts/adaptive_test.py:268` |
| `GLUDD_ADMIN_TOKEN` | Auto-indexed (see source) | — | optional | `src/general_ludd/routers/signing.py:48` |
| `GLUDD_AGENT_LOG` | Auto-indexed (see source) | `/tmp/gludd-agent-results.jsonl` | optional | `scripts/agent_activity_report.py:8` |
| `GLUDD_AGENT_OWNER_PID` | Auto-indexed (see source) | — | optional | `scripts/reap_orphan_pytest.py:135` |
| `GLUDD_AGENT_RESULTS_FILE` | Auto-indexed (see source) | — | optional | `scripts/log_agent_result.py:22` |
| `GLUDD_AGENT_RESULTS_MAX_MB` | Auto-indexed (see source) | `10` | optional | `scripts/log_agent_result.py:25` |
| `GLUDD_ALIVE_PATH` | Auto-indexed (see source) | `/tmp/gludd-plugin-alive.json` | optional | `scripts/check_plugin_health.py:34` |
| `GLUDD_ALLOWED_SIGNERS` | Auto-indexed (see source) | — | optional | `src/general_ludd/runtime/manifest_signer.py:44` |
| `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS` | Auto-indexed (see source) | — | optional | `src/general_ludd/models/gateway.py:1461` |
| `GLUDD_ANTHROPIC_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:295` |
| `GLUDD_AUDIT_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:108` |
| `GLUDD_BENCH_MAX_TOKENS` | Auto-indexed (see source) | `32` | optional | `scripts/benchmark_local_model.py:13` |
| `GLUDD_BENCH_MODEL_DIR` | Auto-indexed (see source) | `/tmp/gludd-qwen-e2e-model` | optional | `scripts/benchmark_local_model.py:11` |
| `GLUDD_BENCH_N` | Auto-indexed (see source) | `10` | optional | `scripts/benchmark_local_model.py:12` |
| `GLUDD_BENCH_PROMPT` | Auto-indexed (see source) | `def fibonacci(n):` | optional | `scripts/benchmark_local_model.py:14` |
| `GLUDD_BINARY_SHA256` | Auto-indexed (see source) | — | optional | `src/general_ludd/filestore/bootstrap.py:45` |
| `GLUDD_BLOCK_COUNTER_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:41` |
| `GLUDD_BLOCK_REASON_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:42` |
| `GLUDD_BUDGET_FAIL_CLOSED_DEGRADED` | Auto-indexed (see source) | — | optional | `src/general_ludd/routers/models.py:692` |
| `GLUDD_CI_CACHE_PATH` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:30` |
| `GLUDD_CI_HISTORY_FILE` | CI verdict history JSON (atomic writes; consulted by the AA032 push guard and ci-verdict-safe recording) | `/tmp/gludd-ci-verdict-history.json` | optional | `scripts/ci_check_cooldown.py:49` |
| `GLUDD_CI_RESTART_COUNT_FILE` | AA023 CI-restart cap counter; reset to `0` once CI reports a terminal GREEN/RED verdict for the pushed SHA | `/tmp/gludd-ci-restart-count` | optional | `scripts/ci_check_cooldown.py:50` |
| `GLUDD_CI_STATE_FILE` | Auto-indexed (see source) | `/tmp/gludd-ci-check-state.json` | optional | `src/general_ludd/git_automation/ci_ops.py:40` |
| `GLUDD_CLAUDE_SESSIONS_BASE` | Auto-indexed (see source) | — | optional | `scripts/agent_liveness.py:139` |
| `GLUDD_CLEAN_TREE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:420` |
| `GLUDD_COLLECTION_LOCK` | Auto-indexed (see source) | — | optional | `scripts/collection_lock.py:33` |
| `GLUDD_COLLECTION_LOCK_TIMEOUT` | Auto-indexed (see source) | — | optional | `scripts/collection_lock.py:53` |
| `GLUDD_COMMIT_LOCK_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3729` |
| `GLUDD_COMMIT_LOCK_PATH` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3632` |
| `GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:1581` |
| `GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:1582` |
| `GLUDD_CONTAINER_RUNTIME` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:130` |
| `GLUDD_CONTEXT_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3314` |
| `GLUDD_CONTINUE_DIRECTIVE` | Auto-indexed (see source) | `/tmp/gludd-continue-directive.json` | optional | `scripts/agent_watchdog.py:340` |
| `GLUDD_COVERAGE_AUDIT` | Auto-indexed (see source) | — | optional | `scripts/audit_coverage.py:292` |
| `GLUDD_COVERAGE_AUDIT_TIMEOUT_SECONDS` | Auto-indexed (see source) | `1800` | optional | `scripts/audit_coverage.py:154` |
| `GLUDD_DAEMON_PORT` | Auto-indexed (see source) | — | optional | `scripts/smoke_daemon.py:113` |
| `GLUDD_DATA_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/ornith/sandbox.py:41` |
| `GLUDD_DB_DISK_PRESSURE_THRESHOLD` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/db_telemetry.py:100` |
| `GLUDD_DEEPINFRA_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:331` |
| `GLUDD_DEEPSEEK_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:367` |
| `GLUDD_DELETION_GATE_THRESHOLD` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:2789` |
| `GLUDD_DIRECTIVE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3359` |
| `GLUDD_DISENGAGE_AUDIT_PATH` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:58` |
| `GLUDD_DISENGAGE_PATH` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:57` |
| `GLUDD_DISK_FREE_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/test_worktree_disk_guard.py:49` |
| `GLUDD_DISPATCH_FLOOR` | Auto-indexed (see source) | `10` | optional | `scripts/dispatch_tracker.py:30` |
| `GLUDD_DISPATCH_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/dispatch_tracker.py:28` |
| `GLUDD_E2E_ACTIVE` | Auto-indexed (see source) | — | optional | `src/general_ludd/daemon.py:3454` |
| `GLUDD_E2E_MAX_SPEND_USD` | Auto-indexed (see source) | — | optional | `src/general_ludd/cloud/azure_game_runtime.py:196` |
| `GLUDD_ENHANCEMENT_RATIO_BLOCK` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:722` |
| `GLUDD_ENHANCEMENT_RATIO_ENFORCE` | Auto-indexed (see source) | `1` | optional | `scripts/audit_observability.py:860` |
| `GLUDD_ENHANCEMENT_RATIO_STATE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:53` |
| `GLUDD_ENVELOPE_KEK_B64` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/envelope_encryption.py:378` |
| `GLUDD_FALSE_DONE_BLOCKS` | Auto-indexed (see source) | `/tmp/gludd-false-done-blocks.json` | optional | `scripts/agent_watchdog.py:338` |
| `GLUDD_FALSE_DONE_BLOCKS_FILE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:176` |
| `GLUDD_FALSE_DONE_MAXOUT` | Auto-indexed (see source) | `/tmp/gludd-false-done-maxout.json` | optional | `scripts/agent_watchdog.py:339` |
| `GLUDD_FALSE_DONE_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:54` |
| `GLUDD_FIREWORKS_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:349` |
| `GLUDD_FLOOR_ENFORCE` | Auto-indexed (see source) | `1` | optional | `scripts/audit_observability.py:859` |
| `GLUDD_FLOOR_TEXT_COMPLETE_COUNT` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:40` |
| `GLUDD_FLOOR_V2_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3374` |
| `GLUDD_FORCE_DELEGATE` | Auto-indexed (see source) | — | optional | `scripts/test_force_delegate_hook.py:17` |
| `GLUDD_FORCE_DELEGATE_GRACE` | Auto-indexed (see source) | — | optional | `scripts/test_force_delegate_hook.py:20` |
| `GLUDD_FORCE_DELEGATE_MAXBLOCK` | Auto-indexed (see source) | — | optional | `scripts/test_force_delegate_hook.py:21` |
| `GLUDD_FORCE_DELEGATE_STATE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:34` |
| `GLUDD_FORCE_DISPATCH_PATH` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:44` |
| `GLUDD_FORCE_PUSH` | Auto-indexed (see source) | — | optional | `scripts/audit_observability.py:865` |
| `GLUDD_FORCE_PUSH_MAX_BYPASS` | Auto-indexed (see source) | `5` | optional | `scripts/push_rate_guard.py:20` |
| `GLUDD_FORCE_PUSH_TRACK_FILE` | Auto-indexed (see source) | — | optional | `scripts/push_rate_guard.py:25` |
| `GLUDD_FORCE_PUSH_WINDOW_HOURS` | Auto-indexed (see source) | `12` | optional | `scripts/push_rate_guard.py:21` |
| `GLUDD_GAME_GEN_MODEL` | Auto-indexed (see source) | — | optional | `scripts/run_game_gen_1_5b.py:12` |
| `GLUDD_GATE_BASETEMP` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:179` |
| `GLUDD_GATE_FRESHNESS_SECS` | Auto-indexed (see source) | — | optional | `scripts/gate_status_attestation.py:313` |
| `GLUDD_GATE_KEY_PATH` | Auto-indexed (see source) | — | optional | `scripts/gate_status_attestation.py:306` |
| `GLUDD_GATE_REFRESH_LOCK_TIMEOUT` | Auto-indexed (see source) | — | optional | `scripts/collection_lock.py:56` |
| `GLUDD_GGUF_MODEL_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/cloud/model_sources.py:27` |
| `GLUDD_GHA_SIGNAL_STATE_DIR` | Auto-indexed (see source) | — | optional | `scripts/ci_signal_exact_sha.py:468` |
| `GLUDD_GOOGLE_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:304` |
| `GLUDD_GROQ_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:358` |
| `GLUDD_GUARD_AHEAD_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/check_green_branch_guard.py:88` |
| `GLUDD_GUARD_CI_VERDICT_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/check_green_branch_guard.py:59` |
| `GLUDD_GUARD_HEAD_SHA_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/check_green_branch_guard.py:76` |
| `GLUDD_GUARD_REMOTE_SHA_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/check_green_branch_guard.py:41` |
| `GLUDD_HEALTH_WARN_STALE` | Auto-indexed (see source) | — | optional | `scripts/check_plugin_health.py:333` |
| `GLUDD_HEARTBEAT_DIR` | Auto-indexed (see source) | `/tmp` | optional | `scripts/verify_plugin_liveness.py:41` |
| `GLUDD_HEARTBEAT_STALE_SECS` | Auto-indexed (see source) | `60` | optional | `scripts/verify_plugin_liveness.py:40` |
| `GLUDD_HF_DOWNLOAD_TIMEOUT` | Auto-indexed (see source) | `30` | optional | `src/general_ludd/small_models/download.py:26` |
| `GLUDD_HOT_MODULE_PREFIX` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:180` |
| `GLUDD_HOT_OUT_DIR` | Auto-indexed (see source) | `/tmp` | optional | `scripts/check_hot_reload_fresh.py:33` |
| `GLUDD_INGEST_TOKEN` | Auto-indexed (see source) | — | optional | `src/general_ludd/receiver/router.py:110` |
| `GLUDD_INGEST_URL` | Auto-indexed (see source) | — | optional | `scripts/provider_smoke_harness.py:145` |
| `GLUDD_INTEGRITY_KEY` | Auto-indexed (see source) | — | optional | `scripts/troubleshoot.py:35` |
| `GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:41` |
| `GLUDD_JOB_INGRESS_MAX_DEPTH` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:40` |
| `GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:43` |
| `GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:44` |
| `GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:45` |
| `GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES` | Auto-indexed (see source) | — | optional | `src/general_ludd/schemas/job.py:42` |
| `GLUDD_KNOWN_MODELS_FILE` | Auto-indexed (see source) | — | optional | `src/general_ludd/small_models/model_hash_db.py:145` |
| `GLUDD_LAST_TEST_RESULT_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:46` |
| `GLUDD_LIBRETRANSLATE_URL` | Auto-indexed (see source) | `http://localhost:5000` | optional | `src/general_ludd/language/translation.py:203` |
| `GLUDD_LIVENESS_CACHE_FILE` | Auto-indexed (see source) | — | optional | `scripts/agent_liveness.py:513` |
| `GLUDD_LIVENESS_CACHE_TTL` | Auto-indexed (see source) | `3` | optional | `scripts/agent_liveness.py:495` |
| `GLUDD_LIVENESS_MAX_AGE` | Auto-indexed (see source) | — | optional | `scripts/check_plugin_health.py:45` |
| `GLUDD_LIVENESS_WINDOW_SEC` | Auto-indexed (see source) | `300.0` | optional | `scripts/agent_liveness.py:93` |
| `GLUDD_LIVE_AGENTS_COUNT` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:802` |
| `GLUDD_MAINTHREAD_STREAK_ENFORCE` | Auto-indexed (see source) | `1` | optional | `scripts/check_enforcement_floor.py:35` |
| `GLUDD_MAINTHREAD_STREAK_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:33` |
| `GLUDD_MAIN_MODEL` | Auto-indexed (see source) | — | optional | `scripts/test_model_ratio_hook.py:53` |
| `GLUDD_MAIN_MODEL_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:38` |
| `GLUDD_MAKE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3169` |
| `GLUDD_MAX_DEPTH` | Auto-indexed (see source) | — | optional | `scripts/check_depth_limit.py:28` |
| `GLUDD_MCP_STDERR_LINE_BYTES` | Auto-indexed (see source) | — | optional | `src/general_ludd/mcp/transport.py:483` |
| `GLUDD_MCP_STDERR_MAX_BYTES` | Auto-indexed (see source) | — | optional | `src/general_ludd/mcp/transport.py:489` |
| `GLUDD_MCP_STDERR_MAX_LINES` | Auto-indexed (see source) | — | optional | `src/general_ludd/mcp/transport.py:495` |
| `GLUDD_MCP_STDERR_TAIL_BYTES` | Auto-indexed (see source) | — | optional | `src/general_ludd/mcp/transport.py:471` |
| `GLUDD_MCP_STDERR_TAIL_LINES` | Auto-indexed (see source) | — | optional | `src/general_ludd/mcp/transport.py:477` |
| `GLUDD_MIN_DISPATCHES` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:1385` |
| `GLUDD_MIN_FREE_GB` | Auto-indexed (see source) | — | optional | `scripts/test_worktree_disk_guard.py:72` |
| `GLUDD_MIN_PLATFORMS` | Auto-indexed (see source) | `4` | optional | `scripts/check_multiplatform_consistency.py:103` |
| `GLUDD_MISTRAL_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:322` |
| `GLUDD_MODELS_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/health/local_model_check.py:13` |
| `GLUDD_MODEL_COMPARE_DIR` | Auto-indexed (see source) | `/tmp/gludd-model-compare` | optional | `scripts/compare_models.py:17` |
| `GLUDD_MODEL_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/small_models/download.py:23` |
| `GLUDD_MODEL_HEALTH_URL` | Auto-indexed (see source) | — | optional | `src/general_ludd/cloud/model_sources.py:22` |
| `GLUDD_MODEL_INDEX_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/infra/model_search.py:20` |
| `GLUDD_MODEL_SOURCE_RETRIES` | Auto-indexed (see source) | `1` | optional | `src/general_ludd/cloud/model_sources.py:20` |
| `GLUDD_MODEL_SOURCE_TIMEOUT` | Auto-indexed (see source) | `30` | optional | `src/general_ludd/cloud/model_sources.py:19` |
| `GLUDD_MODEL_UTIL_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_model_ratio_hook.py:56` |
| `GLUDD_MODEL_UTIL_STATE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:35` |
| `GLUDD_MODEL_UTIL_WINDOW` | Auto-indexed (see source) | — | optional | `scripts/test_model_ratio_hook.py:48` |
| `GLUDD_MT_BACKLOG` | Auto-indexed (see source) | — | optional | `scripts/multitasking_backlog_check.py:83` |
| `GLUDD_MULTITASK_FLOOR_ENFORCE` | Auto-indexed (see source) | `1` | optional | `scripts/check_enforcement_floor.py:32` |
| `GLUDD_MULTITASK_MAX_DISPATCHES` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:1538` |
| `GLUDD_MULTITASK_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:47` |
| `GLUDD_NO_CI_POLL_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3389` |
| `GLUDD_NO_WAIT_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:2676` |
| `GLUDD_OBJECTIVE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3419` |
| `GLUDD_OPENAI_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:286` |
| `GLUDD_OPENROUTER_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:385` |
| `GLUDD_ORPHAN_PYTEST_GRACE_SECONDS` | Auto-indexed (see source) | `1` | optional | `scripts/reap_orphan_pytest.py:297` |
| `GLUDD_ORPHAN_PYTEST_MIN_SECONDS` | Auto-indexed (see source) | `1800` | optional | `scripts/reap_orphan_pytest.py:317` |
| `GLUDD_OUTPUT_TEMPLATES_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/output_templates.py:20` |
| `GLUDD_PERSIST_STOP_BLOCK_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:43` |
| `GLUDD_PER_WORKER_GB` | Auto-indexed (see source) | `1.5` | optional | `scripts/adaptive_test.py:98` |
| `GLUDD_PG_WAKE_RECONNECT_MAX_SECONDS` | Auto-indexed (see source) | `5.0` | optional | `src/general_ludd/daemon.py:2286` |
| `GLUDD_PG_WAKE_RECONNECT_SECONDS` | Auto-indexed (see source) | `0.1` | optional | `src/general_ludd/daemon.py:2285` |
| `GLUDD_PLUGIN_DIR` | Auto-indexed (see source) | `.opencode/plugin` | optional | `scripts/check_hot_reload_fresh.py:29` |
| `GLUDD_PLUGIN_DISENGAGE_DURATION` | Auto-indexed (see source) | `3600` | optional | `scripts/check_plugin_hashes.py:32` |
| `GLUDD_PLUGIN_LOADED_LOG` | Auto-indexed (see source) | `/tmp/gludd-plugin-loaded.log` | optional | `scripts/verify_plugin_liveness.py:42` |
| `GLUDD_PLUGIN_MANIFEST` | Auto-indexed (see source) | `.opencode/plugin-hashes.json` | optional | `scripts/check_plugin_hashes.py:27` |
| `GLUDD_POST_RESULTS_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:48` |
| `GLUDD_PROJECT_ALLOW_ANY_EXEC` | Auto-indexed (see source) | — | optional | `src/general_ludd/project_runner/dast.py:42` |
| `GLUDD_PROJECT_NAMESPACE` | Auto-indexed (see source) | `gludd` | optional | `src/general_ludd/cli.py:1997` |
| `GLUDD_PSK_DISABLE` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/auth.py:67` |
| `GLUDD_PSK_IDENTITY_TTL_SECONDS` | Auto-indexed (see source) | `3600` | optional | `src/general_ludd/security/psk_rotation.py:255` |
| `GLUDD_PSK_ROTATION_OVERLAP_SECONDS` | Auto-indexed (see source) | `300` | optional | `src/general_ludd/security/psk_rotation.py:254` |
| `GLUDD_READ_GRIND_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:36` |
| `GLUDD_RELEASE_CHECK_COOLDOWN_SEC` | Auto-indexed (see source) | `600` | optional | `scripts/check_release_completeness_guard.py:31` |
| `GLUDD_RELEASE_COMPLETENESS_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:45` |
| `GLUDD_RELEASE_DEADLINE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3435` |
| `GLUDD_RESOURCE_NAMESPACE` | Auto-indexed (see source) | — | optional | `scripts/audit_coverage.py:315` |
| `GLUDD_RESOURCE_ROOT` | Auto-indexed (see source) | — | optional | `scripts/resource_arbiter.py:64` |
| `GLUDD_RUNTIME_TEST_STATE_DIR` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:78` |
| `GLUDD_S3_QWEN_05B_URL` | Auto-indexed (see source) | — | optional | `src/general_ludd/cloud/model_sources.py:65` |
| `GLUDD_S3_SMOLLM2_135M_URL` | Auto-indexed (see source) | — | optional | `src/general_ludd/cloud/model_sources.py:84` |
| `GLUDD_SANDBOX_NO_NETWORK` | Auto-indexed (see source) | — | optional | `src/general_ludd/sandbox/enforcer.py:244` |
| `GLUDD_SANDBOX_STATE_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/sandboxes/state.py:24` |
| `GLUDD_SEARXNG_URL` | Auto-indexed (see source) | `http://localhost:8080` | optional | `src/general_ludd/retrieval/searx_client.py:21` |
| `GLUDD_SEARX_CACHE_TTL` | Auto-indexed (see source) | `1800` | optional | `src/general_ludd/retrieval/searx_client.py:19` |
| `GLUDD_SEARX_DISCOVER_TTL` | Auto-indexed (see source) | `3600` | optional | `src/general_ludd/models/searx_discoverer.py:21` |
| `GLUDD_SEARX_DOCKER_URL` | Auto-indexed (see source) | `http://localhost:8080` | optional | `src/general_ludd/infra/model_search.py:18` |
| `GLUDD_SEARX_PORT` | Auto-indexed (see source) | — | optional | `src/general_ludd/searx/config.py:54` |
| `GLUDD_SEARX_RATE_LIMIT` | Auto-indexed (see source) | `2.0` | optional | `src/general_ludd/retrieval/searx_client.py:20` |
| `GLUDD_SEARX_TIMEOUT` | Auto-indexed (see source) | `30.0` | optional | `src/general_ludd/retrieval/searx_client.py:22` |
| `GLUDD_SEARX_URL` | Auto-indexed (see source) | `http://localhost:8888` | optional | `src/general_ludd/cli_service_commands.py:31` |
| `GLUDD_SELF_UPDATE_PUBLIC_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/self_update/signing.py:92` |
| `GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE` | Auto-indexed (see source) | — | optional | `src/general_ludd/self_update/signing.py:96` |
| `GLUDD_SESSION_ABSOLUTE_TTL` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/session_ttl.py:82` |
| `GLUDD_SESSION_ID` | Auto-indexed (see source) | — | optional | `scripts/agent_liveness.py:181` |
| `GLUDD_SESSION_IDLE_TTL` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/session_ttl.py:83` |
| `GLUDD_SESSION_START_ENFORCE` | Auto-indexed (see source) | `1` | optional | `scripts/audit_observability.py:861` |
| `GLUDD_SESSION_START_FILE` | Auto-indexed (see source) | `/tmp/gludd-session-start.json` | optional | `scripts/check_plugin_restart_needed.py:30` |
| `GLUDD_SESSION_START_MIN_DISPATCHES` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3043` |
| `GLUDD_SESSION_STATE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:32` |
| `GLUDD_SHARD_NAME` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:183` |
| `GLUDD_SHARD_STATE_DIR` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:184` |
| `GLUDD_SHARD_SUMMARY_DIR` | Auto-indexed (see source) | `.gate-logs/ci-shards` | optional | `scripts/run_ci_shards_parallel.py:216` |
| `GLUDD_SIGNING_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/runtime/manifest_signer.py:41` |
| `GLUDD_SMOKE_ALLOW_CPU` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:107` |
| `GLUDD_SMOKE_BACKEND` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:94` |
| `GLUDD_SMOKE_BATCH_SIZE` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:102` |
| `GLUDD_SMOKE_HEADROOM` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:106` |
| `GLUDD_SMOKE_HIDDEN_SIZE` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:97` |
| `GLUDD_SMOKE_LOCAL_MODEL` | Auto-indexed (see source) | — | optional | `src/general_ludd/smoke.py:729` |
| `GLUDD_SMOKE_LOG` | Auto-indexed (see source) | `/tmp/gludd-smoke.log` | optional | `scripts/smoke_daemon.py:21` |
| `GLUDD_SMOKE_MAX_MEMORY_GB` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:104` |
| `GLUDD_SMOKE_MODEL_PARAMS` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:105` |
| `GLUDD_SMOKE_SPARSITY` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:75` |
| `GLUDD_SMOKE_STEPS` | Auto-indexed (see source) | — | optional | `scripts/mac_unified_memory_smoke.py:103` |
| `GLUDD_SONNET_TARGET_CONFIG` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:37` |
| `GLUDD_SONNET_TARGET_SHARE` | Auto-indexed (see source) | — | optional | `scripts/test_model_ratio_hook.py:55` |
| `GLUDD_STALLED_TASKS` | Auto-indexed (see source) | `/tmp/gludd-stalled-tasks.txt` | optional | `scripts/agent_watchdog.py:1932` |
| `GLUDD_STALLED_TASKS_FILE` | Auto-indexed (see source) | `/tmp/gludd-stalled-tasks.txt` | optional | `scripts/agent_watchdog.py:341` |
| `GLUDD_STATE_DIR` | Auto-indexed (see source) | — | optional | `src/general_ludd/security/state.py:22` |
| `GLUDD_STOP_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:1920` |
| `GLUDD_STOP_STATE` | Auto-indexed (see source) | `/tmp/gludd-stop-state.json` | optional | `scripts/agent_watchdog.py:337` |
| `GLUDD_STOP_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:27` |
| `GLUDD_STOP_STATE_PATH` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:50` |
| `GLUDD_STOP_TEXT_COMPLETE_COUNT` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:39` |
| `GLUDD_STOP_TOOL_COUNTS_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:51` |
| `GLUDD_STREAK_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:28` |
| `GLUDD_STS_ROLE_ID` | Auto-indexed (see source) | — | optional | `src/general_ludd/daemon.py:2448` |
| `GLUDD_STS_SECRET_ID` | Auto-indexed (see source) | — | optional | `src/general_ludd/daemon.py:2449` |
| `GLUDD_STS_TOKEN_ID` | Auto-indexed (see source) | — | optional | `src/general_ludd/sts/injector.py:89` |
| `GLUDD_TASKS_DIR` | Auto-indexed (see source) | `/tmp/gludd-tasks` | optional | `scripts/agent_liveness.py:175` |
| `GLUDD_TASKS_MD` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:287` |
| `GLUDD_TASK_ANOMALIES` | Auto-indexed (see source) | `/tmp/gludd-task-anomalies.json` | optional | `scripts/agent_watchdog.py:1931` |
| `GLUDD_TASK_DEADLINES_FILE` | Auto-indexed (see source) | `/tmp/gludd-task-deadlines.json` | optional | `scripts/agent_watchdog.py:342` |
| `GLUDD_TASK_DEADLINE_BLOCK` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:940` |
| `GLUDD_TASK_DEADLINE_ENABLED` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:916` |
| `GLUDD_TASK_DEADLINE_STATE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:29` |
| `GLUDD_TASK_DEADLINE_WARNINGS` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:30` |
| `GLUDD_TASK_EMBEDDINGS_PROVIDER` | Auto-indexed (see source) | — | optional | `src/general_ludd/scoring/task_embeddings.py:118` |
| `GLUDD_TASK_KILLED_FILE` | Auto-indexed (see source) | `/tmp/gludd-task-killed.json` | optional | `scripts/task_watchdog.py:62` |
| `GLUDD_TASK_STALE_FILE` | Auto-indexed (see source) | `/tmp/gludd-task-stale.json` | optional | `scripts/run_ci_shards_parallel.py:31` |
| `GLUDD_TASK_TIMEOUT_MS` | Auto-indexed (see source) | — | optional | `scripts/audit_observability.py:858` |
| `GLUDD_TASK_TRACKING_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3451` |
| `GLUDD_TASK_WATCHDOG_LOG` | Auto-indexed (see source) | — | optional | `scripts/task_watchdog.py:67` |
| `GLUDD_TASK_WATCHDOG_PID` | Auto-indexed (see source) | — | optional | `scripts/task_watchdog.py:64` |
| `GLUDD_TASK_WATCHDOG_POLL` | Auto-indexed (see source) | `5` | optional | `scripts/task_watchdog.py:72` |
| `GLUDD_TEXT_ONLY_STATE_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:49` |
| `GLUDD_TMP_DIR` | Auto-indexed (see source) | — | optional | `scripts/cleanup_stale_tmp.py:46` |
| `GLUDD_TODOWRITE_STATE` | Auto-indexed (see source) | — | optional | `scripts/agent_watchdog.py:303` |
| `GLUDD_TODOWRITE_STATE_PATH` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:59` |
| `GLUDD_TODO_GUARD_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/troubleshoot.py:27` |
| `GLUDD_TOGETHER_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:340` |
| `GLUDD_TOKEN_BUDGET_5H` | Auto-indexed (see source) | `316000000` | optional | `scripts/token_window_monitor.py:62` |
| `GLUDD_TOKEN_MONITOR_INTERVAL` | Auto-indexed (see source) | `60` | optional | `scripts/token_window_monitor.py:361` |
| `GLUDD_TOKEN_MONITOR_NORMAL_FLOOR` | Auto-indexed (see source) | `7` | optional | `scripts/token_window_monitor.py:67` |
| `GLUDD_TRANSCRIPT_DIR` | Auto-indexed (see source) | — | optional | `scripts/token_window_monitor.py:42` |
| `GLUDD_VENV_COUNT_OVERRIDE` | Auto-indexed (see source) | — | optional | `scripts/test_worktree_disk_guard.py:50` |
| `GLUDD_VERIFIED_CLAIMS_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:104` |
| `GLUDD_WATCHDOG_CI_FILE` | Auto-indexed (see source) | — | optional | `scripts/run_ci_shards_parallel.py:50` |
| `GLUDD_WATCHDOG_ENABLED` | Auto-indexed (see source) | — | optional | `scripts/test_hook_runtime.py:3592` |
| `GLUDD_WATCHDOG_PID_FILE` | Auto-indexed (see source) | `.gate-logs/watchdog.pid` | optional | `scripts/run_ci_shards_parallel.py:52` |
| `GLUDD_WATCHDOG_VERBOSE` | Auto-indexed (see source) | `0` | optional | `scripts/agent_watchdog.py:324` |
| `GLUDD_WATCHDOG_VERSION` | Auto-indexed (see source) | `1.0` | optional | `scripts/agent_watchdog.py:479` |
| `GLUDD_WORKER_LIMIT` | Auto-indexed (see source) | `8` | optional | `scripts/active_work_status.py:145` |
| `GLUDD_WORKFLOW_DIRS` | Auto-indexed (see source) | — | optional | `scripts/agent_liveness.py:345` |
| `GLUDD_WORKTREE_CAP` | Auto-indexed (see source) | — | optional | `scripts/test_worktree_disk_guard.py:71` |
| `GLUDD_WORKTREE_ENFORCE` | Auto-indexed (see source) | — | optional | `scripts/verify_enforcement.py:107` |
| `GLUDD_XAI_API_KEY` | Auto-indexed (see source) | — | optional | `src/general_ludd/ansible/credential_proxy.py:403` |
| `GLUDD_XDIST_TRACE_LOG` | Auto-indexed (see source) | — | optional | `scripts/run_xdist_trace.py:35` |
| `GLUDD_XDIST_TRACE_TRUNCATE` | Auto-indexed (see source) | — | optional | `scripts/run_xdist_trace.py:36` |
| `GLUDD_XDIST_WORKERS` | Auto-indexed (see source) | — | optional | `scripts/adaptive_test.py:117` |

### 1.1b Active-workstream registry isolation

`GLUDD_ACTIVE_WORKSTREAM_REGISTRY` selects the JSON registry used by worktree
pruning to protect active logical work. Leave it unset for normal operation:
the default hashes the repository's absolute Git common directory into a
12-character namespace below `$TMPDIR/gludd-active-workstreams/`. Worktrees of
one repository therefore share lifecycle state, while unrelated repositories
do not collide. Use an absolute, project-namespaced override only when every
worktree and cleanup runner is configured with the same path.

The registry supports zero-downtime coordination through an adjacent exclusive
lock and a same-directory temporary file followed by atomic replacement.
Readers fail closed on unreadable JSON or an unsupported schema. A path change
would split coordination state, so seed the new registry through the normal
registration lifecycle, switch every consumer together, and only then resume
pruning. Roll back by restoring the previous path; unset the variable only when
the default registry already contains all active workstreams.

Resource use is one compact JSON entry per registered branch plus one lock file;
the registry starts no process and retains no logs. Explicit unregister removes
completed entries. Keep the registry on a local filesystem that supports
advisory locks and atomic rename, and never point multiple projects at the same
override.

Evidence reviewed 2026-08-20: Git's upstream
[`git-worktree` documentation](https://git-scm.com/docs/git-worktree.html)
documents the shared common directory, stable porcelain format, and locked
worktree protection. A user report opened 2026-05-10 describes
[agent sessions leaving locked, orphaned worktrees](https://github.com/anthropics/claude-code/issues/57765)
after abnormal exit. That long-lived failure mode is why Gludd records logical
lifecycle ownership explicitly instead of guessing it from a process ID.

### 1.2 Model-provider credentials

Set **exactly one** provider's key to get a working model. The key name a
profile expects is its `credential_alias` (see §2.2); the canonical names:

| Env var | Provider | Profile example | Default base URL |
|---|---|---|---|
| `ZAI_API_KEY` | Z.AI (GLM) — project default | `config/model_profiles/zai_example.yml` (`zai_coder`) | `https://open.bigmodel.cn/api/paas/v4` (via `ZAI_BASE_URL`) |
| `OPENAI_API_KEY` | OpenAI | `openai_example.yml` (`openai_gpt4`) | `https://api.openai.com/v1` (via `OPENAI_BASE_URL`) |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | — (use via OpenRouter or custom profile) | `https://api.anthropic.com` (via `ANTHROPIC_BASE_URL`) |
| `OPENROUTER_API_KEY` | OpenRouter (multi-provider) | `openrouter_example.yml` (`openrouter_coder`) | `https://openrouter.ai/api/v1` (via `OPENROUTER_BASE_URL`) |
| `DEEPSEEK_API_KEY` | DeepSeek | `deepseek_coder.yml` (`deepseek_coder`) | DeepSeek endpoint (via `deepseek_api_base` alias) |
| `GROQ_API_KEY` | Groq | — | Groq endpoint |
| `MISTRAL_API_KEY` | Mistral | — | Mistral endpoint |
| `FIREWORKS_API_KEY` | Fireworks AI | — | Fireworks endpoint |

Optional companion vars (provider-specific base URL / default model overrides):
`ZAI_BASE_URL`, `ZAI_MODEL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`,
`OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`. The secrets layer
(`secrets/env.py`) lowercases these when injecting into the model gateway
(e.g. `ZAI_BASE_URL` → `zai_api_base`).

Any `*_API_KEY` env var present at daemon boot is also auto-registered as a
metered service for budget tracking (`daemon.py:1123`, `worker/app.py:74`) —
services without an explicit budget entry still get observed.

### 1.3 Database & persistence

| Name | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL override. **SQLite only is supported** (`sqlite+aiosqlite://...`); a non-SQLite URL is refused by `init_engine_from_config`. | `sqlite+aiosqlite:///$XDG_DATA_HOME/general-ludd/general-ludd.db` |
| `XDG_DATA_HOME` | Data-directory root (holds the SQLite file). | `~/.local/share` |

> Postgres is **not** supported in this release. `general-ludd.yml` ships a
> `database:` block for forward compatibility, but the daemon clamps gunicorn
> workers to 1 and refuses non-SQLite URLs. See `cli.py:_clamp_workers_for_sqlite`.

### 1.4 Observability, infra connectors, CI (all optional)

| Name | Purpose | Default |
|---|---|---|
| `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` | Enable LangSmith tracing. Both required to activate. | off |
| `SLURM_API_URL` + `SLURM_AUTH_TOKEN` | Slurm compute integration. | off |
| `AWS_ACCESS_KEY_ID` (+ `AWS_SECRET_ACCESS_KEY`) | AWS pricing/live onboarding. | off |
| `GITHUB_TOKEN` | GitHub Actions connector + issue sources. | off |
| `PROMETHEUS_TOKEN` | Bearer token for Prometheus connector. | off |
| `DATADOG_API_KEY` + `DATADOG_APP_KEY` | Datadog logs connector. | off |
| `POSTGRES_AVAILABLE=1` | Opt-in flag enabling Postgres-dependent tests. | skipped |
| `SLURM_AVAILABLE=1` | Opt-in flag enabling Slurm-dependent tests. | skipped |

---

## 2. Config Files

### 2.0 Config discovery — read this first

The daemon searches for its config directory in exactly this order:

1. `$GLUDD_CONFIG_DIR` (if set)
2. `~/.config/general-ludd`
3. `/etc/general-ludd`

**The repo's own `config/` directory is NOT on that path.** It is a set of
*examples to copy*, not a location the daemon reads.

> **The #1 "why does nothing happen?" trap.** If you start the daemon from a
> repo checkout **without `GLUDD_CONFIG_DIR` set**, no `model_profiles/` are
> found, so no model profiles load, the model gateway stays `None`, and the
> dispatcher silently falls back to a **no-op executor**. Every dispatched agent
> then returns `status="completed"` with **empty output** and **no warning is
> logged** — while `/healthz` and `/readyz` keep returning 200/ready. Agents
> appear to succeed instantly and do nothing.
>
> Do one of these before starting the daemon:
>
> ```bash
> export GLUDD_CONFIG_DIR="$PWD/config"        # point at the repo's config tree
> # ...or install the config into the real discovery path:
> mkdir -p ~/.config/general-ludd
> cp -r config/model_profiles ~/.config/general-ludd/
> cp config/general-ludd.yml ~/.config/general-ludd/
> ```
>
> Confirm it worked: `gludd models router-status` must list an active profile.
> An empty profile list means you are in the no-op-executor failure mode.

### 2.0.1 Layering

Operators override by copying into `~/.config/general-ludd/` (user) or
`/etc/general-ludd/` (system); env vars win over all file layers. Load priority
(high → low):

1. Environment variables
2. `~/.config/general-ludd/user.yml` — per-user overrides
3. `.general-ludd/agent_config.yml` — per-project agent settings
4. `/etc/general-ludd/general-ludd.yml` — system defaults
5. Built-in defaults compiled into the package (**not** the repo's `config/` tree)

### 2.1 Top-level files

| Path | Format | Purpose |
|---|---|---|
| `config/general-ludd.yml` | YAML | **Main config.** Holds `model_routing`, `database`, `agents`, `process_isolation`, `budget`. The default profile is `deepseek_coder` (fallback chain `qwen_coder` → `zai_coder`). |
| `config/model_routing.yml` | YAML | Standalone routing table (alternative to the `model_routing:` block in `general-ludd.yml`). Defines `default_profile`, `fallback_chain`, role/quality/latency/pattern routing. |
| `config/binary_paths.yml` | YAML | Overrides paths to external binaries (terraform, opentofu, vault, openbao, podman, docker, ansible-playbook, git, uv, opa, conftest). Defaults to `shutil.which()` PATH lookup. |
| `config/ratchet.yml` | YAML | Known-failing test tracker (`node_id: reason`). Read by `tests/conftest.py`; the suite stays red until a passing test's marker is lifted. **Not a runtime config** — operators do not edit it. |

### 2.2 `config/model_profiles/*.yml` — per-model definitions

Each file defines one profile referenced by ID from routing tables. Shipped
examples (copy the relevant one, set its `credential_alias` env var, and point
routing at its `model_profile_id`):

| File | `model_profile_id` | Provider | Credential alias |
|---|---|---|---|
| `zai_example.yml` | `zai_coder` | Z.AI (GLM-4.6) | `ZAI_API_KEY` |
| `openai_example.yml` | `openai_gpt4` | OpenAI (GPT-4) | `openai_api_key` |
| `openrouter_example.yml` | `openrouter_coder` | OpenRouter | `openrouter_api_key` |
| `deepseek_coder.yml` | `deepseek_coder` | DeepSeek | `deepseek_api_key` |
| `qwen_coder.yml` | `qwen_coder` | Qwen | (see file) |
| `anthropic_example.yml` | — | Anthropic | `ANTHROPIC_API_KEY` |
| `vllm_example.yml` | — | Local vLLM | (none — local) |
| `llamacpp_example.yml` | — | Local llama.cpp | (none — local) |
| `compactor.yml` | — | Compaction model | (see file) |

Minimal profile shape (from `zai_example.yml`):

```yaml
model_profile_id: zai_coder
provider: openai                     # langchain provider key
provider_package: langchain_openai   # pip-importable package
provider_class_hint: ChatOpenAI      # chat class
model_name: glm-4.6
credential_alias: ZAI_API_KEY        # env var read at call time
api_base_alias: ZAI_BASE_URL         # optional endpoint override
context_window: 64000
max_input_tokens: 60000
max_output_tokens: 16384
cost_per_input_token: 0.001
cost_per_output_token: 0.003
run_budget_usd: 1.0
enabled: true
roles: [coder, planner]
latency_class: fast
quality_class: high
fallback_profiles: []                # profile IDs to try on failure
probe_enabled: false                 # health-probe the profile at boot
```

### 2.3 `config/permissions/*.yml` — PermissionSpec per subject

Capability/deny lists for each agent type and human role. Selected by
`default_human_role` (default `human-operator`) for human users; agents get
their `agent_type` spec. The **intersection rule** applies on subagent
dispatch (effective spec = lowest-common-subset of human ∩ agent ∩ requested).

| File | Subject | Notable scope |
|---|---|---|
| `build.yml` | default build agent | repo `/repo/`, tmp `/tmp/gludd/`, LLM egress to anthropic/openai/z.ai, OpenBao `secret/data/gludd/build/*`, ornith solve/improve |
| `primary.yml` | primary orchestrator agent | widest agent scope |
| `subagent.yml` | dispatched subagent | narrowed from parent |
| `task_implement_change.yml` | task-implement role | change-implementation scoped |
| `agent-ornith.yml` | ornith self-improve agent | training-loop scoped |
| `human-admin.yml` | human admin | full file/net/secret access |
| `human-operator.yml` | human operator (default) | repo + any net + OpenBao read |
| `human-viewer.yml` | human viewer | read-only |

### 2.4 Other `config/` subdirectories

| Path | Purpose |
|---|---|
| `config/agents/default_agents.yml` | Agent definitions (default agent `build`, max_concurrent `4`). |
| `config/tasks/example_tasks.yml` | Seed todos for first-boot / demos. |
| `config/examples/` | Copy-and-edit templates: `user_config_example.yml` (user overrides), `agent_config_example.yml` (per-project), `connectors_example.yml` (observability connectors — Prometheus, Datadog, GH Actions, journald). |
| `config/prompt_profiles/` | Prompt-template definitions. |
| `config/skills/` | Skill catalog definitions. |
| `config/mcp_servers/` | MCP server configs (loaded by `daemon.py` lifespan; external servers registered alongside builtins). |
| `config/ansible/` | Ansible runtime config (ansible.cfg, collection paths). |
| `config/infra/` | Infrastructure integration configs. |
| `config/opa/` | Open Policy Agent rego policies. |
| `config/openbao/` | OpenBao (secrets) connection config. |

### 2.5 Example: minimal `general-ludd.yml`

```yaml
model_routing:
  default_profile: deepseek_coder   # must match a model_profile_id
  weak_model_profile: deepseek_coder
  role_routing:    {coder: deepseek_coder, planner: deepseek_coder, reviewer: deepseek_coder}
  quality_routing: {high: deepseek_coder, medium: deepseek_coder}
  latency_routing: {fast: deepseek_coder}
  pattern_routing: {code_generation: coder, commit_message: weak}
database:                           # SQLite-only; block is forward-compat
  host: localhost
  port: 5432
  name: gludd
  user: gludd
agents:
  default_agent: build
  max_concurrent: 4
process_isolation:
  enabled: false                    # set true + install podman/bwrap for sandboxing
  container_runtime: podman
budget:
  max_usd: 50                       # hard spend cap across all profiles
  warn_percent: 80
```

---

## 3. Minimal Run Path (verified step-by-step)

Each step below lists the exact command, what success looks like, and how it
was verified for this doc.

### Step 0 — Prerequisites

- Python ≥ 3.11
- `uv` (preferred) or `pip`
- SQLite (bundled with Python; zero-config)
- One model-provider API key (§1.2)

### Step 1 — Install

```bash
make init
```

**What it does:** creates the venv, runs `uv sync`, creates runtime dirs.
**Success:** command exits 0; `.venv/` populated.
**Verified:** `make healthcheck` → `Worker app factory OK` / `Event loop import OK`
(run for this doc; confirms `general_ludd.daemon`, worker factory, and event
loop import cleanly). `make bootstrap` additionally runs lint + test +
healthcheck for a full green check.

### Step 2 — Configure one model provider

Pick **one** provider. Cheapest default path is Z.AI:

```bash
export ZAI_API_KEY=your-key-here
# optional, only if not using the profile's built-in endpoint:
# export ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

The default profile (`deepseek_coder`, set in `config/general-ludd.yml` →
`model_routing.default_profile`) reads `DEEPSEEK_API_KEY` at call time via its
`credential_alias`. No file edit is required for the default path.

**To switch provider:** either (a) edit `default_profile:` in
`general-ludd.yml` to a different `model_profile_id` whose
`credential_alias` you have set, or (b) set the matching `*_API_KEY` env var
and rely on auto-discovery (`daemon.py:1123` registers any `*_API_KEY` as a
metered service; `provider_presets.py` maps credential env vars per provider).

**Success:** the chosen `*_API_KEY` is present in `env`; `gludd models
router-status` (against a running daemon) lists the active profile.

### Step 3 — Start the daemon

**From a repo checkout, set `GLUDD_CONFIG_DIR` first** (§2.0) — otherwise no model
profiles load and the dispatcher silently no-ops.

```bash
export GLUDD_CONFIG_DIR="$PWD/config"
gludd daemon
# or with flags:
gludd daemon --host 127.0.0.1 --port 8000 --log-level info --tick-interval 1.0
```

**What it does:** spawns `gunicorn general_ludd.daemon:create_daemon_app()
--worker-class uvicorn_worker.UvicornWorker --workers 1 --bind 127.0.0.1:8000`
(workers clamped to 1 on SQLite). The FastAPI app boots, runs
`alembic stamp_head` on the SQLite DB, starts the event-loop tick task, and
registers model/MCP/permission subsystems.

**Success:** `curl http://localhost:8000/healthz` returns JSON `{"status":"ok"}`
(equivalently `gludd health`). The first tick logs show the event loop
running. Binding to a non-loopback host auto-generates and prints a `GLUDD_AUTH_PSK`
— all clients must then send `Authorization: Bearer <psk>`.

> **`/healthz` and `/readyz` returning 200/ready does NOT prove the daemon can do
> any work.** They report 200 even when zero model profiles loaded and the
> dispatcher is a no-op. The real liveness check is
> `gludd models router-status` — it must list an active profile. See §2.0.

**Verified:** the daemon factory and event loop import cleanly
(`make healthcheck`); full boot behaviour is pinned by
`tests/unit/test_daemon_launch_config.py` and exercised end-to-end by
`make smoke` (daemon start → todo submit → todo complete → daemon stop).

### Step 4 — Submit a todo

```bash
gludd add "Write a hello-world pytest test" --work-type code
```

**What it does:** `POST /api/todos` with `{title, description, queue:"core",
priority:100, work_type}`. The todo is persisted to SQLite and becomes
claimable on the next event-loop tick.

**Success:** JSON response with the new todo's `id`, `status: "queued"`.
Confirm with `gludd list`.

### Step 5 — Watch it complete

```bash
gludd status                 # system + todo summary
gludd list --status queued   # watch the queue drain
gludd status <TODO_ID>       # per-todo detail incl. transitions
```

**What happens:** the event loop claims the runnable todo, dispatches it to
the configured model profile, executes the returned plan via the ansible
runner, and transitions the todo through `queued → running → completed`
(or `failed` / `blocked_on_human`).

**Success:** the todo reaches `completed` with a recorded result; `gludd
status <id>` shows the terminal state and any produced artifacts.

### Full green check (optional, slower)

```bash
make bootstrap               # init + lint + test + healthcheck
make gate                    # lint + typecheck + collect + test + smoke (writes .gate-status)
# or background (recommended on the main thread):
make gate-background && make gate-status-check
```

---

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Cannot connect to daemon at http://localhost:8000` | daemon not running | `gludd daemon` |
| `GLUDD_REQUIRE_AUTH is set but no GLUDD_AUTH_PSK configured ... failing CLOSED (503)` | auth forced without a PSK | `export GLUDD_AUTH_PSK=$(openssl rand -hex 32)` and send it as Bearer token, or unset `GLUDD_REQUIRE_AUTH` |
| 401 on CLI calls | daemon bound to non-loopback (auto-PSK) but CLI lacks it | re-read the PSK printed at daemon boot; `export GLUDD_AUTH_PSK=<that value>` |
| Todo stuck in `queued` | no model profile reachable / key missing | confirm the profile's `credential_alias` env var is set; `gludd models router-status` |
| **Agents return `completed` instantly with EMPTY output, no warning, health still 200** | **No model profiles were found, so the dispatcher fell back to a no-op executor.** Almost always: the daemon was started from a repo checkout without `GLUDD_CONFIG_DIR`. | Set `GLUDD_CONFIG_DIR` (or install the config into `~/.config/general-ludd/`) and restart — see §2.0. Verify with `gludd models router-status`. |
| Every write endpoint fails / DB is read-only | `GLUDD_WRITER_MODE=subprocess` was set | Unset it. `inline` is the only working mode — see §5. |
| `non-SQLite URL refused` | `DATABASE_URL` points at Postgres | unset it (SQLite-only this release); Postgres is unsupported |
| `gunicorn workers clamped to 1` warning | `--workers N>1` on SQLite | expected; single-worker is the honest SQLite config |
| Config not applied | layering mismatch | env vars > `~/.config/general-ludd/user.yml` > `.general-ludd/agent_config.yml` > `/etc/general-ludd/general-ludd.yml` > defaults |

---

## 5. Experimental flags — DO NOT ENABLE

These knobs exist in the code and in config schemas but are **not functional**.
They are listed here so operators do not "discover" them and turn them on.

### `GLUDD_WRITER_MODE=subprocess` — structurally non-functional

Setting this **breaks every write endpoint.** Three independent defects:

- The `WriteQueue` is an in-process deque with no IPC, while the writer is a
  real subprocess — the queue **cannot reach the writer child** at all.
- A config-shape bug leaves the writer child permanently in a stub branch, so it
  never does any work.
- HTTP workers are handed a genuinely read-only engine (`PRAGMA query_only=ON`).

Net effect: writes are rejected and the writer does nothing.
**`inline` (the default) is the only working mode.** Do not set this variable.

### `pipeline.enabled` (feature #77) — EXPERIMENTAL, do not enable

The pipeline feature's quality gate is hardcoded to `return True` — it reports
"GREEN — committed" for a validation that never ran — and its anti-clobber merge
passes the repo's own content as both the merge base and "ours", so it **can
never detect a conflict**. It is harmless today only because nothing feeds it.
Leave `pipeline.enabled` off.

---

## 6. Cross-references

- `docs/quickstart.md` — fast-path narrative version of §3
- `docs/RELEASE_RUNBOOK.md` — cutting a release and verifying it actually shipped
- `docs/configuration.md` — detailed `general-ludd.yml` field reference
- `docs/model-setup.md` — model provider onboarding
- `docs/PROVIDER_ONBOARDING.md` — adding a new provider
- `docs/PROVIDER_ONBOARDING.md` / `docs/profiles.md` — profile authoring
- `docs/operations/` — operator runbooks
- `AGENTS.md` § "Project Overview" / "Key Make Targets" — make target catalogue
- `Makefile` — every runnable target (only `make <target>` is permitted in bash)
