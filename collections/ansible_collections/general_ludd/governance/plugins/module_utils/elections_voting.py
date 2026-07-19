"""
elections_voting -- Election systems, voter eligibility, electoral bodies,
and polling procedures knowledge base for the governance collection.

Data shape:

    ELECTION_SYSTEMS: dict[str, dict]  -- system_type -> description/structure/examples
    ELECTION_DATA: dict[str, dict]     -- ISO-3166-1 alpha-2 country code -> election profile
    ELECTORAL_BODIES: dict[str, dict]  -- country code -> electoral management body
    POLLING_PROCEDURES: dict[str, dict] -- country code -> polling day procedures

Functions:
    get_election_info(country) -> dict | None
    list_election_systems() -> list[str]
    get_electoral_body(country) -> dict | None
    get_polling_procedures(country) -> dict | None
    get_voter_eligibility(country) -> dict | None
    list_countries_with_elections() -> list[str]

Notes:
    * Data reflects statutory provisions and published procedures as of the
      most recent observed national election cycle. Details may change with
      electoral reform; this module provides structured knowledge of election
      architecture, not live electoral guidance.
    * Country codes are ISO 3166-1 alpha-2 (US, GB, DE, ...).
    * Voting age and eligibility reflect national-level elections; sub-national
      elections may differ.
"""

from __future__ import annotations

from typing import Any

# ── Election system definitions ──────────────────────────────────────────────
# Each entry describes a category of electoral system: how votes translate to
# seats, the ballot structure, and example jurisdictions.

ELECTION_SYSTEMS: dict[str, dict[str, Any]] = {
    "fptp": {
        "description": (
            "First-Past-The-Post (plurality): voters cast a single vote for a "
            "candidate in a single-member district. The candidate with the most "
            "votes wins, regardless of whether they achieve a majority. Tends to "
            "produce two-party systems and single-party majority governments."
        ),
        "ballot_type": "single_mark",
        "district_type": "single_member",
        "example_countries": ["US", "GB", "CA", "IN", "NG"],
    },
    "runoff": {
        "description": (
            "Two-Round System: if no candidate secures an absolute majority in "
            "the first round, a runoff is held between the top two candidates. "
            "Ensures the winner has majority support. Used primarily for "
            "presidential elections and some legislative seats."
        ),
        "ballot_type": "single_mark",
        "district_type": "single_member",
        "example_countries": ["FR", "BR", "TR", "AR", "ID"],
    },
    "proportional_representation_list": {
        "description": (
            "Party-List Proportional Representation: voters cast a ballot for a "
            "party list in multi-member districts. Seats are allocated to parties "
            "in proportion to their vote share. Closed-list variants give parties "
            "full control over candidate order; open-list allows voter preference "
            "for individual candidates within the list."
        ),
        "ballot_type": "party_list",
        "district_type": "multi_member",
        "example_countries": ["ES", "PT", "SE", "NO", "DK", "IL", "ZA"],
    },
    "mixed_member_proportional": {
        "description": (
            "Mixed-Member Proportional (MMP): voters cast two votes -- one for "
            "a constituency candidate (FPTP) and one for a party list. List seats "
            "are allocated to compensate for disproportionality in constituency "
            "results, achieving overall proportionality."
        ),
        "ballot_type": "dual_vote",
        "district_type": "mixed",
        "example_countries": ["DE", "NZ", "BO", "LS"],
    },
    "parallel": {
        "description": (
            "Parallel / Mixed-Member Majoritarian: similar to MMP in ballot "
            "structure (two votes for constituency + list), but list seats are "
            "allocated independently without compensating for constituency "
            "disproportionality. Produces more disproportional results than MMP."
        ),
        "ballot_type": "dual_vote",
        "district_type": "mixed",
        "example_countries": ["JP", "KR", "RU", "TH", "LT"],
    },
    "single_transferable_vote": {
        "description": (
            "Single Transferable Vote (STV): voters rank candidates in order of "
            "preference in multi-member districts. A candidate reaching the quota "
            "is elected; surplus votes are transferred to remaining preferences. "
            "Achieves high proportionality while preserving voter choice of "
            "individual candidates."
        ),
        "ballot_type": "ranked_choice",
        "district_type": "multi_member",
        "example_countries": ["IE", "MT", "AU"],
    },
    "instant_runoff": {
        "description": (
            "Instant-Runoff Voting (IRV / ranked-choice): voters rank candidates "
            "in order of preference in single-member districts. The candidate with "
            "fewest first-preference votes is eliminated and their votes are "
            "transferred to the next preference until one candidate has a majority."
        ),
        "ballot_type": "ranked_choice",
        "district_type": "single_member",
        "example_countries": ["AU", "PG", "FJ"],
    },
    "electoral_college": {
        "description": (
            "Indirect election via an electoral college: voters cast ballots for "
            "electors pledged to a candidate. Electors then formally elect the "
            "office-holder. Distinct from direct popular election; the apportionment "
            "of electors may overweight smaller administrative units."
        ),
        "ballot_type": "indirect",
        "district_type": "state_or_province",
        "example_countries": ["US", "IN", "PK", "DE"],
    },
    "majority_bonus": {
        "description": (
            "Majority-Bonus / Reinforced Proportionality: a proportional system "
            "where the largest party or coalition receives additional bonus seats "
            "to ensure a governing majority. Balances proportionality with "
            "governability."
        ),
        "ballot_type": "party_list",
        "district_type": "multi_member",
        "example_countries": ["GR", "IT", "SM", "AM"],
    },
}

