# Self-Update Router — Design (#81)

**Status:** Design-only. Implementation-ready. No code/tests written here.
**Scope:** A user files a gludd todo whose title/description is a natural-language
self-update request — `"update gludd: <NL request>"` — and gludd (a) identifies the
targeted subsystem, (b) decides *how* to apply the change (config/YAML first,
real-code-capable but guarded), (c) prioritizes it into the backlog, and (d)
refuses to touch its own guardrails/security/settings without explicit approval,
leaving an audit trail.

Every code reference below is grounded in files read for this design. Paths are
absolute from the repo root
`/Users/shawnwilson/gludd/.claude/worktrees/agent-a01e70a51186db3a8/`.

---

## 0. Grounding — what already exists

| Concern | File | Symbols |
|---|---|---|
| Todo data model (Pydantic) | `src/general_ludd/schemas/todo.py` | `Todo`, `TodoStatus`, `WorkType`, `RiskLevel`, `ResourceProfile`, `VALID_TRANSITIONS`, `validate_transition` |
| Todo ORM + audit + queues | `src/general_ludd/db/models.py` | `TodoModel`, `QueueModel` (`priority_weight`, `resource_profile`, `pid_group`, `allowed_playbooks`), `AuditEventModel` (`actor`, `entity_type`, `entity_id`, `correlation_id`, `details`) |
| Persistence | `src/general_ludd/db/repository.py` | `TodoRepository.create/update/transition/claim_runnable`, `AuditEventRepository.record_typed`, `AuditEventType` |
| Tool-call routing | `src/general_ludd/dispatch/dynamic_dispatcher.py` | `DynamicDispatcher.dispatch`, `ToolCall`, `DispatchResult`, role-gated via `role_may_dispatch` |
| Hot reload (config + code) | `src/general_ludd/reload/hot_reloader.py` | `HotReloader.reload(ReloadScope)`, `HotReloader.reload_code_module(...)`, `ReloadScope`, `ReloadResult` |
| Capability lattice + guards | `src/general_ludd/security/capability_lattice.py` | `RoleCapabilities`, `capabilities_for`, `role_may_dispatch`, `check_dispatch`, `check_self_modification`, `is_protected_path`, `is_collections_path`, `PROTECTED_FILE_STEMS`, `PROTECTED_PATH_SUBSTRINGS`, `CapabilityError`, `ProtectedPathError` |
| Self-improvement | `src/general_ludd/self_improve/harness.py` | `SelfImprovementHarness.run_gap_analysis()` |
| Scheduler | `src/general_ludd/scheduling/scheduler.py` | `Scheduler.plan(list[WorkItem]) -> list[list[str]]`, `WorkItem(id, resources, depends_on, is_greenfield)`, `can_run_concurrently`, `CycleError` |
| Config | `src/general_ludd/config/user_config.py` | `UserConfig` (`model_routing`, `model_profiles`, `agents`, `budget`, `observability`, `queues`, `self_improve`), `load_user_config` semantics |

**Two facts that shape this entire design:**

1. **The guards already exist and already fail closed.** `check_self_modification`
   (capability_lattice.py:211-235) enforces *two ordered gates* — a protected-path
   deny-list (`PROTECTED_FILE_STEMS` ∪ `PROTECTED_PATH_SUBSTRINGS`) that is **never**
   swappable for any role, then a `collections_self_modify` capability check. The
   self-update router does **not** invent new guards; it *routes every code change
   through this existing function* and never bypasses it.

2. **Code reload already fails closed without a passing health gate.**
   `reload_code_module` (hot_reloader.py:112-249) refuses an unverified swap: a
   missing `health_check`, a raising one, or a failing one all roll the live module
   back to the original bytes and return `success=False`. The router rides this — it
   never writes a `.py` file in place by itself.

---

## 1. NL → subsystem classifier

### 1.1 Subsystem taxonomy

A `SubsystemId` enum enumerates every routable target. Each entry maps to a
`Subsystem` descriptor: its preferred change channel (config/code), its YAML
glob(s), the `WorkType` it produces, the role required to touch it, and whether it
is guard-adjacent. Grounded in the directory layout and `UserConfig` fields read.

