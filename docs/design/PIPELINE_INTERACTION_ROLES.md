# Pipeline Interaction Roles — driving external CI/CD as an agent capability (2026-07-10)

Status: **design-complete, not yet implemented.** Style/format mirrors
`docs/design/WAVE_C_DESIGNS_2026-07-10.md`. Line numbers are current-tree at
authoring time — re-confirm with a Read before implementing, they drift.

**Scope note — do not confuse with `ci_pipeline_medic`.**
`docs/design/CI_PIPELINE_MEDIC_ROLE.md` is about gludd diagnosing/fixing
**gludd's own** GitHub Actions pipeline (GHA-specific, read-mostly via
`gh run`/`make ci-status`, writes only via normal `git push`). This design is
the opposite direction: a gludd agent **driving an external pipeline system**
as part of its assigned work — trigger a deploy, cancel a stuck run, approve a
gate, fetch a failing step's log from *someone else's* Jenkins/Tekton/GitLab-CI
— for ANY backend, not just GHA, and including WRITE/mutating verbs the medic
never needs. The two share the polling idiom (§4) but are otherwise disjoint
capabilities aimed at disjoint targets.

---

## 1. SURVEY — what exists today

### 1.1 Read-only pipeline connectors (the thing being extended)

`src/general_ludd/connectors/` ships ~50 self-contained observability
connectors; ten already carry `KIND = "pipeline"` and normalize CI/CD runs
into the shared 8-key record shape (`ts/source/kind/level_or_status/message/
value/labels/raw`):

