# Azure Container Apps cleanup latency and stuck-deletion runbook

Status: research record and operational contract

Evidence date: 2026-08-01

## Outcome

Gludd must stop chargeable compute promptly, then continue observing Azure's
asynchronous control-plane cleanup until every Gludd-owned resource is absent.
An accepted delete request, a completed Terraform process, or a
`ScheduledForDelete` state is not proof of deletion. Cleanup success requires an
Azure read-after-delete result showing that the namespaced resource group no
longer exists.

Environment deletion and workload shutdown are deliberately separate service
levels:

1. Stop admitting work and delete or scale down the Container App first. Verify
   that GPU replicas are zero so a slow environment deletion does not extend
   compute spend.
2. Delete the managed environment and its namespaced resource group.
3. Keep emitting progress while Azure completes dependency-ordered deletion.
4. Reconcile through Azure after Terraform exits and declare success only after
   verified absence.

This preserves zero-downtime deployment behavior for unrelated Gludd workloads:
teardown of one run is isolated by resource-group name, ownership tags, run ID,
and cleanup lease. A delayed delete must never block event consumption or
shutdown of other compute.

## What Azure guarantees, and what it does not

Azure Resource Manager deletes a resource group's nested children first, then
resources that manage other resources, then the remaining resources. It waits
for dependencies and tracks asynchronous `202` operations. For `408`, `429`, and
`5xx` delete errors, Resource Manager retries for 15 minutes by default. After a
delete it performs a GET and treats `404` as success
([Resource Manager deletion behavior][arm-delete]). Consequently, 15 minutes is
an Azure retry window, not a safe Gludd deadline for declaring a leak.

The managed-environment delete API accepts deletion only when the environment
has no Container Apps ([Managed Environments - Delete][managed-env-delete]). A
Container App therefore has to disappear before the environment can be expected
to disappear. Resource-group deletion is irreversible, so every fallback action
must remain restricted to the exact Gludd run ID and ownership tags.

Azure's Container Apps troubleshooting guide recognizes the state
`provisioningState: ScheduledForDelete` as a stuck-deletion symptom. Its
documented recovery is to identify the associated VNet, delete that VNet, and
retry environment deletion ([Container Apps troubleshooting][aca-troubleshoot]).
Gludd may use that recovery only for a VNet it created exclusively for the run.
It must never delete a shared, user-supplied, or ownership-ambiguous network.

Resource locks can block an entire group delete, including when the group itself
is unlocked but a contained resource is locked. Locks can also be inherited from
a parent scope ([Azure resource locks][azure-locks]). A deny assignment on an
Azure-managed `MC_` or `ME_` resource group is not a lock that Gludd should try
to bypass.

## Long-lived user reports

Forum reports are operational evidence, not platform guarantees. They establish
the timeouts and failure modes that the live tests must exercise.

| Report | User-visible behavior | Gludd requirement |
|---|---|---|
| [Pipeline timeout deleting Container Apps environments, 2024][forum-pipeline-timeout] | Each environment took at least 20–25 minutes; three serial deletions exceeded an hour and required pipeline reruns. | Delete independent run groups concurrently within the configured Azure-operation limit, keep a durable cleanup lease outside the initiating test process, and do not use a 15-minute success/failure boundary. |
| [Terraform environment deletion over 20 minutes, 2025][forum-terraform-slow-delete] | A simple environment reportedly took 21 minutes to delete with Terraform. | Classify an advancing operation as `cleanup.in_progress`, emit heartbeats, and reserve `cleanup.blocked` for a terminal error or lack of observable progress beyond the prolonged-delete threshold. |
| [Invisible child blocked environment deletion, 2024][forum-hidden-child] | Azure reported one remaining Container App even though the environment's Apps view was empty; a forum workaround found a hidden `Microsoft.App/builders` child. | On `ManagedEnvironmentHasContainerApps`, enumerate all Gludd-owned `Microsoft.App` children rather than trusting one portal list. Do not automate an old preview-API workaround without current API validation. |
| [Managed environment stuck in scheduled deletion, 2025][forum-backend-delete] | Portal and CLI deletion failed with an internal error; a deny assignment prevented manual cleanup of the `ME_` managed group. | Preserve environment, group, subscription, operation, and correlation IDs; stop client-side retries after the bounded policy and escalate an orphaned backend state to Azure support. |

The reports make normal-but-slow and blocked materially different states. A
20-minute deletion with changing Azure operation status is not necessarily
stuck. Repeated internal errors, `ScopeLocked`, an undeletable child, or no
status change after the prolonged-delete window requires targeted diagnosis.

## Gludd teardown state machine

```text
workload.drain
  -> compute.stop_requested
  -> compute.zero_verified
  -> cleanup.requested
  -> cleanup.in_progress + periodic heartbeat
  -> cleanup.retry_scheduled (only for retryable conditions)
  -> cleanup.verified

cleanup.in_progress
  -> cleanup.blocked (actionable terminal condition)
  -> cleanup.escalated (Azure-managed orphan/platform state)
```

Every transition must be durably committed before acknowledgement so another
Gunicorn worker can resume it. The lease key is the Azure subscription ID plus
resource-group ID; the idempotency key includes the Terraform run ID and delete
operation ID. A worker crash must delay neither compute shutdown nor the next
poll.

The observable contract is:

- Emit `compute.zero_verified` as soon as the Container App has no GPU replica.
  Cost accounting stops predicted GPU runtime at this timestamp, while delayed
  Azure billing remains subject to later reconciliation.
