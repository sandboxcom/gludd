"""Tests for neuroscience and neuropsychology behavioral module."""

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

neuroscience = importlib.import_module("neuroscience")

BRAIN_REGIONS = neuroscience.BRAIN_REGIONS
NEUROTRANSMITTERS = neuroscience.NEUROTRANSMITTERS
NEURAL_CIRCUITS = neuroscience.NEURAL_CIRCUITS
COGNITIVE_BIASES = neuroscience.COGNITIVE_BIASES
HEURISTICS = neuroscience.HEURISTICS
MIRROR_NEURON_SYSTEMS = neuroscience.MIRROR_NEURON_SYSTEMS
MEMORY_SYSTEMS = neuroscience.MEMORY_SYSTEMS
LEARNING_MECHANISMS = neuroscience.LEARNING_MECHANISMS
NEUROPSYCHIATRIC_CONDITIONS = neuroscience.NEUROPSYCHIATRIC_CONDITIONS
analyze_cognitive_bias = neuroscience.analyze_cognitive_bias
classify_neural_circuit = neuroscience.classify_neural_circuit
assess_decision_making = neuroscience.assess_decision_making
evaluate_empathy_response = neuroscience.evaluate_empathy_response
classify_memory_subtype = neuroscience.classify_memory_subtype
analyze_neuropsychiatric_profile = neuroscience.analyze_neuropsychiatric_profile


# ============================================================================
# Brain Regions — data integrity
# ============================================================================

class TestBrainRegions:
    def test_canonical_regions_present(self):
        expected = {
            "prefrontal_cortex", "amygdala", "hippocampus", "basal_ganglia",
            "thalamus", "cerebellum", "insula", "anterior_cingulate_cortex",
        }
        assert set(BRAIN_REGIONS) >= expected

    def test_all_regions_have_functions(self):
        for name, data in BRAIN_REGIONS.items():
            assert len(data["functions"]) > 0, f"{name} missing functions"

    def test_all_regions_have_clinical_relevance(self):
        for name, data in BRAIN_REGIONS.items():
            assert len(data["clinical_relevance"]) > 0, f"{name} missing clinical_relevance"

    def test_prefrontal_cortex_has_subregions(self):
        assert len(BRAIN_REGIONS["prefrontal_cortex"]["subregions"]) >= 3

    def test_amygdala_has_key_studies(self):
        assert len(BRAIN_REGIONS["amygdala"]["key_studies"]) >= 2

    def test_hippocampus_has_subfields(self):
        assert len(BRAIN_REGIONS["hippocampus"]["subfields"]) >= 4


# ============================================================================
# Neurotransmitters — data integrity
# ============================================================================

class TestNeurotransmitters:
    def test_canonical_nts_present(self):
        expected = {
            "dopamine", "serotonin", "norepinephrine", "glutamate",
            "GABA", "acetylcholine", "oxytocin", "endorphins",
        }
        assert set(NEUROTRANSMITTERS) >= expected

    def test_all_nts_have_functions(self):
        for name, data in NEUROTRANSMITTERS.items():
            assert len(data["functions"]) > 0, f"{name} missing functions"

    def test_all_nts_have_clinical_relevance(self):
        for name, data in NEUROTRANSMITTERS.items():
            assert len(data["clinical_relevance"]) > 0, f"{name} missing clinical_relevance"

    def test_dopamine_has_four_pathways(self):
        assert len(NEUROTRANSMITTERS["dopamine"]["pathways"]) == 4

    def test_serotonin_has_seven_receptor_families(self):
        assert len(NEUROTRANSMITTERS["serotonin"]["receptors"]) >= 7


# ============================================================================
# Neural Circuits — data integrity
# ============================================================================

