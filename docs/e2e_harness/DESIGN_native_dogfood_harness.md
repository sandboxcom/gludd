# Native-Dogfood E2E Harness — Design

Status: DESIGN (read-only investigation done 2026-06-16). No code written.
Author: agent investigation across `daemon.py`, `event_loop/loop.py`, `cli.py`,
`db/models.py`, routers, `models/`, `secrets/`, `scripts/dogfood.py`,
`tests/e2e/`.

---

## 0. Goal

Two native, end-to-end dogfood scenarios driven by the **real** gludd product
spine (project register → enqueue todo → `EventLoop.tick()` dispatch → real
`ExecutionEngine` + git commit → real `ReturnReviewer` → reconcile → COMPLETE),
with the model calls served by a **live z.ai / GLM gateway** when a key is
present, and skipped gracefully when it is not:

1. **Self-host scenario** — gludd makes a *meaningful* change to its OWN repo
   (a behavior change with a test that proves it), via a `file://` clone of this
   repo. This is the existing `make dogfood` flow, upgraded from echo-gateway to
   a real-or-mock gateway and a meaningful task.
2. **Greenfield "todo website" scenario** — gludd initializes a NEW empty
   project, the harness seeds todos describing a "todo task website", gludd works
   the todos to produce site artifacts, and the harness runs **site tests**
   against the generated website (serves pages, todo CRUD works). The temp
   project + DB + workspace are DELETED after the run.

Both reuse the same fixtures and the same secrets loader.

---

## 1. Grounding: how gludd actually runs (verified)

### 1.1 Native start paths
- Console script: `pyproject.toml [project.scripts] gludd = "general_ludd.cli:main"`.
- `gludd daemon --port N` → `cli._cmd_daemon` builds a `subprocess.Popen`
  (gunicorn/uvicorn) and forwards signals. Env it passes through:
  `GLUDD_CONFIG_DIR`, `GLUDD_TEMPLATES_DIR`, `GLUDD_PLAYBOOKS_DIR`,
  `GLUDD_TICK_INTERVAL`, `GLUDD_LOG_LEVEL`, `GLUDD_PSK`.
- The app object is built by `daemon.create_daemon_app(...)` (FastAPI). Startup
  is the `_lifespan` async context manager (full sequence captured in §1.5).
- The `make smoke` target already boots the real daemon on a free port, polls
  `/healthz`, and hits `/api/status` + `/api/todos`. It is the closest existing
  "real HTTP daemon" harness, but lives in the Makefile, not `tests/e2e/`.

### 1.2 The event loop
- `EventLoop.run_forever(interval)` loops `await self.tick(); await sleep(interval)`.
  `stop()` flips `_running`.
- `tick()` opens a DB session (from the session factory), runs `PHASE_ORDER`,
  commits, records `tick_duration_ms`. The phases that matter for us:
  - `claim_runnable_todos` → `TodoRepository.claim_runnable(project_id)`
  - `dispatch_execute_jobs` → `_dispatch_jobs_via_scheduler(claimed)` →
    `_dispatch_execute_job(todo)` (renders prompt, runs playbook via `runner`
    in `asyncio.to_thread`, OR POSTs a `JobSpec` to a worker, OR — in the
    in-process harness — a patched dispatch that calls `ExecutionEngine.execute`)
  - `claim_unreviewed_task_returns` + `dispatch_return_review_jobs` (or inline
    `reviewer.review_return` when a `ReturnReviewer` is wired)
  - `reconcile_completed_decisions` → reads last 50 `TaskDecisionModel`,
    `TodoRepository.transition(todo_id, status, version)`, then
    `_try_commit_completed_work` (git commit + push, deduped by `_pushed_work`)
- Completion = a `TaskDecisionModel(decision="complete")` reconciled +
  version-guarded `transition()` to `COMPLETE`.

### 1.3 How work is created (verified)
- **Project (HTTP):** `POST /admin/projects` — body `AddProjectRequest`
  (`name`, `weight`, `description`, `repo_url`, `workspace_path`,
  `dispatch_mode`). Internally `ProjectManager.add_project(...)` (validates total
  weight ≤ 100, generates `proj-{hex}`) → `materialize_project_workspace(repo_url,
  workspace_path)` (idempotent `git.clone`, SSRF/path guards) → `persist_project`
  upserts `ProjectModel`.
