"""Public finance knowledge module for the governance collection.

Exposes government budgets, procurement processes, public debt instruments,
and sovereign wealth funds as structured knowledge.

Public surface::

    BUDGET_TYPES             tuple of budget-type tokens
    BUDGET_DATA              dict[country_code] -> budget profile
    PROCUREMENT_METHODS      tuple of procurement-method tokens
    PROCUREMENT_RULES        dict[country_code] -> procurement rules
    DEBT_INSTRUMENTS         dict[instrument_type] -> description
    DEBT_DATA                dict[country_code] -> public debt profile
    SOVEREIGN_WEALTH_FUNDS   list[fund dict]

    get_budget_info(country_code)              -> dict | None
    get_procurement_rules(country_code)        -> dict | None
    get_debt_info(country_code)                -> dict | None
    get_swf_by_name(name)                      -> dict | None
    get_swfs_by_country(country_code)          -> list[dict]
    get_swfs_by_type(fund_type)                -> list[dict]
    list_countries_with_budgets()              -> list[str]
    list_swf_countries()                       -> list[str]
    procurement_by_method(country, method)     -> dict | None
    debt_by_holder(country)                    -> dict | None
    debt_to_gdp(country)                       -> float | None
"""

from __future__ import annotations

from typing import Any

BUDGET_TYPES: tuple[str, ...] = (
    "line_item",
    "programme_based",
    "performance_based",
    "zero_based",
    "participatory",
    "gender_responsive",
)

