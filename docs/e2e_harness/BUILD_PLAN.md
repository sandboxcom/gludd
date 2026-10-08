# E2E Harness Build Plan — Ordered, Dependency-Aware

**Generated:** 2026-06-18
**Sources:** DESIGN_native_dogfood_harness.md, AUDIT_model_discovery_weights_cost.md, DESIGN_local_cloud_providers_e2e.md
**Constraint:** Gate is running; Wave A is buildable now (low memory, write-only tasks). Wave B is post-gate (test collection, heavy builds). Wave C needs external services / Azure.

---

## Dependency Graph

```text
ZAI-BUG-1 (ZAI_API_KEY case) ──┐
ZAI-BUG-2 (zai_api_base alias) ─┼──► all live-gateway tests (dogfood + providers)
SSRF-OPT  (ALLOW_LOCAL flag)   ─┘──► all local-provider gateway-path tests

P0 (non-zero rates + cost persist) ──► P1 ──► P4 ──► harness cost assertions
P1 (real scores, activate trace)   ──► P3 (skill_id col, needs alembic)
P2 (autodiscovery GET /models)     ── independent ──► ollama/provider discovery tests
P3 (skill_id col + migration)      ── depends P1 for meaningful data
P4 (SpendLimiter wired)            ── depends P0 for real spend input

H-SECRETS  ──────────────────────────────────► H-DOGFOOD-SELFHOST
H-SECRETS  ──────────────────────────────────► H-DOGFOOD-GREENFIELD
H-GATEWAY  (live vs mock factory)  ──────────► H-DOGFOOD-SELFHOST, H-DOGFOOD-GREENFIELD

P0 + P1  ──────────────────────────────────► H-ZAI-WEIGHTS, H-ZAI-COST
P2       ──────────────────────────────────► H-ZAI-DISCOVERY
SSRF-OPT ──────────────────────────────────► H-OLLAMA, H-VLLM, H-LLAMACPP (gateway path)
(no dep) ──────────────────────────────────► H-SLURM (job control)
(no dep) ──────────────────────────────────► H-AZURE-ENV (SSRF allows public hosts)
H-AZURE-ENV ────────────────────────────────► H-AZURE-PROVISION (extends it)
```

---

## Wave A — Buildable Now (independent, new files only, no gate needed)

These are write-only tasks: new files, config changes, small targeted edits. No
test collection run required. Fan out all in parallel. None touches a file
another A-task needs (except gitignore/makefile, which are tiny serial merges).

---

### A1 · ZAI-BUG-1 — Fix ZAI_API_KEY case-sensitivity in EnvSecretsManager

**Goal:** `EnvSecretsManager.resolve("zai_api_key")` must find `ZAI_API_KEY` in
env without requiring an explicit `secrets.set()` override.

**Files:**
- `src/general_ludd/secrets/env.py` — in `resolve()`, after `os.environ.get(alias)` fails,
  try `os.environ.get(alias.upper())` as a fallback (only when alias passes the
  allowlist regex already applied). One or two lines.

**Migration:** None.
**Depends on:** Nothing. Independent.
**Run now vs post-gate:** Now (edit only).
**TDD acceptance test:** `tests/unit/test_secrets_env.py` — assert
`EnvSecretsManager().resolve("zai_api_key")` returns `"testval"` when
`os.environ["ZAI_API_KEY"] = "testval"` and no explicit override is set.

---

### A2 · ZAI-BUG-2 — Normalize ZAI_BASE_URL → zai_api_base in secrets loader

**Goal:** `.secrets/llm_keys.env` may carry `ZAI_BASE_URL`; the gateway expects
`zai_api_base`. The loader (and `migrate_profile_secrets`) must map the former
to the latter.

**Files:**
- `tests/e2e/dogfood/_secrets.py` — NEW: dotenv loader that reads
  `.secrets/llm_keys.env`, accepts `ZAI_API_BASE` or `ZAI_BASE_URL` for the
  base URL, and always returns keys normalized to `{"zai_api_key", "zai_api_base",
  "model"}`. Silently returns `None` when the file or `ZAI_API_KEY` is absent.
- `.gitignore` — append `/.secrets/` if not present.

