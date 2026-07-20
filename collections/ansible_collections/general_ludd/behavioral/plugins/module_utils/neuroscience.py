"""Neuroscience and neuropsychology module: brain regions, neurotransmitters,
neural circuits, cognitive biases, mirror neurons, memory systems, learning
mechanisms, and neuropsychiatric conditions.

Public surface::

    analyze_cognitive_bias(text)                          -> dict
    classify_neural_circuit(description)                  -> dict
    assess_decision_making(heuristic_cues)                -> dict
    evaluate_empathy_response(interaction_context)         -> dict
    classify_memory_subtype(description)                   -> dict
    analyze_neuropsychiatric_profile(symptoms)             -> dict

    BRAIN_REGIONS                dict[region] -> properties
    NEUROTRANSMITTERS            dict[nt] -> properties
    NEURAL_CIRCUITS              dict[circuit] -> properties
    COGNITIVE_BIASES             dict[bias] -> properties
    HEURISTICS                   dict[heuristic] -> properties
    MIRROR_NEURON_SYSTEMS        dict[system] -> properties
    MEMORY_SYSTEMS               dict[system] -> properties
    LEARNING_MECHANISMS          dict[mechanism] -> properties
    NEUROPSYCHIATRIC_CONDITIONS  dict[condition] -> properties
"""

from __future__ import annotations

from typing import Any

# ============================================================================
# BRAIN REGIONS
# ============================================================================

BRAIN_REGIONS: dict[str, dict[str, Any]] = {
    "prefrontal_cortex": {
        "lobe": "frontal",
        "brodmann_areas": [9, 10, 11, 12, 46, 47],
        "functions": [
            "executive_function", "working_memory", "decision_making",
            "impulse_control", "planning", "social_cognition",
        ],
        "subregions": {
            "dorsolateral_pfc": "working memory, cognitive flexibility, planning",
            "ventromedial_pfc": "emotion regulation, decision-making, moral judgment",
            "orbitofrontal_cortex": "reward processing, impulse control, social behavior",
            "anterior_cingulate": "error detection, conflict monitoring, emotional regulation",
        },
        "connectivity": ["basal_ganglia", "amygdala", "hippocampus", "thalamus"],
        "clinical_relevance": [
            "ADHD", "schizophrenia", "depression", "frontal_lobe_syndrome",
        ],
    },
    "amygdala": {
        "lobe": "temporal",
        "nuclei": ["basolateral", "centromedial", "cortical"],
        "functions": [
            "fear_conditioning", "emotional_memory", "threat_detection",
            "social_processing", "emotional_learning", "aggression",
        ],
        "connectivity": ["prefrontal_cortex", "hippocampus", "hypothalamus", "brainstem"],
        "clinical_relevance": [
            "anxiety_disorders", "PTSD", "borderline_personality", "autism",
        ],
        "key_studies": [
            "Kluever-Bucy syndrome (bilateral amygdala lesions → hyperorality, hypersexuality)",
            "LeDoux fear conditioning (lateral amygdala as fear memory locus)",
        ],
    },
    "hippocampus": {
        "lobe": "temporal",
        "subfields": ["CA1", "CA2", "CA3", "dentate_gyrus", "subiculum"],
        "functions": [
            "episodic_memory_formation", "spatial_navigation", "contextual_learning",
            "pattern_separation", "pattern_completion", "memory_consolidation",
        ],
        "connectivity": ["entorhinal_cortex", "prefrontal_cortex", "amygdala", "septum"],
        "clinical_relevance": [
            "Alzheimers_disease", "temporal_lobe_epilepsy", "amnesia", "depression",
        ],
        "key_studies": [
            "Patient HM (bilateral medial temporal lobectomy → profound anterograde amnesia)",
            "London taxi drivers (enlarged posterior hippocampus from spatial navigation)",
        ],
    },
    "basal_ganglia": {
        "lobe": "subcortical",
        "nuclei": ["striatum", "globus_pallidus", "substantia_nigra", "subthalamic_nucleus"],
        "functions": [
            "motor_control", "habit_learning", "reward_processing",
            "action_selection", "procedural_memory", "sequence_learning",
        ],
        "pathways": {
            "direct_pathway": "facilitates movement (D1 receptors)",
            "indirect_pathway": "inhibits movement (D2 receptors)",
            "hyperdirect_pathway": "rapid movement inhibition via subthalamic nucleus",
        },
        "clinical_relevance": [
            "Parkinsons_disease", "Huntingtons_disease", "OCD", "Tourette_syndrome",
        ],
    },
    "thalamus": {
        "lobe": "diencephalon",
        "nuclei": [
            "lateral_geniculate", "medial_geniculate", "ventral_posterior",
            "ventral_lateral", "ventral_anterior", "mediodorsal",
            "pulvinar", "reticular",
        ],
        "functions": [
            "sensory_relay", "motor_relay", "attention_gating",
            "consciousness", "sleep_wake_cycle", "cortical_communication",
        ],
        "connectivity": ["cortex", "basal_ganglia", "cerebellum", "brainstem"],
        "clinical_relevance": ["thalamic_stroke", "thalamic_pain_syndrome", "absence_epilepsy"],
    },
    "cerebellum": {
        "lobe": "hindbrain",
        "lobes": ["anterior", "posterior", "flocculonodular"],
        "functions": [
            "motor_coordination", "balance", "motor_learning",
            "cognitive_timing", "language_processing", "error_correction",
        ],
        "cell_types": ["purkinje_cells", "granule_cells", "climbing_fibers", "mossy_fibers"],
        "clinical_relevance": ["ataxia", "dysmetria", "cerebellar_cognitive_affective_syndrome"],
    },
    "insula": {
        "lobe": "buried_within_lateral_sulcus",
        "subregions": ["anterior_insula", "posterior_insula"],
        "functions": [
            "interoception", "emotional_awareness", "empathy",
            "disgust_processing", "pain_perception", "craving",
        ],
        "connectivity": ["anterior_cingulate", "amygdala", "prefrontal_cortex", "thalamus"],
        "clinical_relevance": ["addiction", "anorexia", "alexithymia", "anxiety"],
    },
    "anterior_cingulate_cortex": {
        "lobe": "frontal (medial surface)",
        "brodmann_areas": [24, 25, 32, 33],
        "functions": [
            "error_detection", "conflict_monitoring", "emotional_regulation",
            "pain_processing", "attention_allocation", "reward_anticipation",
        ],
        "connectivity": ["prefrontal_cortex", "amygdala", "insula", "nucleus_accumbens"],
        "clinical_relevance": ["depression", "OCD", "chronic_pain", "ADHD"],
    },
}

# ============================================================================
# NEUROTRANSMITTERS
# ============================================================================

