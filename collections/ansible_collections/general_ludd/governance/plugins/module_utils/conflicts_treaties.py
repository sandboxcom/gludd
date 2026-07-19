"""Conflicts & treaties knowledge module for the governance collection.

Exposes active/ongoing conflicts, a treaty database, international courts,
and accessors that let an agent answer "which treaties bind country X?" or
"does court Y have jurisdiction over case type Z?".

Public surface::

    CONFLICT_TYPES       enum of 7 conflict categories
    ACTIVE_CONFLICTS     list of conflict dicts
    TREATY_DATABASE      list of treaty dicts
    INTERNATIONAL_COURTS list of court dicts

    lookup_conflict(region)                -> list[conflict dict]
    get_treaty(treaty_id)                  -> treaty dict | None
    get_treaty_parties(treaty_id)          -> list[str]
    get_treaty_obligations(country)        -> list[obligation dict]
    get_court_jurisdiction(court_id)       -> str | None
    check_court_jurisdiction(court_id, case_type) -> bool
"""

from __future__ import annotations

import enum
from typing import Any


class _ConflictMeta(enum.EnumMeta):
    def __contains__(cls, item: object) -> bool:
        if isinstance(item, cls):
            return True
        if isinstance(item, str):
            return item in {m.value for m in cls}
        return False


class ConflictType(enum.Enum, metaclass=_ConflictMeta):
    INTERSTATE = "interstate"
    INTRASTATE = "intrastate"
    ASYMMETRIC = "asymmetric"
    CYBER = "cyber"
    ECONOMIC = "economic"
    PROXY = "proxy"
    FROZEN = "frozen"


CONFLICT_TYPES = ConflictType


ACTIVE_CONFLICTS: list[dict[str, Any]] = [
    {
        "id": "russo_ukraine_war",
        "name": "Russo-Ukrainian War",
        "region": "Eastern Europe",
        "type": ConflictType.INTERSTATE,
        "parties": ["Russia", "Ukraine"],
        "status": "active",
    },
    {
        "id": "sudan_civil_war",
        "name": "Sudanese Civil War",
        "region": "East Africa",
        "type": ConflictType.INTRASTATE,
        "parties": ["Sudanese Armed Forces", "Rapid Support Forces"],
        "status": "active",
    },
    {
        "id": "myanmar_civil_war",
        "name": "Myanmar Civil War",
        "region": "Southeast Asia",
        "type": ConflictType.INTRASTATE,
        "parties": ["Myanmar military (Tatmadaw)", "Various ethnic armed organizations"],
        "status": "active",
    },
    {
        "id": "israel_hamas_war",
        "name": "Israel-Gaza War",
        "region": "Middle East",
        "type": ConflictType.ASYMMETRIC,
        "parties": ["Israel", "Hamas"],
        "status": "active",
    },
    {
        "id": "sahel_insurgency",
        "name": "Sahel Insurgency",
        "region": "West Africa",
        "type": ConflictType.ASYMMETRIC,
        "parties": ["Burkina Faso", "Mali", "Niger", "JNIM", "ISGS"],
        "status": "active",
    },
    {
        "id": "south_china_sea",
        "name": "South China Sea Dispute",
        "region": "East Asia",
        "type": ConflictType.PROXY,
        "parties": ["China", "Philippines", "Vietnam", "Malaysia", "Brunei"],
        "status": "frozen",
    },
    {
        "id": "korean_conflict",
        "name": "Korean Conflict",
        "region": "East Asia",
        "type": ConflictType.FROZEN,
        "parties": ["North Korea", "South Korea", "United States"],
        "status": "armistice",
    },
]


