"""Tests for behavioral knowledge modules: social_engineering, behavioral_cues, animal_behavior."""

from __future__ import annotations

import importlib
import os
import sys

_COLLECTION_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "collections",
        "ansible_collections",
        "general_ludd",
        "behavioral",
        "plugins",
        "module_utils",
    )
)
if _COLLECTION_DIR not in sys.path:
    sys.path.insert(0, _COLLECTION_DIR)

social_engineering = importlib.import_module("social_engineering")
behavioral_cues = importlib.import_module("behavioral_cues")
animal_behavior = importlib.import_module("animal_behavior")

# social_engineering imports
ATTACK_VECTORS = social_engineering.ATTACK_VECTORS
PSYCHOLOGICAL_PRINCIPLES = social_engineering.PSYCHOLOGICAL_PRINCIPLES
MANIPULATION_TECHNIQUES = social_engineering.MANIPULATION_TECHNIQUES
DETECTION_MARKERS = social_engineering.DETECTION_MARKERS
DEFENSE_PROTOCOLS = social_engineering.DEFENSE_PROTOCOLS
analyze_persuasion_techniques = social_engineering.analyze_persuasion_techniques
detect_manipulation_patterns = social_engineering.detect_manipulation_patterns
assess_trustworthiness = social_engineering.assess_trustworthiness
classify_social_engineering_attack = social_engineering.classify_social_engineering_attack

# behavioral_cues imports
FACIAL_ACTION_UNITS = behavioral_cues.FACIAL_ACTION_UNITS
MICRO_EXPRESSION_TYPES = behavioral_cues.MICRO_EXPRESSION_TYPES
DECEPTION_INDICATORS = behavioral_cues.DECEPTION_INDICATORS
BASIC_EMOTIONS = behavioral_cues.BASIC_EMOTIONS
PLUTCHIK_EMOTIONS = behavioral_cues.PLUTCHIK_EMOTIONS
BODY_LANGUAGE_CUES = behavioral_cues.BODY_LANGUAGE_CUES
classify_emotion = behavioral_cues.classify_emotion
assess_credibility = behavioral_cues.assess_credibility
detect_deception_indicators = behavioral_cues.detect_deception_indicators

# animal_behavior imports
FIXED_ACTION_PATTERNS = animal_behavior.FIXED_ACTION_PATTERNS
ANIMAL_COMMUNICATION_MODES = animal_behavior.ANIMAL_COMMUNICATION_MODES
STRESS_INDICATORS = animal_behavior.STRESS_INDICATORS
SOCIAL_STRUCTURES = animal_behavior.SOCIAL_STRUCTURES
TRAINING_METHODS = animal_behavior.TRAINING_METHODS
ANIMAL_LANGUAGE_RESEARCH = animal_behavior.ANIMAL_LANGUAGE_RESEARCH
classify_behavior = animal_behavior.classify_behavior
interpret_vocalization = animal_behavior.interpret_vocalization
recommend_training_approach = animal_behavior.recommend_training_approach


# ============================================================================
# social_engineering — data integrity
# ============================================================================

class TestAttackVectors:
    def test_all_five_vectors_present(self):
        expected = {"phishing", "pretexting", "baiting", "tailgating", "quid_pro_quo"}
        assert set(ATTACK_VECTORS) == expected

    def test_phishing_has_all_required_fields(self):
        required = {"description", "subtypes", "channels", "indicators", "success_rate_range", "mitigation"}
        assert set(ATTACK_VECTORS["phishing"]) >= required

    def test_pretexting_has_all_required_fields(self):
        required = {"description", "subtypes", "channels", "indicators", "success_rate_range", "mitigation"}
        assert set(ATTACK_VECTORS["pretexting"]) >= required

    def test_baiting_has_all_required_fields(self):
        required = {"description", "subtypes", "channels", "indicators", "success_rate_range", "mitigation"}
        assert set(ATTACK_VECTORS["baiting"]) >= required

    def test_tailgating_has_all_required_fields(self):
        required = {"description", "subtypes", "channels", "indicators", "success_rate_range", "mitigation"}
        assert set(ATTACK_VECTORS["tailgating"]) >= required

    def test_quid_pro_quo_has_all_required_fields(self):
        required = {"description", "subtypes", "channels", "indicators", "success_rate_range", "mitigation"}
        assert set(ATTACK_VECTORS["quid_pro_quo"]) >= required

    def test_every_vector_has_nonempty_indicators(self):
        for name, data in ATTACK_VECTORS.items():
            assert len(data["indicators"]) > 0, f"{name} has empty indicators"

    def test_every_vector_has_nonempty_mitigation(self):
        for name, data in ATTACK_VECTORS.items():
            assert len(data["mitigation"]) > 0, f"{name} has empty mitigation"


