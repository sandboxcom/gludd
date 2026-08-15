# AI Development Lifecycle Codification

**Document:** `docs/research/AI_DLC_CODIFICATION.md`
**Version:** 1.0
**Date:** 2026-07-11
**Status:** Codified into `config/ai_sdlc.yml` + `general_ludd.agent.sdlc_gate` role

---

## Abstract

This document explains how eight AI development lifecycle frameworks — spanning
AWS, Microsoft, Google, NIST, and peer-reviewed academic research — are codified
into gludd's runtime pipeline. The codification maps each framework's phases,
gates, and evidence requirements to gludd's existing event loop
(`claim → dispatch → review → reconcile`) and its ~109 Ansible roles.

The output is:
- **`config/ai_sdlc.yml`** — machine-readable YAML configuration for the full SDLC pipeline
- **`general_ludd.agent.sdlc_gate`** — an Ansible role that enforces stage transitions
- **This document** — the human-readable explanation of the codification

---

## 1. Sources Codified

| # | Source | Type | Key Insight Codified |
|---|--------|------|---------------------|
| 1 | AWS Well-Architected ML Lens | Industry framework | 6 pillars mapped to gludd phases + roles |
| 2 | Microsoft CAF for AI | Industry framework | 6-phase model (Strategy→Manage) mapped to event loop phases |
| 3 | Google Cloud MLOps | Industry framework | Quality gates per stage (data, model, code, test, security, deploy) |
| 4 | NIST AI RMF | Government standard | Govern→Map→Measure→Manage with GenAI action adoption tracking |
| 5 | AI-SDLC Protocol Language | Academic (arXiv:2606.20615) | 2+N team pattern, validation token chain from spec→release |
| 6 | Agentic Agile-V | Academic (arXiv:2605.20456) | SCOPE-V loop, conversation-to-contract gate, evidence-bundle acceptance |
| 7 | Rethinking SE for Agentic AI | Academic (arXiv:2604.10599) | Verification-first lifecycle, layered verification, fail-closed defaults |
| 8 | AI Assurance Pyramid | Academic (arXiv:2605.23459) | 5-layer testing strategy (static → unit → integration → deploy → monitor) |

---

## 2. Core Mapping: Frameworks → gludd Event Loop

gludd's daemon event loop runs a 5-phase cycle every tick:

```text
claim → dispatch → review → reconcile → (repeat)
```

Every AI-DLC framework maps to one or more of these phases. The mapping is
not metaphorical — each phase triggers specific Ansible roles that implement
the framework's requirements.

### 2.1 claim — Intake + Planning

**Event loop action:** The daemon queries runnable todos from the DB via
`_claim_runnable_todos()`, marks them `in_progress` with CAS-based optimistic
locking.

**Frameworks mapped:**

| Framework | Phase/Component | Codified in |
|-----------|----------------|-------------|
| Microsoft CAF | Strategy + Plan + Ready | `sprint_plan`, `story_create`, `project_init` roles |
| NIST AI RMF | Map | `triage_issue`, `spec_lifecycle` APPROACH.md |
| AI-SDLC Protocol | CLAIM + PLAN verbs | `spec_lifecycle.create_task` → `approve_task` |
| Agentic Agile-V | Stake → Capability | `story_create` BUSINESS_CONTEXT.md → `spec_lifecycle` |
| AWS ML Lens | Operational Excellence (planning) | `backlog_groom`, `estimate_story` |

**Validation token produced:** `spec_token` → `approach_token`

### 2.2 dispatch — Implementation

**Event loop action:** `_dispatch_execute_jobs()` fans out execution to the
Ansible runner, which invokes `agent_task` / `implement_change` roles in
worktree-isolated environments.

**Frameworks mapped:**

| Framework | Phase/Component | Codified in |
|-----------|----------------|-------------|
| AI-SDLC Protocol | IMPLEMENT verb | `agent_task` role with worktree isolation |
| Agentic Agile-V | Outcome | `implement_change` + `write_tests` |
| Verification-First | TDD enforcement | `enforce-make.ts` TDD reminder; `make test-count` before commit |
| AWS ML Lens | Performance Efficiency + Cost Optimization | `budget_guard`, `cost_audit`, `spend_limiter` |
| Google MLOps | Model quality gate | `budget_guard` pre-check, model health check |