| `SubsystemId` | What it is | Primary change channel | Target locations (grounded) |
|---|---|---|---|
| `MODEL_ROUTING` | model→provider routing | **config** | `config/model_routing.yml` (read by `HotReloader._reload_models`, hot_reloader.py:335) |
| `MODEL_PROFILES` | per-model profiles | **config** | `UserConfig.model_profiles`, `profiles:` in `model_routing.yml` (hot_reloader.py:350) |
| `QUEUES` | queue caps/weights/PID groups | **config** | `UserConfig.queues`; `QueueModel` cols `priority_weight`, `hard_cap`, `soft_cap`, `resource_profile`, `pid_group`, `allowed_playbooks` (models.py:222-233) |
| `BUDGET_SPEND` | spend caps | **config** | `UserConfig.budget` |
| `OBSERVABILITY` | otel endpoint / service name | **config** | `UserConfig.observability` (`ObservabilityConfig`) |
| `AGENTS` | agent/runtime knobs | **config** | `UserConfig.agents`, `UserConfig.process_isolation` |
| `PROMPTS_TEMPLATES` | prompt templates | **config (template)** | `templates/*.j2` (hot_reloader.py:391); `prompt_profile` on `Todo` |
| `PLAYBOOKS` | work-type playbooks | **config (template)** | `playbooks/*.yml` (hot_reloader.py:402) |
| `SKILLS` | skill docs | **config (template)** | `skills_dirs/*.md` (hot_reloader.py:419) |
| `ROLES` | Ansible roles | **template→code** | `collections/.../agent/roles/` (collections tree — guarded) |
| `CONNECTORS_MCP` | MCP connectors | **template→code** | `collections/.../plugins/`, MCP config |
| `SCORING` | scoring/eval logic | **code** | scoring modules under `src/general_ludd/` |
| `GATEWAY` | model gateway | **code** | gateway modules under `src/general_ludd/` |
| `SCHEDULER_DISPATCH` | scheduling/dispatch | **code** | `scheduling/`, `dispatch/` |
| `SELF_IMPROVE` | gap-analysis harness | **code** | `self_improve/` |
| `GUARDRAILS_SECURITY` | the guards themselves | **DENY / approval-only** | matches `PROTECTED_FILE_STEMS`/`PROTECTED_PATH_SUBSTRINGS` (capability_lattice.py:41-64) |
| `UNKNOWN` | unclassified | — | requires human disambiguation |

The `GUARDRAILS_SECURITY` row is a *first-class taxonomy entry* precisely so the
classifier can recognize and **refuse** it early (§3), rather than discovering the
deny-list only at write time.

### 1.2 Routing strategy (layered, cheap-first, fail-soft)

A three-tier classifier; each tier may short-circuit. No tier ever auto-applies a
change — classification only produces a *plan* for §2/§4 to act on.

1. **Tier 0 — explicit override.** If the request contains a fenced directive
   (`subsystem: queues`) or a known path token, accept it directly. Zero model cost.
2. **Tier 1 — keyword/lexical.** A static keyword map per `SubsystemId`
   (`{"model routing","provider"} → MODEL_ROUTING`, `{"queue","cap","weight"} →
   QUEUES`, `{"prompt","template"} → PROMPTS_TEMPLATES`, `{"guardrail","security",
   "permission","policy"} → GUARDRAILS_SECURITY`, …). Produces a ranked candidate
   list with confidence. High single-winner confidence → done.
3. **Tier 2 — LLM-assisted (Claude).** On ambiguity/low confidence, ask the model to
   pick from the enumerated `SubsystemId` set and emit a structured
   `SubsystemId + change_kind + target_files + rationale`. Routed through the
   existing model gateway. The prompt **constrains output to the closed enum** —
   the model classifies, it does not free-form a path. Disagreement between Tier 1
   and Tier 2, or any `GUARDRAILS_SECURITY` signal, lowers `auto_apply` eligibility
   (forces approval; see §2/§3).

> Embedding-based routing is a documented future option (embed request vs. a
> per-subsystem corpus, cosine top-k) but is **not** required for v1; the keyword +
> LLM tiers cover the taxonomy. Whichever tier resolves, the result is identical in
> shape, so the apply ladder is classifier-agnostic.

