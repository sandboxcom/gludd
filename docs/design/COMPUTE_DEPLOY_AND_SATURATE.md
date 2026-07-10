# Spec: Deploy → Serve → Discover → Register → Saturate self-hosted inference (2026-07-10)

**User directive:** gludd must be able to actually deploy Terraform compute, run
vLLM/llama.cpp serving an OpenAI-compatible endpoint on it, discover and use that
endpoint, and keep the deployed GPU **filled with work and optimally processing** —
end-to-end, or fully specced to do so when the spec is implemented.

**Verdict:** the two ends are BUILT and real; the middle wiring is the gap. This doc
specs the missing pieces as two work items: **D-DEPLOY** (make a provisioned box a
routable provider) and **D-SATURATE** (keep it optimally filled). Grounded in source
(line cites current-tree at authoring; re-Read before editing).

---

## Part A — D-DEPLOY: deploy → serve → discover → register → teardown

### Built today (do NOT rebuild)
- **Terraform apply is real:** `infra/deployment.py` `DeploymentManager.deploy()` (106)
  runs `terraform init` (120) / `apply -auto-approve` (121) / `output -json` (123) via
  `asyncio.create_subprocess_exec` (184), parses outputs (`_parse_outputs`:203), reads
  `instance_ip`/`endpoint_url` (126,135), persists a `DeploymentRecord`; `destroy()` (151)
  refuses unknown ids. Providers (aws/gcp/azure/runpod/vast/lambda/modal/coreweave/do/
  oracle/vsphere) emit module stacks with `base_url`/`endpoint_url` outputs (terraform.py).
- **Serve launcher (local) is real + health-gated:** `infra/local_inference.py`
  `_build_command` (347) → `vllm serve … --host --port` (363) or `python3 -m
  llama_cpp.server` (366); `start_server` (162) + `_wait_for_ready` polls `/health` (207);
  endpoint = `http://host:port/v1` (147). `_validate_host` (56) blocks non-loopback bind
  by default. Remote serve cmd (`_engine_serve_cmd`:96, `docker run vllm-openai --host
  0.0.0.0 --port 8000`) is baked into cloud-init, but only on the vSphere path.
- **Runtime provider registration is real:** `models/gateway.py` `add_profile(model_id,
  provider, model, api_key_env, api_base_alias, **kwargs)` (1735) creates an enabled
  `ModelProfile`, fires hot-reload; `remove_profile` (1764); `call_model_by_role` (1561)
  routes. `UtilizationTracker.register_endpoint` (utilization.py:80).

### The gaps (what D-DEPLOY builds)
1. **Nothing wires deploy → register.** `routers/compute.py admin_compute_deploy` (139)
   calls `deploy()` but never `util.register_endpoint` nor `gateway.add_profile` — the
   deployed box is invisible to routing (`add_profile` callers: only routers/models.py:157
   + hot_reloader; `register_endpoint` callers: only routers/compute.py:125).
2. **SSRF guard BLOCKS the private endpoint (critical).** The gateway resolves
   `api_base_alias` through the secrets store then calls `is_safe_fetch_url(base_url)`
   (gateway.py:510/827) → `is_url_blocked(url, scheme_allowlist={"https"})` (auth.py:176)
   which is **https-only + denies loopback/link-local/RFC-1918/metadata**. A discovered
   `http://10.x:8000/v1` is rejected on both scheme and private-host. No host-allowlist
   override exists.
3. **`api_base_alias` is a SECRETS-STORE KEY, not a URL** — the URL must be written to the
   secrets store under an alias first, then the profile points at that alias.
4. **`allowed_cidr` (default `127.0.0.1/32`, compute.py:78) is threaded into the module
   ONLY on vSphere** (terraform.py:672); aws/gcp/azure/etc omit it while the serve cmd
   binds `--host 0.0.0.0` → world-exposed unauthenticated endpoint on those providers.

