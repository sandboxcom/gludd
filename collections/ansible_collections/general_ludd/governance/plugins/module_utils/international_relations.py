"""
international_relations -- Diplomatic relations, embassies, sanctions, trade
agreements, and visa waiver programs knowledge base for the governance collection.

Data shape:

    DIPLOMATIC_RELATIONS: dict[str, dict]  -- country code -> diplomatic profile
    EMBASSIES: dict[str, dict]             -- country code -> embassy/consular network summary
    SANCTIONS_REGIMES: dict[str, dict]     -- regime name -> sanctions program details
    SANCTIONS_DATA: dict[str, dict]        -- country code -> active sanctions against
    TRADE_AGREEMENTS: dict[str, dict]      -- agreement name -> parties, scope, year
    VISA_WAIVER_PROGRAMS: dict[str, dict]  -- program name -> member countries, conditions

Functions:
    get_diplomatic_relations(country) -> dict | None
    get_embassy_info(country) -> dict | None
    list_sanctions_regimes() -> list[str]
    is_sanctioned(target_country, by_country) -> bool
    get_sanctions_info(target_country) -> dict | None
    get_trade_agreements(country) -> list[dict]
    list_trade_agreements() -> list[str]
    get_visa_waiver_members(program) -> list[str] | None
    list_visa_waiver_programs() -> list[str]

Notes:
    * Data reflects published foreign ministry, treasury/OFAC, and treaty-body
      records as of the most recent update cycle. Sanctions programs and trade
      agreements evolve; this module provides structured knowledge of the
      regime architecture, not live compliance advice.
    * Country codes are ISO 3166-1 alpha-2 (US, GB, DE, ...).
"""

from __future__ import annotations

from typing import Any

# ── Diplomatic relations profiles ────────────────────────────────────────────
# Each entry records the formal diplomatic status, the number of resident
# missions, and foreign-policy posture information for a country.

