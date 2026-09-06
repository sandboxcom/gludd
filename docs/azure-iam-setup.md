# Azure least-privilege identities

Gludd uses two deliberately separate Azure identities:

- the **Container Apps accelerator identity** creates, reads, updates, and deletes
  only model-serving Container Apps inside one existing resource group and joins
  them to one existing managed environment; and
- the **Azure OpenAI self-improvement identity** can read one Azure OpenAI
  account and invoke the Responses API on that account.

Neither runtime identity can create resource groups or managed environments,
change IAM, manage networks, read secrets, register providers, or administer a
subscription. Bootstrap is an operator action. Keep each generated JSON file at
mode `0600`, never paste it into chat or logs, and rotate it after exposure.

## Container Apps accelerator role

The checked-in role is resource-group scoped and contains exactly these ten
control-plane operations:

```text
Microsoft.App/managedEnvironments/read
Microsoft.App/managedEnvironments/join/action
Microsoft.App/managedEnvironments/usages/read
Microsoft.App/managedEnvironments/workloadProfileStates/read
Microsoft.App/containerApps/read
Microsoft.App/containerApps/write
Microsoft.App/containerApps/delete
Microsoft.App/containerApps/revisions/read
Microsoft.App/locations/containerAppOperationResults/read
Microsoft.App/locations/containerAppOperationStatuses/read
```

`Microsoft.App/containerApps/delete` is the only destructive permission. It is
required so every paid live proof can clean up the one app it created. The role
cannot delete the resource group, environment, registry, network, identity, or
role assignment. Azure RBAC `NotActions` is not an explicit deny; the safe
boundary is the exact allowlist above with all unrelated actions absent.

An administrator first creates the resource group and Container Apps managed
environment, including its Consumption GPU workload profile. The runtime role's
assignable scope and assignment are then both narrowed to that exact resource
group.

Create the shared environment and its T4 profile with one operator-owned ARM
group deployment after the resource group exists:

```sh
make --no-print-directory azure-containerapp-environment-bootstrap-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_RESOURCE_GROUP=gludd-models-eastus AZURE_CONTAINERAPP_ENVIRONMENT=gludd-gpu-environment AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME=gpu-t4 AZURE_CONTAINERAPP_WORKLOAD_PROFILE_TYPE=Consumption-GPU-NC8as-T4 AZURE_CONTAINERAPP_LOCATION=eastus | xargs -0 az
```

The generated command is one incremental deployment of the checked-in ARM
template. The template owns only `Microsoft.App/managedEnvironments`; it does
not create IAM, networks, registries, Log Analytics workspaces, Container Apps,
or secrets. Run it with the operator identity, not the Gludd service principal.
The service principal intentionally lacks `managedEnvironments/write` and
cannot alter or delete this shared baseline.

Creating or changing a custom role requires
`Microsoft.Authorization/roleDefinitions/write`. The operator normally gets
that through the Azure built-in role "User Access Administrator" at the exact
resource-group scope or a carefully constrained parent scope. The Gludd runtime
identity does not receive it and therefore cannot create or update a custom role.

For a new role, an authorized operator runs this one line (replace the example
subscription and resource-group values):

```sh
make --no-print-directory azure-accelerator-role-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_RESOURCE_GROUP=gludd-models-eastus | xargs -0 az
```

The Make target writes only a NUL-delimited Azure CLI argument vector. `xargs -0`
invokes exactly one `az` process, so spaces remain data rather than new options.

Because the role in the current subscription already exists, narrow it in place
instead of attempting another create:

```sh
make --no-print-directory azure-accelerator-role-update-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_RESOURCE_GROUP=gludd-models-eastus | xargs -0 az
```

Create a fresh service principal and write its JSON securely:

```sh
(umask 077; make --no-print-directory azure-accelerator-auth-args AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_RESOURCE_GROUP=gludd-models-eastus AZURE_ACCELERATOR_SP_NAME=gludd-accelerator-20260905 | xargs -0 az > /tmp/gludd-azure-accelerator-auth.json)
```

`az ad sp create-for-rbac` scopes the assignment using `--scopes`; it does not
accept a useful `--subscription` option in this command path. Accordingly,
`azure-accelerator-auth-args` does not emit `--subscription`. This avoids the
failure reported by operators and matches the
[scope-only service-principal thread][forum-sp-scope-only]. Azure CLI currently ignores stdin for these JSON
arguments; the target emits argv rather than piping a role document to stdin.

Validate the private file without printing a secret:

```sh
make azure-accelerator-auth-check AZURE_ACCELERATOR_AUTH_FILE=/tmp/gludd-azure-accelerator-auth.json AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_ACCELERATOR_AUTH_VALIDATE_ONLY=0
```

