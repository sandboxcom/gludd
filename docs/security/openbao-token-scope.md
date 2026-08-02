# OpenBao per-agent token scope (D-15, bounded phase)

Status: **partially implemented; D-15 remains open**. This phase provides the
deterministic local security contract. It does not claim the live OpenBao,
multi-worker, snapshot/restore, or stale-token acceptance required to close the
backlog item.

## Security contract

Every delegated scope is an intersection, never a copy of the request:

```text
granted paths        = parent paths intersect requested paths
granted capabilities = parent capabilities intersect requested capabilities
```

The input grammar is intentionally smaller than the full OpenBao ACL grammar.
A mount and path must be canonical, relative, bounded, and made from safe path
segments. A single `*` is accepted only as the last path segment. Absolute
forms, empty/dot/parent segments, percent encoding, backslashes, embedded
wildcards, and the `sys`, `auth`, `identity`, and `cubbyhole` mounts fail
closed. This makes intersection deterministic: an exact path intersects only
itself, and a subtree intersects a descendant by selecting the narrower side.
Sibling and parent-only requests produce no grant.

Scoped minting writes one hash-named policy, then creates an AppRole with only
that policy and `token_no_default_policy=true`. A role-creation failure removes
the newly written policy before surfacing the error. Raw agent IDs are not used
in policy names.

## Finite operator controls

The development branch is the single source for these configuration keys:

| Key | Default | Compiled accepted range |
|---|---:|---:|
| `approle_secret_id_ttl_seconds` | 600 | 30..86400 |
| `approle_token_ttl_seconds` | 3600 | 30..86400 |
| `approle_token_max_ttl_seconds` | 3600 | 30..86400 |
| `approle_secret_id_num_uses` | 1 | 1..100 |
| `approle_token_num_uses` | 128 | 1..100000 |

The token TTL cannot exceed its explicit maximum. Zero is rejected because it
means unlimited in OpenBao. Scoped roles require SecretID binding, set both
the renewable and explicit maximum token TTL, and limit both SecretID logins
and token requests.

The dispatcher invokes its STS finalizer from `finally`, so successful, failed,
cancelled, and timed-out work all use the same revocation path. The revoker
deletes both the AppRole and its hash-derived policy; configured TTL and use
limits remain the fail-closed backstop if OpenBao is temporarily unavailable.
Deployments must wire a `TokenRevoker` into `SubagentTokenInjector` before
claiming this terminal cleanup is active.

## Redacted evidence

`OpenBaoScopeEvidence` is the only scope decision payload emitted by the mint
path. It contains a typed event name, domain-separated SHA-256 prefixes for the
subject and canonical scope, a path count, granted capability names, and a
bounded reason code. It never contains a raw tenant, agent, mount, path, policy,
RoleID, SecretID, or token. Identical decisions generate identical local test
evidence, which allows correlation without disclosure.

## ZDD rollout and rollback

For a live generation, operators SHALL:

1. Load and validate the entire candidate config before admitting work. Invalid
   TTL order, unlimited use counts, traversal, or a disjoint scope aborts only
   the candidate generation.
2. Create a new hash-named policy and AppRole without modifying the active role.
   Authenticate a canary and prove allowed reads plus sibling, parent-only,
   `sys`, traversal, and write denials.
3. Publish redacted grant evidence, atomically expose the new credentials, and
   pin accepted work to that generation. Never log or persist SecretIDs.
4. Drain old work, revoke its AppRole and policy for every terminal outcome,
   then prove old credentials fail. If promotion fails, remove only the
   candidate role/policy and keep the active generation untouched.
5. Inject failure after policy write, role write, credential delivery, each
   terminal outcome, and revocation. Each run must reconcile to zero orphaned
   roles, policies, accessors, and usable stale tokens.

The local suite exercises validation, intersection, exact HCL, rollback,
finite limits, redaction, and completion/failure/cancellation finalization:

```text
make test-specific TESTFILE=tests/unit/test_openbao_scope.py PYTEST_ARGS='-q --tb=short'
```

Closing D-15 still requires a real OpenBao regression probe that authenticates
the minted AppRole, performs positive and negative reads, observes redacted
events across at least two workers, restores a snapshot, and proves expired or
revoked credentials cannot read. It also requires production construction to
wire scoped parent/request inputs and the revoker into every injector.

## Upstream evidence and long-lived reports

- The [OpenBao AppRole guide](https://openbao.org/docs/auth/approle/) documents
  policy-bound machine identities, SecretID TTL/use limits, and token TTL/use
  limits. It explicitly notes that zero permits unlimited use, which is why
  Gludd rejects zero rather than passing it through.
- The [AppRole API reference](https://openbao.org/api-docs/auth/approle/)
  defines `token_policies`, `token_no_default_policy`,
  `token_explicit_max_ttl`, `secret_id_num_uses`, and `token_num_uses`; the
  implementation uses those mature controls rather than inventing a token
  format.
- OpenBao's [policy documentation](https://openbao.org/docs/2.5.x/concepts/policies/)
  explains that attached policies are evaluated on token use. Gludd therefore
  creates a dedicated immutable-per-generation policy instead of relying on a
  shared mutable broad policy.
- User report [openbao/openbao#522](https://github.com/openbao/openbao/issues/522),
  open since September 2024, demonstrates a single-use token reappearing in an
  irrevocable-looking state after Raft snapshot restore. The reporter also
  observed that a finite TTL eventually removed the stuck token. This is why
  Gludd treats explicit revocation, finite use count, and finite TTL as
  independent defenses and keeps snapshot/restore proof in D-15 acceptance.
- User report [openbao/openbao#573](https://github.com/openbao/openbao/issues/573),
  open since October 2024, describes increasing AppRole authentication,
  operation, and revocation latency on Raft in EKS. ZDD tests must therefore
  bound and observe revocation latency rather than assuming a fast backend.
