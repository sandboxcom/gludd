# general_ludd.agent Ansible Collection

Ansible collection for the `general_ludd` agentic SDLC harness.

## Modules

| Module | Description |
|---|---|
| `gludd_ping` | Verify daemon reachability |
| `gludd_model_call` | Run a model generation via the daemon API |
| `gludd_worktree` | Manage git worktrees (present/absent) |
| `gludd_git` | Git operations (commit/branch) |
| `gludd_db` | Todo/resource CRUD via daemon API |
| `gludd_skill` | Render a skill with Jinja2 variables |
| `gludd_mcp_tool` | Invoke an MCP tool (see note below) |

## Roles

| Role | Description |
|---|---|
| `accounting_report` | Fetch per-project accounting; write cost/time/LoC/role/todo report artifact (report-only). |
| `agent_orchestrate` | Env-fact-driven orchestration: read advice, branch to LangGraph workflow or single-shot model call. |
| `agent_task` | Full agent task: db-read → worktree → skill-render → agent-run → quality-gate → git-commit → db-write. Cleans up worktree on failure. |
| `ai_parallel_dispatch` | Dispatch a batch of AI sub-tasks concurrently via the daemon. |
| `audit_dependencies` | Outdated/vulnerable dependency audit (report-only). Analyzes dependency manifests for outdated packages, CVEs, and license compliance. NEVER commits, pushes, or mutates the repo. |
| `audit_security` | Security scan oriented audit (report-only). Checks for hardcoded secrets, injection vulnerabilities, path traversal, auth gaps via model analysis. NEVER commits, pushes, or mutates the repo. |
| `backlog_groom` | Prioritizes, estimates, and flags backlog todos from gludd_facts.todos. Flags oversized stories and under-specified items. REPORT-ONLY by default: never mutates the repo. |
| `budget_guard` | Check remaining run budget; defer/abort when below threshold. |
| `ci_pipeline_repair` | Inspect .github/workflows + version/build config for common CI breakage; emit findings + suggested fixes. Report-only by default. |
| `code_reviewer` | Structured correctness/style/risk review of a diff or repo (NOT security). Verdict: approve\|comment\|request_changes. NEVER mutates the repo. |
| `cost_optimization_auditor` | Analyzes model/token/compute costs from gludd.metrics and gludd.traces; emits ranked savings recommendations. NEVER mutates the repo. |
| `dead_code_auditor` | Surfaces unreferenced code by composing an existing completion/coverage audit artifact. NEVER mutates the repo. |
| `debug_failure` | Analyze a failing test or task return and propose a fix; hands diagnosis off to implement_change. |
| `dependency_update` | Analyze dependencies for outdated packages and vulnerabilities. Report-only by default. |
| `deploy_model_server_slurm` | Deploy a model server onto a Slurm cluster. |
| `document_change` | Generate documentation for a code change via model agent; writes to artifact dir (optionally repo). |
| `dry_code_auditor` | Detects code duplication using jscpd output; reports clone pairs and extraction suggestions. NEVER mutates the repo. |
| `enhancement_auditor` | Proposes ranked enhancements based on gludd_facts history failure signals. NEVER mutates the repo. |
| `estimate_story` | Assigns Fibonacci story points from complexity heuristics calibrated by historical velocity. REPORT-ONLY: never mutates the repo. |
| `feature_audit` | Audit a feature for completeness across CLI/daemon/TUI/interfaces. |
| `feature_gap_auditor` | Diffs intended features (from docs/specs) vs implemented features. NEVER mutates the repo. |
| `flaky_quarantine` | Produce the ratchet/pytest marker change recommendation with evidence requirements for flaky tests. Report-only by default. |
| `gate_triage` | Run the quality gate, parse pass/fail, classify failures as flaky vs real, emit structured artifact. |
| `gludd_update` | Operator self-update surface: turns an "update gludd" request into a prioritized todo. SAFE-BY-DEFAULT. |
| `implement_change` | Apply a model-generated code change in an isolated worktree; commits and cleans up. |
| `issue_reporter` | Converts detected anomalies/findings into structured todos/issues. NEVER mutates the repo. |
| `langgraph_decision` | Drive a LangGraph decision flow and surface the chosen branch. |
| `lint_and_check` | Run linters/checkers and write a status artifact. |
| `log_analyst` | Analyses logs/traces for anomalies: error-line density, per-phase failure rates, cost/token outliers. NEVER mutates the repo. |
| `manage_processes` | Enumerate, monitor, and signal gludd-managed OS processes. Signalling is dry_run by default. |
| `molecule_self_test` | Run molecule tests on gludd's own roles/collections; emit coverage artifact. Enable gated by default. |
| `observe_deploy_correlator` | Correlate deploy events with metric/error signals (observability). |
| `observe_error_spike_rca` | Root-cause an error spike from observability data. |
| `observe_incident_triage` | Triage an incident: gather signals, classify severity. |
| `observe_latency_regression` | Detect and RCA a latency regression. |
| `observe_saturation_capacity` | Report saturation/capacity headroom from metrics. |
| `observe_security_signal` | Triage a security signal from observability feeds. |
| `openbao_break_glass_backup` | OpenBao break-glass encrypted backup: snapshots the raft store, GPG-encrypts, prunes old backups. |
| `parallel_planner` | Compute a concurrency-safe execution plan via the scheduler (plan-only). |
| `project_init` | Scaffold a project-specific ansible collection under <project_dir>/.gludd/collections/. |
| `qa_analyst` | Cross-cut quality verdict: slurps test_matrix/coverage/flaky and computes weighted QA score. NEVER mutates the repo. |
| `refactor_code` | Model-driven code refactoring in an isolated worktree; commits and cleans up. |
| `release_build` | Stamp a PEP 440 timestamped-alpha version, optionally build the release artifact, and verify. |
| `report_audit` | Consolidates audit role outputs (audit_security, audit_dependencies) into one unified report. NEVER mutates the repo. |
| `report_metrics` | Model usage, success/failure rates, and throughput metrics from live gludd_facts. NEVER mutates the repo. |
| `report_status` | Renders a status report from gludd_facts; classifies system health (healthy/degraded/critical). NEVER mutates the repo. |
| `retrospective` | What-went-well / what-went-ill / actions from gludd_facts and gludd_message. REPORT-ONLY. |
| `run_tests` | Run a project test command; write rc/stdout/status JSON artifact. |
| `sbom_generate` | Produces a CycloneDX SBOM using syft. NEVER mutates the repo. |
| `scrum_leader` | Pure composer over agile planning ceremony roles (backlog_groom, sprint_plan, standup_report, retrospective). NEVER mutates the repo. |
| `secret_scan` | Wraps detect-secrets/gitleaks for secret scanning. Report-only. NEVER mutates the repo. |
| `security_gate` | Composing fail-closed security gate: passes only if all required checks present and no finding ≥ block_on_severity. |
| `security_requirements` | Derives security acceptance criteria (authn/authz, input validation, secrets handling, logging) for a story. REPORT-ONLY by default. |
| `security_review` | Reviews a code change or diff for insecure patterns (real grep). Emits gludd_message on findings. NEVER mutates the repo. |
| `self_improve_ab_test` | Run an A/B comparison between two self-improvement candidates. |
| `self_improve_promote` | Promote a winning self-improvement proposal into the baseline. |
| `self_improve_propose` | Propose a self-improvement change with rationale. |
| `soc_analyst` | Security Operations triage: slurps finding artifacts, correlates/dedupes, escalates by count, produces prioritized incident list. NEVER mutates the repo. |
| `sprint_board_report` | Board state grouped by status from gludd_facts.todos/work. REPORT-ONLY. |
| `sprint_plan` | Selects backlog todos into a sprint by capacity vs velocity. REPORT-ONLY by default. |
| `sprint_review` | Completed-work demo summary from gludd_facts.history + traces. REPORT-ONLY. |
| `standup_report` | Yesterday/today/blockers standup from gludd_facts and gludd_message. REPORT-ONLY. |
| `story_create` | Converts a free-form feature request into a structured user story with acceptance_criteria. REPORT-ONLY by default. |
| `stream_input_key_before` | Stream input-key chunk handler (before-key segment). |
| `stream_input_key_both` | Stream input-key chunk handler (before+after key segments). |
| `supply_chain_verify` | Cosign verify signatures/provenance + SLSA attestation. FAIL-CLOSED. NEVER mutates the repo. |
| `threat_model` | STRIDE threat enumeration over a design document + live attack surface from gludd_facts. NEVER mutates the repo. |
| `tool_dispatch` | Dispatch a tool call through the daemon tool router. |
| `triage_issue` | Turn an inbound issue/todo into a structured plan; hands off to downstream role via message queue. |
| `ui_ux_analyst` | Heuristic TUI/CLI/API ergonomics analysis (non-authoritative). NEVER mutates the repo. |
| `validate_and_push` | Run full validation then, only with explicit enable_push=true and passing validation, push to remote. Safe-by-default. |
| `velocity_report` | Points/throughput over recent history; composes report_metrics data. REPORT-ONLY. |
| `write_tests` | Generate and place tests via model agent, then run them via the configured test command. |