class TestNeuralCircuits:
    def test_six_circuits_present(self):
        expected = {
            "default_mode_network", "salience_network", "central_executive_network",
            "reward_circuit", "fear_circuit", "mirror_neuron_system",
        }
        assert set(NEURAL_CIRCUITS) == expected

    def test_all_circuits_have_functions(self):
        for name, data in NEURAL_CIRCUITS.items():
            assert len(data["functions"]) > 0, f"{name} missing functions"

    def test_all_circuits_have_clinical_relevance(self):
        for name, data in NEURAL_CIRCUITS.items():
            assert len(data["clinical_relevance"]) > 0, f"{name} missing clinical_relevance"

    def test_all_circuits_have_nodes(self):
        for name, data in NEURAL_CIRCUITS.items():
            assert len(data["nodes"]) > 0, f"{name} missing nodes"


# ============================================================================
# Cognitive Biases — data integrity
# ============================================================================

class TestCognitiveBiases:
    def test_ten_biases_present(self):
        expected = {
            "confirmation_bias", "anchoring_bias", "availability_heuristic",
            "framing_effect", "loss_aversion", "sunk_cost_fallacy",
            "fundamental_attribution_error", "halo_effect", "overconfidence_bias",
            "dunning_kruger_effect", "negativity_bias", "in_group_bias",
        }
        assert set(COGNITIVE_BIASES) >= expected

    def test_all_biases_have_description(self):
        for name, data in COGNITIVE_BIASES.items():
            assert len(data["description"]) > 0, f"{name} missing description"

    def test_all_biases_have_mitigation(self):
        for name, data in COGNITIVE_BIASES.items():
            assert len(data["mitigation"]) > 0, f"{name} missing mitigation"

    def test_all_biases_have_examples(self):
        for name, data in COGNITIVE_BIASES.items():
            assert len(data["examples"]) > 0, f"{name} missing examples"


# ============================================================================
# Heuristics — data integrity
# ============================================================================

class TestHeuristics:
    def test_six_heuristics_present(self):
        expected = {
            "representativeness_heuristic", "affect_heuristic",
            "recognition_heuristic", "take_the_best_heuristic",
            "gaze_heuristic", "social_circle_heuristic",
        }
        assert set(HEURISTICS) == expected

    def test_all_heuristics_have_description(self):
        for name, data in HEURISTICS.items():
            assert len(data["description"]) > 0, f"{name} missing description"

    def test_all_heuristics_have_examples(self):
        for name, data in HEURISTICS.items():
            assert len(data["examples"]) > 0, f"{name} missing examples"


# ============================================================================
# Mirror Neurons & Social Cognition — data integrity
# ============================================================================

class TestMirrorNeuronSystems:
    def test_four_systems_present(self):
        expected = {
            "parietofrontal_mirror_system", "emotional_mirror_system",
            "theory_of_mind_network", "empathy_dual_system",
        }
        assert set(MIRROR_NEURON_SYSTEMS) == expected

    def test_all_systems_have_location(self):
        for name, data in MIRROR_NEURON_SYSTEMS.items():
            assert "location" in data or "components" in data, f"{name} missing location/components"

    def test_parietofrontal_has_discovery_info(self):
        assert "discovery" in MIRROR_NEURON_SYSTEMS["parietofrontal_mirror_system"]

    def test_theory_of_mind_has_developmental_milestones(self):
        tomm = MIRROR_NEURON_SYSTEMS["theory_of_mind_network"]
        assert len(tomm["developmental_milestones"]) >= 3

    def test_empathy_dual_system_has_components(self):
        eds = MIRROR_NEURON_SYSTEMS["empathy_dual_system"]
        assert "cognitive_empathy" in eds["components"]
        assert "emotional_empathy" in eds["components"]


# ============================================================================
# Memory Systems — data integrity
# ============================================================================

class TestMemorySystems:
    def test_seven_systems_present(self):
        expected = {
            "sensory_memory", "working_memory", "episodic_memory",
            "semantic_memory", "procedural_memory", "conditioned_memory",
            "priming",
        }
        assert set(MEMORY_SYSTEMS) == expected

    def test_all_systems_have_type_info(self):
        for name, data in MEMORY_SYSTEMS.items():
            assert "type" in data or "model" in data or "definition" in data, \
                f"{name} missing type/model/definition"

    def test_episodic_memory_has_brain_regions(self):
        assert len(MEMORY_SYSTEMS["episodic_memory"]["brain_regions"]) >= 3

    def test_working_memory_has_components(self):
        assert len(MEMORY_SYSTEMS["working_memory"]["components"]) >= 3