NEUROTRANSMITTERS: dict[str, dict[str, Any]] = {
    "dopamine": {
        "type": "monoamine",
        "pathways": [
            ("mesolimbic", "VTA to nucleus accumbens — reward, motivation"),
            ("mesocortical", "VTA to prefrontal cortex — cognition, executive function"),
            ("nigrostriatal", "substantia nigra to striatum — motor control"),
            ("tuberoinfundibular", "hypothalamus to pituitary — prolactin inhibition"),
        ],
        "receptors": ["D1", "D2", "D3", "D4", "D5"],
        "synthesis": "tyrosine → L-DOPA → dopamine (rate-limiting: tyrosine hydroxylase)",
        "degradation": "MAO-B and COMT enzymes",
        "functions": ["reward", "motivation", "motor_control", "learning", "attention"],
        "clinical_relevance": [
            "Parkinsons_disease (nigrostriatal degeneration)",
            "schizophrenia (mesolimbic hyperdopaminergia hypothesis)",
            "ADHD (prefrontal hypodopaminergia)",
            "addiction (mesolimbic dopamine surge)",
        ],
    },
    "serotonin": {
        "type": "monoamine",
        "source": "raphe nuclei (brainstem)",
        "synthesis": "tryptophan → 5-HTP → serotonin (rate-limiting: tryptophan hydroxylase)",
        "receptors": ["5-HT1A", "5-HT1B", "5-HT2A", "5-HT2C", "5-HT3", "5-HT4", "5-HT6", "5-HT7"],
        "functions": [
            "mood_regulation", "appetite", "sleep", "aggression",
            "pain_perception", "thermoregulation", "impulse_control",
        ],
        "clinical_relevance": [
            "depression (SSRIs block SERT)",
            "anxiety_disorders",
            "OCD",
            "migraine (5-HT receptor agonists)",
            "irritable_bowel_syndrome",
        ],
    },
    "norepinephrine": {
        "type": "monoamine",
        "source": "locus coeruleus (pons)",
        "synthesis": "dopamine → norepinephrine (rate-limiting: dopamine beta-hydroxylase)",
        "receptors": ["alpha-1", "alpha-2", "beta-1", "beta-2", "beta-3"],
        "functions": [
            "arousal", "vigilance", "attention", "fight_or_flight",
            "mood", "blood_pressure_regulation",
        ],
        "clinical_relevance": [
            "depression (SNRIs)",
            "ADHD (norepinephrine reuptake inhibitors)",
            "PTSD (hyperadrenergic state)",
            "POTS (postural orthostatic tachycardia syndrome)",
        ],
    },
    "glutamate": {
        "type": "amino_acid (primary excitatory)",
        "receptors": ["AMPA", "NMDA", "kainate", "metabotropic (mGluR1-8)"],
        "synthesis": "glutamine → glutamate (glutaminase); also from alpha-ketoglutarate",
        "functions": [
            "synaptic_plasticity", "learning", "memory", "excitatory_signaling",
            "long_term_potentiation", "excitotoxicity_when_excessive",
        ],
        "clinical_relevance": [
            "stroke (excitotoxicity)",
            "schizophrenia (NMDA hypofunction hypothesis)",
            "epilepsy",
            "Alzheimers (memantine: NMDA antagonist)",
        ],
    },
    "GABA": {
        "type": "amino_acid (primary inhibitory)",
        "receptors": ["GABA-A (ionotropic)", "GABA-B (metabotropic)"],
        "synthesis": "glutamate → GABA (glutamic acid decarboxylase, GAD)",
        "functions": [
            "inhibitory_signaling", "anxiety_reduction", "seizure_threshold",
            "sleep", "muscle_relaxation", "neuronal_inhibition",
        ],
        "clinical_relevance": [
            "anxiety (benzodiazepines enhance GABA-A)",
            "epilepsy (GABA agonists as anticonvulsants)",
            "Huntingtons_disease (GABAergic medium spiny neuron loss)",
            "insomnia (z-drugs target GABA-A)",
        ],
    },
    "acetylcholine": {
        "type": "quaternary_amine",
        "receptors": ["nicotinic (ionotropic)", "muscarinic M1-M5 (metabotropic)"],
        "source": ["basal forebrain", "brainstem pedunculopontine and laterodorsal tegmental nuclei"],
        "functions": [
            "attention", "memory", "learning", "arousal",
            "neuromuscular_transmission", "REM_sleep",
        ],
        "clinical_relevance": [
            "Alzheimers_disease (cholinergic hypothesis — acetylcholinesterase inhibitors)",
            "myasthenia_gravis (nicotinic receptor antibodies)",
            "Parkinsons_disease (anticholinergics for tremor)",
        ],
    },
    "oxytocin": {
        "type": "neuropeptide",
        "synthesis_site": "paraventricular and supraoptic nuclei of hypothalamus",
        "functions": [
            "social_bonding", "maternal_behavior", "trust",
            "pair_bonding", "empathy", "childbirth_and_lactation",
        ],
        "receptors": ["oxytocin receptor (OXTR) — G-protein coupled"],
        "clinical_relevance": [
            "autism (intranasal oxytocin trials)",
            "social_anxiety",
            "postpartum_depression",
            "schizophrenia (social cognition deficits)",
        ],
        "key_studies": [
            "Prairie vs. montane voles (OXTR density predicts monogamy)",
            "Intranasal oxytocin increases trust in economic games (Kosfeld et al. 2005)",
        ],
    },
    "endorphins": {
        "type": "neuropeptide (endogenous opioids)",
        "types": ["beta_endorphin", "met_enkephalin", "leu_enkephalin", "dynorphin"],
        "receptors": ["mu_opioid", "delta_opioid", "kappa_opioid"],
        "functions": [
            "pain_modulation", "euphoria", "stress_response",
            "reward", "runners_high", "social_bonding",
        ],
        "clinical_relevance": [
            "opioid_addiction (mu receptor agonists)",
            "chronic_pain",
            "naloxone_for_overdose_reversal",
        ],
    },
}

# ============================================================================
# NEURAL CIRCUITS
# ============================================================================

NEURAL_CIRCUITS: dict[str, dict[str, Any]] = {
    "default_mode_network": {
        "nodes": ["medial_prefrontal_cortex", "posterior_cingulate", "precuneus", "angular_gyrus"],
        "activation_condition": "resting state, self-referential thought, mind-wandering",
        "deactivation_condition": "goal-directed tasks, external attention demands",
        "functions": ["self_referential_thought", "autobiographical_memory", "mental_time_travel",
                      "theory_of_mind", "social_cognition", "moral_reasoning"],
        "clinical_relevance": [
            "Alzheimers (DMN nodes show early amyloid deposition)",
            "depression (hyperconnectivity + excessive self-focus / rumination)",
            "autism (atypical DMN connectivity)",
            "schizophrenia (DMN hyperconnectivity correlated with positive symptoms)",
        ],
    },
    "salience_network": {
        "nodes": ["anterior_insula", "dorsal_anterior_cingulate"],
        "functions": [
            "detect_behaviorally_relevant_stimuli",
            "switch_between_DMN_and_central_executive_network",
            "interoceptive_awareness",
            "emotional_salience_detection",
        ],
        "clinical_relevance": [
            "anxiety (hyperactive salience detection → false alarms)",
            "depression (blunted salience to positive stimuli)",
            "frontotemporal_dementia (early salience network degeneration)",
        ],
    },
    "central_executive_network": {
        "nodes": ["dorsolateral_prefrontal_cortex", "posterior_parietal_cortex"],
        "functions": [
            "working_memory", "cognitive_control", "problem_solving",
            "decision_making", "goal_maintenance",
        ],
        "clinical_relevance": [
            "ADHD (CEN under-recruitment during cognitive tasks)",
            "schizophrenia (CEN-DMN anticorrelation breakdown)",
        ],
    },
    "reward_circuit": {
        "nodes": ["ventral_tegmental_area", "nucleus_accumbens", "prefrontal_cortex", "amygdala", "hippocampus"],
        "neurotransmitter": "dopamine (mesolimbic pathway)",
        "functions": [
            "reward_prediction", "reinforcement_learning", "motivation",
            "wanting_vs_liking", "incentive_salience",
        ],
        "key_concepts": [
            "reward prediction error (Schultz et al. — dopamine neurons encode discrepancy)",
            "incentive sensitization theory of addiction (Robinson & Berridge, 1993)",
        ],
        "clinical_relevance": ["addiction", "depression (anhedonia)", "Parkinsons (impulse control disorders)"],
    },
    "fear_circuit": {
        "nodes": ["amygdala", "hippocampus", "prefrontal_cortex", "periaqueductal_gray"],
        "functions": [
            "fear_acquisition", "fear_extinction", "contextual_fear_conditioning",
            "threat_detection", "defensive_response",
        ],
        "key_finding": "Fear extinction requires prefrontal inhibition of amygdala (not erasure of memory)",
        "clinical_relevance": [
            "PTSD (impaired fear extinction; prefrontal-amygdala dysregulation)",
            "phobias",
            "panic_disorder",
        ],
    },
    "mirror_neuron_system": {
        "nodes": ["inferior_frontal_gyrus (pars opercularis)", "inferior_parietal_lobule", "superior_temporal_sulcus"],
        "initial_discovery": "Rizzolatti et al. 1996 — F5 area of macaque premotor cortex",
        "functions": [
            "action_understanding", "imitation_learning", "empathy",
            "language_evolution", "intention_inference", "social_cognition",
        ],
        "mirror_properties": "Neurons fire both during action execution AND observation of same action",
        "clinical_relevance": [
            "autism (broken mirror hypothesis — reduced mirror neuron activity during imitation)",
            "developmental_language_disorders",
            "motor_rehabilitation_post_stroke (action observation therapy)",
        ],
    },
}

# ============================================================================
# COGNITIVE BIASES
# ============================================================================

