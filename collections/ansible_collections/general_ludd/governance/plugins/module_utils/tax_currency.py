"""
tax_currency -- Tax systems, currencies, tax authorities, and tax treaties
knowledge base for the governance collection.

Data shape:

    TAX_SYSTEMS: dict[str, dict] -- tax_type -> description/structure/examples
    TAX_DATA: dict[str, dict]     -- ISO-3166-1 alpha-2 country code -> tax profile
    CURRENCIES: dict[str, dict]   -- ISO 4217 code -> currency metadata
    TAX_AUTHORITIES: dict[str, dict] -- country code -> revenue authority contact
    TAX_TREATIES: dict[tuple[str, str], dict] -- (country_a, country_b) -> treaty

Functions:
    get_tax_info(country, tax_type) -> dict | None
    get_currency_info(code) -> dict | None
    get_filing_requirements(country, entity_type) -> dict | None
    get_tax_treaty(country_a, country_b) -> dict | None
    list_countries() -> list[str]
    list_currencies() -> list[str]

Notes:
    * Rates and thresholds are illustrative reference values for the most
      recent published tax year and MUST NOT be used for actual tax filing
      without verification against the authority's current guidance. The
      purpose of this module is to give the governance agent structured
      knowledge of tax SYSTEMS, not live tax advice.
    * Country codes are ISO 3166-1 alpha-2 (US, GB, DE, ...). Currency codes
      are ISO 4217 (USD, EUR, JPY, ...) except for digital currencies which
      use their ticker symbols (BTC, ETH).
    * Treaty lookups are symmetric: get_tax_treaty("US", "GB") returns the
      same record as get_tax_treaty("GB", "US").
"""

from __future__ import annotations

from typing import Any

# ── Tax system definitions ─────────────────────────────────────────────────
# Each entry describes a category of taxation: how it works, what it taxes,
# and example jurisdictions that levy it.

TAX_SYSTEMS: dict[str, dict[str, Any]] = {
    "income_progressive": {
        "description": (
            "Personal income tax where the marginal rate rises with taxable "
            "income. Higher earners pay a larger share of each additional "
            "dollar. Brackets are published annually by the revenue authority."
        ),
        "structure": "marginal_brackets",
        "base": "individual_earnings",
        "example_countries": ["US", "GB", "DE", "FR", "CA", "AU", "JP"],
    },
    "income_flat": {
        "description": (
            "Personal income tax levied at a single constant rate regardless "
            "of income level. Often paired with a high tax-free allowance so "
            "the effective rate still rises at low incomes."
        ),
        "structure": "single_rate",
        "base": "individual_earnings",
        "example_countries": ["RU", "AE", "SA", "EE", "LV", "LT"],
    },
    "income_regressive": {
        "description": (
            "A tax whose effective rate falls as the taxable base rises. Pure "
            "regressive income taxes are rare; the descriptor most often "
            "applies to payroll / cap-and-trade-style levies where the rate "
            "is fixed and therefore a larger share of a low earner's wages."
        ),
        "structure": "rate_falls_with_base",
        "base": "payroll_or_consumption",
        "example_countries": ["US"],
    },
    "corporate": {
        "description": (
            "Tax on the net income (profit) of corporations and other legal "
            "entities. Levied at the national level and sometimes also at a "
            "sub-national level (state, canton, province)."
        ),
        "structure": "flat_or_graduated_on_profit",
        "base": "corporate_net_income",
        "example_countries": ["US", "GB", "DE", "FR", "JP", "CA", "AU", "IE"],
    },
    "vat_gst": {
        "description": (
            "Value-Added Tax or Goods-and-Services Tax: a consumption tax "
            "levied at each stage of production on the value added. Ultimately "
            "borne by the final consumer; businesses reclaim input tax. The "
            "most common consumption-tax design worldwide."
        ),
        "structure": "multi_stage_with_input_credit",
        "base": "value_added_at_each_stage",
        "example_countries": ["GB", "DE", "FR", "CA", "AU", "JP", "IN"],
    },
    "sales": {
        "description": (
            "A single-stage retail sales tax collected only at the point of "
            "final sale to the consumer. No input-credit mechanism; common in "
            "US states and Canadian provinces as a complement to GST."
        ),
        "structure": "single_stage_retail",
        "base": "retail_sale_price",
        "example_countries": ["US", "CA"],
    },
    "property": {
        "description": (
            "Recurring ad-valorem tax on real estate and sometimes on other "
            "tangible property (vehicles, boats). Usually levied by municipal "
            "or regional governments to fund local services."
        ),
        "structure": "ad_valorem_recurring",
        "base": "assessed_property_value",
        "example_countries": ["US", "GB", "CA", "AU", "FR"],
    },
    "wealth": {
        "description": (
            "A direct tax on the net worth of an individual or household, "
            "levied annually on aggregate assets minus liabilities. Relatively "
            "rare; subject to active policy debate."
        ),
        "structure": "annual_net_worth_pct",
        "base": "net_assets",
        "example_countries": ["ES", "NO", "CH"],
    },
    "inheritance": {
        "description": (
            "Tax on the transfer of wealth at death ('estate tax' when paid by "
            "the estate, 'inheritance tax' when paid by the beneficiary). "
            "Often paired with a lifetime gift-tax cap to prevent avoidance."
        ),
        "structure": "transfer_based",
        "base": "value_of_inheritance_or_estate",
        "example_countries": ["US", "GB", "DE", "FR", "JP"],
    },
    "capital_gains": {
        "description": (
            "Tax on the profit from the sale of a capital asset (shares, real "
            "estate, business interests). Often levied at a preferential rate "
            "relative to ordinary income to encourage investment."
        ),
        "structure": "realization_based",
        "base": "profit_on_asset_disposal",
        "example_countries": ["US", "GB", "CA", "AU", "DE", "FR"],
    },
    "digital_services": {
        "description": (
            "Tax on gross revenue from specified digital services (advertising, "
            "intermediation, user-data sales). Introduced unilaterally by "
            "several jurisdictions pending the OECD Pillar One consensus."
        ),
        "structure": "turnover_based",
        "base": "gross_digital_revenue",
        "example_countries": ["GB", "FR", "IT", "ES", "AT"],
    },
    "carbon": {
        "description": (
            "A per-tonne levy on greenhouse-gas emissions, either as an "
            "explicit carbon tax or via a cap-and-trade allowance price. "
            "Designed to internalize the social cost of carbon."
        ),
        "structure": "per_tonne_emission",
        "base": "tonnes_CO2_equivalent",
        "example_countries": ["GB", "CA", "SE", "FI", "FR", "DE"],
    },
    "excise": {
        "description": (
            "A selective tax on specific goods — typically alcohol, tobacco, "
            "fuel, gambling, and sugar-sweetened beverages. Applied at "
            "production or import, with rates set per unit (pack, litre) "
            "rather than as a percentage of price."
        ),
        "structure": "per_unit_specific",
        "base": "unit_of_good",
        "example_countries": ["US", "GB", "DE", "IN", "AU", "ZA", "KR", "TR"],
    },
    "tariff": {
        "description": (
            "Customs duty levied on imported goods, either ad valorem (pct of "
            "value), specific (per unit), or compound (both). The Harmonized "
            "System (HS) codes classify goods; rates vary by origin per "
            "bilateral/regional trade agreements and WTO MFN schedules."
        ),
        "structure": "ad_valorem_or_specific",
        "base": "import_value_or_quantity",
        "example_countries": ["US", "IN", "BR", "CN", "ZA", "MX", "TR", "EU"],
    },
}


# ── Per-country tax profiles ────────────────────────────────────────────────
# Reference values for the most recent published tax year. The goal is
# structured knowledge of each country's tax *architecture*, not live advice.

