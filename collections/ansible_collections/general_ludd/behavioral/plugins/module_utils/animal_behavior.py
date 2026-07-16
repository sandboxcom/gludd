"""Animal behavior analysis module: ethology, communication, stress,
social structures, training, and animal language research.

Public surface::

    classify_behavior(species, observation)           -> dict
    interpret_vocalization(species, audio_features)    -> dict
    recommend_training_approach(species, goal)         -> dict

    FIXED_ACTION_PATTERNS         dict[species] -> patterns
    ANIMAL_COMMUNICATION_MODES    dict[mode] -> properties
    STRESS_INDICATORS             dict[indicator] -> species
    SOCIAL_STRUCTURES             dict[structure] -> properties
    TRAINING_METHODS              dict[method] -> properties
    ANIMAL_LANGUAGE_RESEARCH      dict[species] -> findings
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Fixed Action Patterns (Ethology)
# ---------------------------------------------------------------------------

FIXED_ACTION_PATTERNS: dict[str, dict[str, Any]] = {
    "stickleback_zigzag_dance": {
        "species": "Gasterosteus aculeatus (three-spined stickleback)",
        "description": "Male performs zigzag dance in response to female's swollen belly (sign stimulus)",
        "trigger": "Red belly of rival male; swollen belly of female",
        "completion_criterion": "Once initiated, runs to completion even if stimulus is removed",
        "researcher": "Niko Tinbergen (Nobel 1973)",
        "category": "reproductive_behavior",
    },
    "greylag_goose_egg_retrieval": {
        "species": "Anser anser (greylag goose)",
        "description": "When an egg rolls out of the nest, the goose uses a stereotyped neck motion to roll it back",
        "trigger": "Displaced egg near nest",
        "completion_criterion": "Continues rolling motion even if egg is experimentally removed mid-action",
        "researcher": "Konrad Lorenz (Nobel 1973)",
        "category": "parental_care",
    },
    "herring_gull_begging": {
        "species": "Larus argentatus (herring gull)",
        "description": "Chicks peck at red spot on parent's bill to elicit regurgitation feeding",
        "trigger": "Red spot on bill (supernormal stimulus: exaggerated red-on-yellow elicits stronger response)",
        "completion_criterion": "Feeding response triggered even by painted stick with red spot",
        "researcher": "Niko Tinbergen",
        "category": "feeding_behavior",
    },
    "mating_dance_birds_of_paradise": {
        "species": "Paradisaeidae (birds of paradise)",
        "description": "Elaborate, stereotyped courtship display with specific movement sequences",
        "trigger": "Female presence in display court",
        "completion_criterion": "Stimulus-dependent; display intensity varies with female response",
        "category": "courtship_display",
    },
    "spider_web_building": {
        "species": "Araneus diadematus (garden spider)",
        "description": "Innate, species-specific stereotyped web-building sequence",
        "trigger": "Internal state (hunger, circadian rhythm)",
        "completion_criterion": "Sequence completed regardless of partial web destruction",
        "category": "foraging_behavior",
    },
}

# ---------------------------------------------------------------------------
# Imprinting and Instinctive Behavior
# ---------------------------------------------------------------------------

IMPRINTING_DATA: dict[str, dict[str, Any]] = {
    "filial_imprinting": {
        "description": "Young animal forms attachment to the first moving object it encounters during critical period",
        "species_examples": ["ducklings", "goslings", "chicks (precocial birds)"],
        "critical_period": "13-16 hours post-hatching in mallard ducklings",
        "irreversibility": "Once formed, attachment cannot be transferred to another object",
        "researcher": "Konrad Lorenz — goslings imprinted on his boots",
    },
    "sexual_imprinting": {
        "description": "Early-life exposure shapes adult mate preferences",
        "species_examples": ["zebra_finches", "japanese_quail", "sheep", "humans"],
        "westermarck_effect": "Close cohabitation during early childhood reduces adult sexual attraction (human reverse sexual imprinting)",
        "cross_fostering_evidence": "Birds raised by different species prefer mates of the foster species",
    },
    "song_imprinting": {
        "description": "Young birds learn species-specific song by hearing adults during sensitive period",
        "species_examples": ["zebra_finches", "white_crowned_sparrows"],
        "sensitive_period": "20-60 days post-hatch in white-crowned sparrows",
        "sensorimotor_phase": "Sub-song (babbling) followed by crystallization into adult song",
        "isolation_effect": "Birds raised in isolation produce degraded, non-species-typical song (isolate song)",
    },
}

# ---------------------------------------------------------------------------
# Animal Communication Modes
# ---------------------------------------------------------------------------

ANIMAL_COMMUNICATION_MODES: dict[str, dict[str, Any]] = {
    "vocalization": {
        "description": "Sound-based communication including calls, songs, and ultrasonic emissions",
        "examples": [
            ("birdsong", "Passeriformes — territorial defense and mate attraction"),
            ("whale_song", "Megaptera novaeangliae — complex, culturally transmitted songs"),
            ("frog_chorus", "Anura — species-specific advertisement calls"),
            ("infrasound", "Elephas maximus — infrasonic calls detectable over kilometers"),
            ("ultrasound", "Rodentia — ultrasonic vocalizations for pup retrieval and social communication"),
        ],
        "information_encoded": ["species", "individual_identity", "mating_status", "emotional_state", "location"],
    },
    "chemical_signaling": {
        "description": "Pheromones and semiochemicals for long-lasting, environment-persistent messaging",
        "examples": [
            ("ant_trail_pheromones", "Formicidae — foraging paths marked with volatile chemicals"),
            ("moth_sex_pheromones", "Bombyx mori — bombykol detectable by males at parts-per-trillion"),
            ("alarm_pheromones", "Apis mellifera — isoamyl acetate signals threat to hive mates"),
            ("territory_marking", "Canidae — urine marking encodes individual identity and reproductive status"),
        ],
        "advantages": ["works_in_darkness", "persists_in_environment", "long_range", "energetically_efficient"],
    },
    "visual_displays": {
        "description": "Color changes, postures, movements, and bioluminescence",
        "examples": [
            ("peacock_plumage", "Pavo cristatus — honest signal of genetic quality (handicap principle)"),
            ("cuttlefish_chromatophores", "Sepiida — rapid color and texture change for camouflage and communication"),
            ("firefly_bioluminescence", "Lampyridae — species-specific flash patterns for mating"),
            ("threat_displays", "Various — piloerection, dewlap extension, body inflation to appear larger"),
        ],
        "honest_signal_theory": "Zahavi (1975): costly signals are honest because only high-quality individuals can afford them",
    },
    "tactile_communication": {
        "description": "Touch-based signaling including grooming, antennation, and vibratory signals",
        "examples": [
            ("allogrooming", "Primates — social bonding, reconciliation, and tension reduction"),
            ("waggle_dance", "Apis mellifera — tactile + vibratory encoding of food source distance and direction"),
            ("antennation", "Formicidae — antennal contact for nestmate recognition and information transfer"),
            ("seismic_communication", "Elephantidae — foot-stomping vibrations detected through substrate"),
        ],
    },
}

# ---------------------------------------------------------------------------
# Stress Indicators
# ---------------------------------------------------------------------------

STRESS_INDICATORS: dict[str, dict[str, Any]] = {
    "displacement_behaviors": {
        "description": "Irrelevant, out-of-context behaviors performed during motivational conflict",
        "examples": {
            "dogs": ["excessive_self_grooming", "lip_licking", "yawning_when_not_tired", "shaking_when_not_wet"],
            "cats": ["excessive_grooming", "overgrooming_to_bald_patches", "displacement_scratching"],
            "primates": ["self_scratching", "yawning", "self_grooming", "object_manipulation"],
            "birds": ["preening_out_of_context", "beak_wiping", "wing_fluttering"],
            "horses": ["crib_biting", "weaving", "box_walking"],
        },
        "mechanism": "Behavioral disinhibition when two incompatible motivations conflict",
    },
    "stereotypies": {
        "description": "Repetitive, invariant behavior patterns with no obvious goal or function",
        "examples": {
            "zoo_animals": ["pacing", "head_bobbing", "bar_biting", "circling", "rocking"],
            "farm_animals": ["tongue_rolling", "bar_biting", "sham_chewing", "crib_biting"],
            "laboratory_animals": ["route_tracing", "backflipping", "bar_mouthing"],
            "companion_animals": ["tail_chasing", "flank_sucking", "shadow_chasing", "light_chasing"],
        },
        "causes": ["environmental_impoverishment", "confinement", "lack_of_control", "frustration", "early_maternal_separation"],
        "welfare_indicator": "Presence of stereotypies indicates compromised welfare; absence does not guarantee good welfare (some animals become apathetic instead)",
    },
    "avoidance": {
        "description": "Active withdrawal or escape from aversive stimuli",
        "examples": {
            "general": ["hiding", "fleeing", "freezing", "crouching", "tucked_tail", "flattened_ears"],
        },
        "physiological_correlates": ["elevated_cortisol", "increased_heart_rate", "tachycardia", "hypervigilance"],
    },
    "appetite_changes": {
        "description": "Alterations in feeding behavior under stress",
        "hyperphagia": "Stress-induced overeating (comfort eating) common in some species",
        "hypophagia": "Reduced or absent feeding in acute stress",
        "species_variation": "Rodents typically hypophagic; some primates and humans hyperphagic",
    },
}

# ---------------------------------------------------------------------------
# Social Structures
# ---------------------------------------------------------------------------

SOCIAL_STRUCTURES: dict[str, dict[str, Any]] = {
    "dominance_hierarchy": {
        "description": "Rank-ordered social system where higher-ranked individuals have priority access to resources",
        "types": {
            "linear": "A > B > C > D — transitive, strictly ranked (common in chickens: pecking order)",
            "despotic": "Single dominant individual; subordinates are equally low-ranked",
            "triangular": "Non-transitive dominance (A > B, B > C, but C > A); rare, observed in some fish",
        },
        "species_examples": ["Gallus gallus (chickens)", "Papio (baboons)", "Canis lupus (wolves)", "Macaca (macaques)"],
        "formation_factors": ["body_size", "age", "fighting_ability", "alliance_formation", "maternal_rank_inheritance"],
        "benefits_of_stability": ["reduced_aggression", "predictable_resource_access", "stress_reduction"],
    },
    "cooperative_breeding": {
        "description": "System where non-parent individuals (helpers) assist in raising offspring",
        "helpers": ["older_siblings", "non_breeding_adults", "delayed_dispersal_individuals"],
        "species_examples": [
            "Suricata suricatta (meerkats)",
            "Acanthisitta chloris (riflemen)",
            "Callitrichidae (marmosets and tamarins)",
            "Canis familiaris (domestic dogs — human co-breeding)",
        ],
        "evolutionary_explanations": ["kin_selection", "habitat_saturation", "skill_acquisition", "group_augmentation"],
        "hamilton_rule": "rB > C — altruism favored when relatedness × benefit exceeds cost to altruist",
    },
    "eusociality": {
        "description": "Highest level of social organization: reproductive division of labor, overlapping generations, cooperative brood care",
        "castes": ["reproductive (queen/king)", "workers", "soldiers"],
        "species_examples": [
            "Hymenoptera (ants, bees, wasps)",
            "Isoptera (termites)",
            "Heterocephalus glaber (naked mole-rat — only eusocial mammal)",
            "Synalpheus regalis (snapping shrimp — only eusocial marine species)",
        ],
        "haplodiploidy_hypothesis": "In Hymenoptera, females share 75% of genes with sisters (vs. 50% with own offspring), favoring worker caste evolution — Hamilton (1964)",
    },
    "fission_fusion": {
        "description": "Dynamic social system where group composition changes frequently: individuals merge (fusion) and split (fission)",
        "species_examples": ["Pan troglodytes (chimpanzees)", "Tursiops (bottlenose dolphins)", "Elephas (elephants)", "Homo sapiens (hunter-gatherer bands)"],
        "ecological_drivers": ["resource_distribution", "predation_risk", "mating_opportunities"],
        "cognitive_demands": ["individual_recognition", "memory_of_past_interactions", "social_bookkeeping"],
    },
    "pair_bonding": {
        "description": "Long-term, selective attachment between two individuals (usually mating partners)",
        "species_examples": ["prairie_voles (Microtus ochrogaster)", "albatrosses", "gibbons", "beavers", "emperor_penguins"],
        "neurobiological_basis": "Vasopressin V1a receptor density in ventral pallidum; oxytocin in nucleus accumbens",
        "social_monogamy_vs_genetic_monogamy": "Social pair bonds may coexist with extra-pair copulations (genetic non-monogamy)",
    },
}

# ---------------------------------------------------------------------------
# Training Methods
# ---------------------------------------------------------------------------

TRAINING_METHODS: dict[str, dict[str, Any]] = {
    "operant_conditioning": {
        "description": "Learning through consequences: behavior is shaped by reinforcement (increase) or punishment (decrease)",
        "quadrants": {
            "positive_reinforcement": "Add pleasant stimulus to increase behavior — e.g., treat for sitting",
            "negative_reinforcement": "Remove aversive stimulus to increase behavior — e.g., release pressure when horse moves forward",
            "positive_punishment": "Add aversive stimulus to decrease behavior — e.g., leash correction for pulling",
            "negative_punishment": "Remove pleasant stimulus to decrease behavior — e.g., time-out, removing attention",
        },
        "schedules_of_reinforcement": {
            "continuous": "Every response reinforced — fastest acquisition, lowest resistance to extinction",
            "fixed_ratio": "Reinforcement after N responses — high response rate, post-reinforcement pause",
            "variable_ratio": "Reinforcement after variable N responses — highest response rate, most extinction-resistant (gambling mechanism)",
            "fixed_interval": "Reinforcement after constant time — scalloped response pattern",
            "variable_interval": "Reinforcement after variable time — steady, moderate response rate",
        },
        "pioneer": "B.F. Skinner",
    },
    "classical_conditioning": {
        "description": "Learning through association: neutral stimulus paired with biologically significant stimulus elicits learned response",
        "key_concepts": {
            "acquisition": "CS-US pairing builds CR",
            "extinction": "CS alone presentation gradually reduces CR",
            "spontaneous_recovery": "CR reappears after extinction and rest period",
            "generalization": "Stimuli similar to CS also elicit CR",
            "discrimination": "Learning to distinguish CS from similar stimuli",
            "blocking": "Prior CS-US learning blocks learning about new CS presented simultaneously (Kamin effect)",
        },
        "pioneer": "Ivan Pavlov",
        "applications": ["desensitization_and_counterconditioning", "clicker_training_bridge", "systematic_desensitization"],
    },
    "clicker_training": {
        "description": "Using a distinct sound (click) as a conditioned reinforcer (bridge) marking the exact moment of correct behavior",
        "steps": [
            "charge_the_clicker — pair click with primary reinforcer (treat)",
            "mark_behavior — click at the exact moment the desired behavior occurs",
            "reward — follow every click with a treat (never click without treating)",
            "shape_complex_behaviors — reward successive approximations toward goal behavior",
        ],
        "advantages": ["precise_timing", "consistent_marker", "works_across_species", "no_physical_force"],
        "species_applications": ["dogs", "horses", "cats", "birds", "marine_mammals", "zoo_animals", "fish"],
        "pioneer": "Karen Pryor (marine mammal training adapted for companion animals)",
    },
    "habituation": {
        "description": "Decreasing response to a repeated, non-consequential stimulus",
        "distinction_from_sensory_adaptation": "Habituation is stimulus-specific central nervous system learning; sensory adaptation is peripheral receptor fatigue",
        "properties": [
            "stimulus_specificity — response decreases only to the habituated stimulus",
            "spontaneous_recovery — response partially recovers after rest period",
            "dishabituation — novel stimulus temporarily restores response to habituated stimulus",
            "frequency_dependent — higher stimulus frequency produces faster habituation",
        ],
        "applications": ["desensitization_to_scary_stimuli", "noise_phobia_treatment", "veterinary_handling_training"],
    },
    "desensitization_and_counterconditioning": {
        "description": "Pairing gradual exposure to feared stimulus (desensitization) with pleasant association (counterconditioning) to change emotional response",
        "protocol": [
            "establish_stimulus_hierarchy — rank scary stimuli from least to most intense",
            "stay_below_threshold — expose at intensity that does NOT trigger fear response",
            "pair_with_reinforcement — present stimulus, then feed high-value treats",
            "gradually_increase_intensity — only progress when animal shows relaxed body language",
            "never_flood — full-intensity exposure causes learned helplessness, not recovery",
        ],
        "applications": ["noise_phobia", "separation_anxiety", "stranger_directed_fear", "veterinary_procedure_fear"],
    },
    "social_learning": {
        "description": "Learning through observation of conspecifics (or heterospecifics)",
        "mechanisms": [
            "observational_conditioning — observer's emotional response shaped by watching demonstrator",
            "stimulus_enhancement — observer's attention drawn to object/location by demonstrator's activity",
            "imitation — observer replicates demonstrator's specific motor pattern",
            "emulation — observer reproduces the outcome, not the exact method",
        ],
        "species_examples": ["chimpanzee_tool_use_transmission", "killer_whale_hunting_technique_transmission", "dog_social_learning_from_humans"],
    },
}

# ---------------------------------------------------------------------------
# Animal Language Research
# ---------------------------------------------------------------------------

ANIMAL_LANGUAGE_RESEARCH: dict[str, dict[str, Any]] = {
    "washoe": {
        "species": "Pan troglodytes (chimpanzee)",
        "researchers": "Beatrice and Allen Gardner; later Roger Fouts",
        "period": "1966-2007",
        "language_medium": "American Sign Language (ASL)",
        "vocabulary_size": "~350 signs (reliable, contextual use)",
        "key_findings": [
            "First non-human to acquire a human language (ASL)",
            "Spontaneously combined signs into novel phrases (e.g., 'water bird' for swan)",
            "Taught signs to other chimpanzees (Loulis, adopted son, learned ~50 signs from Washoe without human instruction)",
            "Used signs to communicate across contexts (not just food requests)",
            "Demonstrated displacement — signing about absent objects/events",
        ],
        "criticisms": ["overinterpretation_of_combinations", "lack_of_syntactic_structure", "cueing_by_experimenters"],
        "significance": "Pioneered cross-fostering methodology for ape language research; demonstrated that chimpanzees can acquire and spontaneously use a human sign language",
    },
    "koko": {
        "species": "Gorilla gorilla (western lowland gorilla)",
        "researchers": "Francine 'Penny' Patterson",
        "period": "1972-2018",
        "language_medium": "Gorilla Sign Language (modified ASL) + spoken English comprehension",
        "vocabulary_size": "~1,000 signs (claimed); ~2,000 spoken English words understood",
        "key_findings": [
            "Alleged IQ score of 70-95 on human infant tests (controversial)",
            "Reported to lie, joke, and use metaphor",
            "Koko's Kitten: kept a pet cat, signed 'cat' and 'baby', grieved when cat died",
            "Internet chat event: answered questions via interpreter (AOL 1998)",
        ],
        "criticisms": ["heavy_reliance_on_interpreter_interpretation", "lack_of_blind_testing", "single_experimenter_bias", "unreplicable_results"],
    },
    "nim_chimpsky": {
        "species": "Pan troglodytes (chimpanzee)",
        "researchers": "Herbert Terrace",
        "period": "1973-1977",
        "language_medium": "ASL signs",
        "vocabulary_size": "~125 signs",
        "key_findings": [
            "Project Nim — Terrace initially aimed to prove chimpanzee language ability",
            "TERRACE CONCLUDED: Nim was not using language; signs were imitative responding to trainer cues",
            "Nim's sign combinations showed NO increase in grammatical complexity over time (unlike human children)",
            "Nim signed mainly to request (90%+ of utterances), not to comment or converse",
            "Mean length of utterance: 1.1-1.6 signs (human 2-year-olds: 1.5-2.5 words, growing over time)",
            "Project Nim data fundamentally challenged the Washoe and Koko claims",
        ],
        "significance": "Most rigorous experimental test; negative result changed the field's understanding of ape language",
    },
    "alex_the_parrot": {
        "species": "Psittacus erithacus (African grey parrot)",
        "researchers": "Irene Pepperberg",
        "period": "1977-2007",
        "language_medium": "Spoken English (Alex learned to produce ~150 English words)",
        "vocabulary_size": "~150 words, with conceptual understanding of categories",
        "key_findings": [
            "Could label 50+ objects by name, color, material, and shape",
            "Counted objects up to 6; understood concept of zero (none)",
            "Answered questions about object properties ('What color?', 'What shape?', 'How many?')",
            "Understood same/different concept across attributes",
            "Last words to Pepperberg: 'You be good. See you tomorrow. I love you.'",
            "Demonstrated vocal learning with semantic comprehension, not just mimicry",
        ],
        "significance": "Proved that a non-primate, non-mammal brain can support complex cognitive and communicative abilities",
    },
    "dolphin_signature_whistles": {
        "species": "Tursiops truncatus (bottlenose dolphin)",
        "researchers": "Multiple (Sayigh, Janik, King)",
        "period": "1990s-present",
        "language_medium": "Signature whistles — individually distinctive, learned vocalizations",
        "key_findings": [
            "Each dolphin develops a unique signature whistle in first year of life (individually distinctive, like a name)",
            "Dolphins copy each other's signature whistles to address specific individuals (vocal labeling)",
            "Signature whistles used in mother-calf reunions and coalition coordination",
            "Dolphins remember signature whistles of former tank mates for 20+ years (longest non-human social memory documented)",
            "Coalition whistles: allied males converge on a shared whistle, suggesting group identity signaling",
        ],
        "significance": "Only non-human animal demonstrated to use individually distinctive vocal labels that function as names",
    },
    "corvid_tool_use": {
        "species": "Corvus moneduloides (New Caledonian crow)",
        "researchers": "Multiple (Hunt, Weir, Taylor, Rutz)",
        "period": "1996-present",
        "key_findings": [
            "Manufacture hooked tools from twigs and pandanus leaves in the wild",
            "Sequential tool use: use one tool to retrieve another tool needed for the final task (meta-tool use)",
            "Spontaneous problem-solving: bending unfamiliar material (wire) into hooks without prior training",
            "Causal reasoning: understand water displacement (Aesop's fable paradigm — dropping stones to raise water level)",
            "Tool-use technique varies regionally (cultural transmission, not just genetic)",
            "Planning for future needs (caching tools for later use) — mental time travel",
        ],
        "significance": "Corvid intelligence rivals that of great apes despite radically different brain architecture (convergent evolution of cognition)",
    },
    "prairie_dog_alarm_calls": {
        "species": "Cynomys gunnisoni (Gunnison's prairie dog)",
        "researchers": "Con Slobodchikoff",
        "period": "1980s-2010s",
        "key_findings": [
            "Alarm calls encode predator type (hawk, coyote, dog, human), size, color, and speed of approach",
            "Different calls for different humans based on clothing color and size (descriptive, not just categorical)",
            "Calls vary across colonies (regional dialects)",
            "Suggests prairie dogs have a referential communication system more complex than simple alarm calls",
        ],
        "significance": "One of the most sophisticated non-primate referential communication systems documented",
    },
    "bee_waggle_dance": {
        "species": "Apis mellifera (western honey bee)",
        "researchers": "Karl von Frisch (Nobel 1973)",
        "period": "1920s-1940s (foundational); ongoing research",
        "encoding": {
            "direction": "Angle of waggle run relative to vertical on comb = angle of food source relative to sun azimuth",
            "distance": "Duration of waggle run proportional to distance (1 second ≈ 1 km)",
            "quality": "Vigor of dance correlates with food source quality",
        },
        "key_findings": [
            "Only symbolic communication system in an invertebrate",
            "Bees adjust dance for headwind/tailwind (energetic cost encoding)",
            "Dance-followers evaluate dances from multiple foragers before choosing destination",
            "Stop signals: bees head-butt dancing bees to inhibit dances for dangerous or overcrowded food sources (negative feedback loop)",
            "Cross-species dance comprehension: Apis cerana and Apis mellifera understand each other's dialect",
        ],
        "significance": "Demonstrates that complex symbolic communication does not require a large brain or mammalian nervous system",
    },
}

# ---------------------------------------------------------------------------
# Learned vs. Innate Behavior Classification
# ---------------------------------------------------------------------------

BEHAVIOR_CLASSIFICATIONS: dict[str, list[str]] = {
    "innate": [
        "reflexes", "taxis", "kinesis", "fixed_action_patterns",
        "imprinting", "instinct", "circadian_rhythms",
    ],
    "learned": [
        "habituation", "classical_conditioning", "operant_conditioning",
        "observational_learning", "insight_learning", "social_learning",
        "cultural_transmission", "imprinting",
    ],
}

# ---------------------------------------------------------------------------
# Analysis Functions
# ---------------------------------------------------------------------------

def classify_behavior(species: str, observation: dict[str, Any]) -> dict[str, Any]:
    """Classify observed animal behavior by type, function, and developmental origin.

    Args:
        species: Scientific or common name (e.g., "Canis familiaris", "dog").
        observation: Dict with keys:
            - behavior: str — description of observed behavior
            - context: str — environmental/social context
            - frequency: str — "once", "repeated", "constant", "seasonal"
            - elicitor: str | None — apparent trigger

    Returns:
        Dict with keys:
            - behavior_type: str — innate, learned, or mixed
            - functional_category: str — feeding, mating, social, etc.
            - specific_classification: str — e.g., fixed_action_pattern, operant_response
            - confidence: float
            - related_behaviors: list[str]
            - recommendation: str
    """
    behavior = observation.get("behavior", "").lower()
    context = observation.get("context", "").lower()
    frequency = observation.get("frequency", "once")
    elicitor = observation.get("elicitor", "").lower() if observation.get("elicitor") else ""

    behavior_type = "learned"
    functional_category = "general"
    specific_classification = "undetermined"
    confidence = 0.3
    related: list[str] = []

    if any(w in behavior for w in ["stereotyp", "repetitive", "pacing", "circling", "rocking"]):
        behavior_type = "learned"
        functional_category = "stress_response"
        specific_classification = "stereotypy"
        confidence = 0.8
        related = ["displacement_behaviors", "environmental_enrichment_deficit"]

    elif any(w in behavior for w in ["groom", "lick"]):
        if any(w in behavior for w in ["excessive", "constant", "compulsive"]):
            behavior_type = "innate"
            functional_category = "stress_response"
            specific_classification = "displacement_behavior"
            confidence = 0.7
        elif "other" in behavior or "allo" in behavior or "social" in context:
            behavior_type = "innate"
            functional_category = "social_bonding"
            specific_classification = "allogrooming"
            confidence = 0.6
        else:
            behavior_type = "innate"
            functional_category = "self_maintenance"
            specific_classification = "self_grooming"
            confidence = 0.5

    elif any(w in behavior for w in ["vocal", "call", "song", "whistle", "bark", "howl"]):
        behavior_type = "mixed"
        functional_category = "communication"
        specific_classification = "vocalization"
        confidence = 0.6
        related = ["animal_communication", "spectrogram_analysis"]

    elif any(w in behavior for w in ["court", "display", "dance", "mating", "mount"]):
        behavior_type = "innate"
        functional_category = "reproductive"
        if "fixed" in behavior or "stereotyped" in behavior:
            specific_classification = "fixed_action_pattern"
            confidence = 0.8
        elif "learned" in behavior or "cultural" in behavior:
            specific_classification = "culturally_transmitted_courtship"
            behavior_type = "learned"
            confidence = 0.6
        else:
            specific_classification = "courtship_behavior"
            confidence = 0.7

    elif any(w in behavior for w in ["bite", "attack", "fight", "aggress", "threat"]):
        behavior_type = "mixed"
        functional_category = "agonistic"
        specific_classification = "aggression"
        confidence = 0.7
        related = ["dominance_hierarchy", "resource_competition"]

    elif any(w in behavior for w in ["hide", "flee", "escape", "avoid", "retreat"]):
        behavior_type = "innate"
        functional_category = "defensive"
        specific_classification = "avoidance_behavior"
        confidence = 0.7

    elif any(w in behavior for w in ["feed", "eat", "hunt", "forage", "drink"]):
        behavior_type = "mixed"
        functional_category = "feeding"
        specific_classification = "foraging_behavior"
        confidence = 0.6

    elif any(w in behavior for w in ["play", "chase", "wrestle"]):
        behavior_type = "innate"
        functional_category = "play"
        specific_classification = "social_play"
        confidence = 0.5

    elif any(w in behavior for w in ["click", "treat", "reinforce", "train", "command"]):
        behavior_type = "learned"
        functional_category = "training"
        specific_classification = "operant_response" if "click" in behavior or "treat" in behavior else "conditioned_response"
        confidence = 0.9

    related = sorted(set(related))

    recommendation = ""
    if specific_classification == "stereotypy":
        recommendation = "Stereotypy indicates welfare compromise. Assess environmental enrichment and provide species-appropriate stimulation."
    elif specific_classification == "displacement_behavior":
        recommendation = "Displacement behavior suggests motivational conflict. Identify conflicting motivations and reduce stressor."
    elif specific_classification == "fixed_action_pattern":
        recommendation = "Fixed action pattern is species-typical and largely innate. Document the sign stimulus if observable."
    elif specific_classification == "aggression":
        recommendation = "Assess context: defensive vs. offensive, resource-guarding vs. fear-based. Do not punish fear-based aggression."

    return {
        "behavior_type": behavior_type,
        "functional_category": functional_category,
        "specific_classification": specific_classification,
        "confidence": round(confidence, 2),
        "related_behaviors": related,
        "recommendation": recommendation or "No specific recommendation for this behavior.",
    }


def interpret_vocalization(species: str, audio_features: dict[str, Any]) -> dict[str, Any]:
    """Interpret animal vocalization based on species and acoustic features.

    Args:
        species: Species identifier.
        audio_features: Dict with keys:
            - duration_ms: float
            - peak_frequency_hz: float
            - frequency_range_hz: tuple[float, float]
            - call_rate_per_minute: float | None
            - harmonic_structure: str — "tonal", "noisy", "harmonic", "pulsed"
            - pattern: str — "single", "repeated", "ascending", "descending", "trill", "warble"

    Returns:
        Dict with:
            - likely_call_type: str
            - emotional_state: str
            - function: str
            - confidence: float
            - species_reference: str
    """
    duration = audio_features.get("duration_ms", 0)
    peak_freq = audio_features.get("peak_frequency_hz", 0)
    freq_range = audio_features.get("frequency_range_hz", (0, 0))
    call_rate = audio_features.get("call_rate_per_minute", 0.0)
    harmonic_structure = audio_features.get("harmonic_structure", "tonal")
    pattern = audio_features.get("pattern", "single")

    likely_call_type = "undetermined"
    emotional_state = "neutral"
    function = "unknown"
    confidence = 0.3
    species_ref = ""

    species_lower = species.lower()

    if "canis" in species_lower or "dog" in species_lower:
        species_ref = "Canis familiaris vocalization repertoire"
        if pattern == "repeated" and call_rate and call_rate > 20:
            likely_call_type = "barking"
            if duration < 200:
                emotional_state = "alert_or_alarm"
                function = "territorial_defense_or_alert"
                confidence = 0.7
            else:
                emotional_state = "arousal_or_play"
                function = "social_communication"
                confidence = 0.6
        elif pattern == "single" and peak_freq < 500:
            likely_call_type = "growling"
            emotional_state = "threat_or_fear"
            function = "agonistic_warning"
            confidence = 0.8
        elif pattern == "ascending" and peak_freq > 500:
            likely_call_type = "whining"
            emotional_state = "anxiety_or_appeasement"
            function = "care_soliciting"
            confidence = 0.7

    elif "felis" in species_lower or "cat" in species_lower:
        species_ref = "Felis catus vocalization repertoire"
        if peak_freq < 500:
            likely_call_type = "purring"
            emotional_state = "contentment_or_self_soothing"
            function = "social_bonding_or_self_comfort"
            confidence = 0.6
        elif duration < 500 and peak_freq > 800:
            likely_call_type = "meow"
            emotional_state = "solicitation"
            function = "attention_or_resource_request"
            confidence = 0.7
        elif peak_freq > 1000 and "harmonic" in harmonic_structure:
            likely_call_type = "yowling_or_caterwauling"
            emotional_state = "distress_or_mating"
            function = "mate_attraction_or_territorial"
            confidence = 0.7

    elif "equus" in species_lower or "horse" in species_lower:
        species_ref = "Equus caballus vocalization repertoire"
        if pattern == "trill":
            likely_call_type = "whinny"
            emotional_state = "arousal_or_separation"
            function = "social_reconnection"
            confidence = 0.8
        elif pattern == "single" and duration < 200:
            likely_call_type = "snort"
            emotional_state = "alert"
            function = "alarm_or_curiosity"
            confidence = 0.7

    elif "parrot" in species_lower or "psittac" in species_lower:
        species_ref = "Psittacine vocalization repertoire"
        if pattern == "warble" or pattern == "repeated":
            likely_call_type = "contact_call"
            emotional_state = "flock_maintenance"
            function = "social_cohesion"
            confidence = 0.7
        elif peak_freq > 2000:
            likely_call_type = "alarm_call"
            emotional_state = "fear_or_alert"
            function = "predator_warning"
            confidence = 0.8

    elif "bee" in species_lower or "apis" in species_lower:
        species_ref = "Apis mellifera acoustic communication"
        if 200 <= peak_freq <= 300 and "pulsed" in harmonic_structure:
            likely_call_type = "piping"
            emotional_state = "swarming"
            function = "colony_coordination"
            confidence = 0.8

    if likely_call_type == "undetermined":
        if duration < 100 and peak_freq > 1000:
            likely_call_type = "short_high_call"
            emotional_state = "alarm_or_excitement"
            function = "alert"
            confidence = 0.3
        elif duration > 1000:
            likely_call_type = "long_duration_call"
            emotional_state = "territorial_or_mating"
            function = "broadcast_signal"
            confidence = 0.3

    return {
        "likely_call_type": likely_call_type,
        "emotional_state": emotional_state,
        "function": function,
        "confidence": round(confidence, 2),
        "species_reference": species_ref or f"No species-specific reference data for '{species}'",
    }


def recommend_training_approach(species: str, goal: dict[str, Any]) -> dict[str, Any]:
    """Recommend a training approach based on species, goal, and known ethology.

    Args:
        species: Species identifier.
        goal: Dict with keys:
            - target_behavior: str — desired behavior
            - current_state: str — starting point description
            - constraints: list[str] — force-free, time_limited, etc.
            - problem_behavior: str | None — behavior to address

    Returns:
        Dict with:
            - recommended_method: str
            - protocol_steps: list[str]
            - expected_timeline: str
            - cautions: list[str]
            - reinforcement_type: str
            - supporting_methods: list[str]
    """
    target = goal.get("target_behavior", "").lower()
    current = goal.get("current_state", "").lower()
    constraints: list[str] = goal.get("constraints", [])
    problem = (goal.get("problem_behavior") or "").lower()

    species_lower = species.lower()
    recommended_method = "positive_reinforcement"
    reinforcement_type = "food_reward (highest universality)"
    protocol_steps: list[str] = []
    cautions: list[str] = []
    supporting_methods: list[str] = []
    timeline = "2-8 weeks for reliable behavior"

    is_dog = "dog" in species_lower or "canis" in species_lower
    is_cat = "cat" in species_lower or "felis" in species_lower
    is_horse = "horse" in species_lower or "equus" in species_lower
    is_bird = "bird" in species_lower or "parrot" in species_lower or "psittac" in species_lower
    is_marine = "dolphin" in species_lower or "whale" in species_lower or "sea lion" in species_lower
    is_primate = "chimp" in species_lower or "monkey" in species_lower or "ape" in species_lower or "macaque" in species_lower

    if "fear" in problem or "anxiety" in problem or "phobia" in problem:
        recommended_method = "desensitization_and_counterconditioning"
        reinforcement_type = "high_value_food_or_play_reward"
        protocol_steps = [
            "Establish stimulus hierarchy (least to most intense version of fear trigger)",
            "Start at intensity below fear threshold (no fear response)",
            "Present stimulus, then immediately deliver high-value reinforcement",
            "Gradually increase stimulus intensity only when animal shows relaxed body language",
            "If fear response occurs: reduce intensity; you progressed too fast",
            "Continue until stimulus at full intensity elicits relaxed or happy response",
        ]
        timeline = "4-16 weeks depending on severity; some severe cases may require months"
        cautions = ["NEVER flood (full-intensity exposure causes learned helplessness)", "If no progress after 4 weeks, consult veterinary behaviorist"]
        supporting_methods = ["habituation", "classical_conditioning"]

    elif "basic" in target or "obedience" in target or "foundation" in target:
        recommended_method = "clicker_training_with_positive_reinforcement"
        reinforcement_type = "food_reward_for_initial_acquisition; transition_to_variable_reinforcement_for_maintenance"
        protocol_steps = [
            "Charge the clicker: click → treat, 20+ repetitions until animal orients to click",
            "Capture or lure the desired behavior",
            "Click at the exact moment the behavior occurs",
            "Always follow click with treat (never click without treating)",
            "Add verbal cue once behavior is reliable (say cue → behavior → click → treat)",
            "Gradually fade lure and add duration/distance/distraction",
        ]
        timeline = "1-4 weeks for basic behaviors; complex chains require 4-12 weeks"

    elif "recall" in target or "come" in target:
        recommended_method = "positive_reinforcement_with_high_value_reward"
        protocol_steps = [
            "Establish recall cue with highest-value reinforcement (never use recall for punishment)",
            "Start in low-distraction environment at short distance",
            "Gradually increase distance, then add distraction",
            "Use long line for safety during proofing; never punish after successful recall",
            "Maintain variable reinforcement schedule (sometimes jackpot reward)",
        ]
        timeline = "4-8 weeks for reliable recall in moderate distraction"
        cautions = ["Never punish a slow recall — the animal arrived; that is the desired outcome", "If recall is unreliable, reduce distance/simplify environment"]

    elif "aggression" in problem or "reactivity" in problem:
        recommended_method = "behavior_adjustment_training_or_LAT"
        protocol_steps = [
            "Identify trigger and threshold distance (distance at which reaction first occurs)",
            "Work below threshold: animal notices trigger but is not reacting",
            "Reinforce calm behavior or alternative behavior (Look At That / engage-disengage)",
            "Gradually decrease distance as animal builds positive conditioned emotional response",
            "Never punish aggressive displays — punishment suppresses warning signals, increasing bite risk",
        ]
        timeline = "8-52 weeks; aggression cases require professional guidance"
        cautions = [
            "CONSULT a qualified professional (veterinary behaviorist or certified behavior consultant)",
            "Punishment-based methods increase aggression risk and damage trust",
            "Rule out medical causes (pain, neurological) before behavioral intervention",
        ]

    elif is_horse:
        recommended_method = "negative_reinforcement_with_positive_reinforcement"
        reinforcement_type = "release_of_pressure (negative reinforcement) + food_reward (positive reinforcement)"
        protocol_steps = [
            "Apply light, consistent pressure cue",
            "Release pressure INSTANTLY when horse gives correct response (the release IS the reinforcement)",
            "Timing is critical: release must occur within 1 second of desired behavior",
            "Incorporate clicker training for precision behaviors (e.g., targeting, medical procedures)",
            "Build progressively: pressure → behavior → release → rest",
        ]
        timeline = "2-12 weeks; horses learn pressure-release dynamics quickly if timing is precise"
        cautions = ["Pressure that does not release is punishment, not training", "Increasing pressure without the horse understanding the response creates fear and resistance"]

    elif is_marine:
        recommended_method = "positive_reinforcement_free_contact"
        reinforcement_type = "primary_reinforcer (fish, play, tactile) + secondary_reinforcer (whistle)"
        protocol_steps = [
            "Establish bridge (whistle = coming reward, analogous to clicker)",
            "Capture or shape behavior in water; bridge at exact moment of correct behavior",
            "Voluntary participation: animal is NEVER forced; can leave at any time",
            "Build complex chains: multiple behaviors bridged and reinforced sequentially",
            "Medical training: voluntary blood draw, ultrasound, dental exam via trained behaviors",
        ]
        timeline = "Highly variable; marine mammals are among the fastest learners in managed care settings"
        cautions = ["Free contact with large marine mammals carries physical risk; always follow safety protocols"]

    elif is_bird:
        recommended_method = "positive_reinforcement_with_target_training"
        reinforcement_type = "preferred_treat + social_praise"
        protocol_steps = [
            "Establish trust through consistent, gentle handling and predictable routines",
            "Target training: teach bird to touch target stick for reinforcement",
            "Use targeting to guide bird to desired locations without force",
            "Teach step-up (perch on hand) as foundation behavior",
            "Shape complex behaviors through successive approximations",
        ]
        timeline = "2-8 weeks for basic behaviors; birds are rapid learners when motivated"
        cautions = ["Never grab or restrain — damages trust, often permanently", "Parrots may hold grudges; consistency and positive experiences are critical"]

    else:
        protocol_steps = [
            "Establish primary reinforcer (identify what motivates this species/individual)",
            "Use positive reinforcement; capture or shape desired behavior",
            "Keep sessions short (1-5 minutes) and end on success",
            "Progress at the animal's pace; never force or flood",
        ]
        timeline = "Species-dependent; consult ethology literature"

    if "force_free" in constraints or "positive_only" in constraints:
        cautions.append("Force-free constraint active: avoid all aversives including verbal corrections")

    return {
        "recommended_method": recommended_method,
        "protocol_steps": protocol_steps,
        "expected_timeline": timeline,
        "cautions": cautions,
        "reinforcement_type": reinforcement_type,
        "supporting_methods": supporting_methods,
    }
