"""Legal systems knowledge module for the governance collection.

Exposes legal system types, court hierarchies by country, appeal processes,
and legal terminology as structured knowledge.

Public surface::

    LEGAL_SYSTEM_TYPES     tuple of legal system type tokens
    COURT_HIERARCHIES      dict[country_code] -> court hierarchy
    APPEAL_PROCESSES       dict[system_type] -> appeal description
    LEGAL_TERMINOLOGY      dict[term] -> definition + category

    get_legal_system(country_code)     -> dict | None
    get_court_hierarchy(country_code)  -> dict | None
    get_appeal_process(system_type)    -> dict | None
    get_term(term_name)                -> dict | None
    terms_by_category(category)        -> list[dict]
    list_countries()                   -> list[str]
    court_at_level(country, level)     -> list[str]
    supreme_court(country)             -> str | None
"""

from __future__ import annotations

from typing import Any

LEGAL_SYSTEM_TYPES: tuple[str, ...] = (
    "common_law",
    "civil_law",
    "customary_law",
    "religious_law",
    "mixed",
)

COURT_HIERARCHIES: dict[str, dict[str, Any]] = {
    "US": {
        "name": "United States",
        "system_type": "common_law",
        "federal": {
            "levels": (
                "supreme_court",
                "courts_of_appeals",
                "district_courts",
            ),
            "supreme_court": "Supreme Court of the United States (SCOTUS)",
            "courts_of_appeals": "13 Circuit Courts of Appeals (11 numbered + DC + Federal)",
            "district_courts": "94 Federal District Courts",
            "specialized": (
                "Tax Court",
                "Court of International Trade",
                "Court of Federal Claims",
                "Bankruptcy Courts",
                "Foreign Intelligence Surveillance Court",
            ),
        },
        "state": {
            "levels": (
                "state_supreme_court",
                "intermediate_appellate",
                "trial_courts",
            ),
            "description": "Each state maintains its own court system with at least a trial court and a supreme court; most have an intermediate appellate layer.",
            "note": "State supreme court rulings on state law are final unless a federal question is presented.",
        },
        "constitutional_review": "judicial_review_by_supreme_court",
        "judge_selection": "presidential_appointment_with_senate_confirmation (federal); varies by state (election, appointment, or merit selection)",
    },
    "GB": {
        "name": "United Kingdom",
        "system_type": "common_law",
        "federal": {
            "levels": (
                "supreme_court",
                "court_of_appeal",
                "high_court",
            ),
            "supreme_court": "Supreme Court of the United Kingdom",
            "court_of_appeal": "Court of Appeal (Civil Division and Criminal Division)",
            "high_court": "High Court (King's Bench, Chancery, Family)",
            "specialized": (
                "Employment Appeal Tribunal",
                "Competition Appeal Tribunal",
                "Tax Tribunals (First-tier and Upper)",
                "Immigration and Asylum Chambers",
            ),
        },
        "state": {
            "levels": (
                "sheriff_court",
                "high_court_of_justiciary",
            ),
            "description": "Scotland maintains a distinct legal system with its own court hierarchy; Northern Ireland similarly has a separate court structure.",
            "note": "The Supreme Court is the final civil appeal for all UK jurisdictions; criminal appeals from Scotland stop at the High Court of Justiciary.",
        },
        "constitutional_review": "parliamentary_sovereignty_no_judicial_strike_down",
        "judge_selection": "Judicial Appointments Commission (independent)",
    },
    "DE": {
        "name": "Germany",
        "system_type": "civil_law",
        "federal": {
            "levels": (
                "federal_constitutional_court",
                "federal_supreme_courts",
                "higher_regional_courts",
            ),
            "supreme_court": "Bundesverfassungsgericht (Federal Constitutional Court)",
            "federal_supreme_courts": (
                "Bundesgerichtshof (BGH, civil/criminal)",
                "Bundesverwaltungsgericht (BVerwG, administrative)",
                "Bundesarbeitsgericht (BAG, labour)",
                "Bundessozialgericht (BSG, social)",
                "Bundesfinanzhof (BFH, tax)",
            ),
            "specialized": (
                "Federal Patent Court",
                "Truppendienstgerichte (military service courts)",
            ),
            "note": "Germany has five federal supreme courts, each for a distinct jurisdiction.",
        },
        "state": {
            "levels": (
                "landesverfassungsgericht",
                "oberlandesgericht",
                "landgericht",
                "amtsgericht",
            ),
            "description": "Each Land (state) has its own constitutional court, higher regional court (Oberlandesgericht), regional court (Landgericht), and local court (Amtsgericht).",
        },
        "constitutional_review": "concrete_and_abstract_review_by_constitutional_court",
        "judge_selection": "Judges elected by parliamentary committees (Richterwahlausschuss) at federal level; Land-level procedures vary",
    },
    "FR": {
        "name": "France",
        "system_type": "civil_law",
        "federal": {
            "levels": (
                "cour_de_cassation",
                "cours_d_appel",
                "tribunaux",
            ),
            "supreme_court": "Cour de cassation (supreme civil/criminal appeal)",
            "administrative": (
                "Conseil d'État (supreme administrative court)",
                "Cours administratives d'appel",
                "Tribunaux administratifs",
            ),
            "constitutional": "Conseil constitutionnel (constitutional review)",
            "specialized": (
                "Tribunal des conflits (jurisdiction disputes)",
                "Cour de justice de la Republique (ministerial misconduct)",
                "Tribunaux de commerce (commercial disputes)",
            ),
            "note": "France has a dual-court system: judicial (ordinary) courts headed by Cour de cassation, and administrative courts headed by Conseil d'État.",
        },
        "state": {
            "levels": (),
            "description": "France is a unitary state; all courts are national. There is no separate state/federal court system.",
        },
        "constitutional_review": "a_priori_and_a_posteriori_review_by_conseil_constitutionnel",
        "judge_selection": "Career judiciary (ENM, Ecole Nationale de la Magistrature); Constitutional Council members appointed by President, Senate President, and National Assembly President",
    },
    "JP": {
        "name": "Japan",
        "system_type": "civil_law",
        "federal": {
            "levels": (
                "supreme_court",
                "high_courts",
                "district_courts",
                "summary_courts",
            ),
            "supreme_court": "Supreme Court (Saiko Saibansho)",
            "high_courts": "8 High Courts (Koto Saibansho) serving regional circuits",
            "district_courts": "50 District Courts (Chiho Saibansho)",
            "specialized": (
                "Family Courts (Katei Saibansho)",
                "Intellectual Property High Court",
            ),
        },
        "state": {
            "levels": (),
            "description": "Japan is a unitary state with a single national court system.",
        },
        "constitutional_review": "judicial_review_by_supreme_court",
        "judge_selection": "Cabinet appoints Supreme Court justices; Emperor attests. Lower court judges appointed by Cabinet from a Supreme Court nominee list.",
    },
    "CA": {
        "name": "Canada",
        "system_type": "common_law",
        "federal": {
            "levels": (
                "supreme_court",
                "federal_court_of_appeal",
                "federal_court",
            ),
            "supreme_court": "Supreme Court of Canada",
            "specialized": (
                "Tax Court of Canada",
                "Court Martial Appeal Court",
            ),
        },
        "state": {
            "levels": (
                "provincial_court_of_appeal",
                "provincial_superior_court",
                "provincial_court",
            ),
            "description": "Each province and territory has a three-tier system: provincial/territorial court, superior court, and court of appeal. Superior court judges are federally appointed; provincial court judges are provincially appointed.",
            "note": "Quebec uses civil law for private law matters within the common-law federal framework.",
        },
        "constitutional_review": "judicial_review_by_supreme_court",
        "judge_selection": "Federal appointment by Governor General on advice of Cabinet; provincial appointments vary",
    },
    "AU": {
        "name": "Australia",
        "system_type": "common_law",
        "federal": {
            "levels": (
                "high_court",
                "federal_court",
                "federal_circuit_court",
            ),
            "supreme_court": "High Court of Australia",
            "specialized": (
                "Family Court of Australia",
                "Federal Court (exercising appellate jurisdiction)",
                "Administrative Appeals Tribunal",
            ),
        },
        "state": {
            "levels": (
                "state_supreme_court",
                "district_county_court",
                "magistrates_local_court",
            ),
            "description": "Each state and territory has its own court hierarchy; the High Court is the final appellate court for both state and federal jurisdictions.",
        },
        "constitutional_review": "judicial_review_by_high_court",
        "judge_selection": "Federal judges appointed by Governor-General in Council; state appointments by state Governor",
    },
    "CN": {
        "name": "China",
        "system_type": "civil_law",
        "federal": {
            "levels": (
                "supreme_peoples_court",
                "higher_peoples_courts",
                "intermediate_peoples_courts",
                "basic_peoples_courts",
            ),
            "supreme_court": "Supreme People's Court (Zuigao Renmin Fayuan)",
            "specialized": (
                "Military Courts",
                "Maritime Courts",
                "Intellectual Property Courts",
                "Internet Courts",
                "Financial Courts",
            ),
            "note": "Courts are supervised by the standing committees of the People's Congress at each level. Judicial independence is limited by Article 128 of the Constitution.",
        },
        "state": {
            "levels": (),
            "description": "China is a unitary state; all courts are part of the national system. Provinces have Higher People's Courts that serve as provincial-level appellate courts.",
        },
        "constitutional_review": "standing_committee_of_npc_review",
        "judge_selection": "Appointed by the People's Congress at each level; judges are civil servants",
    },
    "IN": {
        "name": "India",
        "system_type": "common_law",
        "federal": {
            "levels": (
                "supreme_court",
                "high_courts",
                "district_courts",
            ),
            "supreme_court": "Supreme Court of India",
            "specialized": (
                "National Green Tribunal",
                "National Company Law Tribunal",
                "Securities Appellate Tribunal",
                "Armed Forces Tribunal",
                "Debt Recovery Tribunals",
            ),
        },
        "state": {
            "levels": (
                "high_court",
                "district_sessions_court",
                "magistrate_courts",
            ),
            "description": "Each state has its own High Court (some serve multiple states). District courts handle civil and criminal matters at the district level.",
        },
        "constitutional_review": "judicial_review_by_supreme_court",
        "judge_selection": "Collegium system (Supreme Court judges recommend appointments to the Court and High Courts); President formally appoints",
    },
    "SA": {
        "name": "Saudi Arabia",
        "system_type": "religious_law",
        "federal": {
            "levels": (
                "supreme_judicial_council",
                "courts_of_appeal",
                "general_courts",
            ),
            "supreme_court": "Supreme Judicial Council (Al-Majlis al-A'la lil-Qada')",
            "specialized": (
                "Commercial Courts",
                "Labour Courts",
                "Administrative Courts (Diwan al-Mazalim / Board of Grievances)",
                "Personal Status Courts (divorce, inheritance)",
            ),
            "note": "Sharia (Islamic law) is the primary source of law. The Basic Law confirms the Quran and Sunnah as the constitution.",
        },
        "state": {
            "levels": (),
            "description": "Saudi Arabia is a unitary monarchy; all courts are national.",
        },
        "constitutional_review": "sharia_compliance_review_by_supreme_judicial_council",
        "judge_selection": "Appointed by royal decree on recommendation of the Supreme Judicial Council; qadis must be graduates of sharia law",
    },
    "ZA": {
        "name": "South Africa",
        "system_type": "mixed",
        "federal": {
            "levels": (
                "constitutional_court",
                "supreme_court_of_appeal",
                "high_courts",
            ),
            "supreme_court": "Constitutional Court (final arbiter of constitutional matters)",
            "specialized": (
                "Labour Court and Labour Appeal Court",
                "Competition Appeal Court",
                "Electoral Court",
                "Land Claims Court",
                "Tax Court",
            ),
        },
        "state": {
            "levels": (
                "high_court_provincial_division",
                "magistrates_courts",
            ),
            "description": "Each province has a division of the High Court. Magistrates' courts handle most criminal and civil matters.",
        },
        "constitutional_review": "judicial_review_by_constitutional_court",
        "judge_selection": "Judicial Service Commission recommends; President appoints after consultation",
    },
}