# ── Per-country election profiles ───────────────────────────────────────────

ELECTION_DATA: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "system_for_lower_house": "fptp",
        "system_for_upper_house": "fptp",
        "presidential_system": "electoral_college",
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 2, "upper_house": 6, "president": 4},
        "term_limits": {"president": 2},
        "election_day": "Tuesday after first Monday in November (even-numbered years)",
        "registered_voters_approx": 161_000_000,
        "voter_id_required": "varies_by_state",
        "early_voting": True,
        "mail_voting": True,
        "notes": "Federal framework; states administer elections with wide variation in rules.",
    },
    "GB": {
        "name": "United Kingdom",
        "system_for_lower_house": "fptp",
        "system_for_upper_house": "appointed",
        "presidential_system": None,
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 5},
        "term_limits": {},
        "election_day": "Thursday; Prime Minister advises monarch to dissolve Parliament",
        "registered_voters_approx": 47_600_000,
        "voter_id_required": True,
        "early_voting": False,
        "mail_voting": True,
        "notes": "Maximum 5-year term; early election requires two-thirds Commons vote or no-confidence. Photo ID required in polling stations since 2023.",
    },
    "DE": {
        "name": "Germany",
        "system_for_lower_house": "mixed_member_proportional",
        "system_for_upper_house": "appointed",
        "presidential_system": "electoral_college",
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 4, "president": 5},
        "term_limits": {"president": 2},
        "election_day": "Sunday; determined by the Federal President on government recommendation",
        "registered_voters_approx": 61_200_000,
        "voter_id_required": True,
        "early_voting": True,
        "mail_voting": True,
        "notes": "Bundestag has MMP; Bundespräsident elected by Federal Convention (Bundesversammlung). 5% threshold for party list seats.",
    },
    "FR": {
        "name": "France",
        "system_for_lower_house": "runoff",
        "system_for_upper_house": "electoral_college",
        "presidential_system": "runoff",
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 5, "upper_house": 6, "president": 5},
        "term_limits": {"president": 2},
        "election_day": "Sunday; two rounds one week apart",
        "registered_voters_approx": 48_700_000,
        "voter_id_required": True,
        "early_voting": False,
        "mail_voting": False,
        "notes": "National Assembly: 577 single-member constituencies, two-round runoff. Senate elected indirectly by ~162,000 grand electors (local officials).",
    },
    "JP": {
        "name": "Japan",
        "system_for_lower_house": "parallel",
        "system_for_upper_house": "parallel",
        "presidential_system": None,
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 4, "upper_house": 6},
        "term_limits": {},
        "election_day": "Sunday; House of Representatives dissolved by Emperor on Cabinet advice",
        "registered_voters_approx": 105_300_000,
        "voter_id_required": False,
        "early_voting": True,
        "mail_voting": True,
        "notes": "Lower House (Shugiin): 289 FPTP seats + 176 PR seats. Upper House (Sangiin): half elected every 3 years. Voting age lowered from 20 to 18 in 2016.",
    },
    "CA": {
        "name": "Canada",
        "system_for_lower_house": "fptp",
        "system_for_upper_house": "appointed",
        "presidential_system": None,
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 4},
        "term_limits": {},
        "election_day": "Monday; third Monday in October every 4 years (fixed-date, but confidence convention allows earlier)",
        "registered_voters_approx": 27_400_000,
        "voter_id_required": True,
        "early_voting": True,
        "mail_voting": True,
        "notes": "Westminster-style FPTP; Elections Canada is independent, non-partisan agency.",
    },
    "AU": {
        "name": "Australia",
        "system_for_lower_house": "instant_runoff",
        "system_for_upper_house": "single_transferable_vote",
        "presidential_system": None,
        "voting_age": 18,
        "compulsory_voting": True,
        "term_length_years": {"lower_house": 3, "upper_house": 6},
        "term_limits": {},
        "election_day": "Saturday; writs issued by Governor-General",
        "registered_voters_approx": 17_400_000,
        "voter_id_required": False,
        "early_voting": True,
        "mail_voting": True,
        "notes": "Compulsory enrollment and voting (unenforced penalty ~A$20). AEC (Australian Electoral Commission) administers independently.",
    },
    "IN": {
        "name": "India",
        "system_for_lower_house": "fptp",
        "system_for_upper_house": "electoral_college",
        "presidential_system": "electoral_college",
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 5, "upper_house": 6, "president": 5},
        "term_limits": {},
        "election_day": "Multi-phase; schedule set by Election Commission, spread over several weeks",
        "registered_voters_approx": 968_000_000,
        "voter_id_required": True,
        "early_voting": False,
        "mail_voting": True,
        "notes": "World's largest democracy. Lok Sabha (lower house): 543 FPTP constituencies. Rajya Sabha (upper house): elected by state assemblies. President elected by electoral college of MPs and MLAs.",
    },
    "BR": {
        "name": "Brazil",
        "system_for_lower_house": "proportional_representation_list",
        "system_for_upper_house": "fptp",
        "presidential_system": "runoff",
        "voting_age": 16,
        "compulsory_voting": True,
        "term_length_years": {"lower_house": 4, "upper_house": 8, "president": 4},
        "term_limits": {"president": 2},
        "election_day": "Sunday in October; two rounds if needed",
        "registered_voters_approx": 156_000_000,
        "voter_id_required": True,
        "early_voting": False,
        "mail_voting": False,
        "notes": "Compulsory voting for ages 18-70; optional for 16-17 and 70+. Electronic voting machines used nationwide since 2000.",
    },
    "ZA": {
        "name": "South Africa",
        "system_for_lower_house": "proportional_representation_list",
        "system_for_upper_house": "proportional_representation_list",
        "presidential_system": "electoral_college",
        "voting_age": 18,
        "compulsory_voting": False,
        "term_length_years": {"lower_house": 5, "president": 5},
        "term_limits": {"president": 2},
        "election_day": "Determined by President; within 90 days of Parliament expiry",
        "registered_voters_approx": 27_800_000,
        "voter_id_required": True,
        "early_voting": False,
        "mail_voting": True,
        "notes": "National Assembly: closed-list PR. President elected by National Assembly from among its members. IEC (Independent Electoral Commission) administers.",
    },
}

