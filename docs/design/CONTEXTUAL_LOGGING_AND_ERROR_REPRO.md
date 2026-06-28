# Design: Contextual Logging + Error Reproduction (request #13)

Status: **DESIGNED (apply-ready, not yet applied)** — 2026-06-25.
Source: read-only design pass over the whole logging/dispatch/store surface (5 mapping agents).
Owner artifact for todo #13 in `docs/SESSION_WORK_LEDGER.md`.

> Operator request: "ensure gludd logs reference the todo task, model, role, gludd
> runner node, etc that produced the log and provides a mechanism to recreate all
> error behaviors" + addendum: "the contextual logging should also point to pid's
> and container names and any serialized dump of process data saved on disk for
> that event."

## Grounding facts (verified, shape the design)

- **stdlib `logging`, NOT structlog.** structlog is declared in `pyproject.toml:33`
  but has **zero imports**. Mirror the existing pattern in
  `src/general_ludd/logging/project_log.py` (`ProjectLogFilter(logging.Filter)` +
  idempotent `install_project_log_filter()`). Root config:
  `cli.py:736` `logging.basicConfig(...)`; daemon logger `daemon.py:66`.
- **No contextvars today** (only a threading-keyed trace shim in
  `observability/metrics_exporter.py`). **No node_id, no container detection, no
  redactor** exist — all net-new. `secrets/env.py` is a resolver, not a scrubber.
- **Durable store = `audit_events`** via `AuditEventRepository` (`db/repository.py:676`).
  **TRAP: `create()` raises if `project_id is None`** → ErrorEvents must pass a
  project_id (`todo.project_id` or sentinel `"_system"`).
- **Process dump blob → `FileStore.write_text`** (`filestore/store.py`) at
  `errors/<error_id>.json` under `~/.local/share/general-ludd/filestore/`.
- CLI is **argparse**: `build_parser()` at `cli.py:241`, dispatch `args.func(args)`
  at `cli.py:725`.

## (1) Contextual logging

NEW `src/general_ludd/logging/context.py`:
- `@dataclass(frozen=True) LogContext` with fields:
  `todo_id, model, role, node_id, work_type, run_id, pid, ppid, container, dump_path`
  (all default `"-"`).
- `ContextVar[LogContext]` + `bind_log_context(**fields)` contextmanager that merges,
  stamps `pid`/`ppid` from `os.getpid()/getppid()`, fills `node_id`/`container` if unset,
  and `_ctx.reset(token)` on exit (nesting-safe — inner binds inherit outer fields).
- `node_id(configured=None)` → configured else `f"{socket.gethostname()}:{os.getpid()}"` (cached).
- `container_id()` → `HOSTNAME` env / `/.dockerenv` / `/proc/self/cgroup`
  (`docker|kubepods|containerd|libpod`), `"-"` if none (cached).
- `set_dump_path(path)` → late-bind the on-disk dump path into the active context.
- `class ContextLogFilter(logging.Filter)` injects all fields onto **every** LogRecord
  (default `"-"`), so existing logs don't break.
- `install_context_log_filter(logger=None)` attaches the filter to BOTH the logger AND
  its handlers (logger-attached filters do NOT run for records bubbling through ancestor
  handlers — must attach at handler level too).
- `CONTEXT_LOG_FORMAT`:
  `"%(asctime)s %(levelname)s %(name)s [todo=%(todo_id)s model=%(model)s role=%(role)s
  node=%(node_id)s wt=%(work_type)s run=%(run_id)s pid=%(pid)s/%(ppid)s
  ctr=%(container)s dump=%(dump_path)s] %(message)s"`

Wiring (exact insertion points):
- **A. Root** — `cli.py:736`: swap `basicConfig(format=...)` for `format=CONTEXT_LOG_FORMAT`
  then `install_context_log_filter()`. Re-call after any later `basicConfig` (also `daemon.py:66`).
- **B. Per-todo** — `event_loop/loop.py` top of `_dispatch_execute_job` (def ~1150):
  `with bind_log_context(todo_id=todo.todo_id, work_type=<wt>, run_id=f"EXEC-{todo.todo_id}",
  node_id=node_id(self._node_id_config)):` wrap the body. (`EXEC-<id>` is the existing
  job_id → reuse as run/correlation id.)
- **C. Per-model-call** — `models/gateway.py` `call_model` (def ~411) after profile resolves
  (~421): `with bind_log_context(model=f"{profile_id}:{profile.model_name}"):` — todo/run/node
  inherited via contextvar nesting.