APPEAL_PROCESSES: dict[str, dict[str, Any]] = {
    "common_law": {
        "description": (
            "Appeals are generally on questions of law; findings of fact by the "
            "trial court are given deference unless clearly erroneous. Intermediate "
            "appellate courts review cases as of right; supreme courts typically "
            "exercise discretionary review (writ of certiorari) for cases of "
            "national significance or to resolve circuit splits."
        ),
        "stages": (
            "trial_verdict",
            "motion_for_new_trial_or_judgment_notwithstanding_verdict",
            "notice_of_appeal",
            "appellate_briefing",
            "oral_argument",
            "appellate_decision",
            "petition_for_discretionary_review",
            "final_appellate_decision",
        ),
        "typical_deadline": "30 days from entry of judgment to file notice of appeal",
        "standard_of_review": "de_novo_for_law; clearly_erroneous_for_fact; abuse_of_discretion_for_procedural",
        "example_countries": ("US", "GB", "CA", "AU", "IN"),
    },
    "civil_law": {
        "description": (
            "Civil-law appeals allow de novo review of both facts and law in many "
            "jurisdictions. The appellate court may re-examine evidence, hear new "
            "witnesses, and substitute its own judgment for the lower court. A "
            "further appeal on questions of law only goes to the supreme/cassation "
            "court, which may affirm or quash the lower ruling and remand for "
            "re-hearing."
        ),
        "stages": (
            "first_instance_judgment",
            "notice_of_appeal",
            "appellate_hearing",
            "appellate_decision",
            "cassation_appeal",
            "cassation_decision",
        ),
        "typical_deadline": "1-3 months from service of judgment to file appeal",
        "standard_of_review": "de_novo_on_facts_and_law; cassation_review_on_law_only",
        "example_countries": ("DE", "FR", "JP", "CN"),
    },
    "religious_law": {
        "description": (
            "Appeals in religious-law systems follow internal hierarchies of "
            "religious courts. Appellate review focuses on correct application of "
            "religious texts and precedent (ijtihad and taqlid in Islamic law). "
            "Factual findings may be re-examined. The highest religious judicial "
            "council serves as the final appellate body."
        ),
        "stages": (
            "initial_ruling",
            "appeal_to_superior_religious_court",
            "appellate_decision",
            "review_by_supreme_judicial_council",
        ),
        "typical_deadline": "30 days from ruling to file appeal",
        "standard_of_review": "correct_application_of_sharia; procedural_fairness",
        "example_countries": ("SA",),
    },
    "customary_law": {
        "description": (
            "Customary-law appeals operate within traditional or community-based "
            "dispute-resolution hierarchies. Elders, chiefs, or customary courts "
            "hear disputes at the local level, with appeals to higher customary "
            "bodies. In formalized systems (e.g. South Africa), customary courts "
            "are integrated into the national judicial hierarchy, and statutory "
            "appeals may lie to magistrates' courts or high courts. Customary "
            "rulings are often subject to constitutional rights review."
        ),
        "stages": (
            "customary_hearing",
            "appeal_to_higher_customary_body",
            "appeal_to_formal_court",
        ),
        "typical_deadline": "varies by community and jurisdiction",
        "standard_of_review": "customary_principles_with_constitutional_oversight",
        "example_countries": ("ZA", "NG", "KE"),
    },
    "mixed": {
        "description": (
            "Mixed legal systems combine features of common-law appeals "
            "(stare decisis, deference to trial findings) with civil-law or "
            "customary-law elements. Constitutional review sits alongside ordinary "
            "appellate review, and the constitutional court is typically the "
            "highest authority on rights questions."
        ),
        "stages": (
            "trial_judgment",
            "first_appeal",
            "second_appeal",
            "constitutional_review",
        ),
        "typical_deadline": "15-90 days depending on jurisdiction and court level",
        "standard_of_review": "varies_by_jurisdiction_and_court",
        "example_countries": ("ZA",),
    },
}

