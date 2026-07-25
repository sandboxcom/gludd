---
name: type-safety
description: Use when writing or reviewing code that carries type annotations (Python, Terraform, TypeScript). Defines what a "tight" variable shape is, enumerates the approved type constructs per language, lists the Any-based anti-patterns to avoid, and gives a tracing workflow for identifying the correct type. Pairs with `make check-types` which mechanically flags `Any` usage in new Python code.
---

# Type Safety

A type annotation is a *contract*. A contract that says "anything" (`Any`,
`object`, `any`, `interface {}`) is not a contract — it is the absence of one.
This skill defines what a **tight** variable shape is and how to produce one
when the obvious answer is `Any`.

## What "tight" means

A variable shape is **tight** when it carries the maximum information the
caller/consumer actually relies on, and no more. Concretely:

- Every field of a record has a named, specific type — not `dict[str, Any]`.
- Every element of a collection has a named element type — not `list[Any]`.
- Every function either returns a concrete type or signals failure via a
  precise union (`T | None`, `Result[T, E]`), never `-> Any`.
- `Optional[T]` / `T | None` is used **only** when `None` is a legitimate
  in-domain value (absent, not-yet-set, skipped). It must not be a euphemism
  for "I don't know what this returns."

A shape is **loose** when a reader cannot, from the annotation alone, name the
fields, element types, or failure modes. Loose shapes migrate bugs from
compile-time to run-time.

---

## Approved constructs, by language

### Python

| Need | Use | Not |
|---|---|---|
| A record/dict with known keys | `TypedDict` (or `pydantic.BaseModel`) | `dict[str, Any]` |
| A bag of validated config | `pydantic.BaseModel` with explicit fields | `dict[str, Any]` |
| A callback / callable shape | `typing.Protocol` | `Callable[..., Any]` |
| A function signature | `typing.Protocol` or `Callable[[A, B], R]` | `Callable[..., Any]` |
| A distinguished primitive (user-id vs int) | `typing.NewType` | bare `int` |
| A fixed set of values | `enum.Enum` / `Literal[...]` | `str` + free-text |
| A value-or-absent | `T \| None` (only if `None` is in-domain) | `Optional[Any]` |
| Heterogeneous tuple | `tuple[int, str, bool]` | `tuple[Any, ...]` |
| A container you genuinely cannot narrow | `object` + an `isinstance` narrowing comment | `Any` |

Prefer `pydantic.BaseModel` when you need runtime validation; prefer
`TypedDict` for static-only structural shapes (e.g., the body of a JSON
payload you only read). Prefer `Protocol` for duck-typed parameters — it
documents the surface area you actually depend on instead of demanding a
concrete class.

#### Python code examples — BEFORE (anti-pattern) vs. AFTER (tight type)

##### TypedDict

```python
# BEFORE — loose: caller cannot know the keys, typos invisible
def process_config(raw: dict[str, Any]) -> dict[str, Any]:
    name = raw["naem"]  # typo — no error until runtime
    return {"result": name.upper()}

# AFTER — tight: every key named, mypy catches typos at static-check time
from typing import TypedDict

class ConfigInput(TypedDict):
    name: str
    timeout: int
    retries: int

class ConfigOutput(TypedDict):
    result: str

def process_config(raw: ConfigInput) -> ConfigOutput:
    name = raw["naem"]  # mypy: TypedDict "ConfigInput" has no key "naem"
    return {"result": name.upper()}
```

##### pydantic.BaseModel

```python
# BEFORE — loose: deserialised blob, every consumer re-validates ad-hoc
def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)

# AFTER — tight: validated once at boundary, consumers get typed fields
from pydantic import BaseModel, PositiveInt

class AppConfig(BaseModel):
    host: str
    port: PositiveInt = 8080
    database_url: str
    pool_size: int = 10

def load_config(path: str) -> AppConfig:
    with open(path) as f:
        return AppConfig.model_validate_json(f.read())
```

##### Protocol (for callbacks and duck-typed parameters)