# ============================================================================
# Learning Mechanisms — data integrity
# ============================================================================

class TestLearningMechanisms:
    def test_seven_mechanisms_present(self):
        expected = {
            "long_term_potentiation", "long_term_depression",
            "spike_timing_dependent_plasticity", "reinforcement_learning_dopamine",
            "hebbian_learning", "error_based_learning", "observational_learning",
        }
        assert set(LEARNING_MECHANISMS) == expected

    def test_all_mechanisms_have_definition(self):
        for name, data in LEARNING_MECHANISMS.items():
            assert len(data["definition"]) > 0, f"{name} missing definition"

    def test_ltp_has_critical_molecules(self):
        assert len(LEARNING_MECHANISMS["long_term_potentiation"]["critical_molecules"]) >= 3


# ============================================================================
# Neuropsychiatric Conditions — data integrity
# ============================================================================

class TestNeuropsychiatricConditions:
    def test_ten_conditions_present(self):
        expected = {
            "major_depressive_disorder", "generalized_anxiety_disorder",
            "schizophrenia", "bipolar_disorder", "ADHD", "PTSD",
            "obsessive_compulsive_disorder", "autism_spectrum_disorder",
            "Alzheimers_disease", "borderline_personality_disorder",
            "Parkinsons_disease",
        }
        assert set(NEUROPSYCHIATRIC_CONDITIONS) >= expected

    def test_all_conditions_have_neurobiology(self):
        for name, data in NEUROPSYCHIATRIC_CONDITIONS.items():
            assert len(data["neurobiology"]) > 0, f"{name} missing neurobiology"

    def test_all_conditions_have_affected_regions(self):
        for name, data in NEUROPSYCHIATRIC_CONDITIONS.items():
            assert len(data["affected_regions"]) > 0, f"{name} missing affected_regions"

    def test_all_conditions_have_treatments(self):
        for name, data in NEUROPSYCHIATRIC_CONDITIONS.items():
            assert len(data["treatments"]) > 0, f"{name} missing treatments"

    def test_schizophrenia_has_symptom_categories(self):
        sz = NEUROPSYCHIATRIC_CONDITIONS["schizophrenia"]
        for cat in ("positive", "negative", "cognitive"):
            assert cat in sz["symptoms"], f"schizophrenia missing {cat} symptoms"


# ============================================================================
# analyze_cognitive_bias
# ============================================================================

class TestAnalyzeCognitiveBias:
    def test_empty_text(self):
        result = analyze_cognitive_bias("")
        assert result["biases_detected"] == []
        assert result["overall_bias_index"] == 0.0

    def test_none_text(self):
        result = analyze_cognitive_bias(None)
        assert result["overall_bias_index"] == 0.0
        assert "Insufficient" in result["recommendation"]

    def test_detects_confirmation_bias(self):
        result = analyze_cognitive_bias(
            "This proves what I always believed. As expected, the data confirms my theory."
        )
        assert "confirmation_bias" in result["biases_detected"]

    def test_detects_loss_aversion(self):
        result = analyze_cognitive_bias(
            "Don't miss this opportunity! Last chance to protect your assets. Act now or lose out forever."
        )
        assert "loss_aversion" in result["biases_detected"]

    def test_detects_sunk_cost_fallacy(self):
        result = analyze_cognitive_bias(
            "We've already invested so much. We've come this far and can't quit now."
        )
        assert "sunk_cost_fallacy" in result["biases_detected"]

    def test_detects_overconfidence_bias(self):
        result = analyze_cognitive_bias(
            "I'm absolutely certain this will work. I guarantee there's no doubt about it."
        )
        assert "overconfidence_bias" in result["biases_detected"]

    def test_detects_negativity_bias(self):
        result = analyze_cognitive_bias(
            "Everything is terrible. This is the worst disaster and nothing goes right."
        )
        assert "negativity_bias" in result["biases_detected"]

    def test_detects_in_group_bias(self):
        result = analyze_cognitive_bias(
            "Us versus them. People like them are not our type."
        )
        assert "in_group_bias" in result["biases_detected"]

    def test_dominant_bias_is_set(self):
        text = "Don't miss! Last chance! Lose! Protect your! Act now! " * 5
        result = analyze_cognitive_bias(text)
        assert result["dominant_bias"] is not None

    def test_clean_text_no_bias(self):
        result = analyze_cognitive_bias("The sky is blue. I went shopping and bought some fruit.")
        assert result["biases_detected"] == []
        assert result["overall_bias_index"] == 0.0

    def test_overall_bias_index_between_zero_and_one(self):
        result = analyze_cognitive_bias(
            "I'm absolutely certain this will work. As expected, the data confirms my theory."
        )
        assert 0.0 <= result["overall_bias_index"] <= 1.0

    def test_high_bias_yields_recommendation(self):
        text = "Don't miss! Last chance! Act now! Lose! " * 10
        result = analyze_cognitive_bias(text)
        rec = result["recommendation"]
        assert "High cognitive bias" in rec or "Moderate cognitive bias" in rec


