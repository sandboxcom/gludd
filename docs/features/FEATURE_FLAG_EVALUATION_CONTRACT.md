# Feature Flag Evaluation Contract

**Status:** Implemented
**Owner:** Runtime configuration
**Target:** v0.1.0-beta.4

## Problem

Feature-flag decisions must remain explainable during staged deployment. A
one-hundred-percent gradual rollout previously evaluated to the correct boolean
value but reported only `default value: True`, losing the rollout stage and
percentage that authorized the decision. A stale test also treated an inverted
targeting rule as though the excluded value were enabled.

## Behavioral contract

1. `TargetingRule(..., invert=True)` negates the underlying predicate. A value
   matched by the non-inverted predicate is excluded and an unmatched value is
   targeted.
2. When targeting rules are configured and none match, evaluation returns
   disabled with `no targeting rules matched`.
3. A registered `GradualRollout` that enables a flag reports the rollout stage
   and percentage in its audit reason, including the 100% boundary.
4. Override precedence and deterministic percentage bucketing remain unchanged.

## Practitioner evidence

The OpenFeature community has documented that portable dynamic targeting is
difficult because rule expressions and fractional assignment can map
inconsistently across implementations
([open-feature discussion #249](https://github.com/orgs/open-feature/discussions/249)).
That long-lived practitioner discussion supports pinning inverted-rule semantics
and evaluation provenance as explicit behavioral contracts rather than inferring
them from a boolean result.

## Zero-downtime deployment and rollback

This change does not alter persisted flag schemas or rollout assignment. Old and
new processes make identical allow/deny decisions, so they may run concurrently
during a rolling deployment. New processes emit more precise rollout reasons.
Rollback is a source-only revert and requires no data migration. Promotion must
stop if decision parity, audit logging, or health probes regress.

## Verification

- Exact regression nodes cover inverted exclusion and the 100% rollout reason.
- The complete feature-flag suites cover overrides, dependencies, targeting,
  deterministic bucketing, staged progression, concurrency, and audit history.
- Coverage must remain at least 85% aggregate and 75% per touched source file.