```python
# BEFORE — loose: any callable accepted, no signature check
def run_tasks(tasks: list[str], handler: Callable[..., Any]) -> None:
    for t in tasks:
        handler(t)  # caller doesn't know what args to pass

# AFTER — tight: Protocol documents the surface the function actually uses
from typing import Protocol

class TaskHandler(Protocol):
    def __call__(self, task_name: str, /) -> bool: ...

def run_tasks(tasks: list[str], handler: TaskHandler) -> None:
    for t in tasks:
        ok = handler(t)
        if not ok:
            break
```

##### Full Callable signature (when Protocol is overkill)

```python
# BEFORE — loose
def debounce(fn: Callable, wait_ms: int) -> Callable: ...

# AFTER — tight: parameter and return types visible at call site
from typing import TypeVar

T = TypeVar("T")

def debounce(
    fn: Callable[[], T],
    wait_ms: int,
) -> Callable[[], T | None]: ...
```

##### NewType (distinguished primitives)

```python
# BEFORE — loose: bare int, nothing prevents passing wrong ID
def get_agent(agent_id: int) -> Agent: ...
def get_worktree(worktree_id: int) -> Worktree: ...

# This compiles silently but is semantically wrong:
agent = get_agent(worktree_id=42)  # worktree_id passed as agent_id

# AFTER — tight: NewType prevents cross-wiring
from typing import NewType

AgentId = NewType("AgentId", int)
WorktreeId = NewType("WorktreeId", int)

def get_agent(agent_id: AgentId) -> Agent: ...
def get_worktree(worktree_id: WorktreeId) -> Worktree: ...

agent = get_agent(agent_id=WorktreeId(42))  # mypy: error — wrong type
```

##### Enum + Literal

```python
# BEFORE — loose: free-text string, no exhaustiveness check
def set_status(task_id: str, status: str) -> None:
    if status == "pending":
        ...
    elif status == "running":
        ...
    # "pendng" silently falls through — no error

# AFTER — tight: Enum for DB storage, Literal for API surface
from enum import Enum
from typing import Literal

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"

# API layer uses Literal for the narrow set:
ApiStatus = Literal["pending", "running", "completed"]

def set_status(task_id: str, status: ApiStatus) -> None:
    if status == "pending":
        ...
    elif status == "running":
        ...
    elif status == "completed":
        ...
    # mypy: exhaustiveness check — no fall-through possible

# DB layer converts from/to Enum:
def _to_enum(status: ApiStatus) -> TaskStatus:
    return TaskStatus(status)
```

##### T | None (only when None is a domain value)

```python
# BEFORE — loose: Optional[Any] is equivalent to Any
def find_agent(name: str) -> Optional[Any]: ...

# AFTER — tight: None meaningfully means "not found"
from typing import Optional

def find_agent(name: str) -> Agent | None:
    result = db.query(Agent).filter_by(name=name).first()
    return result  # type is Agent | None — caller MUST handle None
```

##### Homogeneous tuple (heterogeneous elements)

```python
# BEFORE — loose: tuple of unknown shape
Row = tuple[Any, ...]

# AFTER — tight: fixed-arity tuple with named positions
# (agent_id, agent_name, created_at)
AgentRow = tuple[int, str, datetime]

def parse_row(row: AgentRow) -> Agent:
    agent_id, name, created_at = row  # destructured with correct types
    return Agent(id=agent_id, name=name, created_at=created_at)
```

##### object + isinstance narrowing (last resort)

```python
# BEFORE — loose: Any silences every consumer
def handle_message(msg: Any) -> None: ...

# AFTER — tight: object is honest — consumer MUST narrow
def handle_message(msg: object) -> None:
    if isinstance(msg, DispatchCommand):
        _handle_dispatch(msg)
    elif isinstance(msg, StatusQuery):
        _handle_status(msg)
    else:
        raise TypeError(f"Unknown message type: {type(msg)}")
```

---

### Terraform

| Need | Use | Not |
|---|---|---|
| Input variable | `variable "x" { type = string }` (or `list(...)`, `map(...)`, `object(...)`) | untyped `variable "x" {}` |
| Structured input | `object({ name = string, ports = list(number) })` | `map(any)` |
| Output | `output "x" { value = ... }` with a documented type | `type = any` |
| A module's surface | explicit `type` on every variable + output | reliance on inference |

