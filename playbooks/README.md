# gludd Playbooks

This directory contains all Ansible playbooks executed by the gludd agent harness.
Each playbook is a thin entry point that delegates to a role under
`collections/ansible_collections/general_ludd/*/roles/` (or invokes a module
directly). Playbooks are designed to be idempotent, run on `localhost`, and
emit a JSON artifact to `artifact_dir` (default `/tmp/harness-*`) for the
daemon to consume.

54 playbooks total, grouped into 11 categories below.

---

## Quick Start — Run Your First Playbook

```bash
# 1. From the repo root, ensure deps are installed
make init

# 2. Run a no-op playbook to verify the harness works
ansible-playbook playbooks/noop.yml

# 3. Inspect the artifact it produced
cat /tmp/harness-noop/noop_result.json

# 4. Run a playbook with extra vars (e.g. agent orchestration)
ansible-playbook playbooks/agent_orchestrate.yml \
  -e work_type=feature \
  -e prompt_text="Refactor the auth module" \
  -e daemon_url=http://localhost:8000

# 5. Common vars accepted by most playbooks:
#      artifact_dir  — where JSON result is written (default /tmp/harness-<name>)
#      daemon_url    — gludd daemon base URL (default http://localhost:8000)
#      psk           — pre-shared key for daemon auth (default empty)
#      todo_id       — gludd todo ID being worked (default 'none')
#      job_id        — gludd job ID for traceability (default '<PREFIX>-00000000')
```

Most playbooks are invoked by the daemon via `ansible-runner`, not by hand.
Manual invocation is useful for development, debugging, and smoke tests.

---

## Core Operations

The main agent loop, validation, and lifecycle hooks for the harness itself.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `agent_orchestrate.yml` | Env-fact-driven agent orchestration: gathers env brief + per-work-type advice, branches to multi-step LangGraph workflow or single-shot routed model call under a budget guard | Main daemon entry point for any agent task (`work_type=feature\|fix\|analysis\|...`) | Daemon running, model profile configured | `work_type`, `prompt_text`, `skill_body`, `todo_id`, `min_remaining_usd`, `quality_threshold`, `daemon_url` |
| `quality_gate_validate.yml` | Placeholder for quality gate validation (lint + typecheck + collect + test) | Before commit / release gate | None (placeholder) | — |
| `runtime_validate.yml` | Placeholder for runtime profile validation | Verifying the runtime profile compiles | None (placeholder) | — |
| `validate_task.yml` | Runs a list of test commands against a worktree and records results | Validating a task before marking it done | `worktree_path` (default `/tmp/worktree`) | `todo_id`, `worktree_path`, `test_commands` (list) |
| `self_improve_harness.yml` | Invokes the full `agent_task` lifecycle for self-improvement work; falls back to a minimal artifact if `todo_id`/`repo_path` are unset | Self-improvement todos (agent introspects its own behavior) | Daemon running (when `todo_id` + `repo_path` set) | `job_id`, `todo_id`, `repo_path`, `daemon_url`, `model_profile` |
| `ornith_self_improve.yml` | Thin wrapper around the `ornith_self_improve` role | Running the Ornith self-improvement pass | Role config | — (role-managed) |
| `reload_harness.yml` | Reloads harness components by type (config, modules, plugins) | After config/plugin/module changes | None | `job_id`, `reload_type` (`config\|modules\|plugins`) |
| `return_review.yml` | Reviews a task return via the model gateway; emits a task-decision artifact | Deciding whether a completed task is truly done | `return_id` (required), daemon running | `return_id`, `todo_id`, `model_profile`, `prompt_profile` |
| `enforcement_gate.yml` | Fail-closed pre-commit/pre-push compliance gate via the `enforcement_gate` role | Pre-commit / pre-push hook | Daemon running | `repo_path`, `daemon_url`, `model_profile`, `work_type` |
| `project_init.yml` | Initializes a gludd project-specific collection via the `project_init` role | Running `gludd project init` | None | — (role-managed) |

## Git Workflows