BUDGET_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "currency": "USD",
        "fiscal_year": "October 1 – September 30",
        "budget_type": "programme_based",
        "budget_authority": "Congress (House + Senate appropriations)",
        "budget_process": (
            "President submits budget request (first Monday in February). "
            "Congressional Budget Office (CBO) scores it. House and Senate "
            "Budget Committees pass a budget resolution. Appropriations "
            "committees produce 12 annual spending bills. Continuing "
            "resolutions fund the government if bills are not enacted by "
            "October 1."
        ),
        "revenue_sources": {
            "individual_income_tax": 0.49,
            "payroll_taxes": 0.35,
            "corporate_income_tax": 0.09,
            "excise_and_other": 0.07,
        },
        "expenditure_categories": {
            "social_security": 0.22,
            "health_care_medicare_medicaid": 0.27,
            "defense": 0.13,
            "income_security": 0.08,
            "interest_on_debt": 0.10,
            "other_discretionary": 0.20,
        },
        "debt_ceiling": "statutory_limit_set_by_congress",
        "audit_body": "Government Accountability Office (GAO)",
    },
    "GB": {
        "name": "United Kingdom",
        "currency": "GBP",
        "fiscal_year": "April 6 – April 5",
        "budget_type": "programme_based",
        "budget_authority": "Parliament (House of Commons supply)",
        "budget_process": (
            "Chancellor of the Exchequer presents the Budget (usually "
            "spring) and an Autumn Statement. The Office for Budget "
            "Responsibility (OBR) publishes independent forecasts. "
            "Spending Reviews set multi-year departmental budgets. "
            "Supply Estimates are voted by the House of Commons."
        ),
        "revenue_sources": {
            "income_tax": 0.26,
            "national_insurance": 0.18,
            "vat": 0.17,
            "corporation_tax": 0.09,
            "fuel_duties": 0.05,
            "council_tax": 0.05,
            "other": 0.20,
        },
        "expenditure_categories": {
            "health": 0.18,
            "pensions_welfare": 0.25,
            "education": 0.11,
            "defence": 0.05,
            "interest_on_debt": 0.08,
            "other_public_services": 0.33,
        },
        "audit_body": "National Audit Office (NAO)",
    },
    "DE": {
        "name": "Germany",
        "currency": "EUR",
        "fiscal_year": "calendar year",
        "budget_type": "line_item",
        "budget_authority": "Bundestag (federal parliament)",
        "budget_process": (
            "Ministry of Finance drafts the federal budget. Cabinet "
            "approves the draft in summer. Budget bill goes to the "
            "Bundestag and Bundesrat. Federal budget adopted by "
            "December for the following year. Debt brake "
            "(Schuldenbremse) limits structural deficit to 0.35% "
            "of GDP."
        ),
        "revenue_sources": {
            "income_tax": 0.30,
            "vat": 0.22,
            "social_security_contributions": 0.38,
            "corporate_tax": 0.04,
            "other": 0.06,
        },
        "expenditure_categories": {
            "social_security_pensions": 0.45,
            "health": 0.11,
            "education_research": 0.05,
            "defence": 0.03,
            "interest_on_debt": 0.02,
            "transport_infrastructure": 0.05,
            "other": 0.29,
        },
        "fiscal_rules": ("schuldenbremse_debt_brake", "maastricht_treaty_60_percent"),
        "audit_body": "Bundesrechnungshof (Federal Court of Audit)",
    },
    "FR": {
        "name": "France",
        "currency": "EUR",
        "fiscal_year": "calendar year",
        "budget_type": "programme_based",
        "budget_authority": "Parliament (National Assembly + Senate)",
        "budget_process": (
            "Loi de finances (Finance Act) prepared by Ministry of "
            "Economy and Finance. Submitted to Parliament in October. "
            "Constitutional Council reviews. A multi-year public finance "
            "programming law (LPFP) sets medium-term targets. France "
            "participates in EU budgetary coordination under the "
            "Stability and Growth Pact."
        ),
        "revenue_sources": {
            "vat": 0.19,
            "social_security_contributions": 0.34,
            "income_tax": 0.12,
            "corporate_tax": 0.05,
            "taxe_interieure_consommation": 0.05,
            "other": 0.25,
        },
        "expenditure_categories": {
            "social_protection": 0.42,
            "health": 0.14,
            "education_public_services": 0.10,
            "defence": 0.04,
            "interest_on_debt": 0.03,
            "transport_infrastructure": 0.03,
            "other": 0.24,
        },
        "fiscal_rules": ("stability_and_growth_pact",),
        "audit_body": "Cour des comptes (Court of Accounts)",
    },
    "JP": {
        "name": "Japan",
        "currency": "JPY",
        "fiscal_year": "April 1 – March 31",
        "budget_type": "line_item",
        "budget_authority": "National Diet (House of Representatives + House of Councillors)",
        "budget_process": (
            "Ministry of Finance compiles budget requests from each "
            "ministry. Cabinet approves the draft in December. Diet "
            "deliberates and approves by March 31. Supplementary "
            "budgets are common (typically 2-3 per year). The Fiscal "
            "Structural Reform Act sets deficit-reduction targets."
        ),
        "revenue_sources": {
            "consumption_tax": 0.19,
            "income_tax": 0.18,
            "corporate_tax": 0.14,
            "social_security_contributions": 0.35,
            "other_taxes": 0.08,
            "bond_issuance": 0.06,
        },
        "expenditure_categories": {
            "social_security": 0.33,
            "debt_service": 0.22,
            "local_government_grants": 0.15,
            "public_works": 0.06,
            "defence": 0.05,
            "education_science": 0.05,
            "other": 0.14,
        },
        "fiscal_rules": ("primary_balance_target",),
        "audit_body": "Board of Audit (Kaikei Kensain)",
    },
    "CA": {
        "name": "Canada",
        "currency": "CAD",
        "fiscal_year": "April 1 – March 31",
        "budget_type": "programme_based",
        "budget_authority": "Parliament (House of Commons)",
        "budget_process": (
            "Minister of Finance presents the federal budget (typically "
            "February/March). Budget Implementation Act (BIA) enacts "
            "measures. Main and Supplementary Estimates are tabled for "
            "parliamentary approval. The Parliamentary Budget Officer "
            "(PBO) provides independent analysis."
        ),
        "revenue_sources": {
            "personal_income_tax": 0.47,
            "corporate_income_tax": 0.16,
            "gst": 0.12,
            "payroll_premiums": 0.09,
            "other": 0.16,
        },
        "expenditure_categories": {
            "transfers_to_persons": 0.29,
            "transfers_to_provinces": 0.18,
            "direct_program_spending": 0.32,
            "public_debt_charges": 0.10,
            "defence": 0.07,
            "other": 0.04,
        },
        "fiscal_rules": ("federal_fiscal_anchor", "debt_to_gdp_declining_target"),
        "audit_body": "Office of the Auditor General (OAG)",
    },
    "AU": {
        "name": "Australia",
        "currency": "AUD",
        "fiscal_year": "July 1 – June 30",
        "budget_type": "programme_based",
        "budget_authority": "Parliament (House of Representatives + Senate)",
        "budget_process": (
            "Treasurer presents the Federal Budget in May. Budget "
            "papers tabled in Parliament. Senate Estimates committees "
            "scrutinize departmental spending. The Charter of Budget "
            "Honesty Act 1998 requires intergenerational reports, "
            "mid-year updates, and pre-election economic and fiscal "
            "outlooks."
        ),
        "revenue_sources": {
            "personal_income_tax": 0.47,
            "corporate_tax": 0.20,
            "gst": 0.13,
            "superannuation_taxes": 0.05,
            "excise_and_customs": 0.09,
            "non_tax_revenue": 0.06,
        },
        "expenditure_categories": {
            "social_security_welfare": 0.36,
            "health": 0.16,
            "education": 0.07,
            "defence": 0.06,
            "interest_on_debt": 0.04,
            "grants_to_states": 0.15,
            "other": 0.16,
        },
        "fiscal_rules": ("charter_of_budget_honesty", "medium_term_fiscal_strategy"),
        "audit_body": "Australian National Audit Office (ANAO)",
    },
}

