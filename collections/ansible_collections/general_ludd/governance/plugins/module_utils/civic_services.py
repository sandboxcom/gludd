#!/usr/bin/env python3
"""Civic services knowledge module for the governance collection.

Provides structured knowledge of everyday government interactions — passports,
IDs, licenses, permits, registrations, benefits, FOIA requests, and postal
services — across multiple countries. Designed as a lookup layer for agents
that need to answer "what does it take to get a passport?" or "where do I file
a building permit?" without scraping government websites at runtime.

Public surface::

    SERVICE_CATEGORIES      frozenset of service category names
    SERVICES                knowledge base keyed by service id
    REQUIRED_SERVICE_KEYS   keys every service entry must carry
    POSTAL_SYSTEMS          knowledge base keyed by ISO-3166 alpha-2 country code
    SERVICE_OFFICES         registry of physical offices keyed by (service, location)

    ServiceInfo             dataclass returned by lookup_service
    ServiceOffice           dataclass returned by find_service_office
    PostalSystem            dataclass returned by get_postal_info
    PostageRate             dataclass returned by get_postage_rate

    lookup_service(service_name, country)        -> ServiceInfo | None
    get_requirements(service_id, country=...)    -> list[str] | None
    get_processing_time(service_id)              -> dict[str, Any] | None
    find_service_office(service_id, location)    -> ServiceOffice | None
    get_postal_info(country)                     -> PostalSystem | None
    get_postage_rate(origin, destination, weight)-> PostageRate | None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SERVICE_CATEGORIES: frozenset[str] = frozenset({
    "identification",
    "licenses",
    "permits",
    "registrations",
    "benefits",
    "complaints",
    "information_request",
})

REQUIRED_SERVICE_KEYS: frozenset[str] = frozenset({
    "category",
    "issuing_body",
    "requirements",
    "processing_time",
    "cost",
    "online_portal",
    "appeal_process",
})


def _merge(country_variant: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Return *base* overlaid with any non-None keys from *country_variant*."""
    merged = dict(base)
    for key, value in country_variant.items():
        if value is not None:
            merged[key] = value
    return merged


def _normalize_service_name(name: str) -> str:
    """Normalize a human service name to its canonical id form.

    "driver's license" -> "drivers_license"
    "Passport"         -> "passport"
    "FOIA Request"     -> "foia_request"  (mapped below)
    """
    return (
        name.strip()
        .lower()
        .replace("'", "")
        .replace("\u2019", "")
        .replace("-", "_")
        .replace(" ", "_")
    )


# ---------------------------------------------------------------------------
# SERVICES knowledge base
# ---------------------------------------------------------------------------
# Each top-level entry provides defaults. The ``countries`` sub-dict carries
# country-specific overrides for issuing_body, requirements, cost, etc.
# ``lookup_service`` returns None for a country that has no variant entry.

