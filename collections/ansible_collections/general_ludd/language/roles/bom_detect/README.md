# bom_detect

Detect, strip, and add Byte Order Marks (BOMs) per encoding. Identifies BOM
signatures for UTF-8, UTF-16 (BE/LE), UTF-32 (BE/LE), UTF-7, SCSU, and
GB-18030. Reports BOM presence, size, and RFC/IETF compliance.

## Requirements

- Python 3.11+
- Standard library only

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `input_file` | string | `""` | Path to file |
| `input_bytes` | string | `""` | Hex-encoded bytes |
| `artifact_dir` | string | `/tmp/gludd-bom-detect` | Output directory |
| `strip_bom` | bool | `false` | Strip detected BOM from output |
| `add_bom` | bool | `false` | Add a BOM to output |
| `add_bom_encoding` | string | `UTF-8` | Target encoding for BOM addition |
| `audit_directory` | bool | `false` | Recursively scan directory |
| `audit_path` | string | `""` | Directory path to audit |

## Output

JSON file at `{{ artifact_dir }}/bom_detection.json` containing:
- `bom_detected`: true/false
- `encoding`: BOM-matched encoding (or null)
- `bom_size`: bytes
- `rfc_compliance`: required/optional/none
- `bom_hex`: hex representation of BOM bytes
- `stripped`: hex representation after BOM removal (if strip_bom=true)
- `audit_results`: per-file findings (if audit_directory=true)

## Example

```yaml
- name: Detect BOM in file
  ansible.builtin.include_role:
    name: general_ludd.language.bom_detect
  vars:
    input_file: "/path/to/file.txt"
    strip_bom: true
```
