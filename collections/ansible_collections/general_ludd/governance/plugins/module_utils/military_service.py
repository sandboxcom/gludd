"""
military_service -- Conscription, military branches, service records,
and veteran benefits for the governance collection.

Data shape:

    CONSCRIPTION_DATA[country] -> dict with active/type/age/duration/notes
    MILITARY_BRANCHES[country] -> list of branch dicts with name, role, manpower
    VETERAN_BENEFITS[country] -> dict with categories: healthcare, education, housing,
                                  pension, disability, burial
    ENLISTMENT_PROCESS[country] -> dict with age_range, citizenship, fitness, tests

Functions:
    get_conscription_info(country) -> dict | None
    get_military_branches(country) -> list[dict] | None
    get_veteran_benefits(country, benefit_category=None) -> dict | None
    get_enlistment_process(country) -> dict | None
    list_mandatory_service_countries() -> list[country_code]
"""

from __future__ import annotations

from typing import Any

# ── Conscription / mandatory service data ──────────────────────────────────

CONSCRIPTION_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": True,
        "registration_age": "18-25 (Selective Service, males only)",
        "notes": "All-volunteer force since 1973. Selective Service registration mandatory for males.",
        "reserve_obligation": "8 years total (active + inactive ready reserve)",
    },
    "GB": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": False,
        "registration_age": "N/A",
        "notes": "All-volunteer force since 1963 (last conscripts discharged). No registration.",
        "reserve_obligation": "Regular + Reserve commitment varies by service; typically 4 years reserve.",
    },
    "CA": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": False,
        "registration_age": "N/A",
        "notes": "All-volunteer force. Conscription ended after WWII (1945). No registration.",
        "reserve_obligation": "Primary Reserve service is part-time voluntary.",
    },
    "DE": {
        "active": False,
        "type": "suspended_conscription",
        "registration_required": False,
        "registration_age": "N/A (suspended 2011)",
        "notes": "Conscription suspended 2011. Volunteers fill roles. Can be reactivated by Bundestag.",
        "reserve_obligation": "Former conscripts may be liable for reserve duty until age 60.",
    },
    "FR": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": True,
        "registration_age": "16-25 (Journee Defense et Citoyennete - census day)",
        "notes": "Conscription suspended 2001. Census day (JDC) still mandatory for all 16-year-olds.",
        "reserve_obligation": "Operational Reserve voluntary; citoyen reserve available.",
    },
    "AU": {
        "active": False,
        "type": "volunteer_only",
        "registration_required": False,
        "registration_age": "N/A",
        "notes": "All-volunteer Australian Defence Force since 1972. No registration.",
        "reserve_obligation": "Active Reserve and Standby Reserve categories; voluntary.",
    },
    "IL": {
        "active": True,
        "type": "mandatory_conscription",
        "registration_required": True,
        "registration_age": "18 (both sexes)",
        "duration_months": 32,
        "duration_male_months": 32,
        "duration_female_months": 24,
        "notes": "Mandatory for Jewish, Druze, and Circassian citizens. Exemptions for Arab citizens (can volunteer). Religious women may request exemption.",
        "reserve_obligation": "Reserve duty until age 40 (men) or 24 (women), with annual call-up for training.",
    },
    "KR": {
        "active": True,
        "type": "mandatory_conscription",
        "registration_required": True,
        "registration_age": "18-28 (males only)",
        "duration_months": 18,
        "notes": "Mandatory for male citizens. Duration varies by branch: Army 18mo, Navy 20mo, Air Force 21mo.",
        "reserve_obligation": "Reserve duty until age 40; 2-3 day annual training.",
    },
    "RU": {
        "active": True,
        "type": "mandatory_conscription",
        "registration_required": True,
        "registration_age": "18-27 (males only)",
        "duration_months": 12,
        "notes": "Mandatory for male citizens 18-27. University deferment available. Multiple draft cycles per year.",
        "reserve_obligation": "Reserve classification up to age 50-65 depending on rank.",
    },
    "FI": {
        "active": True,
        "type": "mandatory_conscription",
        "registration_required": True,
        "registration_age": "18-30 (males; voluntary for females)",
        "duration_months": 12,
        "notes": "Mandatory for male citizens. Duration: 165-347 days depending on training role. Voluntary for women since 1995.",
        "reserve_obligation": "Reserve until age 50 (rank and file) or 60 (officers/NCOs).",
    },
    "CH": {
        "active": True,
        "type": "militia_with_conscription",
        "registration_required": True,
        "registration_age": "18-25 (males only)",
        "duration_months": 18,
        "notes": "Militia system with initial training + annual refresher courses (3 weeks/year). Alternative civilian service available.",
        "reserve_obligation": "Annual refresher until age 34 (total service 245 days).",
    },
    "BR": {
        "active": True,
        "type": "selective_conscription",
        "registration_required": True,
        "registration_age": "18 (males only)",
        "duration_months": 12,
        "notes": "Registration mandatory at 18. Selective: only ~5-10% of registered males actually conscripted. Many exemptions available (medical, study, family).",
        "reserve_obligation": "Reserve obligation until age 45.",
    },
}