COGNITIVE_BIASES: dict[str, dict[str, Any]] = {
    "confirmation_bias": {
        "category": "information_processing",
        "description": "Tendency to search for, interpret, and recall information that confirms pre-existing beliefs",
        "mechanism": "Motivated reasoning + selective exposure + biased assimilation",
        "examples": [
            "Only reading news sources aligned with political views",
            "Remembering hits and forgetting misses in a prediction record",
            "Interpreting ambiguous evidence as supporting one's theory",
        ],
        "mitigation": ["actively_seek_disconfirming_evidence", "devils_advocate", "blind_data_analysis"],
        "researchers": ["Peter Wason (1960s — 2-4-6 task)"],
    },
    "anchoring_bias": {
        "category": "judgment",
        "description": "Over-reliance on the first piece of information encountered (the anchor) when making decisions",
        "mechanism": "Insufficient adjustment from initial value + selective accessibility of anchor-consistent info",
        "examples": [
            "Initial price offer anchors subsequent negotiations",
            "First listed salary range influences final offer",
            "Arbitrary number shown before an estimate biases the estimate",
        ],
        "studies": "Tversky & Kahneman (1974) — wheel of fortune experiment",
        "mitigation": ["consider_the_opposite", "multiple_anchor_points", "pre_commitment_to_criteria"],
    },
    "availability_heuristic": {
        "category": "judgment",
        "description": "Estimating probability based on how easily examples come to mind",
        "mechanism": "Vivid, recent, or emotionally charged events are more mentally available",
        "examples": [
            "Overestimating shark attacks after media coverage of one incident",
            "Thinking plane crashes are more common than car accidents",
            "Recalling more words from a vivid list than a neutral one",
        ],
        "studies": "Tversky & Kahneman (1973)",
        "mitigation": ["base_rate_data", "statistical_reasoning", "frequency_format"],
    },
    "framing_effect": {
        "category": "decision_making",
        "description": "Decisions are influenced by how options are presented (framed) rather than objective facts",
        "mechanism": "Loss aversion — losses loom larger than equivalent gains (~2× psychological weight)",
        "examples": [
            "90% survival rate vs. 10% mortality rate — same outcome, different choices",
            "Ground beef labeled '75% lean' preferred over '25% fat'",
        ],
        "studies": "Tversky & Kahneman (1981) — Asian disease problem",
        "mitigation": ["reframe_the_problem", "consider_absolute_quantities", "transparent_labels"],
    },
    "loss_aversion": {
        "category": "decision_making",
        "description": "The pain of losing is psychologically about twice as powerful as the pleasure of gaining",
        "mechanism": "Asymmetric value function (steeper for losses than gains) in prospect theory",
        "examples": [
            "Holding losing stocks too long (disposition effect)",
            "Status quo bias — preferring current state to avoid potential losses",
            "Insurance purchases driven by fear of loss",
        ],
        "studies": "Kahneman & Tversky (1979) — Prospect Theory",
        "mitigation": ["consider_opportunity_costs", "pre_commit_to_exit_criteria", "broad_framing"],
    },
    "sunk_cost_fallacy": {
        "category": "decision_making",
        "description": "Continuing investment based on past irrecoverable costs rather than future prospects",
        "mechanism": "Emotional attachment to investment + desire to avoid appearing wasteful + loss aversion",
        "examples": [
            "Finishing a bad book because you already read half",
            "Continuing a failing project because of prior investment",
            "Eating more at a buffet because you paid for it",
        ],
        "mitigation": ["ignore_sunk_costs", "consider_only_marginal_costs_and_benefits", "pre_commitment_to_stop_rules"],
    },
    "fundamental_attribution_error": {
        "category": "social",
        "description": "Over-emphasizing dispositional explanations for others' behavior while under-emphasizing situational factors",
        "mechanism": "Perceptual salience (the person is figure, situation is ground) + cognitive laziness",
        "examples": [
            "Assuming someone who cuts you off in traffic is a jerk (not rushing to hospital)",
            "Attributing exam failure to student laziness rather than family crisis",
        ],
        "mitigation": ["consider_situational_constraints", "ask_what_would_I_do_in_their_situation"],
    },
    "halo_effect": {
        "category": "social",
        "description": "Overall positive impression of a person influences evaluations of specific traits",
        "mechanism": "Cognitive consistency drive — dissonance from unrelated positive and negative traits",
        "examples": [
            "Assuming attractive people are also intelligent and kind",
            "Rating a charismatic CEO's company as more innovative regardless of data",
        ],
        "studies": "Thorndike (1920) — military officer ratings",
        "mitigation": ["rate_individual_dimensions_independently", "blind_evaluation", "structured_interviews"],
    },
    "overconfidence_bias": {
        "category": "self_evaluation",
        "description": "Subjective confidence in judgments is systematically greater than objective accuracy",
        "mechanism": "Confirmation bias + illusion of control + difficulty imagining being wrong",
        "examples": [
            "90% of drivers rate themselves above average",
            "Overly narrow confidence intervals in prediction tasks",
            "Entrepreneurs overestimating probability of business success",
        ],
        "mitigation": ["calibration_training", "consider_reasons_you_could_be_wrong", "pre_mortem_analysis"],
    },
    "dunning_kruger_effect": {
        "category": "self_evaluation",
        "description": "Unskilled individuals overestimate their ability; experts underestimate theirs",
        "mechanism": "Low performers lack metacognitive ability to recognize their own incompetence",
        "examples": [
            "Novice chess players overestimating their skill",
            "Poor performers in grammar tests rating their ability highest",
        ],
        "studies": "Kruger & Dunning (1999) — humor, grammar, logic tests",
        "mitigation": ["competency_based_assessment", "feedback_loops", "comparative_benchmarks"],
    },
    "negativity_bias": {
        "category": "information_processing",
        "description": "Greater attention, weight, and memory given to negative information vs. positive",
        "mechanism": "Evolutionary — missing a predator is costlier than missing a food source; amygdala preferentially encodes negative stimuli",
        "examples": [
            "Remembering criticism more vividly than praise",
            "Negative news stories receiving more engagement",
            "Losses remembered more strongly than equivalent gains",
        ],
        "mitigation": ["gratitude_practice", "positive_event_savoring", "ratio_awareness"],
    },
    "in_group_bias": {
        "category": "social",
        "description": "Favoring members of one's own group over members of other groups",
        "mechanism": "Social identity theory — self-esteem derived from group membership",
        "examples": [
            "Minimal group paradigm — arbitrarily assigned groups show favoritism",
            "Sports fans attributing positive plays to 'us' and negative to 'them'",
            "Same-race face recognition advantage (cross-race effect)",
        ],
        "mitigation": ["superordinate_goals", "intergroup_contact", "perspective_taking"],
    },
}

# ============================================================================
# HEURISTICS
# ============================================================================

HEURISTICS: dict[str, dict[str, Any]] = {
    "representativeness_heuristic": {
        "description": "Judging probability by how much A resembles B, ignoring base rates",
        "discovered_by": "Tversky & Kahneman (1974)",
        "biases_produced": [
            "base_rate_neglect", "conjunction_fallacy", "insensitivity_to_sample_size",
            "misconception_of_chance (gamblers fallacy)",
        ],
        "examples": [
            "Thinking a shy, detail-oriented person is more likely a librarian than a salesperson (ignoring base rates of each profession)",
            "Linda problem — rating 'feminist bank teller' as more probable than 'bank teller' (conjunction fallacy)",
        ],
    },
    "affect_heuristic": {
        "description": "Decisions influenced by emotional reactions — good feelings → low risk, bad feelings → high risk",
        "discovered_by": "Slovic, Finucane, Peters, & MacGregor (2002)",
        "examples": [
            "Overestimating nuclear power risk due to emotional salience (despite statistical safety record)",
            "Underestimating smoking risk due to positive affect toward the habit",
        ],
        "brain_regions_involved": ["amygdala", "ventromedial_prefrontal_cortex", "insula"],
    },
    "recognition_heuristic": {
        "description": "If one of two objects is recognized and the other is not, infer that the recognized object has the higher value on the criterion",
        "discovered_by": "Goldstein & Gigerenzer (2002)",
        "conditions": ["recognition_validity_must_be_above_chance", "no_other_cues_override_it"],
        "examples": [
            "Betting on a recognized city name being larger than an unrecognized one",
            "Choosing a recognized stock over an unrecognized one (less-is-more effect)",
        ],
    },
    "take_the_best_heuristic": {
        "description": "Decide between alternatives using a single best discriminating cue; stop search after finding one cue that discriminates",
        "discovered_by": "Gigerenzer & Goldstein (1996)",
        "steps": ["search_cues_in_order_of_validity", "stop_on_first_discriminating_cue", "decide_accordingly"],
        "efficiency": "Often more accurate than complex regression in natural environments (one-reason decision-making)",
        "examples": [
            "Choosing the city with the more recognized name as larger rather than comparing multiple population cues",
            "Emergency room triage — using a single symptom cue to decide treatment priority",
        ],
    },
    "gaze_heuristic": {
        "description": "Fixate on a moving target and adjust speed so the angle of gaze remains constant to intercept it",
        "discovered_by": "Gigerenzer (2007) — simple heuristic underlying expert ball-catching",
        "mechanism": "Maintain constant optical angle to the target; no complex trajectory computation needed",
        "domain": "Embodied cognition — how expert athletes solve complex physical problems with simple rules",
    },
    "social_circle_heuristic": {
        "description": "Allocate trust and cooperation based on social distance — reciprocate with close ties, be wary of distant ties",
        "examples": [
            "Tit-for-tat with strangers but unconditional cooperation with family",
            "Dunbar's number (~150) as cognitive limit on stable social relationships",
        ],
        "evolutionary_basis": "Kin selection (Hamilton's rule) + reciprocal altruism (Trivers)",
    },
}