Repository initialization, change automation, and worktree management.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `git_repo_init.yml` | Initializes a git repo and configures `user.email` / `user.name` | Bootstrapping a new project repo | `repo_path` writable | `repo_path`, `user_email`, `user_name` |
| `git_automate_change.yml` | Stages all changes (`git add -A`) and commits them with a message | End-to-end "make this change" commands | Repo already initialized | `repo_path`, `branch_name`, `commit_message`, `tag_name`, `remote` |
| `git_manage_worktree.yml` | Lists git worktrees (`git worktree list --porcelain`) and records the result | Inspecting active worktrees | Repo with worktrees | `repo_path`, `worktree_action` (`list\|add\|remove`), `worktree_path`, `branch_name` |
| `scan_conflict_markers.yml` | Scans the repo for unresolved git conflict markers via the `scan_conflict_markers` role | After a merge / before a push | Repo path | `scan_paths` (list, default all) |

## Security

OpenBao secrets backend, key generation, and signing configuration.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `openbao_bootstrap.yml` | Bootstraps an OpenBao instance (records intent; stub artifact for now) | Initial secrets backend setup | OpenBao image reachable | `openbao_image`, `openbao_port`, `openbao_mode` (`auto\|dev\|prod`) |
| `openbao_backup.yml` | Break-glass backup of OpenBao state via the `openbao_break_glass_backup` role; GPG-encrypts to a recipient | Emergency / scheduled backups | GPG key for `gpg_recipient`, OpenBao running | `backup_dir`, `gpg_recipient` |
| `openbao_image_update_scan.yml` | Scans for OpenBao image updates (records intent; stub artifact) | Weekly update checks | None | `openbao_image` |
| `cosign_key_generate.yml` | Generates a cosign key pair and stores it in OpenBao via Python script | Setting up artifact signing | OpenBao running, `secrets.manager` importable | `project_id`, `key_name`, `cosign_key_password`, `cosign_output_dir` |
| `gitsign_configure.yml` | Configures gitsign (sigstore) for a project via OpenBao and writes local git config | Enabling keyless commit signing | OpenBao running, `secrets.gitsign` importable, git repo at `git_root` | `project_id`, `git_root`, `fulcio_url`, `rekor_url`, `oidc_issuer`, `key_ref` |

## CI/CD

Continuous integration observation and release artifact validation.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `ci_annotations_poll.yml` | Polls live CI annotations for a GitHub Actions run via the `ci_annotations_poll` role | Watching a CI run for failures in real time | `ci_run_id`, `gh` authenticated | `ci_run_id`, `poll_interval_seconds`, `poll_max_seconds`, `early_exit_on_failure` |
| `release_artifacts_validate.yml` | Placeholder for release artifact validation | Before publishing a release | None (placeholder) | — |

## Code Quality

Audits for feature claims, evidence, guardrails, and policy compliance.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `feature_evidence_audit.yml` | Audits `features.yml` for false claims via evidence-quality analysis (role-based) | Sprint / release readiness review | Repo path | — (role-managed) |
| `verify_feature_claims.yml` | Runs `scripts/gen_status_table.py --check` to verify feature-claim `test:` refs resolve | Verifying README status table honesty | Repo path, `uv` installed | — |
| `gap_analysis.yml` | Runs gap analysis on a sprint directory; records a stub artifact | Sprint planning | `sprint_path` existing | `sprint_path`, `repo_root` |
| `audit_plugins.yml` | Multi-role audit: agent floor, delegate discipline, task deadlines, opt-in deletion gate, spec-lifecycle, opt-in enforce-disengage | Periodic plugin/guardrail health audit | Daemon running for some sub-roles | `daemon_url`, `psk`, `include_deletion_gate`, `audit_plugins_run_enforce_disengage` |
| `backlog_guard_audit.yml` | Bug-class sweep + guard-coverage audit via the `backlog_guard_audit` role | Finding gaps in guardrails | Repo path | `skip_collect` |
| `action_policy_validate.yml` | Placeholder for action-policy validation before playbook execution | Pre-execution policy gate | None (placeholder) | `policy_config`, `playbook_target` |
| `enforce_disengage.yml` | Last-resort escape hatch for blocked commits via the `enforce_disengage` role (writes `/tmp/gludd-watchdog-disengage.json`) | When all enforcement is wedging legitimate work | Use sparingly; documented in AGENTS.md | `disengage_duration_hours`, `disengage_max_hours`, `enable_commit`, `enable_push`, `verify_branch` |

## Monitoring