class TestPsychologicalPrinciples:
    def test_cialdini_six_present(self):
        core = {"authority", "scarcity", "social_proof", "liking", "reciprocity", "commitment_and_consistency"}
        present = set(PSYCHOLOGICAL_PRINCIPLES)
        assert present >= core

    def test_urgency_present(self):
        assert "urgency" in PSYCHOLOGICAL_PRINCIPLES

    def test_all_principles_have_linguistic_markers(self):
        for name, data in PSYCHOLOGICAL_PRINCIPLES.items():
            assert len(data["linguistic_markers"]) > 0, f"{name} missing linguistic_markers"

    def test_all_principles_have_exploitation_methods(self):
        for name, data in PSYCHOLOGICAL_PRINCIPLES.items():
            assert len(data["exploitation_methods"]) > 0, f"{name} missing exploitation_methods"


class TestManipulationTechniques:
    def test_eight_techniques_present(self):
        expected = {
            "gaslighting", "love_bombing", "negging", "isolation",
            "intermittent_reinforcement", "projection", "triangulation",
            "catastrophizing",
        }
        assert set(MANIPULATION_TECHNIQUES) == expected

    def test_all_techniques_have_linguistic_markers(self):
        for name, data in MANIPULATION_TECHNIQUES.items():
            assert len(data["linguistic_markers"]) > 0, f"{name} missing linguistic_markers"

    def test_all_techniques_have_tactics(self):
        for name, data in MANIPULATION_TECHNIQUES.items():
            assert len(data["tactics"]) > 0, f"{name} missing tactics"


class TestDetectionMarkers:
    def test_six_detection_markers_present(self):
        expected = {
            "linguistic_inconsistency", "pronoun_avoidance",
            "pressure_tactics", "emotional_manipulation_markers",
            "authority_overclaiming", "narrative_coercion",
        }
        assert set(DETECTION_MARKERS) == expected

    def test_all_markers_have_indicators(self):
        for name, data in DETECTION_MARKERS.items():
            assert len(data["indicators"]) > 0, f"{name} missing indicators"


class TestDefenseProtocols:
    def test_five_protocols_present(self):
        expected = {
            "verification_protocol", "pause_and_assess",
            "security_awareness_training", "reporting_mechanism",
            "information_classification",
        }
        assert set(DEFENSE_PROTOCOLS) == expected

    def test_all_protocols_have_steps_or_components(self):
        for name, data in DEFENSE_PROTOCOLS.items():
            has_steps = len(data.get("steps", [])) > 0
            has_components = len(data.get("components", [])) > 0
            assert has_steps or has_components, f"{name} missing steps/components"


# ============================================================================
# social_engineering — functions
# ============================================================================