# ── Military branches by country ───────────────────────────────────────────

MILITARY_BRANCHES: dict[str, list[dict[str, Any]]] = {
    "US": [
        {
            "name": "Army",
            "role": "land_warfare",
            "manpower_active": 452000,
            "manpower_reserve": 177000,
            "established": 1775,
        },
        {
            "name": "Navy",
            "role": "maritime_warfare",
            "manpower_active": 336000,
            "manpower_reserve": 57000,
            "established": 1775,
        },
        {
            "name": "Air Force",
            "role": "air_space_warfare",
            "manpower_active": 319000,
            "manpower_reserve": 70000,
            "established": 1947,
        },
        {
            "name": "Marine Corps",
            "role": "expeditionary_amphibious",
            "manpower_active": 177000,
            "manpower_reserve": 33000,
            "established": 1775,
        },
        {
            "name": "Space Force",
            "role": "space_warfare",
            "manpower_active": 8600,
            "manpower_reserve": 0,
            "established": 2019,
        },
        {
            "name": "Coast Guard",
            "role": "maritime_law_enforcement",
            "manpower_active": 40000,
            "manpower_reserve": 7000,
            "established": 1790,
            "note": "Department of Homeland Security (peacetime); transfers to Navy during war.",
        },
    ],
    "GB": [
        {
            "name": "British Army",
            "role": "land_warfare",
            "manpower_active": 76000,
            "manpower_reserve": 27000,
            "established": 1660,
        },
        {
            "name": "Royal Navy",
            "role": "maritime_warfare",
            "manpower_active": 32000,
            "manpower_reserve": 3000,
            "established": 1546,
        },
        {
            "name": "Royal Air Force",
            "role": "air_warfare",
            "manpower_active": 31000,
            "manpower_reserve": 3000,
            "established": 1918,
        },
        {
            "name": "Royal Marines",
            "role": "commando_amphibious",
            "manpower_active": 6600,
            "manpower_reserve": 600,
            "established": 1664,
            "note": "Part of the Royal Navy/Naval Service.",
        },
    ],
    "CA": [
        {
            "name": "Canadian Army",
            "role": "land_warfare",
            "manpower_active": 23000,
            "manpower_reserve": 19000,
            "established": 1855,
        },
        {
            "name": "Royal Canadian Navy",
            "role": "maritime_warfare",
            "manpower_active": 8500,
            "manpower_reserve": 4000,
            "established": 1910,
        },
        {
            "name": "Royal Canadian Air Force",
            "role": "air_warfare",
            "manpower_active": 12000,
            "manpower_reserve": 2000,
            "established": 1924,
        },
    ],
    "DE": [
        {
            "name": "Heer (Army)",
            "role": "land_warfare",
            "manpower_active": 62000,
            "manpower_reserve": 15000,
            "established": 1955,
        },
        {
            "name": "Marine (Navy)",
            "role": "maritime_warfare",
            "manpower_active": 16000,
            "manpower_reserve": 2000,
            "established": 1956,
        },
        {
            "name": "Luftwaffe (Air Force)",
            "role": "air_warfare",
            "manpower_active": 27000,
            "manpower_reserve": 4000,
            "established": 1956,
        },
        {
            "name": "Streitkraeftebasis (Joint Support)",
            "role": "logistics_support",
            "manpower_active": 27000,
            "manpower_reserve": 0,
            "established": 2000,
        },
        {
            "name": "Cyber- und Informationsraum (CIR)",
            "role": "cyber_information",
            "manpower_active": 14000,
            "manpower_reserve": 0,
            "established": 2017,
        },
    ],
    "FR": [
        {
            "name": "Armee de Terre (Army)",
            "role": "land_warfare",
            "manpower_active": 114000,
            "manpower_reserve": 22000,
            "established": 1792,
        },
        {
            "name": "Marine Nationale (Navy)",
            "role": "maritime_warfare",
            "manpower_active": 37000,
            "manpower_reserve": 6000,
            "established": 1624,
        },
        {
            "name": "Armee de l'Air et de l'Espace",
            "role": "air_space_warfare",
            "manpower_active": 41000,
            "manpower_reserve": 5000,
            "established": 1909,
        },
        {
            "name": "Gendarmerie Nationale",
            "role": "military_police",
            "manpower_active": 100000,
            "manpower_reserve": 30000,
            "established": 1791,
            "note": "Military force with police duties; under Ministry of Interior for policing, Ministry of Armed Forces for military operations.",
        },
    ],
    "AU": [
        {
            "name": "Australian Army",
            "role": "land_warfare",
            "manpower_active": 29000,
            "manpower_reserve": 15000,
            "established": 1901,
        },
        {
            "name": "Royal Australian Navy",
            "role": "maritime_warfare",
            "manpower_active": 14000,
            "manpower_reserve": 3000,
            "established": 1911,
        },
        {
            "name": "Royal Australian Air Force",
            "role": "air_warfare",
            "manpower_active": 14000,
            "manpower_reserve": 3000,
            "established": 1921,
        },
    ],
    "JP": [
        {
            "name": "Ground Self-Defense Force (GSDF)",
            "role": "land_defense",
            "manpower_active": 150000,
            "manpower_reserve": 50000,
            "established": 1954,
        },
        {
            "name": "Maritime Self-Defense Force (MSDF)",
            "role": "maritime_defense",
            "manpower_active": 45000,
            "manpower_reserve": 1000,
            "established": 1954,
        },
        {
            "name": "Air Self-Defense Force (ASDF)",
            "role": "air_defense",
            "manpower_active": 47000,
            "manpower_reserve": 800,
            "established": 1954,
        },
    ],
    "IL": [
        {
            "name": "Ground Forces",
            "role": "land_warfare",
            "manpower_active": 133000,
            "manpower_reserve": 400000,
            "established": 1948,
        },
        {
            "name": "Air Force",
            "role": "air_warfare",
            "manpower_active": 34000,
            "manpower_reserve": 55000,
            "established": 1948,
        },
        {
            "name": "Navy",
            "role": "maritime_warfare",
            "manpower_active": 10000,
            "manpower_reserve": 10000,
            "established": 1948,
        },
    ],
    "KR": [
        {
            "name": "Army (ROKA)",
            "role": "land_warfare",
            "manpower_active": 464000,
            "manpower_reserve": 2800000,
            "established": 1948,
        },
        {
            "name": "Navy (ROKN)",
            "role": "maritime_warfare",
            "manpower_active": 70000,
            "manpower_reserve": 0,
            "established": 1948,
            "note": "Includes Republic of Korea Marine Corps (~29,000).",
        },
        {
            "name": "Air Force (ROKAF)",
            "role": "air_warfare",
            "manpower_active": 65000,
            "manpower_reserve": 0,
            "established": 1949,
        },
    ],
    "RU": [
        {
            "name": "Ground Forces",
            "role": "land_warfare",
            "manpower_active": 280000,
            "manpower_reserve": 2000000,
            "established": 1992,
        },
        {
            "name": "Aerospace Forces",
            "role": "air_space_warfare",
            "manpower_active": 165000,
            "manpower_reserve": 0,
            "established": 2015,
        },
        {
            "name": "Navy",
            "role": "maritime_warfare",
            "manpower_active": 150000,
            "manpower_reserve": 0,
            "established": 1992,
        },
        {
            "name": "Strategic Rocket Forces",
            "role": "nuclear_deterrence",
            "manpower_active": 50000,
            "manpower_reserve": 0,
            "established": 1959,
        },
        {
            "name": "Airborne Forces (VDV)",
            "role": "rapid_deployment",
            "manpower_active": 45000,
            "manpower_reserve": 0,
            "established": 1930,
        },
    ],
}