TREATY_DATABASE: list[dict[str, Any]] = [
    {
        "id": "geneva_conventions",
        "name": "Geneva Conventions of 1949",
        "subject": "international_humanitarian_law",
        "parties": [
            "United States", "United Kingdom", "France", "Germany", "Russia",
            "China", "India", "Japan", "Canada", "Australia", "Brazil",
            "Argentina", "Mexico", "South Africa", "Nigeria", "Egypt",
            "Israel", "Iran", "Iraq", "Saudi Arabia", "Turkey", "Pakistan",
            "Indonesia", "Vietnam", "Thailand", "Philippines", "South Korea",
            "North Korea", "Italy", "Spain", "Netherlands", "Belgium",
            "Switzerland", "Sweden", "Norway", "Denmark", "Finland",
            "Poland", "Ukraine", "Romania", "Czech Republic", "Austria",
            "Greece", "Portugal", "Ireland", "Hungary", "Bulgaria",
            "Algeria", "Morocco", "Tunisia", "Libya", "Sudan", "Ethiopia",
            "Kenya", "Tanzania", "Uganda", "Ghana", "Ivory Coast",
            "Senegal", "Cameroon", "Angola", "Mozambique", "Zimbabwe",
            "Chile", "Colombia", "Peru", "Venezuela", "Ecuador",
            "Bolivia", "Paraguay", "Uruguay", "Cuba", "Dominican Republic",
            "Guatemala", "Honduras", "Nicaragua", "Costa Rica", "Panama",
            "Afghanistan", "Kazakhstan", "Uzbekistan", "Turkmenistan",
            "Azerbaijan", "Armenia", "Georgia", "Belarus", "Moldova",
            "Slovakia", "Slovenia", "Croatia", "Serbia", "Bosnia and Herzegovina",
            "North Macedonia", "Albania", "Montenegro", "Estonia", "Latvia",
            "Lithuania", "Lebanon", "Jordan", "Syria", "Yemen", "Oman",
            "Qatar", "Bahrain", "Kuwait", "United Arab Emirates",
            "Cambodia", "Laos", "Malaysia", "Singapore", "Brunei",
            "Sri Lanka", "Maldives", "Nepal", "Bhutan", "Bangladesh",
            "Mongolia", "Papua New Guinea", "New Zealand", "Fiji",
            "Solomon Islands", "Vanuatu", "Samoa", "Tonga", "Tuvalu",
            "Kiribati", "Marshall Islands", "Micronesia", "Palau", "Nauru",
            "Tajikistan", "Kyrgyzstan", "Madagascar", "Mauritius",
            "Seychelles", "Comoros", "Djibouti", "Eritrea", "Somalia",
            "Central African Republic", "Chad", "Niger", "Mali",
            "Burkina Faso", "Togo", "Benin", "Burundi", "Rwanda",
            "Democratic Republic of the Congo", "Republic of the Congo",
            "Gabon", "Equatorial Guinea", "Sao Tome and Principe",
            "Lesotho", "Eswatini", "Botswana", "Namibia",
            "South Sudan", "Liberia", "Sierra Leone", "Guinea",
            "Guinea-Bissau", "The Gambia", "Mauritania", "Cape Verde",
            "Timor-Leste", "Bahamas", "Barbados", "Jamaica", "Trinidad and Tobago",
            "Saint Lucia", "Saint Vincent and the Grenadines",
            "Antigua and Barbuda", "Dominica", "Saint Kitts and Nevis",
            "Grenada", "Belize", "Guyana", "Suriname", "Haiti",
            "San Marino", "Monaco", "Liechtenstein", "Andorra",
            "Vatican City", "Malta", "Cyprus", "Iceland",
            "Luxembourg",
        ],
        "enforcement": {
            "mechanism": "ICRC protective powers; universal jurisdiction for grave breaches",
            "body": "International Committee of the Red Cross",
        },
    },
    {
        "id": "paris_agreement",
        "name": "Paris Agreement on Climate Change",
        "subject": "climate_change",
        "parties": [
            "United States", "United Kingdom", "France", "Germany", "China",
            "India", "Japan", "Canada", "Australia", "Brazil", "Argentina",
            "Mexico", "South Africa", "Nigeria", "Egypt", "Russia",
            "Indonesia", "Vietnam", "Thailand", "Philippines", "South Korea",
            "Italy", "Spain", "Netherlands", "Belgium", "Switzerland",
            "Sweden", "Norway", "Denmark", "Finland", "Poland", "Ukraine",
            "Turkey", "Pakistan", "Saudi Arabia", "Iran", "Iraq",
            "United Arab Emirates", "Qatar", "Kuwait", "Oman", "Jordan",
            "Lebanon", "Israel", "Kenya", "Ethiopia", "Ghana", "Senegal",
            "Morocco", "Algeria", "Tunisia", "Colombia", "Peru", "Chile",
            "Norway", "Ireland", "Portugal", "Greece", "Austria",
            "Czech Republic", "Romania", "Hungary", "Bulgaria",
        ],
        "enforcement": {
            "mechanism": "Nationally Determined Contributions (NDCs); global stocktake every 5 years",
            "body": "UNFCCC Secretariat",
        },
    },
    {
        "id": "nato",
        "name": "North Atlantic Treaty",
        "subject": "collective_defense",
        "parties": [
            "United States", "United Kingdom", "France", "Germany", "Canada",
            "Italy", "Belgium", "Netherlands", "Luxembourg", "Denmark",
            "Norway", "Iceland", "Portugal", "Greece", "Turkey", "Spain",
            "Poland", "Hungary", "Czech Republic", "Estonia", "Latvia",
            "Lithuania", "Slovenia", "Slovakia", "Romania", "Bulgaria",
            "Albania", "Croatia", "Montenegro", "North Macedonia",
            "Finland", "Sweden",
        ],
        "enforcement": {
            "mechanism": "Article 5 mutual defense clause; consensus decision-making",
            "body": "North Atlantic Council",
        },
    },
    {
        "id": "npt",
        "name": "Treaty on the Non-Proliferation of Nuclear Weapons (NPT)",
        "subject": "nuclear_nonproliferation",
        "parties": [
            "United States", "United Kingdom", "France", "Russia", "China",
            "Germany", "Japan", "Canada", "Australia", "Brazil", "Argentina",
            "Mexico", "South Africa", "Nigeria", "Egypt", "India",
            "Pakistan", "Israel", "Iran", "Iraq", "Saudi Arabia", "Turkey",
            "Indonesia", "Vietnam", "Thailand", "Philippines", "South Korea",
            "Italy", "Spain", "Netherlands", "Belgium", "Switzerland",
            "Sweden", "Norway", "Denmark", "Finland", "Poland", "Ukraine",
            "Romania", "Czech Republic", "Austria", "Greece", "Portugal",
            "Ireland", "Hungary", "Bulgaria", "Algeria", "Morocco",
            "Tunisia", "Libya", "Sudan", "Ethiopia", "Kenya", "Tanzania",
            "Uganda", "Ghana", "Senegal", "Cameroon", "Colombia", "Peru",
            "Chile", "Venezuela", "Ecuador", "Bolivia", "Paraguay",
            "Uruguay", "Cuba", "Afghanistan", "Kazakhstan", "Uzbekistan",
            "Azerbaijan", "Armenia", "Georgia", "Belarus", "Moldova",
            "Slovakia", "Slovenia", "Croatia", "Serbia", "Bosnia and Herzegovina",
            "North Macedonia", "Albania", "Montenegro", "Estonia", "Latvia",
            "Lithuania", "Lebanon", "Jordan", "Syria", "Yemen", "Oman",
            "Qatar", "Bahrain", "Kuwait", "United Arab Emirates",
            "Cambodia", "Laos", "Malaysia", "Singapore", "Brunei",
            "Sri Lanka", "Maldives", "Nepal", "Bhutan", "Bangladesh",
            "Mongolia", "New Zealand", "Fiji",
        ],
        "enforcement": {
            "mechanism": "IAEA safeguards; UN Security Council referrals for violations",
            "body": "International Atomic Energy Agency",
        },
    },
    {
        "id": "unclos",
        "name": "United Nations Convention on the Law of the Sea (UNCLOS)",
        "subject": "maritime_law",
        "parties": [
            "United Kingdom", "France", "Germany", "China", "India",
            "Japan", "Canada", "Australia", "Brazil", "Argentina",
            "Mexico", "South Africa", "Nigeria", "Egypt", "Russia",
            "Indonesia", "Vietnam", "Thailand", "Philippines", "South Korea",
            "Italy", "Spain", "Netherlands", "Belgium", "Switzerland",
            "Sweden", "Norway", "Denmark", "Finland", "Poland", "Ukraine",
            "Turkey", "Pakistan", "Saudi Arabia", "Iran", "Iraq",
            "United Arab Emirates", "Qatar", "Kuwait", "Oman", "Jordan",
            "Lebanon", "Israel", "Kenya", "Ethiopia", "Ghana", "Senegal",
            "Morocco", "Algeria", "Tunisia", "Colombia", "Peru", "Chile",
            "Ireland", "Portugal", "Greece", "Austria", "Czech Republic",
            "Romania", "Hungary", "Bulgaria", "Albania", "Croatia",
            "Montenegro", "North Macedonia", "Slovenia", "Slovakia",
            "Estonia", "Latvia", "Lithuania",
        ],
        "enforcement": {
            "mechanism": "ITLOS dispute settlement; coastal state enforcement in EEZ",
            "body": "International Tribunal for the Law of the Sea (ITLOS)",
        },
    },
    {
        "id": "cptpp",
        "name": "Comprehensive and Progressive Agreement for Trans-Pacific Partnership (CPTPP)",
        "subject": "trade",
        "parties": [
            "Australia", "Brunei", "Canada", "Chile", "Japan", "Malaysia",
            "Mexico", "New Zealand", "Peru", "Singapore", "Vietnam",
        ],
        "enforcement": {
            "mechanism": "State-to-state dispute settlement; investor-state dispute settlement (ISDS)",
            "body": "CPTPP Commission",
        },
    },
    {
        "id": "usmca",
        "name": "United States-Mexico-Canada Agreement (USMCA)",
        "subject": "trade",
        "parties": ["United States", "Canada", "Mexico"],
        "enforcement": {
            "mechanism": "State-to-state dispute settlement panels; rapid response labor mechanism",
            "body": "USMCA Free Trade Commission",
        },
    },
]


