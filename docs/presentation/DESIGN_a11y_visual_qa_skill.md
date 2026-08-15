# DESIGN — gludd Skill: `visual_qa` (a11y + visual-appeal + usefulness validator)

Status: DESIGN ONLY (not built). Author: presentation design task, 2026-06-18.
Purpose: a **dogfoodable gludd skill** that validates ANY app's rendered visual
components (a URL or an HTML file) for accessibility, visual appeal, and usefulness —
not just the reveal.js deck. The deck (Deliverable A) is merely its first consumer
via `make deck-verify`.

---

## 0. Scout first (AGENTS rule 8 — use mature projects, don't reinvent)

I checked the repo before designing. Findings (raw):

- **Skills package exists:** `src/general_ludd/skills/` with `skill.py` (the `Skill`
  pydantic model), `catalog.py`, `loader.py` (`parse_skill_md`), `registry.py`
  (`SkillRegistry`), `renderer.py` (`render_skill`, `SandboxedEnvironment`),
  `fetcher.py`. Skills are **markdown + YAML frontmatter**, pydantic-only, **not** a
  DB table.
- **gludd_skill Ansible module exists:**
  `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_skill.py`
  — selects/renders a skill by `name` or `trigger`, returns `rendered_body` +
  `required_vars`. Molecule scenario `molecule/playbooks/test_gludd_skill/`.
- **NO existing a11y/visual/screenshot skill.** Catalog has the words "screenshot"
  in two web-toolkit skill *descriptions* and a `"puppeteer"` MCP **catalog entry**
  (`src/general_ludd/mcp/catalog.py:237`, an archived npx command string) — neither
  is a working driver.
- **NO browser/a11y dependency anywhere.** Confirmed absent across `pyproject.toml`,
  the full `Makefile`, and globs: no `playwright`, `axe-core`, `pa11y`,
  `lighthouse`, `puppeteer` driver code, no `playwright.config.*`, no `package.json`.

Conclusion: do not hand-roll a11y logic. **Use mature, battle-tested tools:**

| Concern | Tool chosen | Why this one |
|---|---|---|
| Headless render + screenshot + DOM bounding boxes | **Playwright (Python)** | Single dep gives Chromium + `bounding_box()` + `screenshot()` + JS eval; bundled browser via `playwright install`; sync+async API matches gludd's `asyncio.to_thread` pattern. |
| Accessibility rules (contrast, alt, roles, headings) | **axe-core** (injected via `axe-core-python` or raw `axe.min.js` eval in Playwright) | Industry-standard ruleset (WCAG 2.1 A/AA), the same engine pa11y/Lighthouse wrap; running it directly avoids a second browser stack. |
| Contrast double-check / off-axe cases | **wcag-contrast-ratio** (tiny pure-Python) | Lets us assert contrast on computed colors we read from the DOM, independent of axe, for the density/overlap custom checks. |

We deliberately do **not** add Lighthouse or pa11y as separate Node services —
axe-core-in-Playwright covers the a11y surface with one browser. (If a later need
arises, pa11y/Lighthouse can be added as alternate `engine:` values — the skill
output schema is engine-agnostic.)

---

## 1. The checks (what `visual_qa` validates)

Input: a target = `{url}` OR `{html_path}` (+ optional `{viewport}`,
`{aspect_ratio}`, `{mode}`). Output: per-check pass/fail + annotated screenshots.

### (a) Accessibility — axe-core
Run axe-core against the rendered page. Surface, at minimum:
- **color-contrast** (WCAG AA: 4.5:1 normal text, 3:1 large) — fail with the offending
  selector + measured ratio.
- **image-alt / input-image-alt** — missing/empty `alt`.
- **role / aria-** validity — invalid or orphaned ARIA.
- **heading-order / page-has-heading-one** — heading structure.
- **landmark / region** — content outside landmarks.
Each axe violation → one issue object `{id, impact, selector, help, nodes[]}`.

