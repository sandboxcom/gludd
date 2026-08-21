# Guardrail State and CI Verdict Reliability

Status: implemented 2026-08-20

This slice closes four residual guardrail gaps without changing release data or
daemon state. Context-cache corruption triggers a fresh session check, result
ingestion resumes after either one result or a full wave, incomplete gate logs
still expose phase drift, and the CI adapter accepts both the established
positional branch argument and the public `branch=` keyword.

## Runtime contracts

- Context state and session fixture paths are environment-overridable so
  concurrent worktrees cannot share mutable test state. Missing session files
  remain fail-open, while malformed cache JSON represents “never checked” and
  therefore cannot suppress a stale-session denial.
- A text-only turn after any prior subagent result is blocked. The response
  names the result count, directs the agent to codify evidence, and explicitly
  resumes work; a real tool call still clears the stop condition.
- Session drift is evaluated from every phase already written to
  `.gate-status`, even before a terminal marker appears. An incomplete gate is
  tolerated only when its recorded phases are represented in the session
  evidence block.
- CI selection remains exact-SHA and fail-closed. Callers may pass the branch
  positionally or with `branch=`, but ambiguous dual arguments are rejected.
- Gate and gate-refresh publication has two stable public states: an atomically
  installed `RUNNING <epoch> <pid>` record and a complete terminal snapshot.
  Phase results accumulate in `.gate-status.next`; only after the epoch and
  terminal marker are present is that file renamed over `.gate-status`.

## Dated upstream and practitioner evidence

- On 2026-05-14, an OpenCode practitioner reported startup-wide failures in
  issue [anomalyco/opencode#27530](https://github.com/anomalyco/opencode/issues/27530).
  Follow-up diagnosis on that issue identified malformed persisted JSON after
  interrupted writes as a concrete failure mode. This supports atomic state
  writes plus deterministic recovery semantics instead of trusting a damaged
  cache.
- On 2026-06-14, [anomalyco/opencode#32253](https://github.com/anomalyco/opencode/issues/32253)
  documented plugin-hook exceptions escaping into the TUI after an upstream
  regression. Gludd returns a normal permission-denial object for stale
  context and keeps unexpected-hook exceptions bounded, avoiding throw-based
  control flow.
- On 2024-10-22, practitioners documented required GitHub Actions checks that
  remain pending when branch or path filters skip a workflow in
  [GitHub Community discussion #142210](https://github.com/orgs/community/discussions/142210).
  The CI verdict therefore treats missing, skipped, cancelled, or mismatched
  runs as non-green and supplies the detected branch to the documented
  [`gh run list --branch` filter](https://cli.github.com/manual/gh_run_list).
- On 2024-01-08, pytest practitioners documented that concurrent invocations
  can collide even when each uses `tmp_path` in
  [pytest-dev/pytest#11790](https://github.com/pytest-dev/pytest/issues/11790).
  The gate-status assertions therefore use per-test fixtures rather than the
  repository's live, concurrently updated operational record.
- Reviewed 2026-08-20, CPython's long-lived import cache writer documents and
  implements the mature pattern of writing a temporary file and then using
  `os.replace()` for an
  [atomic rename](https://github.com/python/cpython/blob/main/Lib/importlib/_bootstrap_external.py).
  Gludd mirrors that publication boundary with same-directory temporary files
  and `mv`, so readers cannot observe a half-written phase line.

## ZDD, rollback, security, and resources

The Python changes are live-code compatible and require no migration. The
OpenCode plugin edits require the normal OpenCode restart to load; no live
restart is part of this rollout. Existing default state paths and positional
CI calls remain compatible, so old and new callers can overlap during a
zero-downtime deployment.

Rollback is commit-local: revert the context, post-results, gate-drift, or CI
adapter commit independently. No state transformation must be reversed.
During rollback, preserve the exact-SHA CI verdict and stale-session denial;
weakening either would reopen a security boundary.

No background worker, network poller, or unbounded cache is added. Context
fixtures are worktree-namespaced, gate parsing is linear in two small text
files, and CI querying remains one bounded `gh` subprocess with a ten-second
timeout. Gate publication adds at most two small same-directory files and one
rename; interruption leaves the public state at `RUNNING`, never falsely green.
Rollback is a single commit revert and needs no data migration because the
terminal record format is unchanged. Focused verification uses
warnings-as-errors tests, plugin runtime checks, scoped typing/linting, and
per-file branch coverage.
