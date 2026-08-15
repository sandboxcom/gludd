# Role/Collection Agent-Orchestration Design (2026-06-25)

**Goal.** Give the Ansible role/collection layer a way to *drive agent interactions*:
read the dynamic environment facts (`ansible_facts.gludd_environment` + an advice
block), then **branch** to the right action module — a LangGraph workflow, a
single-shot routed model call, a db read/write, a `bert` (embedding/RAG) call, or
a `vcc` action — instead of hard-coding a single path per role.

This doc (a) inventories the **real** wiring targets that exist today (file:line),
(b) resolves the ambiguous terms `bert` and `vcc`, (c) specifies a new
`agent_orchestrate` role, (d) lists existing-vs-net-new action modules, and (e)
gives an ordered, independently-testable build plan that mirrors the existing
role/module/molecule pattern.

> **State note (verified 2026-06-25).** The dynamic-variable provider is only
> *partially* shipped. `GET /api/environment` exists
> (`src/general_ludd/routers/environment.py:324-399`) and emits an inline
> `optimization` block (NOT an `advice` block). The
> `gludd_environment` **ansible module does NOT exist yet** — the only repo hit
> for that name is a self-reference in the tools catalog
> (`environment.py:62`). There is **no** `GET /api/environment/advise` endpoint
> (zero hits). So the role layer this doc designs depends on two net-new pieces
> (the `advise` endpoint + the `gludd_environment` module) that must be built
> first; their contracts are specified below.

---

## 1. Scope — the real wiring targets

### 1.1 LangGraph workflow surface (SHIPPED)
- **Endpoint:** `POST /admin/models/workflow` —
  `src/general_ludd/routers/models.py:537-660`.
  Body: `{messages:[{role,content}], profile_id, work_type, max_retries=2,
  quality_threshold=0.6, enable_graph=true}`. Runs `LangGraphGateway.call(...)`
  (classify → select → generate → review → quality-gated retry).
  Returns `{content, model, prompt, quality_score, retries, warnings}`.
  Budget-gated (fail-closed) identically to `/admin/models/call`.
- **Module (SHIPPED):** `gludd_langgraph_workflow` —
  `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_langgraph_workflow.py`.
  Stdlib-only; POSTs to `/admin/models/workflow`. argspec:
  `prompt(req), system, work_type=code, model_profile=default, max_retries=2,
  quality_threshold=0.6, enable_graph=true, daemon_url, psk, timeout=300`.
  Returns `content, model, prompt_profile, quality_score, retries, warnings`.
- **Reference role (the pattern to mirror):** `langgraph_decision` —
  `.../agent/roles/langgraph_decision/{tasks,defaults,meta}/main.yml`.
  Block/rescue/always, `default()`-guarded extra-vars, surfaces result via
  `gludd_db: todo_update_status`, writes an artifact JSON. Molecule scenario
  `molecule/playbooks/role_langgraph_decision`.

### 1.2 Router (single-shot routed call) surface
- **Endpoint:** `POST /admin/models/call` —
  `src/general_ludd/routers/models.py:372-535`. Body `{prompt(req), system,
  model_profile | route_task_type, max_tokens, response_format/response_schema}`.
  Direct profile **or** adaptive routing by `route_task_type`. Returns
  `{text, model_profile_id, usage}`. Budget-gated.
- **Routing recommendation (read-only):** `POST /admin/code/suggest-model` —
  `models.py:331-370`. Scores a file's complexity → `task_type` →
  `AdaptiveRouter().route(task_type)` → `model_recommendation`
  (`selected_model_profile_id, composite_score, estimated_cost_usd, fallback,
  reason`).
- **Router class:** `AdaptiveRouter.route(task_type)` —
  `src/general_ludd/scoring/router.py:21` (`route` ~line 167; Tier-2 embedding
  similarity path at `_get_best_with_embeddings`, `router.py:232`).
- **Module (SHIPPED):** `gludd_model_call` —
  `.../plugins/modules/gludd_model_call.py`. POSTs `/admin/models/call`.
  argspec: `prompt(req), model_profile | route_task_type (mutually exclusive),
  max_tokens=2048, daemon_url, psk, timeout=120`. Returns `text,
  model_profile_id, usage`. **This is the single-shot router module a playbook
  uses today.**