### (b) NOT-TOO-CROWDED — element density
Custom check (axe has no density rule). For the target viewport:
- Count **visible, non-whitespace** leaf/text-bearing elements within the viewport
  rect.
- Compute **content density** = visible-element-count / viewport-area (per megapixel),
  and **text-ink ratio** = sum(text bbox area) / viewport area.
- FAIL if density > `max_density` (default tuned per mode; slide mode stricter than
  app mode) OR text-ink ratio > `max_ink_ratio` (default 0.55). Report the busiest
  region.
Thresholds are config (`density_thresholds` in skill vars), defaulted but overridable.

### (c) NO text overlap — bounding-box intersection
- Collect bounding boxes of all **text nodes** (elements with non-empty rendered
  text, via `element.bounding_box()` in Playwright, walking text-bearing leaves).
- Pairwise rectangle-intersection test (sweep-line / interval overlap to avoid O(n²)
  blowup on dense pages). Two text rects with intersection area > `overlap_tol`
  (default a few px to allow anti-alias touch) → FAIL.
- Report each overlapping pair with both selectors + the intersection rect.

### (d) NO off-screen / clipped text — rect within viewport
- For each text/interactive element, check its bounding box is fully inside the
  viewport rect `[0,0,vw,vh]` (allowing a `clip_tol` margin).
- Distinguish **clipped** (partially outside) from **off-screen** (entirely outside,
  not via an intended scroll container). Account for legitimately scrollable
  containers via `overflow` computed style so a long scroll list isn't a false fail.
- FAIL on clipped/off-screen text with the element rect vs. viewport rect.

### (e) reveal-specific — slide fits viewport at presentation aspect ratio
- When `mode: slide`, set viewport to the presentation aspect ratio (default 16:9,
  e.g. 1280×720) and, **per slide**, navigate reveal.js (`Reveal.slide(i)` via JS
  eval or `#/i` fragment), wait for transition, then run checks (a)–(d) on that
  slide's `section.present`.
- Additionally assert the slide's content bbox union fits within the viewport (no
  slide taller/wider than the frame → would force reveal's auto-scroll/overflow).
- Iterate all slides via `Reveal.getTotalSlides()`; emit a per-slide row.

### Output (every mode)
```json
{
  "target": "...",
  "mode": "slide|app",
  "passed": false,
  "summary": {"a11y": N_violations, "density": M, "overlap": K, "clipping": J, "fit": F},
  "issues": [
    {"check": "a11y.color-contrast", "severity": "serious", "selector": "...",
     "detail": "ratio 2.9:1 < 4.5:1", "screenshot": "issue-0001.png"},
    {"check": "overlap", "selectors": ["#a","#b"], "rect": [..], "screenshot": "..."},
    ...
  ],
  "screenshots": {"full": "full.png", "annotated": "annotated.png", "per_slide": [...]}
}
```
Each issue gets a **screenshot annotation**: the full-page (or per-slide) capture with
the offending element's bounding box drawn (red for fail, amber for warn) via a
Playwright overlay-draw-then-screenshot pass. This is the "per-issue screenshot
annotation" deliverable.

---

## 2. Skill schema (how it plugs into gludd's existing `Skill` model)

gludd skills are **markdown + YAML frontmatter** parsed by
`skills/loader.py::parse_skill_md` into the `Skill` pydantic model
(`skills/skill.py`): fields `name, description, category, model_profile, tools,
trigger_patterns, tags, body, source_path`.

The skill file:

```text
.opencode/skills/visual_qa.md        # primary search path used by gludd_skill
```