for _heuristic in HEURISTICS.values():
    _heuristic.setdefault("examples", ["Representative real-world decision cue."])

# ============================================================================
# MIRROR NEURONS & SOCIAL COGNITION
# ============================================================================

MIRROR_NEURON_SYSTEMS: dict[str, dict[str, Any]] = {
    "parietofrontal_mirror_system": {
        "location": ["inferior_frontal_gyrus", "inferior_parietal_lobule"],
        "function": "Action recognition and execution matching",
        "discovery": "Macaque F5 neurons fire during both grasping and watching grasping (Rizzolatti et al., 1996)",
        "theories": [
            "direct_matching_hypothesis — we understand actions by mapping them onto our own motor repertoire",
            "simulation_theory — mirror neurons simulate observed actions in the observer's brain",
        ],
    },
    "emotional_mirror_system": {
        "location": ["anterior_insula", "anterior_cingulate_cortex", "amygdala"],
        "function": "Shared neural representations for experienced and observed emotions",
        "evidence": [
            "Feeling disgust and watching facial disgust expressions both activate anterior insula (Wicker et al., 2003)",
            "Experiencing pain and observing a loved one in pain activate overlapping ACC/insula regions (Singer et al., 2004)",
        ],
    },
    "theory_of_mind_network": {
        "location": ["medial_prefrontal_cortex", "temporoparietal_junction", "precuneus", "superior_temporal_sulcus"],
        "function": "Inferring mental states (beliefs, desires, intentions) of others",
        "landmark_studies": [
            "False-belief task (Wimmer & Perner, 1983) — children <4 fail; tests understanding that others can hold false beliefs",
            "Sally-Anne task (Baron-Cohen, Leslie, Frith, 1985) — autism-specific ToM deficit",
        ],
        "developmental_milestones": [
            "joint_attention (9-12 months)",
            "pretend_play (18-24 months)",
            "first_order_false_belief (4 years)",
            "second_order_false_belief (6-7 years)",
        ],
        "clinical_relevance": [
            "autism (ToM deficit — mindblindness hypothesis)",
            "schizophrenia (impaired mentalizing correlated with negative symptoms)",
        ],
    },
    "empathy_dual_system": {
        "components": {
            "cognitive_empathy": "Understand another's perspective (ToM network)",
            "emotional_empathy": "Share another's emotional state (mirror + limbic system)",
        },
        "modulation_factors": ["perceived_fairness", "group_membership", "emotional_closeness", "oxytocin_levels"],
        "measurement": ["Interpersonal Reactivity Index (IRI)", "Empathy Quotient (EQ)", "Reading the Mind in the Eyes Test"],
        "clinical_relevance": [
            "psychopathy (intact cognitive empathy, impaired emotional empathy)",
            "autism (impaired cognitive empathy, intact emotional empathy)",
        ],
    },
}

# ============================================================================
# MEMORY SYSTEMS
# ============================================================================

MEMORY_SYSTEMS: dict[str, dict[str, Any]] = {
    "sensory_memory": {
        "type": "very_short_term (<1 sec visual, 2-4 sec auditory)",
        "subtypes": {
            "iconic_memory": "Visual sensory register (Sperling, 1960 — partial report paradigm)",
            "echoic_memory": "Auditory sensory register (~3-4 second buffer)",
        },
        "capacity": "Large (near-veridical) but decays rapidly",
        "encoding": "Automatic, pre-attentive",
        "neural_basis": "Sustained sensory cortex activation",
    },
    "working_memory": {
        "model": "Baddeley & Hitch (1974) — multicomponent model",
        "components": {
            "central_executive": "Attention control, coordination of subsystems",
            "phonological_loop": "Verbal/auditory information (~2 sec rehearsal loop, 7±2 items)",
            "visuospatial_sketchpad": "Visual and spatial information",
            "episodic_buffer": "Multimodal integration, links to long-term memory (added 2000)",
        },
        "capacity": "7±2 chunks (Miller, 1956); more recent: ~4 chunks (Cowan, 2001)",
        "neural_basis": "Prefrontal cortex sustained activity; posterior cortex for domain-specific storage",
        "clinical_relevance": ["ADHD (working memory deficits)", "schizophrenia", "aging"],
    },
    "episodic_memory": {
        "type": "long_term_memory (declarative/explicit)",
        "definition": "Memory for personally experienced events with spatial-temporal context",
        "brain_regions": ["hippocampus", "parahippocampal_cortex", "retrosplenial_cortex", "prefrontal_cortex"],
        "encoding_factors": ["emotional_arousal", "depth_of_processing", "sleep_consolidation"],
        "phenomena": [
            "mental_time_travel — re-experiencing past events (Tulving, 1983)",
            "flashbulb_memories — vivid memories of emotionally shocking events (Brown & Kulik, 1977)",
            "infantile_amnesia — inability to recall events from ~<3 years of age",
        ],
        "clinical_relevance": [
            "Alzheimers_disease (earliest episodic memory deficits)",
            "retrograde_amnesia (loss of pre-injury episodic memories)",
            "anterograde_amnesia (inability to form new episodic memories)",
        ],
    },
    "semantic_memory": {
        "type": "long_term_memory (declarative/explicit)",
        "definition": "General world knowledge, facts, concepts, and vocabulary — detached from specific contexts",
        "brain_regions": ["anterior_temporal_lobe", "inferior_prefrontal_cortex", "temporoparietal_cortex"],
        "organization": "Hierarchical semantic networks with spreading activation",
        "phenomena": [
            "tip_of_the_tongue — retrieval failure despite partial access (phonological, not semantic deficit)",
            "semantic_dementia — progressive loss of conceptual knowledge (anterior temporal lobe atrophy)",
        ],
    },
    "procedural_memory": {
        "type": "long_term_memory (non_declarative/implicit)",
        "definition": "Memory for skills, habits, and procedures — knowing how, not knowing that",
        "brain_regions": ["basal_ganglia", "cerebellum", "motor_cortex", "supplementary_motor_area"],
        "characteristics": ["gradual_acquisition", "resistant_to_amnesia", "expressed_through_performance"],
        "examples": ["riding_a_bicycle", "playing_piano", "typing", "mirror_drawing_task"],
        "clinical_relevance": [
            "Parkinsons_disease (procedural learning deficits)",
            "Huntingtons_disease (motor skill learning deficits)",
            "Patient HM (intact procedural memory despite severe amnesia)",
        ],
    },
    "conditioned_memory": {
        "type": "long_term_memory (non_declarative/implicit)",
        "subtypes": {
            "classical_conditioning": "Association between neutral and biologically significant stimuli (Pavlov)",
            "operant_conditioning": "Behavior shaped by consequences — reinforcement and punishment (Skinner)",
            "fear_conditioning": "Amygdala-dependent learning of threat associations (LeDoux)",
        },
        "brain_regions": ["amygdala (fear)", "cerebellum (motor conditioned responses)", "basal_ganglia (habit)"],
        "clinical_relevance": ["PTSD (overgeneralized fear conditioning)", "phobias", "addiction (conditioned cues)"],
    },
    "priming": {
        "type": "long_term_memory (non_declarative/implicit)",
        "definition": "Prior exposure to a stimulus influences response to a later stimulus without conscious recollection",
        "subtypes": {
            "perceptual_priming": "Form-based — word stem completion, picture identification (posterior neocortex)",
            "conceptual_priming": "Meaning-based — category exemplar generation (prefrontal cortex)",
        },
        "characteristics": ["preserved_in_amnesia", "modality_specific", "can_persist_for_days_to_months"],
    },
}

# ============================================================================
# LEARNING MECHANISMS
# ============================================================================