LEGAL_TERMINOLOGY: dict[str, dict[str, Any]] = {
    "stare_decisis": {
        "term": "stare decisis",
        "definition": "The doctrine that courts should follow precedents set by prior decisions. Latin for 'to stand by things decided.' Binding in common-law systems; persuasive in civil-law systems.",
        "category": "common_law_concepts",
        "related_terms": ("precedent", "ratio_decidendi", "obiter_dictum"),
    },
    "ratio_decidendi": {
        "term": "ratio decidendi",
        "definition": "The legal reasoning or principle upon which a court's decision is based. The binding part of a precedent; everything else is obiter dictum (persuasive only).",
        "category": "common_law_concepts",
        "related_terms": ("obiter_dictum", "stare_decisis", "precedent"),
    },
    "obiter_dictum": {
        "term": "obiter dictum",
        "definition": "A remark or observation made by a judge that is not essential to the decision and therefore not binding as precedent, though it may be persuasive in later cases.",
        "category": "common_law_concepts",
        "related_terms": ("ratio_decidendi", "stare_decisis"),
    },
    "habeas_corpus": {
        "term": "habeas corpus",
        "definition": "A legal writ requiring a person under arrest to be brought before a court to determine if their detention is lawful. A fundamental safeguard against arbitrary detention.",
        "category": "rights_and_remedies",
        "related_terms": ("due_process", "judicial_review"),
    },
    "due_process": {
        "term": "due process",
        "definition": "The requirement that legal proceedings be fair and that individuals receive notice and an opportunity to be heard before the government deprives them of life, liberty, or property.",
        "category": "rights_and_remedies",
        "related_terms": ("habeas_corpus", "natural_justice", "fair_trial"),
    },
    "natural_justice": {
        "term": "natural justice",
        "definition": "Principles of procedural fairness: the right to a fair hearing (audi alteram partem) and the rule against bias (nemo iudex in causa sua). Foundational to administrative and judicial proceedings.",
        "category": "rights_and_remedies",
        "related_terms": ("due_process", "fair_trial"),
    },
    "jurisprudence_constante": {
        "term": "jurisprudence constante",
        "definition": "The civil-law equivalent of stare decisis: a series of consistent decisions on the same legal question carries persuasive weight, though no single decision is formally binding.",
        "category": "civil_law_concepts",
        "related_terms": ("stare_decisis", "precedent"),
    },
    "cassation": {
        "term": "cassation",
        "definition": "In civil-law systems, the power of a supreme court to quash (casser) a lower court's decision for error of law and remand for re-hearing. The Cour de cassation is the French supreme court for ordinary appeals.",
        "category": "civil_law_concepts",
        "related_terms": ("cour_de_cassation", "appeal"),
    },
    "certiorari": {
        "term": "certiorari",
        "definition": "A writ by which a higher court reviews a lower court's decision. In the US Supreme Court, a writ of certiorari is the mechanism for discretionary review; granted for less than 2% of petitions.",
        "category": "procedure",
        "related_terms": ("appeal", "discretionary_review"),
    },
    "in_camera": {
        "term": "in camera",
        "definition": "A proceeding held in private (in the judge's chambers) rather than in open court. Used for sensitive evidence, national security matters, or cases involving minors.",
        "category": "procedure",
        "related_terms": ("closed_hearing", "in_chambers"),
    },
    "amicus_curiae": {
        "term": "amicus curiae",
        "definition": "A 'friend of the court' — a person or organization that is not a party to the case but offers information or expertise relevant to the legal issues. Common in constitutional and appellate litigation.",
        "category": "procedure",
        "related_terms": ("intervenor", "third_party_submission"),
    },
    "force_majeure": {
        "term": "force majeure",
        "definition": "A contractual clause that relieves parties from liability or obligation when an extraordinary event beyond their control (natural disaster, war, pandemic) prevents performance.",
        "category": "contract_law",
        "related_terms": ("act_of_god", "frustration_of_purpose", "impossibility"),
    },
    "ultra_vires": {
        "term": "ultra vires",
        "definition": "An act that exceeds the legal authority of the person or body performing it. A government agency acting ultra vires acts without lawful power, and its decision may be void.",
        "category": "administrative_law",
        "related_terms": ("jurisdiction", "void_ab_initio"),
    },
    "res_judicata": {
        "term": "res judicata",
        "definition": "The principle that a matter that has been adjudicated by a competent court may not be pursued further by the same parties. Promotes finality and prevents re-litigation.",
        "category": "procedure",
        "related_terms": ("collateral_estoppel", "issue_preclusion", "finality"),
    },
    "mens_rea": {
        "term": "mens rea",
        "definition": "The mental element of a crime: the intention or knowledge of wrongdoing. A cornerstone of criminal law; a guilty act (actus reus) without a guilty mind is generally insufficient for conviction.",
        "category": "criminal_law",
        "related_terms": ("actus_reus", "criminal_intent", "strict_liability"),
    },
    "actus_reus": {
        "term": "actus reus",
        "definition": "The physical act of a crime (the guilty act), as distinct from the mental state (mens rea). Both elements must typically be proven for a criminal conviction.",
        "category": "criminal_law",
        "related_terms": ("mens_rea", "criminal_law"),
    },
    "subpoena": {
        "term": "subpoena",
        "definition": "A writ ordering a person to attend court (subpoena ad testificandum) or to produce documents or evidence (subpoena duces tecum). Failure to comply may result in contempt of court.",
        "category": "procedure",
        "related_terms": ("summons", "contempt_of_court"),
    },
    "jurisdiction": {
        "term": "jurisdiction",
        "definition": "The authority of a court to hear and decide a case. Includes subject-matter jurisdiction (type of case), personal jurisdiction (authority over parties), and territorial jurisdiction (geographic scope).",
        "category": "procedure",
        "related_terms": ("venue", "forum_non_conveniens", "ultra_vires"),
    },
    "indictment": {
        "term": "indictment",
        "definition": "A formal accusation that a person has committed a crime, issued by a grand jury after reviewing the prosecutor's evidence. Required for federal felony prosecutions in the US.",
        "category": "criminal_law",
        "related_terms": ("grand_jury", "information", "arraignment"),
    },
    "injunction": {
        "term": "injunction",
        "definition": "A court order requiring a party to do or refrain from doing a specific act. Temporary injunctions preserve the status quo pending trial; permanent injunctions are final remedies.",
        "category": "remedies",
        "related_terms": ("temporary_restraining_order", "specific_performance", "declaratory_judgment"),
    },
    "void_ab_initio": {
        "term": "void ab initio",
        "definition": "A legal act or contract that is void from the beginning — as though it never existed. Contrast with voidable, where the act is valid until set aside by a court.",
        "category": "contract_law",
        "related_terms": ("voidable", "ultra_vires", "nullity"),
    },
    "pro_bono": {
        "term": "pro bono",
        "definition": "Legal services provided voluntarily and without charge for the public good (from 'pro bono publico'). Bar associations in many jurisdictions encourage a minimum number of pro bono hours per year.",
        "category": "legal_profession",
        "related_terms": ("legal_aid", "public_defender"),
    },
}