# ============================================================================
# classify_neural_circuit
# ============================================================================

class TestClassifyNeuralCircuit:
    def test_empty_description(self):
        result = classify_neural_circuit("")
        assert result["matched_circuit"] == "unknown"
        assert result["confidence"] == 0.0

    def test_classifies_dmn(self):
        result = classify_neural_circuit(
            "During resting state with mind wandering and self referential thought, default mode activity increases."
        )
        assert result["matched_circuit"] == "default_mode_network"

    def test_classifies_reward_circuit(self):
        result = classify_neural_circuit(
            "Dopamine release in response to reward and motivation with reinforcement learning signals."
        )
        assert result["matched_circuit"] == "reward_circuit"

    def test_classifies_fear_circuit(self):
        result = classify_neural_circuit(
            "Amygdala activation to threat and fear with conditioned responses and defensive freezing."
        )
        assert result["matched_circuit"] == "fear_circuit"

    def test_classifies_mirror_neuron_system(self):
        result = classify_neural_circuit(
            "Neurons fire during both action observation and imitation, supporting empathy and action understanding."
        )
        assert result["matched_circuit"] == "mirror_neuron_system"

    def test_returns_confidence_between_0_and_1(self):
        result = classify_neural_circuit("Working memory, problem solving, cognitive control and goal planning.")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_returns_alternative_matches(self):
        result = classify_neural_circuit(
            "Resting state mind wandering with some fear and threat detection and reward motivation processing."
        )
        assert "alternative_matches" in result


# ============================================================================
# assess_decision_making
# ============================================================================

class TestAssessDecisionMaking:
    def test_none_cues(self):
        result = assess_decision_making(None)
        assert result["decision_quality"] == "unknown"

    def test_empty_cues(self):
        result = assess_decision_making({})
        assert result["decision_quality"] == "unknown"
        assert result["risk_of_bias"] == "unknown"

    def test_high_time_pressure_defaults_heuristics(self):
        result = assess_decision_making({
            "context": "Need to decide quickly",
            "time_pressure": "high",
        })
        assert len(result["likely_heuristics"]) > 0

    def test_high_stakes_without_alternatives_poor(self):
        result = assess_decision_making({
            "context": "Looks like a typical investment opportunity",
            "time_pressure": "low",
            "stakes": "critical",
            "confidence": 0.9,
            "alternatives_considered": 1,
        })
        assert result["risk_of_bias"] in ("critical", "high")

    def test_multiple_alternatives_improves_quality(self):
        result = assess_decision_making({
            "context": "some decision",
            "alternatives_considered": 4,
            "confidence": 0.4,
            "stakes": "moderate",
            "time_pressure": "low",
        })
        assert result["decision_quality"] == "good"

    def test_affect_heuristic_detected(self):
        result = assess_decision_making({
            "context": "This feels right and my gut feeling says yes",
            "stakes": "moderate",
            "time_pressure": "moderate",
        })
        assert "affect_heuristic" in result["likely_heuristics"]

    def test_anchoring_heuristic_detected(self):
        result = assess_decision_making({
            "context": "Starting at the initial reference price of the first quote",
            "stakes": "moderate",
            "time_pressure": "low",
        })
        assert "anchoring_heuristic" in result["likely_heuristics"]

    def test_returns_recommendations(self):
        result = assess_decision_making({
            "context": "just based on looks like a typical case",
            "alternatives_considered": 1,
            "confidence": 0.9,
            "stakes": "high",
            "time_pressure": "high",
        })
        assert len(result["recommendations"]) > 0