INTERNATIONAL_COURTS: list[dict[str, Any]] = [
    {
        "id": "icj",
        "name": "International Court of Justice",
        "jurisdiction": (
            "Contentious cases between states on treaty interpretation, "
            "territorial disputes, and international law; advisory opinions "
            "to UN bodies. Does not prosecute individuals."
        ),
        "procedures": [
            "Application instituting proceedings",
            "Preliminary objections",
            "Merits phase (written and oral)",
            "Binding judgment (appealable only via revision request)",
        ],
        "notable_cases": [
            "Nicaragua v. United States (1986)",
            "Corfu Channel Case (1949)",
            "South West Africa Cases (1966)",
            "Kosovo Advisory Opinion (2010)",
        ],
    },
    {
        "id": "icc",
        "name": "International Criminal Court",
        "jurisdiction": (
            "Individuals accused of genocide, war crimes, crimes against "
            "humanity, and the crime of aggression. Complementary to "
            "national courts; only acts when states are unwilling or unable."
        ),
        "procedures": [
            "Pre-Trial Chamber authorization",
            "Confirmation of charges",
            "Trial",
            "Appeal and revision",
        ],
        "notable_cases": [
            " Prosecutor v. Omar al-Bashir",
            "Prosecutor v. Thomas Lubanga",
            "Prosecutor v. Uhuru Kenyatta",
            "Uganda situation",
        ],
    },
    {
        "id": "icty",
        "name": "International Criminal Tribunal for the former Yugoslavia",
        "jurisdiction": (
            "Individuals responsible for serious violations of international "
            "humanitarian law committed in the territory of the former "
            "Yugoslavia since 1991. Closed 2017; residual mechanism active."
        ),
        "procedures": [
            "Indictment confirmation",
            "Trial",
            "Appeal",
            "Enforcement of sentences",
        ],
        "notable_cases": [
            "Prosecutor v. Slobodan Milosevic",
            "Prosecutor v. Radovan Karadzic",
            "Prosecutor v. Ratko Mladic",
        ],
    },
    {
        "id": "ictr",
        "name": "International Criminal Tribunal for Rwanda",
        "jurisdiction": (
            "Individuals responsible for genocide and other serious "
            "violations of international humanitarian law committed in "
            "Rwanda or by Rwandan citizens in neighboring states in 1994. "
            "Closed 2015; residual mechanism active."
        ),
        "procedures": [
            "Indictment confirmation",
            "Trial",
            "Appeal",
            "Enforcement of sentences",
        ],
        "notable_cases": [
            "Prosecutor v. Jean-Paul Akayesu",
            "Prosecutor v. Jean Kambanda",
            "Prosecutor v. Theoneste Bagosora",
        ],
    },
    {
        "id": "wto_dsb",
        "name": "World Trade Organization Dispute Settlement Body",
        "jurisdiction": (
            "Trade disputes between WTO member states concerning rights "
            "and obligations under covered agreements (GATT, GATS, TRIPS). "
            "Does not hear criminal matters."
        ),
        "procedures": [
            "Consultations (mandatory)",
            "Panel establishment",
            "Panel report circulation",
            "Appellate Body review",
            "Implementation and retaliation authorization",
        ],
        "notable_cases": [
            "US - Gasoline (1996)",
            "EC - Hormones (1998)",
            "US - Steel Safeguards (2003)",
            "Boeing/Airbus (2021)",
        ],
    },
]


