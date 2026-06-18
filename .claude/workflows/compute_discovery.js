export const meta = {
  name: 'compute-resource-discovery',
  description: 'Extend gludd compute layer: per-provider resource DISCOVERY (vmware/k8s/local/aws/gcp/azure) + budget/work-aware AUTO-SELECT, registering picks into UtilizationTracker',
  whenToUse: 'When building or revising the gludd compute resource discovery + auto-selection feature.',
  phases: [
    { title: 'Design', detail: '3 independent designs for the provider-discovery + selector layer, judged' },
    { title: 'Build', detail: 'implement the discovery providers + selector in a worktree, commit via commit-bootstrap' },
    { title: 'Verify', detail: 'adversarial review: creds/SSRF safety, budget-fit selection correctness, offline/lazy-dep' },
    { title: 'Fix', detail: 'fully resolve every real issue on the same branch' },
  ],
}

const FACTS = [
  'EXISTING INFRA (extend, do NOT reinvent): infra/compute.py has ComputeConfig (provider: ComputeProvider enum, gpu_type: GPUType enum, gpu_count, engine, region, spot, max_cost_usd, container_image, api_key_alias, deploy_type, provider_auth_aliases), ComputeInstance, ComputeProvider/GPUType/InferenceEngine enums. infra/deployment.py = DeploymentManager.deploy() (Terraform lifecycle) + _inject_auth_env/_restore_auth_env. infra/terraform.py = HCL generator. infra/utilization.py = UtilizationTracker: registry of ComputeEndpoint (endpoint_id,url,model,gpu_type,gpu_count,max_concurrent,current_load,...) + route_task() that picks the least-utilized available endpoint serving the model; register_endpoint(); release_task(); find_underutilized(); record_tokens(). daemon.py instantiates UtilizationTracker at ~L888 (app.state._utilization_tracker, ext["utilization"]) and registers the compute router ~L1215; DeploymentManager built lazily in routers/compute.py _get_deployment_manager().',
  'WHAT TO ADD: (1) a ComputeProvider DISCOVERY abstraction — an interface like `discover(self, credentials) -> list[DiscoveredResource]` with a normalized DiscoveredResource (provider, id, region, gpu_type, gpu_count, vcpu, mem_gb, cost_per_hour, available, endpoint_url|None). Implementations: local (reuse controllers/load_scrape.scrape_system_load via psutil — CPU/mem/load; add psutil.cpu_count(logical=False) + total RAM; GPU via OPTIONAL nvidia-ml-py/pynvml lazy-import else None), kubernetes (list nodes/allocatable), vmware/vSphere, aws (EC2 describe-instance-types/regions + pricing or a static cost table), gcp, azure. (2) a SELECTOR `select_resource(work_spec, discovered, budget_headroom) -> DiscoveredResource|None` that fits the work to the cheapest resource meeting the requirement under budget. (3) auto-register the picked resource into UtilizationTracker.register_endpoint so existing route_task() can use it. (4) wire a discovery refresh (a daemon async task like the pipeline controller block, or an on-demand /admin/compute/discover endpoint).',
  'CREDS (safe pattern — obey): provider configs reference creds ONLY via provider_auth_aliases = {ENV_VAR_NAME: secrets_alias_name} (never raw secrets). Resolve at call time with secrets_resolver.resolve(alias) (SecretsManager OpenBao or EnvSecretsManager allowlist fallback); inject into env transiently for the SDK/Terraform call then RESTORE in finally (mirror DeploymentManager._inject_auth_env). Fail CLOSED if an alias cannot resolve. NEVER log a secret value. AWS aliases AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY; GCP GOOGLE_CREDENTIALS; Azure ARM_CLIENT_ID/SECRET/TENANT_ID/SUBSCRIPTION_ID; vSphere VSPHERE_USER/PASSWORD/SERVER; k8s KUBECONFIG.',
  'BUDGET/AUTO-PICK (reuse — obey): query headroom via SpendLimiter.remaining(now)->float and/or RunBudgetGuard.check_all_limits(estimated_cost)["remaining_budget"]/["allowed"]; reserve atomically with SpendLimiter.try_charge(cost_usd, kind="infra", model=, project_id=) (fail-closed if cost None + cap set). MIRROR AdaptiveRouter two-pass cost-aware select (scoring/router.py): pass 1 score candidates (quality_weight*quality - cost_weight*(cost/peer_max_cost), weights per task via routing_roles.weights_for); pass 2 hard budget gate — if best exceeds headroom, fall back to cheapest-that-fits, else fail-closed (never pick an over-budget resource). The work item supplies its budget cap (max_cost_usd) at select time; resource needs (gpu/vcpu/mem) come from the work_spec.',
  'DEPS: psutil>=6 and httpx>=0.28 are CORE (use freely; httpx for cloud REST where possible). ALL cloud/k8s/vmware SDKs (boto3, google-cloud-compute, azure-mgmt-compute+azure-identity, kubernetes, pyvmomi, nvidia-ml-py) are ABSENT. RULE: v1 adds NO new hard dep — the LOCAL provider must fully work with psutil+stdlib. Each cloud provider module LAZY-imports its SDK inside the function with try/except ImportError -> raise ProviderNotInstalled("pip install general-ludd-agent[compute]") or return a structured "provider_unavailable" result; NEVER import an SDK at module top (breaks collection). Add a [project.optional-dependencies].compute extra listing the SDKs.',
  'SAFETY/FALLBACK: every provider API endpoint (and any discovered endpoint_url) must pass the SSRF guard (security.is_safe_fetch_url for fetch URLs / connectors.base.is_safe_endpoint for endpoints) before use. Every network call has an explicit timeout + retry (reuse models.timeout_detector TimeoutRetryPolicy/classify or tenacity) + a per-provider circuit-breaker, and returns a STRUCTURED result (ok/error/partial) on offline/timeout — NEVER raise/hang (offline is the norm in tests/sandbox). Cache last good discovery so a transient outage degrades gracefully.',
  'COMMIT: make-only Bash. Commit with the command  make commit-bootstrap MSG=...  (NO-GATE; ruff/secrets/conflict/collection only). NEVER make ship/gate/full test. Use Edit/Write. Do NOT spawn sub-agents. Tests must run OFFLINE: mock httpx/SDK via patch (the repo convention is patch("httpx.get",...) / fake transport); the local provider test uses a mocked scrape_system_load; assert provider_unavailable when an SDK is absent.',
].join('\n\n')

