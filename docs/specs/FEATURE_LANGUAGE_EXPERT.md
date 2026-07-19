# Feature: Language Expert Collection

**Status: DRAFT** | **Created: 2026-07-14** | **Target: v0.1.0-beta.2**

## 1. Overview

Ansible collection `general_ludd.language` providing roles and knowledge modules
that give the agent deep knowledge of all things language-related on computers:
Unicode, encodings, BOMs, localization, internationalization, fonts, phonetics,
language standards, and text processing. Serves as the authoritative in-process
reference for every character-, encoding-, locale-, and script-level concern
the agent encounters.

## 2. Roles (8)

| Role | Category | Purpose |
|------|----------|---------|
| `unicode_analyze` | Unicode | Analyze Unicode properties: script, block, plane, category, normalization form |
| `bom_detect` | BOM | Detect, strip, add Byte Order Marks per encoding |
| `encoding_detect` | Charsets | chardet-based encoding detection, conversion, mojibake repair |
| `locale_format` | L10n | Format dates/numbers/currency per locale via CLDR data |
| `i18n_extract` | I18n | Extract translatable strings from source code (xgettext-style) |
| `font_analyze` | Fonts | Analyze font files: metrics, features, tables, web-font validation |
| `phonetic_transcribe` | Phonetics | Text-to-phoneme conversion (IPA, ARPABET, metaphone) |
| `homoglyph_scan` | Text | Detect confusable/homoglyph characters and invisible codepoints |

## 3. Knowledge Modules

| Module | Content |
|--------|---------|
| `unicode_data.py` | Unicode property lookup, normalization forms (NFC/NFD/NFKC/NFKD), grapheme cluster boundaries, Unicode planes (BMP/SIP/SMP/SSP/SPUA), version history, character categories |
| `charset_map.py` | Encoding tables, BOM byte sequences per encoding, code page mappings (ISO 8859-x, Windows-125x, Shift-JIS, EUC-*, GB*, Big5, KOI8-*), chardet confidence thresholds, mojibake patterns |
| `locale_data.py` | CLDR-derived locale data: number formats, date formats, currency formats, plural rules, RTL script map, locale negotiation priorities, BCP 47 tag parsing |
| `phonetic_data.py` | IPA character table, X-SAMPA to IPA mapping, ARPABET phoneme set, CMU Pronouncing Dictionary subset, Soundex/Metaphone/Double Metaphone algorithm data |
| `homoglyph_data.py` | Unicode confusables table (UTS #39), invisible character codepoints (zero-width, bidi control, soft hyphen), homoglyph categories (Latin/Cyrillic/Greek lookalikes), attack vector mapping |

## 4. Coverage Domains

### 4.1 Unicode
UTF-8, UTF-16 (LE/BE), UTF-32, surrogate pairs, normalization forms (NFC, NFD,
NFKC, NFKD), Unicode planes (BMP, SIP, SMP, SSP, SPUA), Unicode version history,
Unicode Consortium working groups.

### 4.2 Byte Order Marks (BOM)
FEFF (UTF-16 BE), FFFE (UTF-16 LE), EF BB BF (UTF-8), 0000FEFF (UTF-32 BE),
FFFE0000 (UTF-32 LE). Detection, stripping, adding. When BOMs are required vs
optional per RFC/IETF.

### 4.3 Character Sets / Encodings
ISO 8859-1 through 8859-16, Windows-1250 through 1258, Shift-JIS, EUC-JP,
EUC-KR, GB2312, GB18030, Big5, KOI8-R, KOI8-U, IBM code pages. Encoding
detection (chardet/cchardet), conversion between encodings, mojibake
detection and repair.

### 4.4 Localization (L10n)
gettext/.po/.mo files, ICU message format, CLDR (Unicode Common Locale Data
Repository), locale identifiers (BCP 47), date/time/number/currency formatting
per locale, plural rules, RTL vs LTR text handling, locale negotiation.

### 4.5 Internationalization (I18n)
String extraction for translation (xgettext, babel), translation management
systems, pseudolocalization for testing, i18n linting, right-to-left (Arabic,
Hebrew) and bidirectional text (Unicode Bidirectional Algorithm UBA),
CJK (Chinese/Japanese/Korean) text handling.

### 4.6 Fonts
OpenType, TrueType, WOFF/WOFF2, font metrics (ascent/descent/line gap),
kerning, ligatures, font fallback chains, variable fonts, font subsetting,
web fonts, system font stacks per OS, emoji fonts, monospace vs proportional,
CJK font specifics (fullwidth vs halfwidth).

### 4.7 Phonetics
IPA (International Phonetic Alphabet), X-SAMPA, ARPABET, phonetic transcription,
Soundex/Metaphone/Double Metaphone, CMU Pronouncing Dictionary,
text-to-phoneme conversion.

### 4.8 Language Standards
ISO 639-1, ISO 639-2, ISO 639-3 language codes, IETF BCP 47 language tags,
ISO 3166 country codes, ISO 15924 script codes, ITU-T language standards,
W3C Internationalization Activity, Unicode CLDR, IANA language subtag registry.

### 4.9 Text Processing
Grapheme clusters, normalization, collation (Unicode Collation Algorithm UCA),
case folding, text segmentation (word/sentence/line breaking per Unicode
TR14/TR29), confusables/homoglyph detection, invisible character detection
(zero-width chars, bidi control chars).

### 4.10 Working Groups
Unicode Technical Committee (UTC), W3C i18n WG, IETF, ITU-T SG16 (multimedia),
ISO/IEC JTC 1/SC 2 (coded character sets), CLDR technical committee.

## 5. Implementation Plan

| Phase | Scope | Duration |
|-------|-------|----------|
| A | Foundation: galaxy.yml, skeleton, 5 knowledge modules, 1 test file | Day 1 |
| B | Unicode + BOM + Encoding: 3 roles with module_utils integration | Days 2-3 |
| C | L10n + I18n: 2 roles, CLDR locale data population | Days 4-5 |
| D | Fonts + Phonetics + Homoglyph: 3 roles, font/phone/homoglyph data | Days 6-7 |

## 6. Files

```
collections/ansible_collections/general_ludd/language/
├── galaxy.yml
├── README.md
├── roles/
│   ├── unicode_analyze/tasks/main.yml
│   ├── bom_detect/tasks/main.yml
│   ├── encoding_detect/tasks/main.yml
│   ├── locale_format/tasks/main.yml
│   ├── i18n_extract/tasks/main.yml
│   ├── font_analyze/tasks/main.yml
│   ├── phonetic_transcribe/tasks/main.yml
│   └── homoglyph_scan/tasks/main.yml
├── plugins/
│   └── module_utils/
│       └── (reserved for future Ansible module_utils)/
src/general_ludd/language/
├── __init__.py
├── unicode_data.py
├── charset_map.py
├── locale_data.py
├── phonetic_data.py
└── homoglyph_data.py
tests/unit/
└── test_language_expert_collection.py
```

## 7. Dependencies

pip: `ansible-core>=2.16.0`, `chardet>=5.2.0` (optional encoding detection)
system: none (pure Python knowledge modules)

## 8. Test Plan

- Unit: knowledge module exhaustiveness (all Unicode planes, all BOM sequences,
  all ISO 639-1 codes, all locale format categories). Collection schema validation.
- Integration: molecule test per role — each role verifies its task file loads
  and the underlying knowledge module is importable.
- E2E: `make test-language-expert` — runs collection schema check + unit tests
- Gate: knowledge modules >=85% coverage per file