### D-DEPLOY spec items
- **D-DEPLOY.1 — Remote readiness probe** (new `infra/endpoint_registrar.py::probe_ready`):
  after deploy, poll `GET {base_url}/models` (fallback `/health`) 5s-timeout/2s-interval up
  to `serve_timeout` (default 600s for cold boot + model pull); ready ⇔ 200 + non-empty
  `data[]`; capture `data[0].id` as `served_model`. On timeout → `DeploymentRecord.state=
  "unhealthy"`, surface in response, honor `teardown_on_unhealthy` (fix-means-repair: stop
  the spend). Mirror `local_inference._wait_for_ready` but for the remote URL.
- **D-DEPLOY.2 — Registrar** (new): on ready — (a) `util.register_endpoint(instance_id,
  base_url, served_model, gpu_type, gpu_count, max_concurrent)`; (b) `secrets.put(
  f"deploy_{id}_base", base_url)` + `secrets.put(f"deploy_{id}_key", api_key or "sk-local-
  dummy")`; (c) `gateway.add_profile(model_id=f"deploy-{id}", provider="openai",
  model=served_model, api_key_env=f"deploy_{id}_key", api_base_alias=f"deploy_{id}_base",
  role_names=req.roles, enabled=True, api_metered=False, cost_per_*_token=0.0,
  trusted_deploy_origin=True)`; (d) record the linkage on the `DeploymentRecord`. Wire in
  `admin_compute_deploy` after `deploy()` succeeds, gated on `req.register` (default True).
- **D-DEPLOY.3 — SSRF trusted-origin allowance (HARDENED — adversarial review found the
  naive version fully bypassable).** The idea: skip `is_safe_fetch_url` for an endpoint
  gludd itself provisioned. The naive "add `trusted_deploy_origin` to `ModelProfile` + skip
  if host == recorded IP" is EXPLOITABLE and must NOT be built as-is. Required hardening:
  1. **Trust is NOT a `ModelProfile` field.** `gateway.add_profile` passes through any kwarg
     in `ModelProfile.model_fields`, and `model_routing.yml` is hot-reloaded unschema'd — so
     a `trusted_deploy_origin` field is forgeable by any `add_profile`/config caller. Store
     deploy-trust in a **registrar-owned side table** `ModelGateway._trusted_deploy: dict[
     profile_id, ip]`, populated ONLY by a dedicated `add_deploy_profile(...)` (no generic
     `**kwargs`); `add_profile` must strip/reject any `trusted_deploy_origin` kwarg.
  2. **The trusted IP comes from the authoritative registry by profile_id, never carried on
     the profile** (else flag + compare-target are co-forgeable). Gateway looks it up via
     `DeploymentManager.get_deployment_for_profile(profile_id)` (injected dep) or the
     side table; the profile may carry only an opaque `deploy_instance_id`.
  3. **Re-deny metadata/loopback even for trusted origins.** At `deploy()` record time,
     `ipaddress.ip_address(instance_ip)` (reject non-literal) and refuse to register if it's
     loopback / link-local / `169.254.169.254` / `100.100.100.200` — the exception covers
     "my own 10.x box", NEVER IMDS or the daemon's own listener. The gateway skip-path
     re-checks this too.
  4. **Literal-IP compare only, no DNS.** Compare `urlsplit(base_url).hostname ==
     record.ip_address` as literal strings (matching ssrf.py's no-resolve hang-safety
     contract). Record/compare the bare IP (`instance_ip`), never a hostname/`endpoint_url`
     (DNS-rebind). If the alias is a hostname, fall back to the normal guard (which rejects).
  5. **Reserve the deploy namespace + no clobber.** `trusted_deploy_origin` allowed ONLY on
     `model_id` matching the reserved `deploy-{id}` prefix; the registrar refuses to
     overwrite an existing non-deploy profile (else an attacker re-adds a real provider's
     `model_profile_id` with trust + a hostile base_url → redirects that profile's REAL
     `credential_alias` to their server = SSRF **and** credential exfil). Only the
     registrar-issued `deploy_{id}_key` alias may pair with a trusted profile.
  6. **One shared helper.** Implement the check once — `_resolve_base_url_or_raise(profile,
     job_secrets, profile_id)` used by BOTH `get_chat_model` (505-515) and `_invoke_and_bill`
     (827-834) — never duplicated inline (ssrf.py's own docstring: duplication was a past
     drift bug). Caller-supplied aliases stay fully guarded.