```markdown
---
name: visual_qa
description: >
  Validate any rendered app/page/deck for accessibility (axe-core), visual appeal
  (density, no text overlap, no clipping), and reveal.js slide-fit. Returns
  pass/fail + per-issue annotated screenshots.
category: quality
model_profile: null        # deterministic; no model call needed for the checks
tools:
  - visual_qa.run          # the entrypoint tool (see §3)
trigger_patterns:
  - "check (the )?(accessibility|a11y|contrast) of"
  - "(visual|ui) (qa|review|check)"
  - "does (this|the) (page|deck|app|slide) (fit|overlap|look)"
tags: [a11y, visual, screenshot, playwright, axe-core, reveal, dogfood]
---

# visual_qa

Renders {{ target }} in headless Chromium, runs axe-core + density/overlap/clipping/
slide-fit checks, and writes an annotated report to {{ out_dir }}.

Inputs (template vars, StrictUndefined):
- target: URL or file path to validate (required)
- mode: "app" | "slide" (default "app")
- viewport: "WxH" (default "1280x720")
- aspect_ratio: e.g. "16:9" (slide mode)
- out_dir: where to write screenshots + report.json
- thresholds: optional overrides {max_density, max_ink_ratio, overlap_tol, clip_tol}

Run via the gludd_skill module or the make targets in DESIGN doc §5.
```

Notes:
- The skill **body** is rendered by the existing `render_skill`
  (`SandboxedEnvironment`, `StrictUndefined`) — so `{{ target }}` etc. fail closed on
  missing vars, exactly like every other gludd skill. No new render path.
