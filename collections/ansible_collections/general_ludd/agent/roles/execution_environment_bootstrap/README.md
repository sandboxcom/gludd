# Execution environment bootstrap

This role gives Gludd a non-interactive, Ansible-owned lifecycle for the local
container runtime that builds and runs its controller execution environment.
It installs Podman when permitted, creates an exact `gludd-*` Podman machine on
macOS, builds the locked AWX-compatible image beside any active image, verifies
it, and publishes the sanitized `gludd_execution_environment` fact. Calling the
same role with `execution_environment_bootstrap_state: absent` tears down only
that exact image, content-addressed context, and machine.

The build remains zero-downtime: a candidate name contains the runtime-lock
digest, existing images are never pruned, and the fact is not published as
`verified: true` until both offline smoke tests pass. `ephemeral: true` provides
guaranteed cleanup for one-shot builders. The default leaves the verified
machine running so Ansible Runner can use it; Gludd should request `absent` when
its local work queue drains.

## Mature-tool boundary

Podman is the only required host executable. Python is the control plane, not a
replacement container engine: `ansible-builder` invokes Podman to construct the
OCI image, and Podman supplies the Linux VM on macOS. The Azure Python SDK talks
directly to ARM, so this path never requires the Azure CLI. OpenTofu is consumed
by `cloud.terraform` inside the execution environment, so neither `tofu` nor
HashiCorp Terraform belongs on the host.

- [Ansible Builder](https://docs.ansible.com/projects/builder/en/latest/) is the
  only EE constructor. The role calls Gludd's existing observable
  `scripts/ansible_runtime_artifacts.py` driver, which delegates to
  `ansible-builder`; it does not implement another Containerfile generator.
- The official [AWX EE](https://github.com/ansible/awx-ee) uses this same
  `ansible-builder build` path (Podman by default). Gludd's definition is
  therefore AWX-compatible without maintaining a second handwritten Dockerfile.
- `ansible.builtin.package` installs Podman on Linux. Image inspection/removal
  uses Podman's exact-name CLI because this role must work before any external
  Galaxy collection has been installed; adding a Podman collection here would
  create a bootstrap dependency cycle. On macOS the official
  [Podman Installation](https://podman.io/docs/installation) guide recommends
  the native installer rather than Homebrew, so the role downloads the 5.8.2
  universal package with its published SHA-256 before invoking Apple's package
  installer.
- Podman does not expose an Ansible module for `Podman machine` lifecycle. That
  small boundary uses `ansible.builtin.command` with `argv`, strict resource
  caps, and an exact owner name. Every engine operation sets
  `CONTAINER_CONNECTION`, and the role restores the caller's prior default
  connection after initialization.
- OpenTofu is installed inside the EE by the canonical definition. Verification
  runs `/usr/local/bin/tofu version` with `--network=none`; HashiCorp Terraform
  is neither installed nor invoked.

## Community findings carried into the design

The long-running Ansible community
[Execution Environment RFC](https://forum.ansible.com/t/rfc-execution-environments-for-ansible-automation/34009)
established the durable separation used here: Ansible Builder builds OCI images
and Ansible Runner runs them. User discussions in
[r/ansible](https://www.reddit.com/r/ansible/comments/1g9t216/) and another
[EE builder thread](https://www.reddit.com/r/ansible/comments/1d6uvwe/) likewise
report using `ansible-builder` in CI or the `infra.ee_utilities.ee_builder` role
instead of scripting a container build from scratch. Gludd keeps the smaller
direct Builder boundary because it additionally needs exact Podman-machine
connection isolation and periodic async heartbeat events; the underlying
builder remains the same maintained project.

Podman users on macOS and Windows repeatedly encounter a structural difference
rather than a transient bug: containers require a Linux VM. The official
[machine documentation](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html)
defines CPU, memory, and disk limits, while the
[Podman environment contract](https://docs.podman.io/en/stable/markdown/podman.1.html)
documents `CONTAINER_CONNECTION`. The role makes both constraints explicit so
it never borrows or silently retargets another project's machine.

## Invocation

Gludd calls `AnsibleRunnerAdapter.reconcile_execution_environment()`; that
bounded Python API selects `playbooks/bootstrap_execution_environment.yml`,
applies lifecycle-specific timeouts, rejects arbitrary extra variables, and
emits sanitized start/completion/failure events. Make is not part of runtime
provisioning or teardown. Direct playbook callers may use these extra variables:

```yaml
execution_environment_bootstrap_state: present  # or absent
execution_environment_bootstrap_validate_only: false
execution_environment_bootstrap_machine_name: gludd-ee-builder
execution_environment_bootstrap_machine_cpus: 2
execution_environment_bootstrap_machine_memory_mb: 4096
execution_environment_bootstrap_machine_disk_gb: 32
execution_environment_bootstrap_ephemeral: false
```

Installation is an explicit capability grant. Set
`execution_environment_bootstrap_install_podman: false` to require a preexisting
runtime. Unsupported platforms, unsafe names/paths, unbounded resource requests,
and mutable `latest` tags fail closed before any host mutation.