**Validation token produced:** `code_token`

### 2.3 review — Multi-Layer Verification

**Event loop action:** `_review_completed_tasks()` ingests task returns and
optionally dispatches a different model for review. The reviewing model
produces a verdict (approve/retry/reject) that feeds into reconciliation.

**Frameworks mapped:**

| Framework | Phase/Component | Codified in |
|-----------|----------------|-------------|
| NIST AI RMF | Measure | `gate_triage`, `coverage_audit`, `code_reviewer` |
| Google MLOps | Code + Test + Security quality gates | Compose `gate_triage` + `security_gate` + `enforcement_gate` |
| AI-SDLC Protocol | REVIEW + TEST + GATE verbs | `code_reviewer` → `gate_triage` → `security_gate` → `enforcement_gate` |
| Verification-First | Layered verification | 5 layers: structural → behavioral → correctness → security → compliance |
| AI Assurance Pyramid | Layers 1-3 (static, unit, integration) | `lint_and_check`, `run_tests`, `test_matrix` |
| AWS ML Lens | Security + Reliability | `security_gate`, `flaky_quarantine`, `validate_and_push` |

**Validation token produced:** `review_token` → `gate_token`

**This is the BLOCKING stage** — if any quality gate fails, the task is
blocked and cannot advance to reconciliation. Per the fail-closed pattern
from `security_gate` and `enforcement_gate`: a missing required check =
`gate_passed: false` = pipeline blocked.

### 2.4 reconcile — Integration + Deployment

**Event loop action:** `_reconcile_decisions()` processes review verdicts:
- `approve` → merge worktree branch to master, clean up, push
- `retry` → re-dispatch with feedback
- `reject` → mark task as `reconciled` with rejection reason

**Frameworks mapped:**

| Framework | Phase/Component | Codified in |
|-----------|----------------|-------------|
| Microsoft CAF | Govern + Secure + Manage | `enforcement_gate` push guard, `security_gate` final check, `release_build` |
| NIST AI RMF | Manage | Risk treatment (approve=accept, retry=mitigate, reject=avoid) |
| AI-SDLC Protocol | RELEASE verb | `validate_and_push` → `release_build` → `ci_pipeline_verify` |
| Agentic Agile-V | Validate | Evidence-bundle verification before acceptance |
| AI Assurance Pyramid | Layers 4-5 (deploy, monitor) | `observe_deploy_correlator`, `retrospective` |
| AWS ML Lens | Sustainability | `dead_code_auditor`, `dry_code_auditor` |

**Validation token produced:** `merge_token` → `release_token` → `production_token`

---

## 3. Key Codification Decisions

### 3.1 Validation Token Chain (from arXiv:2606.20615)

The AI-SDLC Protocol Language defines a chain of validation tokens that
track a task's passage through the pipeline. Each token is produced by
one stage and consumed by the next:

```text
spec_token  →  approach_token  →  code_token  →  review_token
    →  gate_token  →  merge_token  →  release_token  →  production_token
```

gludd implements this via artifact files in `artifact_dir/`. The `sdlc_gate`
role checks for the presence and validity of each token before allowing a
stage transition. This makes the token chain observable and auditable — a
task cannot silently skip a stage.

### 3.2 Conversation-to-Contract Gate (from arXiv:2605.20456)

The Agentic Agile-V paper identifies a critical boundary: the moment when
human-agent conversation (open-ended planning) becomes contractual
implementation (locked specification). gludd implements this through
`spec_lifecycle.approve_task`:

- **Before:** APPROACH.md is in `drafts/` — mutable, conversational
- **After:** APPROACH.md moves to `active/` — immutable contract
- **Enforcement:** `spec_lifecycle.complete_task` checks that every AC
  in the contract is satisfied before archiving

This is enforced structurally: once a task is approved, its plan cannot
change without running `spec_lifecycle.revise` (explicit opt-out, not
accidental drift).

### 3.3 Evidence-Bundle Acceptance (from arXiv:2605.20456)

The paper's central claim: a task is accepted ONLY when its evidence-bundle
is verified — not on the agent's assertion. gludd codifies this through:

1. **AGENTS.md verification evidence table** — every scope (unit fix, local gate,
   commit, push, CI, release) requires a specific machine-produced measurement
