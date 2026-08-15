# Pluggable Issue-Source Layer (#75)

**Status:** design-only (uncommitted). **Date:** 2026-06-16.

Design for a pluggable ingestion layer that pulls work items from external
trackers (Jira, Redmine, ServiceNow, GitHub Issues, a Markdown todo file, an
Excel/CSV file) into gludd's internal todo list, and writes status back to the
system-of-record (SoR) as gludd claims and completes the work.

This mirrors the existing **connector** contract
(`src/general_ludd/connectors/base.py`, `prometheus.py`, `datadog.py`,
`jenkins.py`): a duck-typed `Source` Protocol with a `KIND` class attr,
`__init__(config, transport=None)`, `*_env` secrets read from `os.environ` at
call time, a literal-host SSRF guard on `base_url`, and an injectable HTTP
transport. The connector layer is read-only telemetry; issue-sources add a
**write-back** half and an **ingestion pipeline** that wires into the
`EventLoop` phase system.

---

## 0. Grounding in the actual todo model

Every schema claim below is grounded in code that was read, not invented:

- **`TodoStatus`** (`src/general_ludd/schemas/todo.py:12-25`): `backlog`,
  `queued`, `active`, `awaiting_result`, `reviewing_return`, `needs_more_work`,
  `blocked`, `manual_hold`, `approval_required`, `complete`, `budget_exceeded`,
  `failed`, `cancelled`.
- **`TodoModel`** (`src/general_ludd/db/models.py:69-125`): columns relevant to
  ingestion are `todo_id` (`TODO-XXXXXXXX`, unique), `project_id` (nullable FK
  to `projects.project_id`), `title` (≤512), `description` (Text), `status`,
  `priority` (Integer), `queue`, `tags` (JSON-in-Text list), `work_type`,
  `created_by`, `artifacts`/`evidence_refs` (JSON-in-Text lists), `version`
  (optimistic-lock Integer), `created_at`/`updated_at`/`completed_at`.
  **There is no `external_id` / `source` column today** — see §3.1 for the
  additive migration this layer requires.
- **Optimistic concurrency**: `TodoRepository.update` / `.transition`
  (`db/repository.py:90-136, 281-330`) guard every write on
  `version == expected_version` (+ optional `project_id`); a lost race raises
  `ConcurrencyError`. The repo state machine `VALID_TRANSITIONS`
  (`repository.py:34-51`) is **narrower** than the schema one
  (`schemas/todo.py:60-91`) — e.g. the repo allows `QUEUED -> ACTIVE` directly.
  The ingestion writer goes through the repo, so it is bound by the repo's
  table.
- **Project scoping**: `TodoRepository` methods all take an optional
  `project_id` filter; the create endpoint
  (`routers/todos.py:83-117`) validates a non-null `project_id` against the
  project manager's **active** projects via `_active_project_ids(app)`. Issue
  ingestion reuses the same active-project check.
- **Create path** today is single-todo `POST /api/todos` with `AddTodoRequest`
  (`routers/todos.py:20-26`); there is **no bulk-create / ingest path**. §3
  adds one (repo-level, not necessarily HTTP).

---

## 1. The `IssueSource` contract

Mirrors `connectors/base.py`'s `Source` Protocol but adds the canonical issue
shape and the write-back half. Lives in a new package
`src/general_ludd/issue_sources/`.

### 1.1 Canonical record: `IssueRecord`

A `TypedDict` (mirroring `NormalizedRecord` in `connectors/base.py:43-78`), the
normalized shape every adapter's `fetch()` returns:

```python
# src/general_ludd/issue_sources/base.py
from typing import Any, TypedDict

class IssueRecord(TypedDict):
    external_id: str          # SoR-native id, STABLE across syncs (e.g. "PROJ-42",
                              #   "1234", GitHub issue number as str). Dedup key.
    source: str               # registered source .name (e.g. "jira:acme.atlassian.net")
    source_kind: str          # the SOURCE class-attr ("jira"/"redmine"/...)
    title: str                # -> TodoModel.title (truncate to 512 on write)
    body: str                 # -> TodoModel.description (Text, no cap)
    status: str               # SoR-native status string, pre-mapping (e.g. "In Progress")
    gludd_status: str         # mapped TodoStatus.value (see §1.3 table); the
                              #   canonical "what state should the todo be in"
    priority: int             # normalized 0..3 (low..critical) -> TodoModel.priority
    assignee: str | None      # SoR assignee login/email (advisory only; not a gludd field)
    labels: list[str]         # -> TodoModel.tags (JSON-in-Text list)
    url: str                  # canonical SoR URL -> stored in evidence_refs as "url:<...>"
    updated_at: float | None  # SoR last-updated epoch seconds; drives conflict detection
    raw: Any                  # untouched SoR payload, for drill-down / re-mapping
```