def _norm_country(country: str) -> str:
    return country.strip().upper()


def get_legal_system(country_code: str) -> dict[str, Any] | None:
    """Return the legal system type and metadata for a country."""
    code = _norm_country(country_code)
    hierarchy = COURT_HIERARCHIES.get(code)
    if hierarchy is None:
        return None
    return {
        "country": code,
        "country_name": hierarchy["name"],
        "system_type": hierarchy["system_type"],
        "constitutional_review": hierarchy["constitutional_review"],
        "judge_selection": hierarchy["judge_selection"],
    }


def get_court_hierarchy(country_code: str) -> dict[str, Any] | None:
    """Return the full court hierarchy for a country."""
    code = _norm_country(country_code)
    hierarchy = COURT_HIERARCHIES.get(code)
    if hierarchy is None:
        return None
    return dict(hierarchy)


def get_appeal_process(system_type: str) -> dict[str, Any] | None:
    """Return the appeal process description for a legal system type."""
    return APPEAL_PROCESSES.get(system_type.strip().lower())


def get_term(term_name: str) -> dict[str, Any] | None:
    """Look up a legal term by name (case-insensitive)."""
    q = term_name.strip().lower().replace(" ", "_")
    return LEGAL_TERMINOLOGY.get(q)


def terms_by_category(category: str) -> list[dict[str, Any]]:
    """Return all legal terms in a given category."""
    cat = category.strip().lower().replace(" ", "_")
    return [t for t in LEGAL_TERMINOLOGY.values() if t["category"] == cat]


