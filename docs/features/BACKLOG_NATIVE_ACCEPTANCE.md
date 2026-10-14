# Native backlog deployment acceptance

`scripts.backlog_native_acceptance.make_native_deployment_validator` creates
the trusted capability accepted by `scripts.backlog_audit.main`. It supports
exactly canonical ledger items S39 and S42. Inventory aliases, all other items,
absent capabilities and unsupported installed runtimes remain `INCOMPLETE`.
The standalone audit has no signer or deployment authority by default.

The adapter uses the existing collection-owned provenance verifier and the
existing `ansible_runtime_artifacts.verify_environment` execution owner. It
adds no daemon, resolver, proof cache, arbitrary task command, service,
collection requirement or core dependency. The normal EE import/OpenTofu
smoke retains its existing behavior. Successful imports or validation-only
execution never produce item acceptance.

The embedding deployment owner supplies immutable `NativeItemBinding` records,
a provenance-record capability and an independently trusted signature verifier.
The signature verifier receives actual canonical attestation payload bytes and
must cryptographically verify them using its independently configured trust
policy before returning their SHA-256. A receipt, `SignatureState.VERIFIED`, a
claimed signer digest or copying a record's attestation hash is insufficient.
No key material is passed to the adapter, task ledger or runtime probe.

Admission binds the canonical item, full candidate SHA, repository namespace,
digest-addressed platform image, dependency-lock bytes, deployed container
name and exact generation, observer label/run ID, fixed probe definition and
fixed input digest. The default invocation capability reads the existing
`item_progress` pointer and `stream_command` status, requires this current live
child and the exact clean tracked source SHA, and retains their diagnostic-only
authority. It never edits those operational observations.

The runtime must expose one unambiguous locally installed platform digest and
one running namespaced canary with matching source/project labels and exact
container generation. It executes only that generation, through the existing
native interpreter, in isolated Python mode with warnings treated as errors.
It does not pull, build, start or promote a candidate container. Existing
native EE warming and cleanup remain owned by their original implementation.

