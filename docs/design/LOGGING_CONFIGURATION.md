# Advanced Configurable Logging — Design (2026-07-10)

Status: **design-complete, not yet implemented.** Line-anchored to current
`master`; re-confirm with a Read before editing (lines drift). Follows the
`WAVE_C_DESIGNS_2026-07-10.md` format: survey → fix/build spec → tests.

---

## 1. Current state (surveyed)

Logging today is **scattered stdlib `getLogger`, no central config**:

- **~180 modules** do `logger = logging.getLogger(__name__)` at import time
  (e.g. `src/general_ludd/daemon.py:99`, `event_loop/loop.py:74`,
  `ansible/core_runner.py:24`, `worker/gunicorn_conf.py:6`,
  `receiver/router.py:100`). No package does `dictConfig`, `fileConfig`, or
  `structlog` anywhere in `src/` (confirmed: zero matches for `dictConfig` /
  `structlog` / `SysLogHandler` / `logconfig` / `uvicorn.access`).
- **One `basicConfig` call, ever**: `cli.py:1142-1148` (`_cmd_daemon`) —
  `logging.basicConfig(level=..., format="%(asctime)s %(levelname)s %(name)s %(message)s")`,
  then `install_project_log_filter()`. Single global level, single format,
  stdout only, no per-component control, no sinks.
- **`src/general_ludd/logging/project_log.py:1-51`** — the only existing
  "structured context" primitive. `ProjectLogAdapter` prefixes `[project_id]`
  onto the message string (not a structured field); `ProjectLogFilter` stamps
  `record.project_id` via a filter installed once on the **root** logger
  (`install_project_log_filter`, called only from `cli.py:1146-1148`). No
  equivalent for `agent_id` / `task_id` / `correlation_id` exists.
- **`observability/metrics_exporter.py:188-196`** — a second, unrelated
  context helper: `CorrelatedLogAdapter`/`get_correlated_logger` prefixes
  `[trace=... span=...]` into the message string. Not composed with
  `ProjectLogAdapter`; not structured; not wired to any handler.
- **Level control is a single global knob, twice over**:
  - `daemon.py:2334-2336` — `GLUDD_LOG_LEVEL` env overrides `log_level` only
    when the caller left it at the `"info"` default (one value, applies
    everywhere).
  - `daemon.py:2525-2527` — hardcoded ad-hoc per-component override:
    `if log_level == "debug": logging.getLogger("httpx").setLevel(DEBUG); logging.getLogger("httpcore").setLevel(DEBUG)`.
    This is the ONE place per-component level selection already exists in the
    codebase, and it's hand-wired to exactly two third-party loggers.
  - `routers/todos.py:569-576` — `POST /admin/log-level` calls
    `logging.getLogger().setLevel(level_upper)` — **root only**, no
    component/namespace targeting, no sinks, five-way enum validated
    (`DEBUG/INFO/WARNING/ERROR/CRITICAL`).
- **`worker/gunicorn_conf.py:1-26`** — `worker_class`, `workers`, `timeout`,
  `max_requests(_jitter)`, plus `on_reload`/`post_fork`/`pre_exec` hooks that
  each just call `logger.info(...)`. **No `logconfig_dict`**, no access-log
  format control, no bridge from gunicorn's own `gunicorn.error` /
  `gunicorn.access` loggers (or the `uvicorn`/`uvicorn.access` loggers the
  `uvicorn_worker.UvicornWorker` installs, line 8) into anything gludd
  controls.
- **`ansible/core_runner.py`** — `verbosity` (`AnsibleOptions.verbosity`,
  lines 169/241/718-727) is passed straight into ansible-core's own CLIARGS;
  it drives ansible's *internal* console/callback verbosity, **not** Python
  `logging`. The `_EventCollectorCallback` (lines 100-161) only accumulates
  `runner_on_start/ok/failed/skipped/unreachable` + `playbook_on_start/stats`
  into an in-memory `self._events` list returned on `AnsibleResult.events` —
  none of those events are emitted through `logger.*` today, so there is no
  logger namespace an operator could dial up for "ansible trace" logs
  independent of everything else. The module's own `logger` (line 24) is only
  used for a handful of control-flow warnings/errors (network-policy block at
  264-272, timeout at 457-459).