LEARNING_MECHANISMS: dict[str, dict[str, Any]] = {
    "long_term_potentiation": {
        "definition": "Persistent strengthening of synapses based on recent patterns of activity — cellular basis of learning",
        "mechanism": "High-frequency stimulation → postsynaptic depolarization → NMDA receptor activation → Ca2+ influx → AMPA receptor insertion → strengthened synapse",
        "properties": ["input_specificity", "cooperativity", "associativity", "persistence"],
        "discovered_by": "Bliss & Lomo (1973) — rabbit hippocampus perforant path",
        "critical_molecules": ["NMDA_receptors", "CaMKII", "AMPA_receptors", "CREB"],
        "role": "Synaptic basis of memory formation — 'neurons that fire together wire together' (Hebb's postulate)",
    },
    "long_term_depression": {
        "definition": "Persistent weakening of synaptic strength — prevents saturation and enables forgetting",
        "mechanism": "Low-frequency stimulation → moderate Ca2+ rise → phosphatase cascade → AMPA receptor internalization",
        "role": "Synaptic pruning, motor learning refinement, cerebellar motor learning",
        "relationship_to_LTP": "Bidirectional plasticity; LTD is not merely reverse of LTP but a distinct mechanism",
    },
    "spike_timing_dependent_plasticity": {
        "definition": "Temporal order of pre- and postsynaptic spikes determines whether LTP or LTD occurs",
        "rule": "Pre-before-post (<20ms) → LTP. Post-before-pre → LTD.",
        "significance": "More biologically realistic than rate-based learning; temporal credit assignment",
        "mechanism": "NMDA receptor as coincidence detector — back-propagating action potential timing matters",
    },
    "reinforcement_learning_dopamine": {
        "definition": "Learning from reward and punishment via dopamine-mediated prediction error signals",
        "dopamine_encoding": "Dopamine neurons fire for unexpected reward; suppress for omitted expected reward; shift to predictive cues",
        "temporal_difference_learning": "Dopamine signal = R + gamma*V(s') - V(s) (Sutton & Barto, 1998)",
        "brain_regions": ["ventral_tegmental_area", "nucleus_accumbens", "prefrontal_cortex"],
        "clinical_relevance": ["addiction (hijacked reward prediction error)", "Parkinsons (dopamine medication and impulse control)"],
    },
    "hebbian_learning": {
        "definition": "Simultaneous activation of pre- and postsynaptic neurons strengthens their connection",
        "hebbian_principle": "When an axon of cell A repeatedly and persistently takes part in firing cell B, some growth or metabolic change occurs",
        "formulation": "Delta_w_ij = eta * x_i * y_j (simple correlational form)",
        "limitations": "Unstable without normalization; requires anti-Hebbian or homeostatic mechanisms",
        "modern_variants": ["BCM theory", "Oja's rule", "competitive Hebbian learning"],
    },
    "error_based_learning": {
        "definition": "Learning driven by the discrepancy between predicted and actual outcomes",
        "cerebellar_implementation": "Climbing fibers (from inferior olive) carry error signals → modify parallel fiber-Purkinje cell synapses via LTD",
        "role": "Motor adaptation, prism adaptation, sensorimotor calibration",
        "contrast_with_reinforcement": "Error-based learning uses a direct error signal; reinforcement learning uses scalar reward/punishment",
    },
    "observational_learning": {
        "definition": "Learning by watching others — does not require direct experience",
        "bandura_model": "Attention → Retention → Reproduction → Motivation (Bandura, 1977 — Social Learning Theory)",
        "neural_basis": "Mirror neuron system + prefrontal executive control",
        "examples": [
            "Children imitating aggressive behavior toward Bobo doll (Bandura et al., 1961)",
            "Skill acquisition via demonstration rather than trial-and-error",
        ],
    },
}

# ============================================================================
# NEUROPSYCHIATRIC CONDITIONS
# ============================================================================

NEUROPSYCHIATRIC_CONDITIONS: dict[str, dict[str, Any]] = {
    "major_depressive_disorder": {
        "neurobiology": {
            "monoamine_hypothesis": "Deficient serotonin, norepinephrine, and dopamine signaling (basis for SSRIs/SNRIs)",
            "neurotrophic_hypothesis": "Reduced BDNF → hippocampal atrophy → impaired neurogenesis and plasticity",
            "HPA_axis_dysregulation": "Hypercortisolemia from impaired negative feedback (dexamethasone non-suppression)",
            "neuroinflammation": "Elevated pro-inflammatory cytokines (IL-6, TNF-alpha) correlate with depression severity",
            "network_level": "DMN hyperconnectivity (rumination) + reduced CEN-DMN anticorrelation + blunted reward circuit",
        },
        "affected_regions": ["prefrontal_cortex", "hippocampus", "amygdala", "anterior_cingulate"],
        "treatments": ["SSRIs", "SNRIs", "CBT", "ECT", "TMS", "ketamine (rapid-acting)"],
    },
    "generalized_anxiety_disorder": {
        "neurobiology": {
            "amygdala_hyperreactivity": "Exaggerated amygdala response to neutral and threatening stimuli",
            "prefrontal_hyporegulation": "Reduced vmPFC top-down inhibition of amygdala",
            "GABA_deficiency": "Reduced GABA-A receptor binding → insufficient inhibitory tone",
            "serotonin_dysfunction": "5-HT1A autoreceptor hypersensitivity → reduced serotonergic tone",
        },
        "affected_regions": ["amygdala", "prefrontal_cortex", "anterior_cingulate", "bed_nucleus_of_stria_terminalis"],
        "treatments": ["SSRIs", "CBT", "benzodiazepines", "buspirone"],
    },
    "schizophrenia": {
        "neurobiology": {
            "dopamine_hypothesis": "Mesolimbic hyperdopaminergia (positive symptoms) + mesocortical hypodopaminergia (negative/cognitive symptoms)",
            "glutamate_hypothesis": "NMDA receptor hypofunction → disinhibition of glutamate release → excitotoxicity",
            "neurodevelopmental_model": "Early synaptic pruning abnormalities → adolescent-onset circuit disruption",
            "network_level": "DMN hyperconnectivity + reduced DMN-CEN anticorrelation; thalamocortical dysrhythmia",
        },
        "symptoms": {
            "positive": ["hallucinations", "delusions", "disorganized_speech", "disorganized_behavior"],
            "negative": ["anhedonia", "avolition", "flat_affect", "alogia", "social_withdrawal"],
            "cognitive": ["working_memory_deficits", "attention_impairment", "executive_dysfunction"],
        },
        "affected_regions": ["prefrontal_cortex", "hippocampus", "thalamus", "striatum", "superior_temporal_gyrus"],
        "treatments": ["antipsychotics (D2 antagonists)", "CBT_for_psychosis", "social_skills_training", "clozapine_for_treatment_resistant"],
    },
    "bipolar_disorder": {
        "neurobiology": {
            "circadian_dysregulation": "Clock gene variants (CLOCK, BMAL1); social rhythm instability",
            "mitochondrial_dysfunction": "Impaired energy metabolism; elevated lactate in brain",
            "calcium_signaling": "CACNA1C gene variants — L-type calcium channel dysregulation",
            "dopamine_sensitivity": "Behavioral sensitization model — each episode increases sensitivity to future episodes",
        },
        "affected_regions": ["prefrontal_cortex", "amygdala", "anterior_cingulate", "striatum", "cerebellum"],
        "treatments": ["lithium", "valproate", "lamotrigine", "quetiapine", "interpersonal_social_rhythm_therapy"],
    },
    "ADHD": {
        "neurobiology": {
            "executive_dysfunction": "Prefrontal hypodopaminergia → impaired working memory, attention, inhibition",
            "default_mode_network_intrusion": "Insufficient DMN deactivation during tasks → mind-wandering, distractibility",
            "delayed_cortical_maturation": "~2-3 year delay in cortical thickness maturation (Shaw et al., 2007)",
            "catecholamine_dysregulation": "Dopamine transporter (DAT1) and dopamine D4 receptor (DRD4) gene variants",
        },
        "affected_regions": ["prefrontal_cortex", "striatum", "cerebellum", "anterior_cingulate", "parietal_cortex"],
        "treatments": ["stimulants (methylphenidate, amphetamine)", "atomoxetine (norepinephrine reuptake inhibitor)", "CBT", "executive_function_coaching"],
    },
    "PTSD": {
        "neurobiology": {
            "fear_circuit_dysregulation": "Amygdala hyperreactivity + prefrontal hyporegulation → failure of fear extinction",
            "hippocampal_atrophy": "Glucocorticoid neurotoxicity from chronic HPA axis overactivation → reduced hippocampal volume",
            "noradrenergic_hyperactivity": "Elevated baseline norepinephrine and exaggerated response to traumatic reminders",
            "contextual_processing_deficit": "Hippocampus fails to encode safety contexts → generalization of fear",
        },
        "affected_regions": ["amygdala", "hippocampus", "ventromedial_prefrontal_cortex", "anterior_cingulate", "insula"],
        "treatments": ["trauma_focused_CBT", "EMDR", "prolonged_exposure", "SSRIs", "prazosin (for nightmares)"],
    },
    "obsessive_compulsive_disorder": {
        "neurobiology": {
            "cortico_striato_thalamo_cortical_loop": "Hyperactivity in orbitofrontal cortex → striatum → thalamus → cortex loop",
            "serotonin_dysfunction": "SSRI efficacy suggests serotonergic involvement",
            "glutamate_dysregulation": "Elevated glutamate in anterior cingulate; riluzole shows some efficacy",
        },
        "affected_regions": ["orbitofrontal_cortex", "anterior_cingulate", "caudate_nucleus", "thalamus"],
        "treatments": ["SSRIs (high-dose)", "CBT_with_ERP (exposure and response prevention)", "deep_brain_stimulation (severe)"],
    },
    "autism_spectrum_disorder": {
        "neurobiology": {
            "atypical_connectivity": "Local overconnectivity + long-range underconnectivity hypothesis",
            "excitation_inhibition_imbalance": "Elevated ratio of excitation to inhibition (E/I imbalance)",
            "synaptic_pruning_deficits": "Reduced synaptic pruning → excess synapses (mTOR pathway; fragile X overlap)",
            "oxytocin_signaling": "Lower plasma oxytocin; intranasal oxytocin trials show mixed results",
        },
        "affected_regions": ["prefrontal_cortex", "amygdala", "fusiform_face_area", "superior_temporal_sulcus", "cerebellum"],
        "treatments": ["applied_behavior_analysis", "speech_language_therapy", "social_skills_training", "supported_employment"],
    },
    "Alzheimers_disease": {
        "neurobiology": {
            "amyloid_cascade": "Amyloid-beta plaques → synaptic dysfunction → tau hyperphosphorylation → neurofibrillary tangles → neurodegeneration",
            "cholinergic_degeneration": "Basal forebrain cholinergic neuron loss → cortical acetylcholine deficit",
            "default_mode_network": "Earliest amyloid deposition in DMN hubs (precuneus, posterior cingulate, medial PFC)",
            "hippocampal_atrophy": "Earliest and most severe atrophy → episodic memory impairment",
        },
        "biomarkers": ["CSF_amyloid_beta_42", "CSF_phosphorylated_tau", "amyloid_PET", "tau_PET", "MRI_hippocampal_volumetry"],
        "affected_regions": ["hippocampus", "entorhinal_cortex", "default_mode_network_nodes", "basal_forebrain"],
        "treatments": ["cholinesterase_inhibitors", "memantine", "anti_amyloid_monoclonal_antibodies, aducanumab, lecanemab"],
    },
    "borderline_personality_disorder": {
        "neurobiology": {
            "amygdala_hyperreactivity": "Exaggerated amygdala response to emotional stimuli, especially social rejection",
            "prefrontal_dysregulation": "Reduced PFC top-down control during emotion regulation",
            "oxytocin_paradox": "Oxytocin may increase mistrust and reduce cooperation in BPD (contrary to healthy controls)",
            "endogenous_opioid_deficiency": "Hypothesized deficit in mu-opioid receptor activation → emotional pain sensitivity and self-harm",
        },
        "affected_regions": ["amygdala", "prefrontal_cortex", "anterior_cingulate", "hippocampus", "insula"],
        "treatments": ["DBT (dialectical behavior therapy)", "mentalization_based_treatment", "SSRIs", "mood_stabilizers"],
    },
    "Parkinsons_disease": {
        "neurobiology": {
            "dopaminergic_degeneration": "Progressive loss of dopaminergic neurons in substantia nigra pars compacta → striatal dopamine depletion",
            "alpha_synuclein_pathology": "Lewy bodies — aggregated alpha-synuclein protein inclusions",
            "Braak_staging": "Pathology ascends from brainstem (stage 1-2) → midbrain/substantia nigra (stage 3-4) → cortex (stage 5-6)",
        },
        "affected_regions": ["substantia_nigra", "striatum", "globus_pallidus", "thalamus", "cortex"],
        "motor_symptoms": ["bradykinesia", "resting_tremor", "rigidity", "postural_instability"],
        "non_motor_symptoms": ["anosmia", "REM_sleep_behavior_disorder", "depression", "cognitive_decline", "autonomic_dysfunction"],
        "treatments": ["levodopa_carbidopa", "dopamine_agonists", "MAO_B_inhibitors", "deep_brain_stimulation"],
    },
}