- **D-DEPLOY.4 — Serve binding/ingress.** Thread `allowed_cidr` into every module block
  that omits it today (terraform.py aws/gcp/azure/runpod/vast/generic; only vsphere:672 has
  it). The vllm-server module MUST restrict ingress on :8000 to `allowed_cidr`; deploy
  precheck (routers/compute.py:178) MUST fail-closed if the SG is `0.0.0.0/0` and the
  endpoint is unauthenticated, unless explicitly forced.
- **D-DEPLOY.5 — Teardown deregistration.** In `admin_compute_destroy` (315) before
  `mgr.destroy`: `gateway.remove_profile(profile_id)`, `util.unregister_endpoint(id)`,
  `secrets.delete` both aliases.
- **Health/liveness:** ongoing liveness via existing `DeploymentHealthChecker` + the
  circuit breaker in `call_model_by_role` (1575) — an unhealthy deploy profile opens its
  breaker and role routing fails over via the existing fallback chain.
- **Tests (mocked, no real cloud/GPU):** registrar happy-path (fake deploy → mocked
  `/v1/models` 200 → asserts register_endpoint + 2 secrets + add_profile with
  trusted_deploy_origin); SSRF allowance (registered profile's private URL does NOT raise
  SSRFRejectionError, but a rebound different host does); readiness timeout →
  unhealthy+teardown; **round-trip** (mock `_run_terraform` output-json → `POST
  /admin/compute/deploy` returns `model_profile_id` → `call_model_by_role` routes to
  `deploy-<id>` → `DELETE …/destroy/<id>` deregisters everything); ingress precheck refuses
  `0.0.0.0/0` unauthenticated.

---

## Part B — D-SATURATE: keep the deployed endpoint optimally filled

### Built today (reuse, don't reinvent)
- **`controllers/saturation.py` `SaturationController`** — pure tested keep-N-busy:
  `SourceCapacity(id, capacity, running).headroom` (39-58) + `plan_backfill_by_source(
  target, running, backlog, per_source_caps)` (134-180) partitions backlog across
  capacity-bounded sources without exceeding headroom. Used by `pipeline/lanes.py
  DispatchLane` (#77) as a decoupled async lane (its own step/run loop, PID-driven target,
  hard floor) — the correct "keep filled" pattern, but for role-agents on code backlog, and
  it never passes `per_source_caps` in prod.
- **`infra/utilization.py` UtilizationTracker/ComputeEndpoint** — full per-endpoint model
  (`max_concurrent`/`current_load`/`available_slots`/`utilization`, `route_task` picks
  least-utilized, `find_idle_gpus`).
