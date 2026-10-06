# Reveal.js Render Resilience and Source-Link Specification

Status: implementation-ready
Contract version: `reveal-render-resilience/v1`
Scope: the generated Reveal.js deck, GitHub Pages artifact, local preview
server, source citations, and presentation-focused browser tests

## Outcome

The presentation must never silently replace a Mermaid chart with empty space.
It must render diagrams only when Reveal has made their slide measurable,
serialize asynchronous rendering, and expose a readable source fallback on
every failure. The published artifact must be self-contained and work below the
GitHub Pages project prefix `/gludd/` without runtime CDN availability.

Every citation that names a repository file must be an immutable GitHub link.
When it names a line or range, the link must include the corresponding GitHub
`#L` anchor. The same anchor opens a read-only local code viewer during
`make deck-serve`, with the cited range selected and scrolled into view.

## Current failure and root cause

The tracked deck currently loads
`reveal.js-mermaid-plugin@2.1.0` as a script but does not register
`RevealMermaid` in Reveal's plugin list. It instead calls the bundled global
Mermaid API directly:

1. after `Reveal.initialize()` resolves, `mermaid.run()` scans every diagram;
2. Reveal keeps non-current slides under `display: none`, so most diagram
   containers do not have measurable geometry at that moment;
3. `slidechanged` fires at the beginning of a transition and calls a second
   `mermaid.run()` without await; and
4. there is no rejection handler, state marker, source fallback, or test that
   proves an SVG is visible.

