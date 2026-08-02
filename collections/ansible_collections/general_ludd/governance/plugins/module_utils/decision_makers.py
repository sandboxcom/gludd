#!/usr/bin/env python3
"""Decision makers knowledge module for the governance collection.

Exposes political decision-maker profiles, role types, influence networks,
and proclivity assessment. Consumed by the governance agent for
"who decides what" queries.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# ── Role taxonomy ───────────────────────────────────────────────────────────

ROLE_TYPES: frozenset[str] = frozenset(
    {
        "head_of_state",
        "minister",
        "legislator",
        "judge",
        "regulator",
        "military_leader",
        "diplomat",
        "bureaucrat",
        "local_official",
    }
)

# ── Profile template ────────────────────────────────────────────────────────

DECISION_MAKER_PROFILE_TEMPLATE: dict[str, type] = {
    "name": str,
    "title": str,
    "body": str,
    "jurisdiction": str,
    "term": str,
    "appointment_process": str,
    "decision_authority": dict,
    "known_positions": list,
    "voting_record_summary": str,
    "public_statements": list,
    "campaign_finance": list,
    "lobbying_connections": list,
    "role": str,
    "person_id": str,
}

# ── Decision-maker profiles ─────────────────────────────────────────────────

DECISION_MAKER_PROFILES: list[dict[str, Any]] = [
    {
        "person_id": "us-sen-01",
        "name": "Senator James Whitfield",
        "title": "United States Senator",
        "body": "us-senate",
        "role": "legislator",
        "jurisdiction": "US-Federal",
        "term": "elected, 6-year term",
        "appointment_process": "popular election",
        "decision_authority": {
            "powers": [
                "vote_on_legislation",
                "approve_treaties",
                "confirm_appointments",
                "override_veto",
            ],
            "binding": True,
            "scope": [
                "taxation",
                "defense_spending",
                "healthcare",
                "immigration",
                "trade",
            ],
        },
        "known_positions": [
            {
                "topic": "taxation",
                "stance": "expansionist",
                "summary": "Favours broadening the tax base with reduced marginal rates.",
            },
            {
                "topic": "defense_spending",
                "stance": "expansionist",
                "summary": "Advocates for increased appropriations.",
            },
        ],
        "voting_record_summary": "Party-line votes on fiscal matters; bipartisan on defence.",
        "public_statements": [
            "March 2025: called for middle-class tax relief.",
            "February 2025: argued for higher defence budget.",
        ],
        "campaign_finance": [
            "Defence industry: $450,000",
            "Financial services: $320,000",
        ],
        "lobbying_connections": [
            "Aerospace Industries Association",
            "American Bankers Association",
        ],
    },
    {
        "person_id": "us-house-01",
        "name": "Representative Maria Gonzalez",
        "title": "US House Representative",
        "body": "us-house",
        "role": "legislator",
        "jurisdiction": "US-Federal",
        "term": "elected, 2-year term",
        "appointment_process": "popular election",
        "decision_authority": {
            "powers": [
                "vote_on_legislation",
                "initiate_revenue_bills",
                "impeachment_power",
            ],
            "binding": True,
            "scope": [
                "healthcare",
                "education",
                "taxation",
            ],
        },
        "known_positions": [
            {
                "topic": "healthcare",
                "stance": "expansionist",
                "summary": "Supports single-payer expansion.",
            },
        ],
        "voting_record_summary": "Consistent YES on healthcare expansion bills.",
        "public_statements": [
            "April 2025: introduced Medicare expansion bill.",
        ],
        "campaign_finance": [
            "Healthcare PACs: $280,000",
            "Teachers union: $150,000",
        ],
        "lobbying_connections": [
            "American Hospital Association",
        ],
    },
    {
        "person_id": "us-fed-01",
        "name": "Dr. Katherine Chen",
        "title": "Chair of the Federal Reserve",
        "body": "us-federal-reserve",
        "role": "bureaucrat",
        "jurisdiction": "US-Federal",
        "term": "appointed, 4-year renewable term",
        "appointment_process": "nominated by President, confirmed by Senate",
        "decision_authority": {
            "powers": [
                "set_interest_rate",
                "open_market_operations",
                "bank_supervision",
                "emergency_lending",
            ],
            "binding": True,
            "scope": [
                "monetary policy",
                "financial stability",
                "banking regulation",
            ],
        },
        "known_positions": [
            {
                "topic": "monetary policy",
                "stance": "neutral",
                "summary": "Data-dependent approach to rate setting.",
            },
        ],
        "voting_record_summary": "N/A (non-voting appointed role).",
        "public_statements": [
            "June 2025: inflation trending toward 2% target.",
        ],
        "campaign_finance": [],
        "lobbying_connections": [],
    },
    {
        "person_id": "eu-com-01",
        "name": "Commissioner Hélène Moreau",
        "title": "European Commissioner for Competition",
        "body": "eu-commission",
        "role": "regulator",
        "jurisdiction": "EU",
        "term": "appointed, 5-year renewable term",
        "appointment_process": "nominated by member states, approved by Parliament",
        "decision_authority": {
            "powers": [
                "antitrust_enforcement",
                "merger_control",
                "state_aid_oversight",
                "market_investigation",
            ],
            "binding": True,
            "scope": [
                "antitrust",
                "merger_review",
                "state_subsidies",
            ],
        },
        "known_positions": [
            {
                "topic": "antitrust",
                "stance": "restrictive",
                "summary": "Aggressive enforcement against Big Tech; record fines levied.",
            },
        ],
        "voting_record_summary": "N/A (commissioner, not legislator).",
        "public_statements": [
            "May 2025: opened formal investigation into digital platform X.",
        ],
        "campaign_finance": [],
        "lobbying_connections": [
            "European Consumer Organisation (BEUC)",
        ],
    },
    {
        "person_id": "us-sc-01",
        "name": "Justice Robert Harker",
        "title": "Associate Justice, Supreme Court of the United States",
        "body": "us-supreme-court",
        "role": "judge",
        "jurisdiction": "US-Federal",
        "term": "lifetime appointment",
        "appointment_process": "nominated by President, confirmed by Senate",
        "decision_authority": {
            "powers": [
                "judicial_review",
                "constitutional_interpretation",
                "final_appeal",
            ],
            "binding": True,
            "scope": [
                "civil_rights",
                "constitutional_law",
                "federal_statutes",
            ],
        },
        "known_positions": [
            {
                "topic": "civil_rights",
                "stance": "restrictive",
                "summary": "Narrow reading of statutory civil rights protections.",
            },
        ],
        "voting_record_summary": "Conservative voting bloc.",
        "public_statements": [
            "October 2024: dissenting opinion in Title VII expansion case.",
        ],
        "campaign_finance": [],
        "lobbying_connections": [
            "Federalist Society",
        ],
    },
]

# ── Influence networks ──────────────────────────────────────────────────────

INFLUENCE_NETWORKS: dict[str, list[dict[str, str]]] = {
    "political_parties": [
        {"name": "Majority Party Caucus", "weight": "high"},
        {"name": "Progressive Caucus", "weight": "medium"},
    ],
    "think_tanks": [
        {"name": "Brookings Institution", "weight": "medium"},
        {"name": "American Enterprise Institute", "weight": "medium"},
    ],
    "industry_groups": [
        {"name": "Chamber of Commerce", "weight": "high"},
        {"name": "Business Roundtable", "weight": "medium"},
    ],
    "labour_unions": [
        {"name": "AFL-CIO", "weight": "medium"},
        {"name": "SEIU", "weight": "medium"},
    ],
}

_PERSON_NETWORKS: dict[str, dict[str, list[dict[str, str]]]] = {
    "us-sen-01": {
        "political_parties": [
            {"name": "Majority Party Caucus", "weight": "high"},
        ],
        "think_tanks": [
            {"name": "American Enterprise Institute", "weight": "high"},
        ],
        "industry_groups": [
            {"name": "Chamber of Commerce", "weight": "medium"},
        ],
    },
}

# ── Bias indicators ─────────────────────────────────────────────────────────

BIAS_INDICATORS: dict[str, str] = {
    "voting_patterns": "How a decision maker has voted on related legislation or resolutions.",
    "campaign_donors": "Top campaign contributors and their policy interests.",
    "board_memberships": "Current and prior board seats, corporate or non-profit.",
    "statements_on_topic": "Public statements, floor speeches, and published opinions on the topic.",
}

# ── Index ───────────────────────────────────────────────────────────────────

_PROFILES_BY_ID: dict[str, dict[str, Any]] = {p["person_id"]: p for p in DECISION_MAKER_PROFILES}


def _match_topic(topic: str, profile: dict[str, Any]) -> bool:
    """Check if profile's known_positions or authority scope covers topic."""
    t = topic.strip().lower()
    for pos in profile.get("known_positions", []):
        if t in pos.get("topic", "").lower():
            return True
    for scope_item in profile.get("decision_authority", {}).get("scope", []):
        if t in scope_item.lower():
            return True
    return False


