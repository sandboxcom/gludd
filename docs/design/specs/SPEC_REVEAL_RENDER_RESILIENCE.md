# Reveal.js Render Resilience and Source-Link Specification

Status: implemented; Chromium and Playwright WebKit verified, native Safari
verification pending the operator-controlled Remote Automation setting
Contract version: `reveal-render-resilience/v1`
Scope: the generated Reveal.js deck, GitHub Pages artifact, local preview
server, source citations, and presentation-focused browser tests

## Outcome

The presentation must never silently replace a Mermaid chart with empty space.
It renders one diagram at a time in a measurable scratch node attached directly
to `document.body`, outside Reveal transforms and hidden slides. The authored
diagram never leaves its Reveal slide: the controller validates the scratch SVG,
copies fresh markup into the live node, and pins an explicit intrinsic width,
height, aspect ratio, and `preserveAspectRatio`. Every failure exposes a readable
source fallback.
The published artifact is self-contained and works below the GitHub Pages
project prefix `/gludd/` without runtime CDN availability.

Every citation that names a repository file must be an immutable GitHub link.
When it names a line or range, the link must include the corresponding GitHub
`#L` anchor. The same anchor opens a read-only local code viewer during
`make deck-serve`, with the cited range selected and scrolled into view.

## Observed failure and root cause

The old controller rendered the entire deck while Reveal kept inactive slides
under `display: none`, allowed overlapping asynchronous work, and treated the
presence of an outer SVG as success. WebKit could therefore receive an empty or
invalid diagram without a durable error state.

Instrumentation around every SVG attribute write captured the decisive failure
in the vendored Mermaid 11.15 runtime: `positionEdgeLabel` attempted to write
`transform="translate(undefined, NaN)"`. An A/B reproduction through both the
Reveal plugin and Mermaid's direct `mermaid.render(uniqueId, source, element)`
API produced the same stack. The failure is in Mermaid edge-label geometry, not
the plugin adapter. Mermaid had finite coordinates calculated from the path, but
the branch selected absent `edge.x` and `edge.y` values instead.

The repaired boundary is deliberately small. The vendoring tool applies the
reviewed `mermaid-webkit-geometry-v2` transform to exactly two known
`positionEdgeLabel` branches, requires exact match counts, and records both
upstream and transformed SHA-256 values in the manifest. Missing or changed
boundaries fail closed. This is not a user-agent workaround. The controller also
disables HTML labels, waits for fonts, and renders serially in a fixed-width,
opacity-zero body scratch node before validating every descendant attribute. It
then copies the SVG into Reveal rather than reparenting the live diagram across
Safari paint trees. A static status is visible before JavaScript runs and is
hidden only after every real diagram is rendered; parser, CSP, or asset failures
therefore leave a visible diagnostic even if the controller never starts.

