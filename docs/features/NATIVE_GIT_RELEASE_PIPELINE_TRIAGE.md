# Native Git-Release Pipeline Triage

S42 replaces the generic service request behind the git-release
`pipeline_triage` role with a collection-owned action that reads one local
JUnit report. The report never leaves the Ansible controller. The result
contains counts, SHA-256 testcase identities, terminal-outcome digests, and no
failure body, property, stdout, stderr, or unredacted test name.

## Why the boundary is native

Ansible's documented execution flow makes an action plugin the supported place
to perform controller-local work before a remote module transfer. The paired
`pipeline_triage` module intentionally fails closed when that interception does
not happen. This follows the [Ansible action-plugin execution
model](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_program_flow_modules.html)
instead of introducing an HTTP hop, listener, subprocess, worker, or managed
host dependency. The locked `defusedxml.ElementTree` library is the only XML
parser; no XPath or `lxml` dependency is added. That distinction also avoids
the additional dependency and XPath surface documented by
[`community.general.xml`](https://docs.ansible.com/projects/ansible/latest/collections/community/general/xml_module.html).

## Long-lived user evidence

- A [2014 ansible-project JUnit/CI thread](https://groups.google.com/g/ansible-project/c/0ic8kasUqbQ)
  shows that operators have wanted Ansible test-result integration for more
  than a decade. The action therefore consumes the established JUnit artifact
  rather than inventing another service protocol.
- A [2024 Ansible user report about unbounded JUnit callback
  payloads](https://www.reddit.com/r/ansible/comments/1cus9rt) motivates both the
  hard cardinality limits and the decision never to return captured output.
- Pytest's longstanding [parameter identity issue
  #469](https://github.com/pytest-dev/pytest/issues/469) demonstrates why raw
  parameterized names are unsuitable as a public stable key. S42 returns only
  SHA-256 identities derived from bounded class, file, and name fields and
  rejects duplicates.

## Fail-closed contract

The action accepts exactly an absolute normalized `root` and a relative
`report_path`. It opens every root and report component with no-follow file
descriptor semantics, requires one regular single-link file, and compares the
device, inode, mode, link count, byte size, modification time, and change time
before and after the read. Input is limited to 16 MiB and read in one-MiB
chunks. DTDs, entities, external references, malformed XML, non-JUnit roots,
zero testcases, duplicate identities, invalid or conflicting summary counts,
and mutation during the read are rejected.

At most 100,000 testcases and 64 failures or errors are admitted. A testcase
may have exactly one terminal `failure`, `error`, or `skipped` child, or no
terminal child for success. Conflicting `status`/`result` attributes are
ambiguous and fail closed. Failure text, messages, properties, system output,
and system error are never copied into the returned structure. Normal and
check mode execute the same read-only function and return byte-equivalent
evidence with `changed: false`.

## Digest-addressed ZDD rollout

Build the controller execution environment once and identify it by immutable
image digest. Run `git_release_pipeline_triage` Molecule syntax, prepare,
converge, idempotence, and verify phases against a canary controller using that
candidate digest. Admit a bounded canary share of new pipeline reports only
after its health and content-free receipt shape match expectations. Then drain
in-flight work from each old controller before moving that controller to the
candidate digest. Because reports are read-only inputs and no state or schema
is migrated, active controllers continue serving throughout the wave.

Rollback stops new admission to the candidate, drains its in-flight reads, and
routes new work to the previously verified immutable environment digest. The
action never changes the JUnit report or managed host, so rollback needs no
data repair and cannot expose a partially transformed artifact.
