"""Governing bodies knowledge module for the governance collection.

Exposes international and supranational governing bodies as a navigable
graph with parent/child relationships, jurisdiction scopes, decision
mechanisms, and national government structures.

Public surface::

    BODY_TYPES              tuple of 7 body-type tokens
    INTERNATIONAL_BODIES    list of body dicts (the knowledge base)
    NATIONAL_STRUCTURES     national-government template (branches, ministries, agencies)
    BODY_RELATIONSHIPS      list of {parent, child, kind, note} edges

    lookup_body(id_or_name_or_alias) -> body dict | None
    get_children(body_id)             -> list[body dict]
    get_descendants(body_id)          -> list[body dict]   (transitive)
    get_jurisdiction(body_id)         -> dict with scope/legal_basis/headquarters
    get_decision_process(body_id)     -> dict with mechanism/process
    bodies_by_type(type)              -> list[body dict]
    relationship(a, b)                -> kind str | None
    relationship_detail(a, b)         -> dict | None
    all_relationships_for(body_id)    -> list[relationship dict]
    national_branches()               -> ("executive","legislative","judicial")
    national_ministries()             -> tuple of ministry tokens
"""

from __future__ import annotations

from typing import Any, cast

BODY_TYPES: tuple[str, ...] = (
    "international",
    "supranational",
    "national",
    "state_provincial",
    "municipal",
    "tribal",
    "special_district",
)


def _body(
    body_id: str,
    name: str,
    aliases: tuple[str, ...],
    body_type: str,
    members: int,
    structure: tuple[str, ...],
    children: tuple[str, ...],
    decision_process: str,
    decision_mechanism: str,
    jurisdiction_scope: str,
    legal_basis: str,
    headquarters: str = "",
) -> dict[str, Any]:
    return {
        "id": body_id,
        "name": name,
        "aliases": aliases,
        "type": body_type,
        "members": members,
        "structure": structure,
        "children": children,
        "decision_process": decision_process,
        "decision_mechanism": decision_mechanism,
        "jurisdiction_scope": jurisdiction_scope,
        "legal_basis": legal_basis,
        "headquarters": headquarters,
    }