### 1.3 Data model — `SelfUpdateRequest` / `SelfUpdatePlan`

New module: `src/general_ludd/self_update/models.py`. Pydantic, mirroring the
`Todo` schema conventions.

```text
class ChangeKind(StrEnum):
    CONFIG_VALUE        # edit a value/var in an existing YAML/config (preferred)
    TEMPLATE_EDIT       # edit a prompt template / playbook / skill doc
    NEW_FROM_TEMPLATE   # scaffold a new role/connector/profile from a template
    SOURCE_CODE         # real .py change (guarded ladder rung 3)
    GUARD_CHANGE        # touches guardrails/security/settings -> approval-only/refuse

class ApplyChannel(StrEnum):
    HOT_RELOAD_CONFIG   # HotReloader.reload(ReloadScope.*)
    HOT_RELOAD_CODE     # HotReloader.reload_code_module(...)  (guarded)
    NONE                # refused / human-only

class SelfUpdateRequest(BaseModel):
    request_id: str            # f"SUR-{uuid4().hex[:8].upper()}"
    raw_text: str              # the NL request verbatim (audit fidelity)
    source_todo_id: str | None # the originating Todo, if filed as one
    project_id: str | None = None
    created_by: str = "user"

class TargetFile(BaseModel):
    path: str                  # resolved absolute path
    is_protected: bool         # is_protected_path(path)
    is_collections: bool       # is_collections_path(path)

class SelfUpdatePlan(BaseModel):
    request_id: str
    subsystem: SubsystemId
    change_kind: ChangeKind
    apply_channel: ApplyChannel
    target_files: list[TargetFile]
    reload_scope: ReloadScope | None      # for HOT_RELOAD_CONFIG
    module_name: str | None               # for HOT_RELOAD_CODE
    classifier_confidence: float          # 0..1
    classifier_tier: Literal["override","keyword","llm"]
    requires_approval: bool               # computed (§2.4)
    refused: bool = False                 # set by guard gate (§3)
    refusal_reason: str | None = None
    work_type: WorkType                   # for the backlog Todo (§4)
    risk_level: RiskLevel                 # drives priority + approval (§4)
    rationale: str
```

`SelfUpdatePlan` is the single artifact handed to §2 (apply), §3 (guard), and §4
(prioritize). It is serialized into the originating `Todo.plan_artifact` /
`Todo.artifacts` (todo.py:124-126) so the plan is inspectable and audit-linked.

---

## 2. Apply strategy ladder — YAML/config FIRST

Rungs are tried in order; the router selects the **lowest rung that can satisfy the
request**. Lower rungs are lower-risk, hot-reloadable, and reversible.

### Rung (a) — pure config/var/template edit *(preferred, low-risk, hot-reloadable)*

- **When:** `change_kind ∈ {CONFIG_VALUE, TEMPLATE_EDIT}` and the target is an
  existing YAML/template under `config/`, `templates/`, `playbooks/`, or a skills dir.
- **How:** edit the YAML/template value, then call
  `HotReloader.reload(scope)` with the matching `ReloadScope`
  (`MODELS` for `model_routing.yml`, `TEMPLATES` for `*.j2`, `PLAYBOOKS` for
  `*.yml`, `SKILLS` for `*.md`, or `CONFIG`/`ALL`). `reload()` returns a
  `ReloadResult(success, scope, details)` and publishes `ConfigReloadedEvent` /
  `ReloadCompletedEvent`. **No process restart, no `.py` swap.**
- **Validation:** YAML must parse (the reload path already guards this — e.g.
  `_reload_models` records `parse_error` and returns `models_reloaded=False` on a
  bad parse, hot_reloader.py:343-346). The router treats a non-success
  `ReloadResult` as a failed apply and does not mark the Todo complete.
- **Reversibility:** the edited YAML is the rollback unit; the prior value is
  captured in the audit `details` (§3) before the edit.

### Rung (b) — new role/connector/profile from a template *(scaffold)*

- **When:** `change_kind == NEW_FROM_TEMPLATE` (e.g. "add a connector for X",
  "add a model profile Y").
