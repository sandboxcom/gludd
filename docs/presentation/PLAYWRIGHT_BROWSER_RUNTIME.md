# Playwright presentation browser runtime

## Acceptance contract

The presentation browser lane requires both pinned Chromium and pinned WebKit.
Neither a probe failure nor an upstream WebKit defect converts an assertion to a
skip, retry, or pass. The native Safari smoke is diagnostic evidence only; it
does not replace the WebKit acceptance.

Before pytest starts, each selected engine must complete a hermetic readiness
probe: launch headless, create a page, navigate that page to an in-memory
`data:` document through `domcontentloaded`, assert the expected title, and
close. This proves the page path that a binary-existence check cannot prove.

## Why the probe has an external deadline

[Playwright issue #41870](https://github.com/microsoft/playwright/issues/41870)
documents an Apple Silicon macOS 26.5 failure where bundled WebKit launch
succeeds but `browser.newPage()` can block forever. The report traces the stall
to WebKit's NetworkProcess cookie-storage initialization and a synchronous
`analyticsd` lookup; Playwright's own 30-second timeout does not fire. A timeout
inside the same Playwright process therefore is not a reliable containment
boundary.

Practitioner reports show the wider failure family has persisted beyond a
single minimal reproduction:

- [Issue #42385](https://github.com/microsoft/playwright/issues/42385) reports
  WebKit-only `page.goto()` stalls on macOS after roughly 65 navigations. No
  request reaches the local server, retrying the same page does not recover,
  and Chromium completes the same suite.
- [Issue #42273](https://github.com/microsoft/playwright/issues/42273) reduces a
  long-lived PWA flake on macOS arm64 to a context-wide WebKit reload wedge.
  Larger Playwright timeouts do not resolve it; a fresh context is required.
- A practitioner thread in
  [r/Playwright](https://www.reddit.com/r/Playwright/comments/1tsyddy/playwright_browsers_freezing_github_actions/)
  reports browser setup occupying GitHub Actions for more than five hours
  without an error. The community response explicitly recommends a workflow
  deadline so a stuck browser operation cannot consume the runner indefinitely.

These reports support a process boundary, not an application-level retry. The
readiness probe runs through Gludd's shared owned-process supervisor in a new
process session with a 15-second outer deadline. On timeout the supervisor
sends `SIGTERM` to that owned process group, allows two seconds for cleanup, and
escalates that same group to `SIGKILL`. The pytest viewport matrix starts only
after the probe passes. Chromium and WebKit follow the identical path, so the
diagnostic does not special-case WebKit into a pass.

Each pytest viewport then receives its own namespaced process session and the
plan's finite 30-to-900-second deadline. That outer supervisor owns pytest and
every Playwright browser descendant as one process group; it terminates,
escalates, joins, and closes that group before returning timeout exit code 124.
The inner pytest subprocess retains a deadline ten seconds beyond the outer
one as a defense-in-depth backstop, so the group-aware supervisor remains the
normal teardown owner.

## Failure output and response

A failed probe emits exactly one JSON root-cause record on stderr. Its stable
fields are `schema_version`, `event`, `browser`, `phase`, `failure_kind`,
`platform`, `timeout_seconds`, `root_cause`, `detail`, `reference`, and `action`.
The transported child exception message appears only in `detail`; raw
tracebacks cannot bury the typed diagnosis. A macOS WebKit timeout links
issue #41870 and says explicitly to keep acceptance red.

To distinguish a bundled-WebKit failure from the system browser, run the native
Safari smoke with every target variable explicit:

```console
make presentation-safari-test PRESENTATION_SAFARI_VALIDATE_ONLY=0 PRESENTATION_SAFARI_DRIVER=/usr/bin/safaridriver PRESENTATION_SAFARI_OUTPUT=/tmp/gludd-presentation-safari PRESENTATION_SAFARI_TIMEOUT=60
```

Then rerun the mandatory two-engine lane; a Safari pass is not a waiver:

```console
make presentation-browser-test PRESENTATION_BROWSER_VALIDATE_ONLY=0 PRESENTATION_BROWSER_ENGINES='chromium webkit' PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser PRESENTATION_BROWSER_TIMEOUT=300
```

## Startup probe-only diagnostic

Operators can isolate browser readiness from the presentation assertions with
the explicit probe-only mode. The observed-command wrapper keeps both watchdog
layers visible, sets every target variable, and selects the same owned cache as
acceptance:

```console
make run-watched CMD='.venv/bin/python scripts/run_presentation_browser_tests.py --startup-probe-only --browser webkit --browser-root /tmp/gludd-playwright-browsers --output-root /tmp/gludd-presentation-browser/webkit --timeout-seconds 300' OBSERVED_LABEL=presentation-webkit-startup-probe RUN_ID=manual-presentation-webkit-startup-probe STALL_SECS=30 MAX_SECS=30 LOG=.gate-logs/observed/presentation-webkit-startup-probe/manual.log OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_RETAIN_RUNS=20
```

This mode validates the locked presentation profile and installed executable,
then runs only the externally bounded launch, first-page creation, and in-memory
navigation handshake. It never runs pytest and does not weaken Chromium or
WebKit acceptance. The probe deadline remains 15 seconds; `--timeout-seconds`
continues to validate the surrounding runner plan and cannot make the probe
unbounded.

The runner emits one JSON result record with event
`presentation-browser-startup-probe`. It reports `passed` with exit code `0`,
`failed`/`process-error` with exit code `1`, or `failed`/`timeout` with exit code
`124`. The stable record contains only the engine, event and phase identifiers,
status, failure kind, platform, deadline, exit code, schema version, and the
static issue reference when a macOS WebKit timeout matches issue #41870. It
never includes the probe URL or document, DOM content, cache/output paths,
executable path, child exception text, or traceback. Supervisor lifecycle and
heartbeat lines contain only the namespaced process identity and timing.

On timeout, the shared supervisor terminates the exact
`gludd-presentation-<engine>-probe` process group, escalates after its bounded
grace period, joins the child, and closes the process handle before emitting the
failure record. A timeout result therefore means the owned probe has already
been reaped; it is not a request for a host-wide browser kill.

## ZDD and resource ownership

This is a fail-fast preflight and does not mutate a running service, published
deck, database, or deployment. Existing presentation traffic remains available
while a candidate revision is tested, satisfying the zero-downtime deployment
boundary: promotion stays blocked on failure and the currently published
revision remains untouched. Browser artifacts stay under the explicit
`/tmp/gludd-*` namespaces, and only the probe's new process session is signaled.
Each acceptance viewport uses a separate namespaced session with the same
ownership rule. No host-wide browser cleanup is used.

## Rollback

Rollback is a code-only revert of the runner, its focused tests, and this note.
It removes both the acceptance preflight and `--startup-probe-only`; there is no
schema migration or persistent state to unwind, and the namespaced browser
cache is unchanged. After reverting, validate the static two-engine plan with:

```console
make presentation-browser-test PRESENTATION_BROWSER_VALIDATE_ONLY=1 PRESENTATION_BROWSER_ENGINES='chromium webkit' PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser PRESENTATION_BROWSER_TIMEOUT=300
```

Removing the probe also removes the outer containment for issue #41870, so a
rollback is safe for deployed traffic but intentionally restores the old risk
that local or CI presentation acceptance can hang until its broader watchdog
fires.
