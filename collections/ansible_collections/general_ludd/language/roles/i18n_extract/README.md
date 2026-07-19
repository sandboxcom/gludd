# i18n_extract

Extract translatable strings from source code for internationalization.
Scans source files for gettext markers, generates .pot template files,
creates per-locale .po files, compiles .mo catalogs, and supports
pseudolocalization for in-place UI testing.

## Requirements

- Python 3.11+
- babel (optional, for richer extraction)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `source_dir` | string | `""` | Directory to scan |
| `source_patterns` | list | `["*.py","*.html","*.js"]` | File patterns |
| `pot_file` | string | `messages.pot` | Output .pot path |
| `output_dir` | string | `/tmp/gludd-i18n-extract` | Output directory |
| `extract_method` | string | `gettext` | Extraction method |
| `pseudolocalize` | bool | `false` | Generate pseudolocalized output |
| `pseudoloc_locale` | string | `en-XA` | Pseudolocale tag |
| `lint_hardcoded` | bool | `false` | Detect hardcoded strings |
| `lint_placeholders` | bool | `false` | Check format placeholders |

## Output

- `{{ output_dir }}/{{ pot_file }}` — .pot template
- `{{ output_dir }}/extraction_report.json` — JSON report with:
  - `string_count`: total translatable strings found
  - `files_scanned`: number of files processed
  - `pot_path`: path to generated .pot file
  - `pseudoloc_po`: path to pseudolocalized .po (if enabled)
  - `lint_findings`: array of lint issues (if enabled)

## Example

```yaml
- name: Extract i18n strings
  ansible.builtin.include_role:
    name: general_ludd.language.i18n_extract
  vars:
    source_dir: "/app/src"
    source_patterns:
      - "*.py"
      - "*.html"
    pot_file: "app.pot"
    pseudolocalize: true
    lint_hardcoded: true
```
