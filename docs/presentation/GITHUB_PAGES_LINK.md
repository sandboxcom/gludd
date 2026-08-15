# GitHub Pages Link — gludd reveal.js Deck

> **Superseded design notice (2026-07-09):** the plan below (build to a
> gitignored `docs/presentation/build/` and publish that) was never
> implemented. The deck actually shipped is tracked directly at
> `docs/presentation/deck/index.html` (no gitignored build output), and is
> deployed by `.github/workflows/pages.yml`, which runs `make deck-build` in
> CI and uploads `docs/presentation/deck/` as the Pages artifact. The GitHub
> Pages site itself was created 2026-07-09 with `build_type=workflow`. Sections
> 2–4 below describe the original (superseded) plan and are kept for history;
> treat the workflow file and the tracked deck path as the current source of
> truth, not this document's mechanism description.

Status: PLANNED (not yet live). This document records the canonical URL, the
recommended publish mechanism, the prerequisites to make it live, and the exact
README snippet for the orchestrator to paste in after the meta-commit.

---

## 1. Canonical URL (planned)

```text
https://sandboxcom.github.io/gludd/
```

This is the root of the GitHub Pages site for the `sandboxcom/gludd` repo.
The deck's `index.html` is published at the root of the Pages deploy, so the
above URL loads the presentation directly.

> Honest flag: this URL does NOT resolve until both prerequisites below are met
> (Pages enabled in repo settings + deck built and published via the workflow).

---

## 2. Why this publish mechanism (not Pages-from-/docs)

The deck design (`DESIGN_revealjs_deck.md` §3) specifies:

```text
docs/presentation/
└── build/index.html    # GENERATED final deck, gitignored
```

`docs/presentation/build/` is a generated artifact that is gitignored. It is
never committed to `main`. This rules out the "Pages served from /docs folder on
main branch" option — there is nothing to serve from `/docs` on `main` because
the built HTML is not committed.

**Recommended mechanism: GitHub Actions Pages deploy workflow**

A `.github/workflows/pages.yml` workflow that:
1. Checks out the repo
2. Installs dependencies (`make install` / `uv sync`)
3. Runs `make deck` to generate `docs/presentation/build/index.html`
4. Uploads `docs/presentation/build/` as the Pages artifact via
   `actions/upload-pages-artifact`
5. Deploys via `actions/deploy-pages`

This publishes `docs/presentation/build/index.html` as
`https://sandboxcom.github.io/gludd/index.html` (served at root as
`https://sandboxcom.github.io/gludd/`).

A `gh-pages` branch is NOT needed; the Actions Pages deploy workflow is the
single-source approach and avoids a secondary branch to maintain.

---

## 3. Prerequisite workflow stub

Add `.github/workflows/pages.yml`:

```yaml
name: Deploy deck to GitHub Pages

on:
  push:
    branches: [main]
    paths:
      - 'docs/presentation/deck/**'
      - 'scripts/build_deck.py'
      - 'scripts/parse_readme_status.py'
      - 'README.md'
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Install dependencies
        run: make install

      - name: Build deck
        run: make deck

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: docs/presentation/build/

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

Trigger: pushes to `main` that touch the deck source tree, or manual
`workflow_dispatch`. The `make deck` target must exist and must produce
`docs/presentation/build/index.html` (see `BUILD_TASK_LIST.md` Wave 0 item 8).

---

## 4. Prerequisites to make the URL live

Both must be satisfied; the URL is a 404 until both are done.

### 4a. Enable GitHub Pages in repo settings (user action required)

1. Go to `https://github.com/sandboxcom/gludd/settings/pages`
2. Under "Build and deployment", set Source to **"GitHub Actions"**
3. Save. No branch selection is needed (the workflow handles it).

This is a repo-settings action. It requires an account with admin access to
`sandboxcom/gludd`. The harness/agent cannot do this — it requires the repo
owner/admin.

### 4b. Deck source must be built and the workflow must have run

The workflow only triggers on `main` pushes or `workflow_dispatch`. The deck
source (`docs/presentation/deck/`) must be committed to `main` (Wave 0 of
`BUILD_TASK_LIST.md`) before the workflow can produce output. Until then,
`make deck` will fail (no template to render).

Sequence:
1. Commit Wave 0 deck source to `main`
2. Merge `.github/workflows/pages.yml`
3. Enable Pages (step 4a)
4. Either push to `main` (auto-trigger) or run workflow manually
5. URL goes live

---

## 5. README snippet (copy-paste ready)

Add this as a new section in README.md after the architecture/modules table,
or as a badge line near the top. The orchestrator should paste this in during
the meta-commit (do not edit README.md while the meta-commit agent is running).

---

### Variant A — badge/inline line (terse, near the top of README)

```markdown
**Presentation:** [gludd, honestly — reveal.js deck](https://sandboxcom.github.io/gludd/) *(live once Pages is enabled and the deck is built; see `docs/presentation/`)*
```

### Variant B — dedicated section (verbose, after the architecture section)

```markdown
## Presentation

A self-describing reveal.js deck — "gludd, honestly" — is generated from live
E2E artifacts and committed design templates. Every maturity claim on a slide
carries the same evidence token the README table carries; missing data renders
an honest "NO DATA — run `make deck-data`" placeholder rather than a fabricated
screenshot.

**Planned URL:** https://sandboxcom.github.io/gludd/

> This link goes live once:
> 1. GitHub Pages is enabled in repo settings (Source: GitHub Actions)
> 2. The deck source (`docs/presentation/deck/`) is committed to `main`
> 3. The `.github/workflows/pages.yml` workflow has run successfully
>
> Until then, build and preview locally with `make deck && make deck-serve`.

Source: `docs/presentation/` | Design: `DESIGN_revealjs_deck.md` | Build tasks: `BUILD_TASK_LIST.md`
```

---

## 6. Summary

| Item | Value |
|---|---|
| Canonical URL | `https://sandboxcom.github.io/gludd/` |
| Publish mechanism | GitHub Actions Pages deploy (`.github/workflows/pages.yml`) |
| Build artifact served | `docs/presentation/build/index.html` (gitignored, regenerated by `make deck`) |
| Pages source setting | "GitHub Actions" (not `/docs` folder — build output is gitignored) |
| User action required | Enable Pages at `github.com/sandboxcom/gludd/settings/pages` |
| Wave 0 must land first | `docs/presentation/deck/` source committed to `main` |
| Status | PLANNED — URL is not yet live |
