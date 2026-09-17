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
self-improvement capacity.

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