- **Project (programmatic, what the harness uses):**
  `ProjectManager().add_project(name=..., weight=100.0, workspace_path=ws,
  repo_url=...)`. For the **greenfield** scenario the workspace is an empty
  `git init`'d dir (no `repo_url` clone), so gludd builds from scratch.
- **Todo (HTTP):** `POST /api/todos` — body `AddTodoRequest` (`title` 1–512,
  `description` ≤4096, `queue` `^[a-z0-9_\-]+$`, `priority`
  low/medium/high/critical, `work_type` `^[a-z_]+$`, `project_id`). Returns 201
  with `todo_id`. Default status `"queued"`. The harness seeds todos this way.

### 1.4 DB isolation (verified)
- SQLite only. `ensure_tables(engine)` = `Base.metadata.create_all`. Default path
  `$XDG_DATA_HOME/general-ludd/general-ludd.db`, override with `GLUDD_DB_PATH`.
- In-process harness (preferred, see §3) uses
  `create_async_engine("sqlite+aiosqlite://")` (in-memory) + an
  `async_sessionmaker` — exactly what `scripts/dogfood.py` does. Nothing touches
  the developer's real DB.

### 1.5 `_lifespan` startup order (for the optional full-daemon variant)
init engine → ensure_tables → seed queues → subsystems (EventBus, HookSystem,
ProjectManager, ModelRegistry, AdaptiveRouter) → `build_secrets_resolver`
(OpenBao or `EnvSecretsManager`) → `migrate_profile_secrets` → PromptRegistry →
RunBudgetGuard → **ModelGateway(profiles, …, secrets_manager)** → ReturnReviewer
→ SpendLimiter → **EventLoop** → `create_task(event_loop.run_forever(interval))`
→ AgentDispatcher → preflight. Shutdown stops the loop, cancels the task,
`engine.dispose()`.

---

## 2. Grounding: the z.ai / GLM secrets path (verified)

### 2.1 z.ai is already a built-in provider
`src/general_ludd/models/provider_presets.py` `PROVIDER_PRESETS["zai"]`:
- `credential_env_var = "ZAI_API_KEY"`
- `credential_alias   = "zai_api_key"`   (lowercase)
- `api_base_alias     = "zai_api_base"`
- default base URL `https://open.bigmodel.cn/api/paas/v4`, default model
  `glm-5.1`, OpenAI-compatible (`langchain-openai` / `ChatOpenAI`).

### 2.2 Secret resolution (verified) and the case-sensitivity trap
`ModelGateway._invoke_and_bill` resolves the **lowercase** `credential_alias`
(`zai_api_key`) through the secrets manager. `EnvSecretsManager.resolve(alias)`:
1. explicit `_overrides[alias]` (always honored), else
2. `os.environ.get(alias)` **only if** `alias` matches the allowlist
   (`*_API_KEY`, `*_API_BASE`, `*_BASE_URL`, `*_API_URL`, `*_AUTH_TOKEN`,
   `GLUDD_SECRET_*`, case-insensitive regex), else
3. `None`.

**Trap:** the regex is case-insensitive but `os.environ.get` is
case-**sensitive**. A bare `export ZAI_API_KEY=...` makes
`resolve("zai_api_key")` call `os.environ.get("zai_api_key")` → `None`. So the
harness MUST bridge the uppercase env var to the lowercase alias via an explicit
override (the reliable path):

```python
secrets.set("zai_api_key", os.environ["ZAI_API_KEY"])      # always honored
secrets.set("zai_api_base", os.environ.get("ZAI_API_BASE")  # optional
            or "https://open.bigmodel.cn/api/paas/v4")
```

`api_base_alias` is SSRF-guarded by `is_safe_fetch_url()` before use as
`base_url`. Note: `open.bigmodel.cn` is a public host and passes.

