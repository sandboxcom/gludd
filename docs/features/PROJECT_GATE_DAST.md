# External-project DAST completion gate

## Outcome

An external project can opt its completion gate into a bounded ZAP baseline
scan by declaring a typed `dast` block in `project.yml`. The block is absent by
default, so existing projects continue to run only their declared lint and test
checks. Once declared, DAST is appended to the production completion gate and
is required unless the project explicitly selects shadow mode.

```yaml
name: web-service
allowed_exec:
  - pytest
  - ruff
  - zap-baseline.py
commands:
  lint: ruff check .
  test: pytest -q
dast:
  target_url: http://127.0.0.1:8080
  fail_on: MEDIUM
  max_duration_s: 900
  required: true
```

Alternatively, `start_command` plus `port` starts a target owned by the scan.
The driver derives its target as `http://127.0.0.1:<port>`; a project cannot
substitute a remote target into that mode. Exactly one of `target_url` and
`start_command` is accepted. The only scanner value is `zap-baseline.py`, and
it must be present on `PATH` and in `allowed_exec`.

## Gate semantics

The gate launches one structured scanner command and supplies exactly one
target (`-t`) and one private JSON report (`-J`). It does not execute a free-form
`dast` command. The report is parsed into typed findings and evaluated against
`LOW`, `MEDIUM`, or `HIGH`.

ZAP documents baseline exits as 0 for success, 1 for configured failures, 2
for warnings without failures, and 3 for other failures. Gludd accepts exits 0
and 2 only when a valid report exists and no finding reaches the configured
threshold. Exit 1, exit 3, any unknown exit, scanner launch or timeout failure,
and an absent, unreadable, malformed, or greater-than-8-MiB report all fail the
required completion check. This separation matters because a warning exit is
not a scanner crash, while an empty report is not proof that the target is
clean. See the official [ZAP baseline scan reference][baseline].

The baseline scan is passive: it spiders the target and waits for passive
analysis instead of performing active attacks. ZAP describes it as suitable
for CI/CD, including production sites, but this integration defaults examples
to loopback and does not claim authenticated or active-scan coverage.

## Automation guidance and practitioner evidence

ZAP is migrating packaged baseline scans toward its
[Automation Framework][automation]. The framework supports ordered jobs,
authentication, job tests, and an `exitStatus` job; its documented default
command-line meanings are likewise 0 success, 1 error, and 2 warning. The
structured driver intentionally records the exit and report independently so a
future Automation Framework adapter can preserve the same fail-closed gate
contract instead of treating every nonzero exit as equivalent.

The 2021 [baseline migration note][baseline-2021] reported that supported
baseline options could already switch to the Automation Framework and advised
using `--autooff` only as a temporary compatibility control. A 2021
[practitioner report about an authenticated baseline scan][auth-report] found
that supplying a context alone did not select the forced user; the scan worked
only after explicitly passing `-U`. That long-lived operational sharp edge is
why this first production wiring does not infer authentication from a project
context. Authentication should arrive as a separately typed, tested profile
extension rather than hidden command text.

ZAP issue [#6993][report-permission], opened in 2021, records a mounted work
directory owned by the wrong user preventing the scanner from writing its
configuration and report. That durable practitioner failure is why a successful
child exit is never sufficient evidence here: Gludd requires its owned private
report, fails closed when it is missing, and uses a project/workspace namespace
for scanner identity instead of sharing an anonymous process or output path.

## Security and resource ownership

- Profile validation rejects shell metacharacters in `start_command`, and both
  target and scanner launch use argv with `shell=False` semantics.
- The scanner is fixed to ZAP baseline, executable allowlisting is fail-closed,
  and target URLs accept only HTTP or HTTPS. Loopback is the local default;
  RFC 1918, link-local, and cloud-metadata addresses are denied for explicit
  targets.
- Scanner and health-check clients do not trust ambient proxies. Scanner
  children receive a sanitized environment with proxy variables removed and
  `NO_PROXY=*`.
- A process-local single-scanner slot prevents overlapping scans. Scanner
  processes receive an internal `GLUDD_PROCESS_NAMESPACE` value in the form
  `gludd-dast-scanner-<project/workspace namespace>`, and an owned target is
  confined to its declared loopback port so parallel projects do not share
  identity. The runner derives this child-only value; operators should not set
  it globally.
- Scanner duration is capped at 900 seconds. Owned target processes start in a
  new process group and teardown escalates from `SIGTERM` to `SIGKILL`; the
  namespaced temporary JSON report is removed in the same `finally` path.
- Output is represented by bounded structured fields rather than unbounded
  scanner stdout or stderr. JSON is size-checked before parsing and cannot
  exceed 8 MiB.

## Zero-downtime delivery and rollback

This is completion control-plane wiring; it does not restart Gludd or mutate a
running target. Roll out by first deploying code with no `dast` block, then add
the block with `required: false` to observe results, and finally atomically set
`required: true`. In-flight completions retain the profile loaded for that gate,
while later completions see the new profile.

The independent rollback is removal of the `dast` block. That immediately
stops new DAST admissions while leaving lint and test completion checks in
place. Before source rollback, allow any owned scan to finish or terminate its
namespaced process group; temporary report teardown is idempotent. No schema,
database, service, or remote-state rollback is required.

## Verification

`tests/integration/test_project_gate_dast_wired.py` covers typed opt-in,
production gate invocation, warning/failure exit meanings, threshold findings,
missing and malformed reports, the 8-MiB bound, proxy isolation, namespacing,
timeout, and independent rollback. Focused coverage is configured in
`config/coverage_project_gate_dast.ini` with an 85% aggregate floor and the
repository's 75% per-file floor.

[baseline]: https://www.zaproxy.org/docs/docker/baseline-scan/
[automation]: https://www.zaproxy.org/docs/automate/automation-framework/
[baseline-2021]: https://www.zaproxy.org/blog/2021-06-15-baseline-scan-changes/
[auth-report]: https://stackoverflow.com/questions/66374190/owasp-zap-against-netlify-password-protected-site
[report-permission]: https://github.com/zaproxy/zaproxy/issues/6993
