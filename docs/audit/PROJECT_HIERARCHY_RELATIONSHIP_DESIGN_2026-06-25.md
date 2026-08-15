# Project Hierarchy & Relationship Design (2026-06-25)

> STATUS: CURRENT — phase 1+2 shipped, phase 3 designed.

Status: DESIGN (apply-ready). Author: relationship-design pass.
Scope: codify where a gludd project lives relative to other projects (parent
environment, child projects, sibling/external projects), declare those edges in
config (names / directories / URLs — never guessed), record the interfaces a
project's position implies, and let the adaptive router/advisor **inherit
what-works-best knowledge across those edges with edge-distance decay**.

> Anything marked **[XCHECK-ROUTER]** depends on the in-flight router deep-read of
> `scoring/router.py` (`_get_best_with_embeddings` / `_similarity_weight` /
> `BenchmarkRepository.get_aggregate_scores`) — re-pin the cited line numbers and
> seam shape at build time before applying.

---

## 0. Grounding (what exists today — cite, do not duplicate)

- **Project entity.** `ProjectModel` (`src/general_ludd/db/models.py:37-53`):
  `project_id` (PK `proj-<hex8>`), `name`, `description`, `workspace_path`,
  `config` (JSON-in-Text), `active`, timestamps. **No relationship field today.**
  `ProjectRepository` (`db/repository.py:994-1034`) = `create` / `get_by_id` /
  `list_active` / `deactivate` (guarded UPDATE). Almost every other table carries
  a `project_id` FK with `ondelete="SET NULL"` (e.g. `TodoModel.project_id`
  `db/models.py:74-79`).
- **Project runtime + persistence seam.** `projects/manager.py`:
  `ProjectManager.add_project(... repo_url=, dispatch_mode=, **config)`
  (`:37-69`); `persist_project()` (`:233-272`) stuffs `repo_url/weight/
  dispatch_mode` into the JSON `config` column because `ProjectModel` has no
  dedicated columns; `rebuild_manager_from_db()` (`:275-304`) reads them back;
  `seed_from_config(config)` (`:307-326`) reads `config["projects"]` (a list of
  dicts). **This is exactly where declared relationships plug in.**
- **Config surface.** `config/user_config.py` `UserConfig(BaseSettings)`
  (`:65-132`): pydantic-settings, `env_prefix="GLUDD_"`,
  `env_nested_delimiter="__"`, `extra="ignore"`, YAML-then-env precedence.
  **`UserConfig` has no `projects` field** — `projects` is read loosely from the
  raw config dict by `seed_from_config`. Example config:
  `config/examples/user_config_example.yml`.
- **The borrowing seam (router).** `scoring/router.py`:
  - `AdaptiveRouter._get_best_from_history(task_type)` (`:162-219`) — exact-match
    path; if an `embedding_store` is present it calls
    `_get_best_with_embeddings`.
  - `_get_best_with_embeddings(task_type, sims)` (`:232-287`) — queries **all**
    task types via `get_aggregate_scores(task_type=None)`, then scales each
    candidate's quality by `_similarity_weight(similarity)` (`:221-230`,
    `floor + alpha*similarity`). This is the generalizable weighting hook.
  - `BenchmarkRepository.get_aggregate_scores()` (`db/repository.py:799-846`)
    **groups by `(prompt_profile_id, model_profile_id, task_type)` only —
    `BenchmarkResultModel` has NO `project_id` column** (`db/models.py:501-532`).
    So today benchmark history is **already cross-project (global)**; there is no
    per-project history key to borrow *across*. This is the central build-time
    fact — see §4.0. **[XCHECK-ROUTER]**
  - Task-type embeddings: `scoring/task_embeddings.py` `TaskEmbeddingStore`
    (`:124-214`) + `TaskEmbeddingModel` (`db/models.py:585-606`). The decay design
    in §4 mirrors this store's cosine-similarity map shape.
- **Surfacing.** `routers/environment.py` `GET /api/environment`
  (`:545-620`) assembles facets (`models/routing/budget/compute/tools/skills/
  queues/system/optimization`) + `/api/environment/advise` (`:622-668`).
  Advisor is pure: `controllers/environment_advisor.py` `build_optimization_hints`
  (`:277-368`) / `build_advice` (`:118-245`).