- **`config/user_config.py:143-219`** — `UserConfig(BaseSettings)` is the
  established pattern for a new config surface: `model_config =
  SettingsConfigDict(env_prefix="GLUDD_", env_nested_delimiter="__",
  extra="ignore")`; nested blocks are plain `BaseModel`s assigned as fields
  (e.g. `observability: ObservabilityConfig = ObservabilityConfig()` at
  line 172, `pipeline: PipelineConfigBlock = PipelineConfigBlock()` at 181).
  Env override for scalar/dict/list fields already works two ways: nested
  delimiter (`GLUDD_PIPELINE__ENABLED=true`) for `BaseModel` sub-fields, and
  whole-field JSON (`GLUDD_AGENTS='{"timeout": 99}'`, `from_yaml` lines
  238-247) for dict/list fields. This is the pattern `LoggingConfig` reuses.
- **`schemas/todo.py:113-162`** and **`agents/types.py:42-49`** — the
  identifiers that must become log-record fields already exist and are named
  consistently: `Todo.todo_id` / `Todo.project_id` / `Todo.assigned_agent`
  (todo.py:114,117,135), and `AgentTask.task_id` / `AgentTask.agent_name` /
  `AgentTask.project_id` (types.py:44-49) — the dataclass the
  `AgentDispatcher` (`agents/dispatcher.py:28-35,45-72`) actually carries
  through `dispatch_one`. There is **no existing `contextvars` usage anywhere**
  in `src/` (zero matches) — today nothing threads `task_id`/`agent_name`
  into a log record; `ProjectLogFilter` only carries `project_id`, and only
  via one filter instance installed on the root logger.
- **`tests/conftest.py:140-197`** — an autouse fixture snapshots **every**
  logger in `logging.Logger.manager.loggerDict` (level, propagate, handlers,
  disabled) plus the root logger and `logging.Logger.manager.disable`, and
  restores all of it after each test, resetting loggers created mid-test to
  defaults. Any `dictConfig` call made during a test is fully undone by this
  fixture — the new logging config is test-isolation-safe by construction,
  no additional teardown needed in the tests this design adds.
- **`receiver/router.py:1-60`** — a **complementary but opposite-direction**
  feature: gludd already *ingests* OTLP/GELF/Fluent/Beats logs pushed IN by
  other systems (`/v1/logs`, `/ingest/gelf`, etc.), buffered for admin
  inspection. That is unrelated to this design, which is about routing
  gludd's *own* emitted logs *out*. No code overlap; flagged so an
  implementer doesn't conflate the two.
- **Security precedent that constrains "pluggable handler" (§5.6 below)**:
  `connectors/registry.py:400-456` (`_check_module_allowlist`,
  `_import_dotted`) and `tests/unit/test_d30_importlib_allowlist.py` — D-30
  established that `importlib.import_module` on an **operator-config-supplied
  string** must be allowlist-gated (module must start with a fixed package
  prefix AND be a member of a `frozenset` of known-good submodules), never a
  blind import, because a hostile config value like `"os"` is otherwise
  arbitrary code execution at config-load time. The custom-handler
  dotted-path requirement in this design must reuse that exact posture.

---

## 2. Requirements recap (verbatim intent)

1. Format choice per sink: JSON / syslog / plain text — selectable.
2. Granularity: per-AGENT log files **and** per-TASK log files, not just one
   global log.
3. Verbosity per COMPONENT, independently (e.g. "verbose gunicorn + simple
   agent logs", or "verbose HTTP access + ansible trace, nothing else").
4. Routing/sinks: multiple sinks, each with its own format + level +
   component filter (e.g. "verbose JSON → sink A, simple syslog → sink B").
5. Pluggability: arbitrary handler/destination via config, no code changes,
   for "any log solution" (Datadog/Loki/Fluent/Splunk/etc.).

---

## 3. Schema — `LoggingConfig`

New module `src/general_ludd/config/logging_config.py` (imported by
`config/user_config.py`, mirroring how `ModelRoutingConfig` is imported at
`user_config.py:10`). Add one field to `UserConfig`:

```python
# user_config.py — new import + field
from general_ludd.config.logging_config import LoggingConfig
...
class UserConfig(BaseSettings):
    ...
    logging: LoggingConfig = LoggingConfig()
```

Default `LoggingConfig()` compiles to **exactly today's behavior** (one
stdout text sink at INFO, root logger only, `project_id` filter installed) —
opting into the new surface never changes default operation, matching the
"fix ≠ disable" / backward-compat posture the rest of `UserConfig` already
follows (e.g. `pipeline.enabled: bool = False` at `user_config.py:26`).