class TestAnalyzePersuasionTechniques:
    def test_empty_text(self):
        result = analyze_persuasion_techniques("")
        assert result["techniques_detected"] == []
        assert result["intensity_score"] == 0.0

    def test_none_text(self):
        result = analyze_persuasion_techniques(None)
        assert result["intensity_score"] == 0.0
        assert "Insufficient" in result["recommendation"]

    def test_detects_urgency(self):
        result = analyze_persuasion_techniques("Urgent! Immediate action required. Act now.")
        assert "urgency" in result["techniques_detected"]

    def test_detects_authority(self):
        result = analyze_persuasion_techniques("Per policy and compliance requirements, this is mandatory.")
        assert "authority" in result["techniques_detected"]

    def test_detects_scarcity(self):
        result = analyze_persuasion_techniques("Only 3 left! Limited supply. Exclusive offer.")
        assert "scarcity" in result["techniques_detected"]

    def test_detects_social_proof(self):
        result = analyze_persuasion_techniques("Everyone is doing it. Thousands of satisfied customers. Join millions.")
        assert "social_proof" in result["techniques_detected"]

    def test_detects_reciprocity(self):
        result = analyze_persuasion_techniques("After all I've done for you, you owe me this. Free gift!")
        assert "reciprocity" in result["techniques_detected"]

    def test_detects_gaslighting(self):
        text = "That never happened. You're being paranoid. You remember it wrong."
        result = analyze_persuasion_techniques(text)
        assert "gaslighting" in result["techniques_detected"]

    def test_detects_love_bombing(self):
        text = "You're my soulmate. I've never felt this way. I can't live without you."
        result = analyze_persuasion_techniques(text)
        assert "love_bombing" in result["techniques_detected"]

    def test_detects_negging(self):
        text = "For a girl, you're pretty good. That's surprising coming from you."
        result = analyze_persuasion_techniques(text)
        assert "negging" in result["techniques_detected"]

    def test_intensity_increases_with_more_patterns(self):
        low = analyze_persuasion_techniques("Urgent")
        high = analyze_persuasion_techniques(
            "Urgent! Immediate action required. Act now. Limited time only. Exclusive offer!"
        )
        assert high["intensity_score"] > low["intensity_score"]

    def test_dominant_technique_is_set(self):
        result = analyze_persuasion_techniques("Urgent! Urgent! Urgent! Act now immediately.")
        assert result["dominant_technique"] == "urgency"

    def test_clean_text_no_detection(self):
        result = analyze_persuasion_techniques("The weather is nice today. I went for a walk in the park.")
        assert result["techniques_detected"] == []
        assert result["intensity_score"] == 0.0

    def test_recommendation_at_moderate_intensity(self):
        text = "Urgent! Act now! Limited time! " * 5
        result = analyze_persuasion_techniques(text)
        assert "Moderate persuasion" in result["recommendation"] or "High persuasion" in result["recommendation"]


class TestDetectManipulationPatterns:
    def test_empty_conversation(self):
        result = detect_manipulation_patterns([])
        assert result["manipulation_detected"] is False
        assert result["overall_risk"] == "low"

    def test_detects_gaslighting_in_conversation(self):
        conversation = [
            {"speaker": "A", "text": "You said you'd be here at 5."},
            {"speaker": "B", "text": "That never happened. You're being paranoid."},
        ]
        result = detect_manipulation_patterns(conversation)
        assert "gaslighting" in result["detected_patterns"]

    def test_returns_speaker_analysis(self):
        conversation = [
            {"speaker": "Alice", "text": "Something something."},
            {"speaker": "Bob", "text": "Urgent! Act now!"},
        ]
        result = detect_manipulation_patterns(conversation)
        assert "Alice" in result["speaker_analysis"]
        assert "Bob" in result["speaker_analysis"]

    def test_no_manipulation_in_clean_conversation(self):
        conversation = [
            {"speaker": "A", "text": "How was your day?"},
            {"speaker": "B", "text": "It was good, thanks. I went to the park."},
        ]
        result = detect_manipulation_patterns(conversation)
        assert result["manipulation_detected"] is False
        assert result["overall_risk"] == "low"

    def test_gaslighting_events_populated(self):
        conversation = [
            {"speaker": "A", "text": "That never happened. You're being paranoid."},
        ]
        result = detect_manipulation_patterns(conversation)
        assert len(result["gaslighting_events"]) > 0


