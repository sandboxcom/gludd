# design_research — Research design elements from websites

Fetch a target URL, extract CSS (stylesheets and inline), analyze design tokens
(colors, fonts, spacing), capture layout structure, and detect CSS frameworks.

## Knowledge Areas

### Inspecting Computed Styles
- `getComputedStyle(element)` — resolved values for every CSS property
- `element.style` — inline style access (camelCase property names)
- `getComputedStyle(element).getPropertyValue('--custom-prop')` — CSS custom property values
- Chrome DevTools: Elements → Computed tab → filter by property

### Extracting Design Tokens
- **CSS custom properties**: `var(--color-primary)`, `var(--spacing-md)`, `var(--font-sans)`
- **Color palette extraction**: background, text, accent, border, shadow colors
- **Color formats**: hex (`#fff`), rgb (`rgb(255,255,255)`), hsl (`hsl(0,0%,100%)`), named colors
- **Color role classification**: primary accent, secondary accent, surface colors, semantic (error/warning/success/info)

### Box Model Analysis
- **Margin**: outer spacing between elements
- **Padding**: inner spacing between border and content
- **Border**: width, style, color, radius
- **Content**: actual element dimensions
- DevTools: Elements → Styles → Box Model visualization

### Typography
- **Font stacks**: comma-separated family list with fallbacks
- **Web fonts**: `@font-face`, Google Fonts, variable fonts (`font-variation-settings`)
- **Font metrics**: size, weight, line-height, letter-spacing, word-spacing
- **Font formats**: woff2, woff, ttf, otf, eot

### Spacing Scale
- **4px grid**: common base unit for spacing tokens
- **8px grid**: alternative base, common in Material Design
- **Spacing scale**: t-shirt sizes (xs/sm/md/lg/xl) or numeric steps (2/4/8/16/32)
- **Consistent unit usage**: all-rem vs mixed px/em/rem

### Layout Structure
- **Grid areas**: named template areas in CSS Grid
- **Flex directions**: row vs column layouts
- **Container queries**: `@container` for component-level responsiveness
- **Layout patterns**: holy grail, sidebar + content, card grid, masonry

### CSS Framework Detection
- **Tailwind**: utility classes (`flex`, `text-sm`, `bg-white`, `p-4`)
- **Bootstrap**: grid classes (`col-md-6`), component classes (`btn`, `modal`, `navbar`)
- **Material Design**: `mdc-` prefix, `mat-` prefix, `md-` prefix
- **Bulma**: `is-primary`, `is-large`, `has-background`
- **Foundation**: `small-12`, `medium-6`, xy-grid classes

### Responsive Breakpoints
- Common breakpoints: 480 (mobile), 768 (tablet), 1024 (desktop small), 1280 (desktop), 1440+ (large)
- `min-width` vs `max-width` mobile-first approach
- Container-based breakpoints vs viewport-based

## Operations

| Operation | Description |
|-----------|-------------|
| `fetch` | Fetch URL and extract linked CSS/stylesheets |
| `extract_tokens` | Extract color palette, font stacks, spacing scale |
| `analyze_layout` | Detect grid areas, flex directions, positioning |
| `full_audit` | Full analysis: fetch + tokens + layout + framework detection |

## Usage

```yaml
- name: Research design of a target website
  include_role:
    name: general_ludd.web.design_research
  vars:
    target_url: https://example.com
    operation: full_audit
    extract_colors: true
    extract_fonts: true
    extract_spacing: true
    extract_layout: true
    max_depth: 1
```
