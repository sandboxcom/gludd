# Directory Structure — General Ludd

> **Quick jump:** [Root files](#root-files) · [Source tree](#srclanguage_ludd) · [Configuration](#config) · [Infrastructure](#infrastructure--operations) · [Quality & tests](#quality--tests) · [Quick navigation](#quick-navigation)

## Root files

| File | Purpose | REQUIRED or GENERATED |
|---|---|---|
| `Makefile` | 5300+ line build system (~200 targets): setup, test, lint, typecheck, gate, git, CI, release, Docker, Ansible, Terraform, disk, submodules | **REQUIRED** — delete and nothing builds |
| `pyproject.toml` | Python package metadata, dependencies (uv), ruff/mypy/pytest config, build system definition | **REQUIRED** — delete and uv/pip fail |
| `gludd.spec` | PyInstaller spec for building standalone executables | **REQUIRED** for `make build-executable` |
| `opencode.json` | OpenCode agent config: plugins list, tool permissions (5-path allowlist), MCP servers, agent definitions | **REQUIRED** — delete and enforcement plugins stop loading |
| `AGENTS.md` | The agent constitution — all policies, guardrails, enforcement rules, and behavioral contracts (~3000 lines) | **REQUIRED** — delete and the agent has no rules |
| `CLAUDE.md` | Symlink to AGENTS.md for Claude Code compatibility | **REQUIRED** (symlink) |
| `TASKS.md` | Machine-verifiable task ledger — tracks all dispatched work across waves | **REQUIRED** — the agent's to-do list |
| `BUGS.md` | Premature-stop incidents, process failures, and structural bugs | **REQUIRED** — the agent's bug tracker |
| `SESSION.md` | Session handoff: last commit, test status, known gaps, next steps | **REQUIRED** — how the agent remembers across restarts |
| `README.md` | Project overview, feature completion status table, quick-start guide | **REQUIRED** — docs |
| `CHANGELOG.md` | Release changelog | **REQUIRED** — docs |
| `CONTRIBUTING.md` | Contributor guide | **REQUIRED** — docs |
| `SECURITY.md` | Security policy | **REQUIRED** — docs |
| `LICENSE` | Project license | **REQUIRED** |
| `THIRD_PARTY_LICENSES.md` | Third-party license attributions | **REQUIRED** |
| `ansible.cfg` | Ansible runtime config: collection paths, roles path, plugins, stdout callback | **REQUIRED** for Ansible playbook execution |
| `alembic.ini` | Alembic DB migration config: connection string, script location | **REQUIRED** for DB migrations |
| `uv.lock` | Locked dependency versions (uv) | **REQUIRED** — reproducible installs |
| `.secrets.baseline` | detect-secrets baseline — known safe secrets, rebuilt on `make secrets-baseline` | **REQUIRED** — secrets gate fails without it |
| `.pre-commit-config.yaml` | Pre-commit hooks: detect-secrets, ruff lint, collect-check | **REQUIRED** — commit gate |
| `Containerfile` | OCI container image build (generic) | **REQUIRED** for container builds |
| `Dockerfile` | Docker build file (legacy/convenience) | **REQUIRED** for some CI workflows |
| `project.yml` | Project-level config overrides | Config |
| `.gitignore` | Git ignore rules for venvs, caches, artifacts, state files | **REQUIRED** |
| `.gitattributes` | Git attribute rules | Config |
| `.gitmodules` | Git submodule declarations | Config |
| `.mcp.json` | MCP (Model Context Protocol) server definitions | Optional — MCP integrations |
| `sandboxcom_github_rsa` | SSH deploy key for sandboxcom GitHub remote | **REQUIRED** for git push/pull to sandboxcom |

**Hidden/dot directories at root** (state, cache, or integration):

| Directory | Purpose | Safe to delete? |
|---|---|---|
| `.venv/` | Python virtual environment (~300 MB) | Yes — `make sync` rebuilds |
| `.mypy_cache/` | Mypy type-checking cache | Yes — regenerates |
| `.ruff_cache/` | Ruff linter cache | Yes — regenerates |
| `.pytest_cache/` | Pytest test cache | Yes — regenerates |
| `.git/` | Git repository | **NO** — the entire history |
| `.github/` | GitHub Actions CI workflows | **NO** — CI pipeline |
| `.opencode/` | OpenCode plugins, skills, agents, node_modules | **NO** — enforcement layer |
| `.opencode.orig/` | Backup snapshot of `.opencode/` | Yes — `make restore-opencode` rebuilds |
| `.claude/` | Claude Code hooks (PreToolUse guards), worktrees, settings | **NO** — Claude Code guardrails |
| `.devspark/` | DevSpark integration (JetBrains plugin) | Yes — integration data |
| `.gludd/` | Gludd daemon state: branches, local memory, replays, retrieval cache, service catalog | Yes — loses daemon state |
| `.integrity/` | Integrity/verification state | Yes — loses verification data |
| `.gate-logs/` | Background gate run logs | Yes — loses gate history |
| `.ansible/` | Ansible runtime cache | Yes — regenerates |
| `.ci-status` | Last CI status cache file | Yes — `make ci-verdict` regenerates |
| `node_modules/` | Node.js dependencies for OpenCode plugins | Yes — `npm install` rebuilds |

## `src/general_ludd/` — Main package

The core application (~137 modules). Everything the daemon, CLI, TUI, and workers need.

**Top-level modules:**

| File | Purpose |
|---|---|
| `daemon.py` | FastAPI daemon — lifespan, routes, middleware |
| `daemon_wiring.py` | Daemon dependency injection — wires all subsystems into the FastAPI app |
| `cli.py` | CLI entry point (Click) — 20+ subcommands |
| `smoke.py` | Smoke-test entry point — validates daemon boots cleanly |
| `output_templates.py` | Shared output formatting |
| `log_analyzer.py` | Log analysis entry point |
| `budget_guard_check.py` | Budget/rate-limit guard |
| `xml_utils.py` | XML parsing utilities |
| `web_utils.py` / `web_server_utils.py` | Web-server utility functions |
| `py.typed` | PEP 561 marker — declares this package supports type checking |

**Subpackage organization** (key areas only — 90+ subpackages):

| Subpackage | Purpose |
|---|---|
| `agents/` | Agent lifecycle, dispatch, coordination |
| `ansible/` | Ansible Runner adapter, playbook execution, paths |
| `approval/` | Human approval workflows for agent actions |
| `auth/` | Authentication and authorization |
| `cli_*.py` | CLI subcommand modules: `cli_account`, `cli_collection`, `cli_perm`, `cli_remediation`, etc. |
| `code_intelligence/` | Code analysis, AST tools |
| `commands/` | Command execution framework |
| `config/` | Configuration loading and management |
| `connectors/` | External service connectors |
| `coordination/` | Agent coordination and task distribution |
| `db/` | SQLAlchemy models, Alembic migrations, repository layer |
| `dispatch/` | Subagent dispatch and management |
| `dogfood/` | Self-hosting / dogfooding infrastructure |
| `entity/` | Entity models and business logic |
| `eval/` | Agent evaluation framework |
| `event_loop/` | Main event loop — schedules and runs agent tasks |
| `execution/` | Execution engine for agent actions |
| `git_automation/` | Git operations, locking, worktree management |
| `governance/` | Policy enforcement, compliance |
| `ipc/` | Inter-process communication |
| `langchain/` | LangChain integration (deprecated in favor of DSPy/AI SDK) |
| `language/` | Language model abstractions |
| `logging/` | Structured logging |
| `mcp/` | Model Context Protocol server/client |
| `memory/` | Memory index, retrieval, facts |
| `metrics/` | Metrics collection and export |
| `models/` | Pydantic models and schemas |
| `networking/` | Network utilities |
| `notifications/` | Notification channels |
| `observability/` | Observability — logs, metrics, traces |
| `orchestration/` | Orchestration engine — workflow state machine |
| `ornith/` | Ornithologist subsystem (self-improvement) |
| `permissions/` | Permission model and intersection evaluator |
| `physics/` | Physical resource management |
| `pipeline/` | CI/CD pipeline integration |
| `planning/` | Task planning and decomposition |
| `prompts/` | Prompt rendering (Jinja2 templates) |
| `quality/` | Code quality gates |
| `remediation/` | Blocker detection and remediation |
| `renderers/` | Output rendering (TUI, CLI, API) |
| `retrieval/` | Retrieval-augmented generation |
| `review/` | Code review and evidence checking |
| `routers/` | FastAPI route handlers |
| `runner/` | Task runner |
| `runtime/` | Runtime environment management |
| `sandbox/` | Code sandboxing |
| `scheduling/` | Task scheduling |
| `schemas/` | Data schemas |
| `secrets/` | Secret management (OpenBao) |
| `security/` | Security scanning and auditing |
| `self_improve/` | Self-improvement loops |
| `skills/` | Skill loading and execution |
| `ssl/` | SSL/TLS certificate management |
| `sts/` | Security Token Service |
| `system/` | System utilities |
| `templates/` | Template management |
| `tui/` | Terminal UI (Textual framework) |
| `validation/` | Input validation |
| `worker/` | Worker process (gunicorn/uvicorn) |
| `worktree/` | Git worktree management |
| `writer/` | Output writer |
| `abtest/` | A/B testing framework |
| `ag*_*/` | Agent generation subpackages (ag2, ag8, ag9, ag13, ag14, ag15, ag16) |

## `tests/` — Test suite

| Directory | Purpose | Files |
|---|---|---|
| `unit/` | Unit tests — one test file per source module | 200+ test files |
| `integration/` | Integration tests — 2+ subsystems together | ~20 test files |
| `e2e/` | End-to-end tests — through the daemon API | ~15 test files |
| `bench/` | Benchmark tests | ~5 test files |
| `connectors/` | Connector-specific tests | ~10 test files |
| `controllers/` | Controller-specific tests | ~10 test files |
| `infra/` | Infrastructure tests | ~5 test files |
| `install/` | Installation tests | ~5 test files |
| `live/` | Live/production tests | ~5 test files |
| `models/` | Model-specific tests | ~10 test files |
| `planning/` | Planning subsystem tests | ~5 test files |
| `routers/` | Route handler tests | ~10 test files |
| `security/` | Security-related tests | ~5 test files |
| `fixtures/` | Shared test fixtures | ~10 files |
| `conftest.py` | Pytest configuration and shared fixtures | **REQUIRED** |

## `config/` — Configuration

| Subdirectory | Purpose | Key files |
|---|---|---|
| `agents/` | Agent role definitions | `default_agents.yml` |
| `model_profiles/` | Model provider profiles (DeepSeek, Claude, Qwen, Llama, etc.) | 10 profile files |
| `prompt_profiles/` | Prompt variant profiles for different agent modes | Multiple profiles |
| `permissions/` | Permission specs: human roles, agent roles, subagent scope | `human-admin.yml`, `human-operator.yml`, `primary.yml`, `subagent.yml` |
| `opa/` | Open Policy Agent (OPA) rules for policy enforcement | `config_policy.rego`, test |
| `openbao/` | OpenBao secret engine config | `default.yml` |
| `examples/` | Example configs: agent, connector, security, LLM setups | 8 example files |
| `infra/` | Infrastructure config: Azure IAM, deployment optimization | 4 files |
| `skills/` | Skill definitions | Config files |
| `tasks/` | Task definitions | Config files |
| `mcp_servers/` | MCP server configurations | Config files |
| `ansible/` | Ansible-specific config | Config files |

**Root-level config files:**

| File | Purpose |
|---|---|
| `general-ludd.yml` | Main application config |
| `model_routing.yml` | Model routing rules |
| `ai_sdlc.yml` | AI SDLC workflow config |
| `binary_paths.yml` | Binary tool paths |
| `ratchet.yml` | Known-unfixed work ratchet — blocks premature stops |
| `tdd_allowlist.yml` | Files exempt from TDD enforcement |
| `coverage_gaps_baseline.json` | Known coverage gaps baseline |
| `dead_code_baseline.txt` | Known dead-code baseline for audit |
| `reconciled_preserved_heads.txt` | Preserved git heads after reconciliation |

## `scripts/` — Build, CI, and operational helpers

~200 scripts for build automation, CI, quality checks, and operational tooling.

**Key categories:**

| Purpose | Example scripts |
|---|---|
| CI/CD | `ci_await.py`, `ci_push_guard.py`, `ci_annotations_poll.py`, `ci_check_cooldown.py`, `gha_usage.py` |
| Quality gates | `check_tdd_compliance.py`, `check_dead_code.py`, `check_coverage_gaps.py`, `check_node_v26_compat.py`, `audit_coverage.py` |
| Enforcement | `verify_enforcement.py`, `verify_plugin_manifest.py`, `validate_plugins.py`, `validate_plugins_runtime.mjs` |
| Release | `verify_release_artifact.py`, `verify_release_completeness.py`, `require_ci_green.py`, `bump_version.py` |
| Git operations | `check_green_branch_guard.py`, `check_worktree_health.py`, `gated_merge.sh`, `ship_async.sh` |
| Daemon/monitoring | `agent_watchdog.py`, `agent_liveness.py`, `task_watchdog.py`, `pipeline_status.py` |
| Testing | `run_unit_shards.py`, `test_hook_runtime.py`, `collect_nodeids.py`, `junit_failures.py` |
| Plugin tooling | `fix_plugin_exports.py`, `check_plugin_health.py`, `list_plugins.py`, `lean_enforcement_plugins.py` |
| Dogfood | `dogfood.py`, `dogfood_features.py` |
| Disk/tmp | `clean_tmp.py`, `check_disk_usage.py`, `disk-guard.sh` |

## `docs/` — Documentation

~110 files covering architecture, design, runbooks, handoffs, and specifications.

| Subdirectory | Purpose |
|---|---|
| `design/` | Technical design documents |
| `architecture/` | Architecture decision records (ADRs) |
| `api/` | API documentation |
| `cli/` | CLI documentation |
| `configuration/` | Configuration guide |
| `deployment/` | Deployment guides |
| `development/` | Developer guides |
| `operations/` | Operational runbooks |
| `security/` | Security documentation |
| `presentation/` | Reveal.js presentation decks |
| `research/` | Research notes and investigations |
| `specs/` | Spec-driven development task specs |
| `archive/` | Archived old docs |
| `audit/` | Audit reports |
| `gludd_porting/` | Claude Code → OpenCode porting docs |
| `guides/` | How-to guides |
| `examples/` | Usage examples |
| `integration/` | Integration docs |
| `internal/` | Internal/team docs |
| `e2e_harness/` | E2E test harness docs |

**Key standalone docs:**

| File | Purpose |
|---|---|
| `RELEASE_RUNBOOK.md` | Step-by-step release procedure |
| `RELEASE_CHECKLIST.md` | Pre-release checklist |
| `ORCHESTRATION.md` | Subagent orchestration model |
| `MULTITASKING_POLICY.md` | Multitasking/parallel dispatch policy |
| `ENFORCEMENT_PLUGINS.md` | Enforcement plugin reference |
| `GATE_LITE.md` | Gate-lite (fast local validation) docs |
| `index.md` | Documentation index |
| `CONFIGURATION_GUIDE.md` | Full configuration reference |
| `architecture.md` | High-level architecture overview |
| `SMOKE_TESTS.md` | Smoke test documentation |

## Infrastructure & operations

### `infra/`

| Subdirectory | Purpose | Key files |
|---|---|---|
| `terraform/` | Terraform IaC — declarative infrastructure | Stack definitions, provider config |
| `kubernetes/` | Kubernetes manifests | Deployment, service, configmap YAML |
| `local-models/` | Local model serving config (vLLM, llama.cpp) | Config files |
| `searxng/` | SearXNG search engine config | `settings.yml`, `uwsgi.ini` |
| `slurm/` | Slurm HPC cluster config | Job scripts |

### `.github/workflows/`

| Workflow | Purpose |
|---|---|
| `build.yml` | Primary CI pipeline: lint, typecheck, test (sharded), build platform binaries, release, pages deploy |
| `molecule.yml` | Ansible molecule tests |
| `pages.yml` | GitHub Pages deployment (presentation decks) |

### `alembic/`

| File | Purpose |
|---|---|
| `env.py` | Alembic migration environment config |
| `script.py.mako` | Migration template |
| `versions/` | Migration scripts (sequential, chain-linked) |

## Integration & tool dirs

### `.opencode/`

| Subdirectory | Purpose |
|---|---|
| `plugin/` + `impl/` | **30 enforcement plugins** (TypeScript) — enforce-floor, enforce-stop, enforce-make, enforce-tdd, etc.  Node v26 `--experimental-strip-types` compatible |
| `plugins/` | Additional OpenCode plugins |
| `agent/` | Agent definitions for OpenCode |
| `skill/` | Built-in skills (`deep-spec/`) |
| `skills/` | Project skills: `background-test-runner`, `enforce-bootstrap`, `guardrail-pattern`, `revealjs-presentation`, `test-quality`, `type-safety` |
| `lib/` | Shared TypeScript library for plugins |
| `node_modules/` | Node.js dependencies for plugin runtime |
| `MAKE_TARGETS.md` | Make target reference for OpenCode |
| `plugin-hashes.json` | Plugin integrity hashes |

### `.claude/`

| Subdirectory | Purpose |
|---|---|
| `hooks/` | Claude Code PreToolUse/PostToolUse shell hooks (23 scripts) |
| `workflows/` | Claude Code workflow definitions |
| `worktrees/` | Git worktrees for isolated subagent work |
| `settings.json` / `settings.local.json` | Hook registration and override |
| `sonnet_ratio_target` | Model utilization ratio target |
| `main_model` | Default model selection |

### `.devspark/`

JetBrains DevSpark plugin integration data. **Generated — safe to delete.**

### `.gludd/`

Persistent daemon state directory:

| Entry | Purpose |
|---|---|
| `branches/` | Branch metadata |
| `git_history.db` | SQLite index of git history (`make git-index`) |
| `local_memory/` | Local memory store |
| `replays/` + `replays-structural-test-N/` | Agent replay recordings |
| `retrieval_cache/` | RAG retrieval cache |
| `searx_cache/` | SearXNG search result cache |
| `service_catalog.yml` | Service discovery catalog |

## Ansible ecosystem

| Directory | Purpose | Key files |
|---|---|---|
| `collections/` | Ansible collections (project precedence over user/bundled) | `ansible_collections/general_ludd/` |
| `playbooks/` | 56 Ansible playbooks — orchestration, CI, git, quality, security, self-improvement, infrastructure | `project_init.yml`, `quality_gate_validate.yml`, `agent_orchestrate.yml`, etc. |
| `roles/` | Ansible roles directory (`.gitkeep` — roles live in `collections/`) | Placeholder |
| `plugins/` | Ansible plugins: `module_utils/` | Library modules |
| `templates/` | Jinja2 prompt and log templates | `prompts/` + `log_output/` |
| `molecule/` | Molecule test scenarios: `internal_tools/`, `mock_daemon/`, `playbooks/`, `prompt_eval/`, `roles/` | Scenario dirs |

## Build & tools

| Directory | Purpose | Safe to delete? |
|---|---|---|
| `build/` | PyInstaller build artifacts | Yes — `make build-executable` rebuilds |
| `dist/` | Distribution outputs (tarballs, executables) | Yes — `make dist` rebuilds |
| `node_modules/` | Node.js dependencies for OpenCode plugins | Yes — `npm install` rebuilds |
| `tools/` | Development/QA tools: `ansible_lint_rules/`, molecule coverage checker, quality gate checker | **REQUIRED** — tools |
| `web_retriever/` | Web content retrieval tool | **REQUIRED** — RAG data source |
| `demos/` | Demo scripts and presentations | **REQUIRED** — reference |
| `.integrity/` | Integrity/verification state data | **REQUIRED** — verification |

## Quick navigation

**"I want to..."**

| Scenario | Go to |
|---|---|
| Understand the build system | `Makefile` (run `make help`) |
| Find a policy/rule | `AGENTS.md` — search for the section heading |
| See what work is pending | `TASKS.md` — unchecked `[ ]` items |
| See what bugs exist | `BUGS.md` |
| Run the test suite | `make test` or `tests/` |
| Find a specific test | `tests/unit/test_<module>.py` |
| Check CI status | `.github/workflows/build.yml` |
| Fix a lint error | `pyproject.toml` — ruff config |
| Fix a type error | `pyproject.toml` — mypy config; `src/general_ludd/py.typed` |
| Add a model provider | `config/model_profiles/` |
| Add an agent role | `config/agents/default_agents.yml` |
| Add a permission | `config/permissions/` |
| Add a playbook | `playbooks/` |
| Add a CI check | `scripts/` + `Makefile` target |
| Add an enforcement rule | `.opencode/plugin/` — TypeScript plugin |
| Add a Claude Code guard | `.claude/hooks/` — shell script |
| Add a skill | `.opencode/skills/` |
| Add a DB migration | `alembic/versions/` |
| Read architectural decisions | `docs/architecture/` |
| Cut a release | `docs/RELEASE_RUNBOOK.md` + `make release-cut` |
| Understand orchestration | `docs/ORCHESTRATION.md` |
| Find a config example | `config/examples/` |
| Check infrastructure as code | `infra/terraform/` |
| See available make targets | `make help` (or `scripts/list_make_targets.py`) |
| Understand the enforcement layer | `.opencode/plugin/` + `docs/ENFORCEMENT_PLUGINS.md` |
| Find the daemon entry point | `src/general_ludd/daemon.py` |
| Find the CLI entry point | `src/general_ludd/cli.py` |
| Add a CLI command | `src/general_ludd/cli_*.py` |
| Inspect DB schema | `src/general_ludd/db/models.py` + `alembic/versions/` |
| Check for secrets | `make secrets-scan` |
| Verify plugin health | `make verify-enforcement` |
| Find presentation source | `docs/presentation/` |
| Find research notes | `docs/research/` |