`any` in Terraform is the same regression as `Any` in Python — it disables
`terraform validate` as a meaningful check. Always declare `type`.

#### Terraform code examples

##### Variables — BEFORE vs. AFTER

```hcl
# BEFORE — loose: terraform validate cannot check callers
variable "cluster_config" {}
variable "node_count" {}
variable "tags" {}

# AFTER — tight: every variable typed; validate catches shape errors
variable "cluster_config" {
  type = object({
    name       = string
    region     = string
    k8s_version = optional(string, "1.29")
    node_pools = list(object({
      name         = string
      machine_type = string
      min_count    = number
      max_count    = number
      disk_size_gb = optional(number, 100)
    }))
  })
}

variable "node_count" {
  type    = number
  default = 3

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 10
    error_message = "node_count must be between 1 and 10."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
```

##### Outputs — BEFORE vs. AFTER

```hcl
# BEFORE — loose: consumer cannot predict the shape
output "cluster_endpoint" {
  value = module.gke.endpoint
}

# AFTER — tight: caller knows the output is a string with description
output "cluster_endpoint" {
  value       = module.gke.endpoint
  description = "The IP address of the GKE cluster endpoint"
}
```

##### Module interface — full example

```hcl
# modules/database/variables.tf
variable "instance" {
  type = object({
    name     = string
    tier     = string           # e.g. "db-f1-micro"
    version  = optional(string, "POSTGRES_15")
    databases = optional(list(string), [])
    users = optional(list(object({
      name     = string
      password = string  # sensitive — marked below
    })), [])
  })
}

# modules/database/outputs.tf
output "connection_name" {
  value       = google_sql_database_instance.main.connection_name
  description = "The Cloud SQL instance connection name (project:region:name)"
}
```

---

### TypeScript

| Need | Use | Not |
|---|---|---|
| An object shape | `interface` or `type` alias | `object` / `Record<string, any>` |
| A keyed map | `Record<K, V>` with concrete `K` and `V` | `{ [k: string]: any }` |
| A callback | `type Handler = (event: E) => void` | `Function` / `(...args: any[]) => any` |
| A constrained generic | `<T extends Foo>` | `<T>` then cast to `any` |
| A union of known shapes | a discriminated union | a loose `any`-bag with a `kind` field |
| An external you can't model | `unknown` + a type guard | `any` |

Prefer `unknown` over `any` for "I haven't parsed this yet" — `unknown`
forces a narrowing; `any` silently infects every consumer.

#### TypeScript code examples — BEFORE vs. AFTER

##### Interface vs. Record<any, any>

```typescript
// BEFORE — loose: consumer doesn't know what keys exist
function createTodo(data: Record<string, any>): Record<string, any> {
  return { id: crypto.randomUUID(), name: data.nam, done: false };
  // typo "nam" — no compile error, returns { id, name: undefined, done }
}

// AFTER — tight: compiler catches typos and missing keys
interface CreateTodoInput {
  name: string;
  priority?: "low" | "medium" | "high";
  dueDate?: Date;
}

interface Todo {
  id: string;
  name: string;
  priority: "low" | "medium" | "high";
  dueDate: Date | null;
  done: boolean;
}

function createTodo(data: CreateTodoInput): Todo {
  return {
    id: crypto.randomUUID(),
    name: data.nam,  // TS Error: Property 'nam' does not exist on type 'CreateTodoInput'
    priority: data.priority ?? "medium",
    dueDate: data.dueDate ?? null,
    done: false,
  };
}
```

##### Record<K, V> for keyed maps

```typescript
// BEFORE — loose
const agentPool: { [key: string]: any } = {};

// AFTER — tight: keys are agent IDs, values are AgentStatus objects
interface AgentStatus {
  model: string;
  busy: boolean;
  startedAt: Date;
}

const agentPool: Record<string, AgentStatus> = {};

function getAgentStatus(agentId: string): AgentStatus | undefined {
  return agentPool[agentId];
}
```

##### Typed handler vs. Function

