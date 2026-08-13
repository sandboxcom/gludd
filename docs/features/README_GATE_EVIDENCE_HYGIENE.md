# README Gate Evidence Hygiene

## Problem

The README phase table presented a historical beta release, shard count, and
coverage percentage as if they were current facts. Those values can change
without the prose changing, so a green-looking README can contradict the
authoritative gate and release state.

## Contract

README phase rows describe the capability and point readers to `.gate-status`
and `TASKS.md` for measured results. They do not hard-code test counts,
coverage percentages, shard cardinality, or a release candidate as current
truth. The existing drift detector fails closed when coverage language and a
literal percentage appear together.

## Practitioner evidence

GitHub Community discussion
[#52616](https://github.com/orgs/community/discussions/52616) is a long-lived
request about representing matrix-build status in README tables. The discussion
uses workflow-backed badges so each platform's state comes from CI rather than
manually copied prose. That same principle applies here: mutable release and
quality evidence stays attached to the live gate.

## ZDD, security, and resources

This documentation-only change has no application downtime and starts no
services. Removing stale success claims is fail-closed: readers are directed to
the current gate rather than being shown an obsolete pass. It adds no generated
assets, background processes, or persistent cache.

## Verification

The README drift unit test must pass against the live file, Markdown lint must
remain green, and task/spec ledgers must continue to validate.
