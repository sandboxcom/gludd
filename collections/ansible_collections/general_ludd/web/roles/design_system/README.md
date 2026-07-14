# `general_ludd.web.design_system` — Design System Token Extraction

Extract and generate design tokens from CSS: spacing scales, color palettes, typography systems, and component-level token definitions. Outputs standardized JSON, CSS custom properties, or SCSS variables.

## Quick start

```yaml
- name: Extract design tokens from CSS
  hosts: localhost
  vars:
    design_system_css_source: "styles/globals.css"
    design_system_token_output_format: "json"
    design_system_extract_spacing: true
    design_system_extract_colors: true
    design_system_extract_typography: true
  roles:
    - general_ludd.web.design_system
```

## Knowledge Domains

### Spacing Systems
- **4px base grid**: foundational unit — all spacing multiples of 4px (4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 80, 96, 128)
- **8px grid**: common design tool alignment — elements snap to 8px increments
- **Spatial scale tokens**: `--space-xs` (4px), `--space-sm` (8px), `--space-md` (16px), `--space-lg` (24px), `--space-xl` (32px), `--space-2xl` (48px), `--space-3xl` (64px)
- **CSS custom properties for spacing**: `var(--space-md)` → consistent spacing across components
- **Responsive spacing**: `clamp(16px, 5vw, 64px)` — fluid between min/max, `padding-inline` for writing-mode-safe horizontal padding
- **Gap vs margin**: CSS grid/flex `gap` replaces margin hacks; no collapsing margin issues
- **Component-level spacing**: each component's internal padding/margin declared from token scale

### Color Systems
- **Palette structure**: primary (brand), secondary (accent), neutral (greys), semantic (success/error/warning/info)
- **Neutral scale**: 50 (lightest) through 950 (darkest) — 10-11 stops for flexible UI
- **Semantic colors**: success (green, positive feedback), warning (amber/yellow, caution), error (red, destructive), info (blue, informational)
- **Color contrast pairing**: light background → dark text (minimum 4.5:1), dark background → light text
- **Dark mode**: `@media (prefers-color-scheme: dark)` — invert surface colors, adjust text brightness, preserve brand identity
- **HSL vs OKLCH**: HSL is perceptually uneven (yellow at 60% lightness ≠ blue at 60%); OKLCH is perceptually uniform — predictable contrast
- **Color tokens**: `--color-primary-500` (base), `--color-primary-300` (light), `--color-primary-700` (dark), `--color-surface`, `--color-text-primary`
- **Opacity variants**: `--color-primary-500/20` — modern CSS color-mix or rgba equivalents

### Typography
- **Modular scaling**: font sizes follow a ratio — major third (1.25), perfect fourth (1.333), perfect fifth (1.5), golden ratio (1.618)
- **Type scale tokens**: `--text-xs` (0.75rem/12px), `--text-sm` (0.875rem/14px), `--text-base` (1rem/16px), `--text-lg` (1.125rem/18px), `--text-xl` (1.25rem/20px), `--text-2xl` (1.5rem/24px), `--text-3xl` (1.875rem/30px), `--text-4xl` (2.25rem/36px)
- **Font stacks**: system fonts (`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, ...`), web fonts via `@font-face`, Google Fonts (`@import url(...)`)
- **Variable fonts**: single file with weight/width/slant axes, `font-variation-settings: 'wght' 400`
- **Line-height ratios**: body text 1.5 (generous readability), headings 1.1-1.3 (tight), code 1.6 (legible monospace)
- **Font-weight scale**: 400 (regular), 500 (medium), 600 (semibold), 700 (bold) — more granularity with variable fonts
- **Letter spacing**: headlines benefit from negative tracking (`-0.02em`), uppercase from positive (`0.05em`)

### Design Token Formats
- **W3C DTCG (Design Tokens Community Group) spec**: standard JSON schema for tokens — groups (color.bg.primary), types (color/dimension/fontWeight/number), modes (light/dark)
- **Style Dictionary**: Amazon's design token build system — JSON/YAML in, platform outputs out (CSS, SCSS, iOS, Android, Figma)
- **Tokens Studio**: Figma plugin — sync tokens between Figma and codebase, GitHub sync, multi-theme
- **CSS custom properties export**: `:root { --color-primary: #3b82f6; }` — native, no build step, runtime theming

### Z-index Scales
- **Standardized layers**: each UI layer assigned a fixed scale position — no guesswork
- **Scale**: base (0), dropdown (100), sticky (200), overlay (300), drawer (400), modal (500), popover (600), toast/alert (700), tooltip (800), loading/spinner (900)
- **Token**: `--z-base`, `--z-dropdown`, `--z-sticky`, `--z-modal`, `--z-toast`, `--z-tooltip`
- **Rule**: never hardcode `z-index: 9999` — always use a token from the scale

## Parameters

| Variable                             | Default   | Description                                  |
|--------------------------------------|-----------|----------------------------------------------|
| `design_system_css_source`           | `""`      | Local CSS file path or URL                   |
| `design_system_token_output_format`  | `"json"`  | Output format: json/css/scss                 |
| `design_system_extract_spacing`      | `true`    | Extract spacing/padding/margin values        |
| `design_system_extract_colors`       | `true`    | Extract color declarations (hex/rgb/hsl)     |
| `design_system_extract_typography`   | `true`    | Extract font-family, size, weight, line-height|
| `design_system_generate_component_tokens` | `false`| Generate component-level design tokens       |
| `design_system_output_dir`           | `/tmp/...`| Output directory for generated tokens        |

## Results

The `_ds_output` fact contains the extracted tokens; a `design_system.json` artifact and `<format>_tokens.<ext>` file are written to the output directory.
