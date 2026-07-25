# gludd Directory Structure

## Quick Navigation

| I want to... | Go to... |
|---|---|
| Add a new feature | `src/general_ludd/`, `tests/unit/` |
| Add a playbook | `playbooks/` |
| Configure a model | `config/model_profiles/` |
| Add a CI check | `.github/workflows/build.yml` |
| Add a molecule test | `molecule/playbooks/` |
| Add documentation | `docs/` |
| Fix enforcement | `.opencode/plugin/` |
| Add a DB migration | `alembic/versions/` |
| Deploy infrastructure | `infra/terraform/stacks/` |
| Add an Ansible role | `collections/` or `roles/` |
| Configure permissions | `config/permissions/` |
| Configure OPA policy | `config/opa/` |
| Add a prompt template | `templates/prompts/` |
| Add a agent definition | `config/agents/` |
| Add a skill | `.opencode/skills/` |
| Add a Claude hook | `.claude/hooks/` |
| Fix a make target | `Makefile` |
| Add a build script | `scripts/` |
| Add unit tests | `tests/unit/` |
| Add integration tests | `tests/integration/` |
| Add end-to-end tests | `tests/e2e/` |
| Configure MCP servers | `config/mcp_servers/` |
| Deploy local models | `infra/local-models/` |
| Manage k8s deployments | `infra/kubernetes/` |
| Deploy via SLURM | `infra/slurm/` |
| Add a demo | `demos/` |

---

## Root Files

| File | Purpose |
|---|---|
| `Makefile` | Build, test, CI, git automation (~5300 lines) |
| `pyproject.toml` | Python project config + dependencies |
| `gludd.spec` | PyInstaller spec for binary builds |
| `uv.lock` | uv dependency lockfile |
| `opencode.json` | Opencode plugin config + permission rules |
| `AGENTS.md` | Agent behavior rules (~3300 lines) |
| `CLAUDE.md` | Claude-specific agent rules |
| `TASKS.md` | Task ledger with evidence (~3300 lines) |
| `BUGS.md` | Process bug tracker |
| `SESSION.md` | Session state + release history |
| `README.md` | Project overview + config guide |
| `CHANGELOG.md` | Release changelog |
| `LICENSE` | MIT license |
| `SECURITY.md` | Security policy |
| `CONTRIBUTING.md` | Contribution guide |
| `THIRD_PARTY_LICENSES.md` | Third-party license notices |
| `Containerfile` / `Dockerfile` | Container image build |
| `Makefile.pushwait` | CI push-wait helper (scripts/) |
| `.secrets.baseline` | detect-secrets baseline |
| `.pre-commit-config.yaml` | Pre-commit hook configuration |
| `.gitignore` | Git ignore rules |
| `.gitmodules` | Git submodule definitions |
| `.gitattributes` | Git attribute rules |
| `.mcp.json` | MCP server manifest |
| `ansible.cfg` | Ansible configuration |
| `alembic.ini` | DB migration config |
| `messages.pot` | Gettext translation template (locale/) |
| `coverage.json` | Coverage data export (build/) |
| `project.yml` | Project-level config |
| `sandboxcom_github_rsa` | SSH key for sandboxcom GitHub remote |

---

## Directories

### `src/general_ludd/` — Main Python Package

The core application package containing daemon, CLI, and all subsystems.

| Key file | Purpose |
|---|---|
| `daemon.py` | FastAPI daemon entry point |
| `daemon_wiring.py` | Daemon dependency wiring |
| `cli.py` | CLI entry point (Click-based) |
| `output_templates.py` | Output format templates |
| `smoke.py` | Quick daemon boot health check |