DIPLOMATIC_RELATIONS: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "un_member_since": 1945,
        "diplomatic_relations_count": 190,
        "foreign_policy_posture": "superpower",
        "alliances": ["NATO", "Five Eyes", "AUKUS", "US-Japan", "US-ROK"],
        "foreign_assistance_budget_usd_bn": 66,
        "state_department_regional_bureaus": [
            "African Affairs", "East Asian and Pacific Affairs",
            "European and Eurasian Affairs", "Near Eastern Affairs",
            "South and Central Asian Affairs", "Western Hemisphere Affairs",
        ],
        "notes": "Largest diplomatic network globally. Permanent UNSC member (P5). Maintains ~270 embassies, consulates, and missions worldwide.",
    },
    "GB": {
        "name": "United Kingdom",
        "un_member_since": 1945,
        "diplomatic_relations_count": 191,
        "foreign_policy_posture": "middle_power",
        "alliances": ["NATO", "Five Eyes", "AUKUS", "Commonwealth"],
        "foreign_assistance_budget_usd_bn": 18,
        "state_department_regional_bureaus": [
            "Africa", "Americas and Caribbean", "Asia-Pacific",
            "Eastern Europe and Central Asia", "Middle East and North Africa",
        ],
        "notes": "FCDO (Foreign, Commonwealth & Development Office) formed in 2020 merger. Permanent UNSC member (P5). Global Britain strategy post-Brexit.",
    },
    "DE": {
        "name": "Germany",
        "un_member_since": 1973,
        "diplomatic_relations_count": 194,
        "foreign_policy_posture": "middle_power",
        "alliances": ["NATO", "EU", "G7"],
        "foreign_assistance_budget_usd_bn": 36,
        "state_department_regional_bureaus": [
            "Europe", "Asia and Pacific", "Africa", "Middle East",
            "Latin America and Caribbean",
        ],
        "notes": "Largest EU member by population and GDP. Federal Foreign Office (Auswärtiges Amt). Strong emphasis on multilateralism and EU foreign policy coordination.",
    },
    "FR": {
        "name": "France",
        "un_member_since": 1945,
        "diplomatic_relations_count": 192,
        "foreign_policy_posture": "middle_power",
        "alliances": ["NATO", "EU", "G7"],
        "foreign_assistance_budget_usd_bn": 16,
        "state_department_regional_bureaus": [
            "Europe", "Africa and Indian Ocean", "Americas and Caribbean",
            "Asia and Oceania", "Middle East and North Africa",
        ],
        "notes": "Permanent UNSC member (P5). Third-largest diplomatic network after US and China. Ministry for Europe and Foreign Affairs (Quai d'Orsay). Strong Francophone Africa ties.",
    },
    "JP": {
        "name": "Japan",
        "un_member_since": 1956,
        "diplomatic_relations_count": 194,
        "foreign_policy_posture": "middle_power",
        "alliances": ["US-Japan Security Treaty", "G7", "Quad"],
        "foreign_assistance_budget_usd_bn": 10,
        "state_department_regional_bureaus": [
            "Asia and Oceania", "North America", "Latin America and Caribbean",
            "Europe", "Middle East and Africa",
        ],
        "notes": "Ministry of Foreign Affairs (Gaimusho). Pacifist constitution (Article 9) constrains military deployments. Leading ODA donor in Asia.",
    },
    "CA": {
        "name": "Canada",
        "un_member_since": 1945,
        "diplomatic_relations_count": 190,
        "foreign_policy_posture": "middle_power",
        "alliances": ["NATO", "Five Eyes", "G7", "Commonwealth"],
        "foreign_assistance_budget_usd_bn": 6,
        "state_department_regional_bureaus": [
            "Americas", "Asia-Pacific", "Europe and Eurasia",
            "Middle East", "Africa",
        ],
        "notes": "Global Affairs Canada (GAC). Strong peacekeeping tradition. Feminist International Assistance Policy since 2017.",
    },
    "AU": {
        "name": "Australia",
        "un_member_since": 1945,
        "diplomatic_relations_count": 185,
        "foreign_policy_posture": "middle_power",
        "alliances": ["ANZUS", "Five Eyes", "AUKUS", "Commonwealth", "Quad"],
        "foreign_assistance_budget_usd_bn": 4,
        "state_department_regional_bureaus": [
            "Pacific", "Southeast Asia", "South and West Asia",
            "Americas and Europe", "Africa and Middle East",
        ],
        "notes": "Department of Foreign Affairs and Trade (DFAT). Indo-Pacific focus. Strong Pacific Islands development assistance role.",
    },
    "IN": {
        "name": "India",
        "un_member_since": 1945,
        "diplomatic_relations_count": 185,
        "foreign_policy_posture": "regional_power",
        "alliances": ["Quad", "BRICS", "SCO", "Commonwealth"],
        "foreign_assistance_budget_usd_bn": 2,
        "state_department_regional_bureaus": [
            "Eurasia", "Americas", "East Asia", "West Asia and North Africa",
            "Africa", "Europe",
        ],
        "notes": "Ministry of External Affairs (MEA). Non-aligned foreign policy tradition. Strategic autonomy doctrine. Growing Indian Ocean and Indo-Pacific engagement.",
    },
    "BR": {
        "name": "Brazil",
        "un_member_since": 1945,
        "diplomatic_relations_count": 183,
        "foreign_policy_posture": "regional_power",
        "alliances": ["BRICS", "Mercosur", "G20"],
        "foreign_assistance_budget_usd_bn": 1,
        "state_department_regional_bureaus": [
            "South America", "Central America and Caribbean",
            "North America", "Europe", "Africa", "Asia and Oceania",
        ],
        "notes": "Ministry of Foreign Affairs (Itamaraty). Longstanding commitment to multilateralism and non-intervention. Largest economy in Latin America.",
    },
    "ZA": {
        "name": "South Africa",
        "un_member_since": 1945,
        "diplomatic_relations_count": 170,
        "foreign_policy_posture": "regional_power",
        "alliances": ["BRICS", "African Union", "Commonwealth", "G20"],
        "foreign_assistance_budget_usd_bn": 1,
        "state_department_regional_bureaus": [
            "Africa", "Asia and Middle East", "Americas and Caribbean",
            "Europe",
        ],
        "notes": "Department of International Relations and Cooperation (DIRCO). Pan-African foreign policy. Ubuntu diplomacy. Mediator in African conflicts.",
    },
    "CN": {
        "name": "China",
        "un_member_since": 1971,
        "diplomatic_relations_count": 180,
        "foreign_policy_posture": "superpower",
        "alliances": ["SCO", "BRICS"],
        "foreign_assistance_budget_usd_bn": 6,
        "state_department_regional_bureaus": [
            "Asia", "West Asia and North Africa", "Africa",
            "Europe and Central Asia", "North America and Oceania",
            "Latin America and Caribbean",
        ],
        "notes": "Ministry of Foreign Affairs. Largest diplomatic network after US. Belt and Road Initiative spans 140+ countries. Permanent UNSC member (P5). One-China policy shapes diplomatic recognition.",
    },
    "RU": {
        "name": "Russia",
        "un_member_since": 1945,
        "diplomatic_relations_count": 190,
        "foreign_policy_posture": "revisionist_power",
        "alliances": ["CSTO", "SCO", "BRICS"],
        "foreign_assistance_budget_usd_bn": 1,
        "state_department_regional_bureaus": [
            "Europe", "North America", "Asia-Pacific",
            "Middle East and North Africa", "Africa", "Latin America",
        ],
        "notes": "Ministry of Foreign Affairs. Permanent UNSC member (P5). Relations with Western states severely degraded since February 2022.",
    },
}

# ── Embassy and consular network summaries ──────────────────────────────────

