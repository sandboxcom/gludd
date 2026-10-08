# Web Collection — `general_ludd.web`

Comprehensive Ansible collection for web frontend engineering — from design research
and token extraction through component generation to accessibility validation. Covers
the complete web development lifecycle: research a target site, extract its design
system, scaffold matching HTML/CSS, integrate with modern frameworks (React, Next.js,
HTMX, GraphQL), and validate the result against WCAG 2.1 AA.

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                          Agent / Playbook                            │
│  general_ludd.web.design_research      general_ludd.web.html_css_core│
│  general_ludd.web.javascript_debug     general_ludd.web.framework_integration│
│  general_ludd.web.accessibility_audit  general_ludd.web.component_scaffold│
└────────┬──────────┬──────────┬──────────┬──────────┬──────────┬─────┘
         │          │          │          │          │          │
  ┌──────▼──────┐ ┌─▼────────┐ ┌▼────────┐ ┌▼───────┐ ┌▼───────┐ ┌▼──────────┐
  │design_research│ │html_css │ │javascript│ │framework│ │access  │ │component   │
  │ fetch + token│ │_core    │ │_debug   │ │_int    │ │_audit  │ │_scaffold   │
  │ extraction  │ │validate │ │lint +   │ │React,   │ │WCAG AA │ │token-driven│
  │             │ │+ audit  │ │errors   │ │Next,etc │ │check   │ │generation  │
  └──────┬──────┘ └─┬───────┘ └┬────────┘ └┬───────┘ └┬───────┘ └┬──────────┘
         │          │          │          │          │          │
  ┌──────▼──────────▼──────────▼──────────▼──────────▼──────────▼──────────┐
  │                         Python Libraries                               │
  │  html.parser (stdlib) — HTML5 parsing + semantic tree                  │
  │  urllib / httpx — HTTP fetching for design research                   │
  │  cssutils — structured CSS parsing, token extraction                   │
  │  re (stdlib) — regex-based CSS property checking                       │
  │  json (stdlib) — design token serialization                            │
  │  Node.js + eslint — JS linting (optional, javascript_debug)            │
  │  axe-core / Lighthouse CLI — accessibility validation                  │
  └────────────────────────────────────────────────────────────────────────┘
```

### Data flow

1. **Agent** invokes a role via FQCN with target URL, input files, or specification
2. **Role** fetches pages, parses HTML/CSS/JS, extracts tokens, generates artifacts
3. **Python libraries** parse and analyze; Node.js tooling handles JS linting and
   browser-adjacent validation
4. **Artifacts** are written to `artifact_dir` (`/tmp/gludd-web/{role_name}/` by
   default) — design tokens JSON, validated HTML reports, generated components,
   accessibility audit results

## Section 1: Collection Overview

The web is built on three layers — HTML (structure), CSS (presentation), and
JavaScript (behavior) — plus the frameworks and tooling that compose them.
This collection treats each layer as *observable and analyzable*: HTML structure
is validated, CSS properties are checked and extracted, JavaScript errors are
classified, design systems are reverse-engineered from live websites, and the
result is validated against accessibility standards. Every operation produces
an auditable artifact.

### Why the web platform matters

| Domain | Role | Examples |
|--------|------|----------|
| **Design research** | Reverse-engineer an existing site's design system | Extract color palette, typography scale, spacing grid, breakpoints |
| **HTML/CSS authoring** | Validate semantic structure and responsive design | ARIA landmark audit, media query coverage, CSS syntax checking |
| **JavaScript quality** | Lint, error-classify, and debug production JS | Console error triage, source map verification, unhandled rejection detection |
| **Framework integration** | Scaffold, generate, test, and analyze framework apps | React component scaffolding, Next.js route generation, HTMX attribute injection, GraphQL schema generation |
| **Accessibility** | Validate against WCAG 2.1 AA | axe-core audit, Lighthouse accessibility score, keyboard navigation check |
| **Component generation** | Generate UI components from design tokens | Token-driven React components, CSS custom property systems |

### Collection scope

| Role | Purpose | Backend |
|------|---------|---------|
| `design_research` | Fetch target URL, extract CSS tokens (colors, fonts, spacing), capture layout structure, detect CSS framework | `urllib` / `httpx`, `cssutils`, `html.parser` |
| `html_css_core` | Validate HTML5 semantic structure, check CSS syntax, generate responsive boilerplate, audit ARIA landmarks | `html.parser` (stdlib), `re` |
| `javascript_debug` | Check JS syntax, lint with eslint, analyze error patterns, verify source maps, detect unhandled rejections | Node.js + eslint (optional), `re` |
| `framework_integration` | Scaffold/generate/test/analyze for React, Next.js, HTMX, GraphQL, REST APIs | Framework-specific tooling |
| `accessibility_audit` | WCAG 2.1 AA compliance check via axe-core and Lighthouse | axe-core CLI, Lighthouse CLI |
| `component_scaffold` | Generate token-driven HTML/CSS/React components from design tokens | Token injection + template rendering |

## Section 2: Web Design Knowledge Reference

### 2.1 HTML5

#### Semantic elements

| Element | ARIA Implicit Role | Purpose |
|---------|-------------------|---------|
| `<header>` | `banner` (if top-level) | Introductory content or navigation |
| `<nav>` | `navigation` | Major navigation block |
| `<main>` | `main` | Dominant content of the document |
| `<article>` | `article` | Self-contained composition |
| `<section>` | `region` (with accessible name) | Thematic grouping |
| `<aside>` | `complementary` | Tangentially related content |
| `<footer>` | `contentinfo` (if top-level) | Footer for nearest sectioning content |
| `<figure>` / `<figcaption>` | `figure` | Self-contained media with caption |
| `<details>` / `<summary>` | `group` | Expandable disclosure widget |
| `<time>` | none (inline) | Machine-readable date/time |

#### ARIA landmarks

Landmarks provide navigation shortcuts for screen readers. Prefer native HTML5
elements (which have implicit roles) over explicit ARIA roles where possible.

| Landmark Role | Native Equivalent | Usage |
|---------------|-------------------|-------|
| `banner` | `<header>` (top-level) | Site-wide header, once per page |
| `navigation` | `<nav>` | Primary and secondary nav |
| `main` | `<main>` | One per page |
| `complementary` | `<aside>` | Sidebar content |
| `contentinfo` | `<footer>` (top-level) | Copyright, legal, contact |
| `search` | `role="search"` (no native equivalent) | Search form |
| `form` | `<form aria-label="...">` | Form with accessible name |
| `region` | `<section aria-label="...">` | Grouped content with label |

**Rules**:
- Every page MUST have exactly one `main` landmark
- Top-level `header` and `footer` MUST NOT be nested in other landmarks
- Every landmark SHOULD have a unique accessible name (`aria-label` or `aria-labelledby`)
- `<nav>` blocks SHOULD be labeled if multiple exist

#### Forms and validation

```html
<form novalidate> <!-- suppress browser validation for custom UX -->
  <label for="email">Email</label>
  <input type="email" id="email" name="email"
         required autocomplete="email"
         pattern="[^@\s]+@[^@\s]+\.[^@\s]+"
         aria-describedby="email-hint email-error">
  <div id="email-hint">Must be a valid email address.</div>
  <div id="email-error" role="alert" hidden>Please enter a valid email.</div>
</form>
```

**Constraint validation API**:
- `input.validity` (ValidityState): `valueMissing`, `typeMismatch`, `patternMismatch`,
  `tooShort`, `tooLong`, `rangeUnderflow`, `rangeOverflow`, `stepMismatch`, `badInput`,
  `customError`
- `input.checkValidity()` → boolean
- `input.reportValidity()` → boolean + shows browser UI
- `input.setCustomValidity('message')` → sets `customError` flag

#### Custom data attributes

```html
<div data-controller="tabs" data-tabs-active-tab="2"
     data-tabs-target="panel" data-action="click->tabs#select">