# ============================================================================
# evaluate_empathy_response
# ============================================================================

class TestEvaluateEmpathyResponse:
    def test_none_context(self):
        result = evaluate_empathy_response(None)
        assert result["empathy_level"] == "unknown"
        assert result["composite_empathy"] == 0.0

    def test_high_empathy_scores(self):
        result = evaluate_empathy_response({
            "perspective_taking": 0.9,
            "emotional_accuracy": 0.9,
            "emotional_contagion": 0.9,
            "prosocial_intent": 0.9,
            "attention_to_other": 0.9,
            "verbal_acknowledgment": 0.9,
        })
        assert result["empathy_level"] == "high"
        assert result["composite_empathy"] > 0.6

    def test_low_empathy_scores(self):
        result = evaluate_empathy_response({
            "perspective_taking": 0.1,
            "emotional_accuracy": 0.1,
            "emotional_contagion": 0.1,
            "prosocial_intent": 0.1,
            "attention_to_other": 0.1,
            "verbal_acknowledgment": 0.1,
        })
        assert result["empathy_level"] == "low"

    def test_cognitive_vs_emotional_separation(self):
        result = evaluate_empathy_response({
            "perspective_taking": 0.9,
            "emotional_accuracy": 0.9,
            "emotional_contagion": 0.1,
            "prosocial_intent": 0.1,
            "attention_to_other": 0.1,
            "verbal_acknowledgment": 0.5,
        })
        assert result["cognitive_empathy_score"] > 0.7
        assert result["emotional_empathy_score"] < 0.3

    def test_factors_includes_profile(self):
        result = evaluate_empathy_response({
            "perspective_taking": 0.5,
            "emotional_accuracy": 0.5,
            "emotional_contagion": 0.5,
            "prosocial_intent": 0.5,
            "attention_to_other": 0.5,
        })
        assert "profile" in result["factors"]

    def test_psychopathy_like_profile(self):
        result = evaluate_empathy_response({
            "perspective_taking": 0.9,
            "emotional_accuracy": 0.9,
            "emotional_contagion": 0.1,
            "prosocial_intent": 0.0,
            "attention_to_other": 0.1,
        })
        assert "psychopathy" in result["factors"]["profile"].lower()


# ============================================================================
# classify_memory_subtype
# ============================================================================

class TestClassifyMemorySubtype:
    def test_empty_description(self):
        result = classify_memory_subtype("")
        assert result["matched_system"] == "unknown"
        assert result["confidence"] == 0.0

    def test_classifies_episodic_memory(self):
        result = classify_memory_subtype(
            "I remember when I first went to the beach. That time was a personal experience I can relive."
        )
        assert result["matched_system"] == "episodic_memory"

    def test_classifies_semantic_memory(self):
        result = classify_memory_subtype(
            "Paris is the capital of France. That's a fact and general knowledge everyone knows."
        )
        assert result["matched_system"] == "semantic_memory"

    def test_classifies_procedural_memory(self):
        result = classify_memory_subtype(
            "Knowing how to ride a bike — it becomes automatic muscle memory without thinking."
        )
        assert result["matched_system"] == "procedural_memory"

    def test_classifies_working_memory(self):
        result = classify_memory_subtype(
            "Holding a phone number in mind while rehearsing it, about seven plus or minus two items."
        )
        assert result["matched_system"] == "working_memory"

    def test_classifies_sensory_memory(self):
        result = classify_memory_subtype(
            "A fleeting momentary trace like an echoic afterimage that is a brief impression."
        )
        assert result["matched_system"] == "sensory_memory"

    def test_returns_confidence_between_0_and_1(self):
        result = classify_memory_subtype("I remember when something happened")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_returns_alternative_matches(self):
        result = classify_memory_subtype(
            "I remember when that personal experience as a fact knowing how to ride a bike."
        )
        assert "alternative_matches" in result