- **Ansible vars seam.** `VariableNamespaceModel` / `VariableValueModel`
  (`db/models.py:269-317`) + `VariableNamespaceRepository.load_vars_for_project`
  (`db/repository.py:691-708`) — project-scoped vars (with a NULL-project global
  fallback) materialized for roles.
- **Migration pattern to mirror.** `alembic/versions/002_add_projects_and_
  project_id.py` (table + FK + index, with matching `downgrade`) and
  `007_add_task_embeddings.py` (additive table + seed + dialect-guarded index).
  Head revision = `007`; the new migration is `008`.

---

## 1. Relationship data model

A project's edges are a directed graph. Rather than overload `ProjectModel.config`
JSON (un-queryable, no constraints), add a first-class edge table.

### 1.1 SQLAlchemy model (`db/models.py`, additive — append near `ProjectModel`)

```python
class RelationType(enum.StrEnum):
    PARENT = "parent"      # the environment THIS project runs inside
    CHILD = "child"        # a project that runs inside THIS one
    SIBLING = "sibling"    # peer under a shared parent (gludd may control it)
    EXTERNAL = "external"  # a neighbor gludd does NOT control


class LocationKind(enum.StrEnum):
    GLUDD_PROJECT_NAME = "gludd_project_name"  # resolves to a ProjectModel.name
    DIRECTORY = "directory"                    # absolute/relative path on disk
    URL = "url"                                # git/https/service URL


def _gen_rel_id() -> str:
    return f"rel-{uuid4().hex[:12]}"


class ProjectRelationshipModel(Base):
    """A declared edge from one project to a neighbor (parent/child/sibling/external).

    Edges are USER-DECLARED (config or API), never inferred, so the AI never
    guesses topology. The neighbor is identified by (location_kind, location_value):
    a gludd project NAME, a DIRECTORY path, or a URL. When the neighbor is itself a
    gludd project, ``related_project_id`` is resolved and FK-linked; for external
    neighbors it stays NULL and only the location fields identify it.
    """

    __tablename__ = "project_relationships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_gen_rel_id)
    # The project this edge belongs to (the "from" side). FK to projects, cascade
    # delete: an edge has no meaning without its owning project.
    project_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    location_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # The identifier of the neighbor under location_kind (a name, a path, a URL).
    location_value: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Resolved gludd project id of the neighbor, when it IS a gludd project and we
    # could resolve its name/dir/url to a ProjectModel. NULL for external/unresolved.
    # SET NULL (not CASCADE): losing the neighbor project must not delete the edge —
    # the operator's declared intent survives so it can re-resolve later.
    related_project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.project_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    controlled_by_gludd: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Optional free-form hint describing the interface this edge implies
    # (e.g. "GET /health", "publishes kafka topic orders"). See §3.
    interface_hint: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Optional structured interface contract (JSON-in-Text; see §3). Empty by default.
    interface_contract: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        # A project declares a given neighbor under a given relation at most once.
        UniqueConstraint(
            "project_id", "relation_type", "location_kind", "location_value",
            name="uq_project_relationship_edge",
        ),
        Index("ix_project_rel_from_type", "project_id", "relation_type"),
        Index("ix_project_rel_related", "related_project_id"),
    )
```

### 1.2 Cardinality / constraint choices

- **One parent, many of the rest.** A project may have **at most one** `parent`
  edge. SQL `UNIQUE` cannot express "one row of a given enum value per
  `project_id`" portably, so enforce it in the repository (§3 `set_parent`
  replaces any existing parent edge) **and** add a partial unique index where the
  backend supports it (PostgreSQL):
  `CREATE UNIQUE INDEX uq_one_parent ON project_relationships(project_id) WHERE relation_type='parent'`.
  On SQLite the repo guard is authoritative (mirrors how `002` leaves cross-tenant
  guards to the repo layer). Children/siblings/external are unbounded.
