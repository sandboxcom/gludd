# Container App deploy role

`general_ludd.azure.container_app_deploy` is the controller-side state machine
for an already approved Azure Container Apps GPU model environment. OpenTofu is
the sole writer. The role never calls an Azure mutation module and never runs
`az`; it decides whether to observe, apply one immutable audited Terraform plan,
or destroy one owner-bound state.

Azure state is read with
`azure.azcollection.azure_rm_resource_info`. Terraform-compatible execution is
delegated to the certified `cloud.terraform` collection with its `binary_path`
set to `/usr/local/bin/tofu`. The execution environment checksum-pins OpenTofu
for each supported architecture. OpenTofu is licensed under MPL-2.0, avoiding a
BUSL-licensed HashiCorp Terraform binary without duplicating the mature Ansible
module, Terraform language, provider, or state machinery. The collection-local
filters reject scope drift, pagination, secret-shaped fields, impossible
utilization samples, foreign apps, insufficient quota, and ownership drift
before a mutation. An apply additionally requires the saved plan's SHA-256 to
match the approved digest. Bounded Ansible async polling emits progress during
long operations.

The final sanitized snapshot is exposed as the host variable
`gludd_azure_containerapp`. Although playbooks commonly call such values facts,
the underlying Azure calls correctly use `_info`: Ansible reserves `*_facts`
modules for host-local properties and recommends `_info` for online services.
When `containerapp_idle_retention` contains the exact planner request schema,
the same fact also contains a content-free `retention` decision: chosen layers,
reason codes, current micro-dollar ceilings, measured p95 time saved, and the
mandatory reconciliation timestamp. The filter delegates to Gludd's core
planner, so the role and Python lifecycle cannot drift into separate pricing or
dependency algorithms. An empty mapping disables retention planning.

When `containerapp_revision_name` is supplied, the role also reads only that
app's bounded revision and replica inventories and publishes `diagnosis` under
the same fact. A terminal `Failed`/`Unhealthy` revision with zero materialized
replicas becomes `placement_unavailable`: it is retryable through a separately
approved profile or region and is explicitly not model-quality evidence. A
terminal revision with a materialized replica becomes `runtime_unhealthy`
instead, while healthy requested replicas permit inference. Provider messages,
resource names, model identities, and credentials never enter this fact.

See [Azure model-runner idle retention](../../../../../../docs/azure-idle-retention.md)
for the four user postures, billing boundaries, exact configuration, and
practitioner reports that shaped the fail-closed behavior.

## Inputs and lifecycle

All variables are prefixed `containerapp_`; the role is disabled by default.
`containerapp_state` accepts `observe`, `planned`, `present`, or `absent`.
`present` requires an audited immutable plan and digest unless the exact owned
resources are already ready. `absent` refuses foreign applications or an owner
tag mismatch. Credentials are inherited through the controller environment and
are never accepted as role variables or copied into facts.

For a zero-downtime update, generate and audit the OpenTofu plan first, run the
role with `state=present`, wait for the new revision to become ready, send the
bounded inference proof, rerun with a metric timespan, and only then retire the
old revision through OpenTofu. On any failure, leave the current healthy
revision serving and run the owner-bound `absent` transition only for ephemeral
model-work capacity, including self-improvement.

## Live failure replay

The role ships the canonical
[`live_failure_replay_v1.json`](files/live_failure_replay_v1.json) incident
corpus and its Draft 2020-12
[`schema`](files/live_failure_replay_v1.schema.json). The 22 chronological
fixtures cover every bounded failure or rejection observed during the live
Azure work documented from 2026-09-10 through 2026-09-21. Startup fixtures run
through the same collection filter used by the role. Other fixtures bind to an
exact regression node that exercises the already-owned production boundary.
Run the corpus contract with:

```console
make test-files TESTFILES='tests/unit/test_azure_containerapp_live_failure_replay.py' PYTEST_ARGS=''
```

The corpus is operational evidence, not a raw-log archive. It retains only
fixed reason classes, bounded counts and timings, reviewed actions, source-line
ranges, and public practitioner references. It excludes prompts, credentials,
provider text, resource IDs, endpoints, model names, and repositories. Repeated
canaries remain separate chronological fixtures even when they replay through
the same corrected classifier, so a later implementation cannot erase the
history that falsified an earlier hypothesis.

