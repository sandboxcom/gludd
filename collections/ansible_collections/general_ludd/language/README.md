# general_ludd.language Ansible Collection

Ansible collection providing deep knowledge of all things language-related on
computers: Unicode, character encodings, BOMs, localization, internationalization,
fonts, phonetics, and text processing.

## Roles

| Role | Category | Purpose |
|------|----------|---------|
| `unicode_analyze` | Unicode | Analyze Unicode properties: script, block, plane, category, normalization |
| `bom_detect` | BOM | Detect, strip, add Byte Order Marks per encoding |
| `encoding_detect` | Charsets | chardet-based encoding detection, conversion, mojibake repair |
| `locale_format` | L10n | Format dates/numbers/currency per locale via CLDR data |
| `i18n_extract` | I18n | Extract translatable strings from source code |
| `font_analyze` | Fonts | Analyze font files: metrics, features, tables, web-font validation |
| `phonetic_transcribe` | Phonetics | Text-to-phoneme conversion (IPA, ARPABET, metaphone) |
| `homoglyph_scan` | Text | Detect confusable/homoglyph characters and invisible codepoints |

## Knowledge Modules

| Module | Content |
|--------|---------|
| `unicode_data.py` | Unicode properties, normalization, grapheme clusters, plane taxonomy |
| `charset_map.py` | Encoding tables, BOM sequences, code page mappings, mojibake patterns |
| `locale_data.py` | CLDR-derived formats, plural rules, RTL maps, BCP 47 parsing |
| `phonetic_data.py` | IPA/X-SAMPA/ARPABET tables, Soundex/Metaphone, CMU dictionary |
| `homoglyph_data.py` | UTS #39 confusables, invisible chars, homoglyph attack vectors |

## Dependencies

- `general_ludd.agent >= 0.1.0`
- Python: `chardet>=5.2.0` (optional)