INTERNATIONAL_BODIES: list[dict[str, Any]] = [
    _body(
        "un",
        "United Nations",
        ("UN", "United Nations Organization"),
        "international",
        193,
        (
            "General Assembly",
            "Security Council",
            "Economic and Social Council",
            "Trusteeship Council",
            "International Court of Justice",
            "Secretariat",
        ),
        (
            "un_ga", "un_sc", "un_ecosoc", "un_tc", "icj", "un_secretariat",
            "who", "world_bank", "imf", "unicef", "unhcr", "fao", "ilo",
            "unesco", "unep", "unido", "wto", "iaea", "icao", "imo",
        ),
        "Member states vote in assemblies; Security Council has binding authority.",
        "qualified_majority_with_veto",
        "global",
        "UN Charter (1945)",
        "New York, NY, USA",
    ),
    _body(
        "un_ga",
        "UN General Assembly",
        ("General Assembly", "UNGA"),
        "international",
        193,
        ("Plenary", "Main Committees"),
        ("un_hrc",),
        "One member, one vote; resolutions by simple or two-thirds majority.",
        "one_member_one_vote",
        "global",
        "UN Charter Article 9-22",
        "New York, NY, USA",
    ),
    _body(
        "un_sc",
        "UN Security Council",
        ("Security Council", "UNSC"),
        "international",
        15,
        ("Permanent Members (P5)", "Non-permanent Members (E10)"),
        (),
        "9 of 15 affirmative votes including the concurring votes of all P5; veto by any P5.",
        "qualified_majority_with_veto",
        "global",
        "UN Charter Article 23-32",
        "New York, NY, USA",
    ),
    _body(
        "un_hrc",
        "UN Human Rights Council",
        ("UNHRC", "HRC"),
        "international",
        47,
        ("Plenary", "Universal Periodic Review"),
        (),
        "Members elected by General Assembly; decisions by majority vote.",
        "simple_majority",
        "global",
        "GA Resolution 60/251 (2006)",
        "Geneva, Switzerland",
    ),
    _body(
        "un_ecosoc",
        "United Nations Economic and Social Council",
        ("ECOSOC", "Economic and Social Council"),
        "international",
        54,
        (
            "High-level Segment",
            "Coordination Segment",
            "Humanitarian Affairs Segment",
            "Management Segment",
        ),
        (),
        "Each member has one vote; decisions are taken by a majority of members present and voting.",
        "simple_majority",
        "global",
        "UN Charter Articles 61-72",
        "New York, NY, USA",
    ),
    _body(
        "un_tc",
        "United Nations Trusteeship Council",
        ("Trusteeship Council", "TC"),
        "international",
        5,
        ("Council",),
        (),
        "Operations are suspended; the Council meets when required by its President or members.",
        "simple_majority",
        "global",
        "UN Charter Articles 86-91",
        "New York, NY, USA",
    ),
    _body(
        "un_secretariat",
        "United Nations Secretariat",
        ("UN Secretariat", "Secretariat"),
        "international",
        0,
        (
            "Secretary-General",
            "Departments and Offices",
            "Duty Stations",
        ),
        (),
        "The Secretary-General directs the international civil service under mandates of UN organs.",
        "administrative",
        "global",
        "UN Charter Articles 97-101",
        "New York, NY, USA",
    ),
    _body(
        "eu",
        "European Union",
        ("EU", "European Union"),
        "supranational",
        27,
        (
            "European Council",
            "Council of the EU",
            "European Parliament",
            "European Commission",
            "Court of Justice of the EU",
        ),
        ("eu_parliament", "eu_commission", "eu_council", "ecj"),
        "Member-state ministers in Council co-legislate with Parliament; Commission proposes.",
        "qualified_majority",
        "regional",
        "Treaty on European Union (Maastricht, 1992) + Treaty on the Functioning of the EU",
        "Brussels, Belgium",
    ),
    _body(
        "eu_parliament",
        "European Parliament",
        ("EP", "Europarl"),
        "supranational",
        720,
        ("Plenary", "Committees"),
        (),
        "Directly elected; co-decision with Council under ordinary legislative procedure.",
        "simple_majority",
        "regional",
        "TEU Article 14; TFEU Article 294",
        "Strasbourg/Brussels",
    ),
    _body(
        "au",
        "African Union",
        ("AU",),
        "international",
        55,
        (
            "Assembly of the Union",
            "Executive Council",
            "Pan-African Parliament",
            "African Court on Human and Peoples' Rights",
            "Peace and Security Council",
        ),
        (),
        "Assembly decisions by consensus or two-thirds majority.",
        "qualified_majority",
        "regional",
        "Constitutive Act of the African Union (2000)",
        "Addis Ababa, Ethiopia",
    ),
    _body(
        "asean",
        "Association of Southeast Asian Nations",
        ("ASEAN",),
        "international",
        10,
        (
            "ASEAN Summit",
            "Coordinating Council",
            "ASEAN Secretariat",
        ),
        (),
        "Decisions by consultation and consensus (the 'ASEAN way').",
        "consensus",
        "regional",
        "Bangkok Declaration (1967); ASEAN Charter (2007)",
        "Jakarta, Indonesia",
    ),
    _body(
        "nato",
        "North Atlantic Treaty Organization",
        ("NATO", "North Atlantic Alliance"),
        "international",
        32,
        (
            "North Atlantic Council",
            "Military Committee",
            "NATO Headquarters",
        ),
        (),
        "Decisions by consensus of all member states; no formal voting.",
        "consensus",
        "regional",
        "North Atlantic Treaty (Washington Treaty, 1949)",
        "Brussels, Belgium",
    ),
    _body(
        "wto",
        "World Trade Organization",
        ("WTO",),
        "international",
        164,
        (
            "Ministerial Conference",
            "General Council",
            "Dispute Settlement Body",
            "Trade Policy Review Body",
        ),
        (),
        "Member states negotiate; decisions by consensus, else by majority of votes cast.",
        "consensus_or_majority",
        "global",
        "Marrakesh Agreement (1994)",
        "Geneva, Switzerland",
    ),
    _body(
        "who",
        "World Health Organization",
        ("WHO",),
        "international",
        194,
        (
            "World Health Assembly",
            "Executive Board",
            "Secretariat",
        ),
        (),
        "Decisions by majority vote at the World Health Assembly.",
        "simple_majority",
        "global",
        "Constitution of the WHO (1948)",
        "Geneva, Switzerland",
    ),
    _body(
        "eu_commission",
        "European Commission",
        ("EC", "Commission"),
        "supranational",
        27,
        ("College of Commissioners", "Directorates-General"),
        (),
        "Commissioners appointed by member states; Parliament confirms; legislative initiative.",
        "qualified_majority",
        "regional",
        "TEU Article 17; TFEU Articles 244-250",
        "Brussels, Belgium",
    ),
    _body(
        "eu_council",
        "Council of the European Union",
        ("Council of the EU", "Council of Ministers"),
        "supranational",
        27,
        ("Rotating Presidency", "Council configurations"),
        (),
        "Member-state ministers in relevant configuration; qualified majority voting or unanimity.",
        "qualified_majority",
        "regional",
        "TEU Article 16; TFEU Articles 237-243",
        "Brussels, Belgium",
    ),
    _body(
        "ecj",
        "Court of Justice of the European Union",
        ("CJEU", "ECJ", "European Court of Justice"),
        "supranational",
        27,
        ("Court of Justice", "General Court", "Civil Service Tribunal"),
        (),
        "Judges issue binding rulings on EU law interpretation and enforcement.",
        "judicial",
        "regional",
        "TEU Article 19; TFEU Articles 251-281",
        "Luxembourg City, Luxembourg",
    ),
    _body(
        "world_bank",
        "World Bank Group",
        ("World Bank", "IBRD"),
        "international",
        189,
        (
            "Board of Governors",
            "Board of Executive Directors",
        ),
        (),
        "Decisions by weighted voting based on capital subscriptions.",
        "weighted_voting",
        "global",
        "Articles of Agreement of the IBRD (1944)",
        "Washington, DC, USA",
    ),
    _body(
        "imf",
        "International Monetary Fund",
        ("IMF", "Fund"),
        "international",
        190,
        (
            "Board of Governors",
            "Executive Board",
        ),
        (),
        "Decisions by weighted voting (quotas); special majorities for key actions.",
        "weighted_voting",
        "global",
        "Articles of Agreement of the IMF (1944)",
        "Washington, DC, USA",
    ),
    _body(
        "icc",
        "International Criminal Court",
        ("ICC",),
        "international",
        124,
        (
            "Presidency",
            "Judicial Divisions",
            "Office of the Prosecutor",
            "Registry",
        ),
        (),
        "Judges issue binding rulings under the Rome Statute.",
        "judicial",
        "global",
        "Rome Statute of the International Criminal Court (1998)",
        "The Hague, Netherlands",
    ),
    _body(
        "icj",
        "International Court of Justice",
        ("ICJ", "World Court"),
        "international",
        193,
        (
            "Bench of 15 Judges",
            "Chambers",
            "Registrar",
        ),
        (),
        "Judges issue binding judgments on contentious cases and advisory opinions.",
        "judicial",
        "global",
        "Statute of the International Court of Justice (UN Charter, 1945)",
        "The Hague, Netherlands",
    ),
]