- **D. Per-role** — `agents/dispatcher.py` `dispatch_one` after `config = registry.get(...)`:
  `with bind_log_context(role=task.agent_name, run_id=task.task_id):`. Mirror in
  `dispatch/dynamic_dispatcher.py` for tool-call dispatch.
- **node_id config:** add `node_id: str | None = None` to `ObservabilityConfig`
  (`config/user_config.py`), thread onto `EventLoop` as `self._node_id_config`;
  falls back to `hostname:pid` if absent.

## (2) Error reproduction

NEW `src/general_ludd/observability/error_repro.py`:
- `_redact(obj)` — recursive key-substring scrub
  (`key|token|secret|psk|password|passwd|api_base|base_url|authorization|credential|cookie`),
  applied to env, `model_request`, and `todo_snapshot` before ANY serialization.
- `@dataclass ErrorEvent`: `error_id (ERR-<hex12>)`, `created_at`, `stage`
  (`todo_exec|model_call|role_dispatch`), `todo_id`, `project_id`, `run_id`, `work_type`,
  `role`, `model`, `node_id`, `container`, `pid`, `ppid`, `exc_type`, `exc_msg`, `traceback`,
  `rng_seed`, `todo_snapshot`, `model_request`, `dump_path`.
- **`async def capture_error(exc, *, stage, todo=None, model_request=None, rng_seed=None,
  audit_repo=None, project_id=None)`** — best-effort, never raises:
  1. build ErrorEvent from `current_context()` + inputs (todo via `model_dump()` then `_redact`);
  2. write on-disk dump (`asdict(ev)` + `argv`, `cwd`, `_redact_env()`, `rss_bytes` via
     `resource.getrusage`) to filestore `errors/<error_id>.json`; `set_dump_path(abspath)`;
  3. if `audit_repo` present, `await audit_repo.create(event_type="error_occurred",
     entity_type="error", entity_id=error_id, project_id=project_id or "_system", details=...)`.
- Capture sites (all already have `except Exception`):
  - `event_loop/loop.py` model-call guard (`models/job_invocation.py:133-144`) + phase handler
    (`loop.py:568-577`) — has `self._active_session` → `AuditEventRepository(self._active_session)`.
  - `models/gateway.py:566-570` (`except Exception as exc: ... raise`) — **filestore-only**
    (sync, no session) before `raise`.
  - `agents/dispatcher.py:106-118` (`except Exception` after `logger.exception`).
- **`gludd reproduce <error_id> [--apply]`** — register in `cli.py build_parser()` (~249),
  handler `_cmd_reproduce`: load `errors/<id>.json` via FileStore, print context summary;
  dry-run by default (shows redacted `model_request`/`todo_snapshot`); `--apply` rebuilds
  `Todo(**snapshot)` and replays through the same path (model_call → `gateway.call_model`;
  todo_exec → enqueue + one EventLoop tick). Optional `routers/reproduce.py`
  `POST /api/reproduce/{error_id}?apply=false` mirroring `routers/dispatch.py:38`.

## Risks / open items (must address at apply)
1. **`audit_events` requires non-null `project_id`** — pass `"_system"` sentinel outside a
   project, else the DB row silently no-ops. Highest apply risk.
2. **Sync gateway vs async DB** — `gateway.call_model` is synchronous; only write the
   filestore dump there. Do the `audit_events` row at async call sites (loop, dispatcher).
   Make `capture_error` `async def`; from the sync gateway call only the dump path.
3. **No RNG seed today** — sampling flows opaquely through `**kwargs`. True reproducibility
   requires *introducing* a per-todo seed (stash in context + ErrorEvent). Capture is easy;
   provider determinism is not guaranteed.
4. **Filter-on-handlers caveat** — re-call `install_context_log_filter()` after any
   `basicConfig` reconfig (also at `daemon.py:66`).
5. structlog is a dead dependency — RESOLVED to stdlib; do not build on structlog.

## Tests to add
- `test_log_context.py`: bind/unbind nesting, filter injects defaults, container/node helpers.
- `test_error_repro.py`: `_redact` scrubs secret keys in env/request/todo; `capture_error`
  writes the dump + sets dump_path; `project_id` sentinel path; never raises on a broken repo.
- `test_cli_reproduce.py`: dry-run prints summary; `--apply` rebuilds Todo from snapshot.
