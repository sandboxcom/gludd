# homoglyph_scan

Scan text and domain names for confusable/homoglyph characters per UTS #39.
Detects invisible characters, bidi spoofing (CVE-2021-42574 Trojan Source),
mixed-script text, and classifies attack vectors with severity ratings.

## Requirements

- Python 3.11+
- Standard library only (unicodedata, re)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `input_text` | string | `""` | Text to scan |
| `input_domain` | string | `""` | Domain name to scan |
| `check_confusables` | bool | `true` | UTS #39 confusable detection |
| `check_invisible` | bool | `true` | Invisible character detection |
| `check_bidi` | bool | `true` | Bidi spoofing detection |
| `check_mixed_script` | bool | `true` | Mixed-script detection |
| `min_severity` | string | `low` | Minimum severity filter |
| `artifact_dir` | string | `/tmp/gludd-homoglyph-scan` | Output directory |

## Output

JSON file at `{{ artifact_dir }}/homoglyph_scan.json` containing:
- `total_findings`: count of all issues detected
- `severity_counts`: breakdown by severity
- `findings`: array of {type, severity, character, codepoint, description}
- `attack_vectors`: array of identified attack categories
- `safe`: true if no findings above min_severity
- `mixed_scripts`: scripts detected in input

## Example

```yaml
- name: Scan for homoglyph attacks
  ansible.builtin.include_role:
    name: general_ludd.language.homoglyph_scan
  vars:
    input_text: "раypal.com"
    check_confusables: true
    check_invisible: true
    check_bidi: true
```