NATIONAL_STRUCTURES: dict[str, Any] = {
    "branches": ("executive", "legislative", "judicial"),
    "legislative": {
        "structure_types": ("unicameral", "bicameral"),
        "description": (
            "National law-making body; unicameral (one chamber) or "
            "bicameral (two chambers: typically a lower house and an upper house)."
        ),
    },
    "executive": {
        "structure_types": ("presidential", "parliamentary", "monarchic"),
        "description": (
            "Head of state and head of government; may be combined (presidential) "
            "or separated (parliamentary). Implements and enforces the law."
        ),
    },
    "judicial": {
        "structure_types": ("common_law", "civil_law", "mixed", "religious"),
        "description": (
            "Constitutional and supreme courts that interpret the law and "
            "adjudicate disputes; may include lower appellate and trial courts."
        ),
    },
    "ministries": (
        "finance_treasury",
        "defense",
        "foreign_affairs",
        "interior_homeland",
        "justice_attorney_general",
        "health",
        "education",
        "transport",
        "energy",
        "environment",
        "labor_employment",
        "commerce_trade",
        "agriculture",
        "housing_urban_development",
        "digital_telecommunications",
    ),
    "agencies": (
        "central_bank",
        "regulatory_bodies",
        "tax_authority",
        "audit_office",
        "intelligence_services",
        "civil_service_commission",
        "statistics_bureau",
        "electoral_commission",
    ),
    "examples": (
        "presidential_federal",
        "parliamentary_unitary",
        "parliamentary_federal",
        "semi_presidential",
    ),
}