2. **`enforce-verified-claims.ts`** — structurally blocks text containing
   done-words ("landed", "fixed", "shipped") without evidence tokens
3. **`sdlc_gate` evidence checks** — recursively verifies that every required
   artifact for the current stage exists and is valid before advancing

The evidence bundle per task:
- **Scope evidence:** acceptance criteria checked, BUSINESS_CONTEXT.md
- **Execution evidence:** commit SHAs, unified diff
- **Quality evidence:** `.gate-status` PASS, coverage maintained
- **Review evidence:** code_reviewer verdict, security_gate pass
- **Deployment evidence:** `VERIFIED <branch>@<sha>`, CI green, artifact verified

### 3.4 Verification-First Lifecycle (from arXiv:2604.10599)

The paper argues that agentic SE must invert the traditional lifecycle:
verification before implementation, not after. gludd implements this at
four levels:

1. **TDD policy** (`AGENTS.md`): write failing test FIRST, run it, THEN implement
2. **Layered verification**: structural static analysis (lint, typecheck) runs
   before behavioral tests (unit, integration) before deployment checks (CI)
3. **Observable verification**: gate output pasted alongside claims, not inferred
4. **Fail-closed defaults**: `security_gate` and `enforcement_gate` treat missing
   checks as blocks, not passes

### 3.5 AI Assurance Pyramid (from arXiv:2605.23459)

The 5-layer pyramid maps directly to gludd's existing role families:

| Layer | Name | gludd Roles |
|-------|------|------------|
| 1 | Static Analysis | `lint_and_check`, `type_safety_audit`, `scan_conflict_markers` |
| 2 | Unit Testing | `run_tests`, `coverage_audit`, `task_deadline_check` |
| 3 | Integration Testing | `test_matrix`, `flaky_quarantine`, molecule scenarios |
| 4 | Deployment Testing | `validate_and_push`, `ci_pipeline_verify`, `release_build` |
| 5 | Production Monitoring | `observe_deploy_correlator`, `observe_error_spike_rca`, `soc_analyst` |

Each layer gates the next — a failure at Layer 1 blocks Layer 2, etc.
This is enforced by the `sdlc_gate` role's composing check pattern (mirroring
`security_gate`).

### 3.6 2+N Team Pattern (from arXiv:2606.20615)

The paper proposes a "2+N" team: 2 humans (product owner + domain expert)
and N AI agents (implementers + reviewer + integrator). gludd maps this to:

- **Human pair:** `story_create` (product owner), `estimate_story` (domain expert)
- **Agent cluster:** `agent_task` × N (10-agent floor), `code_reviewer` (reviewer),
  `agent_orchestrate` (integrator)

The 10-agent floor (`CLAUDE_AGENT_FLOOR=10`) ensures sufficient parallel
implementation capacity, while the review/reconcile phases provide the
independent verification that the 2+N model requires.

### 3.7 NIST AI RMF GenAI Action Tracking

`config/ai_sdlc.yml` includes an adoption matrix tracking which of the 200+
GenAI-specific actions from NIST AI RMF Profile (600-1) are adopted in gludd.
Each entry names the specific role, plugin, or policy that implements it.
Currently codified: 20 actions across the Govern/Map/Measure/Manage functions.

---

## 4. How gludd's Existing Roles Map to SDLC Stages

gludd already has 109 Ansible roles covering every SDLC phase. The
`config/ai_sdlc.yml` `role_stage_mapping` section categorizes them into
the 8 pipeline stages:

```text
┌──────────────┐   ┌───────────────┐   ┌────────────────┐   ┌──────────────┐
│   INTAKE     │   │   PLANNING    │   │ IMPLEMENTATION │   │    REVIEW    │
│              │   │               │   │                │   │              │
│ triage_issue │──▶│ spec_lifecycle│──▶│  agent_task    │──▶│code_reviewer │
│ story_create │   │ sprint_plan   │   │implement_change│   │ gate_triage  │
│ backlog_groom│   │ estimate_story│   │ write_tests    │   │security_gate │
└──────────────┘   └───────────────┘   └────────────────┘   └──────────────┘
                                                                     │
                                                                     ▼
┌──────────────┐   ┌───────────────┐   ┌────────────────┐   ┌──────────────┐
│   OPERATE    │   │  DEPLOYMENT   │   │  INTEGRATION   │   │     GATE     │
│              │   │               │   │                │   │              │
│ observe_*    │◀──│ release_build │◀──│validate_and_push│◀──│enforcement_  │
│ retrospective│   │ci_pipeline_   │   │git_commit_push │   │gate          │
│ soc_analyst  │   │verify         │   │agent_orchestrate│  │verify_feature│
└──────────────┘   └───────────────┘   └────────────────┘   │_claims       │
                                                            └──────────────┘
```

