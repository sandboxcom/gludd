# Sprint 0 implementation and operations

This shard preserves the model, prompt, human-interaction, observability, secrets, Git, reload, packaging, testing, sprint-board, dogfood, risk, bibliography, and living-note material routed from [`sprint0.md`](../sprint0.md).

## 15. Model Gateway And Model Profiles

LangChain is the primary model abstraction. Support every provider that LangChain documents by using provider packages, dynamic configuration, and automatic dependency-update todos.

Model profile fields:

```text
model_profile_id
role_names: list of roles this profile can serve
provider
provider_package
provider_class_hint
model_name
api_base_alias
credential_alias
context_window
max_input_tokens
max_output_tokens
cost_per_input_token
cost_per_output_token
subscription_window_tokens
subscription_window_seconds
local_resource_limits
supports_tool_calling
supports_json_schema
supports_streaming
supports_reasoning_effort
latency_class
quality_class
risk_allowed
resource_profile
fallback_profiles
enabled
probe_enabled
```

Role-to-model routing is configurable:

```yaml
model_roles:
  return_review: strong_configured_model
  implementation: code_configured_model
  test_creation: code_configured_model
  gap_analysis: long_context_configured_model
  log_audit: audit_configured_model
  prompt_eval: strong_configured_model
  self_improvement_review: independent_strong_configured_model
```

Model gateway responsibilities:

[ ] Load model profile.
[ ] Ensure provider package is installed or create dependency update todo.
[ ] Resolve credential alias from OpenBao when needed.
[ ] Enforce budget before call.
[ ] Render prompt.
[ ] Call model through LangChain.
[ ] Validate structured output.
[ ] Record usage and cost metadata.
[ ] Redact logs.
[ ] Emit provider health.
[ ] Fall back only when configured.

No surprises policy:

[ ] If a model profile is not configured, it does not exist.
[ ] If local model endpoint is not configured, do not use local models.
[ ] If provider credentials are missing, create configuration todo and continue unrelated work.
[ ] If package is missing, run dependency update workflow automatically.

### 15.1 Example: OpenAI API Profile

This example intentionally uses placeholders for model names. Replace them with model IDs available to the configured account.

```yaml
model_profiles:
  openai_strong:
    provider: openai
    provider_package: langchain-openai
    provider_class_hint: ChatOpenAI
    model_name: "configured-openai-model-id"
    credential_alias: openbao://kv/model/openai/api_key
    api_base_alias: null
    roles: [return_review, self_improvement_review, architecture]
    resource_profile: ai_heavy
    api_metered: true
    run_budget_usd: 200
    enabled: true
```

### 15.2 Example: OpenRouter Profile

```yaml
model_profiles:
  openrouter_code:
    provider: openrouter
    provider_package: langchain-openrouter
    provider_class_hint: ChatOpenRouter
    model_name: "configured-openrouter-model-id"
    credential_alias: openbao://kv/model/openrouter/api_key
    roles: [implementation, test_creation, debug]
    resource_profile: ai_heavy
    api_metered: true
    run_budget_usd: 200
    enabled: true
```

### 15.3 Example: llama.cpp Local Endpoint Profile

No model artifact is downloaded by the harness. This only uses a server the user configured.

```yaml
model_profiles:
  llamacpp_local_configured:
    provider: openai_compatible
    provider_package: langchain-openai
    provider_class_hint: ChatOpenAI
    model_name: "configured-local-llamacpp-model-name"
    api_base_alias: openbao://kv/model/llamacpp/base_url
    credential_alias: openbao://kv/model/llamacpp/api_key_optional
    roles: [summarization, low_risk_analysis]
    resource_profile: local_heavy
    local_resource_limits:
      max_active_jobs: 1
      throttle_on_loadavg_10m: true
      throttle_on_memory_pressure: true
    enabled: false
```

### 15.4 Example: vLLM Plus llama.cpp Local Profiles

```yaml
model_profiles:
  vllm_local_configured:
    provider: openai_compatible
    provider_package: langchain-openai
    provider_class_hint: ChatOpenAI
    model_name: "configured-vllm-served-model"
    api_base_alias: openbao://kv/model/vllm/base_url
    credential_alias: openbao://kv/model/vllm/api_key_optional
    roles: [implementation, test_creation]
    resource_profile: local_heavy
    enabled: false

  llamacpp_local_small_configured:
    provider: openai_compatible
    provider_package: langchain-openai
    provider_class_hint: ChatOpenAI
    model_name: "configured-llamacpp-small-model"
    api_base_alias: openbao://kv/model/llamacpp_small/base_url
    credential_alias: openbao://kv/model/llamacpp_small/api_key_optional
    roles: [formatting, summarization]
    resource_profile: local_heavy
    enabled: false
```

### 15.5 Example: Z.AI OpenAI-Compatible Profile

```yaml
model_profiles:
  zai_coding_plan_configured:
    provider: zai
    provider_package: langchain-openai
    provider_class_hint: ChatOpenAI
    model_name: "configured-zai-model-id"
    api_base_alias: openbao://kv/model/zai/base_url
    credential_alias: openbao://kv/model/zai/api_key
    roles: [implementation, return_review]
    resource_profile: ai_heavy
    api_metered: false
    subscription_window_seconds: 18000
    subscription_window_target_percent: 99
    enabled: false
```

### 15.6 Example: Optional opencode Harness Integration

opencode is not required. Prefer LangChain. This integration exists only if configured for delegation or model registry import.

```yaml
opencode_integration:
  enabled: false
  mode: registry_import_only  # registry_import_only | delegate_jobs
  binary_path: opencode
  config_path: .opencode.json
  allowed_queues: []
  credential_policy: use_opencode_config_only
  notes: "Do not require opencode for normal harness operation."
```

---

## 16. Prompt System

### 16.1 Prompt Source Policy

Use public, auditable prompt patterns and official documentation. Do not ingest, reproduce, depend on, or adapt leaked proprietary system prompts or confidential prompt text.

Initial public sources to study:

[ ] Aider public prompt files and docs.
[ ] Goose customization docs.
[ ] opencode public docs/source for agents, prompts, and tool gating.
[ ] Claude Code public docs for memory files, hooks, skills, prompt-library patterns, subagents, and operational workflows.
[ ] LangChain prompt template docs.

### 16.2 Prompt Registry Fields

```text
prompt_profile_id
name
version
task_categories
base_template_path
partials
output_schema
allowed_playbooks
forbidden_actions
model_compatibility
created_by
created_at
parent_profile_id
eval_score
last_eval_at
default_policy
```

Prompt lifecycle:

```text
draft -> test -> experimental -> default_candidate -> default -> deprecated
```

No normal human approval is required for prompt promotion unless configured. High-risk prompt changes require stronger tests, independent review, and rollback evidence.

### 16.3 Harness-Aware Base Prompt Template

Store as `templates/prompts/base_harness_aware.md.j2`.

```text
You are a harness-aware coding agent operating inside an autonomous Python coding system.

You are not a free-form chat assistant. You are a controlled worker that must produce structured, auditable outputs.

Harness capabilities available to you:
- Read todo records provided in context.
- Propose todo updates using the requested schema.
- Request Ansible playbooks for validation, Molecule testing, quality gates, git worktree management, container actions, dependency updates, log audits, prompt evaluation, gap analysis, and self-improvement.
- Inspect task returns, artifacts, logs, diffs, test output, ARA refs, and model transcripts that are provided in context.
- Propose child todos when work is incomplete, blocked, risky, missing tests, or missing evidence.
- Use multiple model profiles only when the harness grants access.
- Improve prompt profiles and harness code only through the tested self-improvement workflow.

Hard rules:
- Do not mark work complete unless acceptance criteria, required Molecule evidence, configured coverage gates, and validation evidence support completion.
- Do not invent evidence.
- Do not leak secrets or private variables.
- Do not bypass queue, task, rule, policy, or playbook systems.
- Do not silently discard failures.
- Prefer small, reversible changes.
- Prefer mature FOSS libraries/modules/collections over custom code.
- For project-specific Ansible content, create or update verbose Molecule scenarios before claiming completion.
- Do not lower coverage thresholds to pass a task; create a config-change todo with evidence instead.
- Record uncertainty and confidence.

Current harness context:
- job_id: {{ job_id }}
- todo_id: {{ todo_id | default('none') }}
- return_id: {{ return_id | default('none') }}
- queue: {{ queue }}
- work_type: {{ work_type }}
- resource_profile: {{ resource_profile }}
- prompt_profile: {{ prompt_profile }}
- model_profile: {{ model_profile }}
- allowed_playbooks: {{ allowed_playbooks }}
- configured_action_policy: {{ configured_action_policy }}
- configured_quality_gates: {{ configured_quality_gates }}
- required_molecule_scenarios: {{ required_molecule_scenarios | default([]) }}

Output must match the requested schema exactly.
```

### 16.4 Return Review Prompt Template