BODY_RELATIONSHIPS: list[dict[str, str]] = [
    {"parent": "un", "child": "who", "kind": "parent_child",
     "note": "WHO is a UN specialized agency reporting to the General Assembly."},
    {"parent": "un", "child": "world_bank", "kind": "parent_child",
     "note": "World Bank formally entered into relationship with the UN via ECOSOC."},
    {"parent": "un", "child": "imf", "kind": "parent_child",
     "note": "IMF formally entered into relationship with the UN via ECOSOC."},
    {"parent": "un", "child": "un_ga", "kind": "parent_child",
     "note": "General Assembly is a principal organ of the UN."},
    {"parent": "un", "child": "un_sc", "kind": "parent_child",
     "note": "Security Council is a principal organ of the UN."},
    {"parent": "un", "child": "icj", "kind": "parent_child",
     "note": "ICJ is the principal judicial organ of the UN."},
    {"parent": "un", "child": "un_ecosoc", "kind": "parent_child",
     "note": "ECOSOC is a principal organ coordinating the UN system's economic and social work."},
    {"parent": "un", "child": "un_tc", "kind": "parent_child",
     "note": "The Trusteeship Council is a principal organ whose operations are suspended."},
    {"parent": "un", "child": "un_secretariat", "kind": "parent_child",
     "note": "The Secretariat is the UN's principal administrative organ."},
    {"parent": "un_ga", "child": "un_hrc", "kind": "parent_child",
     "note": "HRC is a subsidiary organ of the General Assembly."},
    {"parent": "eu", "child": "eu_parliament", "kind": "parent_child",
     "note": "European Parliament is an EU institution."},
    {"parent": "eu", "child": "eu_commission", "kind": "parent_child",
     "note": "European Commission is the EU executive body."},
    {"parent": "eu", "child": "eu_council", "kind": "parent_child",
     "note": "Council of the EU co-legislates with Parliament under the ordinary legislative procedure."},
    {"parent": "eu", "child": "ecj", "kind": "parent_child",
     "note": "CJEU is the judicial institution of the EU, interpreting and enforcing EU law."},
    {
        "parent": "imf",
        "child": "world_bank",
        "kind": "overlapping_jurisdiction",
        "note": "Bretton Woods twins share fiscal and monetary oversight with distinct mandates.",
    },
    {
        "parent": "world_bank",
        "child": "imf",
        "kind": "overlapping_jurisdiction",
        "note": "Bretton Woods twins share fiscal and monetary oversight with distinct mandates.",
    },
    {
        "parent": "icc",
        "child": "icj",
        "kind": "overlapping_jurisdiction",
        "note": "Both are Hague courts; ICC prosecutes people while ICJ adjudicates state disputes.",
    },
    {
        "parent": "icj",
        "child": "icc",
        "kind": "overlapping_jurisdiction",
        "note": "Both are Hague courts; ICC prosecutes people while ICJ adjudicates state disputes.",
    },
    {"parent": "un_sc", "child": "icc", "kind": "regulatory",
     "note": "Security Council can refer situations to the ICC under Rome Statute Article 13(b)."},
]


def _index() -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for body in INTERNATIONAL_BODIES:
        idx[body["id"]] = body
    return idx


_INDEX = _index()


def lookup_body(query: str) -> dict[str, Any] | None:
    """Look up a body by id, name, or alias (case-insensitive).

    Returns the matching body dict or None for empty/unknown queries.
    """
    if not query or not query.strip():
        return None
    q = query.strip().lower()
    for body in INTERNATIONAL_BODIES:
        if body["id"].lower() == q:
            return body
        if body["name"].lower() == q:
            return body
        for alias in body["aliases"]:
            if alias.lower() == q:
                return body
    return None