The macOS reproduction has three distinct delivery results and none is relabeled
as native Safari success. First, both the built/static Pages copy and the native
runner aborted on a stale out-of-bounds `daemon.py:1-3126` citation; the authored
citation was made line-count independent and the exact artifact now builds.
Second, the public Pages probe still reports `published display revision is
missing`, so that legacy URL cannot demonstrate the development fix. Third, the
repaired native probe reaches Safari session creation but exits `3` because
Remote Automation is disabled. Chromium and Playwright WebKit exercise the
invariants, while native Safari compatibility remains pending operator action.

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
- [`mermaid-js/mermaid#3577`](https://github.com/mermaid-js/mermaid/issues/3577)
  records that Mermaid's asynchronous renderer cannot be called concurrently
  without await semantics, and that the breakage may be intermittent and silent.
- [`zjffun/reveal.js-mermaid-plugin#5`](https://github.com/zjffun/reveal.js-mermaid-plugin/issues/5)
  records inherited Reveal `pre` line-height clipping diagram text. Browser
  acceptance must check bounding boxes, not merely the existence of an SVG.
- [`gitlab-org/gitlab-docs#599`](https://gitlab.com/gitlab-org/gitlab-docs/-/issues/599)
  records the exact `translate(undefined, NaN)` symptom when Mermaid diagrams
  are initialized inside hidden tabs.
- [`mermaid-js/mermaid#8113`](https://github.com/mermaid-js/mermaid/issues/8113)
  documents viewport-versus-user-unit measurement drift beneath transformed
  containers and recommends measuring in an untransformed body scratch area.
- [`mermaid-js/mermaid#7323`](https://github.com/mermaid-js/mermaid/issues/7323)
  records Safari diagrams and labels that appear only after reload or zoom.
- [`mermaid-js/mermaid#5122`](https://github.com/mermaid-js/mermaid/issues/5122)
  is a Safari user report for Mermaid label geometry changing under page zoom.
- [`mermaid-js/mermaid#6666`](https://github.com/mermaid-js/mermaid/issues/6666)
  records older Safari rejecting a Mermaid 11 bundle before any diagram can be
  drawn, which is why the boot diagnostic cannot depend on JavaScript.
- [WebKit bug 198609](https://bugs.webkit.org/show_bug.cgi?id=198609) documents
  Safari retaining stale outer-SVG intrinsic geometry until a relayout, with
  developer tools themselves capable of masking the failure.
- [GitHub Community discussion 12523](https://github.com/orgs/community/discussions/12523)
  is a practitioner report of Mermaid output failing specifically in Safari on
  macOS while the same source differed across other delivery contexts.

The implementation documentation must keep these links. They explain why a
static syntax lint and an assertion for `<svg>` alone are insufficient.

## Dependency decision

### Mermaid integration

Vendor the maintained
[`reveal.js-mermaid-plugin@11.15.0`](https://github.com/zjffun/reveal.js-mermaid-plugin)
bundle because it carries the pinned Mermaid 11.15 runtime, but do not delegate
render scheduling to the plugin. The reviewed vendor transform exposes that
runtime as `globalThis.gluddMermaid`. The controller calls the supported awaited
`gluddMermaid.render()` API once per diagram, with `startOnLoad: false` and
`htmlLabels: false`. Reveal still owns slides and navigation; Gludd owns the
render lifecycle and validation boundary.

Do not write a second Mermaid parser or layout engine. The Gludd controller owns
health state, serialization, the body scratch node, fallback text, and
content-free telemetry. Mermaid remains the sole parser and layout engine.

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
`pytest-playwright==0.9.0` in a presentation-test dependency group. Chromium
and Playwright WebKit are both mandatory. The
[official test-runner documentation](https://playwright.dev/python/docs/intro)
recommends the pytest plugin. Chromium installation and cache paths must be
owned by Gludd, namespaced to this project, bounded, and observable through
make targets; browser tests must not silently skip in the Pages workflow.

Playwright WebKit is not native Safari. On macOS,
`make presentation-safari-test` provides a separate bounded W3C WebDriver
smoke. It never enables or prompts for Safari Remote Automation. If that setting
is disabled, the target exits `3`, writes a content-free
`remote-automation-disabled` report, and prints the exact operator action. A
native Safari compatibility claim remains pending until that target passes.

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

### Measurable staging and serialization

Set Mermaid `startOnLoad: false` and `htmlLabels: false`; never call a
whole-document `mermaid.run()`. After Reveal initialization and
`document.fonts.ready`, queue authored diagrams in document order. Keep the
current visible slide and every inactive authored diagram attached to Reveal.
Create a separate fixed-width, opacity-zero scratch node directly under
`document.body`, call and await `gluddMermaid.render()` there, validate the
result, and copy fresh markup into the authored node. There is one in-flight render
for the deck, so no two Mermaid layouts compete.

Successful diagrams are idempotent. Navigation does not destroy a good SVG or
render it a second time. Each diagram has an isolated error boundary: one bad
source becomes a readable failure, while later diagrams continue. Print mode
uses the same serial document order.

After each success, verify a finite positive viewBox, finite positive SVG client
and bounding boxes, every descendant attribute, and every `foreignObject`
geometry. Any `undefined`, `NaN`, or `Infinity` token is a failure. A
present-but-empty SVG is a failure. At stable deck idle, the
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

The mandatory Chromium and Playwright WebKit tests serve the final artifact below
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
    zero-sized diagrams; and
11. run the direct Mermaid API A/B probe and reject any descendant geometry
    containing `undefined`, `NaN`, or `Infinity`;
12. prove a live chart never leaves its Reveal slide during scratch rendering,
    receives an explicit stable SVG viewport, and repaints after direct hash
    navigation plus cached reload;
13. open the built artifact over `file://` with the same strict geometry and
    console assertions; and
14. block scripts with CSP and require the authored boot diagnostic to remain
    visible instead of presenting silent empty space.

The invalid diagram and blocked asset cases are expected failures and must not
be counted as unexplained console errors. All other warnings and errors fail the
suite. On failure, retain an HTML snapshot, screenshot, console/request log,
diagram state inventory, and Playwright trace in a namespaced temporary output
directory.

The native Safari smoke is a distinct macOS operator target, not an alias for
Playwright WebKit. It repeats cold and cached readiness, visits every Reveal
slide, validates every chart and descendant geometry, opens the read-only source
viewer, and rejects any content-free client error category. Remote Automation
disabled is a visible, machine-readable blocked state rather than a skip.

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
Only `master` preserves the validated artifact and deploys Pages.

Rollback reverts the controller and the exact manifest-bound vendor transform
together while retaining original Mermaid source and public GitHub links. Do
not roll back to un-awaited whole-document `mermaid.run()` calls or CDN-only
assets. If an upstream renderer changes the two reviewed patch boundaries,
vendoring fails closed; pin the last browser-green transformed digest and keep
fail-visible source active. No service downtime or database operation is
involved.

## Acceptance criteria

- The tracked deck contains no `mermaid.run()` and uses the pinned, awaited
  `gluddMermaid.render()` boundary one diagram at a time.
- The published artifact has no runtime CDN dependency and works under both `/`
  and `/gludd/` with no 404.
- Chromium and Playwright WebKit cold/cache, forward/back navigation, and resize
  leave every valid diagram with one
  positive-size SVG and zero unrendered diagrams.
- The native Safari target either passes the same geometry contract or fails
  closed with durable operator guidance; WebKit is never reported as Safari.
- Direct hash, reload/cache, `file://`, `/gludd/`, and script-blocking CSP paths
  produce either positive visible SVG geometry or a visible diagnostic.
- Invalid syntax, invalid geometry, timeout, and blocked asset faults are
  visible, categorized, bounded, and retain original source.
- Every repository file reference is a GitHub blob link; every cited line or
  range has correct GitHub #L anchors and valid bounds.
- Loopback clicks open Ace at the correct file and selected lines; public clicks
  retain the immutable GitHub destination.
- Static, unit, integration, browser, Markdown, asset-integrity, and complete
  project gates pass with the required 85% aggregate / 75% per-file coverage.
