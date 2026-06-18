# BUILD TASK LIST — presentation deck + visual_qa skill

Ordered, in waves. Dependency on **Task #3's E2E data** is called out explicitly.
Both design docs: `DESIGN_revealjs_deck.md` (A), `DESIGN_a11y_visual_qa_skill.md` (B).

## Wave 0 — buildable NOW (no E2E data, no browser dep)
1. `scripts/parse_readme_status.py` — parse README Feature & Task Completion Status
   table → `features[]` (title, pct, evidence, bucket). [A §2, §6]
2. reveal.js source skeleton: `docs/presentation/deck/` template + partials + theme +
   vendored pinned reveal.js. [A §3]
3. `scripts/build_deck.py` (string/Jinja2 substitution, reuse `skills/renderer.py`
   SandboxedEnvironment) + `_no_data.html` placeholder discipline. [A §2, §4]
4. `scripts/deck_honesty_lint.py` + banned-marketing-token + %-must-match-README
   checks. [A §5]
5. Static slides §1 intro, §3 overlap matrix, §4 weaknesses (all from README). [A §1]
6. `visual_qa.md` skill file (frontmatter + body) — renders via existing
   `gludd_skill` today. [B §2]
7. `VisualQaReport` pydantic schema + geometry checks (density/overlap/clipping/
   slide-fit) + synthetic-bbox unit tests + fixtures. [B §1, §4]
8. make targets: `parse-readme-status`, `deck`, `deck-serve`. [A §4]

## Wave 1 — add the browser dependency (the one hard gap)
9. Add `playwright` to `pyproject.toml`; `make visual-qa-install`
   (`playwright install chromium`). [B §6.1]
10. Vendor pinned `axe.min.js` + VERSION. [B §6.2]
11. `@pytest.mark.requires_browser` marker + skip logic so the core gate stays green
    without Chromium. [B §6.3]
12. `src/general_ludd/visual_qa/runner.py` + `checks/a11y.py` + `annotate.py`
    (real screenshots + axe-core). [B §3]
13. `make visual-qa`, `make visual-qa-test`. [B §5]
14. Keep `visual-qa-test` a SEPARATE optional CI job, not in the core gate. [B §6.4]

## Wave 2 — DEPENDS ON TASK #3 E2E DATA
15. `make deck-data` — collect dogfood run log
    (`tests/e2e/test_obj16_dogfood_loop.py` / `DogfoodRunner`), greenfield workspace
    tree, model-discovery JSON, `/api/metrics`+`/api/traces` snapshot, features[] →
    `deck-data.json`. **Blocked until the E2E flows emit capturable artifacts.** [A §2]
16. Dynamic scenario slides §5.1–5.3, 5.5–5.7 wired to deck-data.json. [A §1]
17. **§5.4 site screenshot** — requires BOTH Wave 1 (Playwright) AND the greenfield
    E2E output. Until then: `_no_data.html` placeholder, not a fake. [A §1, §5.7]

## Wave 3 — close the loop + fast-follows
18. `make deck-verify` = run `visual_qa` (B) on built deck (MODE=slide), fail build on
    a11y/density/overlap/clip/fit errors. THIS links A↔B. [A §4]
19. `gludd_visual_qa` Ansible module + molecule scenario (fast-follow after CLI
    proven). [B §3, §6]
20. `tests/e2e/test_visual_qa_e2e.py` against the built deck. [B §4]

## Honest summary
- **Now (no blockers):** Wave 0 — ~all of deck §1–§4 + the deterministic half of the
  skill.
- **Needs one new dep:** Wave 1 — Playwright/Chromium + axe. This is the only hard
  infra add.
- **Needs Task #3 E2E data:** Wave 2 — every real-numbers/real-screenshot scenario
  slide (§5). §5.4 is double-blocked (browser AND greenfield output).
- Nothing here fabricates data: missing artifacts render honest placeholders by design.