This is two independent, reproducible races rather than a syntax-only problem.
Mermaid's long-lived
[`mermaid-js/mermaid#1846`](https://github.com/mermaid-js/mermaid/issues/1846)
reports that a chart rendered beneath `display: none` is blank when later made
visible. The Reveal-specific
[`mermaid-js/mermaid#1824`](https://github.com/mermaid-js/mermaid/issues/1824)
shows later slides receiving a `16` by `16` view box. Mermaid maintainers also
record in
[`mermaid-js/mermaid#3577`](https://github.com/mermaid-js/mermaid/issues/3577)
that asynchronous renders cannot safely run concurrently and calls made in
succession without await can fail inconsistently without an obvious error.

The timing in the deck is therefore exactly the timing the upstream projects
warn about. Reveal documents that `slidechanged` fires immediately, while
[`slidetransitionend`](https://revealjs.com/events/#slide-transition-end) fires
after the new slide is fully visible. Mermaid documents `await mermaid.run()`
as the supported complex-integration API and recommends `startOnLoad: false`
for caller-controlled rendering in its
[`mermaid.run` guidance](https://mermaid.js.org/config/usage.html#using-mermaid-run).

## Practitioner evidence retained with the feature

These reports are regression inputs, not incidental research notes:

- [`mermaid-js/mermaid#1846`](https://github.com/mermaid-js/mermaid/issues/1846)
  has remained open since January 2021. Its minimal reproduction is a hidden
  parent that later becomes visible, which is how Reveal manages inactive
  slides.
- [`mermaid-js/mermaid#1824`](https://github.com/mermaid-js/mermaid/issues/1824)
  has documented Reveal charts rendered at effectively invisible dimensions
  since December 2020.
- [`mgaitan/sphinxcontrib-mermaid#126`](https://github.com/mgaitan/sphinxcontrib-mermaid/issues/126)
  records the same zero-dimension behavior in hidden tabs and explicitly calls
  out Reveal.js. Practitioners reported that refresh-on-visibility worked;
  the eventual fix deferred work until intersection/visibility.
- [`mermaid-js/mermaid#4140`](https://github.com/mermaid-js/mermaid/issues/4140)
  attributes an intermittent async render error to competing initialization
  and recommends disabling automatic start.
- [`zjffun/reveal.js-mermaid-plugin#5`](https://github.com/zjffun/reveal.js-mermaid-plugin/issues/5)
  records inherited Reveal `pre` line-height clipping diagram text. Browser
  acceptance must check bounding boxes, not merely the existence of an SVG.

The implementation documentation must keep these links. They explain why a
static syntax lint and an assertion for `<svg>` alone are insufficient.

## Dependency decision

### Mermaid integration

Use the maintained, Reveal-specific
[`reveal.js-mermaid-plugin@11.15.0`](https://github.com/zjffun/reveal.js-mermaid-plugin)
and register `RevealMermaid` in the `plugins` array. Version `11.15.0` was the
current published plugin during this design review and carries Mermaid
`11.15.0`; it replaces the three-year-old `2.1.0` bundle. Its documented
integration exists specifically because direct Mermaid start-on-load can be
wrong under Reveal.

Do not write a second Mermaid parser or layout engine. A small Gludd runtime
controller may own health state, visibility scheduling, fallback text, and
telemetry around the upstream plugin. It must not duplicate Mermaid syntax or
rendering logic.

### Local code viewer

Use vendored
[`ace-builds@1.44.0`](https://github.com/ajaxorg/ace) for the local, read-only
code viewer. Ace is actively maintained, supports large documents and more than
120 language modes, and provides the two operations this feature needs:
`gotoLine` and `selection.setSelectionRange`. Its documented `readOnly` option
prevents the presentation from presenting an editor-like mutation surface.

Reveal's existing highlight plugin is retained for embedded snippets, but it is
not a file viewer: it does not fetch an arbitrary source file, select a range,
or center that range after navigation. Monaco adds workers and base-path
configuration that are unnecessary and fragile under a Pages subpath.
CodeMirror 6 would require a new module bundle. Ace's prebuilt no-conflict
distribution works as a pinned static asset without a JavaScript build system.

### Browser proof

Use the official Python Playwright stack, pinned as `playwright==1.63.0` and
`pytest-playwright==0.9.0` in a presentation-test dependency group. The
[official test-runner documentation](https://playwright.dev/python/docs/intro)
recommends the pytest plugin. Chromium installation and cache paths must be
owned by Gludd, namespaced to this project, bounded, and observable through
make targets; browser tests must not silently skip in the Pages workflow.

## Asset ownership and Pages subpath contract

The runtime must not depend on third-party CDNs after the page response. Check
the minified distributions into `docs/presentation/deck/vendor/` with their
license texts and a machine-readable manifest containing package, version,
source URL, SHA-256, and destination. The required set is:

- Reveal.js `5.1.0`: core CSS, black theme, core runtime, highlight, and notes;
- `reveal.js-mermaid-plugin@11.15.0`: the browser bundle that includes its
  compatible Mermaid runtime; and
- `ace-builds@1.44.0`: no-conflict core, selected modes, and one theme.

Every HTML/CSS/JavaScript asset reference must use relative URLs such as
`./vendor/reveal/reveal.css`; no asset may begin with `/`, include `@latest`, or
call jsDelivr, unpkg, or another runtime CDN. This makes the same artifact work
at `/`, `/gludd/`, and a scratch preview directory. The asset manifest is
fail-closed: missing, unexpected, or digest-mismatched files fail the build.

The Pages workflow must validate the exact upload directory before deployment.
Its browser job serves that directory beneath `/gludd/`, records every request,
and fails on any same-origin non-2xx asset response. “No 404” includes source
maps when they are referenced; omit source-map comments if maps are not
vendored. Validation runs on `development` and pull requests that touch the
presentation, while deployment remains restricted to the protected release
branch.

## Diagram lifecycle contract

### Progressive source and state

Each diagram retains its original source in the DOM. Source is readable before
JavaScript runs and after any renderer failure. JavaScript may hide that source
only after a usable SVG is verified. Each `.mermaid` host exposes exactly one
`data-mermaid-state` value:

- `pending`: original source visible, no render has started;
- `rendering`: source remains available to the failure handler;
- `rendered`: one SVG exists with finite, positive viewBox dimensions and a
  non-zero bounding box; and
- `failed`: source is visible with a “Diagram render failed” notice containing
  a stable error category but not raw exception or page data.

An asset load failure gets category `renderer-unavailable`; invalid Mermaid
gets `invalid-source`; a zero-sized result gets `invalid-geometry`; and a
bounded timeout gets `render-timeout`. Failures are announced with an ARIA live
status, are sent to `console.error` once, and never leave an empty diagram host.
The original source must remain recoverable for retry and diagnostics.

### Visibility and serialization

Register the upstream Mermaid bundle as a Reveal plugin so Reveal waits for its
asynchronous initialization. Set Mermaid `startOnLoad: false`; remove the two
manual whole-document `mermaid.run()` calls.

The controller queues work from Reveal `ready`, `slidetransitionend`,
`overviewhidden`, and print/PDF preparation. Interactive mode selects Mermaid
nodes from the current visible slide only. It waits one animation frame, then
checks `offsetParent`, a non-empty client rectangle, and computed visibility
before invoking the upstream renderer. There is one in-flight render promise
for the entire deck. Repeated events coalesce by diagram identity and never
start competing calls.

Successful diagrams are idempotent. Navigation does not destroy a good SVG or
render it a second time. A node in `failed` state may be retried once after it
becomes visible if its prior category was `invalid-geometry` or
`render-timeout`; invalid source is not retried until content changes. Print
mode renders slides sequentially in document order after Reveal has prepared
print layout.

After each success, verify both a finite positive viewBox and a non-zero
bounding box. A present-but-empty SVG is a failure. At stable deck idle, the
health summary must report the exact totals for pending, rendering, rendered,
and failed. The normal deck acceptance condition is zero unrendered diagrams,
where unrendered means pending, rendering, failed, missing SVG, or invalid
geometry.

## Repository source-link contract

### Public destination

`scripts/build_deck.py` must expose the full 40-character commit as
`{{GIT_SHA_FULL}}`. Every file citation is an anchor whose public `href` is:

```text
https://github.com/sandboxcom/gludd/blob/{{GIT_SHA_FULL}}/{path}
https://github.com/sandboxcom/gludd/blob/{{GIT_SHA_FULL}}/{path}#L{start}
https://github.com/sandboxcom/gludd/blob/{{GIT_SHA_FULL}}/{path}#L{start}-L{end}
```

The canonical range template is therefore
`github.com/sandboxcom/gludd/blob/{{GIT_SHA_FULL}}/` plus
`#L{start}-L{end}`. A directory citation uses the corresponding immutable
`tree/{{GIT_SHA_FULL}}/{path}` URL. The label remains human-readable and the
anchor also carries:

```html
data-source-path="src/general_ludd/daemon.py"
data-source-lines="170-195"
```

Single-line citations encode the same number twice or use a single integer;
the parser must accept one canonical representation and emit it consistently.
All paths use repository-relative POSIX spelling. A static test fails for an
unlinked path-like token in citation elements, a missing file, a start below
one, an end before start, an end past EOF, a mutable branch URL, or malformed
GitHub #L anchors. Generated links must resolve tokens before Pages upload.

### Local viewer behavior

On GitHub Pages and any non-loopback origin, an anchor behaves normally and
opens its immutable GitHub URL. On `localhost`, `127.0.0.1`, or `[::1]`, the
deck intercepts a plain primary-button click and opens an accessible modal. It
must not intercept modifier clicks, keyboard requests to open a new tab, or a
missing local source response; those continue to GitHub.

The modal lazily creates one Ace instance, sets it read only, applies the mode
derived from a fixed extension map, and displays the repository-relative path.
After loading text it creates an Ace `Range(start - 1, 0, end - 1,
lineLength)`, calls `selection.setSelectionRange`, then `gotoLine(start, 0,
true)`. Focus moves into the dialog, Escape closes it, focus returns to the
trigger, and a visible “Open on GitHub” link always remains available.

The local server exposes `GET /__gludd_source__?path={url-encoded-path}` only
on its loopback listener. It resolves against the repository root and serves
UTF-8 regular files from a build-generated citation allowlist. It rejects an
absolute path, `..`, NUL, path traversal after decoding, a directory, a file
outside the allowlist, and any symlink whose resolved target escapes the root.
Responses are size bounded, use `text/plain; charset=utf-8`, set
`X-Content-Type-Options: nosniff`, and never expose exception paths. This route
does not exist in the static Pages artifact.

## Required automated tests

### Static and unit tests

1. Parse the built deck and require every repo file citation to have the exact
   immutable blob/tree URL, matching `data-source-path`, and matching
   `data-source-lines`/GitHub #L anchors.
2. Prove every cited file/range exists in the checkout and is within EOF.
3. Require relative URLs for every same-origin stylesheet, script, image, and
   font; reject runtime CDN hosts and version ranges.
4. Verify the vendor manifest is exact and every digest/license matches.
5. Exercise all diagram state transitions, coalescing, timeout, invalid
   geometry, retry eligibility, and fail-visible source restoration without a
   real network.
6. Exercise the source endpoint with allowed files plus encoded traversal,
   double-encoded traversal, absolute paths, symlink escape, directory, binary,
   oversize, missing, and unlisted inputs.
7. Exercise citation conversion for one line, a range, file only, directory,
   HTML escaping, and token replacement.

### Browser tests

The mandatory headless Chromium test serves the final artifact below
`http://127.0.0.1:{ephemeral-port}/gludd/`; fixed port `8080` is forbidden in
parallel tests. It captures console errors, page errors, failed requests, and
all same-origin response statuses. It must:

1. load with no 404 or other failed same-origin request and no console errors;
2. wait for Reveal readiness and assert the diagram inventory is non-zero;
3. navigate forward through every slide and navigate backward through every slide,
   awaiting `slidetransitionend` at each step;
4. for every diagram, require `data-mermaid-state="rendered"`, exactly one SVG,
   a finite positive viewBox, and a non-zero bounding box;
5. assert the final health summary has zero unrendered diagrams;
6. open a line-range citation locally, assert Ace is read only, the expected
   path is shown, the expected range is selected, and the first line is in the
   visible viewport;
7. inspect the same anchor's public href and require the exact GitHub #L range;
8. inject an invalid diagram and prove the source plus “Diagram render failed”
   remain visible without breaking Reveal navigation;
9. run a blocked asset fault by aborting the Mermaid bundle request and prove
   every diagram remains readable as source with `renderer-unavailable`; and
10. repeat at a narrow viewport and after a resize event to catch clipped or
    zero-sized diagrams.

The invalid diagram and blocked asset cases are expected failures and must not
be counted as unexplained console errors. All other warnings and errors fail the
suite. On failure, retain an HTML snapshot, screenshot, console/request log,
diagram state inventory, and Playwright trace in a namespaced temporary output
directory.

### GitHub Pages workflow

The Pages validation job must build the same directory it uploads, start the
subpath server with an observable readiness line, run the browser suite, and
always terminate its exact process tree. It runs before
`actions/upload-pages-artifact`. Deployment cannot proceed when static,
browser, source-link, or asset-integrity checks fail.

Focused production code must keep at least 85% aggregate coverage and no less
than 75% per touched file. Browser-only lines are supplemented by deterministic
unit tests rather than excluded from coverage. Tests may not be weakened or
skipped because Chromium is absent from a developer machine; the narrow local
target must print the install target and fail closed, while CI installs the
exact pinned browser.

## Observability and security

The deck exposes `window.gluddPresentationHealth()` for tests and operator
diagnostics. It returns counts and stable error categories only; it does not
include Mermaid source, repository file content, stack traces, absolute paths,
or query strings. A visible compact status appears only when a render failed.

Mermaid uses its strict security mode. Source citations are authored data, but
file content still enters Ace through `setValue`, never `innerHTML`. The local
source endpoint is loopback-only, allowlisted, bounded, traversal resistant,
and unavailable from Pages. The viewer does not offer save, paste-to-server,
command execution, or arbitrary path input.

Vendoring removes runtime supply-chain and availability dependence on a CDN.
Asset updates are explicit reviewed commits: refresh the manifest, verify
digests/licenses, run the full presentation suite, and document the upstream
release. No `latest` URL or install-at-page-load path is permitted.

## ZDD rollout and rollback

This is a static-site change and does not restart the Gludd daemon. Roll out in
four independently reversible commits: vendored assets and integrity checks;
Mermaid lifecycle plus fallback; citation links plus local viewer; then browser
and Pages enforcement. Keep progressive source visible throughout rollout so
an older browser still sees diagram text.

Before promotion, push the integrated commit to remote `development` and let
the non-deploying Pages validation run there. After the full project gate and
remote CI are green, promote through the existing development-to-release flow.
The protected release branch alone deploys Pages.

Rollback reverts the runtime controller and asset reference commit while
retaining the original Mermaid source and public GitHub links. Do not roll back
to un-awaited whole-document `mermaid.run()` calls or CDN-only assets. If an
upstream renderer regression is discovered, pin the last green vendored
manifest and keep fail-visible source active; no service downtime or database
operation is involved.

## Acceptance criteria

- The tracked deck contains no direct `mermaid.run()` and registers the pinned
  `RevealMermaid` plugin.
- The published artifact has no runtime CDN dependency and works under both `/`
  and `/gludd/` with no 404.
- Forward/back navigation and resize leave every valid diagram with one
  positive-size SVG and zero unrendered diagrams.
- Invalid syntax, invalid geometry, timeout, and blocked asset faults are
  visible, categorized, bounded, and retain original source.
- Every repository file reference is a GitHub blob link; every cited line or
  range has correct GitHub #L anchors and valid bounds.
- Loopback clicks open Ace at the correct file and selected lines; public clicks
  retain the immutable GitHub destination.
- Static, unit, integration, browser, Markdown, asset-integrity, and complete
  project gates pass with the required 85% aggregate / 75% per-file coverage.