PROCUREMENT_METHODS: tuple[str, ...] = (
    "open_tender",
    "restricted_tender",
    "competitive_dialogue",
    "negotiated_procedure",
    "request_for_proposals",
    "framework_agreement",
    "direct_award",
    "electronic_auction",
    "two_stage_tender",
)

PROCUREMENT_RULES: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "legal_framework": "Federal Acquisition Regulation (FAR)",
        "governing_body": "General Services Administration (GSA) / Federal Acquisition Regulatory Council",
        "thresholds": {
            "micro_purchase": {"usd": 10_000},
            "simplified_acquisition": {"usd": 250_000},
            "full_and_open": {"usd": "> 250,000"},
        },
        "preferred_methods": ("open_tender", "framework_agreement", "request_for_proposals"),
        "preferences": (
            "Small Business Set-Asides",
            "Buy American Act preferences",
            "Service-Disabled Veteran-Owned",
            "Women-Owned Small Business",
            "HUBZone program",
        ),
        "dispute_resolution": "Government Accountability Office (GAO) bid protests; Court of Federal Claims",
        "transparency": "SAM.gov (System for Award Management); USASpending.gov",
    },
    "GB": {
        "name": "United Kingdom",
        "legal_framework": "Procurement Act 2023 (replacing EU-derived rules)",
        "governing_body": "Cabinet Office / Government Commercial Function",
        "thresholds": {
            "low_value": {"gbp": 12_000},
            "below_wto_gpa": {"gbp": "varies by sector"},
            "wto_gpa_works": {"gbp": 5_372_000},
            "wto_gpa_goods_services": {"gbp": 139_000},
        },
        "preferred_methods": ("open_tender", "competitive_dialogue", "framework_agreement"),
        "preferences": (
            "Social Value Act 2012 considerations",
            "SME participation targets (government target: 33% of procurement spend)",
            "Prompt Payment Code",
        ),
        "dispute_resolution": "High Court (Technology and Construction Court); Public Procurement Review Service",
        "transparency": "Contracts Finder (for contracts >£12,000); Find a Tender (for above-threshold notices)",
    },
    "DE": {
        "name": "Germany",
        "legal_framework": "Gesetz gegen Wettbewerbsbeschraenkungen (GWB) Part 4 + Vergabeverordnung (VgV)",
        "governing_body": "Federal Ministry for Economic Affairs and Climate Action",
        "thresholds": {
            "national": {"eur": "varies by sector (typically EUR 100,000)"},
            "eu_wide_works": {"eur": 5_538_000},
            "eu_wide_goods_services": {"eur": 143_000},
        },
        "preferred_methods": ("open_tender", "restricted_tender", "negotiated_procedure"),
        "preferences": (
            "Mittelstand (SME) splitting of contracts into lots",
            "Sustainability and environmental criteria",
            "Compliance with collective bargaining agreements (Tariftreue)",
        ),
        "dispute_resolution": "Vergabekammern (procurement tribunals); Higher Regional Court",
        "transparency": "Bundesanzeiger (Federal Gazette); TED (Tenders Electronic Daily, for EU-wide)",
    },
    "FR": {
        "name": "France",
        "legal_framework": "Code de la commande publique (Public Procurement Code, 2019)",
        "governing_body": "Direction des Affaires Juridiques (DAJ, Ministry of Economy)",
        "thresholds": {
            "direct_award": {"eur": 40_000},
            "adapted_procedure_map": {"eur": "40,000 – 143,000 (goods/services) or 5,538,000 (works)"},
            "eu_wide_works": {"eur": 5_538_000},
            "eu_wide_goods_services": {"eur": 143_000},
        },
        "preferred_methods": ("open_tender", "competitive_dialogue", "negotiated_procedure"),
        "preferences": (
            "SME access (allotissement, mandatory contract splitting)",
            "Social and environmental clauses (achats responsables)",
            "Local economic impact",
        ),
        "dispute_resolution": "Administrative Tribunal (tribunal administratif); Conseil d'Etat on appeal",
        "transparency": "BOAMP (Bulletin Officiel des Annonces des Marches Publics); TED",
    },
    "JP": {
        "name": "Japan",
        "legal_framework": "Public Accounting Act + Local Autonomy Act",
        "governing_body": "Ministry of Finance / Ministry of Internal Affairs and Communications",
        "thresholds": {
            "wto_gpa_works": {"jpy": 720_000_000},
            "wto_gpa_goods": {"jpy": 10_000_000},
            "wto_gpa_services": {"jpy": 10_000_000},
        },
        "preferred_methods": ("open_tender", "restricted_tender", "direct_award"),
        "preferences": (
            "SME set-asides",
            "Disaster-area preference",
            "Quality and low-carbon criteria",
        ),
        "dispute_resolution": "Government Procurement Review Board (Office of Government Procurement Review)",
        "transparency": "Kampo (Official Gazette); website notices",
    },
}

