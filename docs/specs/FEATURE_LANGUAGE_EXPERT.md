# Feature: Language Expert Collection

**Status: CLOSED** | **Created: 2026-07-14** | **Closed: 2026-08-03** | **Target: v0.1.0-beta.3**

## 1. Overview

Ansible collection `general_ludd.language` providing roles and knowledge modules
that give the agent deep knowledge of all things language-related on computers:
Unicode, encodings, BOMs, localization, internationalization, fonts, phonetics,
language standards, and text processing. Serves as the authoritative in-process
reference for every character-, encoding-, locale-, and script-level concern
the agent encounters.

## 2. Roles (11) — All Built & Verified

| Role | Category | Purpose | Status |
|------|----------|---------|--------|
| `unicode_analyze` | Unicode | Analyze Unicode properties: script, block, plane, category, normalization form | DONE |
| `bom_detect` | BOM | Detect, strip, add Byte Order Marks per encoding | DONE |
| `encoding_detect` | Charsets | chardet-based encoding detection, conversion, mojibake repair | DONE |
| `locale_format` | L10n | Format dates/numbers/currency per locale via CLDR data | DONE |
| `i18n_extract` | I18n | Extract translatable strings from source code (xgettext-style) | DONE |
| `font_analyze` | Fonts | Analyze font files: metrics, features, tables, web-font validation | DONE |
| `phonetic_transcribe` | Phonetics | Text-to-phoneme conversion (IPA, ARPABET, metaphone) | DONE |
| `homoglyph_scan` | Text | Detect confusable/homoglyph characters and invisible codepoints | DONE |
| `language_detect` | Detection | Statistical + stopword language detection, LLM fallback | DONE |
| `translate` | Translation | Multi-language translation (dictionary + LLM fallback) | DONE |
| `transliterate` | Transliteration | Script-to-script conversion (Cyrillic, Arabic, CJK) | DONE |

## 3. Knowledge Modules (12 Total)

### Core Modules (8) — `src/general_ludd/language/`

