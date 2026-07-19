# unicode_analyze

Analyze Unicode properties of text: codepoint details, character categories,
block names, Unicode planes, all four normalization forms (NFC/NFD/NFKC/NFKD),
grapheme cluster segmentation per UAX #29, surrogate pair decoding, script
detection, and UTF-8/16/32 byte encodings.

## Requirements

- Python 3.11+
- Standard library only (unicodedata, codecs)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `input_text` | string | `""` | Text to analyze |
| `input_file` | string | `""` | Path to file (alternative to input_text) |
| `artifact_dir` | string | `/tmp/gludd-unicode-analyze` | Output directory |
| `output_format` | string | `json` | Output format |
| `include_codepoints` | bool | `true` | Individual codepoint details |
| `include_categories` | bool | `true` | Unicode character categories |
| `include_blocks` | bool | `true` | Unicode block names |
| `include_planes` | bool | `true` | Unicode plane assignment |
| `include_normalization` | bool | `true` | All four normalization forms |
| `include_grapheme_clusters` | bool | `true` | Grapheme cluster segmentation |
| `include_scripts` | bool | `true` | ISO 15924 script property |
| `include_surrogates` | bool | `true` | Surrogate pair detection |
| `include_utf_encodings` | bool | `true` | UTF-8/16/32 byte encodings |
| `include_version_info` | bool | `false` | Unicode version history |

## Output

JSON file at `{{ artifact_dir }}/unicode_analysis.json` containing:
- `input_length`: character count
- `input_byte_length`: UTF-8 byte count
- `codepoints`: array of {index, char, codepoint (U+XXXX), category, block, plane, script, name}
- `normalization`: object with NFC/NFD/NFKC/NFKD forms
- `grapheme_clusters`: array of cluster strings
- `surrogates`: array of decoded surrogate pairs
- `utf_encodings`: byte representations in each UTF encoding
- `summary`: {categories_found, planes_found, scripts_found, block_count}

## Example

```yaml
- name: Analyze Unicode text
  ansible.builtin.include_role:
    name: general_ludd.language.unicode_analyze
  vars:
    input_text: "Hello, 世�!"
    include_normalization: true
    include_grapheme_clusters: true
```