class TestAssessTrustworthiness:
    def test_empty_profile(self):
        result = assess_trustworthiness({})
        assert 0.0 <= result["trust_score"] <= 1.0

    def test_high_persuasion_lowers_trust(self):
        profile = {
            "communication_history": [
                {"speaker": "X", "text": "Urgent! Act now! Immediate! " * 10},
            ],
        }
        result = assess_trustworthiness(profile)
        assert "persuasion intensity" in str(result["risk_factors"]) or result["trust_score"] < 0.9

    def test_vague_identity_claims_lower_trust(self):
        profile = {
            "identity_claims": ["executive", "official", "management"],
        }
        result = assess_trustworthiness(profile)
        assert "vaguely stated" in str(result["risk_factors"])

    def test_pressure_signals_lower_trust(self):
        profile = {
            "behavioral_signals": ["pressure_tactics", "urgent_demands"],
        }
        result = assess_trustworthiness(profile)
        assert "pressure signals" in str(result["risk_factors"])

    def test_trust_score_clamped(self):
        profile = {
            "identity_claims": ["bad"] * 10,
            "behavioral_signals": ["pressure_tactics"] * 10,
        }
        result = assess_trustworthiness(profile)
        assert result["trust_score"] >= 0.0

    def test_recommendation_at_high_risk(self):
        profile = {
            "identity_claims": ["a"] * 10,
            "behavioral_signals": ["pressure_tactics"] * 20,
            "communication_history": [
                {"speaker": "X", "text": "Urgent! Act now! " * 20},
            ],
        }
        result = assess_trustworthiness(profile)
        assert result["trust_score"] < 0.4
        assert "HIGH RISK" in result["recommendation"]


class TestClassifySocialEngineeringAttack:
    def test_phishing_detection(self):
        scenario = {
            "description": "Please verify your password and login credentials immediately",
            "channel": "email",
            "requested_action": "Enter your username and password",
            "indicators": ["urgency_in_subject_line", "requests_for_credentials"],
        }
        result = classify_social_engineering_attack(scenario)
        assert result["primary_attack_type"] in ATTACK_VECTORS

    def test_tech_support_scam_classified(self):
        scenario = {
            "description": "This is technical support. Your computer has a virus. We need to verify your account.",
            "channel": "phone",
            "requested_action": "Grant remote access",
            "indicators": ["unsolicited_service_offer"],
        }
        result = classify_social_engineering_attack(scenario)
        assert result["primary_attack_type"] in ("quid_pro_quo", "pretexting")

    def test_returns_risk_level(self):
        scenario = {
            "description": "Urgent! Your account will be closed. Act now!",
            "channel": "email",
            "requested_action": "Provide password immediately",
            "indicators": [],
        }
        result = classify_social_engineering_attack(scenario)
        assert result["risk_level"] in ("low", "medium", "high", "critical")

    def test_high_risk_scenario(self):
        scenario = {
            "description": "Urgent! Immediate action required! Your account will be deleted.",
            "channel": "email",
            "requested_action": "Enter credentials now",
            "indicators": ["urgency_in_subject_line", "requests_for_credentials"],
        }
        result = classify_social_engineering_attack(scenario)
        assert result["risk_level"] in ("high", "critical")

    def test_recommends_protocols(self):
        scenario = {
            "description": "Some scenario text",
            "channel": "email",
            "requested_action": "Do something",
            "indicators": [],
        }
        result = classify_social_engineering_attack(scenario)
        assert len(result["recommended_protocols"]) > 0


# ============================================================================
# behavioral_cues — data integrity
# ============================================================================

class TestFacialActionUnits:
    def test_canonical_aus_present(self):
        core = {
            "AU1", "AU2", "AU4", "AU5", "AU6", "AU7", "AU9", "AU10",
            "AU11", "AU12", "AU14", "AU15", "AU17", "AU20", "AU23",
            "AU24", "AU25", "AU26",
        }
        present = set(FACIAL_ACTION_UNITS)
        assert present >= core

    def test_all_aus_have_muscle_field(self):
        for code, data in FACIAL_ACTION_UNITS.items():
            assert "muscle" in data, f"{code} missing muscle"

    def test_all_aus_have_associated_emotions(self):
        for code, data in FACIAL_ACTION_UNITS.items():
            assert len(data["associated_emotions"]) > 0, f"{code} missing associated_emotions"