| Module | Content | Status |
|--------|---------|--------|
| `unicode_data.py` | Unicode property lookup, normalization forms (NFC/NFD/NFKC/NFKD), grapheme cluster boundaries, Unicode planes (BMP/SIP/SMP/SSP/SPUA) | DONE |
| `charset_map.py` | Encoding tables, BOM byte sequences per encoding, code page mappings, chardet confidence thresholds, mojibake patterns | DONE |
| `locale_data.py` | CLDR-derived locale data: number/date/currency formats, plural rules, RTL script map, BCP 47 tag parsing | DONE |
| `phonetic_data.py` | IPA character table, X-SAMPA to IPA, ARPABET phoneme set, CMU Pronouncing Dictionary, Soundex/Metaphone/Double Metaphone | DONE |
| `homoglyph_data.py` | Unicode confusables table (UTS #39), invisible character codepoints, homoglyph categories, attack vector mapping | DONE |
| `font_data.py` | OpenType table definitions, font format specs, variable font axes, font metric functions | DONE |
| `i18n_data.py` | ICU placeholder extraction, untranslated string detection, PO parse/serialize, pseudolocalization | DONE |
| `cross_patterns.py` | Cross-language import detection, FFI pattern detection, polyglot build detection, script invocation detection | DONE |

### Collection Module Utils (4) — `plugins/module_utils/`

| Module | Content | Status |
|--------|---------|--------|
| `contracts.py` | Pydantic contracts: language detection, translation, transliteration, homoglyph scan, phonetic, font analysis, locale | DONE |
| `core.py` | Delegating wrapper: imports from 8 src modules into Ansible module_utils namespace with typed wrappers | DONE |
| `capability_router.py` | Capability-based dispatch via agent daemon: `LanguageRouter` with `route()`, `route_detect()`, `route_translate()`, `route_transliterate()` | DONE |
| `model_client.py` | LLM-based language detection and translation via `ModelClient`, with offline fallback | DONE |

## 4. Coverage Domains

### 4.1 Unicode
UTF-8, UTF-16 (LE/BE), UTF-32, surrogate pairs, normalization forms (NFC, NFD,
NFKC, NFKD), Unicode planes (BMP, SIP, SMP, SSP, SPUA), Unicode version history,
Unicode Consortium working groups.

Plane classification follows the 17 fixed 65,536-code-point planes, independently
of whether a character is currently assigned or rendered. In particular,
U+DFFFF is in unassigned plane 13, while U+E0000 starts the Supplementary
Special-purpose Plane; neither belongs to the BMP. This is pinned to the Unicode
16 [allocation model](https://www.unicode.org/versions/Unicode16.0.0/core-spec/chapter-2/).

The distinction has user-visible consequences. A Noto CJK practitioner report
for [U+32C3C](https://github.com/notofonts/noto-cjk/issues/319) identifies the
code point as Tertiary Ideographic Plane data while documenting missing-glyph
rendering across major platforms. Gludd must keep code-point plane identity
separate from font coverage: an unsupported glyph remains in its standard plane
and must not be relabeled BMP or unassigned merely because a renderer shows a
tofu box.

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

## 6. Files (Actual — Built)

```
collections/ansible_collections/general_ludd/language/
├── galaxy.yml                         (model_capabilities 11, role_capabilities 11, capability tags 11)
├── README.md
├── roles/
│   ├── unicode_analyze/{files,tasks,defaults,vars,meta}/
│   ├── bom_detect/{files,tasks,defaults,vars,meta,README}/
│   ├── encoding_detect/{files,tasks,defaults,vars,meta,README}/
│   ├── locale_format/{files,tasks,defaults,vars,meta,README}/
│   ├── i18n_extract/{files,tasks,defaults,vars,meta,README}/
│   ├── font_analyze/{files,tasks,defaults,vars,meta,README}/
│   ├── phonetic_transcribe/{files,tasks,defaults,vars,meta,README}/
│   ├── homoglyph_scan/{files,tasks,defaults,vars,meta,README}/
│   ├── language_detect/{files,tasks,defaults,meta}/
│   ├── translate/{files,tasks,defaults,meta}/
│   └── transliterate/{files,tasks,defaults,meta}/
├── plugins/
│   └── module_utils/
│       ├── __init__.py
│       ├── contracts.py              (237 lines, 18 pydantic models)
│       ├── core.py                   (240 lines, 23 wrappers)
│       ├── capability_router.py      (156 lines, LanguageRouter class)
│       └── model_client.py           (174 lines, LLM detect + translate)
├── tests/
│   ├── test_language_analysis_behavior.py
│   ├── test_integration_phonetic_data.py
│   ├── test_integration_homoglyph_data.py
│   ├── test_integration_locale_data.py
│   ├── test_integration_charset_map.py
│   ├── test_integration_role_paths.py
│   └── test_integration_unicode_data.py
src/general_ludd/language/
├── __init__.py
├── unicode_data.py
├── charset_map.py
├── locale_data.py
├── phonetic_data.py
├── homoglyph_data.py
├── font_data.py
├── i18n_data.py
├── cross_patterns.py
├── core.py
├── corpus.py
├── contracts.py
├── detection.py
├── polyglot.py
├── tooling.py
├── translation.py
└── transliteration.py
```

## 9. Completion Record

Closed 2026-08-03 against `d6758aa2` on `development`.

### What was built (vs planned)

| Component | Planned | Built |
|-----------|---------|-------|
| Roles | 8 | 11 (added language_detect, translate, transliterate) |
| Core modules (`src/`) | 5 | 17 (added font_data, i18n_data, cross_patterns, core, corpus, contracts, detection, polyglot, tooling, translation, transliteration) |
| Module_utils (`plugins/`) | 0 | 4 (contracts, core, capability_router, model_client) |
| Collection tests | 1 | 7 |
| galaxy.yml | skeleton | full (11 model_capabilities, 11 role_capabilities, capability tags) |

### Capability Router Wiring

11 capabilities declared in galaxy.yml: `language_detection`, `translation`, `transliteration`, `unicode_analyze`, `encoding_detect`, `font_analyze`, `homoglyph_scan`, `phonetic_transcribe`, `locale_format`, `i18n_extract`, `bom_detect`.

Capability router verification test: `tests/unit/test_capability_router_language.py` — collection discovery, tag index lookups, cross-collection isolation.

### Architecture S65.4 Fixes Applied

The architecture audit (S65.4) identified 5 language collection violations:
- No contracts module → RESOLVED (`contracts.py`, 237 lines, 18 pydantic models)
- ViewModel-without-Model in locale_format → RESOLVED (core.py with proper delegation)
- Model bypass via script calls in roles → RESOLVED (capability_router.py + model_client.py)
- ViewModel gating destructive ops → RESOLVED (contracts enforce input validation)
- Deployment constraint on core.py → RESOLVED (core.py is pure delegation, no deployment dependency)

All 5 architecture violations closed.

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