```typescript
// BEFORE — loose
function onEvent(event: string, handler: Function): void { ... }

// AFTER — tight: handler signature is enforced at call site
interface DispatchEvent {
  type: "dispatch";
  agentId: string;
  taskId: string;
}

type EventHandler<E> = (event: E) => void;

function onEvent<E>(eventType: string, handler: EventHandler<E>): void { ... }

onEvent<DispatchEvent>("dispatch", (event) => {
  console.log(event.agentId);  // TS knows event.agentId is string
});
```

##### Constrained generic vs. bare `<T>`

```typescript
// BEFORE — loose: T could be anything, so you can't access any fields on it
function getById<T>(id: string): T { ... }

// AFTER — tight: T extends HasId, so you can safely access .id
interface HasId {
  id: string;
}

function getById<T extends HasId>(id: string): T { ... }
```

##### Discriminated union vs. any-bag

```typescript
// BEFORE — loose: consumer must do runtime checks on every field
type Message = Record<string, any>;

// AFTER — tight: the discriminant "kind" narrows the type automatically
interface TextMessage {
  kind: "text";
  body: string;
}
interface ImageMessage {
  kind: "image";
  url: string;
  width: number;
  height: number;
}
interface FileMessage {
  kind: "file";
  filename: string;
  mimeType: string;
  sizeBytes: number;
}

type Message = TextMessage | ImageMessage | FileMessage;

function handleMessage(msg: Message): void {
  switch (msg.kind) {
    case "text":
      console.log(msg.body.toUpperCase());  // TS knows msg.body exists
      break;
    case "image":
      console.log(`${msg.url} (${msg.width}x${msg.height})`);
      break;
    case "file":
      console.log(`${msg.filename} (${msg.sizeBytes} bytes)`);
      break;
    // No default needed — exhaustiveness is checked. Add a new variant
    // to Message and this function gets a compile error until you handle it.
  }
}
```

##### unknown + type guard vs. any

```typescript
// BEFORE — loose: any infects every consumer; no narrowing required
function parse(raw: any): any {
  return JSON.parse(raw);
}

// AFTER — tight: unknown forces narrowing before use
function parse(raw: string): unknown {
  return JSON.parse(raw);
}

// Consumer must narrow:
function isTodo(data: unknown): data is Todo {
  return (
    typeof data === "object" &&
    data !== null &&
    "id" in data &&
    "name" in data &&
    typeof (data as Record<string, unknown>).id === "string"
  );
}

const data = parse('{"id": "1", "name": "Buy milk"}');
if (isTodo(data)) {
  console.log(data.name.toUpperCase());  // TS knows data is Todo here
}
// data is still unknown outside the guard — cannot accidentally use it
```

---

## Anti-patterns (mechanically flagged in Python)

These are the regressions `make check-types` catches. Each one replaces a
checkable contract with a hole.

| Anti-pattern | Why it's bad | Fix |
|---|---|---|
| `-> Any` | Caller must read the body to know what comes back | Return the concrete type; if multiple, a `Union`/`Result` |
| `dict[str, Any]` | Keys and values untyped; typos invisible | `TypedDict` or `BaseModel` |
| `list[Any]` / `tuple[Any, ...]` | Element type unknown | `list[T]` / `tuple[A, B, C]` |
| `Optional[Any]` | Equivalent to `Any` — `None` info adds nothing | The concrete optional `T \| None`, or just `T` |
| `Callable[..., Any]` | Args and return both holes | `Protocol` or full `Callable[[...], R]` |
| `Any` as a parameter | Every caller is untype-checked | Name the parameter's type or `Protocol` |
| `typing.Any` in a `cast(...)` | `cast(Any, x)` is a no-op cast | Cast to the real target type, or remove the cast |

### Anti-pattern code examples — wrong way vs. right way

#### AP-1: `-> Any`

```python
# WRONG — caller gets no type info
def fetch_workflows() -> Any:
    return db.query(Workflow).all()

# RIGHT — return the concrete iterable type
from typing import Sequence

def fetch_workflows() -> Sequence[Workflow]:
    return db.query(Workflow).all()
```

#### AP-2: `dict[str, Any]`