TAX_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "currency": "USD",
        "tax_types": [
            "income_progressive",
            "corporate",
            "sales",
            "property",
            "inheritance",
            "capital_gains",
            "income_regressive",
        ],
        "filing_deadline": "April 15 (individuals); March 15 (S-corp/partnership); April 15 (C-corp)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_usd": 0, "rate": 0.10, "label": "10%"},
            {"threshold_usd": 11_600, "rate": 0.12, "label": "12%"},
            {"threshold_usd": 47_150, "rate": 0.22, "label": "22%"},
            {"threshold_usd": 100_525, "rate": 0.24, "label": "24%"},
            {"threshold_usd": 191_950, "rate": 0.32, "label": "32%"},
            {"threshold_usd": 243_725, "rate": 0.35, "label": "35%"},
            {"threshold_usd": 609_350, "rate": 0.37, "label": "37%"},
        ],
        "corporate_rate": 0.21,
        "standard_deduction_usd": 14_600,
        "notes": "Federal brackets shown; many states levy additional income/sales tax.",
    },
    "GB": {
        "name": "United Kingdom",
        "currency": "GBP",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
            "digital_services",
            "carbon",
        ],
        "filing_deadline": "January 31 (self-assessment, after tax year end Apr 5)",
        "tax_year": "apr_to_apr",
        "income_brackets": [
            {"threshold_gbp": 0, "rate": 0.20, "label": "Basic rate"},
            {"threshold_gbp": 37_700, "rate": 0.40, "label": "Higher rate"},
            {"threshold_gbp": 125_140, "rate": 0.45, "label": "Additional rate"},
        ],
        "corporate_rate": 0.25,
        "standard_rate_vat": 0.20,
        "digital_services_rate": 0.02,
        "notes": "Personal allowance £12,570 frozen through 2028.",
    },
    "DE": {
        "name": "Germany",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "July 31 (assessment; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.14, "label": "Zone 1 (rising zone)"},
            {"threshold_eur": 17_005, "rate": 0.24, "label": "Zone 2"},
            {"threshold_eur": 66_760, "rate": 0.42, "label": "Top rate"},
            {"threshold_eur": 277_825, "rate": 0.45, "label": "Wealth tax bracket"},
        ],
        "corporate_rate": 0.15,
        "solidarity_surcharge": 0.055,
        "standard_rate_vat": 0.19,
        "notes": "Church tax (Kirchensteuer) of 8-9% applies to members.",
    },
    "FR": {
        "name": "France",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "wealth",
            "inheritance",
            "capital_gains",
            "digital_services",
            "carbon",
        ],
        "filing_deadline": "May/June (online; varies by departement)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.0, "label": "0%"},
            {"threshold_eur": 11_294, "rate": 0.11, "label": "11%"},
            {"threshold_eur": 28_797, "rate": 0.30, "label": "30%"},
            {"threshold_eur": 82_341, "rate": 0.41, "label": "41%"},
            {"threshold_eur": 177_106, "rate": 0.45, "label": "45%"},
        ],
        "corporate_rate": 0.25,
        "standard_rate_vat": 0.20,
        "wealth_tax_name": "IFI (Immobilier)",
        "notes": "Wealth tax now limited to real estate (IFI).",
    },
    "JP": {
        "name": "Japan",
        "currency": "JPY",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
        ],
        "filing_deadline": "March 15 (kakutei shinkoku; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_jpy": 0, "rate": 0.05, "label": "5%"},
            {"threshold_jpy": 1_950_000, "rate": 0.10, "label": "10%"},
            {"threshold_jpy": 3_300_000, "rate": 0.20, "label": "20%"},
            {"threshold_jpy": 6_950_000, "rate": 0.23, "label": "23%"},
            {"threshold_jpy": 9_000_000, "rate": 0.33, "label": "33%"},
            {"threshold_jpy": 18_000_000, "rate": 0.40, "label": "40%"},
            {"threshold_jpy": 40_000_000, "rate": 0.45, "label": "45%"},
        ],
        "corporate_rate": 0.305,
        "standard_rate_vat": 0.10,
        "notes": "Local inhabitant tax adds a flat ~10% on top.",
    },
    "CA": {
        "name": "Canada",
        "currency": "CAD",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "sales",
            "property",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "April 30 (individuals); June 15 (self-employed)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_cad": 0, "rate": 0.15, "label": "15% (federal)"},
            {"threshold_cad": 55_867, "rate": 0.205, "label": "20.5%"},
            {"threshold_cad": 111_733, "rate": 0.26, "label": "26%"},
            {"threshold_cad": 173_205, "rate": 0.29, "label": "29%"},
            {"threshold_cad": 246_752, "rate": 0.33, "label": "33%"},
        ],
        "corporate_rate": 0.15,
        "standard_rate_vat": 0.05,
        "notes": "Provincial income tax and PST/HST added on top of federal.",
    },
    "AU": {
        "name": "Australia",
        "currency": "AUD",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "October 31 (self-lodge); May (tax agent)",
        "tax_year": "jul_to_jun",
        "income_brackets": [
            {"threshold_aud": 0, "rate": 0.0, "label": "Tax-free threshold"},
            {"threshold_aud": 18_200, "rate": 0.19, "label": "19%"},
            {"threshold_aud": 45_000, "rate": 0.325, "label": "32.5%"},
            {"threshold_aud": 120_000, "rate": 0.37, "label": "37%"},
            {"threshold_aud": 180_000, "rate": 0.45, "label": "45%"},
        ],
        "corporate_rate": 0.30,
        "standard_rate_vat": 0.10,
        "notes": "GST is a federal VAT; states levy payroll/land tax.",
    },
    "IN": {
        "name": "India",
        "currency": "INR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
        ],
        "filing_deadline": "July 31 (individuals, non-audit); October 31 (audit cases)",
        "tax_year": "apr_to_mar",
        "income_brackets": [
            {"threshold_inr": 0, "rate": 0.0, "label": "Nil (old regime)"},
            {"threshold_inr": 300_000, "rate": 0.05, "label": "5%"},
            {"threshold_inr": 700_000, "rate": 0.10, "label": "10%"},
            {"threshold_inr": 1_000_000, "rate": 0.15, "label": "15%"},
            {"threshold_inr": 1_200_000, "rate": 0.20, "label": "20%"},
            {"threshold_inr": 1_500_000, "rate": 0.30, "label": "30%"},
        ],
        "corporate_rate": 0.252,
        "standard_rate_vat": 0.18,
        "notes": "Dual regime: old (deductions) vs new (lower rates, fewer deductions). Surcharge + cess apply.",
    },
    "BR": {
        "name": "Brazil",
        "currency": "BRL",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
        ],
        "filing_deadline": "April 30 (DIRPF IRPF; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_brl": 0, "rate": 0.0, "label": "Isento (exempt)"},
            {"threshold_brl": 2_640.00, "rate": 0.075, "label": "7.5%"},
            {"threshold_brl": 3_751.05, "rate": 0.15, "label": "15%"},
            {"threshold_brl": 4_664.68, "rate": 0.225, "label": "22.5%"},
            {"threshold_brl": 5_590.76, "rate": 0.275, "label": "27.5%"},
        ],
        "corporate_rate": 0.34,
        "standard_rate_vat": 0.17,
        "notes": "Complex multi-layered system: federal (IRPJ, CSLL, PIS, COFINS), state (ICMS), municipal (ISS).",
    },
    "MX": {
        "name": "Mexico",
        "currency": "MXN",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
        ],
        "filing_deadline": "April 30 (annual ISR; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_mxn": 0, "rate": 0.0, "label": "Tasa 0%"},
            {"threshold_mxn": 8_952.49, "rate": 0.016, "label": "1.92%"},
            {"threshold_mxn": 75_984.56, "rate": 0.064, "label": "6.40%"},
            {"threshold_mxn": 133_536.20, "rate": 0.088, "label": "10.88%"},
            {"threshold_mxn": 155_423.69, "rate": 0.16, "label": "16.00%"},
            {"threshold_mxn": 250_001.55, "rate": 0.30, "label": "30.00%"},
            {"threshold_mxn": 500_003.10, "rate": 0.35, "label": "35.00%"},
        ],
        "corporate_rate": 0.30,
        "standard_rate_vat": 0.16,
        "notes": "IVA 16% nationally; northern border zone rate reduced to 8%.",
    },
    "ZA": {
        "name": "South Africa",
        "currency": "ZAR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "November 23 (SARS eFiling, non-provisional); January (provisional)",
        "tax_year": "mar_to_feb",
        "income_brackets": [
            {"threshold_zar": 0, "rate": 0.0, "label": "Tax-free threshold"},
            {"threshold_zar": 95_750, "rate": 0.18, "label": "18%"},
            {"threshold_zar": 237_100, "rate": 0.26, "label": "26%"},
            {"threshold_zar": 370_500, "rate": 0.31, "label": "31%"},
            {"threshold_zar": 512_800, "rate": 0.36, "label": "36%"},
            {"threshold_zar": 673_000, "rate": 0.39, "label": "39%"},
            {"threshold_zar": 857_900, "rate": 0.41, "label": "41%"},
            {"threshold_zar": 1_817_000, "rate": 0.45, "label": "45%"},
        ],
        "corporate_rate": 0.27,
        "standard_rate_vat": 0.15,
        "notes": "Carbon tax phased in; CGT inclusion rate 40% for individuals.",
    },
    "KR": {
        "name": "South Korea",
        "currency": "KRW",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
        ],
        "filing_deadline": "May 5 (final return; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_krw": 0, "rate": 0.06, "label": "6%"},
            {"threshold_krw": 14_000_000, "rate": 0.15, "label": "15%"},
            {"threshold_krw": 50_000_000, "rate": 0.24, "label": "24%"},
            {"threshold_krw": 88_000_000, "rate": 0.35, "label": "35%"},
            {"threshold_krw": 150_000_000, "rate": 0.38, "label": "38%"},
            {"threshold_krw": 300_000_000, "rate": 0.40, "label": "40%"},
            {"threshold_krw": 500_000_000, "rate": 0.42, "label": "42%"},
            {"threshold_krw": 1_000_000_000, "rate": 0.45, "label": "45%"},
        ],
        "corporate_rate": 0.24,
        "standard_rate_vat": 0.10,
        "notes": "Inheritance tax nominal rates among the highest in OECD at 50% top bracket.",
    },
    "SG": {
        "name": "Singapore",
        "currency": "SGD",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
        ],
        "filing_deadline": "April 15 (paper); April 18 (e-file)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_sgd": 0, "rate": 0.0, "label": "0%"},
            {"threshold_sgd": 20_000, "rate": 0.02, "label": "2%"},
            {"threshold_sgd": 30_000, "rate": 0.035, "label": "3.5%"},
            {"threshold_sgd": 40_000, "rate": 0.07, "label": "7%"},
            {"threshold_sgd": 80_000, "rate": 0.115, "label": "11.5%"},
            {"threshold_sgd": 120_000, "rate": 0.15, "label": "15%"},
            {"threshold_sgd": 160_000, "rate": 0.18, "label": "18%"},
            {"threshold_sgd": 200_000, "rate": 0.19, "label": "19%"},
            {"threshold_sgd": 240_000, "rate": 0.195, "label": "19.5%"},
            {"threshold_sgd": 280_000, "rate": 0.20, "label": "20%"},
            {"threshold_sgd": 320_000, "rate": 0.22, "label": "22%"},
            {"threshold_sgd": 500_000, "rate": 0.23, "label": "23%"},
            {"threshold_sgd": 1_000_000, "rate": 0.24, "label": "24%"},
        ],
        "corporate_rate": 0.17,
        "standard_rate_vat": 0.09,
        "notes": "No capital gains tax; territorial basis. GST raised from 8% to 9% in January 2024.",
    },
    "RU": {
        "name": "Russia",
        "currency": "RUB",
        "tax_types": [
            "income_flat",
            "corporate",
            "vat_gst",
            "property",
        ],
        "filing_deadline": "April 30 (3-NDFL); March 28 (corporate)",
        "tax_year": "calendar",
        "corporate_rate": 0.20,
        "standard_rate_vat": 0.20,
        "notes": "Flat 13% income tax (15% on income over 5 million RUB since 2021).",
    },
    "AE": {
        "name": "United Arab Emirates",
        "currency": "AED",
        "tax_types": [
            "corporate",
            "vat_gst",
        ],
        "filing_deadline": "9 months after fiscal year end (corporate); quarterly VAT returns",
        "tax_year": "calendar_or_fiscal",
        "corporate_rate": 0.09,
        "standard_rate_vat": 0.05,
        "notes": "No personal income tax. Mainland 9% CIT; free zones may be exempt.",
    },
    "SA": {
        "name": "Saudi Arabia",
        "currency": "SAR",
        "tax_types": [
            "corporate",
            "vat_gst",
        ],
        "filing_deadline": "120 days after financial year end (ZTKA income tax)",
        "tax_year": "calendar_or_hijri",
        "corporate_rate": 0.20,
        "standard_rate_vat": 0.15,
        "notes": "No personal income tax. Zakat 2.5% on assets for Saudi nationals.",
    },
    "TR": {
        "name": "Turkey",
        "currency": "TRY",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
        ],
        "filing_deadline": "March 31 (annual income; calendar year); April 30 (corporate)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_try": 0, "rate": 0.15, "label": "15%"},
            {"threshold_try": 110_000, "rate": 0.20, "label": "20%"},
            {"threshold_try": 230_000, "rate": 0.27, "label": "27%"},
            {"threshold_try": 870_000, "rate": 0.35, "label": "35%"},
            {"threshold_try": 3_000_000, "rate": 0.40, "label": "40%"},
        ],
        "corporate_rate": 0.25,
        "standard_rate_vat": 0.20,
        "notes": "High inflation environment causes bracket adjustments.",
    },
    "IT": {
        "name": "Italy",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
            "digital_services",
        ],
        "filing_deadline": "November 30 (Modello 730); September 30 (Redditi PF)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.23, "label": "23%"},
            {"threshold_eur": 28_000, "rate": 0.35, "label": "35%"},
            {"threshold_eur": 50_000, "rate": 0.43, "label": "43%"},
        ],
        "corporate_rate": 0.24,
        "standard_rate_vat": 0.22,
        "digital_services_rate": 0.03,
        "notes": "Regional income tax (IRAP) adds ~3.9%. Flat tax 7% for foreign retirees in south.",
    },
    "ES": {
        "name": "Spain",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "wealth",
            "inheritance",
            "capital_gains",
            "digital_services",
        ],
        "filing_deadline": "June 30 (Renta/IRPF; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.19, "label": "19% (estatal)"},
            {"threshold_eur": 12_450, "rate": 0.24, "label": "24%"},
            {"threshold_eur": 20_200, "rate": 0.30, "label": "30%"},
            {"threshold_eur": 35_200, "rate": 0.37, "label": "37%"},
            {"threshold_eur": 65_000, "rate": 0.45, "label": "45%"},
            {"threshold_eur": 300_000, "rate": 0.47, "label": "47%"},
        ],
        "corporate_rate": 0.25,
        "standard_rate_vat": 0.21,
        "digital_services_rate": 0.03,
        "notes": "Wealth tax (Impuesto sobre el Patrimonio) applies above ~EUR 700k.",
    },
    "NL": {
        "name": "Netherlands",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "inheritance",
            "capital_gains",
        ],
        "filing_deadline": "May 1 (preliminary); September 1 (final)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.093, "label": "Box 1 tier 1 (9.3%)"},
            {"threshold_eur": 38_098, "rate": 0.3697, "label": "Box 1 tier 2 (36.97%)"},
        ],
        "corporate_rate": 0.258,
        "standard_rate_vat": 0.21,
        "notes": "Box system: Box 1 (employment), Box 2 (substantial interest), Box 3 (savings/investments).",
    },
    "CH": {
        "name": "Switzerland",
        "currency": "CHF",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "wealth",
            "property",
        ],
        "filing_deadline": "March 31 (cantonal, extended); calendar year",
        "tax_year": "calendar",
        "corporate_rate": 0.149,
        "standard_rate_vat": 0.081,
        "notes": "Three-level system: federal (max 11.5%), cantonal, and communal. Wealth tax at cantonal level.",
    },
    "SE": {
        "name": "Sweden",
        "currency": "SEK",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "May 2 (inkomstdeklaration; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_sek": 0, "rate": 0.32, "label": "Municipal ~32% (flat)"},
            {"threshold_sek": 614_000, "rate": 0.52, "label": "National 20% + municipal ~32%"},
        ],
        "corporate_rate": 0.206,
        "standard_rate_vat": 0.25,
        "notes": "Carbon tax among the highest globally (~SEK 1,300/tCO2).",
    },
    "NO": {
        "name": "Norway",
        "currency": "NOK",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "wealth",
            "capital_gains",
            "carbon",
        ],
        "filing_deadline": "April 30 (selvangivelsen; calendar year)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_nok": 0, "rate": 0.22, "label": "22% (general)"},
            {"threshold_nok": 198_350, "rate": 0.232, "label": "Bracket 1 (1.2% surtax)"},
            {"threshold_nok": 279_150, "rate": 0.262, "label": "Bracket 2 (4.2% surtax)"},
            {"threshold_nok": 642_950, "rate": 0.342, "label": "Bracket 3 (12.2% surtax)"},
            {"threshold_nok": 926_800, "rate": 0.392, "label": "Bracket 4 (17.2% surtax)"},
        ],
        "corporate_rate": 0.22,
        "standard_rate_vat": 0.25,
        "notes": "Surtax on top of 22% flat rate. Wealth tax 1% on net worth above ~NOK 1.7m.",
    },
    "IE": {
        "name": "Ireland",
        "currency": "EUR",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
        ],
        "filing_deadline": "October 31 (ROS); November 15 (paper)",
        "tax_year": "calendar",
        "income_brackets": [
            {"threshold_eur": 0, "rate": 0.20, "label": "Standard rate 20%"},
            {"threshold_eur": 42_000, "rate": 0.40, "label": "Higher rate 40%"},
        ],
        "corporate_rate": 0.15,
        "standard_rate_vat": 0.23,
        "notes": "USC (Universal Social Charge) adds 0.5-8% on top. Attracts multinational HQs via low CIT.",
    },
    "NZ": {
        "name": "New Zealand",
        "currency": "NZD",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
        ],
        "filing_deadline": "July 7 (IR3 for individuals); March 31 (corporate extension)",
        "tax_year": "apr_to_mar",
        "income_brackets": [
            {"threshold_nzd": 0, "rate": 0.105, "label": "10.5%"},
            {"threshold_nzd": 15_600, "rate": 0.175, "label": "17.5%"},
            {"threshold_nzd": 53_500, "rate": 0.30, "label": "30%"},
            {"threshold_nzd": 78_100, "rate": 0.33, "label": "33%"},
            {"threshold_nzd": 180_000, "rate": 0.39, "label": "39%"},
        ],
        "corporate_rate": 0.28,
        "standard_rate_vat": 0.15,
        "notes": "No capital gains tax (except bright-line test for property). No inheritance tax.",
    },
    "DK": {
        "name": "Denmark",
        "currency": "DKK",
        "tax_types": [
            "income_progressive",
            "corporate",
            "vat_gst",
            "property",
            "capital_gains",
        ],
        "filing_deadline": "May 1 (individuals); June 30 (self-employed)",
        "tax_year": "calendar",
        "corporate_rate": 0.22,
        "standard_rate_vat": 0.25,
        "notes": "Top marginal rate ~55.9% (AM-bidrag + bottom tax + top tax). High-tax high-welfare model.",
    },
}


