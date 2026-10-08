# Exact-Gate Shadow Receipt Authentication

## Delivered shadow boundary

The S83.179 authentication follow-up adds a bounded HMAC envelope to pass and
failure receipts. It does not admit a receipt, restore coverage, skip Pytest, or
change the terminal gate result. Every selected batch still executes with one
worker and the existing 16-file limit; auditor and progress output retain
`skips=0`.

The implementation reuses the project's gate-status attestation model rather
than defining a new cryptographic scheme: a private 32-byte local key,
HMAC-SHA256, canonical input bytes, constant-time comparison, explicit
freshness, and the existing key creation/permission lifecycle in
`scripts/gate_status_attestation.py`. The receipt-specific domain
`gludd-ci-batch-receipt-envelope-v1` prevents a receipt signature from being
interpreted as a gate-status signature. No new dependency, public-key format,
remote key service, network lookup, or home-grown cipher is introduced.

This local symmetric trust model is intentionally insufficient for admission.
It proves only that an envelope was produced by a process holding the same
local gate key. It does not prove hosted provenance, protect a compromised
local account, establish multi-host identity, or compose terminal release
evidence. Those properties require a separate reviewed design and cold parity
evidence before any execution can be replaced.

## Envelope and classification

Writers sign the receipt kind, canonical action digest, exact manifest digest,
signer fingerprint, issue time, expiry, schema, algorithm, and domain. The
envelope is strict canonical JSON in an optional private `attestation.json`
artifact. It is at most 4 KiB and contains no key bytes, key path, receipt path,
test name, node ID, payload, exception body, log, environment value, or secret.
Receipts remain limited to JSON and Coverage.py's SQLite data; no pickle or
executable serialization is accepted.

Verification is deterministic and offline. The runner captures one integer
verification epoch and one bounded trust policy at session setup, then performs
no clock, DNS, HTTP, certificate, transparency-log, or key-server lookup per
receipt. At most eight trusted signers and 32 revoked signer fingerprints are
accepted. Envelope validity is seven days by default and cannot exceed 30 days.
The auditor and progress renderer expose only these fixed classifications:

| Classification | Meaning | Future eligibility |
| --- | --- | --- |
| `verified` | Exact envelope, trusted active signer, intact signature, and valid time window. | Pass receipts may be labelled `exact-safe-candidate`; still no skip. |
| `unsigned-legacy` | Valid pre-migration receipt with no envelope. | Refused. |
| `unknown-signer` | No captured trust key matches the fingerprint. | Refused. |
| `tampered` | Signed fields, manifest binding, or signature do not match. | Refused. |
| `revoked` | The captured policy explicitly revokes the signer. | Refused. |
| `expired` | The captured epoch is outside the bounded validity window. | Refused. |
| `malformed` | Schema, types, bounds, file safety, encoding, or JSON are invalid. | Refused. |

Only an authenticated pass receipt can receive the *future-eligible* label.
That label remains report-only: `skip_authorized` is always false. A verified
failure receipt remains `prior-failure-non-reusable`; authentication confirms
its integrity but cannot turn diagnostic evidence into a pass. Mixed or
ambiguous evidence fails closed.

## Migration, rotation, and rollback

Existing unsigned receipts are preserved as `unsigned-legacy`. They may be
structurally validated for bounded diagnostics, but never become timing samples
or future-eligible pass candidates. New writers publish the envelope before the
completion marker, so a completed signed receipt cannot omit its declared
authentication artifact.

The current local key is trusted for the captured session. Rotation naturally
makes older receipts `unknown-signer` unless an explicit bounded transition
policy supplies the old key; a revoked fingerprint is refused even if its key
is present. An expired receipt is not silently renewed. Operators should keep
the old generation cold during rotation and obtain fresh shadow parity rather
than extending an envelope in place.