const DESIGN = {
  type: 'object', additionalProperties: false,
  required: ['approach','module_layout','provider_interface','providers','selector','creds_safety','fallback_offline','optional_extra','wiring','risks'],
  properties: {
    approach: { type: 'string' },
    module_layout: { type: 'string', description: 'Package layout under src/general_ludd/compute_discovery/ (or infra/discovery/) + where it registers into UtilizationTracker + daemon.' },
    provider_interface: { type: 'string', description: 'The ComputeProvider discovery ABC + normalized DiscoveredResource dataclass (fields).' },
    providers: { type: 'string', description: 'local (psutil, zero new dep) + kubernetes/vmware/aws/gcp/azure each lazy-importing its SDK with provider_unavailable fallback. Which use httpx-REST vs an SDK.' },
    selector: { type: 'string', description: 'select_resource(work_spec, discovered, headroom): the budget-fit two-pass (AdaptiveRouter-mirrored) cost-aware pick; how needs (gpu/vcpu/mem) are matched; auto-register into UtilizationTracker.' },
    creds_safety: { type: 'string', description: 'provider_auth_aliases + secrets_resolver.resolve at call time, transient env inject + finally-restore, fail-closed, never logged.' },
    fallback_offline: { type: 'string', description: 'timeout+retry+breaker, structured offline result, cache last-good, never raise/hang; SSRF guard on every endpoint.' },
    optional_extra: { type: 'string', description: 'the [project.optional-dependencies].compute extra + lazy-import pattern so base import/collection never breaks.' },
    wiring: { type: 'string', description: 'discovery refresh: a daemon async task (pipeline-controller pattern) and/or an /admin/compute/discover endpoint; auto-register picks.' },
    risks: { type: 'array', items: { type: 'string' } },
  },
}

