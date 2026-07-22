"""Social engineering and psychological manipulation analysis module.

Exposes attack vectors, psychological principles, manipulation techniques,
detection signals, and defense protocols as structured data and analytical
functions.

Public surface::

    analyze_persuasion_techniques(text)             -> dict
    detect_manipulation_patterns(conversation)      -> dict
    assess_trustworthiness(profile)                 -> dict
    classify_social_engineering_attack(scenario)    -> dict

    ATTACK_VECTORS              dict[vector_name] -> properties
    PSYCHOLOGICAL_PRINCIPLES    dict[principle_name] -> properties
    MANIPULATION_TECHNIQUES     dict[technique_name] -> properties
    DETECTION_MARKERS           dict[marker_name] -> properties
    DEFENSE_PROTOCOLS           dict[protocol_name] -> properties
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# ---------------------------------------------------------------------------
# Attack Vectors
# ---------------------------------------------------------------------------

ATTACK_VECTORS: dict[str, dict[str, Any]] = {
    "phishing": {
        "description": "Fraudulent communication appearing to come from a reputable source to induce individuals to reveal sensitive information",
        "subtypes": ["spear_phishing", "whaling", "clone_phishing", "vishing", "smishing"],
        "channels": ["email", "phone", "sms", "social_media", "instant_messaging"],
        "indicators": [
            "urgency_in_subject_line",
            "generic_greeting",
            "mismatched_urls",
            "spoofed_sender_domain",
            "unsolicited_attachments",
            "requests_for_credentials",
            "threats_of_account_closure",
        ],
        "success_rate_range": "10-30%",
        "mitigation": ["dmarc_dkim_spf", "user_training", "mfa", "email_filtering"],
    },
    "pretexting": {
        "description": "Creating a fabricated scenario to engage a targeted victim and increase the chance they will divulge information or perform actions",
        "subtypes": ["impersonation", "survey_scam", "technical_support", "law_enforcement"],
        "channels": ["phone", "in_person", "email", "social_media"],
        "indicators": [
            "unverifiable_identity_claims",
            "requests_for_personal_details",
            "manufactured_urgency",
            "name_dropping_authority_figures",
            "scripted_narrative_consistency",
        ],
        "success_rate_range": "20-40%",
        "mitigation": ["call_back_verification", "identity_verification_protocols", "need_to_know_policy"],
    },
    "baiting": {
        "description": "Offering something enticing to exploit the victim's curiosity or greed",
        "subtypes": ["usb_drop", "free_download_trap", "clickbait_content", "fake_job_offer"],
        "channels": ["physical_media", "websites", "social_media", "job_boards"],
        "indicators": [
            "too_good_to_be_true_offers",
            "free_items_requiring_personal_info",
            "abandoned_physical_media",
            "urgent_limited_time_offers",
        ],
        "success_rate_range": "15-25%",
        "mitigation": ["disable_autorun", "endpoint_detection", "physical_security_policy"],
    },
    "tailgating": {
        "description": "Gaining unauthorized physical access by following an authorized person through a secure entry",
        "subtypes": ["piggybacking", "door_holding", "uniform_impersonation", "delivery_persona"],
        "channels": ["physical_access_points", "parking_garages", "loading_docks"],
        "indicators": [
            "person_following_closely",
            "carrying_items_to_avoid_using_badge",
            "claiming_forgotten_badge",
            "appearing_as_maintenance_or_delivery",
        ],
        "success_rate_range": "30-50%",
        "mitigation": ["mantrap_entrances", "security_guards", "badge_access_turnstiles", "employee_vigilance_training"],
    },
    "quid_pro_quo": {
        "description": "Offering a service or benefit in exchange for information or access",
        "subtypes": ["fake_tech_support", "survey_gift_card", "research_participation", "consulting_offer"],
        "channels": ["phone", "email", "social_media", "in_person"],
        "indicators": [
            "unsolicited_service_offer",
            "request_for_credentials_to_receive_benefit",
            "pressure_to_act_immediately",
            "vague_organization_affiliation",
        ],
        "success_rate_range": "10-20%",
        "mitigation": ["help_desk_verification", "employee_reporting_culture", "service_request_policy"],
    },
}

# ---------------------------------------------------------------------------
# Psychological Principles (Cialdini's six + extensions)
# ---------------------------------------------------------------------------

PSYCHOLOGICAL_PRINCIPLES: dict[str, dict[str, Any]] = {
    "authority": {
        "description": "People tend to obey authority figures, even when asked to perform objectionable acts",
        "exploitation_methods": [
            "fake_credentials_or_badges",
            "impersonating_executives",
            "citing_fabricated_expertise",
            "name_dropping_regulators_or_law_enforcement",
        ],
        "linguistic_markers": [
            "per_policy", "management_requires", "as_per_director",
            "compliance_office", "executive_order", "mandated_by",
        ],
        "milgram_reference": "65% obedience to lethal shocks in original experiment",
        "defense": "independent_verification_of_authority_claims",
    },
    "scarcity": {
        "description": "People assign more value to opportunities when they are less available",
        "exploitation_methods": [
            "limited_time_offers",
            "exclusive_access_claims",
            "only_N_left_countdowns",
            "fomo_triggering_language",
        ],
        "linguistic_markers": [
            "limited_time", "only_x_left", "exclusive_offer",
            "act_now", "before_its_gone", "last_chance",
            "while_supplies_last", "dont_miss_out",
        ],
        "effectiveness": "Highest when scarcity is newly imposed or from competition",
        "defense": "recognize_arousal_as_cue_to_reassess",
    },
    "urgency": {
        "description": "Pressure to act quickly suppresses critical thinking and verification behavior",
        "exploitation_methods": [
            "countdown_timers",
            "immediate_action_required",
            "threat_of_loss_or_penalty",
            "escalating_consequences",
        ],
        "linguistic_markers": [
            "immediate_action_required", "within_24_hours", "urgent",
            "critical", "time_sensitive", "respond_now",
            "your_account_will_be", "failure_to_act",
        ],
        "interaction_with_scarcity": "urgency amplifies scarcity effect; combined they are the highest-conversion manipulation pair",
        "defense": "pause_and_verify_protocol",
    },
    "social_proof": {
        "description": "People copy the actions of others in an attempt to undertake behavior in a given situation",
        "exploitation_methods": [
            "fake_testimonials_and_reviews",
            "manufactured_popularity_metrics",
            "bot_follower_counts",
            "everyone_is_doing_it_framing",
        ],
        "linguistic_markers": [
            "thousands_of_satisfied_customers", "everyone_is",
            "join_x_others", "most_popular", "trending",
            "best_selling", "x_people_are_viewing",
        ],
        "asche_conformity_reference": "37% conformity rate in line-judgment experiments",
        "defense": "verify_claims_independently_not_through_consensus",
    },
    "liking": {
        "description": "People are more likely to be persuaded by people they like",
        "exploitation_methods": [
            "similarity_feigning",
            "compliment_bombing",
            "familiarity_building",
            "attractiveness_optimization",
            "shared_interest_fabrication",
        ],
        "linguistic_markers": [
            "im_like_you", "we_share", "as_a_fellow",
            "i_understand_how_you_feel", "between_us",
            "youre_different_from_others",
        ],
        "tupperware_reference": "Tupperware parties: buying from a friend vs stranger — 2x higher conversion",
        "defense": "separate_liking_from_decision_merit",
    },
    "reciprocity": {
        "description": "People feel obliged to give back to others who have given to them",
        "exploitation_methods": [
            "free_sample_then_upsell",
            "unsolicited_favor_followed_by_request",
            "door_in_the_face_technique",
            "foot_in_the_door_technique",
        ],
        "linguistic_markers": [
            "ive_done_x_for_you", "you_owe_me", "after_all_ive_helped",
            "can_you_do_this_one_thing", "i_scratch_your_back",
            "return_the_favor",
        ],
        "hare_krishna_reference": "Free flower given then donation requested — dramatically increased donations",
        "defense": "recognize_unsolicited_favors_as_potential_setups",
    },
    "commitment_and_consistency": {
        "description": "People feel compelled to align their future behavior with past commitments",
        "exploitation_methods": [
            "low_ball_technique",
            "foot_in_the_door",
            "public_commitment_leveraging",
            "written_goal_escalation",
        ],
        "linguistic_markers": [
            "you_said_you_would", "we_agreed", "youve_already",
            "stay_consistent", "follow_through", "keep_your_word",
        ],
        "freedman_fraser_reference": "76% compliance with large request after small initial commitment vs 17% without",
        "defense": "recognize_escalation_of_commitment_as_manipulation",
    },
}

# ---------------------------------------------------------------------------
# Manipulation Techniques
# ---------------------------------------------------------------------------

MANIPULATION_TECHNIQUES: dict[str, dict[str, Any]] = {
    "gaslighting": {
        "description": "Psychological manipulation that causes the victim to question their own memory, perception, or sanity",
        "tactics": [
            "denying_events_occurred",
            "trivializing_emotions",
            "shifting_blame_to_victim",
            "withholding_information",
            "countering_victims_memory",
            "blocking_and_diverting",
        ],
        "linguistic_markers": [
            "that_never_happened", "youre_being_paranoid",
            "youre_too_sensitive", "i_never_said_that",
            "youre_imagining_things", "you_always_twist_things",
            "everyone_agrees_youre_wrong", "you_remembered_it_wrong",
        ],
        "psychological_effects": ["anxiety", "confusion", "self_doubt", "depression", "dependency"],
        "relationship_contexts": ["intimate_partner", "workplace", "family", "political_propaganda"],
    },
    "love_bombing": {
        "description": "Overwhelming someone with excessive affection, attention, and admiration to influence or control them",
        "tactics": [
            "excessive_communication",
            "grand_gestures_early",
            "constant_praise_and_flattery",
            "future_faking",
            "isolation_from_support_network",
        ],
        "linguistic_markers": [
            "youre_perfect", "ive_never_met_anyone_like_you",
            "we_were_meant_to_be", "soulmate",
            "i_cant_live_without_you", "youre_my_everything",
            "we_should_spend_all_our_time_together",
        ],
        "red_flags": [
            "intensity_out_of_proportion_to_relationship_length",
            "jealousy_disguised_as_care",
            "rapid_escalation_timeline",
        ],
        "exploitation_contexts": ["romantic_relationships", "cults", "multilevel_marketing", "political_recruitment"],
    },
    "negging": {
        "description": "Backhanded compliments or subtle insults designed to undermine confidence and increase the victim's need for the manipulator's approval",
        "tactics": ["backhanded_compliments", "comparative_insults", "disguised_criticism", "sarcastic_praise"],
        "linguistic_markers": [
            "youre_pretty_good_for_a", "i_dont_usually_like_x_but_youre_different",
            "you_clean_up_nicely", "thats_surprising_coming_from_you",
            "youre_smarter_than_you_look", "most_people_couldnt_pull_that_off",
        ],
        "psychological_impact": "creates_dependency_on_manipulator_for_self_esteem_restoration",
        "defense": "recognize_qualifying_language_as_neg_not_compliment",
    },
    "isolation": {
        "description": "Systematically cutting off the victim from their support network to increase dependency on the manipulator",
        "tactics": [
            "criticizing_friends_and_family",
            "monopolizing_time",
            "creating_jealousy_scenarios",
            "relocation_to_distant_location",
            "controlling_communication_methods",
        ],
        "linguistic_markers": [
            "your_friends_dont_care_about_you", "theyre_a_bad_influence",
            "we_dont_need_anyone_else", "its_us_against_the_world",
            "i_should_be_enough_for_you", "you_spend_too_much_time_with_them",
        ],
        "progression_model": "gradual_erosion_not_sudden_cutoff",
        "defense": "maintain_independent_relationships_and_regular_contact",
    },
    "intermittent_reinforcement": {
        "description": "Unpredictable alternation between reward and punishment to create trauma bonding and addictive attachment",
        "tactics": [
            "hot_and_cold_behavior",
            "unpredictable_affection",
            "random_reward_schedules",
            "intermittent_punishment",
        ],
        "linguistic_markers": [
            "i_cant_stay_away", "its_different_this_time",
            "they_dont_understand_you_like_i_do",
        ],
        "behavioral_mechanism": "variable_ratio_reinforcement_schedule_most_resistant_to_extinction",
        "psychological_effects": ["trauma_bonding", "anxious_attachment", "addiction_cycles"],
        "defense": "recognize_inconsistent_patterns_as_control_not_love",
    },
    "projection": {
        "description": "Attributing one's own unacceptable thoughts, feelings, or behaviors to another person",
        "tactics": [
            "accusing_others_of_own_behavior",
            "deflecting_accountability",
            "reversing_victim_and_offender_roles",
        ],
        "linguistic_markers": [
            "youre_the_one_whos_x", "i_only_did_it_because_you",
            "if_you_hadnt_x_i_wouldnt_have", "youre_just_like",
            "look_who_is_talking",
        ],
        "psychological_origin": "defense_mechanism_against_cognitive_dissonance",
        "defense": "maintain_objective_records_of_own_actions_separate_from_accusations",
    },
    "triangulation": {
        "description": "Engaging a third party to validate the manipulator's perspective and undermine the victim's reality",
        "tactics": [
            "bringing_third_parties_into_disputes",
            "comparing_victim_to_others_unfavorably",
            "gossip_campaigns",
            "ally_recruitment_against_victim",
        ],
        "linguistic_markers": [
            "even_x_agrees_with_me", "everyone_thinks", "x_told_me",
            "x_never_complains_about", "why_cant_you_be_more_like",
        ],
        "workplace_equivalent": "mobbing — group_bullying_orchestrated_by_manipulator",
        "defense": "direct_communication_with_cited_third_parties",
    },
    "catastrophizing": {
        "description": "Exaggerating the negative consequences of a decision or situation to induce fear-based compliance",
        "tactics": [
            "worst_case_scenario_presentation",
            "slippery_slope_arguments",
            "magnifying_minor_risks",
            "predicting_disaster_from_inaction",
        ],
        "linguistic_markers": [
            "if_you_dont_x_then_y_will_happen", "this_will_destroy",
            "youll_regret_this", "everything_will_fall_apart",
            "youll_never_recover", "this_is_the_end",
        ],
        "cognitive_distortion": "magnification_without_evidence_of_probability",
        "defense": "request_specific_probability_and_evidence_for_claimed_outcome",
    },
}

# ---------------------------------------------------------------------------
# Detection Markers
# ---------------------------------------------------------------------------

DETECTION_MARKERS: dict[str, dict[str, Any]] = {
    "linguistic_inconsistency": {
        "description": "Pattern of statements that contradict each other or evolve in self-serving ways",
        "indicators": [
            "changing_details_between_retellings",
            "contradictory_self_descriptions",
            "shifting_timeline_elements",
            "inconsistent_emotional_tone_across_topic",
        ],
        "detection_method": "statement_analysis_comparing_multiple_accounts",
    },
    "pronoun_avoidance": {
        "description": "Reduced use of first-person pronouns to distance from actions or claims",
        "indicators": [
            "low_i_me_my_usage_relative_to_baseline",
            "increased_passive_voice_constructions",
            "impersonal_constructions",
            "third_person_self_reference",
        ],
        "linguistic_inquiry_word_count_reference": "LIWC analysis: deceptive statements show reduced I-words",
        "detection_method": "pronoun_ratio_analysis_against_established_baseline",
    },
    "pressure_tactics": {
        "description": "Temporal or social pressure designed to short-circuit verification behavior",
        "indicators": [
            "artificial_deadlines",
            "limited_opportunity_claims",
            "consequence_threats_for_delay",
            "bypassing_normal_procedures",
        ],
        "detection_method": "identify_insistence_on_immediate_action_without_legitimate_need",
    },
    "emotional_manipulation_markers": {
        "description": "Language designed to evoke specific emotional responses to disable rational analysis",
        "indicators": [
            "fear_triggering_language",
            "outrage_induction",
            "flattery_prior_to_request",
            "guilt_tripping_phrases",
            "shame_based_compliance_demands",
        ],
        "detection_method": "sentiment_analysis_tracking_emotional_valence_shifts",
    },
    "authority_overclaiming": {
        "description": "Excessive or unverifiable claims of authority, credentials, or special access",
        "indicators": [
            "vague_organization_references",
            "unverifiable_credentials",
            "name_dropping_without_specific_connection",
            "insider_knowledge_claims",
        ],
        "detection_method": "independent_verification_of_every_authority_claim",
    },
    "narrative_coercion": {
        "description": "Framing that eliminates perceived alternatives to create false dilemmas",
        "indicators": [
            "binary_choice_presentation",
            "false_dichotomies",
            "excluded_middles",
            "youre_either_with_us_or_against_us",
        ],
        "detection_method": "identify_unacknowledged_alternatives_in_presented_narrative",
    },
}

# ---------------------------------------------------------------------------
# Defense Protocols
# ---------------------------------------------------------------------------

DEFENSE_PROTOCOLS: dict[str, dict[str, Any]] = {
    "verification_protocol": {
        "description": "Independent verification of identity and claims before any action",
        "steps": [
            "establish_out_of_band_contact_method",
            "request_specific_identifying_information",
            "verify_through_published_channel_not_provided_link",
            "document_the_interaction_for_review",
        ],
        "applicable_to": ["phishing", "pretexting", "quid_pro_quo"],
        "effectiveness": "high — eliminates most impersonation-based attacks",
    },
    "pause_and_assess": {
        "description": "Structured delay before responding to emotionally charged or urgent requests",
        "steps": [
            "acknowledge_receipt_without_committing",
            "take_minimum_30_minutes_before_responding",
            "consult_one_independent_third_party",
            "evaluate_request_on_merits_not_emotions",
        ],
        "applicable_to": ["all_attacks"],
        "effectiveness": "high — urgency is the primary enabler of most social engineering",
    },
    "security_awareness_training": {
        "description": "Structured program teaching recognition and response patterns",
        "components": [
            "simulated_phishing_exercises",
            "case_study_analysis",
            "psychological_principle_education",
            "reporting_procedure_practice",
            "regular_refresher_intervals",
        ],
        "effectiveness_metrics": [
            "phishing_click_through_rate_reduction",
            "reporting_rate_increase",
            "response_time_to_threats",
        ],
        "applicable_to": ["all_attacks"],
    },
    "reporting_mechanism": {
        "description": "Clear, low-friction channels for reporting suspected social engineering attempts",
        "components": [
            "dedicated_reporting_email_or_hotline",
            "non_punitive_reporting_policy",
            "rapid_response_team",
            "feedback_loop_to_reporter",
            "aggregate_anonymized_metrics",
        ],
        "barriers_to_reporting": ["fear_of_blame", "uncertainty_about_threshold", "perceived_futility"],
        "applicable_to": ["all_attacks"],
    },
    "information_classification": {
        "description": "Classifying and compartmentalizing information to limit what any single person can disclose",
        "components": [
            "need_to_know_access_policy",
            "data_classification_tiers",
            "segmented_information_domains",
            "access_audit_logging",
        ],
        "principle": "a_compromised_individual_can_only_leak_what_they_know",
        "applicable_to": ["pretexting", "quid_pro_quo"],
    },
}

# ---------------------------------------------------------------------------
# Linguistic Pattern Databases
# ---------------------------------------------------------------------------

_URGENCY_PATTERNS: list[str] = [
    r"\b(urgent|immediate|critical|asap)\b",
    r"\b(within\s+\d+\s+(hours?|minutes?))\b",
    r"\b(before\s+(it'?s|its)\s+too\s+late)\b",
    r"\b(act\s+now|don'?t\s+delay|time\s+is\s+running)\b",
    r"\b(limited\s+time|expires?\s+(in|on|soon))\b",
]

_AUTHORITY_PATTERNS: list[str] = [
    r"\b(official|authorized|mandated|required\s+by)\b",
    r"\b(compliance|regulatory|government|federal)\b",
    r"\b(policy\s+requires|per\s+regulation|according\s+to)\b",
]

_SCARCITY_PATTERNS: list[str] = [
    r"\b(only\s+\d+\s+left|limited\s+supply)\b",
    r"\b(exclusive|rare|once\s+in\s+a\s+lifetime)\b",
    r"\b(while\s+supplies?\s+last)\b",
]

_SOCIAL_PROOF_PATTERNS: list[str] = [
    r"\b(everyone\s+(is|has|does)|most\s+people)\b",
    r"\b(thousands?|millions?)\s+of\b",
    r"\b(join(ing)?\s+\d+)\b",
    r"\b(trending|popular|best.?selling)\b",
]

_RECIPROCITY_PATTERNS: list[str] = [
    r"\b(i'?ve\s+done|after\s+all\s+i'?ve)\b",
    r"\b(you\s+owe\s+me|return\s+the\s+favor)\b",
    r"\b(free\s+(sample|gift|offer|trial))\b",
]

_GASLIGHTING_PATTERNS: list[str] = [
    r"\b(that\s+never\s+happened)\b",
    r"\b(you'?re\s+(being\s+)?(paranoid|too\s+sensitive|crazy|imagining|overreacting))\b",
    r"\b(i\s+never\s+said\s+that)\b",
    r"\b(you\s+remember\w*\s+(it\s+)?wrong)\b",
    r"\b(it'?s\s+all\s+in\s+your\s+head)\b",
]

_LOVE_BOMBING_PATTERNS: list[str] = [
    r"\b(soul\s*mate|perfect|meant\s+to\s+be)\b",
    r"\b(can'?t\s+live\s+without\s+you)\b",
    r"\b(you'?re\s+my\s+everything)\b",
    r"\b(i'?ve\s+never\s+(felt|met|been))\b",
]

_NEGGING_PATTERNS: list[str] = [
    r"\b(for\s+a\s+\w+,\s+you'?re)\b",
    r"\b(you\s+\w+\s+nicely)\b",
    r"\b(that'?s\s+surprising\s+(coming\s+)?from\s+you)\b",
    r"\b(you'?re\s+\w+er\s+than\s+you\s+look)\b",
]

_DISTANCING_PATTERNS: list[str] = [
    r"\b(it\s+was\s+decided|mistakes?\s+were\s+made)\b",
    r"\b(someone|they|people)\s+(said|did|told)\b",
    r"\b(allegedly|reportedly|supposedly)\b",
]

_ALL_PATTERNS: dict[str, list[str]] = {
    "urgency": _URGENCY_PATTERNS,
    "authority": _AUTHORITY_PATTERNS,
    "scarcity": _SCARCITY_PATTERNS,
    "social_proof": _SOCIAL_PROOF_PATTERNS,
    "reciprocity": _RECIPROCITY_PATTERNS,
    "gaslighting": _GASLIGHTING_PATTERNS,
    "love_bombing": _LOVE_BOMBING_PATTERNS,
    "negging": _NEGGING_PATTERNS,
    "distancing_language": _DISTANCING_PATTERNS,
}


# ---------------------------------------------------------------------------
# Analysis Functions
# ---------------------------------------------------------------------------

def analyze_persuasion_techniques(text: str) -> dict[str, Any]:
    """Analyze text for persuasion and influence techniques.

    Scans the provided text against known linguistic pattern databases
    for urgency, authority, scarcity, social proof, and reciprocity markers.

    Args:
        text: The text string to analyze.

    Returns:
        Dict with keys:
            - techniques_detected: list[str] — named techniques found
            - matches: dict[str, list[str]] — technique -> matched phrases
            - intensity_score: float — 0.0 to 1.0 normalized intensity
            - dominant_technique: str | None — technique with most matches
            - recommendation: str — suggested defensive action
    """
    if not text or not isinstance(text, str):
        return {
            "techniques_detected": [],
            "matches": {},
            "intensity_score": 0.0,
            "dominant_technique": None,
            "recommendation": "Insufficient text for analysis.",
        }

    text_lower = text.lower()
    techniques: dict[str, list[str]] = {}

    for technique, patterns in _ALL_PATTERNS.items():
        matches: list[str] = []
        for pattern in patterns:
            found = re.findall(pattern, text_lower, re.IGNORECASE)
            matches.extend(found)
        if matches:
            techniques[technique] = matches

    total_matches = sum(len(m) for m in techniques.values())
    technique_names = list(techniques.keys())

    persuasion_techniques = {
        k: v
        for k, v in techniques.items()
        if k in ("urgency", "authority", "scarcity", "social_proof", "reciprocity")
    }
    manipulation_techniques = {
        k: v
        for k, v in techniques.items()
        if k not in ("urgency", "authority", "scarcity", "social_proof", "reciprocity", "distancing_language")
    }
    manipulation_detected = bool(manipulation_techniques)

    word_count = len(text.split())
    intensity = total_matches / (word_count + 10.0) if word_count > 0 else 0.0
    intensity = min(1.0, intensity)

    dominant = max(techniques, key=lambda k: len(techniques[k])) if techniques else None

    recommendation = "No significant persuasion pressure detected."
    if intensity > 0.3:
        recommendation = "Moderate persuasion pressure detected. Apply pause-and-assess protocol."
    if intensity > 0.6:
        recommendation = "High persuasion pressure. Verification protocol strongly recommended before any action."
    if manipulation_detected:
        recommendation += " Manipulation techniques detected; exercise extreme caution."
    if techniques.get("urgency") and techniques.get("scarcity"):
        recommendation += " Combined urgency+scarcity highest-risk pattern present."

    return {
        "techniques_detected": technique_names,
        "matches": techniques,
        "intensity_score": round(intensity, 3),
        "dominant_technique": dominant,
        "recommendation": recommendation,
    }


def detect_manipulation_patterns(conversation: list[dict[str, str]]) -> dict[str, Any]:
    """Detect manipulation patterns across a multi-turn conversation.

    Args:
        conversation: List of dicts with 'speaker' and 'text' keys.

    Returns:
        Dict with keys:
            - manipulation_detected: bool
            - detected_patterns: list[str] — named manipulation techniques
            - speaker_analysis: dict[speaker] -> per-speaker metrics
            - escalation_detected: bool — whether pressure increased over time
            - gaslighting_events: list[dict] — gaslighting instances with context
            - overall_risk: str — 'low', 'medium', 'high', 'critical'
    """
    if not conversation:
        return {
            "manipulation_detected": False,
            "detected_patterns": [],
            "speaker_analysis": {},
            "escalation_detected": False,
            "gaslighting_events": [],
            "overall_risk": "low",
        }

    speaker_texts: dict[str, list[str]] = {}
    speaker_turns: dict[str, int] = Counter()

    for turn in conversation:
        speaker = turn.get("speaker", "unknown")
        text = turn.get("text", "")
        speaker_texts.setdefault(speaker, []).append(text)
        speaker_turns[speaker] += 1

    speaker_analysis: dict[str, Any] = {}
    all_patterns_found: set[str] = set()
    gaslighting_events: list[dict[str, Any]] = []
    speaker_intensities: list[float] = []

    for speaker, texts in speaker_texts.items():
        combined = " ".join(texts)
        analysis = analyze_persuasion_techniques(combined)
        speaker_analysis[speaker] = {
            "turns": speaker_turns[speaker],
            "techniques": analysis["techniques_detected"],
            "intensity": analysis["intensity_score"],
            "dominant_technique": analysis["dominant_technique"],
        }
        speaker_intensities.append(analysis["intensity_score"])
        all_patterns_found.update(analysis["techniques_detected"])

        if "gaslighting" in analysis["techniques_detected"]:
            for text in texts:
                gaslight_matches = []
                for pattern in _GASLIGHTING_PATTERNS:
                    found = re.findall(pattern, text, re.IGNORECASE)
                    gaslight_matches.extend(found)
                if gaslight_matches:
                    gaslighting_events.append({
                        "speaker": speaker,
                        "text_snippet": text[:200],
                        "matched_patterns": gaslight_matches,
                    })

    manipulation_keywords = {"gaslighting", "love_bombing", "negging", "isolation"}
    detected_manipulation = list(all_patterns_found & manipulation_keywords)

    escalation_detected = False
    if len(speaker_intensities) >= 2 and max(speaker_intensities) > 0.3:
        escalation_detected = True

    overall_risk = "low"
    if detected_manipulation:
        overall_risk = "high"
    elif all_patterns_found:
        overall_risk = "medium"
    if escalation_detected and detected_manipulation:
        overall_risk = "critical"

    return {
        "manipulation_detected": bool(detected_manipulation),
        "detected_patterns": sorted(all_patterns_found),
        "speaker_analysis": speaker_analysis,
        "escalation_detected": escalation_detected,
        "gaslighting_events": gaslighting_events,
        "overall_risk": overall_risk,
    }


def assess_trustworthiness(profile: dict[str, Any]) -> dict[str, Any]:
    """Assess trustworthiness indicators from an interaction profile.

    Evaluates consistency, transparency, and behavioral signals.

    Args:
        profile: Dict with keys:
            - identity_claims: list[str] — claimed affiliations/credentials
            - communication_history: list[dict] — past interaction turns
            - consistency_checks: list[dict] — results of verification attempts
            - behavioral_signals: list[str] — observed behavioral patterns

    Returns:
        Dict with keys:
            - trust_score: float — 0.0 to 1.0
            - risk_factors: list[str]
            - transparency_index: float
            - consistency_index: float
            - recommendation: str
    """
    trust_score = 1.0
    risk_factors: list[str] = []

    identity_claims = profile.get("identity_claims", [])
    communication = profile.get("communication_history", [])
    consistency = profile.get("consistency_checks", [])
    signals = profile.get("behavioral_signals", [])

    if identity_claims:
        unverifiable = sum(1 for c in identity_claims if isinstance(c, str) and len(c.split()) <= 3)
        if unverifiable > 0:
            trust_score -= 0.15 * unverifiable
            risk_factors.append(f"{unverifiable} vaguely stated identity claims")

    if communication:
        all_text = [t.get("text", "") for t in communication if isinstance(t, dict)]
        combined = " ".join(all_text)
        persuasion = analyze_persuasion_techniques(combined)
        if persuasion["intensity_score"] > 0.3:
            trust_score -= 0.2
            risk_factors.append(f"persuasion intensity {persuasion['intensity_score']}")

        distanced = persuasion.get("matches", {}).get("distancing_language", [])
        if len(distanced) > 2:
            trust_score -= 0.15
            risk_factors.append("high distancing language usage")

    verified = sum(1 for c in consistency if c.get("verified", False))
    failed = sum(1 for c in consistency if not c.get("verified", False) and c.get("checked", False))
    total_checked = verified + failed
    consistency_index = verified / total_checked if total_checked > 0 else 0.5
    if total_checked > 0:
        trust_score += (consistency_index - 0.5) * 0.4
        if failed > 0:
            risk_factors.append(f"{failed} failed consistency checks")

    pressure_signals = [s for s in signals if "pressure" in s.lower() or "urgent" in s.lower()]
    evasion_signals = [s for s in signals if "evasive" in s.lower() or "deflect" in s.lower()]
    if pressure_signals:
        trust_score -= 0.1 * len(pressure_signals)
        risk_factors.append(f"{len(pressure_signals)} pressure signals")
    if evasion_signals:
        trust_score -= 0.1 * len(evasion_signals)
        risk_factors.append(f"{len(evasion_signals)} evasion signals")

    trust_score = max(0.0, min(1.0, trust_score))
    transparency_index = 0.5
    if identity_claims:
        specificity = sum(len(c.split()) for c in identity_claims if isinstance(c, str)) / len(identity_claims)
        transparency_index = min(1.0, specificity / 10.0)
    if total_checked > 0:
        transparency_index = (transparency_index + consistency_index) / 2

    recommendation = "Proceed with normal caution."
    if trust_score < 0.7:
        recommendation = "Exercise elevated caution. Verify key claims independently."
    if trust_score < 0.4:
        recommendation = "HIGH RISK. Do not share sensitive information. Initiate verification protocol."

    return {
        "trust_score": round(trust_score, 3),
        "risk_factors": risk_factors,
        "transparency_index": round(transparency_index, 3),
        "consistency_index": round(consistency_index, 3),
        "recommendation": recommendation,
    }


def classify_social_engineering_attack(scenario: dict[str, Any]) -> dict[str, Any]:
    """Classify a social engineering scenario by attack vector and severity.

    Args:
        scenario: Dict with keys:
            - description: str — narrative of the approach
            - channel: str — communication channel
            - requested_action: str — what the attacker wants
            - indicators: list[str] — red flags observed

    Returns:
        Dict with keys:
            - primary_attack_type: str
            - secondary_types: list[str]
            - confidence: float — 0.0 to 1.0
            - matched_indicators: list[str]
            - risk_level: str
            - recommended_protocols: list[str]
            - reasoning: str
    """
    description = scenario.get("description", "")
    channel = scenario.get("channel", "")
    requested_action = scenario.get("requested_action", "")
    observed = scenario.get("indicators", [])

    description_lower = description.lower()
    action_lower = requested_action.lower()

    vector_scores: dict[str, float] = {}

    credential_keywords = ["password", "login", "credential", "username", "pin", "ssn", "social security"]
    info_keywords = ["verify", "confirm", "update your", "validate your", "account information"]
    tech_keywords = ["technical support", "tech support", "virus", "malware", "hacked", "compromised"]
    physical_keywords = ["door", "building", "access", "badge", "entry", "escort"]
    gift_keywords = ["free", "gift card", "prize", "won", "reward", "survey"]

    if any(kw in description_lower or kw in action_lower for kw in credential_keywords):
        vector_scores["phishing"] = 0.8
    elif any(kw in description_lower for kw in tech_keywords):
        vector_scores["quid_pro_quo"] = 0.7

    if any(kw in description_lower for kw in info_keywords):
        vector_scores["pretexting"] = max(vector_scores.get("pretexting", 0.0), 0.7)

    if channel in ("physical_access", "in_person") or any(kw in description_lower for kw in physical_keywords):
        vector_scores["tailgating"] = 0.8

    if any(kw in description_lower for kw in gift_keywords):
        vector_scores["baiting"] = max(vector_scores.get("baiting", 0.0), 0.7)

    if not vector_scores:
        for vector_name, vector_data in ATTACK_VECTORS.items():
            matching = sum(1 for ind in observed if any(pat.lower() in ind.lower() for pat in vector_data["indicators"]))
            if matching > 0:
                vector_scores[vector_name] = min(1.0, matching * 0.3)

    if not vector_scores:
        vector_scores["phishing"] = 0.3

    sorted_vectors = sorted(vector_scores.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_vectors[0][0]
    confidence = sorted_vectors[0][1]
    secondary = [v[0] for v in sorted_vectors[1:3] if v[1] > 0.4]

    matched_indicators: list[str] = []
    if primary in ATTACK_VECTORS:
        for ind in ATTACK_VECTORS[primary]["indicators"]:
            if any(o.lower().find(ind.split("_")[0].lower()) >= 0 for o in observed):
                matched_indicators.append(ind)

    risk_level = "medium"
    urgency_markers = ["urgent", "immediate", "now", "critical", "limited"]
    if any(u in description_lower or u in action_lower for u in urgency_markers):
        risk_level = "high"
    if confidence > 0.7 and risk_level == "high":
        risk_level = "critical"

    recommended = []
    if risk_level in ("high", "critical"):
        recommended = ["verification_protocol", "pause_and_assess", "reporting_mechanism"]
    else:
        recommended = ["pause_and_assess"]

    reasoning_parts = [f"Primary vector '{primary}' at confidence {confidence:.2f}"]
    if secondary:
        reasoning_parts.append(f"secondary possibilities: {', '.join(secondary)}")
    reasoning_parts.append(f"{len(matched_indicators)} matched indicators")

    return {
        "primary_attack_type": primary,
        "secondary_types": secondary,
        "confidence": round(confidence, 3),
        "matched_indicators": matched_indicators,
        "risk_level": risk_level,
        "recommended_protocols": recommended,
        "reasoning": "; ".join(reasoning_parts),
    }