- **`gateway._health_tracker`** circuit breaker per profile (fails over, doesn't throttle).
- **Host-load caps:** `FloorController.auto_tune` (floor.py:63-101), PID `LoadController`.
- **`infra/gpu_metrics.py`** collects `gpu_sm_util_pct` etc (no dispatch consumer).

### The gaps
1. **UtilizationTracker is NEVER consulted by dispatch** — `route_task`/`release_task`
   called only from admin routes/tests, never from `_dispatch_jobs_via_scheduler`
   (loop.py:1487) or `_dispatch_execute_job_isolated`. `current_load` is always 0 in prod.
   The only event-loop consumer, `_phase_check_compute_utilization` (3481), does the
   OPPOSITE — tears down idle GPUs (cost-saving), never fills a busy one.
2. **No per-endpoint concurrency limit for primary model calls** — the only semaphore is
   `gateway._fallback_semaphore` (1394), guarding fallback hops only, static, non-adaptive.
   Dispatch is capped only by host-CPU FloorController/PID (endpoint-blind).
3. **Idle gap:** `run_forever` (757) sleeps a fixed 1.0s every tick regardless of
   remaining backlog/headroom; `_dispatch_jobs_via_scheduler` awaits the WHOLE batch draining
   (`asyncio.wait_for(gather)` 1562-1567) → a freed slot mid-batch idles until the slowest
   job finishes.

### D-SATURATE spec items
- **D-SATURATE.1 — Per-endpoint dispatch gate:** `EventLoop._endpoint_semaphores: dict[str,
  asyncio.Semaphore]`, lazily sized from `ComputeEndpoint.max_concurrent` (mirror the
  `_fallback_semaphore` pattern, asyncio, keyed by endpoint). In `_dispatch_jobs_via_scheduler`,
  before a model-calling batch item: `route_task()` → acquire the endpoint semaphore →
  dispatch → release + `release_task()` in `finally`. This is the missing wiring that makes
  `current_load` real.
- **D-SATURATE.2 — Capacity-aware backfill:** feed `SourceCapacity(endpoint_id,
  max_concurrent, current_load)` per endpoint into `SaturationController.
  plan_backfill_by_source` when computing how many claimed todos to dispatch, alongside the
  host caps (the exact tested algorithm DispatchLane already uses).
- **D-SATURATE.3 — Close the idle gap:** preferred — adopt DispatchLane's decoupled
  step/run lane for model-call dispatch (own short interval, reconciled from live
  `current_load`, independent of the fixed tick sleep and of whole-batch draining).
  Incremental fallback: in `run_forever`, skip/shorten the sleep whenever QUEUED backlog > 0
  AND `sum(ep.available_slots)` > 0.
- **D-SATURATE.4 — Adaptive per-endpoint target:** extend `_phase_check_compute_utilization`
  — raise `ep.max_concurrent` (bounded by a configured ceiling = vLLM `--max-num-seqs`) when
  utilization is pinned near 1.0 for N ticks with backlog remaining and
  `health_tracker.is_healthy()`; lower it (floor 1) when the breaker trips or timeouts rise
  for that profile (same shape as `FloorController.auto_tune`, scoped per-endpoint). vLLM's
  continuous batching means high concurrency = high utilization, so prefer routing many
  concurrent small requests to a self-hosted endpoint.
- **D-SATURATE.5 — Observability:** add `endpoint_utilization_pct`/`endpoint_target`/
  `endpoint_saturation_idle_ticks` to `_tick_metrics` (668-673); surface via the existing
  `/admin/compute/utilization` + `/admin/compute/endpoints` routes (which already report
  the capacity fields — they just need `current_load` to become real via D-SATURATE.1).
- **Tests:** backlog + healthy endpoint (max=4) → fills to exactly 4 in-flight, 5th waits;
  rising timeouts → target lowers next check tick; idle endpoint + backlog N ticks → target
  raised, never exceeding the ceiling; backlog + headroom → fill loop re-dispatches without
  waiting the full tick (fake-clock sleep-count); e2e N≫max todos never exceed max in-flight
  and drain to 0 without idle ticks.

## End-to-end acceptance ("fully able to do this when the spec is implemented")
`POST /admin/compute/deploy` provisions a GPU box, vLLM/llama.cpp serves `/v1` restricted
to `allowed_cidr`, the readiness probe confirms it, the registrar makes it a role-routable
provider (past the SSRF guard via the IP-bound trusted-origin allowance), and the
saturation loop keeps it filled to its per-endpoint concurrency target (adaptive to
health) while backlog exists — draining QUEUED work with no idle gap — and `DELETE
…/destroy` cleanly deregisters and tears down. D-DEPLOY + D-SATURATE together deliver this.