phase('Design')
const angles = [
  'MVP-first: local provider (psutil) + the selector + UtilizationTracker registration, zero new dep, fully working offline',
  'safety-first: creds via provider_auth_aliases+secrets_resolver, SSRF guard, fail-closed budget gate, structured offline fallback',
  'breadth-first: clean ComputeProvider discovery ABC with all six providers lazy-imported + the [compute] extra + cache',
]
const designs = (await parallel(angles.map(function (angle, i) {
  return function () {
    return agent(
      'gludd repo. Design a COMPUTE RESOURCE DISCOVERY + budget/work-aware AUTO-SELECT feature that EXTENDS gludd existing infra. The user wants: specify a provider (vmware/kubernetes/local/aws/gcp/azure) and have available compute auto-discovered and auto-picked given the work + budget. Design lens: ' + angle + '\n\nGROUND-TRUTH FACTS (obey them — reuse existing infra, do not reinvent):\n' + FACTS + '\n\nProduce concrete specs in the schema fields. Keep v1 zero-new-hard-dep (local works on psutil); cloud SDKs lazy in a [compute] extra; creds via provider_auth_aliases+secrets_resolver; budget via SpendLimiter/RunBudgetGuard + AdaptiveRouter two-pass; structured offline fallback; SSRF-guard every endpoint; auto-register picks into UtilizationTracker.',
      { label: 'design:' + i, phase: 'Design', schema: DESIGN, effort: 'high' }
    )
  }
}))).filter(Boolean)

const chosen = await agent(
  'You are the design judge for the gludd compute-discovery feature. Pick the STRONGEST design and graft the best from the others into one final spec. Favor: a clean provider discovery ABC, a correct budget-fit selector (fail-closed, never over-budget), safe creds (provider_auth_aliases+secrets_resolver, never logged), zero-new-hard-dep v1 with cloud SDKs lazy in a [compute] extra, and structured offline fallback with SSRF guards. Auto-register picks into UtilizationTracker so existing route_task works.\n\nFACTS:\n' + FACTS + '\n\nCANDIDATES:\n' + designs.map(function (d, i) { return '=== design ' + i + ' ===\n' + JSON.stringify(d, null, 2) }).join('\n\n') + '\n\nReturn the FINAL chosen design (same schema fields), paste-ready.',
  { label: 'design:judge', phase: 'Design', schema: DESIGN, effort: 'high' }
)
log('Design chosen.')

phase('Build')
const built = await agent(
  'gludd repo, make-only Bash. Implement this COMPUTE DISCOVERY + AUTO-SELECT feature end-to-end and COMMIT it. Use Edit/Write; commit with the command  make commit-bootstrap MSG=...  ONLY (NEVER make ship/gate/full test). Do NOT spawn sub-agents.\n\nFACTS:\n' + FACTS + '\n\nFINAL DESIGN:\n' + JSON.stringify(chosen, null, 2) + '\n\nDeliver: a ComputeProvider discovery ABC + normalized DiscoveredResource; a working LOCAL provider (psutil via scrape_system_load, zero new dep; GPU via lazy pynvml else None); cloud/k8s/vmware provider stubs that LAZY-import their SDK (try/except ImportError -> structured provider_unavailable / ProviderNotInstalled) and resolve creds via provider_auth_aliases+secrets_resolver (transient env inject + finally restore, fail-closed, never logged); a select_resource() budget-fit selector mirroring AdaptiveRouter two-pass (reusing SpendLimiter.remaining/try_charge + RunBudgetGuard.check_all_limits) that NEVER picks over-budget and auto-registers the pick into UtilizationTracker.register_endpoint; SSRF-guard (is_safe_fetch_url) on every endpoint; explicit timeout+retry+breaker + structured offline result on every network call (never raise/hang) + last-good cache; an /admin/compute/discover endpoint (PSK-gated, mirror routers/compute.py) and/or a daemon refresh task; and a [project.optional-dependencies].compute extra. Add comprehensive OFFLINE tests (mock httpx/SDK + scrape_system_load; assert: local discovery works, an absent SDK yields provider_unavailable not ImportError at module load, the selector never returns an over-budget pick and fails closed when nothing fits, an SSRF-internal endpoint is rejected, an offline/timeout returns a structured error not a raise/hang). Then git-add and run  make commit-bootstrap MSG=feat: compute resource discovery + budget-aware auto-select (local + lazy cloud/k8s/vmware providers, SSRF/creds-safe, offline fallback). Return branch, SHA, file list, and the full text of the provider ABC + selector for review.',
  { label: 'build:compute', phase: 'Build', isolation: 'worktree', effort: 'high' }
)