Builder helper `issue_record(*, external_id, source, ...) -> IssueRecord` with
well-formed defaults, exactly as `normalized_record()` does.

`priority` normalization (`int` 0..3): adapters map their native priority scale
to `{0: low, 1: medium, 2: high, 3: critical}`, matching the `_PRIORITY_MAP`
inversion in `routers/todos.py` (low=0, medium=1, high=2, critical=3).

### 1.2 The `IssueSource` Protocol

```python
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class IssueSource(Protocol):
    """Structural contract every issue-source adapter satisfies.

    Implementations need not inherit — duck typing, like connectors/base.py.
    """
    name: str          # human-readable, derived from validated host (e.g. "jira:acme.atlassian.net")
    SOURCE: str         # class attr: kind marker — one of VALID_SOURCES (see below)

    def health(self) -> dict[str, Any]:
        """Status dict. MUST NOT raise — report failure in the dict.
        (Same contract as Source.health.)"""
        ...

    def fetch(self, spec: dict[str, Any]) -> list[IssueRecord]:
        """Return normalized IssueRecords matching ``spec`` (a query/filter dict:
        jql, project keys, label filters, ``updated_since`` epoch for incremental
        sync, paging cursor). MUST be paginated internally and bounded by spec."""
        ...

    def write_back(self, external_id: str, transition: "WriteBackTransition") -> "WriteBackResult":
        """Push a gludd-side state change to the SoR. Idempotent: writing the
        same transition twice MUST be a no-op (see §3.4). MUST NOT raise on a
        transport/HTTP error — return a WriteBackResult with ok=False so the
        poller can retry."""
        ...

VALID_SOURCES = ("jira", "redmine", "servicenow", "github", "markdown", "csv")
```

`WriteBackTransition` is a small enum of gludd-side lifecycle events the SoR
cares about (not the full 13-state machine):

```python
class WriteBackTransition(enum.StrEnum):
    CLAIMED   = "claimed"    # gludd took the work -> SoR "in progress"
    COMPLETED = "completed"  # gludd finished      -> SoR "closed/done"
    FAILED    = "failed"     # gludd gave up        -> SoR "reopened"/comment (advisory)
    BLOCKED   = "blocked"    # gludd is blocked     -> SoR comment/label (advisory)

class WriteBackResult(TypedDict):
    ok: bool
    external_id: str
    applied_status: str | None   # the SoR status now in effect (None if no-op/failed)
    detail: str                  # human-readable; error message on failure
    no_op: bool                  # True when the SoR was already in the target state
```

### 1.3 `__init__`, secrets, SSRF guard, transport (mirrors connectors)

The shared base implements the exact patterns from
`connectors/prometheus.py:88-157` and `datadog.py:91-160`:

```python
class _BaseIssueSource:
    SOURCE: str = ""  # subclass sets

    def __init__(self, config: dict[str, Any], transport: HttpRequest | None = None) -> None:
        if transport is None:
            raise ValueError("transport must be injected")   # mirrors prometheus/datadog
        self._base_url = _validate_base_url(config.get("base_url", ""))  # SSRF guard, §below
        # secrets: store the ENV-VAR NAME, never the value; read at call time
        self._token_env = config.get("token_env")
        self._user_env = config.get("user_env")
        self._transport = transport
        self._timeout = float(config.get("timeout", 30.0))
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"{self.SOURCE}:{host}"
```

**Secret resolution** is copied verbatim in spirit from
`prometheus._headers` (`prometheus.py:151-157`): the adapter stores
`self._token_env` (a string like `"JIRA_TOKEN"`) and at request time does
`os.environ.get(self._token_env)`. Secret **values never live on the object**
and never appear in `config`/yaml — only the env-var *name* does. The
`project_secrets_manager` already threaded into the `EventLoop`
(`daemon.py` `secrets_resolver`) can supply per-project env overlays.

**SSRF guard** `_validate_base_url(base_url) -> str` is lifted directly from
`prometheus._validate_base_url` (`prometheus.py:88-111`) + `_is_blocked_ip`
(`prometheus.py:69-85`): http/https only, **no DNS resolution**, reject
loopback / private / link-local / reserved / multicast / unspecified / non-global
IPs and `_BLOCKED_HOSTNAMES` (localhost, cloud metadata `169.254.169.254`,
etc.), strip trailing slash. The two file-based adapters (markdown, csv) have no
network, so they **skip** the URL guard but add a **path guard** instead (§2.5):
the file path must resolve under a configured allow-root (no `..` escape, no
symlink-out), since a file path is the SSRF-equivalent attack surface for them.

**Transport injection**: a `HttpRequest = Callable[..., tuple[int, Any]]`
(method, url, params/json/headers -> (status, json)), mirroring datadog's
`HttpRequest`. Read-only sources only need `GET`; write-back needs
`POST/PUT/PATCH`, so issue-sources standardize on the multi-method
`http_request` shape (datadog-style) rather than prometheus' `http_get`. File
adapters inject a `fs` transport (a small reader/writer protocol) instead of
`http_request`, so they stay testable without touching the real filesystem.

