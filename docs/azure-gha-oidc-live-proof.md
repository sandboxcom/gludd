# Azure live proof in GitHub Actions

The ordinary `build.yml` pull-request and push jobs remain credential-free. They
exercise the same policy, lifecycle, OpenTofu materialization, fake transports,
and teardown contracts without contacting Azure. A separate
`azure-containerapp-live.yml` workflow is the opt-in hosted proof that creates,
exercises, and removes one bounded GPU model candidate.

The live workflow does not use a committed Azure credential or a GitHub secret.
With job permission `id-token: write`, GitHub creates a short-lived OIDC identity
for that individual job. Microsoft Entra trusts its exact repository/environment
subject and exchanges it for an Azure access token. The client ID, tenant ID, and
subscription ID are identifiers, not authenticators; the protected GitHub
Environment supplies them as configuration variables.

## One-time configuration

Create a GitHub Environment named `azure-containerapp-live`. Its protection
settings are an exact admission contract, not an operator convention:

- require at least one named user or team reviewer;
- enable **Prevent self-review**;
- disable **Allow administrators to bypass configured protection rules**; and
- select custom deployment branches and tags with exactly the patterns
  `development`, `master`, and `v*`.

Those patterns admit scheduled proof from the default branch, deliberate proof
from the shared development branch, and versioned release proof. They reject
feature and pull-request refs. Define these Environment variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINERAPP_ENVIRONMENT`
- `AZURE_CONTAINERAPP_LOCATION`
- `AZURE_CONTAINERAPP_WORKLOAD_PROFILE`

Define the non-secret repository variable
`AZURE_CONTAINERAPP_LIVE_ENABLED=true` only when scheduled and manually
dispatched live proofs should be admitted. Job-level conditions are evaluated
before Environment variables are loaded, so the enable switch intentionally is
a repository variable. Removing it or setting it to any other value makes the
weekly schedule a zero-compute skipped job.

## Protected-Environment admission guard

Before requesting an OIDC assertion, the hosted job runs
`make azure-containerapp-environment-guard`. The target uses the maintained
GitHub CLI to make exactly two authenticated `GET` requests: one for the
Environment and one for its complete custom branch-policy list. Its token has
only `actions: read` and `contents: read`; the guard has no mutation route.

The guard fails closed when the name differs, administrators can bypass rules,
the required-reviewer rule is absent or ambiguous, self-review is possible, no
valid reviewer remains, custom branch policies are disabled, pagination is
incomplete, or the exact three-pattern set drifts. A rejection occurs before
GitHub mints the Azure assertion and before any paid resource command runs. A
successful receipt contains only the reviewer count, allowed policy names, and
a SHA-256 digest over the verified configuration. Reviewer identities and raw
API responses never reach logs. GitHub CLI launch failures, timeouts, and
nonzero responses collapse to content-free lookup rejection codes.

The module graph classifies this guard in the security layer: it decides
whether a protected Environment may cross the credential-minting and paid
compute admission boundary, while deliberately importing no Azure SDK or
business-orchestration code. That ownership is reinforced by long-lived
practitioner evidence. [GitHub Community discussion 12241](https://github.com/orgs/community/discussions/12241)
opened in March 2022 and still recorded feature-availability confusion in May
2025, while [discussion 39054](https://github.com/orgs/community/discussions/39054)
opened in November 2022 and continued receiving branch/tag protection reports
through January 2026. Protection-setting drift therefore outlives individual
workflow revisions and remains a fail-closed security concern.

The make-target contract has a network-free behavioral check:

```console
make azure-containerapp-environment-guard \
  AZURE_CONTAINERAPP_GITHUB_REPOSITORY=sandboxcom/gludd \
  AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT=azure-containerapp-live \
  AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_JSON= \
  AZURE_CONTAINERAPP_GITHUB_BRANCH_POLICIES_JSON= \
  AZURE_CONTAINERAPP_GITHUB_ENVIRONMENT_VALIDATE_ONLY=1
```

Configure the Environment before merging the guarded workflow. Configuration
changes do not interrupt ordinary CI or an already running Container App. If a
future GitHub response change rejects a scheduled proof, keep the Environment's
stricter rules, correct the read-only adapter, and rerun the workflow; no Azure
rollback or resource cleanup is needed because admission failed before compute.

On the existing accelerator Entra application, create one federated identity
credential with:

- issuer `https://token.actions.githubusercontent.com`;
- audience `api://AzureADTokenExchange`; and
- the exact subject GitHub emits for the `azure-containerapp-live` Environment.