- Emit a cleanup heartbeat at least every 30 seconds with elapsed time,
  provisioning state, last Azure status/error code, attempt count, and the next
  poll time. Never buffer the only copy of progress in a Terraform log file.
- Persist Azure request, operation, and correlation IDs without credentials.
  Surface the first actionable error immediately so tests can begin remediation
  before the rest of the suite finishes.
- Poll with bounded exponential backoff and jitter. Do not issue overlapping
  deletes while Azure reports an operation in progress.
- Continue an out-of-process cleanup lease if the initiating E2E test times out.
  The test may fail, but the cleanup worker must remain responsible until absence
  is verified or escalation evidence is durable.
- Mark `cleanup.verified` only when an authoritative resource-group GET returns
  `404` and a subscription-scoped lookup finds no matching Gludd ownership tag.

## Failure-directed recovery

| Signal | Safe automated response | Stop condition |
|---|---|---|
| Container App still exists | Delete the exact run-owned app, wait for its GET to return `404`, then retry the environment. | Any child lacks matching ownership evidence. |
| `ManagedEnvironmentHasContainerApps` | Enumerate all `Microsoft.App` children, including apps, jobs, and builders; delete only run-owned children through a current supported API. | An invisible or Azure-managed child cannot be removed. |
| `ScopeLocked` | Resolve the exact inherited lock scope and remove it only when the lock carries Gludd ownership evidence and the run authorized cleanup. | Subscription, shared group, policy-managed, or ownership-ambiguous lock. |
| `ScheduledForDelete` with an exclusive VNet | Follow the Microsoft recovery: record `infrastructureSubnetId`, delete the run-owned VNet, then retry the environment delete. | VNet is shared, supplied by the user, or ownership cannot be proven. |
| `408`, `429`, or `5xx` | Honor server retry guidance, apply jittered backoff, and retain the same cleanup lease and idempotency identity. | Retry budget expires without a new observable operation state. |
| `409` or another operation in progress | Poll the existing operation; do not launch another delete. | Existing operation becomes terminal or exceeds the prolonged-delete threshold without progress. |
| Internal error plus managed-group deny assignment | Capture IDs and activity evidence, mark `cleanup.escalated`, and open an Azure support incident. | Never attempt to override the Azure-managed deny assignment. |

Terraform destroy remains the primary path. The Azure reconciliation path is a
fail-safe for partial state, provider timeout, process loss, or an Azure resource
that outlives Terraform state. Repeatedly rerunning the entire destroy command is
not a recovery strategy because it hides operation identity and can overlap an
active asynchronous deletion.

## Live E2E acceptance evidence

The Azure provision tests must expose teardown evidence while they run, not only
in a final report:

1. Record the namespaced group, app, environment, Terraform run ID, Azure
   operation IDs, ownership tags, and predicted spend before apply.
2. Prove the app becomes ready, run the workload, and continuously consume Gludd
   events from more than one Gunicorn worker.
3. Begin teardown on success, failure, cancellation, or timeout. Emit the first
   cleanup event before waiting for any long Azure operation.
4. Verify GPU replicas reach zero and record that timestamp separately from final
   environment deletion.
5. Keep the test output live with phase markers and heartbeats. A prolonged but
   advancing delete stays visible as in progress.
6. After Terraform exits, query Azure independently. Retry only the diagnosed
   blocker and finish with verified resource-group and ownership-tag absence.
7. Preserve cleanup duration, retry counts, status/error transitions, and final
   absence evidence as E2E artifacts. A test is not green if cleanup was merely
   requested or deferred without an active durable lease.

The live suite should include fault cases for a child that outlives Terraform, a
retryable `429`, an inherited lock that Gludd owns, and a worker termination
during polling. Destructive VNet recovery and backend-force-deletion scenarios
must use fakes unless the test owns an isolated Azure subscription and exact
resources explicitly created for that scenario.

## Sources

Primary Azure guidance:

- [Azure Resource Manager resource-group and resource deletion][arm-delete]
- [Managed Environments - Delete REST API][managed-env-delete]
- [Troubleshooting in Azure Container Apps][aca-troubleshoot]
- [Lock your Azure resources][azure-locks]
- [Azure OSS Developer Support: issues deleting Container App
  environments][azure-oss-delete]

User forums:

- [Deleting Azure Container App takes long time and times out the
  pipeline][forum-pipeline-timeout]
- [Azure Container App Environment deletion takes over 20 minutes][forum-terraform-slow-delete]
- [Cannot delete container app environment][forum-hidden-child]
- [App Environment stuck and requires backend force deletion][forum-backend-delete]

[aca-troubleshoot]: https://learn.microsoft.com/en-us/azure/container-apps/troubleshooting#manually-delete-the-vnet-being-used-by-the-azure-container-apps-environment
[arm-delete]: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/delete-resource-group
[azure-locks]: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources
[azure-oss-delete]: https://azureossd.github.io/2026/02/16/Issues-with-deleting-Container-App-Environments/index.html
[forum-backend-delete]: https://learn.microsoft.com/en-gb/answers/questions/5564946/app-environment-stuck-requires-backend-force-delet
[forum-hidden-child]: https://learn.microsoft.com/en-us/answers/questions/1660320/cannot-delete-container-app-environment
[forum-pipeline-timeout]: https://stackoverflow.com/questions/78004168/deleting-azure-container-app-takes-long-time-and-time-out-the-pipeline
[forum-terraform-slow-delete]: https://stackoverflow.com/questions/79365700/azure-container-app-environment-deletion-takes-over-20-minutes-is-this-normal
[managed-env-delete]: https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/managed-environments/delete?view=rest-resource-manager-containerapps-2026-01-01