# ── Public functions ────────────────────────────────────────────────────────


def lookup_decision_maker(body: str, role: str | None = None) -> list[dict[str, Any]]:
    """Return decision-maker profiles associated with a governing body.

    Args:
        body: Governing body id (e.g. ``"us-senate"``, ``"us-house"``).
        role: Optional role filter (must be in :data:`ROLE_TYPES`).

    Returns:
        List of matching profiles (empty if none found or role invalid).
    """
    if role is not None and role not in ROLE_TYPES:
        return []
    results: list[dict[str, Any]] = []
    for profile in DECISION_MAKER_PROFILES:
        if profile.get("body") == body:
            if role is None or profile.get("role") == role:
                results.append(profile)
    return results


def get_decision_authority(person_id: str) -> dict[str, Any] | None:
    """Return the decision-authority block for a decision maker.

    Returns None if ``person_id`` is unknown.
    """
    profile = _PROFILES_BY_ID.get(person_id)
    if profile is None:
        return None
    return dict(profile.get("decision_authority", {}))


def get_influence_network(person_id: str) -> dict[str, list[dict[str, str]]] | None:
    """Return the influence-network affiliations for a decision maker.

    Returns a dict keyed by network category (keys from
    :data:`INFLUENCE_NETWORKS`), empty dict if the person has no known
    affiliations, or None if ``person_id`` is unknown.
    """
    if person_id not in _PROFILES_BY_ID:
        return None
    return _PERSON_NETWORKS.get(person_id, {})