## Constrained and locally served models

Model size, price, provider, and a `local` or `weak` profile label do not grant a
role. Before dispatching work to a constrained model, collection callers must
use `general_ludd.routing_roles.SmallModelTaskPolicy` with proof for the exact
task kind, routing role, collection, model/runtime/prompt identity, and
acceptance contract. A result is usable only after `record_completion()` returns
`accept`; retry and escalation decisions must be honored.

The default eligible outputs are artifact-only context compaction, bounded
enumeration, schema extraction, format normalization, documentation drafting,
and failure classification. Roles that mutate a repository, execute tools,
write to a network, use credentials, deploy, release, or make a security
decision are not eligible. In particular, `agent_task`, `implement_change`,
`refactor_code`, `write_tests`, `validate_and_push`, deployment roles, and
release roles require a stronger authorized model for the side-effecting step.

See
[`docs/design/SMALL_MODEL_TASK_POLICY.md`](../../../../docs/design/SMALL_MODEL_TASK_POLICY.md)
for the task matrix, proof schema, local-suite requirements, retry/dedupe rules,
role prompt envelope, upstream user reports, and ZDD rollout procedure.

## Authentication

All modules that contact the daemon accept `daemon_url` and `psk` parameters.
`psk` is never logged (`no_log: true`).

Set `GLUDD_AUTH_PSK` in the environment — the module_utils shim reads it automatically.

## Note on MCP

`gludd_mcp_tool` returns `not_implemented` per the W3.9 decision (MCP is
honestly fenced until the protocol wiring is completed).