Each stage has:
- **Required roles** that MUST execute before the stage is considered complete
- **Gatekeeper roles** that provide additional validation
- **Entry gates** checked by `sdlc_gate` before the stage begins
- **Exit gates** checked by `sdlc_gate` before advancing to the next stage
- **Validation tokens** produced on successful exit
- **Evidence artifacts** written to `artifact_dir/`

---

## 5. The sdlc_gate Role

The `general_ludd.agent.sdlc_gate` role (new) is the PIECE that enforces
stage transitions at runtime. It mirrors the composing fail-closed pattern
of `security_gate` and the fail-closed enforcement of `enforcement_gate`:

### Design

- **SAFE-BY-DEFAULT:** never mutates the repo or triggers side effects
- **FAIL-CLOSED:** a missing required artifact or failed check = `gate_passed: false`
- **COMPOSING:** ingests per-stage result JSONs from `artifact_dir/`
- **OBSERVABLE:** emits `gludd_message` on every stage transition
- **TIMEOUT-AWARE:** checks stage elapsed time against `stage_timeouts` config

### Operations

The role is invoked with an `operation` parameter:

| Operation | Description |
|-----------|-------------|
| `check_stage_entry` | Verify entry gate for a stage is satisfied |
| `check_stage_exit` | Verify exit gate for a stage is satisfied (all roles complete, all checks pass) |
| `advance_stage` | Mark a stage as complete, produce validation token, emit transition event |
| `audit_token_chain` | Walk the token chain for a task and report missing/broken tokens |
| `list_stages` | Enumerate all stages and their status for a task |
| `validate_pipeline` | End-to-end pipeline health check |

### Stage Check Logic

For each stage, the role:
1. Loads the stage config from `config/ai_sdlc.yml`
2. Checks all `entry_gate` conditions (for entry check) or `exit_gate` conditions (for exit check)
3. Verifies required evidence artifacts exist and are well-formed
4. Checks timeout constraints (elapsed time vs `stage_timeouts`)
5. Returns `gate_passed: true/false` with per-check details
6. On block: emits `gludd_message priority=high` + `topic=sdlc_gate_blocked`

---

## 6. Integration with gludd's Enforcement Stack

The SDLC codification integrates with all three layers of gludd's enforcement
architecture:

### Layer 1: Config (opencode.json / make gate)
- `config/ai_sdlc.yml` defines the pipeline spec
- `make gate` includes `sdlc_gate` checks via the gate pipeline
- `GLUDD_SDLC_ENFORCE=1` elevates advisory checks to blocking

### Layer 2: Runtime Hook (opencode plugin)
- `enforce-stop.ts` checks that no task advances past a blocking stage
  with a red gate (mirroring the enforcement_gate commit guard)
- `enforce-deadline.ts` checks stage elapsed time against configured timeouts

### Layer 3: Agent Prompt (AGENTS.md)
- The 7-rule mechanical contract covers TDD, evidence, gate freshness
- The "Done Claims Require Observable Verification Evidence" table maps
  directly to evidence-bundle acceptance
- The "No Unseen Events" invariant ensures stage transitions are observable

---

## 7. Usage

### View the pipeline for a task

```bash
gludd sdlc status --task-id <id>
```

### Validate the token chain

```bash
gludd sdlc audit-tokens --task-id <id>
```

### Enforce a stage gate (via Ansible)

```yaml
- name: Enforce review stage gate
  general_ludd.agent.sdlc_gate:
    operation: check_stage_exit
    stage: review
    task_id: "{{ task_id }}"
    artifact_dir: "/tmp/gludd-sdlc-{{ task_id }}"
    daemon_url: "{{ daemon_url }}"
    psk: "{{ psk }}"
  register: sdlc_result
  failed_when: not sdlc_result.gate_passed
```

### Stage advancement in the event loop