# ── Currencies (ISO 4217 + digital) ────────────────────────────────────────

CURRENCIES: dict[str, dict[str, Any]] = {
    "USD": {"name": "United States Dollar", "symbol": "$", "decimal_places": 2, "type": "fiat", "country": "US"},
    "EUR": {"name": "Euro", "symbol": "€", "decimal_places": 2, "type": "fiat", "country": "EU"},
    "GBP": {"name": "Pound Sterling", "symbol": "£", "decimal_places": 2, "type": "fiat", "country": "GB"},
    "JPY": {"name": "Japanese Yen", "symbol": "¥", "decimal_places": 0, "type": "fiat", "country": "JP"},
    "CAD": {"name": "Canadian Dollar", "symbol": "C$", "decimal_places": 2, "type": "fiat", "country": "CA"},
    "AUD": {"name": "Australian Dollar", "symbol": "A$", "decimal_places": 2, "type": "fiat", "country": "AU"},
    "CNY": {"name": "Chinese Yuan Renminbi", "symbol": "¥", "decimal_places": 2, "type": "fiat", "country": "CN"},
    "INR": {"name": "Indian Rupee", "symbol": "₹", "decimal_places": 2, "type": "fiat", "country": "IN"},
    "CHF": {"name": "Swiss Franc", "symbol": "CHF", "decimal_places": 2, "type": "fiat", "country": "CH"},
    "SEK": {"name": "Swedish Krona", "symbol": "kr", "decimal_places": 2, "type": "fiat", "country": "SE"},
    "NOK": {"name": "Norwegian Krone", "symbol": "kr", "decimal_places": 2, "type": "fiat", "country": "NO"},
    "RUB": {"name": "Russian Ruble", "symbol": "₽", "decimal_places": 2, "type": "fiat", "country": "RU"},
    "BRL": {"name": "Brazilian Real", "symbol": "R$", "decimal_places": 2, "type": "fiat", "country": "BR"},
    "MXN": {"name": "Mexican Peso", "symbol": "$", "decimal_places": 2, "type": "fiat", "country": "MX"},
    "ZAR": {"name": "South African Rand", "symbol": "R", "decimal_places": 2, "type": "fiat", "country": "ZA"},
    "SGD": {"name": "Singapore Dollar", "symbol": "S$", "decimal_places": 2, "type": "fiat", "country": "SG"},
    "HKD": {"name": "Hong Kong Dollar", "symbol": "HK$", "decimal_places": 2, "type": "fiat", "country": "HK"},
    "NZD": {"name": "New Zealand Dollar", "symbol": "NZ$", "decimal_places": 2, "type": "fiat", "country": "NZ"},
    "AED": {"name": "UAE Dirham", "symbol": "د.إ", "decimal_places": 2, "type": "fiat", "country": "AE"},
    "KRW": {"name": "South Korean Won", "symbol": "₩", "decimal_places": 0, "type": "fiat", "country": "KR"},
    "TRY": {"name": "Turkish Lira", "symbol": "₺", "decimal_places": 2, "type": "fiat", "country": "TR"},
    "SAR": {"name": "Saudi Riyal", "symbol": "ر.س", "decimal_places": 2, "type": "fiat", "country": "SA"},
    "DKK": {"name": "Danish Krone", "symbol": "kr", "decimal_places": 2, "type": "fiat", "country": "DK"},
    "PLN": {"name": "Polish Zloty", "symbol": "zł", "decimal_places": 2, "type": "fiat", "country": "PL"},
    "THB": {"name": "Thai Baht", "symbol": "฿", "decimal_places": 2, "type": "fiat", "country": "TH"},
    "MYR": {"name": "Malaysian Ringgit", "symbol": "RM", "decimal_places": 2, "type": "fiat", "country": "MY"},
    "IDR": {"name": "Indonesian Rupiah", "symbol": "Rp", "decimal_places": 2, "type": "fiat", "country": "ID"},
    "PHP": {"name": "Philippine Peso", "symbol": "₱", "decimal_places": 2, "type": "fiat", "country": "PH"},
    "ARS": {"name": "Argentine Peso", "symbol": "$", "decimal_places": 2, "type": "fiat", "country": "AR"},
    "CLP": {"name": "Chilean Peso", "symbol": "$", "decimal_places": 0, "type": "fiat", "country": "CL"},
    "EGP": {"name": "Egyptian Pound", "symbol": "E£", "decimal_places": 2, "type": "fiat", "country": "EG"},
    "NGN": {"name": "Nigerian Naira", "symbol": "₦", "decimal_places": 2, "type": "fiat", "country": "NG"},
    "VND": {"name": "Vietnamese Dong", "symbol": "₫", "decimal_places": 0, "type": "fiat", "country": "VN"},
    "TWD": {"name": "New Taiwan Dollar", "symbol": "NT$", "decimal_places": 2, "type": "fiat", "country": "TW"},
    "ILS": {"name": "Israeli New Shekel", "symbol": "₪", "decimal_places": 2, "type": "fiat", "country": "IL"},
    "COP": {"name": "Colombian Peso", "symbol": "$", "decimal_places": 2, "type": "fiat", "country": "CO"},
    "CZK": {"name": "Czech Koruna", "symbol": "Kč", "decimal_places": 2, "type": "fiat", "country": "CZ"},
    "HUF": {"name": "Hungarian Forint", "symbol": "Ft", "decimal_places": 2, "type": "fiat", "country": "HU"},
    "RON": {"name": "Romanian Leu", "symbol": "L", "decimal_places": 2, "type": "fiat", "country": "RO"},
    "UAH": {"name": "Ukrainian Hryvnia", "symbol": "₴", "decimal_places": 2, "type": "fiat", "country": "UA"},
    "PEN": {"name": "Peruvian Sol", "symbol": "S/", "decimal_places": 2, "type": "fiat", "country": "PE"},
    "QAR": {"name": "Qatari Riyal", "symbol": "ر.ق", "decimal_places": 2, "type": "fiat", "country": "QA"},
    "KWD": {"name": "Kuwaiti Dinar", "symbol": "د.ك", "decimal_places": 3, "type": "fiat", "country": "KW"},
    "PKR": {"name": "Pakistani Rupee", "symbol": "₨", "decimal_places": 2, "type": "fiat", "country": "PK"},
    "BDT": {"name": "Bangladeshi Taka", "symbol": "৳", "decimal_places": 2, "type": "fiat", "country": "BD"},
    "KES": {"name": "Kenyan Shilling", "symbol": "KSh", "decimal_places": 2, "type": "fiat", "country": "KE"},
    "GHS": {"name": "Ghanaian Cedi", "symbol": "₵", "decimal_places": 2, "type": "fiat", "country": "GH"},
    "MAD": {"name": "Moroccan Dirham", "symbol": "DH", "decimal_places": 2, "type": "fiat", "country": "MA"},
    "DZD": {"name": "Algerian Dinar", "symbol": "دج", "decimal_places": 2, "type": "fiat", "country": "DZ"},
    "BHD": {"name": "Bahraini Dinar", "symbol": ".د.ب", "decimal_places": 3, "type": "fiat", "country": "BH"},
    "OMR": {"name": "Omani Rial", "symbol": "ر.ع.", "decimal_places": 3, "type": "fiat", "country": "OM"},
    "JOD": {"name": "Jordanian Dinar", "symbol": "د.ا", "decimal_places": 3, "type": "fiat", "country": "JO"},
    "ETB": {"name": "Ethiopian Birr", "symbol": "Br", "decimal_places": 2, "type": "fiat", "country": "ET"},
    "VES": {"name": "Venezuelan Bolivar", "symbol": "Bs.", "decimal_places": 2, "type": "fiat", "country": "VE"},
    "UYU": {"name": "Uruguayan Peso", "symbol": "$U", "decimal_places": 2, "type": "fiat", "country": "UY"},
    "LKR": {"name": "Sri Lankan Rupee", "symbol": "Rs", "decimal_places": 2, "type": "fiat", "country": "LK"},
    "NPR": {"name": "Nepalese Rupee", "symbol": "रू", "decimal_places": 2, "type": "fiat", "country": "NP"},
    "ISK": {"name": "Icelandic Krona", "symbol": "kr", "decimal_places": 0, "type": "fiat", "country": "IS"},
    "BGN": {"name": "Bulgarian Lev", "symbol": "лв", "decimal_places": 2, "type": "fiat", "country": "BG"},
    "HRK": {"name": "Croatian Kuna", "symbol": "kn", "decimal_places": 2, "type": "fiat", "country": "HR"},
    "TND": {"name": "Tunisian Dinar", "symbol": "د.ت", "decimal_places": 3, "type": "fiat", "country": "TN"},
    "GEL": {"name": "Georgian Lari", "symbol": "₾", "decimal_places": 2, "type": "fiat", "country": "GE"},
    "KZT": {"name": "Kazakhstani Tenge", "symbol": "₸", "decimal_places": 2, "type": "fiat", "country": "KZ"},
    "MMK": {"name": "Myanmar Kyat", "symbol": "Ks", "decimal_places": 2, "type": "fiat", "country": "MM"},
    "IRR": {"name": "Iranian Rial", "symbol": "﷼", "decimal_places": 2, "type": "fiat", "country": "IR"},
    "PAB": {"name": "Panamanian Balboa", "symbol": "B/.", "decimal_places": 2, "type": "fiat", "country": "PA"},
    "CRC": {"name": "Costa Rican Colon", "symbol": "₡", "decimal_places": 2, "type": "fiat", "country": "CR"},
    "GTQ": {"name": "Guatemalan Quetzal", "symbol": "Q", "decimal_places": 2, "type": "fiat", "country": "GT"},
    "BWP": {"name": "Botswana Pula", "symbol": "P", "decimal_places": 2, "type": "fiat", "country": "BW"},
    "MUR": {"name": "Mauritian Rupee", "symbol": "₨", "decimal_places": 2, "type": "fiat", "country": "MU"},
    "XOF": {"name": "West African CFA Franc", "symbol": "CFA", "decimal_places": 0, "type": "fiat", "country": "WAEMU"},
    "XAF": {
        "name": "Central African CFA Franc",
        "symbol": "FCFA",
        "decimal_places": 0,
        "type": "fiat",
        "country": "CEMAC",
    },
    "XCD": {"name": "East Caribbean Dollar", "symbol": "EC$", "decimal_places": 2, "type": "fiat", "country": "OECS"},
    # ── Digital / crypto ──
    "BTC": {"name": "Bitcoin", "symbol": "₿", "decimal_places": 8, "type": "digital", "issuer": "decentralized"},
    "ETH": {"name": "Ether", "symbol": "Ξ", "decimal_places": 18, "type": "digital", "issuer": "decentralized"},
    "USDT": {
        "name": "Tether (US Dollar tether)",
        "symbol": "₮",
        "decimal_places": 6,
        "type": "digital",
        "issuer": "Tether Limited",
    },
    "USDC": {"name": "USD Coin", "symbol": "$", "decimal_places": 6, "type": "digital", "issuer": "Circle"},
}