```python
# WRONG — keys untyped, typos at runtime
def build_deploy_spec(env: str) -> dict[str, Any]:
    return {
        "verison": "1.2.3",  # "verison" not "version" — silent bug
        "environmnet": env,   # "environmnet" not "environment"
    }

# RIGHT — TypedDict catches every typo
class DeploySpec(TypedDict):
    version: str
    environment: str
    replicas: int

def build_deploy_spec(env: str) -> DeploySpec:
    return {
        "verison": "1.2.3",  # mypy: Extra key "verison" for TypedDict "DeploySpec"
        "environment": env,
        "replicas": 3,
    }
```

#### AP-3: `list[Any]` / `tuple[Any, ...]`

```python
# WRONG — element type is a mystery
def get_agent_ids() -> list[Any]:
    return [row[0] for row in db.execute("SELECT id FROM agents")]

# RIGHT — declare what the list contains
def get_agent_ids() -> list[str]:
    result = db.execute("SELECT id FROM agents")
    return [str(row[0]) for row in result]
```

#### AP-4: `Optional[Any]`

```python
# WRONG — Optional[Any] ≡ Any, the None info is noise
def maybe_result() -> Optional[Any]:
    if random.random() > 0.5:
        return {"status": "ok"}
    return None

# RIGHT — name the type; None has clear semantics
def maybe_result() -> dict[str, str] | None:
    if random.random() > 0.5:
        return {"status": "ok"}
    return None
```

#### AP-5: `Callable[..., Any]`

```python
# WRONG — no signature information
def register_hook(name: str, fn: Callable[..., Any]) -> None:
    hooks[name] = fn

# RIGHT — Protocol documents the contract
class HookFn(Protocol):
    def __call__(self, context: HookContext, /) -> HookResult: ...

def register_hook(name: str, fn: HookFn) -> None:
    hooks[name] = fn
```

#### AP-6: `Any` as parameter

```python
# WRONG — all callers untyped
def schedule(job: Any, at: Any) -> Any: ...

# RIGHT — name the types
from datetime import datetime

class Job(Protocol):
    def run(self) -> None: ...

def schedule(job: Job, at: datetime) -> str: ...
```

#### AP-7: `cast(Any, x)` — no-op cast

```python
# WRONG — cast(Any, ...) does nothing, just silences mypy
from typing import Any, cast

result = cast(Any, some_typed_value)
result.whatever_i_want  # no error — Any infects everything downstream

# RIGHT — cast to the real type (or remove the cast)
from typing import cast

result = cast(MyConcreteType, some_typed_value)
# OR: just don't cast — fix the type mismatch instead
```

#### AP-8: `Any` in generic parameters

```python
# WRONG — the generic parameter is unconstrained
_cache: dict[str, Any] = {}

def get_cache(key: str) -> Any: ...

# RIGHT — use a bounded generic or Protocol
from typing import Protocol

class Cacheable(Protocol):
    cache_key: str
    expires_at: float

T = TypeVar("T", bound=Cacheable)

_cache: dict[str, Cacheable] = {}

def get_cache(key: str) -> Cacheable | None:
    return _cache.get(key)
```

### Legitimate `Any` usage

`Any` is acceptable **only** in these narrow cases:

1. **C-extension / third-party library without types.** The upstream has no stubs
   and you cannot add one. Use `Any` only at the import boundary, then immediately
   wrap with a typed facade:

   ```python
   from untyped_lib import raw_query  # type: ignore[import-untyped]

   def typed_query(sql: str) -> list[dict[str, object]]:
       result: Any = raw_query(sql)  # Any only at boundary
       assert isinstance(result, list)
       return result
   ```

2. **Genuinely dynamic dispatcher** where keys are unknown at type-check time:

   ```python
   _handlers: dict[str, Callable[[JsonObject], JsonObject]] = {}

   def dispatch(action: str, payload: JsonObject) -> JsonObject:
       handler = _handlers.get(action)
       if handler is None:
           raise ValueError(f"Unknown action: {action}")
       return handler(payload)
   ```

   Here `action` is a free-text string from an external source — but the
   handler return type and payload type are still tight.

3. **Never legitimate:** `TypeVar` bound by `Any`. Use `object` or a real bound.

---

## How to identify the correct type — tracing workflow

