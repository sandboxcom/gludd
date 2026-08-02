"""Contracts knowledge module for the governance collection.

Exposes a registry of governance contracts — formal agreements between
sovereign states, international organisations, and other governance entities:
treaties, bilateral agreements, multilateral conventions, memoranda of
understanding, protocols, and amendments.

Public surface::

    CONTRACT_TYPES      frozenset of contract type tokens
    CONTRACT_STATUSES   frozenset of contract status tokens
    CONTRACTS           dict[code] -> contract record

    get_contract(code)             -> dict | None
    contracts_by_party(code)       -> list[dict]
    contracts_by_type(type)        -> list[dict]
    contracts_by_status(status)    -> list[dict]
    list_contracts(**filters)      -> list[dict]
    get_contract_parties(code)     -> frozenset | None
"""

from __future__ import annotations

from typing import Any

CONTRACT_TYPES: frozenset[str] = frozenset(
    (
        "treaty",
        "bilateral_agreement",
        "multilateral_convention",
        "memorandum_of_understanding",
        "protocol",
        "amendment",
    )
)

CONTRACT_STATUSES: frozenset[str] = frozenset(
    (
        "in_force",
        "signed",
        "ratified",
        "provisionally_applied",
        "dormant",
        "superseded",
        "denounced",
        "expired",
    )
)