- **How:** render a vetted template into a *new* file (not an in-place edit of live
  code). For a new model profile this can degrade to rung (a) (add a `profiles:`
  entry to `model_routing.yml` → `ReloadScope.MODELS`, which `_reload_models` applies
  via `update_routing_config`/`add_profile`, hot_reloader.py:357-373) — **prefer that
  path**. For a new role/connector under the `collections/` tree, the file lands in a
  collections path, so the write is gated by `check_self_modification` and requires a
  `collections_self_modify` role (capability_lattice.py:229-234).
- **Validation:** lint + typecheck the scaffold; a new playbook/template is picked
  up by the matching reload scope.

### Rung (c) — real source-code change *(guarded; last resort)*

- **When:** `change_kind == SOURCE_CODE` — the request genuinely needs a `.py` edit
  in a routable code subsystem (`SCORING`, `GATEWAY`, `SCHEDULER_DISPATCH`,
  `SELF_IMPROVE`, or a collections role).
- **How (no shortcuts):** the router prepares the candidate source as a *separate
  file* and calls
  `HotReloader.reload_code_module(module_name, candidate_source_path, health_check=..., role=...)`.
  That function (hot_reloader.py:112-249), in order:
  1. resolves the live `__file__`;
  2. calls `check_self_modification(live_path, role)` **before any byte is written** —
     protected guard files raise `ProtectedPathError`; a collections swap without
     `collections_self_modify` raises `CapabilityError`; both abort the swap;
  3. snapshots the live bytes, `os.replace`s the candidate over the live path,
     `importlib.reload`s;
  4. requires a **passing `health_check`** — a missing/raising/failing gate rolls
     back to the original bytes and returns `success=False` with `rolled_back=True`
     (hot_reloader.py:219-245). The router **always supplies a real health check**
     (e.g. a `/readyz` poll) — never `None`.
- **Pre-land validation gate:** before invoking `reload_code_module`, the candidate
  must pass `make lint`, `make typecheck`, and the test gate (`make test` /
  `make qa`). The router does not call these directly in-process; it files the
  code-change work as a `Todo` whose `test_commands`/`acceptance_criteria` carry the
  gate, so the normal TDD pipeline (per `AGENTS.md`) validates it before any swap is
  attempted. A code-change `SelfUpdatePlan` therefore lands as a gated backlog item,
  not an inline mutation.

### 2.4 Auto-apply vs. approval matrix

`requires_approval` is computed from `change_kind`, `risk_level`, target protection,
and classifier confidence:

| Condition | `requires_approval` | Rung |
|---|---|---|
| `change_kind == CONFIG_VALUE`, non-guard target, `risk_level ≤ LOW`, high confidence | **No (auto)** | (a) |
| `change_kind == TEMPLATE_EDIT`, non-guard, confidence high | **No (auto)** | (a) |
| `change_kind == NEW_FROM_TEMPLATE` reducible to a config add | **No (auto)** | (b)→(a) |
| `NEW_FROM_TEMPLATE` writing a collections file | **Yes** | (b) |
| `change_kind == SOURCE_CODE` (any) | **Yes** | (c) |
| `risk_level ≥ HIGH`, or low classifier confidence, or Tier1/Tier2 disagreement | **Yes** | any |
| Any `GUARDRAILS_SECURITY` / protected-path signal (`change_kind == GUARD_CHANGE`) | **Yes — and refused by default** (§3) | NONE |

Auto-appliable changes (config rung (a), low risk) flow straight through
`HotReloader.reload(...)`. Everything else parks the Todo in
`TodoStatus.APPROVAL_REQUIRED` (todo.py:21) — a real, existing status whose only
exits are `QUEUED`, `CANCELLED`, `MANUAL_HOLD` (todo.py:87). The Todo's
`approval_policy` field (todo.py:129) records *which* policy gated it.

---

## 3. Guardrails — never modify its own guards/security/settings without approval

This is the load-bearing section. The router **reuses, never re-implements**, the
existing guards.

### 3.1 Refusal is structural, not advisory

Two enforcement points, both already fail-closed:

1. **Classification-time refusal.** If the classifier resolves
   `SubsystemId.GUARDRAILS_SECURITY`, or any resolved `TargetFile.path` satisfies
   `is_protected_path(path)` (capability_lattice.py:195-208 — matches
   `PROTECTED_FILE_STEMS` = `{guardrails, capability_policy, capability_lattice,
   fs_write_policy, action_policy, permissions, permission, policy, enforce_make}`
   or any of `PROTECTED_PATH_SUBSTRINGS` = `/.opencode/`, `/.claude/`,
   `/module_utils/capability_policy`, `/module_utils/fs_write_policy`,
   `/security/capability_lattice`), the router sets `plan.refused = True`,
   `apply_channel = NONE`, and records `refusal_reason`. It does **not** queue an
   auto-apply. The request may only proceed as a `MANUAL_HOLD`/`APPROVAL_REQUIRED`
   Todo for a human to action *outside* the auto-update path. **Settings files
   (`/.claude/`, `/.opencode/`) are inside the protected substrings and are never
   touched by this router** — consistent with the standing instruction to never
   modify `.claude`/`.opencode`/settings.

2. **Apply-time refusal (defense in depth).** Even if a classification bug let a
   protected path through, `reload_code_module` calls
   `check_self_modification(live_path, role)` *before writing a byte*
   (hot_reloader.py:167-176). A protected file raises `ProtectedPathError`; the swap
   aborts with the live module byte-for-byte unchanged. The router therefore cannot
   modify a guard file even via a misrouted code change. This is the same guard the
   dispatcher consults via `role_may_dispatch` (dynamic_dispatcher.py:183) for the
   `collection` kind, which additionally requires `collections_self_modify`
   (capability_lattice.py:154-168).

**Critically: the router runs under a non-self-improvement role by default.** Only
`self_improve_agent` / `self_research_agent` hold `collections_self_modify`
(capability_lattice.py:98-107); `coder`/`operator` do not. The `operator` grant table
contains the `collection` dispatch label, but effective authorization is conjunctive:
`role_may_dispatch("operator", "collection")` is false until the independent
`collections_self_modify` grant is present. A collections write is checked again at
`check_self_modification`, so neither dispatch nor mutation can be reached by label
alone. Escalating to a `collections_self_modify` role is itself approval-gated.

### 3.1.1 Practitioner evidence for conjunctive grants