### 2.3 `.secrets/llm_keys.env` ↔ gludd env-var name reconciliation
The user spec says the dotenv carries `ZAI_API_KEY` and `ZAI_BASE_URL`.
- `ZAI_API_KEY` matches gludd exactly. ✓
- `ZAI_BASE_URL` is **not** gludd's alias name. gludd's base alias is
  `zai_api_base` (the preset), and `ZAI_BASE_URL` *does* pass the env allowlist
  (`*_BASE_URL$`), but the gateway never resolves an alias literally named
  `ZAI_BASE_URL`. So the harness loader must **map** `ZAI_BASE_URL` →
  override `zai_api_base`. Decision: keep `.secrets/llm_keys.env` as the spec
  says (`ZAI_API_KEY`, `ZAI_BASE_URL`) and have the loader translate names.
  Document both accepted keys (`ZAI_BASE_URL` and `ZAI_API_BASE`) for safety.

`.secrets/` does **not** exist in the repo today (only `.secrets.baseline` for
detect-secrets). The harness creates/reads `.secrets/llm_keys.env`; it must be
git-ignored (add `/.secrets/` to `.gitignore`).

---

## 3. Harness architecture decision: in-process ASGI, not subprocess daemon

Two ways to "run gludd natively":

| | A. In-process ASGI (chosen) | B. Subprocess `gludd daemon` |
|---|---|---|
| DB isolation | in-memory `sqlite+aiosqlite://`, zero footprint | needs `GLUDD_DB_PATH` temp file |
| Tick control | call `loop.tick()` deterministically | wall-clock `run_forever`, must poll |
| Gateway injection | pass the gateway object directly | needs a `model_profiles/zai.yaml` + `ZAI_API_KEY` in subprocess env |
| Speed / flake | fast, deterministic, no port | slower, port-bound, the existing TUI e2e already shows port-8000 flake |
| Realism | real routers, real engine, real git, real reviewer | + real uvicorn + real lifespan |

**Decision: Scenario tests use variant A (in-process), mirroring the proven
`scripts/dogfood.py`.** Add ONE thin "real daemon boots and serves" smoke as
variant B (reuse `make smoke`'s pattern with an ephemeral port from the existing
`_find_free_port()` helper) so we still prove the native binary path. Variant B
does NOT need the live key (mock profile) — it only asserts `/healthz`,
`/api/status`, `POST /api/todos`.

---

## 4. File layout

```
tests/e2e/dogfood/
  __init__.py
  conftest.py                 # fixtures (secrets, in-proc app, gateway, workspaces)
  _secrets.py                 # dotenv loader for .secrets/llm_keys.env (no print)
  _gateway.py                 # real-zai-or-mock gateway factory + capture
  _site.py                    # site-test helpers (serve generated site, CRUD)
  test_self_host.py           # Scenario 1: gludd edits its own repo (meaningful)
  test_todo_website.py        # Scenario 2: greenfield todo website + site tests
  test_daemon_smoke.py        # Variant B: real `gludd daemon` boots + serves (mock)
scripts/
  dogfood.py                  # EXISTS — keep; refactor shared bits into the lib above
.secrets/
  llm_keys.env                # UNCOMMITTED (gitignored): ZAI_API_KEY=..., ZAI_BASE_URL=...
docs/e2e_harness/
  DESIGN_native_dogfood_harness.md   # this doc
```

Makefile additions (make-only policy):
```
test-e2e-dogfood:   pytest tests/e2e/dogfood/ -v -s    # -s so live model logs stream
dogfood-live:       ZAI from .secrets → run both scenarios live
dogfood-site:       run only the todo-website scenario
```

---

## 5. Secrets loader (`_secrets.py`)

```python
# Loads .secrets/llm_keys.env, returns a dict or None; NEVER prints values.
# Pseudocode — final code in the module.
def load_llm_keys(repo_root) -> dict | None:
    path = repo_root / ".secrets" / "llm_keys.env"
    if not path.exists():
        return None                      # caller skips gracefully
    env = _parse_dotenv(path)            # KEY=VALUE, ignores #comments/blank
    key = env.get("ZAI_API_KEY") or os.environ.get("ZAI_API_KEY")
    if not key:
        return None
    base = (env.get("ZAI_API_BASE") or env.get("ZAI_BASE_URL")
            or os.environ.get("ZAI_API_BASE") or os.environ.get("ZAI_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    return {"zai_api_key": key, "zai_api_base": base, "model": env.get("ZAI_MODEL", "glm-5.1")}
```
- Never logs the value. On dump/repr, mask to `zai-***{last4}` only if needed.
- Fixture `zai_creds` returns this dict; if `None`, dependent tests
  `pytest.skip("no .secrets/llm_keys.env; live zai tests skipped")`.