### 1.4 Status-mapping tables (external ↔ gludd)

Each adapter owns a **read map** (SoR status -> `TodoStatus`) and a
**write map** (`WriteBackTransition` -> SoR transition/value). gludd statuses
are from `schemas/todo.py:12-25`. The read map only ever targets *ingestible*
states — `backlog`, `queued`, `complete`, `cancelled`, `blocked` — because a
freshly-ingested external issue must not be dropped mid-pipeline into an
internal-only state like `active`/`reviewing_return` (those are owned by the
event loop).

| External (generic) | gludd `TodoStatus` (read) |
|---|---|
| open / new / to-do / backlog | `backlog` |
| ready / triaged / selected | `queued` |
| in progress / in-review | `queued` (gludd will re-claim; SoR "in progress" usually means a human already owns it — see §3.2 conflict rule) |
| done / closed / resolved / fixed | `complete` |
| won't-do / wontfix / rejected / cancelled / duplicate | `cancelled` |
| blocked / on-hold / waiting | `blocked` |

Write map (gludd -> SoR), generic; per-source specifics in §2:

| `WriteBackTransition` | SoR effect |
|---|---|
| `CLAIMED` | move issue to the SoR "in progress" status |
| `COMPLETED` | move issue to the SoR "done/closed" status |
| `FAILED` | reopen / add a "gludd-failed" comment+label (advisory) |
| `BLOCKED` | add a "gludd-blocked" comment/label (advisory) |

Each per-source table is data, declared on the adapter class so it is testable
and overridable from config (a `status_map` config key can override the read
map without code changes — config-driven, like the connectors).

---

## 2. Per-source adapters

Each adapter sets `SOURCE`, declares its read/write maps, and implements
`fetch`/`write_back` over the injected transport. All HTTP adapters resolve auth
from `*_env` at call time and pass through `_validate_base_url`.

### 2.1 Jira (`SOURCE = "jira"`)

- **Auth env**: `user_env` (account email) + `token_env` (API token). Header:
  `Authorization: Basic base64(user:token)` (mirrors `jenkins._auth_headers`,
  `jenkins.py:151-158`). `base_url` e.g. `https://acme.atlassian.net`.
- **Read**: `GET /rest/api/2/search?jql=<jql>&startAt=<n>&maxResults=<m>&fields=...`.
  `spec` carries `jql` (e.g. `project = PROJ AND updated >= "<ts>"`), paging
  via `startAt`/`total`. Incremental sync appends `updated >= <updated_since>`.
- **Read mapping**: `external_id <- issue.key` (e.g. `PROJ-42`);
  `title <- fields.summary`; `body <- fields.description`;
  `status <- fields.status.name`; `gludd_status <-` read map of
  `fields.status.statusCategory.key` (`new`->backlog, `indeterminate`->queued,
  `done`->complete); `priority <-` map `fields.priority.name`
  (Lowest/Low->0, Medium->1, High->2, Highest->3); `assignee <-`
  `fields.assignee.emailAddress`; `labels <- fields.labels`;
  `url <- f"{base_url}/browse/{key}"`; `updated_at <- parse(fields.updated)`.
- **Write-back**: `GET /rest/api/2/issue/{key}/transitions` to discover the
  transition id whose `to.statusCategory` matches the target, then
  `POST /rest/api/2/issue/{key}/transitions` with `{"transition": {"id": tid}}`.
  `CLAIMED`->transition into an "In Progress" category status;
  `COMPLETED`->"Done" category. Idempotent: if the issue is already in the
  target category, return `no_op=True` without POSTing. `FAILED`/`BLOCKED` ->
  `POST /rest/api/2/issue/{key}/comment`.

### 2.2 Redmine (`SOURCE = "redmine"`)

- **Auth env**: `token_env` -> header `X-Redmine-API-Key: <token>`.
  `base_url` e.g. `https://redmine.acme.org`.
- **Read**: `GET /issues.json?status_id=*&updated_on=>=<ts>&offset=<n>&limit=<m>`
  (`limit` capped at 100 by Redmine; page via `offset`/`total_count`).
- **Read mapping**: `external_id <- issue.id` (as str); `title <- issue.subject`;
  `body <- issue.description`; `status <- issue.status.name`; `gludd_status <-`
  read map of `issue.status.name` (New/Open->backlog, In Progress->queued,
  Closed/Resolved/Rejected->complete/cancelled); `priority <-` map
  `issue.priority.name`; `assignee <- issue.assigned_to.name`;
  `labels <-` issue tags / category (Redmine has no native labels — fall back to
  `[issue.category.name]` when present); `url <- f"{base_url}/issues/{id}"`;
  `updated_at <- parse(issue.updated_on)`.