## Practitioner evidence

- [Ansible custom facts regression #84750](https://github.com/ansible/ansible/issues/84750)
  shows why these cloud observations are gathered explicitly per lifecycle pass
  instead of being installed as an implicit `gather_facts` provider.
- [Azure collection API-version failure #2191](https://github.com/ansible-collections/azure/issues/2191)
  demonstrates that provider API discovery can drift underneath a collection;
  the role pins the Container Apps and Monitor API versions and validates the
  returned identities.
- [community.general Terraform check-mode issue #7422](https://github.com/ansible-collections/community.general/issues/7422)
  documents a task-level check-mode apply surprise. This role uses a separately
  audited saved plan, verifies its digest, and does not treat Ansible check mode
  as the security boundary.
- [OpenTofu licensing](https://opentofu.org/docs/intro/) documents the Linux
  Foundation project's MPL-2.0 license. The role therefore supplies the `tofu`
  executable through `cloud.terraform`'s supported binary override instead of
  downloading the BUSL-licensed HashiCorp CLI.
- [OpenTofu module compatibility issue #3347](https://github.com/opentofu/opentofu/issues/3347)
  records practitioners' concern about existing `.tf` modules and
  `required_version`. Maintainers confirm `.tf` support and their ecosystem
  compatibility commitment, so Gludd keeps the established configuration and
  provider modules rather than forking them into `.tofu` copies.
- [OpenTofu AzureRM lock-timeout issue #4225](https://github.com/opentofu/opentofu/issues/4225)
  tracks a long-standing case where an AzureRM state lock can fail immediately
  instead of honoring `-lock-timeout`. The role does not treat that timeout as
  its safety boundary: it additionally binds the exact state, owner, operation,
  saved plan, and plan digest and leaves failure visible to the caller.
- [Serverless GPU replicas stuck in `AssigningReplica`](https://learn.microsoft.com/en-us/answers/questions/5939955/typical-scheduling-latency-for-consumption-gpu-nc8)
  records an operator seeing a GPU Container App remain unplaced for days without
  a useful health event. Gludd therefore records zero-replica terminal startup as
  finite-horizon placement evidence, bounds the wait, and recommends failover
  without lowering the model's quality score. This matches the 2026-09-21 Gludd
  East US T4 run, where one requested replica remained at zero before the exact
  revision became `Failed`/`Unhealthy` and cleanup independently proved absence.
- [T4 and A100 replicas stuck in `AssigningReplica`](https://learn.microsoft.com/en-us/answers/questions/5572527/container-app-using-serverless-gpu-stuck-assigning)
  corroborates the 900-second A100 zero-container bound. A successful ARM write
  and healthy revision summary never substitute for serving-container evidence.
- [Container Apps workload-profile exhaustion #1705](https://github.com/microsoft/azure-container-apps/issues/1705)
  records a materialized replica with `WorkLoad Profile Full`. Gludd therefore
  distinguishes a zero-replica placement failure from a nonempty unhealthy
  runtime and never lowers model quality for either infrastructure outcome.
- [OpenTofu existing-resource import #1571](https://github.com/opentofu/opentofu/issues/1571)
  captures the state-adoption problem exposed by a retained Azure environment.
  Gludd imports only an independently verified, exact owner-bound environment
  before generating another audited plan.
- [Azure Monitor empty time series](https://learn.microsoft.com/en-au/answers/questions/460863/azure-monitor-rest-api-empty-timeseries-data-point)
  supports treating a structurally valid empty metric response as bounded
  pending evidence rather than malformed provider output.
- [Azure Monitor dimension registration delay](https://learn.microsoft.com/en-us/answers/questions/5811384/not-able-to-select-the-failure-type-dimension-valu)
  motivated the bounded registration retry. The five- and fifteen-minute live
  failures remain separate replay cases because waiting did not make the query
  shape valid; both still fail closed without positive GPU evidence.
- [Intermittent T4 placement #1511](https://github.com/microsoft/azure-container-apps/issues/1511)
  and [constrained A100/H100 capacity #1797](https://github.com/microsoft/azure-container-apps/issues/1797)
  justify versioned, exact-scope operational failover. They are availability
  observations, not hardware guarantees or model-quality evidence.