```

- Prefix: `data-` (spec-compliant)
- Access via `element.dataset.tabsActiveTab` (camelCase conversion)
- Used by HTMX, Stimulus, Alpine.js for declarative behavior binding

#### Web Components

Three technologies compose a Web Component:

| Spec | Purpose | Example |
|------|---------|---------|
| **Custom Elements** | Define new HTML tags | `<user-card name="Alice"></user-card>` |
| **Shadow DOM** | Encapsulated DOM + styles | `this.attachShadow({mode: 'open'})` |
| **`<template>`** | Inert DOM fragment for cloning | `<template id="card-tmpl">...</template>` |

**Custom element lifecycle**:
- `connectedCallback()` — element added to document
- `disconnectedCallback()` — element removed from document
- `adoptedCallback()` — element moved to new document
- `attributeChangedCallback(name, oldVal, newVal)` — observed attribute changed
- `static observedAttributes = ['name', 'email']` — declare tracked attributes

**Shadow DOM encapsulation**:
- Styles inside shadow DOM don't leak out
- Styles outside shadow DOM don't pierce in (except inherited properties and
  CSS custom properties, which cross the boundary)
- `::part()` pseudo-element exposes named parts for external styling
- Slots (`<slot>`) compose light DOM children into shadow DOM

### 2.2 CSS3

#### Cascade layers (`@layer`)

Control the cascade priority explicitly rather than relying on specificity wars:

```css
@layer reset, base, components, utilities;

@layer reset {
  *, *::before, *::after { box-sizing: border-box; margin: 0; }
}

@layer base {
  h1 { font-size: 2rem; }
}

@layer components {
  .card { padding: 1rem; border-radius: 0.5rem; }
}

@layer utilities {
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; }
}
```

A declaration in a later-named layer always wins, regardless of specificity.
Unlayered styles win over ALL layered styles. `!important` inverts layering:
an `!important` in an earlier layer beats an `!important` in a later one.

#### Container queries (`@container`)

Style elements based on their parent container's size, not the viewport:

```css
.card-grid {
  container-type: inline-size;
  container-name: card-grid;
}

@container card-grid (min-width: 400px) {
  .card { display: flex; flex-direction: row; }
}

@container card-grid (max-width: 399px) {
  .card { display: flex; flex-direction: column; }
}
```

**Container query units**: `cqw`, `cqh`, `cqi`, `cqb`, `cqmin`, `cqmax` — all
relative to the container, not the viewport.

#### CSS Grid

```css
.page-layout {
  display: grid;
  grid-template-columns: minmax(250px, 1fr) 3fr;
  grid-template-rows: auto 1fr auto;
  grid-template-areas:
    "header  header"
    "sidebar content"
    "footer  footer";
  gap: 1rem;
}