class TestBasicEmotions:
    def test_ekman_six_present(self):
        core = {"happiness", "sadness", "anger", "fear", "disgust", "surprise"}
        assert set(BASIC_EMOTIONS) >= core

    def test_contempt_present(self):
        assert "contempt" in BASIC_EMOTIONS

    def test_all_emotions_have_prototypical_AUs(self):
        for name, data in BASIC_EMOTIONS.items():
            assert len(data["prototypical_AUs"]) > 0, f"{name} missing prototypical_AUs"


class TestPlutchikEmotions:
    def test_eight_primary_emotions(self):
        expected = {"joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation"}
        assert set(PLUTCHIK_EMOTIONS) == expected

    def test_all_have_intensity_variants(self):
        for _name, data in PLUTCHIK_EMOTIONS.items():
            assert "intensity_variants" in data
            assert set(data["intensity_variants"]) >= {"low", "moderate", "high"}

    def test_all_have_opposite(self):
        for _name, data in PLUTCHIK_EMOTIONS.items():
            assert "opposite" in data
            assert data["opposite"] in PLUTCHIK_EMOTIONS


class TestDeceptionIndicators:
    def test_indicators_span_categories(self):
        categories = {d["category"] for d in DECEPTION_INDICATORS.values()}
        assert "verbal" in categories
        assert "non_verbal" in categories

    def test_all_indicators_have_description(self):
        for name, data in DECEPTION_INDICATORS.items():
            assert len(data["description"]) > 0, f"{name} missing description"


class TestBodyLanguageCues:
    def test_cues_present(self):
        expected = {
            "pupil_dilation", "blink_rate", "voice_stress", "posture",
            "mirroring", "feet_direction", "lip_compression",
            "chin_withdrawal",
        }
        assert set(BODY_LANGUAGE_CUES) >= expected


# ============================================================================
# behavioral_cues — functions
# ============================================================================

class TestClassifyEmotion:
    def test_happiness_from_au6_au12(self):
        result = classify_emotion({
            "action_units": ["AU6", "AU12"],
            "intensity": "C",
            "duration_ms": 2000,
            "symmetry": "symmetric",
        })
        assert result["primary_emotion"] == "happiness"
        assert result["expression_type"] == "duchenne_genuine"

    def test_social_smile_without_au6(self):
        result = classify_emotion({
            "action_units": ["AU12"],
            "intensity": "B",
            "duration_ms": 6000,
            "symmetry": "symmetric",
        })
        assert result["expression_type"] in ("social_smile", "social_or_posed")

    def test_sadness_aus(self):
        result = classify_emotion({
            "action_units": ["AU1", "AU4", "AU15"],
            "intensity": "C",
            "duration_ms": 3000,
            "symmetry": "symmetric",
        })
        assert result["primary_emotion"] == "sadness"

    def test_micro_expression_detected(self):
        result = classify_emotion({
            "action_units": ["AU6", "AU12"],
            "intensity": "B",
            "duration_ms": 100,
            "symmetry": "symmetric",
        })
        assert result["expression_type"] == "micro_expression"

    def test_asymmetric_expression(self):
        result = classify_emotion({
            "action_units": ["AU1", "AU5"],
            "intensity": "D",
            "duration_ms": 1000,
            "symmetry": "asymmetric_left_dominant",
        })
        assert result["expression_type"] in ("social_or_posed", "micro_expression")

    def test_disgust_aus(self):
        result = classify_emotion({
            "action_units": ["AU9", "AU10"],
            "intensity": "C",
            "duration_ms": 2000,
            "symmetry": "symmetric",
        })
        assert result["primary_emotion"] == "disgust"

    def test_returns_dimensional_values(self):
        result = classify_emotion({
            "action_units": ["AU6", "AU12"],
            "intensity": "B",
            "duration_ms": 2000,
            "symmetry": "symmetric",
        })
        assert "valence" in result["dimensional_values"]
        assert "arousal" in result["dimensional_values"]