**Migration:** None.
**Depends on:** Nothing. Independent of A1 (A1 fixes the daemon path; A2 fixes
the harness loader).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_secrets_loader.py` — create a tmp
dotenv with `ZAI_API_KEY=k` / `ZAI_BASE_URL=http://x`, call `load_llm_keys`,
assert result `{"zai_api_key":"k", "zai_api_base":"http://x", "model":"glm-5.1"}`.
Also assert missing-file → `None`.

---

### A3 · SSRF-OPT — GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS opt-in flag

**Goal:** Add a narrowly-scoped env flag that permits `http` + loopback /
RFC-1918 base URLs through the SSRF guard, for the `api_base_alias` resolution
path only (not skill fetcher, not connectors). Default remains fail-closed.

**Files:**
- `src/general_ludd/models/gateway.py` — in `_invoke_and_bill`, around the
  `is_safe_fetch_url(base_url)` raise: if
  `os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"`, skip the guard
  for this one check only. Log a one-line warning.
- `docs/e2e_harness/BUILD_PLAN.md` (this file) — already documents the flag.

**Migration:** None.
**Depends on:** Nothing. Independent.
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_gateway_ssrf_opt.py` — with the flag
unset, assert `_invoke_and_bill` raises `ValueError` for `http://localhost:1234`;
with flag set to `"1"`, assert it proceeds past the guard (can mock the actual
HTTP call).

---

### A4 · P0a — Set non-zero cost rates on zai profile preset

**Goal:** `ModelProfile.cost_per_input_token / cost_per_output_token` must be
non-zero on the zai preset so the real formula at `gateway.py:319-322` produces
dollars instead of 0.0.

**Files:**
- `src/general_ludd/models/provider_presets.py` — add
  `cost_per_input_token = 0.000001` (glm-5.1 approximate; adjust to real price
  once known) and `cost_per_output_token = 0.000002` to the `"zai"` preset dict.
  Also add the `free_models_endpoint` field pointing to `{api_base_url}/models`
  (primes P2 without writing code yet).

**Migration:** None (preset is code, not DB).
**Depends on:** Nothing.
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_provider_presets.py` — assert
`PROVIDER_PRESETS["zai"]["cost_per_input_token"] > 0`. Existing preset tests
should not regress.

---

### A5 · P0b — Persist cost_estimate and real token counts into benchmark_results

**Goal:** Close the cost write loop: when a task completes,
`benchmark_results.cost_usd` receives the gateway's computed cost instead of
the hardcoded `0.0` at `execution/engine.py:212`.

**Files:**
- `src/general_ludd/execution/engine.py` — in the completion handler (~line
  338-354), collect `model_response.cost_estimate`,
  `model_response.usage.input_tokens`, `model_response.usage.output_tokens` from
  the gateway call result and pass them into `record_job_benchmark(...)` instead
  of `cost_usd=0.0` and the `len//4` estimate.
- `src/general_ludd/event_loop/benchmark.py` — extend `record_job_benchmark`
  signature to accept `cost_usd: float`, `input_tokens: int`, `output_tokens:
  int` (all default `0`/`0`/`0` for backward compat). Write them to the DB row.

**Migration:** None (columns already exist in `benchmark_results` —
`db/models.py:491-522`).
**Depends on:** A4 (rates must be non-zero for cost > 0 to flow).
**Run now vs post-gate:** Now (edit only).
**TDD acceptance test:** `tests/unit/test_benchmark_cost.py` — mock a
`ModelResponse(cost_estimate=0.05, input_tokens=100, output_tokens=200)`,
call `record_job_benchmark` with those values, query the row, assert
`row.cost_usd == 0.05` and `row.input_tokens == 100`.

---

### A6 · P1 — Activate real graded scores in benchmark write path

**Goal:** Replace the fixed heuristics at `event_loop/benchmark.py:30-32`
(completion=1.0/0.0, code_quality=0.5) with the real graded scorer from
`scoring/engine.py:223-224`. Wire the event loop's dead `_active_traces` /
`_benchmark_recorder` fields (`event_loop/loop.py:189-190`) to actually create
and complete spans so `record_from_trace` has real data.