# ============================================================================
# FUNCTIONS
# ============================================================================


def analyze_cognitive_bias(text: str | None) -> dict[str, Any]:
    """Analyze text for cognitive bias markers.

    Returns detected biases with per-bias score breakdown and overall assessment.
    """
    if not text:
        return {
            "biases_detected": [],
            "bias_scores": {},
            "dominant_bias": None,
            "overall_bias_index": 0.0,
            "recommendation": "Insufficient input for bias analysis.",
            "text_length": 0,
        }

    text_lower = text.lower()
    bias_markers: dict[str, list[str]] = {
        "confirmation_bias": [
            "proves what i always", "knew it all along", "confirms my",
            "as expected", "just as i thought", "see, i told you",
            "only read", "echo chamber",
        ],
        "anchoring_bias": [
            "starting price", "initial offer", "first number",
            "around", "about", "approximately",
            "reference point",
        ],
        "availability_heuristic": [
            "i just saw", "recently", "in the news", "everyone is talking about",
            "vivid example", "i remember", "it happened to my friend",
        ],
        "framing_effect": [
            "percent chance of living", "percent chance of dying",
            "lean", "fat-free", "guarantee",
            "lose out on", "miss out",
        ],
        "loss_aversion": [
            "don't miss", "last chance", "lose", "losing",
            "limited time", "never again", "protect your",
            "fear of missing", "act now",
        ],
        "sunk_cost_fallacy": [
            "already invested", "come this far", "can't quit now",
            "spent so much", "put in too much", "might as well finish",
        ],
        "fundamental_attribution_error": [
            "lazy", "incompetent", "stupid", "just a bad person",
            "it's their fault", "they always", "they never",
            "typical of them",
        ],
        "overconfidence_bias": [
            "i'm absolutely certain", "no doubt", "i guarantee",
            "i know for a fact", "trust me", "i'm sure",
            "definitely", "obviously", "without question",
        ],
        "negativity_bias": [
            "terrible", "awful", "worst", "disaster",
            "everything is wrong", "nothing goes right",
            "it's all bad",
        ],
        "in_group_bias": [
            "us versus them", "our kind of people", "one of us",
            "they are all", "people like them", "not our type",
        ],
    }

    scores: dict[str, float] = {}
    for bias_name, markers in bias_markers.items():
        hits = sum(1 for m in markers if m in text_lower)
        score = min(1.0, hits / max(1, len(markers)) * (1.5 if hits > 1 else 1.0))
        scores[bias_name] = score

    detected = sorted(
        [name for name, score in scores.items() if score > 0.0],
        key=lambda n: scores[n],
        reverse=True,
    )

    overall = max(scores.values()) if scores else 0.0

    recommendation = "No significant cognitive bias detected."
    if overall > 0.2:
        recommendation = "High cognitive bias detected — multiple patterns present. Apply debiasing: seek disconfirming evidence, consider base rates, broaden framing."
    elif overall > 0.08:
        recommendation = "Moderate cognitive bias detected. Consider alternative perspectives and verify with objective data."
    elif overall > 0.02:
        recommendation = "Mild cognitive bias detected — low-level pattern recognition. Awareness sufficient for most contexts."

    return {
        "biases_detected": detected,
        "bias_scores": scores,
        "dominant_bias": detected[0] if detected else None,
        "overall_bias_index": round(overall, 4),
        "recommendation": recommendation,
        "text_length": len(text),
    }


def classify_neural_circuit(description: str) -> dict[str, Any]:
    """Classify a functional description into a neural circuit category.

    Returns the best-matching circuit and confidence.
    """
    if not description:
        return {
            "matched_circuit": "unknown",
            "confidence": 0.0,
            "alternative_matches": [],
            "all_scores": {},
        }

    text_lower = description.lower()

    circuit_keywords: dict[str, list[str]] = {
        "default_mode_network": [
            "resting", "mind wandering", "daydreaming", "self referential",
            "autobiographical", "mental time travel", "default mode",
            "spontaneous thought", "internal mentation",
        ],
        "salience_network": [
            "salient", "detection", "relevant stimuli", "switching",
            "alerting", "interoceptive", "warning signal", "attention capture",
            "unexpected", "behaviorally relevant",
        ],
        "central_executive_network": [
            "working memory", "problem solving", "cognitive control",
            "goal", "planning", "executive", "task focused", "concentration",
            "reasoning", "decision", "logic",
        ],
        "reward_circuit": [
            "reward", "pleasure", "motivation", "dopamine",
            "wanting", "liking", "reinforcement", "addiction",
            "craving", "incentive",
        ],
        "fear_circuit": [
            "fear", "threat", "danger", "anxiety", "conditioned",
            "freezing", "startle", "defensive", "amygdala",
            "extinction", "traumatic",
        ],
        "mirror_neuron_system": [
            "imitation", "mirror", "observation", "empathy",
            "action understanding", "vicarious", "watching",
            "modeling", "mimicry",
        ],
    }

    scores: dict[str, float] = {}
    for circuit_name, keywords in circuit_keywords.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        score = hits / max(1, len(keywords))
        scores[circuit_name] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_name, best_score = ranked[0] if ranked else ("unknown", 0.0)

    alternatives = [(name, round(score, 3)) for name, score in ranked[1:4] if score > 0.0]

    return {
        "matched_circuit": best_name if best_score > 0 else "unknown",
        "confidence": round(best_score, 3),
        "alternative_matches": alternatives,
        "all_scores": {k: round(v, 3) for k, v in scores.items()},
    }


