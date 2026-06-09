---
{
  "name": "medium-frontend-design",
  "description": "Production-grade UI generation that escapes AI's default visual signature. Bold typography, intentional color palettes, purposeful animations. Source: unicodeveloper/medium-2026.",
  "tags": [
    "frontend",
    "design",
    "ui",
    "css",
    "animations",
    "medium-2026"
  ],
  "category": "frontend"
}
---

# Frontend Design

Escape distributional convergence — the statistical center of AI-generated design
that produces Inter font, purple gradients, and grid cards every time.

## Principles

1. **Distinctive typography** — Choose typefaces with personality. Avoid Inter, Roboto,
   system-ui as primary. Pair a display font with a readable body font.
2. **Intentional color** — Build palettes from a single dominant hue with strategic
   accents. No default blue-purple gradients. Use contrast purposefully.
3. **Purposeful animation** — Animate to communicate state changes, hierarchy, or
   causality. Never animate for decoration. Every animation answers "what changed?"
4. **Layout variety** — Break the card grid habit. Use editorial layouts, asymmetric
   grids, full-bleed sections, and whitespace as a design element.
5. **Visual signature** — The result should NOT look like AI output. Users should
   see a deliberate design system, not a template.

## Process

1. Before writing any CSS/component code, describe the design system:
   typography scale, color tokens, spacing scale, component variants
2. Choose 2-3 reference sites that match the desired aesthetic
3. Build the design tokens first, then components
4. Review output: does this look like a senior designer reviewed it?

## Anti-patterns

- Defaulting to Inter/system-ui + blue + white + grid cards
- Using the same spacing and sizing everywhere (uniform = AI feel)
- Animating everything with `fadeIn` transitions
- Ignoring mobile breakpoints until the end

## Install reference

Original Anthropic skill: `npx skills add anthropics/claude-code --skill frontend-design`
277,000+ installs as of March 2026.