# ── Veteran benefits by country ────────────────────────────────────────────

VETERAN_BENEFITS: dict[str, dict[str, Any]] = {
    "US": {
        "administering_body": "Department of Veterans Affairs (VA)",
        "benefits_portal": "https://www.va.gov",
        "categories": {
            "healthcare": {
                "description": "VA healthcare system (VA hospitals, clinics, Vet Centers)",
                "eligibility": "Honorable discharge, service-connected conditions, or income-based",
            },
            "education": {
                "description": "GI Bill (Post-9/11, Montgomery, VR&E), tuition assistance",
                "eligibility": "Active duty service after 9/11/2001 or equivalent service period",
            },
            "housing": {
                "description": "VA home loan guarantee, adapted housing grants (SHA, SAH)",
                "eligibility": "Honorable discharge + minimum service period (90 days active)",
            },
            "pension": {
                "description": "VA pension for low-income wartime veterans (or survivors pension)",
                "eligibility": "Age 65+ or disabled, wartime service, income < threshold",
            },
            "disability": {
                "description": "Disability compensation (tax-free, rating 0-100%)",
                "eligibility": "Service-connected disability or aggravation of pre-existing condition",
            },
            "burial": {
                "description": "National cemetery burial, headstone, flag, burial allowance",
                "eligibility": "Veterans, spouses, and dependent children",
            },
        },
    },
    "GB": {
        "administering_body": "Veterans UK (Ministry of Defence)",
        "benefits_portal": "https://www.gov.uk/government/organisations/veterans-uk",
        "categories": {
            "healthcare": {
                "description": "NHS priority treatment for service-related conditions; Veterans' Mental Health services (Op COURAGE)",
                "eligibility": "Service-related condition; GP registration for general NHS care",
            },
            "education": {
                "description": "Enhanced Learning Credits (ELC); Publicly Funded Further Education/Higher Education scheme",
                "eligibility": "Service leavers with 6+ years and certain discharge reasons",
            },
            "pension": {
                "description": "Armed Forces Pension Scheme (AFPS); War Pension Scheme",
                "eligibility": "Service years + age; war pensions for pre-2005 injuries",
            },
            "disability": {
                "description": "Armed Forces Compensation Scheme (AFCS); War Disablement Pension",
                "eligibility": "Injury/illness caused or worsened by service",
            },
            "housing": {
                "description": "Forces Help to Buy; Service Family Accommodation priority",
                "eligibility": "Service personnel; veterans with urgent housing needs",
            },
        },
    },
    "CA": {
        "administering_body": "Veterans Affairs Canada (VAC)",
        "benefits_portal": "https://www.veterans.gc.ca",
        "categories": {
            "healthcare": {
                "description": "VAC health care benefits; treatment benefits program; Veterans Independence Program",
                "eligibility": "Service-related condition or low-income veteran",
            },
            "education": {
                "description": "Education and Training Benefit (CAD $40,000-80,000); Career Transition Services",
                "eligibility": "6+ years of service (honorable discharge)",
            },
            "pension": {
                "description": "Canadian Armed Forces pension; disability pension; War Veterans Allowance",
                "eligibility": "Service years; disability rating; income-tested for allowance",
            },
            "disability": {
                "description": "Disability benefits (tax-free, lump-sum or monthly)",
                "eligibility": "Service-related disability or illness",
            },
        },
    },
    "DE": {
        "administering_body": "Bundeswehr Social Welfare / Versorgungsamt",
        "benefits_portal": "https://www.bundeswehr.de/de/betreuung-fuersorge",
        "categories": {
            "healthcare": {
                "description": "Free medical care (Heilfuersorge) for service-connected conditions; Bundeswehrkrankenhaeuser",
                "eligibility": "Service-related condition; career soldiers get continued coverage",
            },
            "pension": {
                "description": "Soldatenversorgung; statutory pension insurance (Gesetzliche Rentenversicherung)",
                "eligibility": "Service period; time soldiers and voluntary service counted toward pension",
            },
            "disability": {
                "description": "Einsatzversorgung (deployment welfare); Wehrdienstbeschaedigung",
                "eligibility": "Injury/illness from military service or deployment",
            },
        },
    },
    "FR": {
        "administering_body": "Office National des Anciens Combattants et Victimes de Guerre (ONACVG)",
        "benefits_portal": "https://www.onac-vg.fr",
        "categories": {
            "healthcare": {
                "description": "Free medical care for service-connected injuries; military invalidity pension",
                "eligibility": "Recognized service-related injury or illness",
            },
            "pension": {
                "description": "Military retirement pension (pension militaire de retraite); Caisse des depots",
                "eligibility": "15-25 years service depending on rank and category",
            },
            "education": {
                "description": "Scholarships for children of veterans; ONACVG educational assistance",
                "eligibility": "Children of deceased or disabled veterans; means-tested",
            },
        },
    },
    "AU": {
        "administering_body": "Department of Veterans' Affairs (DVA)",
        "benefits_portal": "https://www.dva.gov.au",
        "categories": {
            "healthcare": {
                "description": "Gold Card (all conditions); White Card (accepted conditions); DVA health services",
                "eligibility": "Service-related; Gold Card for qualifying service/veterans 70+",
            },
            "education": {
                "description": "Veteran Education Scheme; TAFE/university support",
                "eligibility": "Service leavers; children of veterans",
            },
            "pension": {
                "description": "Service Pension (income-tested); Age Service Pension; veteran payment",
                "eligibility": "Qualifying service; age 60+ or permanent incapacity",
            },
            "disability": {
                "description": "Disability Compensation Payment; Military Rehabilitation and Compensation Act (MRCA)",
                "eligibility": "Service-related injury, disease, or death",
            },
            "housing": {
                "description": "DVA Home Care; Defence Home Ownership Assistance Scheme",
                "eligibility": "Qualifying service and assessed need",
            },
        },
    },
    "IL": {
        "administering_body": "Ministry of Defense - Rehabilitation and Benefits Division",
        "benefits_portal": "https://www.mod.gov.il",
        "categories": {
            "healthcare": {
                "description": "Free medical care for service-related injuries; rehabilitation hospitals",
                "eligibility": "Recognized service-connected disability",
            },
            "education": {
                "description": "Tuition assistance for discharged soldiers (Pikadon); academic grants",
                "eligibility": "Completed mandatory service; income supplement for discharged soldiers",
            },
            "disability": {
                "description": "IDF disabled veterans benefits; monthly allowance by disability percentage",
                "eligibility": "Disability rated by Medical Board during or after service",
            },
            "housing": {
                "description": "Housing assistance; mortgage subsidies for disabled veterans",
                "eligibility": "Disabled veterans with 20%+ disability rating",
            },
        },
    },
    "KR": {
        "administering_body": "Ministry of Patriots and Veterans Affairs (MPVA)",
        "benefits_portal": "https://www.mpva.go.kr",
        "categories": {
            "healthcare": {
                "description": "Free medical care at veterans hospitals; priority treatment",
                "eligibility": "Service-connected conditions; national merit recipients",
            },
            "education": {
                "description": "Tuition exemption for veterans and children; scholarship programs",
                "eligibility": "Veterans with distinguished service and their children",
            },
            "employment": {
                "description": "Veterans' employment preference in public sector (3-10% of new hires)",
                "eligibility": "Veterans with distinguished service; disabled veterans",
            },
        },
    },
}