| Sub-package | Purpose |
|---|---|
| `abtest/` | A/B testing framework for model variants |
| `account/` | User account management |
| `accounting/` | Cost accounting and billing |
| `agents/` | Agent definitions and test-generation agents |
| `ansible/` | Ansible Runner adapter and path resolution |
| `approval/` | Approval gate for agent actions |
| `auth/` | Authentication (OIDC, STS) |
| `benchmark/` | Model benchmarking |
| `budget/` | Budget envelope, credit tracking, combined cost |
| `business/` | Business logic and rules engine |
| `chat/` | Chat interface and formatters |
| `code_intelligence/` | Code analysis and callgraph |
| `collections/` | Ansible collection management |
| `commands/` | CLI command dispatching |
| `compaction/` | Context compaction strategies |
| `compat/` | Compatibility shims |
| `config/` | Configuration loading and validation |
| `connectors/` | External service connectors (Trello, InfluxDB, etc.) |
| `controllers/` | Pause controller and state machines |
| `coordination/` | Agent coordination primitives |
| `db/` | SQLAlchemy models and database access |
| `dependency/` | Dependency update management |
| `dispatch/` | Agent dispatch and routing |
| `dogfood/` | Dogfood sprint parser, runner, validator |
| `entity/` | Entity extraction and research |
| `eval/` | Evaluation harness |
| `event_loop/` | Background event loop |
| `events/` | Event schema and helpers |
| `execution/` | Sandboxed code execution |
| `filestore/` | File storage (S3, local) |
| `git_automation/` | Git repo locking and automation |
| `governance/` | Policy governance |
| `hardware/` | Hardware detection and management |
| `history/` | Git history indexing |
| `infra/` | Infrastructure provisioning |
| `integration/` | External integration adapters |
| `integrity/` | File integrity hashing |
| `ipc/` | Inter-process communication queues |
| `issue_sources/` | Issue tracking source adapters |
| `langchain/` | LangChain/LangGraph integration |
| `language/` | Language and i18n support |
| `log_analysis/` | Log analysis and prompt evaluation |
| `logging/` | Structured logging |
| `mcp/` | MCP (Model Context Protocol) integration |
| `memory/` | Memory store (local SQLite) |
| `metrics/` | Metrics collection and cardinality |
| `model_weights/` | Model weight management |
| `models/` | Shared data models |
| `networking/` | Network configuration and checks |
| `notifications/` | Push and event notifications |
| `observability/` | Tracing and observability |
| `observe/` | Live system observation |
| `onboard/` | Cloud provider onboarding (AWS, Azure, GCP) |
| `orchestration/` | Agent orchestration engine |
| `ornith/` | Ornith self-improvement system |
| `os_expert/` | OS-level diagnostics |
| `permissions/` | Permission system |
| `physics/` | Physics simulation and analysis |
| `pipeline/` | CI pipeline controller and state |
| `planning/` | Task planning and critiquing |
| `pricing_intel/` | Model pricing intelligence |
| `process/` | Process registry and management |
| `project_runner/` | Project execution runner |
| `projects/` | Multi-project management |
| `prompts/` | Prompt management and assembly |
| `quality/` | Quality gate checks |
| `receiver/` | Webhook and event receiver |
| `reload/` | Hot-reload mechanism |
| `remediation/` | Blocker detector, dispatcher, reporter |
| `renderers/` | Schema rendering and execution |
| `replay/` | Event replay system |
| `retrieval/` | RAG retrieval pipeline |
| `review/` | Code review and evidence checking |
| `routers/` | FastAPI route definitions |
| `routing_roles/` | Model routing role definitions |
| `rules/` | Business rules engine |
| `runner/` | Playbook and task runner |
| `runtime/` | Runtime validation and packaging |
| `sandbox/` | Sandbox execution environments |
| `sandbox_exec/` | Sandbox code execution |
| `scheduling/` | Task scheduling |
| `schemas/` | JSON/API schemas |
| `scoring/` | Model scoring and routing |
| `searx/` | SearXNG search integration |
| `secrets/` | Secrets management (OpenBao) |
| `security/` | Security audits, sandboxes, fix-not-disable |
| `self_improve/` | Self-improvement strategies |
| `self_update/` | Self-update mechanism |
| `service_discovery/` | Service discovery |
| `skills/` | Skill system |
| `ssl/` | SSL certificate management, compliance, HSM |
| `ssl_agent/` | SSL agent orchestration |
| `stream/` | Stream processing |
| `sts/` | Short-term credentials (minter, revoker, rotator, injector, reaper) |
| `system/` | System utilities |
| `templates/` | Template rendering |
| `tui/` | Text-based UI (breadcrumb, views) |
| `validation/` | Gap analysis, backlog audit, log audit |
| `worker/` | Worker (task execution) |
| `worktree/` | Git worktree management |
| `writer/` | Writer supervisor |

