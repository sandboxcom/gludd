# `general_ludd.web.ux_engineering` — UX Engineering Audit

Automated UX engineering analysis: WCAG accessibility audits, z-index stacking context analysis, visual hierarchy validation, and Nielsen usability heuristic checks.

## Quick start

```yaml
- name: Run accessibility audit
  hosts: localhost
  vars:
    ux_engineering_target_url_or_file: "https://example.com"
    ux_engineering_accessibility_standard: "wcag21_aa"
    ux_engineering_check_z_index: true
    ux_engineering_check_visual_hierarchy: true
  roles:
    - general_ludd.web.ux_engineering
```

## Knowledge Domains

### Z-axis / Stacking Context
- **z-index only works on positioned elements**: `position: relative | absolute | fixed | sticky` (not `static`)
- **Stacking context triggers**: `position` non-static, `opacity < 1`, `transform`, `filter`, `will-change`, `isolation: isolate`, `perspective`, `clip-path`, `mask`, `mix-blend-mode`, `z-index` with `flex`/`grid` items
- **Paint order**: background → border → negative z-index children → block-level descendants → float descendants → inline descendants → positioned children (z-index: auto, then 0, then positive)
- **Compositing layers**: GPU-accelerated via `transform: translateZ(0)` or `will-change: transform` — improves scroll performance but increases memory
- **GPU acceleration trade-offs**: smoother animations, but each layer consumes VRAM; over-promotion causes jank

### Visual Hierarchy
- **Size**: larger elements draw attention first — use scale intentionally
- **Color contrast**: high contrast (dark on light) draws focus; low contrast recedes
- **Whitespace**: items closer together are perceived as related (proximity = grouping)
- **Typographic hierarchy**: h1 most prominent → h6 least; consistent scale ratio (e.g. 1.25 major third)
- **F-pattern / Z-pattern reading**: F-pattern for text-heavy pages (scan left edge + first lines); Z-pattern for sparse/landing pages (top-left → top-right → bottom-left → bottom-right)
- **Gestalt principles**: proximity (near = related), similarity (same look = same meaning), continuity (aligned elements form a path), closure (brain fills gaps)

### Accessibility (WCAG)
- **WCAG 2.1/2.2 conformance levels**: A (minimum), AA (standard, most laws reference), AAA (enhanced)
- **ARIA**: roles (banner, navigation, main, complementary, contentinfo), states (aria-expanded, aria-selected, aria-checked), properties (aria-label, aria-describedby, aria-labelledby)
- **Semantic HTML**: `<nav>` not `<div class="nav">`, `<main>` for primary content, `<button>` for actions (not `<div onclick>`), `<header>`/`<footer>`/`<article>`/`<aside>`/`<section>`
- **Keyboard navigation**: tabindex (0 = focusable, -1 = script-focusable only, positive = custom order — avoid), focus management (move focus after modal open/close), skip navigation links
- **Screen reader compatibility**: alt text on images (decorative: alt=""), form labels (`<label for>` or `aria-labelledby`), live regions (`aria-live="polite"`/`"assertive"`, `role="status"`/`"alert"`)
- **Color contrast ratios**: normal text ≥ 4.5:1 (AA), large text (≥18px or 14px bold) ≥ 3:1 (AA); AAA: 7:1 / 4.5:1
- **Focus indicators**: visible focus ring (never `outline: none` without replacement), `:focus-visible` for mouse vs keyboard distinction
- **Reduced motion**: `@media (prefers-reduced-motion: reduce)` — disable animations/transitions

### Usability Heuristics (Nielsen's 10)
1. **Visibility of system status**: loading spinners, progress bars, "N items in cart"
2. **Match between system and real world**: plain language, real-world metaphors (trash icon = delete)
3. **User control and freedom**: undo, back buttons, cancelable operations, "emergency exit"
4. **Consistency and standards**: same button means same action across pages; platform conventions (Ctrl+S = save)
5. **Error prevention**: confirmation dialogs for destructive actions, input constraints (date pickers, dropdowns), disabled submit until valid
6. **Recognition not recall**: visible options (not memorized commands), autocomplete, breadcrumbs
7. **Flexibility and efficiency**: keyboard shortcuts for power users, customizable dashboards, defaults that work for novices
8. **Aesthetic and minimalist design**: no irrelevant information, each extra unit of information competes with relevant units
9. **Help users recognize/diagnose/recover from errors**: specific error messages ("Email is invalid" not "Error"), constructive suggestions ("Did you mean example@gmail.com?")
10. **Help and documentation**: searchable, task-focused, step-by-step, concise

### Testing Frameworks
- **axe-core**: automated accessibility engine — catches ~57% of issues, integrates with Playwright/Puppeteer/Selenium
- **Lighthouse**: audits performance, accessibility, SEO, best practices — CLI (`lighthouse <url>`) or Chrome DevTools
- **WAVE**: visual overlay showing ARIA, contrast, structure issues on rendered page
- **pa11y**: headless accessibility testing for CI (`pa11y <url>`), JSON output, threshold configuration
- **Puppeteer/Playwright**: automated browser interaction — fill forms, click buttons, verify focus order, test keyboard navigation

## Parameters

| Variable                               | Default         | Description                              |
|----------------------------------------|-----------------|------------------------------------------|
| `ux_engineering_target_url_or_file`    | `""`            | URL or local HTML file to audit          |
| `ux_engineering_accessibility_standard`| `"wcag21_aa"`   | WCAG conformance target                  |
| `ux_engineering_check_z_index`         | `true`          | Analyze stacking context conflicts       |
| `ux_engineering_check_visual_hierarchy`| `true`          | Validate heading/document structure      |
| `ux_engineering_usability_heuristics`  | `false`         | Run Nielsen heuristic checks             |
| `ux_engineering_output_dir`            | `/tmp/...`      | Output directory for audit artifacts     |

## Results

The `_uxe_output` fact contains the audit result JSON; a `ux_engineering.json` artifact is written to the output directory.