EMBASSIES: dict[str, dict[str, Any]] = {
    "US": {
        "country": "United States",
        "embassies_worldwide": 170,
        "consulates_worldwide": 93,
        "largest_embassy": "Baghdad, Iraq (104 acres, ~16,000 personnel)",
        "hosts_foreign_embassies": 176,
        "notable_absence": {
            "no_embassy_no_relations": [],
            "interests_section": {"CU": "Swiss Embassy represents US interests"},
        },
        "diplomatic_passport_visa_free_for": ["US citizens"],
        "notes": "State Department Bureau of Overseas Buildings Operations manages facilities.",
    },
    "GB": {
        "country": "United Kingdom",
        "embassies_worldwide": 163,
        "consulates_worldwide": 66,
        "largest_embassy": "Washington, DC, United States",
        "hosts_foreign_embassies": 171,
        "notable_absence": {
            "no_embassy_no_relations": [],
            "interests_section": {},
        },
        "diplomatic_passport_visa_free_for": ["GB citizens", "Commonwealth"],
        "notes": "FCDO manages the diplomatic estate. Some missions shared with Canada under CANZUK cooperation.",
    },
    "DE": {
        "country": "Germany",
        "embassies_worldwide": 154,
        "consulates_worldwide": 62,
        "largest_embassy": "Washington, DC, United States",
        "hosts_foreign_embassies": 159,
        "notable_absence": {
            "no_embassy_no_relations": [],
            "interests_section": {
                "KP": "Swedish Embassy represents German interests in Pyongyang",
            },
        },
        "diplomatic_passport_visa_free_for": ["DE citizens", "EU citizens"],
        "notes": "Auswärtiges Amt operates consulates in key cities. EU delegations complement bilateral embassies.",
    },
    "FR": {
        "country": "France",
        "embassies_worldwide": 163,
        "consulates_worldwide": 47,
        "largest_embassy": "Beijing, China",
        "hosts_foreign_embassies": 163,
        "notable_absence": {
            "no_embassy_no_relations": ["KP"],
            "interests_section": {},
        },
        "diplomatic_passport_visa_free_for": ["FR citizens", "EU citizens"],
        "notes": "Third-largest diplomatic network. Strong presence in Francophone Africa (40+ embassies).",
    },
    "JP": {
        "country": "Japan",
        "embassies_worldwide": 155,
        "consulates_worldwide": 65,
        "largest_embassy": "Washington, DC, United States",
        "hosts_foreign_embassies": 152,
        "notable_absence": {
            "no_embassy_no_relations": ["KP"],
            "interests_section": {},
        },
        "diplomatic_passport_visa_free_for": ["JP citizens"],
        "notes": "Gaimusho (MOFA) operates extensive Asian consular network. No official diplomatic relations with North Korea.",
    },
}

# ── Sanctions regimes ───────────────────────────────────────────────────────
# Describes multi-national and unilateral sanctions programs. Each regime
# records its legal basis, administering body, and target categories.