- **Two FKs to `projects`** with **different `ondelete`**: `project_id` is
  `CASCADE` (the owning side); `related_project_id` is `SET NULL` (a resolved
  neighbor) so deleting the neighbor degrades the edge to "declared but
  unresolved" rather than destroying the operator's declaration. This mirrors the
  repo-wide `SET NULL` convention for `project_id` FKs (`db/models.py` passim).
- **No self-edge.** Repository rejects `related_project_id == project_id` and a
  `location_value` resolving to self.
- **Edges are directed and NOT auto-reciprocal.** Declaring A's `parent=B` does
  **not** auto-create B's `child=A` (keeps declaration explicit and avoids write
  amplification across projects the operator may not own). The graph API (§3)
  offers an opt-in `reciprocate=True` helper and a consistency check that flags
  asymmetric edges.

### 1.3 Alembic migration sketch (`alembic/versions/008_add_project_relationships.py`)

Mirrors `002` (table+FK+index) and `007` (additive, dialect-guarded index).

```python
"""Add project_relationships table (declared project topology edges).

Revision ID: 008
Revises: 007
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres(bind: sa.engine.Connection) -> bool:
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "project_relationships",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("relation_type", sa.String(16), nullable=False),
        sa.Column("location_kind", sa.String(32), nullable=False),
        sa.Column("location_value", sa.String(1024), nullable=False),
        sa.Column("related_project_id", sa.String(32), nullable=True),
        sa.Column("controlled_by_gludd", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("interface_hint", sa.String(1024), nullable=True),
        sa.Column("interface_contract", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_project_id"], ["projects.project_id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "relation_type", "location_kind",
                            "location_value", name="uq_project_relationship_edge"),
    )
    op.create_index("ix_project_relationships_project_id",
                    "project_relationships", ["project_id"])
    op.create_index("ix_project_rel_from_type",
                    "project_relationships", ["project_id", "relation_type"])
    op.create_index("ix_project_rel_related",
                    "project_relationships", ["related_project_id"])
    # Postgres-only "one parent" partial unique index; SQLite relies on the repo guard.
    if _is_postgres(op.get_bind()):
        op.execute(
            "CREATE UNIQUE INDEX uq_one_parent ON project_relationships(project_id) "
            "WHERE relation_type = 'parent'"
        )


def downgrade() -> None:
    if _is_postgres(op.get_bind()):
        op.execute("DROP INDEX IF EXISTS uq_one_parent")
    op.drop_index("ix_project_rel_related", "project_relationships")
    op.drop_index("ix_project_rel_from_type", "project_relationships")
    op.drop_index("ix_project_relationships_project_id", "project_relationships")
    op.drop_table("project_relationships")
```

> Note the repo runs an `alembic-check` ORM/migration parity gate (Makefile
> `alembic-check`, memory: migration-drift findings). The new ORM model **and**
> migration must land together or that gate fails.

---

## 2. Config surface — DECLARED, not inferred

The operator names neighbors so the AI never guesses topology. Two entry points:
static config (boot) and a runtime API (§3). Declared always wins over any future
inference.

### 2.1 Config shape (YAML; under each project in `projects:`)

Extends the existing `seed_from_config` list shape (`projects/manager.py:307-326`,
which already reads `name/weight/description/workspace_path/repo_url/
dispatch_mode`). Add a `relationships:` list per project:

```yaml
projects:
  - name: payments-api
    weight: 40
    repo_url: https://github.com/acme/payments-api
    workspace_path: payments-api
    relationships:
      - relation: parent
        location: acme-platform          # a gludd project NAME
        kind: gludd_project_name
        controlled_by_gludd: true
        interface_hint: "consumes platform auth: GET /oauth/introspect"
      - relation: child
        location: ./services/ledger       # a DIRECTORY (relative to workspace)
        kind: directory
        controlled_by_gludd: true
      - relation: external
        location: https://api.stripe.com  # a URL, NOT controlled by gludd
        kind: url
        controlled_by_gludd: false
        interface_hint: "calls Stripe Charges API; we observe only"
```

`kind` is optional: when omitted, `seed_from_config` infers it pragmatically
(starts with a scheme `://` → `url`; contains `/` or `.` path-like → `directory`;
else `gludd_project_name`). **Inference is only for the `kind` discriminator of an
explicitly-declared edge — never for the existence or target of a relationship.**

