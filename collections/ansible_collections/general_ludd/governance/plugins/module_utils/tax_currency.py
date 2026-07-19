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
}


# ── Per-country tax profiles ────────────────────────────────────────────────
# Reference values for the most recent published tax year. The goal is
# structured knowledge of each country's tax *architecture*, not live advice.

TAX_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "currency": "USD",
        "tax_types": [
            "income_progressive", "corporate", "sales", "property",
            "inheritance", "capital_gains", "income_regressive",
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
            "income_progressive", "corporate", "vat_gst", "property",
            "inheritance", "capital_gains", "digital_services", "carbon",
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
            "income_progressive", "corporate", "vat_gst", "property",
            "inheritance", "capital_gains", "carbon",
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
            "income_progressive", "corporate", "vat_gst", "property",
            "wealth", "inheritance", "capital_gains", "digital_services",
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
            "income_progressive", "corporate", "vat_gst", "property",
            "inheritance", "capital_gains",
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
            "income_progressive", "corporate", "vat_gst", "sales",
            "property", "capital_gains", "carbon",
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
            "income_progressive", "corporate", "vat_gst", "property",
            "capital_gains", "carbon",
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
    # ── Digital / crypto ──
    "BTC": {"name": "Bitcoin", "symbol": "₿", "decimal_places": 8, "type": "digital", "issuer": "decentralized"},
    "ETH": {"name": "Ether", "symbol": "Ξ", "decimal_places": 18, "type": "digital", "issuer": "decentralized"},
    "USDT": {"name": "Tether (US Dollar tether)", "symbol": "₮", "decimal_places": 6, "type": "digital", "issuer": "Tether Limited"},
    "USDC": {"name": "USD Coin", "symbol": "$", "decimal_places": 6, "type": "digital", "issuer": "Circle"},
}


# ── Exchange rate sources ──────────────────────────────────────────────────
# Reference sources an agent or operator can consult for live FX. Listed here
# as documentation; the module does NOT fetch live rates.

EXCHANGE_RATE_SOURCES: list[dict[str, str]] = [
    {"name": "European Central Bank (ECB) reference rates", "url": "https://www.ecb.europa.eu/stats/eurofxref/", "frequency": "daily", "coverage": "~30 currencies vs EUR"},
    {"name": "IMF SDR rates", "url": "https://www.imf.org/external/np/fin/data/rms_mth.aspx", "frequency": "daily", "coverage": "SDR basket (USD, EUR, JPY, GBP, CNY, RMB)"},
    {"name": "Open Exchange Rates", "url": "https://openexchangerates.org/", "frequency": "hourly", "coverage": "200+ currencies, JSON API"},
    {"name": "Frankfurter (ECB-backed, open API)", "url": "https://frankfurter.app/", "frequency": "daily", "coverage": "~30 currencies, free no-key"},
    {"name": "exchangerate.host", "url": "https://exchangerate.host/", "frequency": "daily", "coverage": "150+ currencies, free no-key"},
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
    elif ttype == "carbon":
        result["details"] = {"notes": country_data.get("notes")}
    elif ttype in ("property", "wealth", "inheritance", "capital_gains", "sales"):
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


def get_filing_requirements(
    country: str, entity_type: str
) -> dict[str, Any] | None:
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