- **Write-back**: `PUT /issues/{id}.json` with
  `{"issue": {"status_id": <id>}}`. The adapter needs a config map
  `status_ids: {in_progress: 2, done: 5, ...}` (Redmine status ids are
  per-install). `CLAIMED`->in_progress id; `COMPLETED`->done id;
  `FAILED`/`BLOCKED`->`PUT` with a `notes` field (a journal comment). Idempotent
  by reading current `status.id` first.

### 2.3 ServiceNow (`SOURCE = "servicenow"`)

- **Auth env**: `user_env` + `token_env` (Basic auth) **or** `token_env` alone
  for OAuth bearer. `base_url` e.g. `https://acme.service-now.com`.
- **Read**: `GET /api/now/table/incident?sysparm_query=<enc>&sysparm_limit=<m>&sysparm_offset=<n>`
  (the table is configurable via `config["table"]`, default `incident`).
  Incremental: `sys_updated_on>=<ts>` in `sysparm_query`. Paging via
  `sysparm_offset` + `X-Total-Count` header.
- **Read mapping**: `external_id <- record.sys_id` (or `number` like `INC0010023`
  if `config["id_field"]="number"`); `title <- record.short_description`;
  `body <- record.description`; `status <- record.state`; `gludd_status <-`
  read map of numeric `state` (1 New->backlog, 2 In Progress->queued,
  6 Resolved/7 Closed->complete, 8 Cancelled->cancelled);
  `priority <-` map `record.priority` (1 Critical->3 .. 4 Low->0, inverted);
  `assignee <- record.assigned_to.value`;
  `labels <-` `[record.category, record.subcategory]` filtered;
  `url <- f"{base_url}/nav_to.do?uri=incident.do?sys_id={sys_id}"`;
  `updated_at <- parse(record.sys_updated_on)`.
- **Write-back**: `PATCH /api/now/table/incident/{sys_id}` with
  `{"state": "<n>"}`. `CLAIMED`->state 2 (In Progress); `COMPLETED`->state 6
  (Resolved) or 7 (Closed) per `config["done_state"]`; `FAILED`/`BLOCKED` ->
  `PATCH` with `work_notes`. Idempotent by GET-then-PATCH.

### 2.4 GitHub Issues (`SOURCE = "github"`)

- **Auth env**: `token_env` -> header `Authorization: Bearer <token>` (PAT or
  fine-grained token). `base_url` default `https://api.github.com`
  (GHES: `https://ghe.acme.org/api/v3`). `config["repo"]` = `"owner/name"`.
- **Read**: `GET /repos/{owner}/{repo}/issues?state=all&since=<iso>&per_page=100&page=<n>`.
  Filter out pull requests (records with a `pull_request` key). Paging via
  `Link: rel="next"` header.
- **Read mapping**: `external_id <- issue.number` (as str);
  `title <- issue.title`; `body <- issue.body`; `status <- issue.state`
  (open/closed) + `state_reason` (completed/not_planned); `gludd_status <-`
  read map (open->backlog, closed+completed->complete, closed+not_planned->cancelled);
  `priority <-` derive from labels (`priority:high` etc.) else 1;
  `assignee <- issue.assignee.login`; `labels <- [l.name for l in issue.labels]`;
  `url <- issue.html_url`; `updated_at <- parse(issue.updated_at)`.
- **Write-back**: `CLAIMED` has no native GitHub "in progress" — apply a label
  via `POST /repos/{r}/issues/{n}/labels` `{"labels":["in-progress"]}` (label
  name from `config["in_progress_label"]`) and optionally self-assign.
  `COMPLETED` -> `PATCH /repos/{r}/issues/{n}` with
  `{"state":"closed","state_reason":"completed"}`. `FAILED`/`BLOCKED` ->
  `POST /repos/{r}/issues/{n}/comments`. Idempotent: GET the issue, skip PATCH
  if already in the target `state`/label.

### 2.5 Markdown todo file (`SOURCE = "markdown"`)

- **Auth env**: none. **Path guard** instead of URL guard (§1.3): the file path
  must resolve under `config["root"]` (realpath, no `..`/symlink escape).
  Injected `fs` transport for read/write (testable).
- **Read**: parse GitHub-flavored checkbox lines:
  `- [ ] title` (unchecked) / `- [x] title` (checked). `external_id` is derived
  **stably** so re-parsing the same line dedups: `external_id <-
  f"{relpath}#{line_no}:{slug(title)}"` — or, preferred, an inline
  `&lt;!-- gludd:id=... --&gt;` marker the adapter injects on first ingest so reorders
  don't change ids (see idempotency note). `title <-` the text after the
  checkbox; `body <-` indented continuation lines; `gludd_status <-`
  `[x]`->complete, `[ ]`->backlog; `priority <-` `(!)`/`(!!)`/`(!!!)` markers or
  1; `labels <-` `#tag` tokens; `url <- f"file://{abspath}#L{line_no}"`;
  `updated_at <-` file mtime.