DEBT_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "treasury_bill": {
        "description": "Short-term government debt security with maturity of one year or less. Sold at a discount to face value; the return is the difference between purchase price and redemption value.",
        "typical_maturity": "4, 8, 13, 17, 26, or 52 weeks",
        "risk_level": "lowest_risk",
        "issuing_countries": ("US", "GB", "DE", "FR", "JP"),
    },
    "treasury_note": {
        "description": "Medium-term government debt security with a fixed interest rate and maturity between 2 and 10 years. Pays semi-annual coupon interest.",
        "typical_maturity": "2, 3, 5, 7, or 10 years",
        "risk_level": "low_risk",
        "issuing_countries": ("US", "GB", "CA"),
    },
    "government_bond": {
        "description": "Long-term government debt security with maturity typically greater than 10 years. Fixed-rate coupon payments; benchmark for a country's sovereign yield curve.",
        "typical_maturity": "10, 20, 30, or 50 years",
        "risk_level": "low_to_moderate_risk",
        "issuing_countries": ("US", "GB", "DE", "FR", "JP", "CA", "AU"),
    },
    "sovereign_green_bond": {
        "description": "A government bond where proceeds are exclusively used to finance or re-finance eligible green projects (renewable energy, clean transport, climate adaptation).",
        "typical_maturity": "5–30 years",
        "risk_level": "low_risk",
        "issuing_countries": ("GB", "FR", "DE"),
    },
    "inflation_linked_bond": {
        "description": "Government bond whose principal and interest payments are indexed to an inflation measure (e.g. CPI). Protects investors from purchasing-power erosion.",
        "typical_maturity": "5, 10, 30 years",
        "risk_level": "low_risk",
        "issuing_countries": ("US", "GB", "FR", "JP", "CA", "AU"),
    },
    "zero_coupon_bond": {
        "description": "A bond sold at a deep discount to face value with no periodic coupon payments. The full face value is repaid at maturity. The implied interest is the accretion of the discount.",
        "typical_maturity": "10+ years",
        "risk_level": "moderate_risk",
        "issuing_countries": ("US", "DE"),
    },
    "sukuk": {
        "description": "Sharia-compliant financial certificate similar to a bond, representing ownership in an underlying asset or project. Returns come from asset performance, not interest.",
        "typical_maturity": "3–10 years",
        "risk_level": "moderate_risk",
        "issuing_countries": ("SA", "AE", "MY", "ID"),
    },
    "samurai_bond": {
        "description": "A yen-denominated bond issued in Japan by a non-Japanese entity. The Japanese government also issues domestic bonds to the market.",
        "typical_maturity": "3–20 years",
        "risk_level": "low_to_moderate_risk",
        "issuing_countries": ("JP",),
    },
}

