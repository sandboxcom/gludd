# language_detect

Detect the language of input text across 50+ languages using statistical and
stopword-based classification.

## Usage

```bash
python files/language_detect.py --text "Hello world" --output language_detection.json
```

## Files

- `tasks/main.yml` — role entry point (script invocation + artifact handling)
- `files/language_detect.py` — standalone detection script
- `defaults/main.yml` — default variables