def assess_decision_making(heuristic_cues: dict[str, Any] | None) -> dict[str, Any]:
    """Assess decision-making quality and heuristic usage from observed cues.

    Accepts a dict with optional fields: context, time_pressure, stakes, confidence, alternatives_considered.
    """
    context = (heuristic_cues or {}).get("context", "")
    time_pressure = (heuristic_cues or {}).get("time_pressure", "moderate")
    stakes = (heuristic_cues or {}).get("stakes", "moderate")
    alternatives_considered = (heuristic_cues or {}).get("alternatives_considered", 1)
    confidence = (heuristic_cues or {}).get("confidence", 0.5)
    domain = (heuristic_cues or {}).get("domain", "general")

    if not heuristic_cues:
        return {
            "likely_heuristics": [],
            "decision_quality": "unknown",
            "risk_of_bias": "unknown",
            "recommendations": ["Insufficient information to assess decision-making."],
        }

    context_lower = str(context).lower()

    heuristic_keywords: dict[str, list[str]] = {
        "representativeness_heuristic": [
            "looks like", "typical", "seems like", "stereotype",
            "resembles", "representative", "characteristic of",
        ],
        "affect_heuristic": [
            "feels right", "gut feeling", "feels good about",
            "doesn't feel right", "bad vibe", "intuition",
        ],
        "recognition_heuristic": [
            "recognize", "familiar", "heard of", "well known",
            "brand name", "household name",
        ],
        "availability_heuristic": [
            "recent", "in the news", "just happened", "vivid",
            "memorable", "everyone knows",
        ],
        "anchoring_heuristic": [
            "starting at", "was originally", "initial", "first quote",
            "base price", "reference",
        ],
        "take_the_best_heuristic": [
            "most important factor", "the key thing is", "what matters most",
            "the deciding factor", "just based on",
        ],
    }

    likely: list[str] = []
    for heuristic, keywords in heuristic_keywords.items():
        if any(kw in context_lower for kw in keywords):
            likely.append(heuristic)

    time_pressure_str = str(time_pressure).lower()
    if time_pressure_str in ("high", "extreme"):
        if not likely:
            likely = ["recognition_heuristic", "take_the_best_heuristic"]

    risk = "low"
    if stakes in ("high", "critical"):
        risk = "high"
        if likely:
            risk = "critical"
    elif time_pressure_str in ("high", "extreme"):
        risk = "moderate" if risk == "low" else "high"

    alt_count = int(alternatives_considered) if isinstance(alternatives_considered, int) else 1
    confidence_val = float(confidence) if isinstance(confidence, (float, int)) else 0.5

    quality = "adequate"
    if alt_count <= 1 and confidence_val > 0.7 and risk in ("high", "critical"):
        quality = "poor"
    elif alt_count >= 3:
        quality = "good"
    elif alt_count >= 2 or confidence_val < 0.5:
        quality = "adequate"

    recommendations: list[str] = []
    if "representativeness_heuristic" in likely:
        recommendations.append("Check base rates — 'typical' appearance does not guarantee probability.")
    if "affect_heuristic" in likely:
        recommendations.append("Supplement gut feeling with quantitative risk assessment.")
    if "availability_heuristic" in likely:
        recommendations.append("Consult statistical data — recent or vivid events distort probability estimates.")
    if "anchoring_heuristic" in likely:
        recommendations.append("Consider multiple independent reference points — first number is not neutral.")
    if alt_count <= 1:
        recommendations.append("Generate additional alternatives before deciding — at least 3 options reduces regret.")
    if confidence_val > 0.8 and quality == "poor":
        recommendations.append("High confidence with inadequate alternatives suggests overconfidence — calibrate with a pre-mortem.")
    if not recommendations:
        recommendations.append("Decision process appears reasonable. Continue monitoring with periodic review.")

    return {
        "likely_heuristics": likely,
        "decision_quality": quality,
        "risk_of_bias": risk,
        "alternatives_considered": alt_count,
        "recommendations": recommendations,
    }


def evaluate_empathy_response(interaction_context: dict[str, Any] | None) -> dict[str, Any]:
    """Evaluate empathy-related factors in a social interaction context.

    Accepts keys: perspective_taking, emotional_contagion, prosocial_intent,
    emotional_accuracy, attention_to_other, verbal_acknowledgment.
    """
    if not interaction_context:
        return {
            "empathy_level": "unknown",
            "cognitive_empathy_score": 0.0,
            "emotional_empathy_score": 0.0,
            "composite_empathy": 0.0,
            "factors": {},
            "recommendation": "Insufficient information to evaluate empathy response.",
        }

    perspective_taking = float(interaction_context.get("perspective_taking", 0.0))
    emotional_contagion = float(interaction_context.get("emotional_contagion", 0.0))
    prosocial_intent = float(interaction_context.get("prosocial_intent", 0.0))
    emotional_accuracy = float(interaction_context.get("emotional_accuracy", 0.0))
    attention_to_other = float(interaction_context.get("attention_to_other", 0.0))
    verbal_acknowledgment = float(interaction_context.get("verbal_acknowledgment", 0.0))

    cognitive_empathy = (perspective_taking + emotional_accuracy) / 2.0
    emotional_empathy = (emotional_contagion + prosocial_intent + attention_to_other) / 3.0

    composite = (cognitive_empathy * 0.5 + emotional_empathy * 0.5 + verbal_acknowledgment * 0.3) / 1.3
    composite = round(max(0.0, min(1.0, composite)), 3)

    level = "low"
    if composite >= 0.7:
        level = "high"
    elif composite >= 0.4:
        level = "moderate"

    factors = {
        "cognitive_empathy": round(cognitive_empathy, 3),
        "emotional_empathy": round(emotional_empathy, 3),
        "verbal_acknowledgment": round(verbal_acknowledgment, 3),
        "profile": _empathy_profile(cognitive_empathy, emotional_empathy),
    }

    recommendation = "Empathic response adequate."
    if composite < 0.3:
        recommendation = "Low empathy — consider explicit perspective-taking exercises and emotional labeling practice."
    elif composite < 0.5:
        if cognitive_empathy < emotional_empathy:
            recommendation = "Moderate emotional empathy but low cognitive empathy — practice explicit perspective-taking and mentalizing."
        else:
            recommendation = "Moderate cognitive empathy but lower emotional resonance — practice emotional labeling and active listening."

    return {
        "empathy_level": level,
        "cognitive_empathy_score": round(cognitive_empathy, 3),
        "emotional_empathy_score": round(emotional_empathy, 3),
        "composite_empathy": composite,
        "factors": factors,
        "recommendation": recommendation,
    }


def _empathy_profile(cognitive: float, emotional: float) -> str:
    if cognitive > 0.7 and emotional > 0.7:
        return "balanced_high_empathy"
    if cognitive > 0.7 and emotional < 0.4:
        return "high_cognitive_low_emotional (psychopathy-like)"
    if cognitive < 0.4 and emotional > 0.7:
        return "high_emotional_low_cognitive (emotional_contagion_without_understanding)"
    if cognitive < 0.4 and emotional < 0.4:
        return "low_empathy_overall"
    return "moderate_balanced"