def find_decision_maker(topic: str, jurisdiction: str | None = None) -> list[dict[str, Any]]:
    """Find decision makers whose known positions or authority scope cover
    ``topic``, optionally filtered by jurisdiction.

    Matching is case-insensitive on the topic string.
    """
    results: list[dict[str, Any]] = []
    for profile in DECISION_MAKER_PROFILES:
        if jurisdiction is not None and profile.get("jurisdiction") != jurisdiction:
            continue
        if _match_topic(topic, profile):
            results.append(profile)
    return results


def assess_proclivity(person_id: str, topic: str) -> dict[str, Any]:
    """Assess a decision maker's predicted proclivity (lean) on a topic.

    Returns:
        A dict with ``person_id``, ``topic``, ``score`` (-1.0 restrictive to
        1.0 expansionist), ``lean`` (``"restrictive"``, ``"expansionist"``, or
        ``"neutral"``), ``signals`` (list of signal dicts), and ``confidence``
        (0.0 to 1.0). Unknown persons receive ``{"error": ...}``.
    """
    profile = _PROFILES_BY_ID.get(person_id)
    if profile is None:
        return {
            "error": f"unknown person_id {person_id!r}",
            "person_id": person_id,
            "topic": topic,
        }

    t = topic.strip().lower()
    signals: list[dict[str, Any]] = []

    # 1. Known positions
    for pos in profile.get("known_positions", []):
        if t in pos.get("topic", "").lower():
            stance = pos.get("stance", "neutral")
            signals.append(
                {
                    "source": "known_position",
                    "topic": pos["topic"],
                    "stance": stance,
                    "summary": pos.get("summary", ""),
                }
            )

    # 2. Voting record
    voting = profile.get("voting_record_summary", "")
    if voting and voting != "N/A (non-voting appointed role).":
        vr_lower = voting.lower()
        if t in vr_lower:
            signals.append(
                {
                    "source": "voting_record",
                    "summary": voting,
                }
            )

    # 3. Influence networks (only when topic-relevant signals exist above)
    has_topic_signal = bool([s for s in signals if s["source"] in ("known_position", "voting_record")])
    if has_topic_signal:
        networks = _PERSON_NETWORKS.get(person_id, {})
        for category, entries in networks.items():
            signals.append(
                {
                    "source": "influence_network",
                    "category": category,
                    "entries": entries,
                }
            )

    # 4. Campaign finance (only when topic-relevant signals exist above)
    if has_topic_signal:
        campaign = profile.get("campaign_finance", [])
        t_lower = t.replace("_", " ")
        if campaign:
            for item in campaign:
                if any(kw in item.lower() for kw in t_lower.split()):
                    signals.append(
                        {
                            "source": "campaign_finance",
                            "contributor": item,
                        }
                    )

    # Compute score and lean
    if not signals:
        return {
            "person_id": person_id,
            "topic": topic,
            "score": 0.0,
            "lean": "neutral",
            "confidence": 0.0,
            "signals": [],
        }

    stance_count = 0
    stance_score = 0.0
    for sig in signals:
        if sig["source"] == "known_position":
            stance_count += 1
            if sig["stance"] == "expansionist":
                stance_score += 0.7
            elif sig["stance"] == "restrictive":
                stance_score -= 0.7
        elif sig["source"] in ("influence_network", "campaign_finance"):
            stance_count += 1
            # Influence signals lean expansionist by default (more funding = more push)
            stance_score += 0.3
        elif sig["source"] == "voting_record":
            stance_count += 1
            stance_score += 0.2

    if stance_count == 0:
        return {
            "person_id": person_id,
            "topic": topic,
            "score": 0.0,
            "lean": "neutral",
            "confidence": 0.0,
            "signals": signals,
        }

    raw = stance_score / stance_count
    clamped = max(-1.0, min(1.0, raw))
    confidence = min(1.0, stance_count / 4.0)

    if clamped > 0.2:
        lean = "expansionist"
    elif clamped < -0.2:
        lean = "restrictive"
    else:
        lean = "neutral"

    return {
        "person_id": person_id,
        "topic": topic,
        "score": round(clamped, 2),
        "lean": lean,
        "confidence": round(confidence, 2),
        "signals": signals,
    }