SANCTIONS_REGIMES: dict[str, dict[str, Any]] = {
    "un_security_council": {
        "name": "UN Security Council Sanctions",
        "administered_by": "UNSC sanctions committees",
        "type": "multilateral",
        "basis": "UN Charter Chapter VII",
        "measures": [
            "arms embargoes", "asset freezes", "travel bans",
            "commodity trade restrictions", "financial sanctions",
        ],
        "active_programs": {
            "KP": "1718 Committee (DPRK): nuclear/ballistic program sanctions since 2006",
            "IR": "2231 list: nuclear program, arms, and missile technology restrictions",
            "LY": "1970 Committee: arms embargo, asset freeze, travel ban",
            "SO": "751 Committee: arms embargo on Al-Shabaab; charcoal export ban",
            "YE": "2140 Committee: targeted sanctions on Houthi leadership",
            "CF": "2127 Committee: arms embargo; targeted asset freeze and travel ban",
        },
        "notes": "The most widely-recognized sanctions framework. SC resolutions are binding on all member states under Article 25.",
    },
    "us_ofac": {
        "name": "US OFAC Comprehensive Sanctions",
        "administered_by": "Office of Foreign Assets Control, US Treasury",
        "type": "unilateral",
        "basis": "IEEPA, TWEA, and various Executive Orders and statutes",
        "measures": [
            "comprehensive trade embargoes",
            "asset freezes (SDN List)",
            "financial system exclusion (CAPTA, correspondent banking restrictions)",
            "secondary sanctions (non-US persons exposed to US nexus)",
        ],
        "active_country_programs": ["CU", "IR", "KP", "SY", "RU", "BY"],
        "active_list_based_programs": [
            "SDN (Specially Designated Nationals)", "SSI (Sectoral Sanctions)",
            "CAATSA", "Magnitsky (Global Magnitsky Human Rights Accountability Act)",
        ],
        "notes": "OFAC administers 30+ sanctions programs. SDN list has 10,000+ entries. Secondary sanctions create extraterritorial risk.",
    },
    "eu_restrictive_measures": {
        "name": "EU Restrictive Measures (Sanctions)",
        "administered_by": "European External Action Service (EEAS) / Council of the EU",
        "type": "multilateral_regional",
        "basis": "Article 215 TFEU and Common Foreign and Security Policy (CFSP) decisions",
        "measures": [
            "asset freezes", "travel bans", "sectoral economic sanctions",
            "arms embargoes", "luxury goods bans", "SWIFT exclusions",
        ],
        "active_country_programs": ["RU", "BY", "SY", "KP", "IR", "VE", "MM", "AF"],
        "thematic_programs": [
            "human rights (Magnitsky-style EU Global Human Rights Sanctions Regime)",
            "cyber-attacks", "chemical weapons", "terrorism",
        ],
        "notes": "EU sanctions require unanimity in Council. Implemented via regulations directly applicable in member states. Russia sanctions since 2014 significantly escalated 2022.",
    },
    "uk_sanctions": {
        "name": "UK Sanctions",
        "administered_by": "Office of Financial Sanctions Implementation (OFSI), HM Treasury",
        "type": "unilateral",
        "basis": "Sanctions and Anti-Money Laundering Act 2018",
        "measures": [
            "asset freezes", "travel bans", "trade sanctions",
            "transport sanctions", "financial services restrictions",
        ],
        "active_country_programs": ["RU", "BY", "SY", "KP", "IR", "VE", "MM"],
        "thematic_programs": [
            "Global Human Rights Sanctions (Magnitsky-style)",
            "Global Anti-Corruption Sanctions",
            "Cyber sanctions",
            "Chemical weapons sanctions",
        ],
        "notes": "Post-Brexit autonomous sanctions regime. OFSI Consolidated List is the reference. UK closely coordinates with US and EU.",
    },
    "ofac_sectoral": {
        "name": "US Sectoral Sanctions",
        "administered_by": "OFAC, US Treasury",
        "type": "unilateral",
        "basis": "IEEPA / CAATSA",
        "measures": [
            "sector-specific financial and investment restrictions",
            "technology export controls",
            "corresponding banking restrictions",
            "capital-raising prohibitions",
        ],
        "active_sector_programs": {
            "RU": "SSI (Sectoral Sanctions Identifications): energy, financial, defense sectors. Directive 1-4 restrictions.",
            "IR": "financial, oil, petrochemical, automotive, shipping, construction, metals sectors",
        },
        "notes": "Sectoral sanctions differ from comprehensive: they restrict specific transactions rather than blocking all property. Primarily aimed at Russia and Iran.",
    },
}

# ── Per-country sanctions data ──────────────────────────────────────────────
# Which sanctions regimes target each country. Regime keys reference the
# SANCTIONS_REGIMES table.

SANCTIONS_DATA: dict[str, dict[str, Any]] = {
    "KP": {
        "name": "North Korea (DPRK)",
        "sanctioned_by": ["un_security_council", "us_ofac", "eu_restrictive_measures", "uk_sanctions"],
        "primary_concern": "nuclear weapons and ballistic missile programs",
        "severity": "comprehensive",
        "since_year": 2006,
        "notes": "Tightest international sanctions regime. UNSC 1718 Committee oversees. US treats as state sponsor of terrorism.",
    },
    "IR": {
        "name": "Iran",
        "sanctioned_by": ["un_security_council", "us_ofac", "eu_restrictive_measures", "uk_sanctions", "ofac_sectoral"],
        "primary_concern": "nuclear program, ballistic missiles, human rights, regional destabilization",
        "severity": "comprehensive",
        "since_year": 1979,
        "notes": "JCPOA (2015) lifted many sanctions; US withdrawal (2018) re-imposed them. UNSC restrictions partially re-imposed under snapback mechanism.",
    },
    "RU": {
        "name": "Russia",
        "sanctioned_by": ["us_ofac", "eu_restrictive_measures", "uk_sanctions", "ofac_sectoral"],
        "primary_concern": "full-scale invasion of Ukraine (2022), annexation of Crimea (2014)",
        "severity": "comprehensive",
        "since_year": 2014,
        "notes": "Most extensive sanctions against a major economy. SWIFT disconnection for key banks, oil price cap, technology export bans. No UNSC sanctions (Russia holds veto).",
    },
    "BY": {
        "name": "Belarus",
        "sanctioned_by": ["us_ofac", "eu_restrictive_measures", "uk_sanctions"],
        "primary_concern": "complicity in Russian invasion of Ukraine; internal repression; electoral fraud",
        "severity": "comprehensive",
        "since_year": 2004,
        "notes": "Sanctions significantly expanded 2020-2022. Lukashenko regime designated. Sectoral restrictions mirror Russia sanctions.",
    },
    "SY": {
        "name": "Syria",
        "sanctioned_by": ["us_ofac", "eu_restrictive_measures", "uk_sanctions"],
        "primary_concern": "civil war, human rights violations, chemical weapons use",
        "severity": "comprehensive",
        "since_year": 2011,
        "notes": "US Caesar Act (2020) imposes secondary sanctions on anyone dealing with Syrian regime. No UNSC sanctions (Russia/China veto).",
    },
    "CU": {
        "name": "Cuba",
        "sanctioned_by": ["us_ofac"],
        "primary_concern": "historical: nationalization of US property, human rights; Helms-Burton Act",
        "severity": "comprehensive",
        "since_year": 1960,
        "notes": "Longest-running US sanctions program. EU, UK, and most states do not sanction Cuba. UN General Assembly annually votes 185+ to 2 to end embargo.",
    },
    "VE": {
        "name": "Venezuela",
        "sanctioned_by": ["us_ofac", "eu_restrictive_measures", "uk_sanctions"],
        "primary_concern": "electoral fraud, human rights abuses, corruption, undermining of democratic institutions",
        "severity": "targeted_on_officials_and_sectors",
        "since_year": 2014,
        "notes": "US sanctions target state oil company PDVSA, gold sector, and senior officials. EU sanctions are targeted on individuals.",
    },
    "MM": {
        "name": "Myanmar",
        "sanctioned_by": ["us_ofac", "eu_restrictive_measures", "uk_sanctions"],
        "primary_concern": "military coup (2021), human rights abuses, Rohingya genocide",
        "severity": "targeted_on_military_and_officials",
        "since_year": 1997,
        "notes": "Sanctions significantly expanded after February 2021 coup. EU sanctions target military-controlled enterprises and officials.",
    },
    "AF": {
        "name": "Afghanistan",
        "sanctioned_by": ["eu_restrictive_measures"],
        "primary_concern": "Taliban takeover (2021); human rights; terrorism",
        "severity": "targeted_on_taliban",
        "since_year": 2016,
        "notes": "UNSC 1988 list sanctions Taliban individuals. EU autonomous sanctions also apply. Humanitarian carve-outs for aid delivery.",
    },
}

