# build_presentation role

Builds, validates, and (optionally) deploys the gludd reveal.js presentation
as a **repeatable gludd-managed task**. Codifies the deck build pipeline that
was previously a loose collection of `make` targets + manual steps.

## Diagrams — prefer Mermaid

**Mermaid text diagrams are the preferred diagram format for the deck**,
over SVG (or other binary/raster image formats). Rationale:

- **Version-control friendly** — Mermaid is plain text, so diagrams diff
  cleanly and review changes line-by-line. SVG edited by hand does not;
  SVG exported from a drawing tool churns on every save.
- **In-tree editable** — no external drawing tool round-trip. Authors edit
  the diagram source in the same commit as the prose around it.
- **Reveals in reveal.js** — the deck loads `mermaid.min.js` and renders
  ```mermaid fenced blocks inside `<section>` slides at runtime. No binary
  asset pipeline, no image-hosting path to manage.
- **Single source of truth** — the same `.mmd` / ```mermaid source renders
  in the deck, in GitHub README rendering, and in generated docs.

When a diagram genuinely cannot be expressed in Mermaid (e.g. a photograph
or a pixel-precise schematic), commit the SVG/PNG under
`docs/presentation/deck/assets/` and reference it from the slide. Treat
binary image additions as the exception, not the default.

The role optionally validates Mermaid syntax in the deck source when a
Mermaid CLI (`@mermaid-js/mermaid-cli`, exposing the `mmdc` binary) is
available; see step 6 in the pipeline table below. The step is
safe-by-default — when `mmdc` is absent the validation is skipped, not
failed.

### Absent deck directories

An absent `presentation_dir` is valid for build-only and staged workflows.
The role checks that it is a directory before invoking
`ansible.builtin.find`, and treats absence as zero Mermaid files. This avoids
the warning that `find` intentionally emits for missing/non-directory paths
while preserving a successful no-op result.

This has been a recurring source of noisy automation output: users reported
the exact `find` warning in
[Ansible Automation Platform](https://stackoverflow.com/questions/74703214/within-ansible-automation-platform-getting-path-to-directory-does-not-exist)
and asked how to suppress it for optional locations in
[r/ansible](https://www.reddit.com/r/ansible/comments/15306ul/hide_warnings_from_a_single_task_module/).
Ansible's
[`find` return-value documentation](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/find_module.html#return-values)
confirms that invalid paths are reported through `skipped_paths`; preflighting
with `stat` keeps that diagnostic reserved for genuinely unexpected paths.

## Pipeline

| Step | Toggle | Default |
|------|--------|---------|
| Gather live `gludd_facts` for honest deck stats | (always) | on |
| Require green gate before emitting numbers (`gludd_gate_check`) | `require_green_gate` | off |
| 1. Regenerate `deck-data.json` from README status table (`make deck-data`) | `regenerate_deck_data` | on |
| 2. Honesty lint — banned tokens + %-must-match-README (`make deck-honesty`) | `honesty_check_enabled` | on |
| 3. Validate reveal.js HTML structure (`<section>` + `reveal.js` load) | `validate_html_structure` | on |
| 4. Serve locally for visual QA (`make deck-serve`) | `serve_locally` | off |
| 5. Deploy: commit + push deck artifacts via `gludd_git` | `deploy_target` | `""` (off) |
| 6. Validate Mermaid diagram syntax (`mmdc` if available, else skip) | `validate_mermaid_syntax` | on |

**SAFE-BY-DEFAULT:** deploy is off. Push requires a second explicit opt-in
(`enable_deploy_push: true`).

## Uses gludd modules

- **`gludd_facts`** — live project stats (work/todo/model/history) for honest
  deck numbers.
- **`gludd_gate_check`** — verifies `.gate-status` is complete+passing before
  stats are emitted (honest-metrics contract: every number backed by a real
  gate run).
- **`gludd_git`** — commit + push regenerated deck artifacts for GitHub Pages.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `presentation_dir` | `docs/presentation/deck` | reveal.js deck dir |
| `deck_data_source` | `README.md` | parsed into `deck-data.json` |
| `deck_data_path` | `docs/presentation/deck-data.json` | generated artifact |
| `honesty_check_enabled` | `true` | run `make deck-honesty` |
| `deploy_target` | `""` | `""` / `github_pages` |
| `require_green_gate` | `false` | refuse build if gate not green |
| `serve_locally` | `false` | launch `make deck-serve` (foreground) |
| `enable_deploy_push` | `false` | second opt-in for deploy push |
| `validate_mermaid_syntax` | `true` | validate `.mmd` / ```mermaid blocks via `mmdc` if present (skipped when CLI absent) |
| `mermaid_cli_bin` | `mmdc` | name/path of the Mermaid CLI binary |

## Example — local build + honesty lint only

```yaml
- name: Build + validate the deck (no deploy)
  ansible.builtin.include_role:
    name: general_ludd.agent.build_presentation
  vars:
    presentation_dir: docs/presentation/deck
    honesty_check_enabled: true
```

## Example — full build + deploy to GitHub Pages

```yaml
- name: Build + deploy the deck
  ansible.builtin.include_role:
    name: general_ludd.agent.build_presentation
  vars:
    deploy_target: github_pages
    enable_deploy_commit: true
    enable_deploy_push: true
    deploy_commit_message: "docs(deck): rebuild for v0.2.0"
```

## Artifacts

- `{{ artifact_dir }}/build_presentation.json` — structured pipeline result
- `{{ artifact_dir }}/build_presentation.md` — human-readable build report
