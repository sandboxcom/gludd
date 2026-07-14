# phonetic_transcribe

Convert text to phoneme representations. Supports IPA transcription,
ARPABET encoding, Soundex/Metaphone/Double Metaphone hashing, and
CMU Pronouncing Dictionary lookup with homophone detection.

## Requirements

- Python 3.11+
- Standard library only

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `input_text` | string | `""` | Text to transcribe |
| `method` | string | `arpabet` | Transcription method |
| `cmu_dict_lookup` | bool | `true` | Look up in CMU dictionary |
| `artifact_dir` | string | `/tmp/gludd-phonetic-transcribe` | Output directory |

## Output

JSON file at `{{ artifact_dir }}/phonetic_transcription.json` containing:
- `input_text`: original text
- `method`: transcription method used
- `words`: array of {word, transcription, stress_pattern}
- `soundex_code`: Soundex hash (if method=soundex)
- `metaphone_codes`: primary + alternate (if method=double_metaphone)
- `ipa_equivalent`: IPA representation
- `homophones`: detected homophones (from CMU dict)

## Example

```yaml
- name: Transcribe to ARPABET
  ansible.builtin.include_role:
    name: general_ludd.language.phonetic_transcribe
  vars:
    input_text: "hello world"
    method: "arpabet"
    cmu_dict_lookup: true
```