# ── Exchange rate sources ──────────────────────────────────────────────────
# Reference sources an agent or operator can consult for live FX. Listed here
# as documentation; the module does NOT fetch live rates.

EXCHANGE_RATE_SOURCES: list[dict[str, str]] = [
    {
        "name": "European Central Bank (ECB) reference rates",
        "url": "https://www.ecb.europa.eu/stats/eurofxref/",
        "frequency": "daily",
        "coverage": "~30 currencies vs EUR",
    },
    {
        "name": "IMF SDR rates",
        "url": "https://www.imf.org/external/np/fin/data/rms_mth.aspx",
        "frequency": "daily",
        "coverage": "SDR basket (USD, EUR, JPY, GBP, CNY, RMB)",
    },
    {
        "name": "Open Exchange Rates",
        "url": "https://openexchangerates.org/",
        "frequency": "hourly",
        "coverage": "200+ currencies, JSON API",
    },
    {
        "name": "Frankfurter (ECB-backed, open API)",
        "url": "https://frankfurter.app/",
        "frequency": "daily",
        "coverage": "~30 currencies, free no-key",
    },
    {
        "name": "exchangerate.host",
        "url": "https://exchangerate.host/",
        "frequency": "daily",
        "coverage": "150+ currencies, free no-key",
    },
]


