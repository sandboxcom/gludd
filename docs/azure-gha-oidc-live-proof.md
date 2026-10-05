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

Create a GitHub Environment named `azure-containerapp-live`. Add deployment
branch/tag restrictions and required reviewers appropriate for paid cloud work.
Define these Environment variables:

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
Microsoft documents the Entra federated-credential exchange in
[Authenticate to Azure from GitHub Actions by OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect).
The Azure SDK constructor contract is documented under
[`WorkloadIdentityCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.workloadidentitycredential).