```python
# config/logging_config.py
from __future__ import annotations
from pydantic import BaseModel, Field

class LoggerFilterConfig(BaseModel):
    """Include/exclude a sink's traffic by logger-name namespace (prefix match)."""
    include: list[str] = Field(default_factory=list)   # e.g. ["general_ludd.ansible"]
    exclude: list[str] = Field(default_factory=list)   # e.g. ["general_ludd.ansible.trace"]

class SinkConfig(BaseModel):
    name: str                                    # unique id, used as dictConfig handler key
    destination: str = "stdout"                  # stdout|stderr|file|syslog|journald|http_otlp|custom
    format: str = "text"                         # text|json|syslog|custom
    level: str = "INFO"
    filter: LoggerFilterConfig = LoggerFilterConfig()
    propagate: bool = False                      # False = this sink's loggers stop here (no double-log to root)
    # destination=file
    file_path: str | None = None                 # may contain {agent_id}/{task_id}/{project_id} — see §4
    # destination=syslog
    syslog_address: str = "/dev/log"              # or "host:port"
    syslog_facility: str = "user"
    # destination=http_otlp
    otlp_endpoint: str | None = None
    # destination=custom — dotted "module.path:ClassName", see §5.6 for the allowlist gate
    handler_class: str | None = None
    handler_kwargs: dict[str, Any] = Field(default_factory=dict)
    # custom formatter, same allowlist gate as handler_class
    formatter_class: str | None = None

class PerScopeFileRouting(BaseModel):
    enabled: bool = False
    path_template: str = "logs/agents/{agent_id}.log"   # or logs/tasks/{task_id}.log
    level: str = "INFO"
    format: str = "text"

class LoggingConfig(BaseModel):
    enabled: bool = True
    # Per-component level overrides, independent of each other. Key = logger
    # namespace (dotted, prefix match via dictConfig's own logger tree —
    # "general_ludd.ansible" also governs "general_ludd.ansible.trace" unless
    # the child has its own explicit entry). Examples:
    #   {"gunicorn.access": "DEBUG", "general_ludd.agents": "INFO"}
    #   {"uvicorn.access": "DEBUG", "general_ludd.ansible.trace": "DEBUG"}
    component_levels: dict[str, str] = Field(default_factory=dict)
    sinks: list[SinkConfig] = Field(
        default_factory=lambda: [SinkConfig(name="default_stdout")]
    )
    per_agent_files: PerScopeFileRouting = PerScopeFileRouting(
        path_template="logs/agents/{agent_id}.log"
    )
    per_task_files: PerScopeFileRouting = PerScopeFileRouting(
        path_template="logs/tasks/{task_id}.log"
    )
    # Security gate for SinkConfig.destination == "custom" / formatter_class —
    # see §5.6. Off by default (fail-closed), same posture as
    # GLUDD_ALLOW_NO_AUTH at daemon.py:2400-2416.
    allow_custom_handlers: bool = False
    trusted_handler_modules: list[str] = Field(default_factory=list)
```

Env overrides (pydantic-settings `env_prefix="GLUDD_"`,
`env_nested_delimiter="__"`, matching `user_config.py:157-161`):

```text
GLUDD_LOGGING__ENABLED=true
GLUDD_LOGGING__COMPONENT_LEVELS='{"gunicorn.access":"DEBUG","general_ludd.agents":"INFO"}'
GLUDD_LOGGING__SINKS='[{"name":"json_out","destination":"file","file_path":"logs/gludd.json.log","format":"json","level":"INFO"},{"name":"syslog_out","destination":"syslog","format":"syslog","level":"WARNING"}]'
GLUDD_LOGGING__PER_AGENT_FILES__ENABLED=true
GLUDD_LOGGING__ALLOW_CUSTOM_HANDLERS=true
```
(List/dict-valued fields take whole-field JSON exactly like the existing
`GLUDD_AGENTS='{"timeout": 99}'` convention at `user_config.py:147`; scalar
sub-fields of a `BaseModel` block take the `__` nested-delimiter form exactly
like `GLUDD_PIPELINE__ENABLED=true`.)