Then prove the exact environment, profile type, environment usage, and profile
headroom through three fixed read-only ARM paths:

```sh
make azure-containerapp-preflight AZURE_ACCELERATOR_AUTH_FILE=/tmp/gludd-azure-accelerator-auth.json AZURE_ACCELERATOR_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_CONTAINERAPP_RESOURCE_GROUP=gludd-models-eastus AZURE_CONTAINERAPP_ENVIRONMENT=gludd-gpu-environment AZURE_CONTAINERAPP_WORKLOAD_PROFILE_NAME=gpu-t4 AZURE_CONTAINERAPP_LOCATION=eastus AZURE_CONTAINERAPP_MODEL_ID=Qwen/Qwen2.5-0.5B-Instruct AZURE_CONTAINERAPP_MODEL_REVISION=7ae557604adf67be50417f59c2c2f167def9a775 AZURE_CONTAINERAPP_PARAMETER_COUNT=494032768 AZURE_CONTAINERAPP_WEIGHT_BITS=16 AZURE_CONTAINERAPP_KV_CACHE_MIB=2048 AZURE_CONTAINERAPP_RUNTIME_OVERHEAD_MIB=3072 AZURE_CONTAINERAPP_PREFLIGHT_LIVE=1
```

The preflight does not list subscription resources or call the regional profile
catalog. It selects the smallest sufficient profile locally, verifies the named
environment has that exact configured type, and requires at least one unit of
headroom from `workloadProfileStates`. HTTP 401/403 and 404 become distinct,
secret-free trace reasons so a missing environment is never confused with bad
credentials.

On 2026-09-06 the supplied accelerator credential passed authentication and the
preflight stopped with the typed, content-free reason `environment_not_found`.
That is a real readiness blocker, not an authorization failure: the service
principal intentionally cannot create the shared managed environment. Run the
operator bootstrap command above, then repeat the read-only preflight. Do not add
`managedEnvironments/write`, resource-group creation, provider registration, or
broader subscription access to make the runtime principal bootstrap itself.

If an older Gludd principal has a subscription-scoped assignment, adding a new
resource-group assignment is not enough: the broader assignment must also be
removed. Resolve and verify the service principal object ID before running an
operator command beginning with:

```sh
az role assignment delete --assignee-object-id OBJECT_ID --role "General Ludd Accelerator Deployer" --scope /subscriptions/SUBSCRIPTION_ID
```

Do not use that principal for live writes until the role definition is narrowed,
the resource-group assignment exists, the old assignment is gone, and Azure's
eventual-consistency delay has passed. Re-run the read-only preflight afterward.

## Azure OpenAI self-improvement role

This optional identity is separate because Container Apps GPU serving does not
need Cognitive Services access. The custom role is assignable at the exact
resource-group scope because Azure does not permit a custom-role assignable scope
to name an arbitrary account resource; Azure documents assignable scopes as a
management-group or subscription-level resource, a subscription, or a resource
group. The role assignment remains narrowed to the exact account resource.

Its only data-plane operation is:

```text
Microsoft.CognitiveServices/accounts/OpenAI/responses/write
```

It also has the two read operations needed to identify the account and deployment.
It cannot deploy models, list keys, change networking, or write account settings.

An authorized operator creates or updates the checked-in custom role separately,
then generates credentials with one Azure CLI invocation:

```sh
(umask 077; make --no-print-directory azure-self-improve-auth-args AZURE_SELF_IMPROVE_SUBSCRIPTION_ID=11111111-2222-3333-4444-555555555555 AZURE_SELF_IMPROVE_RESOURCE_GROUP=gludd-models AZURE_SELF_IMPROVE_ACCOUNT=gludd-self-improve AZURE_SELF_IMPROVE_SP_NAME=gludd-self-improve-20260905 | xargs -0 az > /tmp/gludd-azure-self-improve-auth.json)
```

The operator or secret-injection workflow must read the JSON and map it to the
documented Gludd environment variables without echoing it. Gludd never accepts
the credential as a Make argument.

