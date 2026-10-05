# Multi-Provider E2E Harness Design — local & cloud compute/model backends

Status: DESIGN (read-only investigation, 2026-06-18). No code shipped here.
Author: harness design pass.
Scope: meaningful E2E tests that exercise gludd talking to **ollama**, **vllm**,
**llama.cpp**, **slurm**, and **azure** (azure running vllm/ollama/llama.cpp/slurm).

This document is grounded in the actual gludd code, not aspiration. Every
"testable now" claim names the module that makes it real; every "needs an
adapter" claim names the gap that blocks it. The headline gap is in
[§2.1 — the SSRF guard blocks `localhost`](#21-blocker-1-ssrf-guard-rejects-loopback--rfc-1918-the-1-blocker), and it blocks *every* local backend, so read that first.

---

## 0. TL;DR for reviewers

- **What works today, end to end, against a real local server:** ollama, vllm,
  and llama.cpp — *because all three speak OpenAI-compatible `/v1`* and gludd's
  `ModelGateway` instantiates a LangChain `ChatOpenAI` against an arbitrary
  `base_url`. The model-call path is genuinely exercisable now.
- **What needs one small gludd change first:** the gateway resolves `base_url`
  through `is_safe_fetch_url()`, which is **https-only and denies `localhost` /
  `127.0.0.1` / RFC-1918**. A local `http://localhost:11434` base URL is rejected
  before the call is made. See [§2.1](#21-blocker-1-ssrf-guard-rejects-loopback--rfc-1918-the-1-blocker). This is the one change that unblocks
  the entire local-backend suite. Until it lands, local model-call E2E must
  inject the chat-model object directly (a real but lower-fidelity path).
- **Slurm** is testable now at the *dispatch* layer (submit → poll → cancel)
  against a real cluster or the Slurm REST API; `SlurmAdapter` already exists.
  The slurm→served-model loop reuses the local-model gap above.
- **Azure**: recommend the **env-pointer** approach for CI (point at an
  already-running Azure endpoint via `*_BASE_URL`); a **full-provision** test
  (terraform up a GPU VM, serve a model, tear down) is an opt-in,
  cost-gated, manually-run marker. gludd already has `TerraformGenerator` for
  Azure GPU instances and `DeploymentManager` for the lifecycle, but full
  provisioning in CI is slow and expensive — keep it opt-in.
- **Conditional skip is mandatory and uniform**: every test is gated by a
  backend-reachability check + an env var, and skips with a *clear reason* when
  the backend is absent, so CI stays green without any of these services.

---

## 1. What gludd already models (the abstractions the harness builds on)

Two layers matter. The harness asserts against both.

### 1.1 Model layer — `ModelGateway` / `ModelProfile` / `ProviderRegistry`

`src/general_ludd/models/gateway.py`

- `ModelProfile(BaseModel)` is the unit of "a model I can call". Relevant fields:
  - `provider: str = "openai"` (string, not enum — dynamically resolved)
  - `provider_package: str` / `provider_class_hint: str` (e.g. `"ChatOpenAI"`)
  - `model_name: str`
  - `api_base_alias: str | None` — a **secrets key**, resolved to the `base_url`
  - `credential_alias: str | None` — a secrets key, resolved to the API key
  - `cost_per_input_token` / `cost_per_output_token`, `run_budget_usd`,
    `api_metered`, `latency_class`, `quality_class`, `fallback_profiles`.
- `ModelGateway._invoke_and_bill(...)` (gateway.py ~233–350) is the call path:
  1. `registry.get_provider_class(provider_name)` → dynamically imports the
     LangChain class (`ChatOpenAI`, `ChatVLLM`, `ChatLlamaCpp`, …).
  2. resolves `credential_alias` → `api_key`.
  3. resolves `api_base_alias` → `base_url`, **then runs it through
     `is_safe_fetch_url()`** (SSRF guard) and raises if blocked.
  4. `chat_model = provider_cls(**init_kwargs)` and `chat_model.invoke(messages)`.
  5. bills: reads `usage_metadata`, coerces token counts (rejects bool/negatives),
     `cost = in*cost_per_input + out*cost_per_output`, calls
     `budget_guard.record_spend(cost)` and `health_tracker.record_success`.
- `ProviderRegistry` (`models/provider_registry.py`): `register_provider(name,
  package, class_hint)`, `get_provider_class(name)`, `is_installed(name)`.
- `provider_presets.py`: hardcoded provider table. **Today it only contains the
  hosted OpenAI-compatible providers** (openrouter, openai, anthropic, zai, groq,
  deepseek). There is **no `ollama` / `vllm` / `llamacpp` preset** — those are
  reachable only because they are OpenAI-compatible and you can point a generic
  `ChatOpenAI` profile at their `base_url`. (Adding presets is an optional
  ergonomic gap; see [§2.3](#23-gap-no-presets-for-local-backends-ergonomic-not-blocking).)
- `ModelRouter` (`models/router.py`): `resolve_role`, `resolve_by_quality`,
  `resolve_by_latency`. The harness can assert a backend's profile is selectable
  by role.

**Key consequence:** for the harness, "register a backend" == construct a
`ModelProfile` (provider `openai`, class `ChatOpenAI`), register the provider,
set the `api_base_alias` secret to the backend's `/v1` URL, and build a
`ModelGateway`. This is exactly the idiom in `tests/live/test_zai_*.py`
(`_build_zai_gateway()`), so the harness is a direct generalization of an
existing, passing pattern.

### 1.2 Compute layer — instances, endpoints, utilization, slurm

- `src/general_ludd/infra/compute.py` — `ComputeProvider` enum (AWS, Azure, GCP,
  RunPod, Vast.ai, Lambda Labs, Modal, CoreWeave, DigitalOcean, Oracle),
  `GPUType`, `InferenceEngine`, and `ComputeInstance` (provider, status,
  ip_address, port, gpu_type, `endpoint_url`, `cost_incurred`). **There is no
  k8s and no vmware provider** (the discovery skill calls these out as net-new).
- `src/general_ludd/infra/utilization.py` — `UtilizationTracker` +
  `ComputeEndpoint`. `register_endpoint(endpoint_id, url, model, **kwargs)`,
  `route_task(task_id, model)`, `release_task`, `record_tokens`,
  `get_utilization_report`. This is gludd's record of "a backend that can serve
  a model", and **the harness asserts that a discovered backend lands here**.
- `src/general_ludd/infra/local_inference.py` — `LocalInferenceManager` +
  `LocalServerConfig(engine: "vllm"|"llamacpp"|"slurm", model_path, model_name,
  host, port, ...)`. `create_server()` sets `endpoint_url =
  http://{host}:{port}/v1`. `start_server()` spawns the real process (vllm/
  llamacpp) or submits a Slurm job. **This module is how gludd *starts* a local
  server**; the existing tests mock `asyncio.create_subprocess_exec` and assert
  on argv. A real-process E2E variant is new ground (see [§4.2](#42-vllm)).
- `src/general_ludd/infra/slurm.py` — `SlurmAdapter` (`submit`, `status`,
  `cancel`, `available`, `list_jobs`), dual-mode: **local CLI** (`sbatch`/
  `sacct`/`scancel`) and **REST** (`api_url` → `/slurm/v0.0.40/...`). Heavily
  injection-hardened. `available()` is the natural reachability probe for the
  skip gate.
- `src/general_ludd/infra/terraform.py` — `TerraformGenerator` (GCP/AWS/Azure GPU
  IaC). `src/general_ludd/infra/deployment.py` — `DeploymentManager` (ephemeral
  instance lifecycle). These power the *full-provision* Azure path.
- `src/general_ludd/pricing_intel/catalog.py` — `PricingCatalog.cheapest_compute()`
  for cost/weight assertions.

---

## 2. Gaps that block (or limit) real E2E — be honest

### 2.1 BLOCKER 1: SSRF guard rejects loopback / RFC-1918 (the #1 blocker)

`models/gateway.py` resolves `api_base_alias` and gates it:

```python
from general_ludd.security.auth import is_safe_fetch_url
if not is_safe_fetch_url(base_url):
    raise ValueError(f"SSRF guard: refusing blocked api_base_alias URL {base_url!r} ...")
```

`is_safe_fetch_url` delegates to `security/ssrf.py`, which is **https-only**
(`auth = {"https"}`) and **denies** any host in `BLOCKED_HOST_NAMES`
(`localhost`, `ip6-localhost`, …) or any loopback / link-local / private /
reserved IP literal (`127.0.0.1`, `::1`, `10.*`, `192.168.*`, `169.254.*`).

**Therefore a local backend URL like `http://localhost:11434/v1` or
`http://127.0.0.1:8000/v1` is rejected by the gateway before any HTTP call.**
This blocks the *full-fidelity* gateway path for ollama, vllm, and llama.cpp —
i.e. all three local model backends.

**Resolution options (pick one; recommend A):**

- **(A) Recommended — an explicit, opt-in local-base-url allowance.** Add a
  narrowly-scoped escape hatch the gateway honors *only* when an operator opts in,
  e.g. an env flag `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` (or a per-profile
  `allow_local_base_url: bool` on `ModelProfile`) that, when set, permits `http`
  + loopback/RFC-1918 hosts for the `api_base_alias` resolution path *only*. This
  keeps the default fail-closed posture (the SSRF guard still protects the skill
  fetcher and connectors) while making local model serving — the entire point of
  ollama/vllm/llama.cpp support — actually usable. This is a real product gap,
  not just a test gap: a user running ollama on their laptop hits the same wall.
  The harness's gateway-path tests are gated on this flag and skip with a clear
  reason when it is unset.
- **(B) Lower-fidelity fallback that works today (no gludd change).** Skip the
  `api_base_alias` resolution entirely by constructing the LangChain
  `ChatOpenAI(base_url=..., api_key="not-needed")` object yourself and passing it
  to the gateway as a pre-built chat model (or by stubbing the registry's
  `get_provider_class` to return a factory bound to the local URL). This exercises
  the *real* gateway billing/cache/health logic and a *real* HTTP call to the
  real local server — it just bypasses the SSRF check on the base URL. Honest
  characterization: this is a faithful E2E of the call+bill path but **not** of
  gludd's own URL-resolution path. Use it as the interim default so the suite is
  meaningful before (A) ships; mark the bypass clearly in the test.

The doc below assumes (A) lands eventually and documents both gating flags so the
same test files work in either mode.

### 2.2 LIMIT: Local model-call uses generic ChatOpenAI, not a native adapter

There is **no native ollama adapter** and the vllm/llama.cpp story is
"OpenAI-compatible `/v1` via ChatOpenAI", not the LangChain community
`ChatVLLM` / `ChatLlamaCpp` classes (those are referenced as class *hints* but
the presets and the realistic path are OpenAI-compat). This is **fine for E2E**
— ollama (`/v1`), vllm (`/v1`), and llama.cpp server (`/v1`) all expose
OpenAI-compatible chat completions, so a `ChatOpenAI`-backed profile is a true
exercise of the backend. Flag only: ollama's *native* `/api/generate` and
`/api/tags` (model discovery) endpoints are **not** spoken by gludd at all; if we
want autodetect-models-from-ollama as a feature, that is a new adapter (see
[§4.1](#41-ollama)).

### 2.3 GAP: no presets for local backends (ergonomic, not blocking)

`provider_presets.py` has no `ollama`/`vllm`/`llamacpp` entry. Not a blocker
(generic `openai` provider + explicit `api_base_alias` works), but adding presets
(`api_base_url: http://localhost:11434/v1`, `provider_class: ChatOpenAI`,
`credential_env_var: <none / dummy>`) would make `list_configured_providers()`
and role wiring nicer. Optional follow-up; the harness does **not** depend on it.

### 2.4 GAP: no compute *discovery* wiring for local/slurm/azure

`UtilizationTracker` tracks endpoints you *register*, but nothing *discovers*
them. The `compute-resource-discovery` skill is the home for "probe ollama for
loaded models", "ask slurm what partitions/GPUs exist", "list azure VMs". For the
harness, "discovery" is therefore asserted at the level the code supports today:
the test probes the backend itself (HTTP `/v1/models`, `sinfo`, azure CLI) and
asserts gludd **registers** the result into `UtilizationTracker` /
`LocalInferenceManager`. True auto-discovery is a separate feature; the harness
tests the *register + route + bill* spine, which is real.

### 2.5 Summary table — testable now vs needs change

| Backend | Model call (real server) | Compute discovery / dispatch | Blocking gap |
|---|---|---|---|
| ollama    | YES via ChatOpenAI `/v1` — gated on §2.1 fix or §2.1(B) bypass | partial: probe `/v1/models`, register into UtilizationTracker | §2.1 SSRF; native `/api/*` discovery = new adapter |
| vllm      | YES via ChatOpenAI `/v1` — same gating | YES: `LocalInferenceManager` start (real-process variant new) + register | §2.1 SSRF |
| llama.cpp | YES via ChatOpenAI `/v1` — same gating | YES: `LocalInferenceManager(engine=llamacpp)` + register | §2.1 SSRF |
| slurm     | model loop reuses local gap | YES now: `SlurmAdapter` submit/poll/cancel (CLI or REST) | only the served-model loop inherits §2.1 |
| azure     | YES via env-pointer `/v1` (treat as remote OpenAI-compat — SSRF allows it) | env-pointer: YES; full-provision: YES via Terraform/DeploymentManager but slow/costly | full-provision is opt-in only (cost) |

Note the asymmetry: **azure env-pointer model calls are *not* blocked by §2.1**,
because a public Azure endpoint is https + a global host, which the SSRF guard
permits. The SSRF wall is specifically a *local-backend* problem.

---

## 3. Harness layout, shared fixtures, and the skip contract

```text
tests/e2e/providers/
  conftest.py                  # shared probes, skip helpers, gateway builder
  test_ollama_e2e.py
  test_vllm_e2e.py
  test_llamacpp_e2e.py
  test_slurm_e2e.py
  test_azure_e2e.py            # env-pointer (CI-friendly)
  test_azure_provision_e2e.py  # full terraform provision (opt-in, costly)
```

### 3.1 The skip contract (uniform across all files)

Two independent gates, ANDed. A test runs only if **both** pass; otherwise it
skips with a message naming exactly what was missing:

1. **Configured** — the env var pointing at the backend is set
   (`OLLAMA_BASE_URL`, `VLLM_BASE_URL`, …). Absent ⇒
   `skip("OLLAMA_BASE_URL not set — set it to run the ollama E2E")`.
2. **Reachable** — a cheap liveness probe succeeds (HTTP GET `/v1/models` with a
   2s timeout; `SlurmAdapter.available()`; `az account show` rc==0). Unreachable
   ⇒ `skip("ollama at $OLLAMA_BASE_URL not reachable: <reason>")`.

This is the established gludd idiom (module-level env helper feeding
`@pytest.mark.skipif`, per `tests/integration/test_hf_model_integration.py` and
`tests/live/test_zai_*.py`). The reachability probe is added on top so a *stale*
env var (service died) skips cleanly instead of erroring. **Net effect: with none
of these services present, the whole `tests/e2e/providers/` tree skips and CI
stays green.**

> Ratchet gotcha (from `tests/conftest.py`): a test that is *expected to fail*
> must be listed in `config/ratchet.yml` or the suite fails. These tests must
> never xfail — they **skip** when the backend is absent and **pass** when
> present. Do not add them to ratchet.yml.

### 3.2 conftest.py — shared helpers (design, not final code)

```python
# tests/e2e/providers/conftest.py
import os, httpx, pytest
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry

ALLOW_LOCAL = os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"  # §2.1(A)

def _http_alive(url: str, path: str = "/v1/models", timeout: float = 2.0) -> tuple[bool, str]:
    try:
        r = httpx.get(url.rstrip("/") + path, timeout=timeout)
        return (r.status_code < 500, f"HTTP {r.status_code}")
    except Exception as exc:                      # noqa: BLE001 — probe is best-effort
        return (False, f"{type(exc).__name__}: {exc}")

def require_backend(env_var: str, path: str = "/v1/models"):
    """Return base_url or pytest.skip with a precise reason (config AND reachability)."""
    url = os.environ.get(env_var)
    if not url:
        pytest.skip(f"{env_var} not set — set it to run this provider E2E")
    ok, why = _http_alive(url, path)
    if not ok:
        pytest.skip(f"backend at {url} ({env_var}) not reachable: {why}")
    return url

def build_local_gateway(base_url: str, model: str, *, profile_id: str):
    """Build a real ModelGateway pointed at a local OpenAI-compatible /v1 server.

    Path (A): set the api_base_alias secret + GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS.
    Path (B) fallback: inject a pre-built ChatOpenAI to bypass the SSRF base-url
    check while still exercising the real call+bill path. See §2.1.
    """
    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain-openai", "ChatOpenAI")
    profile = ModelProfile(
        model_profile_id=profile_id, provider="openai",
        provider_class_hint="ChatOpenAI", model_name=model,
        api_base_alias=f"{profile_id}_base", credential_alias=f"{profile_id}_key",
        cost_per_input_token=0.0, cost_per_output_token=0.0,  # local = free; assert $0
        api_metered=False, enabled=True, roles=["e2e"],
    )
    secrets = _EnvSecrets({f"{profile_id}_base": base_url, f"{profile_id}_key": "local-no-key"})
    return ModelGateway(profiles=[profile], provider_registry=registry, secrets_manager=secrets)
```

`_EnvSecrets` is a 3-line in-memory `resolve(alias)->str|None` stub matching the
`_SecretsResolver` Protocol in `gateway.py`. (Path B substitutes a registry whose
`get_provider_class` returns a `partial(ChatOpenAI, base_url=base_url)` so the
SSRF check is never reached — used only while §2.1(A) is unshipped.)

---

## 4. Per-backend test designs

Each section gives: **env vars**, **skip rule**, **what it asserts**, and **what
is real vs mocked**.

### 4.1 ollama

`tests/e2e/providers/test_ollama_e2e.py`

- **Env:** `OLLAMA_BASE_URL` (e.g. `http://localhost:11434/v1`),
  `OLLAMA_MODEL` (default `llama3.2:1b` — small, fast).
- **Skip:** `require_backend("OLLAMA_BASE_URL")` (config + GET `/v1/models`
  reachable). Gateway-path tests additionally `skipif not ALLOW_LOCAL` with
  reason "set GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1 (see §2.1) to exercise the real
  gateway base-url resolution".
- **Asserts (meaningful, in order):**
  1. **Backend registers** — `UtilizationTracker.register_endpoint("ollama-e2e",
     url, model=OLLAMA_MODEL)` then assert it appears in `list_endpoints()` and
     `route_task("t1", model=OLLAMA_MODEL)` routes to it (real routing logic).
  2. **Autodetect a model** — GET `OLLAMA_BASE_URL/v1/models`, assert
     `OLLAMA_MODEL` (or any model) is present; this is the "discovery" gludd can
     do today (probe the OpenAI-compat list endpoint). *Stretch / new-adapter:*
     also hit native `/api/tags` and note in a comment that gludd has no native
     ollama discovery adapter yet (§2.2).
  3. **Completes a small task** — `gateway.call_model("ollama-e2e",
     [{"role":"user","content":"Reply with the single word: pong"}])`. Assert
     `response.content` is non-empty and contains "pong" (case-insensitive,
     substring — small models wander). This is a **real HTTP call to a real
     ollama server** through the **real gateway** (empty-200 guard, billing,
     health-reset all execute).
  4. **Records cost/weight** — assert `response.cost_estimate == 0.0` (local,
     zero per-token cost) and that `usage_metadata` carried token counts (proves
     the bill path ran). Call `tracker.record_tokens("ollama-e2e",
     out_tokens)` and assert the endpoint's `total_tokens`/`total_requests`
     advanced (the "weight" gludd tracks for utilization).
- **Real vs mocked:** server real, HTTP real, gateway real, billing real. Nothing
  mocked. Only the *base-url SSRF check* is either honored (path A) or bypassed
  (path B) — called out in the test docstring.

### 4.2 vllm

`tests/e2e/providers/test_vllm_e2e.py`

- **Env:** `VLLM_BASE_URL` (e.g. `http://localhost:8000/v1`), `VLLM_MODEL`
  (e.g. `Qwen/Qwen2.5-0.5B-Instruct`). Optional `VLLM_E2E_SPAWN=1` to opt into
  the real-process spawn variant.
- **Skip:** `require_backend("VLLM_BASE_URL")`; spawn variant additionally
  `skipif not VLLM_E2E_SPAWN` and `skipif shutil.which("vllm") is None`.
- **Asserts:**
  1. **Same model-call + register + bill flow as ollama** (vllm is OpenAI-compat
     `/v1`), reusing `build_local_gateway` and `UtilizationTracker`.
  2. **`LocalInferenceManager` config path** (unique to vllm/llamacpp): construct
     `LocalServerConfig(engine="vllm", model_name=VLLM_MODEL, host, port)`,
     `mgr.create_server(cfg)`, assert `server.endpoint_url ==
     http://{host}:{port}/v1` and that `_build_command(cfg)` yields
     `["vllm","serve",MODEL,"--host",host,"--port",str(port)]` (validates gludd's
     own spawn-argv construction — the thing the existing unit tests mock).
  3. **Real-process spawn variant (opt-in, `VLLM_E2E_SPAWN=1`):**
     `await mgr.start_server(sid)` actually launches vllm, poll `/v1/models`
     until ready (bounded ~120s with a clear timeout-skip), run the small task
     against the *spawned* server, then `await mgr.stop_server(sid)` and assert
     the process is reaped. This is the only test that exercises gludd's real
     subprocess lifecycle end to end. Heavy — hence opt-in.
- **Real vs mocked:** model-call + register + bill real; argv-construction real;
  spawn variant fully real (and slow). Default (non-spawn) variant talks to an
  already-running vllm.

### 4.3 llama.cpp

`tests/e2e/providers/test_llamacpp_e2e.py`

- **Env:** `LLAMACPP_BASE_URL` (e.g. `http://localhost:8080/v1` for
  `llama-server`, or `:8000/v1` for `python -m llama_cpp.server`),
  `LLAMACPP_MODEL` (model name as the server reports it), optional
  `LLAMACPP_MODEL_PATH` (gguf path) + `LLAMACPP_E2E_SPAWN=1`.
- **Skip:** `require_backend("LLAMACPP_BASE_URL")`; spawn variant additionally
  `skipif not LLAMACPP_E2E_SPAWN`.
- **Asserts:**
  1. **Model-call + register + bill flow** (llama.cpp server is OpenAI-compat
     `/v1`). Note: if pointed at the raw `/completion` endpoint instead of `/v1`,
     gludd cannot drive it (no completion adapter) — the test asserts the `/v1`
     chat path and documents that `/completion`-only servers are unsupported.
  2. **`LocalInferenceManager(engine="llamacpp")` argv** — assert
     `_build_command` produces `["python3","-m","llama_cpp.server","--model",
     PATH,"--host",host,"--port",str(port),"--n_gpu_layers",...,"--n_ctx",...]`,
     validating gludd's llamacpp launch construction.
  3. **Spawn variant (opt-in):** start the real `llama_cpp.server`, wait for
     `/v1/models`, run the task, stop. Bounded + clear timeout-skip.
- **Real vs mocked:** as vllm — call/register/bill real, argv real, spawn real
  when opted in.

### 4.4 slurm

`tests/e2e/providers/test_slurm_e2e.py`

Slurm is the one backend with a **real dispatch path that is fully testable
today** (no §2.1 dependency for the job-control assertions).

- **Env (two modes):**
  - **CLI mode:** run on a node with `sbatch`/`sacct`/`scancel` on PATH;
    `SLURM_E2E=1` to opt in (so a dev box that happens to have slurm tools
    doesn't auto-run cluster jobs).
  - **REST mode:** `SLURM_REST_URL` (e.g. `http://controller:6820`) +
    `SLURM_REST_TOKEN`. (Per §2.1 the SLURM REST URL is *not* run through the
    model SSRF guard — `SlurmAdapter` makes its own httpx calls — so a private
    controller host is fine here.)
- **Skip:** `adapter = SlurmAdapter(api_url=os.environ.get("SLURM_REST_URL"),
  auth_token=...)`; `if not adapter.available(): skip("slurm not reachable
  (CLI sbatch missing or REST /ping failed)")`. CLI mode also
  `skipif not SLURM_E2E`.
- **Asserts (real submit→poll→cancel/complete loop):**
  1. **Discovery/availability** — `adapter.available()` is True;
     `adapter.list_jobs()` returns a list (proves query path works).
  2. **Dispatch a tiny job** — `job_id = adapter.submit(command="echo
     gludd-e2e && sleep 5", job_name="gludd-e2e", time_limit="00:02:00")`.
     Assert `job_id` matches `^[0-9]` (real id, parsed from sbatch/REST).
  3. **Poll to a terminal state** — loop `adapter.status(job_id)` with a bounded
     deadline (~3 min, clear timeout-skip), assert it transitions through
     PENDING/RUNNING and ends COMPLETED (and for the echo job `exit_code == 0`).
  4. **Cancel path** — submit a second longer job, `adapter.cancel(job_id)`,
     assert subsequent status is CANCELLED (or that a re-status doesn't error).
  5. **gludd-served-model-on-slurm (opt-in, `SLURM_SERVE_E2E=1`):** use
     `LocalInferenceManager` with `LocalServerConfig(engine="slurm",
     model_name=..., host, port)` → `start_server` submits an sbatch job that
     runs `python -m llama_cpp.server`. Assert a `local_server_submitted_slurm`
     event with a `slurm_job_id`. The actual model call against the slurm-served
     endpoint reuses the local model-call path and therefore inherits §2.1; gate
     it behind `ALLOW_LOCAL` + reachability of the served `/v1` URL
     (`SLURM_SERVED_BASE_URL`). This loop is the genuine "compute-resource
     discovery + dispatch + model call" story for HPC, and it is honest about the
     one piece (the served-URL SSRF allowance) it depends on.
- **Real vs mocked:** submit/poll/cancel against a real scheduler (CLI or REST) —
  fully real. No mocking. Heavy; strongly opt-in.

### 4.5 azure — env-pointer (CI-friendly, RECOMMENDED default)

`tests/e2e/providers/test_azure_e2e.py`

The pragmatic Azure path: point gludd at an **already-running** Azure endpoint
(an Azure ML / VM hosting vllm/ollama/llama.cpp behind an OpenAI-compatible
`/v1`, or Azure OpenAI itself). No provisioning, no teardown, cheap, CI-safe.

- **Env:** `AZURE_BASE_URL` (the `/v1` endpoint), `AZURE_MODEL`,
  `AZURE_API_KEY` (or `AZURE_OPENAI_API_KEY`). Optionally `AZURE_BACKEND_KIND ∈
  {vllm,ollama,llamacpp,azure_openai}` purely for assertion labeling.
- **Skip:** `require_backend("AZURE_BASE_URL")` — config + reachability. Because
  the endpoint is remote https + a public host, **the SSRF guard permits it**, so
  this test exercises the *full* gateway base-url-resolution path with **no
  §2.1 dependency** — it is the most faithful gateway E2E in the suite.
- **Asserts:** identical shape to ollama (register into `UtilizationTracker`;
  list/route; small task returns "pong"; `cost_estimate` computed from the
  profile's per-token costs — here set the profile's real Azure prices and assert
  `cost > 0`, proving the metered-billing path; `record_spend` reflected in the
  budget guard). For vllm/ollama/llamacpp-on-azure, additionally GET
  `/v1/models` and assert `AZURE_MODEL` is listed (discovery).
- **Real vs mocked:** end to end real, including gludd's own SSRF-checked URL
  resolution and metered billing. This is the strongest "gludd talks to a backend
  running vllm/ollama/llama.cpp on azure" assertion and it is CI-runnable today.

### 4.6 azure — full provision (opt-in, costly, manual)

`tests/e2e/providers/test_azure_provision_e2e.py`

The literal "azure provisions a VM running vllm/ollama/llama.cpp/slurm" ask.
**Cost/feasibility tradeoff:** a GPU VM (e.g. `Standard_NC*`) costs real money per
hour, provisioning + model download + serve readiness is several minutes, and a
failed teardown leaks spend — this is **not** CI-appropriate. Make it a
single, clearly-marked, opt-in test.

- **Marker/env:** `@pytest.mark.azure_provision` (registered in pytest.ini) +
  hard gate `skipif os.environ.get("AZURE_PROVISION_E2E") != "1"`. Requires
  `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, azure creds, and a
  `GLUDD_E2E_MAX_SPEND_USD` ceiling the test refuses to exceed.
- **Design (uses existing gludd machinery):**
  1. `TerraformGenerator` (infra/terraform.py) renders an Azure GPU instance with
     a cloud-init/startup that installs and serves the chosen engine
     (`AZURE_PROVISION_ENGINE ∈ {vllm,ollama,llamacpp,slurm}`).
  2. `DeploymentManager` (infra/deployment.py) provisions it, yielding a
     `ComputeInstance(provider=ComputeProvider.AZURE, endpoint_url=...)`.
  3. **try/finally with guaranteed teardown** — the `finally` always calls
     `DeploymentManager.destroy(...)`; the test also asserts teardown ran (no
     leaked instance) and that `cost_incurred`/`PricingCatalog.cheapest_compute`
     stayed under `GLUDD_E2E_MAX_SPEND_USD`.
  4. Once `endpoint_url` is reachable, run the §4.5 model-call + register + bill
     assertions against the freshly-provisioned `/v1` (its public https host
     passes the SSRF guard).
  5. For `engine=slurm`, additionally run the §4.4 submit/poll/cancel loop via
     `SlurmAdapter(api_url=<provisioned controller REST>)`.
- **Honesty:** this is the highest-fidelity, highest-cost test. It validates the
  *whole* provision→serve→discover→dispatch→bill→destroy spine. Keep it manual
  and budget-guarded. Recommend running it on a cadence (nightly/weekly) outside
  the PR gate, never in the per-commit suite.

#### Live progress and timeout-safe teardown

The 2026-08-01 live run confirmed that Azure can spend several minutes creating
the Container Apps environment before an endpoint exists. A prior run reached a
successful HTTP inference but exposed two lifecycle gaps: the returned instance
still reported zero cost, and the suite timeout interrupted `terraform destroy`.
`DeploymentManager` therefore treats deployment progress as a first-class event
stream: command start, sanitized output lines, completion/failure, deploy, and
destroy events are published while Terraform is still running. The returned
Azure instance attributes elapsed provision time at the same tier rate used by
`DeployStrategist`, and cancellation is deferred until destroy has finished.
Signal/atexit cleanup uses the initialized Terraform state directly rather than
trying to start a nested asyncio loop.

This is also a long-lived platform concern, not only a test-runner concern:

- A [2025 Microsoft Q&A report](https://learn.microsoft.com/en-us/answers/questions/5570112/container-app-environment-stuck-in-waiting-provisi)
  describes a Container Apps environment stuck in `Waiting`, returning 409 while
  an earlier operation remains in progress. Live events must surface that state
  before the entire E2E deadline expires.
- A [2024 Microsoft Q&A thread](https://learn.microsoft.com/en-us/answers/questions/1660320/cannot-delete-container-app-environment)
  records environment deletion as asynchronous and potentially slow, including
  hidden builder dependencies. Cleanup success must come from Terraform/Azure
  state reconciliation, not merely from issuing a delete request.
- Microsoft's [Container Apps troubleshooting guide](https://learn.microsoft.com/en-us/azure/container-apps/troubleshooting)
  documents indefinitely provisioning revisions and environments scheduled for
  deletion that need dependency cleanup. The live harness consequently preserves
  state for retry when destroy fails instead of declaring cleanup complete.

---

## 5. Make targets (proposed — Bash here is make-only)

Add to the Makefile so the harness is invokable per the repo policy:

- `make test-e2e-providers` — run the whole `tests/e2e/providers/` tree (skips
  everything not configured; CI-green by default).
- `make test-e2e-ollama` / `-vllm` / `-llamacpp` / `-slurm` / `-azure` — single
  backend, injecting that backend's env vars.
- `make test-e2e-azure-provision` — sets `AZURE_PROVISION_E2E=1` and the spend
  ceiling; intended for manual/scheduled use, NOT the PR gate.
- A `make test-e2e-providers-local` convenience that sets
  `GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS=1` and points the three local URLs at
  default ports, for a dev who has ollama/vllm/llamacpp running locally.

These mirror the existing `make test-live-zai` / `make test-integration` style.

---

## 6. Recommendations & sequencing

1. **Ship the §2.1(A) local-base-url allowance first.** It is a real product gap
   (a user running ollama locally cannot use it through the gateway today) and it
   is the single change that turns ollama/vllm/llamacpp from "bypass-only" E2E
   into full-fidelity E2E. Small, well-scoped, fail-closed by default.
2. **Land the env-pointer files now** (`test_ollama_e2e`, `test_vllm_e2e`,
   `test_llamacpp_e2e`, `test_slurm_e2e`, `test_azure_e2e`) using §2.1(B) bypass
   as the interim local path. They are CI-green when nothing is configured and
   become meaningful the moment a backend URL is exported. Slurm and azure-env
   are already full-fidelity with no §2.1 dependency.
3. **Keep azure full-provision opt-in** (`test_azure_provision_e2e`) behind a
   marker + spend ceiling + guaranteed teardown; run it scheduled, not per-PR.
4. **Follow-ups (not blockers):** add local-backend presets (§2.3); add a native
   ollama discovery adapter for `/api/tags` (§2.2); wire real discovery into the
   `compute-resource-discovery` skill so "autodetect models" is a gludd feature
   the harness can assert against directly rather than via raw `/v1/models`
   probes (§2.4).

Every test above skips with a precise, human-readable reason when its backend is
absent, so adding this entire tree leaves the default `make test` / CI gate green
with no external services running.
