# Beta4 Full-E2E Recovery

## Incident

The beta4 full-E2E run exposed three independent contract failures:

- the authentication test treated every public `GET /readyz` response as a
  readiness success, although the full harness intentionally keeps an
  automatically-created event loop unavailable until an explicit probe task is
  installed; and
- the Azure Container App materializer copied the vLLM module into its isolated
  Terraform root but left the cost-watchdog module outside that root, with an
  invalid `../../modules` reference. `terraform init` therefore failed before
  validation with an unreadable-module error; and
- two enforcement simulators were grouped correctly inside one xdist run but
  still raced on global `/tmp/gludd-*` files when the outer E2E scheduler ran
  the test files in independent pytest processes.

## Runtime contracts

Authentication and readiness are separate signals. `/healthz`, `/readyz`,
`/docs`, and `/openapi.json` remain public even when protected routes fail
closed because no PSK is configured. `/readyz` returns 503 while the daemon
cannot accept work; that status is not an authentication denial. The regression
checks both sides at once: public paths never return the authentication error,
while a protected admin path does.

An Azure Container App validation directory is now self-contained. One ordered
module inventory drives both source localization and asset copying, so the vLLM
and cost-watchdog references cannot drift independently. Missing repository
assets fail before Terraform starts instead of silently producing a partial
root.

Mutable test state is scoped at the process boundary that owns it. Direct
enforcement-test invocations retain the production-compatible `/tmp` default,
while the full runner assigns each test file a private state directory below
its namespaced resource root. One validated path helper is shared by both
simulators. Xdist grouping continues to serialize their tests inside a pytest
process, but correctness no longer depends on grouping crossing process
boundaries where it cannot operate.

## Practitioner evidence

HashiCorp Terraform issue
[#23333](https://github.com/hashicorp/terraform/issues/23333), opened
2019-11-09, records years of practitioner reports around upward-traversing local
module paths and `Unreadable module directory`; maintainers confirmed that
root-local `./...` paths behaved correctly. Issue
[#22785](https://github.com/hashicorp/terraform/issues/22785), opened
2019-09-12 and still open when reviewed on 2026-08-20, includes a 2022 CI report
where a packaged module tree accidentally omitted nested modules. Both reports
support shipping a complete, root-local module graph rather than depending on
the source checkout's parent layout.

Kubernetes issue
[#122591](https://github.com/kubernetes/kubernetes/issues/122591), opened
2024-01-04 and still open when reviewed on 2026-08-20, documents the operational
difference between readiness and liveness: a failed probe correctly removes a
workload from service without necessarily implying process death. Long-lived
issue [#89898](https://github.com/kubernetes/kubernetes/issues/89898), opened
2020-04-06, records intermittent readiness failures under real resource and
connection pressure. Those reports reinforce that a 503 readiness signal must
not be reinterpreted as a failed public-authentication contract.

Pytest issue
[#11790](https://github.com/pytest-dev/pytest/issues/11790), opened 2024-01-08,
documents user reports that separate concurrent pytest invocations can collide
even when each invocation expects a unique `tmp_path`; the accepted remedy is
to uniqueify the temporary-root prefix. Pytest-xdist issue
[#18](https://github.com/pytest-dev/pytest-xdist/issues/18), open since
2015-12-02, explains that grouping and fixture locality are scheduler concepts
within one xdist controller. Together they support per-invocation state roots
instead of assuming an xdist group coordinates independently launched pytest
processes.

## Committed-head gate follow-up

A later committed-head gate exposed four portability assumptions: token-bucket
tests measured scheduler time, a tiny Diffie-Hellman test group assumed random
samples were unique, the base dependency set tried to run an OpenCV media test,
and a nested E2E command did not quote its private basetemp. The same run found
a stale hard-coded deploy-key name and an unused async database double that
leaked an unawaited coroutine warning.

Uv issue
[#14645](https://github.com/astral-sh/uv/issues/14645), opened 2025-07-16,
records a CI user finding that an optional dependency is absent unless the
corresponding `--extra` is explicitly supplied to `uv sync`. The opencv-python
project tells server and CI users to select exactly one headless wheel, while
issue [#677](https://github.com/opencv/opencv-python/issues/677), open since
2022-06-10, records conflicts caused by installing overlapping OpenCV variants.
Gludd therefore keeps OpenCV out of the core runtime and installs the locked
`game-e2e` extra only in the dedicated GHE job.

Rate-limit arithmetic now uses an injected monotonic clock in tests, so host
scheduler timing is outside the acceptance boundary. The game job performs a
frozen optional-extra sync before running its media suite; base-runtime gates
skip only the media node when OpenCV is absent. Every E2E file retains an
independently quoted basetemp below its owned run root.

This changes only tests, their dedicated CI environment, and ephemeral runner
state. A running Gludd deployment is neither restarted nor migrated. Rollback
is a code/workflow revert, with no database, model, tag, or release-asset
mutation. The CI runner owns and removes its basetemp through the existing
bounded teardown path.

## Dual-track committed-head release contract

Beta4 validation is not a local-first, hosted-later sequence. Each complete
change is committed and pushed to its feature branch as soon as its focused
checks and collection gate pass. GHA/GHE must then report a run for that exact
commit SHA while the full local committed-head gate runs concurrently. A local
result from an uncommitted tree cannot substitute for hosted evidence, and a
hosted result for an earlier SHA cannot validate later local edits.

Every repair discovered by either lane is a separate focused, tested commit and
is pushed immediately so both lanes reconverge on the new SHA. Promotion from
the feature branch to `development`, from `development` to `master`, and the
`v0.1.0-beta.4` tag each require local and hosted green results for the same
immutable commit. A failed or absent hosted run blocks promotion even when all
local checks pass. This ordering keeps rollback code-only, exposes platform and
runner differences early, and prevents a long uncommitted local sweep from
delaying the CI feedback that the release depends on.

## Verification and resources

The focused matrix runs one deterministic authentication/readiness test in both
normal and `GLUDD_E2E_ACTIVE=1` modes, the Azure materialization unit family,
the real state-free Terraform init/validate E2E, and both enforcement files in
two simultaneous outer-scheduler processes. The tests use pytest-owned
temporary directories, two bounded workers, no cloud credentials, and no cloud
resource creation. Every copied module is repository-owned and contains no
state or secrets.

## ZDD and rollback

The change affects newly-created validation and deployment directories only.
Existing Terraform state and running deployments are untouched, so rollout is
zero-downtime. Rollback is a code-only revert: no state migration is required.
During a mixed-version rollout, old daemon processes continue using their
already-materialized directories while new processes produce the complete
root-local module graph. Enforcement simulator state is ephemeral beneath each
file's E2E base directory and is removed by the existing bounded runner cleanup;
it never mutates a live plugin session's shared state during a full-suite run.