Docker inspect normally provides `RepoDigests`; Podman can provide `Digest`.
The adapter validates those closed fields and supported manifest `Descriptor`
fields, then reuses `verify_container_push.image_digest_from_output` to require
one consistent digest. Wrong, malformed or conflicting digests and index
descriptors remain incomplete. The [official Moby inspect response definition](https://github.com/moby/moby/blob/master/api/types/image/image_inspect.go)
distinguishes configuration `Id` from manifest `RepoDigests` and documents the
optional multi-platform descriptor. The adapter never substitutes `Id` for a
manifest digest.

Both captured identity inspections and the installed probe require empty
stderr, including when their return code is zero. Warnings, errors and other
diagnostics fail closed with a distinct stderr stage; there is no suppression
or success override.

The signed subject is a bounded JSON artifact with exactly `schema_version`,
`identity`, `probe` and `installed_files`. Its independent dependency lock is
bounded JSON in the provenance owner's existing `packages` format. That exact
lock must already be installed as
`/opt/gludd/ansible-ee/dependency-lock.json`; its actual bytes and installed
distribution versions are checked before and after the probe. Both
`ansible-core` and `defusedxml` must be represented. Missing lock material is
incomplete evidence, not permission to derive a substitute lock.

The native git-release collection must already be installed below
`/usr/share/ansible/collections/ansible_collections/general_ludd/git_release`.
The signed subject binds its `MANIFEST.json`, `FILES.json`, provenance verifier,
JUnit parser and pipeline-triage action. Imported probe modules must resolve to
those exact installed paths; checkout imports cannot satisfy this condition.
The program checks those file digests before and after execution using the
existing descriptor-confined verifier.

Probe inputs must already exist below `/opt/gludd/item-probes`, with the exact
bytes declared by `FIXED_INPUTS` in the reviewed adapter. S39 verifies a sealed
artifact/lock pair and proves that an incorrect digest is rejected. S42 executes
the existing controller action and JUnit parser, checks complete failure counts
and byte-equivalent check-mode output, and proves that a DTD/entity input is
rejected. Neither probe executes a command or success flag from `TASKS.md`.

Before returning acceptance, the owner rechecks the actual installed image and
generation, signed subject, independent signer result, lock, fixed native
operation result and current invocation identity. Missing, ambiguous, changed
or skipped evidence yields a bounded `INCOMPLETE` marker identifying its stage.
The public factory also rejects replacement of its native verifier or
subprocess boundary. Fixture execution of the private owner is diagnostic
source evidence and never public completion authority.

## Proof limits and zero-downtime rollback

The current EE requirements do not include git-release, chemistry or materials.
This change deliberately does not widen that lock. An unchanged current EE
therefore cannot supply these S39/S42 installed probes. S38/S40/S41/S43/S44/S45/
S46/S49 and non-EE items remain unsupported until their own reviewed installed
probes exist. Trusted signer integration, a sealed subject/lock, an installed
supported collection and canary, and a current native invocation are still
required for real deployment acceptance.

The adapter returns an item verdict only; it sets neither a checkbox nor any
release/gate success record. Full canonical gate, exact-SHA hosted CI and normal
branch/promotion proof remain required. Removing the injected capability returns
items to `INCOMPLETE` without touching application traffic, candidate images or
the active deployment. Existing canary drain and rollback stay with the native
deployment owner. Every marker retains `release_ready=false` and
`gate_evidence=false`.

## Practitioner evidence and verification

The [Pods recreation rules practitioner discussion](https://discuss.kubernetes.io/t/pods-recreation-rules/27076)
records successful pipelines leaving an old tagged image running. That durable
operational symptom motivates inspecting the actual deployed platform subject
and generation. [Docker's image-digest documentation](https://docs.docker.com/dhi/explore/security-concepts/digests/)
and [SLSA build provenance](https://slsa.dev/spec/v1.2/build-provenance) explain
the independent immutable artifact/source/builder bindings used here. These
sources do not constitute deployment proof for Gludd.

Focused fixtures execute the actual collection operations in bounded Python
children and independently verify real Ed25519 signatures. Failing-first
controls exposed missing ownership, an unchecked installed lock, runner
substitution, and a different source SHA. Adversarial controls cover wrong item,
SHA, namespace, image/platform, generation, probe/input, installed code, signer,
invocation, read bounds, changed descriptors, import-only/forged results and
post-execution drift. The scope uses unchanged 85% aggregate and 75% per-file
line/branch floors. These fixtures provide source acceptance only. Follow-up
failing controls reproduced four unsupported Docker shapes, one malformed
digest acceptance and six accepted rc-zero warning/error stderr cases. All
78 controls then pass with the original signer/probe assertions intact.

Successful private-owner fixture execution does not cover the public
factory's successful acceptance path. The factory's substitution guard remains
enforced in all fixtures. Its successful path still requires an actual sealed
installed deployment and trusted native invocation; that proof is missing.

Main-checkout adoption passes all 284 related acceptance/CLI/runtime cases on
Python 3.11 and the combined 368-case acceptance/watcher replay on Python 3.14,
with fatal warnings. The canonical adapter profile measures 78 cases at 96.05%
lines and 91.43% branches (`.gate-logs/coverage-20261014T033132Z.json`), with 18
default typing/protocol exclusions and no added exclusion pragma. The original
EE runtime profile passes all 569 cases at 93.05% lines and 86.05% branches
(`.gate-logs/coverage-20261014T033211Z.json`), with seven default exclusions.
Both profiles retain the 85/75 floors. Production and test strict typing, Ruff,
docstrings, all 258 resource owners and task registration/integrity pass.
Publication, full gate and exact-SHA CI remain pending; these results do not
supply the missing public installed-acceptance proof.