- **Write-back**: rewrite the matching line in place. `COMPLETED`-> flip
  `- [ ]` to `- [x]`; `CLAIMED`-> append a `(in-progress)` tag or a
  `&lt;!-- gludd:status=in_progress --&gt;` marker (no native "in progress" in
  Markdown checkboxes). Write is **read-modify-write** under an advisory file
  lock; the adapter re-reads, locates the line by the stable `gludd:id` marker
  (not line number, which drifts), edits, and atomically replaces (write temp +
  rename). Idempotent: flipping an already-`[x]` line is a no-op.

### 2.6 Excel / CSV (`SOURCE = "csv"`)

- **Auth env**: none. Path guard as in §2.5. Injected `fs` transport.
- **Read**: CSV via `csv.DictReader`; XLSX via `openpyxl` (read-only workbook).
  `config["columns"]` maps spreadsheet headers to canonical fields, e.g.
  `{id: "Ticket", title: "Summary", status: "State", priority: "Pri",
  assignee: "Owner", labels: "Tags"}`. `external_id <-` the id column (must be
  present and unique; if absent, fall back to `f"{relpath}#row{n}"`).
  `gludd_status <-` read map of the status column value; `labels <-` split the
  labels column on `;`/`,`; `url <- f"file://{abspath}#row{n}"`;
  `updated_at <-` file mtime (or a configured `updated` column).
- **Write-back**: write the gludd state to a dedicated **status column**
  (`config["writeback_column"]`, default `gludd_status`). `CLAIMED`->write
  `"in_progress"`; `COMPLETED`->write `"done"`; `FAILED`/`BLOCKED`->write the
  value + a note column. For XLSX, open with `openpyxl` (writable), set the cell
  by (row, status-col), `save()` atomically (temp + rename). For CSV, full
  read-all / rewrite-all (CSV has no in-place cell update). Idempotent: skip the
  write if the cell already holds the target value. **Concurrency caveat**: a
  human editing the same .xlsx in Excel holds the file; write-back must catch
  the lock error and surface `ok=False` for retry rather than corrupting it.

---

## 3. The ingestion pipeline

### 3.1 Required additive schema change

The dedup key is `(source_kind, external_id)`. `TodoModel`
(`db/models.py:69-125`) has no such columns today, so this layer needs an
**additive, nullable** migration (no change to existing rows):

```python
# new columns on TodoModel
issue_source: Mapped[str | None]  = mapped_column(String(32), nullable=True, index=True)   # SOURCE kind
external_id:  Mapped[str | None]  = mapped_column(String(256), nullable=True, index=True)  # SoR id
external_url: Mapped[str | None]  = mapped_column(String(1024), nullable=True)
external_updated_at: Mapped[float | None] = mapped_column(Float, nullable=True)            # last SoR mtime ingested
__table_args__ += (UniqueConstraint("issue_source", "external_id", name="uq_todo_external"),)
```

The `UniqueConstraint` makes "one todo per external issue" a **DB invariant**,
so a duplicate ingest converges via `INSERT ... ON CONFLICT DO UPDATE`
(the same race-safe upsert pattern already used in `FeatureRepository.upsert`,
`PromptProfileRepository.upsert`, `VariableNamespaceRepository.set_var` —
`repository.py:732-756, 1008-1047, 548-621`). Existing internal todos keep
`issue_source = NULL` and are unaffected (NULLs are distinct in a SQLite unique
index, so internal todos never collide).

`url` is mirrored into `evidence_refs` as `url:<...>` so it shows up in the
existing evidence grammar (`db/models.py:373` documents that grammar) and into
the dedicated `external_url` column for cheap lookup.

### 3.2 Dedup + conflict handling

A new `IssueIngestRepository` (sibling of `TodoRepository`,
`db/repository.py`) owns the upsert:

```python
async def ingest(self, rec: IssueRecord, project_id: str | None) -> tuple[TodoModel, str]:
    # returns (todo, action) where action in {"created","updated","skipped","conflict"}
```

Algorithm per fetched `IssueRecord`:

1. **Lookup** existing todo by `(issue_source, external_id)` (+ `project_id`
   scope, validated against active projects exactly like `routers/todos.py:85`).
2. **Not found -> CREATE**: race-safe `INSERT ... ON CONFLICT(uq_todo_external)
   DO NOTHING`, then re-select (converge on the winner of a concurrent ingest).
   Map fields per §1.1; `created_by="issue_source:<SOURCE>"`;
   `status = rec.gludd_status`; `tags = rec.labels`. Record a `TodoEventModel`
   (`db/models.py:128-151`) `event_type="ingested"`,
   `actor="issue_source:<SOURCE>"`.