---

## 4. Structured context: agent_id / task_id / project_id / correlation_id

No `contextvars` exist today (§1). Add
`src/general_ludd/logging/run_context.py`:

```python
import contextvars, uuid
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_id", default=None)
_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("project_id", default=None)
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

class RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = _task_id.get()
        record.agent_id = _agent_id.get()
        record.project_id = _project_id.get()
        record.correlation_id = _correlation_id.get() or "-"
        record.component = record.name
        return True

class run_context(contextlib.AbstractContextManager):
    """Bind task_id/agent_id/project_id/correlation_id for the duration of a
    `with` block; restores prior values (nested-safe) on exit."""
    def __init__(self, *, task_id=None, agent_id=None, project_id=None, correlation_id=None): ...
    def __enter__(self): ...   # contextvars.Token per var, only set() for non-None kwargs
    def __exit__(self, *exc): ...  # reset() each token
```

This **replaces** `ProjectLogFilter`/`ProjectLogAdapter`
(`logging/project_log.py`) as the source of `record.project_id` — keep the
old module for the deprecation window (still imported by `cli.py:1146`) but
have the compiler (§5) install `RunContextFilter` instead so `project_id`
comes from the same contextvar as `agent_id`/`task_id`, not a filter that
only ever supported one value process-wide.

**Bind points** (each wraps the existing call in `with run_context(...):`):
- `agents/dispatcher.py` — `dispatch_one`/`_run_task` (wherever it awaits
  `self._executor(task)`, near `dispatcher.py:88-` `_get_semaphore` /
  the dispatch loop): `with run_context(task_id=task.task_id, agent_id=task.agent_name, project_id=task.project_id):`.
  Every log line emitted by the executor coroutine and anything it calls
  (model gateway, tool loop, ansible runner) picks up the same three IDs —
  contextvars propagate through `await` inside the same task, which is
  exactly the boundary `dispatch_one` owns.