```text
{% include 'base_harness_aware.md.j2' %}

Task:
Review the task return and decide what should happen next.

Inputs:
- Task return JSON:
{{ task_return_json }}

- Candidate todos:
{{ candidate_todos_json }}

- Relevant artifacts:
{{ artifact_summaries }}

Decision requirements:
1. Determine which todo this return belongs to.
2. Determine whether the todo is complete, incomplete, failed, blocked, unsafe, duplicate, or should be placed on configured manual hold.
3. Identify concrete evidence for the decision.
4. Update todo statuses only when justified.
5. Create child todos for failures, missing tests, missing Molecule scenarios, weak Molecule assertions, incomplete work, missing dependencies, or follow-up improvements.
6. Request validation, Molecule, and quality-gate playbooks when the return lacks required evidence.
7. For Ansible-bound work, require Molecule scenario evidence and quality gate evidence before complete.
8. Do not request human approval for normal work. Increase validation or independent model review instead.
9. Use approval_required only when the task policy explicitly says it is a manual-hold task.

Return only JSON that matches the TaskDecision schema.
```

### 16.5 Molecule-First TDD Prompt Partial

Store as `templates/prompts/partials/molecule_first_tdd.md.j2` and include it in implementation, test-creation, validation, return-review, gap-analysis, dependency-update, and self-improvement prompts when a todo touches Ansible content or quality gates.

```text
MOLECULE_FIRST_TDD_CONTRACT:
1. Before changing production code, identify the smallest failing test or missing scenario that proves the todo is real.
2. For Python code, update or create pytest coverage before implementation.
3. For Ansible playbooks, roles, collections, templates, or internal tool-call wrappers, update or create the Molecule scenario before implementation.
4. The scenario must call the real playbook, role, or wrapper boundary used by the worker.
5. The verify step must use explicit assertions with useful failure messages and must check artifacts, task returns, audit events, ARA refs when enabled, secret redaction when relevant, cleanup, and failure behavior.
6. Idempotent automation must pass Molecule idempotence. Non-idempotent automation must have an explicit configured exemption and a repeat-run or dry-run safety scenario.
7. Custom Ansible modules/plugins need ansible-test where applicable in addition to Molecule lifecycle coverage.
8. Coverage thresholds come from config. Never hardcode them and never lower them just to pass a task.
9. Completion requires evidence: pytest/coverage refs for Python changes and Molecule/quality-gate refs for Ansible-bound changes.
10. Prefer mature, maintained FOSS modules, roles, collections, callbacks, pytest plugins, packaging tools, container tooling, and Molecule features before custom harness code.
11. For packaging/runtime work, require pip bundle validation and slim container validation evidence when the change can affect those artifacts.
12. For dogfood/self-improvement work, prove the change can be installed or run through the same artifact path a user would receive.
```

Prompt acceptance checks:

[ ] Return-review prompts ask for Molecule evidence when Ansible content changed.
[ ] Implementation prompts ask the model to create or update Molecule first.
[ ] Gap-analysis prompts flag missing Molecule scenarios as high-confidence gaps.
[ ] Log-audit prompts inspect Molecule/ARA/Runner evidence before qualitative model judgment.
[ ] Self-improvement prompts block reload when Molecule coverage, Python coverage, pip bundle validation, or slim container validation gates fail for affected areas.

---

## 17. Human Interaction Model

Humans can interact with the todo list and configuration, but normal operation must not require them.

Human interfaces:

[ ] CLI list/filter todos.
[ ] CLI add/edit/cancel todos.
[ ] CLI pause/resume queues.
[ ] CLI set budgets.
[ ] CLI set model profiles.
[ ] CLI set OpenBao config.
[ ] CLI release manual-hold tasks.
[ ] Minimal web/API todo board after CLI works.

Rules:

[ ] Human edits win conflicts through optimistic concurrency.
[ ] Human can pause everything with emergency stop.
[ ] Human can place any todo on manual hold.
[ ] The system must continue unrelated work while manual-hold tasks exist.
[ ] Manual-hold update proposals must not be silently executed.

Tests:

[ ] `test_human_edit_conflict_reloads_todo_before_model_update`.
[ ] `test_manual_hold_does_not_block_unrelated_work`.
[ ] `test_user_can_release_approval_required_openbao_update`.

---

## 18. Logging, Observability, ARA, And Self-Audit

### 18.1 Structured Logs

Required fields:

```text
timestamp
level
event_name
service
process_id
worker_id
job_id
todo_id
return_id
queue
bucket_id
playbook
model_profile
prompt_profile
trace_id
span_id
message
data
redaction_status
ara_playbook_ref
```

Initial event names:

```text
loop.tick.start
loop.tick.end
queue.bucket.reserved
queue.bucket.released
job.dispatch.requested
job.dispatch.accepted
job.dispatch.failed
playbook.started
playbook.completed
playbook.failed
model.call.started
model.call.completed
model.call.failed
task_return.created
task_return.claimed
decision.created
decision.applied
todo.created
todo.updated
todo.status_changed
pid.evaluated
rule.applied
policy.validation.started
policy.validation.denied
policy.validation.allowed
git.commit.created
git.merge.completed
git.push.completed
git.force_push.rejected
openbao.bootstrap.started
openbao.image_digest.pinned
openbao.update_proposal.created
reload.requested
reload.completed
reload.failed
audit.finding_created
```

### 18.2 ARA First

[ ] Use ARA to record Ansible playbook results before building custom Ansible run visualization.
[ ] Store ARA references in artifacts and logs.
[ ] Use ARA data in log audit and gap analysis.
[ ] Evaluate whether ARA's API/UI satisfies MVP run browsing.

### 18.3 Metrics

[ ] Active buckets by queue/resource profile.
[ ] Queue depth by status.
[ ] Return-review backlog.
[ ] Job duration by playbook.
[ ] Job failures by playbook.
[ ] 1/5/10 minute load averages.
[ ] Logical CPU count.
[ ] Model usage by profile/prompt/category.
[ ] Cost estimate by run/window.
[ ] Non-API usage burn-line position.
[ ] Local model resource pressure.
[ ] Test pass/fail rate.
[ ] Manual-hold count.
[ ] PID output and throttle reasons.
[ ] Reload success/failure.
[ ] Secret redaction findings.

### 18.4 Self-Audit

[ ] Run deterministic audit after N jobs or M failures.
[ ] Run model-assisted audit on redacted logs at lower frequency.
[ ] Create todos for confirmed findings.
[ ] Create gap-analysis tasks for ambiguous audit themes.
[ ] Never expose raw secrets to audit prompts.

---

## 19. Secrets With OpenBao And hvac

OpenBao is the secret source. hvac is the Python client.

Modes:

```text
external_openbao: configured address/token/auth method.
local_bootstrap: start local OpenBao container when external config is absent.
```

Default local bootstrap:

```yaml
openbao:
  mode: auto
  local_image: ghcr.io/openbao/openbao
  local_image_digest_pin: auto_after_first_pull
  local_container_runtime: podman_preferred
  kv_mount: secret
  auth_method: approle
  approle_role_name: agentic-harness
  weekly_image_update_scan: true
  weekly_image_update_creates_manual_hold: true
```

Secret reference rules:

[ ] Store secret values in OpenBao, not PostgreSQL.
[ ] Store aliases/paths in PostgreSQL.
[ ] Model prompts see only aliases unless an explicit model-safe secret policy exists.
[ ] Job-private vars may contain resolved secrets only for playbooks that need them.
[ ] Logs redact values and high-risk aliases.

Tests:

[ ] `test_secret_alias_resolves_only_in_job_private_scope`.
[ ] `test_model_context_does_not_contain_secret_value`.
[ ] `test_openbao_missing_external_config_bootstraps_local_container`.

---

## 20. Git Autonomy

The AI controls the repo from init onward unless configuration restricts it.

Git defaults:

[ ] Initialize repo if absent.
[ ] Create worktree per todo.
[ ] Branch per todo.
[ ] Commit after validation gates pass.
[ ] Merge automatically according to strategy.
[ ] Tag automatically according to release/checkpoint policy.
[ ] Push immediately when real remote is configured.
[ ] Use local bare mirror only when no real remote exists and mirror validation is useful.
[ ] Never force-push.
[ ] Sigstore signing is configurable.

Branch naming example:

```text
agent/TODO-000123/add-return-review-20260528153045
```

Commit message template:

```text
TODO-000123: add return review decision schema

Evidence:
- validate_task.yml: artifact://...
- pytest: artifact://...
- return_review: artifact://...
```

Release tag default:

```text
YYYYMMDDHHMMSS
```

Agent checkpoint tag default:

```text
agent/TODO-000123/20260528153045/abcdef1
```

---

## 21. Hot Reload And Self-Improvement

Reload categories:

```text
config reload: safe, frequent, no process restart required
prompt reload: safe if schema validates and version increments
rule reload: safe if simulation passes
worker code reload: moderate risk; drain Gunicorn workers
ansible content reload: validate syntax/lint/policy before use
event loop code reload: staged handoff
schema migration: backup and explicit migration plan
```

Self-improvement gates:

[ ] Todo exists.
[ ] Failing test or acceptance spec exists.
[ ] Worktree exists.
[ ] Implementation is minimal.
[ ] Unit tests pass.
[ ] Integration tests pass for touched subsystem.
[ ] Playbook syntax/lint/policy checks pass.
[ ] Required Molecule scenarios and quality gates pass.
[ ] Library-first research artifact exists for nontrivial custom code.
[ ] Pip install bundle validates when packaging/runtime surfaces changed.
[ ] Slim agent container validates when packaging/runtime/container surfaces changed.
[ ] Release artifact validation passes when dogfood promotion depends on the change.
[ ] Dogfood smoke task passes.
[ ] Log audit has no critical findings.
[ ] Reload plan exists.
[ ] Rollback plan exists.
[ ] Automatic rollback works in tests.

No normal human approval is required for self-improvement; replace approval with stronger automated validation and independent model review when risk is high. The harness may continuously improve itself only through the same package, container, Molecule, quality-gate, git, reload, and log-audit path that normal tasks use. No special local-only dogfood shortcut is allowed to become the default path.


---

## 21.5 Runtime Packaging, Native Execution, And Container Volume Contract

The harness must be runnable in two native Python modes and one container mode. These modes are first-class product interfaces and must remain covered by tests, Molecule scenarios where Ansible is involved, and runtime validation playbooks. The release output must include a pip install bundle and a slim agent-only container image. Both artifacts must be built from the same source commit and validated before dogfood promotion.

Release artifact goals:

[ ] A pip-only user can install the harness without uv by using the pip install bundle.
[ ] A container user can run the harness by starting the slim agent-only image with explicit data-source mounts.
[ ] The container artifact is not a development environment. It only runs the agent and its runtime entry points.
[ ] The pip bundle and container image are generated by Ansible playbooks and validated by deterministic tests.
[ ] The dogfood harness uses the same artifacts it would release to a user, not special local shortcuts.

Supported modes:

```text
native_uv: preferred native mode using uv project sync/run workflows.
native_pip: fallback native mode using Python venv plus pip requirements/constraints or wheel install.
container: image-based mode using rootless Podman first, Docker fallback when configured, and explicit volume/bind mounts for all mutable data sources.
```

Native uv requirements:

[ ] A clean checkout can run `uv sync --locked` or the configured equivalent.
[ ] A clean checkout can run the worker, event loop, tests, and playbooks through `uv run`.
[ ] `uv.lock` or the configured uv lock artifact is committed and updated only through dependency tooling.
[ ] uv mode does not assume globally installed Python packages.
[ ] uv mode records the uv version and Python version in validation artifacts.

Native pip requirements:

[ ] A clean checkout can create a venv using the configured Python.
[ ] pip can install the project and required runtime dependencies without uv.
[ ] pip fallback uses generated requirements/constraints and/or a wheel produced from the same `pyproject.toml` package metadata.
[ ] Requirements/constraints are kept in sync by `dependency_update.yml`; manual drift is a failing validation issue.
[ ] pip mode records pip version, Python version, requirements hashes when available, and installed package versions in validation artifacts.

Pip install bundle requirements:

[ ] The bundle is produced by `pip_install_bundle.yml`.
[ ] The bundle contains a wheel, sdist, runtime dependency wheelhouse, requirements/constraints, manifest, checksums, and installation notes.
[ ] The bundle installs in a clean venv using pip only.
[ ] The bundle does not include dev-only dependencies unless a separate optional dev bundle is configured.
[ ] The bundle does not include mutable runtime data, secrets, logs, worktrees, local model artifacts, database files, or OpenBao/ARA state.
[ ] The bundle exposes the same console scripts and package metadata as uv/native mode.
[ ] The bundle validation command and evidence are attached to the task return.

Container requirements:

[ ] The container image is built from the same Python package and validated wheel used by native modes and the pip bundle.
[ ] The container entrypoint runs the same Gunicorn/FastAPI worker and event-loop commands as native modes.
[ ] The production image just runs the agent. It contains application code, static runtime templates/playbooks/prompts, and declared runtime dependencies only.
[ ] Mutable state is not written to the image layer except disposable temporary files.
[ ] All durable data sources are either external services or explicit mounts.
[ ] Required mounts are configured before startup and validated by `data_source_mount_audit.yml` and `runtime_validate.yml`.
[ ] The image is built through `slim_agent_container_build.yml` and validated through `container_image_validate.yml`.
[ ] The root filesystem should be read-only when practical, with writable mounts for explicit runtime paths.
[ ] Rootless Podman is preferred; Docker fallback is allowed by policy.
[ ] Container mode supports both named volumes and bind mounts according to data-source purpose.
[ ] The final image size budget is configurable, and exceeding it creates a packaging optimization todo rather than silently bloating the release artifact.

Data-source mount contract:

```yaml
runtime:
  active_profile: local-container
  profiles:
    local-native-uv:
      mode: native_uv
      project_root: .
      data_roots:
        artifacts: ./.harness/artifacts
        logs: ./.harness/logs
        runner_private_data: ./.harness/runner
        cache: ./.harness/cache
    local-native-pip:
      mode: native_pip
      project_root: .
      venv_path: ./.venv
      requirements_files:
        - requirements.txt
      constraints_files:
        - constraints.txt
    local-container:
      mode: container
      runtime: auto_podman_first
      image_ref: agentic-harness:local
      mounts:
        - purpose: config
          source_type: bind
          host_path: ./config
          container_path: /config
          access: ro
          required: true
        - purpose: repo
          source_type: bind
          host_path: ./repos
          container_path: /data/repos
          access: rw
          required: true
        - purpose: artifacts
          source_type: named_volume
          volume_name: agentic-harness-artifacts
          container_path: /data/artifacts
          access: rw
          required: true
        - purpose: runner_private_data
          source_type: named_volume
          volume_name: agentic-harness-runner
          container_path: /data/runner
          access: rw
          required: true
        - purpose: logs
          source_type: named_volume
          volume_name: agentic-harness-logs
          container_path: /data/logs
          access: rw
          required: true
```

Default data-source purposes:

[ ] `config`: harness configuration and prompt/config templates; read-only by default in container mode.
[ ] `repo`: repositories under harness control; writable if the harness is expected to edit code.
[ ] `worktrees`: isolated git worktrees; may share repo mount or use its own mount.
[ ] `artifacts`: immutable job artifacts and task return evidence.
[ ] `logs`: structured logs when not sent only to stdout/log backend.
[ ] `runner_private_data`: Ansible Runner private data directories and event capture.
[ ] `cache`: dependency/model/provider caches where configured.
[ ] `postgres_data`: only when PostgreSQL itself is container-managed; otherwise PostgreSQL is an external service connection.
[ ] `openbao_data`: only when OpenBao itself is container-managed and not ephemeral local dev mode.
[ ] `ara_data`: only when ARA has local file-backed data; PostgreSQL-backed ARA should use PostgreSQL.
[ ] `model_cache`: only for explicitly configured local models or local inference endpoints.
[ ] `external_dataset`: user-specified data source mounts for future tasks.

Runtime equivalence checks:

[ ] The same app factory imports in all modes.
[ ] The same playbook registry is visible in all modes.
[ ] The same prompt templates are visible in all modes.
[ ] The same config schema validates in all modes.
[ ] `noop.yml` can run in all enabled modes.
[ ] `validate_task.yml` can run in the active mode.
[ ] Missing required mounts fail before job dispatch.
[ ] Container mode writes artifacts to the artifact mount.
[ ] Native modes write artifacts to configured native data roots.

TDD prompts for runtime work:

```text
When modifying packaging, install logic, containers, data roots, runtime startup, or release artifacts:
1. Write or update tests for native_uv, native_pip, pip bundle, and container modes unless the change is explicitly mode-specific.
2. Add or update Molecule scenarios for Ansible playbooks that build, start, inspect, package, validate, or audit runtimes.
3. Prove required data-source mounts are explicit and validated.
4. Prove no runtime mode writes durable state to an untracked/default location.
5. Prove pip fallback and pip install bundle still work without uv.
6. Prove uv remains the preferred happy path for native development.
7. Prove the slim container runs the agent from the same validated wheel artifact.
8. Prove the production container excludes dev/test-only dependencies and mutable state.
9. Record mature-library/tooling choices before writing custom packaging/container code.
10. Attach runtime, bundle, container, and release-artifact validation artifacts to the task return.
```

---

## 21.6 Release Artifact Research Findings And Design Rationale

These findings guide implementation and should be revisited by `library_research_gate.yml` and dependency-update work when upstream tools change. Prefer official documentation and mature project docs over blog posts.

Python packaging findings:

[ ] The Python Packaging User Guide describes modern project packaging around pyproject.toml, source distributions, and wheels: https://packaging.python.org/tutorials/packaging-projects/
[ ] PyPA build is a standards-oriented build frontend that builds source and wheel distributions from pyproject-based projects: https://build.pypa.io/
[ ] uv can build Python packages through `uv build`, and uv can support packaging workflows without becoming the only installer path: https://docs.astral.sh/uv/guides/package/
[ ] pip supports installing from PyPI, version-control repositories, local projects, and distribution files, so the final install bundle should be consumable by pip without uv: https://pip.pypa.io/en/latest/user_guide/
[ ] pip wheel can build wheel archives for requirements/dependencies, which fits a wheelhouse-based pip install bundle: https://pip.pypa.io/en/stable/cli/pip_wheel/
[ ] pyproject.toml should remain the source of build-system and project metadata; choose Hatchling, setuptools, or another mature backend based on evidence, not custom build code: https://packaging.python.org/en/latest/guides/writing-pyproject-toml/

