# Unified SDLC Data-Source Ingestion + Relevance-Threshold Pipeline

Status: design (uncommitted). Implementation-ready.
Date: 2026-06-16.

## 0. Premise and what is already real

The user requirement: **Ansible task-output parsing must feed the SAME pipeline
as log/event/metric/trace parsing.** Collecting application + run data is part
of the SDLC, so all useful data flows through one ingestion process. Each source
is **rated** for how useful its data is likely to be *for a given task*. The user
sets a **threshold**; sources at/above it auto-run. The agent may *additionally*
opt in to sources *below* the threshold when it judges them useful for the
specific task.

This design grounds every claim in code that exists today. Two grounding notes
correct the prompt's assumptions, verified by reading the tree:

1. **`connectors/observe/facade.py` does not exist; `observe/facade.py` does.**
   The cross-source facade is `general_ludd.observe.facade.GluddObserve`
   (`query_sources` / `correlate_incident` / `timeline` / `topology`). It takes
   a *provider* (dict / callable / registry-like) and never imports the registry
   (`observe/facade.py:100-115`). This is the unified query layer all sources
   already flow through.

2. **`ansible/result_parser.py` and `ansible/runstate.py` do not exist yet.**
   The real Ansible run-result shape is
   `general_ludd.ansible.core_runner.AnsibleResult`
   (`status / rc / stats / events / host_results / error`,
   `core_runner.py:190-204`), populated by `_EventCollectorCallback`
   (`core_runner.py:92-153`) which captures per-task `runner_on_ok` /
   `runner_on_failed` / `runner_on_skipped` / `runner_on_unreachable` /
   `playbook_on_stats` events. Persisted run rows live in
   `db.models.RoleRunModel` (`models.py:471-488`) and timeline/artifact history
   in `observability.run_history.RunHistoryRecorder`
   (`run_history.py:17-66`). **The adapter in §1 normalizes `AnsibleResult`**;
   if `result_parser.py`/`runstate.py` are added later they feed the same
   adapter unchanged (they are upstream of the record shape, not a replacement
   for it).

Everything below is built on these real spines:

- **Source contract** — `connectors/base.py:113-129`: a `Source` has `name`,
  `KIND`, `health() -> dict` (must not raise), `query(spec) -> list[dict]`.
  Records are `NormalizedRecord` TypedDicts
  (`ts/source/kind/level_or_status/message/value/labels/raw`,
  `base.py:43-78`) built via `normalized_record(...)` (`base.py:81-106`).
  KIND is an **open string set** — concrete connectors already use KINDs beyond
  the four canonical ones (`argo_workflows.py:91` uses `"pipeline"`; the facade
  treats KIND as opaque, `observe/facade.py:38-41`).
- **Cross-source join** — `connectors/normalize.py`: `normalize_join_keys`
  (`normalize.py:270-301`) folds heterogeneous `labels` into a canonical `join`
  sub-dict; `correlate` groups by a join key (`normalize.py:304-335`).
- **Registry** — `connectors/registry.py:80-237` `ConnectorRegistry`:
  `from_config` builds live sources; `get` / `by_kind` / `query(name, spec)` /
  `health_all` / `list_sources`. URL-free `query(name, spec)` is the SSRF
  firewall (`registry.py:210-237`).
- **Relevance precedent** — `scoring/router.py:19-220` `AdaptiveRouter`:
  composite cost/quality scoring over `BenchmarkRepository.get_aggregate_scores`
  (`db/repository.py:627-672`), `min_samples` gating, fail-closed cost cap.
- **Config** — `config/user_config.py:65-126` `UserConfig` (pydantic-settings,
  `GLUDD_*` env override of YAML, nested via `__`). Sub-blocks are `BaseModel`s
  like `PipelineConfigBlock` (`user_config.py:18-34`).