SERVICES: dict[str, dict[str, Any]] = {
    "passport": {
        "category": "identification",
        "issuing_body": "National passport authority",
        "requirements": [
            "Completed application form",
            "Proof of citizenship (birth certificate or naturalization certificate)",
            "Government-issued photo identification",
            "Two compliant passport photos",
            "Applicable filing fee",
        ],
        "processing_time": {
            "routine": "6-8 weeks",
            "expedited": "2-3 weeks",
        },
        "cost": {
            "currency": "varies",
            "routine": "varies by country and validity period",
            "expedited": "additional expedite surcharge",
        },
        "online_portal": "varies by country (see country variant)",
        "appeal_process": (
            "If an application is denied the applicant may request an "
            "administrative review or file an appeal with the issuing "
            "authority, typically within 30-90 days of the denial notice."
        ),
        "countries": {
            "US": {
                "issuing_body": "U.S. Department of State",
                "requirements": [
                    "Form DS-11 (first-time) or DS-82 (renewal)",
                    "Proof of U.S. citizenship",
                    "Valid government-issued photo ID",
                    "One color passport photo",
                    "Passport book fee ($130) plus execution fee ($35) for DS-11",
                ],
                "cost": {
                    "currency": "USD",
                    "routine_book": 130,
                    "execution_fee": 35,
                    "expedite_surcharge": 60,
                },
                "online_portal": "https://travel.state.gov/content/travel/en/passports.html",
            },
            "GB": {
                "issuing_body": "His Majesty's Passport Office",
                "requirements": [
                    "Completed online application",
                    "Two passport photos",
                    "Original birth or adoption certificate",
                    "Countersignatory (for first-time adult applications)",
                    "Application fee (GBP 93.50 standard adult online)",
                ],
                "cost": {
                    "currency": "GBP",
                    "standard_adult_online": 93.50,
                    "paper_adult": 104.50,
                },
                "online_portal": "https://www.gov.uk/apply-renew-passport",
            },
            "CA": {
                "issuing_body": "Service Canada (Immigration, Refugees and Citizenship Canada)",
                "requirements": [
                    "Completed application form",
                    "Proof of Canadian citizenship",
                    "Two identical passport photos signed by a guarantor",
                    "Valid travel document or identification",
                    "Application fee (CAD $120 adult, 5-year)",
                ],
                "cost": {
                    "currency": "CAD",
                    "adult_5yr": 120,
                    "adult_10yr": 160,
                },
                "online_portal": "https://www.canada.ca/en/immigration-refugees-citizenship/services/canadian-passports.html",
            },
        },
    },
    "national_id": {
        "category": "identification",
        "issuing_body": "National identity registry",
        "requirements": [
            "Completed application form",
            "Proof of identity and citizenship",
            "Biometric capture (photo and/or fingerprints)",
            "Proof of address/residence",
            "Filing fee (where applicable)",
        ],
        "processing_time": {
            "routine": "2-4 weeks",
            "expedited": "1-2 weeks (where available)",
        },
        "cost": {
            "currency": "varies",
            "routine": "free to nominal fee in most countries",
        },
        "online_portal": "varies by country",
        "appeal_process": (
            "Denials may be appealed through the issuing registry's "
            "administrative review process or via judicial review."
        ),
        "countries": {
            "US": {
                "issuing_body": "No national ID card system (REAL ID driver's license serves as federal ID)",
                "requirements": [
                    "REAL ID-compliant driver's license or state ID",
                    "Proof of identity (passport or birth certificate)",
                    "Proof of Social Security Number",
                    "Two proofs of address",
                    "Lawful status documentation",
                ],
                "online_portal": "https://www.dhs.gov/real-id",
            },
            "DE": {
                "issuing_body": "Bundesamt fuer Migration und Fluechtlinge (BAMF) / local buergeramt",
                "requirements": [
                    "Anmeldung (registration certificate)",
                    "Valid passport or travel document",
                    "One biometric photo",
                    "Application fee (EUR 37 for adults)",
                ],
                "cost": {"currency": "EUR", "routine": 37},
                "online_portal": "https://www.bmi.bund.de",
            },
            "FR": {
                "issuing_body": "Agence nationale des titres securises (ANTS)",
                "requirements": [
                    "Justificatif d'identite",
                    "Justificatif de domicile",
                    "Photo d'identite conforme",
                    "Pre-demande en ligne (ANTS)",
                ],
                "cost": {"currency": "EUR", "CNI": "free"},
                "online_portal": "https://ants.gouv.fr",
            },
        },
    },
    "drivers_license": {
        "category": "licenses",
        "issuing_body": "State/provincial department of motor vehicles",
        "requirements": [
            "Proof of identity and date of birth",
            "Proof of residency/state residence",
            "Social Security Number or equivalent national ID",
            "Pass written knowledge test",
            "Pass road skills test",
            "Pass vision screening",
            "Application fee",
        ],
        "processing_time": {
            "routine": "2-4 weeks after test passage",
            "expedited": "same-day temporary license common",
        },
        "cost": {
            "currency": "varies",
            "routine": "varies by jurisdiction ($20-$90 typical)",
        },
        "online_portal": "state/provincial DMV portal",
        "appeal_process": (
            "License suspensions or denials may be appealed through an "
            "administrative hearing at the DMV, with further appeal to "
            "state/provincial courts available."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Department of Motor Vehicles (DMV / BMV / DPS)",
                "requirements": [
                    "Proof of identity (birth certificate or passport)",
                    "Proof of Social Security Number",
                    "Two proofs of state residency",
                    "Pass knowledge, road, and vision tests",
                    "Application fee ($20-$90 by state)",
                ],
                "online_portal": "https://www.dmv.org (state-specific portals)",
            },
            "GB": {
                "issuing_body": "Driver and Vehicle Licensing Agency (DVLA)",
                "requirements": [
                    "Valid UK passport or identity document",
                    "National Insurance number",
                    "Addresses for last 3 years",
                    "Pass theory and practical driving tests",
                    "Provisional license fee (GBP 34 online)",
                ],
                "cost": {"currency": "GBP", "provisional_online": 34, "full_first": 62},
                "online_portal": "https://www.gov.uk/apply-first-provisional-driving-licence",
            },
            "CA": {
                "issuing_body": "Provincial Ministry of Transportation (e.g., Ontario MTO, ICBC in BC)",
                "requirements": [
                    "Proof of identity and legal presence",
                    "Pass vision, knowledge, and road tests",
                    "Application fee (varies by province)",
                ],
                "online_portal": "https://www.canada.ca/en/services/transport/driving.html",
            },
        },
    },
    "marriage_license": {
        "category": "licenses",
        "issuing_body": "County clerk / local vital records office",
        "requirements": [
            "Valid government-issued photo ID for both parties",
            "Proof of age (18+, or parental consent if younger)",
            "Social Security Numbers or equivalent",
            "Proof of termination of any prior marriages (if applicable)",
            "Application fee",
            "Some jurisdictions require blood test or waiting period",
        ],
        "processing_time": {
            "routine": "same day to 5 days (varies by jurisdiction)",
            "expedited": "same-day issuance common",
        },
        "cost": {
            "currency": "varies",
            "routine": "$30-$100 typical",
        },
        "online_portal": "county clerk / registrar portal",
        "appeal_process": (
            "Denials based on eligibility (age, prior marriage status) may be "
            "appealed through the local court system or registrar review."
        ),
        "countries": {
            "US": {
                "issuing_body": "County Clerk or Court Clerk office",
                "requirements": [
                    "Valid photo ID for both parties (driver's license or passport)",
                    "Social Security Numbers",
                    "Divorce decree or death certificate if previously married",
                    "Fee ($30-$100 depending on county)",
                    "Some states impose a 1-3 day waiting period",
                ],
                "online_portal": "county clerk websites (varies by county)",
            },
            "GB": {
                "issuing_body": "Local register office",
                "requirements": [
                    "Give notice of marriage at local register office (28 days)",
                    "Valid passport or birth certificate",
                    "Proof of address",
                    "Fee for notice (GBP 35) and certificate (GBP 11)",
                ],
                "cost": {"currency": "GBP", "notice": 35, "certificate": 11},
                "online_portal": "https://www.gov.uk/marriage-certificates",
            },
        },
    },
    "business_registration": {
        "category": "registrations",
        "issuing_body": "National/regional company registry or secretary of state",
        "requirements": [
            "Unique business name (availability search)",
            "Articles of incorporation / organization",
            "Registered agent designation",
            "Business purpose statement",
            "Filing fee",
            "Tax ID application (EIN or equivalent)",
        ],
        "processing_time": {
            "routine": "1-7 business days",
            "expedited": "same-day to 24 hours available",
        },
        "cost": {
            "currency": "varies",
            "routine": "$50-$500 by entity type and jurisdiction",
        },
        "online_portal": "secretary of state / companies house portal",
        "appeal_process": (
            "Name rejection or filing denials may be appealed to the "
            "secretary of state or equivalent registrar."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Secretary of State (e.g., Delaware Division of Corporations)",
                "requirements": [
                    "Certificate of Incorporation or Articles of Organization",
                    "Registered agent in the state of formation",
                    "Filing fee ($50-$500 by state and entity type)",
                    "Obtain federal EIN from IRS (free, online)",
                ],
                "cost": {"currency": "USD", "LLC_range": "50-500", "corp_range": "100-1000"},
                "online_portal": "https://www.irs.gov/businesses/small-businesses-self-employed/apply-for-an-employer-identification-number-ein-online",
            },
            "GB": {
                "issuing_body": "Companies House",
                "requirements": [
                    "Memorandum and articles of association",
                    "Company name (availability check)",
                    "Registered office address",
                    "Director and shareholder details (SIC code)",
                    "Filing fee (GBP 12 online, GBP 40 paper)",
                ],
                "cost": {"currency": "GBP", "online_incorporation": 12},
                "online_portal": "https://www.gov.uk/limited-company-formation",
            },
        },
    },
    "property_deed": {
        "category": "registrations",
        "issuing_body": "County recorder / land registry",
        "requirements": [
            "Executed deed (warranty, quitclaim, or grant deed)",
            "Prior owner's deed or title reference",
            "Legal property description",
            "Transfer tax payment",
            "Recording fee",
            "Title search / title insurance (recommended)",
        ],
        "processing_time": {
            "routine": "1-4 weeks for recording",
            "expedited": "24-48 hour recording available in some counties",
        },
        "cost": {
            "currency": "varies",
            "routine": "recording fee + transfer tax by jurisdiction",
        },
        "online_portal": "county recorder / land registry e-recording portal",
        "appeal_process": (
            "Recording rejections (defective deed, missing notarization) may be "
            "corrected and refiled. Title disputes resolved through civil court."
        ),
        "countries": {
            "US": {
                "issuing_body": "County Recorder or Clerk (land records)",
                "requirements": [
                    "Notarized deed with legal description",
                    "Preliminary change of ownership report (some states)",
                    "Transfer tax (varies by county)",
                    "Recording fee ($10-$100 first page)",
                ],
                "online_portal": "county recorder e-recording portals",
            },
            "GB": {
                "issuing_body": "HM Land Registry",
                "requirements": [
                    "Transfer deed (TR1)",
                    "Official copies of title",
                    "Stamp Duty Land Tax certificate",
                    "Registration fee (scale 1, varies by value)",
                ],
                "cost": {"currency": "GBP", "scale1_from": 80},
                "online_portal": "https://www.gov.uk/government/organisations/hm-land-registry",
            },
        },
    },
    "building_permit": {
        "category": "permits",
        "issuing_body": "Municipal building / planning department",
        "requirements": [
            "Completed permit application",
            "Site plan and construction drawings",
            "Zoning compliance verification",
            "Licensed contractor information (most jurisdictions)",
            "Impact fees and plan review fees",
            "Energy/code compliance documentation",
        ],
        "processing_time": {
            "routine": "2-8 weeks depending on scope",
            "expedited": "1-2 weeks for minor projects in some cities",
        },
        "cost": {
            "currency": "varies",
            "routine": "based on project valuation ($/sq ft or percentage)",
        },
        "online_portal": "municipal permitting portal",
        "appeal_process": (
            "Permit denials may be appealed to the local Board of Zoning "
            "Appeals or Building Code Board of Appeals."
        ),
        "countries": {
            "US": {
                "issuing_body": "City/County Building Department or Department of Permitting Services",
                "requirements": [
                    "Two sets of stamped construction drawings",
                    "Site plan showing setbacks and easements",
                    "Energy code compliance (REScheck or COMcheck)",
                    "Permit fee based on project valuation",
                    "Licensed contractor signature (most jurisdictions)",
                ],
                "online_portal": "municipal e-permitting portals (e.g., Accela-based)",
            },
            "CA": {
                "issuing_body": "Municipal building department / Service Ontario for provincial",
                "requirements": [
                    "Construction drawings and site plan",
                    "Building Code Identification Number (BCIN) designer",
                    "Energy efficiency design summary",
                    "Permit fee (varies by municipality)",
                ],
                "online_portal": "municipal permitting portals",
            },
        },
    },
    "library_card": {
        "category": "registrations",
        "issuing_body": "Public library system",
        "requirements": [
            "Proof of address/residence in the service area",
            "Photo identification",
            "Completed application (often online)",
            "Parent/guardian signature for minors",
            "No fee for residents (non-resident fee in some systems)",
        ],
        "processing_time": {
            "routine": "same day / instant for online registration",
            "expedited": "N/A (already same-day)",
        },
        "cost": {
            "currency": "varies",
            "routine": "free for residents",
        },
        "online_portal": "public library system website",
        "appeal_process": (
            "Card revocations (overdue fines, lost materials) may be appealed "
            "to the library director. Outstanding obligations must be resolved."
        ),
        "countries": {
            "US": {
                "issuing_body": "Municipal or county public library system",
                "requirements": [
                    "Photo ID with current address",
                    "Proof of residence (utility bill, lease)",
                    "Completed application (online or in-person)",
                    "Free for residents; non-resident fee $25-$100/yr",
                ],
                "online_portal": "local public library websites",
            },
            "GB": {
                "issuing_body": "Local authority public library service",
                "requirements": [
                    "Proof of name and address",
                    "Online registration or in-branch",
                    "Free for residents",
                ],
                "cost": {"currency": "GBP", "resident": "free"},
                "online_portal": "https://www.gov.uk/local-library-services",
            },
        },
    },
    "postal_services": {
        "category": "registrations",
        "issuing_body": "National postal operator",
        "requirements": [
            "Valid sender and recipient addresses",
            "Appropriate postage (see get_postage_rate)",
            "Packaging compliant with size/weight limits",
            "Customs declaration for international mail (CN22/CN23)",
        ],
        "processing_time": {
            "routine": "1-3 days domestic, 1-3 weeks international",
            "expedited": "next-day domestic, 3-5 days international express",
        },
        "cost": {
            "currency": "varies by country",
            "routine": "see get_postage_rate for specific rates",
        },
        "online_portal": "see POSTAL_SYSTEMS for country-specific portals",
        "appeal_process": (
            "Lost or damaged mail claims are filed with the national postal "
            "operator. Claims require proof of postage, value, and damage/loss."
        ),
        "countries": {
            "US": {
                "issuing_body": "United States Postal Service (USPS)",
                "online_portal": "https://www.usps.com",
            },
            "GB": {
                "issuing_body": "Royal Mail Group",
                "online_portal": "https://www.royalmail.com",
            },
            "CA": {
                "issuing_body": "Canada Post",
                "online_portal": "https://www.canadapost.ca",
            },
        },
    },
    "military_enlistment": {
        "category": "registrations",
        "issuing_body": "National armed forces recruitment command",
        "requirements": [
            "Age 17-34 (varies by country and branch)",
            "High school diploma or equivalent",
            "U.S. citizenship or permanent resident status (or equivalent)",
            "Pass Armed Services Vocational Aptitude Battery (ASVAB)",
            "Meet medical and physical fitness standards",
            "Moral character review (background check)",
        ],
        "processing_time": {
            "routine": "1-6 months from application to ship date",
            "expedited": "delayed entry program common",
        },
        "cost": {
            "currency": "varies",
            "routine": "no cost to applicant",
        },
        "online_portal": "national military recruitment website",
        "appeal_process": (
            "Enlistment disqualifications (medical, moral) may be appealed or "
            "waived through the recruiting command's waiver process."
        ),
        "countries": {
            "US": {
                "issuing_body": "Military Entrance Processing Command (MEPS) via branch recruiters",
                "requirements": [
                    "Age 17-34 (with parental consent under 18)",
                    "High school diploma or GED",
                    "U.S. citizen or permanent resident",
                    "Pass ASVAB and medical exam at MEPS",
                    "Meet height/weight and fitness standards",
                ],
                "online_portal": "https://www.military.com/join-armed-forces",
            },
            "GB": {
                "issuing_body": "Ministry of Defence (each service branch)",
                "requirements": [
                    "Age 16-35 (varies by role)",
                    "British, Commonwealth, or Irish citizen",
                    "Pass entrance tests and medical",
                    "Meet fitness standards",
                ],
                "online_portal": "https://www.armedforces.co.uk",
            },
        },
    },
    "voter_registration": {
        "category": "registrations",
        "issuing_body": "State/provincial election office or national election commission",
        "requirements": [
            "Proof of citizenship and age (18+)",
            "Proof of residence in the electoral district",
            "Completed registration form (online, mail, or in-person)",
            "Some jurisdictions require party affiliation for primary elections",
        ],
        "processing_time": {
            "routine": "2-4 weeks for processing",
            "expedited": "same-day registration available in some states",
        },
        "cost": {
            "currency": "varies",
            "routine": "free",
        },
        "online_portal": "national/state election office portal",
        "appeal_process": (
            "Registration denials may be appealed to the local board of "
            "elections or election commission."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Board of Elections / Secretary of State",
                "requirements": [
                    "U.S. citizen, age 18+ by election day",
                    "Resident of the state/county",
                    "Completed registration (online, mail via NVRA, or DMV auto-registration)",
                    "22 states + DC offer same-day registration",
                ],
                "cost": {"currency": "USD", "registration": "free"},
                "online_portal": "https://vote.gov",
            },
            "GB": {
                "issuing_body": "Electoral Registration Officer (local council)",
                "requirements": [
                    "British, Commonwealth, or EU citizen resident in UK",
                    "Age 16+ (vote at 18)",
                    "National Insurance number",
                    "Online registration takes 5 minutes",
                ],
                "cost": {"currency": "GBP", "registration": "free"},
                "online_portal": "https://www.gov.uk/register-to-vote",
            },
            "CA": {
                "issuing_body": "Elections Canada",
                "requirements": [
                    "Canadian citizen, age 18+",
                    "Proof of identity and address",
                    "Online, mail, or in-person at Elections Canada office",
                ],
                "cost": {"currency": "CAD", "registration": "free"},
                "online_portal": "https://www.elections.ca",
            },
        },
    },
    "tax_filing": {
        "category": "registrations",
        "issuing_body": "National revenue/tax authority",
        "requirements": [
            "Tax identification number (SSN/ITIN/NINO/etc.)",
            "Records of income (W-2, 1099, P60, T4, etc.)",
            "Records of deductions and credits",
            "Completed tax return forms",
            "Payment for any tax owed (or refund processing)",
        ],
        "processing_time": {
            "routine": "refund in 2-6 weeks (e-file), 6-8 weeks (paper)",
            "expedited": "direct deposit speeds refund by 1-2 weeks",
        },
        "cost": {
            "currency": "varies",
            "routine": "free file available; paid software $0-$200",
        },
        "online_portal": "national tax authority e-filing portal",
        "appeal_process": (
            "Tax return adjustments or audit findings may be appealed through "
            "the tax authority's appeals process, then to tax court."
        ),
        "countries": {
            "US": {
                "issuing_body": "Internal Revenue Service (IRS)",
                "requirements": [
                    "SSN or ITIN",
                    "W-2, 1099, and other income statements",
                    "Form 1040 (individual) or business return",
                    "Records of deductions (mortgage interest, charitable, etc.)",
                    "Free File available for income under $79,000",
                ],
                "cost": {"currency": "USD", "free_file_threshold": 79000},
                "online_portal": "https://www.irs.gov/filing",
            },
            "GB": {
                "issuing_body": "HM Revenue and Customs (HMRC)",
                "requirements": [
                    "National Insurance number",
                    "P60 or P45 from employer(s)",
                    "Self Assessment (SA100) if self-employed or complex affairs",
                    "Records of expenses and reliefs",
                ],
                "cost": {"currency": "GBP", "self_assessment_filing": "free online"},
                "online_portal": "https://www.gov.uk/self-assessment-tax-returns",
            },
            "CA": {
                "issuing_body": "Canada Revenue Agency (CRA)",
                "requirements": [
                    "Social Insurance Number (SIN)",
                    "T4 slips from employers",
                    "Records of RRSP contributions, deductions, credits",
                    "NETFILE-certified software or paper return",
                ],
                "cost": {"currency": "CAD", "NETFILE": "free via certified software"},
                "online_portal": "https://www.canada.ca/en/revenue-agency/services/e-services/e-services-individuals/account-individuals.html",
            },
        },
    },
    "benefits_claims": {
        "category": "benefits",
        "issuing_body": "National social security / benefits administration",
        "requirements": [
            "Proof of identity and eligibility category",
            "Work credits / contribution history (where applicable)",
            "Medical documentation (for disability claims)",
            "Financial records (for income-tested benefits)",
            "Completed claim application",
        ],
        "processing_time": {
            "routine": "2-6 months (disability), 2-8 weeks (retirement/unemployment)",
            "expedited": "expedited processing for dire need / terminal illness cases",
        },
        "cost": {
            "currency": "varies",
            "routine": "no cost to file a claim",
        },
        "online_portal": "national benefits agency portal",
        "appeal_process": (
            "Denied claims follow a multi-level appeal: reconsideration -> "
            "administrative law judge -> appeals council -> federal court."
        ),
        "countries": {
            "US": {
                "issuing_body": "Social Security Administration (SSA)",
                "requirements": [
                    "SSN and proof of age",
                    "W-2 / self-employment tax records",
                    "Medical records (disability claims)",
                    "Bank information for direct deposit",
                    "Online application via my Social Security",
                ],
                "online_portal": "https://www.ssa.gov/benefits",
            },
            "GB": {
                "issuing_body": "Department for Work and Pensions (DWP)",
                "requirements": [
                    "National Insurance number",
                    "Bank/building society account details",
                    "Income and housing cost details (Universal Credit)",
                    "Fit note from GP (for health-related benefits)",
                ],
                "online_portal": "https://www.gov.uk/browse/benefits",
            },
        },
    },
    "foia_request": {
        "category": "information_request",
        "issuing_body": "Federal/national agency FOIA office",
        "requirements": [
            "Written request describing the records sought",
            "Agency identified (addressed to the correct FOIA office)",
            "Reasonable description of records (not overly broad)",
            "Fee category declaration (news, commercial, other)",
            "Some agencies accept online submission via FOIA portal",
        ],
        "processing_time": {
            "routine": "20 business days (statutory), often 1-6 months in practice",
            "expedited": "expedited review for urgent need (media, due-process)",
        },
        "cost": {
            "currency": "varies",
            "routine": "first 100 pages free, then per-page fees",
        },
        "online_portal": "national FOIA request portal",
        "appeal_process": (
            "Denials may be appealed to the agency's FOIA appeals office within "
            "90 days, then to federal court under the FOIA statute."
        ),
        "countries": {
            "US": {
                "issuing_body": "Agency FOIA Officers (each federal agency)",
                "requirements": [
                    "Written request to the specific agency FOIA office",
                    "Description of records sought",
                    "Fee category and willingness to pay (optional cap)",
                    "Online submission via National FOIA Portal",
                ],
                "cost": {"currency": "USD", "first_pages_free": 100},
                "online_portal": "https://www.foia.gov",
            },
            "GB": {
                "issuing_body": "Information Commissioner's Office (ICO) / public authority",
                "requirements": [
                    "Written request to the public authority",
                    "Description of information sought",
                    "Authority must respond within 20 working days",
                ],
                "cost": {"currency": "GBP", "standard": "free up to cost limit"},
                "online_portal": "https://ico.org.uk",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Physical service offices
# ---------------------------------------------------------------------------
# Keyed by (service_id, location). Locations are major cities. Not exhaustive;
# find_service_office returns None for uncovered (service, location) pairs.

SERVICE_OFFICES: dict[tuple[str, str], dict[str, str]] = {
    ("passport", "New York"): {
        "office_name": "New York Passport Agency",
        "address": "376 Hudson Street, New York, NY 10014",
        "hours": "Mon-Fri 8:00am-3:00pm (by appointment)",
        "phone": "1-877-487-2778",
    },
    ("passport", "Los Angeles"): {
        "office_name": "Los Angeles Passport Agency",
        "address": "11000 Wilshire Blvd, Suite 1000, Los Angeles, CA 90024",
        "hours": "Mon-Fri 8:00am-3:00pm (by appointment)",
        "phone": "1-877-487-2778",
    },
    ("passport", "Chicago"): {
        "office_name": "Chicago Passport Agency",
        "address": "230 South Dearborn Street, Suite 381, Chicago, IL 60604",
        "hours": "Mon-Fri 9:00am-4:00pm (by appointment)",
        "phone": "1-877-487-2778",
    },
    ("drivers_license", "New York"): {
        "office_name": "NYC DMV (Manhattan)",
        "address": "11 Greenwich Street, New York, NY 10004",
        "hours": "Mon-Fri 8:00am-4:00pm, Sat 8:00am-12:00pm",
        "phone": "1-518-486-9786",
    },
    ("business_registration", "Wilmington"): {
        "office_name": "Delaware Division of Corporations",
        "address": "401 Federal Street, Suite 4, Dover, DE 19901",
        "hours": "Mon-Fri 8:00am-4:30pm",
        "phone": "1-302-739-3073",
    },
    ("building_permit", "San Francisco"): {
        "office_name": "San Francisco Department of Building Inspection",
        "address": "49 South Van Ness Avenue, San Francisco, CA 94103",
        "hours": "Mon-Fri 7:30am-5:00pm (permit counter closes at 4:00pm)",
        "phone": "1-415-558-6088",
    },
    ("library_card", "New York"): {
        "office_name": "New York Public Library (Stephen A. Schwarzman Building)",
        "address": "476 Fifth Avenue, New York, NY 10018",
        "hours": "Mon-Sat 10:00am-6:00pm",
        "phone": "1-917-275-6975",
    },
    ("foia_request", "Washington"): {
        "office_name": "National FOIA Portal (online submission)",
        "address": "Online via https://www.foia.gov",
        "hours": "24/7 online portal",
        "phone": "N/A (online)",
    },
}


# ---------------------------------------------------------------------------
# POSTAL_SYSTEMS knowledge base
# ---------------------------------------------------------------------------

POSTAL_SYSTEMS: dict[str, dict[str, Any]] = {
    "US": {
        "name": "USPS",
        "full_name": "United States Postal Service",
        "services": [
            "First-Class Mail", "Priority Mail", "Priority Mail Express",
            "Media Mail", "Retail Ground", "International shipping",
        ],
        "tracking_url": "https://tools.usps.com/go/TrackConfirmAction",
        "rate_url": "https://postcalc.usps.com",
        "customs_required": True,
        "currency": "USD",
        "domestic_base_rate": 0.73,
        "international_base_rate": 1.50,
        "per_100g_domestic": 0.20,
        "per_100g_international": 0.85,
        "domestic_eta_days": 3,
        "international_eta_days": 14,
    },
    "GB": {
        "name": "Royal Mail",
        "full_name": "Royal Mail Group Ltd",
        "services": [
            "1st Class", "2nd Class", "Signed For", "Special Delivery Guaranteed",
            "International Standard", "International Tracked",
        ],
        "tracking_url": "https://www.royalmail.com/track-your-item",
        "rate_url": "https://www.royalmail.com/prices",
        "customs_required": True,
        "currency": "GBP",
        "domestic_base_rate": 1.25,
        "international_base_rate": 2.75,
        "per_100g_domestic": 0.35,
        "per_100g_international": 0.90,
        "domestic_eta_days": 2,
        "international_eta_days": 12,
    },
    "CA": {
        "name": "Canada Post",
        "full_name": "Canada Post Corporation",
        "services": [
            "Lettermail", "Registered Mail", "Xpresspost", "Priority",
            "International Parcel (Air/Surface)",
        ],
        "tracking_url": "https://www.canadapost.ca/trackweb",
        "rate_url": "https://www.canadapost.ca/shippping-rates-prices",
        "customs_required": True,
        "currency": "CAD",
        "domestic_base_rate": 1.07,
        "international_base_rate": 2.50,
        "per_100g_domestic": 0.30,
        "per_100g_international": 0.95,
        "domestic_eta_days": 4,
        "international_eta_days": 14,
    },
    "DE": {
        "name": "Deutsche Post",
        "full_name": "Deutsche Post AG (DHL Group)",
        "services": [
            "Standardbrief", "Kompaktbrief", "Grossbrief", "Einschreiben",
            "DHL Paket", "DHL Express International",
        ],
        "tracking_url": "https://www.deutschepost.de/de/s/sendungsverfolgung.html",
        "rate_url": "https://www.deutschepost.de/de/p/paketpreise.html",
        "customs_required": True,
        "currency": "EUR",
        "domestic_base_rate": 0.85,
        "international_base_rate": 1.60,
        "per_100g_domestic": 0.25,
        "per_100g_international": 0.70,
        "domestic_eta_days": 2,
        "international_eta_days": 10,
    },
    "FR": {
        "name": "La Poste",
        "full_name": "Groupe La Poste",
        "services": [
            "Lettre Prioritaire", "Lettre Vert", "Lettre Suivie",
            "Colissimo", "Chronopost International",
        ],
        "tracking_url": "https://www.laposte.fr/outils/suivre-vos-envois",
        "rate_url": "https://www.laposte.fr/tarifs",
        "customs_required": True,
        "currency": "EUR",
        "domestic_base_rate": 1.16,
        "international_base_rate": 1.80,
        "per_100g_domestic": 0.28,
        "per_100g_international": 0.75,
        "domestic_eta_days": 2,
        "international_eta_days": 11,
    },
    "AU": {
        "name": "Australia Post",
        "full_name": "Australia Post",
        "services": [
            "Letter", "Large Letter", "Registered Post", "Express Post",
            "International Standard", "International Express",
        ],
        "tracking_url": "https://auspost.com.au/mystatus",
        "rate_url": "https://auspost.com.au/parcels-mail/calculate-postage-delivery-time",
        "customs_required": True,
        "currency": "AUD",
        "domestic_base_rate": 1.20,
        "international_base_rate": 2.80,
        "per_100g_domestic": 0.30,
        "per_100g_international": 1.00,
        "domestic_eta_days": 3,
        "international_eta_days": 14,
    },
    "JP": {
        "name": "Japan Post",
        "full_name": "Japan Post Holdings Co., Ltd.",
        "services": [
            "Yu-Pack", "Yu-Mail", "Kan-i-Kaki-Komi", "EMS",
            "International Parcel (AIR/SAL/SEA)",
        ],
        "tracking_url": "https://trackings.post.japanpost.jp/services/srv/search/",
        "rate_url": "https://www.post.japanpost.jp/int/charge/",
        "customs_required": True,
        "currency": "JPY",
        "domestic_base_rate": 84,
        "international_base_rate": 150,
        "per_100g_domestic": 20,
        "per_100g_international": 90,
        "domestic_eta_days": 2,
        "international_eta_days": 10,
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ServiceInfo:
    """A resolved civic service entry for a specific country."""

    service_id: str
    category: str
    issuing_body: str
    requirements: list[str]
    processing_time: dict[str, Any]
    cost: dict[str, Any]
    online_portal: str
    appeal_process: str
    country: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


@dataclass
class ServiceOffice:
    """A physical office location for an in-person civic service."""

    service_id: str
    location: str
    office_name: str
    address: str
    hours: str
    phone: str

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


@dataclass
class PostalSystem:
    """National postal operator information."""

    country_code: str
    name: str
    full_name: str
    services: list[str]
    tracking_url: str
    rate_url: str
    customs_required: bool

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


@dataclass
class PostageRate:
    """Estimated postage for a route and weight."""

    origin: str
    destination: str
    weight_grams: int
    service_class: str
    cost: float
    currency: str
    estimated_days: int

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def lookup_service(service_name: str, country: str) -> ServiceInfo | None:
    """Look up a civic service by name and country.

    Parameters
    ----------
    service_name:
        Human-readable service name. Normalized case-insensitively and with
        spaces/hyphens/apostrophes collapsed to canonical ids
        (e.g. ``"driver's license"`` -> ``"drivers_license"``).
    country:
        ISO-3166 alpha-2 country code (e.g. ``"US"``, ``"GB"``).

    Returns
    -------
    ServiceInfo | None
        Populated dataclass if the service exists and has a variant for the
        requested country; ``None`` otherwise.
    """
    canonical = _normalize_service_name(service_name)
    base = SERVICES.get(canonical)
    if base is None:
        return None

    variants = base.get("countries", {})
    variant = variants.get(country.upper())
    if variant is None:
        return None

    merged = _merge(variant, base)
    return ServiceInfo(
        service_id=canonical,
        category=merged["category"],
        issuing_body=merged["issuing_body"],
        requirements=merged["requirements"],
        processing_time=merged["processing_time"],
        cost=merged["cost"],
        online_portal=merged["online_portal"],
        appeal_process=merged["appeal_process"],
        country=country.upper(),
    )


def get_requirements(
    service_id: str, country: str | None = None
) -> list[str] | None:
    """Return the document/preparation requirements for a service.

    If *country* is provided and a country-specific variant exists, its
    requirements are returned; otherwise the default requirements are used.
    Returns ``None`` for an unknown service.
    """
    canonical = _normalize_service_name(service_id)
    base = SERVICES.get(canonical)
    if base is None:
        return None

    if country is not None:
        variant = base.get("countries", {}).get(country.upper())
        if variant is not None and variant.get("requirements"):
            return list(variant["requirements"])

    return list(base["requirements"]) if base.get("requirements") else None


def get_processing_time(service_id: str) -> dict[str, Any] | None:
    """Return the processing-time tiers (routine, expedited, etc.) for a service.

    Returns ``None`` for an unknown service.
    """
    canonical = _normalize_service_name(service_id)
    base = SERVICES.get(canonical)
    if base is None:
        return None
    return dict(base.get("processing_time", {}))


def find_service_office(
    service_id: str, location: str
) -> ServiceOffice | None:
    """Find a physical office for an in-person civic service.

    Parameters
    ----------
    service_id:
        Canonical service id (e.g. ``"passport"``).
    location:
        City or region name (e.g. ``"New York"``).

    Returns
    -------
    ServiceOffice | None
        The office record if one is registered for the (service, location)
        pair; ``None`` otherwise.
    """
    canonical = _normalize_service_name(service_id)
    key = (canonical, location.strip())
    record = SERVICE_OFFICES.get(key)
    if record is None:
        return None
    return ServiceOffice(
        service_id=canonical,
        location=location.strip(),
        office_name=record["office_name"],
        address=record["address"],
        hours=record["hours"],
        phone=record["phone"],
    )


# ---------------------------------------------------------------------------
# Postal functions
# ---------------------------------------------------------------------------

def get_postal_info(country: str) -> PostalSystem | None:
    """Return postal operator information for a country.

    Parameters
    ----------
    country:
        ISO-3166 alpha-2 country code.

    Returns
    -------
    PostalSystem | None
        ``None`` if the country has no postal system in the knowledge base.
    """
    info = POSTAL_SYSTEMS.get(country.upper())
    if info is None:
        return None
    return PostalSystem(
        country_code=country.upper(),
        name=info["name"],
        full_name=info.get("full_name", info["name"]),
        services=list(info["services"]),
        tracking_url=info["tracking_url"],
        rate_url=info.get("rate_url", ""),
        customs_required=bool(info["customs_required"]),
    )


def get_postage_rate(
    origin: str, destination: str, weight: int
) -> PostageRate | None:
    """Estimate postage for a route and package weight.

    Parameters
    ----------
    origin:
        ISO-3166 alpha-2 country code of the sender.
    destination:
        ISO-3166 alpha-2 country code of the recipient.
    weight:
        Package weight in grams.

    Returns
    -------
    PostageRate | None
        ``None`` if either country is unknown. International routes cost more
        than domestic; heavier packages cost more than lighter ones.
    """
    origin_info = POSTAL_SYSTEMS.get(origin.upper())
    dest_info = POSTAL_SYSTEMS.get(destination.upper())
    if origin_info is None or dest_info is None:
        return None

    is_international = origin.upper() != destination.upper()
    weight_grams = max(0, weight)

    if is_international:
        base = float(origin_info["international_base_rate"])
        per_100g = float(origin_info["per_100g_international"])
        eta = int(origin_info["international_eta_days"])
        service_class = "International Standard"
    else:
        base = float(origin_info["domestic_base_rate"])
        per_100g = float(origin_info["per_100g_domestic"])
        eta = int(origin_info["domestic_eta_days"])
        service_class = "Domestic Standard"

    increments = weight_grams / 100.0
    total = round(base + per_100g * increments, 2)

    return PostageRate(
        origin=origin.upper(),
        destination=destination.upper(),
        weight_grams=weight_grams,
        service_class=service_class,
        cost=total,
        currency=origin_info["currency"],
        estimated_days=eta,
    )


CIVIC_SERVICES = SERVICES


__all__ = [
    "SERVICE_CATEGORIES",
    "REQUIRED_SERVICE_KEYS",
    "SERVICES",
    "CIVIC_SERVICES",
    "SERVICE_OFFICES",
    "POSTAL_SYSTEMS",
    "ServiceInfo",
    "ServiceOffice",
    "PostalSystem",
    "PostageRate",
    "lookup_service",
    "get_requirements",
    "get_processing_time",
    "find_service_office",
    "get_postal_info",
    "get_postage_rate",
]