Container findings:

[ ] Docker's build best-practices documentation recommends multi-stage builds to reduce final image size and keep only files needed to run the app: https://docs.docker.com/build/building/best-practices/
[ ] The Docker Official Python image documents `python:<version>-slim` variants as smaller images containing minimal Debian packages needed to run Python: https://hub.docker.com/_/python
[ ] Podman can build images from Containerfiles/Dockerfiles and supports similar build-context workflows, matching the rootless-Podman-first runtime policy: https://docs.podman.io/en/stable/markdown/podman-build.1.html
[ ] Container volume and mount docs should be preferred over custom mount code for data-source handling: https://docs.podman.io/en/v4.3/markdown/options/volume.html and https://docs.docker.com/engine/storage/volumes/

Resulting design decisions:

[ ] Build the project once as a normal Python package.
[ ] Use that wheel in native pip validation, the pip install bundle, and the slim container final stage.
[ ] Keep uv as the preferred development/native workflow, but validate pip without uv as a hard release gate.
[ ] Keep the runtime container final stage small and boring: Python runtime, installed wheel, runtime dependencies, static runtime assets, non-root user where practical, agent entrypoint, health check, and explicit mounts.
[ ] Add dev/test images only as separate optional artifacts; do not conflate them with the release runtime image.
[ ] Treat artifact generation as Ansible playbooks with Molecule coverage, not as ad hoc shell scripts.

---

## 22. TDD Strategy

Testing is a product feature of the harness, not a CI afterthought. Python tests validate harness logic. Molecule tests validate the Ansible tool surface. ansible-test validates custom Ansible collection plugins/modules where those exist. Quality gates connect all of those signals to todo completion, git automation, reload, and dogfood promotion.

### 22.1 Research Findings To Encode

[ ] Molecule is documented as an Ansible testing framework for collections, playbooks, and roles, and it can target systems/services reachable from Ansible: https://docs.ansible.com/projects/molecule/
[ ] Molecule's testing philosophy maps core phases to actions: dependency, create, prepare, converge, idempotence, side_effect, verify, cleanup, and destroy: https://docs.ansible.com/projects/molecule/philosophy/
[ ] Molecule playbook testing documentation shows playbook projects with Podman scenarios, inventory, create, prepare, converge, verify, cleanup, and destroy lifecycle files: https://docs.ansible.com/projects/molecule/getting-started-playbooks/
[ ] Molecule configuration supports project-level dependency prerun, shared state, scenario configuration, delegated/default drivers, and Ansible as the provisioner: https://docs.ansible.com/projects/molecule/configuration/
[ ] Molecule command docs expose `molecule test`, `molecule matrix`, scenario selection, and debug/verbosity paths: https://docs.ansible.com/projects/molecule/usage/
[ ] Molecule installation docs recommend ansible-dev-tools and show separate installation of ansible-lint and molecule-plugins[podman] for Podman drivers: https://docs.ansible.com/projects/molecule/installation/
[ ] community.molecule is intended to help write and maintain Molecule tests: https://docs.ansible.com/projects/molecule/collection/
[ ] pytest-ansible can discover Molecule scenarios and run them as pytest tests: https://ansible.readthedocs.io/projects/pytest-ansible/getting_started/
[ ] ansible-test sanity, unit, and integration tests are the collection/plugin layer for Ansible collection internals: https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_collections_testing.html
[ ] pytest-cov provides `--cov-fail-under`, branch coverage, reporting, and explicit config path options: https://pytest-cov.readthedocs.io/en/latest/config.html
[ ] coverage.py supports config-driven source/omit, concurrency, branch/report precision, and `fail_under` behavior: https://coverage.readthedocs.io/en/7.13.5/config.html
[ ] pytest-testinfra can test actual server state configured by Ansible and is optional when Ansible verify tasks are not expressive enough: https://testinfra.readthedocs.io/
[ ] uv project workflows document syncing project environments and running commands from project-managed environments: https://docs.astral.sh/uv/guides/projects/
[ ] pip requirements files are the documented fallback mechanism for repeatable pip installs: https://pip.pypa.io/en/latest/user_guide/
[ ] pyproject.toml is the standard packaging/tool configuration file for Python projects: https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
[ ] Podman volume options support host paths and named volumes, with absolute container paths and explicit mount options: https://docs.podman.io/en/v4.3/markdown/options/volume.html
[ ] Docker bind mount syntax maps host paths to container paths, and Docker volumes provide persistent data stores managed by Docker: https://docs.docker.com/engine/storage/bind-mounts/ and https://docs.docker.com/engine/storage/volumes/

### 22.2 Test Pyramid

```text
Many unit tests:
- schemas
- todo state machine
- rule engine
- PID controller
- queue allocation
- budget windows
- model gateway stubs
- prompt profile validation
- action policy matching
- Molecule coverage manifest matching
- quality gate config parsing
- redaction
- path validation

Many Molecule scenario tests:
- registered playbooks
- project-specific roles
- internal tool-call wrappers
- project-specific collections
- templates that materially affect playbook behavior
- action policy allow/deny fixtures
- artifact emission and task-return contracts
- ARA callback/reference capture where enabled

Some integration tests:
- event loop with fake DB or test Postgres
- worker endpoint with no-op playbook
- native uv install/start smoke
- native pip install/start smoke
- container image start with explicit data-source mounts
- Ansible Runner on localhost
- Molecule scenario runner against selected fixtures
- ARA recording
- OpenBao local bootstrap
- git worktree lifecycle
- container runtime detection
- dependency update dry-run
- CLI todo edit/list

Some ansible-test runs when collection plugins exist:
- sanity tests
- unit tests for module_utils/plugins
- integration tests for custom modules/plugins

Few end-to-end dogfood tests:
- todo -> queue -> worker -> playbook -> return -> review -> complete
- failing test -> child todo -> fix -> validate -> commit -> push
- missing Molecule scenario -> child todo -> scenario -> quality gate passes
- dependency missing -> dependency_update -> provider works
- self-improvement no-op -> quality gate -> reload -> audit
```

### 22.3 Quality Gate Configuration

Coverage targets must be configuration values. The code enforces them by reading config and failing the gate; the model cannot waive them.

```yaml
quality_gates:
  enabled: true
  python:
    enabled: true
    line_coverage_min_percent: 90
    branch_coverage_min_percent: 80
    coverage_config_path: pyproject.toml
    pytest_cov_fail_under_from_config: true
  molecule:
    enabled: true
    coverage_min_percent: 100
    require_for_registered_playbooks: true
    require_for_internal_tool_calls: true
    require_for_roles: true
    require_for_collections: true
    require_verbose_verify_tasks: true
    allow_configured_exemptions: true
    exemption_max_age_days: 14
  ansible_test:
    enabled_for_custom_collection_plugins: true
  enforcement:
    block_todo_complete: true
    block_commit: true
    block_merge: true
    block_tag: true
    block_push: true
    block_reload: true
```

### 22.4 Molecule Scenario Standards

[ ] Use clear scenario names: `playbooks_validate_task_success`, `internal_tool_git_push_denied_force`, `role_openbao_bootstrap_missing_external_config`.
[ ] Each scenario has README-style comments or metadata explaining purpose, inputs, expected state, and failure meaning.
[ ] `converge.yml` calls the real playbook, role, or wrapper.
[ ] `verify.yml` contains explicit asserts with fail messages; avoid vague smoke tests only.
[ ] Negative-path scenarios are required when the production pipeline has meaningful error handling.
[ ] Each scenario captures artifacts into the job artifact directory when possible.
[ ] Tests check secret redaction for jobs involving credentials or model context.
[ ] Idempotent content uses Molecule idempotence.
[ ] Non-idempotent content records a tested exemption and repeat/dry-run safety evidence.
[ ] Scenarios should use rootless Podman where container lifecycle is needed and configured.
[ ] Scenarios should use delegated/default mode when testing external resources or harness-provided test fixtures is clearer than container provisioning.
[ ] Scenarios should prefer mature modules and collections over shell tasks.
[ ] Scenarios should be reproducible locally with one command from the repo root.

### 22.5 First Failing Tests

