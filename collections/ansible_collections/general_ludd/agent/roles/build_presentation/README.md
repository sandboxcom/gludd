# build_presentation role

Builds, validates, and (optionally) deploys the gludd reveal.js presentation
as a **repeatable gludd-managed task**. Codifies the deck build pipeline that
was previously a loose collection of `make` targets + manual steps.

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