### 2.2 pydantic-settings mapping (`config/user_config.py`)

`UserConfig` currently has **no** typed `projects` field; `projects` flows through
the raw dict to `seed_from_config`. Two options:

- **Minimal (recommended for phase 1):** keep `projects` loosely-typed (it already
  works via the dict path) and validate the `relationships` sub-shape in
  `seed_from_config` + the repository. Zero `UserConfig` change; lowest blast
  radius.
- **Typed (phase 2 hardening):** introduce pydantic models and add a field:

  ```python
  class ProjectRelationshipConfig(BaseModel):
      relation: Literal["parent", "child", "sibling", "external"]
      location: str
      kind: Literal["gludd_project_name", "directory", "url"] | None = None
      controlled_by_gludd: bool = False
      interface_hint: str | None = None
      interface_contract: dict[str, Any] = {}

  class ProjectConfig(BaseModel):
      name: str
      weight: float = 10.0
      description: str = ""
      workspace_path: str = ""
      repo_url: str = ""
      dispatch_mode: str = "active"
      relationships: list[ProjectRelationshipConfig] = []

  class UserConfig(BaseSettings):
      ...
      projects: list[ProjectConfig] = []   # NEW typed field
  ```

  Env override: `GLUDD_PROJECTS='[{"name":"payments-api","relationships":[...]}]'`
  (the existing `from_yaml` JSON-merge at `:120-132` already handles
  `GLUDD_<FIELD>` JSON blobs).

### 2.3 Config → DB mapping

In `projects/manager.py`, when persisting a project (`persist_project`, `:233`),
also upsert its declared edges:

1. Resolve each edge's `related_project_id`: for `kind=gludd_project_name`, look up
   `ProjectModel` by name; for `kind=directory`, match `workspace_path`; for
   `kind=url`, match the `repo_url` stored in `config`. Unresolved → `related_
   project_id=NULL`, edge kept (declared intent preserved; re-resolve on the next
   `rebuild_manager_from_db`).
2. `ProjectRelationshipRepository.upsert_edge(...)` (§3) keyed on the unique edge
   tuple, idempotent like `persist_project`.
3. `controlled_by_gludd` defaults from the resolution: a resolved gludd project is
   controllable unless the operator says otherwise; an unresolved URL/dir defaults
   to `False`.

---

## 3. Interface contracts (pragmatic, not over-engineered)

A project's **position** implies expected interfaces: a `child` typically exposes
an endpoint the `parent` consumes; an `external` neighbor exposes an interface we
only **observe**. gludd records this as a lightweight contract, validates only what
it cheaply can, and surfaces it.

### 3.1 What gludd records

`interface_hint` (free text, always) + optional structured `interface_contract`
(JSON-in-Text, same convention as `ProjectModel.config` / `FeatureModel`):

```json
{
  "direction": "exposes" | "consumes" | "observes",
  "protocol": "http" | "grpc" | "kafka" | "cli" | "library" | "other",
  "endpoints": [
    {"name": "health", "spec": "GET /health", "expected_status": 200}
  ],
  "notes": "free text"
}
```