---

## 6. Gateway factory (`_gateway.py`) — live vs mock

The gateway is the ONLY place the live key is needed. Everything else (routers,
engine, git, reviewer, reconcile) is offline-real.

```python
def build_gateway(zai_creds, *, mock_response):
    """Return (gateway, mode). mode in {"live","mock"}.

    live: a real ModelGateway with the zai profile enabled and the key
          registered as an explicit override (bridges the uppercase-env trap).
    mock: a MagicMock whose call_model returns `mock_response` (offline).
    """
    if zai_creds is None:
        gw = MagicMock(); gw.call_model = MagicMock(return_value=MagicMock(content=mock_response))
        return gw, "mock"
    secrets = EnvSecretsManager()
    secrets.set("zai_api_key", zai_creds["zai_api_key"])     # §2.2 bridge
    secrets.set("zai_api_base", zai_creds["zai_api_base"])
    profile = ModelProfile(model_profile_id="zai-glm", provider="zai",
                           provider_package="langchain-openai",
                           provider_class_hint="ChatOpenAI",
                           model_name=zai_creds["model"],
                           credential_alias="zai_api_key",
                           api_base_alias="zai_api_base", enabled=True)
    gw = ModelGateway(profiles=[profile], secrets_manager=secrets, ...)
    return gw, "live"
```

Mocked-vs-live matrix:

| Step | Live key present | No key (offline) |
|---|---|---|
| project register / workspace materialize | real | real |
| `POST /api/todos` seed | real | real |
| `EventLoop.tick` claim/dispatch | real | real |
| **code-gen model call (ExecutionEngine)** | **live zai** | mock echo |
| real git branch + commit in workspace | real | real |
| **review adjudication (ReturnReviewer)** | **live zai** | mock → "complete" |
| reconcile → COMPLETE | real | real |
| **site tests on generated website** | real (real artifacts) | real (mock-written artifacts)¹ |
| teardown / delete temp dir + DB | real | real |

¹ In offline mode the mock writes a *fixed, known-good* todo-site scaffold so the
site tests still execute and prove the harness's site-test machinery works (the
"does the website serve + CRUD" assertions run against a deterministic artifact).
Live mode proves the *model* can produce a working site; offline mode proves the
*harness* without burning tokens. CI runs offline; `make dogfood-live` runs live.

---

## 7. Scenario 1 — self-host, MEANINGFUL change (`test_self_host.py`)

Upgrades `scripts/dogfood.py` from a marker file to a behavior change with a
test, so it satisfies "make MEANINGFUL updates to gludd's behavior".

Flow (setup → seed → run → assert → teardown):
1. **setup:** in-mem engine + `create_all`; FastAPI app with
   `_session_factory`; register `todos` router; `AsyncClient(ASGITransport)`.
   `ProjectManager().add_project(name="gludd-self", weight=100, workspace_path=ws,
   repo_url=f"file://{REPO_ROOT}")`; `git.clone` self into `ws`; set committer id.
2. **seed:** `POST /api/todos` with a meaningful task, e.g.
   *"Add `EventLoop.tick_count` property returning total ticks, with a unit test
   in tests/unit/test_event_loop_tick_count.py asserting it increments."*
   `work_type="code"`, `priority="high"`.
3. **run:** wire `ExecutionEngine(model_gateway=gw, workspace_path=ws)`; build
   `EventLoop(session=factory)`; patch `_dispatch_execute_job` to call
   `engine.execute(JobSpec(...))` and persist a `TaskReturn` (as dogfood.py does).
   `await loop.tick()` (dispatch). Then `ReturnReviewer(gateway=gw, …)` →
   `TaskDecisionModel`. `await loop.tick()` (reconcile).