def list_countries() -> list[str]:
    """Return the sorted list of country codes covered by COURT_HIERARCHIES."""
    return sorted(COURT_HIERARCHIES.keys())


def court_at_level(country_code: str, level: str) -> list[str]:
    """Return the court names at a given hierarchy level for a country.

    Args:
        country_code: ISO-3166-1 alpha-2 code.
        level: One of 'supreme_court', 'courts_of_appeals', 'district_courts',
               'high_court', 'state_supreme_court', etc.

    Returns:
        List of court name strings at that level, or empty list if not found.
    """
    code = _norm_country(country_code)
    hierarchy = COURT_HIERARCHIES.get(code)
    if hierarchy is None:
        return []
    fed = hierarchy.get("federal", {})
    state = hierarchy.get("state", {})
    lvl = level.strip().lower()
    if lvl in fed:
        val = fed[lvl]
        if isinstance(val, tuple):
            return list(val)
        return [str(val)]
    if lvl in state:
        val = state[lvl]
        if isinstance(val, tuple):
            return list(val)
        return [str(val)]
    if lvl == "levels" or lvl == "federal_levels":
        result = list(fed.get("levels", ()))
        if lvl == "levels":
            result.extend(list(state.get("levels", ())))
        return result
    return []


def supreme_court(country_code: str) -> str | None:
    """Return the name of the supreme court for a country."""
    code = _norm_country(country_code)
    hierarchy = COURT_HIERARCHIES.get(code)
    if hierarchy is None:
        return None
    fed = hierarchy.get("federal", {})
    sc = fed.get("supreme_court")
    if sc:
        return str(sc)
    return fed.get("supreme_court", None)


__all__ = [
    "LEGAL_SYSTEM_TYPES",
    "COURT_HIERARCHIES",
    "APPEAL_PROCESSES",
    "LEGAL_TERMINOLOGY",
    "get_legal_system",
    "get_court_hierarchy",
    "get_appeal_process",
    "get_term",
    "terms_by_category",
    "list_countries",
    "court_at_level",
    "supreme_court",
]