phase('Verify')
const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['dimension','sound','issues','severity'],
  properties: {
    dimension: { type: 'string' },
    sound: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    severity: { type: 'string', enum: ['none','low','medium','high','blocking'] },
  },
}
const lenses = [
  'CREDS + SSRF: are provider creds resolved via provider_auth_aliases+secrets_resolver at call time, injected transiently + restored in finally, fail-closed, and NEVER logged? Is every provider API endpoint + discovered endpoint_url SSRF-guarded (is_safe_fetch_url)? Any raw secret in config/logs, any internal-IP reachable?',
  'BUDGET-FIT SELECTION: does select_resource NEVER return an over-budget resource (hard fail-closed gate, like AdaptiveRouter pass 2)? Does it correctly fit work needs (gpu/vcpu/mem) and use SpendLimiter/RunBudgetGuard headroom? Any path that picks the cheapest-but-insufficient or an over-cap resource?',
  'DEP-SAFETY + OFFLINE: does the package import with ZERO new deps installed (all cloud SDKs lazy, not top-level — collection-check passed)? Does an absent SDK yield a structured provider_unavailable, not ImportError? Does every network call have timeout+retry+breaker and return a STRUCTURED result (never raise/hang) when offline? Are tests truly offline (mocked)?',
]
const builtText = (typeof built === 'string') ? built : JSON.stringify(built)
const verdicts = (await parallel(lenses.map(function (lens, i) {
  return function () {
    return agent(
      'Adversarially review this just-built gludd compute-discovery feature. Lens: ' + lens + '\n\nFACTS:\n' + FACTS + '\n\nBUILD RESULT (branch + key files):\n' + builtText + '\n\nBe skeptical — default sound=false unless the evidence clearly shows the property holds. List concrete issues + the fix. severity blocking = a leaked/loggable secret, an SSRF hole, an over-budget pick, a top-level optional import that breaks collection, or a raise/hang on offline.',
      { label: 'verify:' + i, phase: 'Verify', schema: VERDICT, effort: 'high' }
    )
  }
}))).filter(Boolean)

const toFix = verdicts.filter(function (v) { return v && !v.sound && v.severity !== 'none' && (v.issues || []).length })
let fixResult = null
if (toFix.length) {
  phase('Fix')
  const allIssues = toFix.map(function (b) { return '- [' + b.dimension + ' / ' + b.severity + '] ' + (b.issues || []).join('; ') }).join('\n')
  fixResult = await agent(
    'gludd repo, make-only Bash. The compute-discovery feature has the issues below from adversarial review. Check out the build feature branch (info below) and FULLY resolve EVERY listed issue — do NOT skip/defer/partially address any; add a test proving each fix. Re-commit with the command  make commit-bootstrap MSG=fix: compute discovery - fully resolve creds/SSRF/budget/offline review. Use Edit/Write; NO make ship/gate; NO sub-agents.\n\nBUILD:\n' + builtText + '\n\nALL ISSUES TO FULLY RESOLVE:\n' + allIssues + '\n\nReturn the updated commit SHA and, for EACH issue, exactly how it was resolved.',
    { label: 'fix:compute', phase: 'Fix', isolation: 'worktree', effort: 'high' }
  )
}

return {
  chosen_approach: chosen ? chosen.approach : null,
  build: built,
  verdicts: verdicts,
  blocking_count: toFix.length,
  fix: fixResult,
}