.header  { grid-area: header; }
.sidebar { grid-area: sidebar; }
.content { grid-area: content; }
.footer  { grid-area: footer; }
```

**Key properties**:
- `grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))` — responsive
  column grid that automatically fills rows
- `grid-auto-rows: minmax(100px, auto)` — implicit row sizing
- `place-items: center` — shorthand for `align-items` + `justify-items`
- `place-content: center` — shorthand for `align-content` + `justify-content`

**Subgrid** (inheriting parent grid tracks):
```css
.card { display: grid; grid-template-rows: subgrid; grid-row: span 3; }
```

#### Flexbox

```css
.navbar {
  display: flex;
  flex-direction: row;
  justify-content: space-between;  /* main axis */
  align-items: center;             /* cross axis */
  gap: 1rem;
  flex-wrap: wrap;
}
```

**Key shorthand**:
- `flex: 1 1 300px` = `flex-grow: 1; flex-shrink: 1; flex-basis: 300px`
- `flex: 1` = `flex-grow: 1; flex-shrink: 1; flex-basis: 0%`
- `flex: auto` = `flex-grow: 1; flex-shrink: 1; flex-basis: auto`

**Alignment cheat sheet**:

| Property | Axis | Values |
|----------|------|--------|
| `justify-content` | Main (horizontal in row) | `start`, `end`, `center`, `space-between`, `space-around`, `space-evenly` |
| `align-items` | Cross (vertical in row) | `stretch`, `start`, `end`, `center`, `baseline` |
| `align-self` | Cross (per-item) | Same as `align-items` |
| `align-content` | Cross (multi-line) | Same as `justify-content` |

#### Custom properties (CSS variables)

```css
:root {
  --color-primary: oklch(0.55 0.2 250);
  --color-primary-hover: oklch(from var(--color-primary) calc(l + 0.1) c h);
  --spacing-unit: 0.25rem;
  --spacing-md: calc(var(--spacing-unit) * 4);  /* 1rem */
}
```

**Fallback**: `color: var(--color-accent, var(--color-primary))` — tries
`--color-accent`, falls back to `--color-primary`.

**Invalid-at-computed-value-time**: If a custom property's value is invalid for
the property it's used in, the property takes its initial/inherited value.
This is different from an invalid declaration, which is ignored entirely.

#### Logical properties

Direction-relative replacements for physical properties:

| Physical | Logical (inline/block) | Meaning |
|----------|------------------------|---------|
| `margin-left` | `margin-inline-start` | Start of the inline (writing) direction |
| `margin-right` | `margin-inline-end` | End of the inline direction |
| `margin-top` | `margin-block-start` | Start of the block (stacking) direction |
| `margin-bottom` | `margin-block-end` | End of the block direction |
| `width` | `inline-size` | Size in the inline direction |
| `height` | `block-size` | Size in the block direction |
| `text-align: left` | `text-align: start` | Align to the start of the line |
| `border-left` | `border-inline-start` | Border on the inline-start side |

Shorthand: `margin-inline: 1rem 2rem` (start end), `padding-block: 0.5rem 1rem`
(start end), `margin: block-start inline-end block-end inline-start` (shorthand
order: logical).

#### Viewport units

| Unit | Meaning | Use case |
|------|---------|----------|
| `dvh` | Dynamic viewport height (changes when browser UI shows/hides) | Full-screen hero, mobile bottom nav |
| `svh` | Small viewport height (URL bar visible) | Above-the-fold content |
| `lvh` | Large viewport height (URL bar hidden) | Full-screen with overlay |
| `dvw` | Dynamic viewport width | Full-width containers |
| `svw` / `lvw` | Small / large viewport width | Edge cases |

Prefer `dvh` for interactive mobile layouts where the browser chrome
appears and disappears. Use `100svh` for initial paint to avoid layout shift.

#### Modern color

**oklch()** — perceptually uniform color space:
```css
--brand: oklch(0.55 0.2 250);    /* L=0.55 lightness, C=0.2 chroma, H=250° hue */
--brand-dark: oklch(0.35 0.15 250);
--brand-light: oklch(0.9 0.05 250);
```

Advantages over `hsl()`:
- Uniform lightness: `oklch(0.5 0.2 0deg)` and `oklch(0.5 0.2 180deg)` are
  perceptually equally light (unlike HSL, where yellow appears brighter)
- Wide gamut: reaches colors `hsl()` cannot express (P3 gamut)
- Accessible palette generation: hold L+C constant, rotate H for harmonious colors

**color-mix()** — blend colors in any color space:
```css
background: color-mix(in oklch, var(--brand) 20%, transparent);
text: color-mix(in srgb, red 50%, blue);
```

**Relative color syntax** (derive from existing color):
```css
--color-hover: oklch(from var(--color-primary) calc(l + 0.1) c h);
--color-alpha: oklch(from var(--color-primary) l c h / 0.5);
```

#### Nesting

```css
.card {
  padding: 1rem;

  & h2 { font-size: 1.25rem; }           /* descendant */
  &:hover { box-shadow: 0 2px 8px; }      /* pseudo-class */
  & .title { font-weight: bold; }          /* nested class (same as .card .title) */
  @media (width >= 768px) { padding: 2rem; }  /* nested media query */
}
```

**Rules**:
- Nesting selector (`&`) is required when the nested rule starts with a letter
  (otherwise it's ambiguous — could be a property)
- Relative selectors (`> .child`, `+ .sibling`) don't need `&`
- Conditionals (`@media`, `@container`, `@supports`) can nest freely

#### `:has()` selector (the "parent selector")

```css
.card:has(img) { grid-template-rows: auto 1fr; }          /* card with an image */
.container:has(:focus-visible) { outline: 2px solid blue; } /* any child focused */
.form:has(:invalid) .submit { opacity: 0.5; }              /* form has invalid input */
label:has(+ input:required)::after { content: " *"; }      /* label for required field */
```

`:has()` accepts a *relative selector list* and matches the parent if any
selector in the list matches. It is the first CSS selector that can "look up"
the DOM tree.

**Performance**: `:has()` is lazily evaluated by browsers — it recalculates
only when children change, not on every style recalc.

#### Scroll-driven animations

```css
@keyframes reveal {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.card {
  animation: reveal linear;
  animation-timeline: view();
  animation-range: entry 0% entry 100%;
}
```

**Timeline types**:
- `scroll()` — tied to scroll position of nearest scroll container
- `view()` — tied to element's visibility within its scrollport
- Named timeline: `scroll-timeline-name: --my-timeline` + `animation-timeline: --my-timeline`

**Animation range** (for `view()`): `entry`, `exit`, `contain`, `cover`, with
percentage offsets: `animation-range: entry 20% exit 80%`.

### 2.3 JavaScript (ES6+)

#### Core syntax

| Feature | Example | Purpose |
|---------|---------|---------|
| **Arrow functions** | `const add = (a, b) => a + b` | Lexical `this`, concise syntax |
| **Destructuring** | `const { name, age } = user` | Extract object/array values |
| **Spread** | `const merged = { ...a, ...b }` | Shallow merge objects/arrays |
| **Rest parameters** | `function fn(first, ...rest)` | Collect remaining arguments |
| **Optional chaining** | `user?.address?.street` | Safe deep property access |
| **Nullish coalescing** | `value ?? defaultValue` | Default only on `null`/`undefined` |
| **Template literals** | `` `Hello, ${name}!` `` | String interpolation |
| **Default parameters** | `function fn(x = 10)` | Parameter defaults |

**Async patterns**:

```javascript
// async/await
async function fetchUser(id) {
  const response = await fetch(`/api/users/${id}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// Promise.allSettled — never rejects, returns all outcomes
const results = await Promise.allSettled([
  fetch('/api/a'), fetch('/api/b'), fetch('/api/c')
]);

// Promise.any — first fulfilled, or AggregateError if all reject
const fastest = await Promise.any([fetchFromCache(), fetchFromServer()]);

// Top-level await (ESM modules)
const config = await fetch('/config.json').then(r => r.json());
```

#### Fetch API

```javascript
const response = await fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
  body: JSON.stringify({ key: 'value' }),
  signal: AbortSignal.timeout(5000),    // 5s timeout
  credentials: 'same-origin',
});

// Streaming response
const decoder = new TextDecoder();
for await (const chunk of response.body) {
  console.log(decoder.decode(chunk, { stream: true }));
}
```

**Response helpers**: `response.json()`, `response.text()`, `response.blob()`,
`response.formData()`, `response.arrayBuffer()`.

#### Modules

```javascript
// Named exports
export const PI = 3.14159;
export function circleArea(r) { return PI * r * r; }

// Default export
export default class User { /* ... */ }

// Import
import User, { PI, circleArea } from './math.js';
import * as math from './math.js';
import { circleArea as area } from './math.js';
```

**Dynamic import** (code splitting):
```javascript
const module = await import('./heavy-chart-lib.js');
module.renderChart(data);
```

#### Event delegation

```javascript
// Single listener on parent handles all children (even dynamically added ones)
document.querySelector('.list').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;  // not a button we care about
  const action = button.dataset.action;
  handleAction(action);
});
```

**Benefits**: One listener vs N, works for dynamically added children,
natural in frameworks (React synthetic events, HTMX after-swap).

#### Custom events

```javascript
// Dispatch
element.dispatchEvent(new CustomEvent('item-selected', {
  detail: { id: 42, name: 'Widget' },
  bubbles: true,
  composed: true,  // crosses shadow DOM boundary
}));

// Listen
document.addEventListener('item-selected', (event) => {
  console.log(event.detail.id);
});
```

#### IntersectionObserver

```javascript
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);  // one-shot
    }
  }
}, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
```

**Use cases**: lazy loading images (`loading="lazy"` native attribute preferred),
scroll-triggered animations, infinite scroll, ad viewability tracking.

#### ResizeObserver

```javascript
const observer = new ResizeObserver((entries) => {
  for (const entry of entries) {
    const { width, height } = entry.contentRect;
    console.log(`Element resized to ${width}x${height}`);
  }
});
observer.observe(document.querySelector('.chart-container'));
```

**Use cases**: responsive chart resizing, container query polyfills,
dynamic layout adjustments. Prefer CSS `@container` queries when possible;
use ResizeObserver when you need JavaScript-level reactions.

### 2.4 Z-Axis Mastery

#### Stacking context creation

Not all `z-index` values are equal — they resolve within their *stacking context*.
A new stacking context is created by:

| Trigger | Example |
|---------|---------|
| `position: relative | absolute | fixed | sticky` + `z-index: <integer>` | `.modal { position: fixed; z-index: 400; }` |
| `opacity < 1` | `.faded { opacity: 0.5; }` |
| `transform` (any value) | `.rotated { transform: rotate(5deg); }` |
| `filter` (any value) | `.blurred { filter: blur(5px); }` |
| `will-change: transform | filter | opacity` | `.animated { will-change: transform; }` |
| `isolation: isolate` | `.group { isolation: isolate; }` — explicit, no side effects |

**Critical rule**: A child with `z-index: 9999` inside a stacking context with
`sibling-z-index: 100` can NEVER appear above that sibling. The child's
`z-index` is scoped to its parent's stacking context.

**`isolation: isolate`** is the explicit, zero-side-effect way to create a
stacking context — use it when you need z-index containment without the visual
side effects of `opacity`/`transform`/`filter`.

#### Paint order (back to front)

1. Background and borders of the stacking context root
2. Stacking contexts with negative `z-index`
3. Non-positioned, non-floated block-level elements (in-flow)
4. Non-positioned floats
5. In-flow, non-positioned inline elements
6. Stacking contexts with `z-index: 0` or `z-index: auto`
7. Stacking contexts with positive `z-index`

This is why `z-index: -1` can push an element *behind* its stacking context's
background — it lands in paint layer 2, before the root's background in layer 1.

#### Compositing (GPU layer promotion)

Browsers promote elements to their own compositor layer when they can be
rendered independently. This enables smooth 60fps animations.

**Triggers**:
- `transform: translateZ(0)` (classic hack)
- `will-change: transform` (modern, explicit)
- `contain: layout style paint` (isolation hint)
- 3D transforms (`rotateX`, `perspective`)
- `<video>`, `<canvas>`, `<iframe>` elements

**Rules**:
- Promote sparingly — each layer consumes GPU memory
- Use `will-change` immediately before animation, remove it after
- `contain: layout` prevents the element from affecting parent layout, enabling
  independent re-layout

#### Z-index scale (convention)

| z-index | Layer | Examples |
|---------|-------|----------|
| 0 | Base content | Body text, images, cards |
| 100 | Dropdowns | Select menus, autocomplete, date pickers |
| 200 | Sticky elements | Sticky headers, sticky table columns |
| 300 | Overlays / drawers | Side drawers, slide-out panels |
| 400 | Modals | Dialog boxes, lightboxes |
| 500 | Popovers | `popover` attribute elements, tooltip-like overlays |
| 600 | Toast notifications | Snackbars, alerts |
| 700 | Tooltips | Always-on-top hover tooltips |

This scale is a *convention*, not a browser spec. Every project should define
its own z-index scale as CSS custom properties:

```css
:root {
  --z-base: 0;
  --z-dropdown: 100;
  --z-sticky: 200;
  --z-overlay: 300;
  --z-modal: 400;
  --z-popover: 500;
  --z-toast: 600;
  --z-tooltip: 700;
}
```

## Section 3: Role Reference

### 3.1 `html_css_core` — HTML5 Validation and CSS Checking

**FQCN**: `general_ludd.web.html_css_core`

**Purpose**: Validate HTML5 semantic structure, check CSS syntax, audit ARIA
landmarks, generate responsive boilerplate, and verify media query breakpoint
coverage.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `html_file` | path | (required) | HTML file to validate |
| `css_file` | path | `""` | CSS file to check (optional) |
| `operation` | string | `validate_html` | One of `validate_html`, `check_css`, `responsive_boilerplate`, `semantic_audit` |
| `validate_w3c` | bool | `false` | Send to W3C Nu validator (requires network) |
| `responsive_check` | bool | `false` | Parse media queries and verify breakpoint coverage |
| `target_breakpoints` | list | `[768, 1024, 1440]` | Breakpoints (px) to verify |
| `artifact_dir` | path | `/tmp/gludd-web/html_css_core` | Output directory for artifact JSON |

**Operations**:

| Operation | Description | Artifact output |
|-----------|-------------|-----------------|
| `validate_html` | Parse HTML, check well-formedness, flag unclosed tags, detect missing alt text | `errors[]`, `warnings[]`, `valid` flag |
| `check_css` | Validate CSS syntax, detect unused selectors, flag `!important` overuse | `css_valid` flag, property report |
| `responsive_boilerplate` | Generate HTML5 boilerplate with viewport meta, responsive CSS grid scaffold | Boilerplate file written to artifact dir |
| `semantic_audit` | Count ARIA landmarks, detect missing `main`, flag nested banners/footers | `semantic_tree` dict, landmark map |

**Usage example** — validate an HTML page and check its CSS:

```yaml
- name: Validate HTML5 structure and audit semantics
  include_role:
    name: general_ludd.web.html_css_core
  vars:
    html_file: /src/index.html
    css_file: /src/styles/main.css
    operation: validate_html
    validate_w3c: true
```

**Usage example** — semantic landmark audit:

```yaml
- name: Audit ARIA landmarks
  include_role:
    name: general_ludd.web.html_css_core
  vars:
    html_file: /src/index.html
    operation: semantic_audit
```

**Output artifact** (`html_css_core.json`):
```json
{
  "role": "html_css_core",
  "status": "ok",
  "operation": "validate_html",
  "valid": true,
  "css_valid": true,
  "responsive_pass": false,
  "verdict": "validate_html_complete",
  "errors": [],
  "warnings": ["Missing alt text on img: /src/assets/hero.png"],
  "semantic_tree": {}
}
```

### 3.2 `framework_integration` — React, Next.js, HTMX, GraphQL, REST

**FQCN**: `general_ludd.web.framework_integration`

**Purpose**: Scaffold, generate, test, and analyze applications across five
framework targets: React, Next.js, HTMX, GraphQL, and REST APIs.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `framework_integration_framework` | string | `react` | `react`, `nextjs`, `htmx`, `graphql`, or `rest` |
| `framework_integration_operation` | string | `scaffold` | `scaffold`, `generate`, `test`, or `analyze` |
| `framework_integration_component_name` | string | `""` | Component name for React/Next.js scaffold |
| `framework_integration_endpoint_url` | string | `""` | REST endpoint URL for rest operations |
| `framework_integration_graphql_schema_file` | string | `""` | `.graphql` schema file for GraphQL operations |
| `framework_integration_output_dir` | string | `/tmp/gludd-web/framework_integration` | Output directory |

**Operations per framework**:

| Framework | `scaffold` | `generate` | `test` | `analyze` |
|-----------|-----------|------------|--------|-----------|
| `react` | Create component file with boilerplate | Generate CRUD component from schema | Run Jest + React Testing Library | Audit component tree, detect anti-patterns |
| `nextjs` | Create `pages/` or `app/` route | Generate API route handler | Run Playwright E2E | Bundle analysis, ISR/SSG audit |
| `htmx` | Create HTML page with hx- attributes | Generate multi-step form with hx-post/hx-swap | Verify hx-trigger behavior | Attribute coverage audit |
| `graphql` | Create schema file with Query/Mutation | Generate resolvers from schema | Run schema validation | N+1 detection, depth limit check |
| `rest` | Create route file with Express/Fastify boilerplate | Generate OpenAPI spec from route annotations | Run Supertest integration tests | Endpoint coverage audit |

**Usage example** — scaffold a React component:

```yaml
- name: Scaffold React dashboard widget
  include_role:
    name: general_ludd.web.framework_integration
  vars:
    framework_integration_framework: react
    framework_integration_operation: scaffold
    framework_integration_component_name: DashboardWidget
```

**Usage example** — analyze a GraphQL schema:

```yaml
- name: Audit GraphQL schema for N+1 risks
  include_role:
    name: general_ludd.web.framework_integration
  vars:
    framework_integration_framework: graphql
    framework_integration_operation: analyze
    framework_integration_graphql_schema_file: /src/graphql/schema.graphql
```

**Output artifact** (`framework_integration.json`):
```json
{
  "role": "framework_integration",
  "status": "ok",
  "framework": "react",
  "operation": "scaffold",
  "component_name": "DashboardWidget",
  "verdict": "scaffold_complete",
  "output": "Generated /tmp/gludd-web/framework_integration/DashboardWidget.tsx"
}
```

### 3.3 `design_research` — Design Token Extraction

**FQCN**: `general_ludd.web.design_research`

**Purpose**: Fetch a target URL, extract CSS design tokens (colors, fonts,
spacing, breakpoints), detect the CSS framework in use, and capture layout
structure for replication.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_url` | string | (required) | URL to analyze |
| `capture_screenshot` | bool | `true` | Take full-page screenshot for reference |
| `extract_tokens` | bool | `true` | Parse CSS for design tokens |
| `detect_framework` | bool | `true` | Attempt to identify CSS framework |
| `include_inline_styles` | bool | `false` | Also parse inline `style` attributes |
| `max_depth` | int | `3` | Max link-following depth for multi-page analysis |
| `artifact_dir` | path | `/tmp/gludd-web/design_research` | Output directory |

**What is extracted**:

| Token Category | Specifics |
|----------------|-----------|
| **Colors** | Background, text, border, accent colors; semantic colors (error, success, warning) |
| **Typography** | Font families, font sizes (heading scale, body), line heights, font weights, letter spacing |
| **Spacing** | Padding/margin patterns, gap values, grid gutters |
| **Breakpoints** | Media query breakpoints, container query thresholds |
| **Shadows** | `box-shadow` values by elevation level |
| **Border radius** | Rounding scales on cards, buttons, inputs |
| **Framework** | Bootstrap, Tailwind, Material UI, Bulma, Foundation, or custom |

**Usage example**:

```yaml
- name: Extract design tokens from target site
  include_role:
    name: general_ludd.web.design_research
  vars:
    target_url: https://example.com
    capture_screenshot: true
    extract_tokens: true
    detect_framework: true
```

**Output artifact** (`design_tokens.json`):
```json
{
  "source_url": "https://example.com",
  "detected_framework": null,
  "tokens": {
    "colors": {
      "primary": "#1a73e8",
      "primary_hover": "#1557b0",
      "background": "#ffffff",
      "text": "#202124",
      "text_secondary": "#5f6368",
      "border": "#dadce0",
      "error": "#d93025",
      "success": "#188038"
    },
    "typography": {
      "font_family_base": "\"Google Sans\", Roboto, Arial, sans-serif",
      "font_size_base": "16px",
      "font_scale": [12, 14, 16, 20, 24, 32, 48],
      "line_height_base": 1.5,
      "font_weight_headings": 500
    },
    "spacing": {
      "unit": "8px",
      "scale": [4, 8, 12, 16, 24, 32, 48, 64]
    },
    "breakpoints": [600, 960, 1280, 1920],
    "border_radius": {
      "small": "4px",
      "medium": "8px",
      "large": "16px",
      "pill": "9999px"
    }
  },
  "screenshot_path": "/tmp/gludd-web/design_research/screenshot.png"
}
```

### 3.4 `javascript_debug` — JavaScript Linting and Error Analysis

**FQCN**: `general_ludd.web.javascript_debug`

**Purpose**: Check JavaScript syntax, lint with ESLint, classify error patterns
from production logs, verify source maps, and detect unhandled Promise rejections.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `js_file` | path | `""` | JavaScript file to lint/check |
| `js_files_glob` | string | `""` | Glob pattern for multiple files |
| `error_log` | path | `""` | Production error log file to analyze |
| `check_syntax` | bool | `true` | Validate JS syntax (no runtime) |
| `run_eslint` | bool | `false` | Run ESLint (requires Node.js) |
| `analyze_errors` | bool | `false` | Parse and classify error patterns from `error_log` |
| `verify_source_maps` | bool | `false` | Check `.map` file presence and validity |
| `artifact_dir` | path | `/tmp/gludd-web/javascript_debug` | Output directory |

**Error classification categories**:

| Category | Pattern | Example |
|----------|---------|---------|
| `type_error` | `TypeError: Cannot read property 'X' of undefined` | Missing null check on API response |
| `reference_error` | `ReferenceError: X is not defined` | Missing import or undeclared variable |
| `syntax_error` | `SyntaxError: Unexpected token` | Malformed JS, missing closing brace |
| `network_error` | `TypeError: Failed to fetch` | API timeout or CORS block |
| `unhandled_rejection` | `UnhandledPromiseRejectionWarning` | `.catch()` missing on async call |
| `dom_error` | `NotFoundError: Failed to execute 'removeChild'` | Element removed between query and action |
| `security_error` | `SecurityError: The operation is insecure` | Cross-origin iframe access, sandboxed eval |

**Usage example** — analyze production error logs:

```yaml
- name: Classify production JS errors
  include_role:
    name: general_ludd.web.javascript_debug
  vars:
    error_log: /var/log/frontend/errors.log
    analyze_errors: true
    verify_source_maps: true
```

### 3.5 `accessibility_audit` — WCAG 2.1 AA Compliance

**FQCN**: `general_ludd.web.accessibility_audit`

**Purpose**: Run automated accessibility checks against a URL or local HTML
files, grade against WCAG 2.1 AA success criteria, and produce a prioritized
remediation report.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_url` | string | `""` | URL to audit (live site) |
| `html_files` | list | `[]` | Local HTML files to audit |
| `run_axe_core` | bool | `true` | Run axe-core automated checks |
| `run_lighthouse` | bool | `false` | Run Lighthouse accessibility audit |
| `lighthouse_threshold` | int | `90` | Minimum acceptable Lighthouse a11y score (0-100) |
| `check_keyboard` | bool | `false` | Verify focus order and keyboard operability |
| `wcag_level` | string | `AA` | Target level: `A`, `AA`, or `AAA` |
| `artifact_dir` | path | `/tmp/gludd-web/accessibility_audit` | Output directory |

**Violation severity**:

| Severity | Meaning | Example |
|----------|---------|---------|
| `critical` | Blocks users from accessing content | Image without alt text serving as primary content |
| `serious` | Severely impairs usability for some users | Form input without label |
| `moderate` | Hinders but does not block access | Low color contrast on secondary text |
| `minor` | WCAG technical violation with low user impact | Redundant title attribute |

**Usage example**:

```yaml
- name: Audit accessibility of deployed site
  include_role:
    name: general_ludd.web.accessibility_audit
  vars:
    target_url: https://staging.example.com
    run_axe_core: true
    run_lighthouse: true
    check_keyboard: true
    wcag_level: AA
```

**Output artifact** (`accessibility_audit.json`):
```json
{
  "role": "accessibility_audit",
  "status": "ok",
  "target_url": "https://staging.example.com",
  "axe_violations": {
    "critical": 0,
    "serious": 2,
    "moderate": 5,
    "minor": 3
  },
  "lighthouse_score": 87,
  "lighthouse_passes_threshold": false,
  "keyboard_operable": true,
  "verdict": "needs_remediation"
}
```

### 3.6 `component_scaffold` — Token-Driven Component Generation

**FQCN**: `general_ludd.web.component_scaffold`

**Purpose**: Generate HTML/CSS components (buttons, cards, nav bars, forms,
modals) from a design token specification. Supports React with CSS Modules
and vanilla HTML/CSS output.

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tokens_file` | path | (required) | Design tokens JSON (as produced by `design_research`) |
| `component_type` | string | (required) | One of `button`, `card`, `navbar`, `form`, `modal`, `hero`, `footer`, `table` |
| `framework` | string | `html` | Target output: `html` or `react` |
| `include_variants` | bool | `true` | Generate all token-appropriate variants (size, color, state) |
| `css_approach` | string | `custom-properties` | `custom-properties`, `css-modules`, or `tailwind` |
| `output_dir` | string | `/tmp/gludd-web/component_scaffold` | Output directory |

**Components available**:

| Component | Variants Generated | Token Dependencies |
|-----------|--------------------|--------------------|
| `button` | `primary`, `secondary`, `outline`, `ghost`, `danger`; sizes: `sm`, `md`, `lg`; states: `hover`, `focus`, `active`, `disabled` | `colors`, `typography`, `spacing`, `border_radius` |
| `card` | `default`, `elevated`, `outlined`, `interactive`; with/without image slot, header, footer | `colors`, `spacing`, `border_radius`, `shadows` |
| `navbar` | `horizontal`, `vertical`; `fixed`, `sticky`; with/without search, dropdown | `colors`, `typography`, `spacing` |
| `form` | `default`, `floating-label`, `inline`; input types: text, email, password, select, textarea, checkbox, radio | `colors`, `typography`, `spacing`, `border_radius` |
| `modal` | `default`, `sheet` (bottom sheet), `fullscreen`; with/without backdrop, close button | `colors`, `spacing`, `border_radius`, `shadows`, `z-index` |
| `hero` | `centered`, `split` (text + image), `fullscreen`; with/without CTA buttons | `colors`, `typography`, `spacing` |
| `footer` | `simple`, `multi-column`, `stacked`; with/without social links, newsletter form | `colors`, `typography`, `spacing` |
| `table` | `default`, `striped`, `bordered`, `compact`; sortable headers, pagination | `colors`, `typography`, `spacing`, `border_radius` |

**Usage example** — generate button set from extracted tokens:

```yaml
- name: Extract design tokens
  include_role:
    name: general_ludd.web.design_research
  vars:
    target_url: https://example.com
  register: _tokens_result

- name: Generate React button components from tokens
  include_role:
    name: general_ludd.web.component_scaffold
  vars:
    tokens_file: "{{ _tokens_result.artifact_dir }}/design_tokens.json"
    component_type: button
    framework: react
    include_variants: true
    css_approach: css-modules
```

## Section 4: Design Research Workflow

The canonical workflow for reverse-engineering a website's design system and
replicating it with valid, accessible markup:

### Step 1: Research — Extract design values from target

```yaml
- name: Research target design system
  include_role:
    name: general_ludd.web.design_research
  vars:
    target_url: "{{ target_url }}"
    capture_screenshot: true
    extract_tokens: true
    detect_framework: true
  register: research
```

**What happens**: The role fetches the HTML, parses all linked and inline CSS,
and extracts the raw design values — hex codes, font stacks, pixel values,
media query thresholds. A full-page screenshot is captured for visual reference.

### Step 2: Tokenize — Normalize raw values into design tokens

The `design_research` role normalizes extracted values into a structured token
spec. Raw `#1a73e8` becomes `primary`; `16px` / `20px` / `24px` becomes a
typographic scale; `8px` / `16px` / `24px` becomes a spacing scale. The output
is a `design_tokens.json` file consumable by downstream roles.

### Step 3: Scaffold — Generate HTML/CSS with token-driven styles

```yaml
- name: Scaffold base layout
  include_role:
    name: general_ludd.web.html_css_core
  vars:
    operation: responsive_boilerplate
  register: boilerplate
```

Produce a semantic HTML5 shell — `header`, `main`, `footer` — with a responsive
CSS Grid layout, viewport meta tag, and CSS custom properties populated from
the design tokens.

### Step 4: Build — Add token-driven components

```yaml
- name: Generate primary button component
  include_role:
    name: general_ludd.web.component_scaffold
  vars:
    tokens_file: "{{ research.artifact_dir }}/design_tokens.json"
    component_type: button
    framework: react
    css_approach: css-modules

- name: Generate card component
  include_role:
    name: general_ludd.web.component_scaffold
  vars:
    tokens_file: "{{ research.artifact_dir }}/design_tokens.json"
    component_type: card
    framework: react
    css_approach: css-modules
```

Each component is generated with variants (size, color, state), proper ARIA
attributes, and token-driven CSS. Components share the same token source,
guaranteeing visual consistency.

### Step 5: Validate — Accessibility audit and usability check

```yaml
- name: Validate HTML semantics
  include_role:
    name: general_ludd.web.html_css_core
  vars:
    html_file: /output/index.html
    css_file: /output/styles.css
    operation: validate_html
    validate_w3c: true

- name: Audit accessibility
  include_role:
    name: general_ludd.web.accessibility_audit
  vars:
    target_url: "{{ staging_url }}"
    run_axe_core: true
    run_lighthouse: true
    check_keyboard: true
    wcag_level: AA
```

The HTML is validated for well-formedness and semantics. The deployed result
is tested with axe-core and Lighthouse. Any violations return a prioritized
remediation list.

### Step 6: Iterate — Compare against source, refine

The design research role can be re-run to diff the generated site against the
source — has the color palette drifted? Are spacing values consistent?
Re-extract tokens and compare the JSON diffs.

```yaml
- name: Re-extract tokens from generated site
  include_role:
    name: general_ludd.web.design_research
  vars:
    target_url: "{{ staging_url }}"
  register: generated_tokens

- name: Diff source vs generated tokens
  ansible.builtin.debug:
    msg: "Token drift detected. Review {{ generated_tokens.artifact_dir }}/design_tokens.json"
```

## Section 5: Framework Quick Reference

### 5.1 React — Component Patterns

| Pattern | When to use | Signature |
|---------|------------|-----------|
| **Controlled component** | Form inputs where React owns the value | `<input value={val} onChange={e => setVal(e.target.value)} />` |
| **Uncontrolled component** | Simple forms, file inputs, integrating with non-React DOM libs | `<input defaultValue="hello" ref={inputRef} />` |
| **Compound component** | Families of components that share implicit state (tabs, accordion, select) | `<Tabs><TabList><Tab>...</Tab></TabList><TabPanels>...</TabPanels></Tabs>` |
| **Render props** | Sharing behavior between components without HOCs | `<Mouse render={({x, y}) => <p>{x}, {y}</p>} />` — largely superseded by hooks |
| **Higher-Order Component** | Cross-cutting concerns (auth, theming, logging) wrapping a component | `const ProtectedPage = withAuth(Page)` |
| **Custom hook** | Extracting reusable stateful logic | `function useDebounce(value, delay) { ... }` |
| **Context provider** | Global-ish state: theme, auth, locale, feature flags | `<ThemeProvider value={theme}><App /></ThemeProvider>` |

**Rules**:
- Prefer hooks over HOCs and render props for new code
- Compound components use `React.Children.map` + `cloneElement` or Context
- Custom hooks should start with `use` (`useMediaQuery`, `useLocalStorage`)
- Avoid prop drilling beyond 2 levels — use Context or composition

### 5.2 Next.js — Routing and Data Fetching

**App Router** (v13+, recommended for new projects):

| Pattern | File | Purpose |
|---------|------|---------|
| **Page route** | `app/dashboard/page.tsx` | Renders at `/dashboard` |
| **Dynamic route** | `app/users/[id]/page.tsx` | Renders at `/users/42` |
| **Layout** | `app/dashboard/layout.tsx` | Persistent shell across `dashboard/*` routes |
| **Loading UI** | `app/dashboard/loading.tsx` | Shown during page navigation (React Suspense boundary) |
| **Error boundary** | `app/dashboard/error.tsx` | Catches errors in route segment |
| **Route handler** | `app/api/users/route.ts` | `GET`, `POST`, `PUT`, `DELETE` handlers |
| **Middleware** | `middleware.ts` (root) | Runs before every request (auth, redirects, A/B) |

**Data fetching strategies**:

| Strategy | How | When |
|----------|-----|------|
| **Static (SSG)** | `fetch(..., { cache: 'force-cache' })` or no `fetch` options | Blog posts, marketing pages, docs |
| **Dynamic (SSR)** | `fetch(..., { cache: 'no-store' })` or `cookies()`/`headers()` call | Dashboards, user-specific pages |
| **Revalidation (ISR)** | `fetch(..., { next: { revalidate: 3600 } })` | Product pages, CMS content |
| **Client-side** | `useEffect` + `fetch`, SWR, React Query | Real-time data, user interactions after hydration |
| **Static params** | `generateStaticParams()` | Pre-render known dynamic routes at build time |

**Deployment targets**:
- **Vercel**: zero-config, Edge Functions, ISR
- **Node.js server**: `next start`, any Node host
- **Static export**: `output: 'export'` in `next.config.js` — no SSR, no ISR
- **Docker**: official `node:20-alpine` base, multi-stage build

### 5.3 HTMX — Attribute Reference

HTMX extends HTML with attributes for AJAX, CSS transitions, WebSockets, and
server-sent events — no JavaScript required.

| Attribute | Purpose | Example |
|-----------|---------|---------|
| `hx-get` | Issue GET to URL | `hx-get="/api/users"` |
| `hx-post` | Issue POST to URL | `hx-post="/api/users"` |
| `hx-put` / `hx-patch` / `hx-delete` | Issue PUT/PATCH/DELETE | `hx-delete="/api/users/42"` |
| `hx-trigger` | Event that triggers the request | `hx-trigger="click"`, `hx-trigger="keyup changed delay:500ms"`, `hx-trigger="revealed"` |
| `hx-target` | CSS selector for response insertion target | `hx-target="#result"`, `hx-target="next .panel"` |
| `hx-swap` | How to insert response content | `innerHTML` (default), `outerHTML`, `beforebegin`, `afterbegin`, `beforeend`, `afterend`, `delete`, `none` |
| `hx-swap-oob` | Out-of-band swap (multiple targets from one response) | `hx-swap-oob="true"` on response elements |
| `hx-select` | CSS selector to extract from response | `hx-select="#main-content"` |
| `hx-indicator` | Element to show during request | `hx-indicator="#spinner"` |
| `hx-push-url` | Push URL to browser history | `hx-push-url="true"` |
| `hx-boost` | Progressively enhance anchors/forms | `hx-boost="true"` |
| `hx-confirm` | Confirmation dialog before request | `hx-confirm="Are you sure?"` |
| `hx-vals` | Add extra JSON values to request | `hx-vals='{"view":"list"}'` |

**Event system**:

| Event | Fires when | Usage |
|-------|-----------|-------|
| `htmx:beforeRequest` | Before request is issued | Modify request config, show loading |
| `htmx:afterRequest` | After response received | Logging, analytics |
| `htmx:beforeSwap` | Before content is swapped | Modify response content, cancel swap |
| `htmx:afterSwap` | After content is swapped | Initialize JS widgets, focus management |
| `htmx:responseError` | Server returns 4xx/5xx | Show error UI, retry logic |
| `htmx:beforeHistorySave` | Before URL is pushed | Clean up client state |

**Extensions**: `json-enc` (JSON request body), `head-support` (`<head>` merging),
`ws` (WebSocket), `sse` (Server-Sent Events), `preload` (speculative loading),
`path-deps` (path-dependent polling).

### 5.4 GraphQL — Operation Types and Schema Design

**Operation types**:

```graphql
# Query — read data
query GetUser($id: ID!) {
  user(id: $id) {
    name
    posts(first: 10) {
      title
    }
  }
}

# Mutation — write data
mutation CreatePost($input: PostInput!) {
  createPost(input: $input) {
    post { id title }
    errors { field message }
  }
}

# Subscription — real-time updates
subscription OnNewMessage($roomId: ID!) {
  messageAdded(roomId: $roomId) {
    id text sender { name }
  }
}
```

**Apollo Client patterns**:
```javascript
const { loading, error, data } = useQuery(GET_USER, {
  variables: { id: userId },
  skip: !userId,
  fetchPolicy: 'cache-and-network',
});

const [createPost, { loading }] = useMutation(CREATE_POST, {
  update(cache, { data: { createPost } }) {
    cache.modify({ fields: { posts(existing = []) {
      return [...existing, createPost.post];
    }}});
  },
});
```

**Schema design tips**:
- Use `input` types (not `type`) for mutation arguments — they're value types,
  not identity types
- Prefer field-level error reporting: `{ post, errors { field message } }` over
  throwing errors from resolvers
- Use `@deprecated(reason: "...")` to communicate API changes
- Implement pagination with the Relay Cursor Connections spec: `edges`, `node`,
  `pageInfo { hasNextPage, endCursor }`
- Set a `maxDepth` and query cost limit at the server to prevent DoS
- Use `DataLoader` (Facebook's batching/caching library) to avoid the N+1 problem

## Section 6: Accessibility Checklist — WCAG 2.1 AA

This checklist covers all WCAG 2.1 success criteria at Level AA. The
`accessibility_audit` role checks the automatable criteria; criteria
marked **Manual** require human judgment.

### 6.1 Perceivable — Information must be presentable to users

| # | Criterion | Level | Auto | Check |
|---|-----------|-------|------|-------|
| 1.1.1 | **Non-text Content**: All images, icons, and non-text content have text alternatives | A | Partial | `img` has `alt`; decorative images use `alt=""`; complex images have `longdesc` or text equivalent |
| 1.2.1 | **Audio-only / Video-only**: Prerecorded audio/video has text alternative | A | No | Manual: transcript for audio, descriptive text for video-only |
| 1.2.2 | **Captions**: Prerecorded video has synchronized captions | A | No | Manual: VTT captions present in `<track>` |
| 1.2.3 | **Audio Description or Media Alternative**: Video has audio description or text alternative | A | No | Manual: described video or full text transcript |
| 1.2.4 | **Captions (Live)**: Live video has captions | AA | No | Manual: live captioning service integrated |
| 1.2.5 | **Audio Description (Prerecorded)**: Video has audio description | AA | No | Manual: described video track |
| 1.3.1 | **Info and Relationships**: Structure conveyed through markup (headings, lists, tables) | A | Yes | Heading hierarchy (`h1`→`h6` sequential), `<ul>`/`<ol>` for lists, `<table>` with `<th scope>` |
| 1.3.2 | **Meaningful Sequence**: Content order in DOM matches visual reading order | A | Yes | DOM order matches visual order; no CSS `order` abuse that breaks reading sequence |
| 1.3.3 | **Sensory Characteristics**: Instructions don't rely solely on shape, color, size, or location | A | Yes | "Click the red button" → "Click the Submit button" |
| 1.3.4 | **Orientation**: Content works in both portrait and landscape | AA | Yes | No `orientation: portrait` lock without essential purpose |
| 1.3.5 | **Identify Input Purpose**: Input fields have `autocomplete` attributes | AA | Yes | `autocomplete="email"`, `autocomplete="tel"`, `autocomplete="cc-number"` |
| 1.4.1 | **Use of Color**: Color is not the only means of conveying information | A | Yes | Error state uses icon + text, not just red; links are underlined, not just colored |
| 1.4.2 | **Audio Control**: Auto-playing audio can be paused/stopped | A | Yes | No `<audio autoplay>` without visible controls |
| 1.4.3 | **Contrast (Minimum)**: Text has 4.5:1 contrast ratio (3:1 for large text) | AA | Yes | axe-core measures this; `oklch()` palette generation ensures compliant ratios |
| 1.4.4 | **Resize Text**: Text can be resized to 200% without loss of content | AA | Yes | No fixed-height containers that clip text; use `rem`/`em` for font sizes |
| 1.4.5 | **Images of Text**: Use text instead of images of text (except logos) | AA | Yes | No `img` of text where CSS-styled text would work |
| 1.4.10 | **Reflow**: Content reflows to a single column at 320px (no horizontal scroll at 1280px @ 400% zoom) | AA | Yes | Viewport meta with `width=device-width`; no `min-width` on `<body>` |
| 1.4.11 | **Non-text Contrast**: UI components and graphics have 3:1 contrast | AA | Yes | Button borders, focus rings, chart elements all ≥3:1 |
| 1.4.12 | **Text Spacing**: No loss of content when user overrides: line-height 1.5, letter-spacing 0.12em, word-spacing 0.16em, paragraph-spacing 2em | AA | Yes | Use relative units; don't set `height` on text containers |
| 1.4.13 | **Content on Hover or Focus**: Dismissable, hoverable, persistent tooltips/additional content | AA | Yes | Tooltip is dismissable with Escape; pointer can move to tooltip without it disappearing |

### 6.2 Operable — Interface must be operable

| # | Criterion | Level | Auto | Check |
|---|-----------|-------|------|-------|
| 2.1.1 | **Keyboard**: All functionality operable via keyboard | A | Partial | Tab through every interactive element; no `onmouseover`-only actions |
| 2.1.2 | **No Keyboard Trap**: Focus can always leave a component via keyboard | A | Yes | Tab past a modal → focus trapped inside; Escape closes modal, returns focus |
| 2.1.4 | **Character Key Shortcuts**: Single-character shortcuts can be remapped or turned off | AA | No | Manual: if present, must be configurable |
| 2.2.1 | **Timing Adjustable**: Time limits can be turned off, adjusted, or extended | A | No | Manual: session timeouts, quiz timers |
| 2.2.2 | **Pause, Stop, Hide**: Auto-updating/moving/scrolling content can be paused | A | Yes | Carousels have pause button; auto-refreshing feeds have stop control |
| 2.3.1 | **Three Flashes or Below**: Nothing flashes more than 3 times/second | A | Yes | No strobe-like animations; `prefers-reduced-motion` respected |
| 2.4.1 | **Bypass Blocks**: Skip-to-content link at top of page | A | Yes | `&lt;a href="#main"&gt;Skip to main content&lt;/a&gt;` as first focusable element |
| 2.4.2 | **Page Titled**: Every page has a descriptive `<title>` | A | Yes | `<title>` is unique and describes page purpose |
| 2.4.3 | **Focus Order**: Focus moves in a meaningful sequence | A | Yes | Tab order matches visual order; no `tabindex` values > 0 |
| 2.4.4 | **Link Purpose (In Context)**: Link text (or link + context) describes destination | A | Yes | No "click here" / "read more" without surrounding context |
| 2.4.5 | **Multiple Ways**: At least two ways to find a page (nav, search, sitemap) | AA | No | Manual: search + nav menu + sitemap |
| 2.4.6 | **Headings and Labels**: Headings and labels describe topic or purpose | AA | Partial | `<h2>Billing History</h2>` not `<h2>Section 3</h2>` |
| 2.4.7 | **Focus Visible**: Focus indicator is clearly visible | AA | Yes | `:focus-visible` has 3:1 contrast outline; never `outline: none` without replacement |
| 2.5.1 | **Pointer Gestures**: Multi-point gestures have single-point alternatives | A | Yes | Pinch-to-zoom has +/- buttons; swipe has arrow buttons |
| 2.5.2 | **Pointer Cancellation**: Down-event alone doesn't trigger action | A | Yes | Use `click` (fires on up-event), not `mousedown`/`touchstart` |
| 2.5.3 | **Label in Name**: Visible label text is part of the accessible name | A | Yes | `aria-label` or `aria-labelledby` includes the visible text |
| 2.5.4 | **Motion Actuation**: Actions triggered by device motion have UI alternatives | A | No | Manual: shake-to-undo has an on-screen button |

### 6.3 Understandable — Information and operation must be understandable

| # | Criterion | Level | Auto | Check |
|---|-----------|-------|------|-------|
| 3.1.1 | **Language of Page**: `<html>` has a `lang` attribute | A | Yes | `<html lang="en">` |
| 3.1.2 | **Language of Parts**: Language changes inline are marked | AA | Yes | `<blockquote lang="fr">...</blockquote>` |
| 3.2.1 | **On Focus**: Focus does not trigger a context change | A | Yes | Focusing an input does not submit a form or navigate |
| 3.2.2 | **On Input**: Changing a form control setting does not auto-trigger context change | A | Yes | Selecting a dropdown does not auto-submit; use a Submit button |
| 3.2.3 | **Consistent Navigation**: Navigation order is consistent across pages | AA | Yes | Nav links appear in same order on every page |
| 3.2.4 | **Consistent Identification**: Components with same function are identified consistently | AA | Yes | Search icon always labeled "Search", not "Find" on some pages |
| 3.3.1 | **Error Identification**: Form errors are described in text | A | Yes | `aria-describedby` links input to error message; error message is visible |
| 3.3.2 | **Labels or Instructions**: Inputs have labels and instructions | A | Yes | Every `<input>` has `<label>`; required fields marked; format hints provided |
| 3.3.3 | **Error Suggestion**: Errors include suggestions for correction | AA | Yes | "Invalid date format" → "Use YYYY-MM-DD format" |
| 3.3.4 | **Error Prevention (Legal, Financial, Data)**: Reversible, checked, or confirmed | AA | No | Manual: confirmation step before order submission, data deletion |

### 6.4 Robust — Content must be robust enough for current and future user agents

| # | Criterion | Level | Auto | Check |
|---|-----------|-------|------|-------|
| 4.1.1 | **Parsing** (WCAG 2.0; removed in 2.2 but still good practice) | A | Yes | No duplicate IDs; elements properly nested; no unclosed tags |
| 4.1.2 | **Name, Role, Value**: All UI components expose name, role, state to assistive technology | A | Yes | Custom widgets have correct ARIA; `<button>` not `<div onclick>`; `aria-expanded` on toggles |
| 4.1.3 | **Status Messages**: Status messages are announced without moving focus | AA | Yes | `role="status"` or `role="alert"` or `aria-live="polite"` |

### Keyboard focus management cheat sheet

| Action | Expected Behavior |
|--------|-------------------|
| Open modal | Focus moves to first focusable element in modal; focus trapped inside |
| Close modal | Focus returns to the element that opened the modal |
| Navigate to new page (SPA) | Focus moves to `<h1>` or skip-to-content target |
| Delete item from list | Focus moves to next item or previous item |
| Open dropdown menu | Focus moves to first menu item; arrow keys navigate; Escape closes |
| Activate tab panel | Focus stays on tab; Tab key moves to active panel |
| Submit form with errors | Focus moves to first field with error |

## Section 7: Tool Matrix

### 7.1 Python Libraries

| Library | Role Used By | Purpose |
|---------|-------------|---------|
| `html.parser` (stdlib) | `html_css_core`, `design_research` | Parse HTML5 documents into token streams; detect semantic structure |
| `re` (stdlib) | `html_css_core`, `javascript_debug` | Regex-based CSS property validation, JS error pattern matching |
| `json` (stdlib) | All roles | Design token serialization/deserialization |
| `urllib` / `urllib.request` (stdlib) | `design_research` | Fetch HTML and CSS from target URLs |
| `httpx` (optional) | `design_research` | Async HTTP client for multi-page analysis; HTTP/2 support |
| `cssutils` | `design_research` | Structured CSS parsing: extract rules, properties, media queries |
| `subprocess` (stdlib) | `javascript_debug`, `accessibility_audit`, `framework_integration` | Invoke Node.js tooling (eslint, axe-core, Lighthouse, framework CLIs) |

### 7.2 Browser / Node.js Tools

| Tool | Role Used By | Purpose | Invocation |
|------|-------------|---------|------------|
| **Chrome DevTools** | `design_research` (manual reference) | Visual inspection, Computed panel, Coverage tab | UI only (not scriptable by role) |
| **Lighthouse** | `accessibility_audit` | Accessibility, performance, SEO, best-practices audits | `lighthouse <url> --only-categories=accessibility --output=json` |
| **axe-core CLI** | `accessibility_audit` | Automated WCAG violation detection | `axe <url> --stdout` or `@axe-core/cli` |
| **ESLint** | `javascript_debug` | JS/TS linting and static analysis | `eslint --format=json file.js` |
| **Playwright** | `framework_integration` (test operation) | Browser automation for E2E testing | `playwright test` (via framework CLIs) |
| **Puppeteer** | `design_research` (optional) | Full-page rendering, CSS computed style extraction | `puppeteer.launch()` via Node.js script |

### 7.3 Testing Frameworks by Role

| Role | Test Level | Framework / Approach |
|------|-----------|---------------------|
| `html_css_core` | Unit | pytest with `html.parser` assertions on input HTML fixtures |
| `html_css_core` | Integration | Molecule: provision Docker container, run role against sample HTML |
| `framework_integration` | Unit | Jest + React Testing Library (React); Supertest (REST); graphql-tools (GraphQL) |
| `framework_integration` | E2E | Playwright against scaffolded Next.js app |
| `javascript_debug` | Unit | pytest: validate error classification accuracy against fixture logs |
| `javascript_debug` | Integration | ESLint invocation on known-violation files, verify output |
| `design_research` | Unit | pytest: mock HTTP responses, assert token extraction accuracy |
| `design_research` | Integration | Molecule: run role against a known-version static site, diff output tokens |
| `accessibility_audit` | Unit | pytest: parse axe-core JSON output, assert violation count thresholds |
| `accessibility_audit` | Integration | Molecule: deploy a known-violation page, run role, assert violations detected |
| `component_scaffold` | Unit | pytest: generate component, parse output HTML, assert semantic structure |
| `component_scaffold` | Integration | Molecule: generate components from token fixture, run `html_css_core` validate on output |

## Appendix: Dependencies

```yaml
# galaxy.yml excerpt
dependencies:
  general_ludd.agent: ">=0.1.0"
```

**Python**:
- Python 3.9+ stdlib: `html.parser`, `re`, `argparse`, `json`, `urllib`
- `cssutils` (optional): structured CSS parsing in `design_research`
- `httpx` (optional): async HTTP fetching in `design_research`

**Node.js** (optional, for extended features):
- `eslint`: JS/TS linting in `javascript_debug`
- `@axe-core/cli`: automated accessibility checks in `accessibility_audit`
- `lighthouse`: comprehensive accessibility audits in `accessibility_audit`
- Framework CLIs: `create-react-app`, `create-next-app` (for `framework_integration` scaffold)
