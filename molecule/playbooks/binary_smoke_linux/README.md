# Linux binary smoke scenario

This scenario executes the built `dist/gludd` artifact inside Ubuntu 24.04.
It uses Molecule's Docker driver so that release validation exercises a Linux
binary even when the release captain is working from macOS.

## Driver dependency

The base `molecule` package includes only the delegated driver. The Docker
scenario therefore requires `molecule-plugins[docker]`, which is declared in
both of Gludd's development dependency lists and locked in `uv.lock`.
The scenario's `collections.yml` installs the plugin's declared
`community.docker` and `ansible.posix` Ansible collections into each
namespaced Molecule run; `requirements.yml` explicitly records that no roles
are downloaded.

This explicit dependency follows the
[Molecule installation guide](https://docs.ansible.com/projects/molecule/installation/)
and its
[continuous-integration example](https://docs.ansible.com/projects/molecule/ci/).
It also prevents the long-lived community failure mode where the scenario
configuration says `driver: docker` but plugin discovery fails:

- [molecule-plugins discussion #135](https://github.com/ansible-community/molecule-plugins/discussions/135)
  records Docker-driver discovery breaking despite otherwise successful
  Molecule installation.
- [Ansible community report: Molecule on macOS](https://www.reddit.com/r/ansible/comments/1czhczz/using_molecule_docker_driver_on_macos/)
  describes the same class of plugin/runtime mismatch in a virtual
  environment.

On macOS, `make molecule-test` also detects the running Podman machine socket
and exports it as `DOCKER_HOST`. This follows
[Podman Desktop's Docker-client guidance](https://podman-desktop.io/docs/migrating-from-docker/using-the-docker_host-environment-variable)
and avoids relying on a privileged `/var/run/docker.sock` compatibility link.
[Podman Desktop issue #1767](https://github.com/containers/podman-desktop/issues/1767)
documents the long-lived failure mode where that default socket mapping is
reported as active but Docker-compatible clients still cannot connect.

`make podman-up` creates or starts the project-owned `gludd` machine with four
CPUs, 4 GiB of memory, and a bounded 20 GiB disk. The name and resource limits
can be changed explicitly with `PODMAN_MACHINE`, `VCPU`, `VMEM`, and `VDISK`;
the defaults keep Gludd isolated from other projects and avoid the known 2 GiB
release-gate crash.

When Podman machine boot is unavailable, `make lima-up` creates or starts the
project-owned `gludd-docker` VM from Lima's official Docker template. The
Molecule entry point prefers that ready socket, then falls back to the
namespaced Podman socket. This follows
[Lima's Docker example](https://lima-vm.io/docs/examples/containers/docker/)
and keeps the test identical at the Docker API boundary rather than weakening
or skipping the Linux execution.

The Linux executable build uses Docker's `--pull=always` policy so a cached,
outdated `uv:python3.12-bookworm-slim` image cannot leave known base-package
updates pending. This follows the official
[Docker run pull-policy documentation](https://docs.docker.com/reference/cli/docker/container/run/#set-the-pull-policy---pull)
and avoids mutating a stale slim image with a broad in-container distribution
upgrade, which can emit package-maintainer warnings unrelated to Gludd.

Run the scenario through its namespaced project entry point:

```text
make molecule-test SCENARIO=binary_smoke_linux
```
