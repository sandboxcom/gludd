# Connector Batch-5 Compatibility and Resource Contracts

This document captures the contracts used by the batch-5 E2E shard when it
models host readers, ingest formats, and namespaced workloads.  The tests use
deterministic fakes and fixtures; they do not require a live OpenShift, Nomad,
Podman, containerd, or privileged `dmesg` environment.

## Runtime and Namespace Contracts

Every worker receives a stable `project_namespace`.  Resource locks, temporary
directories, container names, network names, telemetry labels, and Terraform
state paths must include that namespace.  A worker may only release leases
whose namespace matches its own.  Admission must fail closed when the worker
budget is exhausted and must return an owner-visible reason rather than
waiting indefinitely.

| Runtime | Required isolation contract | E2E assertion |
| --- | --- | --- |
| OpenShift | Map the project namespace to an OpenShift `Project`/Kubernetes namespace. Set CPU, memory, and pod requests/limits before admission; expose `ResourceQuota` rejection as a typed capacity error. | Two projects with identical workload names receive independent leases. A quota rejection does not consume a lease or leave a pending worker. |
| Nomad | Include namespace and job ID in the allocation identity. Declare CPU/memory resources and observe `pending`/`blocked` placement states, not only terminal job state. | An exhausted allocation is reported as `pending` with its namespace and is eventually released; another namespace remains schedulable. |
| Podman | Use a project-specific container/pod/network/volume name and a project-specific cgroup path. Rootless runs must detect unavailable cgroup delegation and return a typed unsupported-capacity result. | Parallel projects cannot address or delete one another's containers, and unsupported rootless limits never appear as silently enforced limits. |
| containerd | Use a containerd namespace and unique snapshot/lease identifiers. Track shim and temporary-mount cleanup by owner; cgroup metrics are scoped to the namespace. | Stopping one project removes only its shims, mounts, and leases. A stale resource is surfaced as cleanup debt rather than reused by a different project. |
| dmesg/kernel events | Treat kernel messages as host-wide observations. Capture only an injected fixture in tests, preserve timestamp/severity, and correlate OOM/cgroup/permission messages with the owning project namespace when available. | Parsing is deterministic for OOM, cgroup, and access-denied lines; missing or unreadable `dmesg` is an explicit capability result, never an empty success. |

Connector adapters must not infer ownership from a bare PID, container name, or
job name.  The namespace and an owner token are mandatory correlation fields.
When a provider cannot represent namespaces (for example, a host-level dmesg
reader), Gludd keeps ownership in its lease/telemetry envelope instead.

## Connector-Specific Contracts

* **Redfish and SNMP:** endpoint, credential reference, timeout, and retry
  policy are configuration inputs.  Tests inject a transport and assert that
  authentication failures, timeout errors, and malformed payloads become
  typed connector errors without retry storms.
* **Host readers (syslog, journald, Windows/macOS readers):** tests use a
  temporary fixture or fake reader.  A missing source is either an empty stream
  when the platform explicitly reports absence, or a typed capability error;
  it must never read another project's host fixture.
* **Ingest formats:** JSON, line-delimited JSON, CSV, and key/value payloads
  preserve the source namespace and event timestamp.  Malformed records are
  counted and isolated; one bad record cannot terminate a worker or leak a
  partially parsed event.
* **Profiling and telemetry connectors:** Pyroscope, Parca, StatsD, and similar
  readers receive endpoint and labels explicitly.  Responses retain service,
  timestamp, and project labels and map to the connector's declared `KIND`.

## Long-Lived Community Findings

These reports are user/community discussions, not normative specifications.
They explain why the contracts above are tested explicitly:

* OpenShift users report deployments blocked by project or
  `ClusterResourceQuota`, including quota usage affecting other projects:
  [ClusterResourceQuota usage above project quota](https://access.redhat.com/solutions/4291601)
  and [OpenShift quotas and rolling updates](https://access.redhat.com/articles/7084813).
* Nomad operators found that `pending`/`blocked` allocation metrics were a
  more reliable signal of placement failure than waiting for a job to become
  terminal:
  [Detecting resource exhaustion / placement failure](https://discuss.hashicorp.com/t/detecting-resource-exhaustion-placement-failure/3066).
* Podman users discuss rootless-in-rootless CI, cgroup delegation, and SELinux
  constraints.  The practical consequence is to detect unsupported cgroup
  limits rather than claiming isolation that the runtime cannot enforce:
  [Rootless Podman in rootless Podman](https://github.com/containers/podman/discussions/19813)
  and [rootless cgroup support](https://github.com/containers/podman/issues/1429).
* containerd users have reported shim accumulation, cgroup deletion races, and
  temporary-mount cleanup failures.  These are the reason cleanup is owner
  scoped and observable:
  [containerd shim process leak](https://github.com/containerd/containerd/issues/4297),
  [cgroup deleted while collecting metrics](https://github.com/containerd/containerd/issues/9309),
  and [mount management](https://github.com/containerd/containerd/issues/11303).
* Docker users running parallel Compose projects report collisions when the
  project name is shared; the same namespace rule applies to Gludd workers:
  [Parallel Compose execution discussion](https://forums.docker.com/t/parallel-execution-of-docker-compose-up-commands-with-same-configuration/136142).

## Verification

Run the focused compatibility tests before the full E2E shard:

```text
make test-files TESTFILES='tests/e2e/test_connectors_batch5_workflows.py tests/e2e/test_concurrent_connector_workers_e2e.py' PYTEST_ARGS=-q
make lint-files FILES='tests/e2e/test_connectors_batch5_workflows.py tests/e2e/test_concurrent_connector_workers_e2e.py'
make check-task-registration
make validate-task-ledger
```

Any new provider mismatch must add a failing fixture first, then update this
contract and the corresponding task evidence after the focused tests pass.