_COURT_JURISDICTION_MAP: dict[str, set[str]] = {
    "icj": {
        "interstate_dispute", "boundary_dispute", "treaty_interpretation",
        "advisory_opinion",
    },
    "icc": {
        "war_crimes", "genocide", "crimes_against_humanity", "aggression",
    },
    "icty": {
        "war_crimes", "genocide", "crimes_against_humanity",
    },
    "ictr": {
        "genocide", "war_crimes", "crimes_against_humanity",
    },
    "wto_dsb": {
        "trade_dispute", "tariff_dispute", "subsidies_dispute",
        "intellectual_property_dispute",
    },
}


def lookup_conflict(region: str) -> list[dict[str, Any]]:
    """Return conflicts whose ``region`` exactly matches (case-sensitive)."""
    return [c for c in ACTIVE_CONFLICTS if c["region"] == region]


def get_treaty(treaty_id: str) -> dict[str, Any] | None:
    """Return the treaty with the given id (case-sensitive), or None."""
    for t in TREATY_DATABASE:
        if t["id"] == treaty_id:
            return t
    return None


def get_treaty_parties(treaty_id: str) -> list[str]:
    """Return the list of party names for a treaty (empty if unknown)."""
    t = get_treaty(treaty_id)
    if t is None:
        return []
    return list(t["parties"])


