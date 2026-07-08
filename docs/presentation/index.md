# Presentation

Reveal.js deck design and build tasks.

## Contents

| Document | Description |
|----------|-------------|
| [Accessibility Visual QA Skill](a11y-visual-qa-skill.md) | A11y visual QA skill design |
| [GitHub Pages Link](github-pages-link.md) | GitHub Pages deployment |
| [Build Task List](build-task-list.md) | Build task list |
| [Reveal.js Deck Design](revealjs-deck.md) | Reveal.js deck design |

## Status

> **Built and deployed.** The `make deck`, `make deck-serve`, `make deck-data`, `make deck-build`, and `make deck-honesty` targets are defined in the Makefile. The `docs/presentation/deck/` source tree and `scripts/build_deck.py` are committed. GitHub Pages deploys via `.github/workflows/pages.yml`.

## Live URL

https://sandboxcom.github.io/gludd/

## Go-Live Conditions

1. GitHub Pages enabled in repo settings (Source: GitHub Actions)
2. Deck source (`docs/presentation/deck/`) committed to `main`
3. `.github/workflows/pages.yml` workflow has run successfully

---

[Back to Documentation Index](../index.md)