# ── Electoral bodies ────────────────────────────────────────────────────────

ELECTORAL_BODIES: dict[str, dict[str, Any]] = {
    "US": {
        "name": "Federal Election Commission (FEC)",
        "country": "United States",
        "portal_url": "https://www.fec.gov/",
        "independence": "independent_regulatory",
        "composition_members": 6,
        "appointment": "nominated by President, confirmed by Senate; no more than 3 from one party",
        "role": "enforce campaign finance law; disclose campaign finance data; administer public funding of presidential elections",
        "notes": "States and counties run actual elections; FEC oversees federal campaign finance. No single national election commission.",
    },
    "GB": {
        "name": "Electoral Commission",
        "country": "United Kingdom",
        "portal_url": "https://www.electoralcommission.org.uk/",
        "independence": "independent_statutory",
        "composition_members": 10,
        "appointment": "nominated by political parties represented in House of Commons; confirmed by Speaker's Committee",
        "role": "register political parties; regulate party and election finance; set standards for electoral registration and polling; report on elections",
        "notes": "Created in 2001. Oversees UK parliamentary elections and referendums but not local elections in Scotland or Northern Ireland.",
    },
    "DE": {
        "name": "Bundeswahlleiter (Federal Returning Officer)",
        "country": "Germany",
        "portal_url": "https://www.bundeswahlleiterin.de/",
        "independence": "independent_administrative",
        "composition_members": 1,
        "appointment": "appointed by Federal Ministry of the Interior",
        "role": "conduct Bundestag and European Parliament elections in Germany; determine official results; publish voter turnout and seat allocation",
        "notes": "Supported by state and municipal election offices. Bundeswahlausschuss (Federal Electoral Committee) validates results.",
    },
    "FR": {
        "name": "Conseil Constitutionnel (Constitutional Council)",
        "country": "France",
        "portal_url": "https://www.conseil-constitutionnel.fr/",
        "independence": "constitutional_court",
        "composition_members": 9,
        "appointment": "3 by President of Republic, 3 by President of National Assembly, 3 by President of Senate; former Presidents are ex-officio members",
        "role": "validate presidential election results; rule on electoral disputes for parliamentary elections; oversee referendums",
        "notes": "Interior Ministry administers voting operations; Constitutional Council adjudicates disputes and certifies results.",
    },
    "JP": {
        "name": "Central Election Management Council (Sosenkyo Kanri Iinkai)",
        "country": "Japan",
        "portal_url": "https://www.soumu.go.jp/senkyo/",
        "independence": "administrative_commission",
        "composition_members": 5,
        "appointment": "appointed by Prime Minister with Diet consent",
        "role": "oversee national elections; administer proportional-representation list; coordinate prefectural and municipal election commissions",
        "notes": "Each prefecture and municipality has its own election management commission. Ministry of Internal Affairs and Communications provides overall coordination.",
    },
    "IN": {
        "name": "Election Commission of India (ECI)",
        "country": "India",
        "portal_url": "https://www.eci.gov.in/",
        "independence": "constitutional_body",
        "composition_members": 3,
        "appointment": "Chief Election Commissioner and Election Commissioners appointed by President on advice of Prime Minister",
        "role": "superintendence, direction, and control of elections to Parliament, state legislatures, and offices of President and Vice President",
        "notes": "Constitutional authority under Article 324. Operates through Chief Electoral Officers in each state. Largest election management body in the world.",
    },
    "AU": {
        "name": "Australian Electoral Commission (AEC)",
        "country": "Australia",
        "portal_url": "https://www.aec.gov.au/",
        "independence": "independent_statutory",
        "composition_members": 3,
        "appointment": "Electoral Commissioner appointed by Governor-General; also includes the Australian Statistician and a non-judicial member",
        "role": "maintain electoral roll; conduct federal elections and referendums; enforce compulsory enrollment and voting; administer party registration and funding disclosure",
        "notes": "Highly regarded for independence. Redistribution committees determine electoral boundaries.",
    },
    "BR": {
        "name": "Tribunal Superior Eleitoral (TSE)",
        "country": "Brazil",
        "portal_url": "https://www.tse.jus.br/",
        "independence": "specialized_judiciary",
        "composition_members": 7,
        "appointment": "3 justices from Supreme Federal Court, 2 from Superior Court of Justice, 2 lawyers appointed by President",
        "role": "administer elections; regulate campaign finance; adjudicate electoral disputes; certify election results",
        "notes": "Regional Electoral Courts (TREs) in each state. Brazil pioneered electronic voting machines (urna eletrônica) since 1996.",
    },
    "ZA": {
        "name": "Independent Electoral Commission of South Africa (IEC)",
        "country": "South Africa",
        "portal_url": "https://www.elections.org.za/",
        "independence": "constitutional_body",
        "composition_members": 5,
        "appointment": "appointed by President on recommendation of National Assembly after public nomination process",
        "role": "manage elections at all levels; compile and maintain voters' roll; promote voter education; declare election results",
        "notes": "Constitution (Chapter 9 institution). Considered a model for independent electoral administration in Africa.",
    },
}

