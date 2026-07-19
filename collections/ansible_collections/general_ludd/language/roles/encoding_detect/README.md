# encoding_detect

Detect character encoding from raw bytes using chardet-style analysis with
confidence scores. Convert between encodings (transcoding), detect mojibake
patterns, and suggest repairs. Covers 47+ encodings across single-byte,
multi-byte CJK, and Cyrillic.

## Requirements

- Python 3.11+
- chardet (optional, fallback to chardet built-in)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `input_file` | string | `""` | Path to file |
| `input_bytes` | string | `""` | Hex-encoded bytes |
| `min_confidence` | float | `0.50` | Minimum confidence threshold |
| `target_encoding` | string | `utf-8` | Target encoding for conversion |
| `detect_mojibake` | bool | `false` | Enable mojibake detection |
| `artifact_dir` | string | `/tmp/gludd-encoding-detect` | Output directory |

## Output

JSON file at `{{ artifact_dir }}/encoding_detection.json` containing:
- `detected_encoding`: best-guess encoding name
- `confidence`: 0.0-1.0 score
- `confidence_level`: entry/usable/reliable/trusted
- `all_candidates`: array of {encoding, confidence} for all candidates
- `converted_preview`: first 200 chars in target encoding
- `mojibake_detected`: true/false (if enabled)
- `mojibake_pattern`: matched signature (if detected)
- `byte_length`: input size in bytes

## Example

```yaml
- name: Detect and convert encoding
  ansible.builtin.include_role:
    name: general_ludd.language.encoding_detect
  vars:
    input_file: "/path/to/shift_jis.txt"
    target_encoding: "utf-8"
    min_confidence: 0.60
    detect_mojibake: true
```