[ ] `test_todo_state_machine_rejects_invalid_transition`.
[ ] `test_event_loop_dispatches_return_review_for_unreviewed_return`.
[ ] `test_event_loop_respects_queue_hard_cap`.
[ ] `test_load_controller_throttles_local_heavy_only`.
[ ] `test_worker_rejects_unknown_playbook`.
[ ] `test_worker_rejects_project_playbook_without_required_molecule_coverage`.
[ ] `test_ansible_noop_playbook_creates_task_return`.
[ ] `test_validate_task_creates_child_todo_on_pytest_failure`.
[ ] `test_validate_task_creates_child_todo_on_molecule_failure`.
[ ] `test_molecule_coverage_requires_registered_playbook_scenario`.
[ ] `test_molecule_coverage_requires_internal_tool_call_scenario`.
[ ] `test_quality_gate_reads_python_coverage_threshold_from_config`.
[ ] `test_quality_gate_reads_molecule_coverage_threshold_from_config`.
[ ] `test_quality_gate_blocks_completion_when_molecule_coverage_below_configured_minimum`.
[ ] `test_quality_gate_blocks_commit_merge_tag_push_reload_on_failure`.
[ ] `test_git_worktree_create_isolated_branch`.
[ ] `test_force_push_is_rejected`.
[ ] `test_model_gateway_rejects_over_budget_call`.
[ ] `test_missing_model_provider_triggers_dependency_update`.
[ ] `test_prompt_profile_schema_requires_output_schema`.
[ ] `test_log_redactor_masks_secret_like_values`.
[ ] `test_action_policy_denies_configured_collection`.
[ ] `test_openbao_pins_digest_after_bootstrap`.
[ ] `test_openbao_update_scan_creates_approval_required_manual_hold`.
[ ] `test_runtime_validate_native_uv_from_clean_checkout`.
[ ] `test_runtime_validate_native_pip_from_clean_checkout`.
[ ] `test_runtime_validate_container_requires_explicit_mounts`.
[ ] `test_pip_install_bundle_installs_in_clean_venv_without_uv`.
[ ] `test_pip_install_bundle_contains_manifest_checksums_and_wheelhouse`.
[ ] `test_slim_agent_container_runs_agent_entrypoint_only`.
[ ] `test_slim_agent_container_uses_same_validated_wheel_as_pip_bundle`.
[ ] `test_release_artifacts_validate_blocks_stale_or_missing_artifacts`.
[ ] `test_library_research_gate_blocks_unjustified_custom_code`.
[ ] `test_container_artifacts_written_to_mounted_volume`.
[ ] `test_data_source_mount_audit_detects_untracked_mutable_path`.
[ ] `test_self_improvement_requires_validation_before_reload`.

### 22.6 CI And Local Validation Commands

Primary CI commands:

```text
uv sync --locked
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=${PYTHON_LINE_COVERAGE_MIN_PERCENT:-90}
uv run pytest tests/integration
uv run ruff check .
uv run mypy src
uv run pre-commit run --all-files
ansible-playbook --syntax-check playbooks/*.yml
ansible-lint playbooks roles
uv run molecule test --all
uv run python tools/check_molecule_coverage.py --config harness.yml
uv run python tools/check_quality_gates.py --config harness.yml
uv run ansible-playbook playbooks/pip_install_bundle.yml
uv run ansible-playbook playbooks/slim_agent_container_build.yml
uv run ansible-playbook playbooks/release_artifacts_validate.yml
```

Collection/plugin validation when custom collection code exists:

```text
ansible-test sanity --docker default -v
ansible-test units --docker default -v
ansible-test integration --docker default -v
```

Fallback commands:

```text
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest --cov --cov-report=term-missing
python -m ruff check .
python -m mypy src
pre-commit run --all-files
python -m molecule test --all
```

---

## 23. Sprint Board

### Objective 1: Repository Skeleton And Developer Workflow

Status: [ ] Not started
Queue: core
Risk: low

Tasks:

[ ] Create Python package under `src/general_ludd`.
[ ] Add `pyproject.toml` using uv-compatible dependency groups, package metadata, build backend, and console-script entry points.
[ ] Add pytest, pytest-cov, coverage.py, pytest-ansible, ruff, mypy, pydantic, FastAPI, uvicorn-worker, gunicorn, httpx, ansible-runner, ansible-dev-tools or molecule plus ansible-lint, structlog, prometheus-client, psutil, SQLAlchemy, Alembic, psycopg, hvac.
[ ] Add test folders: unit, integration, e2e.
[ ] Add Molecule scenario roots for playbooks, roles, and internal tool-call wrappers.
[ ] Add `playbooks`, `roles`, `templates/prompts`, `tools/ansible_lint_rules`, `scripts`, `docs`.
[ ] Add pre-commit config.
[ ] Add Makefile or justfile.
[ ] Add README with local bootstrap for native uv, native pip, pip install bundle, and container modes.
[ ] Add runtime profile examples for native uv, native pip, and container with explicit mounts.
[ ] Add this sprint as `docs/sprint.md`.

Acceptance criteria:

[ ] `uv run pytest` passes with placeholder tests.
[ ] `uv run python tools/check_quality_gates.py --config harness.yml` passes with bootstrap thresholds.
[ ] `uv run ruff check .` passes.
[ ] `uv run mypy src` passes or has documented initial scope.
[ ] `ansible-playbook --syntax-check playbooks/noop.yml` passes.
[ ] Worker app starts and `/healthz` returns healthy.
[ ] Native uv runtime smoke path is documented.
[ ] Native pip runtime smoke path is documented.
[ ] Pip install bundle creation and install smoke path are documented.
[ ] Container runtime smoke path is documented with explicit data-source mounts.

### Objective 2: PostgreSQL Schema And Todo State Machine

Status: [ ] Not started
Queue: core
Risk: medium

Tasks:

[ ] Add Alembic migrations.
[ ] Add todo schema.
[ ] Add task return schema.
[ ] Add task decision schema.
[ ] Add audit event schema.
[ ] Add variable namespace schema.
[ ] Add queue/bucket schema.
[ ] Add optimistic concurrency.
[ ] Add SKIP LOCKED claim helpers.

Acceptance criteria:

[ ] Todos can be created/updated/listed.
[ ] Invalid transitions fail.
[ ] Concurrent claim test passes.
[ ] Audit event emitted for every todo change.

### Objective 3: Worker App And Ansible Runner MVP

Status: [ ] Not started
Queue: worker
Risk: medium

Tasks:

[ ] Implement FastAPI app factory.
[ ] Add Gunicorn config.
[ ] Implement job spec schema.
[ ] Implement Ansible Runner adapter.
[ ] Implement no-op playbook.
[ ] Implement no-op Molecule scenario that converges the real no-op playbook and verifies artifact/task-return output.
[ ] Capture artifacts and events.
[ ] Write task return after playbook.

Acceptance criteria:

[ ] Unknown playbook rejected.
[ ] No-op job creates task return.
[ ] Artifact directory is created.
[ ] No-op Molecule scenario passes and stores artifacts.
[ ] Worker logs contain correlation IDs.

### Objective 4: Event Loop MVP

Status: [ ] Not started
Queue: core
Risk: medium

Tasks:

[ ] Implement tick loop.
[ ] Claim unreviewed returns.
[ ] Dispatch return-review jobs.
[ ] Claim runnable todos.
[ ] Dispatch execute jobs.
[ ] Reconcile decisions.
[ ] Add lease reclaim.

Acceptance criteria:

[ ] Event loop dispatches return review for unreviewed return.
[ ] Event loop does not execute playbooks inline.
[ ] Expired leases are reclaimed.

### Objective 5: PID/Rules/Resource Profiles

Status: [ ] Not started
Queue: core
Risk: medium

Tasks:

[ ] Implement resource profile enum.
[ ] Implement system load scrape.
[ ] Implement load controller.
[ ] Implement budget controller.
[ ] Implement rule engine.
[ ] Implement bucket allocation.
[ ] Simulate high local load.

Acceptance criteria:

[ ] Local-heavy work throttles when 10-minute load exceeds logical CPU count.
[ ] AI-heavy remote work does not throttle solely because local CPU is high.
[ ] API budget cap blocks expensive calls.
[ ] Non-API burn-line controller works when allowance configured.

### Objective 6: Return Review Pipeline

Status: [ ] Not started
Queue: model
Risk: high

Tasks:

[ ] Add base harness-aware prompt.
[ ] Add return-review prompt.
[ ] Add model gateway stub.
[ ] Add TaskDecision validation.
[ ] Add return-review playbook.
[ ] Add decision application code.

Acceptance criteria:

[ ] Passing validation can complete todo.
[ ] Failing validation creates child todo.
[ ] Missing evidence requests validation.
[ ] Invalid model JSON is rejected.

### Objective 7: Model Gateway And Provider Auto-Install

Status: [ ] Not started
Queue: model
Risk: high

Tasks:

[ ] Add model profile schema.
[ ] Add LangChain provider registry.
[ ] Add dynamic import.
[ ] Add missing package detection.
[ ] Add dependency update todo creation.
[ ] Add OpenAI example config.
[ ] Add OpenRouter example config.
[ ] Add llama.cpp/vLLM local endpoint examples, disabled by default.
[ ] Add Z.AI example config.
[ ] Add optional opencode integration config.

Acceptance criteria:

[ ] Stub model works.
[ ] Missing provider package routes to dependency update.
[ ] Local model profile is ignored unless enabled.
[ ] Model role routing is configurable.

### Objective 8: Dependency Update Pipeline

Status: [ ] Not started
Queue: dependency
Risk: medium

Tasks:

[ ] Implement `dependency_update.yml`.
[ ] Use uv first.
[ ] Implement pip fallback.
[ ] Update lockfiles.
[ ] Update tests/docs/shims.
[ ] Commit automatically after validation.

Acceptance criteria:

[ ] Dependency update changes lockfile or records no-op reason.
[ ] Tests run after dependency update.
[ ] Provider package install is automatic through this pipeline.

