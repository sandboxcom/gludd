# translate

Translate text across languages via dictionary lookup and optional LLM fallback.

## Usage

```bash
python files/translate.py --text "Hello" --source auto --target fr --output translation.json
```

## Files

- `tasks/main.yml` — role entry point (script invocation + artifact handling)
- `files/translate.py` — standalone translation script
- `defaults/main.yml` — default variables