# ============================================================================
# analyze_neuropsychiatric_profile
# ============================================================================

class TestAnalyzeNeuropsychiatricProfile:
    def test_none_profile(self):
        result = analyze_neuropsychiatric_profile(None)
        assert result["top_matches"] == []
        assert "Insufficient" in result["recommendation"]

    def test_no_symptoms(self):
        result = analyze_neuropsychiatric_profile({})
        assert result["top_matches"] == []
        assert "No specific" in result["recommendation"]

    def test_depression_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["concentration_difficulty", "worthlessness", "guilt"],
            "emotional_symptoms": ["low_mood", "anhedonia"],
            "behavioral_symptoms": ["sleep_disturbance", "appetite_change", "fatigue"],
            "duration": "weeks",
            "course": "episodic",
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "major_depressive_disorder" in matches

    def test_anxiety_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["catastrophizing", "hypervigilance"],
            "emotional_symptoms": ["excessive_worry", "irritability", "restlessness"],
            "behavioral_symptoms": ["sleep_disturbance", "avoidance"],
            "duration": "months",
            "course": "waxing_and_waning",
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "generalized_anxiety_disorder" in matches

    def test_adhd_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["inattention", "forgetfulness", "disorganization"],
            "emotional_symptoms": ["emotional_dysregulation"],
            "behavioral_symptoms": ["hyperactivity", "impulsivity", "fidgeting"],
            "age_of_onset": "childhood",
            "duration": "lifetime",
            "course": "chronic",
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "ADHD" in matches

    def test_ptsd_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["flashbacks", "intrusive_memories", "dissociation"],
            "emotional_symptoms": ["emotional_numbing", "irritability"],
            "behavioral_symptoms": ["avoidance", "hypervigilance", "startle_response", "sleep_disturbance"],
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "PTSD" in matches

    def test_schizophrenia_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["cognitive_decline", "disorganized_speech"],
            "emotional_symptoms": ["flat_affect", "paranoia"],
            "behavioral_symptoms": ["social_withdrawal", "disorganized_behavior"],
            "age_of_onset": "late adolescence",
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "schizophrenia" in matches

    def test_alzheimers_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": [
                "memory_loss", "confusion", "disorientation",
                "language_difficulty", "impaired_judgment", "executive_dysfunction",
            ],
            "emotional_symptoms": ["personality_changes"],
            "behavioral_symptoms": [],
            "age_of_onset": "over 65",
            "course": "progressive",
            "duration": "years",
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "Alzheimers_disease" in matches

    def test_ocd_symptoms_match(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["obsessions", "intrusive_thoughts", "contamination_fears"],
            "emotional_symptoms": [],
            "behavioral_symptoms": ["compulsions", "repetitive_behaviors", "checking", "rituals"],
        })
        matches = [m["condition"] for m in result["top_matches"]]
        assert "obsessive_compulsive_disorder" in matches

    def test_gives_recommendation(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["memory_loss", "confusion"],
            "emotional_symptoms": ["low_mood"],
            "behavioral_symptoms": ["sleep_disturbance"],
        })
        assert len(result["recommendation"]) > 0

    def test_differential_when_multiple_matches(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["concentration_difficulty", "worthlessness", "sleep_disturbance"],
            "emotional_symptoms": ["low_mood", "excessive_worry", "irritability"],
            "behavioral_symptoms": ["fatigue", "restlessness", "avoidance"],
            "duration": "months",
            "course": "episodic",
        })
        if len(result["top_matches"]) >= 2:
            assert len(result["differential_considerations"]) > 0

    def test_scores_between_0_and_1(self):
        result = analyze_neuropsychiatric_profile({
            "cognitive_symptoms": ["inattention", "memory_loss"],
            "emotional_symptoms": ["low_mood"],
            "behavioral_symptoms": ["hyperactivity"],
        })
        for condition, score in result["condition_scores"].items():
            assert 0.0 <= score <= 1.5, f"{condition} score {score} out of range"
