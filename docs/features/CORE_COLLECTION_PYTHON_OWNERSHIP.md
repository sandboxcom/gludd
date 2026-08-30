# Core and collection Python ownership

## Contract

Gludd core owns the daemon, authenticated control plane, release trust path,
resource lifecycle, and shared transport contracts. Domain implementations belong
to their Ansible collection and are imported by fully qualified collection name.
Collection-specific Python requirements belong to collection execution-environment
metadata rather than the base `general-ludd-agent` dependency set.

The beta.4 migration consolidates the pure-Python ASN.1 DER/OID implementation in
`general_ludd.security.plugins.module_utils.asn1`. The `ssl_cert` role remains the
managed-host owner and continues to use the mature OpenSSL CLI for file inspection;
the collection module utility is the controller/plugin API for deterministic DER
encoding, parsing, and OID lookup. The final ownership cleanup removes the six
legacy ASN.1/OID symbols and their duplicate OID table from
`general_ludd.ssl_agent.cert_manager`; core retains no compatibility copy.

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

The 2026-08-29 ownership audit also proved that this ASN.1/OID slice introduces no
Python package requirement: the collection implementation imports only Python's
standard library. `cryptography` stays in the core dependency set because core
certificate generation/parsing, authentication, encrypted storage, release
verification, and TLS trust paths still import it. Consequently this cleanup does
not change `pyproject.toml` or `uv.lock`; an AST regression fails if any of the six
legacy symbols returns to core or if the collection utility gains a non-stdlib
import.

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

Exact-SHA run `33043838717` then passed 22 of 23 executable jobs and isolated
one Python 3.11 ordered-test failure in the self-update rollback boundary. The
old rollback implementation called `importlib.reload()` on the snapshotted
module. Reload reused the module object but re-executed its namespace, creating
new dataclass identities; an endpoint could consequently produce a
`SelfUpdateRequest` that had the same qualified name and fields as the request
type held by a pre-reload consumer but failed `isinstance`. Rollback now captures
and restores the original shallow namespace, rebinds the parent package, and
keeps the `ModuleSnapshot` record in a stable type module. The exact hosted order
and an end-to-end source rollback both prove the pre-reload identities survive.

Exact-SHA run `33048791238` passed both Python gates, every platform build, the
controller EE, provider E2E, game building, and 17 of 23 executable jobs before
the Python 3.11 `unit-1d` shard exposed SQLite result code 17 during four-engine
concurrent schema startup. One connection committed DDL while another was
introspecting `agent_tokens`, invalidating the prepared `PRAGMA table_info`
statement. `ensure_tables()` already bounded retries for the two other expected
SQLite startup races; it now treats only `database schema has changed` as the
third transient DDL race. A deterministic failing-first regression and the real
four-engine test pass on Python 3.14 and an isolated Python 3.11.14 environment;
unrelated `OperationalError` values still fail startup immediately.

The subsequent local release gate exposed a static-evidence integration gap:
`module_snapshot_types.py` was exercised through the public
`module_snapshot.ModuleSnapshot` re-export, but the coverage-gap index resolved
re-exports only from package `__init__.py` files. The mapper now follows explicit
imports and aliases from regular source modules as well. A failing-first fixture
pins that behavior, while prose mentions and unrelated imports still do not count
as coverage. This keeps the test inventory exact without adding a production
allowlist or duplicating the stable type test.

## Upstream and practitioner evidence

Reviewed and revalidated 2026-08-29:

- [Ansible collection structure](https://docs.ansible.com/projects/ansible-core/2.19/dev_guide/developing_collections_structure.html)
  specifies collection-FQCN imports for `plugins/module_utils`.
- [Ansible module utilities](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_module_utilities.html)
  places utilities used by a particular module family in the same collection.
- [Ansible Builder collection dependencies](https://docs.ansible.com/projects/builder/en/latest/collection_metadata/)
  defines collection `meta/execution-environment.yml` and Python requirement
  introspection as the supported dependency boundary.
- An Ansible practitioner reported in January 2020 that local plugins could not
  import local module utilities even when local modules could, in
  [ansible/ansible#66402](https://github.com/ansible/ansible/issues/66402). The
  collection-FQCN contract and installed collection artifact avoid that ambiguous
  loader path.
- An Ansible Builder user reported a collection requirement that silently failed
  introspection in
  [ansible/ansible-builder#364](https://github.com/ansible/ansible-builder/issues/364).
  Gludd therefore validates requirements and the resulting runtime in focused
  tests rather than trusting image assembly alone.
- [Python's import reference](https://docs.python.org/3/reference/import.html)
  documents that `importlib.reload()` reuses a module object and reinitializes
  its contents by executing the module again; that behavior explains why a
  module-object-only snapshot cannot preserve class identity.
- CPython maintainers warn that reload is not fully reliable when consumers use
  `from module import name` in the
  [programming FAQ](https://docs.python.org/3/faq/programming.html#when-i-edit-an-imported-module-and-reimport-it-the-changes-don-t-show-up).
  The stable type module and namespace restoration avoid requiring every live
  consumer to rebind imported class names atomically.
- [SQLite result-code documentation](https://www.sqlite.org/rescode.html#schema)
  defines `SQLITE_SCHEMA` as a prepared statement invalidated by another
  connection's schema change and documents SQLite's own bounded reprepare loop.
- In the long-lived
  [SQLite user-forum schema-change thread](https://sqlite.org/forum/forumpost/1df053250d),
  practitioners reproduced result code 17 across concurrent connections and
  SQLite maintainers explained that the statement is safe to prepare and execute
  again, but may still surface after SQLite exhausts its internal retry bound.

## Zero-downtime deployment, rollback, and resources

The change is additive at the collection artifact boundary and subtractive in the
core wheel. Build the physics, radio, forensics, and security collections and their
controller EE before switching consumers to the new FQCNs. Verify ASN.1 DER round
trips and every numerical/security adapter before deploying the collections and
core wheel together. Consumers of the six removed core symbols must switch to the
security-collection FQCN in the same rollout. Existing managed-host OpenSSL
execution is unchanged, and the game E2E runtime remains isolated behind its
explicit extra, so no certificate or service restart is required.

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

The module-rollback repair is also ZDD: it changes only in-process recovery after
a failed hot reload and needs no daemon restart, schema migration, or managed-host
change. A successful forward reload is unchanged. If rollback is requested, the
owner reinstalls the captured module object and namespace under the same lock,
rebinds the parent package, and returns only after all requested modules are
visible in `sys.modules`. Rollback of this repair is the prior core wheel; no
persistent state is written. The snapshot owns shallow Python references only—no
process, task, client, descriptor, socket, or temporary artifact—and releases
them when the snapshot becomes unreachable.

The SQLite startup repair is ZDD and fail closed. It changes no schema and does
not restart a running daemon; it only repeats the existing idempotent
`MetaData.create_all(checkfirst=True)` pass when SQLite explicitly reports one of
three concurrent-DDL race results, within the existing 20-attempt bound and busy
timeout. Other database errors propagate on their first occurrence. Rollback is
the prior core wheel; the database file remains compatible and needs no data
operation. Each attempt owns its SQLAlchemy transaction context, which closes
before the bounded delay, so no connection, cursor, task, or descriptor survives
an attempt or cancellation.

The coverage-mapper repair is build-time only and has no application deployment
or resource lifecycle. It parses repository files read-only, retains no open file
handles, and exits before runtime packaging. Rollback restores the previous
checker, but would also restore the false-negative release gate for ordinary
module re-exports; no application process, schema, or artifact format changes.
