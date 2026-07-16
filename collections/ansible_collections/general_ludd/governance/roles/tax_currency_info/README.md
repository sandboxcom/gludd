# tax_currency_info

Exposes the `tax_currency.py` module_util to agents. Returns the ISO 4217
currency, tax authority, VAT/sales-tax rate, and tax-year convention for a
country.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `tax_currency_info_enabled` | `false` | Must be `true` to run |
| `tax_currency_info_country` | `null` | 2-letter ISO country code |
| `tax_currency_info_output_dir` | `/tmp/gludd-governance-tax` | Artifact directory |

## Result facts

- `tax_currency_info_result` — parsed JSON
- `tax_currency_info_verdict` — compact summary (found, currency, tax_authority)