DEBT_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "currency": "USD",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 123.3,
        "net_debt_to_gdp_pct": 98.2,
        "main_instruments": ("treasury_bill", "treasury_note", "government_bond", "inflation_linked_bond"),
        "largest_foreign_holders": ("Japan", "China", "United Kingdom", "Luxembourg", "Canada"),
        "credit_rating": {
            "sp": "AA+",
            "moodys": "Aaa (negative outlook)",
            "fitch": "AA+",
        },
        "debt_management_office": "Bureau of the Fiscal Service (Treasury)",
        "interest_cost_pct_revenue": 14.0,
    },
    "GB": {
        "name": "United Kingdom",
        "currency": "GBP",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 97.6,
        "net_debt_to_gdp_pct": 88.8,
        "main_instruments": ("treasury_bill", "government_bond", "inflation_linked_bond", "sovereign_green_bond"),
        "largest_foreign_holders": ("United States", "Japan", "Ireland", "China", "Luxembourg"),
        "credit_rating": {
            "sp": "AA",
            "moodys": "Aa3",
            "fitch": "AA-",
        },
        "debt_management_office": "UK Debt Management Office (DMO)",
        "interest_cost_pct_revenue": 10.0,
    },
    "DE": {
        "name": "Germany",
        "currency": "EUR",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 63.6,
        "net_debt_to_gdp_pct": 45.0,
        "main_instruments": ("treasury_bill", "government_bond", "inflation_linked_bond", "sovereign_green_bond"),
        "largest_foreign_holders": ("Luxembourg", "China", "Japan", "Netherlands", "France"),
        "credit_rating": {
            "sp": "AAA",
            "moodys": "Aaa",
            "fitch": "AAA",
        },
        "debt_management_office": "Bundesrepublik Deutschland Finanzagentur (German Finance Agency)",
        "interest_cost_pct_revenue": 4.8,
    },
    "FR": {
        "name": "France",
        "currency": "EUR",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 111.6,
        "net_debt_to_gdp_pct": 101.1,
        "main_instruments": ("treasury_bill", "government_bond", "inflation_linked_bond", "sovereign_green_bond"),
        "largest_foreign_holders": ("Luxembourg", "Japan", "United Kingdom", "Netherlands", "Germany"),
        "credit_rating": {
            "sp": "AA",
            "moodys": "Aa2",
            "fitch": "AA-",
        },
        "debt_management_office": "Agence France Tresor (AFT)",
        "interest_cost_pct_revenue": 7.8,
    },
    "JP": {
        "name": "Japan",
        "currency": "JPY",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 255.2,
        "net_debt_to_gdp_pct": 160.8,
        "main_instruments": ("treasury_bill", "government_bond", "samurai_bond"),
        "largest_foreign_holders": ("China", "United States", "United Kingdom", "Luxembourg", "Singapore"),
        "credit_rating": {
            "sp": "A+",
            "moodys": "A1",
            "fitch": "A",
        },
        "debt_management_office": "Ministry of Finance (Financial Bureau)",
        "interest_cost_pct_revenue": 22.0,
    },
    "CA": {
        "name": "Canada",
        "currency": "CAD",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 49.7,
        "net_debt_to_gdp_pct": 14.8,
        "main_instruments": ("treasury_bill", "treasury_note", "government_bond", "inflation_linked_bond"),
        "largest_foreign_holders": ("United States", "United Kingdom", "Japan", "China", "Luxembourg"),
        "credit_rating": {
            "sp": "AAA",
            "moodys": "Aaa",
            "fitch": "AA+",
        },
        "debt_management_office": "Bank of Canada (fiscal agent) / Department of Finance",
        "interest_cost_pct_revenue": 8.9,
    },
    "AU": {
        "name": "Australia",
        "currency": "AUD",
        "as_of": "2024 reference",
        "gross_debt_to_gdp_pct": 36.0,
        "net_debt_to_gdp_pct": 22.4,
        "main_instruments": ("treasury_bill", "government_bond", "inflation_linked_bond"),
        "largest_foreign_holders": ("Japan", "United States", "United Kingdom", "China", "Singapore"),
        "credit_rating": {
            "sp": "AAA",
            "moodys": "Aaa",
            "fitch": "AAA",
        },
        "debt_management_office": "Australian Office of Financial Management (AOFM)",
        "interest_cost_pct_revenue": 4.0,
    },
}

