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

### D-DEPLOY deeper gap: the terraform GENERATION path is non-functional (2026-07-10 audit)

A readiness audit went one level below the register/discover gaps above and
CONFIRMED against the tree that the generation path itself cannot provision a
real, reachable compute endpoint today — independent of D-DEPLOY.1-5. These are
BLOCKING PREREQUISITES: the register → discover → saturate work already specced
above assumes `deploy()` produces a live box with a real `base_url`; it does not.

1. **Module source path is unresolvable at apply time.** `TerraformGenerator`
   (`src/general_ludd/infra/terraform.py`, e.g. `_generate_aws` ~228-281) emits
   `module "vllm_server" { source = "./modules/vllm-server" }` (`"../modules/
   vllm-server"` for vsphere) into a per-deploy tempdir `deploy_dir =
   os.path.join(self._working_dir, f"d-{uuid}")` (`deployment.py:112`). Nothing
   copies or symlinks the real module source into that tempdir — confirmed by
   reading `deploy()` (`deployment.py:106-149`): it only `os.makedirs(deploy_dir)`
   and writes the generated `main.tf`, then runs `init`/`apply`/`output -json`
   (120-123) directly; there is no `shutil.copy`/symlink of `infra/terraform/
   modules/` anywhere in `deployment.py` or `terraform.py`. `terraform init`
   therefore cannot resolve the relative module source from an otherwise-empty
   directory — real deploys fail at `init`, before any cloud API call is made.
   REQUIRED FIX: copy (or vendor) the `infra/terraform/modules/*` tree into
   `deploy_dir` before `init`, or emit an absolute/registry module source.

2. **Even resolved, the module provisions nothing.** `infra/terraform/modules/
   vllm-server/main.tf` (read in full) only creates a `terraform_data`
   resource (`vllm_server_cloud_init`, lines 49-64) — a local no-op holding
   cloud-init text as its `input`/`output`, no cloud provider block, no compute
   resource. `outputs.tf:10-13` hardcodes `base_url = "http://localhost:8000/v1"`
   as a literal string for every provider — it is not derived from any
   instance attribute. Applying this module (once item 1 is fixed) would
   "succeed" with zero real infrastructure and a `base_url` that never points
   at anything actually running. REQUIRED FIX: the module must create a real
   compute resource (`aws_instance` / `azurerm_linux_virtual_machine` /
   `vsphere_virtual_machine` / etc.) per provider and derive `base_url` from
   that resource's actual IP/DNS output.

3. **The only real instance resource is orphaned.** A real `aws_instance.
   inference` exists at `infra/terraform/stacks/aws-vllm/main.tf:50` (read in
   full), composing `../../modules/{vllm-server,network,gpu-cost-watchdog}`
   with a genuine `vpc_security_group_ids`/`user_data`/spot-market block — this
   is the one place a real box would actually get created. But it is exercised
   ONLY by `make tf-init`/`tf-validate STACK=aws-vllm` and its own tests;
   `DeploymentManager.deploy()` (`deployment.py:106`) and `admin_compute_deploy`
   (`routers/compute.py:139-313`, confirmed by full read) never invoke the
   stack tree, only `TerraformGenerator.generate()`'s inline HCL. Even the
   stack's own outputs (`security_group_id`, `watchdog_user_data`,
   `main.tf:72-80`) don't expose an instance IP. REQUIRED FIX: either route
   `DeploymentManager` at the stack tree per provider, or promote the stack's
   real-instance resources into the generated module so both paths converge.

4. **No readiness probe before marking running.** `deploy()`
   (`deployment.py:106-147`) sets `state="running"` on the `DeploymentRecord`
   and returns a `ComputeInstance(status="running", ...)` immediately after
   `terraform output -json` succeeds (123-147) — no wait for the server
   process on the box to come up. `DeploymentHealthChecker`
   (`models/deployment_health.py:224`, confirmed by full read of its
   docstring/API) is purely reactive: `is_healthy`/`record_failure`/
   `record_success`/`get_status` — nothing polls an endpoint. The only actual
   poll loop in the codebase, `_wait_for_ready` (`infra/local_inference.py:
   207-224`, confirmed by read), is the SEPARATE local-inference path (spawns
   a local subprocess and polls `http://{host}:{port}/health`) — it is never
   called by `DeploymentManager`. REQUIRED FIX: this is exactly D-DEPLOY.1
   (`infra/endpoint_registrar.py::probe_ready`) above — flagging here that
   today there is truly zero readiness gate on the remote-deploy path, not
   even a stub.

5. **Teardown leaks: no unregister, no secrets delete.**
   `admin_compute_destroy` (`routers/compute.py:315-331`, confirmed by full
   read) runs real `terraform destroy` via `mgr.destroy(instance_id)` and pops
   `app.state._compute_deployments`, but never calls `util.unregister_endpoint`
   (nothing registered it in the first place — see gap 1 in Part A) and never
   deletes secrets (no `secrets_resolver.delete`/equivalent call exists
   anywhere in the repo grep-checked at authoring). Ephemeral credentials
   stamped onto `app.state._compute_deployments_ephemeral` (`routers/
   compute.py:231-232, 270`) during deploy are never read back or consumed on
   destroy — the code comment "the EventLoop reconcile phase can find it" (231,
   267) describes a consumer that does not exist. REQUIRED FIX: implement the
   reconcile-phase consumer that reads `_compute_deployments_ephemeral` and
   revokes/deletes the ephemeral account on workload completion or teardown,
   OR — as a smaller interim fix — delete ephemeral creds inline in
   `admin_compute_destroy` when present. This is also exactly where D-DEPLOY.5
   (registrar `secrets.delete` for both aliases) must land once D-DEPLOY.2
   actually creates those aliases.