CONTRACTS: dict[str, dict[str, Any]] = {
    "UN_CHARTER": {
        "code": "UN_CHARTER",
        "name": "Charter of the United Nations",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "US",
                "GB",
                "FR",
                "CN",
                "RU",
                "DE",
                "JP",
                "IN",
                "BR",
                "CA",
                "AU",
                "IT",
                "ES",
                "MX",
                "KR",
                "ZA",
                "NG",
                "EG",
                "AR",
                "TR",
                "SA",
                "ID",
                "PK",
                "BD",
                "NG",
            )
        ),
        "effective_date": "1945-10-24",
        "depositary": "United States Government",
        "subject": "international_peace_security",
        "url": "https://treaties.un.org/doc/Publication/CTC/uncharter.pdf",
    },
    "NATO_TREATY": {
        "code": "NATO_TREATY",
        "name": "North Atlantic Treaty",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "US",
                "GB",
                "CA",
                "FR",
                "DE",
                "IT",
                "ES",
                "TR",
                "NL",
                "BE",
                "LU",
                "PT",
                "GR",
                "NO",
                "DK",
                "IS",
                "PL",
                "CZ",
                "HU",
                "RO",
                "BG",
                "EE",
                "LV",
                "LT",
                "SK",
                "SI",
                "HR",
                "AL",
                "ME",
                "MK",
                "FI",
                "SE",
            )
        ),
        "effective_date": "1949-08-24",
        "depositary": "United States Government",
        "subject": "collective_defense",
        "url": "https://www.nato.int/nato_static_fl2014/assets/pdf/stock_publications/20120822_nato_treaty_en_light_2009.pdf",
    },
    "PARIS_AGREEMENT": {
        "code": "PARIS_AGREEMENT",
        "name": "Paris Agreement under the UNFCCC",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "US",
                "GB",
                "FR",
                "DE",
                "CN",
                "IN",
                "JP",
                "BR",
                "CA",
                "AU",
                "ZA",
                "KR",
                "ID",
                "MX",
                "IT",
                "ES",
                "TR",
                "SA",
                "NG",
                "AR",
                "EG",
                "PK",
                "BD",
            )
        ),
        "effective_date": "2016-11-04",
        "depositary": "United Nations Secretary-General",
        "subject": "climate_change",
        "url": "https://treaties.un.org/doc/Treaties/2016/02/20160215 06-03 PM/Ch_XXVII-7-d.pdf",
    },
    "WTO_AGREEMENT": {
        "code": "WTO_AGREEMENT",
        "name": "Marrakesh Agreement Establishing the World Trade Organization",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(("US", "GB", "FR", "DE", "CN", "JP", "IN", "BR", "CA", "AU", "KR", "ZA", "MX")),
        "effective_date": "1995-01-01",
        "depositary": "WTO Director-General",
        "subject": "international_trade",
        "url": "https://www.wto.org/english/docs_e/legal_e/04-wto_e.htm",
    },
    "GENEVA_CONVENTIONS": {
        "code": "GENEVA_CONVENTIONS",
        "name": "Geneva Conventions of 1949",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            ("US", "GB", "FR", "DE", "CN", "RU", "JP", "IN", "BR", "CA", "AU", "CH", "SE", "NO", "IT", "ES")
        ),
        "effective_date": "1950-10-21",
        "depositary": "Swiss Federal Council",
        "subject": "international_humanitarian_law",
        "url": "https://www.icrc.org/en/war-and-law/treaties-customary-law/geneva-conventions",
    },
    "VIENNA_CONVENTION_TREATIES": {
        "code": "VIENNA_CONVENTION_TREATIES",
        "name": "Vienna Convention on the Law of Treaties",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(("GB", "FR", "DE", "JP", "BR", "CA", "AU", "IT", "ES", "MX", "KR", "ZA", "RU", "IN")),
        "effective_date": "1980-01-27",
        "depositary": "United Nations Secretary-General",
        "subject": "treaty_law",
        "url": "https://treaties.un.org/doc/Publication/UNTS/Volume 1155/volume-1155-I-18232-English.pdf",
    },
    "USMCA": {
        "code": "USMCA",
        "name": "United States-Mexico-Canada Agreement",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(("US", "CA", "MX")),
        "effective_date": "2020-07-01",
        "depositary": "United States Government",
        "subject": "international_trade",
        "preceded": "NAFTA",
        "url": "https://ustr.gov/trade-agreements/free-trade-agreements/united-states-mexico-canada-agreement",
    },
    "EU_UK_TCA": {
        "code": "EU_UK_TCA",
        "name": "EU-UK Trade and Cooperation Agreement",
        "type": "bilateral_agreement",
        "status": "in_force",
        "parties": frozenset(("EU", "GB")),
        "effective_date": "2021-05-01",
        "depositary": "European Commission",
        "subject": "international_trade",
        "url": "https://ec.europa.eu/info/strategy/relations-non-eu-countries/relations-united-kingdom/eu-uk-trade-and-cooperation-agreement_en",
    },
    "US_JP_DEFENSE": {
        "code": "US_JP_DEFENSE",
        "name": "Treaty of Mutual Cooperation and Security between the United States and Japan",
        "type": "bilateral_agreement",
        "status": "in_force",
        "parties": frozenset(("US", "JP")),
        "effective_date": "1960-06-23",
        "depositary": "United States Government",
        "subject": "collective_defense",
        "url": "https://www.mofa.go.jp/region/n-america/us/q&a/ref/1.html",
    },
    "LEAGUE_COVENANT": {
        "code": "LEAGUE_COVENANT",
        "name": "Covenant of the League of Nations",
        "type": "multilateral_convention",
        "status": "superseded",
        "parties": frozenset(("GB", "FR", "IT", "JP")),
        "effective_date": "1920-01-10",
        "depositary": "League of Nations",
        "subject": "international_peace_security",
        "superseded_by": "UN_CHARTER",
    },
    "ABM_TREATY": {
        "code": "ABM_TREATY",
        "name": "Anti-Ballistic Missile Treaty",
        "type": "bilateral_agreement",
        "status": "denounced",
        "parties": frozenset(("US", "RU")),
        "effective_date": "1972-10-03",
        "withdrawn_date": "2002-06-13",
        "depositary": "United States Government",
        "subject": "arms_control",
    },
    "UNCLOS": {
        "code": "UNCLOS",
        "name": "United Nations Convention on the Law of the Sea",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "GB",
                "FR",
                "DE",
                "CN",
                "JP",
                "IN",
                "BR",
                "CA",
                "AU",
                "RU",
                "ZA",
                "KR",
                "MX",
                "NO",
                "NL",
                "IT",
                "ES",
                "PT",
                "GR",
                "TR",
                "EG",
                "NG",
                "AR",
            )
        ),
        "effective_date": "1994-11-16",
        "depositary": "United Nations Secretary-General",
        "subject": "maritime_law",
        "url": "https://www.un.org/depts/los/convention_agreements/texts/unclos/unclos_e.pdf",
    },
    "CTBT": {
        "code": "CTBT",
        "name": "Comprehensive Nuclear-Test-Ban Treaty",
        "type": "multilateral_convention",
        "status": "signed",
        "parties": frozenset(
            ("GB", "FR", "DE", "JP", "CA", "AU", "IT", "ES", "KR", "ZA", "MX", "BR", "ID", "TR", "NL", "SE", "NO")
        ),
        "signature_date": "1996-09-24",
        "depositary": "United Nations Secretary-General",
        "subject": "arms_control",
        "url": "https://www.ctbto.org/our-mission/the-treaty",
    },
    "STCW": {
        "code": "STCW",
        "name": "International Convention on Standards of Training, Certification and Watchkeeping for Seafarers",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "GB",
                "FR",
                "DE",
                "CN",
                "JP",
                "IN",
                "BR",
                "CA",
                "AU",
                "RU",
                "KR",
                "IT",
                "ES",
                "GR",
                "TR",
                "NL",
                "NO",
                "DK",
                "SE",
                "FI",
                "PT",
            )
        ),
        "effective_date": "1984-04-28",
        "depositary": "International Maritime Organization",
        "subject": "maritime_safety",
        "url": "https://www.imo.org/en/OurWork/HumanElement/Pages/STCW-Conv-LINK.aspx",
    },
    "CERN_CONVENTION": {
        "code": "CERN_CONVENTION",
        "name": "Convention for the Establishment of a European Organization for Nuclear Research",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "FR",
                "DE",
                "IT",
                "ES",
                "CH",
                "GB",
                "NL",
                "BE",
                "SE",
                "NO",
                "DK",
                "FI",
                "PT",
                "GR",
                "PL",
                "CZ",
                "HU",
                "SK",
                "BG",
                "IL",
            )
        ),
        "effective_date": "1954-09-29",
        "depositary": "UNESCO Director-General",
        "subject": "scientific_cooperation",
        "url": "https://home.cern/about/who-we-are/our-governance/member-states",
    },
    "WHO_CONSTITUTION": {
        "code": "WHO_CONSTITUTION",
        "name": "Constitution of the World Health Organization",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "US",
                "GB",
                "FR",
                "DE",
                "CN",
                "JP",
                "IN",
                "BR",
                "CA",
                "AU",
                "IT",
                "ES",
                "ZA",
                "KR",
                "MX",
                "ID",
                "TR",
                "SA",
                "NG",
                "EG",
                "AR",
                "PK",
                "BD",
            )
        ),
        "effective_date": "1948-04-07",
        "depositary": "United Nations Secretary-General",
        "subject": "public_health",
        "url": "https://www.who.int/about/governance/constitution",
    },
    "KIGALI_AMENDMENT": {
        "code": "KIGALI_AMENDMENT",
        "name": "Kigali Amendment to the Montreal Protocol",
        "type": "amendment",
        "status": "in_force",
        "parties": frozenset(
            ("GB", "FR", "DE", "CN", "JP", "IN", "BR", "CA", "AU", "KR", "ZA", "MX", "IT", "ES", "NL", "SE", "NO", "TR")
        ),
        "effective_date": "2019-01-01",
        "amends": "MONTREAL_PROTOCOL",
        "depositary": "United Nations Secretary-General",
        "subject": "ozone_protection",
        "url": "https://treaties.un.org/doc/Treaties/2016/10/20161015 03-23 PM/Ch_XXVII-2-f.pdf",
    },
    "US_FR_TAX_TREATY": {
        "code": "US_FR_TAX_TREATY",
        "name": (
            "Convention between the Government of the United States of America "
            "and the Government of the French Republic for the Avoidance of "
            "Double Taxation"
        ),
        "type": "bilateral_agreement",
        "status": "in_force",
        "parties": frozenset(("US", "FR")),
        "effective_date": "1996-01-01",
        "depositary": "United States Government",
        "subject": "double_taxation",
    },
    "INTERPOL_CONSTITUTION": {
        "code": "INTERPOL_CONSTITUTION",
        "name": "Constitution of the International Criminal Police Organization",
        "type": "multilateral_convention",
        "status": "in_force",
        "parties": frozenset(
            (
                "US",
                "GB",
                "FR",
                "DE",
                "CN",
                "JP",
                "IN",
                "BR",
                "CA",
                "AU",
                "IT",
                "ES",
                "ZA",
                "KR",
                "MX",
                "ID",
                "TR",
                "SA",
                "NG",
                "EG",
                "AR",
            )
        ),
        "effective_date": "1956-06-13",
        "depositary": "French Government",
        "subject": "law_enforcement_cooperation",
        "url": "https://www.interpol.int/Who-we-are/Legal-framework/Legal-documents",
    },
}