When you reach for `Any`, you have stopped doing the work. The type is
discoverable; trace it. Each step below includes a concrete example.

### Step 1: Read the producer

What function/object/value produces this? Its declared return type / field
type IS the type. Copy it verbatim.

```python
# You are writing:
def handle_dispatch(result: Any) -> None:
    agent_id = result.agent_id  # what type is agent_id?
    ...

# Step 1: find the producer
# In src/general_ludd/dispatcher.py:
def dispatch(task: Task) -> DispatchResult:  # <-- THIS is the type
    ...

# Fix: import and use the producer's return type
from general_ludd.dispatcher import DispatchResult, dispatch

def handle_dispatch(result: DispatchResult) -> None:
    agent_id = result.agent_id  # mypy now knows agent_id: str
    ...
```

### Step 2: Read the consumer

What does the caller do with it? Each access narrows the possible type.

```python
# You have a value `msg` of unknown type.
# Step 2: read ALL consumers:
def process(msg: Any) -> None:
    task_id = msg.get("task_id")           # consumer 1: dict with string keys
    agent_name = msg.get("agent_name")     # consumer 2: more string keys
    actions = msg.get("actions", [])       # consumer 3: key "actions" with list value
    for action in actions:                 # consumer 4: iterates over list
        _run(action.get("name"))           # consumer 5: each element is a dict with "name"

# Fix: synthesize the type from what consumers actually require
class ActionItem(TypedDict):
    name: str
    params: NotRequired[dict[str, str]]

class DispatchMessage(TypedDict):
    task_id: str
    agent_name: str
    actions: NotRequired[list[ActionItem]]

def process(msg: DispatchMessage) -> None:
    task_id = msg.get("task_id")
    ...
```

### Step 3: Read the schema

If the value crosses a boundary (JSON, DB row, API response), there IS a schema.
Do not re-derive the type ad hoc at each call site.

```python
# You are writing:
def handle_webhook(body: Any) -> None: ...

# Step 3: find the schema — it's in the Pydantic model
# In src/general_ludd/webhooks/models.py:
class GitHubPushEvent(BaseModel):
    ref: str
    before: str
    after: str
    repository: RepositoryInfo
    commits: list[CommitInfo]

# Fix: import the existing model — do not re-define the shape
from general_ludd.webhooks.models import GitHubPushEvent

def handle_webhook(body: GitHubPushEvent) -> None:
    branch = body.ref.removeprefix("refs/heads/")
    ...
```

### Step 4: Read the test

Tests construct the value — they tell you the literal shape.

```python
# In tests/unit/test_worktree_monitor.py:
def test_monitor_detects_abandoned_worktree():
    # The test constructs a value — it IS the shape specification
    worktree_info = {
        "path": "/tmp/gludd-worktrees/agent-fix-slurm",
        "branch": "agent-fix-slurm",
        "last_commit_date": "2024-01-15T10:00:00Z",
        "commits_behind_master": 12,
    }
    result = monitor.check_abandoned(worktree_info)
    assert result.is_abandoned is True

# The test says the type is:
class WorktreeInfo(TypedDict):
    path: str
    branch: str
    last_commit_date: str
    commits_behind_master: int

# Fix: extract the TypedDict from the test fixture.
```

### Step 5: Union the variants

If multiple producers feed this site, take the union — do not collapse into `Any`.

```python
# BEFORE — loose: Any hides the two distinct shapes
def handle_result(result: Any) -> None:
    if "error" in result:
        log_error(result["error"])
    else:
        save(result["data"])

# AFTER — tight: discriminated union on "status" field
class SuccessResult(TypedDict):
    status: Literal["ok"]
    data: dict[str, object]

class ErrorResult(TypedDict):
    status: Literal["error"]
    error: str
    code: int

DispatchResult = SuccessResult | ErrorResult

def handle_result(result: DispatchResult) -> None:
    if result["status"] == "error":
        log_error(result["error"])
        # mypy knows result has "error" and "code" here
    else:
        save(result["data"])
        # mypy knows result has "data" here
```

### Step 6: The `object` fallback (last resort)

If, and only if, the type is truly unknowable, use `object` and require
the consumer to narrow.

