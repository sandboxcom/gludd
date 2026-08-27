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
| NumPy / SciPy / PyWavelets algorithm adapters | formal collection | Migrate from base core |
| SRP / Shamir / Salsa20 adapters | security collection | Migrate from base core |
| `cryptography` | core auth, updater, encryption, and TLS trust paths | Retain in core |
| Argon2 / bcrypt | core password authentication | Retain in core |
| psutil | core process ownership and daemon lifecycle | Retain in core |
| hvac | core secret-manager control plane | Retain in core |
| PQCrypto SPHINCS+ | frozen release signature smoke | Retain in core for beta.4 |

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
core wheel. Build the security collection and controller EE before switching any
consumer to the new FQCN, verify DER round trips, then deploy the collection and
core wheel together. Existing managed-host OpenSSL execution is unchanged, so no
certificate or service restart is required.

Rollback reinstalls the prior core wheel and prior security collection together;
there is no data migration. ASN.1 parsing is read-only except for caller-owned
output artifacts. The module utility opens no network connection, subprocess,
thread, task, or persistent file handle, so teardown has no hidden resource owner.