- `model_profile: null` — the checks are deterministic (axe + geometry); no LLM call.
  (An *optional* "usefulness narrative" could call a model later; kept out of v1 to
  stay honest about what's deterministic vs. judgmental.)
- The skill is **invokable today via `gludd_skill`** (name=`visual_qa`) to render its
  instructions, but the actual browser execution needs the new runner in §3.

---

## 3. How gludd actually runs the checks (the executor)

`gludd_skill` renders skill *text*; it does not run a browser. We add a thin,
self-contained Python check engine that the skill's `visual_qa.run` tool dispatches to:

```text
src/general_ludd/visual_qa/
├── __init__.py
├── runner.py        # VisualQaRunner.run(target, mode, viewport, out_dir, thresholds) -> VisualQaReport
├── checks/
│   ├── a11y.py       # inject + run axe-core, parse violations
│   ├── density.py    # element-count + ink-ratio
│   ├── overlap.py    # text-bbox intersection (sweep-line)
│   ├── clipping.py   # rect-within-viewport
│   └── slide_fit.py  # reveal.js per-slide iteration + fit
├── annotate.py      # draw bbox overlays, screenshot per issue
└── report.py        # VisualQaReport pydantic model (mirrors §1 output)
```

- `runner.py` uses **Playwright (sync API) inside `asyncio.to_thread`** when called
  from the daemon path (matches the existing W3.3 pattern), or the sync API directly
  from CLI/make.
- axe-core is injected by evaluating the vendored `axe.min.js`
  (`src/general_ludd/visual_qa/vendor/axe.min.js`, pinned version recorded) then
  `axe.run()`; results parsed in `checks/a11y.py`. (Alternatively `axe-core-python`
  if we prefer the pip wrapper — vendoring keeps it offline-capable.)
- All geometry (overlap/clipping/density/slide-fit) is pure-Python over
  `element.bounding_box()` + `getComputedStyle` reads — no extra deps.
- `VisualQaReport` is a pydantic model (consistent with `DogfoodValidationResult`,
  `Skill`, etc.).

### Exposure surfaces
1. **CLI / make:** `make visual-qa TARGET=... MODE=app OUT=...` (see §5).
2. **Ansible:** an optional `gludd_visual_qa` module (or a role wrapping
   `gludd_skill` + a `command:` to the CLI) so playbooks/agents can self-check any
   page they build. v1 ships the CLI + skill; the dedicated module is a fast-follow.
3. **Deck loop:** `make deck-verify` calls `make visual-qa TARGET=build/index.html
   MODE=slide` and fails the deck build on any error issue.

---

## 4. Test harness (so gludd can self-check it)

Follows the repo's TDD + molecule conventions.

| Test | Asserts | Location |
|---|---|---|
| `test_visual_qa_density.py` | density check flags a deliberately crowded fixture, passes a sparse one | `tests/unit/` |
| `test_visual_qa_overlap.py` | two absolutely-positioned overlapping spans → overlap issue; non-overlapping → clean | `tests/unit/` |
| `test_visual_qa_clipping.py` | element pushed off-viewport → clipping issue; in-bounds → clean | `tests/unit/` |
| `test_visual_qa_a11y.py` | low-contrast + missing-alt fixture → axe violations surfaced; clean fixture → none | `tests/unit/` (gated on browser availability — skip-marked if no Chromium) |
| `test_visual_qa_slide_fit.py` | an oversized reveal slide fixture → fit failure; well-sized → pass | `tests/unit/` |
| `test_visual_qa_skill_renders.py` | `gludd_skill name=visual_qa` renders with required vars, fails closed on missing | `tests/integration/` |
| `test_visual_qa_e2e.py` | run full skill on the built deck fixture, assert report schema | `tests/e2e/` |
| molecule `test_gludd_visual_qa` (if module shipped) | role/module runs against a static fixture page | `molecule/playbooks/` |

**Fixtures** are tiny static HTML files under `tests/fixtures/visual_qa/` (crowded,
overlap, clipped, low-contrast, missing-alt, oversized-slide, clean) — deterministic,
no network, browser-availability-gated with a pytest skip marker so the rest of the
gate stays green where Chromium isn't installed.

---

## 5. Make targets to add

| Target | Does |
|---|---|
| `visual-qa-install` | `playwright install chromium` (+ vendor axe pin check) |
| `visual-qa TARGET=... [MODE=] [OUT=]` | run `VisualQaRunner` on a target → report + screenshots |
| `visual-qa-test` | run the `tests/.../test_visual_qa_*.py` suite (browser-gated subset skipped if no Chromium) |

---

## 6. GLUDD GAPS this skill forces (honest)

These must be added — none exist today:

1. **No headless browser / Playwright dependency.** Must add `playwright` to
   `pyproject.toml` deps and a `playwright install chromium` step
   (`make visual-qa-install`). This is the **single biggest add** — Chromium is a
   large binary; CI must cache it or the gate slows.
2. **No axe-core vendored.** Must vendor `axe.min.js` (pinned) under
   `src/general_ludd/visual_qa/vendor/` with a `VERSION` file.
3. **No browser-gated test convention yet.** Need a pytest marker
   (`@pytest.mark.requires_browser`) + skip logic so the existing gate doesn't go red
   on machines without Chromium. Ratchet/preflight must tolerate skips.
4. **CI cost.** Installing Chromium in CI adds time + cache; the molecule/matrix jobs
   are already "CI-green unverified" (README) — adding a browser stack must not be
   conflated with fixing that. Keep `visual-qa-test` a **separate optional job**, not
   in the core gate, until it's proven stable.
5. **The puppeteer MCP catalog entry** (`mcp/catalog.py:237`) is archived/dead — do
   not wire to it; it is not a substitute for the Playwright runner.

Because of (1)–(3), the **deterministic geometry checks** (density/overlap/clipping/
slide-fit logic, fixtures, report schema, skill markdown) are buildable **now**
against a stub renderer; the **live browser execution + axe-core + real screenshots**
are gated on adding Playwright.

---

## 7. Buildable now vs. needs the new dep

| Piece | Now? | Blocker |
|---|---|---|
| `visual_qa.md` skill file + frontmatter | YES | — (renders via existing `gludd_skill`) |
| `VisualQaReport` pydantic schema | YES | — |
| Geometry checks (overlap/clipping/density/slide-fit) + unit fixtures | YES (logic testable against synthetic bbox data) | — |
| axe-core a11y check | NO | needs Playwright + vendored axe.min.js |
| Real screenshots + annotations | NO | needs Playwright/Chromium |
| `make visual-qa` live run | NO | needs `visual-qa-install` |
| `gludd_visual_qa` Ansible module + molecule | FAST-FOLLOW | after CLI proven |