### Objective 9: OpenBao Secrets Bootstrap

Status: [ ] Not started
Queue: infra
Risk: high

Tasks:

[ ] Implement OpenBao config schema.
[ ] Implement external OpenBao detection.
[ ] Implement local bootstrap with ghcr.io/openbao/openbao.
[ ] Implement hvac client.
[ ] Configure AppRole and KV v2.
[ ] Resolve aliases.
[ ] Pin image digest after first pull.
[ ] Add weekly image update scan.
[ ] Create approval-required manual-hold todo for image updates.

Acceptance criteria:

[ ] External config wins.
[ ] Local bootstrap works in test environment when container runtime available.
[ ] Secrets are not logged.
[ ] Image digest is pinned.
[ ] Weekly update scan does not auto-update.

### Objective 10: Git Autonomy

Status: [ ] Not started
Queue: git
Risk: high

Tasks:

[ ] Implement repo init.
[ ] Implement worktree creation.
[ ] Implement pre-commit validation.
[ ] Implement commit.
[ ] Implement merge.
[ ] Implement release tag `YYYYMMDDHHMMSS`.
[ ] Implement checkpoint tags.
[ ] Implement real remote push.
[ ] Implement local bare mirror fallback.
[ ] Permanently reject force-push.
[ ] Add optional Sigstore config.

Acceptance criteria:

[ ] Validated change commits automatically.
[ ] Real remote push happens when remote configured.
[ ] Force-push is rejected.
[ ] Tags follow default format.

### Objective 11: Ansible Action Policy And ARA

Status: [ ] Not started
Queue: ansible
Risk: high

Tasks:

[ ] Implement action policy config.
[ ] Implement action manifest generator.
[ ] Implement ansible-lint custom deny rule.
[ ] Implement `action_policy_validate.yml`.
[ ] Feed action manifest into Molecule coverage checks.
[ ] Configure project-local collections/roles.
[ ] Evaluate ansible-policy/OPA as future option.
[ ] Implement `ara_setup.yml`.
[ ] Store ARA refs in artifacts.

Acceptance criteria:

[ ] Empty deny lists allow normal playbooks.
[ ] Disabled collection/role/module is denied.
[ ] ARA records no-op run.
[ ] Post-run audit compares expected and observed actions.
[ ] Missing Molecule coverage for a registered playbook is detected.

### Objective 12: Molecule Testing And Quality Gates

Status: [ ] Not started
Queue: qa
Risk: high

Tasks:

[ ] Implement Molecule scenario directory conventions.
[ ] Implement `molecule_test.yml`.
[ ] Implement `molecule_coverage_audit.yml`.
[ ] Implement `quality_gate_validate.yml`.
[ ] Implement `tools/check_molecule_coverage.py`.
[ ] Implement `tools/check_quality_gates.py`.
[ ] Add quality gate config to `harness.yml` or `pyproject.toml`.
[ ] Add no-op playbook scenario.
[ ] Add return-review scenario.
[ ] Add validate-task scenario.
[ ] Add action-policy scenario.
[ ] Add git automation non-idempotent exemption and repeat/dry-run safety scenario.
[ ] Add OpenBao bootstrap scenario.
[ ] Add ansible-test invocation path for future custom collection plugins.

Acceptance criteria:

[ ] Configured Python coverage threshold is enforced.
[ ] Configured Molecule coverage threshold is enforced.
[ ] Every registered project playbook has a mapped scenario or configured exemption.
[ ] Every internal tool-call wrapper has a mapped scenario or configured exemption.
[ ] Verbose verify assertions are detected for required scenarios.
[ ] Quality gate failure blocks completion, commit, merge, tag, push, and reload for affected work.
[ ] Quality gate failure creates actionable child todos.

### Objective 13: Validation, Gap Analysis, And Log Audit

Status: [ ] Not started
Queue: audit
Risk: medium

Tasks:

[ ] Implement validate_task playbook.
[ ] Validate task must run required Molecule scenarios and quality gates.
[ ] Implement gap analysis playbook.
[ ] Implement log audit playbook.
[ ] Add deterministic audit checks.
[ ] Add model-assisted audit after redaction.
[ ] Create todos from findings.

Acceptance criteria:

[ ] Failing tests create child todos.
[ ] Failing or missing Molecule scenarios create child todos.
[ ] Gap analysis finds known missing test in fixture repo.
[ ] Log audit detects synthetic secret leak.
[ ] Audit findings become todos.

### Objective 14: Self-Improvement And Reload

Status: [ ] Not started
Queue: self_improve
Risk: high

Tasks:

[ ] Implement self-improvement workflow.
[ ] Self-improvement must update Molecule scenarios and pass quality gates before reload.
[ ] Implement reload config.
[ ] Implement prompt/rule reload.
[ ] Implement worker code reload with drain.
[ ] Add dogfood smoke task.
[ ] Add rollback path.

Acceptance criteria:

[ ] No-op self-improvement completes through harness.
[ ] Failed reload rolls back.
[ ] Active jobs are not lost.


### Objective 15: Runtime Packaging And Deployment Modes

Status: [ ] Not started
Queue: infra
Risk: high

Tasks:

[ ] Implement runtime profile schema for `native_uv`, `native_pip`, and `container`.
[ ] Implement data-source mount registry and validation.
[ ] Implement `runtime_validate.yml`.
[ ] Implement `native_install_validate.yml`.
[ ] Implement `pip_install_bundle.yml`.
[ ] Implement `slim_agent_container_build.yml`.
[ ] Implement `container_image_validate.yml`.
[ ] Implement `release_artifacts_validate.yml`.
[ ] Implement `data_source_mount_audit.yml`.
[ ] Add Containerfile/Dockerfile using the validated wheel artifact from the pip bundle path.
[ ] Add generated pip requirements/constraints workflow through dependency_update tooling.
[ ] Add pip bundle manifest/checksum/wheelhouse generation.
[ ] Add slim container image size-budget configuration and inspection checks.
[ ] Add library-first research gate for packaging/container code changes.
[ ] Add container examples for rootless Podman and Docker fallback.
[ ] Add Molecule scenarios for runtime validation playbooks and mount failure paths.
[ ] Add docs for native uv, native pip, and container startup.

Acceptance criteria:

[ ] Clean checkout runs in native uv mode.
[ ] Clean checkout runs in native pip mode without uv.
[ ] Pip install bundle installs and runs in a clean pip-only venv.
[ ] Slim agent-only container image starts with configured explicit mounts.
[ ] Container image fails fast when required artifact/runner/repo/config mounts are missing.
[ ] Container artifacts and Runner private data are written to mounted data sources.
[ ] No durable runtime state is written only to the image layer.
[ ] Runtime validation is attached to quality gate evidence.
[ ] Dependency update tooling keeps uv lock and pip fallback artifacts synchronized.
[ ] Release artifact validation blocks tags, pushes, reloads, and dogfood promotion when the pip bundle or slim container is stale or missing.

### Objective 16: Continuous Self-Dogfooding Release Loop

Status: [ ] Not started
Queue: self_improve
Risk: high

Goal:
Move the project from one-off dogfood smoke tests to a continuous agentic improvement loop that uses the same install, runtime, packaging, validation, git, reload, and audit paths required for users.

Tasks:

[ ] Define a dogfood run profile that targets the harness repository itself.
[ ] Seed the todo list from this sprint, gap analysis, test failures, lint failures, dependency scans, log audits, and release-artifact validation failures.
[ ] Ensure the event loop can select, execute, review, and complete harness-improvement todos without special local-only shortcuts.
[ ] Require `library_research_gate.yml` artifacts for nontrivial custom harness code.
[ ] Require failing tests or Molecule scenarios before implementation.
[ ] Require `pip_install_bundle.yml` and `slim_agent_container_build.yml` when changes touch package/runtime/container surfaces.
[ ] Require `release_artifacts_validate.yml` before dogfood promotion, reload, release tag, or real remote push of packaging-sensitive changes.
[ ] Use the generated pip bundle and slim container in at least one recurring dogfood smoke cycle.
[ ] Create child todos for dogfood gaps rather than weakening gates.
[ ] Add log-audit rules that detect special-case dogfood bypasses.

Acceptance criteria:

[ ] The harness can create and complete a real improvement todo against its own repository.
[ ] The todo includes failing-test or Molecule evidence before implementation.
[ ] The change is validated, bundled for pip, built into a slim container when affected, committed, pushed, reloaded, and audited automatically.
[ ] The dogfood run uses configured model profiles and configured runtime profiles, not hidden defaults.
[ ] Dogfood promotion fails when release artifacts are missing, stale, oversized, or not linked to the current commit.
[ ] Logs and todos prove there were no local-only bypasses.

---

## 24. Dogfood Plan

Milestone 0: Harness skeleton.

[ ] Human creates initial todos manually or from this sprint.
[ ] Event loop runs against stub workers.
[ ] Quality gate runs in bootstrap mode with configured thresholds.
[ ] No-op playbook proves return path.
[ ] No-op Molecule scenario proves playbook scenario path.

Milestone 1: First autonomous closed loop.

[ ] Todo requests no-op playbook.
[ ] Worker executes playbook.
[ ] Task return created.
[ ] Return review classifies result.
[ ] Todo marked complete with evidence.