# ── Trade agreements ────────────────────────────────────────────────────────

TRADE_AGREEMENTS: dict[str, dict[str, Any]] = {
    "usmca": {
        "name": "USMCA (US-Mexico-Canada Agreement)",
        "type": "free_trade_area",
        "parties": ["US", "CA", "MX"],
        "signed_year": 2018,
        "effective_year": 2020,
        "scope": ["goods", "services", "digital trade", "intellectual property", "labor", "environment"],
        "notes": "Replaced NAFTA (1994). Digital trade chapter is most advanced in any FTA. Labour provisions include facility-level rapid response mechanism.",
    },
    "eu_single_market": {
        "name": "EU Single Market",
        "type": "single_market",
        "parties": [
            "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
            "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
            "PL", "PT", "RO", "SK", "SI", "ES", "SE",
        ],
        "signed_year": 1993,
        "effective_year": 1993,
        "scope": ["goods", "services", "capital", "people (four freedoms)"],
        "notes": "Also includes EEA members (NO, IS, LI) via separate agreement. Switzerland participates through bilateral agreements. The most integrated economic zone globally.",
    },
    "cptpp": {
        "name": "CPTPP (Comprehensive and Progressive Agreement for Trans-Pacific Partnership)",
        "type": "free_trade_area",
        "parties": ["AU", "BN", "CA", "CL", "JP", "MY", "MX", "NZ", "PE", "SG", "VN", "GB"],
        "signed_year": 2018,
        "effective_year": 2018,
        "scope": ["goods", "services", "investment", "intellectual property", "government procurement", "labor", "environment"],
        "notes": "Successor to TPP after US withdrawal (2017). UK joined 2023 (first new member). China, Taiwan, and others have applied to join.",
    },
    "rcep": {
        "name": "RCEP (Regional Comprehensive Economic Partnership)",
        "type": "free_trade_area",
        "parties": ["AU", "BN", "KH", "CN", "ID", "JP", "KR", "LA", "MY", "MM", "NZ", "PH", "SG", "TH", "VN"],
        "signed_year": 2020,
        "effective_year": 2022,
        "scope": ["goods", "services", "investment", "intellectual property", "e-commerce"],
        "notes": "Largest FTA by population and GDP (~30% of global). Less ambitious than CPTPP on labor and environment. India opted out (2019).",
    },
    "mercosur": {
        "name": "Mercosur (Southern Common Market)",
        "type": "customs_union",
        "parties": ["AR", "BR", "PY", "UY"],
        "signed_year": 1991,
        "effective_year": 1991,
        "scope": ["goods (common external tariff)", "services in progress"],
        "notes": "Associate members: BO, CL, CO, EC, PE. Venezuela suspended since 2016. EU-Mercosur trade agreement concluded 2019 but not yet ratified.",
    },
    "afcfta": {
        "name": "AfCFTA (African Continental Free Trade Area)",
        "type": "free_trade_area",
        "parties": ["Signatories: 54 of 55 AU members (all except Eritrea as of 2023)"],
        "signed_year": 2018,
        "effective_year": 2021,
        "scope": ["goods", "services", "intellectual property", "investment", "competition policy"],
        "notes": "Largest FTA by number of countries since WTO. Aim to eliminate 90% of tariffs over 5-10 years. Implementation phased. Potential to lift 30M out of extreme poverty.",
    },
    "tca": {
        "name": "EU-UK Trade and Cooperation Agreement (TCA)",
        "type": "free_trade_area",
        "parties": ["GB", "EU (27)"],
        "signed_year": 2020,
        "effective_year": 2021,
        "scope": ["goods (zero tariffs / zero quotas)", "limited services", "fisheries", "level playing field"],
        "notes": "Post-Brexit agreement. Does not cover foreign policy, defence, or financial services equivalence. Contains rebalancing and review mechanisms.",
    },
    "australia_nz_closer": {
        "name": "Australia-New Zealand Closer Economic Relations",
        "type": "free_trade_area",
        "parties": ["AU", "NZ"],
        "signed_year": 1983,
        "effective_year": 1983,
        "scope": ["goods", "services", "labor mobility"],
        "notes": "One of the most comprehensive bilateral trade agreements. Full free movement of citizens. Mutual recognition of qualifications and occupations.",
    },
    "asean_fta": {
        "name": "ASEAN Free Trade Area (AFTA)",
        "type": "free_trade_area",
        "parties": ["BN", "KH", "ID", "LA", "MY", "MM", "PH", "SG", "TH", "VN"],
        "signed_year": 1992,
        "effective_year": 1993,
        "scope": ["goods (Common Effective Preferential Tariff scheme)"],
        "notes": "Near-zero intra-ASEAN tariffs. ASEAN has also signed FTA agreements with CN, JP, KR, IN, AU/NZ (ASEAN+1 FTAs). RCEP is the broader framework.",
    },
}