class TestAssessCredibility:
    def test_detailed_statement_credible(self):
        statement = {
            "text": (
                "On Monday afternoon at about 3pm, I was at the park near the fountain "
                "when I saw John approach. He said 'Hey, how are you?' and I replied "
                "that I was fine. We talked for about 20 minutes about the project "
                "because Sarah had asked me to update him. The sky was clear blue."
            ),
            "speaker": "witness",
            "delivery_context": "free_recall",
        }
        result = assess_credibility(statement)
        assert "logical_structure" in result["criteria_met"] or len(result["criteria_met"]) > 0

    def test_short_statement_less_credible(self):
        statement = {
            "text": "I don't know. Someone said something happened.",
            "speaker": "suspect",
            "delivery_context": "interview",
        }
        result = assess_credibility(statement)
        assert result["credibility_index"] < 0.6

    def test_detects_risk_flags(self):
        statement = {
            "text": "It was decided that mistakes were made. Allegedly someone did something.",
            "speaker": "suspect",
            "delivery_context": "interrogatory",
        }
        result = assess_credibility(statement)
        assert len(result["risk_flags"]) > 0

    def test_credibility_between_zero_and_one(self):
        statement = {
            "text": "Something happened.",
            "speaker": "A",
            "delivery_context": "free_recall",
        }
        result = assess_credibility(statement)
        assert 0.0 <= result["credibility_index"] <= 1.0


class TestDetectDeceptionIndicators:
    def test_detects_distancing_language(self):
        transcript = [
            {"speaker": "A", "text": "It was decided that mistakes were made."},
        ]
        result = detect_deception_indicators(transcript)
        assert "distancing_language" in result["detected_indicators"]

    def test_detects_equivocation(self):
        transcript = [
            {"speaker": "A", "text": "Allegedly and supposedly someone reported something."},
        ]
        result = detect_deception_indicators(transcript)
        assert "verbal_equivocation" in result["detected_indicators"]

    def test_short_transcript_sparseness(self):
        transcript = [
            {"speaker": "A", "text": "I don't know."},
        ]
        result = detect_deception_indicators(transcript)
        assert "detail_sparseness" in result["detected_indicators"]

    def test_detects_behavioral_indicators(self):
        transcript = [
            {"speaker": "A", "text": "I definitely was there at exactly the time you mention."},
        ]
        notes = {"self_touching": True, "barrier_behaviors": True, "pitch_increased": True}
        result = detect_deception_indicators(transcript, notes)
        assert "self_touching" in result["detected_indicators"]
        assert "pitch_changes" in result["detected_indicators"]

    def test_no_false_positives_on_clean_transcript(self):
        transcript = [
            {"speaker": "A", "text": (
                "I was at the office on Monday. I spoke with Sarah about "
                "the quarterly report. We discussed the projections and she "
                "agreed with my analysis."
            )},
        ]
        result = detect_deception_indicators(transcript)
        assert result["deception_probability"] < 0.3

    def test_speaker_analysis_populated(self):
        transcript = [
            {"speaker": "Alice", "text": "It was decided that mistakes were made."},
            {"speaker": "Bob", "text": "Allegedly someone reported something."},
        ]
        result = detect_deception_indicators(transcript)
        assert "Alice" in result["speaker_analysis"]
        assert "Bob" in result["speaker_analysis"]


# ============================================================================
# animal_behavior — data integrity
# ============================================================================

class TestFixedActionPatterns:
    def test_five_faps_present(self):
        keys = set(FIXED_ACTION_PATTERNS)
        assert len(keys) >= 5

    def test_all_have_species(self):
        for name, data in FIXED_ACTION_PATTERNS.items():
            assert "species" in data, f"{name} missing species"


class TestAnimalCommunicationModes:
    def test_four_modes_present(self):
        expected = {"vocalization", "chemical_signaling", "visual_displays", "tactile_communication"}
        assert set(ANIMAL_COMMUNICATION_MODES) == expected

    def test_all_modes_have_examples(self):
        for name, data in ANIMAL_COMMUNICATION_MODES.items():
            assert len(data["examples"]) > 0, f"{name} missing examples"