`direction` is derived-with-override from `relation_type`:
- `child` → default `exposes` (child exposes; parent consumes)
- `parent` → default `consumes`
- `sibling` → default `consumes` (peer-to-peer)
- `external` → forced `observes` when `controlled_by_gludd=False` (we cannot assert
  a contract we don't control; we only record what we see).

### 3.2 What gludd validates / exposes (pragmatic floor)

- **Phase-1 validation = shape only.** Validate the JSON parses and `direction/
  protocol` are in-enum. No live probing in phase 1.
- **Phase-2 (optional) liveness probe.** For `protocol=http` + an `endpoints[].spec`
  like `GET /health`, an opt-in check can issue the request **through the existing
  SSRF guard** (`connectors/_ssrf_guard.py`, `security/ssrf.py`) and record
  pass/fail. Reuse, don't reinvent — and never probe `external` URLs without the
  SSRF allow-list, mirroring `materialize_project_workspace`'s
  `reject_unsafe_repo_url` fail-closed posture (`projects/manager.py:189-199`).
- **Exposure** is via `/api/environment` (§5): `project.relationships[].interface`
  carries the hint + contract so a running job knows "I am a child of X; X expects
  me to expose GET /health".

### 3.3 Repository (`db/repository.py`, new `ProjectRelationshipRepository`)

```python
class ProjectRelationshipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_edge(self, data: dict[str, Any]) -> ProjectRelationshipModel: ...
        # ON CONFLICT (uq_project_relationship_edge) DO UPDATE — mirrors
        # PromptProfileRepository.upsert (db/repository.py:881-904).

    async def set_parent(self, project_id: str, edge: dict[str, Any]) -> None: ...
        # Guarded: delete any existing relation_type='parent' row for project_id,
        # then insert — enforces the one-parent rule on SQLite (PG has the partial
        # unique index). Single transaction.

    async def list_edges(
        self, project_id: str, relation_type: str | None = None
    ) -> list[ProjectRelationshipModel]: ...

    async def neighbors(
        self, project_id: str, relation_type: str | None = None
    ) -> list[ProjectRelationshipModel]: ...
        # Resolved gludd-project neighbors (related_project_id IS NOT NULL).

    async def graph_from(self, project_id: str, max_depth: int = 3) -> dict: ...
        # BFS over resolved edges up to max_depth. Cycle-safe (visited set).
        # Returns {project_id: [(neighbor_id, relation_type, edge_distance)]}.
        # This is the substrate the router decay (§4) walks.
```

A thin REST surface (`routers/projects.py`, which already exists) adds:
`POST /api/projects/{id}/relationships`, `GET /api/projects/{id}/relationships`,
`DELETE /api/projects/{id}/relationships/{rel_id}` — so an operator declares edges
at runtime, not only at boot.

---

## 4. Knowledge inheritance across edges (the core mechanism)

gludd tracks which prompts/models/skills work best per task-type (benchmark
aggregates → `AdaptiveRouter`). That knowledge should **flow along declared edges**:
when the current project's own history is thin, borrow a parent's/sibling's
proven picks — weighted DOWN by edge distance and control.

### 4.0 The build-time fact that shapes this (READ FIRST) **[XCHECK-ROUTER]**

`BenchmarkResultModel` has **no `project_id` column** (`db/models.py:501-532`) and
`get_aggregate_scores` groups by `(prompt_profile_id, model_profile_id, task_type)`
only (`db/repository.py:799-846`). **Today benchmark history is global, not
per-project.** Cross-project inheritance therefore requires first making history
**project-aware**, then borrowing across the project edges. Two sub-steps:

1. **Add `project_id` to benchmark history** (additive column + index; same
   `SET NULL` FK convention). `record_result` already takes a `data` dict so the
   caller passes `project_id`. `get_aggregate_scores` gains an optional
   `project_id` filter and a `project_id` group key.
2. **Generalize the existing similarity-weighting seam** from *task-type
   similarity* to *(task-type similarity) × (project-relationship weight)*. The
   `_get_best_with_embeddings` machinery (`router.py:232-287`) already queries all
   rows and multiplies quality by a weight — we widen that weight.

If step 1 is deemed too invasive for an early phase, an interim form keeps history
global but **scopes the ROUTER's view**: a project-scoped router only *prefers*
picks it has local evidence for and falls back to global — strictly weaker than
true edge-decay borrowing, and explicitly a stepping stone.

### 4.1 Where project_id enters the history key

History key becomes `(project_id, prompt_profile_id, model_profile_id, task_type)`.
The router is constructed (or re-scoped) per dispatched project — the daemon already
threads `project_id` everywhere (e.g. `TodoRepository.scoped`,
`db/repository.py:137-147`). `AdaptiveRouter.__init__` gains:
`project_id: str | None`, `relationship_graph: dict | None`
(from `ProjectRelationshipRepository.graph_from`), and two decay knobs
`edge_decay: float = 0.5`, `external_penalty: float = 0.5`.

### 4.2 Data flow

```text
route(task_type, project_id=P)
  -> get_aggregate_scores(project_id=P, task_type=...)            # own history
     thin? (sum(sample_count) < min_samples)                     # threshold gate
        -> graph_from(P, max_depth)  -> [(neighbor N, rel, dist)] # declared edges
        -> for each N: get_aggregate_scores(project_id=N, ...)    # borrowed history
        -> weight each borrowed candidate by:
              task_sim_weight (existing _similarity_weight)       # §4 task axis
            * project_rel_weight(rel, dist, controlled)           # NEW project axis
        -> rank borrowed ∪ own by _cost_adjusted_rank(...)        # router.py:289-302
  -> RoutingDecision(reason="inherited_<rel>_history" | "best_historical_score")
```

Own-project candidates always carry `project_rel_weight = 1.0`. Borrowed candidates
are strictly ≤ own. The existing `_similarity_weight` (task-type axis) stays
intact; the project axis multiplies it — **two independent similarity dimensions**.

### 4.3 The weighting formula (generalizes task-type similarity)

Existing task axis (`router.py:221-230`):
`task_w(sim) = similarity_floor + similarity_alpha * sim`.

New project-relationship axis:

```text
project_rel_weight(relation, edge_distance, controlled_by_gludd) =
    base[relation] * (edge_decay ** (edge_distance - 1)) * control_factor

  base = { own: 1.00, parent: 0.80, child: 0.60, sibling: 0.70, external: 0.40 }
  edge_decay        = 0.5   # parent(d=1)=0.80; grandparent(d=2)=0.40; great-gp(d=3)=0.20
  control_factor    = 1.0 if controlled_by_gludd else external_penalty (0.5)
```

Final per-candidate quality multiplier:

```text
quality_multiplier = task_w(task_similarity) * project_rel_weight(rel, dist, ctl)
```

Worked: a **parent** (d=1, controlled) proven model on the **same** task type:
`task_w(1.0)=1.0 * (1.0 * 0.5^0 * 1.0)=0.80` → 80% of an own-project pick — it can
win when own history is empty but loses to any decent own-project evidence. A
**grandparent** controlled: `0.80 * 0.5 = 0.40`. An **external** sibling-of-parent
on a *similar* (not identical) task: `task_w(0.7)=0.7 * (0.70*0.5*0.5)=0.7*0.175 ≈
0.12` — present but heavily discounted. Decay is monotone in distance; controlled
always ≥ uncontrolled at equal distance; parent ≥ child (parents are the
environment-of-record). Tunable via the `edge_decay`/`external_penalty`/`base`
knobs (config under `model_routing` or a new `relationship_routing` block).

### 4.4 What is borrowed

The same row carries **model_profile_id + prompt_profile_id**, so borrowing a
benchmark aggregate inherits **both the best model AND the best prompt** for that
task type. Skill effectiveness rides along when skill usage is recorded into the
benchmark task description / tags (future). Task embeddings are **global already**
(`TaskEmbeddingModel` has no project_id, `db/models.py:585-606`) — they need no
per-project copy; the project axis is orthogonal to the task-embedding axis.

### 4.5 Safety

- Borrowing is **read-only and additive** — it can only *add* candidates the router
  would otherwise lack; it never raises a borrowed pick above an equally-scored own
  pick (own weight 1.0 ≥ any borrowed weight).
- The cost cap / fail-closed logic (`router.py:88-116`, `_exceeds_cap` NaN→over) is
  unchanged: an inherited pick still passes the same budget gate.
- Cycle-safe BFS (`graph_from` visited set) — a `parent↔child` mutual declaration
  cannot loop.
- Decay floor: candidates whose final multiplier `< min_borrow_weight` (e.g. 0.05)
  are dropped to avoid noise from very distant/external edges.

---

## 5. Surfacing

### 5.1 `/api/environment` (`routers/environment.py`)

Add two facets to `EnvironmentBrief` (`:120-131`) + the handler (`:559-620`),
each independently guarded and fail-soft to `[]`/`{}` like every existing facet:

```python
class EnvironmentBrief(BaseModel):
    ...
    project: dict[str, Any] = {}   # NEW
```

```python
def _project_facet(app, project_id) -> dict:
    # relationships: resolved + declared edges with interface contracts
    # inherited_knowledge: what the router would borrow for common work-types
    return {
        "project_id": project_id,
        "relationships": [
            {"relation": "parent", "location_kind": "gludd_project_name",
             "location_value": "acme-platform", "controlled_by_gludd": True,
             "related_project_id": "proj-abc123", "edge_distance": 1,
             "interface": {"direction": "consumes", "hint": "GET /oauth/introspect"}},
            ...
        ],
        "inherited_knowledge": {
            "feature": {"model_profile": "glm-4.6", "prompt_profile": "pp-xyz",
                        "source_project_id": "proj-abc123", "relation": "parent",
                        "edge_distance": 1, "weight": 0.8, "sample_count": 12},
            ...   # per common work-type, only when own history is thin
        },
    }
```

`relationships` comes from `ProjectRelationshipRepository.list_edges` +
`graph_from`; `inherited_knowledge` is the **router's borrow result rendered for
explanation** — it calls the same §4 path read-only per common work-type. This is
the "AI understands its position + inherited picks" surface the user asked for.
`project_id` is resolved from the request scope (query param or the daemon's active
project), defaulting to the single active project.

### 5.2 Role / collection layer as dynamic ansible vars

The role layer reads project-scoped vars via `VariableNamespaceRepository.
load_vars_for_project` (`db/repository.py:691-708`) and the `gludd_environment` /
`gludd_facts` ansible modules. Expose relationships + inherited picks as vars under
a `relationships` namespace so playbooks/roles consume them without bespoke code:

```text
gludd_parent_project        = "acme-platform"
gludd_parent_controlled     = true
gludd_children_projects     = ["ledger", "notifications"]
gludd_external_neighbors    = ["https://api.stripe.com"]
gludd_inherited_model__feature  = "glm-4.6"     # borrowed pick per work-type
gludd_inherited_prompt__feature = "pp-xyz"
gludd_interface_expectations    = [{"neighbor": "acme-platform", "direction": "consumes", "spec": "GET /oauth/introspect"}]
```

Materialization: on project seed/rebuild, write these into a `relationships`
`VariableNamespaceModel` for the project (idempotent `set_var`,
`db/repository.py:716-768`). The existing NULL-project global fallback means a
project with no declared edges simply inherits nothing — no special-casing. The
`gludd_environment` module already injects the `/api/environment` brief, so the
`project` facet flows to roles for free; the dedicated vars are the
ergonomic/templatable form.

---

## 6. Phased, independently-testable build plan

Each phase is shippable and gated on its own tests. Phases 1–3 are pure
plumbing (no router risk); 4 is the **[XCHECK-ROUTER]** phase.

| Phase | Deliverable | Test approach |
|---|---|---|
| **P1 — schema + migration** | `RelationType`/`LocationKind` enums, `ProjectRelationshipModel`, `008` migration (+ PG partial unique index). | Unit: model create/round-trip; FK cascade (delete owning project → edges gone) and SET NULL (delete neighbor → `related_project_id` NULL). `make alembic-check` ORM/migration parity. Migrate-up/down on a temp SQLite DB. |
| **P2 — config surface** | `relationships:` parsing in `seed_from_config`; optional typed `ProjectConfig`/`ProjectRelationshipConfig`; `kind` inference; config→DB upsert in `persist_project`. | Unit: YAML → declared edges (names/dirs/urls); `GLUDD_PROJECTS` env JSON override; idempotent re-seed (no dup rows); unresolved neighbor kept with NULL `related_project_id`. |
| **P3 — repository + graph API** | `ProjectRelationshipRepository` (`upsert_edge`/`set_parent`/`list_edges`/`neighbors`/`graph_from`); REST in `routers/projects.py`. | Unit: one-parent guard (second parent replaces first); `graph_from` BFS depth + cycle-safety; self-edge rejection. API: POST/GET/DELETE edges; PSK-gated. |
| **P4 — router cross-project borrowing** **[XCHECK-ROUTER]** | `project_id` on `BenchmarkResultModel` (+ migration `009`); `get_aggregate_scores(project_id=...)`; `AdaptiveRouter` project-axis (`project_rel_weight`, decay knobs); borrow path in `_get_best_from_history`. | Unit: own-history-only unchanged (regression vs current `router.py` tests); thin-own-history borrows parent pick with `reason="inherited_parent_history"`; decay monotonicity (parent > grandparent > external); own pick always ≥ borrowed at equal score; cost-cap still fail-closed. |
| **P5 — `/api/environment` surfacing** | `project` facet (`relationships` + `inherited_knowledge`); guarded + fail-soft. | API: facet present with declared edges; thin-history project shows inherited picks; no-edge project shows empty facet, never 500 (mirror existing facet-failure tests). |
| **P6 — ansible var exposure** | `relationships` namespace vars on seed/rebuild; `gludd_environment` carries the `project` facet. | Unit: vars materialized per project (idempotent); role/molecule scenario asserts `gludd_parent_project` / `gludd_inherited_model__feature` rendered. |

---

## 7. Open questions for the user (with stated default assumptions)

1. **External (non-gludd-controlled) knowledge.** Do we store only the *observed*
   interface of an external neighbor, or also let the operator feed in known-good
   prompts/models for work against it?
   **Default:** store observed interface + `interface_hint` only; external history
   is borrowed (if any exists) at the heavy `external_penalty` (0.4 × control 0.5).
   Operator-fed prompts are out of scope until requested.
2. **Auto-reciprocity.** Should declaring A.parent=B auto-create B.child=A?
   **Default: NO** (explicit declaration; opt-in `reciprocate=True` helper + a
   consistency check that flags asymmetric edges). Avoids writing into projects the
   operator may not own.
3. **Per-project benchmark history (P4 step 1).** Add `project_id` to
   `BenchmarkResultModel` now, or ship the interim "scope the router's view of
   global history" form first?
   **Default:** add the column (additive, low-risk, `SET NULL` FK) — true
   edge-decay borrowing needs a per-project key; the interim form is a strictly
   weaker stepping stone.
4. **Decay constants.** Are the defaults (`base` map, `edge_decay=0.5`,
   `external_penalty=0.5`, `min_borrow_weight=0.05`) acceptable, or should they be
   operator-tunable from day one?
   **Default:** ship the constants under a `relationship_routing` config block
   (overridable) with these defaults.
5. **Directory/URL resolution to gludd projects.** When a `directory`/`url` edge
   matches an existing project's `workspace_path`/`repo_url`, auto-resolve
   `related_project_id`?
   **Default: YES**, best-effort on seed/rebuild; unresolved stays NULL and
   re-resolves on the next rebuild (declared intent never lost).
6. **Interface liveness probing.** Should phase-2 actually probe `child`/`sibling`
   `GET /health` endpoints?
   **Default:** record + shape-validate only in early phases; live probing is
   opt-in, behind the existing SSRF guard, fail-closed.

---

## 8. Apply-ready summary

- **Data model:** new `ProjectRelationshipModel` edge table (`project_id` CASCADE +
  `related_project_id` SET NULL, `relation_type`/`location_kind`/`location_value`,
  `controlled_by_gludd`, `interface_hint`/`interface_contract`), unique per edge
  tuple, one-parent enforced (PG partial index + repo guard). Migration `008`
  mirrors `002`/`007`.
- **Config:** `relationships:` list per project in the existing `projects:` config,
  parsed by `seed_from_config`, mapped to edges in `persist_project`; declared >
  inferred (only the `kind` discriminator is ever inferred).
- **Inheritance seam:** generalize `_similarity_weight` (task axis) by multiplying a
  new **project-relationship axis** `project_rel_weight(relation, edge_distance,
  controlled) = base[relation] * edge_decay**(dist-1) * control_factor` onto each
  borrowed candidate's quality, after making benchmark history project-aware
  (`project_id` on `BenchmarkResultModel`). Own=1.0 ≥ parent(0.8) ≥ grandparent(0.4)
  ≥ external(≤0.2). **[XCHECK-ROUTER]**
- **Phase-1 buildable slice:** `ProjectRelationshipModel` + migration `008` +
  `ProjectRelationshipRepository` + config parsing — pure plumbing, zero router
  risk, fully unit-testable, and immediately surfaceable in `/api/environment` as a
  `project.relationships` facet **before** any inheritance logic lands.
