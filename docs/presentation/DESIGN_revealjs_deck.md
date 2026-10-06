# DESIGN — reveal.js Deck: "gludd, honestly"

Status: IMPLEMENTED; browser/Pages resilience added for v0.1.2. Original design: 2026-06-18.
Target output: a reveal.js HTML deck driven by gludd's **live E2E test artifacts**, not hand-authored screenshots.

> Honesty contract (inherited from README + BUGS.md): every maturity claim on a
> slide must carry the same evidence token the README table carries (`[commit]`,
> `[test]`, `[audit]`), or be rendered with a visible "UNVERIFIED" badge. No slide
> may state a percentage the README status table does not back. Marketing
> adjectives ("blazing", "production-ready", "seamless") are linted out (see §7).

---

## 0. Why this deck exists / what makes it different

This is not a sales deck. It is a **self-describing artifact**: the same daemon the
deck is about generates the data the deck shows. Two E2E flows feed it:

1. **Dogfood self-edit** — gludd running its event loop on its own repo
   (`make dogfood`, `src/general_ludd/dogfood/` — `DogfoodRunner`, `SprintItem`,
   `DogfoodValidator`; E2E test `tests/e2e/test_obj16_dogfood_loop.py`).
2. **Greenfield build** — gludd given a from-scratch todo-website task, producing a
   rendered site we screenshot.

If a flow did not run, its slides render a "NO DATA — run `make deck-data`" placeholder
rather than a fabricated screenshot. That placeholder is the honest default.

---

## 1. Deck outline (slide-by-slide), with data binding

Each slide lists: **content**, **data source**, **honesty note**.

### Section 1 — What gludd is (4 slides)

| # | Title | Content | Data source | Honesty note |
|---|---|---|---|---|
| 1.1 | Title | "gludd — an autonomous, Ansible-driven, multi-model coding daemon. Alpha research software." | static | The word "alpha" is on slide 1, not buried. |
| 1.2 | The loop | claim -> dispatch -> review -> reconcile -> repeat. Daemon (FastAPI, single gunicorn worker). | `README.md` architecture Mermaid block rendered by GitHub Markdown or reveal.js Mermaid plugin | - |
| 1.3 | The Ansible tool boundary | Every task = an Ansible playbook composing `general_ludd.agent` modules (`gludd_facts`, `gludd_model_call`, `gludd_git`, …). Auditable, idempotent. | README "Modules" table (12 modules) | Count is live via `make collection-roles`, not hardcoded. |
| 1.4 | Multi-model gateway + dogfood | Router → `zai_coder` w/ fallback `deepseek_coder`, `qwen_coder`. Real API calls, tenacity retry, cost accounting. Daemon can run on its own repo. | README Models table + `make deck-data` model-discovery JSON | `make dogfood` PASSES but **monkeypatches dispatch** (no real API key) — slide says so. |

### Section 2 — Features, with HONEST maturity (3 slides, data-driven)

These slides are **generated from the README status table** (parsed, not retyped),
so they can never drift from the source of truth.