### `tests/` — Test Suite

| Directory | Purpose |
|---|---|
| `tests/unit/` | Unit tests (~800+ files) |
| `tests/unit/sts/` | STS-specific unit tests |
| `tests/integration/` | Integration tests (2+ subsystems) |
| `tests/integration/sts/` | STS integration tests |
| `tests/e2e/` | End-to-end tests (daemon API) |
| `tests/e2e/dogfood/` | Dogfood end-to-end tests |
| `tests/e2e/providers/` | Provider end-to-end tests |
| `tests/e2e/games/` | Game building end-to-end tests |
| `tests/controllers/` | Controller-specific tests |
| `tests/fixtures/` | Test fixtures (external pyproject, etc.) |

### `config/` — Configuration Files

| File / Directory | Purpose |
|---|---|
| `general-ludd.yml` | Main application configuration |
| `project.yml` | Project-level settings |
| `ai_sdlc.yml` | AI SDLC configuration |
| `model_routing.yml` | Model routing rules |
| `binary_paths.yml` | External binary paths |
| `ratchet.yml` | Known-unfixed work ratchet |
| `tdd_allowlist.yml` | TDD compliance allowlist |
| `agents/` | Agent definitions |
| `model_profiles/` | AI model provider configs (Anthropic, OpenAI, DeepSeek, Qwen, etc.) |
| `permissions/` | Permission specs (human roles, agent roles, build, subagent) |
| `opa/` | OPA policy + tests (IAM, config, Terraform) |
| `infra/` | Infrastructure + IAM config (AWS, Azure, GCP) |
| `examples/` | Example config files (agent, connectors, memory, user) |
| `skills/` | Skill instruction files |
| `tasks/` | Example task definitions |
| `ansible/` | Ansible isolation configuration |
| `openbao/` | OpenBao default configuration |
| `prompt_profiles/` | Agent prompt profiles |
| `mcp_servers/` | MCP server configurations |

### `.opencode/` — Opencode Integration

| Directory | Purpose |
|---|---|
| `.opencode/plugin/` | Enforcement plugins (24 .ts files) |
| `.opencode/plugin/impl/` | Plugin implementation modules |
| `.opencode/skills/` | Skill definitions (guardrail-pattern, test-quality, type-safety, etc.) |
| `.opencode.orig/` | Backup of .opencode/ |

### `.claude/` — Claude Code Integration

| File / Directory | Purpose |
|---|---|
| `.claude/hooks/` | 23 shell hooks (PreToolUse, PostToolUse, Stop) |
| `.claude/workflows/` | Claude workflow scripts |
| `.claude/settings.json` | Hook registration and settings |
| `.claude/settings.local.json` | Local overrides |

### `.github/workflows/` — CI/CD

| File | Purpose |
|---|---|
| `build.yml` | Main CI workflow (gate, build, release) |

### `alembic/` — Database Migrations

| Directory | Purpose |
|---|---|
| `alembic/versions/` | 36+ sequential migration scripts |

### `docs/` — Documentation

| Directory | Purpose |
|---|---|
| `docs/design/` | Design documents and specs |
| `docs/audit/` | Audit reports and findings |
| `docs/research/` | Research notes (model routing, RAG, memory) |
| `docs/guides/` | Implementation and remediation guides |
| `docs/api/` | API documentation |
| `docs/examples/` | Example files |
| `docs/deployment/` | Deployment guides |
| `docs/presentation/` | Reveal.js presentation deck |
| `docs/profiles/` | Profile documentation |
| `docs/gludd_porting/` | Porting guides for harness guardrails |
| `docs/e2e_harness/` | E2E harness design docs |

