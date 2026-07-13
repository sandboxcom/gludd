# html_css_core — HTML5 authoring, CSS3 styling, responsive design

Validate HTML5 semantic structure, check CSS syntax, generate responsive boilerplate,
and audit semantic landmarks.

## Knowledge Areas

### HTML5 Semantic Elements
- `<header>`, `<nav>`, `<main>`, `<article>`, `<section>`, `<aside>`, `<footer>`
- `<figure>`, `<figcaption>`, `<details>`, `<summary>`, `<time>`, `<mark>`, `<data>`
- Heading hierarchy (`<h1>`-`<h6>`) — no level skipping
- Document outline algorithm

### ARIA Landmarks
- `banner`, `navigation`, `main`, `complementary`, `contentinfo`, `region`, `search`, `form`
- `role` attribute on HTML5 semantic elements (prefer implicit vs explicit when element matches)
- Accessible name computation via `aria-label`, `aria-labelledby`

### CSS Layout
- **Grid**: `grid-template-columns`, `grid-template-rows`, `grid-template-areas`, `grid-column`, `grid-row`, `grid-area`, `gap`
- **Flexbox**: `flex-direction`, `flex-wrap`, `justify-content`, `align-items`, `align-content`, `flex-grow/shrink/basis`
- **Box alignment**: `place-items`, `place-content`, `place-self`

### Responsive Design
- **Media queries**: `min-width`, `max-width`, `prefers-color-scheme`, `prefers-reduced-motion`, `orientation`
- **Container queries**: `container-type`, `container-name`, `@container`
- **Viewport units**: `vw`, `vh`, `dvh`, `svh`, `lvh`, `vmin`, `vmax`
- **Clamp functions**: `clamp(min, preferred, max)`, `min()`, `max()`
- **Logical properties**: `margin-inline`, `padding-inline`, `border-inline`, `inset-inline`, `inline-size`, `block-size`

### CSS Architecture
- **Custom properties**: `--color-primary`, `--spacing-unit`, etc. via `var()`
- **Cascade layers**: `@layer base`, `@layer components`, `@layer utilities`
- **At-rules**: `@import`, `@font-face`, `@keyframes`, `@supports`

## Operations

| Operation | Description |
|-----------|-------------|
| `validate_html` | Parse HTML, check well-formedness, detect semantic elements, find ARIA roles |
| `check_css` | Parse CSS file, validate property names, detect media queries, custom properties |
| `responsive_boilerplate` | Generate responsive CSS boilerplate with configured breakpoints |
| `semantic_audit` | Full semantic structure audit: landmarks, headings, ARIA coverage |

## Usage

```yaml
- name: Validate HTML and check CSS
  include_role:
    name: general_ludd.web.html_css_core
  vars:
    html_file: /src/index.html
    css_file: /src/style.css
    operation: validate_html
    validate_w3c: false
    responsive_check: true
    target_breakpoints: [480, 768, 1024, 1440]
```