class TestStressIndicators:
    def test_four_indicator_categories_present(self):
        expected = {"displacement_behaviors", "stereotypies", "avoidance", "appetite_changes"}
        assert set(STRESS_INDICATORS) == expected

    def test_all_have_description(self):
        for name, data in STRESS_INDICATORS.items():
            assert "description" in data, f"{name} missing description"


class TestSocialStructures:
    def test_five_structures_present(self):
        expected = {"dominance_hierarchy", "cooperative_breeding", "eusociality", "fission_fusion", "pair_bonding"}
        assert set(SOCIAL_STRUCTURES) == expected

    def test_all_have_description(self):
        for name, data in SOCIAL_STRUCTURES.items():
            assert "description" in data, f"{name} missing description"

    def test_all_have_species_examples(self):
        for name, data in SOCIAL_STRUCTURES.items():
            assert len(data["species_examples"]) > 0, f"{name} missing species_examples"


class TestTrainingMethods:
    def test_six_methods_present(self):
        expected = {
            "operant_conditioning", "classical_conditioning",
            "clicker_training", "habituation",
            "desensitization_and_counterconditioning", "social_learning",
        }
        assert set(TRAINING_METHODS) == expected


class TestAnimalLanguageResearch:
    def test_eight_research_programs(self):
        expected = {
            "washoe", "koko", "nim_chimpsky", "alex_the_parrot",
            "dolphin_signature_whistles", "corvid_tool_use",
            "prairie_dog_alarm_calls", "bee_waggle_dance",
        }
        assert set(ANIMAL_LANGUAGE_RESEARCH) == expected

    def test_all_have_key_findings(self):
        for name, data in ANIMAL_LANGUAGE_RESEARCH.items():
            assert len(data["key_findings"]) > 0, f"{name} missing key_findings"

    def test_all_have_significance(self):
        for name, data in ANIMAL_LANGUAGE_RESEARCH.items():
            assert "significance" in data, f"{name} missing significance"


# ============================================================================
# animal_behavior — functions
# ============================================================================

class TestClassifyBehavior:
    def test_stereotypy_detection(self):
        obs = {
            "behavior": "pacing back and forth repetitively",
            "context": "zoo enclosure",
            "frequency": "constant",
            "elicitor": "none apparent",
        }
        result = classify_behavior("Panthera leo", obs)
        assert result["specific_classification"] == "stereotypy"
        assert result["behavior_type"] == "learned"

    def test_displacement_behavior(self):
        obs = {
            "behavior": "excessive self grooming",
            "context": "crowded shelter",
            "frequency": "constant",
            "elicitor": "other dogs",
        }
        result = classify_behavior("Canis familiaris", obs)
        assert result["specific_classification"] in ("displacement_behavior", "stereotypy")

    def test_aggression_classification(self):
        obs = {
            "behavior": "bite and attack during feeding",
            "context": "resource guarding",
            "frequency": "repeated",
            "elicitor": "approach during feeding",
        }
        result = classify_behavior("Canis familiaris", obs)
        assert result["functional_category"] == "agonistic"

    def test_play_behavior(self):
        obs = {
            "behavior": "wrestle and chase with littermate",
            "context": "free play area",
            "frequency": "repeated",
            "elicitor": "littermate present",
        }
        result = classify_behavior("Canis familiaris", obs)
        assert result["functional_category"] == "play"

    def test_courtship_behavior(self):
        obs = {
            "behavior": "elaborate courtship dance display",
            "context": "mating season",
            "frequency": "seasonal",
            "elicitor": "female presence",
        }
        result = classify_behavior("Paradisaea apoda", obs)
        assert result["functional_category"] in ("reproductive", "communication")

    def test_behavior_type_not_empty(self):
        obs = {
            "behavior": "some behavior",
            "context": "unknown",
            "frequency": "once",
            "elicitor": "",
        }
        result = classify_behavior("unknown species", obs)
        assert result["behavior_type"] in ("innate", "learned", "mixed")
        assert result["specific_classification"] != "undetermined"