### `scripts/` — Build and CI Helpers

100+ scripts for linting, testing, coverage, CI polling, plugin diagnostics, release verification, git automation, and more.

### `playbooks/` — Ansible Playbooks

50+ playbooks for agent orchestration, model deployment, gap analysis, quality gates, prompt evaluation, secrets management, and more.

### `molecule/` — Molecule Test Scenarios

Molecule test suites for Ansible roles and playbooks, including role-level tests (agent_task, audit_security, dependency_update, etc.) and integration tests (facts, accounting, message, etc.).

### `collections/` — Ansible Collections

| Collection | Purpose |
|---|---|
| `general_ludd/agent/` | Agent roles (implement_change, build_presentation, enforce_disengage, ci_annotations_poll) |
| `general_ludd/web_server/` | Web server roles (reverse_proxy, http_server, forward_proxy, security_hardening, cgi_wsgi) |
| `general_ludd/radio/` | Radio/signal roles (spectrum_scan, link_budget, regulation_lookup) + plugin_utils |
| `general_ludd/binary_re/` | Binary reverse engineering roles (cyberchef_transform) |

### `infra/` — Infrastructure as Code

| Directory | Purpose |
|---|---|
| `infra/terraform/` | Terraform IaC (modules, stacks, policies, examples) |
| `infra/terraform/stacks/` | Deployable stacks (AWS, Azure, GCP, Kubernetes, QEMU, RunPod, Vast, vSphere) |
| `infra/terraform/modules/` | Reusable modules (llamacpp-server, vllm-server, network, kubernetes-deploy, onboard-iam, qemu-vm, gpu-cost-watchdog) |
| `infra/terraform/examples/` | Example .tfvars files |
| `infra/terraform/policies/` | OPA/Rego policy checks |
| `infra/local-models/` | Local model deployment (ollama, vllm, llamacpp via Docker Compose) |
| `infra/kubernetes/` | Kubernetes deployment manifests |
| `infra/slurm/` | SLURM job scripts |
| `infra/searxng/` | SearXNG deployment config |

### `templates/` — Jinja2 Templates

| Directory | Purpose |
|---|---|
| `templates/prompts/` | Jinja2 prompt templates (code_review, gap_analysis, implementation, test_creation, self_improvement, etc.) |
| `templates/prompts/partials/` | Reusable prompt partials |
| `templates/log_output/` | Log output format templates (smoke traces, reports) |

### Other Directories

| Directory | Purpose |
|---|---|
| `roles/` | Top-level Ansible roles directory |
| `plugins/` | Ansible plugin_utils (model_analysis) |
| `tools/` | Quality gate and molecule coverage checkers |
| `demos/` | Demo scripts (NF features demo) |
| `dist/` | Built artifacts (standalone binary) |
| `build/` | PyInstaller build output |
| `.gate-logs/` | Background gate run output logs |
| `.gludd/` | Project state (service catalog, replays, cache DBs, variant metrics, git history index) |
| `.gludd/local_memory/` | Local memory SQLite cache |
| `.gludd/retrieval_cache/` | Retrieval cache SQLite |
| `.gludd/searx_cache/` | SearXNG search cache SQLite |
| `.gludd/replays/` | Event replay data |
| `.integrity/` | File integrity hashes |
| `.ansible/` | Ansible runtime data |
| `.devspark/` | DevSpark integration |
| `web_retriever/` | Web search integration |

### Hidden / Build Artifact Directories (not committed)

| Directory | Purpose |
|---|---|
| `.venv/` | Python virtual environment |
| `.mypy_cache/` | MyPy type checking cache |
| `.ruff_cache/` | Ruff lint cache |
| `.pytest_cache/` | Pytest cache |
| `node_modules/` | Node.js dependencies (for plugins) |
| `__pycache__/` | Python bytecode cache |
