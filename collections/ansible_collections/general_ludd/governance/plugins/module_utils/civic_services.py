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

SERVICE_CATEGORIES: frozenset[str] = frozenset(
    {
        "identification",
        "licenses",
        "permits",
        "registrations",
        "benefits",
        "complaints",
        "information_request",
    }
)

REQUIRED_SERVICE_KEYS: frozenset[str] = frozenset(
    {
        "category",
        "issuing_body",
        "requirements",
        "processing_time",
        "cost",
        "online_portal",
        "appeal_process",
    }
)


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
    return name.strip().lower().replace("'", "").replace("\u2019", "").replace("-", "_").replace(" ", "_")


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
            "Name rejection or filing denials may be appealed to the secretary of state or equivalent registrar."
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
            "Permit denials may be appealed to the local Board of Zoning Appeals or Building Code Board of Appeals."
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
            "Registration denials may be appealed to the local board of elections or election commission."
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
    "birth_certificate": {
        "category": "identification",
        "issuing_body": "National vital records office / civil registry",
        "requirements": [
            "Completed application form",
            "Parent(s) valid photo identification",
            "Proof of parentage (if applying on behalf of minor)",
            "Full name and date of birth of registrant",
            "Applicable filing or copy fee",
        ],
        "processing_time": {
            "routine": "2-6 weeks (initial registration at birth within 5-10 days; certified copies later)",
            "expedited": "same-day or 3-5 day expedited copy service",
        },
        "cost": {
            "currency": "varies",
            "routine": "$10-$30 per certified copy",
        },
        "online_portal": "national/state vital records portal (e.g., VitalChek)",
        "appeal_process": (
            "Corrections (name, parentage, date) require a court order or "
            "administrative amendment form filed with the vital records office."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Vital Records Office / County Clerk-Recorder",
                "requirements": [
                    "Completed application for certified copy",
                    "Valid government-issued photo ID of requester",
                    "Full name at birth, date and place of birth",
                    "Parent(s) names",
                    "Fee $10-$30 per certified copy (varies by state)",
                ],
                "cost": {"currency": "USD", "certified_copy": "10-30"},
                "online_portal": "https://www.vitalchek.com",
            },
            "GB": {
                "issuing_body": "General Register Office (GRO)",
                "requirements": [
                    "Online or postal application",
                    "Full name, date and place of birth",
                    "Parent(s) names",
                    "Fee GBP 11 for standard certificate",
                ],
                "cost": {"currency": "GBP", "standard": 11},
                "online_portal": "https://www.gov.uk/order-copy-birth-death-marriage-certificate",
            },
        },
    },
    "death_certificate": {
        "category": "identification",
        "issuing_body": "National vital records office / civil registry",
        "requirements": [
            "Full name of deceased",
            "Date and place of death",
            "Medical certificate of cause of death (completed by physician/coroner)",
            "Informant details (next of kin or funeral director)",
            "Applicable copy fee",
        ],
        "processing_time": {
            "routine": "1-4 weeks from death registration",
            "expedited": "same-day or 3-5 day expedited copy service",
        },
        "cost": {
            "currency": "varies",
            "routine": "$10-$30 per certified copy",
        },
        "online_portal": "national/state vital records portal",
        "appeal_process": (
            "Corrections to cause of death require medical certification; clerical errors corrected via amendment form."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Vital Records Office / County Clerk-Recorder",
                "requirements": [
                    "Valid government-issued photo ID of requester",
                    "Full name, date and place of death",
                    "Relationship to deceased or legal interest justification",
                    "Fee $10-$25 per certified copy",
                ],
                "cost": {"currency": "USD", "certified_copy": "10-25"},
                "online_portal": "https://www.vitalchek.com",
            },
            "GB": {
                "issuing_body": "General Register Office (GRO)",
                "requirements": [
                    "Full name, date and place of death",
                    "Certificate of cause of death (registered by medical professional)",
                    "Fee GBP 11 for standard certificate",
                ],
                "cost": {"currency": "GBP", "standard": 11},
                "online_portal": "https://www.gov.uk/order-copy-birth-death-marriage-certificate",
            },
            "CA": {
                "issuing_body": "Provincial Vital Statistics Agency",
                "requirements": [
                    "Completed application stating relationship to deceased",
                    "Full name, date and place of death",
                    "Fee varies by province (CAD $15-$35)",
                ],
                "cost": {"currency": "CAD", "certified_copy": "15-35"},
                "online_portal": "https://www.canada.ca/en/services/health/vital-statistics.html",
            },
        },
    },
    "name_change": {
        "category": "registrations",
        "issuing_body": "Court clerk / civil registry / local government office",
        "requirements": [
            "Petition for name change (filed in appropriate court)",
            "Proof of identity (valid government-issued photo ID)",
            "Proof of residency",
            "Fingerprint-based criminal background check (most jurisdictions)",
            "Publication of name change in a local newspaper (some jurisdictions)",
            "Filing fee",
            "Court hearing (if contested or for minors)",
        ],
        "processing_time": {
            "routine": "4-8 weeks from petition to court order",
            "expedited": "2-3 weeks (emergency or protective order cases)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$100-$450 by jurisdiction",
        },
        "online_portal": "county court clerk / Superior Court website",
        "appeal_process": (
            "Name change petitions denied by a judge may be re-filed with "
            "additional supporting documentation or appealed to a higher court."
        ),
        "countries": {
            "US": {
                "issuing_body": "County Superior Court / District Court",
                "requirements": [
                    "Petition for Change of Name (form varies by state)",
                    "Valid government-issued photo ID",
                    "Fingerprint-based criminal background check",
                    "Publication in newspaper of general circulation (4 weeks, waivable)",
                    "Filing fee $100-$435",
                    "Court hearing (mandatory for minors or contested changes)",
                ],
                "cost": {"currency": "USD", "court_filing": "100-435"},
                "online_portal": "county superior/district court self-help portals",
            },
            "GB": {
                "issuing_body": "HM Courts & Tribunals Service / Deed Poll",
                "requirements": [
                    "Deed Poll (enrolled or unenrolled)",
                    "Statutory declaration before a solicitor or commissioner for oaths",
                    "Published in the London Gazette (for enrolled deed poll)",
                    "Fee GBP 42.44 for enrolled deed poll",
                ],
                "cost": {"currency": "GBP", "enrolled": 42.44},
                "online_portal": "https://www.gov.uk/change-name-deed-poll",
            },
        },
    },
    "immigration_visa": {
        "category": "permits",
        "issuing_body": "National immigration authority / consular service",
        "requirements": [
            "Valid passport with at least 6 months remaining validity",
            "Completed visa application form",
            "Passport-sized photograph(s) meeting biometric specifications",
            "Fee payment receipt",
            "Supporting documents (purpose-of-travel, financial means, ties to home country)",
            "Biometrics appointment (fingerprints and photograph)",
            "Medical examination and police certificate (for certain visa categories)",
            "Interview at embassy/consulate (for most categories)",
        ],
        "processing_time": {
            "routine": "2-12 weeks depending on category, nationality, and consular workload",
            "expedited": "1-2 weeks premium processing (where available)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$30-$1,500 by visa category and nationality",
        },
        "online_portal": "national immigration authority online portal",
        "appeal_process": (
            "Visa denials may be appealed through an administrative review "
            "or judicial review process. Time limits are typically 30-90 days "
            "from denial notification. Re-application is often permissible."
        ),
        "countries": {
            "US": {
                "issuing_body": "U.S. Department of State / U.S. Citizenship and Immigration Services (USCIS)",
                "requirements": [
                    "Form DS-160 (non-immigrant) or DS-260 (immigrant)",
                    "Valid passport (6+ months validity past intended stay)",
                    "5x5 cm biometric photo (digital and physical)",
                    "Visa fee (varies: $160 non-immigrant tourist, $190 B1/B2)",
                    "Interview at U.S. embassy/consulate",
                    "Supporting documents: ties to home, financial evidence, itinerary",
                ],
                "cost": {"currency": "USD", "tourist_B1B2": 185, "immigrant": "325-535"},
                "online_portal": "https://travel.state.gov/content/travel/en/us-visas.html",
            },
            "GB": {
                "issuing_body": "UK Visas and Immigration (UKVI)",
                "requirements": [
                    "Online application via UKVI portal",
                    "Biometric residence permit (BRP) appointment",
                    "Proof of funds and English language requirement",
                    "Visa fee (e.g., GBP 115 standard visitor, varies by category)",
                    "Immigration Health Surcharge (IHS) for long-term visas",
                ],
                "cost": {"currency": "GBP", "standard_visitor": 115},
                "online_portal": "https://www.gov.uk/apply-uk-visa",
            },
            "CA": {
                "issuing_body": "Immigration, Refugees and Citizenship Canada (IRCC)",
                "requirements": [
                    "Online application via IRCC portal",
                    "Biometrics (fingerprints and photo)",
                    "Fee CAD $100 (visitor) to CAD $1,365 (economic immigrant)",
                    "Medical exam and police certificates for long-term categories",
                ],
                "cost": {"currency": "CAD", "visitor": 100, "express_entry": 1365},
                "online_portal": "https://www.canada.ca/en/immigration-refugees-citizenship/services/application/account.html",
            },
            "AU": {
                "issuing_body": "Department of Home Affairs",
                "requirements": [
                    "Online ImmiAccount application",
                    "Health examination for long-stay visas",
                    "Character requirements (police certificates)",
                    "Visa application charge (AUD $150 visitor; significantly higher for skilled/permanent)",
                ],
                "cost": {"currency": "AUD", "visitor_subclass_600": 150},
                "online_portal": "https://immi.homeaffairs.gov.au/visas/getting-a-visa",
            },
        },
    },
    "unemployment_benefits": {
        "category": "benefits",
        "issuing_body": "National employment insurance / labor department",
        "requirements": [
            "Proof of prior employment and earnings (base period wages)",
            "Proof of job separation (layoff, not voluntary quit or discharge for cause)",
            "Active work search documentation (weekly)",
            "Ability and availability to work",
            "Registration with public employment service",
            "Completed claim application (online or phone)",
        ],
        "processing_time": {
            "routine": "1-3 weeks for initial claim determination",
            "expedited": "direct deposit payment within 1-2 weeks of approval",
        },
        "cost": {
            "currency": "varies",
            "routine": "no cost to file a claim; employer-funded insurance",
        },
        "online_portal": "national employment insurance / labor department portal",
        "appeal_process": (
            "Denied claims may be appealed to an administrative hearing or "
            "tribunal within 10-30 days of the denial notice."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Unemployment Insurance Agency (U.S. Department of Labor oversight)",
                "requirements": [
                    "Monetary determination: minimum base period wages (varies by state)",
                    "Non-monetary: laid off through no fault of own, not fired for cause",
                    "Weekly work search: 2-5 employer contacts per week",
                    "Claim filed via state UI portal or phone",
                    "Typical benefit: 50% of prior wages, $200-$700/week",
                    "Maximum duration: 12-26 weeks (varies by state)",
                ],
                "cost": {"currency": "USD", "state_max_weekly": "235-1,015"},
                "online_portal": "https://www.dol.gov/general/topic/unemployment-insurance",
            },
            "GB": {
                "issuing_body": "Department for Work and Pensions (Jobcentre Plus)",
                "requirements": [
                    "National Insurance number",
                    "Jobseeker's Allowance or Universal Credit application",
                    "Attend fortnightly appointments at Jobcentre Plus",
                    "Show active job search in Claimant Commitment",
                    "Be available for and actively seeking work",
                    "Aged 18+ to State Pension age",
                ],
                "cost": {"currency": "GBP", "JSA_25plus_weekly": 71.70},
                "online_portal": "https://www.gov.uk/jobs-seeking-allowance",
            },
            "CA": {
                "issuing_body": "Employment and Social Development Canada (Service Canada / EI program)",
                "requirements": [
                    "EI regular benefits: 420-700 hours of insurable employment (varies by regional rate)",
                    "Separation through no fault of own (Record of Employment)",
                    "Active job search; report bi-weekly via My Service Canada Account",
                    "Typical benefit: 55% of insurable earnings, max CAD $650/week",
                ],
                "cost": {"currency": "CAD", "max_weekly_benefit": 650},
                "online_portal": "https://www.canada.ca/en/services/benefits/ei.html",
            },
        },
    },
    "disability_benefits": {
        "category": "benefits",
        "issuing_body": "National social security / disability benefits administration",
        "requirements": [
            "Medical documentation establishing the disabling condition",
            "Work history and credits (for social-insurance-based systems)",
            "Physician's statement of functional limitations",
            "Completed disability claim application",
            "Financial records (for income-tested or supplemental programs)",
            "Attendance at consultative medical examination if requested",
        ],
        "processing_time": {
            "routine": "3-6 months initial decision; 12-18 months through appeals",
            "expedited": "expedited processing for terminal illness or extreme hardship",
        },
        "cost": {
            "currency": "varies",
            "routine": "no cost to file a claim",
        },
        "online_portal": "national disability benefits portal",
        "appeal_process": (
            "Multi-level appeal: reconsideration -> administrative law judge "
            "-> appeals council -> federal/district court."
        ),
        "countries": {
            "US": {
                "issuing_body": "Social Security Administration (SSDI/SSI)",
                "requirements": [
                    "SSDI: 20+ work credits (typically 5+ of last 10 years worked)",
                    "SSI: limited income and resources ($2,000 individual / $3,000 couple)",
                    "Medical evidence: condition expected to last 12+ months or result in death",
                    "Unable to perform substantial gainful activity (SGA: $1,470/month in 2024)",
                    "File online, by phone, or at local SSA office",
                ],
                "cost": {"currency": "USD", "SGA_threshold_2024": 1550},
                "online_portal": "https://www.ssa.gov/disability",
            },
            "GB": {
                "issuing_body": "Department for Work and Pensions (PIP / ESA)",
                "requirements": [
                    "Personal Independence Payment (PIP) for daily living and mobility needs",
                    "Employment and Support Allowance (ESA) for limited work capacity",
                    "Work Capability Assessment (WCA) with healthcare professional",
                    "GP/specialist medical evidence",
                ],
                "cost": {"currency": "GBP", "PIP_enhanced_daily_living_weekly": 101.75},
                "online_portal": "https://www.gov.uk/pip",
            },
            "CA": {
                "issuing_body": "Service Canada (CPP Disability) / provincial disability programs",
                "requirements": [
                    "CPP Disability: severe and prolonged disability preventing any gainful work",
                    "Minimum contribution requirements (4 of last 6 years)",
                    "Medical report from treating physician",
                    "Provincial disability supplements: income-tested",
                ],
                "cost": {"currency": "CAD", "CPPD_max_monthly": 1538.67},
                "online_portal": "https://www.canada.ca/en/services/benefits/publicpensions/cpp/cpp-disability-benefit.html",
            },
        },
    },
    "vehicle_registration": {
        "category": "registrations",
        "issuing_body": "State/provincial department of motor vehicles or equivalent",
        "requirements": [
            "Proof of ownership (title, bill of sale, or manufacturer's certificate of origin)",
            "Proof of insurance meeting minimum liability requirements",
            "Valid government-issued photo identification",
            "Safety inspection certificate (where required)",
            "Emissions test certificate (where required)",
            "Vehicle identification number (VIN) verification",
            "Registration fee and applicable taxes",
        ],
        "processing_time": {
            "routine": "same day to 2 weeks (dependent on inspection requirements)",
            "expedited": "instant online renewal for existing registrations",
        },
        "cost": {
            "currency": "varies",
            "routine": "$30-$300/year depending on vehicle type and jurisdiction",
        },
        "online_portal": "state/provincial DMV or equivalent portal",
        "appeal_process": (
            "Registration denials (failed inspection, title issues) may be "
            "resolved by correcting the deficiency and re-submitting."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Department of Motor Vehicles (DMV)",
                "requirements": [
                    "Vehicle title (or Manufacturer's Statement of Origin for new)",
                    "Proof of insurance (minimum liability per state law)",
                    "Smog certificate (CA, NV, and ~30 other states)",
                    "Safety inspection (varies by state: TX, VA, PA, etc.)",
                    "Registration fee based on vehicle value/weight/age",
                    "Sales/use tax if purchased from private party or out-of-state dealer",
                ],
                "cost": {"currency": "USD", "annual_registration": "30-300"},
                "online_portal": "state DMV portals",
            },
            "GB": {
                "issuing_body": "Driver and Vehicle Licensing Agency (DVLA)",
                "requirements": [
                    "Valid MOT certificate (for vehicles 3+ years old)",
                    "Vehicle insurance (continuous insurance enforcement)",
                    "Vehicle tax paid (VED, based on CO2 emissions)",
                    "V5C registration certificate (log book)",
                ],
                "cost": {"currency": "GBP", "VED": "0-2,365 (based on CO2)"},
                "online_portal": "https://www.gov.uk/vehicle-tax",
            },
            "DE": {
                "issuing_body": "Kfz-Zulassungsstelle (local vehicle registration office)",
                "requirements": [
                    "Valid TUEV/HU inspection certificate",
                    "Proof of liability insurance (eVB-Nummer)",
                    "Fahrzeugbrief (vehicle title) and Fahrzeugschein (registration)",
                    "Registration fee and motor vehicle tax (Kfz-Steuer, based on displacement/CO2)",
                ],
                "cost": {"currency": "EUR", "licence_plate_fee": "10-30"},
                "online_portal": "https://www.kba.de",
            },
        },
    },
    "business_license": {
        "category": "licenses",
        "issuing_body": "Municipal / county business license office",
        "requirements": [
            "Completed business license application",
            "Trade name / DBA registration (if using a fictitious business name)",
            "Business entity registration (LLC, corporation, etc.)",
            "Zoning clearance from planning department",
            "Proof of liability insurance (for in-person businesses)",
            "Health department clearance (for food/beauty businesses)",
            "Professional qualifications (for regulated trades)",
            "License fee",
        ],
        "processing_time": {
            "routine": "2-6 weeks",
            "expedited": "1-5 business days (premium processing where available)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$50-$500 depending on business type and jurisdiction",
        },
        "online_portal": "city/county business license portal",
        "appeal_process": (
            "License denials or revocations may be appealed through the "
            "local hearing examiner or Business License Review Board."
        ),
        "countries": {
            "US": {
                "issuing_body": "City Hall / County Business License Division",
                "requirements": [
                    "Business license application (city/county specific)",
                    "Fictitious business name (DBA) registration at county clerk",
                    "Zoning clearance or home occupation permit",
                    "State seller's permit (for retail/wholesale businesses)",
                    "Professional/occupational license (if regulated: contractor, cosmetologist, etc.)",
                    "Fee varies by business type and gross receipts estimate",
                ],
                "cost": {"currency": "USD", "initial_fee": "50-500"},
                "online_portal": "city/county business license portals",
            },
            "GB": {
                "issuing_body": "Local Authority (district/borough council)",
                "requirements": [
                    "Business rates registration with Valuation Office Agency",
                    "Planning permission for change of use (if applicable)",
                    "Trading Standards compliance",
                    "Environmental health registration (food businesses)",
                    "Premises licence (if selling alcohol, entertainment, late-night refreshment)",
                ],
                "cost": {"currency": "GBP", "council_fees": "varies by activity"},
                "online_portal": "https://www.gov.uk/set-up-business-uk",
            },
        },
    },
    "liquor_license": {
        "category": "licenses",
        "issuing_body": "State/provincial liquor control board / local alcohol licensing authority",
        "requirements": [
            "Completed license application",
            "Background check of all owners and managers",
            "Proof of age (21+ in US, 18+ in most jurisdictions)",
            "Zoning approval and distance-measurement from schools/churches",
            "Public notice / posting period",
            "Responsible beverage service training",
            "Liability insurance",
            "License fee (varies widely by type: on-premise, off-premise, manufacturer)",
        ],
        "processing_time": {
            "routine": "2-6 months from application to hearing",
            "expedited": "temporary event permits within 1-2 weeks",
        },
        "cost": {
            "currency": "varies",
            "routine": "$300-$15,000+ depending on license type and jurisdiction",
        },
        "online_portal": "state liquor control board / local council licensing portal",
        "appeal_process": (
            "Denied applications or license revocations may be appealed to "
            "the liquor control board's appeals division or state administrative court."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Alcoholic Beverage Control (ABC) Board / local council",
                "requirements": [
                    "State ABC license application (on-premise = bar/restaurant, off-premise = liquor store)",
                    "FBI and state criminal background check for all principals",
                    "Zoning compliance: typically 500-1000 ft from schools, churches, parks",
                    "Posted public notice at premises for 30 days",
                    "Responsible vendor / TIPS certification",
                    "License fee: $300-$15,000+ depending on class and county population",
                ],
                "cost": {"currency": "USD", "annual_on_premise": "300-15000"},
                "online_portal": "state ABC board portals",
            },
            "GB": {
                "issuing_body": "Local Authority Licensing Board",
                "requirements": [
                    "Premises licence application with operating schedule",
                    "Designated Premises Supervisor (DPS) with personal licence",
                    "Personal licence (requires accredited qualification, GBP 37 application fee)",
                    "28-day public consultation period (blue notice on premises)",
                    "Licensing objectives: crime prevention, public safety, public nuisance, child protection",
                    "Licensing hearing if objections received from responsible authorities or residents",
                ],
                "cost": {"currency": "GBP", "premises_licence": "100-1,905 (rateable-value based)"},
                "online_portal": "https://www.gov.uk/apply-for-a-licence/premises-licence",
            },
        },
    },
    "gun_permit": {
        "category": "permits",
        "issuing_body": "National/state firearms licensing authority or police service",
        "requirements": [
            "Completed application form",
            "Proof of age (typically 18+ for long guns, 21+ for handguns)",
            "Government-issued photo identification",
            "Criminal background check (fingerprints)",
            "Mental health records check",
            "Firearms safety training certificate",
            "Safe storage declaration or inspection",
            "Application fee",
            "Waiting period (jurisdiction dependent)",
        ],
        "processing_time": {
            "routine": "1-6 months depending on jurisdiction",
            "expedited": "N/A (required checks cannot be bypassed)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$30-$500 depending on jurisdiction and permit type",
        },
        "online_portal": "police service / firearms registry portal",
        "appeal_process": (
            "Denials may be appealed to the licensing authority's review board "
            "or a firearms appeals tribunal. Judicial review is typically available."
        ),
        "countries": {
            "US": {
                "issuing_body": "ATF (federal) / State Bureau of Firearms or local sheriff (varies by state)",
                "requirements": [
                    "Federal: ATF Form 4473 background check (NICS) at licensed dealer for all firearm purchases",
                    "State: concealed carry permit (16 states require may-issue or shall-issue permit)",
                    "Fingerprints and FBI background check",
                    "Firearm safety training course (typically 8-16 hours for CCW permits)",
                    "Real ID-compliant driver's license or government ID",
                    "States with strict requirements (CA, NY, IL, MA): firearm safety certificate, roster of approved handguns, ammunition background check",
                ],
                "cost": {"currency": "USD", "ATF_transfer": "15-50 (dealer fee)"},
                "online_portal": "state DOJ / ATF portals",
            },
            "GB": {
                "issuing_body": "Police firearms licensing department (local force)",
                "requirements": [
                    "Firearm Certificate (FAC) or Shotgun Certificate application to local police",
                    "Good reason for firearm ownership (hunting, target shooting, deer stalking)",
                    "GP medical report on mental and physical fitness",
                    "Two character referees",
                    "Secure storage: approved gun safe bolted to structure; ammunition separate",
                    "In-person home visit by Firearms Enquiry Officer (FEO)",
                    "Certificate valid 5 years; renewal required",
                    "Section 1 firearms: ammunition limits specified on FAC; expanding ammunition now restricted",
                ],
                "cost": {"currency": "GBP", "FAC_grant": 88, "FAC_renewal": 62},
                "online_portal": "https://www.gov.uk/shotgun-and-firearm-certificates",
            },
            "CA": {
                "issuing_body": "Royal Canadian Mounted Police (RCMP) Canadian Firearms Program",
                "requirements": [
                    "Possession and Acquisition Licence (PAL) for non-restricted firearms",
                    "Restricted PAL (RPAL) for handguns and restricted long guns",
                    "Canadian Firearms Safety Course (CFSC) for PAL; + restricted course for RPAL",
                    "RCMP background check (daily continuous-eligibility screening post-licensing)",
                    "Spouse/partner notification and 28-day mandatory waiting period",
                    "Safe storage: trigger lock + locked container or safe; ammunition stored separately",
                ],
                "cost": {"currency": "CAD", "PAL_non_restricted": "62.55 (5yr)"},
                "online_portal": "https://www.rcmp-grc.gc.ca/en/firearms",
            },
        },
    },
    "food_service_permit": {
        "category": "permits",
        "issuing_body": "Local health department / environmental health agency",
        "requirements": [
            "Completed permit application",
            "Floor plan and facility layout (for new establishments)",
            "Menu and food preparation procedures",
            "Certified Food Protection Manager on staff (ServSafe or equivalent)",
            "Handwashing sink, warewashing equipment, and refrigeration specifications",
            "Waste disposal plan and grease trap compliance",
            "Water supply testing (if on private well)",
            "Permit fee and plan review fee",
        ],
        "processing_time": {
            "routine": "2-6 weeks from application to permit issuance (after inspections)",
            "expedited": "1-2 weeks for low-risk operations (pre-packaged foods only)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$100-$1,000 depending on risk category and jurisdiction",
        },
        "online_portal": "local health department / environmental health portal",
        "appeal_process": (
            "Permit denials or violations may be appealed to the local Board "
            "of Health or Environmental Health Hearing Board."
        ),
        "countries": {
            "US": {
                "issuing_body": "County/City Environmental Health Department",
                "requirements": [
                    "Food service establishment permit application",
                    "Plan review for new construction or remodel ($100-$500)",
                    "Certified Food Protection Manager (CFPM) certificate (ServSafe, NRFSP)",
                    "Pre-opening health inspection",
                    "Routine inspections: 1-3 per year (risk-based frequency)",
                    "HACCP plan for specialized processes (sushi, sous vide, smoking)",
                ],
                "cost": {"currency": "USD", "annual_permit": "100-1000"},
                "online_portal": "county environmental health portals",
            },
            "GB": {
                "issuing_body": "Local Authority Environmental Health / Food Standards Agency",
                "requirements": [
                    "Food business registration (free, at least 28 days before opening)",
                    "Food Safety Management System (HACCP-based, typically Safer Food Better Business)",
                    "Level 2 Food Safety certificate for food handlers",
                    "Food Hygiene Rating Scheme inspection (0-5 score; displayed at premises)",
                    "Approval required for handling products of animal origin (meat, dairy, fish)",
                ],
                "cost": {"currency": "GBP", "registration": "free"},
                "online_portal": "https://www.food.gov.uk/business-guidance/register-a-food-business",
            },
        },
    },
    "childcare_license": {
        "category": "licenses",
        "issuing_body": "State/provincial childcare licensing division",
        "requirements": [
            "Completed application with facility details",
            "Criminal background check and child abuse registry clearance for all staff",
            "Facility meets health, safety, and fire codes",
            "Staff-to-child ratios meet minimum standards",
            "Lead staff have required education and early childhood credentials",
            "Immunization records for children (or exemption documentation)",
            "CPR and first aid certification for staff",
            "Indoor and outdoor space requirements per child",
            "Liability insurance",
        ],
        "processing_time": {
            "routine": "2-6 months from application to license",
            "expedited": "not typically available (background checks are rate-limiting)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$50-$300 application fee + annual license fee",
        },
        "online_portal": "state childcare licensing portal",
        "appeal_process": (
            "License denials or revocations may be appealed through an "
            "administrative hearing with the licensing division."
        ),
        "countries": {
            "US": {
                "issuing_body": "State Department of Social Services / Department of Children and Family Services",
                "requirements": [
                    "FBI and state criminal background check + child abuse/neglect registry for every adult in facility",
                    "Health and safety pre-licensing inspection (fire marshal, building code, lead paint for pre-1978 buildings)",
                    "Staff-to-child ratio: 1:4 for infants (0-12mo), 1:6 for toddlers, 1:10-12 for preschool",
                    "Director qualified: BA in ECE or related + experience",
                    "Lead teacher qualified: CDA or AA in ECE",
                    "CPR/first aid certified staff present at all times",
                ],
                "cost": {"currency": "USD", "application": "50-200", "annual": "25-300"},
                "online_portal": "state childcare licensing portals",
            },
            "GB": {
                "issuing_body": "Ofsted (Office for Standards in Education) / Childminder Agencies",
                "requirements": [
                    "Ofsted registration (Early Years Register for 0-5; Childcare Register for 5-8)",
                    "DBS (Disclosure and Barring Service) enhanced check for all staff",
                    "EYFS (Early Years Foundation Stage) framework compliance",
                    "Paediatric first aid certificate",
                    "Safeguarding training and designated safeguarding lead",
                    "Health declaration and GP reference",
                ],
                "cost": {"currency": "GBP", "Ofsted_registration": "35-220"},
                "online_portal": "https://www.gov.uk/register-childminder-childcare-provider",
            },
        },
    },
    "health_insurance": {
        "category": "benefits",
        "issuing_body": "National health insurance authority / public health exchange",
        "requirements": [
            "Proof of identity and citizenship/residency status",
            "Household income documentation (for subsidized plans)",
            "Employer coverage information (if applicable)",
            "Enrollment during open enrollment period or qualifying life event",
            "Premium payment (first month's premium for coverage to be active)",
        ],
        "processing_time": {
            "routine": "immediate enrollment; coverage begins 1st of following month (or faster for QLEs)",
            "expedited": "immediate coverage for qualifying life events",
        },
        "cost": {
            "currency": "varies",
            "routine": "subsidized premiums based on income; full-cost premiums vary by plan tier",
        },
        "online_portal": "national health insurance exchange / marketplace portal",
        "appeal_process": (
            "Coverage denials or eligibility determinations may be appealed "
            "through the health insurance appeals process within 90 days."
        ),
        "countries": {
            "US": {
                "issuing_body": "HealthCare.gov (federal marketplace) / state-based exchanges",
                "requirements": [
                    "Application during Open Enrollment (Nov 1 - Jan 15) or after Qualifying Life Event",
                    "SSN and income verification (tax return or pay stubs)",
                    "ACA-compliant plan tiers: Bronze (60% covered), Silver (70%), Gold (80%), Platinum (90%)",
                    "Subsidies: premium tax credit (income 100-400% FPL) and cost-sharing reductions (Silver only, 100-250% FPL)",
                    "Employer coverage must be unaffordable (>9.02% of household income) for marketplace subsidy eligibility",
                ],
                "cost": {"currency": "USD", "bronze_avg_monthly": "320-420"},
                "online_portal": "https://www.healthcare.gov",
            },
            "GB": {
                "issuing_body": "National Health Service (NHS)",
                "requirements": [
                    "NHS care is residence-based and free at the point of use",
                    "GP registration at local surgery (bring proof of address and ID)",
                    "No enrollment period: register any time",
                    "Immigration Health Surcharge (IHS) for non-UK residents on visas >6 months",
                    "Private health insurance is supplementary and voluntary",
                ],
                "cost": {"currency": "GBP", "NHS": "free at point of use (tax-funded)"},
                "online_portal": "https://www.nhs.uk/nhs-services/gps/how-to-register-with-a-gp-surgery/",
            },
            "CA": {
                "issuing_body": "Provincial/Territorial Health Insurance Plan",
                "requirements": [
                    "Provincial health card application (e.g., OHIP in Ontario, MSP in BC)",
                    "Proof of residency and immigration status",
                    "Waiting period: up to 3 months in some provinces (BC, ON for new residents)",
                    "Provincial plan covers medically necessary hospital and physician services",
                    "Extended health benefits (dental, drugs, vision) typically employer-provided or private",
                ],
                "cost": {"currency": "CAD", "provincial_plan": "tax-funded (no direct premium)"},
                "online_portal": "https://www.canada.ca/en/health-canada/services/health-cards.html",
            },
            "FR": {
                "issuing_body": "Caisse Primaire d'Assurance Maladie (CPAM) / Protection Universelle Maladie (PUMA)",
                "requirements": [
                    "PUMA provides continuous healthcare coverage to all legal residents",
                    "Carte Vitale (green health insurance card) issued upon registration",
                    "State covers ~70% of costs; complementary insurance (mutuelle) covers remainder",
                    "CMU-C / ACS: free or subsidized complementary insurance for low-income residents",
                    "Registration at local CPAM office with proof of residence and identity",
                ],
                "cost": {"currency": "EUR", "public_coverage": "70% (complementaire for remainder)"},
                "online_portal": "https://www.ameli.fr",
            },
        },
    },
    "divorce_filing": {
        "category": "registrations",
        "issuing_body": "Family court / civil court clerk's office",
        "requirements": [
            "Petition for dissolution of marriage (or legal separation)",
            "Proof of residency (typically 3 months - 1 year in the jurisdiction)",
            "Grounds for divorce (no-fault: irreconcilable differences; or fault-based grounds)",
            "Marriage certificate",
            "Financial disclosure (income, assets, debts)",
            "Parenting plan (if children under 18) and child support calculation",
            "Filing fee (fee waiver available for low-income petitioners)",
            "Service of process on respondent",
        ],
        "processing_time": {
            "routine": "6-12 months from filing to final decree (uncontested; longer if contested)",
            "expedited": "summary dissolution in 1-3 months (short marriage, no children, limited assets)",
        },
        "cost": {
            "currency": "varies",
            "routine": "$200-$450 filing fee + service costs + attorney fees (if represented)",
        },
        "online_portal": "family court / court clerk e-filing portal",
        "appeal_process": (
            "Final decrees may be appealed to the appellate division within "
            "30-60 days. Modifications to custody/support may be filed at any time "
            "based on material change in circumstances."
        ),
        "countries": {
            "US": {
                "issuing_body": "County Superior/Family Court Clerk",
                "requirements": [
                    "Petition for Dissolution (FL-100 in CA; state-specific form otherwise)",
                    "Residency: petitioner or respondent must have lived in state for 3-6 months (varies) and county for 3 months",
                    "No-fault: irreconcilable differences / irretrievable breakdown (all 50 states)",
                    "Financial disclosure: income & expense declaration, schedule of assets & debts",
                    "Mandatory parenting class for divorcing parents (children under 18)",
                    "Filing fee: $200-$450 (fee waiver available for low-income petitioners)",
                ],
                "cost": {"currency": "USD", "filing_fee": "200-450"},
                "online_portal": "county superior court e-filing portals (e.g., Odyssey eFileCA, TurboCourt)",
            },
            "GB": {
                "issuing_body": "HM Courts & Tribunals Service (Family Court)",
                "requirements": [
                    "Divorce application (Form D8) filed online or by post",
                    "Marriage certificate (original or certified copy)",
                    "Grounds: irretrievable breakdown established by one of five facts (England/Wales)",
                    "No-fault divorce (since April 2022): 20-week minimum from application to Conditional Order",
                    "Court fee: GBP 593 (help with fees available for low-income applicants)",
                    "Financial remedy application (Form A) for financial settlement (separate from divorce itself)",
                ],
                "cost": {"currency": "GBP", "court_fee": 593},
                "online_portal": "https://www.gov.uk/apply-for-divorce",
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
            "First-Class Mail",
            "Priority Mail",
            "Priority Mail Express",
            "Media Mail",
            "Retail Ground",
            "International shipping",
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
            "1st Class",
            "2nd Class",
            "Signed For",
            "Special Delivery Guaranteed",
            "International Standard",
            "International Tracked",
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
            "Lettermail",
            "Registered Mail",
            "Xpresspost",
            "Priority",
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
            "Standardbrief",
            "Kompaktbrief",
            "Grossbrief",
            "Einschreiben",
            "DHL Paket",
            "DHL Express International",
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
            "Lettre Prioritaire",
            "Lettre Vert",
            "Lettre Suivie",
            "Colissimo",
            "Chronopost International",
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
            "Letter",
            "Large Letter",
            "Registered Post",
            "Express Post",
            "International Standard",
            "International Express",
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
            "Yu-Pack",
            "Yu-Mail",
            "Kan-i-Kaki-Komi",
            "EMS",
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


def get_requirements(service_id: str, country: str | None = None) -> list[str] | None:
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


def find_service_office(service_id: str, location: str) -> ServiceOffice | None:
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


def get_postage_rate(origin: str, destination: str, weight: int) -> PostageRate | None:
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