# ── Legacy API (backward compatible) ────────────────────────────────────────

DECISION_MAKERS: dict[str, list[dict[str, Any]]] = {
    "US": [
        {
            "role": "President of the United States",
            "institution": "Executive Office",
            "branch": "executive",
            "selection": "elected (Electoral College, 4-yr term)",
        },
        {
            "role": "Speaker of the House",
            "institution": "House of Representatives",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Senate Majority Leader",
            "institution": "Senate",
            "branch": "legislative",
            "selection": "elected by caucus",
        },
        {
            "role": "Chief Justice of the United States",
            "institution": "Supreme Court",
            "branch": "judicial",
            "selection": "appointed (lifetime)",
        },
    ],
    "GB": [
        {
            "role": "Prime Minister",
            "institution": "Crown / Parliament",
            "branch": "executive",
            "selection": "appointed by monarch (leader of majority)",
        },
        {"role": "Monarch", "institution": "The Crown", "branch": "head of state", "selection": "hereditary"},
        {
            "role": "Lord Speaker",
            "institution": "House of Lords",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Speaker of the House of Commons",
            "institution": "House of Commons",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
    ],
    "DE": [
        {
            "role": "Bundeskanzler (Federal Chancellor)",
            "institution": "Bundesregierung",
            "branch": "executive",
            "selection": "elected by Bundestag",
        },
        {
            "role": "Bundespräsident (Federal President)",
            "institution": "Head of state",
            "branch": "head of state",
            "selection": "elected by Federal Convention",
        },
        {
            "role": "President of the Bundestag",
            "institution": "Bundestag",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
    ],
    "FR": [
        {
            "role": "Président de la République",
            "institution": "Presidency",
            "branch": "executive",
            "selection": "elected (5-yr term)",
        },
        {
            "role": "Prime Minister",
            "institution": "Government",
            "branch": "executive",
            "selection": "appointed by President",
        },
        {
            "role": "President of the National Assembly",
            "institution": "National Assembly",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
    ],
    "JP": [
        {
            "role": "Prime Minister of Japan",
            "institution": "Cabinet",
            "branch": "executive",
            "selection": "designated by Diet",
        },
        {"role": "Emperor", "institution": "Imperial House", "branch": "head of state", "selection": "hereditary"},
        {
            "role": "President of the House of Representatives",
            "institution": "National Diet",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
    ],
    "IN": [
        {
            "role": "Prime Minister of India",
            "institution": "Union Council of Ministers",
            "branch": "executive",
            "selection": "appointed by President (majority of Lok Sabha)",
        },
        {
            "role": "President of India",
            "institution": "Rashtrapati Bhavan",
            "branch": "head of state",
            "selection": "elected by Electoral College",
        },
        {
            "role": "Speaker of the Lok Sabha",
            "institution": "Lok Sabha",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Chief Justice of India",
            "institution": "Supreme Court",
            "branch": "judicial",
            "selection": "appointed",
        },
    ],
    "AU": [
        {
            "role": "Prime Minister of Australia",
            "institution": "Cabinet",
            "branch": "executive",
            "selection": "commissioned by Governor-General",
        },
        {
            "role": "Governor-General",
            "institution": "Crown representative",
            "branch": "head of state",
            "selection": "appointed (representing the Monarch)",
        },
        {
            "role": "Speaker of the House of Representatives",
            "institution": "Parliament",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
    ],
    "IT": [
        {
            "role": "President of the Council of Ministers (Prime Minister)",
            "institution": "Palazzo Chigi",
            "branch": "executive",
            "selection": "appointed by President of the Republic",
        },
        {
            "role": "President of the Republic",
            "institution": "Quirinale",
            "branch": "head of state",
            "selection": "elected by Parliament in joint session (7-yr term)",
        },
        {
            "role": "President of the Chamber of Deputies",
            "institution": "Montecitorio",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Senate",
            "institution": "Palazzo Madama",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Constitutional Court",
            "institution": "Corte Costituzionale",
            "branch": "judicial",
            "selection": "elected by the Court from among its judges",
        },
    ],
    "ES": [
        {
            "role": "President of the Government (Prime Minister)",
            "institution": "Palacio de la Moncloa",
            "branch": "executive",
            "selection": "invested by Congress of Deputies",
        },
        {
            "role": "King of Spain (Monarch)",
            "institution": "La Zarzuela Palace",
            "branch": "head of state",
            "selection": "hereditary (constitutional monarchy)",
        },
        {
            "role": "President of the Congress of Deputies",
            "institution": "Congreso de los Diputados",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Senate",
            "institution": "Senado",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Constitutional Court",
            "institution": "Tribunal Constitucional",
            "branch": "judicial",
            "selection": "appointed by King on proposal of Congress, Senate, Government, and CGPJ",
        },
    ],
    "MX": [
        {
            "role": "President of the United Mexican States",
            "institution": "National Palace (Palacio Nacional)",
            "branch": "executive",
            "selection": "directly elected (6-yr single term, no re-election)",
        },
        {
            "role": "President of the Chamber of Deputies",
            "institution": "Camara de Diputados",
            "branch": "legislative",
            "selection": "elected by chamber annually",
        },
        {
            "role": "President of the Senate",
            "institution": "Camara de Senadores",
            "branch": "legislative",
            "selection": "elected by chamber annually",
        },
        {
            "role": "Chief Justice of the Supreme Court",
            "institution": "Suprema Corte de Justicia de la Nacion",
            "branch": "judicial",
            "selection": "elected by the full court from among its ministers (4-yr term)",
        },
    ],
    "ZA": [
        {
            "role": "President of the Republic of South Africa",
            "institution": "Union Buildings",
            "branch": "executive",
            "selection": "elected by National Assembly from its members",
        },
        {
            "role": "Speaker of the National Assembly",
            "institution": "Parliament of South Africa",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Chairperson of the National Council of Provinces",
            "institution": "Parliament of South Africa",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Chief Justice of the Constitutional Court",
            "institution": "Constitutional Court (Braamfontein)",
            "branch": "judicial",
            "selection": "appointed by President after consultation with JSC",
        },
    ],
    "KR": [
        {
            "role": "President of the Republic of Korea",
            "institution": "Blue House",
            "branch": "executive",
            "selection": "directly elected (single 5-yr term)",
        },
        {
            "role": "Prime Minister",
            "institution": "Government Complex Sejong/Seoul",
            "branch": "executive",
            "selection": "appointed by President, confirmed by National Assembly",
        },
        {
            "role": "Speaker of the National Assembly",
            "institution": "National Assembly (Yeouido)",
            "branch": "legislative",
            "selection": "elected by chamber (2-yr term)",
        },
        {
            "role": "Chief Justice of the Supreme Court",
            "institution": "Supreme Court of Korea (Seocho)",
            "branch": "judicial",
            "selection": "appointed by President, confirmed by National Assembly (6-yr term, non-renewable)",
        },
    ],
    "SE": [
        {
            "role": "Prime Minister of Sweden (Statsminister)",
            "institution": "Rosenbad / Government Offices",
            "branch": "executive",
            "selection": "proposed by Speaker of Riksdag, confirmed by negative vote",
        },
        {
            "role": "Monarch (King of Sweden)",
            "institution": "Royal Palace (Stockholm)",
            "branch": "head of state",
            "selection": "hereditary (ceremonial constitutional monarch)",
        },
        {
            "role": "Speaker of the Riksdag (Talman)",
            "institution": "Riksdag (Parliament)",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "Chief Justice of the Supreme Court",
            "institution": "Hoegsta Domstolen",
            "branch": "judicial",
            "selection": "appointed by Government",
        },
    ],
    "NL": [
        {
            "role": "Prime Minister of the Netherlands",
            "institution": "Binnenhof (Catshuis)",
            "branch": "executive",
            "selection": "appointed by monarch (leader of governing coalition)",
        },
        {
            "role": "Monarch (King of the Netherlands)",
            "institution": "Huis ten Bosch",
            "branch": "head of state",
            "selection": "hereditary (constitutional monarchy)",
        },
        {
            "role": "President of the House of Representatives",
            "institution": "Tweede Kamer (Binnenhof)",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Senate",
            "institution": "Eerste Kamer (Binnenhof)",
            "branch": "legislative",
            "selection": "elected by chamber",
        },
        {
            "role": "President of the Supreme Court",
            "institution": "Hoge Raad der Nederlanden (The Hague)",
            "branch": "judicial",
            "selection": "appointed by Government on recommendation of the Court (lifetime)",
        },
    ],
    "BR": [
        {
            "role": "President of the Federative Republic of Brazil",
            "institution": "Palacio do Planalto",
            "branch": "executive",
            "selection": "directly elected (4-yr term, renewable once)",
        },
        {
            "role": "President of the Chamber of Deputies",
            "institution": "Camara dos Deputados (Congresso Nacional)",
            "branch": "legislative",
            "selection": "elected by chamber (2-yr term)",
        },
        {
            "role": "President of the Federal Senate",
            "institution": "Senado Federal (Congresso Nacional)",
            "branch": "legislative",
            "selection": "elected by chamber (2-yr term)",
        },
        {
            "role": "President of the Supreme Federal Court (STF)",
            "institution": "Supremo Tribunal Federal (Praca dos Tres Poderes)",
            "branch": "judicial",
            "selection": "elected by the full court from among its ministers (2-yr term)",
        },
    ],
}

BRANCHES = frozenset({dm["branch"] for makers in DECISION_MAKERS.values() for dm in makers})


def list_countries() -> list[str]:
    return sorted(DECISION_MAKERS)


def lookup_decision_makers(country: str, branch: str | None = None) -> dict[str, Any]:
    """Lookup decision-maker roles for ``country`` optionally filtered by ``branch``."""
    code = country.upper()
    if code not in DECISION_MAKERS:
        return {
            "found": False,
            "country": code,
            "message": f"No decision-maker data for country code '{code}'",
        }
    makers = DECISION_MAKERS[code]
    if branch:
        wanted = branch.lower()
        makers = [m for m in makers if m["branch"] == wanted]
    return {
        "found": True,
        "country": code,
        "branch_filter": branch,
        "count": len(makers),
        "decision_makers": makers,
        "available_branches": sorted({m["branch"] for m in DECISION_MAKERS[code]}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Governance decision-maker lookup")
    parser.add_argument("--country", required=True, help="ISO 3166-1 alpha-2 code")
    parser.add_argument("--branch", default=None, help="Optional branch filter")
    parser.add_argument("--list-countries", action="store_true")
    args = parser.parse_args()

    if args.list_countries:
        print(json.dumps({"countries": list_countries()}, indent=2))
        return 0

    result = lookup_decision_makers(args.country, args.branch)
    print(json.dumps(result, indent=2))
    return 0 if result["found"] else 1


__all__ = [
    "BIAS_INDICATORS",
    "BRANCHES",
    "DECISION_MAKERS",
    "DECISION_MAKER_PROFILES",
    "DECISION_MAKER_PROFILE_TEMPLATE",
    "INFLUENCE_NETWORKS",
    "ROLE_TYPES",
    "assess_proclivity",
    "find_decision_maker",
    "get_decision_authority",
    "get_influence_network",
    "list_countries",
    "lookup_decision_maker",
    "lookup_decision_makers",
]

if __name__ == "__main__":
    sys.exit(main())