Milestone 2: First code change.

[ ] Agent creates failing test.
[ ] Agent implements minimal code.
[ ] validate_task passes.
[ ] git automation commits and pushes.

Milestone 3: First self-improvement.

[ ] Harness identifies missing playbook capability.
[ ] Harness creates todo.
[ ] Harness implements playbook/test.
[ ] Harness validates, commits, reloads, audits.

Milestone 4: First dependency auto-install.

[ ] Model profile references missing LangChain provider package.
[ ] Provider inventory creates dependency update todo.
[ ] dependency_update installs via uv.
[ ] Tests/docs update.
[ ] Model profile health passes.

Milestone 5: First OpenBao weekly update proposal.

[ ] Weekly scan identifies candidate digest.
[ ] Manual-hold approval-required todo is created.
[ ] Normal work continues.
[ ] User can later approve/update the todo.

Milestone 6: Runtime mode parity.

[ ] Harness starts through native uv from a clean checkout.
[ ] Harness starts through native pip from a clean checkout.
[ ] Harness starts in a container with explicit data-source mounts.
[ ] No-op task completes in each enabled runtime mode.
[ ] Data-source mount audit passes.

Milestone 7: Release artifacts are real product surfaces.

[ ] `pip_install_bundle.yml` creates a pip-only install bundle from the current commit.
[ ] A clean pip-only venv installs and runs the agent from the bundle.
[ ] `slim_agent_container_build.yml` builds the agent-only runtime image from the validated wheel.
[ ] The slim container starts with explicit test mounts and completes a no-op task.
[ ] `release_artifacts_validate.yml` proves bundle/container/version/commit/entrypoint parity.

Milestone 8: Continuous agentic self-improvement.

[ ] Gap analysis creates a real harness-improvement todo from missing functionality or weak tests.
[ ] The agent writes failing tests or Molecule scenarios before implementation.
[ ] The agent records mature-library research before adding nontrivial custom code.
[ ] The harness implements the change in its own repo.
[ ] Validation, Molecule, quality gates, pip bundle, slim container, log audit, git automation, and reload all pass as applicable.
[ ] The updated harness resumes work and creates no bypass-related audit findings.

---

## 25. Risk Register

| Risk | Probability | Impact | Mitigation | Status |
| --- | --- | --- | --- | --- |
| Model marks incomplete work complete | Medium | High | Require validation evidence, schema checks, independent review for high risk | [ ] |
| Local load runaway | Medium | High | 10-minute load controller, local-heavy throttles, hard caps | [ ] |
| API spend runaway | Medium | High | USD 200 default cap, budget preflight, usage windows | [ ] |
| Subscription/non-API usage exhausted too early | Medium | Medium | 5-hour configurable window, 99 percent linear burn target | [ ] |
| Secret leak | Medium | High | OpenBao aliases, redaction, audit, no model-visible secrets | [ ] |
| Ansible action policy incomplete | Medium | High | Layered static lint, pinned deps, Runner isolation, ARA audit | [ ] |
| Custom code grows too large | Medium | Medium | FOSS-first policy, research notes, library adapters | [ ] |
| Dependency auto-update breaks harness | Medium | Medium | Dedicated pipeline, tests/docs/shims, rollback | [ ] |
| OpenBao local dev token leaks | Low | High | File permissions, redaction, local-only mode, audit | [ ] |
| Force-push loses work | Low | High | Permanent force-push rejection | [ ] |
| ARA overhead/noise | Medium | Low | Evaluate early, configure retention, fallback to Runner events | [ ] |
| Molecule coverage becomes noisy or incomplete | Medium | High | Manifest-driven coverage, verbose scenarios, configured exemptions with expiry, child todos | [ ] |
| Hot reload breaks active jobs | Medium | High | Worker drain, staged reload, rollback tests | [ ] |
| Runtime modes drift | Medium | High | runtime_validate, native_install_validate, container_image_validate, parity tests | [ ] |
| Container hides mutable data in image layer | Medium | High | explicit mount registry, mount audit, image validation, read-only rootfs where practical | [ ] |
| pip fallback breaks silently | Medium | Medium | clean venv validation, generated requirements/constraints, dependency_update sync checks | [ ] |
| pip install bundle is stale or incomplete | Medium | High | pip_install_bundle, checksum manifest, clean pip-only venv smoke test, release_artifacts_validate | [ ] |
| Slim container becomes a hidden dev environment | Medium | Medium | multi-stage build, wheel-only final stage, dev/test dependency inspection, image size budget | [ ] |
| Dogfood uses special-case shortcuts | Medium | High | recurring dogfood smoke through release artifacts, log-audit bypass detection, release_artifacts_validate | [ ] |
| Library-first rule becomes paperwork | Medium | Medium | library_research_gate, task-return artifacts, return-review checks, follow-up todos for large custom adapters | [ ] |
| Prompt drift | Medium | Medium | Prompt registry, eval, audit, versioning | [ ] |

---

## 26. Open Questions And Configurable Decisions

No current blocker questions remain from the latest user answers. These are configurable choices to set during implementation:

[ ] Exact real remote URL and default target branch.
[ ] Exact OpenBao external address/namespace/mount/role names if not using local bootstrap.
[ ] Exact model IDs for OpenAI, OpenRouter, Z.AI, local endpoints, and any other LangChain provider.
[ ] Whether Sigstore signing should be enabled for commits/tags/artifacts.
[ ] Whether Docker fallback should require per-repo opt-in or remain globally allowed.
[ ] Which queues are allowed to use optional opencode delegation, if any.
[ ] The exact set of high-risk self-improvement tests required before reload.
[ ] Python line and branch coverage thresholds if the defaults of 90 and 80 percent should be changed.
[ ] Whether Molecule coverage minimum should remain 100 percent for registered project-specific Ansible tool units.
[ ] Which runtime profile should be enabled by default in each environment: native_uv, native_pip, or container.
[ ] Exact host paths or named volumes for repo, worktrees, artifacts, logs, Runner private data, and external datasets.
[ ] Whether container root filesystem should be read-only in every profile or only hardened profiles.

---

## 27. Bibliography And Source URLs

Architecture, workers, and Python runtime:

[ ] Gunicorn design: https://docs.gunicorn.org/en/stable/design.html
[ ] Gunicorn project: https://gunicorn.org/
[ ] Uvicorn deployment and uvicorn-worker deprecation notice: https://uvicorn.dev/deployment/
[ ] uvicorn-worker package: https://pypi.org/project/uvicorn-worker/
[ ] FastAPI deployment workers: https://fastapi.tiangolo.com/deployment/server-workers/
[ ] Pydantic: https://docs.pydantic.dev/
[ ] SQLAlchemy: https://docs.sqlalchemy.org/
[ ] Alembic: https://alembic.sqlalchemy.org/
[ ] psycopg: https://www.psycopg.org/psycopg3/docs/
[ ] structlog: https://www.structlog.org/
[ ] prometheus-client Python: https://github.com/prometheus/client_python
[ ] psutil: https://psutil.readthedocs.io/
[ ] Tenacity: https://tenacity.readthedocs.io/

PostgreSQL:

[ ] PostgreSQL SELECT / FOR UPDATE / SKIP LOCKED: https://www.postgresql.org/docs/current/sql-select.html
[ ] PostgreSQL explicit locking: https://www.postgresql.org/docs/current/explicit-locking.html

Ansible, Runner, policy, and audit:

[ ] Ansible Runner introduction: https://docs.ansible.com/projects/runner/en/latest/intro/
[ ] Ansible Runner execution environments: https://docs.ansible.com/projects/runner/en/latest/execution_environments/
[ ] Ansible collections install guide: https://docs.ansible.com/projects/ansible/latest/collections_guide/collections_installing.html
[ ] Ansible Galaxy user guide: https://docs.ansible.com/projects/ansible/latest/galaxy/user_guide.html
[ ] Ansible callback plugins: https://docs.ansible.com/ansible/latest/plugins/callback.html
[ ] ansible-lint configuration: https://docs.ansible.com/projects/lint/configuring/
[ ] ansible-lint usage and custom rule loading: https://docs.ansible.com/projects/lint/usage/
[ ] ansible-lint custom rules: https://docs.ansible.com/projects/lint/custom-rules/
[ ] ansible-lint no-changed-when rule: https://docs.ansible.com/projects/lint/rules/no-changed-when/
[ ] ansible-lint command-instead-of-shell rule: https://docs.ansible.com/projects/lint/rules/command-instead-of-shell/
[ ] Ansible policy-as-code discussion/prototype reference: https://forum.ansible.com/t/welcome-to-the-policy-as-code-forum-get-started-here/5265
[ ] ARA Records Ansible: https://ara.recordsansible.org/
[ ] ARA documentation: https://ara.readthedocs.io/en/latest/
[ ] ARA Ansible plugins and use cases: https://ara.readthedocs.io/en/latest/ansible-plugins-and-use-cases.html

Containers, volumes, and signing:

[ ] Podman rootless tutorial: https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md
[ ] Podman documentation: https://docs.podman.io/
[ ] Podman volume option docs: https://docs.podman.io/en/v4.3/markdown/options/volume.html
[ ] Podman mount option docs: https://docs.podman.io/en/v4.4/markdown/options/mount.html
[ ] Podman volume mount docs: https://docs.podman.io/en/latest/markdown/podman-volume-mount.1.html
[ ] Docker documentation: https://docs.docker.com/
[ ] Docker bind mounts: https://docs.docker.com/engine/storage/bind-mounts/
[ ] Docker volumes: https://docs.docker.com/engine/storage/volumes/
[ ] Docker build best practices and multi-stage builds: https://docs.docker.com/build/building/best-practices/
[ ] Docker Official Python image and slim variants: https://hub.docker.com/_/python
[ ] Docker Official Python image repository: https://github.com/docker-library/python
[ ] Podman build docs: https://docs.podman.io/en/stable/markdown/podman-build.1.html
[ ] Sigstore Cosign verify: https://docs.sigstore.dev/cosign/verifying/verify/
[ ] Cosign repository: https://github.com/sigstore/cosign

OpenBao and secrets:

[ ] OpenBao install docs and container registries: https://openbao.org/docs/install/
[ ] OpenBao AppRole auth: https://openbao.org/docs/auth/approle/
[ ] OpenBao KV secrets engine: https://openbao.org/docs/secrets/kv/
[ ] OpenBao Docker Hub page: https://hub.docker.com/r/openbao/openbao
[ ] hvac documentation: https://hvac.readthedocs.io/
[ ] hvac AppRole usage: https://hvac.readthedocs.io/en/stable/usage/auth_methods/approle.html
[ ] hvac KV v2 usage: https://hvac.readthedocs.io/en/stable/usage/secrets_engines/kv_v2.html

Python dependency management and packaging:

[ ] uv documentation: https://docs.astral.sh/uv/
[ ] uv project guide: https://docs.astral.sh/uv/guides/projects/
[ ] uv pip compile/lock/sync docs: https://docs.astral.sh/uv/pip/compile/
[ ] uv pip compatibility: https://docs.astral.sh/uv/pip/compatibility/
[ ] uv build and package guide: https://docs.astral.sh/uv/guides/package/
[ ] uv build backend notes: https://docs.astral.sh/uv/concepts/build-backend/
[ ] pip documentation: https://pip.pypa.io/
[ ] pip user guide and requirements files: https://pip.pypa.io/en/latest/user_guide/
[ ] pip install requirements option: https://pip.pypa.io/en/latest/cli/pip_install/
[ ] pip wheel command: https://pip.pypa.io/en/stable/cli/pip_wheel/
[ ] PyPA build documentation: https://build.pypa.io/
[ ] Python Packaging tutorial for packages, wheels, and sdists: https://packaging.python.org/tutorials/packaging-projects/
[ ] Python Packaging guide to pyproject.toml: https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
[ ] pyproject.toml specification: https://packaging.python.org/en/latest/specifications/pyproject-toml/
[ ] Hatchling build backend documentation: https://hatch.pypa.io/latest/config/build/
[ ] setuptools pyproject.toml configuration: https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html
[ ] Wheel binary distribution format: https://packaging.python.org/specifications/binary-distribution-format/

LangChain and model providers:

[ ] LangChain model docs: https://docs.langchain.com/oss/python/langchain/models
[ ] LangChain all provider integrations: https://docs.langchain.com/oss/python/integrations/providers/all_providers
[ ] LangChain provider overview: https://docs.langchain.com/oss/python/integrations/providers/overview
[ ] LangChain OpenAI chat integration: https://docs.langchain.com/oss/python/integrations/chat/openai
[ ] LangChain OpenRouter chat integration: https://docs.langchain.com/oss/python/integrations/chat/openrouter
[ ] OpenRouter LangChain guide: https://openrouter.ai/docs/guides/community/langchain
[ ] vLLM OpenAI-compatible server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server/
[ ] llama.cpp server: https://github.com/ggml-org/llama.cpp
[ ] llama-cpp-python OpenAI-compatible server: https://llama-cpp-python.readthedocs.io/en/latest/server/
[ ] Z.AI quick start: https://docs.z.ai/guides/overview/quick-start
[ ] Z.AI OpenAI-compatible Python SDK guide: https://docs.z.ai/guides/develop/openai/python
[ ] Z.AI tool integration notes: https://docs.z.ai/devpack/tool/others
[ ] opencode models docs: https://opencode.ai/docs/models/
[ ] opencode providers docs: https://opencode.ai/docs/providers/
[ ] opencode agents docs: https://opencode.ai/docs/agents/
[ ] opencode repository: https://github.com/opencode-ai/opencode

Testing, Molecule, and coverage:

[ ] Molecule home/about: https://docs.ansible.com/projects/molecule/
[ ] Molecule testing philosophy: https://docs.ansible.com/projects/molecule/philosophy/
[ ] Molecule installation: https://docs.ansible.com/projects/molecule/installation/
[ ] Molecule configuration: https://docs.ansible.com/projects/molecule/configuration/
[ ] Molecule workflow reference: https://docs.ansible.com/projects/molecule/workflow/
[ ] Molecule command line usage: https://docs.ansible.com/projects/molecule/usage/
[ ] Molecule playbook testing guide: https://docs.ansible.com/projects/molecule/getting-started-playbooks/
[ ] Molecule collection testing guide: https://docs.ansible.com/projects/molecule/getting-started-collections/
[ ] Molecule Podman example: https://docs.ansible.com/projects/molecule/examples/podman/
[ ] community.molecule collection: https://docs.ansible.com/projects/molecule/collection/
[ ] pytest-ansible documentation: https://ansible.readthedocs.io/projects/pytest-ansible/
[ ] pytest-ansible Molecule scenario integration: https://ansible.readthedocs.io/projects/pytest-ansible/getting_started/
[ ] Ansible collection testing with ansible-test: https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_collections_testing.html
[ ] pytest documentation: https://docs.pytest.org/
[ ] pytest-cov configuration: https://pytest-cov.readthedocs.io/en/latest/config.html
[ ] coverage.py configuration: https://coverage.readthedocs.io/en/7.13.5/config.html
[ ] pytest-testinfra documentation: https://testinfra.readthedocs.io/

Prompt and agent inspiration:

[ ] Aider repository: https://github.com/Aider-AI/aider
[ ] Aider prompts directory: https://github.com/Aider-AI/aider/tree/main/aider/coders
[ ] Aider documentation: https://aider.chat/docs/
[ ] Goose documentation: https://block.github.io/goose/docs/
[ ] Goose repository: https://github.com/block/goose
[ ] Claude Code public docs: https://docs.anthropic.com/en/docs/claude-code
[ ] Claude Code memory docs: https://docs.anthropic.com/en/docs/claude-code/memory
[ ] Claude Code hooks docs: https://docs.anthropic.com/en/docs/claude-code/hooks
[ ] LangChain prompt templates: https://docs.langchain.com/oss/python/langchain/prompts

Git and automation:

[ ] Git documentation: https://git-scm.com/docs
[ ] git-worktree docs: https://git-scm.com/docs/git-worktree
[ ] git-tag docs: https://git-scm.com/docs/git-tag
[ ] pre-commit documentation: https://pre-commit.com/

---


## Revision 7 Update Notes

[ ] Added first-class runtime delivery requirements for native uv, native pip, and containerized execution with explicit data-source mounts.
[ ] Added runtime profile and data-source mount state concepts.
[ ] Added `runtime_validate.yml`, `native_install_validate.yml`, `container_image_validate.yml`, and `data_source_mount_audit.yml` playbooks.
[ ] Added runtime packaging TDD prompts, quality evidence requirements, first failing tests, dogfood milestone, risk entries, and bibliography references.

---

## Revision 8 Update Notes

[ ] Reinforced mature, maintained FOSS/library/module/collection/tooling preference throughout operating rules, principles, prompts, and quality gates.
[ ] Added `library_research_gate.yml` to make the library-first rule auditable rather than aspirational.
[ ] Added `pip_install_bundle.yml` for a pip-only install bundle containing wheel, sdist, wheelhouse, requirements/constraints, manifest, and checksums.
[ ] Added `slim_agent_container_build.yml` for a multi-stage slim runtime container that just runs the agent from the validated wheel artifact.
[ ] Added `release_artifacts_validate.yml` to block stale/missing release artifacts before tags, pushes, reloads, and dogfood promotion.
[ ] Expanded runtime packaging TDD prompts, sprint board objectives, risks, dogfood milestones, and bibliography references.
[ ] Added continuous self-dogfooding requirements so the harness improves itself through the same tested artifact path used by users.

---

## 28. Living Notes

```text
LIVING_NOTES:
- Revision 6 promoted Molecule tests and configurable coverage gates to first-class sprint requirements.
- Add implementation discoveries here.
- Add dependency research here.
- Add prompt evaluation observations here.
- Add load tuning notes here.
- Add model routing notes here.
- Add ARA evaluation notes here.
- Add OpenBao bootstrap notes here.
- Add action policy false positives/false negatives here.
- Add Molecule scenario and quality-gate tuning notes here.
- Add runtime packaging, native install, container mount, and data-source audit discoveries here.
```
