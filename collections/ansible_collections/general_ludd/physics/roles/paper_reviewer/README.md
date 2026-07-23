# `general_ludd.physics.paper_reviewer` — Research Paper Reviewer

Analyze research papers, extract sections and findings, and score rigor.

## Quick start

```yaml
- name: Review a research paper
  hosts: localhost
  vars:
    review_paper_title: "Neural Quantum States"
    review_depth: "standard"
  roles:
    - general_ludd.physics.paper_reviewer
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `review_paper_text` | `""` | Paper text to review |
| `review_paper_title` | `""` | Paper title |
| `review_depth` | `standard` | Review depth (quick/standard/deep/meta_review) |
| `review_criteria` | (7 criteria) | Evaluation criteria |
| `review_output_dir` | `/tmp/gludd-review` | Output directory |