A long-running Kubernetes RBAC support discussion documented operators trying to
permit `pods/portforward` without also allowing manual pod creation; the working
resolution was a narrowly scoped subresource grant instead of broad `pods:create`
([kubernetes/kubernetes#110999](https://github.com/kubernetes/kubernetes/issues/110999)).
That practitioner report supports keeping Gludd's collection routing label separate
from the mutation capability and requiring both at the security boundary.

### 3.2 Audit trail

Every `SelfUpdatePlan` lifecycle event writes an `AuditEventModel`
(models.py:236-256) via `AuditEventRepository.record_typed(...)`
(repository.py:474-496). New `AuditEventType` members extend the existing enum:
`SELF_UPDATE_REQUESTED`, `SELF_UPDATE_CLASSIFIED`, `SELF_UPDATE_REFUSED`,
`SELF_UPDATE_APPROVAL_REQUIRED`, `SELF_UPDATE_APPLIED`,
`SELF_UPDATE_ROLLED_BACK`. Each row carries:

- `actor` — the acting role (defaults `"agent"`; the user for user-filed requests);
- `entity_type = "self_update"`, `entity_id = request_id`;
- `correlation_id` — the `source_todo_id`, linking the audit chain to the backlog;
- `details` (JSON) — the full `SelfUpdatePlan`, the protected/collections flags, the
  prior config/template value (for rollback), and for code changes the
  `ReloadResult.details` (`rolled_back`, `rollback_verified`, hot_reloader.py:318-322).

A refusal is **always** audited (`SELF_UPDATE_REFUSED` with `refusal_reason`), so an
attempt to self-modify a guard leaves an immutable record even though no file
changed. Audit rows are queryable by entity or project
(`AuditEventRepository.list_by_entity` / `list_by_project`, repository.py:498-516).

---

## 4. Prioritization — entering the backlog

### 4.1 Request → Todo

The request becomes a `TodoModel` via `TodoRepository.create(todo_data)`
(repository.py:77-81), with fields drawn from the `SelfUpdatePlan`:

| Todo field | Value |
|---|---|
| `title` | the NL request (`"update gludd: ..."`) |
| `description` | `plan.rationale` + resolved target files |
| `work_type` | `plan.work_type` (e.g. `INFRA` for config, `CODE` for source) (todo.py:28-42) |
| `risk_level` | `plan.risk_level` (todo.py:45-49) |
| `queue` | the subsystem's queue (config changes → a low-risk queue; code → `core`) |
| `priority` | computed (§4.2) — non-negative (validated, todo.py:151-156) |
| `status` | `BACKLOG`, then `QUEUED` if auto-appliable, else `APPROVAL_REQUIRED` |
| `approval_policy` | `"none"` when auto; otherwise the gating policy (todo.py:129) |
| `dependencies` | empty for config; gate/scaffold deps for code |
| `acceptance_criteria` / `test_commands` | the validation gate for rung (b)/(c) |
| `plan_artifact` | serialized `SelfUpdatePlan` |
| `tags` | `["self_update", subsystem.value]` |

Status moves use `Todo.transition_to` / `TodoRepository.transition` against the real
state machine (`VALID_TRANSITIONS`, todo.py:60-91): `BACKLOG→QUEUED`, or
`BACKLOG→...→APPROVAL_REQUIRED` for gated work, with `APPROVAL_REQUIRED→QUEUED` only
after a human approves.

### 4.2 Computed priority

`priority: int` exists on both `Todo` (todo.py:104) and `TodoModel`, but is
**currently not consumed by the scheduler** — `Scheduler.plan` orders purely by
dependencies + exclusive resources (scheduler.py:89-181). So priority is used at the
**claim/queue** layer, not inside `plan`. The router computes:

```
priority = base(change_kind) + risk_bonus(risk_level) - approval_penalty
```

- `base`: config edits > template edits > scaffold > source-code (lower-risk, faster
  wins promoted), reflecting the YAML-first stance.
- `risk_bonus`: security/correctness-relevant requests (e.g. a budget cap fix) get a
  bump.
- `approval_penalty`: anything needing approval cannot jump ahead of ready work.

The queue's `priority_weight` (`QueueModel.priority_weight`, models.py:223) scales
the effective ordering at claim time, and `hard_cap`/`soft_cap`/`pid_group`
(models.py:225-227) bound how many self-update items run concurrently.

### 4.3 Interaction with scheduler/dispatcher

- **Claim:** `TodoRepository.claim_runnable(limit, project_id)` (optimistic
  `QUEUED→ACTIVE` claim) pulls runnable self-update Todos; `priority` orders this set.
- **Schedule:** each claimed Todo becomes a `WorkItem` (scheduler.py:31-49). To make
  self-update changes mutually exclusive on the file they touch, the router sets
  `WorkItem.resources` to include a token per target path
  (`f"path:{target}"`) and `f"subsystem:{subsystem}"`. Two self-update items editing
  the same YAML then **serialize into different batches** (`can_run_concurrently`
  returns False on shared resources, scheduler.py:52-65), preventing concurrent edits
  to the same config file. Independent config edits remain concurrent.
- **Code-change ordering:** a `SOURCE_CODE` item depends on its validation-gate items
  via `WorkItem.depends_on`, so `Scheduler.plan` (Kahn topo sort, scheduler.py:115-145)
  emits the gate before the swap; the `reload_code_module` step runs only after the
  gate batch completes.
- **Dispatch:** the role-bound `DynamicDispatcher` (dynamic_dispatcher.py:152-199) is
  the choke point — a self-update tool-call of `kind="collection"` from a role lacking
  `collections_self_modify` is denied fail-closed *before* its handler runs, mirroring
  the §3 guard at the dispatch layer.

---

## 5. Wiring + test strategy *(described, not implemented)*

### 5.1 Wiring

- **New package** `src/general_ludd/self_update/`:
  - `models.py` — `SelfUpdateRequest`, `SelfUpdatePlan`, `ChangeKind`,
    `ApplyChannel`, `SubsystemId`, `Subsystem` descriptor table.
  - `classifier.py` — `classify(SelfUpdateRequest) -> SelfUpdatePlan` (Tier 0/1/2;
    LLM tier via the model gateway, output constrained to the `SubsystemId` enum).
  - `router.py` — `SelfUpdateRouter` orchestrating: classify → guard-gate
    (`check_self_modification` / `is_protected_path`) → choose rung → compute
    priority → `TodoRepository.create` → audit. Holds refs to `HotReloader`,
    `TodoRepository`, `AuditEventRepository`, and the acting `role`.
  - `apply.py` — the rung ladder: rung (a)/(b) → `HotReloader.reload(scope)`;
    rung (c) → `HotReloader.reload_code_module(...)` with a mandatory `health_check`.
- **Ingress:** a self-update Todo is recognized by a title prefix
  (`"update gludd:"`) or `tags=["self_update"]`. The event-loop tick (or the todos
  router) detects such a Todo and hands its text to `SelfUpdateRouter`. The router is
  **always constructed with a non-self-improvement role by default**; collections
  writes require explicit role escalation, which is approval-gated.
- **No new bypass paths:** the router calls only `HotReloader`, `TodoRepository`,
  `AuditEventRepository`, and the capability-lattice functions. It never writes a
  `.py` file directly and never touches `.claude`/`.opencode`/settings.

### 5.2 Test strategy (describe only — a gate is running; do not run pytest here)

- **Classifier (unit):** golden NL requests → expected `(SubsystemId, ChangeKind,
  ApplyChannel)`. Includes adversarial inputs that *mention* guardrails/security/
  permissions/policy → must classify `GUARDRAILS_SECURITY` and set `refused=True`.
- **Guard refusal (unit, security-critical):** for each `PROTECTED_FILE_STEMS` stem
  and each `PROTECTED_PATH_SUBSTRINGS` substring, assert the router refuses
  (`apply_channel == NONE`, `refused`, audited `SELF_UPDATE_REFUSED`) and that no
  file write is attempted. Independently assert `check_self_modification` raises
  `ProtectedPathError` for those paths (defense-in-depth verification).
- **Role gating (unit):** a router under `coder`/`operator` is denied any
  `collections/` write; only `self_improve_agent`/`self_research_agent` may, and only
  after approval. Assert via `capabilities_for` / `role_may_dispatch`.
- **Apply ladder (unit, mocked `HotReloader`):** rung (a) calls `reload(scope)` with
  the right `ReloadScope`; a non-success `ReloadResult` blocks Todo completion. Rung
  (c) is *never* called with `health_check=None`, and a failing health check leaves
  `rolled_back=True` and the Todo not complete.
- **Prioritization (unit):** priority ordering (config > template > scaffold > code),
  `approval_penalty` keeps approval-gated items behind ready work, and same-file
  self-update items get a shared `WorkItem.resources` token so `Scheduler.plan`
  serializes them.
- **Audit (unit):** every lifecycle transition writes one `AuditEventModel` with
  correct `entity_type`/`entity_id`/`correlation_id`; refusals are always audited.
- **Integration (gated, run via `make test`/`make qa`):** end-to-end on a real
  temp config dir — file a `"update gludd: bump queue X soft_cap"` Todo, assert the
  YAML changes, `HotReloader.reload(ReloadScope.CONFIG)` succeeds, the Todo reaches
  `COMPLETE`, and the audit chain is intact. A code-change request lands as a gated
  `APPROVAL_REQUIRED` Todo and is **not** auto-applied.

---

## 6. Design invariants (summary)

1. **YAML/config first.** The ladder always prefers the lowest, hot-reloadable rung;
   source-code changes are the guarded last resort.
2. **No guard is ever self-modified without explicit human approval.** Enforced twice:
   classification-time refusal and `check_self_modification` at apply time. Settings
   (`/.claude/`, `/.opencode/`) are protected substrings and out of scope entirely.
3. **No unverified code lands.** `reload_code_module` requires a passing health gate
   and rolls back fail-closed; the router always supplies one and gates the candidate
   through `make lint`/`make typecheck`/`make test` first.
4. **Default-DENY roles.** The router runs under a least-privilege role; collections
   writes need an approval-gated escalation.
5. **Everything is audited**, including (especially) refusals.