# ── Tax authorities ────────────────────────────────────────────────────────

TAX_AUTHORITIES: dict[str, dict[str, Any]] = {
    "US": {
        "name": "Internal Revenue Service (IRS)",
        "country": "United States",
        "portal_url": "https://www.irs.gov/",
        "filing_system": "e-file (Form 1040); Modernized e-File (MeF)",
        "phone": "+1-800-829-1040 (individuals)",
        "online_portal": "IRS Free File / Direct Pay / Where's My Refund",
        "vat_authority": "n/a (no federal VAT; states administer sales tax)",
    },
    "GB": {
        "name": "HM Revenue & Customs (HMRC)",
        "country": "United Kingdom",
        "portal_url": "https://www.gov.uk/government/organisations/hm-revenue-customs",
        "filing_system": "Self Assessment (SA302); Making Tax Digital (MTD)",
        "phone": "+44 300 200 3300",
        "online_portal": "Government Gateway / Personal Tax Account",
        "vat_authority": "HMRC (VAT registered businesses)",
    },
    "DE": {
        "name": "Bundeszentralamt fur Steuern (BZSt)",
        "country": "Germany",
        "portal_url": "https://www.bzst.de/EN/",
        "filing_system": "ELSTER (Elektronische Steuererklärung)",
        "phone": "+49 228 406-0",
        "online_portal": "ELSTEROnline / Mein ELSTER",
        "vat_authority": "BZSt (cross-border) + state Finanzämter",
    },
    "FR": {
        "name": "Direction Generale des Finances Publiques (DGFiP)",
        "country": "France",
        "portal_url": "https://www.impots.gouv.fr/",
        "filing_system": "en ligne (impots.gouv.fr); pre-filled return",
        "phone": "+33 1 72 95 20 00",
        "online_portal": "Particulier / Professionnel espace",
        "vat_authority": "DGFiP (TVA)",
    },
    "JP": {
        "name": "National Tax Agency (NTA / Kokuzeicho)",
        "country": "Japan",
        "portal_url": "https://www.nta.go.jp/",
        "filing_system": "e-Tax (kakutei shinkoku); paper accepted",
        "phone": "+81 3 3590 3111",
        "online_portal": "e-Tax G-series portal",
        "vat_authority": "NTA (consumption tax / shohizei)",
    },
    "CA": {
        "name": "Canada Revenue Agency (CRA)",
        "country": "Canada",
        "portal_url": "https://www.canada.ca/en/revenue-agency.html",
        "filing_system": "NETFILE / EFILE; My Account / My Business Account",
        "phone": "+1-800-959-8281 (individuals)",
        "online_portal": "CRA My Account",
        "vat_authority": "CRA (GST/HST)",
    },
    "AU": {
        "name": "Australian Taxation Office (ATO)",
        "country": "Australia",
        "portal_url": "https://www.ato.gov.au/",
        "filing_system": "myTax (online); myDeductions app; SBR via agents",
        "phone": "+61 13 28 61",
        "online_portal": "ATO online services (myGov)",
        "vat_authority": "ATO (GST)",
    },
    "IN": {
        "name": "Income Tax Department (ITD)",
        "country": "India",
        "portal_url": "https://www.incometax.gov.in/",
        "filing_system": "e-Filing 2.0; ITR-1 through ITR-7",
        "phone": "+91-1800-180-1961",
        "online_portal": "e-Filing portal (incometax.gov.in)",
        "vat_authority": "Central Board of Indirect Taxes and Customs (CBIC) — GST",
    },
    "BR": {
        "name": "Receita Federal do Brasil (RFB)",
        "country": "Brazil",
        "portal_url": "https://www.gov.br/receitafederal/",
        "filing_system": "DIRPF (IRPF); e-CAC for IRPJ/CSLL",
        "phone": "+55 61 3412-5000",
        "online_portal": "e-CAC (Centro Virtual de Atendimento)",
        "vat_authority": "RFB (PIS/COFINS federal); state SEFAZ (ICMS); municipal (ISS)",
    },
    "MX": {
        "name": "Servicio de Administracion Tributaria (SAT)",
        "country": "Mexico",
        "portal_url": "https://www.sat.gob.mx/",
        "filing_system": "Declaraciones via SAT portal (CIEEC)",
        "phone": "+52 55 627 22 728",
        "online_portal": "SAT Portal / Buzon Tributario",
        "vat_authority": "SAT (IVA)",
    },
    "ZA": {
        "name": "South African Revenue Service (SARS)",
        "country": "South Africa",
        "portal_url": "https://www.sars.gov.za/",
        "filing_system": "eFiling / MobiApp; IRP6 for provisional",
        "phone": "+27 80 000 7277",
        "online_portal": "SARS eFiling",
        "vat_authority": "SARS (VAT)",
    },
    "KR": {
        "name": "National Tax Service (NTS / Guksecheong)",
        "country": "South Korea",
        "portal_url": "https://www.nts.go.kr/",
        "filing_system": "Hometax (online); paper accepted",
        "phone": "+82 126",
        "online_portal": "Hometax (hometax.go.kr)",
        "vat_authority": "NTS (VAT / Bugase)",
    },
    "SG": {
        "name": "Inland Revenue Authority of Singapore (IRAS)",
        "country": "Singapore",
        "portal_url": "https://www.iras.gov.sg/",
        "filing_system": "myTax Portal; e-Filing B1/B",
        "phone": "+65 6356 8300",
        "online_portal": "myTax Portal (Singpass login)",
        "vat_authority": "IRAS (GST)",
    },
    "RU": {
        "name": "Federal Tax Service of Russia (FNS / Nalog.ru)",
        "country": "Russia",
        "portal_url": "https://www.nalog.gov.ru/",
        "filing_system": "Lichny Kabinet (Personal Account); electronic/signed",
        "phone": "+7 800 222-22-22",
        "online_portal": "Nalog.ru Personal Account",
        "vat_authority": "FNS (NDS / VAT)",
    },
    "AE": {
        "name": "Federal Tax Authority (FTA)",
        "country": "United Arab Emirates",
        "portal_url": "https://tax.gov.ae/",
        "filing_system": "EmaraTax portal; electronic filing only",
        "phone": "+971 4 306 5000",
        "online_portal": "EmaraTax (emaratax.gov.ae)",
        "vat_authority": "FTA (VAT)",
    },
    "SA": {
        "name": "Zakat, Tax and Customs Authority (ZATCA)",
        "country": "Saudi Arabia",
        "portal_url": "https://zatca.gov.sa/",
        "filing_system": "ZATCA e-Services; Tax Return Filing System",
        "phone": "+966 19993",
        "online_portal": "ZATCA e-Services Portal",
        "vat_authority": "ZATCA (VAT)",
    },
    "TR": {
        "name": "Gelir Idaresi Baskanligi (GIB / Revenue Administration)",
        "country": "Turkey",
        "portal_url": "https://www.gib.gov.tr/",
        "filing_system": "Hazir Beyan Sistemi / e-Beyanname",
        "phone": "+90 444 0 189",
        "online_portal": "Internet Vergi Dairesi (IVE)",
        "vat_authority": "GIB (KDV / VAT)",
    },
    "IT": {
        "name": "Agenzia delle Entrate",
        "country": "Italy",
        "portal_url": "https://www.agenziaentrate.gov.it/",
        "filing_system": "Modello 730 / Redditi PF; dichiarazione precompilata",
        "phone": "+39 800 90 96 96",
        "online_portal": "Fisconline / Entratel",
        "vat_authority": "Agenzia delle Entrate (IVA)",
    },
    "ES": {
        "name": "Agencia Estatal de Administracion Tributaria (AEAT)",
        "country": "Spain",
        "portal_url": "https://sede.agenciatributaria.gob.es/",
        "filing_system": "Renta WEB; autoliquidacion for corporations",
        "phone": "+34 91 554 87 70",
        "online_portal": "Sede Electronica AEAT",
        "vat_authority": "AEAT (IVA)",
    },
    "NL": {
        "name": "Belastingdienst",
        "country": "Netherlands",
        "portal_url": "https://www.belastingdienst.nl/",
        "filing_system": "Aangifte (online); DigiD login required",
        "phone": "+31 55 538 53 85",
        "online_portal": "Mijn Belastingdienst (DigiD)",
        "vat_authority": "Belastingdienst (BTW / VAT)",
    },
    "CH": {
        "name": "Eidgenossische Steuerverwaltung (ESTV / AFC)",
        "country": "Switzerland",
        "portal_url": "https://www.estv.admin.ch/",
        "filing_system": "Online tax declaration per canton",
        "phone": "+41 58 462 71 10",
        "online_portal": "Cantonal tax portal (varies by canton)",
        "vat_authority": "ESTV (MWST / VAT)",
    },
    "SE": {
        "name": "Skatteverket",
        "country": "Sweden",
        "portal_url": "https://www.skatteverket.se/",
        "filing_system": "Inkomstdeklaration (e-leg); BankID required",
        "phone": "+46 771 567 567",
        "online_portal": "Mina sidor / Skatteverket",
        "vat_authority": "Skatteverket (moms / VAT)",
    },
    "NO": {
        "name": "Skatteetaten (Norwegian Tax Administration)",
        "country": "Norway",
        "portal_url": "https://www.skatteetaten.no/",
        "filing_system": "Selvangivelsen (altinn.no); pre-filled return",
        "phone": "+47 800 80 000",
        "online_portal": "Altinn / Skatteetaten",
        "vat_authority": "Skatteetaten (MVA / VAT)",
    },
    "IE": {
        "name": "Revenue (Irish Tax and Customs)",
        "country": "Ireland",
        "portal_url": "https://www.revenue.ie/",
        "filing_system": "ROS (Revenue Online Service); Form 11 / Form 12",
        "phone": "+353 1 738 3636",
        "online_portal": "Revenue Online Service (ROS)",
        "vat_authority": "Revenue (VAT)",
    },
    "NZ": {
        "name": "Inland Revenue (IR / Te Tari Taake)",
        "country": "New Zealand",
        "portal_url": "https://www.ird.govt.nz/",
        "filing_system": "myIR (online portal); RealMe login",
        "phone": "+64 4 832 5210",
        "online_portal": "myIR (myir.ird.govt.nz)",
        "vat_authority": "Inland Revenue (GST)",
    },
    "DK": {
        "name": "Skattestyrelsen (Danish Tax Agency)",
        "country": "Denmark",
        "portal_url": "https://www.skat.dk/",
        "filing_system": "TastSelv (online self-service); NemID/MitID login",
        "phone": "+45 72 22 18 18",
        "online_portal": "TastSelv / Skat.dk",
        "vat_authority": "Skattestyrelsen (moms / VAT)",
    },
}