def get_children(body_id: str) -> list[dict[str, Any]]:
    """Return the direct child bodies of ``body_id`` as a list of body dicts."""
    parent = _INDEX.get(body_id)
    if parent is None:
        return []
    children = []
    for child_id in parent["children"]:
        child = _INDEX.get(child_id)
        if child is not None:
            children.append(child)
    return children


def get_descendants(body_id: str) -> list[dict[str, Any]]:
    """Return all transitive descendant bodies of ``body_id`` (BFS)."""
    if body_id not in _INDEX:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    queue: list[str] = [body_id]
    while queue:
        current_id = queue.pop(0)
        current = _INDEX.get(current_id)
        if current is None:
            continue
        for child_id in current["children"]:
            if child_id in seen:
                continue
            child = _INDEX.get(child_id)
            if child is None:
                continue
            seen.add(child_id)
            out.append(child)
            queue.append(child_id)
    return out


def get_jurisdiction(body_id: str) -> dict[str, Any]:
    """Return jurisdiction metadata for a body.

    Includes scope (global/regional/national), legal_basis, and headquarters.
    Returns ``{"error": ...}`` for unknown bodies.
    """
    body = _INDEX.get(body_id)
    if body is None:
        return {"error": f"unknown body: {body_id!r}"}
    return {
        "scope": body["jurisdiction_scope"],
        "legal_basis": body["legal_basis"],
        "headquarters": body.get("headquarters", ""),
    }


def get_decision_process(body_id: str) -> dict[str, Any]:
    """Return the decision-process metadata for a body.

    Includes ``mechanism`` (e.g. consensus, qualified_majority_with_veto,
    weighted_voting, judicial) and ``process`` (human-readable description).
    Returns ``{"error": ...}`` for unknown bodies.
    """
    body = _INDEX.get(body_id)
    if body is None:
        return {"error": f"unknown body: {body_id!r}"}
    return {
        "mechanism": body["decision_mechanism"],
        "process": body["decision_process"],
    }


def bodies_by_type(body_type: str) -> list[dict[str, Any]]:
    """Return all bodies whose ``type`` matches ``body_type``."""
    if body_type not in BODY_TYPES:
        return []
    return [b for b in INTERNATIONAL_BODIES if b["type"] == body_type]


def relationship(a: str, b: str) -> str | None:
    """Return the relationship kind between body ``a`` and body ``b``.

    Returns one of ``parent_child``, ``overlapping_jurisdiction``,
    ``advisory``, ``regulatory``, or None if no direct relationship exists.
    """
    for rel in BODY_RELATIONSHIPS:
        if rel["parent"] == a and rel["child"] == b:
            return rel["kind"]
    return None


def relationship_detail(a: str, b: str) -> dict[str, str] | None:
    """Return the full relationship record between ``a`` and ``b`` if any."""
    for rel in BODY_RELATIONSHIPS:
        if rel["parent"] == a and rel["child"] == b:
            return {"kind": rel["kind"], "note": rel.get("note", "")}
    return None


def all_relationships_for(body_id: str) -> list[dict[str, str]]:
    """Return every relationship record in which ``body_id`` participates."""
    out: list[dict[str, str]] = []
    for rel in BODY_RELATIONSHIPS:
        if rel["parent"] == body_id or rel["child"] == body_id:
            out.append(rel)
    return out


def national_branches() -> tuple[str, ...]:
    """Return the tuple of national-government branch names."""
    return cast(tuple[str, ...], NATIONAL_STRUCTURES["branches"])


def national_ministries() -> tuple[str, ...]:
    """Return the tuple of national-government ministry category tokens."""
    return cast(tuple[str, ...], NATIONAL_STRUCTURES["ministries"])


__all__ = [
    "BODY_RELATIONSHIPS",
    "BODY_TYPES",
    "INTERNATIONAL_BODIES",
    "NATIONAL_STRUCTURES",
    "all_relationships_for",
    "bodies_by_type",
    "get_children",
    "get_decision_process",
    "get_descendants",
    "get_jurisdiction",
    "lookup_body",
    "national_branches",
    "national_ministries",
    "relationship",
    "relationship_detail",
]