SOVEREIGN_WEALTH_FUNDS: list[dict[str, Any]] = [
    {
        "name": "Government Pension Fund Global (GPFG)",
        "short_name": "Oljefondet (Oil Fund)",
        "country": "NO",
        "country_name": "Norway",
        "founded": 1990,
        "assets_usd_bn": 1_700,
        "type": "sovereign_pension_fund",
        "funding_source": "petroleum_revenue",
        "mandate": "Long-term management of Norway's petroleum wealth for future generations. Invests globally across equities, fixed income, and real estate.",
        "benchmark": "FTSE Global All Cap + Bloomberg Global Aggregate",
        "governance": "Norges Bank Investment Management (NBIM) under mandate from the Ministry of Finance.",
        "transparency_rating": "high",
        "ethical_guidelines": "Ethical Council screens for ESG violations; exclusions on coal, tobacco, and certain weapons.",
    },
    {
        "name": "China Investment Corporation (CIC)",
        "short_name": "CIC",
        "country": "CN",
        "country_name": "China",
        "founded": 2007,
        "assets_usd_bn": 1_350,
        "type": "sovereign_wealth_fund",
        "funding_source": "foreign_exchange_reserves",
        "mandate": "Diversify and maximize returns on China's foreign exchange reserves through global investment across asset classes.",
        "benchmark": "Custom multi-asset reference portfolio",
        "governance": "Reports to the State Council of the PRC.",
        "transparency_rating": "medium",
        "ethical_guidelines": "UNPRI signatory since 2015.",
    },
    {
        "name": "Abu Dhabi Investment Authority (ADIA)",
        "short_name": "ADIA",
        "country": "AE",
        "country_name": "United Arab Emirates",
        "founded": 1976,
        "assets_usd_bn": 993,
        "type": "sovereign_wealth_fund",
        "funding_source": "petroleum_revenue",
        "mandate": "Invest the Emirate of Abu Dhabi's surplus hydrocarbon revenues for long-term wealth preservation and capital appreciation.",
        "benchmark": "Proprietary multi-asset reference portfolio",
        "governance": "Board of Directors chaired by the Ruler of Abu Dhabi; management delegated to ADIA's managing director.",
        "transparency_rating": "medium",
        "ethical_guidelines": "UNPRI signatory.",
    },
    {
        "name": "Kuwait Investment Authority (KIA)",
        "short_name": "KIA",
        "country": "KW",
        "country_name": "Kuwait",
        "founded": 1953,
        "assets_usd_bn": 800,
        "type": "sovereign_wealth_fund",
        "funding_source": "petroleum_revenue",
        "mandate": "Manage Kuwait's General Reserve Fund and Future Generations Fund. The latter receives 10% of annual oil revenue by law and may not be drawn upon without special legislation.",
        "benchmark": "Multi-asset reference portfolio",
        "governance": "Board of Directors chaired by the Minister of Finance; independently managed.",
        "transparency_rating": "medium",
        "ethical_guidelines": "Generally conservative; avoids highly speculative investments.",
    },
    {
        "name": "GIC Private Limited",
        "short_name": "GIC",
        "country": "SG",
        "country_name": "Singapore",
        "founded": 1981,
        "assets_usd_bn": 770,
        "type": "sovereign_wealth_fund",
        "funding_source": "foreign_exchange_reserves_and_budget_surpluses",
        "mandate": "Preserve and enhance the international purchasing power of Singapore's reserves, with a long-term (20-year) investment horizon.",
        "benchmark": "GIC Reference Portfolio (65% global equities, 35% global bonds)",
        "governance": "Wholly owned by the Government of Singapore; managed as a private company under the Companies Act. Board includes ministers and independent experts.",
        "transparency_rating": "high",
        "ethical_guidelines": "UNPRI signatory; sustainability integrated into investment process.",
    },
    {
        "name": "Temasek Holdings",
        "short_name": "Temasek",
        "country": "SG",
        "country_name": "Singapore",
        "founded": 1974,
        "assets_usd_bn": 290,
        "type": "strategic_holding_company",
        "funding_source": "state_owned_enterprise_equity",
        "mandate": "Active investor and shareholder in Singaporean and global companies. Focus on long-term value creation and portfolio transformation.",
        "benchmark": "Internal return targets; publishes total shareholder return annually.",
        "governance": "Owned by the Government of Singapore; overseen by independent board; publishes annual Temasek Review.",
        "transparency_rating": "high",
        "ethical_guidelines": "UNPRI signatory; publishes sustainability report; net-zero committed by 2050.",
    },
    {
        "name": "Public Investment Fund (PIF)",
        "short_name": "PIF",
        "country": "SA",
        "country_name": "Saudi Arabia",
        "founded": 1971,
        "assets_usd_bn": 925,
        "type": "sovereign_wealth_fund",
        "funding_source": "petroleum_revenue_and_asset_transfers",
        "mandate": "Drive Saudi Vision 2030 economic transformation; invest domestically and globally across sectors to diversify the economy away from oil.",
        "benchmark": "Multi-strategy domestic and international portfolio",
        "governance": "Board chaired by the Crown Prince; management reports to the Council of Economic and Development Affairs.",
        "transparency_rating": "medium",
        "ethical_guidelines": "UNPRI signatory.",
    },
    {
        "name": "National Wealth Fund (NWF)",
        "short_name": "NWF (Russia)",
        "country": "RU",
        "country_name": "Russia",
        "founded": 2008,
        "assets_usd_bn": 133,
        "type": "sovereign_wealth_fund",
        "funding_source": "petroleum_revenue",
        "mandate": "Co-finance voluntary pension savings and cover the Pension Fund deficit; serves as a fiscal buffer for oil-price shocks.",
        "benchmark": "Liquid foreign-currency assets (IMF SDR basket currencies)",
        "governance": "Ministry of Finance manages; Bank of Russia is operational agent.",
        "transparency_rating": "low",
        "ethical_guidelines": "Constrained by sanctions regimes on permissible counterparties and assets.",
    },
    {
        "name": "Future Fund",
        "short_name": "Future Fund (AU)",
        "country": "AU",
        "country_name": "Australia",
        "founded": 2006,
        "assets_usd_bn": 145,
        "type": "sovereign_pension_fund",
        "funding_source": "budget_surpluses_and_asset_transfers",
        "mandate": "Strengthen the Commonwealth's long-term financial position by covering unfunded superannuation liabilities for public servants.",
        "benchmark": "CPI + 4-5% target return over rolling 10-year periods",
        "governance": "Future Fund Board of Guardians; managed by Future Fund Management Agency. Independent of government.",
        "transparency_rating": "high",
        "ethical_guidelines": "ESG integrated; publishes annual responsible-investment report.",
    },
    {
        "name": "Alaska Permanent Fund (APF)",
        "short_name": "APF",
        "country": "US",
        "country_name": "United States (Alaska)",
        "founded": 1976,
        "assets_usd_bn": 80,
        "type": "subnational_sovereign_fund",
        "funding_source": "petroleum_revenue_and_investment_returns",
        "mandate": "Invest at least 25% of Alaska's mineral royalties; pay an annual dividend to every qualified Alaskan resident (Permanent Fund Dividend).",
        "benchmark": "Multi-asset reference portfolio",
        "governance": "Alaska Permanent Fund Corporation (APFC); Board of Trustees appointed by the Governor.",
        "transparency_rating": "high",
        "ethical_guidelines": "Publishes holdings and annual report; sustainability considerations in manager selection.",
    },
]