# ── Index builders ──────────────────────────────────────────────────────────

_PARTY_INDEX: dict[str, list[str]] | None = None
_TYPE_INDEX: dict[str, list[str]] | None = None
_STATUS_INDEX: dict[str, list[str]] | None = None


def _ensure_indexes() -> None:
    global _PARTY_INDEX, _TYPE_INDEX, _STATUS_INDEX
    if _PARTY_INDEX is not None:
        return
    _PARTY_INDEX = {}
    _TYPE_INDEX = {}
    _STATUS_INDEX = {}
    for code, contract in CONTRACTS.items():
        for party in contract["parties"]:
            _PARTY_INDEX.setdefault(party, []).append(code)
        _TYPE_INDEX.setdefault(contract["type"], []).append(code)
        _STATUS_INDEX.setdefault(contract["status"], []).append(code)


# ── Public API ──────────────────────────────────────────────────────────────


def get_contract(code: str) -> dict[str, Any] | None:
    lookup = code.upper()
    if lookup not in CONTRACTS:
        return None
    return dict(CONTRACTS[lookup])


def contracts_by_party(country_code: str) -> list[dict[str, Any]]:
    _ensure_indexes()
    assert _PARTY_INDEX is not None
    codes = _PARTY_INDEX.get(country_code.upper(), [])
    return [dict(CONTRACTS[c]) for c in codes]


