---
name: a11y-visual-qa
description: Static HTML accessibility + visual-QA checker — runs WCAG-oriented checks and structural visual-QA on a built HTML file or site directory
category: quality
model_profile: null
tools: [read, glob, bash]
trigger_patterns:
  - "a11y check"
  - "accessibility check"
  - "visual qa"
  - "check accessibility"
  - "run a11y"
  - "wcag check"
tags: [a11y, accessibility, visual-qa, quality, html, wcag]
---

# A11y + Visual-QA Skill

## Purpose

Run a static accessibility and visual-QA audit on a built HTML file or a
directory of HTML files (e.g. a reveal.js presentation export, a docs build,
or a static site). No browser required — pure Python stdlib `html.parser`.

## Usage

### Single file

```bash
make a11y-check FILE=docs/presentation/build/index.html
```

### Site directory (recurses over all *.html)

```bash
make a11y-check SITE=docs/presentation/build/
```

Exits 0 on pass, 1 on any `error`-severity finding.

## What Is Checked

### A11y (Accessibility)

| Check | Severity | Rule |
|-------|----------|------|
| `lang-attribute` | error | `<html lang="...">` must be set |
| `title-element` | error | `<title>` must be present and non-empty |
| `landmark-missing` | warning | `<main>`, `<nav>`, `<header>`, `<footer>` (or role= equivalents) |
| `heading-order` | error/warning | h1 present; no skipped levels (h2 -> h4) |
| `img-alt` | error | every `<img>` must have an `alt` attribute |
| `aria-presence` | info | at least one `aria-*` or `role=` attribute present |
| `color-contrast` | warning | heuristic flag on identical / known-bad inline color+background pairs |

### Visual / Structural QA

| Check | Severity | Rule |
|-------|----------|------|
| `viewport-meta` | warning | `<meta name="viewport">` must be present |
| `overflow-heuristic` | info | `overflow:hidden` on `<body>`/`<html>` suppresses scrollbars |
| `broken-asset` | warning | relative `src`/`href` assets that don't exist on disk |

## Report Format

Each finding:
```json
{
  "check": "img-alt",
  "severity": "error",
  "message": "<img> missing alt attribute",
  "element": "<img src=\"slide.png\">"
}
```

## Python API

```python
from general_ludd.quality.a11y_checker import (
    check_html,        # check_html(html_str, file_path="...") -> A11yReport
    check_html_file,   # check_html_file("/path/to/index.html") -> A11yReport
    check_site_dir,    # check_site_dir("/path/to/site/") -> list[A11yReport]
)
```

## Deferred (Follow-up Wave)

- Browser-rendered visual diff / screenshot compare (Playwright)
- Real WCAG 2.1 color-contrast ratio calculation (APCA / WCAG formula)
- CSS file parsing for external stylesheet color rules
- Link checker for external URLs (HTTP HEAD requests)
- PDF export a11y (tagged PDF structure)
