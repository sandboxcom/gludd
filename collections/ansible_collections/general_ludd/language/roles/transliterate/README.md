# transliterate

Transliterate text between scripts (Cyrillic, Arabic, CJK, Indic, and more).

## Usage

```bash
python files/transliterate.py --text "Привет" --target-script Latin --output transliteration.json
```

## Files

- `tasks/main.yml` — role entry point (script invocation + artifact handling)
- `files/transliterate.py` — standalone transliteration script
- `defaults/main.yml` — default variables