System metrics, reports, and backlog observation.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `system_load_scrape.yml` | Scrapes system load metrics (CPU, memory, disk via `psutil`) to a JSON artifact | Health checks, capacity planning | `psutil` Python package | `job_id`, `include_gpu` |
| `system_report.yml` | Consolidated report: runs `report_status` + `report_metrics` + `report_audit` roles | Operator dashboard / shift handoff | Daemon running | `daemon_url`, `audit_security_artifact`, `audit_dependencies_artifact` |
| `token_window_monitor.yml` | Monitors the 5-hour token window and throttles the subagent floor via the `token_window_monitor` role | Cost management / avoiding rate limits | Repo path | `twm_mode` (`once\|loop`), `calibrate_pct`, `twm_normal_floor` |
| `multitasking_backlog_check.yml` | Inspects the gludd multitasking backlog via the `multitasking_backlog_check` role | Verifying the floor is maintained | Repo path | `backlog_check_mode` (`assert-done\|report`), `backlog_file` |
| `log_audit.yml` | Audits logs for anomalies (stub artifact for now) | Routine log review | `log_source` path | `log_source` |
| `log_analyzer.yml` | Analyzes log files for errors and clusters via the `operations.log_analyzer` role | Incident response / log triage | Log files reachable | — (role-managed) |
| `data_source_mount_audit.yml` | Placeholder for data-source mount audit | Verifying mounted data sources | None (placeholder) | — |

## Infrastructure

Networking, dependencies, container images, and install validation.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `networking.yml` | Imports the `general_ludd.networking.networking` role | Testing / applying network config | Role config | — (role-managed) |
| `dependency_update.yml` | Runs `lint_and_check` role after a dependency update; records the update | After bumping a package | `project_dir` | `package_name`, `version_constraint`, `project_dir` |
| `native_install_validate.yml` | Placeholder for native install (uv/pip) validation | Verifying the install path | None (placeholder) | — |
| `pip_install_bundle.yml` | Placeholder for pip bundle install | Installing a bundled set of packages | None (placeholder) | — |
| `container_image_validate.yml` | Placeholder for container image validation | Before shipping a container | None (placeholder) | — |
| `slim_agent_container_build.yml` | Placeholder for slim agent container build | Building the minimal agent image | None (placeholder) | — |
| `ara_setup.yml` | Placeholder for ARA (Ansible Run Analyzer) recording backend setup | Enabling playbook recording | None (placeholder) | `ara_enabled`, `ara_backend` |

## Testing

Molecule test execution and coverage auditing.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `molecule_test.yml` | Runs the test suite via the `run_tests` role (defaults to `make test-count`); writes a molecule-test artifact | Running molecule scenarios | Test directory | `test_dir`, `scenario`, `job_id`, `todo_id` |
| `molecule_coverage_audit.yml` | Placeholder for molecule coverage audit | Coverage reporting | None (placeholder) | — |

## AI / Model Operations

Model-backed decision, generation, and prompt evaluation.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `langgraph_decide.yml` | LangGraph model-backed decision playbook (role-based multi-step decision workflow) | Complex decisions requiring graph reasoning | Daemon running | `prompt_text` (or `model_response`), `skill_body`, `work_type`, `model_profile`, `daemon_url` |
| `langchain_generate.yml` | Single-shot LangChain generation playbook; asserts prompt non-empty, generates, optionally records result via `gludd_db` | Text generation / analysis | Daemon running, `prompt_text` required | `prompt_text`, `skill_body`, `model_profile`, `capability_role`, `daemon_url` |
| `prompt_eval.yml` | Prompt evaluation for dogfood testing; calls `gludd_model_call` and writes an eval artifact | Evaluating prompt quality | Daemon running, `prompt_text` | `prompt_text`, `model_profile`, `daemon_url` |

## Streaming / Ingestion

Operator scenarios using the `gludd_stream` module to ingest audio, video, or text streams and dispatch chunks to cloned roles.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `stream_audio_to_tasks.yml` | Streams ALSA audio (`hw:0,0`) -> whisper.cpp transcription -> classifies each transcript as note/question/discard -> submits notes as `self_improve` todos | Voice-driven todo capture | ALSA device, whisper.cpp + model at `whisper_model` | `audio_device`, `whisper_model`, `daemon_url` |
| `stream_video_feature_detection.yml` | Polls a webcam (`/dev/video0`) every 30s -> hands frame batch to a vision-capable agent -> appends analysis to a markdown report (capped at 10 dispatches) | Shift-change spot checks; security/monitoring | V4L2 webcam at `video_device`, vision-capable agent | `video_device`, `analysis_prompt`, `daemon_url` |
| `stream_text_log_tail.yml` | Tails a log file in 10 KiB chunks -> cloned role greps for `ERROR\|FATAL` -> posts hits to Slack (or dry-run artifact if no webhook) | Real-time log error alerting | `SLACK_WEBHOOK_URL` env var for posting; log file readable | `log_path`, `slack_webhook_url` (env), `daemon_url` |

