---
name: revealjs-presentation
description: Build, serve, and validate reveal.js presentations for gludd. Covers deck structure, diagram creation, stats injection, honesty linting, and GitHub Pages deployment. Use when creating or updating project presentations.
---

# reveal.js Presentation Skill

## When to use
- User asks to "fix the presentation", "update the deck", "add slides"
- User asks to "make a presentation" or "create slides"
- User mentions reveal.js, deck, or sandboxcom.github.io/gludd
- README presentation link is broken or stale

## Architecture
- **Source**: `docs/presentation/deck/index.html` (reveal.js 5.1.0, CDN-loaded)
- **Data**: `docs/presentation/deck-data.json` (auto-generated from README status table)
- **Build**: `scripts/build_deck.py` (regenerates data + lints)
- **Serve**: `make deck-serve` (http://localhost:8080/)
- **Deploy**: `.github/workflows/pages.yml` (GitHub Pages)
- **URL**: https://sandboxcom.github.io/gludd/

## Workflow

### 1. Update deck content
- Edit `docs/presentation/deck/index.html` directly (reveal.js HTML)
- Use `<section>` tags for slides; `data-vertical` for vertical slides
- CSS is inline in `<style>` block
- NO external CSS/JS files (CDN only)

### 2. Regenerate stats data
- Run `make deck-data` — outputs JSON with feature counts, test counts, version
- Run `make deck` — regenerates `deck-data.json` + runs honesty lint
- Copy relevant stats into the deck HTML (manual; the build script doesn't auto-inject yet)

### 3. Create diagrams
- **Mermaid diagrams**: embed directly in HTML via `<div class="mermaid">...</div>` (reveal.js mermaid plugin)
- **SVG diagrams**: create as standalone `.svg` files in `docs/presentation/deck/assets/`, reference via `<img src="assets/diagram.svg">`
- **ASCII art**: convert to SVG using `docs/presentation/scripts/ascii_to_svg.py` (if created) or hand-draw
- Architecture diagram source: `README.md:188-221` (ASCII)
- Event loop flow: claim → dispatch → review → reconcile
- Security layers: 3-layer guardrail model (config → plugin → prompt)

### 4. Honesty lint
- Run `make deck-honesty` — checks for banned marketing tokens
- Every stat must be machine-produced (cite `make deck-data` or `make gate` output)
- No hardcoded counts that could go stale
- "100%" claims need evidence tokens or CI-UNVERIFIED badges

### 5. Serve locally
- `make deck-serve` — serves at http://localhost:8080/
- Open in browser to verify rendering
- Check: diagrams render, stats are current, links work

### 6. Deploy
- Commit changes to `docs/presentation/deck/`
- Push to master — `.github/workflows/pages.yml` auto-deploys
- Verify at https://sandboxcom.github.io/gludd/ (may take 1-2 min)

## Content guidelines

### Non-technical audience
- Lead with "what gludd does" not "how gludd works"
- Use analogies: "gludd is like a junior developer that never sleeps"
- Avoid jargon: "event loop" → "work cycle", "dispatch" → "assigns the task"
- Show concrete examples: "you type 'fix the login bug', gludd writes the code, tests it, and submits a pull request"
- Use figures + diagrams over prose

### Granular walkthrough
- Show the FULL lifecycle: user submits todo → daemon claims it → model generates code → tests run → review → git commit
- Each step gets its own slide with a diagram
- Include real code snippets (small, annotated)
- Include real stats (test count, role count, provider count)

### Diagrams that reflect reality
- Architecture diagram must match actual code (src/general_ludd/ structure)
- Event loop must show actual phases (claim → dispatch → review → reconcile)
- Security model must show actual 3 layers (config → plugin → prompt)
- Stats must come from `make deck-data` (not hardcoded)

## File structure
```
docs/presentation/
  deck/
    index.html          # main reveal.js deck
    assets/
      architecture.svg  # system architecture diagram
      event-loop.svg    # event loop flow diagram
      security.svg      # 3-layer security model
      stats.json        # auto-generated stats
  deck-data.json        # auto-generated feature data
  scripts/
    build_deck.py       # build script
    ascii_to_svg.py     # ASCII → SVG converter (if created)
  DESIGN_revealjs_deck.md
  BUILD_TASK_LIST.md
```

## Common pitfalls
- **Stale version**: always update the title slide version to match `pyproject.toml`
- **Stale stats**: always run `make deck-data` before editing stats slides
- **Broken links**: verify all internal links + the GitHub Pages URL
- **CDN failures**: reveal.js CDN can be slow; consider vendoring for offline use
- **Mermaid not rendering**: ensure the mermaid plugin is loaded after reveal.js
- **ASCII art rendering**: use `<pre>` tags or convert to SVG; don't rely on HTML entities