def _norm_country(country: str) -> str:
    return country.strip().upper()


def get_budget_info(country_code: str) -> dict[str, Any] | None:
    """Return the budget profile for a country."""
    code = _norm_country(country_code)
    return BUDGET_DATA.get(code)


def get_procurement_rules(country_code: str) -> dict[str, Any] | None:
    """Return the procurement rules for a country."""
    code = _norm_country(country_code)
    return PROCUREMENT_RULES.get(code)


def get_debt_info(country_code: str) -> dict[str, Any] | None:
    """Return the public debt profile for a country."""
    code = _norm_country(country_code)
    return DEBT_DATA.get(code)


def get_swf_by_name(name: str) -> dict[str, Any] | None:
    """Look up a sovereign wealth fund by name or short_name (case-insensitive)."""
    q = name.strip().lower()
    for fund in SOVEREIGN_WEALTH_FUNDS:
        if fund["name"].lower() == q:
            return fund
        if fund.get("short_name", "").lower() == q:
            return fund
    return None


def get_swfs_by_country(country_code: str) -> list[dict[str, Any]]:
    """Return all sovereign wealth funds for a given country code."""
    code = _norm_country(country_code)
    return [f for f in SOVEREIGN_WEALTH_FUNDS if f["country"] == code]


def get_swfs_by_type(fund_type: str) -> list[dict[str, Any]]:
    """Return all sovereign wealth funds of a given type."""
    ft = fund_type.strip().lower().replace(" ", "_")
    return [f for f in SOVEREIGN_WEALTH_FUNDS if f["type"] == ft]


