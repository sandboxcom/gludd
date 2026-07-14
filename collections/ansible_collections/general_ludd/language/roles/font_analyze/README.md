# font_analyze

Analyze font files (OpenType, TrueType, WOFF, WOFF2). Parses binary font
headers and tables to extract metrics, enumerate features, detect variable
font axes, and validate web font compatibility.

## Requirements

- Python 3.11+
- Standard library only (struct, io)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `font_file` | string | `""` | Path to font file |
| `check_metrics` | bool | `true` | Extract font metrics |
| `check_tables` | bool | `true` | Enumerate OpenType tables |
| `check_features` | bool | `true` | Check kerning/ligatures/GSUB/GPOS |
| `check_web_font` | bool | `false` | Validate web font format |
| `check_cjk` | bool | `false` | CJK-specific checks |
| `check_variable` | bool | `false` | Variable font axis detection |
| `check_monospace` | bool | `false` | Check monospace property |
| `artifact_dir` | string | `/tmp/gludd-font-analyze` | Output directory |

## Output

JSON file at `{{ artifact_dir }}/font_analysis.json` containing:
- `file`: font file path
- `format`: ttf/otf/woff/woff2
- `size_bytes`: file size
- `metrics`: {ascent, descent, line_gap, cap_height, x_height, em_units}
- `tables`: array of table tag records found
- `table_count`: total tables
- `features`: {kerning, ligatures, has_GSUB, has_GPOS}
- `variable_axes`: detected axes (if enabled)
- `is_monospace`: true/false (if enabled)
- `web_font_valid`: true/false (if enabled)

## Example

```yaml
- name: Analyze font file
  ansible.builtin.include_role:
    name: general_ludd.language.font_analyze
  vars:
    font_file: "/usr/share/fonts/opentype/NotoSans-Regular.otf"
    check_web_font: false
    check_variable: true
```