def contracts_by_type(contract_type: str) -> list[dict[str, Any]]:
    _ensure_indexes()
    assert _TYPE_INDEX is not None
    key = contract_type.lower()
    codes = _TYPE_INDEX.get(key, [])
    return [dict(CONTRACTS[c]) for c in codes]


def contracts_by_status(status: str) -> list[dict[str, Any]]:
    _ensure_indexes()
    assert _STATUS_INDEX is not None
    key = status.lower()
    codes = _STATUS_INDEX.get(key, [])
    return [dict(CONTRACTS[c]) for c in codes]


def list_contracts(
    contract_type: str | None = None,
    status: str | None = None,
    party: str | None = None,
) -> list[dict[str, Any]]:
    result: set[str] | None = None
    if contract_type is not None:
        found = {c["code"] for c in contracts_by_type(contract_type)}
        result = found if result is None else result & found
    if status is not None:
        found = {c["code"] for c in contracts_by_status(status)}
        result = found if result is None else result & found
    if party is not None:
        found = {c["code"] for c in contracts_by_party(party)}
        result = found if result is None else result & found
    if result is None:
        result = set(CONTRACTS.keys())
    return [dict(CONTRACTS[c]) for c in sorted(result)]


def get_contract_parties(code: str) -> frozenset[str] | None:
    contract = CONTRACTS.get(code.upper())
    if contract is None:
        return None
    return contract["parties"]