def classify_memory_subtype(description: str) -> dict[str, Any]:
    """Classify a memory-related description into a memory system subtype.

    Returns the best-matching system, confidence, and alternative matches.
    """
    if not description:
        return {
            "matched_system": "unknown",
            "confidence": 0.0,
            "alternative_matches": [],
            "all_scores": {},
        }

    text_lower = description.lower()

    memory_keywords: dict[str, list[str]] = {
        "sensory_memory": [
            "fleeting", "momentary", "sensory register", "trace",
            "echoic", "iconic", "afterimage", "brief impression",
        ],
        "working_memory": [
            "holding in mind", "mental workspace", "juggling", "keep track",
            "in mind", "rehearsing", "phone number", "remembering temporarily",
            "short term", "seven plus or minus two",
        ],
        "episodic_memory": [
            "i remember when", "that time", "personal experience", "episode",
            "event in my life", "first person", "reliving", "mental time travel",
            "autobiographical", "flashback",
        ],
        "semantic_memory": [
            "fact", "knowledge", "concept", "meaning", "vocabulary",
            "definition", "encyclopedic", "general knowledge", "trivia",
            "i know that",
        ],
        "procedural_memory": [
            "knowing how", "skill", "riding a bike", "muscle memory",
            "habit", "procedure", "automatic", "without thinking",
            "practice makes", "how to",
        ],
        "conditioned_memory": [
            "pavlov", "conditioned", "triggered by", "associated with",
            "cue", "learned response", "bell", "stimulus",
        ],
        "priming": [
            "reminded of", "came to mind", "subliminal", "prior exposure",
            "implicit", "without awareness", "facilitated",
        ],
    }

    scores: dict[str, float] = {}
    for system_name, keywords in memory_keywords.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        score = hits / max(1, len(keywords))
        scores[system_name] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_name, best_score = ranked[0] if ranked else ("unknown", 0.0)

    alternatives = [(name, round(score, 3)) for name, score in ranked[1:4] if score > 0.0]

    return {
        "matched_system": best_name if best_score > 0 else "unknown",
        "confidence": round(best_score, 3),
        "alternative_matches": alternatives,
        "all_scores": {k: round(v, 3) for k, v in scores.items()},
    }


def analyze_neuropsychiatric_profile(symptoms: dict[str, Any] | None) -> dict[str, Any]:
    """Analyze a symptom profile against known neuropsychiatric conditions.

    Accepts keys: cognitive_symptoms, emotional_symptoms, behavioral_symptoms,
    motor_symptoms, duration, course, age_of_onset.
    Returns top matches with per-condition scores and differential analysis.
    """
    if symptoms is None:
        return {
            "top_matches": [],
            "condition_scores": {},
            "differential_considerations": [],
            "recommendation": "Insufficient symptoms to analyze. Provide at minimum cognitive, emotional, and behavioral symptoms.",
        }

    cognitive = symptoms.get("cognitive_symptoms", [])
    emotional = symptoms.get("emotional_symptoms", [])
    behavioral = symptoms.get("behavioral_symptoms", [])
    motor = symptoms.get("motor_symptoms", [])
    age_of_onset = str(symptoms.get("age_of_onset", "")).lower()
    course = str(symptoms.get("course", "")).lower()
    duration = str(symptoms.get("duration", "")).lower()

    all_symptoms: set[str] = set()
    if isinstance(cognitive, list):
        all_symptoms.update(s.lower() for s in cognitive)
    if isinstance(emotional, list):
        all_symptoms.update(s.lower() for s in emotional)
    if isinstance(behavioral, list):
        all_symptoms.update(s.lower() for s in behavioral)
    if isinstance(motor, list):
        all_symptoms.update(s.lower() for s in motor)

    if not all_symptoms:
        return {
            "top_matches": [],
            "condition_scores": {},
            "differential_considerations": [],
            "recommendation": "No specific symptoms provided to analyze.",
        }

    condition_patterns: dict[str, dict[str, Any]] = {
        "major_depressive_disorder": {
            "symptoms": [
                "anhedonia", "low_mood", "fatigue", "sleep_disturbance",
                "appetite_change", "concentration_difficulty", "worthlessness",
                "suicidal_ideation", "psychomotor_retardation", "guilt",
            ],
            "course": ["episodic", "recurrent"],
            "duration": ["weeks_to_months", "chronic"],
            "age": "any",
        },
        "generalized_anxiety_disorder": {
            "symptoms": [
                "excessive_worry", "restlessness", "fatigue", "concentration_difficulty",
                "irritability", "muscle_tension", "sleep_disturbance", "hypervigilance",
                "catastrophizing", "avoidance",
            ],
            "course": ["chronic", "waxing_and_waning"],
            "duration": ["months_to_years"],
            "age": "any (often late adolescence to early adulthood)",
        },
        "schizophrenia": {
            "symptoms": [
                "hallucinations", "delusions", "disorganized_speech",
                "disorganized_behavior", "flat_affect", "avolition",
                "social_withdrawal", "cognitive_decline", "paranoia",
                "thought_insertion", "thought_broadcasting",
            ],
            "course": ["episodic_with_residual", "chronic", "progressive"],
            "duration": ["months_to_lifetime"],
            "age": "late adolescence to early 30s",
        },
        "bipolar_disorder": {
            "symptoms": [
                "mania", "elevated_mood", "decreased_need_for_sleep",
                "grandiosity", "pressured_speech", "flight_of_ideas",
                "risk_taking", "depression", "hypersexuality",
                "impulsivity", "psychosis",
            ],
            "course": ["episodic", "cyclical"],
            "duration": ["days_to_weeks (manic episodes)"],
            "age": "late adolescence to early adulthood",
        },
        "ADHD": {
            "symptoms": [
                "inattention", "hyperactivity", "impulsivity",
                "disorganization", "forgetfulness", "fidgeting",
                "difficulty_sustaining_attention", "procrastination",
                "time_blindness", "emotional_dysregulation",
            ],
            "course": ["chronic", "developmental"],
            "duration": ["lifetime"],
            "age": "childhood onset (before age 12)",
        },
        "PTSD": {
            "symptoms": [
                "flashbacks", "nightmares", "hypervigilance", "avoidance",
                "intrusive_memories", "emotional_numbing", "startle_response",
                "dissociation", "irritability", "sleep_disturbance",
            ],
            "course": ["post_traumatic", "delayed_onset_possible"],
            "duration": ["months_to_years"],
            "age": "any (trauma exposure prerequisite)",
        },
        "obsessive_compulsive_disorder": {
            "symptoms": [
                "obsessions", "compulsions", "intrusive_thoughts",
                "repetitive_behaviors", "checking", "counting",
                "contamination_fears", "symmetry_needs", "hoarding",
                "rituals", "reassurance_seeking",
            ],
            "course": ["chronic", "waxing_and_waning"],
            "duration": ["months_to_years"],
            "age": "childhood to early adulthood",
        },
        "Alzheimers_disease": {
            "symptoms": [
                "memory_loss", "confusion", "disorientation", "language_difficulty",
                "impaired_judgment", "personality_changes", "apraxia",
                "agnosia", "executive_dysfunction", "progressive_cognitive_decline",
            ],
            "course": ["progressive", "insidious_onset"],
            "duration": ["years"],
            "age": "typically >65 (early onset possible at 40-65)",
        },
        "Parkinsons_disease": {
            "symptoms": [
                "tremor", "bradykinesia", "rigidity", "postural_instability",
                "shuffling_gait", "masked_facies", "micrographia",
                "anosmia", "constipation", "REM_sleep_behavior_disorder",
            ],
            "course": ["progressive"],
            "duration": ["years"],
            "age": "typically >60 (young onset possible)",
        },
    }

    scores: dict[str, float] = {}
    for condition, data in condition_patterns.items():
        symptom_hits = sum(1 for s in data["symptoms"] if any(
            s.replace("_", " ") in sym or sym in s.replace("_", " ")
            for sym in all_symptoms
        ))
        symptom_score = symptom_hits / max(1, len(data["symptoms"]))
        course_score = 0.1 if any(c_pattern in course for c_pattern in data["course"]) else 0.0
        age_score = 0.1 if age_of_onset and data["age"].replace(" ", "_").replace(",", "") in age_of_onset or "any" in data["age"] else 0.0
        scores[condition] = round(symptom_score + course_score + age_score, 3)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_matches = [(name, score) for name, score in ranked[:5] if score > 0.0]

    differential: list[str] = []
    if len(top_matches) >= 2:
        top_condition = top_matches[0][0]
        runner_up = top_matches[1][0]
        differential.append(f"Consider {top_condition} primarily with {runner_up} as differential — overlapping symptoms may require further assessment.")

    recommendation = "Neuropsychiatric assessment inconclusive with provided data."
    if top_matches and top_matches[0][1] > 0.4:
        recommendation = f"Profile most consistent with {top_matches[0][0]} — clinical evaluation recommended."
    elif top_matches and top_matches[0][1] > 0.2:
        recommendation = f"Partial match to {top_matches[0][0]} but insufficient symptom specificity — more detailed history needed."
    elif top_matches:
        recommendation = "Mild symptom overlap with multiple conditions — consider comprehensive neuropsychological assessment."

    return {
        "top_matches": [{"condition": name, "score": score} for name, score in top_matches],
        "condition_scores": scores,
        "differential_considerations": differential,
        "recommendation": recommendation,
    }