3. **Found -> reconcile** (this is the conflict case):
   - **`rec.updated_at <= todo.external_updated_at`** -> **skip** (nothing new
     from the SoR since the last ingest). Cheap idempotency gate.
   - **SoR changed AND gludd hasn't touched it locally** (todo is still in an
     ingest-owned state `backlog`/`queued` and its `version` is unchanged since
     ingest) -> **update** title/body/labels/priority/status via
     `TodoRepository.update(..., expected_version=todo.version)`; bump
     `external_updated_at`. The optimistic `version` guard
     (`repository.py:90-136`) makes this safe under concurrent ticks.
   - **SoR changed AND gludd is mid-work** (todo is `active`/`awaiting_result`/
     `reviewing_return`/etc.) -> **conflict, gludd wins the workflow state**:
     do **not** rewrite `status` (the event loop owns it), but DO refresh the
     advisory fields (title/body/labels) and record a `TodoEventModel`
     `event_type="external_conflict"` with the diff in `reason`, so a human/the
     reviewer can see the SoR moved underneath an in-flight job. Surfacing, not
     clobbering — this matches the codebase's "loser never silently clobbers the
     winner" stance (`repository.py:99-104`).
   - **SoR says closed/cancelled while gludd is mid-work** -> record
     `external_conflict`, leave the job to finish; the reconcile phase will
     converge on `complete`/`cancelled` afterward (and write-back becomes a
     no-op since the SoR is already closed).

**Conflict policy summary**: SoR is authoritative for *content* (title, body,
labels, priority) and for *initial* state; gludd is authoritative for the
*workflow* state once a todo leaves the ingest-owned states. Divergence is
always recorded as a `TodoEventModel`, never silently dropped.

### 3.3 Sync loop / poller, and where it wires into the daemon

The poller is a new **EventLoop phase**, registered exactly like the existing
phases (`event_loop/loop.py:38-50` `PHASE_ORDER`, `:377-386` `_run_phases`):

1. Add `"sync_issue_sources"` to `PHASE_ORDER` — placed **first**, before
   `claim_runnable_todos`, so freshly-ingested `queued` todos are claimable in
   the same tick.
2. Implement `async def _phase_sync_issue_sources(self)` on `EventLoop`,
   mirroring `_phase_evaluate_pid_controllers` (`loop.py:589-616`): wrapped in
   try/except so a failing source never aborts the tick (the phase runner
   already isolates exceptions per-phase at `loop.py:381-386`).

Phase body:

```python
async def _phase_sync_issue_sources(self) -> None:
    cfg = self._config_snapshot.get("issue_sources", [])
    if not cfg:
        return
    interval = ...  # per-source poll interval; skip a source if last_poll + interval > now
    for src_cfg in cfg:
        source = self._issue_registry.get(src_cfg["name"])   # IssueSourceRegistry, §3.6
        if source is None or not self._due(source, now):
            continue
        spec = {**src_cfg.get("spec", {}), "updated_since": self._cursor.get(source.name)}
        try:
            records = source.fetch(spec)         # bounded, paginated, incremental
        except Exception as exc:
            logger.error("issue-source %s fetch failed: %s", source.name, exc)
            continue                             # resilience, like Observability.find
        async with self._active_session_factory() as session:
            repo = IssueIngestRepository(session)
            for rec in records:
                await repo.ingest(rec, project_id=src_cfg.get("project_id"))
            await session.commit()
        self._cursor[source.name] = max((r["updated_at"] or 0) for r in records) or self._cursor.get(source.name)
        self._tick_metrics["issues_ingested"] += len(records)
```

The `EventLoop` is constructed in `daemon.py:551-577`; the registry +
per-source config are loaded from the startup config there (alongside
`queues`/`model_profiles`) and the `project_secrets_manager`
(`secrets_resolver`) is reused for per-project secret env overlays. The
incremental cursor (`updated_since` per source) lives in EventLoop tick state
and is persisted via a `VariableNamespace` (`variable_namespaces`/
`variable_values`, `db/models.py:259-307`) namespaced
`issue_source_cursor` so it survives restarts. Poll interval is a per-source
config key (e.g. `poll_interval_seconds: 300`); the phase no-ops between
intervals so it doesn't hammer the SoR every 1s tick.

### 3.4 Write-back lifecycle + idempotency

Write-back is driven by **todo state changes**, fired from the event loop, not
the poller's read path:

- **Claim -> in-progress**: when a todo with `issue_source != NULL` is claimed
  (`TodoRepository.claim_runnable` flips `QUEUED->ACTIVE`,
  `repository.py:163-232`), enqueue a `write_back(external_id, CLAIMED)`. The
  claim already writes a `TodoEventModel` (`repository.py:221-229`); write-back
  hooks off that event so it's emitted exactly once per claim.