# ── Polling procedures ──────────────────────────────────────────────────────

POLLING_PROCEDURES: dict[str, dict[str, Any]] = {
    "US": {
        "polling_station_type": "decentralized_varied",
        "identification_required": "varies_by_state",
        "id_types_accepted": [
            "state-issued driver's license or ID card",
            "US passport or passport card",
            "military ID",
            "tribal ID",
        ],
        "assistance_provisions": [
            "assistance for blind, disabled, or illiterate voters",
            "language assistance under Voting Rights Act Section 203",
            "curbside voting in some jurisdictions",
        ],
        "opening_hours": "varies by state; typically 07:00 to 19:00 or 20:00",
        "counting_method": "machine_count_with_paper_trail",
        "observers": "party poll watchers and nonpartisan observers (only in some states)",
        "notes": "Each state sets its own rules within federal statutory and constitutional bounds. HAVA 2002 sets minimum standards.",
    },
    "GB": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "UK photocard driving licence",
            "Passport (UK, Commonwealth, EEA)",
            "Voter Authority Certificate (free photo ID)",
            "Blue Badge",
            "Biometric immigration document",
        ],
        "assistance_provisions": [
            "tactile voting device for blind or partially-sighted voters",
            "large-print ballot display",
            "assistance from companion or polling station staff",
        ],
        "opening_hours": "07:00 to 22:00",
        "counting_method": "manual_count",
        "observers": "accredited electoral observers (Electoral Commission)",
        "notes": "Photo ID requirement introduced by Elections Act 2022, first applied at 2023 local elections. Manual counting at centralized count venues.",
    },
    "DE": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "Personalausweis (national ID card)",
            "Reisepass (German passport)",
        ],
        "assistance_provisions": [
            "assistance at polling station on request",
            "ballot templates for blind voters",
            "accessible polling stations required by law",
        ],
        "opening_hours": "08:00 to 18:00",
        "counting_method": "manual_count_public",
        "observers": "any citizen may observe counting (public by law)",
        "notes": "Voting machines trialled then rejected by Constitutional Court (2009). Manual paper ballots with public counting are the standard.",
    },
    "FR": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "Carte nationale d'identite (national ID)",
            "Passeport (passport)",
            "Carte Vitale with photo",
            "Permis de conduire (driving licence, since 2015)",
        ],
        "assistance_provisions": [
            "assistance for disabled voters by an elector of their choice",
            "signature guide for visually impaired",
            "accessible polling stations required by law",
        ],
        "opening_hours": "08:00 to 18:00 (extended to 20:00 in large cities)",
        "counting_method": "manual_count_public",
        "observers": "designated party scrutineers; any voter may observe",
        "notes": "Proxy voting (procuration) widely available. No postal voting for general elections. Manual paper ballots (bulletin de vote).",
    },
    "IN": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "Voter ID card (EPIC -- Electors Photo Identity Card)",
            "Aadhaar card",
            "Passport",
            "Driving licence",
            "MNREGA job card",
            "PAN card",
        ],
        "assistance_provisions": [
            "Braille-enabled EVM ballot units",
            "companion assistance for blind or disabled voters",
            "postal ballot for voters 85+ and persons with disabilities",
        ],
        "opening_hours": "07:00 to 18:00 (varies by state)",
        "counting_method": "electronic_voting_machines_with_vvpat",
        "observers": "ECI-appointed General Observers, Expenditure Observers, and Police Observers for every constituency",
        "notes": "Largest election logistics operation globally. EVMs with Voter-Verifiable Paper Audit Trail (VVPAT) used nationwide. Booth-level officers appointed by ECI.",
    },
    "BR": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "Titulo de Eleitor (voter registration card)",
            "e-Titulo (digital voter ID app, with photo)",
            "Official photo ID (RG, CNH, passport)",
        ],
        "assistance_provisions": [
            "audio guidance on electronic voting machines",
            "Braille keypad overlay",
            "assistant for voters with disabilities",
        ],
        "opening_hours": "08:00 to 17:00",
        "counting_method": "electronic_voting_machines",
        "observers": "party representatives, OAS observers, and NGO observers (invited by TSE)",
        "notes": "Universal electronic voting machines (urna eletrônica) used since 2000. Results transmitted via secure satellite network. No paper ballots.",
    },
    "AU": {
        "polling_station_type": "centralized_standard",
        "identification_required": False,
        "id_types_accepted": ["voter states name and address; enrolled address checked"],
        "assistance_provisions": [
            "assistance in marking ballot if visually impaired, illiterate, or physically disabled",
            "AEC mobile polling teams for remote communities and aged care",
        ],
        "opening_hours": "08:00 to 18:00",
        "counting_method": "manual_count_distribution_of_preferences",
        "observers": "scrutineers appointed by candidates",
        "notes": "Compulsory voting; AEC may ask name, address, and whether voter has voted already but cannot demand ID. No-vote fine is nominal (~A$20).",
    },
    "ZA": {
        "polling_station_type": "centralized_standard",
        "identification_required": True,
        "id_types_accepted": [
            "Green barcoded ID book",
            "Smart ID card",
            "Temporary Identity Certificate",
        ],
        "assistance_provisions": [
            "assistance for physically disabled or visually impaired voters",
            "Braille ballot templates",
            "mobile voting stations for remote areas",
        ],
        "opening_hours": "07:00 to 21:00",
        "counting_method": "manual_count",
        "observers": "party agents, domestic observer groups, and international observer missions",
        "notes": "IEC manages comprehensive voter education campaigns. Results displayed at each voting station before central collation.",
    },
}

