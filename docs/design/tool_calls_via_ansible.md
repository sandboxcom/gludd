# Tool Calls via Ansible Collections & Roles — Audit + Migration Plan

Status: design / uncommitted draft
Date: 2026-06-16
Scope: read-only audit of how agent actions execute today vs the stated principle, and an executable migration plan.

## The principle (binding)

> In gludd, an agent's **tool calls / actions** must execute through **Ansible
> collections & roles** (the product's native execution model) — **not** bespoke
> internal Python. Python is acceptable **only** for the daemon's own internal
> orchestration with specific needs (tick-bounded, in-process, lock-held).

"Action" here means anything an agent/role *does to the world*: git ops, filesystem
writes, shell/command, HTTP/fetch, DB CRUD, secret resolution, model/gateway calls,
worktree management, observability queries. "Daemon-internal orchestration" means the
event loop's own plumbing: leasing todos, the tick, committing agent output, reclaiming
worktrees, the single-writer DB boundary, the per-repo git lock.

---

## Executive summary

gludd **already has** the principle-aligned execution model and it is **the live
production path**, not an aspiration:

- The event loop's primary execute path runs a **playbook**
  (`event_loop/loop.py:933` → `AnsibleRunnerAdapter.run_playbook`), and the worker's
  `/jobs/execute` does the same (`worker/app.py:227`).
- Playbooks invoke **roles** (~60 under
  `collections/ansible_collections/general_ludd/agent/roles/`), and roles invoke
  **`general_ludd.agent.gludd_*` modules** + `ansible.builtin.*` for every action
  (verified: `agent_task`, `implement_change`, `refactor_code`, etc. all use
  `gludd_db` / `gludd_worktree` / `gludd_git` / `gludd_skill` / `gludd_agent_run`).
- Modules are **thin shims** over the existing Python via `module_utils` — e.g.
  `gludd_git.py:109` imports `general_ludd.git_automation.repo.GitAutomation`; logic is
  not duplicated. A unified **capability policy** already gates every module dimension
  (`module_utils/capability_policy.py`, default-DENY per role).

So the principle is **mostly satisfied** for the core coding loop. The violations are
specific and enumerable:

1. **`ExecutionEngine` (`execution/engine.py`)** — a complete *parallel* in-process
   Python implementation of the action set (git via `subprocess`, `patch`, raw file
   writes) that bypasses the modules entirely. It is **defined but never instantiated**
   anywhere in `src/` or `collections/` (grep confirms only a comment + its own
   `tool_loop` docstring reference it). Dead/legacy bypass — highest-priority removal.
2. **`DynamicDispatcher` `collection` kind is permanently unwired** —
   `daemon_wiring.py:184` hard-codes `collection_handler=None`. The one routing surface
   whose whole purpose is "roles call roles/collections/MCP/skills" (issue #26) cannot
   reach a collection/module at all; it fails-closed for `kind=collection`.
3. **New subsystems have no Ansible module surface** — `connectors/` (40+ sources),
   `observe/`, `receiver/`, `issue_sources/`, `self_update/`, `skills/fetcher` (HTTP),
   and secrets are reached **only** via Python daemon routers. The 7 `observe_*` roles
   already call a `gludd_observe` module that **does not exist** (explicit
   "DEFERRED WIRING — #73 next wave" comments in every one of them).
4. **`gludd_mcp_tool` is an honest placeholder** (`short_description: ... not yet
   wired`) and the in-module agent loop's tool calls (`gludd_agent_run` →
   `ToolCallLoop`) go to MCP servers, never back into Ansible modules.

The migration is therefore mostly **completion + consolidation**, not greenfield: kill
the parallel Python engine, wire the `collection` kind, and add the missing thin module
shims for the new packages — each following the established `gludd_git`/`gludd_db`
patterns.

---

## (a) Current tool-execution map

### Two execution stacks exist

**Stack A — Ansible (principle-aligned, LIVE).**

```text
event_loop/loop.py::_dispatch_execute_job   (loop.py:826)
  └─ AnsibleRunnerAdapter.run_playbook        (loop.py:933 → ansible/runner.py:112)
       └─ CoreAnsibleRunner.run_playbook      (ansible/core_runner.py:218)
            └─ ansible-core PlaybookExecutor   (core_runner.py:512; native library, not subprocess)
                 └─ ROLE tasks                 (collections/.../roles/<role>/tasks/main.yml)
                      ├─ general_ludd.agent.gludd_* MODULE
                      │     └─ module_utils shim → existing general_ludd.* Python
                      └─ ansible.builtin.{file,copy,command,set_fact,assert,fail}
```

Worker path is identical from `run_playbook` down (`worker/app.py:227`), reached over
HTTP for distributed execution.

**Stack B — in-process Python (VIOLATING / legacy).**

```text
ExecutionEngine.execute                       (execution/engine.py:196)  ← never instantiated
  ├─ subprocess.run(["git","checkout","-b",...])   (engine.py:120)
  ├─ subprocess.run(["git","commit",...])          (engine.py:131)
  ├─ subprocess.run(["patch","-p1",...])           (engine.py:436)
  ├─ open(...,"w") raw file writes                 (engine.py:354)
  └─ subprocess.run(["make","test"])               (engine.py:83)
```

### The model-tool-call routing surface

`DynamicDispatcher.dispatch` (`dispatch/dynamic_dispatcher.py:176`) routes a model
tool-call by `kind ∈ {role, collection, mcp, skill}` to an **injected handler**, gated
by the per-role capability lattice (`security/capability_lattice.py`,
`dynamic_dispatcher.py:183`). Real handlers are built in `daemon_wiring.py`:

| `kind` | Handler (`daemon_wiring.py`) | What it actually does | Ansible? |
|---|---|---|---|
| `role` | `make_role_handler` → `AgentDispatcher.dispatch_one` | Python agent runner | **No** |
| `mcp` | `make_mcp_handler` → `MCPClient.call_tool` | MCP transport | n/a (MCP) |
| `skill` | `make_skill_handler` → `SkillRegistry.get(name).body` | returns skill text | n/a |
| `collection` | **always `None`** (`daemon_wiring.py:184`) | nothing — fail-closed | **Missing** |

The dispatcher carries a standing `# TODO(integration)` (`dynamic_dispatcher.py:8`):
it is *not* called from the event-loop turn handler. The HTTP surface
`POST /api/dispatch` (`routers/dispatch.py`) exposes it, but the daemon's own loop does
not route model tool-calls through it.

### Module shim pattern (the target, already in use)

Two module families, both thin:

- **Local-execution shim** (`gludd_git`, `gludd_worktree`): module imports the existing
  Python class and calls it. Ex: `gludd_git.py:109` → `GitAutomation`; that class holds
  the per-repo git lock (#63, `git_automation/locking.py`, `repo.py:175`).
- **Daemon-HTTP shim** (`gludd_db`, `gludd_facts`, `gludd_model_call`, `gludd_dispatch`,
  `gludd_spend`, `gludd_features`, `gludd_accounting`, `gludd_message`): module is a
  thin HTTP client to a daemon REST endpoint via `GluddClient`
  (`module_utils/gludd.py`). `gludd_db` explicitly **"never raw SQLite"** because the
  daemon owns the single writer (`gludd_db.py:8,13`).

Shared `module_utils`: `gludd.py` (client + `ok_result`/`error_result`),
`fs_write_policy.py` (path allowlist), `capability_policy.py` (per-role default-DENY
across fs/collections-self-modify/facts/network/secrets/db), `fs_write_audit.py`.

### Inventory of existing modules

`gludd_git, gludd_worktree, gludd_db, gludd_facts, gludd_introspect, gludd_metrics,
gludd_traces, gludd_message, gludd_model_call, gludd_agent_run, gludd_skill,
gludd_dispatch, gludd_reload, gludd_schedule, gludd_spend, gludd_abtest, gludd_features,
gludd_accounting, gludd_ping, gludd_mcp_tool (placeholder), gludd_introspect`.

---

## (b) Gap list — Python-direct actions that should be Ansible modules/roles

Ordered by priority.

### G1 — `ExecutionEngine` parallel Python action stack (HIGH; remove)
`execution/engine.py` re-implements git-branch, git-commit, file-write, unified-diff
apply, and test-run in raw `subprocess`/`open`, bypassing every module and the
capability policy. **Never instantiated** in `src/`/`collections/` (dead path).
Grounding: `engine.py:120,131,354,436,83`; grep for `ExecutionEngine(` in `src` and
`collections` returns no call sites.

### G2 — `collection` dispatch kind permanently unwired (HIGH)
The one routing kind that means "a role/agent calls a collection module" returns
fail-closed because `collection_handler` is hard-`None`. Grounding:
`daemon_wiring.py:13,182-184`; `dynamic_dispatcher.py:158-169`.

### G3 — `DynamicDispatcher` not wired into the event loop (HIGH)
Model tool-calls in the daemon's own turn handler never flow through the dispatcher;
the standing `# TODO(integration)` is unresolved. So even the `role`/`mcp` handlers are
only reachable over the HTTP shim, not from the loop. Grounding:
`dynamic_dispatcher.py:8`; absence of any `DynamicDispatcher(` call in `event_loop/`
(grep: only `routers/dispatch.py` + `daemon_wiring.py` construct it).

### G4 — `gludd_observe` module gap closed (beta.3)

The six `observe_*` roles now resolve
`general_ludd.agent.gludd_observe`. The read-only module discovers only
daemon-registered connector names, adapts them to `GluddObserve`, and exposes
`query_sources`, `timeline`, `topology`, and `correlate_incident`. A
default-deny capability check restricts every built-in observe role to the
local daemon before any HTTP call.

User-forum evidence reinforced treating the resolver message as a real missing
runtime surface rather than a lint exception: an Ansible collection user
reported the same `resolved_fqcn=None` warning together with a runtime
`Cannot resolve ... to an action or module` failure. Maintainers advised making
the collection/module discoverable rather than hiding the warning. See
[Role in collection cannot find module in the same collection](https://forum.ansible.com/t/role-in-collection-cannot-find-module-in-the-same-collection/45676).
Accordingly, beta.3 adds the real module and no `mock_modules`, skip, or warning
suppression.

### G5 — HTTP/fetch has no module (`skills/fetcher`) (MEDIUM)
`skills/fetcher` (the agent's web/HTTP fetch action) is invoked only via
`routers/skills.py:10`. There is no `gludd_fetch` module, so a role cannot perform an
outbound fetch as a gated action. Grounding: `routers/skills.py:10`.

### G6 — `receiver/`, `issue_sources/`, `self_update/` have no module surface (MEDIUM)
These newer packages (webhook receive, external issue ingestion, agent self-update) are
Python-only behind routers (`receiver/router.py`, `self_update/`). No agent-facing
module exists, so an agent cannot drive them as gated tool-calls. Grounding: untracked
`src/general_ludd/{receiver,issue_sources,self_update}/`; `receiver/router.py`.

### G7 — `gludd_mcp_tool` is a placeholder; in-loop tool calls bypass modules (MEDIUM)
`gludd_mcp_tool` is an honest stub (`short_description: ... not yet wired`), and
`gludd_agent_run`'s `ToolCallLoop` routes tool calls to MCP servers
(`execution/tool_loop.py:117`), never to gludd modules. The inner agent loop's actions
are thus outside the Ansible surface. Grounding: `gludd_mcp_tool.py:8`;
`gludd_agent_run.py:27-33` ("actual MCP tools require W3.9 option-a wiring");
`tool_loop.py:117`.

### G8 — secrets resolution has no dedicated module (LOW)
`capability_policy` already models `secret_prefixes`, but there is no `gludd_secret`
module; secrets are resolved inside other modules/Python (`module_utils/gludd.py:169`
gateway path, `secrets/env.py`). A first-class gated module would make secret access an
auditable tool-call. Grounding: `capability_policy.py:228` (`check_secret_access`
exists but no module calls it).

---

## (c) Migration plan

General rule for every item: **wrap, don't rewrite.** Create a `gludd_<x>` module that
imports the existing `general_ludd.*` logic via a `module_utils` shim (local-execution
pattern) or calls a daemon REST endpoint (daemon-HTTP pattern), mirroring `gludd_git` /
`gludd_db`. Each new module gets a `role` field and calls
`capability_policy.for_role(role).check_*` before acting (default-DENY). Then route the
dispatcher's `collection` kind to an ansible-runner invocation of that module.

### M1 — Retire the `ExecutionEngine` Python stack (addresses G1)
- Confirm no remaining callers (done: none in `src`/`collections`).
- Delete `execution/engine.py` action methods, or reduce `ExecutionEngine` to a thin
  client that **dispatches a playbook** via `AnsibleRunnerAdapter` for the
  code-generation work types (the worker already does exactly this at
  `worker/app.py:204-231`). Keep `_resolve_in_workspace` jail logic only if reused.
- Move its tests to assert the playbook path is taken.
- Net effect: there is **one** action stack (Ansible), not two.

### M2 — Wire the `collection` dispatch kind (addresses G2, G3)
- Add `make_collection_handler(ansible_runner)` to `daemon_wiring.py` returning an
  async handler `(name, args) -> result` that runs a one-task playbook invoking
  `general_ludd.agent.<name>` with `args` as module params, via
  `AnsibleRunnerAdapter.run_playbook` (a generated ephemeral playbook, or a generic
  `dispatch_module.yml` parameterized by `module_name`/`module_args` extravars).
- Replace `collection_handler=None` (`daemon_wiring.py:184`) with this handler.
- Resolve the `# TODO(integration)` (`dynamic_dispatcher.py:8`): call the dispatcher
  from the event-loop turn handler when a model returns `tool_calls`, then re-render the
  next prompt from the `VariableStore` (`dispatch/variable_store.py::apply_results`).
- Capability gating already exists at two layers (`role_may_dispatch` in the dispatcher,
  `capability_policy` in the module) — keep both (defense in depth).

### M3 — Create `gludd_observe` module (addresses G4) — completed in beta.3

- `plugins/modules/gludd_observe.py` uses the daemon-HTTP source-discovery/query
  boundary and the existing `GluddObserve` facade; connector and correlation
  logic is not duplicated.
- It returns
  `ansible_facts.gludd_observe.{records,groups,topology,errors}` in the exact
  shape consumed by the six roles.
- Per-role local-daemon grants, fail-closed discovery validation, source-error
  isolation, and JSON-safe topology output are covered by focused tests.

### M4 — Create `gludd_fetch` module (addresses G5)
- New module wrapping `skills/fetcher`, gated by
  `capability_policy.check_network_host` (the policy dimension already exists,
  `capability_policy.py:206`). Daemon-HTTP or local-execution shim per how `fetcher`
  is structured.

### M5 — Modules for `receiver` / `issue_sources` / `self_update` (addresses G6)
- `gludd_receiver` (ingest a buffered webhook batch), `gludd_issue_source` (pull/normalize
  external issues), `gludd_self_update` (apply a validated self-update). Each a thin shim
  over the existing package, daemon-HTTP where a single-writer/router boundary applies
  (self_update especially — mirror `gludd_db`'s "never bypass the daemon" rule).
- `self_update` grants must require `collections_self_modify`
  (`capability_policy.py:169`) — only self-improvement roles.

### M6 — Promote `gludd_mcp_tool` from placeholder; decide inner-loop policy (addresses G7)
- Implement `gludd_mcp_tool` against `MCPClient.call_tool` (the `mcp` handler already
  proves the path, `daemon_wiring.py:58-65`).
- Decision needed (record in this doc once made): are `ToolCallLoop`'s inner tool calls
  (`tool_loop.py:117`) **in scope** for "must be Ansible"? Recommendation: **no** — MCP
  is itself a legitimate tool transport and the loop is tick-bounded model
  orchestration; treat it as an internal exception (see (e) E5). But the *outer* action
  the loop performs (writing files, committing) must remain in roles, which it already
  is (`agent_task` commits via `gludd_git`).

### M7 — `gludd_secret` module (addresses G8) — LOW
- Thin module over the secrets manager, gated by `check_secret_access`
  (`capability_policy.py:228`). Makes secret resolution an auditable, capability-gated
  tool-call instead of an implicit side effect inside other modules.

---

## (d) Capability-policy implications

Every new module must be a first-class citizen of the default-DENY policy in
`module_utils/capability_policy.py`. Concretely:

- **New grant dimensions / ops.** `gludd_observe` reads from sources → extend
  `facts_prefixes` (or add an `observe_sources` dimension) for the `observe_*` roles.
  `gludd_fetch` → `network_hosts` (dimension exists). `gludd_secret` → `secret_prefixes`
  (exists). `gludd_self_update` → `collections_self_modify` (exists; restrict to
  self-improve roles). `gludd_receiver`/`gludd_issue_source` → new `db_ops` or a new
  ingest dimension.
- **Each new role needs a `CapabilityPolicy` entry** in `_builtin_table()`
  (`capability_policy.py:303`) granting *only* the ops it invokes — the wiring unit test
  already enforces "adding a db op to a role REQUIRES extending its grant"
  (`capability_policy.py:374-380`). Extend that test to cover the new dimensions so a
  module added without a grant fails closed and the test catches it.
- **Two-layer gate stays.** Dispatcher-level `role_may_dispatch(role, kind)`
  (`dynamic_dispatcher.py:183`) gates the *kind*; module-level
  `capability_policy.for_role(role).check_*` gates the *specific action*. The new
  `collection` handler (M2) must pass the acting `role` through to the module params so
  the module-level check runs (every role task already does
  `role: "{{ capability_role }}"`, e.g. `agent_task/tasks/main.yml:44`).
- **fs writes** keep flowing through `fs_write_policy` + `check_fs_write`, with the
  `collections/` self-modify guard (`capability_policy.py:132-180`) — so a migrated
  file-write action (replacing `engine.py:354`) is *more* constrained than today's raw
  `open()`, not less.

---

## (e) Justified Python-only exceptions (daemon-internal orchestration)

These are legitimately Python and should **not** be migrated to modules; each is
tick-bounded, in-process, or a single-writer/lock boundary the daemon must own.

- **E1 — The event-loop tick itself** (`event_loop/loop.py` phases, leasing in
  `event_loop/lease.py`). This is the orchestrator that *invokes* playbooks; it cannot
  itself be a playbook without infinite regress. In-process, tick-bounded.
- **E2 — Worktree reclaim / lifecycle owned by the loop.** Creating/removing worktrees
  *as an agent action* is already a module (`gludd_worktree`, used in `agent_task`
  begin/always blocks). The daemon's own *reclaim* sweep of leaked worktrees is internal
  housekeeping and stays Python (disk-discipline housekeeping, not an agent action).
- **E3 — The per-repo git lock (#63)** (`git_automation/locking.py`, held at
  `repo.py:175`). This is in-process serialization that lives *inside* the Python that
  `gludd_git` shims — correct: the module calls `GitAutomation`, which takes the lock.
  The lock is an implementation detail of the action, not a separate action.
- **E4 — The single-writer DB boundary.** The daemon owns the only SQLite writer;
  `gludd_db` deliberately goes over HTTP and **never** opens SQLite
  (`gludd_db.py:8,13`). The daemon-side repository code (`db/repository.py`) is internal
  and stays Python. Migrating it to a module would violate single-writer.
- **E5 — `ToolCallLoop` MCP tool calls** (`execution/tool_loop.py`). Model
  orchestration: prompt/tool/response iteration bounded by `MAX_TOOL_ITERATIONS` and a
  per-tool timeout. MCP is itself a sanctioned tool transport. The *outer* effects
  (commit, file write) are still done by roles/modules; only the model conversation loop
  is Python. (Pending the M6 decision being recorded.)
- **E6 — `ara` / `core_runner` / `runner` themselves.** The ansible-core invocation
  machinery (`ansible/core_runner.py`, `ansible/runner.py`) is the substrate that runs
  the modules; it is necessarily Python.

Boundary test for "is this a justified exception?": *Does an agent/role choose to invoke
this as a discrete action?* If yes → it must be a module (Stack A). If it is plumbing the
daemon runs to make agents possible (tick, lock, single-writer, the runner itself) → it
stays Python.

---

## Priority-ordered backlog

| # | Item | Gap | Priority | Effort |
|---|---|---|---|---|
| M1 | Retire/collapse `ExecutionEngine` to the playbook path | G1 | HIGH | M |
| M2 | Wire `collection` dispatch kind + loop integration | G2,G3 | HIGH | M |
| M3 | Create `gludd_observe` (unblocks 7 roles) | G4 | HIGH | M |
| M4 | Create `gludd_fetch` (HTTP as gated action) | G5 | MED | S |
| M5 | Modules for receiver / issue_sources / self_update | G6 | MED | L |
| M6 | Promote `gludd_mcp_tool`; record inner-loop policy | G7 | MED | S |
| M7 | Create `gludd_secret` | G8 | LOW | S |

Every item reuses an established pattern (`gludd_git` local-shim or `gludd_db`
daemon-HTTP) and the existing `capability_policy` — so the migration is consolidation,
not redesign.

---

## File index (claims grounded here)

- `src/general_ludd/execution/engine.py` — Stack B Python action impl (`:120,131,354,436,83`); never instantiated.
- `src/general_ludd/execution/tool_loop.py` — `ToolCallLoop`, MCP per-tool timeout (`:117`).
- `src/general_ludd/dispatch/dynamic_dispatcher.py` — kind routing, capability gate (`:176,183`); `# TODO(integration)` (`:8`).
- `src/general_ludd/dispatch/variable_store.py` — inter-turn re-render (`apply_results`).
- `src/general_ludd/daemon_wiring.py` — real handlers; `collection_handler=None` (`:184`).
- `src/general_ludd/routers/dispatch.py` — `POST /api/dispatch` shim.
- `src/general_ludd/event_loop/loop.py` — live execute path → `run_playbook` (`:933`); return_review (`:482`).
- `src/general_ludd/worker/app.py` — `/jobs/execute` → playbook (`:227`); model-then-playbook (`:204`).
- `src/general_ludd/ansible/runner.py`, `ansible/core_runner.py` — native ansible-core runner.
- `src/general_ludd/routers/observe.py`, `connectors/registry.py`, `observe/facade.py` — canonical observability path.
- `src/general_ludd/git_automation/{repo.py,locking.py}` — git + per-repo lock (#63).
- `src/general_ludd/db/repository.py` — single-writer DB (internal).
- `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_*.py` — module inventory.
- `collections/.../plugins/modules/gludd_git.py` (`:109`), `gludd_db.py` (`:8,13`), `gludd_agent_run.py`, `gludd_mcp_tool.py` (`:8`).
- `collections/.../plugins/module_utils/{gludd.py,capability_policy.py,fs_write_policy.py,fs_write_audit.py}`.
- `collections/.../roles/agent_task/tasks/main.yml` — canonical principle-aligned agent loop.
- `collections/.../plugins/modules/gludd_observe.py` — capability-gated daemon adapter over `GluddObserve`.
- `collections/.../roles/observe_*/tasks/main.yml` — consume the module's facts.
