"""Behavioral cue analysis module: facial expressions, deception detection,
emotional state classification.

Public surface::

    classify_emotion(expression_data)                -> dict
    assess_credibility(statement)                    -> dict
    detect_deception_indicators(transcript, notes)   -> dict

    FACIAL_ACTION_UNITS          dict[au_code] -> properties
    MICRO_EXPRESSION_TYPES       dict[type_name] -> properties
    DECEPTION_INDICATORS         dict[indicator_name] -> properties
    BASIC_EMOTIONS               dict[emotion_name] -> FACS profile
    PLUTCHIK_EMOTIONS            dict[emotion_name] -> wheel properties
    BODY_LANGUAGE_CUES           dict[cue_name] -> signal meaning
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# ---------------------------------------------------------------------------
# Facial Action Coding System (FACS) — Action Units
# ---------------------------------------------------------------------------

FACIAL_ACTION_UNITS: dict[str, dict[str, Any]] = {
    "AU1": {
        "name": "Inner Brow Raiser",
        "muscle": "Frontalis (pars medialis)",
        "description": "Raises the inner portion of the eyebrows",
        "associated_emotions": ["sadness", "surprise", "fear"],
        "intensity_range": "A-E (trace to maximum)",
    },
    "AU2": {
        "name": "Outer Brow Raiser",
        "muscle": "Frontalis (pars lateralis)",
        "description": "Raises the outer portion of the eyebrows",
        "associated_emotions": ["surprise", "attention"],
    },
    "AU4": {
        "name": "Brow Lowerer",
        "muscle": "Depressor glabellae, Depressor supercilii, Corrugator supercilii",
        "description": "Lowers and draws together the eyebrows",
        "associated_emotions": ["anger", "concentration", "confusion", "fear"],
    },
    "AU5": {
        "name": "Upper Lid Raiser",
        "muscle": "Levator palpebrae superioris",
        "description": "Raises the upper eyelid",
        "associated_emotions": ["surprise", "fear", "anger"],
    },
    "AU6": {
        "name": "Cheek Raiser",
        "muscle": "Orbicularis oculi (pars orbitalis)",
        "description": "Raises the cheeks, narrows the eye aperture",
        "associated_emotions": ["happiness", "genuine_smile_marker"],
        "note": "Duchenne marker — distinguishes genuine from social smiles when co-occurring with AU12",
    },
    "AU7": {
        "name": "Lid Tightener",
        "muscle": "Orbicularis oculi (pars palpebralis)",
        "description": "Tightens the eyelids",
        "associated_emotions": ["anger", "disgust", "concentration"],
    },
    "AU9": {
        "name": "Nose Wrinkler",
        "muscle": "Levator labii superioris alaeque nasi",
        "description": "Wrinkles the nose, pulls skin upward along sides of nose",
        "associated_emotions": ["disgust"],
    },
    "AU10": {
        "name": "Upper Lip Raiser",
        "muscle": "Levator labii superioris",
        "description": "Raises the upper lip",
        "associated_emotions": ["disgust", "contempt"],
    },
    "AU11": {
        "name": "Nasolabial Deepener",
        "muscle": "Zygomaticus minor",
        "description": "Deepens the nasolabial fold",
        "associated_emotions": ["sadness"],
    },
    "AU12": {
        "name": "Lip Corner Puller",
        "muscle": "Zygomaticus major",
        "description": "Pulls the lip corners up and laterally",
        "associated_emotions": ["happiness"],
    },
    "AU14": {
        "name": "Dimpler",
        "muscle": "Buccinator",
        "description": "Tightens the lip corners, producing dimples",
        "associated_emotions": ["contempt", "disgust"],
    },
    "AU15": {
        "name": "Lip Corner Depressor",
        "muscle": "Depressor anguli oris",
        "description": "Pulls lip corners downward",
        "associated_emotions": ["sadness", "disgust"],
    },
    "AU17": {
        "name": "Chin Raiser",
        "muscle": "Mentalis",
        "description": "Pushes chin boss upward, pushes lower lip upward",
        "associated_emotions": ["sadness", "doubt", "pouting"],
    },
    "AU20": {
        "name": "Lip Stretcher",
        "muscle": "Risorius with platysma",
        "description": "Stretches lips laterally",
        "associated_emotions": ["fear"],
    },
    "AU23": {
        "name": "Lip Tightener",
        "muscle": "Orbicularis oris",
        "description": "Tightens and narrows the lips",
        "associated_emotions": ["anger", "determination"],
    },
    "AU24": {
        "name": "Lip Pressor",
        "muscle": "Orbicularis oris",
        "description": "Presses lips together",
        "associated_emotions": ["anger", "suppression", "frustration"],
    },
    "AU25": {
        "name": "Lips Part",
        "muscle": "Depressor labii inferioris, relaxation of mentalis/orbicularis oris",
        "description": "Parts lips",
        "associated_emotions": ["surprise", "relaxation"],
    },
    "AU26": {
        "name": "Jaw Drop",
        "muscle": "Masseter (relaxed), temporalis, pterygoids",
        "description": "Drops the jaw",
        "associated_emotions": ["surprise"],
    },
}

# ---------------------------------------------------------------------------
# Micro-Expression Types
# ---------------------------------------------------------------------------

MICRO_EXPRESSION_TYPES: dict[str, dict[str, Any]] = {
    "simulated": {
        "description": "Deliberately produced expression; not a genuine emotional response",
        "duration": "variable (can be held)",
        "symmetry": "typically asymmetric (left face ≠ right face)",
        "onset_offset": "abrupt onset and offset; can be held arbitrarily",
        "detection_challenge": "may look genuine to untrained observers",
    },
    "neutralized": {
        "description": "Genuine expression is suppressed and face is held neutral",
        "duration": "varies; expression leaks may appear as micro-expressions",
        "indicators": "sudden neutralization following emotional stimulus; residual muscle tone",
        "detection_challenge": "requires baseline comparison and stimulus timing analysis",
    },
    "masked": {
        "description": "Genuine expression is covered by a different expression",
        "duration": "varies",
        "indicators": "incongruent facial actions; e.g., smile masking anger shows AU12 without AU6",
        "detection_challenge": "Duchenne smile test: genuine happiness = AU6 + AU12; social smile = AU12 only",
    },
    "micro_expression": {
        "description": "Very brief (<500ms) involuntary facial expression revealing concealed emotion",
        "duration": "1/25 to 1/5 second (40-200ms)",
        "symmetry": "typically symmetrical (involuntary)",
        "detection": "requires frame-by-frame analysis or trained observer; METT/SETT training programs",
        "paul_ekman_research": "Ekman & Friesen (1969): micro-expressions reveal concealed emotions when the expresser attempts to hide their true feelings",
    },
}

# ---------------------------------------------------------------------------
# Basic Emotions (Ekman's six + extensions)
# ---------------------------------------------------------------------------

BASIC_EMOTIONS: dict[str, dict[str, Any]] = {
    "happiness": {
        "prototypical_AUs": ["AU6", "AU12"],
        "variant_AUs": ["AU6+12+25"],
        "physiological_markers": ["decreased_heart_rate", "zygomatic_activation"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["goal_attainment", "social_connection", "sensory_pleasure"],
    },
    "sadness": {
        "prototypical_AUs": ["AU1", "AU4", "AU15"],
        "variant_AUs": ["AU1+4+15+17", "AU1+4+11"],
        "physiological_markers": ["increased_heart_rate", "crying", "elevated_cortisol"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["loss", "helplessness", "separation"],
    },
    "anger": {
        "prototypical_AUs": ["AU4", "AU5", "AU7", "AU23"],
        "variant_AUs": ["AU4+5+7+23", "AU4+5+17+24"],
        "physiological_markers": ["increased_heart_rate", "increased_skin_conductance", "elevated_testosterone"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["goal_blockage", "injustice", "frustration"],
    },
    "fear": {
        "prototypical_AUs": ["AU1", "AU2", "AU4", "AU5", "AU7", "AU20", "AU26"],
        "physiological_markers": ["increased_heart_rate", "pupil_dilation", "amygdala_activation"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["threat", "danger", "uncertainty"],
    },
    "disgust": {
        "prototypical_AUs": ["AU9", "AU10"],
        "variant_AUs": ["AU9+10+15+17", "AU9+10"],
        "physiological_markers": ["decreased_heart_rate", "nausea_response", "anterior_insula_activation"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["contamination", "moral_violation", "bodily_products"],
    },
    "surprise": {
        "prototypical_AUs": ["AU1", "AU2", "AU5", "AU26"],
        "variant_AUs": ["AU1+2+5+25+26", "AU1+2+5"],
        "physiological_markers": ["orienting_response", "pupil_dilation", "skin_conductance_increase"],
        "cross_cultural_universality": True,
        "typical_elicitors": ["unexpected_event", "novelty", "sudden_change"],
        "note": "Briefest of the basic emotions; quickly transitions to fear, anger, or relief depending on appraisal",
    },
    "contempt": {
        "prototypical_AUs": ["AU12", "AU14"],
        "variant_AUs": ["unilateral_AU12+14"],
        "physiological_markers": ["asymmetric_facial_activation"],
        "cross_cultural_universality": "debated — recognized across cultures but not universally displayed",
        "typical_elicitors": ["moral_superiority", "social_comparison", "disrespect"],
        "note": "Only basic emotion expressed asymmetrically (unilateral)",
    },
}

# ---------------------------------------------------------------------------
# Plutchik's Wheel of Emotions
# ---------------------------------------------------------------------------

PLUTCHIK_EMOTIONS: dict[str, dict[str, Any]] = {
    "joy": {
        "opposite": "sadness",
        "intensity_variants": {"low": "serenity", "moderate": "joy", "high": "ecstasy"},
        "adaptive_function": "reproduction — signals safety, invites social bonding",
        "wheel_position": "primary (petal 1)",
    },
    "trust": {
        "opposite": "disgust",
        "intensity_variants": {"low": "acceptance", "moderate": "trust", "high": "admiration"},
        "adaptive_function": "incorporation — accepting nourishment, forming alliances",
        "wheel_position": "primary (petal 2)",
    },
    "fear": {
        "opposite": "anger",
        "intensity_variants": {"low": "apprehension", "moderate": "fear", "high": "terror"},
        "adaptive_function": "protection — withdrawal from threat",
        "wheel_position": "primary (petal 3)",
    },
    "surprise": {
        "opposite": "anticipation",
        "intensity_variants": {"low": "distraction", "moderate": "surprise", "high": "amazement"},
        "adaptive_function": "orientation — attending to the unexpected",
        "wheel_position": "primary (petal 4)",
    },
    "sadness": {
        "opposite": "joy",
        "intensity_variants": {"low": "pensiveness", "moderate": "sadness", "high": "grief"},
        "adaptive_function": "reintegration — signaling need for comfort, conserving energy after loss",
        "wheel_position": "primary (petal 5)",
    },
    "disgust": {
        "opposite": "trust",
        "intensity_variants": {"low": "boredom", "moderate": "disgust", "high": "loathing"},
        "adaptive_function": "rejection — expelling harmful substances, avoiding contamination",
        "wheel_position": "primary (petal 6)",
    },
    "anger": {
        "opposite": "fear",
        "intensity_variants": {"low": "annoyance", "moderate": "anger", "high": "rage"},
        "adaptive_function": "destruction — overcoming obstacles, defending territory",
        "wheel_position": "primary (petal 7)",
    },
    "anticipation": {
        "opposite": "surprise",
        "intensity_variants": {"low": "interest", "moderate": "anticipation", "high": "vigilance"},
        "adaptive_function": "exploration — mapping the environment, planning",
        "wheel_position": "primary (petal 8)",
    },
}

PLUTCHIK_DYADS: dict[str, list[str]] = {
    "love": ["joy", "trust"],
    "submission": ["trust", "fear"],
    "awe": ["fear", "surprise"],
    "disapproval": ["surprise", "sadness"],
    "remorse": ["sadness", "disgust"],
    "contempt": ["disgust", "anger"],
    "aggression": ["anger", "anticipation"],
    "optimism": ["anticipation", "joy"],
}


# ---------------------------------------------------------------------------
# Deception Indicators
# ---------------------------------------------------------------------------

DECEPTION_INDICATORS: dict[str, dict[str, Any]] = {
    "verbal_equivocation": {
        "category": "verbal",
        "description": "Use of ambiguous, non-committal, or evasive language",
        "indicators": [
            "hedging_phrases",
            "qualifying_statements",
            "vague_references",
            "passive_voice_constructions",
        ],
        "research_reference": "Vrij (2008): deceivers use fewer first-person pronouns and more negative emotion words",
    },
    "distancing_language": {
        "category": "verbal",
        "description": "Linguistic patterns that reduce personal connection to the statement",
        "indicators": [
            "reduced_i_words",
            "increased_passive_voice",
            "third_person_self_reference",
            "impersonal_constructions",
        ],
        "liwc_correlation": "Lower I-word count correlates with deception in multiple studies",
    },
    "detail_sparseness": {
        "category": "verbal",
        "description": "Statements lacking specific, verifiable details",
        "indicators": [
            "missing_temporal_specificity",
            "absent_sensory_details",
            "generic_characters_without_names",
            "vague_locations",
        ],
        "criterion_based_content_analysis": "CBCA criteria include quantity of details as truthfulness indicator",
    },
    "narrative_inconsistency": {
        "category": "verbal",
        "description": "Contradictions within or between statements",
        "indicators": [
            "contradictory_timelines",
            "logically_incompatible_details",
            "changing_sequence_of_events",
            "details_appearing_in_later_retellings_that_were_absent_earlier",
        ],
    },
    "self_touching": {
        "category": "non_verbal",
        "description": "Increased self-manipulation: touching face, hair, clothing",
        "indicators": [
            "face_touching",
            "hair_stroking",
            "clothing_adjustment",
            "hand_wringing",
            "object_fidgeting",
        ],
        "note": "Context-dependent; baseline comparison essential — not universally correlated with deception",
    },
    "barrier_behaviors": {
        "category": "non_verbal",
        "description": "Creating physical barriers between self and interlocutor",
        "indicators": [
            "crossed_arms",
            "placing_object_between_self_and_other",
            "turning_body_away",
            "increased_interpersonal_distance",
            "using_clothing_as_shield",
        ],
    },
    "eye_behavior_changes": {
        "category": "non_verbal",
        "description": "Deviations from baseline eye contact and blinking patterns",
        "indicators": [
            "increased_blink_rate",
            "gaze_aversion_during_critical_questions",
            "excessive_eye_contact_as_overcompensation",
            "pupil_dilation",
            "eye_blocking",
        ],
        "myth_busting": "Gaze aversion alone is NOT a reliable deception indicator; baseline comparison is critical",
    },
    "illustrator_reduction": {
        "category": "non_verbal",
        "description": "Decreased use of hand gestures that accompany speech",
        "indicators": [
            "hands_becoming_still",
            "reduced_gesturing_during_critical_portions",
            "gestures_mismatched_with_verbal_content",
        ],
        "mechanism": "Cognitive load hypothesis: deception requires more mental resources, reducing spontaneous gesturing",
    },
    "response_latency_changes": {
        "category": "paraverbal",
        "description": "Unusual timing in responses to questions",
        "indicators": [
            "increased_latency_on_critical_questions",
            "unnaturally_short_latency_on_prepared_answers",
            "response_time_inconsistency",
        ],
    },
    "pitch_changes": {
        "category": "paraverbal",
        "description": "Fundamental frequency deviations from baseline",
        "indicators": [
            "elevated_voice_pitch",
            "pitch_instability",
            "increase_in_speech_errors_and_disfluencies",
        ],
        "mechanism": "Stress-induced vocal cord tension raises fundamental frequency",
    },
    "micro_expression_leakage": {
        "category": "facial",
        "description": "Brief involuntary facial expressions revealing concealed emotions",
        "indicators": [
            "fear_micro_before_neutral_or_smiling_expression",
            "disgust_flicker",
            "contempt_asymmetry",
            "sadness_brief_appearance_and_suppression",
        ],
        "detection_requirement": "Frame-by-frame video analysis or METT-trained observer",
    },
}

# ---------------------------------------------------------------------------
# Body Language Cues
# ---------------------------------------------------------------------------

BODY_LANGUAGE_CUES: dict[str, dict[str, Any]] = {
    "pupil_dilation": {
        "category": "autonomic",
        "signals": ["increased_interest", "cognitive_load", "arousal", "attraction"],
        "controllability": "involuntary — controlled by autonomic nervous system",
        "measurement_method": "pupillometry; visible to ~0.1mm precision",
    },
    "blink_rate": {
        "category": "autonomic",
        "baseline": "15-20 blinks per minute in neutral state",
        "signal_increase": "stress, anxiety, cognitive load, deception preparation",
        "signal_decrease": "concentration, visual attention, threat assessment",
        "controllability": "partially controllable but difficult to sustain",
    },
    "voice_stress": {
        "category": "paraverbal",
        "measured_by": "fundamental frequency (F0), jitter, shimmer, micro-tremors",
        "signal_increase": "stress, anxiety, deception, cognitive load",
        "controllability": "partially controllable with training; basal shifts persist under stress",
        "note": "Voice stress analysis (VSA) reliability is debated; polygraph (physiological channels) shows higher accuracy",
    },
    "posture": {
        "open_posture": ["uncrossed_limbs", "torso_exposed", "palms_visible", "leaning_forward"],
        "closed_posture": ["crossed_arms", "crossed_legs", "torso_angled_away", "hunched_shoulders"],
        "implication": "Open posture correlates with comfort, confidence, and receptivity; closed posture with discomfort, defensiveness, or threat assessment",
    },
    "mirroring": {
        "description": "Unconscious imitation of interlocutor's posture, gestures, and speech patterns",
        "signal": "rapport, liking, agreement, affiliation",
        "deliberate_mirroring": "Used in sales and negotiation to build rapport intentionally",
        "detection": "Deliberate mirroring at natural latency (2-4 seconds) vs. immediate mimicry which signals insincerity",
    },
    "feet_direction": {
        "description": "Feet point toward where a person wants to go",
        "signal_toward": "interest, engagement, desire to approach",
        "signal_away_or_blocked": "disengagement, desire to leave, discomfort",
        "reliability": "High — feet are less consciously controlled than face or hands",
    },
    "lip_compression": {
        "description": "Pressing lips together tightly or pulling them inward",
        "signal": "suppression of speech, holding back, negative emotion processing",
        "associated_states": ["anger_suppression", "disagreement", "withholding_information", "stress"],
    },
    "chin_withdrawal": {
        "description": "Pulling chin back and down toward the neck",
        "signal": "defensiveness, threat perception, disagreement",
        "scenario": "Often seen when receiving critical feedback or perceiving attack",
    },
}

# ---------------------------------------------------------------------------
# Dimensional Emotion Models
# ---------------------------------------------------------------------------

DIMENSIONAL_MODEL: dict[str, dict[str, Any]] = {
    "valence_arousal_dominance": {
        "axes": ["valence (pleasant-unpleasant)", "arousal (activated-deactivated)", "dominance (in_control-overwhelmed)"],
        "reference": "Russell (1980) circumplex model; Mehrabian & Russell (1974) PAD model",
        "mapping": {
            "happiness": {"valence": 0.81, "arousal": 0.51, "dominance": 0.69},
            "sadness": {"valence": -0.63, "arousal": -0.27, "dominance": -0.33},
            "anger": {"valence": -0.51, "arousal": 0.59, "dominance": 0.42},
            "fear": {"valence": -0.64, "arousal": 0.60, "dominance": -0.43},
            "disgust": {"valence": -0.60, "arousal": 0.35, "dominance": 0.11},
            "surprise": {"valence": 0.40, "arousal": 0.67, "dominance": -0.13},
        },
    },
}

# ---------------------------------------------------------------------------
# Analysis Functions
# ---------------------------------------------------------------------------

def classify_emotion(expression_data: dict[str, Any]) -> dict[str, Any]:
    """Classify emotion from FACS action unit data and contextual signals.

    Args:
        expression_data: Dict with keys:
            - action_units: list[str] — FACS AU codes observed (e.g. ["AU6", "AU12"])
            - intensity: str — overall expression intensity "A" through "E"
            - duration_ms: int — expression duration in milliseconds
            - symmetry: str — "symmetric", "asymmetric_left_dominant", "asymmetric_right_dominant"
            - context: str — situational context (optional)

    Returns:
        Dict with keys:
            - primary_emotion: str | None
            - secondary_emotions: list[str]
            - confidence: float
            - expression_type: str — genuine, social, masked, or neutralized
            - dimensional_values: dict — VAD coordinates
            - note: str — interpretive note
    """
    action_units = expression_data.get("action_units", [])
    intensity = expression_data.get("intensity", "B")
    duration_ms = expression_data.get("duration_ms", 0)
    symmetry = expression_data.get("symmetry", "symmetric")

    au_set = set(action_units)

    emotion_scores: dict[str, float] = {}
    for emotion, data in BASIC_EMOTIONS.items():
        prototypical = set(data.get("prototypical_AUs", []))
        variants = [set(v.split("+")) for v in data.get("variant_AUs", [])]
        if not prototypical and not variants:
            continue
        best_match = 0.0
        if prototypical:
            overlap = len(au_set & prototypical)
            total = len(prototypical)
            best_match = max(best_match, overlap / total if total > 0 else 0.0)
        for variant_set in variants:
            if variant_set:
                overlap = len(au_set & variant_set)
                total = len(variant_set)
                best_match = max(best_match, overlap / total if total > 0 else 0.0)
        if best_match > 0:
            emotion_scores[emotion] = best_match

    sorted_emotions = sorted(emotion_scores.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_emotions[0][0] if sorted_emotions else None
    confidence = sorted_emotions[0][1] if sorted_emotions else 0.0
    secondary = [e[0] for e in sorted_emotions[1:3] if e[1] > 0.3]

    expression_type = "genuine"
    if duration_ms > 5000:
        expression_type = "social_or_posed"
    if symmetry.startswith("asymmetric") and primary not in ("contempt",):
        expression_type = "social_or_posed"

    au6_present = "AU6" in au_set
    au12_present = "AU12" in au_set
    if au12_present and not au6_present and primary == "happiness":
        expression_type = "social_smile"
    if au12_present and au6_present and primary == "happiness":
        expression_type = "duchenne_genuine"

    if duration_ms < 500:
        expression_type = "micro_expression"

    vad = {
        "valence": 0.0,
        "arousal": 0.0,
        "dominance": 0.0,
    }
    if primary and primary in DIMENSIONAL_MODEL["valence_arousal_dominance"].get("mapping", {}):
        vad = dict(DIMENSIONAL_MODEL["valence_arousal_dominance"]["mapping"][primary])

    note = ""
    if confidence < 0.5:
        note = "Low confidence; insufficient or ambiguous AU data. Consider additional channels."
    elif expression_type == "micro_expression":
        note = "Micro-expression detected — may indicate concealed emotion. Consider frame-by-frame review."
    elif expression_type == "social_smile":
        note = "Social smile without Duchenne marker (AU6). Likely deliberate rather than spontaneous enjoyment."
    elif expression_type == "duchenne_genuine":
        note = "Duchenne smile (AU6+AU12) indicates genuine positive affect."

    return {
        "primary_emotion": primary,
        "secondary_emotions": secondary,
        "confidence": round(confidence, 3),
        "expression_type": expression_type,
        "dimensional_values": vad,
        "note": note,
    }


def assess_credibility(statement: dict[str, Any]) -> dict[str, Any]:
    """Assess the credibility of a verbal statement against CBCA criteria.

    Uses elements of Criteria-Based Content Analysis (CBCA) to evaluate
    statement veracity.

    Args:
        statement: Dict with keys:
            - text: str — the statement text
            - speaker: str — identifier for the speaker
            - delivery_context: str — free_recall / interview / interrogatory

    Returns:
        Dict with keys:
            - credibility_index: float — 0.0 to 1.0
            - criteia_met: list[str] — CBCA criteria satisfied
            - criteia_absent: list[str] — CBCA criteria not observed
            - risk_flags: list[str] — deception indicators
            - recommendation: str
    """
    text = statement.get("text", "")
    speaker = statement.get("speaker", "unknown")

    criteria_met: list[str] = []
    risk_flags: list[str] = []

    criteria_checks = [
        ("logical_structure", lambda t: bool(re.search(r"\b(because|therefore|so|as a result|since|due to)\b", t, re.IGNORECASE))),
        ("unstructured_production", lambda t: not bool(re.search(
            r"^(firstly|secondly|thirdly|step \d|finally|in conclusion)", t, re.IGNORECASE | re.MULTILINE
        ))),
        ("quantity_of_details", lambda t: len(t.split()) > 100),
        ("contextual_embedding", lambda t: bool(re.search(
            r"\b(place|location|time|when|where|spatial|temporal)\b", t, re.IGNORECASE
        )) or bool(re.search(r"\b(monday|tuesday|january|morning|afternoon|evening)\b", t, re.IGNORECASE))),
        ("interactions_description", lambda t: bool(re.search(
            r"\b(she said|he told|they asked|i replied|i said to|responded)\b", t, re.IGNORECASE
        ))),
        ("speech_reproduction", lambda t: bool(re.search(r'"[^"]{5,}"', t))),
        ("unexpected_complications", lambda t: bool(re.search(
            r"\b(but|however|unexpectedly|surprisingly|turned out|unfortunately)\b", t, re.IGNORECASE
        )) or bool(re.search(r"\b(something went wrong|didn't work|failed|broke|stopped)\b", t, re.IGNORECASE))),
        ("superfluous_details", lambda t: len(t.split()) > 200 and bool(re.search(
            r"\b(colou?r|smell|taste|sound|felt like|seemed|looked like)\b", t, re.IGNORECASE
        ))),
        ("self_deprecation", lambda t: bool(re.search(
            r"\b(i was wrong|my mistake|i should have|i forgot|i didn't know)\b", t, re.IGNORECASE
        ))),
        ("pardoning_perpetrator", lambda t: False),  # requires semantic analysis beyond regex
    ]

    for criterion_name, check_fn in criteria_checks:
        if check_fn(text):
            criteria_met.append(criterion_name)

    deception_checks = [
        ("distancing_language", lambda t: bool(re.search(r"\b(it was decided|mistakes were made|allegedly)\b", t, re.IGNORECASE))),
        ("low_first_person_pronouns", lambda t: _pronoun_ratio(t) < 0.03),
        ("generic_references", lambda t: bool(re.search(r"\b(someone|they say|people|everybody knows)\b", t, re.IGNORECASE))),
        ("absent_temporal_specificity", lambda t: not bool(re.search(r"\b(\d{1,2}[:\s]?\d{2}|around \d|approximately \d)\b", t))),
    ]

    for flag_name, check_fn in deception_checks:
        if check_fn(text):
            risk_flags.append(flag_name)

    credibility_index = len(criteria_met) / len(criteria_checks)
    credibility_index = round(min(1.0, credibility_index - 0.2 * len(risk_flags)), 3)
    credibility_index = max(0.0, credibility_index)

    recommendation = "Statement appears adequately detailed."
    if credibility_index < 0.4:
        recommendation = "Low credibility indicators. Further verification strongly recommended."
    elif credibility_index < 0.6:
        recommendation = "Moderate credibility. Corroborate key details with independent sources."
    elif risk_flags:
        recommendation = "Credibility adequate but deception indicators present. Verify specific claims."

    criteria_absent = [c[0] for c in criteria_checks if c[0] not in criteria_met]

    return {
        "credibility_index": round(credibility_index, 3),
        "criteria_met": sorted(criteria_met),
        "criteria_absent": sorted(criteria_absent),
        "risk_flags": risk_flags,
        "recommendation": recommendation,
    }


def _pronoun_ratio(text: str) -> float:
    """Compute I/me/my pronoun density."""
    words = text.split()
    if not words:
        return 0.0
    i_words = sum(1 for w in words if w.lower() in ("i", "me", "my", "mine", "myself", "i'm", "i've", "i'll", "i'd"))
    return i_words / len(words)


def detect_deception_indicators(
    transcript: list[dict[str, Any]],
    behavioral_notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect deception indicators from transcript and behavioral observation data.

    Args:
        transcript: List of dicts with 'speaker', 'text', and optional 'timestamp'.
        behavioral_notes: Optional dict with observed non-verbal behaviors.

    Returns:
        Dict with keys:
            - deception_probability: float — 0.0 to 1.0
            - detected_indicators: list[str]
            - indicator_categories: dict[str, list[str]]
            - speaker_analysis: dict[str, dict]
            - recommendation: str
    """
    behavioral_notes = behavioral_notes or {}
    detected: list[str] = []
    categories: dict[str, list[str]] = {"verbal": [], "non_verbal": [], "paraverbal": [], "facial": []}

    combined_text = " ".join(t.get("text", "") for t in transcript)
    text_lower = combined_text.lower()

    if re.search(r"\b(it was decided|mistakes were made|errors were committed)\b", text_lower, re.IGNORECASE):
        detected.append("distancing_language")
        categories["verbal"].append("distancing_language")
    if re.search(r"\b(allegedly|reportedly|supposedly|apparently)\b", text_lower, re.IGNORECASE):
        detected.append("verbal_equivocation")
        categories["verbal"].append("verbal_equivocation")

    words = combined_text.split()
    if words:
        i_ratio = sum(1 for w in words if w.lower() in ("i", "me", "my", "mine", "myself")) / len(words)
        if i_ratio < 0.02:
            detected.append("low_first_person_pronouns")
            categories["verbal"].append("low_first_person_pronouns")

    if len(words) < 50:
        detected.append("detail_sparseness")
        categories["verbal"].append("detail_sparseness")
    if not re.search(r"\b(\d{1,2}:\d{2}|january|february|march|monday|tuesday|morning|afternoon|evening)\b", text_lower, re.IGNORECASE):
        detected.append("missing_temporal_specificity")
        categories["verbal"].append("missing_temporal_specificity")

    behavioral = behavioral_notes
    if behavioral.get("self_touching"):
        detected.append("self_touching")
        categories["non_verbal"].append("self_touching")
    if behavioral.get("barrier_behaviors"):
        detected.append("barrier_behaviors")
        categories["non_verbal"].append("barrier_behaviors")
    if behavioral.get("illustrator_reduction"):
        detected.append("illustrator_reduction")
        categories["non_verbal"].append("illustrator_reduction")
    if behavioral.get("response_latency_increased"):
        detected.append("response_latency_changes")
        categories["paraverbal"].append("response_latency_changes")
    if behavioral.get("pitch_increased"):
        detected.append("pitch_changes")
        categories["paraverbal"].append("pitch_changes")
    if behavioral.get("micro_expressions_observed"):
        detected.append("micro_expression_leakage")
        categories["facial"].append("micro_expression_leakage")

    non_empty_categories = {k: v for k, v in categories.items() if v}

    probability = min(1.0, len(detected) * 0.15)
    for cat, items in non_empty_categories.items():
        if len(items) >= 2:
            probability = min(1.0, probability + 0.1)

    speaker_analysis: dict[str, dict[str, Any]] = {}
    for turn in transcript:
        speaker = turn.get("speaker", "unknown")
        text = turn.get("text", "")
        if speaker not in speaker_analysis:
            speaker_analysis[speaker] = {
                "turns": 0,
                "total_words": 0,
                "distancing_markers": 0,
                "equivocation_markers": 0,
            }
        sa = speaker_analysis[speaker]
        sa["turns"] += 1
        sa["total_words"] += len(text.split())
        if re.search(r"\b(it was decided|mistakes were made)\b", text, re.IGNORECASE):
            sa["distancing_markers"] += 1
        if re.search(r"\b(allegedly|reportedly|supposedly)\b", text, re.IGNORECASE):
            sa["equivocation_markers"] += 1

    recommendation = "No significant deception indicators detected."
    if probability > 0.3:
        recommendation = "Moderate deception indicators. Cross-reference with physiological channels."
    if probability > 0.5:
        recommendation = "Elevated deception indicators. Comprehensive assessment recommended. Do not rely on this single channel."
    if probability > 0.7:
        recommendation = "Strong deception indicators across multiple channels. Treat statements as high-risk."

    return {
        "deception_probability": round(probability, 3),
        "detected_indicators": detected,
        "indicator_categories": non_empty_categories,
        "speaker_analysis": speaker_analysis,
        "recommendation": recommendation,
    }