def get_treaty_obligations(country: str) -> list[dict[str, Any]]:
    """Return treaty obligations binding on ``country``.

    Each entry is ``{"treaty_id": ..., "subject": ...}``. Only treaties the
    country is a party to are included. Returns ``[]`` for unknown countries.
    """
    out: list[dict[str, Any]] = []
    for t in TREATY_DATABASE:
        if country in t["parties"]:
            out.append({"treaty_id": t["id"], "subject": t["subject"]})
    return out


def get_court_jurisdiction(court_id: str) -> str | None:
    """Return the jurisdiction description string for a court, or None."""
    for c in INTERNATIONAL_COURTS:
        if c["id"] == court_id:
            return c["jurisdiction"]
    return None


def check_court_jurisdiction(court_id: str, case_type: str) -> bool:
    """Return True if ``court_id`` may hear ``case_type``."""
    cases = _COURT_JURISDICTION_MAP.get(court_id)
    if cases is None:
        return False
    return case_type in cases


TREATIES: dict[str, list[dict[str, Any]]] = {
    t["id"]: [t] for t in TREATY_DATABASE
}


def lookup_treaties(treaty_id: str) -> dict[str, Any] | None:
    for t in TREATY_DATABASE:
        if t["id"].lower() == treaty_id.lower():
            result: dict[str, Any] = dict(t)
            result["country"] = treaty_id.upper()
            return result
    return None


__all__ = [
    "ConflictType",
    "CONFLICT_TYPES",
    "ACTIVE_CONFLICTS",
    "TREATY_DATABASE",
    "TREATIES",
    "INTERNATIONAL_COURTS",
    "lookup_conflict",
    "lookup_treaties",
    "get_treaty",
    "get_treaty_parties",
    "get_treaty_obligations",
    "get_court_jurisdiction",
    "check_court_jurisdiction",
]