# ── Visa waiver programs ────────────────────────────────────────────────────

VISA_WAIVER_PROGRAMS: dict[str, dict[str, Any]] = {
    "us_visa_waiver": {
        "name": "US Visa Waiver Program (VWP)",
        "administered_by": "US Department of Homeland Security (CBP/DHS)",
        "type": "reciprocal_short_stay",
        "member_countries": [
            "AD", "AU", "AT", "BE", "BN", "CL", "HR", "CZ", "DK", "EE",
            "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IL", "IT", "JP",
            "KR", "LV", "LI", "LT", "LU", "MT", "MC", "NL", "NZ", "NO",
            "PL", "PT", "QA", "SM", "SG", "SK", "SI", "ES", "SE", "CH",
            "TW", "GB",
        ],
        "conditions": [
            "ESTA authorization required (online, fee, valid 2 years)",
            "Maximum stay 90 days",
            "E-passport with biometric chip mandatory",
            "Reciprocity: US citizens must have visa-free access to member country",
        ],
        "notes": "42 countries as of 2024. Countries can be removed unilaterally. Overstay and refusal rates monitored. ETA (UK) and ETIAS (EU) are similar pre-authorization programs.",
    },
    "schengen_visa_waiver": {
        "name": "Schengen Area Short-Stay Visa Waiver",
        "administered_by": "European Commission / Schengen member states",
        "type": "reciprocal_short_stay",
        "member_countries": [
            "AT", "BE", "BG", "HR", "CZ", "DK", "EE", "FI", "FR", "DE",
            "GR", "HU", "IS", "IT", "LV", "LI", "LT", "LU", "MT", "NL",
            "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH",
        ],
        "conditions": [
            "90 days in any 180-day period",
            "Reciprocity: EU/Schengen citizens must have access to partner country",
            "ETIAS pre-authorization (launch expected 2025)",
        ],
        "additional_visa_free_nationals": [
            "US", "GB", "CA", "AU", "NZ", "JP", "KR", "IL", "AE", "BR",
            "AR", "CL", "UY", "SG", "HK", "MO", "TW", "MY", "CR", "PA",
            "and ~40 other nationalities (total ~60 countries)",
        ],
        "notes": "The Schengen acquis applies to most EU states plus EFTA (NO, IS, CH, LI). Ireland and Cyprus are EU but not in Schengen.",
    },
    "uk_visa_waiver": {
        "name": "UK Short-Term Study and Tourist Visa Waiver",
        "administered_by": "UK Visas and Immigration (Home Office)",
        "type": "non_reciprocal_short_stay",
        "member_countries": [
            "US", "CA", "AU", "NZ", "JP", "KR", "SG", "HK", "MO", "TW",
            "IL", "AE", "BR", "AR", "CL", "UY", "CR", "PA", "and ~50 others",
        ],
        "conditions": [
            "Maximum stay 6 months",
            "Electronic Travel Authorisation (ETA) required for visa-free nationals (phased rollout from 2023)",
            "No right to work, study (limited), or use public funds",
        ],
        "notes": "UK operates its own visa system post-Brexit (no longer part of EU visa policy). ETA is similar to US ESTA. EU/EEA/Swiss nationals also now require ETA.",
    },
    "apac_business_travel_card": {
        "name": "APEC Business Travel Card (ABTC)",
        "administered_by": "APEC (Asia-Pacific Economic Cooperation)",
        "type": "business_facilitation",
        "member_countries": [
            "AU", "BN", "CL", "CN", "HK", "ID", "JP", "KR", "MY", "MX",
            "NZ", "PG", "PE", "PH", "RU", "SG", "TW", "TH", "VN",
        ],
        "conditions": [
            "Pre-cleared for short-term business travel",
            "5-year validity",
            "Fast-track immigration lanes at participating airports",
            "Applications through home economy's issuing authority",
        ],
        "notes": "Not a visa waiver per se but pre-clearance. Does not apply to US and Canada (transitional members with limited participation). Russia participation suspended.",
    },
    "ecowas_free_movement": {
        "name": "ECOWAS Free Movement Protocol",
        "administered_by": "Economic Community of West African States (ECOWAS)",
        "type": "regional_free_movement",
        "member_countries": [
            "BJ", "BF", "CV", "CI", "GM", "GH", "GN", "GW", "LR", "ML",
            "NE", "NG", "SN", "SL", "TG",
        ],
        "conditions": [
            "Right of entry without visa for 90 days",
            "Right of residence and establishment",
            "ECOWAS Travel Certificate or national passport required",
        ],
        "notes": "Most advanced free-movement protocol in Africa. Implementation varies -- some countries maintain border checks despite protocol. ECOWAS passport in circulation.",
    },
    "gcc_free_movement": {
        "name": "GCC Free Movement",
        "administered_by": "Gulf Cooperation Council (GCC)",
        "type": "regional_free_movement",
        "member_countries": ["SA", "AE", "KW", "QA", "BH", "OM"],
        "conditions": [
            "Visa-free travel between GCC states for GCC citizens",
            "National ID card sufficient (no passport required)",
            "Right to own property, work, and access services in other GCC states (varies by country)",
        ],
        "notes": "GCC is a political and economic union. Full free movement exists for GCC nationals. Non-GCC residents require separate visas per country. Qatar-GCC travel normalized after 2021 Al-Ula agreement.",
    },
    "canzuk": {
        "name": "CANZUK Free Movement Proposal",
        "administered_by": "proposed / advocacy (not yet implemented)",
        "type": "proposed_free_movement",
        "member_countries": ["CA", "AU", "NZ", "GB"],
        "conditions": [
            "Proposed: reciprocal free movement and right to work",
            "Currently: each country has separate visa-waiver and working-holiday programs",
            "Existing bilateral: TTTA (Trans-Tasman) between AU and NZ already in force",
        ],
        "notes": "CANZUK is an advocacy campaign, not an existing treaty. AU-NZ Trans-Tasman Travel Arrangement (1973) is the closest existing model -- full free movement and work rights between AU and NZ.",
    },
}