- **Complete -> closed**: when the reconcile phase moves a todo to
  `COMPLETE` (the only terminal success state, `repository.py:50`), enqueue
  `write_back(external_id, COMPLETED)`. Similarly `FAILED`/`BLOCKED` ->
  advisory write-backs.

Write-backs run in a **second phase** `"flush_issue_writebacks"` (added to
`PHASE_ORDER` near `emit_tick_metrics`, i.e. after dispatch), draining a
durable outbox rather than calling the SoR inline during a DB transaction. The
outbox is a small table (or reuses the `agent_messages` queue,
`db/models.py:404-437`, recipient `"issue_writeback"`) so a write-back survives
a crash between the local commit and the SoR call. Per outbox row:

1. Call `source.write_back(external_id, transition)`.
2. **Idempotency** is enforced two ways: (a) the adapter does GET-then-mutate
   and returns `no_op=True` if the SoR is already in the target state (§2); and
   (b) the outbox row carries the originating `todo.version`, so replaying a
   stale write-back after the todo advanced is detected and dropped.
3. On `ok=True` -> delete/ack the outbox row, record `TodoEventModel`
   `event_type="writeback_applied"`.
4. On `ok=False` -> leave the row, increment `attempts`, **exponential backoff**
   with jitter; after `max_attempts` (config, default 5) move to a
   `dead_letter` state and record `writeback_failed` + emit an
   `agent_messages` broadcast so it's visible (the codebase's
   "unseen events aren't events" invariant). Never block the tick; never raise
   out of the phase (`write_back` is contractually no-raise, §1.2).

This gives **at-least-once** delivery with **idempotent apply** =
effectively-once SoR state, the standard outbox pattern, and matches the
repo's first-writer-wins / no-clobber concurrency philosophy.

### 3.5 Failure / retry semantics summary

| Failure | Handling |
|---|---|
| `fetch()` transport error | logged, source skipped this tick, cursor unchanged -> retried next interval (no data loss; `updated_since` re-pulls) |
| partial page failure | adapter raises -> whole source skipped this tick; next tick re-pulls from last cursor |
| ingest DB lost-race | `ON CONFLICT DO NOTHING` + re-select converges (no duplicate todo) |
| `write_back()` transport error | `ok=False` -> outbox retry w/ backoff, then dead-letter + broadcast |
| SoR already in target state | adapter returns `no_op=True` -> ack, no duplicate transition |
| stale write-back replay | outbox version guard drops it |
| file locked (xlsx/markdown) | `ok=False` -> retried; atomic temp+rename prevents corruption |

### 3.6 Registry + config-driven construction

`IssueSourceRegistry` mirrors `connectors/base.py`'s `SourceRegistry:155-179`
(name->source map, `register`/`get`/`by_source(kind)`/`all`, last-write-wins).
A small factory maps the `SOURCE` string to the adapter class
(`{"jira": JiraIssueSource, ...}`) and constructs each from its config block —
this is the **config-driven dispatch** the connectors lack but the issue layer
needs (sources are declared in yaml, not imperative code). Example config
(loaded by `daemon.py` into the startup config, validated by a pydantic model):

```yaml
issue_sources:
  - name: jira-acme
    source: jira
    base_url: https://acme.atlassian.net
    user_env: JIRA_USER
    token_env: JIRA_TOKEN
    project_id: proj-abc123          # validated against ACTIVE projects (routers/todos.py:85)
    poll_interval_seconds: 300
    spec: { jql: "project = PROJ AND statusCategory != Done" }
    status_map: { "Code Review": queued }   # optional read-map override
    writeback: { in_progress_status: "In Progress", done_status: "Done" }
```

A pydantic `IssueSourceConfig` model validates each block at load time (unknown
`source` -> startup error; missing required env-var *names* -> startup error;
`base_url` runs the SSRF guard at construction, so a private-IP base_url fails
fast at boot, not at first poll).

---

## 4. Capability-policy / grants

Issue ingestion is an **external-write** capability and must be gated, not
ambient:

- **Read grant** (`fetch`): a source needs a per-project grant
  `issue_source.read:<SOURCE>`. Without it the source is registered but its
  poll phase skips it. Read is comparatively low-risk (it only creates
  `queued` todos) but still costs API quota and can flood the backlog, so it's
  rate-limited per source (the `poll_interval_seconds` floor) and bounded per
  fetch (`spec` max page count).
- **Write-back grant** (`write_back`): a **separate, stronger** grant
  `issue_source.write:<SOURCE>` — mutating an external system-of-record is the
  high-blast-radius action. A source can be read-only (ingest issues, never
  write back) by holding read without write. When the write grant is absent,
  `flush_issue_writebacks` records `writeback_skipped_no_grant` instead of
  calling the SoR.
- **Project scoping**: every ingested todo carries the source's configured
  `project_id`, validated against **active** projects exactly as
  `routers/todos.py:85-97` does; a source pointed at an inactive/unknown project
  fails config validation. Cross-project ingest is impossible — a source writes
  todos only into its own project scope.