4. **assert:** todo status == `complete`; a `gludd/*` branch exists in `ws`; the
   changed file(s) exist; **and the meaningful change is real** — run the
   generated unit test *inside the workspace* via `make`-equivalent
   (`uv run pytest tests/unit/test_event_loop_tick_count.py` executed with cwd=ws
   through the harness's allowed runner) and assert exit 0. (Offline mode: the
   mock emits a known-good patch + test so this still passes deterministically.)
5. **teardown:** `client.aclose()`, `engine.dispose()`, `shutil.rmtree(tmp)`.

This is the existing dogfood spine + (a) real gateway when keyed and (b) a
behavior+test assertion instead of a marker.

---

## 8. Scenario 2 — greenfield "todo website" (`test_todo_website.py`)

### 8.1 Setup
- `tmp = mkdtemp("gludd-todosite-")`; `ws = tmp/workspace`; `git init` an EMPTY
  repo (no clone — gludd builds from scratch); set committer id.
- in-mem engine + session factory + app + todos router + client (as §7).
- `ProjectManager().add_project(name="todo-site", weight=100, workspace_path=ws)`
  (no `repo_url` → no materialize; greenfield).
- gateway via §6.

### 8.2 Seed todos ("populate some todo items" → "build a todo website")
`POST /api/todos` several items the loop will work, e.g.:
1. "Scaffold a FastAPI todo-website backend at app/main.py with an in-memory
   store and `GET /` serving an HTML page that lists todos."
2. "Add REST CRUD: `GET /api/todos`, `POST /api/todos`, `PUT /api/todos/{id}`,
   `DELETE /api/todos/{id}` returning JSON."
3. "Serve a minimal `index.html` with a form to add a todo and a list with
   delete buttons that call the API."
4. "Add `tests/test_site.py` (pytest + httpx TestClient) covering create / list
   / update-complete / delete."

(work_type `code`, priority `high`/`medium`.) Backend chosen as **FastAPI** so
the site test can drive it with the already-present `httpx`/`starlette`
TestClient — no extra deps, no real port needed (though §8.4 also offers a real
HTTP variant).

The reconciled beta4 harness sends this contract as one acceptance-rich todo.
That preserves the complete generated-site assertion while bounding live-model
cost and avoiding ordering failures between four independently dispatched
tasks. Splitting the contract into several todos remains a future scheduler
stress case, not a prerequisite for proving the greenfield lifecycle.

### 8.3 Run
- Same patched-dispatch tick loop as §7, but iterate: for each seeded todo, run
  tick (dispatch) → review → tick (reconcile), or run `run_forever` for a bounded
  number of ticks with a per-todo timeout (§11 stall-guard). Each completed todo
  leaves a real git commit on a `gludd/*` branch in `ws`.
- Live mode: the model authors the website files. Offline mode: the mock emits
  the deterministic scaffold (a known-good FastAPI todo app + index.html + test)
  so the site tests still run.

### 8.4 Site tests (`_site.py`) — "verify the generated website works"
Two layers, both run against the artifacts in `ws`:

A. **In-process (default, dep-free):** import the generated `app/main.py` app
   object via an isolated import (spec from file path), wrap in
   `starlette.testclient.TestClient`, and assert:
   - `GET /` → 200, HTML contains a todo form / list container.
   - `POST /api/todos {"title": "buy milk"}` → 201/200, returns an id.
   - `GET /api/todos` → list contains "buy milk".
   - `PUT /api/todos/{id} {"done": true}` → reflects completion.
   - `DELETE /api/todos/{id}` → 200; subsequent `GET` no longer lists it.
   - (CRUD round-trip = the required "todo CRUD works".)

B. **Real-server (optional, `dogfood-site` full mode):** launch the generated
   app on an ephemeral port (`_find_free_port()` from the existing e2e conftest),
   `uvicorn`/`python -m app.main`, poll `GET /healthz` or `/`, run the same CRUD
   over real `httpx` against `http://127.0.0.1:PORT`, then kill it. Proves it
   "serves pages" over a real socket.

C. **The model's own test:** also execute the generated `tests/test_site.py`
   inside `ws` and assert exit 0 (cross-check that the model's tests pass, not
   just ours).

If the generated app's entrypoint/route shape differs from the seeded contract,
the site tests fail loudly (no escape hatch) — that is the signal the model
didn't build a working site. Offline mode pins the contract so machinery is
always exercised.