# ── Functions ──────────────────────────────────────────────────────────────


def get_conscription_info(country: str) -> dict[str, Any] | None:
    """Return conscription data for a country code."""
    code = country.strip().upper()
    return dict(CONSCRIPTION_DATA.get(code)) if code in CONSCRIPTION_DATA else None


def get_military_branches(country: str) -> list[dict[str, Any]] | None:
    """Return the military branches and their basic info for a country."""
    code = country.strip().upper()
    branches = MILITARY_BRANCHES.get(code)
    if branches is None:
        return None
    return [dict(b) for b in branches]


def get_veteran_benefits(country: str, benefit_category: str | None = None) -> dict[str, Any] | None:
    """Return veteran benefits info, optionally filtered by category."""
    code = country.strip().upper()
    benefits = VETERAN_BENEFITS.get(code)
    if benefits is None:
        return None
    result = dict(benefits)
    if benefit_category and "categories" in result:
        cat = result["categories"].get(benefit_category.strip().lower())
        if cat:
            result["categories"] = {benefit_category: dict(cat)}
        else:
            return None
    return result


def get_enlistment_process(country: str) -> dict[str, Any] | None:
    """Return the enlistment process overview for a country."""
    con = CONSCRIPTION_DATA.get(country.strip().upper())
    if con is None:
        return None
    return {
        "country": country.strip().upper(),
        "conscription_active": con["active"],
        "registration_required": con.get("registration_required", False),
        "registration_age": con.get("registration_age", "N/A"),
        "duration_months": con.get("duration_months", 0),
        "notes": con.get("notes", ""),
    }


def list_mandatory_service_countries() -> list[str]:
    """Return country codes where conscription is currently active."""
    return sorted(code for code, data in CONSCRIPTION_DATA.items() if data.get("active") is True)


__all__ = [
    "CONSCRIPTION_DATA",
    "MILITARY_BRANCHES",
    "VETERAN_BENEFITS",
    "get_conscription_info",
    "get_military_branches",
    "get_veteran_benefits",
    "get_enlistment_process",
    "list_mandatory_service_countries",
]