class TestInterpretVocalization:
    def test_dog_bark(self):
        features = {
            "duration_ms": 300,
            "peak_frequency_hz": 600,
            "frequency_range_hz": (200, 2000),
            "call_rate_per_minute": 25,
            "harmonic_structure": "noisy",
            "pattern": "repeated",
        }
        result = interpret_vocalization("Canis familiaris", features)
        assert result["likely_call_type"] in ("barking", "undetermined")

    def test_dog_growl(self):
        features = {
            "duration_ms": 800,
            "peak_frequency_hz": 300,
            "frequency_range_hz": (100, 600),
            "call_rate_per_minute": 1,
            "harmonic_structure": "noisy",
            "pattern": "single",
        }
        result = interpret_vocalization("dog", features)
        assert result["likely_call_type"] == "growling"
        assert "threat" in result["emotional_state"] or "fear" in result["emotional_state"]

    def test_cat_meow(self):
        features = {
            "duration_ms": 400,
            "peak_frequency_hz": 900,
            "frequency_range_hz": (400, 1500),
            "call_rate_per_minute": 5,
            "harmonic_structure": "harmonic",
            "pattern": "single",
        }
        result = interpret_vocalization("Felis catus", features)
        assert result["likely_call_type"] == "meow"

    def test_horse_whinny(self):
        features = {
            "duration_ms": 1200,
            "peak_frequency_hz": 2000,
            "frequency_range_hz": (500, 4000),
            "call_rate_per_minute": 2,
            "harmonic_structure": "harmonic",
            "pattern": "trill",
        }
        result = interpret_vocalization("Equus caballus", features)
        assert result["likely_call_type"] == "whinny"

    def test_unknown_species_default(self):
        features = {
            "duration_ms": 50,
            "peak_frequency_hz": 3000,
            "frequency_range_hz": (1000, 5000),
            "call_rate_per_minute": 10,
            "harmonic_structure": "tonal",
            "pattern": "single",
        }
        result = interpret_vocalization("unknown_creature", features)
        assert "likely_call_type" in result
        assert "confidence" in result


class TestRecommendTrainingApproach:
    def test_fear_treatment_recommends_desensitization(self):
        goal = {
            "target_behavior": "remain calm during thunderstorms",
            "current_state": "pacing, panting, hiding",
            "constraints": ["force_free"],
            "problem_behavior": "thunderstorm phobia",
        }
        result = recommend_training_approach("dog", goal)
        assert (
            "desensitization" in result["recommended_method"]
            or "counterconditioning" in result["recommended_method"]
        )

    def test_basic_obedience_clicker(self):
        goal = {
            "target_behavior": "sit",
            "current_state": "no training",
            "constraints": [],
            "problem_behavior": "",
        }
        result = recommend_training_approach("Canis familiaris", goal)
        assert "clicker" in result["recommended_method"] or "positive_reinforcement" in result["recommended_method"]

    def test_aggression_case_warning(self):
        goal = {
            "target_behavior": "no longer lunge at dogs",
            "current_state": "lunging, barking at other dogs on walks",
            "constraints": ["force_free"],
            "problem_behavior": "leash reactivity",
        }
        result = recommend_training_approach("dog", goal)
        assert any("consult" in c.lower() or "professional" in c.lower() for c in result["cautions"])

    def test_returns_all_fields(self):
        goal = {
            "target_behavior": "come when called",
            "current_state": "ignores recall",
            "constraints": [],
            "problem_behavior": "",
        }
        result = recommend_training_approach("dog", goal)
        for field in ("recommended_method", "protocol_steps", "expected_timeline", "reinforcement_type"):
            assert field in result, f"Missing {field}"

    def test_horse_training_pressure_release(self):
        goal = {
            "target_behavior": "move forward on cue",
            "current_state": "unresponsive to leg",
            "constraints": [],
            "problem_behavior": "",
        }
        result = recommend_training_approach("Equus caballus", goal)
        assert (
            "negative_reinforcement" in result["recommended_method"]
            or "pressure" in str(result["protocol_steps"]).lower()
        )