```python
# Type is truly unknowable — user-supplied plugin returns opaque blob
def invoke_plugin(plugin_name: str, input_data: bytes) -> object:
    plugin = _load(plugin_name)
    return plugin.run(input_data)  # plugin is third-party, no type info

# Consumer must narrow:
def handle_plugin_result(raw: object) -> None:
    if isinstance(raw, dict):
        # raw is dict[Any, Any] here — still loose, but scoped
        _handle_dict_result(raw)
    elif isinstance(raw, str):
        _handle_text_result(raw)
    elif raw is None:
        _handle_empty_result()
    else:
        raise TypeError(f"Plugin returned unexpected type: {type(raw)}")
```

---

## Real-world worked example

### Starting point — the loose function

```python
# src/general_ludd/review/evidence_checker.py

def check_claim(claim: Any, context: Any) -> Any:
    """
    Check whether a claim made by an agent is supported by evidence.
    """
    if claim.get("type") == "commit":
        sha = claim.get("sha")
        if sha:
            return _verify_commit(sha, context)
    elif claim.get("type") == "test":
        count = claim.get("count")
        if count:
            return _verify_tests(count, context)
    return {"status": "unverified", "reason": "No verifiable claim"}
```

### Tracing step-by-step

**Step 1 — Producer:** The claim comes from `review_loop.py:extract_claims()`.
Read it:

```python
# src/general_ludd/review/review_loop.py
def extract_claims(response_text: str) -> list[dict[str, object]]:
    # parses agent response, returns list of claim dicts
```

So the producer type is `list[dict[str, object]]` — each element is
`dict[str, object]`. Not great but it's the boundary type.

**Step 2 — Consumer:** The function does:
- `claim.get("type")` → str
- `claim.get("sha")` → str | None
- `claim.get("count")` → int | None
- `context` passed to `_verify_commit(sha, context)` — what does `_verify_commit` need?

Read `_verify_commit`:

```python
def _verify_commit(sha: str, ctx: Any) -> dict[str, object]:
    repo = ctx.get("repo_path")  # str
    branch = ctx.get("branch")   # str
```

So `context` needs `repo_path: str` and `branch: str`.

**Step 3 — Schema:** There's an existing model from `_verify_commit`'s
callers — `ReviewContext` in `db/models.py`:

```python
class ReviewContext(BaseModel):
    repo_path: str
    branch: str
    session_id: str
```

**Step 4 — Tests:** `test_evidence_checker.py` constructs claims like:

```python
claim = {"type": "commit", "sha": "abc123", "repo": "gludd"}
```

So the claim type has `type`, `sha`, `repo` fields. But wait — the test
uses `repo` while the consumer uses `sha`. Without a TypedDict, this
inconsistency is invisible.

**Step 5 — Union:** Two claim types exist: commit-claim and test-claim.
Union them.

### Final tightened version

```python
# src/general_ludd/review/evidence_checker.py

from typing import Literal, TypedDict, NotRequired, assert_never
from general_ludd.db.models import ReviewContext

class CommitClaim(TypedDict):
    type: Literal["commit"]
    sha: str

class TestClaim(TypedDict):
    type: Literal["test"]
    count: int
    test_file: NotRequired[str]

Claim = CommitClaim | TestClaim

class VerificationResult(TypedDict):
    status: Literal["verified", "unverified"]
    evidence: NotRequired[str]
    reason: NotRequired[str]


def check_claim(claim: Claim, context: ReviewContext) -> VerificationResult:
    if claim["type"] == "commit":
        return _verify_commit(claim["sha"], context)
    elif claim["type"] == "test":
        return _verify_tests(claim["count"], context)
    else:
        assert_never(claim)  # exhaustiveness check — new claim type = compile error


def _verify_commit(sha: str, ctx: ReviewContext) -> VerificationResult:
    repo = ctx.repo_path   # typed attribute access, not .get("repo_path")
    branch = ctx.branch    # no more dict-lookup-on-untyped
    ...

def _verify_tests(count: int, ctx: ReviewContext) -> VerificationResult:
    ...
```

**Before/after comparison:**

