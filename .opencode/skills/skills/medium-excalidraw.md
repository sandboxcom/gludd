---
{
  "name": "medium-excalidraw",
  "description": "Visual architecture diagram generation from natural language. Self-validating: generates Excalidraw JSON, renders to PNG, reviews layout, fixes issues. Maps visual structure to conceptual structure. Source: unicodeveloper/medium-2026.",
  "tags": [
    "diagrams",
    "architecture",
    "excalidraw",
    "visual",
    "documentation",
    "medium-2026"
  ],
  "category": "documentation"
}
---

# Excalidraw Diagram Generator

Generate production-quality architecture diagrams from natural language.
Diagrams argue, not just display — visual structure maps to conceptual structure.

## Design Philosophy

1. **Visual structure = conceptual structure** — Fan-out for one-to-many,
   timelines for sequential flows, convergence for aggregation. Never default
   to uniform card grids.
2. **Evidence artifacts** — Include actual code snippets and real JSON payloads
   inline. No placeholder text.
3. **Self-validation** — Generate Excalidraw JSON, render to PNG via Playwright,
   review for layout issues (overlapping text, misaligned arrows, unbalanced
   spacing), fix problems before presenting.

## Workflow

1. User describes the architecture or system in natural language
2. Agent generates Excalidraw JSON with appropriate visual structure
3. Render to PNG for self-review
4. Fix layout issues (overlap, alignment, balance)
5. Present final diagram file

## Use Cases

- Architecture diagrams for multi-tenant systems
- Sequence diagrams (OAuth2 flows, request pipelines)
- Data flow diagrams with real payloads
- System design for code reviews
- Onboarding diagrams for new engineers

## Brand Customization

Colors live in `references/color-palette.md`. Edit once, every diagram
follows your palette.

## Anti-patterns

- Uniform card grids for everything (doesn't communicate structure)
- Placeholder text in boxes (use real code/data)
- Presenting first drafts without self-review
- Diagrams that only label components without showing relationships

## Install reference

Original: `npx skills add https://github.com/coleam00/excalidraw-diagram-skill --skill excalidraw-diagram`
