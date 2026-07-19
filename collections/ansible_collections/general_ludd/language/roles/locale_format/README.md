# locale_format

Format dates, numbers, and currency values according to CLDR locale data.
Parses BCP 47 locale tags and applies locale-specific formatting patterns.
Supports plural rules, RTL detection, and locale negotiation.

## Requirements

- Python 3.11+
- babel (optional, for richer locale support)

## Role Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `value` | string | `""` | Value to format |
| `value_type` | string | `date` | date, number, or currency |
| `locale` | string | `en-US` | BCP 47 locale tag |
| `date_length` | string | `medium` | Date format length |
| `currency_code` | string | `USD` | ISO 4217 currency code |
| `artifact_dir` | string | `/tmp/gludd-locale-format` | Output directory |

## Output

JSON file at `{{ artifact_dir }}/locale_format.json` containing:
- `locale`: parsed BCP 47 tag
- `language_name`: full language name
- `script`: ISO 15924 script code
- `territory`: ISO 3166 country code
- `is_rtl`: boolean
- `formatted_value`: the formatted output string
- `format_rules`: the format pattern used
- `number_format`: decimal/grouping/percent separators
- `plural_rules`: applicable plural rule set
- `first_day_of_week`: CLDR first day
- `measurement_system`: metric/US/UK

## Example

```yaml
- name: Format date in German
  ansible.builtin.include_role:
    name: general_ludd.language.locale_format
  vars:
    value: "2026-07-14"
    value_type: "date"
    locale: "de-DE"
    date_length: "full"

- name: Format currency in Japanese Yen
  ansible.builtin.include_role:
    name: general_ludd.language.locale_format
  vars:
    value: "1234567"
    value_type: "currency"
    locale: "ja-JP"
    currency_code: "JPY"
```
