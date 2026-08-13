# Raft Response Accounting and Election Timing

## Scope

Gludd's in-process Raft model implements leader election, append replication,
commit advancement, snapshots, and membership changes. This contract covers the
message-accounting and logical-time boundary needed for deterministic progress;
it does not add a network transport or claim Byzantine fault tolerance.

The normative safety model remains the Raft paper, particularly election safety,
log matching, and current-term commit restrictions:
[Ongaro and Ousterhout, Raft](https://raft.github.io/raft.pdf).

## Response identity contract

A granted vote counts only when all of these are true:

- the response term exactly matches the candidate's current term;
- the response names a currently configured peer;
- that peer has not already been counted in the candidate's vote set; and
- the candidate is still a candidate.

`RequestVoteResponse.voter_id` is additive and optional at construction for
compatibility. An identity-free or unknown response cannot be attributed and is
ignored fail closed.

An append response identifies its follower and the absolute index that follower
matched. The leader maintains `next_index` and `match_index` independently per
configured peer. Success advances only that peer; rejection backs up only that
peer's next prefix. A higher-term response demotes the leader, and stale,
identity-free, or non-member responses cannot advance commit state.

## Log and time invariants

A follower first checks `prev_log_index` and `prev_log_term`. A mismatch
rejects the request without truncating the follower: truncation begins only when
a subsequent incoming entry conflicts at an index whose prefix has already been
proven.

Election timers use the same logical `now` supplied to `tick()`. RPC handlers
therefore never mix wall-clock monotonic values with a simulated or externally
owned clock. Valid leader traffic and granted votes defer election beyond both
the configured maximum and two heartbeat intervals. Candidate retry jitter
combines the supplied random sample with a stable membership rank, preventing a
deterministic test clock—or correlated entropy after simultaneous restart—from
locking every node into the same split-vote cadence.

Lower-term AppendEntries remains stale traffic: it is rejected and cannot demote
a current candidate. Equal- or higher-term leader traffic resets the follower
deadline.

## Security and observability

Raft assumes authenticated transport between configured members. This module
adds no authentication; the transport must bind the claimed response identity
to its authenticated peer. The state machine still limits counting and
replication changes to configured peer IDs, deduplicates votes, and rejects
unknown identities.

Transport instrumentation should record message type, term, authenticated peer,
success, matched index, role transition, and retry index. It must not log command
payloads by default. A deployment alarm should cover repeated elections,
leaderless duration, unknown response identities, and sustained decrement of a
follower's `next_index`.

## Zero-downtime rollout and rollback

The new response fields are additive and default to `None`, preserving older
Python call sites that construct responses with only term and outcome. Roll out
response-producing followers first so existing consumers can ignore the added
attributes, then upgrade candidates/leaders after a majority can emit identity.
The new consumer ignores identity-free responses rather than guessing their
sender.

Canary a three-node group first. Require one stable leader, successful replication
to a majority, and no candidate after sustained heartbeats before expanding.
During rollback, revert leaders/candidates first and followers second. No log,
snapshot, or persisted-state migration is required, so rollback does not rewrite
consensus data.

## Practitioner evidence

A 2014 etcd operator report documents repeated near-election-timeout heartbeats
and unexpected leader changes on an otherwise lightly loaded same-LAN cluster.
That long-lived report demonstrates why heartbeat/election timing must be an
observable invariant rather than a test-only detail:
[etcd issue #915](https://github.com/etcd-io/etcd/issues/915).

A 2022 report describes frequent elections disrupting availability; maintainers
pointed to randomized election deadlines, pre-vote, and measuring peer latency:
[etcd issue #14071](https://github.com/etcd-io/etcd/issues/14071).
Gludd's bounded scope does not implement pre-vote, but it does ensure valid
traffic resets one logical clock and deterministic retries do not remain
synchronized.

## Verification

The focused family covers three- and five-node election, even quorums, distinct
vote identity, forged/non-member rejection, follower-specific replication retry,
commit/application, stale terms, prefix conflicts, snapshots, membership, and
logical heartbeat reset. It passes 51 tests under strict warnings with 89.28%
branch coverage for `src/general_ludd/distributed/raft.py`.