| Dimension | Before | After |
|---|---|---|
| Claim shape | `Any` — silent typo on `claim.get("sha")` | `CommitClaim \| TestClaim` — mypy catches `claim["shaa"]` |
| Context | `Any` — `.get()` with no field awareness | `ReviewContext` — attribute access, field names checked |
| Return type | `Any` — caller gets no info | `VerificationResult` — caller knows keys and types |
| New claim type | Silent fall-through to "unverified" | `assert_never` — compile error forces handler |

---

## pytest fixtures for type-checking tests

### Fixture: validate function has no `Any` in signature

```python
# tests/unit/test_type_strictness.py

import typing
import inspect
import pytest
from general_ludd.review.evidence_checker import check_claim


def _has_any_in_annotation(annotation: object) -> bool:
    """Recursively check if an annotation tree contains typing.Any."""
    if annotation is typing.Any:
        return True
    origin = typing.get_origin(annotation)
    if origin is not None:
        args = typing.get_args(annotation)
        return any(_has_any_in_annotation(a) for a in args)
    return False


def test_check_claim_has_no_any_in_signature() -> None:
    """check_claim must not use Any in its parameter or return annotations."""
    hints = typing.get_type_hints(check_claim)
    for param_name, annotation in hints.items():
        assert not _has_any_in_annotation(annotation), (
            f"check_claim parameter '{param_name}' has Any in annotation: {annotation}"
        )


@pytest.mark.parametrize("module_path,func_name", [
    ("general_ludd.dispatcher", "dispatch"),
    ("general_ludd.review.evidence_checker", "check_claim"),
    ("general_ludd.git_automation.locking", "acquire_lock"),
])
def test_module_functions_are_tight(module_path: str, func_name: str) -> None:
    """Every exported function must not use Any in its signature."""
    mod = __import__(module_path, fromlist=[func_name])
    func = getattr(mod, func_name)
    hints = typing.get_type_hints(func)
    for param_name, annotation in hints.items():
        assert not _has_any_in_annotation(annotation), (
            f"{module_path}.{func_name}: '{param_name}' contains Any"
        )
```

### Fixture: validate all TypedDict fields are concrete

```python
def _all_fields_concrete(td: type) -> list[str]:
    """Return list of field names that are typed as Any."""
    bad: list[str] = []
    for field_name, field_type in td.__annotations__.items():
        if _has_any_in_annotation(field_type):
            bad.append(field_name)
    return bad


def test_all_typeddicts_have_concrete_fields() -> None:
    """No TypedDict in the codebase may have an Any-typed field."""
    from general_ludd.review.evidence_checker import CommitClaim, VerificationResult
    from general_ludd.dispatcher import DispatchRequest

    for td in [CommitClaim, VerificationResult, DispatchRequest]:
        bad = _all_fields_concrete(td)
        assert not bad, f"{td.__name__} has Any-typed fields: {bad}"
```

---

## Mechanical enforcement

- **`make check-types`** — scans `src/` for `Any` in any annotation context
  (returns, params, AnnAssign, stringified annotations, nested inside
  `dict[...]`/`Optional[...]`/`Union[...]`). Exits non-zero on any hit.
- **`make check-types BASELINE=config/type_any_baseline.txt`** — same scan,
  but tolerates pre-existing violations listed in the baseline so the gate
  enforces on **new** code only. Add a line `path/to/file.py:LINE` for each
  legacy violation you are not fixing today; the gate fails the moment a new
  one appears.
- **`tests/unit/test_type_strictness.py`** — pins the scanner's behavior
  (detection of every anti-pattern, baseline filtering, exit codes).

Workflow when the gate fails:
1. Run `make check-types` and read the reported file:line.
2. Apply the 6-step tracing workflow above to find the real type.
3. Replace `Any` with that type. If you cannot, use `object` with a narrowing
   pattern — never silence by switching to `Any` elsewhere.
4. Re-run `make check-types`. Commit only when green.

---

## See also

- `scripts/check_type_strictness.py` — the scanner implementation.
- `tests/unit/test_type_strictness.py` — the behavioral spec.
- `docs/architecture.md` — data-flow diagrams that help trace producer →
  consumer types across the daemon/worker boundary.