- **Dispatch** — `dispatch/dynamic_dispatcher.py:142-237` `DynamicDispatcher`:
  routes `ToolCall(kind, name, args)` to handlers; `kind` ∈
  `{role, collection, mcp, skill}`; every dispatch is capability-gated by
  `role_may_dispatch` (`capability_lattice.py:157-168`), default-DENY.

---

## 1. Ansible output as a first-class Source

### 1.1 Goal

`AnsibleResult` (and any future `ParsedRun`/`RunSummary`) must become a stream
of `NormalizedRecord`s registered as a `Source` so it fans out through
`GluddObserve.query_sources` exactly like Loki logs or Prometheus metrics.

### 1.2 The KIND

Use **`KIND = "run"`** for per-task Ansible run records (one record per
`runner_on_*` event) and reuse the existing **`"pipeline"`** KIND only when the
record represents a whole job/play outcome. `"run"` is a new open-set KIND — no
change to `base.VALID_KINDS` is needed because the facade never validates KIND
against it (`observe/facade.py:38-41`). `correlate_incident`'s default kinds
(`observe/facade.py:211`) should be extended by callers to include `"run"` so
Ansible task failures correlate with the logs/metrics around them.

### 1.3 The adapter: `AnsibleResult` -> `list[NormalizedRecord]`

New module `general_ludd/ansible/run_source.py`. Two pieces:

**(a) A pure normalizer** (`normalize_ansible_result`) that maps one
`AnsibleResult` to records using the existing builder. Field mapping, grounded
in `core_runner.py:92-153` event shapes and `base.py:81-106`:

| record field      | source in `AnsibleResult` / event                                   |
|-------------------|----------------------------------------------------------------------|
| `kind`            | `"run"`                                                               |
| `source`          | the registered source name (the play/job/role identifier)            |
| `ts`              | event time if captured (add to callback), else play-end `time.time()`|
| `level_or_status` | `runner_on_ok`→`"success"`, `runner_on_failed`→`"failed"`, `runner_on_unreachable`→`"unreachable"`, `runner_on_skipped`→`"skipped"`; folds to canonical severity via `normalize.py:56-108` (`failed`→`error`, `success`→`info`) |
| `message`         | `f"{event['task']} on {event['host']}"`                              |
| `value`           | `rc` for the play-level record (numeric), else `None`                |
| `labels`          | `{"host": event["host"], "task": event["task"], "service": <role/play>, "rc": rc, "ignore_errors": ...}` — host/service are the canonical join aliases (`normalize.py:113-114`) so run records correlate with logs/traces on the same host/service |
| `raw`             | the untouched `event["result"]` payload, for drill-down              |

The play-level summary record additionally carries
`labels["stats"] = AnsibleResult.stats` and (when the failure classifier lands)
`labels["failure_class"] = classify_failure(...)`. Because `host` and `service`
land in `labels` under canonical aliases, `normalize_join_keys`
(`normalize.py:270-301`) and `GluddObserve.topology` (`observe/facade.py:261-291`)
pick run records up with **zero** extra code.

**(b) A `Source` wrapper** (`AnsibleRunSource`) satisfying the duck-typed
contract (`base.py:113-129`) — no base-class inheritance required:

```text
class AnsibleRunSource:
    KIND = "run"
    def __init__(self, name, provider): self.name=name; self._provider=provider
    def health(self) -> dict:        # MUST NOT raise (base.py:124)
        return {"ok": True, "source": self.name, "kind": self.KIND}
    def query(self, spec) -> list[dict]:
        results = self._provider(spec)            # AnsibleResult(s) for spec
        out = []
        for r in results:
            out.extend(normalize_ansible_result(r, source_name=self.name))
        return out                                 # normalized-record dicts
```

`query(spec)` honors the same `spec` shape every connector sees, including the
`start`/`end` epoch bounds `GluddObserve` injects (`observe/facade.py:182-188`).
The `provider` is injected so the source can read live runs (a
`CoreAnsibleRunner` execution), replayed runs (`RunHistoryRecorder.get_timeline`,
`run_history.py:44-48`), or persisted rows (`RoleRunRepository`,
`repository.py:1178`). This injection mirrors `GluddObserve`'s own
provider-injection design (`observe/facade.py:100-115`) and keeps the source
testable without a live Ansible.