- **Secret isolation**: per §1.3, only env-var *names* live in config; the
  `project_secrets_manager` (`daemon.py` `secrets_resolver`) resolves the actual
  token per project, so project A's source cannot read project B's token.
- **Approval policy**: high-risk write-backs (e.g. closing a ServiceNow incident)
  can require `approval_policy != "none"` on the originating todo
  (`TodoModel.approval_policy`, `db/models.py:108`); the write-back phase holds
  such rows in the outbox until approved, reusing the existing
  `APPROVAL_REQUIRED` machinery rather than inventing a new gate.
- **Audit**: every ingest, conflict, and write-back records an
  `AuditEventModel` (`db/models.py:236-256`) via `AuditEventRepository`
  (`repository.py:451-516`) with `entity_type="issue_source"`,
  `entity_id=f"{SOURCE}:{external_id}"`, so the full external<->internal flow is
  attributable.

---

## 5. Test strategy (mocked transport; tests NOT written here)

Mirror the connector tests: every adapter is constructed with an **injected
transport**, so no test ever touches the network or the real filesystem.

**Unit — contract conformance (per adapter)**
- `isinstance(adapter, IssueSource)` holds (runtime-checkable Protocol), and
  `SOURCE` is in `VALID_SOURCES`.
- `__init__` raises `ValueError` when `transport=None` (mirrors
  prometheus/datadog `:129-147`).
- SSRF: `base_url` of `http://127.0.0.1`, `http://169.254.169.254`,
  `http://10.0.0.1`, `file://...`, `ftp://...` each raise `ValueError`
  (table-driven, reusing the `connectors` SSRF test vectors). File adapters:
  a path with `..` or a symlink escaping `root` raises.
- Secrets: with `token_env="X"` and `os.environ["X"]` set (monkeypatched), the
  outgoing request carries the right auth header; with the env var **unset**,
  no auth header is emitted and the secret value never appears on the object.

**Unit — `fetch` mapping (per adapter)**
- Feed a canned SoR JSON page through the mock transport; assert the returned
  `IssueRecord` has the exact `external_id`, `title`, mapped `gludd_status`,
  normalized `priority`, `labels`, `url`, `updated_at`. One case per row of the
  read-map table (every external status -> expected `TodoStatus`).
- Pagination: a 2-page mock; assert all records returned and the second page
  request carried the right cursor/offset.
- Incremental: `spec["updated_since"]` is threaded into the query
  (jql/`updated_on`/`since`/`sysparm_query`).

**Unit — `write_back` mapping + idempotency (per adapter)**
- `CLAIMED`/`COMPLETED`/`FAILED`/`BLOCKED` each issue the expected
  method+endpoint+body against the mock transport.
- Idempotency: mock the GET to report the SoR already in the target state ->
  assert `no_op=True` and **no** mutating call was made.
- No-raise: mock the transport to return a 5xx / raise -> assert `write_back`
  returns `WriteBackResult(ok=False)` and does not propagate.

**Unit — ingestion (`IssueIngestRepository`, in-memory SQLite like existing
repo tests)**
- create path: new `IssueRecord` -> one `TodoModel` with
  `issue_source`/`external_id` set, `status == gludd_status`, an `ingested`
  `TodoEventModel`.
- dedup: ingesting the same `(source, external_id)` twice -> one row
  (`uq_todo_external` upheld), second call returns `action="skipped"` or
  `"updated"`.
- conflict: ingest, claim (-> `active`), then ingest a newer SoR version ->
  `status` unchanged, an `external_conflict` `TodoEventModel` recorded.
- stale: ingest with `updated_at <= external_updated_at` -> `action="skipped"`,
  no write.
- project scoping: ingest with an inactive `project_id` is rejected the same way
  `routers/todos.py` rejects it.

**Unit — poller phase + write-back outbox**
- `_phase_sync_issue_sources` with a registry of two fake sources (one raising
  in `fetch`): assert the raiser is isolated and the other still ingests
  (resilience, like `Observability.find`).
- claim of an `issue_source` todo enqueues exactly one `CLAIMED` outbox row;
  complete enqueues exactly one `COMPLETED`.
- outbox drain: `ok=True` -> row acked + `writeback_applied` event;
  `ok=False` -> attempts incremented, backoff respected, dead-letter +
  broadcast after `max_attempts`.
- stale-replay: an outbox row whose `version` is behind the todo is dropped.

**Unit — capability policy**
- read grant absent -> source skipped; write grant absent ->
  `writeback_skipped_no_grant`, SoR never called.
- approval-required todo -> write-back held in outbox until approved.

No integration/E2E tests are specified here (design-only); the mocked-transport
unit suite fully covers the contract, mapping tables, idempotency, conflict
rules, and policy gates without any external dependency.
```text
```