| Connector | File | Base URL default | Auth | Endpoint used |
|---|---|---|---|---|
| GitHub Actions | `connectors/github_actions.py:37,83-187` | `https://api.github.com` | `Authorization: Bearer` via `token_env` (default `GITHUB_TOKEN`), `github_actions.py:106-109` | `GET /repos/{repo}/actions/runs` (`:112`), jobs drill-down `GET .../runs/{id}/jobs` (`:177-187`, labeled a "stub") |
| Jenkins | `connectors/jenkins.py:79-195` | none — `base_url` required | HTTP Basic via `user_env`/`token_env` (`:109-116`) | `GET {base}/job/{job}/api/json?tree=builds[...]` (`:118-123`) |
| GitLab CI | `connectors/gitlab_ci.py:78-197` | `https://gitlab.com` | `PRIVATE-TOKEN` header via `token_env` (`:104-112`) | `GET /api/v4/projects/{id}/pipelines` (`:114-131`), jobs `GET .../pipelines/{id}/jobs` (`:183-196`) |
| CircleCI | `connectors/circleci.py:32,109,128` | `https://circleci.com` | token via `token_env` (pattern mirrors github_actions) | `_pipeline_url()` (`:128`) |
| Buildkite | `connectors/buildkite.py:72,84,118-121` | validated via `_guard_base_url` (`:72`) | token header | `_builds_url()` (`:121`) |
| Travis CI | `connectors/travis.py` | (same self-contained shape) | token via `token_env` | builds list |
| Azure DevOps | `AzureDevOpsSource` `connectors/azure_devops.py:96` | `https://dev.azure.com` | HTTP Basic, empty user + PAT password (`base64(":"+PAT)`, `:131-134`), PAT via `token_env` default `AZURE_DEVOPS_PAT` (`:120`); SSRF `_validate_base_url`→`is_url_blocked` (`:40-46`) | Azure DevOps **Build API** `_apis/build/builds` — `query()` GET builds (`:191`), `fetch_logs()` GET `builds/{id}/logs` (`:213`) |
| AWS CodePipeline | `AwsPipelineSource` `connectors/aws_pipeline.py:145` | n/a — **boto3 client, not raw HTTP; NO SSRF guard** (boto3 resolves its own signed endpoint) | boto3 default credential chain via injectable `client_factory` (`:129,171-197`) — **no `token_env`/secrets-resolver seam at all** (ambient AWS creds) | `codepipeline.list_pipeline_executions` (`:243`) + CloudWatch Logs `filter_log_events` (`:297`) |
| Argo Workflows | `connectors/argo_workflows.py:1-60` | opt-in private (`allow_private`, mirrors k8s) | Bearer via `token_env` | workflow list (phase in `_PHASE_STATUSES`, `:40`) |
| Kubernetes (Tekton's substrate) | `connectors/kubernetes.py:246-477` | rejects private unless `allow_private=True` (`:19-25,110-170`) | ServiceAccount Bearer via `token_env` (default `K8S_TOKEN`, `:271,288-291`) | generic REST — `mode="logs"` → pod logs (`:353-408`), `mode="events"` → cluster events (`:411-462`); **no CRD read/write today** — Tekton `PipelineRun` (`tekton.dev/v1`) support means adding a `mode="crd"` path, not a new client |

**Every one of these is READ-ONLY.** None has a trigger/cancel/approve/rerun
method — `query()`/`health()`/best-effort drill-down (`fetch_jobs`,
`fetch_failed_logs`) only. A **Jenkins** read-only connector already exists
(`connectors/jenkins.py:79-195`, table above). There is **no** Tekton,
Concourse, Drone, or Woodpecker connector of any kind today
(`make grep Q='tekton'`, `Q='concourse'`, `Q='drone'`, `Q='woodpecker'` → no
matches under `src/`) — those four are net-new adapters (§2.3).

### 1.2 Shared connector spine (`connectors/base.py`)

`base.py:145-163` — the structural `Source` Protocol (`name`, `KIND`,
`health()`, `query(spec)`); `base.py:409-435 is_safe_endpoint()` — literal-host
SSRF guard (no DNS), delegates to `general_ludd.security.ssrf.is_url_blocked`.
Every connector above calls `is_url_blocked(base_url, scheme_allowlist=...)`
itself at construction (e.g. `github_actions.py:42-46`,
`jenkins.py:89-95`) rather than importing `base.is_safe_endpoint` directly —
duplicated call, same underlying policy. `kubernetes.py:110-170` is the one
connector with an **operator opt-in** (`allow_private=True`) because K8s/Argo
API servers are routinely on RFC-1918 addresses — the same opt-in pattern
Jenkins/Tekton/ArgoCD/Concourse need here, since they too are "usually
internal."

### 1.3 `ConnectorRegistry` (`connectors/registry.py:94-476`) — the wiring point to extend, not replace

Builds a `name -> live instance` map from an operator config list
(`from_config`, `:104-121`). Per-entry class resolution is `factory` / `class`
(dotted path) / `module` (`_resolve_factory`, `:178-211`), and — the load-bearing
security property — **every** `module`/`class` selector is hard-checked against
`_ALLOWED_CONNECTOR_MODULES`, a frozenset built once from
`pkgutil.iter_modules(general_ludd.connectors)` (`:340-343`), rejecting anything
outside `general_ludd.connectors.*` BEFORE import (`_check_module_allowlist`,
`:400-443`) — this is what stops an operator config value like
`"module": "os"` from being an RCE. `query(name, spec)` is deliberately
URL-free (`:274-305`) — a caller addresses a pre-registered source by name, never
a raw endpoint, so request-time SSRF is structurally impossible. **A
`PipelineProviderRegistry` for mutating ops (§3.4) must copy this exact
allowlist-then-construct-then-validate sequence**, not invent a new one.

### 1.4 Ansible module + role pattern to mirror

`collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_*.py`
— 35 modules today (`make collection-modules`). Two directly relevant
exemplars:

- `gludd_break_glass.py:1-241` — the closest existing analogue to a
  **mutating, safety-sensitive** module: `DOCUMENTATION`/`EXAMPLES`/`RETURN`
  YAML in the docstring (`:5-87`), `argument_spec` with `no_log: true` on the
  credential (`token`, `:206`), `supports_check_mode=True` but the destructive
  branch (`restore`) explicitly **refuses to run under check_mode**
  (`do_restore`, `:180-181`) while the non-destructive branch (`snapshot`)
  is check-mode-safe (`:222-229`) — this check-mode-refusal-for-mutation
  pattern is exactly what `gludd_pipeline_trigger`/`approve` need.
- `gludd_dispatch.py:1-85` — the `daemon_url` + `psk` (`no_log: true`,
  `:38-42`) + `state` choices pattern (`dispatch`/`available`/`recent`,
  `:20-24`) used by every daemon-talking module; the new `gludd_pipeline_*`
  modules follow the same `daemon_url`/`psk`/`timeout` triplet rather than
  inventing per-backend auth flags directly in the module (the daemon-side
  `PipelineProvider` owns the backend token via the secrets resolver, per §3).

**MCP tools are auto-generated from these modules, not hand-registered.**
`scripts/gen_mcp_tools.py:344-401` walks every `gludd_*.py`, AST-parses its
`argument_spec` (`extract_argument_spec`, `:98-118`) into a JSON-schema
(`argspec_to_json_schema`, `:146-172`), and its docstring `DOCUMENTATION` block
into a description (`build_tool_def`, `:313-324`), writing
`docs/MCP_TOOLS_MANIFEST.json` + `docs/MCP_TOOLS_TOPICS.yml`. **Consequence for
this design: there is no separate "MCP tool registration" step for
`pipeline_trigger`/`pipeline_status`/etc. — writing a correct `gludd_pipeline_*`
Ansible module with a proper `DOCUMENTATION`/`argument_spec` IS the MCP tool
definition.** `no_log: true` options are surfaced as `x-no-log: true` in the
generated schema (`:159-161`) so a binder can redact them from any transcript.

### 1.5 Capability gating a mutating op must plug into

- `security/permissions.py:60-119` — `Capability(resource, actions,
  constraints)` / `PermissionSpec(agent_type, capabilities, denied, ...)`. This
  is the model to extend: a new `resource="pipeline:<backend>"` capability
  with `actions=["status","logs","trigger","cancel","approve","rerun"]` and
  `constraints={"allowed_pipelines": [...]}` — same shape `secret:openbao`
  already uses for `openbao_paths` (`:65-73`). Per Wave C finding C-SEC-1
  (`docs/design/WAVE_C_DESIGNS_2026-07-10.md:16-42`), `denied` capabilities are
  **not yet enforced** at the intersection/STS layers — a `pipeline:*` deny
  carve-out (e.g. "may trigger any pipeline except `prod-deploy`") would
  silently not apply until that fix lands; note this as a shared dependency,
  don't re-fix it here.
- `agents/registry.py:16-54 AgentRegistry.can_invoke` — gates agent-to-agent
  dispatch (invoker may only reach `allowed_subagents` glob patterns), not
  backend-resource access. Not the right gate for "may this role trigger
  pipeline X" — that's the `PermissionSpec`/`Capability` layer above. Cited
  here only so a reader doesn't reach for the wrong gate.
- `execution/human_gate.py:1-100+ HumanGate` — LangGraph `interrupt()`/
  `Command(resume=...)` gate, active when `config.review.human_in_the_loop`
  is true (`_is_human_gate_enabled`, `:41-47`); falls back to the existing
  HumanTodo polling flow when LangGraph is absent (`:8-11`). This is the
  mechanism `approve_gate`/`reject_gate` (§3) route through for
  "an agent approving a prod deploy" — see §5.
- `docs/design/TIME_TIMERS_SCOPED_NOTIFICATIONS.md` — **not yet implemented**
  (status line 3). Today notification is flat broadcast
  (`events/bus.py EventBus.publish` delivers to every subscriber of a type,
  no scoping). The scoped-notification/scope-router layer that doc designs is
  the eventual target for "notify only the human who owns `prod-deploy`"; until
  it lands, `approve_gate` on a sensitive pipeline routes through
  `HumanGate`/`HumanTodoRepository` same as any other human-in-the-loop
  decision (flat, but functional).

### 1.6 Polling idiom to reuse (`ci_pipeline_verify` role)

`collections/ansible_collections/general_ludd/agent/roles/ci_pipeline_verify/tasks/main.yml:35-49`
— the `uri` + `register`/`until`/`retries`/`delay` idiom:

```yaml
- ansible.builtin.uri: {url: "{{ ci_status_endpoint }}", method: GET, ...}
  register: _ci_poll
  until:
    - _ci_poll.status == 200
    - _ci_poll.json.status == 'completed'
  retries: "{{ (ci_poll_timeout_seconds | int) // (ci_poll_interval | int) }}"
  delay: "{{ ci_poll_interval }}"
  ignore_errors: true
```

This is the SAME idiom `roles/drive_pipeline` (§3.3) reuses for
`wait_for_run` — generalized to poll `gludd_pipeline_status` (a daemon call)
instead of a raw `uri`/`gh run list`, so the wait works identically whether the
backend is GHA, Jenkins, or Tekton.

---

## 2. DESIGN — `PipelineProvider` abstraction

### 2.1 Verb set (one Python Protocol, `src/general_ludd/pipeline_drive/provider.py`, new module — do not collide with the existing `src/general_ludd/pipeline/` package, which is gludd's internal task-pipeline, not CI/CD)

```python
class PipelineProvider(Protocol):
    name: str
    KIND: ClassVar[str] = "pipeline_driver"  # distinct from connectors' KIND="pipeline" (read-only)

    def list_pipelines(self) -> list[PipelineRef]: ...
    def get_pipeline_config(self, pipeline: str) -> dict[str, object]: ...
    def trigger_run(self, pipeline: str, params: dict[str, object]) -> RunRef: ...
    def list_runs(self, pipeline: str, spec: dict[str, object]) -> list[RunRef]: ...
    def get_run_status(self, run_id: str) -> RunStatus: ...
    def get_run_logs(self, run_id: str, step: str | None = None) -> str: ...
    def get_artifacts(self, run_id: str) -> list[ArtifactRef]: ...
    def cancel_run(self, run_id: str) -> bool: ...
    def rerun(self, run_id: str) -> RunRef: ...
    def approve_gate(self, run_id: str, gate: str) -> bool: ...
    def reject_gate(self, run_id: str, gate: str, reason: str) -> bool: ...
    def wait_for_run(self, run_id: str, timeout_s: float) -> RunStatus: ...
```

`wait_for_run` is provider-side sugar over `get_run_status` polling (same
loop every backend needs) so a caller (role or MCP tool) doesn't reimplement
the `until`/`retries`/`delay` idiom in every context — but the Ansible-level
`roles/drive_pipeline` (§2.3) ALSO exposes the raw poll for the declarative
callers that prefer it, mirroring `ci_pipeline_verify`.

### 2.2 Verb → backend mapping (what's real vs. unsupported)

| Verb | GitHub Actions | Jenkins | GitLab CI | ArgoCD | Tekton (k8s) | Notes |
|---|---|---|---|---|---|---|
| `trigger_run` | `POST /repos/{r}/actions/workflows/{id}/dispatches` (`workflow_dispatch`, requires a `workflow_dispatch:` trigger in the YAML) | `POST /job/{job}/build` or `/buildWithParameters`, needs a CSRF **crumb** first: `GET /crumbIssuer/api/json` | `POST /projects/:id/pipeline` (`ref` + `variables`) | `POST /api/v1/applications/{app}/sync` | `POST {api}/apis/tekton.dev/v1/namespaces/{ns}/pipelineruns` (create a PipelineRun CRD) | GHA dispatch is CONFIG-gated — a workflow with no `workflow_dispatch:` trigger cannot be remotely triggered at all; the provider must surface that as a clean refusal, not a 404 |
| `cancel_run` | `POST .../runs/{id}/cancel` | `POST /job/{job}/{n}/stop` | `POST /projects/:id/pipelines/:id/cancel` | `POST .../applications/{app}/operation` (terminate) | `PATCH` PipelineRun `spec.status=PipelineRunCancelled` | |
| `rerun` | `POST .../runs/{id}/rerun` | re-trigger `build` with same params (no native "rerun" concept) | `POST .../pipelines/:id/retry` | re-`sync` | create a new PipelineRun from the same spec (Tekton has no rerun primitive either) | |
| `approve_gate` | `POST .../runs/{id}/pending_deployments` (environment protection rule review) | Jenkins has no native manual-gate API — only via the `input` step's `POST /job/{job}/{n}/input/{id}/proceed` when a `Pipeline` script used `input()` | GitLab "manual" jobs: `POST /projects/:id/jobs/:id/play` | ArgoCD sync windows / manual sync approval isn't a discrete API call — `sync` itself IS the approval | **not supported** — Tekton has no native manual-approval primitive (would need a custom Task) | **Not every backend supports this verb** — the provider Protocol method must be allowed to raise `NotImplementedError("approve_gate unsupported by <backend>")`, and callers (MCP tool, role) must handle that cleanly rather than assume universal support |
| `get_run_logs(step=)` | `GET .../jobs/{job_id}/logs` (per-job; GHA has no single "step" log endpint — steps are addressed by job) | `GET /job/{job}/{n}/consoleText` (whole build; no per-step Jenkins log endpoint either) | `GET /projects/:id/jobs/:id/trace` | pod logs via the underlying Argo-executor pod (delegates to the **Kubernetes connector's** `mode="logs"`, `kubernetes.py:353-408` — reuse, don't reimplement) | same — Tekton TaskRun pod logs via `kubernetes.py` | Tekton/ArgoCD logs ALWAYS go through the k8s connector; there is no Tekton/Argo-native log API distinct from "read the pod's logs" |
| `get_artifacts` | `GET .../runs/{id}/artifacts` (returns a signed zip URL) | `GET /job/{job}/{n}/artifact/{path}` | `GET /projects/:id/jobs/:id/artifacts` | n/a (Argo Workflows: artifact repo config, S3/GCS-backed — out of scope, no generic API) | n/a (PipelineRun results are string params, not artifacts) | |

Concourse, Azure Pipelines, Drone/Woodpecker, Spinnaker, CircleCI, Buildkite
follow the same shape (REST trigger + REST cancel + REST/log-stream); each
gets its own adapter (§2.3) but no new verb — omitted from the table for
space, not because they're unsupported.

### 2.3 Backend adapters

New package `src/general_ludd/pipeline_drive/adapters/` (self-contained,
duck-typed, **same construction rules as `connectors/`**: injectable
`http_get`/`transport`, literal-host SSRF guard via `is_url_blocked` at
construction, `token_env`/`user_env` name-only config, no `shell=True`, no
`subprocess`):

- **Extend, don't fork, the read side.** `GitHubActionsSource`,
  `GitlabCiSource`, `CircleCiSource`, `BuildkiteSource` already exist as
  read-only connectors. Each gets a **sibling** `*Driver` class in
  `pipeline_drive/adapters/` (e.g. `GitHubActionsDriver`) rather than adding
  mutating methods onto the `connectors.*Source` classes directly — the
  `connectors` package's contract (`Source` Protocol, `KIND="pipeline"`) is a
  read-only, resilient-fan-out contract (`Observability.find()` assumes
  `query()` never mutates); bolting `trigger_run` onto `GitHubActionsSource`
  would violate that assumption for every caller of the observability facade.
  The `*Driver` class MAY internally reuse the `*Source`'s `_headers()`/
  `base_url` validation helpers (import, don't duplicate, where the sibling
  file already computes the identical guard).
- **Transport-shape retrofit (GET-only vs method-capable).** The read
  connectors split into two transport idioms. Method-capable —
  `Callable[[str, str, Mapping, float], tuple[int, bytes]]` (already `POST`-able):
  `argo_workflows.py:36`, `buildkite.py:36`, `travis.py:31`. GET-only —
  `Callable[[str, dict], tuple[int, object]]` (`http_get`): `github_actions.py:35`,
  `gitlab_ci.py:33`, `circleci.py:36`, `azure_devops.py:37`, `jenkins.py:36`. For
  the GET-only group, the `*Driver` sibling injects a NEW
  `http_request(method, url, headers, body)` transport as a **second** ctor arg
  (defaulting to a real `httpx.Client(follow_redirects=False)` impl, mirroring
  `argo_workflows.py:_httpx_transport:102`); the existing `http_get` seam stays
  untouched for reads, so no current connector test changes. Method-capable
  adapters reuse their existing transport as-is.
- **New adapters** (no existing connector to extend): `JenkinsDriver`
  (REST + crumb-issuer CSRF flow — `GET /crumbIssuer/api/json` then attach
  `Jenkins-Crumb` header on every mutating POST), `TektonDriver` (wraps
  `connectors.kubernetes.KubernetesSource` for the REST transport/SSRF-guard/
  Bearer-auth plumbing, adds `PipelineRun` CRUD under
  `apis/tekton.dev/v1/namespaces/{ns}/pipelineruns`), `ConcourseDriver`
  (`fly`-equivalent REST: `POST /api/v1/teams/{team}/pipelines/{p}/jobs/{j}/builds`),
  `AzurePipelinesDriver` (extends `AzureDevOpsSource`'s `api-version` pattern,
  `POST .../pipelines/{id}/runs`), `DroneDriver` (`POST /api/repos/{repo}/builds/{n}`).
  `ArgoCdDriver` for Argo CD app-sync (distinct from the existing
  `ArgoWorkflowsSource`, which is Argo *Workflows* — a different product with a
  different API; don't conflate the two Argos).
- **AWS CodePipeline** follows `aws_pipeline.py`'s boto3 pattern, not raw
  HTTP: `codepipeline.start_pipeline_execution` / `stop_pipeline_execution` /
  `put_approval_result` (this one's Approve/Reject on a manual-approval action
  IS AWS's native `approve_gate`). No SSRF guard needed — boto3 resolves its
  own signed regional endpoint from the credential chain, same reasoning as
  the existing connector (`aws_pipeline.py:171-197`). **Caveat (verified):** the
  existing connector authenticates via the *ambient* boto3 credential chain and
  has **no `token_env`/secrets-resolver seam** — this sidesteps the
  operator-token-allowlist model every other adapter here relies on. For
  mutating CodePipeline verbs, gate on the `allowed_pipelines`/`allowed_verbs`
  registry allowlist (§2.4) + the capability layer (§5) instead of a per-provider
  token, and scope the IAM role handed to the daemon to exactly the pipelines in
  `allowed_pipelines` (least-privilege at the AWS IAM boundary, since the
  in-process token gate does not apply).
- **SSRF posture**: ArgoCD/Jenkins/Tekton/Concourse are — per the existing
  `kubernetes.py`/`argo_workflows.py` precedent — routinely on internal
  addresses. Mirror `kubernetes.py:110-170`'s `allow_private` opt-in exactly
  (never a blanket RFC-1918 deny for this domain); loopback/link-local/
  metadata stay hard-blocked regardless. This matches the task brief's note
  that an operator allowlist (like the DAST design's) is the right posture
  here, not a blanket deny.
- **No arbitrary command exec, anywhere.** No adapter shells out to `gh`,
  `jenkins-cli`, `fly`, `argocd` CLI, or `kubectl` — REST/boto3 only, mirroring
  every existing connector's "no shell=True" invariant. (`ci_pipeline_verify`'s
  `gh run list` command task, §1.6, is the ONE place gludd already shells to a
  CLI for pipeline interaction — that's acceptable there because it targets
  gludd's OWN repo under gludd's own control, not an operator-configured
  arbitrary external system; this design's adapters must not repeat that
  shape for a Jenkins/Concourse the operator points at.)

### 2.4 `PipelineProviderRegistry`

`src/general_ludd/pipeline_drive/registry.py` — copy
`connectors/registry.py:94-476`'s shape verbatim: `from_config(configs)`
builds a `name -> live PipelineProvider` map; `factory`/`class`/`module`
selector with the SAME `_ALLOWED_*_MODULES` frozenset pattern scoped to
`general_ludd.pipeline_drive.adapters.*` (not `general_ludd.connectors.*` —
a separate allowlist, so a config entry can never smuggle a connector module
in as a driver or vice versa); `_validate_source_class` preflight before
construction; malformed entries recorded in `errors()`, never abort the
build. Config carries `allowed_pipelines: list[str]` (glob patterns) and
`allowed_verbs: list[str]` per entry — the capability gate (§3) is enforced
at the CALL site (daemon/role layer), but the registry-level allowlist is a
second, structural belt: a provider constructed for `{"allowed_verbs":
["status","logs"]}` raises on `trigger_run` before ever building the HTTP
request, so a bug in the capability layer cannot alone turn into an
unauthorized trigger.

---

## 3. Ansible modules + role

### 3.1 New `gludd_pipeline_*` modules (mirror `gludd_dispatch.py`'s `daemon_url`/`psk`/`timeout` triplet — the module talks to the gludd daemon, which owns the `PipelineProviderRegistry`; it never embeds a backend token itself)

| Module | `state`/mode | Daemon endpoint (new) | Check-mode |
|---|---|---|---|
| `gludd_pipeline_trigger` | trigger a run | `POST /api/pipeline/{provider}/trigger` | refuses in check_mode (mirrors `gludd_break_glass.py:180-181`'s restore refusal — triggering is always a mutation) |
| `gludd_pipeline_status` | poll one run | `GET /api/pipeline/{provider}/runs/{run_id}` | safe (read-only) |
| `gludd_pipeline_logs` | fetch logs | `GET /api/pipeline/{provider}/runs/{run_id}/logs` | safe |
| `gludd_pipeline_approve` | approve/reject a gate | `POST /api/pipeline/{provider}/runs/{run_id}/gate` (`decision: approve\|reject`, `reason` required for reject) | refuses in check_mode always — no safe simulation of "approved a prod gate" |
| `gludd_pipeline_wait` | poll to terminal | `GET /api/pipeline/{provider}/runs/{run_id}` in a loop, OR delegate entirely to the daemon's `wait_for_run` (single blocking call with server-side timeout, avoiding an Ansible-side retry loop for the common case) | safe |

Every module: `token`-shaped arguments are never accepted directly (the
backend token lives server-side, resolved by the secrets resolver per
provider config, same posture as every connector's `token_env`); `psk` is
`no_log: true` (mirrors `gludd_dispatch.py:38-42`); `argument_spec` choices
and descriptions follow `gludd_break_glass.py`'s DOCUMENTATION block shape so
`gen_mcp_tools.py` produces a clean schema automatically — no MCP-side change
needed (§1.4).

### 3.2 `roles/drive_pipeline/`

`tasks/main.yml` composes the modules declaratively: trigger → wait (reusing
the `until`/`retries`/`delay` idiom from `ci_pipeline_verify/tasks/main.yml:35-49`,
substituted with `gludd_pipeline_status` in place of `uri`) → on-fail branch
that calls `gludd_pipeline_logs` for the failing step → `gludd_message`
(existing module) to report. `defaults/main.yml` exposes
`pipeline_provider`, `pipeline_name`, `pipeline_params`, `pipeline_poll_interval`,
`pipeline_poll_timeout_seconds`, mirroring `ci_pipeline_verify/defaults/main.yml`'s
naming convention.

---

## 4. MCP surface

No new manual registration step (§1.4) — `make gen-mcp-tools` (existing
target, `scripts/gen_mcp_tools.py:378-397`) picks up the five new
`gludd_pipeline_*` modules automatically once they exist under
`collections/.../plugins/modules/`, emitting `general_ludd.agent.gludd_pipeline_trigger`
etc. into `docs/MCP_TOOLS_MANIFEST.json` with `x-no-log` on any sensitive
field. `make mcp-docs-check` (existing target) validates the manifest stays
in sync with the modules — run it in CI once these land, same as every other
module.

---

## 5. Security

**Read-only verbs** (`get_run_status`, `list_runs`, `get_run_logs`,
`get_artifacts`, `list_pipelines`, `get_pipeline_config`, `wait_for_run`) —
gated the same as any existing connector `query()`: registry-allowlisted
provider name, no request-time URL (mirrors `registry.py:274-305`'s
URL-free `query(name, spec)` — a `gludd_pipeline_status` call takes a
`provider` NAME, never a raw endpoint).

**Mutating verbs** (`trigger_run`, `cancel_run`, `rerun`, `approve_gate`,
`reject_gate`) require, in order:

1. A `Capability(resource="pipeline:<provider>", actions=[...])` grant on the
   calling role's `PermissionSpec` (§1.5) — checked at the daemon endpoint
   before the `PipelineProviderRegistry` call, same layer that gates
   `secret:openbao` reads today.
2. The provider-level `allowed_pipelines` glob + `allowed_verbs` allowlist
   (§2.4) — belt-and-suspenders, so a capability-layer bug can't alone
   authorize an unlisted pipeline or verb.
3. Token resolution ONLY via the secrets resolver (`token_env`/`user_env`
   name in config, value read from `os.environ` at call time — never logged,
   never returned in a response body, matching every connector's existing
   posture).
4. SSRF/allowlist on the provider's configured host per §2.3 (operator
   `allow_private` opt-in for internal Jenkins/ArgoCD/Tekton/Concourse,
   metadata/loopback hard-blocked regardless).

**`approve_gate`/`reject_gate` are additionally sensitive** — an agent
approving a production deployment gate is a materially different risk than an
agent triggering a CI build. Require, on top of the four gates above:

- A DISTINCT capability action (`"approve"`, not folded into `"trigger"`) so
  an operator can grant trigger/cancel without granting approve.
  `constraints={"allowed_pipelines": [...]}` should also support a
  gate-specific carve-out (e.g. approve non-prod gates but not `prod-deploy`'s)
  — this is exactly the `denied` capability shape Wave C's C-SEC-1
  (§1.5) needs fixed to actually enforce; note the dependency, don't
  silently assume it works until that lands.
- Route through `HumanGate` (§1.5) when `config.review.human_in_the_loop` is
  enabled: an `approve_gate` call on a pipeline whose config flags it
  sensitive (new `human_confirm: true` per-pipeline config key, §6) pauses via
  `interrupt()`/`Command(resume=...)` exactly like a low-confidence COMPLETE
  decision does today, falling back to the HumanTodo polling path when
  LangGraph is unavailable — no new confirmation mechanism, reuse the one
  that exists. Once `docs/design/TIME_TIMERS_SCOPED_NOTIFICATIONS.md` lands,
  route the pending-approval notification through its scope router so only
  the pipeline's designated approver is paged, instead of a flat broadcast.
- Deny (fail closed, not silently downgrade to a no-op) if `human_confirm`
  is set but neither LangGraph nor the HumanTodo fallback path resolves
  within a bounded wait — an agent must never treat "the human didn't answer"
  as "approved."

Never log a resolved token, `Authorization` header value, or PSK in any error
message, exception string, or Ansible `no_log`-missing field — mirrors the
existing `is_configured`/health-check posture (`base.py:452-477`) and
`registry.py:251-253`'s explicit refusal to leak `str(exc)` into a health
manifest (DSN/credential leak prevention).

---

## 6. Config schema

**Wiring path (mirror the connectors' — verified this survey).** Read-only
connectors flow `config/general-ludd.yml` → `UserConfig.connectors: list[dict]`
(`config/user_config.py:174`) → `daemon.py:2941-2943` → `wire_observability()`
(`routers/observe.py:249-286`) → `ConnectorRegistry.from_config(...)`. The new
`pipeline_drive.providers` list gets a **sibling** `UserConfig.pipeline_drive`
field and a `wire_pipeline_drive()` builder that calls
`PipelineProviderRegistry.from_config(...)` (§2.4) from the same daemon startup
site — do not overload the `connectors` list, so a read-only connector and a
write-capable driver can never be confused at config or registry level. A
working connector config example to copy the entry shape from lives at
`config/examples/connectors_example.yml:47-55`.

```yaml
pipeline_drive:
  providers:
    - name: prod-gha
      kind: github_actions
      base_url: https://api.github.com          # optional, this is the default
      token_env: GITHUB_TOKEN
      allowed_pipelines: ["deploy-prod", "deploy-staging"]
      allowed_verbs: [status, logs, trigger, cancel]   # approve_gate NOT listed -> registry-level refusal even with capability
      human_confirm: false

    - name: internal-jenkins
      kind: jenkins
      base_url: https://jenkins.internal.example.com
      allow_private: true                        # internal host opt-in, mirrors kubernetes.py
      user_env: JENKINS_USER
      token_env: JENKINS_TOKEN
      allowed_pipelines: ["*"]
      allowed_verbs: [status, logs, trigger, cancel, rerun]

    - name: cluster-tekton
      kind: tekton
      api_server: https://10.0.4.2:6443          # RFC-1918, requires allow_private
      allow_private: true
      namespace: ci
      token_env: K8S_TOKEN
      allowed_pipelines: ["build-*"]
      allowed_verbs: [status, logs, trigger, cancel]

    - name: prod-argocd
      kind: argocd
      base_url: https://argocd.internal.example.com
      allow_private: true
      token_env: ARGOCD_TOKEN
      allowed_pipelines: ["prod-app"]
      allowed_verbs: [status, trigger]           # sync = trigger
      human_confirm: true                        # gate every prod-app sync through HumanGate
```

---

## 7. Test plan

**Precedent test files to mirror (verified this survey):**
- Connector unit tests use an injected mock-transport callable that records
  `(url, headers)` and returns canned `(status, json)` by URL substring — zero
  real network: `tests/unit/test_connector_github_actions.py:64-77` (`_MockTransport`),
  `tests/unit/test_connector_jenkins.py` (imports `JenkinsSource`). The new
  `*Driver` tests copy this exact injection shape.
- SSRF/no-redirect coverage is a separate parametrized group:
  `tests/security/test_connector_ssrf_no_redirect.py:96-99`
  (`_GROUP1_SIMPLE_GET = ["github_actions", "circleci", "gitlab_ci"]`) — the new
  pipeline adapters should join this group.
- Registry RCE-prevention precedent: `tests/unit/test_connector_registry.py`,
  `tests/unit/test_connector_registry_import_guard.py` (module-allowlist bypass
  attempts), full-wiring round-trip `tests/integration/test_obs_connector_e2e.py`.
- Ansible-module tests install a local `_FakeAnsibleModule` stand-in (records
  `argument_spec`/`check_mode`, captures `exit_json`/`fail_json`) via
  `monkeypatch.setattr(module, "AnsibleModule", ...)`, then call `module.main()`
  directly and assert on the captured payload:
  `tests/unit/test_gludd_git_module.py:46+`, `tests/unit/test_gludd_process_module.py:42+`;
  `tests/unit/test_gludd_embed_module.py` additionally asserts `supports_check_mode`
  is a no-op-that-reports-`changed` — the exact shape `gludd_pipeline_trigger`/
  `gludd_pipeline_approve` check-mode-refusal tests must follow.

**New tests:**
- `PipelineProviderRegistry.from_config` rejects a `module` outside
  `general_ludd.pipeline_drive.adapters.*` (mirrors
  `tests/unit/test_connector_registry*.py`'s RCE-prevention tests for
  `connectors/registry.py`).
- `GitHubActionsDriver.trigger_run` against a mocked transport: POSTs
  `workflow_dispatch` with the right `ref`/`inputs`, returns a `RunRef`;
  against a workflow with no `workflow_dispatch` trigger, the mocked 404/422
  surfaces as a clean `PipelineTriggerRefused`, not a raw HTTP exception.
- `wait_for_run` polls a mocked transport returning `queued` → `in_progress` →
  `success`/`failure`, terminating on the first terminal status, honoring
  `timeout_s` (returns a timed-out `RunStatus`, never raises/hangs).
- `get_run_logs` on a failing run returns the failing step's log text (GHA:
  the failing job's log; Tekton/Argo: delegates to
  `KubernetesSource(mode="logs")` — mock the k8s pod-log endpoint, assert the
  driver did NOT reimplement log fetching).
- `JenkinsDriver.trigger_run` crumb flow: mocked `GET /crumbIssuer/api/json`
  returns a crumb, the subsequent `POST /job/{job}/build` carries the
  `Jenkins-Crumb` header; a crumb-issuer 404 (older Jenkins w/ CSRF disabled)
  falls back to no-crumb POST rather than failing outright.
- Capability gate: a role whose `PermissionSpec` has no `pipeline:<provider>`
  capability (or lacks `"trigger"` in `actions`) is refused BEFORE any HTTP
  call (assert the mocked transport was never invoked) — same shape as
  `tests/unit/test_dispatch_permission_gate.py`'s fail-closed assertions for
  `can_invoke`.
- Registry-level `allowed_verbs` refusal: a provider configured without
  `"approve"` in `allowed_verbs` refuses `approve_gate` even when the caller's
  capability grants `"approve"` — the belt-and-suspenders case (§2.4).
- SSRF: a `base_url`/`api_server` pointing at `169.254.169.254` or
  `metadata.google.internal` is rejected at construction regardless of
  `allow_private`; an RFC-1918 `api_server` is rejected WITHOUT
  `allow_private` and accepted WITH it (mirrors
  `tests/unit/test_kubernetes_connector*.py`'s existing SSRF matrix).
- `approve_gate` with `human_confirm: true` and `human_in_the_loop` enabled:
  asserts `HumanGate.await_approval`-equivalent is invoked and the gate does
  NOT resolve to approved until the human path returns a decision; a timed-out
  human wait resolves to refused, never approved-by-default.
- AWS CodePipeline driver: `start_pipeline_execution`/`stop_pipeline_execution`/
  `put_approval_result` against a fake boto3 client double (mirrors
  `aws_pipeline.py`'s existing test doubles) — boto3-absent import still
  succeeds, `health()`/mutating calls report `"boto3 unavailable"` rather than
  raising ImportError at module load.