| # | Title | Content | Data source | Honesty note |
|---|---|---|---|---|
| 2.1 | What actually works (100%) | Daemon spine G0–G7, model gateway + failover, security hardening suite (#43/#44/#50–#61), Ansible collection (12 modules, ~34 roles), molecule (49 scenarios local). | parsed README rows where `%==100` | "100% (local)" rows get a **CI-UNVERIFIED** badge (molecule, matrix). |
| 2.2 | Partial / wired-but-inert | SpendLimiter **20%** (passes `projected_cost_usd=0.0` — cap never fires), Scoring router **20%** (no `avg_cost` column → no-op), Scheduler parallel dispatch **75%**, DynamicDispatcher **25%**, PipelineController **75%**. | parsed README rows where `0<%<100` | These are the rows the README flags as "wired-but-inert security control" — quoted verbatim. |
| 2.3 | Not built (0%) | Connector dedup, `model_weights/` package, persistent memory, eval harness, semantic retrieval, sandbox, HITL gates, consensus, replay. CVE upgrades open. | parsed README rows where `%==0` | These are design-only. The slide says "design-only", matching README `[audit]`. |

### Section 3 — Where gludd OVERLAPS other agentic tools (1–2 slides)

Fair comparison. gludd shares real capabilities with mainstream agents; this slide
names the overlap without claiming superiority.

| Capability | gludd | Claude Code | Cursor | Aider | Devin | OpenHands |
|---|---|---|---|---|---|---|
| Autonomous multi-step edit loop | yes (event loop) | yes | partial (IDE-driven) | yes (chat loop) | yes | yes |
| Multi-model / model routing | yes (gateway + fallback) | Anthropic-family | multi | multi | proprietary | multi |
| Runs tests / quality gate before landing | yes (`make gate`) | yes (hooks/tests) | partial | yes | yes | yes |
| Lands work in git (branch+commit) | yes (`gludd_git`) | yes | yes | yes | yes | yes |
| Separate reviewer model | yes (ReturnReviewer) | via subagents | no | no | internal | partial |
| Cost/budget tracking | partial (caps inert) | usage view | n/a | token est. | n/a | partial |
| Self-hosting / dogfood | yes (`make dogfood`) | n/a | n/a | n/a | n/a | n/a |

Honesty note: cells are "yes/partial/no" not checkmark theater; gludd's "partial"
cells link back to Section 2 inert-feature rows. Competitor cells reflect publicly
documented behavior as of the deck build date and carry a "as-documented" footnote —
not benchmarked by us.

### Section 4 — Where OTHER tools are PREFERRED over gludd (1–2 slides)

This is the honesty centerpiece. Pulled directly from the README low-% rows.

- **No UI / no IDE integration.** gludd is a daemon + CLI; Cursor/Claude Code give
  interactive editing. → prefer those for human-in-the-loop coding.
- **Connector / observability layer unwired** (README: 60% built but `daemon.py`
  never imports `connectors`, `observe` router not registered → effectively 5%
  wired). → prefer purpose-built observability tooling.
- **SpendLimiter inert** (20%) — budget cap "literally never fires". → if hard cost
  caps matter, gludd does not give them yet.
- **SQLite-only, single-worker** — no horizontal scale. → Devin/OpenHands for fleet.
- **Alpha maturity, CI-green unverified** — molecule/matrix pass locally only.
- **Open P1 security findings** (16 grounded in NEW_FINDINGS_2026-06-16: PSK
  fail-open, `/api/status` leaks db_url, secret leakage in logs, dead permission
  matrix). → do not run untrusted in prod.

Data source: README §"Where … PREFERRED" maps 1:1 to README low-% rows +
NEW_FINDINGS table. Each bullet carries its `file:line` evidence.

### Section 5 — Real scenarios from the E2E harness (5–7 slides)

The payoff. Real numbers and screenshots, or honest placeholders.

| # | Title | Content | Data source |
|---|---|---|---|
| 5.1 | Scenario A: self-edit | The dogfood flow: task submitted → playbook dispatched → diff produced → reviewed → reconciled. | `tests/e2e/test_obj16_dogfood_loop.py` run log + `DogfoodRunner.run_smoke_task` output |
| 5.2 | Self-edit, the numbers | tokens, cost USD, model used, wall-clock, pass/fail of resulting gate. | `make deck-data` parsed run log JSON |
| 5.3 | Scenario B: greenfield todo site | Task: "build a todo website". Show the generated `index.html` / app structure. | greenfield E2E workspace tree |
| 5.4 | The rendered site (screenshot) | Actual screenshot of the generated todo app in a headless browser. | **Playwright screenshot** (see Deliverable B — dependency gap) |
| 5.5 | Model discovery | What models the gateway discovered/routed to, weights, fallback chain exercised. | model-discovery JSON from gateway |
| 5.6 | Cost & weights | Per-run cost, per-role routing weights actually used. | metrics/traces from `/api/metrics`, `/api/traces` |
| 5.7 | Honest scorecard | What the run proved vs. what it didn't (e.g. dispatch monkeypatched). | derived from run metadata |

Honesty note: 5.4 depends on a screenshot pipeline gludd **does not have yet**
(no Playwright dep — see Deliverable B §"Gaps"). Until that lands, 5.4 is a
"NO SCREENSHOT — pipeline not built" placeholder. Do not fake it.

---

## 2. How the deck consumes E2E output (the data contract)

The deck is **data + template**, never hand-edited HTML for the dynamic slides.
A single make target produces a `deck-data.json` the template reads at build time.

```text
make deck-data        # runs/locates E2E artifacts, emits docs/presentation/deck-data.json
make deck             # renders reveal.js HTML from template + deck-data.json
make deck-serve       # local static serve for preview
```

### Artifact → slide mapping

| E2E artifact (source) | Producer | Feeds slide(s) | deck-data.json key |
|---|---|---|---|
| README status table | `README.md` (parsed by `scripts/parse_readme_status.py`) | 2.1, 2.2, 2.3 | `features[]` (title, pct, evidence, bucket) |
| Dogfood run log | `make dogfood` / `dogfood/runner.py` stdout+log | 5.1, 5.2, 5.7 | `dogfood_run` (tokens, cost_usd, model, wallclock, gate_result, diff_summary) |
| Greenfield workspace tree | greenfield E2E workspace dir | 5.3 | `greenfield.tree[]` |
| Generated site HTML | greenfield E2E output `index.html` | 5.4 (screenshot) | `greenfield.screenshot_path` |
| Site screenshot | **a11y/visual-qa skill (Deliverable B)** Playwright capture | 5.4 | `greenfield.screenshot_path` + `greenfield.a11y_report` |
| Model discovery | gateway discovery result JSON | 5.5 | `models.discovered[]`, `models.fallback_chain[]` |
| Cost/weights/metrics | `/api/metrics`, `/api/traces` snapshot | 5.6 | `metrics`, `traces`, `weights` |
| Architecture Mermaid | `README.md` Mermaid block | 1.2 | rendered by GitHub Markdown or reveal.js Mermaid plugin |

### "NO DATA" discipline

`scripts/build_deck.py` MUST, for every dynamic slide, check its `deck-data.json`
key. Missing/empty key → render the slide's placeholder partial
(`partials/_no_data.html`) with the exact make target the viewer should run. A built
deck therefore truthfully shows which E2E flows have and have not run.

---

## 3. Where the deck is generated / stored

```text
docs/presentation/
├── DESIGN_revealjs_deck.md          # this doc
├── DESIGN_a11y_visual_qa_skill.md   # Deliverable B
├── deck/                            # reveal.js source (committed)
│   ├── index.template.html          # reveal.js shell + {{slot}} include points
│   ├── partials/
│   │   ├── _features_table.html      # rendered from deck-data.features[]
│   │   ├── _scenario_dogfood.html
│   │   ├── _scenario_greenfield.html
│   │   ├── _comparison_matrix.html
│   │   ├── _no_data.html             # honest placeholder
│   │   └── _scorecard.html
│   ├── css/gludd-theme.css           # low-density theme (see Deliverable B density rules)
│   └── assets/                       # static SVG (loop, architecture)
├── deck-data.json                   # GENERATED, gitignored (build artifact)
└── build/index.html                 # GENERATED final deck, gitignored
```

- Reveal.js 5.1.0, `reveal.js-mermaid-plugin@11.15.0`, and
  `ace-builds@1.44.0` are vendored beneath `deck/vendor/`. Their exact package,
  version, source URL, license, and SHA-256 are recorded in
  `deck/vendor/manifest.json`; the build validates the complete tree before use.
- `deck-data.json`, `build/` are gitignored (regenerated). The **template + partials +
  theme are committed**; the data is not.

### Reveal.js source layout (template mechanics)

`index.template.html` is a standard reveal.js single-page deck. Dynamic sections are
HTML comment slots:

```html
<div class="reveal"><div class="slides">
  <!-- static intro slides 1.1–1.4 authored inline -->
  <!--SLOT:features-->        <!-- replaced by _features_table.html × 3 -->
  <!--SLOT:comparison-->      <!-- replaced by _comparison_matrix.html -->
  <!--SLOT:weaknesses-->      <!-- authored from README low-% rows -->
  <!--SLOT:scenario-dogfood-->
  <!--SLOT:scenario-greenfield-->
  <!--SLOT:scorecard-->
</div></div>
```

`scripts/build_deck.py` is a tiny string-substitution renderer (Jinja2 already a
gludd dep — reuse `skills/renderer.py`'s `SandboxedEnvironment` pattern for any
templated partial so untrusted run-log strings can't SSTI the deck).

---

## 4. Build and verification pipeline

| Target | Does | Depends on |
|---|---|---|
| `parse-readme-status` | `scripts/parse_readme_status.py` → features[] | README.md |
| `deck-data` | collect dogfood + greenfield + metrics + model-discovery + features → deck-data.json | E2E flows, `parse-readme-status`, a11y skill (for screenshots) |
| `deck` | render template+partials with deck-data.json → build/index.html | `deck-data` |
| `deck-verify` | run the a11y/visual-qa skill (Deliverable B) on built deck; fail build on a11y/density/overlap errors | `deck`, Deliverable B skill |
| `deck-serve` | static serve build/ | `deck` |
| `vendor-presentation-assets` | fail-closed digest/license validation; explicit refresh mode | vendored manifest |
| `presentation-browser-install` | validate/install pinned Chromium and WebKit in a namespaced cache | locked `ci` profile set (`presentation-test` member) |
| `presentation-browser-test` | serial `/gludd/` Chromium + WebKit acceptance with retained diagnostics | exact built deck + both engines |
| `presentation-pages-probe` | cache-busted, content-free comparison of the live Pages revision to one exact SHA | public Pages URL |

`deck-verify` closes the loop: **the deck about gludd is itself validated by a gludd
skill.** That is the linkage between the two deliverables.

---

## 5. Honesty enforcement (lint the deck)

A `scripts/deck_honesty_lint.py` (wired into `deck` build) fails the build if:

1. A slide states a `%` not present in parsed README features[] for that feature key.
2. A "100%" claim lacks an evidence token AND lacks a CI-UNVERIFIED badge for
   "local-only" rows.
3. A banned marketing token appears (`production-ready`, `blazing`, `seamless`,
   `enterprise-grade`, `revolutionary`, `effortless`).
4. A scenario slide shows fabricated numbers (any number not traceable to a
   deck-data.json key).

This mirrors the existing repo guard `test_status_snapshot.py::TestReadmeNoHardcodedMetrics`
— same philosophy, applied to the deck.

---

## 6. What's buildable NOW vs. needs E2E data

| Slide group | Buildable now? | Blocker |
|---|---|---|
| 1.1–1.4 intro | YES | static + README |
| 2.1–2.3 features | YES | `parse_readme_status.py` only |
| 3 overlap matrix | YES | static (documented competitor behavior) |
| 4 weaknesses | YES | README low-% rows |
| 5.1, 5.2, 5.7 dogfood | PARTIAL | needs a dogfood run log capture (run exists; needs JSON emit) |
| 5.3 greenfield tree | NEEDS E2E | needs greenfield E2E flow to exist + run |
| 5.4 site screenshot | BLOCKED | needs Deliverable B (Playwright) — no headless browser dep yet |
| 5.5 model discovery | PARTIAL | needs gateway discovery JSON emit |
| 5.6 cost/weights | PARTIAL | `/api/metrics`+`/api/traces` exist; needs snapshot capture |

So ~60% of the deck (all of §1–§4) is buildable from current artifacts today.
§5 is gated on Task #3's E2E data and Deliverable B's screenshot pipeline.

---

## 7. Remaining data dependencies / risks

- The presentation browser lane now uses pinned Playwright Chromium and WebKit. Run
  `make presentation-browser-install` once for the namespaced browser cache,
  then `make presentation-browser-test`; absence of either engine fails closed with
  the exact installation command instead of skipping the checks.
- Greenfield E2E flow may not exist yet as a runnable target — if not, §5.3–5.4 are
  design-only until it lands.
- Dogfood monkeypatches dispatch → §5.2 numbers are "structurally real, dispatch
  simulated"; the scorecard slide must say so.

## Diagram Rendering Note - 2026-07-22

Markdown docs use Mermaid fenced code blocks because GitHub renders Mermaid natively in repository Markdown, issues, pull requests, discussions, gists, and wikis. Do not add a third-party GitHub Mermaid plugin for Markdown diagrams unless GitHub native rendering fails for a documented reason. GitHub docs warn that third-party Mermaid plugins can cause rendering errors.

The reveal.js deck is separate from GitHub Markdown. It vendors the Mermaid runtime carried by the reveal.js Mermaid plugin, but Gludd owns an awaited, per-diagram render boundary. Keep source diagrams in Mermaid so README, design docs, and the deck can share the same diagram vocabulary.

## Resilient runtime and source navigation — v0.1.2

The deck delegates Mermaid parsing and layout to the runtime carried by vendored
`reveal.js-mermaid-plugin@11.15.0`, while the controller schedules the work
itself. After fonts are ready, it renders into a dedicated fixed-width,
opacity-zero scratch node attached directly to `document.body`, outside Reveal
transforms and hidden slides. The authored node never leaves its Reveal slide.
The controller awaits `gluddMermaid.render()`, validates every descendant of the
scratch SVG, then URL-encodes that validated SVG into a decoded
`data:image/svg+xml` replaced image with explicit intrinsic width and height.
Before serialization, the controller writes explicit `text-anchor="middle"`
attributes on Mermaid's outer text rows and expands the SVG view box by 16
units on every edge. This avoids relying on inherited alignment inside a data
image and gives glyph paint a deterministic safety margin. The live image keeps
its intrinsic aspect ratio and is capped at 500 logical pixels (320 on compact
mixed-content slides); diagram explanations that cannot fit beside that bound
are placed on the next slide instead of being clipped below Reveal's canvas.
The live Reveal slide therefore never depends on Safari laying out an inline SVG
whose percentage height is derived from a transformed or aspect-ratio container.
Each diagram receives an independent ten-second deadline, so one slow chart can
no longer consume the shared budget and force every later chart to fail. The
controller retains the source-preserving
`pending`/`rendering`/`rendered`/`failed` state. Root and flowchart HTML labels
remain disabled.

The captured WebKit failure was not merely a slow load. Mermaid's
`positionEdgeLabel` wrote `translate(undefined, NaN)` for a valid authored
chart. The same stack reproduced through both the plugin and direct Mermaid API.
The vendoring tool therefore applies the reviewed `mermaid-webkit-geometry-v2`
transform to exactly two known edge-label branches, using Mermaid's already
calculated path coordinates when `edge.x` or `edge.y` is non-finite. Exact match
counts and pre/post SHA-256 values are manifest-bound; changed upstream bytes
fail closed. The runtime rejects non-finite values in every descendant SVG
attribute, nonpositive `foreignObject` geometry, and invalid SVG client/bounding
boxes. One failed diagram retains readable source without failing later charts.

This behavior intentionally preserves the long-lived practitioner evidence in
[`mermaid-js/mermaid#1846`](https://github.com/mermaid-js/mermaid/issues/1846),
[`mermaid-js/mermaid#1824`](https://github.com/mermaid-js/mermaid/issues/1824),
[`mermaid-js/mermaid#3577`](https://github.com/mermaid-js/mermaid/issues/3577),
[`mermaid-js/mermaid#5122`](https://github.com/mermaid-js/mermaid/issues/5122),
[`mermaid-js/mermaid#6666`](https://github.com/mermaid-js/mermaid/issues/6666),
[`mgaitan/sphinxcontrib-mermaid#126`](https://github.com/mgaitan/sphinxcontrib-mermaid/issues/126),
and
[`zjffun/reveal.js-mermaid-plugin#5`](https://github.com/zjffun/reveal.js-mermaid-plugin/issues/5),
plus the Safari/macOS initial-layout report
[`mermaid-js/mermaid#7323`](https://github.com/mermaid-js/mermaid/issues/7323).
The exact invalid transform is also reported in
[`gitlab-org/gitlab-docs#599`](https://gitlab.com/gitlab-org/gitlab-docs/-/issues/599),
and transformed-container unit mismatches plus the body-scratch mitigation are
tracked in
[`mermaid-js/mermaid#8113`](https://github.com/mermaid-js/mermaid/issues/8113).
Safari's stale intrinsic SVG geometry and developer-tools-triggered relayout are
tracked in [WebKit bug 198609](https://bugs.webkit.org/show_bug.cgi?id=198609),
and a macOS Safari practitioner report is retained in
[GitHub Community discussion 12523](https://github.com/orgs/community/discussions/12523).
The replaced-image boundary additionally follows the long-lived inline-SVG
percentage-sizing reports in
[WebKit bug 68995](https://bugs.webkit.org/show_bug.cgi?id=68995) and
[WebKit bug 82489](https://bugs.webkit.org/show_bug.cgi?id=82489), plus the
practitioner cases where inline SVG is absent or incorrectly sized only in
[Safari](https://stackoverflow.com/questions/25090516/inline-svg-breaks-in-safari-and-mobile-safari/78364325)
and the responsive-SVG discussion on the
[Apple Developer Forums](https://developer.apple.com/forums/thread/685035).
Mermaid's long-lived reports also document off-center multi-line SVG labels when
HTML labels are disabled
([mermaid-js/mermaid#1177](https://github.com/mermaid-js/mermaid/issues/1177))
and flowchart differences when decoded as an image
([mermaid-js/mermaid#1572](https://github.com/mermaid-js/mermaid/issues/1572)).
The newer fractional-device-pixel wrapping report
([mermaid-js/mermaid#7794](https://github.com/mermaid-js/mermaid/issues/7794))
reinforces why the acceptance contract measures actual painted text boxes rather
than assuming generated markup is aligned.
Those reports cover hidden-slide zero geometry, concurrent asynchronous renders,
visibility-triggered recovery, reload/zoom-sensitive WebKit layout, invalid
descendant transforms, and clipped text; an SVG-exists assertion alone would
not catch those failures.

The browser contract serves the exact Pages upload tree below `/gludd/`. In both
Chromium and WebKit it requires all charts to become decoded SVG images with
positive natural and client dimensions on a cold load and a cached reload,
visits every chart forward and backward, proves direct hash navigation and
reload, exercises the same artifact over `file://`, and injects a rule that
collapses every live inline SVG to zero dimensions. Charts must remain visible
because no live inline SVG is permitted. A script-blocking CSP
must leave the static diagnostic visible. Malformed-source, blocked-asset,
source-viewer, console, network, and HTTP failure paths remain strict. Runtime JS
and CSS URLs carry the exact 40-character build SHA so a Safari cache cannot
combine old controller code with new deck markup.
The layout audit waits for `slidetransitionend`, reveals every fragment, and
compares every text, table, and diagram boundary with Reveal's logical canvas;
it also decodes every image and verifies that each painted label stays within
the SVG viewport and each multi-line row shares one horizontal center. The deck
also derives the on-screen font scale from each image and its SVG view box and
rejects chart text below eight CSS pixels at the 1280x720 acceptance viewport.
Long workflows are arranged as short vertical groups across the slide so that
fitting the canvas does not merely trade clipping for unreadably small text. The deck
uses a fade transition so neighboring slide content never flies through the
viewport and resembles persistent off-screen text.

The 2026-10-06 macOS reproduction separated delivery failures instead of
guessing from Playwright. The exact deployed public revision
`1d1cf6b8559d574d04418df52acbec535f16b1cd` passed the revision probe, yet the
operator still observed no charts in Safari. That report invalidated the prior
WebKit-only completion claim and motivated the replaced-image boundary above.
The native runner reaches session creation and then reports Remote Automation
disabled (exit 3), so native automated compatibility remains pending until that
operator-controlled setting is enabled. The user-visible Safari failure is
treated as authoritative evidence even while Chromium and Playwright WebKit
pass.

Playwright WebKit is deliberately not called native Safari. The separate
`make presentation-safari-test` target uses `/usr/bin/safaridriver`, is bounded,
serves the exact `/gludd/` artifact, and repeats cold/cache, every-slide geometry,
client-error, and source-viewer checks. It never enables or prompts for Remote
Automation. On the 2026-10-06 macOS probe that operator setting was disabled, so
the target exited `3` and retained a content-free
`remote-automation-disabled` report with the exact Safari menu action. Native
Safari compatibility remains pending until the operator enables that setting
and this target passes; Chromium and Playwright WebKit evidence remains valid.

At build time, repository `file:line` citations become immutable GitHub blob
links for the exact 40-character commit. On the loopback-only preview server,
plain clicks open the same citation in a vendored read-only Ace viewer with the
range selected and scrolled into view. The `/__gludd_source__` endpoint accepts
only build-generated allowlisted UTF-8 files, rejects traversal and symlink
escapes, caps response size, and is absent from the static Pages artifact.
When a browser closes a cached preview socket during teardown, the local server
suppresses only `ConnectionResetError` and `BrokenPipeError`; every unexpected
server exception still uses the standard traceback path. A focused regression
pins both sides of that diagnostic boundary.

The Pages workflow builds one directory and tests that resolved directory below
`/gludd/` on `development`, `master`, and relevant pull requests. Development is
validation-only. A push to `master` alone preserves and deploys the validated
files. `presentation-pages-probe` fetches the public artifact with cache bypass
and fails unless its embedded full SHA equals the deploying master SHA.

Pre-fix deployment evidence on 2026-10-06 was explicitly stale: Pages run
`37434869685` validated commit `8ef55fdab99e3a180113c90e2115d048b5c20c94`
but skipped deployment, while the public artifact did not contain even a short
revision marker (`published display revision is missing`). It therefore could
not represent current development. The public URL remains legacy until the
browser-green change is promoted to `master` and its deploy plus revision probe
pass.

The merge-forward browser regression was a separate HTTP cache-boundary bug.
On WebKit's second direct-hash navigation, `SimpleHTTPRequestHandler` honored
the top-level document's `If-Modified-Since` header and returned `304`.
Playwright correctly exposes that navigation response as non-OK, so acceptance
stopped before it could assert chart repaint. The preview server now marks only
the deck document (`/gludd/`, `/gludd/index.html`) `Cache-Control: no-store` and
removes its conditional validators; fingerprinted assets remain cacheable. A
regression test sends the same conditional request and requires a `200` body.
This matches Playwright's reported `304` response semantics in
[`microsoft/playwright#29441`](https://github.com/microsoft/playwright/issues/29441)
without weakening navigation or geometry assertions.

Hosted-runner evidence has the same strict provenance boundary. Pages run
`37455701172` tested commit `9cccc35c4435983679ac315d0e5fa63ebed7310c`
and failed every WebKit launch for missing GTK/GStreamer and related Linux
libraries; that historical job contained no dependency-install step, so it is
not evidence that the current contract failed. The current job invokes the
locked Python runtime's official `playwright install --with-deps webkit` path in
the namespaced browser cache, under a hard timeout, and then performs a real
headless WebKit launch probe before starting acceptance. This follows
[Playwright's CI guidance](https://playwright.dev/docs/ci) and
[browser installation guidance](https://playwright.dev/docs/browsers), while
retaining practitioner reports where nominal installs still left hosted WebKit
unlaunchable:
[`microsoft/playwright#27255`](https://github.com/microsoft/playwright/issues/27255),
[`microsoft/playwright#30538`](https://github.com/microsoft/playwright/issues/30538),
and
[Stack Overflow 79090211](https://stackoverflow.com/questions/79090211/playwright-tests-in-github-actions-error-for-webkit-with-host-system-is-missing).
The launch probe, rather than workflow-step presence, is the fail-closed proof.

Pages run `37488101748` exposed a separate dependency-environment ownership
failure: the workflow synced the default `development` set, which intentionally
omits `presentation-test`, and the dependency installer then failed as
`.venv/bin/python -m playwright` reported `No module named playwright`. The
workflow now names the locked `ci` set, `.venv`, and Python 3.11 explicitly
before any browser operation; that set includes `presentation-test`, so the
interpreter used by the runner owns the pinned Playwright module. This avoids
treating an execution-time extra label as proof that the environment contains
the dependency. Practitioner reports show why this remains an explicit
contract: [`astral-sh/uv#13319`](https://github.com/astral-sh/uv/issues/13319)
records a non-default group being removed by a later uv operation, while
[`astral-sh/uv#14645`](https://github.com/astral-sh/uv/issues/14645) records an
extra included through a dependency group being absent until explicitly
selected. The Pages regression therefore pins the install set itself and its
ordering ahead of the WebKit dependency probe.

ZDD rollback reverts the controller and manifest-bound vendor transform on
development, validates the last browser-green bytes, then promotes that revert
through the normal master-only release flow. The deploy job consumes only the
artifact from its required validation job, and the revision probe provides the
post-deploy identity check; no in-place Pages mutation or unverified fallback
is used.