# ── Accessor functions ─────────────────────────────────────────────────────


def _norm_country(country: str) -> str:
    """Normalize a country code to ISO-3166-1 alpha-2 upper-case."""
    return country.strip().upper()


def get_election_info(country: str) -> dict[str, Any] | None:
    """Return the election profile for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict with election systems, voting age, term lengths, and other
        electoral profile data. Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    data = ELECTION_DATA.get(code)
    if data is None:
        return None
    result: dict[str, Any] = dict(data)
    result["country"] = code
    return result


def list_election_systems() -> list[str]:
    """Return the sorted list of known election system types."""
    return sorted(ELECTION_SYSTEMS.keys())


def get_electoral_body(country: str) -> dict[str, Any] | None:
    """Return the electoral management body for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict with the electoral body's name, independence, composition,
        and role. Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    body = ELECTORAL_BODIES.get(code)
    if body is None:
        return None
    return dict(body)


def get_polling_procedures(country: str) -> dict[str, Any] | None:
    """Return polling-day procedures for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict describing polling station operations, ID requirements,
        assistance provisions, hours, and counting method for national
        elections. Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    proc = POLLING_PROCEDURES.get(code)
    if proc is None:
        return None
    return dict(proc)


def get_voter_eligibility(country: str) -> dict[str, Any] | None:
    """Return voter eligibility criteria for a country.

    Args:
        country: ISO-3166-1 alpha-2 code (case-insensitive).

    Returns:
        A dict with voting_age, compulsory, and residency requirements.
        Returns ``None`` if the country is unknown.
    """
    code = _norm_country(country)
    data = ELECTION_DATA.get(code)
    if data is None:
        return None
    return {
        "country": code,
        "voting_age": data["voting_age"],
        "compulsory_voting": data["compulsory_voting"],
        "name": data["name"],
    }


def list_countries_with_elections() -> list[str]:
    """Return the sorted list of ISO-3166-1 alpha-2 codes covered by ELECTION_DATA."""
    return sorted(ELECTION_DATA.keys())


COUNTRY_ELECTIONS = ELECTION_DATA


def lookup_elections(country: str) -> dict[str, Any] | None:
    code = _norm_country(country)
    data = ELECTION_DATA.get(code)
    if data is None:
        return None
    result: dict[str, Any] = dict(data)
    result["found"] = True
    result["country"] = code
    return result


def get_voting_method(method_name: str) -> dict[str, Any] | None:
    q = method_name.strip().lower().replace(" ", "_")
    if q in ("paper_ballot", "paper"):
        return {"method": "paper_ballot", "description": "Traditional paper ballot marked by hand and counted manually or by optical scanner.", "voting_machine_support": "optional"}
    return None