# ── Accessor functions ──────────────────────────────────────────────────────


def _norm_country(country: str) -> str:
    """Normalize a country code to ISO-3166-1 alpha-2 upper-case."""
    return country.strip().upper()


def get_diplomatic_relations(country: str) -> dict[str, Any] | None:
    """Return the diplomatic relations profile for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict with foreign policy posture, alliances, diplomatic network
        size, and related metadata. Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    data = DIPLOMATIC_RELATIONS.get(code)
    if data is None:
        return None
    return dict(data)


def get_embassy_info(country: str) -> dict[str, Any] | None:
    """Return embassy and consular network information for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict with embassy/consulate counts, largest embassy, and notable
        diplomatic presences or absences. Returns ``None`` if unknown.
    """
    code = _norm_country(country)
    data = EMBASSIES.get(code)
    if data is None:
        return None
    return dict(data)


def list_sanctions_regimes() -> list[str]:
    """Return the sorted list of known sanctions regime keys."""
    return sorted(SANCTIONS_REGIMES.keys())


def is_sanctioned(target_country: str, by_country: str) -> bool:
    """Check whether one country sanctions another.

    Args:
        target_country: ISO-3166-1 alpha-2 code of the potentially sanctioned country.
        by_country: ISO-3166-1 alpha-2 code of the potential sanctioning country.

    Returns:
        ``True`` if ``by_country`` is listed as sanctioning ``target_country``
        under any of the known sanctions regimes. ``False`` otherwise, including
        when either country is unknown.
    """
    target = _norm_country(target_country)
    by = _norm_country(by_country)
    target_data = SANCTIONS_DATA.get(target)
    if target_data is None:
        return False
    regimes_sanctioning = target_data.get("sanctioned_by", [])

    SENDER_TO_REGIMES: dict[str, set[str]] = {
        "US": {"us_ofac", "ofac_sectoral"},
        "GB": {"uk_sanctions"},
        "FR": {"eu_restrictive_measures"},
        "DE": {"eu_restrictive_measures"},
        "IT": {"eu_restrictive_measures"},
        "ES": {"eu_restrictive_measures"},
        "NL": {"eu_restrictive_measures"},
        "BE": {"eu_restrictive_measures"},
        "PL": {"eu_restrictive_measures"},
        "SE": {"eu_restrictive_measures"},
        "DK": {"eu_restrictive_measures"},
        "FI": {"eu_restrictive_measures"},
        "AT": {"eu_restrictive_measures"},
        "IE": {"eu_restrictive_measures"},
        "PT": {"eu_restrictive_measures"},
        "CZ": {"eu_restrictive_measures"},
        "RO": {"eu_restrictive_measures"},
        "HU": {"eu_restrictive_measures"},
        "GR": {"eu_restrictive_measures"},
        "BG": {"eu_restrictive_measures"},
        "SK": {"eu_restrictive_measures"},
        "HR": {"eu_restrictive_measures"},
        "SI": {"eu_restrictive_measures"},
        "LT": {"eu_restrictive_measures"},
        "LV": {"eu_restrictive_measures"},
        "EE": {"eu_restrictive_measures"},
        "LU": {"eu_restrictive_measures"},
        "MT": {"eu_restrictive_measures"},
        "CY": {"eu_restrictive_measures"},
        "JP": set(),
        "CA": set(),
        "AU": set(),
        "IN": set(),
        "BR": set(),
        "ZA": set(),
        "CN": set(),
        "RU": set(),
    }

    sender_regimes = SENDER_TO_REGIMES.get(by, set())
    if not sender_regimes:
        return False

    return bool(set(regimes_sanctioning) & sender_regimes)


def get_sanctions_info(target_country: str) -> dict[str, Any] | None:
    """Return sanctions information for a target country.

    Args:
        target_country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict describing which entities sanction this country, why, and the
        severity. Returns ``None`` if the country is unknown or not sanctioned.
    """
    code = _norm_country(target_country)
    data = SANCTIONS_DATA.get(code)
    if data is None:
        return None
    return dict(data)


def get_trade_agreements(country: str) -> list[dict[str, Any]]:
    """Return all trade agreements to which a country is a party.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A list of trade agreement dicts (name, type, parties, scope).
        Returns an empty list if the country is not found in any agreement.
    """
    code = _norm_country(country)
    results: list[dict[str, Any]] = []
    for _key, agreement in TRADE_AGREEMENTS.items():
        parties = agreement.get("parties", [])
        if code in parties:
            results.append(dict(agreement))
    return results


def list_trade_agreements() -> list[str]:
    """Return the sorted list of known trade agreement keys."""
    return sorted(TRADE_AGREEMENTS.keys())


def get_visa_waiver_members(program: str) -> list[str] | None:
    """Return the member country list for a visa waiver program.

    Args:
        program: Program key (case-insensitive), e.g. ``"us_visa_waiver"``.

    Returns:
        A list of ISO-3166-1 alpha-2 country codes that are members.
        Returns ``None`` if the program key is unknown.
    """
    key = program.strip().lower()
    data = VISA_WAIVER_PROGRAMS.get(key)
    if data is None:
        return None
    return list(data.get("member_countries", []))


def list_visa_waiver_programs() -> list[str]:
    """Return the sorted list of known visa waiver program keys."""
    return sorted(VISA_WAIVER_PROGRAMS.keys())


ALLIANCES: dict[str, list[str]] = {
    code: data.get("alliances", [])
    for code, data in DIPLOMATIC_RELATIONS.items()
}


def lookup_diplomatic_relations(country: str) -> dict[str, Any] | None:
    code = _norm_country(country)
    data = DIPLOMATIC_RELATIONS.get(code)
    if data is None:
        return None
    result: dict[str, Any] = dict(data)
    result["found"] = True
    result["country"] = code
    return result


def lookup_sanctions(target_country: str) -> dict[str, Any] | None:
    info = get_sanctions_info(target_country)
    if info is None:
        return None
    info["found"] = True
    return info


def search_alliance(query: str) -> list[dict[str, Any]]:
    q = query.strip().lower()
    results: list[dict[str, Any]] = []
    for _key, agreement in TRADE_AGREEMENTS.items():
        if q in agreement["name"].lower():
            results.append(dict(agreement))
    if not results:
        for code, data in DIPLOMATIC_RELATIONS.items():
            for alliance in data.get("alliances", []):
                if q in alliance.lower():
                    entry: dict[str, Any] = {"alliance": alliance, "member": code, "member_name": data["name"]}
                    if alliance.lower() == "nato":
                        entry["full_name"] = "North Atlantic Treaty Organization"
                    elif alliance.lower() == "five eyes":
                        entry["full_name"] = "Five Eyes Intelligence Alliance"
                    elif alliance.lower() == "aukus":
                        entry["full_name"] = "AUKUS Trilateral Security Pact"
                    results.append(entry)
    return results