## Utilities

Demos, status tables, spec lifecycle, and harness smoke tests.

| Playbook | Description | When to Use | Required Config | Key Vars |
|----------|-------------|-------------|-----------------|----------|
| `noop.yml` | No-op playbook for harness testing; writes a `noop_result.json` artifact | Smoke-testing the harness end-to-end | None | `job_id`, `todo_id`, `artifact_dir` |
| `generate_status_table.yml` | Generates or checks the README Feature & Task Completion Status table via the `generate_status_table` role | Refreshing README status; CI gate | Repo path | `gen_mode` (`check\|generate`), `manifest_path`, `output_path` |
| `spec_lifecycle.yml` | Spec-Driven Development lifecycle entry point: drives the 3-stage pipeline (drafts -> active -> archive) via the `spec_lifecycle` role | Managing `.spec.md/` tasks | `.spec.md/` dir | `operation` (`init\|create_task\|approve_task\|complete_task\|discard_task\|list\|revise\|repair\|interview\|map_codebase\|diagram_architecture`), `task_name`, `task_size` |
| `agent_coordination_demo.yml` | Two-play demo of the facts-as-message-queue inter-agent channel: Role A reads `gludd_facts` and sends via `gludd_message`; Role B receives and processes | Learning / verifying the coordination channel | Daemon running | `daemon_url`, `demo_sender`, `demo_recipient`, `demo_topic` |

---

## Conventions

- **All playbooks target `hosts: localhost` with `connection: local`** — they never SSH to a remote host.
- **`artifact_dir`** (default `/tmp/harness-<name>` or `/tmp/gludd-<name>`) is where each playbook writes its JSON result for the daemon to consume.
- **Every gludd-supplied var is `default()`-guarded** so playbooks run standalone with no extra vars for smoke testing.
- **Roles vs tasks**: thin playbooks (e.g. `ornith_self_improve.yml`, `project_init.yml`) delegate entirely to a role; rich playbooks (e.g. `agent_coordination_demo.yml`, `stream_*.yml`) inline tasks for demonstration.
- **Placeholders**: several playbooks (e.g. `runtime_validate.yml`, `release_artifacts_validate.yml`, `pip_install_bundle.yml`) currently emit a debug msg only — they are reserved slots for future implementation.

## Invocation Examples

```bash
# Smoke test (no deps)
ansible-playbook playbooks/noop.yml

# Agent orchestration (requires daemon)
ansible-playbook playbooks/agent_orchestrate.yml \
  -e work_type=feature \
  -e prompt_text="Add a /health endpoint" \
  -e todo_id=TODO-1234

# Git change automation
ansible-playbook playbooks/git_automate_change.yml \
  -e repo_path=. \
  -e commit_message="fix: patch the auth bug"

# Cosign key generation (requires OpenBao)
ansible-playbook playbooks/cosign_key_generate.yml \
  -e project_id=default \
  -e key_name=release-key \
  -e cosign_key_password="$COSIGN_PASSWORD"

# Stream audio to todos (requires whisper.cpp + ALSA)
ansible-playbook playbooks/stream_audio_to_tasks.yml \
  -e audio_device=hw:0,0 \
  -e whisper_model=/models/base.en.bin

# Spec lifecycle — create a task
ansible-playbook playbooks/spec_lifecycle.yml \
  -e operation=create_task \
  -e task_name="Add OAuth flow" \
  -e task_size=medium

# System report (requires daemon)
ansible-playbook playbooks/system_report.yml \
  -e daemon_url=http://localhost:8000
```

## Adding a New Playbook

1. Copy `noop.yml` as a template (it has the minimal structure).
2. Add `name:`, `hosts: localhost`, `connection: local`, `gather_facts: false`.
3. Default every var with `{{ var | default('...') }}`.
4. Create the artifact dir, write a JSON result, emit a debug msg.
5. If the playbook grows beyond ~30 lines, extract a role under
   `collections/ansible_collections/general_ludd/agent/roles/<name>/`.
6. Add an entry to the appropriate category table in this README.