Azure CLI issues [#31995][azure-cli-31995] and [#31579][azure-cli-31579] are
tracked because service-principal/RBAC behavior and warnings have changed across
CLI releases. Pin the CI toolchain and keep the hermetic fake-CLI tests green.

## Operational evidence and forum findings

Operators have reported GPU apps that never start on T4, long A100 cold starts
that still incur billing, non-root image incompatibilities, and region-specific
image-pull authorization failures. Gludd therefore requires a digest-pinned
image, a revision-pinned model, an arbitrary non-root-compatible cache path,
environment-level quota evidence, a zero-minimum/one-maximum replica bound,
continuous phase traces, a cost cap, and verified app-only cleanup before a live
proof is permitted.

- [T4 image never starts][aca-1511]
- [A100 startup and billing delay][aca-1763]
- [arbitrary non-root UID compatibility][aca-1746]
- [region-specific image-pull authorization][aca-1629]

Research was rechecked on 2026-09-06 against current Microsoft documentation and
the original practitioner threads:

- Azure CLI customer reports [#30526][azure-cli-30526] (opened December 2024)
  and [#31239][azure-cli-31239] (opened April 2025) show GPU workload-profile
  creation rejecting a T4 profile that the CLI itself listed as supported. The
  checked-in bootstrap therefore uses one explicit ARM group deployment rather
  than relying on the affected `az containerapp env workload-profile add`
  mutation path.
- Container Apps report [#1511][aca-1511] remains open after intermittent T4
  executions failed before image pull, while [#1763][aca-1763] reports an open
  investigation into 25-minute A100 startup delays and billing during the wait.
  The live proof consequently has bounded visible phase heartbeats, no hidden
  retry, a hard TTL/cost ceiling, and cleanup on every terminal path.
- A 2024 scale-to-zero report [#1239][aca-1239] associated stuck replicas with
  unexpectedly high cost. Gludd does not treat a requested zero minimum as proof
  of cleanup: it destroys the one owned app and independently reads the exact app
  until absence is established.
- Microsoft's current [quota reference][azure-containerapp-quotas] states that T4
  and A100 GPU quota is scoped to the managed environment. The current
  [workload-profile guide][azure-workload-profiles] identifies
  `Consumption-GPU-NC8as-T4` as 8 cores/56 GiB and
  `Consumption-GPU-NC24-A100` as 24 cores/220 GiB. Gludd therefore verifies the
  named environment's usage/state instead of inferring capacity from a regional
  catalog response.
- Microsoft's [GPU comparison][azure-gpu-types] records 16 GiB VRAM for T4 and
  80 GiB for A100. The planner selects the smallest profile whose immutable
  parameter/weight/cache/overhead estimate fits; the current 494,032,768-parameter
  Qwen proof selects T4, while larger unquantized models fail closed or require an
  exact A100 profile.
- The platform announcement [#1746][aca-1746] requires serverless GPU images to
  tolerate an arbitrary non-root UID. The proof image and writable cache paths
  must remain non-root compatible; startup-time package installation is not an
  accepted workaround.

Practitioner threads are operational evidence rather than service contracts. The
official quota, workload-profile, GPU, RBAC, and REST references below define the
normative boundary.

## References

- [Azure custom-role scope rules][azure-custom-role-scope]
- [Azure RBAC role definitions][azure-role-definitions]
- [Azure Container Apps permissions][azure-app-permissions]
- [Azure Container Apps GPU types][azure-gpu-types]
- [Azure CLI workload-profile management][azure-workload-profiles]
- [Managed-environment usage API][azure-environment-usages]
- [Workload-profile state API][azure-workload-profile-states]

[forum-sp-scope-only]: https://learn.microsoft.com/en-us/answers/questions/1337773/how-to-create-service-principal
[azure-cli-31995]: https://github.com/Azure/azure-cli/issues/31995
[azure-cli-31579]: https://github.com/Azure/azure-cli/issues/31579
[azure-cli-30526]: https://github.com/Azure/azure-cli/issues/30526
[azure-cli-31239]: https://github.com/Azure/azure-cli/issues/31239
[aca-1511]: https://github.com/microsoft/azure-container-apps/issues/1511
[aca-1763]: https://github.com/microsoft/azure-container-apps/issues/1763
[aca-1746]: https://github.com/microsoft/azure-container-apps/issues/1746
[aca-1629]: https://github.com/microsoft/azure-container-apps/issues/1629
[aca-1239]: https://github.com/microsoft/azure-container-apps/issues/1239
[azure-custom-role-scope]: https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles
[azure-role-definitions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/role-definitions
[azure-app-permissions]: https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/compute#microsoftapp
[azure-gpu-types]: https://learn.microsoft.com/en-us/azure/container-apps/gpu-types
[azure-containerapp-quotas]: https://learn.microsoft.com/en-us/azure/container-apps/quotas
[azure-workload-profiles]: https://learn.microsoft.com/en-us/azure/container-apps/workload-profiles-manage-cli
[azure-environment-usages]: https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environment-usages/list?view=rest-resource-manager-containerapps-2025-07-01
[azure-workload-profile-states]: https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environments/list-workload-profile-states?view=rest-resource-manager-containerapps-2025-07-01
