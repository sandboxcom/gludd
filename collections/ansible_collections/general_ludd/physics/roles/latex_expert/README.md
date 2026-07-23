# `general_ludd.physics.latex_expert` — LaTeX Document Expert

Render LaTeX equations, format tables, and generate research paper documents.

## Quick start

```yaml
- name: Generate a quantum mechanics paper
  hosts: localhost
  vars:
    latex_document_class: "article"
    latex_title: "Quantum Well Solutions"
  roles:
    - general_ludd.physics.latex_expert
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `latex_document_class` | `article` | Document class |
| `latex_font_size` | `11pt` | Font size |
| `latex_title` | `Generated Document` | Document title |
| `latex_author` | `Agentic Harness` | Document author |
| `latex_packages` | `[amsmath, amssymb, graphicx, hyperref]` | LaTeX packages |
| `latex_output_format` | `pdf` | Output format |
| `latex_output_dir` | `/tmp/gludd-latex` | Output directory |