### 1.4 Registration

`AnsibleRunSource` is registered like any connector. Two paths, both real:

- **Programmatic** — `SourceRegistry.register(AnsibleRunSource(...))`
  (`base.py:165-167`) or directly into the dict/callable provider `GluddObserve`
  accepts.
- **Config-driven** — a `ConnectorRegistry.from_config` entry selecting it via
  the `class` selector (`registry.py:152-154`):
  `{"name": "deploy-run", "kind": "run", "class": "general_ludd.ansible.run_source:AnsibleRunSource"}`.
  The registry sets `source.name` from config (`registry.py:132-133`), groups it
  under KIND `"run"` in `by_kind()` (`registry.py:180-185`), and exposes it via
  `list_sources()` — so it is reachable from `/api/observe/sources`
  (`routers/observe.py:87-127`) and from `wire_observability`
  (`routers/observe.py:130-173`) with no router change.

Once registered, **a run record is indistinguishable from a log record to every
downstream consumer**: `query_sources(["run","logs","metrics"], spec)` returns a
single time-ordered merge (`observe/facade.py:163-204`), and
`correlate_incident(seed, kinds=("run","logs","traces"))` groups an Ansible
failure with the telemetry around it on `trace_id`/`host`/`service`.

---

## 2. Source relevance rating

### 2.1 `TaskContext` data model

New module `general_ludd/relevance/context.py`. A frozen pydantic `BaseModel`
(matches the project's schema style, e.g. `schemas/benchmark.py:24-42`):

```text
class TaskContext(BaseModel):
    task_type: TaskType                       # schemas/benchmark.py:11-22 (reuse)
    target_files: list[str] = []              # files the task touches
    services: list[str] = []                  # service/host hints for join match
    recent_failures: list[dict] = []          # prior failed records/run summaries
    keywords: list[str] = []                   # free-text task hints
    role: str | None = None                    # acting role (capability gate, §3)
    project_id: str | None = None              # scopes historical usefulness
```

`TaskType` is **reused verbatim** from `schemas/benchmark.py:11-22`
(`BUG_FIX`, `FEATURE`, `DEBUGGING`, `SECURITY_FIX`, ...). This is the same enum
`AdaptiveRouter` keys on (`scoring/router.py:39-46`), so relevance scoring and
model routing share one task taxonomy.

### 2.2 The `SourceDescriptor` (what gets rated)

A source — connector, the new run source, *or* a tool/collection/role (§4) — is
rated through a uniform descriptor so the scorer is source-agnostic:

```text
class SourceDescriptor(BaseModel):
    name: str
    kind: str                  # "logs"/"metrics"/"run"/"pipeline"/"collection"/"role"/...
    family: str = "unknown"    # normalize.auth_family / registry meta (registry.py:135-139)
    cost: float = 0.0          # relative est. cost to pull (latency/$/tokens), 0..1
    dispatch_kind: str | None = None   # for tool/collection/role sources (§4)
```

For connectors this is exactly `ConnectorRegistry.list_sources()` output
(`registry.py:168-170`, `{name, kind, family}`) plus a `cost`. No new
enumeration code — the registry already produces it.

### 2.3 `relevance(task, source) -> RelevanceScore`

New module `general_ludd/relevance/scorer.py`. The score is a weighted blend of
four signals, deliberately mirroring `AdaptiveRouter`'s composite-with-weights
shape (`scoring/router.py:26-33`, `cost_weight`/`quality_weight`) and
`BenchmarkScores.composite_score` (`schemas/benchmark.py:51-64`):

```text
class RelevanceScore(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    affinity: float; usefulness: float; recency: float; cost: float
    reason: str                # e.g. "kind=run affinity 0.9 for DEBUGGING"
```

The four signals:

1. **KIND↔task-type affinity** (heuristic table, the default-on path). A static
   `AFFINITY: dict[TaskType, dict[str, float]]` keyed by the reused `TaskType`.
   Grounded examples:
   - `DEBUGGING` → `{run: 0.9, logs: 0.9, traces: 0.8, metrics: 0.6, pipeline: 0.7}`
   - `SECURITY_FIX` → `{logs: 0.8, run: 0.7, events: 0.9, metrics: 0.4}`
   - `FEATURE` → `{run: 0.5, pipeline: 0.6, logs: 0.4, metrics: 0.3}`
   - `OPTIMIZATION` → `{metrics: 0.9, traces: 0.8, run: 0.4}`
   A missing cell defaults to a low floor (e.g. `0.2`), never 0 (so a source is
   always *opt-in-able*, never invisible).

2. **Historical usefulness** (learned, optional hook). Drawn from the **same**
   `BenchmarkRepository.get_aggregate_scores` path the router uses
   (`db/repository.py:627-672`, `scoring/router.py:108-145`). A new
   `get_source_usefulness(task_type, source_kind)` aggregates an outcome signal:
   for each historical task, did including this source's records improve the
   benchmark outcome (completion/quality)? Until that table is populated, this
   signal returns `None` and the blend renormalizes over the present signals —
   exactly how `AdaptiveRouter` falls back when `min_samples`/repo is missing
   (`scoring/router.py:108-112`, `85-93`, fail-soft to default). This keeps the
   scorer useful on day one and self-improving as run history accrues.

3. **Recency / topical match.** A source whose `service`/`host` join keys
   (`normalize.py:113-114`) intersect `TaskContext.services`/`target_files`, or
   whose recent records appear in `TaskContext.recent_failures`, gets a recency
   boost. Computed by a cheap `source.health()`/last-record probe; never raises
   (health contract, `base.py:124`).

4. **Cost** (penalty). `SourceDescriptor.cost`, blended with a negative weight —
   identical posture to `AdaptiveRouter`'s `cost_weight` (`router.py:32`,
   `47-74`). Non-finite cost is treated as maximally expensive (fail-closed),
   mirroring `_exceeds_cap` (`router.py:95-106`).