- `event_loop/loop.py` — the per-tick per-todo dispatch site inside
  `dispatch_execute_jobs` (one of the `PHASE_ORDER` phases,
  `loop.py:88-100`): bind `task_id=todo.todo_id, project_id=todo.project_id`
  before invoking the executor for that todo, for logging emitted directly
  from the tick loop (outside the dispatcher's own task).
- `execution/tool_loop.py` / `execution/engine.py` — inherit the ambient
  context from the enclosing `dispatch_one` call; no separate bind needed
  unless a tool spawns its own asyncio Task (contextvars do **not** cross
  `asyncio.create_task` boundaries automatically in all cases — copy the
  current `contextvars.Context` via `asyncio.get_running_loop().create_task(coro, context=contextvars.copy_context())`. Flag this at any
  `create_task`/`asyncio.gather` call inside the dispatch path as a
  follow-up integration check.
- A fresh `correlation_id` (`uuid.uuid4().hex[:16]`) is generated once per
  HTTP request in the existing `auth_and_stats_middleware`
  (`daemon.py:2468-2523`) and bound via `run_context(correlation_id=...)` for
  the request's duration, so every log line touched by one API call — across
  routers, dispatcher, ansible — shares one id (this subsumes
  `metrics_exporter.CorrelatedLogAdapter`'s per-thread trace id at
  `metrics_exporter.py:173-196`, which can be retired in favor of this one
  mechanism).

---

## 5. dictConfig compiler

New module `src/general_ludd/observability/log_config.py`:

```python
def build_dict_config(cfg: LoggingConfig) -> dict[str, Any]: ...
def install_logging(cfg: LoggingConfig) -> None:
    logging.config.dictConfig(build_dict_config(cfg))
```

### 5.1 Formatters
- `text`: `"%(asctime)s %(levelname)s %(name)s [task=%(task_id)s agent=%(agent_id)s project=%(project_id)s] %(message)s"`
  (superset of today's `cli.py:1143` format string — same fields, plus the
  new context fields, defaulting to `None`/`-` so unbound records still
  format cleanly).
- `json`: a small stdlib-only `logging.Formatter` subclass
  (`observability/log_config.py:JsonFormatter`) emitting one JSON object per
  line with `timestamp, level, component (record.name), message, task_id,
  agent_id, project_id, correlation_id` plus `exc_info` when present. No new
  dependency (repo has no `python-json-logger`/`structlog`, confirmed §1) —
  `json.dumps(..., default=str)` over a fixed field dict.
- `syslog`: RFC-3164-ish short format
  `"%(name)s: %(levelname)s %(message)s"` (the `SysLogHandler` itself adds
  the actual syslog header) — a separate formatter, not the file/stdout text
  one, since syslog daemons re-add timestamp/host.
- `custom`: `formatter_class` dotted path, same allowlist gate as §5.6.

### 5.2 Filters
- `run_context` → `RunContextFilter` (§4), installed on **every** handler
  (attached in `'filters'` for each handler entry) so every sink sees the
  context fields regardless of which logger emitted the record.
- Per-sink `LoggerFilterConfig` (include/exclude) compiles to a
  `logging.Filter` subclass that checks `record.name` against the
  include/exclude prefix lists (empty `include` = allow-all, matching
  today's "one sink gets everything" default).

### 5.3 Handlers — one per `SinkConfig`
| `destination` | dictConfig `class` |
|---|---|
| `stdout`/`stderr` | `logging.StreamHandler` (`stream=ext://sys.stdout`/`stderr`) |
| `file` | `logging.handlers.RotatingFileHandler` (or `PerScopeFileHandler`, §5.4, when the sink is the per-agent/per-task auto-router) |
| `syslog` | `logging.handlers.SysLogHandler` (`address` = `syslog_address`, split `host:port` → tuple, else treat as a Unix socket path; `facility` from `syslog_facility`) |
| `journald` | `logging.handlers.SysLogHandler` pointed at `/dev/log` (journald consumes syslog datagrams) — no new dependency required for the common case; a `systemd.journal.JournalHandler` (via `handler_class`, §5.6) is the opt-in richer alternative |
| `http_otlp` | `logging.handlers.HTTPHandler` for simple HTTP-POST sinks, or a `handler_class` OTLP log exporter (§5.6) for real OTLP — plain `HTTPHandler` is the zero-dependency default, matching the "no new deps unless the operator opts in" posture |
| `custom` | `handler_class` dotted path + `handler_kwargs`, gated by §5.6 |

### 5.4 Per-agent / per-task file routing — the dynamic-destination problem

`dictConfig` handlers are **static**: one handler instance per config entry,
built once at startup. But per-agent/per-task routing needs a *new* file per
distinct `agent_id`/`task_id` value, unknown at config-compile time. Solution:
compile `per_agent_files`/`per_task_files` to **one** handler instance each —
a custom `PerScopeFileHandler(logging.Handler)` in `log_config.py` that:

1. Takes `path_template` and `scope_attr` (`"agent_id"` or `"task_id"`) at
   construction.
2. On `emit(record)`: reads `getattr(record, scope_attr, None)` (populated by
   `RunContextFilter`, §4 — filters run before handlers per stdlib ordering
   guarantees within a single logger's handler chain, and dictConfig applies
   filters listed in the handler's own `'filters'` key before that handler's
   `emit`). If unset, **drop silently** (a record with no bound `task_id`
   simply isn't routed to the per-task file — it still reaches every other
   sink normally).
3. Resolves `path = path_template.format(**{scope_attr: value, "agent_id":..., "task_id":..., "project_id":...})`,
   sanitizes the resolved value (reject `/`, `..`, null bytes — same
   path-traversal posture as `capability_lattice.py`'s realpath-based checks
   referenced in `WAVE_C_DESIGNS_2026-07-10.md` C-RELOAD) before it becomes a
   filesystem path component.
4. Maintains an internal `dict[str, logging.FileHandler]` cache keyed by the
   resolved path — lazily `mkdir -p` + open on first use, reuse thereafter,
   delegate the actual write to the cached `FileHandler.emit`. (An LRU cap
   with idle-handle close, e.g. 200 open files, is a fast-follow — flag as
   an explicit `TODO` in the module, not required for correctness at typical
   agent/task fan-out.)

This is the *only* handler type that needs new runtime logic; every other
sink type maps directly onto an existing stdlib `logging.handlers.*` class.

### 5.5 Loggers section + propagation

For each key in `component_levels`, emit a `'loggers'` entry:
`{name: {"level": LEVEL, "propagate": True}}` by default (propagation stays
on so the component still reaches the default sinks unless it also has a
dedicated sink). For a logger namespace that is the `include` target of a
sink with `propagate: false` (e.g. "route all `general_ludd.ansible.trace`
records to their own file and nowhere else"), emit
`{"propagate": False, "handlers": [<that sink's handler name>]}` instead —
matching the STEP-3 requirement ("per-component loggers with
`propagate=False` when routed to a dedicated sink").

Root logger (`''` key in dictConfig) gets the handlers for every sink whose
`filter.include` is empty (the "default catch-all" sinks) at the level of
the **lowest** configured sink (dictConfig handlers each still enforce their
own `level`, so root can stay at the min and let each handler filter up).

### 5.6 Pluggable custom handler — allowlist-gated (D-30 posture)

`destination: "custom"` / `formatter_class` are **rejected at compile time**
(`build_dict_config` raises `ValueError`) unless
`LoggingConfig.allow_custom_handlers` is `True` — same fail-closed-by-default
shape as `GLUDD_ALLOW_NO_AUTH` (`daemon.py:2400-2416`). When enabled, the
dotted path must resolve to a module that is **either**:
- under `logging.handlers` / `logging` themselves (the stdlib is inherently
  trusted — `SysLogHandler`, `HTTPHandler`, `QueueHandler`, etc.), **or**
- present verbatim in `LoggingConfig.trusted_handler_modules` — an
  **operator-populated allowlist**, checked with the identical
  hard-reject-before-import shape as `connectors/registry.py:400-430`
  (`_check_module_allowlist`): compute the module portion of
  `"module.path:ClassName"`, raise `ValueError` if it is not in the allowlist,
  **then** `importlib.import_module`. A bare `"datadog_handler.DatadogHandler"`
  string is *never* enough on its own — the operator must additionally list
  `datadog_handler` in `trusted_handler_modules`, so a hostile/mistaken
  `"os.system"` in `handler_class` is rejected before any import happens,
  exactly like D-30 (`tests/unit/test_d30_importlib_allowlist.py`).
- `handler_kwargs` are passed as `**kwargs` to the resolved class — no further
  sanitization beyond what the target handler itself does (documented
  operator responsibility, same as any dictConfig `handler_kwargs` pattern).

---

## 6. Gunicorn / uvicorn.access / ansible-trace wiring

- **`worker/gunicorn_conf.py`**: add
  `logconfig_dict = build_dict_config(load_user_config(...).logging)`
  (gunicorn's Arbiter reads the module-level `logconfig_dict` attribute and
  calls `dictConfig` with it directly — no gunicorn code change needed,
  just populating the attribute this file currently lacks). Because gunicorn
  applies this **in the worker process**, `gunicorn.error`/`gunicorn.access`
  AND (since `worker_class = "uvicorn_worker.UvicornWorker"`, line 8)
  `uvicorn`/`uvicorn.access`/`uvicorn.error` are all just ordinary keys under
  `component_levels` / sink filters — e.g. the user's "verbose gunicorn +
  simple agent" scenario is `component_levels: {"gunicorn.access": "DEBUG",
  "gunicorn.error": "DEBUG", "general_ludd.agents": "INFO"}` with no gunicorn-
  specific code path at all. `gunicorn_conf.py` has no `config_dir` argument
  today (it's a bare module gunicorn imports by path) — resolve config via
  `GLUDD_CONFIG_DIR` env (already read the same way at `daemon.py:2338`) so
  the worker and the daemon process load the identical `UserConfig`.
- **`ansible/core_runner.py`**: give ansible execution its own dedicated
  namespace so it's independently dial-able. Add `logger.debug`/`.info` calls
  inside `_EventCollectorCallback` (lines 100-161) using a **child** logger
  `logging.getLogger(f"{__name__}.trace")` (i.e.
  `general_ludd.ansible.core_runner.trace`, or hoist to a fixed
  `general_ludd.ansible.trace` name for a stable component key):
  `v2_runner_on_start/ok` → DEBUG, `v2_playbook_on_start/stats` → INFO,
  `v2_runner_on_failed/unreachable` → WARNING. This directly satisfies the
  user's "verbose ... ansible trace logs but nothing else" scenario:
  `component_levels: {"general_ludd.ansible.trace": "DEBUG"}` with every
  other namespace left at its sink's default (e.g. WARNING), and a sink whose
  `filter.include = ["general_ludd.ansible.trace"]` and `propagate: false`
  isolates it to its own file/JSON sink per §5.5.
- **`routers/todos.py:569-576`**: extend `/admin/log-level` into
  `POST /admin/logging` accepting a full `LoggingConfig` body (or a partial
  patch merged over the current one), calling `install_logging()` again for
  a live hot-reload — replaces the single `logging.getLogger().setLevel(...)`
  call with the full compiler, backward-compatible by keeping the old
  endpoint as a thin wrapper that sets `component_levels={"": level}` (root)
  and recompiles.

---

## 7. Example configs (user's exact scenarios)

**(a) "verbose gunicorn logs and simple agent logs"**
```yaml
logging:
  component_levels:
    gunicorn.access: DEBUG
    gunicorn.error: DEBUG
    general_ludd.agents: INFO
  sinks:
    - name: default_stdout
      destination: stdout
      format: text
      level: INFO
```

**(b) "verbose HTTP access logs and ansible trace logs but nothing else"**
```yaml
logging:
  component_levels:
    uvicorn.access: DEBUG
    general_ludd.ansible.trace: DEBUG
  sinks:
    - name: http_and_ansible
      destination: file
      file_path: logs/http-ansible.log
      format: text
      level: DEBUG
      filter:
        include: ["uvicorn.access", "general_ludd.ansible.trace"]
      propagate: false
    # everything else stays at the package default (WARNING) and is dropped —
    # no catch-all sink configured, matching "nothing else"
```

**(c) "verbose JSON logs to one sink, simple syslog logs to another"**
```yaml
logging:
  sinks:
    - name: json_sink
      destination: file
      file_path: logs/gludd.json.log
      format: json
      level: DEBUG
    - name: syslog_sink
      destination: syslog
      format: syslog
      level: WARNING
      syslog_address: /dev/log
      syslog_facility: local0
```

**(d) per-agent + per-task files, plus an external shipper**
```yaml
logging:
  per_agent_files:
    enabled: true
    path_template: "logs/agents/{agent_id}.log"
    level: INFO
  per_task_files:
    enabled: true
    path_template: "logs/tasks/{task_id}.log"
    level: DEBUG
  allow_custom_handlers: true
  trusted_handler_modules: ["my_company_log_shipper"]
  sinks:
    - name: default_stdout
      destination: stdout
    - name: loki
      destination: custom
      handler_class: "my_company_log_shipper.LokiHandler"
      handler_kwargs: {url: "https://loki.internal:3100", labels: {app: gludd}}
      format: json
      level: INFO
```

---

## 8. Wiring points (implementation checklist)

1. `config/logging_config.py` — new (schema, §3).
2. `config/user_config.py` — add `logging: LoggingConfig` field + import.
3. `logging/run_context.py` — new (`contextvars` + `RunContextFilter` +
   `run_context` ctx-manager, §4).
4. `observability/log_config.py` — new (`build_dict_config`,
   `install_logging`, `JsonFormatter`, `PerScopeFileHandler`,
   `_import_dotted_handler` allowlist gate, §5).
5. `cli.py:1142-1148` (`_cmd_daemon`) — replace `logging.basicConfig(...)` +
   `install_project_log_filter()` with
   `install_logging(load_user_config(config_dir).logging)`.
6. `worker/gunicorn_conf.py` — add module-level `logconfig_dict` (§6).
7. `agents/dispatcher.py` — bind `run_context(task_id=, agent_id=, project_id=)`
   around the executor call (§4).
8. `event_loop/loop.py` — bind `run_context(task_id=, project_id=)` in the
   `dispatch_execute_jobs` phase (§4).
9. `daemon.py:2468` (`auth_and_stats_middleware`) — bind
   `run_context(correlation_id=uuid4)` per request (§4).
10. `ansible/core_runner.py` — add trace-level `logger.debug/info/warning`
    calls in `_EventCollectorCallback` under a stable
    `general_ludd.ansible.trace` namespace (§6).
11. `routers/todos.py` — extend `/admin/log-level` → `/admin/logging` (§6).

---

## 9. Test plan

- `tests/unit/test_logging_config_schema.py` — `LoggingConfig()` defaults
  compile via `build_dict_config` to a `dictConfig`-valid dict (call
  `logging.config.dictConfig` on it directly, assert no exception); env
  override round-trip (`GLUDD_LOGGING__COMPONENT_LEVELS='{"x":"DEBUG"}'` →
  `load_user_config().logging.component_levels == {"x": "DEBUG"}`).
- `tests/unit/test_log_config_component_levels.py` — two loggers
  (`gunicorn.access`, `general_ludd.agents`) end up at different effective
  levels after `install_logging`; a logger with no explicit entry inherits
  the root/default level.
- `tests/unit/test_log_config_per_task_routing.py` — bind
  `run_context(task_id="T1")`, emit a record on some logger, assert the
  configured per-task file (`logs/tasks/T1.log`) contains the message and a
  DIFFERENT `task_id="T2"` context does not leak into it; unbound context
  (`run_context` never entered) does not create a stray file.
- `tests/unit/test_log_config_multi_sink.py` — two sinks (`json_sink`
  format=json, `syslog_sink` format=syslog) both receive the same log call;
  assert the json sink's captured line is valid JSON with `agent_id`/
  `task_id`/`project_id`/`correlation_id`/`component`/`level`/`timestamp`
  keys, and the syslog sink's `SysLogHandler.emit` was invoked with the
  syslog-formatted (not JSON) string — mock the socket, don't require a real
  syslog daemon.
- `tests/unit/test_log_config_custom_handler.py` — `allow_custom_handlers=False`
  + a `destination: custom` sink → `build_dict_config` raises `ValueError`
  before any import is attempted (patch `importlib.import_module` and assert
  it's never called, mirroring `test_d30_importlib_allowlist.py`'s pattern);
  `allow_custom_handlers=True` with the target module **not** in
  `trusted_handler_modules` → still raises; **in** the allowlist → the
  handler class is imported and installed (use a stub handler class in
  `tests/fixtures/` as the "external" target).
  `handler_class="os.system"`-style disallowed stdlib bypass attempts
  parametrized like `test_d30_importlib_allowlist.py`'s `bad_module` list.
- `tests/unit/test_log_config_filter_include_exclude.py` — a sink with
  `filter.include=["general_ludd.ansible.trace"]` and `propagate: false`
  receives ansible-trace records and does NOT receive e.g.
  `general_ludd.agents` records; an `exclude` entry suppresses a sub-
  namespace while its siblings still pass.
- `tests/unit/test_ansible_trace_logging.py` — `_EventCollectorCallback`
  methods emit to `general_ludd.ansible.trace` at the documented levels
  (patch the logger, assert `.debug`/`.info`/`.warning` called with the
  right event names) — confirms the new namespace exists and is
  independently dial-able without touching `AnsibleResult.events` behavior.
- `tests/unit/test_gunicorn_logconfig.py` — `worker/gunicorn_conf.py` exposes
  a `logconfig_dict` module attribute that is a valid dictConfig dict
  (extends the existing `tests/unit/test_worker.py` import pattern, lines
  223-250, which already does `importlib.import_module("general_ludd.worker.gunicorn_conf")`).
- Compatibility: no new teardown needed — `tests/conftest.py:140-197`'s
  autouse snapshot/restore already covers any logger `install_logging`
  touches; verify with one existing-suite full run
  (`make test-unit TESTFILE=tests/unit/test_logging_config_schema.py`) plus
  `make gate-async` before landing, per `CLAUDE.md`'s canonical loop.