`--no-shadow-receipt-authentication` is the narrow rollback. It writes legacy
unsigned shadow receipts so execution, coverage, cleanup, replay reporting, and
progress observability continue, while future eligibility is forced to zero.
`--no-shadow-batch-receipts` disables the complete receipt session. The
`GLUDD_RECEIPT_REVOKED_SIGNERS` input accepts only a bounded, duplicate-free
comma-separated set of lowercase SHA-256 fingerprints. An unsafe, unavailable, or
actively revoked key disables the optional shadow receipt session; it never
changes the executed gate result or leaks the key path in its marker.

## Security, privacy, resources, and ZDD

The key and envelope must be regular, owner-held private files; symlinks,
foreign ownership, permissive modes, duplicate JSON keys, non-ASCII content,
unknown fields, oversized input, and unsupported algorithms are malformed.
Signature comparisons use constant-time comparison. Authentication results are
content-free fixed strings, and dataclass representations suppress key bytes.
The runner logs no signer fingerprint, key, path, signature, or receipt content.

Each envelope adds at most 4 KiB inside the existing two-generation/2 GiB
receipt ceiling. Verification retains at most eight 32-byte keys and 32
revocation fingerprints, and traverses only the already bounded 512-candidate,
16 MiB manifest snapshot. Authentication adds no process, worker, thread,
daemon, listener, database, remote service, background pruner, or unbounded
output. Failure receipts retain their 256-per-generation ceiling, and progress
retains its 64-sample/4 KiB output bounds.

The change is zero-downtime validation metadata. It starts after the runner has
selected its existing plan, and it neither touches serving traffic nor changes
a database schema or release asset. Older runners ignore the optional cache
namespace. Rollback changes only shadow metadata; cache removal remains a
separate owned validate/apply operation after the active gate lease ends.

## Mature-tool and practitioner evidence

The standard-library HMAC primitive and existing gate-attestation key lifecycle
remain the cryptographic owners. Practitioner reports shaped the trust and
operational boundary:

- [Pants issue 115](https://github.com/pantsbuild/pants/issues/115) discusses
  invalidating poisoned local and remote cache artifacts, including CI-only
  population, cache-key changes, and pruning. Authentication therefore cannot
  rehabilitate corrupt evidence or replace a force-cold path.
- [cosign issue 1273](https://github.com/sigstore/cosign/issues/1273) records
  historical verification failures after trust material expired or rotated.
  Gludd reports `unknown-signer`, `revoked`, and `expired` separately and keeps
  legacy migration shadow-only instead of silently broadening trust.
- [Pants issue 20133](https://github.com/pantsbuild/pants/issues/20133) reports
  fine-grained remote-cache rate limiting. Receipt verification is local,
  deterministic, bounded, and offline rather than making a network dependency
  part of gate progress.
- [Bazel issue 28598](https://github.com/bazelbuild/bazel/issues/28598) reports a
  credential-exfiltration risk involving remote-cache configuration. This phase
  accepts no remote endpoint and never emits key material, key paths, arbitrary
  artifact paths, or receipt payloads.

These reports support authenticated local evidence, not admission. Promotion
still requires repeated cold/shadow parity, exact node-plan reconciliation,
fresh aggregate branch coverage, hosted provenance, terminal attestation
composition, force-cold controls, and a separately reviewed threat model.

## Verification ledger

The failing-first focused test added an authenticated replay marker and failed
because the runner did not expose authentication state. The repaired marker is
content-free and retains `skips=0`. Focused tests cover exact signatures,
unsigned legacy migration, unknown, tampered, revoked, expired, and malformed
envelopes; private key creation and unsafe-mode/symlink refusal; bounded trust
and revocation sets; pass-only future eligibility; verified failure
non-reusability; progress timing refusal; privacy; and independent rollback.

The authentication suite passes 23/23 and the bounded nine-file serial slice
passes 366/366. Branch-aware Coverage.py reports 88% aggregate across the five
measured scripts: `ci_receipt_auth.py` is 98%, `ci_batch_receipts.py` is 88%,
`ci_batch_replay_audit.py` is 85%, `ci_gate_progress.py` is 92%, and
`run_ci_shards_serial.py` is 88%. No measured file is below 75%.

Full-gate, commit, merge, push, receipt admission, hosted provenance, and
terminal release composition remain outside this shadow-only slice while the
canonical gate owns the repository.