**Files:**
- `src/general_ludd/event_loop/loop.py` — initialize `_benchmark_recorder` and
  create a span per dispatched task; complete the span in the reconcile phase
  with the final decision and token usage.
- `src/general_ludd/event_loop/benchmark.py` — call
  `observability/recorder.py::record_from_trace` when a trace span is present,
  falling back to the existing heuristic when it is not (backward compat guard).
- `src/general_ludd/observability/recorder.py` — verify `record_from_trace` path
  is import-safe (no prod caller yet; check for any late-binding issues).

**Migration:** None (columns exist).
**Depends on:** A5 (P0b must be wired so the row exists to write scores into).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_benchmark_scores.py` — complete a mock
trace with `completion_score=0.9`, `code_quality_score=0.7`, assert the DB row
carries those values rather than the old heuristic constants.

---

### A7 · P2 — Build model autodiscovery (GET {api_base}/models)

**Goal:** Add `discover_models(api_base, api_key)` that calls the OpenAI-compat
`GET {api_base}/models` endpoint, parses `data[].id`, and returns/registers the
discovered model ids. Wire behind `free_models_endpoint` preset field. Expose
through `/admin/models` when a live key is present.

**Files:**
- `src/general_ludd/models/model_registry.py` — add
  `discover_remote_models(api_base: str, api_key: str | None) -> list[str]`; use
  `httpx.get(f"{api_base}/models", headers={"Authorization": f"Bearer
  {api_key}"}, timeout=5)`, parse `response.json()["data"][*]["id"]`.
- `src/general_ludd/routers/models.py` — in the `/admin/models` handler (line
  237-242), if the profile has `free_models_endpoint` set (or a live key), call
  `discover_remote_models` and merge discovered ids into the response under a
  `"discovered"` key.

**Migration:** None.
**Depends on:** A4 (preset must have `free_models_endpoint` field set).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_model_discovery.py` — mock
`httpx.get` to return `{"data":[{"id":"glm-5.1"},{"id":"glm-4"}]}`, call
`discover_remote_models("https://open.bigmodel.cn/api/paas/v4", "k")`, assert
result is `["glm-5.1","glm-4"]`. Integration test (needs network, marked
`@pytest.mark.live_zai`): call with real key and assert non-empty list.

---

### A8 · P3 — Add skill_id dimension to benchmark_results (schema + alembic)

**Goal:** Add a nullable `skill_id` column to `benchmark_results` and include it
in the `get_aggregate_scores` group-by, so per-skill-per-model effectiveness is
measurable.

**Files:**
- `src/general_ludd/db/models.py` — add `skill_id: Mapped[str | None]` column to
  `BenchmarkResultModel` (nullable, indexed).
- `src/general_ludd/db/repository.py` — extend `get_aggregate_scores`
  (line 636-683) to include `skill_id` in the group-by and the returned
  aggregate dict.
- `src/general_ludd/event_loop/benchmark.py` — pass `skill_id` through from
  caller (default `None`).
- **ALEMBIC MIGRATION** — generate migration: `alembic revision --autogenerate
  -m "add_skill_id_to_benchmark_results"`. This is the D-11 migration; coordinate
  with any other pending schema migrations to avoid head conflicts.

**Migration:** YES — new nullable column on `benchmark_results`. Must coordinate
with D-11 migration head if one is in flight. Use `--head base` if needed or
chain onto the current head. Nullable + indexed = safe on existing data.
**Depends on:** A6 (P1 must write real rows so skill_id has meaning; without it
you can add the column but it stays NULL everywhere).
**Run now vs post-gate:** Now (schema edit + alembic rev). Migration itself runs
on the gate's startup sequence.
**TDD acceptance test:** `tests/unit/test_benchmark_skill_id.py` — call
`record_job_benchmark(... skill_id="code-gen-skill")`, query, assert
`row.skill_id == "code-gen-skill"`. Also assert `get_aggregate_scores` groups
correctly when two rows share the same `(model_id, skill_id)`.

---

### A9 · P4 — Wire SpendLimiter into dispatch + persist to spend_records

**Goal:** Honor the TODO at `controllers/spend_limiter.py:17-28`. Call
`SpendLimiter.would_exceed(projected_cost)` before each model call in dispatch,
and write a `spend_records` row at call time with the real cost (from P0b).

**Files:**
- `src/general_ludd/controllers/spend_limiter.py` — implement `would_exceed()`:
  query `spend_records` for the current window, sum `cost_usd`, compare to limit.
- `src/general_ludd/execution/engine.py` — before `gateway.call_model(...)`,
  call `spend_limiter.would_exceed(estimated_cost)` and raise / return early if
  exceeded (fail-closed).
- `src/general_ludd/db/repository.py` — add `record_spend(project_id, model_id,
  cost_usd, tokens)` that writes a `spend_records` row.
- `src/general_ludd/daemon.py` — ensure `SpendLimiter` is passed into
  `ExecutionEngine` at startup (it is already constructed; just wire it through).

**Migration:** None (`spend_records` table already exists at `db/models.py:440-468`).
**Depends on:** A5 (P0b — needs real cost_estimate to check against; with 0.0
the limiter is a no-op even if wired).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `tests/unit/test_spend_limiter.py` — pre-seed
`spend_records` with enough rows to exceed the limit, assert `would_exceed(0.01)`
returns True and dispatch raises/exits early. Also assert below-limit case passes
through.

---

### A10 · H-CONFTEST-DOGFOOD — Harness fixtures conftest + gitignore

**Goal:** Create the shared fixture infrastructure for the dogfood E2E tests.

**Files:**
- `tests/e2e/dogfood/__init__.py` — empty.
- `tests/e2e/dogfood/conftest.py` — fixtures: `repo_root`, `zai_creds` (calls
  `_secrets.load_llm_keys`, returns `None` gracefully), `gateway_mode`,
  `tmp_workspace` (auto-rmtree), `inproc_app` (in-mem sqlite + FastAPI +
  AsyncClient + auto-dispose), `project_manager`, `build_gateway_fn`, `free_port`
  (reuse `_find_free_port` from `tests/e2e/conftest.py`). Markers: `live_zai`,
  `e2e`. Serial-only (no xdist `-n`).
- `tests/e2e/dogfood/_gateway.py` — `build_gateway(zai_creds, mock_response)`:
  live path via `EnvSecretsManager` + real `ModelGateway` with explicit
  `secrets.set("zai_api_key", ...)` override (bridges A1's fix for harness use);
  mock path via `MagicMock`.

**Migration:** None.
**Depends on:** A2 (secrets loader), A1 (the set-override pattern in _gateway.py).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `pytest tests/e2e/dogfood/conftest.py --collect-only` —
all fixtures importable, no collection error. `inproc_app` fixture creates + tears
down an in-mem sqlite cleanly (assert `engine.is_disposed` after fixture scope).

---

### A11 · H-CONFTEST-PROVIDERS — Provider E2E shared conftest

**Goal:** Create the shared probe + skip + gateway-builder helpers for
multi-provider tests.

**Files:**
- `tests/e2e/providers/__init__.py` — empty.
- `tests/e2e/providers/conftest.py` — `_http_alive(url, path, timeout)`,
  `require_backend(env_var, path)`, `build_local_gateway(base_url, model,
  profile_id)` with path A (ALLOW_LOCAL) + path B (SSRF bypass via pre-built
  ChatOpenAI), `ALLOW_LOCAL` constant from env.

**Migration:** None.
**Depends on:** A3 (SSRF opt-in; conftest reads the flag).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `pytest tests/e2e/providers/ --collect-only` — no
import or collection errors even with no backends configured.

---

### A12 · MAKEFILE — Add e2e make targets

**Goal:** Wire all new test directories into Makefile targets per make-only
policy.

**Files:**
- `Makefile` — add:
  - `test-e2e-dogfood` — `pytest tests/e2e/dogfood/ -v -s -p no:xdist`
  - `dogfood-live` — sets `ZAI_*` from `.secrets/llm_keys.env`, runs dogfood
  - `dogfood-site` — runs only `test_todo_website.py`
  - `test-e2e-providers` — `pytest tests/e2e/providers/ -v -s`
  - `test-e2e-ollama` / `-vllm` / `-llamacpp` / `-slurm` / `-azure` — single-backend
  - `test-e2e-providers-local` — sets `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` + default ports
  - `test-e2e-azure-provision` — sets `AZURE_PROVISION_E2E=1` (scheduled only)

**Migration:** None.
**Depends on:** A10, A11 (directories must exist first).
**Run now vs post-gate:** Now.
**TDD acceptance test:** `make test-e2e-dogfood` and `make test-e2e-providers`
both exit 0 with "no tests ran" or all skip when no key/backend configured.

---

## Wave B — Post-Gate (test collection required, heavier builds)

Run these after the current gate finishes. Fan out B1–B4 in parallel; B5+ depend
on the product fixes landing (A5/A6/A7/A8/A9 must be merged + gate green before B5+).

---

### B1 · H-DOGFOOD-SELFHOST — Scenario 1: self-host meaningful change

**Goal:** Write `test_self_host.py` — gludd edits its own repo, leaves a real
git commit, the generated test passes.

**Files:**
- `tests/e2e/dogfood/test_self_host.py` — setup (in-mem DB + ProjectManager
  + `file://` clone-self); seed one meaningful todo ("Add
  `EventLoop.tick_count` property with a unit test"); patch
  `_dispatch_execute_job` → `ExecutionEngine.execute`; tick × dispatch → review
  → tick × reconcile; assert `todo.status == "complete"`, `gludd/*` branch
  exists, generated test file exits 0 (offline: deterministic known-good patch).
  Mark `@pytest.mark.e2e`.

**Migration:** None.
**Depends on:** A10 (fixtures), A1+A2 (secret path for live mode). Soft dep on
A5+A6 (without them live assertions are vacuous, but offline mode is unblocked).
**Run now vs post-gate:** Post-gate (needs pytest collection without
interrupting the running gate).
**TDD acceptance test:** `make test-e2e-dogfood -k test_self_host` — passes
offline (mock gateway), skips live asserts when no `.secrets/llm_keys.env`.

---

### B2 · H-DOGFOOD-GREENFIELD — Scenario 2: greenfield todo website + site tests

**Goal:** Write `test_todo_website.py` + `_site.py` — gludd builds a todo
FastAPI website from scratch; site tests assert CRUD works.

**Files:**
- `tests/e2e/dogfood/_site.py` — `run_site_tests(ws_path)`: import
  `app/main.py` via spec, wrap in `starlette.testclient.TestClient`, assert GET
  `/` 200 + HTML, POST/GET/PUT/DELETE CRUD round-trip. Optional real-server
  variant via ephemeral port + httpx.
- `tests/e2e/dogfood/test_todo_website.py` — setup (git-init empty greenfield);
  seed 4 todos; tick loop per todo; teardown (`shutil.rmtree`). Assert all todos
  complete; run `_site.py::run_site_tests(ws)`. Offline: mock emits known-good
  FastAPI scaffold.

**Migration:** None.
**Depends on:** A10, B1 (shares tick-loop pattern; write B1 first for shared
patterns).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make dogfood-site` — offline mode must pass; live
mode `make dogfood-live` asserts real model produces a working todo site.

---

### B3 · H-DAEMON-SMOKE — Real daemon boots + serves (Variant B)

**Goal:** Write `test_daemon_smoke.py` — real `gludd daemon` binary on an
ephemeral port, mock profile (no key needed), assert `/healthz`, `/api/status`,
`POST /api/todos`.

**Files:**
- `tests/e2e/dogfood/test_daemon_smoke.py` — reuse `_find_free_port()`, start
  `gludd daemon --port {port}` via subprocess, poll `/healthz` (bounded 10s),
  assert `/api/status` shape, `POST /api/todos` 201, kill. Mark `@pytest.mark.e2e`.

**Migration:** None.
**Depends on:** A10 (conftest), A12 (make target).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-dogfood -k test_daemon_smoke` — passes
without any secret (mock profile). Must complete in < 30s.

---

### B4 · H-ZAI-CONNECTION — zai connection + basic call assertions

**Goal:** Write the zai E2E test: build a real gateway from the zai preset,
make a small model call, assert response is non-empty.

**Files:**
- `tests/e2e/providers/test_zai_e2e.py` — `require_backend` skips unless
  `.secrets/llm_keys.env` present; calls `gateway.call_model(...)` with
  `"Reply with the single word: pong"`; asserts `response.content` contains
  "pong"; asserts `response.usage_metadata` has token counts. Mark
  `@pytest.mark.live_zai`.

**Migration:** None.
**Depends on:** A11 (conftest), A1+A2 (bug fixes so the real key resolves).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-providers -k test_zai_e2e` — skips
without key; passes with key.

---

### B5 · H-ZAI-WEIGHTS-COST — zai weights + cost assertions (non-vacuous)

**Goal:** After a model call through the real gateway, assert `cost_estimate >
0` and that `benchmark_results.cost_usd` was written with a real value.

**Files:**
- `tests/e2e/providers/test_zai_e2e.py` (extend B4) — after the call, query
  the benchmark row for that task, assert `row.cost_usd > 0` and
  `row.composite_score > 0` (not the old 0.5 heuristic). Assert
  `AdaptiveRouter.rank()` returns the zai profile in a non-zero-cost position.

**Migration:** None.
**Depends on:** B4, A5 (P0b — cost must be persisted), A6 (P1 — real scores).
**Run now vs post-gate:** Post-gate (after A5+A6 merge + gate green).
**TDD acceptance test:** `make test-e2e-providers -k test_zai_e2e::test_cost_persisted`
— asserts `row.cost_usd > 0.0`; fails before A5 lands (proving the fix matters).

---

### B6 · H-ZAI-DISCOVERY — zai autodiscovery assertions

**Goal:** After P2 ships, assert `discover_remote_models` returns at least one
model id from the live zai endpoint.

**Files:**
- `tests/e2e/providers/test_zai_e2e.py` (extend B4/B5) — call
  `discover_remote_models(api_base, api_key)`, assert `len(result) >= 1` and
  `"glm-5.1" in result` (or any id). Assert `/admin/models` response includes
  a `"discovered"` key with non-empty list.

**Migration:** None.
**Depends on:** B4, A7 (P2 — discovery code must exist).
**Run now vs post-gate:** Post-gate (after A7 merge + gate green).
**TDD acceptance test:** `make test-e2e-providers -k test_zai_discovery` —
skips without key; asserts non-empty model list with key.

---

### B7 · H-OLLAMA — ollama E2E (register + call + weights)

**Goal:** Write `test_ollama_e2e.py` covering register → route → model call →
cost/token record → UtilizationTracker tokens advanced.

**Files:**
- `tests/e2e/providers/test_ollama_e2e.py` — `require_backend("OLLAMA_BASE_URL")`;
  `build_local_gateway(OLLAMA_BASE_URL, OLLAMA_MODEL, "ollama-e2e")`; assert
  register → route → `gateway.call_model` → non-empty response; assert
  `response.cost_estimate == 0.0` (local = free); GET `/v1/models` → OLLAMA_MODEL
  present; `tracker.record_tokens` → `total_tokens` advanced.
  Gateway-path tests: `skipif not ALLOW_LOCAL` with clear reason.

**Migration:** None.
**Depends on:** A3 (SSRF opt-in), A11 (conftest). Soft dep on A5+A6 (tracker
assertions are independent of those).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-ollama` — skips without `OLLAMA_BASE_URL`;
all assertions pass with a real ollama server and `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1`.

---

### B8 · H-VLLM — vllm E2E (call + LocalInferenceManager argv + optional spawn)

**Files:**
- `tests/e2e/providers/test_vllm_e2e.py` — `require_backend("VLLM_BASE_URL")`;
  model-call + register + bill flow (same as B7); `LocalInferenceManager` config
  path + argv assertion; spawn variant gated on `VLLM_E2E_SPAWN=1` +
  `shutil.which("vllm")`.

**Migration:** None.
**Depends on:** A3, A11, B7 (shares pattern).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-vllm` — skips without `VLLM_BASE_URL`.

---

### B9 · H-LLAMACPP — llama.cpp E2E

**Files:**
- `tests/e2e/providers/test_llamacpp_e2e.py` — as B8 but `LLAMACPP_BASE_URL`,
  `LLAMACPP_MODEL`, `LLAMACPP_E2E_SPAWN=1` variant, argv asserts for
  `python3 -m llama_cpp.server`.

**Migration:** None.
**Depends on:** A3, A11, B7 (pattern).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-llamacpp` — skips without `LLAMACPP_BASE_URL`.

---

### B10 · H-SLURM — Slurm dispatch E2E (submit/poll/cancel, no SSRF dep)

**Goal:** Real `SlurmAdapter` submit → poll → cancel loop. No SSRF dependency
(Slurm makes its own httpx calls, not through the model SSRF guard).

**Files:**
- `tests/e2e/providers/test_slurm_e2e.py` — `SlurmAdapter.available()` skip gate;
  submit `"echo gludd-e2e && sleep 5"`, assert real job_id, poll to COMPLETED;
  submit + cancel, assert CANCELLED. `SLURM_SERVE_E2E=1` opt-in: spawn model
  via `LocalInferenceManager(engine="slurm")`, assert `slurm_job_id` event, then
  model call (gated on `ALLOW_LOCAL` + served URL reachable).

**Migration:** None.
**Depends on:** A11. The slurm-serve sub-variant depends on A3.
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-slurm` — skips without `SLURM_E2E=1` or
`SLURM_REST_URL`.

---

### B11 · H-AZURE-ENV — Azure env-pointer E2E (CI-friendly, full SSRF path)

**Goal:** Most faithful gateway test in the suite — public https host passes SSRF
guard natively, no flag needed. Register + route + call + metered billing.

**Files:**
- `tests/e2e/providers/test_azure_e2e.py` — `require_backend("AZURE_BASE_URL")`;
  profile with real Azure per-token rates set; assert `response.cost_estimate > 0`
  (metered billing; proves the P0 fix is end-to-end real); GET `/v1/models` +
  assert `AZURE_MODEL` present. No A3 dependency.

**Migration:** None.
**Depends on:** A11, A5 (P0b — cost must be > 0 for the billing assertion to
pass). Soft dep on A4 (rates must be set on the profile).
**Run now vs post-gate:** Post-gate.
**TDD acceptance test:** `make test-e2e-azure` — skips without `AZURE_BASE_URL`;
`cost_estimate > 0` assertion proves the billing path is wired.

---

## Wave C — Needs External Services / Scheduled (not CI gate)

---

### C1 · H-AZURE-PROVISION — Full provision/teardown test (opt-in, cost-gated)

**Goal:** Prove the provision → serve → discover → call → bill → destroy spine
using `TerraformGenerator` + `DeploymentManager`.

**Files:**
- `tests/e2e/providers/test_azure_provision_e2e.py` — hard gate
  `AZURE_PROVISION_E2E=1` + `GLUDD_E2E_MAX_SPEND_USD`; `@pytest.mark.azure_provision`;
  `try/finally` with guaranteed `DeploymentManager.destroy()`; assert
  `cost_incurred < GLUDD_E2E_MAX_SPEND_USD`; reuse B11 assertions once endpoint
  is live; optional slurm sub-variant.

**Migration:** None.
**Depends on:** B11 (env-pointer assertions reused), A5+A6 (real cost).
**Run now vs post-gate:** Scheduled (nightly/weekly). Never in PR gate.
**TDD acceptance test:** `make test-e2e-azure-provision` — exits 0 only when
`AZURE_PROVISION_E2E=1` set, spend ceiling not exceeded, teardown ran.

---

## Orchestrator Fan-out Order

### Immediate (gate still running — dispatch in parallel now):

A1, A2, A3, A4 — all independent, touch different files.
A5 depends on A4: dispatch A4 first, then A5 as soon as A4 merges.
A6 depends on A5: chain after A5.
A7 depends on A4 (preset field): dispatch in parallel with A5.
A8 depends on A6: chain after A6 (needs alembic, coordinate with D-11 head).
A9 depends on A5: chain after A5, in parallel with A6.
A10 depends on A1+A2: dispatch after A1+A2 merge.
A11 depends on A3: dispatch after A3 merges.
A12 depends on A10+A11: dispatch last among Wave A.

### After gate finishes (post-gate fan-out):

Fan out B1, B2, B3, B4 in parallel (all are independent new files).
B5 after A5+A6+B4 merge. B6 after A7+B4 merge.
B7, B8, B9 in parallel (all independent provider tests).
B10 parallel with B7-B9.
B11 after A5+A11 merge, parallel with B7-B10.

### Scheduled (not CI):

C1 after B11.

---

## Task Summary Table

| ID | Wave | Goal (one line) | Files | Migration | Depends | Fan-out now? |
|----|------|-----------------|-------|-----------|---------|--------------|
| A1 | A | Fix ZAI_API_KEY case in EnvSecretsManager | secrets/env.py | No | None | YES |
| A2 | A | Normalize ZAI_BASE_URL→zai_api_base in harness loader | tests/e2e/dogfood/_secrets.py, .gitignore | No | None | YES |
| A3 | A | Add GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS opt-in flag | models/gateway.py | No | None | YES |
| A4 | A | Set non-zero cost rates on zai preset | models/provider_presets.py | No | None | YES |
| A5 | A | Persist cost_estimate + real tokens into benchmark_results | execution/engine.py, event_loop/benchmark.py | No | A4 | After A4 |
| A6 | A | Activate real graded scores (wire dead trace path) | event_loop/loop.py, benchmark.py, observability/recorder.py | No | A5 | After A5 |
| A7 | A | Build model autodiscovery GET /models | models/model_registry.py, routers/models.py | No | A4 | After A4 |
| A8 | A | Add skill_id column to benchmark_results + alembic migration | db/models.py, db/repository.py, event_loop/benchmark.py, alembic | YES (D-11 coord) | A6 | After A6 |
| A9 | A | Wire SpendLimiter into dispatch + persist spend_records | controllers/spend_limiter.py, execution/engine.py, db/repository.py, daemon.py | No | A5 | After A5 |
| A10 | A | Dogfood harness conftest + fixtures + gateway factory | tests/e2e/dogfood/__init__.py, conftest.py, _gateway.py | No | A1, A2 | After A1+A2 |
| A11 | A | Provider E2E shared conftest (probes + skip + builder) | tests/e2e/providers/__init__.py, conftest.py | No | A3 | After A3 |
| A12 | A | Add all e2e Makefile targets | Makefile | No | A10, A11 | After A10+A11 |
| B1 | B | Scenario 1: self-host meaningful change + git branch | tests/e2e/dogfood/test_self_host.py | No | A10 | Post-gate |
| B2 | B | Scenario 2: greenfield todo website + site tests | tests/e2e/dogfood/test_todo_website.py, _site.py | No | A10, B1 | Post-gate |
| B3 | B | Real daemon smoke: boots, healthz, todos | tests/e2e/dogfood/test_daemon_smoke.py | No | A10, A12 | Post-gate |
| B4 | B | zai basic connection + response assertion | tests/e2e/providers/test_zai_e2e.py | No | A11, A1, A2 | Post-gate |
| B5 | B | zai cost + weights non-vacuous assertions | test_zai_e2e.py (extend) | No | B4, A5, A6 | Post-gate after A5+A6 |
| B6 | B | zai autodiscovery assertions | test_zai_e2e.py (extend) | No | B4, A7 | Post-gate after A7 |
| B7 | B | ollama E2E: register+call+tokens | tests/e2e/providers/test_ollama_e2e.py | No | A3, A11 | Post-gate |
| B8 | B | vllm E2E: call+argv+optional spawn | tests/e2e/providers/test_vllm_e2e.py | No | A3, A11 | Post-gate |
| B9 | B | llama.cpp E2E: call+argv+optional spawn | tests/e2e/providers/test_llamacpp_e2e.py | No | A3, A11 | Post-gate |
| B10 | B | Slurm E2E: submit/poll/cancel (no SSRF dep) | tests/e2e/providers/test_slurm_e2e.py | No | A11 | Post-gate |
| B11 | B | Azure env-pointer: full SSRF path + metered billing | tests/e2e/providers/test_azure_e2e.py | No | A11, A5 | Post-gate after A5 |
| C1 | C | Azure full provision/teardown (opt-in, scheduled) | tests/e2e/providers/test_azure_provision_e2e.py | No | B11, A5, A6 | Scheduled |