Blend: `score = w_a*affinity + w_u*usefulness + w_r*recency - w_c*cost`, weights
renormalized over present signals, clamped to `[0,1]` (the `RelevanceScore`
validator enforces the range, like `RoutingCandidate._score_range`,
`schemas/benchmark.py:113-118`).

### 2.4 Optional model-assisted hook

`relevance(...)` accepts an optional `model_scorer: Callable[[TaskContext,
SourceDescriptor], float] | None`. When provided and confident it overrides the
heuristic affinity (blended, not replaced), the way `AdaptiveRouter` applies a
`quantization_map` confidence penalty as a *multiplier* on the base score
(`scoring/router.py:147-156`). Default `None` ⇒ pure heuristic, fully offline.

---

## 3. Threshold gating + model opt-in

### 3.1 Config schema

New `BaseModel` sub-block on `UserConfig`, following `PipelineConfigBlock`
(`user_config.py:18-34`, `97`):

```text
class DataSourceConfigBlock(BaseModel):
    threshold: float = 0.6                       # global auto-include cutoff
    per_task_type: dict[str, float] = {}         # e.g. {"debugging": 0.4}
    max_auto_sources: int = 8                    # cap auto fan-out width
    max_optional_presented: int = 12             # cap the optional menu size
    allow_model_optin: bool = True               # master switch for §3.3
```

Wired onto `UserConfig` as `data_sources: DataSourceConfigBlock =
DataSourceConfigBlock()` (`user_config.py:88-97`). It inherits the full
precedence stack for free: YAML default, overridden by
`GLUDD_DATA_SOURCES__THRESHOLD=0.4` (nested `__` delimiter,
`user_config.py:79-83`). `per_task_type` lets a debugging task lower the bar
without touching feature work — resolved as
`per_task_type.get(task_type.value, threshold)`.

