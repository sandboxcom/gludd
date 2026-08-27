# Core and collection Python ownership

## Contract

Gludd core owns the daemon, authenticated control plane, release trust path,
resource lifecycle, and shared transport contracts. Domain implementations belong
to their Ansible collection and are imported by fully qualified collection name.
Collection-specific Python requirements belong to collection execution-environment
metadata rather than the base `general-ludd-agent` dependency set.

The first beta.4 migration moves the pure-Python ASN.1 DER/OID implementation from
`general_ludd.ssl.asn1` to
`general_ludd.security.plugins.module_utils.asn1`. The `ssl_cert` role remains the
managed-host owner and continues to use the mature OpenSSL CLI for file inspection;
the collection module utility is the controller/plugin API for deterministic DER
encoding, parsing, and OID lookup. Core retains no compatibility copy.

## Dependency dispositions

The mechanical import audit on 2026-08-27 produced these dispositions:

| Dependency family | Current owner | Disposition |
|---|---|---|
| Ansible controller | `ansible-controller` extra and controller EE | Already outside base core |
| Azure SDK | `azure` extra and Azure collection | Already outside base core |
| Scapy | `networking` extra and networking collection | Already outside base core |
| llama.cpp / CUDA | local and CUDA inference extras | Already outside base core |
| pygame / OpenCV / image comparison | game E2E extra | Already outside base core |
| NumPy / SciPy / PyWavelets algorithm adapters | physics collection | Migrated from base core |
| SRP / Shamir / Salsa20 adapters | security collection | Migrated from base core |
| `cryptography` | core auth, updater, encryption, and TLS trust paths | Retain in core |
| Argon2 / bcrypt | core password authentication | Retain in core |
| psutil | core process ownership and daemon lifecycle | Retain in core |
| hvac | core secret-manager control plane | Retain in core |
| PQCrypto SPHINCS+ | frozen release signature smoke | Retain in core for beta.4 |

The physics, radio, forensics, and security collections now declare their
controller-side Python dependencies through `meta/execution-environment.yml` and
`meta/ee-requirements.txt`. The core wheel no longer declares NumPy, SciPy,
PyWavelets, PyCryptodome, Shamir, or srptools. The development dependency group
retains them solely to execute collection tests, and the game E2E extra declares
its own NumPy requirement.

## Hosted and local parity

The first exact-SHA hosted run, GitHub Actions run `33036151138`, found two
environment-specific boundaries that focused local tests did not expose. The
Linux E2E shard did not have the source checkout's `collections/` directory on
its Python namespace, and the Python 3.11 unit shard exposed tests that patched
process-global `Path.exists` and `os.stat` while xdist was using those same
objects. E2E setup now mirrors the Galaxy package root only for source tests;
production retains FQCN imports with no core fallback. PID observation uses
module-local path and ownership seams, so a test cannot corrupt pytest's process
or filesystem observations.

The explicit-Python replay target also previously replaced the checkout's shared
`.venv`. The dual-track runner correctly detected the interpreter drift and
failed closed. Replays now create `UV_PROJECT_ENVIRONMENT` beneath Gludd's
namespaced resource root and remove it on success, failure, or interruption.
This permits Python 3.11 reproduction alongside the canonical local producer
without changing the producer's interpreter or attestation.

The next exact-SHA hosted run, GitHub Actions run `33039982588`, passed 21 of
23 executable jobs, including every platform package, controller EE, Molecule
shard, provider E2E, game build, and core gate. Its two Python 3.11 failures
closed two further observation leaks. Protected Linux `/proc/<pid>/cwd` entries
may raise `PermissionError`; stale-owner discovery now treats that single
read-only observation as unavailable and tries its bounded `lsof` fallback.
Executable fallback tests now compare canonical real paths, so a hosted
`/usr/bin/python` symlink resolving to `/usr/bin/python3.12` cannot make a
platform-specific fixture fail while production continues to bind the exact
executable identity.

## Upstream and practitioner evidence

Reviewed 2026-08-27:

- [Ansible collection structure](https://docs.ansible.com/projects/ansible-core/2.19/dev_guide/developing_collections_structure.html)
  specifies collection-FQCN imports for `plugins/module_utils`.
- [Ansible module utilities](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_module_utilities.html)
  places utilities used by a particular module family in the same collection.
- [Ansible Builder collection dependencies](https://docs.ansible.com/projects/builder/en/latest/collection_metadata/)
  defines collection `meta/execution-environment.yml` and Python requirement
  introspection as the supported dependency boundary.
- An Ansible user reported long-lived import failures when local plugins attempted
  to consume non-FQCN module utilities in
  [ansible/ansible#66402](https://github.com/ansible/ansible/issues/66402). The
  collection-FQCN contract avoids that ambiguous loader path.
- An Ansible Builder user reported a collection requirement that silently failed
  introspection in
  [ansible/ansible-builder#364](https://github.com/ansible/ansible-builder/issues/364).
  Gludd therefore validates requirements and the resulting runtime in focused
  tests rather than trusting image assembly alone.

## Zero-downtime deployment, rollback, and resources

The change is additive at the collection artifact boundary and subtractive in the
core wheel. Build the physics, radio, forensics, and security collections and their
controller EE before switching consumers to the new FQCNs. Verify ASN.1 DER round
trips and every numerical/security adapter before deploying the collections and
core wheel together. Existing managed-host OpenSSL execution is unchanged, and
the game E2E runtime remains isolated behind its explicit extra, so no certificate
or service restart is required.

Rollback reinstalls the prior core wheel and the prior collection artifacts
together; there is no data migration. ASN.1 parsing and the migrated adapters are
read-only except for caller-owned output values and artifacts. They open no network
connection, subprocess, thread, task, or persistent file handle, so teardown has
no hidden resource owner.

The hosted-parity follow-up is also zero-downtime: collection namespace setup is
test-only, PID observation behavior is unchanged outside its local wrappers, and
the replay virtual environment is ephemeral. Rollback restores the previous test
setup and helper implementation; no daemon or managed host changes. Replay
environments and pytest basetemps share one namespaced owner and are deleted by a
single trap, including cancellation. A failed cleanup remains visible through the
target exit status rather than being hidden by a persistent shared environment.
Protected procfs and symlink-canonicalization fixes are read-only: they never
signal a process or remove a lock. If neither procfs nor `lsof` can prove the
owner cwd, discovery returns no ownership observation and therefore preserves the
candidate process. Rollback restores the earlier observer without changing any
daemon, lock-file schema, collection artifact, or managed host.