# ── Tax treaties ───────────────────────────────────────────────────────────
# Stored once per unordered pair; accessor normalizes argument order. Each
# entry records the bilateral income/capital tax treaty's signature year and
# the OECD Model basis.

TAX_TREATIES: dict[tuple[str, str], dict[str, Any]] = {
    ("US", "GB"): {
        "countries": ("US", "GB"),
        "signed_year": 2001,
        "protocol_year": 2002,
        "type": "income_and_capital_gains",
        "status": "in_force",
        "model": "OECD Model 2017",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("US", "DE"): {
        "countries": ("US", "DE"),
        "signed_year": 1989,
        "protocol_year": 2006,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("US", "FR"): {
        "countries": ("US", "FR"),
        "signed_year": 1994,
        "protocol_year": 2009,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("US", "CA"): {
        "countries": ("US", "CA"),
        "signed_year": 1980,
        "protocol_year": 2007,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("US", "JP"): {
        "countries": ("US", "JP"),
        "signed_year": 2003,
        "protocol_year": 2013,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.10,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("US", "AU"): {
        "countries": ("US", "AU"),
        "signed_year": 1982,
        "protocol_year": 2001,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("GB", "DE"): {
        "countries": ("GB", "DE"),
        "signed_year": 1964,
        "protocol_year": 2010,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("GB", "FR"): {
        "countries": ("GB", "FR"),
        "signed_year": 2008,
        "protocol_year": 2009,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("GB", "JP"): {
        "countries": ("GB", "JP"),
        "signed_year": 2006,
        "protocol_year": 2012,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.10,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("GB", "CA"): {
        "countries": ("GB", "CA"),
        "signed_year": 1978,
        "protocol_year": 2002,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("GB", "AU"): {
        "countries": ("GB", "AU"),
        "signed_year": 2003,
        "protocol_year": 2003,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("DE", "FR"): {
        "countries": ("DE", "FR"),
        "signed_year": 1959,
        "protocol_year": 2015,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("DE", "JP"): {
        "countries": ("DE", "JP"),
        "signed_year": 1966,
        "protocol_year": 2016,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
    ("CA", "AU"): {
        "countries": ("CA", "AU"),
        "signed_year": 1999,
        "protocol_year": 2002,
        "type": "income_and_capital",
        "status": "in_force",
        "model": "OECD Model",
        "withholding_dividends": 0.15,
        "withholding_interest": 0.0,
        "withholding_royalties": 0.0,
    },
}


# ── Accessor functions ─────────────────────────────────────────────────────


def _norm_country(country: str) -> str:
    """Normalize a country code to ISO-3166-1 alpha-2 upper-case."""
    return country.strip().upper()


def _norm_code(code: str) -> str:
    """Normalize a currency code to upper-case."""
    return code.strip().upper()


def get_tax_info(country: str, tax_type: str) -> dict[str, Any] | None:
    """Return the tax-type-specific information for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).
        tax_type: Key from :data:`TAX_SYSTEMS` (e.g. ``"income_progressive"``,
            ``"vat_gst"``). Case-insensitive.

    Returns:
        A dict describing how ``tax_type`` is implemented in ``country``,
        with a ``country`` field, the system definition under ``system``, and
        any country-specific fields (brackets, rates) copied at top level.
        Returns ``None`` if the country or tax type is unknown, or if the
        country does not levy that tax type.
    """
    code = _norm_country(country)
    ttype = tax_type.strip().lower()
    country_data = TAX_DATA.get(code)
    if country_data is None:
        return None
    system = TAX_SYSTEMS.get(ttype)
    if system is None:
        return None
    if ttype not in country_data.get("tax_types", []):
        return None

    result: dict[str, Any] = {
        "country": code,
        "country_name": country_data["name"],
        "currency": country_data["currency"],
        "tax_type": ttype,
        "system": system,
        "filing_deadline": country_data.get("filing_deadline"),
    }

    # Surface the most relevant country-specific field per tax type.
    if ttype in ("income_progressive", "income_flat", "income_regressive"):
        brackets = country_data.get("income_brackets")
        if brackets is not None:
            result["brackets"] = brackets
        result["details"] = {"standard_deduction_usd": country_data.get("standard_deduction_usd")}
    elif ttype == "corporate":
        result["rate"] = country_data.get("corporate_rate")
        result["details"] = {"solidarity_surcharge": country_data.get("solidarity_surcharge")}
    elif ttype == "vat_gst":
        result["standard_rate"] = country_data.get("standard_rate_vat")
        result["rate"] = country_data.get("standard_rate_vat")
    elif ttype == "digital_services":
        result["rate"] = country_data.get("digital_services_rate")
    elif ttype == "carbon" or ttype in ("property", "wealth", "inheritance", "capital_gains", "sales"):
        result["details"] = {"notes": country_data.get("notes")}
    return result


def get_currency_info(code: str) -> dict[str, Any] | None:
    """Return metadata for an ISO 4217 or digital currency code.

    Args:
        code: Currency code (case-insensitive), e.g. ``"USD"`` or ``"btc"``.

    Returns:
        A copy of the currency record, or ``None`` if unknown.
    """
    record = CURRENCIES.get(_norm_code(code))
    if record is None:
        return None
    return dict(record)


def get_filing_requirements(country: str, entity_type: str) -> dict[str, Any] | None:
    """Return filing requirements for an entity type in a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).
        entity_type: One of ``"individual"``, ``"corporation"``,
            ``"partnership"``, ``"trust"``, ``"nonprofit"`` (case-insensitive).

    Returns:
        A dict with at least ``country``, ``entity_type``, ``deadline``, and
        ``requirements``. Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    country_data = TAX_DATA.get(code)
    if country_data is None:
        return None
    etype = entity_type.strip().lower()

    # Country-specific deadline lines per entity type.
    deadline_map: dict[str, dict[str, str]] = {
        "US": {
            "individual": "April 15 (Form 1040)",
            "corporation": "April 15 (Form 1120, C-corp) / March 15 (Form 1120-S, S-corp)",
            "partnership": "March 15 (Form 1065)",
            "trust": "April 15 (Form 1041)",
            "nonprofit": "May 15 (Form 990)",
        },
        "GB": {
            "individual": "January 31 (Self Assessment, after tax year end Apr 5)",
            "corporation": "12 months after accounting period end (CT600)",
            "partnership": "January 31 (SA, partnership pages)",
            "trust": "January 31 (Trust and Estate SA)",
            "nonprofit": "1 year after period end (Charities SA900)",
        },
        "DE": {
            "individual": "July 31 (Einkommensteuererklärung)",
            "corporation": "electronic via ELSTER; 5 months after FY end (KSt 1)",
            "partnership": "March 31 (Gewerbesteuer, Feststellungserklärung)",
            "trust": "depends on trust classification",
            "nonprofit": "separate Gemeinnützigkeit declaration",
        },
        "FR": {
            "individual": "May/June online (varies by departement)",
            "corporation": "within 3 months of FY end (no. 2065)",
            "partnership": "within 3 months of FY end (no. 2035 / 2065)",
            "trust": "annual declaration (no. 2181)",
            "nonprofit": "annual declaration (no. 2070)",
        },
        "JP": {
            "individual": "March 15 (kakutei shinkoku)",
            "corporation": "within 2 months of FY end (hojin zei)",
            "partnership": "no entity-level income tax; partners file individually",
            "trust": "special trust taxation (tokutei shintaku)",
            "nonprofit": "separate NPO/Hojin filing",
        },
        "CA": {
            "individual": "April 30 (T1); June 15 if self-employed",
            "corporation": "within 6 months of FY end (T2)",
            "partnership": "March 31 (T5013) if >5M revenue or capital",
            "trust": "within 90 days of year end (T3)",
            "nonprofit": "6 months after FY end (T1044 / T3010 for charities)",
        },
        "AU": {
            "individual": "October 31 (self-lodge); May 15 (tax agent)",
            "corporation": "12 months after FY end (Company tax return)",
            "partnership": "October 31 (Partnership tax return)",
            "trust": "within 2 months of FY end (Trust tax return)",
            "nonprofit": "within 60 days of FY end (Annual Information Statement)",
        },
        "IN": {
            "individual": "July 31 (non-audit); October 31 (audit cases)",
            "corporation": "October 31 (audit cases); December 31 (transfer pricing)",
            "partnership": "July 31 (non-audit); October 31 (audit)",
            "trust": "July 31 (non-audit); October 31 (audit)",
            "nonprofit": "October 31 (audit cases)",
        },
        "BR": {
            "individual": "April 30 (DIRPF)",
            "corporation": "last business day of July (ECF); monthly PIS/COFINS by 25th",
            "partnership": "individual partners file DIRPF by April 30",
            "trust": "treated per beneficiary; April 30 deadline",
            "nonprofit": "March 31 (DIPJ, annual)",
        },
        "MX": {
            "individual": "April 30 (annual declaracion ISR)",
            "corporation": "April 30 (annual); monthly provisional by 17th",
            "partnership": "partners taxed individually; April 30 annual",
            "trust": "annual information return per SAT rules",
            "nonprofit": "April 30 (annual return if required)",
        },
        "ZA": {
            "individual": "November 23 (eFiling, non-provisional); January 31 (provisional if registered)",
            "corporation": "12 months after FY end (ITR14)",
            "partnership": "partners file individually per above",
            "trust": "within 12 months of FY end (ITR12T)",
            "nonprofit": "12 months after FY end (IT12EI)",
        },
        "KR": {
            "individual": "May 5 (final return)",
            "corporation": "within 3 months of FY end (Beobin Se)",
            "partnership": "partners taxed individually; May 5",
            "trust": "special trust taxation rules apply",
            "nonprofit": "within 3 months of FY end",
        },
        "SG": {
            "individual": "April 15 (paper); April 18 (e-file)",
            "corporation": "November 30 (Form C-S/C); December 15 (e-file extension)",
            "partnership": "April 15 (Form P); individual partners file by April 18",
            "trust": "April 18 (e-file)",
            "nonprofit": "annual return per IRAS rules",
        },
        "RU": {
            "individual": "April 30 (3-NDFL)",
            "corporation": "March 28 (annual return); monthly advance by 28th",
            "partnership": "each partner taxed individually; April 30",
            "trust": "per beneficiary tax regime",
            "nonprofit": "March 28 (annual return)",
        },
        "AE": {
            "individual": "n/a (no personal income tax)",
            "corporation": "9 months after fiscal year end (CT return)",
            "partnership": "no entity-level tax",
            "trust": "treaty-dependent; consult FTA",
            "nonprofit": "9 months after FY end if subject to CIT",
        },
        "SA": {
            "individual": "n/a (no personal income tax)",
            "corporation": "120 days after FY end (ZTKA income tax)",
            "partnership": "no entity-level tax",
            "trust": "120 days after FY end (if taxable)",
            "nonprofit": "120 days after FY end (if subject to tax)",
        },
        "TR": {
            "individual": "March 31 (annual income return)",
            "corporation": "April 30 (annual CIT return); quarterly provisional",
            "partnership": "partners taxed individually; March 31",
            "trust": "per beneficiary tax regime",
            "nonprofit": "April 30 (annual CIT return if applicable)",
        },
        "IT": {
            "individual": "November 30 (Modello 730); September 30 (Redditi PF)",
            "corporation": "within 11 months of FY end (Modello Redditi SC)",
            "partnership": "within 11 months of FY end",
            "trust": "within 11 months of FY end",
            "nonprofit": "within 11 months of FY end",
        },
        "ES": {
            "individual": "June 30 (Renta/IRPF)",
            "corporation": "within 6 months and 25 days of FY end (Impuesto sobre Sociedades)",
            "partnership": "within 6 months and 25 days of FY end",
            "trust": "annual information return",
            "nonprofit": "within 6 months and 25 days of FY end",
        },
        "NL": {
            "individual": "May 1 (preliminary); September 1 (final extension)",
            "corporation": "6 months after FY end (VpB)",
            "partnership": "partners taxed individually; May 1 / September 1",
            "trust": "6 months after FY end (if taxable)",
            "nonprofit": "6 months after FY end (VpB if applicable)",
        },
        "CH": {
            "individual": "March 31 (cantonal, extension to September 30)",
            "corporation": "varies by canton; typically 6 months after FY end",
            "partnership": "partners taxed individually",
            "trust": "per canton rules",
            "nonprofit": "varies by canton (non-Gemeinnutzigkeit)",
        },
        "SE": {
            "individual": "May 2 (inkomstdeklaration)",
            "corporation": "6 months after FY end (Inkomstskatt 2)",
            "partnership": "partners taxed individually; May 2",
            "trust": "per beneficiary tax regime",
            "nonprofit": "6 months after FY end",
        },
        "NO": {
            "individual": "April 30 (selvangivelsen)",
            "corporation": "May 31 (Skattemelding for naeringsdrivende)",
            "partnership": "partners taxed individually; April 30",
            "trust": "May 31 (if taxable)",
            "nonprofit": "May 31 (if taxable)",
        },
        "IE": {
            "individual": "October 31 (ROS); November 15 (paper)",
            "corporation": "9 months after accounting period end plus 23 days",
            "partnership": "October 31 (ROS)",
            "trust": "October 31 (ROS)",
            "nonprofit": "9 months after AP end",
        },
        "NZ": {
            "individual": "July 7 (IR3); March 31 (tax agent extension)",
            "corporation": "31 March (IR4, tax agent extension to March 31 next year)",
            "partnership": "July 7 (IR7)",
            "trust": "July 7 (IR6)",
            "nonprofit": "various; generally within 4 months of balance date",
        },
        "DK": {
            "individual": "May 1 (individuals); July 1 (self-employed extension)",
            "corporation": "6 months after FY end (Selskabsselvangivelse)",
            "partnership": "partners taxed individually; May 1",
            "trust": "6 months after FY end",
            "nonprofit": "6 months after FY end (if taxable)",
        },
    }

    country_deadlines = deadline_map.get(code, {})
    deadline = country_deadlines.get(etype, country_data.get("filing_deadline", ""))

    requirements_map: dict[str, list[str]] = {
        "individual": [
            "Report worldwide income if resident; territorial rules vary by country.",
            "Maintain records for the statutory retention period (typically 5-7 years).",
            "Claim deductions/credits supported by documentation.",
        ],
        "corporation": [
            "Prepare financial statements per local GAAP or IFRS.",
            "Compute taxable income with adjustments for non-deductibles.",
            "File annual corporate return and pay balance due by deadline.",
        ],
        "partnership": [
            "File information return (partnership generally not taxed at entity level).",
            "Issue partner Schedule K-1 / equivalent profit-allocation statements.",
            "Each partner reports their share on their own return.",
        ],
        "trust": [
            "Fiduciary files the trust tax return.",
            "Distributions carry tax characteristics to beneficiaries.",
            "Beneficiaries report income received on personal returns.",
        ],
        "nonprofit": [
            "Maintain tax-exempt status by filing annual information return.",
            "Document that activities further the exempt purpose.",
            "Report unrelated business taxable income (UBTI) if applicable.",
        ],
    }

    return {
        "country": code,
        "entity_type": etype,
        "deadline": deadline,
        "requirements": requirements_map.get(
            etype,
            ["Consult the revenue authority for entity-specific requirements."],
        ),
    }


def get_tax_treaty(country_a: str, country_b: str) -> dict[str, Any] | None:
    """Return the bilateral tax treaty between two countries, if any.

    Lookup is symmetric: ``get_tax_treaty("US", "GB")`` and
    ``get_tax_treaty("GB", "US")`` return the same record.

    Args:
        country_a, country_b: ISO-3166-1 alpha-2 codes (case-insensitive).

    Returns:
        The treaty record (with a ``countries`` field normalized so
        ``country_a`` always appears first), or ``None`` if no treaty exists
        between the two jurisdictions.
    """
    a = _norm_country(country_a)
    b = _norm_country(country_b)
    if a == b:
        return None
    # Lookup is symmetric; normalize to a canonical sorted country pair so
    # get_tax_treaty("US","GB") == get_tax_treaty("GB","US") exactly.
    record = TAX_TREATIES.get((a, b)) or TAX_TREATIES.get((b, a))
    if record is None:
        return None
    result = dict(record)
    result["countries"] = tuple(sorted((a, b)))
    return result


def list_countries() -> list[str]:
    """Return the sorted list of ISO-3166-1 alpha-2 codes covered by TAX_DATA."""
    return sorted(TAX_DATA.keys())


def list_currencies() -> list[str]:
    """Return the sorted list of currency codes covered by CURRENCIES."""
    return sorted(CURRENCIES.keys())


TAX_CURRENCY = TAX_DATA


def get_currency_count(code: str) -> int:
    """Return how many TAX_DATA countries use a given currency code."""
    c = code.strip().upper()
    return sum(1 for v in TAX_DATA.values() if v.get("currency") == c)