### 3.2 Gather API: `select_sources(task, threshold) -> Selection`

New module `general_ludd/relevance/selection.py`:

```text
class RatedSource(BaseModel):
    name: str; kind: str; rating: float; cost: float; reason: str

class Selection(BaseModel):
    auto: list[RatedSource]       # rating >= threshold (run without asking)
    optional: list[RatedSource]   # rating <  threshold (presented to model)

class SourceSelector:
    def __init__(self, provider, scorer, config: DataSourceConfigBlock): ...
    def select_sources(self, task: TaskContext) -> Selection:
        thr = self._config.per_task_type.get(task.task_type.value,
                                              self._config.threshold)
        rated = [self._scorer.relevance(task, d)            # §2.3
                 for d in self._descriptors(task.role)]     # §3.4 capability filter
        rated.sort(key=lambda r: r.score, reverse=True)
        auto = [r for r in rated if r.score >= thr][:max_auto_sources]
        optional = [r for r in rated if r.score < thr][:max_optional_presented]
        return Selection(auto=..., optional=...)
```

`_descriptors` enumerates from the same provider `GluddObserve` already accepts
(`observe/facade.py:122-160`): a `ConnectorRegistry` yields `list_sources()`
(`registry.py:168-170`); the tool/collection/role catalog yields the §4
descriptors. So **connectors, the run source, and tools are rated by one call**.

The **auto** set is handed straight to `GluddObserve.query_sources([r.kind for r
in auto], spec)` (`observe/facade.py:163-204`) — the existing fan-out, now
relevance-filtered instead of "all KINDs". Per-source failure isolation
(`observe/facade.py:191-201`) is unchanged, so a low-value flaky source can never
abort the gather.

### 3.3 Model opt-in

The **optional** list is rendered into the prompt as a menu — `name`, `rating`,
`cost`, one-line `reason` — so the agent sees *what it could pull and why it
wasn't auto-run*. When the model wants one, it emits a tool-call the existing
parser already understands: `parse_tool_calls` accepts
`{"kind": "...", "name": "...", "args": {...}}`
(`dynamic_dispatcher.py:69-135`). For a data source the call is
`{"kind": "collection", "name": "<source-name>", "args": {"spec": {...}}}` (or a
dedicated `kind: "source"` if added to the dispatcher's handler map,
`dynamic_dispatcher.py:161-169`).

Honoring the request (`SourceSelector.honor_optin(name, task) -> Selection`):