For a repository using the legacy subject form, that subject is
`repo:OWNER/REPOSITORY:environment:azure-containerapp-live`. GitHub repositories
created, renamed, transferred, or opted into immutable subjects after the 2026
cutover use an owner-ID/repository-ID subject instead. Copy the current GitHub
subject exactly; Entra subject matching is case-sensitive. Do not configure both
forms speculatively.

Assign the existing `General Ludd Accelerator Deployer` role to that application's
service principal at only the configured resource-group path, as described in
[`azure-iam-setup.md`](azure-iam-setup.md). The role cannot change IAM, register
providers, read secrets, delete the group, or mutate resources outside the
Container Apps proof allowlist.

## Run-time boundary

The workflow uses GitHub's maintained Actions toolkit to request an assertion for
the Azure token-exchange audience. It masks that assertion before writing one
runner-temporary, owner-private mode-`0600` file. Gludd rejects symlinks,
non-regular files, wrong ownership, broader modes, oversized input, malformed JWT
shape, and mismatched subscription identifiers before constructing an Azure
client.

Gludd passes the file to Microsoft's explicit `WorkloadIdentityCredential` for
management SDK calls and configures native OIDC in the AzureRM/AzAPI OpenTofu
providers. No `az login`, client secret, ambient `DefaultAzureCredential` chain,
or assertion value on a command line is involved. Cleanup removes the temporary
assertion even when deployment fails. Provider output is censored into bounded
phase/state/failure events, while OpenTofu's machine UI supplies continuous
progress heartbeats.

Every live run is serialized per repository, times out after 75 minutes, permits
one model request, caps admitted cost at USD 5, gives resources a 60-minute TTL,
and selects `always_destroy`. The workflow has no pull-request or push trigger, so
fork code cannot obtain its OIDC identity. Keep the normal hermetic workflow as a
required check; make this paid proof a protected operational check, not a
credential prerequisite for untrusted contributions.

## Operational findings

The implementation accounts for recurring operator reports rather than treating
OIDC setup failures as generic Azure failures:

- [GitHub Community discussion 12241](https://github.com/orgs/community/discussions/12241)
  records the long-running request to prevent a deployment initiator from
  approving their own Environment; the guard requires the resulting
  `prevent_self_review` setting.
- [GitHub Community discussion 39054](https://github.com/orgs/community/discussions/39054)
  shows an Environment continuing to allow `master` after the repository moved
  to `main`, which blocked deployments. The guard compares the complete policy
  set instead of accepting the generic "custom policies enabled" flag.
- [GitHub Community discussion 141195](https://github.com/orgs/community/discussions/141195)
  reports surprising ref selection when Environment branch protection meets a
  chained workflow. Gludd keeps this paid workflow direct and pins its admitted
  refs explicitly.
- [go-github issue 2722](https://github.com/google/go-github/issues/2722)
  documents that GitHub returned `can_admins_bypass` before its REST schema
  documented the field. The guard deliberately requires the live field to be
  present and false rather than treating an omitted value as safe.
- [Azure Login issue 482](https://github.com/Azure/login/issues/482) demonstrates
  that an almost-correct federated subject still fails because Entra matching is
  exact and case-sensitive.
- [AzureRM issue 27490](https://github.com/hashicorp/terraform-provider-azurerm/issues/27490)
  records confusion caused by omitting native OIDC enablement; Gludd sets
  `ARM_USE_OIDC=true` explicitly.
- [AzureRM issue 20794](https://github.com/hashicorp/terraform-provider-azurerm/issues/20794)
  shows unwanted Azure CLI fallback when provider authentication is ambiguous;
  Gludd sets `ARM_USE_CLI=false` and supplies one explicit method.
- [Azure Identity issue 44488](https://github.com/Azure/azure-sdk-for-python/issues/44488)
  shows why stale federated assertions must not become durable credentials. The
  workflow creates one immediately before the bounded proof and always removes
  it afterward.
- [Azure Identity issue 45900](https://github.com/Azure/azure-sdk-for-python/issues/45900)
  exposed workload/managed-identity client-ID coupling in broad default chains;
  Gludd constructs `WorkloadIdentityCredential` directly.

GitHub documents the OIDC permission and protected-Environment pattern in
[Configuring OpenID Connect in Azure](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure).
The read-only verification fields and token permission are documented under
[REST API endpoints for deployment environments](https://docs.github.com/en/rest/deployments/environments)
and [deployment branch policies](https://docs.github.com/en/rest/deployments/branch-policies).
Microsoft documents the Entra federated-credential exchange in
[Authenticate to Azure from GitHub Actions by OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect).
The Azure SDK constructor contract is documented under
[`WorkloadIdentityCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.workloadidentitycredential).
