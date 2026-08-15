# DESIGN — reveal.js Deck: "gludd, honestly"

Status: DESIGN ONLY (not built). Author: presentation design task, 2026-06-18.
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

- reveal.js itself is **vendored or pinned** (a `deck/vendor/reveal.js@x.y.z/`) — no
  CDN at present-time, so the deck renders offline (matches gludd's offline-fallback
  ethos). Pin the exact version; record it in `deck/vendor/VERSION`.
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

## 4. Build pipeline (make targets to add)

| Target | Does | Depends on |
|---|---|---|
| `parse-readme-status` | `scripts/parse_readme_status.py` → features[] | README.md |
| `deck-data` | collect dogfood + greenfield + metrics + model-discovery + features → deck-data.json | E2E flows, `parse-readme-status`, a11y skill (for screenshots) |
| `deck` | render template+partials with deck-data.json → build/index.html | `deck-data` |
| `deck-verify` | run the a11y/visual-qa skill (Deliverable B) on built deck; fail build on a11y/density/overlap errors | `deck`, Deliverable B skill |
| `deck-serve` | static serve build/ | `deck` |

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

## 7. Open dependencies / risks

- No headless browser in repo → §5.4 and `deck-verify` both need Deliverable B's
  Playwright addition. **This is the single hard dependency.**
- Greenfield E2E flow may not exist yet as a runnable target — if not, §5.3–5.4 are
  design-only until it lands.
- Dogfood monkeypatches dispatch → §5.2 numbers are "structurally real, dispatch
  simulated"; the scorecard slide must say so.

## Diagram Rendering Note - 2026-07-22

Markdown docs use Mermaid fenced code blocks because GitHub renders Mermaid natively in repository Markdown, issues, pull requests, discussions, gists, and wikis. Do not add a third-party GitHub Mermaid plugin for Markdown diagrams unless GitHub native rendering fails for a documented reason. GitHub docs warn that third-party Mermaid plugins can cause rendering errors.

The reveal.js deck is separate from GitHub Markdown and keeps using the existing reveal.js Mermaid plugin. Keep the source diagram in Mermaid so README, design docs, and the deck can share the same diagram vocabulary.