def list_countries_with_budgets() -> list[str]:
    """Return sorted country codes covered by BUDGET_DATA."""
    return sorted(BUDGET_DATA.keys())


def list_swf_countries() -> list[str]:
    """Return sorted unique country codes with sovereign wealth funds."""
    return sorted({f["country"] for f in SOVEREIGN_WEALTH_FUNDS})


def procurement_by_method(country_code: str, method: str) -> dict[str, Any] | None:
    """Check if a country supports a given procurement method.

    Returns procurement rules plus the method, or None if not found.
    """
    code = _norm_country(country_code)
    rules = PROCUREMENT_RULES.get(code)
    if rules is None:
        return None
    m = method.strip().lower()
    if m not in rules.get("preferred_methods", ()):
        return None
    result = dict(rules)
    result["matched_method"] = m
    return result


def debt_by_holder(country_code: str) -> dict[str, Any] | None:
    """Return the top foreign holders of debt for a country."""
    code = _norm_country(country_code)
    info = DEBT_DATA.get(code)
    if info is None:
        return None
    return {
        "country": code,
        "country_name": info["name"],
        "gross_debt_to_gdp_pct": info["gross_debt_to_gdp_pct"],
        "largest_foreign_holders": info.get("largest_foreign_holders", ()),
    }


def debt_to_gdp(country_code: str) -> float | None:
    """Return gross debt-to-GDP ratio for a country."""
    code = _norm_country(country_code)
    info = DEBT_DATA.get(code)
    if info is None:
        return None
    return info["gross_debt_to_gdp_pct"]


COUNTRY_BUDGETS = BUDGET_DATA


def lookup_budget(country: str) -> dict[str, Any] | None:
    code = _norm_country(country)
    data = get_budget_info(code)
    if data is None:
        return None
    result: dict[str, Any] = dict(data)
    result["found"] = True
    result["country"] = code
    return result


def lookup_sovereign_debt(country: str) -> dict[str, Any] | None:
    code = _norm_country(country)
    data = get_debt_info(code)
    if data is None:
        return None
    result: dict[str, Any] = dict(data)
    result["found"] = True
    result["country"] = code
    return result


def lookup_pension_system(country: str) -> dict[str, Any] | None:
    pensions = {
        "US": {"country": "US", "name": "Social Security", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 67, "funding_source": "payroll_tax"},
        "GB": {"country": "GB", "name": "State Pension", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 66, "funding_source": "national_insurance"},
        "DE": {"country": "DE", "name": "Gesetzliche Rentenversicherung", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 67, "funding_source": "social_security"},
        "FR": {"country": "FR", "name": "Regime general", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 64, "funding_source": "social_security"},
        "JP": {"country": "JP", "name": "Kokumin Nenkin", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 65, "funding_source": "social_security"},
        "CA": {"country": "CA", "name": "Canada Pension Plan (CPP)", "type": "pay_as_you_go", "coverage": "universal", "retirement_age": 65, "funding_source": "payroll_tax"},
        "AU": {"country": "AU", "name": "Age Pension", "type": "means_tested", "coverage": "universal", "retirement_age": 67, "funding_source": "general_revenue"},
    }
    code = _norm_country(country)
    result = pensions.get(code)
    if result is not None:
        result = dict(result)
        result["found"] = True
    return result


__all__ = [
    "BUDGET_TYPES",
    "BUDGET_DATA",
    "COUNTRY_BUDGETS",
    "PROCUREMENT_METHODS",
    "PROCUREMENT_RULES",
    "DEBT_INSTRUMENTS",
    "DEBT_DATA",
    "SOVEREIGN_WEALTH_FUNDS",
    "get_budget_info",
    "lookup_budget",
    "get_procurement_rules",
    "get_debt_info",
    "lookup_sovereign_debt",
    "lookup_pension_system",
    "get_swf_by_name",
    "get_swfs_by_country",
    "get_swfs_by_type",
    "list_countries_with_budgets",
    "list_swf_countries",
    "procurement_by_method",
    "debt_by_holder",
    "debt_to_gdp",
]