The daemon's event loop (`src/general_ludd/event_loop/loop.py`) calls
`sdlc_gate` at each phase boundary. The `_claim_runnable_todos()` phase
checks that a task's current stage has its entry gates satisfied before
claiming it. The `_reconcile_decisions()` phase checks exit gates before
advancing.

---

## 8. Research-to-Code Traceability

Every framework concept maps to a specific, grep'able implementation artifact:

| Research Concept | Implementation Artifact |
|-----------------|------------------------|
| AI-SDLC Protocol CLAIM verb | `src/general_ludd/event_loop/loop.py` `_claim_runnable_todos()` |
| Conversation-to-contract gate | `spec_lifecycle/tasks/main.yml` `approve_task` → APPROACH.md immutable |
| Evidence-bundle acceptance | `enforce-verified-claims.ts` + AGENTS.md evidence table |
| 2+N team pattern | AGENTS.md `CLAUDE_AGENT_FLOOR=10` + 109 roles in `collections/.../roles/` |
| Verification-first lifecycle | `enforce-make.ts` TDD reminder + gate_triage + code_reviewer |
| AI Assurance Pyramid Layer 1 | `lint_and_check/tasks/main.yml` |
| AI Assurance Pyramid Layer 5 | `observe_deploy_correlator/tasks/main.yml` |
| NIST AI RMF Govern GV-1.1 | `enforcement_gate/tasks/main.yml` fail-closed pattern |
| NIST AI RMF Map MAP-1.1 | `story_create` BUSINESS_CONTEXT.md |
| NIST AI RMF Measure MEAS-2.1 | `code_reviewer/tasks/main.yml` structured findings |
| Microsoft CAF Govern phase | `enforcement_gate` push guard + gate freshness |
| AWS ML Lens Security pillar | `security_gate` composing check (5 sub-checks) |
| Google MLOps deploy quality gate | `validate_and_push` → `ci_pipeline_verify` → `release_build` |

---

## 9. Future Work

Areas where the research implies capability not yet implemented:

1. **Automated conversation-to-contract detection:** arXiv:2605.20456 proposes
   LLM-based detection of when a conversation shifts from planning to commitment.
   Currently gludd requires explicit `approve_task` — a future `conversation_monitor`
   role could detect the boundary automatically.

2. **Dynamic team sizing (2+N):** The 2+N paper allows N to vary by task complexity.
   gludd's 10-agent floor is static. A future `dynamic_floor_adjuster` could scale
   the pool based on `estimate_story` size.

3. **Cross-project token chain:** Validation tokens are currently per-task. A
   cross-project token (e.g., a "platform_token" that gates multi-project releases)
   would implement the multi-team pattern from the protocol language.

4. **NIST AI RMF full action coverage:** Currently 20/200+ GenAI actions are
   codified. The adoption matrix in `config/ai_sdlc.yml` provides the gap-tracking
   structure for incremental coverage.

5. **AI Assurance Pyramid automation:** Layer 3 (integration testing) and Layer 4
   (deployment testing) currently require explicit role invocation. A future
   "auto-pyramid" mode could chain all 5 layers automatically in a single operation.

---

## 10. References

1. AWS, "Machine Learning Lens — AWS Well-Architected Framework," 2023.
   `https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/`

2. Microsoft, "Cloud Adoption Framework for AI," 2024.
   `https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/`

3. Google Cloud, "Practitioners Guide to MLOps," 2021.
   `https://cloud.google.com/resources/mlops-whitepaper`

4. NIST, "AI Risk Management Framework (AI RMF 1.0)," January 2023.
   `https://www.nist.gov/itl/ai-risk-management-framework`

5. arXiv:2606.20615, "AI-SDLC Protocol Language: A Protocol Language for the
   AI-Native Software Development Lifecycle," 2026.

6. arXiv:2605.20456, "Agentic Agile-V: An Agile Software Development Model
   for Agentic Coding," 2026.

7. arXiv:2604.10599, "Rethinking Software Engineering for Agentic Artificial
   Intelligence," 2026.

8. arXiv:2605.23459, "The AI Assurance Pyramid: A Five-Level Framework for
   Testing AI-Generated Code," 2026.

---

*Codified by gludd on 2026-07-11. Machine-enforced via `config/ai_sdlc.yml` +
`general_ludd.agent.sdlc_gate`.*