### 8.5 Teardown (delete the project)
`finally:` → `client.aclose()`, `engine.dispose()` (drops the in-mem DB),
`shutil.rmtree(tmp, ignore_errors=True)` (deletes workspace + artifacts). The
project row lived only in the in-mem DB, so it vanishes with the engine. Explicit
`DELETE /admin/projects/{project_id}` is also called first for realism/coverage.

---

## 9. Fixtures (`conftest.py`)

| Fixture | Scope | Yields |
|---|---|---|
| `repo_root` | session | `Path` to repo root |
| `zai_creds` | session | dict from `_secrets.load_llm_keys` or `None` (skip marker source) |
| `gateway_mode` | session | `"live"` if `zai_creds` else `"mock"` |
| `tmp_workspace` | function | fresh `mkdtemp`; auto-`rmtree` on teardown |
| `inproc_app` | function | `(app, client, factory, engine)`; auto-dispose |
| `project_manager` | function | `ProjectManager()` |
| `build_gateway` | function | factory from `_gateway.build_gateway` |
| `free_port` | function | `_find_free_port()` (reuse existing helper) |

Markers: `@pytest.mark.live_zai` (deselected in CI default), `@pytest.mark.e2e`.
A `pytest.skip` inside `zai_creds==None` paths for the *live-only* assertions,
while the offline path still runs.

---

## 10. What needs the live key vs runs offline

- **Needs live zai key:** the actual quality of model-authored code — i.e. the
  *proof that a real model can build a working todo site* and make a meaningful
  self-edit. Run via `make dogfood-live` / `make test-e2e-dogfood` when
  `.secrets/llm_keys.env` is present.
- **Runs fully offline (CI default):** the entire harness machinery — project
  init, todo seeding, tick/dispatch/review/reconcile, real git commits, the
  site-test runner, CRUD assertions, teardown — using the deterministic mock
  artifact. This guarantees the harness itself is green in CI without secrets and
  without tokens; the live run only swaps the two gateway calls.

### 10.1 Practitioner evidence and beta4 reconciliation

Long-lived upstream reports reinforce keeping the default proof deterministic
and making the optional live boundary explicit:

- [HTTPX discussion #2056](https://github.com/encode/httpx/discussions/2056)
  records intermittent `RemoteProtocolError` failures in CI across years of
  follow-up. The default site gate therefore uses an in-process ASGI transport,
  not a socket or external network whose connection lifecycle can flake.
- [openai-python issue #557](https://github.com/openai/openai-python/issues/557)
  documents credentials and base URLs being captured before later environment
  changes were visible. The live case builds a gateway from explicitly loaded
  credentials and skips only that case when the credential is absent; the
  offline lifecycle never depends on mutable ambient configuration.
- [Starlette issue #2524](https://github.com/Kludex/starlette/issues/2524)
  tracks `TestClient` compatibility drift after an HTTPX deprecation. The
  generated-app contract stays behind `_site.py` and is exercised against the
  repository's pinned Starlette/HTTPX pair so dependency changes fail one
  focused compatibility gate.
- [pytest issue #2239](https://github.com/pytest-dev/pytest/issues/2239)
  shows the long-running CI ambiguity around an empty collected suite. The
  credential-free scenario is consequently a real always-collected test, while
  only the live credential variant may skip.

The reconciled implementation uses current async engine and task-return APIs,
passes artifact evidence through review, reconciles the todo to `complete`,
requires a real `gludd/*` branch and commit, fails closed on every HTML/CRUD
assertion, and removes the workspace during teardown. Dispatch and reconcile
ticks have 30-second bounds. Offline and live runs traverse the same lifecycle;
offline review is deterministic, while live mode uses the configured gateway
for generation and review. Focused CI verification is:

```console
make test-files GLUDD_XDIST=0 TESTFILES='tests/e2e/dogfood/test_dogfood_todo_site.py'
```

Without a ZAI credential this runs seven cases and skips only the live case.

---

## 11. Hardest parts + gludd gaps that block a clean run

1. **No public "init project + run to completion" one-shot.** There is no
   `gludd run-project-until-done`. The loop is tick-driven and dispatch in the
   real daemon goes through Ansible playbooks (`_WORK_TYPE_PLAYBOOK_MAP`,
   `validate_task.yml`, etc.) — NOT a direct `ExecutionEngine` call. The existing
   `scripts/dogfood.py` works around this by **monkeypatching
   `loop._dispatch_execute_job`** to call `ExecutionEngine.execute` in-process.
   The harness inherits that monkeypatch. *Gap to flag:* a first-class
   "in-process execution dispatch mode" (a `dispatch_mode="inproc_engine"` on the
   project, or an injectable `runner` that wraps `ExecutionEngine`) would remove
   the monkeypatch and make this a supported path rather than a test hack. This
   is the single biggest realism gap.

2. **`ZAI_API_KEY` uppercase vs `zai_api_key` lowercase alias (likely live bug).**
   §2.2. The harness sidesteps it with an explicit `secrets.set(...)` override,
   but a *plain* `export ZAI_API_KEY=...` against the real daemon may resolve to
   `None`. *Gap to flag / quick fix candidate:* have profile construction (or
   `EnvSecretsManager.resolve`) fall back to an uppercased env lookup, or have
   `migrate_profile_secrets` bridge `credential_env_var → credential_alias`. The
   design notes this as "likely, read-not-executed"; confirm with one live call.

3. **`.secrets/llm_keys.env` key name vs gludd alias.** Spec uses
   `ZAI_BASE_URL`; gludd's alias is `zai_api_base`. Loader translates (§5). Must
   gitignore `/.secrets/`.

4. **The model must hit a known contract for site tests to pass.** A free-form
   model may name files/routes differently than the seeded acceptance criteria.
   Mitigation: put the exact entrypoint path + route shape into the todo
   `description`/acceptance criteria, and have the site test probe a couple of
   likely entrypoints before failing. Offline mode pins it so machinery is always
   covered.

5. **Multi-todo orchestration + stall risk.** Running several todos to completion
   means many ticks; a live model call can hang. Use a bounded tick budget +
   per-todo `asyncio.wait_for` timeout (gludd already has `RunBudgetGuard` /
   `SpendLimiter`; wire `spend_window_usd` low in the harness config to cap
   token spend). Honor the repo's observability rule: stream `-s` logs, heartbeat
   each tick, never go silent on the live run.

6. **Real-server site test needs the generated app importable/launchable.** If
   the model emits non-FastAPI (e.g. Flask/Node), layer A's TestClient import
   breaks. Constrain the seed to FastAPI (already in gludd's deps) so layer A
   needs zero new dependencies; layer B (real port) is the general fallback.

7. **`tests/e2e/conftest.py` is a dead helper, not a real conftest.** New
   fixtures go in `tests/e2e/dogfood/conftest.py`; reuse only `_find_free_port`.
   `make test-e2e` runs xdist — these dogfood tests are stateful/serial, so mark
   them to run on a single worker (or in their own `make test-e2e-dogfood` target
   without `-n`).

---

## 12. Summary of the concrete flow

```
[fixture] load .secrets/llm_keys.env → zai_creds or None (skip live asserts)
[setup]   in-mem sqlite + create_all + FastAPI app + todos router + AsyncClient
          ProjectManager.add_project(ws)  (clone-self OR git-init greenfield)
[seed]    POST /api/todos  × N  (meaningful self-edit  |  build-todo-website)
[gateway] build_gateway(zai_creds) → live ModelGateway(zai)  OR  mock echo
[run]     loop = EventLoop(session=factory); patch _dispatch_execute_job→engine
          repeat: tick (dispatch) → ReturnReviewer → TaskDecision → tick (reconcile)
[assert]  todo.status == complete; gludd/* branch + commit in ws;
          self-host: run generated unit test in ws → exit 0
          website:   site tests — serve / + CRUD round-trip + model's test → green
[teardown] DELETE /admin/projects/{id}; client.aclose(); engine.dispose();
           shutil.rmtree(tmp)  → project, DB, workspace all gone
```

Offline (no key): identical, with the two gateway calls mocked to a deterministic
known-good artifact so CI is green without secrets; live (`make dogfood-live`):
the two calls go to real GLM and prove a real model builds a working site and
makes a real self-edit.
```
