# Native SearXNG Controller Runtime

S38 makes SearXNG a real Python dependency of the Ansible controller instead
of treating it as a Terraform project, Compose service, or raw URL. The travel
collection owns a bounded adapter around the official `searx.webapp` WSGI
application. Native searches use Flask's in-process test client and never open
a listener, start a subprocess, run `curl`, transfer a Python runtime to a
managed host, or return invented results.

## Runtime boundary

- `general_ludd.travel.searxng_instance` owns idempotent lifecycle state in the
  controller action process. At most 16 validated namespaces may be retained.
- `general_ludd.travel.searxng_search` defaults to `transport: native`. Check
  mode imports nothing, allocates nothing, and performs no search.
- Queries are limited to 2,048 characters, result sets to 100 rows, engine and
  category lists to 20 bounded names, and pages to 100. Invalid upstream
  status, JSON, or result shapes fail closed without logging query text.
- Generated settings are owner-only temporary resources and are removed on
  stop and failed startup. Operator settings reject symlinks and group/world
  writable files.
- `TravelIndexManager` accepts an explicit backend callable and returns copies
  of those exact backend rows. The removed `.example.com` and `Simulated`
  generator can no longer turn an unavailable backend into false success.
- `transport: remote` plus `remote_url` remains an explicit compatibility
  rollback. Native mode rejects URL, Compose-project, and Terraform-project
  arguments.

The travel Galaxy artifact is staged in the controller execution environment.
The official upstream source is fixed to revision
`7b4612e86250389dc9d5ee67e4cc2cd64d06602a`; the EE runtime lock records the
content hashes of that requirement and the complete definition. Image
verification imports `searx.webapp` with networking disabled, alongside
Ansible Core and Runner. The controller uses Python 3.11 and does not rely on a
managed host's ambient interpreter.

## Long-lived upstream evidence

- [Discussion #1789](https://github.com/searxng/searxng/discussions/1789)
  documents that JSON must be explicitly enabled and that public JSON access
  is commonly disabled because of bot abuse. The generated native settings
  enable only JSON, and the adapter applies strict input/result bounds.
- [Discussion #3106](https://github.com/searxng/searxng/discussions/3106)
  records an ARM source install failing while compiling `pytomlpp`, with the
  maintainer noting the architecture had not been tested. Gludd builds the
  dependency once in a digest-addressed controller EE and proves the import
  before admitting jobs; it does not compile SearXNG independently on hosts.
- [Issue #3896](https://github.com/searxng/searxng/issues/3896) records an
  apparently successful upgrade that later failed on a missing `tomli` import
  for older Python. The fixed Python 3.11 EE, exact source revision, and
  `searx.webapp` smoke make that dependency failure visible before rollout.
- [Issue #3474](https://github.com/searxng/searxng/issues/3474) records a
  concurrent-mutation exception while timeout errors were translated. The pin
  is newer than its upstream repair, and Gludd still treats exceptions and
  malformed result payloads as failures rather than empty or simulated data.

## Zero-downtime delivery and rollback

The EE is immutable. Build and warm a new digest beside the active digest,
verify its offline imports and collection artifacts, then route only new
Ansible jobs to it. Existing jobs drain on the old digest; no running controller
is mutated. After the drain reaches zero, retire the old image while retaining
its digest for the release rollback window.

Rollback reverses only new-job routing to the previous verified digest and
drains jobs already admitted to the candidate. If native integration itself is
the incident and an operator-managed SearXNG endpoint is already healthy,
`transport: remote` is the bounded compatibility rollback. Neither path
reintroduces the deleted Compose files, Terraform glue, or an implicit local
listener.

## Verification

- Warning-fatal runtime, action, index, and EE artifact unit suites.
- Strict-zero travel collection Python-boundary scan.
- Delegated Molecule syntax, converge, idempotence, and verify phases with an
  isolated namespaced state directory and no ignored failures.
- EE validate/build and digest-addressed verification smoke contracts.
- Branch-aware coverage with an 85% aggregate floor and a 75% per-file floor,
  plus Ruff, strict mypy, Markdown, task/resource checks, and repository
  collection.
