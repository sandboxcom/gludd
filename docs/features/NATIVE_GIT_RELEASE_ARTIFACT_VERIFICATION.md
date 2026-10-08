# Native git-release artifact verification

## Outcome

The `general_ludd.git_release` collection now verifies a release artifact and
its dependency lock directly on the managed host. The `artifact_verify` role no
longer delegates this safety decision to a Gludd daemon. Normal mode and Ansible
check mode execute the same read-only verification and always report
`changed: false`.

The collection owns the single provenance implementation. Existing Python
callers keep the `general_ludd.git_release.provenance` import, but that module is
only a compatibility re-export. There is no second verifier to drift.

## Evidence from long-lived operator reports

The design incorporates failure modes reported over many Ansible generations:

- The Ansible 1.2.3 security announcement described a predictable-path symbolic
  link attack that could redirect a controller connection and disclose
  credentials or configuration. That history supports refusing every symbolic
  link component instead of trusting a resolved pathname:
  [Ansible Release 1.2.3](https://forum.ansible.com/t/ansible-release-1-2-3/13342).
- An AWX operator later encountered an `Invalid linkname` refusal when an
  archive symbolic link escaped its role root. The recommended resolution was
  to keep the target inside the owned root, matching this module's
  root-relative policy:
  [AWX invalid linkname discussion](https://forum.ansible.com/t/awx-23-8-1-getting-error-invalid-linkname-for-tarfile-member/6242/2).
- Ansible issue
  [#9429](https://github.com/ansible/ansible/issues/9429) records the operational
  incompatibility of MD5-based behavior on FIPS hosts. The module exposes only
  SHA-256 and accepts exactly 64 lowercase hexadecimal characters.
- Ansible issue
  [#69383](https://github.com/ansible/ansible/issues/69383) is a long-lived
  report of `fetch` producing a checksum mismatch across privilege boundaries.
  Verification here hashes the final local file descriptor directly and never
  compares a checksum from a separate transfer context.

These reports are evidence for the local policy; they do not imply that this
module replaces Ansible's general-purpose file transfer modules.

## Fail-closed boundary

One invocation accepts exactly five values: an absolute root, a root-relative
artifact path, its SHA-256, a root-relative dependency-lock path, and its
SHA-256. It enforces:

- one regular artifact no larger than 2 GiB;
- one regular dependency lock no larger than 16 MiB;
- 1 MiB streaming reads with no whole-artifact allocation;
- no absolute input paths, `..`, empty components, or symbolic links;
- component-by-component `openat`-style traversal below an opened root;
- pre/post `fstat` identity, size, link-count, and timestamp equality;
- constant-time comparison of exact lowercase SHA-256 values; and
- no network, subprocess, listener, credential, signing key, or mutation API.

Any missing evidence, malformed digest, unsafe path, non-regular input, size
breach, concurrent change, or mismatch fails the Ansible task. A partial
verification is never returned.

## Zero-downtime deployment and rollback

Verification is a pre-promotion admission gate. It does not write, replace,
activate, or delete an artifact. A failed candidate therefore leaves the
currently active release untouched and traffic continues to the prior verified
artifact. Recovery is:

1. keep the active release and its health checks in service;
2. quarantine the rejected candidate and preserve its failure evidence;
3. rebuild or reacquire the candidate and lock under a new immutable location;
4. rerun the identical digest gate in check mode or normal mode; and
5. promote only after downstream health gates also pass.

If a later deployment health gate fails, the release orchestrator rolls traffic
back to the prior artifact whose digest evidence was already admitted. Because
this verifier has no mutation authority, rollback never depends on undoing a
side effect from verification.

## Verification coverage

Unit and adversarial tests cover valid inputs, digest format and mismatch,
absolute and parent escapes, symbolic links at root/intermediate/final
positions, non-regular files, both size ceilings, 1 MiB chunks, concurrent
metadata change, module failure shape, and check-mode parity. The
`git_release_expert` Molecule scenario exercises the real role, repeats it in
check mode, and proves a tampered digest fails closed.