1. The requested `name` **must be in this task's `optional` list** — a name that
   is neither auto nor optional is rejected (the model cannot conjure a source
   that was never enumerated; this is the §1.4 "operator-registered sources
   only" SSRF posture, `registry.py:17-25`, applied at selection time).
2. Once admitted, it joins the auto set and is queried through the same
   `query(name, spec)` path (`registry.py:210-237`) — URL-free, so opt-in can
   never steer egress at a new host.

### 3.4 Capability gating of opt-in

Tool/collection/role sources (§4) are gated **before** they appear in the menu
*and* again at dispatch. `_descriptors(role)` filters out any
tool-call-kind source the acting role may not dispatch, using
`role_may_dispatch(role, kind)` (`capability_lattice.py:157-168`, default-DENY).
So a `coder` role (which lacks the `collection` kind,
`capability_lattice.py:110-114`) never even sees collection sources in its
optional menu. At dispatch time `DynamicDispatcher` enforces the same lattice a
second time (`dynamic_dispatcher.py:183-199`) — defense in depth: the menu is
advisory, the dispatcher is the enforcement boundary. Plain connector/run
sources (no `dispatch_kind`) are not capability-gated; they are read-only
telemetry pulls subject only to the registry's name-allowlist.

---

## 4. Unified pipeline wiring

### 4.1 Where it sits

```text
  ┌─────────────────────────── data-gather role / event-loop turn ───────────────────────────┐
  │  TaskContext  ──►  SourceSelector.select_sources(task)                                     │
  │                         │            │                                                     │
  │              auto[] ────┘            └──── optional[]  ──► prompt menu (name/rating/cost)   │
  │                 │                                              │ model emits tool_call      │
  │                 ▼                                              ▼ honor_optin (gated §3.3-4)  │
  │        GluddObserve.query_sources(kinds, spec)  ◄────────── admitted optional source        │
  │                 │  (observe/facade.py:163-204; per-source isolation)                        │
  │                 ▼                                                                            │
  │   normalize_join_keys / correlate / topology  (normalize.py:270-335; facade.py:206-291)     │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ providers:  ConnectorRegistry (logs/metrics/traces/pipeline)
                      AnsibleRunSource  (KIND="run", §1)
                      ToolCatalog       (collection/role/skill descriptors, §4.2)
```

The gather step is the **only** place sources are chosen; everything downstream
(`query_sources`, `correlate_incident`, `timeline`, `topology`) is the existing,
unmodified `GluddObserve` surface (`observe/facade.py`). The run source (§1) is
just one more provider entry — proving the "Ansible output through the same
pipeline" requirement structurally, not by special-casing.

### 4.2 Tools / collections / roles as rateable sources

A tool/collection/role is a `SourceDescriptor` with a `dispatch_kind`
(`"collection"`/`"role"`/`"skill"`, the `DynamicDispatcher` kinds,
`dynamic_dispatcher.py:27`). A new `ToolCatalog` enumerates available
tools/collections/roles into descriptors (mirroring
`DynamicDispatcher.list_available`, `dynamic_dispatcher.py:235-237`). They are
scored by the **same** `relevance(...)` (their "records" are the dispatch
*output* the agent would gather), gated by `role_may_dispatch` (§3.4), and — when
auto or admitted-optional — invoked through `DynamicDispatcher.dispatch`
(`dynamic_dispatcher.py:176-229`). So "pull the `observe_logs` collection to
gather more data" and "auto-include the Loki connector" are the **same decision**
made by the **same scorer** under the **same threshold**, differing only in the
execution backend (dispatcher vs. `query_sources`).

### 4.3 Event-loop integration point

The integration hook already exists as a TODO:
`dynamic_dispatcher.py:8-12` notes the event-loop turn handler should call
`DynamicDispatcher` on `tool_calls` then re-render from the `VariableStore`. The
data-gather role inserts `SourceSelector` immediately before that turn: it runs
the auto set, writes the merged records into the gather variables, presents the
optional menu, and lets the model's opt-in tool-calls flow through the existing
dispatch path. No event-loop API changes — this slots into the documented seam.

---

## 5. Implementation plan + test strategy

### 5.1 Modules to add

| module | responsibility | grounds on |
|--------|----------------|------------|
| `ansible/run_source.py` | `normalize_ansible_result()` + `AnsibleRunSource` (KIND `"run"`) | `core_runner.py:92-204`, `base.py:81-129` |
| `relevance/context.py` | `TaskContext`, `SourceDescriptor` (pydantic) | `schemas/benchmark.py:11-42` |
| `relevance/scorer.py` | `relevance(task, source) -> RelevanceScore`, affinity table, usefulness hook, optional `model_scorer` | `scoring/router.py:26-156`, `repository.py:627-672` |
| `relevance/selection.py` | `SourceSelector.select_sources` / `honor_optin`, `Selection`/`RatedSource` | `observe/facade.py:122-204`, `registry.py:168-170` |
| `relevance/catalog.py` | `ToolCatalog` → tool/collection/role descriptors | `dynamic_dispatcher.py:235-237`, `capability_lattice.py:95-130` |
| `config/user_config.py` (edit) | add `DataSourceConfigBlock` + `data_sources` field | `user_config.py:18-34, 88-97` |
| `db/repository.py` (edit) | `BenchmarkRepository.get_source_usefulness(...)` | `repository.py:627-678` |

No edits to `base.py`, `normalize.py`, `observe/facade.py`, or `registry.py`:
the run source and tool descriptors satisfy the existing contracts, which is the
whole point.

### 5.2 Build order

1. `ansible/run_source.py` + its tests (record-shape conformance). Unblocks the
   "Ansible through one pipeline" requirement standalone.
2. `relevance/context.py` + `scorer.py` (pure, offline, heuristic-only).
3. `config` block + `selection.py` (threshold gating over a fake provider).
4. `catalog.py` + capability gating + dispatcher opt-in honoring.
5. `get_source_usefulness` learned signal (last; system is useful without it).

### 5.3 Test strategy (all unit, offline — make-only per CLAUDE.md)

- **Record conformance** — feed a synthetic `AnsibleResult` (failed + ok +
  skipped + unreachable events) to `normalize_ansible_result`; assert every dict
  has all eight `NormalizedRecord` keys (`base.py:43-78`), `kind=="run"`,
  `level_or_status` folds via `normalize.py:56-108`, and `host`/`service` land in
  `labels` so `normalize_join_keys` (`normalize.py:270-301`) and
  `GluddObserve.topology` (`observe/facade.py:261-291`) pick them up.
- **Source contract** — `isinstance(AnsibleRunSource(...), Source)` via the
  `@runtime_checkable` protocol (`base.py:113`); `health()` never raises; a
  provider that throws yields an error record, not an exception
  (mirror `observe/facade.py:191-201`).
- **End-to-end fan-out** — register `AnsibleRunSource` into a dict provider with
  a Loki-like fake, call `GluddObserve.query_sources(["run","logs"], spec)`,
  assert one time-ordered merge and that `correlate_incident(..., by="host")`
  groups a run failure with a log line on the same host.
- **Scorer** — affinity table returns `DEBUGGING`→`run`/`logs` high,
  `OPTIMIZATION`→`metrics` high; usefulness `None` renormalizes (no crash, like
  `scoring/router.py:108-112`); non-finite cost ⇒ max penalty
  (mirror `_exceeds_cap`, `router.py:95-106`); score clamped `[0,1]`.
- **Selection** — with `threshold=0.6`, sources split correctly into
  `auto`/`optional`; `per_task_type` override lowers the bar for `debugging`;
  `max_auto_sources` caps width; env override
  `GLUDD_DATA_SOURCES__THRESHOLD=0.4` takes effect (`user_config.py:79-83`).
- **Opt-in honoring** — a requested optional name is admitted; a name in neither
  set is rejected (SSRF/allowlist posture); admitted source is queried via
  `query(name, spec)` (`registry.py:210-237`).
- **Capability gating** — a `coder` role's optional menu excludes `collection`
  sources (`role_may_dispatch` false, `capability_lattice.py:110-114`); a
  `self_improve_agent` sees them; dispatch of a denied kind fail-closes at
  `DynamicDispatcher` (`dynamic_dispatcher.py:183-199`) even if a menu bug let it
  through.
- **Config** — `DataSourceConfigBlock` defaults; YAML load + env override round
  trip (mirror existing `UserConfig` tests).

### 5.4 Invariants this design preserves

- **Never raises on a single source** — run source + scorer + selection all
  fail-soft; failures become error records (`base.py:217-229`,
  `observe/facade.py:191-201`).
- **Operator-registered sources only** — opt-in can pull only enumerated names;
  no URL ever enters from model output (`registry.py:17-25`, §3.3-3.4).
- **Default-DENY capability** — tool/collection/role opt-in gated twice
  (`capability_lattice.py:157-168`, `dynamic_dispatcher.py:183-199`).
- **One task taxonomy** — relevance and model routing both key on `TaskType`
  (`schemas/benchmark.py:11-22`), so the system has a single, coherent notion of
  "what kind of work is this."