### 1.3 db (read/write todos, returns, benchmarks) surface
- **Module (SHIPPED):** `gludd_db` — `.../plugins/modules/gludd_db.py`.
  Ops: `todo_get` (GET `/api/todos/{id}`), `todo_create` (POST `/api/todos`),
  `todo_update_status` (PATCH `/api/todos/{id}`), `resource_preference`
  (GET `/api/resource-preferences`). **Fail-closed capability gate**: every op
  is checked against the per-`role` default-DENY `capability_policy.py`
  (`gludd_db.py:175-184`). Never opens SQLite directly (single-writer rule).
- **Read aggregations (SHIPPED, read-only facts):**
  - `gludd_facts` → `GET /api/facts` → `ansible_facts.gludd`
    (`work/todos/models/history/messages`) — `gludd_facts.py`.
  - `gludd_metrics` → `GET /api/metrics` → `ansible_facts.gludd_metrics`
    (agents/usage/cost/**benchmarks**) — `routers/facts.py:352`.
  - `gludd_traces` → `ansible_facts.gludd_traces`.
- **Gap:** there is **no** write path for benchmark rows or task-returns via an
  ansible module today (only todos write). If a role must *record* a benchmark /
  return, that is net-new (see §4, optional `gludd_db` op extension).

### 1.4 `bert` — RESOLVED: the embedding / RAG-routing layer (NOT a literal BERT model)
**Evidence.** No `bert` / `sentence-transformers` model in `src/` (the only
`bert` hits are CLI **model-search test fixtures**, `tests/e2e/test_cli_e2e.py:368`).
The embedding substrate that actually exists:
- `general_ludd.skills.embeddings` — `HashEmbedder` (default, no external deps)
  and `OpenAIEmbedder` (`text-embedding-3-small`, used when an OpenAI key is
  present) — `src/general_ludd/skills/embeddings.py:107-129`.
- `general_ludd.scoring.task_embeddings.TaskEmbeddingStore` — canonical
  per-task-type vectors for **Tier-2 RAG routing**
  (`src/general_ludd/scoring/task_embeddings.py:1`, model rows in
  `db/models.py:586` `task_embeddings`). `AdaptiveRouter` consumes it via
  `embedding_store` (`scoring/router.py:30,167-177,232`).
- `SkillRegistry.match_trigger` embedding fallback (`skills/registry.py:58`).

**Conclusion.** "bert" = **the embedding/similarity layer that feeds the
AdaptiveRouter's RAG routing and skill matching.** It is reached *indirectly*
today (the router/skill-registry call it internally). **There is NO HTTP or
ansible surface to query embedding similarity directly.** To let a role "use
bert," we must add a thin read endpoint (see §4 `gludd_embed` / the
`/api/embeddings/similar` endpoint). Confirm with the user before building.

### 1.5 `vcc` — UNDEFINED in-repo (do NOT bind without confirmation)
**Evidence.** `make grep Q='vcc'` → **zero matches** anywhere in the repo.
`"version control"` → zero. So `vcc` is not an existing acronym in this codebase.
Plausible candidate meanings, ranked by repo evidence:
1. **Version-Control Connector** — i.e. git actions. The shipped `gludd_git`
   module (`plugins/modules/gludd_git.py`, ops `commit`/`branch`, wraps
   `general_ludd.git_automation.repo.GitAutomation`) is the only "VC" surface
   that exists. *Most likely meaning if the user means "let the role commit /
   branch as part of orchestration."*
2. **Virtual Compute Connector** — the infra/compute provisioning layer
   (`src/general_ludd/infra/compute.py:21` `ComputeProvider`
   {aws,azure,gcp,runpod,vast_ai,lambda_labs,modal,**coreweave**,...},
   `GPUType`, `ComputeConfig`; terraform emit in `infra/terraform.py`). The
   environment brief already exposes a `compute` facet
   (`routers/environment.py:190-209`). *Likely if "vcc" means spinning
   up/selecting GPU compute for a job.* (`coreweave` is the closest literal
   token but is not "vcc".)
3. **Virtual Compute Cluster** — a synonym for (2).

**Decision:** UNDEFINED → no binding is asserted in this design. The role spec
below leaves a clearly-marked `vcc` task slot wired to **whichever** module the
user confirms (`gludd_git` for meaning 1, or a net-new `gludd_compute` for
meaning 2). See Open Questions §6.

---

## 2. Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │  role: agent_orchestrate (tasks/main.yml)     │
                         └──────────────────────────────────────────────┘
                                            │
              (1) gather dynamic vars       ▼
        general_ludd.agent.gludd_environment  work_type=<...>  advise=true
                                            │
                  sets ansible_facts.gludd_environment  (+ .gludd_environment.advice)
                                            │
        ┌───────────────────────────────────┴───────────────────────────────────┐
        │ READS:  .models[] .routing{} .budget{run_remaining_usd,...} .compute{}  │
        │         .tools[] .skills[] .queues[] .system{}                          │
        │ ADVICE: .advice.recommendation.{model_profile,use_workflow}            │
        │         .advice.est_cost_usd  .advice.resource_hints{defer,...}        │
        └───────────────────────────────────┬───────────────────────────────────┘
                                            │ BRANCH on advice + budget
        ┌──────────────┬───────────────┬────┴─────────┬───────────────┬──────────────┐
        ▼              ▼               ▼              ▼               ▼              ▼
  use_workflow    NOT use_wf      db read/write   bert (RAG)     vcc (confirm)   defer
        │              │               │              │               │              │
 gludd_langgraph  gludd_model_call  gludd_db    gludd_embed*   gludd_git OR    (no-op +
   _workflow      (route_task_type) (todo_*,    /api/embed-     gludd_compute*  artifact:
  /admin/models/  /admin/models/    bench)      ings/similar*  (git_automation  budget_block)
   workflow        call             /api/todos   *NET-NEW        | infra/compute)
   (SHIPPED)       (SHIPPED)        (SHIPPED)                     *NET-NEW if (2)
                                            │
                                            ▼
                       surface result -> gludd_db: todo_update_status
                                            │
                                            ▼
                          write artifact JSON (success | failure | deferred)
```

`*` = NET-NEW (does not exist yet). Everything else is shipped.

---

## 3. Role spec — `agent_orchestrate`

Path: `collections/ansible_collections/general_ludd/agent/roles/agent_orchestrate/`
Structure mirrors `langgraph_decision`: `defaults/main.yml`, `meta/main.yml`,
`tasks/main.yml`, plus molecule scenario `molecule/playbooks/role_agent_orchestrate`.

### 3.1 `defaults/main.yml`
```yaml
---
# agent_orchestrate role defaults — env-fact-driven action dispatch.
# Daemon connectivity
daemon_url: "http://localhost:8000"
psk: ""                       # no_log in tasks; prefer GLUDD_AUTH_PSK env

# Work shape (drives advice + routing)
work_type: "code"             # e.g. code | docs | review | mechanical | security
prompt_text: ""               # primary generation/decision input
skill_body: ""                # optional system instruction
todo_id: ""                   # optional todo to surface status back to

# Capability identity for the default-DENY db policy (capability_policy.py).
# agent_task grants {todo_get, todo_update_status}; widen only if you write more.
capability_role: "agent_task"

# Budget gate: below this remaining run budget, defer instead of acting.
min_remaining_usd: 0.02

# Quality knobs (passed through to the workflow path)
quality_threshold: 0.6
max_retries: 2

# Optional sub-action toggles (off by default; opt-in per playbook)
enable_db_read: true          # fetch the todo before acting
enable_vcc: false             # commit/provision step — see Open Questions
vcc_repo_path: ""             # used only when enable_vcc and vcc==git
enable_bert: false            # RAG/embedding similarity hint — NET-NEW endpoint

# Artifact output
artifact_dir: "/tmp/harness-agent-orchestrate"
```

### 3.2 `tasks/main.yml` (the load-bearing branching logic)
Real Jinja against the **actual** fact keys (`gludd_environment.budget.run_remaining_usd`,
`gludd_environment.advice.recommendation.{model_profile,use_workflow}`,
`gludd_environment.advice.resource_hints.defer`).

```yaml
---
# agent_orchestrate — read env facts, branch to the right action module.

- name: Create artifact directory
  ansible.builtin.file:
    path: "{{ artifact_dir }}"
    state: directory
    mode: "0755"

# (1) Gather the dynamic environment brief WITH advice for this work_type.
- name: Gather gludd environment + advice
  general_ludd.agent.gludd_environment:
    work_type: "{{ work_type }}"
    advise: true
    daemon_url: "{{ daemon_url }}"
    psk: "{{ psk }}"
  no_log: "{{ psk | length > 0 }}"
  # -> sets ansible_facts.gludd_environment (incl. .advice)

- name: Resolve advice + budget into decision vars
  ansible.builtin.set_fact:
    _adv: "{{ gludd_environment.advice | default({}, true) }}"
    _remaining: "{{ gludd_environment.budget.run_remaining_usd | default(none, true) }}"
    _rec_profile: >-
      {{ gludd_environment.advice.recommendation.model_profile
         | default('default', true) }}
    _use_wf: "{{ gludd_environment.advice.recommendation.use_workflow | default(false, true) | bool }}"
    _hint_defer: "{{ gludd_environment.advice.resource_hints.defer | default(false, true) | bool }}"

# (c) Budget / resource gate: defer when out of budget or advisor says defer.
- name: Defer when budget exhausted or advisor recommends deferral
  block:
    - name: Write deferred artifact
      ansible.builtin.copy:
        content: >-
          {{ {'todo_id': todo_id, 'status': 'deferred', 'work_type': work_type,
              'reason': ('budget_block' if _budget_blocked else 'advisor_defer'),
              'remaining_usd': _remaining, 'est_cost_usd': (_adv.est_cost_usd | default(none))}
             | to_nice_json }}
        dest: "{{ artifact_dir }}/agent_orchestrate_result.json"
        mode: "0644"
    - name: End play (deferred)
      ansible.builtin.meta: end_play
  vars:
    _budget_blocked: >-
      {{ (_remaining is not none)
         and (_remaining | float < min_remaining_usd | float) }}
  when: >-
    _hint_defer
    or ((_remaining is not none) and (_remaining | float < min_remaining_usd | float))

# (db) Optional read: fetch the todo for context (capability-gated).
- name: Read todo context
  general_ludd.agent.gludd_db:
    op: todo_get
    todo_id: "{{ todo_id }}"
    daemon_url: "{{ daemon_url }}"
    psk: "{{ psk }}"
    role: "{{ capability_role }}"
  register: _todo
  failed_when: false
  no_log: "{{ psk | length > 0 }}"
  when: enable_db_read | bool and (todo_id | length > 0)

# ---- main action block ----
- name: Orchestrated action
  block:

    # (b) BRANCH 1 — advisor says use the full LangGraph workflow.
    - name: Run LangGraph workflow (advice.use_workflow)
      general_ludd.agent.gludd_langgraph_workflow:
        prompt: "{{ prompt_text }}"
        system: "{{ skill_body }}"
        work_type: "{{ work_type }}"
        model_profile: "{{ _rec_profile }}"
        max_retries: "{{ max_retries }}"
        quality_threshold: "{{ quality_threshold }}"
        daemon_url: "{{ daemon_url }}"
        psk: "{{ psk }}"
      register: _wf
      no_log: "{{ psk | length > 0 }}"
      when: _use_wf

    # (b) BRANCH 2 — single-shot routed call (cheap/mechanical work).
    - name: Single-shot routed model call (no workflow)
      general_ludd.agent.gludd_model_call:
        prompt: "{{ prompt_text }}"
        # advice gave us a concrete profile; fall back to adaptive routing by
        # work_type only when the advisor returned no profile.
        model_profile: "{{ _rec_profile if (_rec_profile != 'default') else omit }}"
        route_task_type: "{{ work_type if (_rec_profile == 'default') else omit }}"
        daemon_url: "{{ daemon_url }}"
        psk: "{{ psk }}"
      register: _single
      no_log: "{{ psk | length > 0 }}"
      when: not _use_wf

    - name: Select effective output
      ansible.builtin.set_fact:
        _content: "{{ (_wf.content if _use_wf else _single.text) | default('') }}"
        _used_profile: "{{ (_wf.model if _use_wf else _single.model_profile_id) | default(_rec_profile) }}"

    # (bert) OPTIONAL — RAG/embedding similarity hint (NET-NEW module).
    - name: Fetch RAG/embedding similarity hint (bert)
      general_ludd.agent.gludd_embed:      # NET-NEW — see §4
        op: similar_task_types
        work_type: "{{ work_type }}"
        daemon_url: "{{ daemon_url }}"
        psk: "{{ psk }}"
      register: _bert
      failed_when: false
      no_log: "{{ psk | length > 0 }}"
      when: enable_bert | bool

    # (vcc) OPTIONAL — version-control/compute action (binding UNCONFIRMED).
    # Shown as the git binding (meaning 1). Swap to gludd_compute for meaning 2.
    - name: VCC action (git commit of produced output)
      general_ludd.agent.gludd_git:        # OR gludd_compute (NET-NEW) — confirm
        path: "{{ vcc_repo_path }}"
        op: commit
        message: "agent_orchestrate: {{ work_type }} for {{ todo_id | default('adhoc') }}"
      register: _vcc
      failed_when: false
      no_log: "{{ psk | length > 0 }}"
      when: enable_vcc | bool and (vcc_repo_path | length > 0)

    - name: Assemble result
      ansible.builtin.set_fact:
        agent_orchestrate_result:
          todo_id: "{{ todo_id }}"
          work_type: "{{ work_type }}"
          path: "{{ 'workflow' if _use_wf else 'single_shot' }}"
          model_profile: "{{ _used_profile }}"
          quality_score: "{{ _wf.quality_score | default(none) }}"
          content_excerpt: "{{ _content[:500] }}"
          bert_hint: "{{ _bert.result | default(none) }}"
          vcc_sha: "{{ _vcc.sha | default(none) }}"

    # surface back to gludd (capability-gated)
    - name: Surface status back to gludd
      general_ludd.agent.gludd_db:
        op: todo_update_status
        todo_id: "{{ todo_id }}"
        status: "{{ 'done' if (_content | length > 0) else 'blocked' }}"
        daemon_url: "{{ daemon_url }}"
        psk: "{{ psk }}"
        role: "{{ capability_role }}"
      failed_when: false
      no_log: "{{ psk | length > 0 }}"
      when: todo_id | length > 0

    - name: Write success artifact
      ansible.builtin.copy:
        content: "{{ agent_orchestrate_result | combine({'status': 'success'}) | to_nice_json }}"
        dest: "{{ artifact_dir }}/agent_orchestrate_result.json"
        mode: "0644"

  rescue:
    - name: Write failure artifact
      ansible.builtin.copy:
        content: >-
          {{ {'todo_id': todo_id, 'status': 'failed', 'work_type': work_type,
              'error': (ansible_failed_result.msg | default('agent_orchestrate failed'))}
             | to_nice_json }}
        dest: "{{ artifact_dir }}/agent_orchestrate_result.json"
        mode: "0644"
      failed_when: false
    - name: Fail the play after rescue
      ansible.builtin.fail:
        msg: "agent_orchestrate failed for {{ todo_id | default('(adhoc)') }} — see {{ artifact_dir }}"
```

### 3.3 Where each downstream slots in
- **langgraph workflow** → `gludd_langgraph_workflow` (BRANCH 1, `when: _use_wf`).
- **router single-shot** → `gludd_model_call` with `route_task_type`/`model_profile`
  (BRANCH 2, `when: not _use_wf`).
- **db** → `gludd_db` (`todo_get` read at top, `todo_update_status` surface at end).
- **bert** → `gludd_embed` (NET-NEW), `when: enable_bert`.
- **vcc** → `gludd_git` (meaning 1) or `gludd_compute` (meaning 2, NET-NEW),
  `when: enable_vcc`.

---

## 4. Action-module inventory: existing vs NET-NEW

### Already exist (reuse as-is)
| Module | Endpoint wrapped | Returned facts |
|---|---|---|
| `gludd_langgraph_workflow` | `POST /admin/models/workflow` | content, model, quality_score, retries, warnings |
| `gludd_model_call` | `POST /admin/models/call` | text, model_profile_id, usage |
| `gludd_db` | `/api/todos`, `/api/resource-preferences` | todo, created, updated, preference |
| `gludd_facts` / `gludd_metrics` / `gludd_traces` | `/api/facts`, `/api/metrics` | `ansible_facts.gludd*` (incl. benchmarks via metrics) |
| `gludd_git` | wraps `git_automation.repo.GitAutomation` | sha, branch |

### NET-NEW (must be built)

**4a. `gludd_environment` module** (REQUIRED — the dynamic-var provider the whole
role depends on; endpoint half-exists, module does not).
- Wraps `GET /api/environment` (and the new `?advise=...&work_type=...`, see 4b).
- `argument_spec`: `work_type=dict(type='str', default='code')`,
  `advise=dict(type='bool', default=True)`,
  `daemon_url=dict(type='str', default='http://localhost:8000')`,
  `psk=dict(type='str', no_log=True, default='')`,
  `timeout=dict(type='int', default=30)`. `supports_check_mode=True`, read-only.
- Returns `{"ansible_facts": {"gludd_environment": <EnvironmentBrief + advice>}}`
  (mirror `gludd_facts.py:110-111`'s `ansible_facts.gludd` shape, namespace
  `gludd_environment`).

**4b. `GET /api/environment/advise` endpoint + `advice` block** (REQUIRED).
The current `EnvironmentBrief.optimization` (`environment.py:96-97`,
`controllers/environment_advisor.build_optimization_hints`) returns
`{hints[], recommended_profile_for{}}` — it does NOT match the `advice` shape the
role branches on. Add a work-type-aware advisor:
- New pure helper `build_advice(*, work_type, models, routing, budget, system)` in
  `controllers/environment_advisor.py` returning:
  ```
  advice = {
    "recommendation": {"model_profile": <str>, "use_workflow": <bool>},
    "est_cost_usd": <float|None>,
    "resource_hints": {"defer": <bool>, "reason": <str|None>},
  }
  ```
  Derivation (reuse existing signals): `model_profile` ← `recommended_profile_for`
  for `work_type` (falls back to `routing.default_profile`); `use_workflow` ←
  `work_type in _QUALITY_WORK_TYPES` (review/design/security/etc. → workflow;
  mechanical/cheap → single-shot); `defer` ← budget fraction ≥ critical OR
  `system.load_avg`/`mem_available_mb` pressure; `est_cost_usd` ← profile
  `cost_per_*` × a nominal token estimate.
- `GET /api/environment/advise?work_type=...` returns just the `advice` block (so
  `gludd_environment` can fetch advice without re-pulling the full brief), and the
  full `GET /api/environment` embeds `advice` alongside `optimization` for
  backward compat.

**4c. `gludd_embed` module + `/api/embeddings/similar` endpoint** ("bert" — only
if the user confirms a direct RAG surface is wanted; see Open Questions).
- Endpoint: `POST /api/embeddings/similar` → calls
  `TaskEmbeddingStore.similarity_to(task_type)` (`task_embeddings.py:183-219`) /
  `SkillEmbedder` similarity. Read-only.
- Module `argument_spec`: `op=dict(choices=['similar_task_types',
  'similar_skills'], required=True)`, `work_type`/`query`, `daemon_url`, `psk`,
  `timeout`. Returns `{result: [{name, similarity}, ...]}`.

**4d. `gludd_compute` module** (ONLY if `vcc` == meaning 2; see Open Questions).
- Wraps a compute select/provision endpoint over `infra/compute.ComputeConfig`
  (does not exist as an endpoint today — would also be net-new). DEFER until the
  `vcc` binding is confirmed; do not build speculatively.

---

## 5. Build plan (ordered, each phase independently testable)

Mirrors the established pattern: each module ships with a `test_gludd_<x>`
molecule scenario + a `tests/integration/test_playbook_registry.py` assertion;
each role ships with a `role_<name>` molecule scenario.

- **Phase 1 — `advice` foundation (no ansible yet).**
  Add `build_advice(...)` to `controllers/environment_advisor.py` + the
  `GET /api/environment/advise` route + embed `advice` in `EnvironmentBrief`.
  Test: unit tests for `build_advice` (pure, like the existing advisor tests) +
  a router test asserting the `advice` shape and fail-soft defaults. *Ships value
  immediately — the model can already read advice over HTTP.*

- **Phase 2 — `gludd_environment` module.**
  New `plugins/modules/gludd_environment.py` wrapping the endpoint, returning
  `ansible_facts.gludd_environment`. Test: `test_gludd_environment` molecule
  scenario (mock daemon) + `test_playbook_registry` assertion that the module
  injects `ansible_facts.gludd_environment` (mirror the gludd_metrics asserts at
  `test_playbook_registry.py:354`). Add to `_ANSIBLE_TOOL_MODULES` (already
  self-listed at `environment.py:62`).

- **Phase 3 — `agent_orchestrate` role (workflow + router + db branches only).**
  `roles/agent_orchestrate/{defaults,meta,tasks}/main.yml` using ONLY shipped
  modules (`gludd_environment`, `gludd_langgraph_workflow`, `gludd_model_call`,
  `gludd_db`) — `enable_bert`/`enable_vcc` default false so the role is fully
  testable without 4c/4d. Register a playbook (`playbooks/agent_orchestrate.yml`)
  + molecule scenario `role_agent_orchestrate`. Test: molecule run that exercises
  both branches (use_workflow true/false) against a mock daemon, asserting the
  artifact JSON. This is the **minimum shippable orchestration layer.**

- **Phase 4 — `bert` (`gludd_embed` + `/api/embeddings/similar`).**
  Only after user confirms a direct RAG surface is desired. Endpoint → module →
  `test_gludd_embed` scenario → flip `enable_bert` path in the role's molecule
  test.

- **Phase 5 — `vcc` binding.**
  After the user confirms meaning. If meaning 1 (git): the role already wires
  `gludd_git` behind `enable_vcc` — just add a molecule case. If meaning 2
  (compute): build `gludd_compute` + its endpoint (its own multi-step phase).

Each phase is independently mergeable; Phase 3 is the keystone and depends only
on Phases 1-2.

---

## 6. Open questions for the user

1. **`vcc` binding (blocking for Phase 5).** Which do you mean?
   (a) **Version-Control Connector** = git commit/branch via the shipped
   `gludd_git` (already wired behind `enable_vcc`); or
   (b) **Virtual Compute Connector** = select/provision GPU compute via
   `infra/compute.py` (needs a net-new endpoint + `gludd_compute` module); or
   (c) something else entirely. `vcc` has **zero** occurrences in the repo today,
   so no binding is assumed.

2. **`bert` surface (blocking for Phase 4).** "bert" maps to the existing
   embedding/RAG layer (`skills.embeddings` + `scoring.task_embeddings`,
   `HashEmbedder` default / `text-embedding-3-small` with a key — **not** a
   literal BERT). It has **no** HTTP/ansible surface. Do you want a new
   read-only `/api/embeddings/similar` endpoint + `gludd_embed` module so a role
   can query similarity directly, or is the *implicit* use (router/skill-registry
   already call it internally) sufficient?

3. **`advice` block shape.** The endpoint today emits `optimization`
   (`{hints, recommended_profile_for}`), not the `advice`
   (`recommendation.model_profile / use_workflow / est_cost_usd /
   resource_hints`) shape the role branches on. Confirm the `advice` field names
   in §4b — the role's `when:` expressions are pinned to them.

4. **Benchmark/return WRITES.** `gludd_db` writes only todos. If a role must
   *record* benchmark rows or task-returns (not just read them via
   `gludd_metrics`), that needs a new `gludd_db` op + endpoint — out of scope
   unless requested.
```