**Prerequisite ordering.** Items 1-3 are the blocking core: until the module
source resolves (1) and the module (or the stack it should route to, 3)
creates a real compute resource with a real `base_url` (2), there is no
reachable endpoint for D-DEPLOY.1's readiness probe to poll, D-DEPLOY.2's
registrar to register, or D-DEPLOY.3's SSRF trusted-origin allowance to guard.
Items 4-5 are real gaps but are already captured by the existing D-DEPLOY.1/
D-DEPLOY.5 spec items above — restated here to make explicit that today they
have literally nothing to gate/clean up, not merely an incomplete version.

**What already works (do not rebuild, confirmed by this audit):** the
terraform BINARY invocation is real (`deployment.py:176-201`,
`asyncio.create_subprocess_exec` against the resolved `tofu`/`terraform`
binary, real stdout/stderr capture and non-zero-exit `RuntimeError`); the HTTP
deploy-trigger glue (`admin_compute_deploy`, full request validation → config
→ precheck → `mgr.deploy()` → response) is real; the static misconfig precheck
(`infra/deploy_precheck.py::precheck`, invoked at `compute.py:178`) runs and
can fail-closed with `force` override; and `terraform destroy`
(`deployment.py:151-174`, deploy-before-destroy registry guard) is real. The
gap this audit found is entirely in the HCL content + module wiring — not in
the Python plumbing around it.

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

### D-SATURATE adversarial-review corrections (2026-07-10)

Adversarial review of the D-SATURATE spec items above found 4 CONFIRMED holes.
These are corrections/hardening to D-SATURATE.1 and D-SATURATE.2, not new scope —
apply them when those items are implemented.

1. **The "asyncio pattern to mirror" is actually threading, not asyncio.**
   `_fallback_semaphore` (`src/general_ludd/models/gateway.py:413-414,1394-1431`)
   uses `threading.Semaphore` + `threading.Lock`, invoked synchronously from a
   plain `def`. Only the SHAPE is reusable (lazy dict, sized from config,
   create-on-first-use) — the primitive MUST be swapped to `asyncio.Semaphore`.
   Blocking on a `threading.Semaphore` inside the async dispatch path
   (`loop.py:1487 _dispatch_jobs_via_scheduler`, a real `async def`) would freeze
   the entire event loop for all coroutines while held. State this swap
   explicitly as a hard requirement on D-SATURATE.1 — do not literally copy
   gateway's primitive.

2. **`plan_backfill_by_source` has no item↔source affinity model.**
   `saturation.py:170-178` is first-fit, assuming any backlog item runs on any
   source with headroom. D-SATURATE.2 routes todos to specific endpoints by
   role/model compatibility — nothing in the spec says how todos get
   pre-filtered to compatible endpoints before this call. As written it could
   hand a todo needing model X to an endpoint serving model Y. **Required:** add
   a compatibility/affinity pre-filter step ahead of `plan_backfill_by_source` in
   D-SATURATE.2. (Also note: `SourceCapacity`'s field is `source_id`, not `id` —
   correct the naming used above.)

3. **Mid-flight endpoint teardown orphans blocked waiters (sharpest hole).**
   `admin_compute_destroy` (`routers/compute.py:315-331`) pops the deployment and
   `UtilizationTracker.unregister_endpoint` (`utilization.py:86-89`) fully
   removes the endpoint. Releasing a *held* `asyncio.Semaphore` is safe, but a
   coroutine *blocked* in `await sem.acquire()` for a torn-down endpoint hangs
   forever — nothing signals teardown to a blocked waiter. **Required:** D-SATURATE.1
   must wire a cancellation/timeout on endpoint acquire (e.g. `asyncio.wait_for`
   around `acquire()`, or a teardown event that wakes and fails blocked waiters)
   as part of the teardown path added in D-DEPLOY.5.

4. **Semaphore lifetime is spec-ambiguous.** "lazily sized from
   `ComputeEndpoint.max_concurrent`" doesn't pin per-call vs daemon-lifetime.
   **Required:** state explicitly that `_endpoint_semaphores` is an
   instance-lifetime dict on the long-lived `EventLoop`, constructed once via
   create-if-absent GUARDED by an `asyncio.Lock` — the create-if-absent step is
   an unguarded check-then-act race under concurrent coroutines otherwise. Mirror
   gateway's `threading.Lock`-guarded equivalent but with `asyncio.Lock`.

**Framing correction:** `UtilizationTracker` is ALREADY wired into `EventLoop`
(`daemon.py:1732` passes the `app.state._utilization_tracker` singleton into
`loop.py:329`) — the gap is purely missing `route_task`/`release_task` calls at
dispatch time, not plumbing. Separately, `UtilizationTracker` has NO locking:
`register_endpoint` (`utilization.py:80-84`) overwrites silently, discarding any
in-flight `current_load` for a re-registered endpoint — worth a guard when
D-DEPLOY.2/D-DEPLOY.5 re-register/deregister endpoints that may have in-flight
work.

## End-to-end acceptance ("fully able to do this when the spec is implemented")
`POST /admin/compute/deploy` provisions a GPU box, vLLM/llama.cpp serves `/v1` restricted
to `allowed_cidr`, the readiness probe confirms it, the registrar makes it a role-routable
provider (past the SSRF guard via the IP-bound trusted-origin allowance), and the
saturation loop keeps it filled to its per-endpoint concurrency target (adaptive to
health) while backlog exists — draining QUEUED work with no idle gap — and `DELETE
…/destroy` cleanly deregisters and tears down. D-DEPLOY + D-SATURATE together deliver this.
