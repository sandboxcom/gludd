"""Biology — molecular biology, genetics, evolution, microbiology, neuroscience, ecology, bioinformatics, physiology."""

from __future__ import annotations

import math
from typing import Any

DNA_TO_RNA: dict[str, str] = {"A": "U", "T": "A", "G": "C", "C": "G"}

CODON_TABLE: dict[str, str] = {
    "UUU": "Phe", "UUC": "Phe", "UUA": "Leu", "UUG": "Leu",
    "CUU": "Leu", "CUC": "Leu", "CUA": "Leu", "CUG": "Leu",
    "AUU": "Ile", "AUC": "Ile", "AUA": "Ile", "AUG": "Met",
    "GUU": "Val", "GUC": "Val", "GUA": "Val", "GUG": "Val",
    "UCU": "Ser", "UCC": "Ser", "UCA": "Ser", "UCG": "Ser",
    "CCU": "Pro", "CCC": "Pro", "CCA": "Pro", "CCG": "Pro",
    "ACU": "Thr", "ACC": "Thr", "ACA": "Thr", "ACG": "Thr",
    "GCU": "Ala", "GCC": "Ala", "GCA": "Ala", "GCG": "Ala",
    "UAU": "Tyr", "UAC": "Tyr", "UAA": "STOP", "UAG": "STOP",
    "CAU": "His", "CAC": "His", "CAA": "Gln", "CAG": "Gln",
    "AAU": "Asn", "AAC": "Asn", "AAA": "Lys", "AAG": "Lys",
    "GAU": "Asp", "GAC": "Asp", "GAA": "Glu", "GAG": "Glu",
    "UGU": "Cys", "UGC": "Cys", "UGA": "STOP", "UGG": "Trp",
    "CGU": "Arg", "CGC": "Arg", "CGA": "Arg", "CGG": "Arg",
    "AGU": "Ser", "AGC": "Ser", "AGA": "Arg", "AGG": "Arg",
    "GGU": "Gly", "GGC": "Gly", "GGA": "Gly", "GGG": "Gly",
}

AMINO_ACID_PROPERTIES: dict[str, dict[str, Any]] = {
    "Ala": {"abbrev": "A", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 89.09},
    "Arg": {"abbrev": "R", "polarity": "polar", "charge": "positive", "mass_Da": 174.20},
    "Asn": {"abbrev": "N", "polarity": "polar", "charge": "neutral", "mass_Da": 132.12},
    "Asp": {"abbrev": "D", "polarity": "polar", "charge": "negative", "mass_Da": 133.10},
    "Cys": {"abbrev": "C", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 121.15},
    "Gln": {"abbrev": "Q", "polarity": "polar", "charge": "neutral", "mass_Da": 146.15},
    "Glu": {"abbrev": "E", "polarity": "polar", "charge": "negative", "mass_Da": 147.13},
    "Gly": {"abbrev": "G", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 75.07},
    "His": {"abbrev": "H", "polarity": "polar", "charge": "positive", "mass_Da": 155.16},
    "Ile": {"abbrev": "I", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 131.18},
    "Leu": {"abbrev": "L", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 131.18},
    "Lys": {"abbrev": "K", "polarity": "polar", "charge": "positive", "mass_Da": 146.19},
    "Met": {"abbrev": "M", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 149.21},
    "Phe": {"abbrev": "F", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 165.19},
    "Pro": {"abbrev": "P", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 115.13},
    "Ser": {"abbrev": "S", "polarity": "polar", "charge": "neutral", "mass_Da": 105.09},
    "Thr": {"abbrev": "T", "polarity": "polar", "charge": "neutral", "mass_Da": 119.12},
    "Trp": {"abbrev": "W", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 204.23},
    "Tyr": {"abbrev": "Y", "polarity": "polar", "charge": "neutral", "mass_Da": 181.19},
    "Val": {"abbrev": "V", "polarity": "nonpolar", "charge": "neutral", "mass_Da": 117.15},
}

NUCLEOTIDE_PROPERTIES: dict[str, dict[str, Any]] = {
    "A": {"name": "Adenine", "type": "purine", "complement": "T", "mass_Da": 135.13},
    "T": {"name": "Thymine", "type": "pyrimidine", "complement": "A", "mass_Da": 126.11},
    "G": {"name": "Guanine", "type": "purine", "complement": "C", "mass_Da": 151.13},
    "C": {"name": "Cytosine", "type": "pyrimidine", "complement": "G", "mass_Da": 111.10},
    "U": {"name": "Uracil", "type": "pyrimidine", "complement": "A", "mass_Da": 112.09},
}

DNA_REPLICATION_ENZYMES: list[dict[str, Any]] = [
    {"enzyme": "Helicase", "function": "Unwinds double helix at replication fork. Breaks H-bonds between base pairs.", "direction": "5' to 3' on lagging strand template."},
    {"enzyme": "DNA_Polymerase_III", "function": "Primary replicative polymerase. Extends primer in 5'->3' direction. 3'->5' exonuclease proofreading.", "processivity": "High (~1000 nt/s, ~500 kb per binding event for E. coli)."},
    {"enzyme": "Primase", "function": "Synthesizes short RNA primers (~10 nt). Required because DNA polymerase cannot initiate de novo.", "product": "RNA primer complementary to template."},
    {"enzyme": "Ligase", "function": "Seals nicks between Okazaki fragments on lagging strand. Forms phosphodiester bonds.", "energy_source": "ATP (eukaryotes, phages) or NAD+ (bacteria)."},
    {"enzyme": "Topoisomerase", "function": "Relieves torsional stress (supercoiling) ahead of replication fork by transiently breaking and rejoining DNA strands.", "types": "Type I: single-strand break. Type II (gyrase): double-strand break (ATP-dependent)."},
]

GENETIC_INHERITANCE: list[dict[str, Any]] = [
    {"pattern": "autosomal_dominant", "description": "Single mutant allele sufficient for phenotype. Appears in every generation. Affected individual has 50% chance of passing to offspring.", "examples": ["Huntington disease (HTT gene)", "Marfan syndrome (FBN1)", "Achondroplasia (FGFR3)"], "penetrance": "Often complete; may be age-dependent."},
    {"pattern": "autosomal_recessive", "description": "Two mutant alleles required. Often skips generations. Carriers (heterozygotes) unaffected. 25% recurrence risk for carrier parents.", "examples": ["Cystic fibrosis (CFTR)", "Sickle cell disease (HBB)", "Tay-Sachs (HEXA)", "Phenylketonuria (PAH)"], "penetrance": "Complete for homozygotes/compound heterozygotes."},
    {"pattern": "x_linked_recessive", "description": "Mutation on X chromosome. Males (XY) affected more frequently. No male-to-male transmission. Carrier females may show mild symptoms (skewed X-inactivation).", "examples": ["Duchenne muscular dystrophy (DMD)", "Hemophilia A (F8)", "Red-green color blindness"], "penetrance": "Complete in males. Variable in females."},
    {"pattern": "x_linked_dominant", "description": "Single mutant X allele sufficient in females. Often lethal in hemizygous males. Twice as common in females.", "examples": ["Rett syndrome (MECP2)", "Fragile X syndrome (FMR1, trinucleotide repeat)", "Hypophosphatemic rickets (PHEX)"], "penetrance": "High. Male lethality possible."},
    {"pattern": "mitochondrial", "description": "Mutation in mitochondrial DNA. Inherited only from mother (maternal inheritance). All offspring of affected female at risk. Variable expressivity due to heteroplasmy.", "examples": ["LHON (Leber hereditary optic neuropathy)", "MELAS", "MERRF"], "penetrance": "Threshold effect: phenotype depends on fraction of mutant mtDNA."},
]

EVOLUTIONARY_MECHANISMS: list[dict[str, Any]] = [
    {"mechanism": "natural_selection", "description": "Differential survival and reproduction of individuals with heritable traits better suited to environment.", "types": ["Directional: shifts mean phenotype", "Stabilizing: reduces variance around optimum", "Disruptive: favors extremes, may lead to speciation", "Balancing: maintains polymorphism (heterozygote advantage, frequency-dependent)"], "key_figures": ["Darwin (1859)", "Fisher (1918, 1930)", "Haldane, Wright"]},
    {"mechanism": "genetic_drift", "description": "Random fluctuation in allele frequencies due to finite population size. Stronger in small populations.", "effects": ["Fixation or loss of alleles independent of fitness", "Reduction in heterozygosity (1/2N per generation)", "Founder effects and bottlenecks"], "mathematical_framework": "Wright-Fisher model, coalescent theory (Kingman 1982)."},
    {"mechanism": "gene_flow", "description": "Movement of alleles between populations via migration. Homogenizes allele frequencies. Counters local adaptation and drift.", "measurement": "F_ST: fixation index. Measures population differentiation. F_ST = 0 (no differentiation) to 1 (complete). Empirical: humans F_ST ~ 0.15 globally."},
    {"mechanism": "mutation", "description": "Ultimate source of all genetic variation. Random changes in DNA sequence. Rate: ~10^-8 per nucleotide per generation in humans.", "types": ["Point: substitution, insertion, deletion", "Chromosomal: duplication, inversion, translocation", "Genome: polyploidy (common in plants)"], "fate": "Most mutations are neutral or deleterious. Few are beneficial and drive adaptation."},
]

TAXONOMIC_RANKS: list[str] = ["domain", "kingdom", "phylum", "class", "order", "family", "genus", "species"]

ORGANISM_CLASSIFICATION: dict[str, dict[str, Any]] = {
    "Homo_sapiens": {"domain": "Eukarya", "kingdom": "Animalia", "phylum": "Chordata", "class": "Mammalia", "order": "Primates", "family": "Hominidae", "genus": "Homo", "species": "Homo sapiens", "cell_type": "eukaryotic", "diet": "omnivore", "habitat": "terrestrial"},
    "Escherichia_coli": {"domain": "Bacteria", "kingdom": "Bacteria", "phylum": "Pseudomonadota", "class": "Gammaproteobacteria", "order": "Enterobacterales", "family": "Enterobacteriaceae", "genus": "Escherichia", "species": "Escherichia coli", "cell_type": "prokaryotic", "gram_stain": "negative", "habitat": "gut"},
    "Saccharomyces_cerevisiae": {"domain": "Eukarya", "kingdom": "Fungi", "phylum": "Ascomycota", "class": "Saccharomycetes", "order": "Saccharomycetales", "family": "Saccharomycetaceae", "genus": "Saccharomyces", "species": "Saccharomyces cerevisiae", "cell_type": "eukaryotic", "metabolism": "facultative_anaerobe", "habitat": "fruit surfaces / fermentation"},
    "Drosophila_melanogaster": {"domain": "Eukarya", "kingdom": "Animalia", "phylum": "Arthropoda", "class": "Insecta", "order": "Diptera", "family": "Drosophilidae", "genus": "Drosophila", "species": "Drosophila melanogaster", "cell_type": "eukaryotic", "model_organism": True, "habitat": "cosmopolitan"},
    "Arabidopsis_thaliana": {"domain": "Eukarya", "kingdom": "Plantae", "phylum": "Angiosperms", "class": "Eudicots", "order": "Brassicales", "family": "Brassicaceae", "genus": "Arabidopsis", "species": "Arabidopsis thaliana", "cell_type": "eukaryotic", "model_organism": True, "genome_size_Mb": 135},
    "Staphylococcus_aureus": {"domain": "Bacteria", "kingdom": "Bacteria", "phylum": "Bacillota", "class": "Bacilli", "order": "Bacillales", "family": "Staphylococcaceae", "genus": "Staphylococcus", "species": "Staphylococcus aureus", "cell_type": "prokaryotic", "gram_stain": "positive", "pathogenicity": "opportunistic_pathogen"},
    "Plasmodium_falciparum": {"domain": "Eukarya", "kingdom": "Chromista", "phylum": "Apicomplexa", "class": "Aconoidasida", "order": "Haemosporida", "family": "Plasmodiidae", "genus": "Plasmodium", "species": "Plasmodium falciparum", "cell_type": "eukaryotic", "disease": "malaria", "vector": "Anopheles mosquito"},
    "Methanococcus_jannaschii": {"domain": "Archaea", "kingdom": "Archaea", "phylum": "Euryarchaeota", "class": "Methanococci", "order": "Methanococcales", "family": "Methanococcaceae", "genus": "Methanococcus", "species": "Methanococcus jannaschii", "cell_type": "prokaryotic", "metabolism": "methanogen", "habitat": "deep-sea hydrothermal vents"},
}

NEUROTRANSMITTERS: list[dict[str, Any]] = [
    {"name": "Acetylcholine", "abbrev": "ACh", "type": "excitatory/inhibitory", "receptors": ["nicotinic (ionotropic)", "muscarinic (metabotropic)"], "function": "Neuromuscular junction (muscle contraction). Autonomic nervous system. Learning, memory, attention.", "disorders": ["Alzheimer disease (cholinergic deficit)", "Myasthenia gravis (AChR autoantibodies)"]},
    {"name": "Dopamine", "abbrev": "DA", "type": "modulatory", "receptors": ["D1-like (D1, D5; Gs)", "D2-like (D2, D3, D4; Gi)"], "function": "Reward, motivation, motor control, prolactin inhibition.", "disorders": ["Parkinson disease (nigrostriatal degeneration)", "Schizophrenia (mesolimbic hyperactivity hypothesis)", "Addiction (reward pathway)"], "pathways": ["mesolimbic", "mesocortical", "nigrostriatal", "tuberoinfundibular"]},
    {"name": "Serotonin", "abbrev": "5-HT", "type": "modulatory", "receptors": ["5-HT1-7 families (all metabotropic except 5-HT3 ionotropic)"], "function": "Mood, appetite, sleep, pain perception, GI motility.", "disorders": ["Depression (SSRIs)", "Anxiety", "Migraine (5-HT1 agonists)"]},
    {"name": "GABA", "abbrev": "GABA", "type": "inhibitory", "receptors": ["GABAA (ionotropic, Cl- channel)", "GABAB (metabotropic, Gi)"], "function": "Primary inhibitory neurotransmitter in CNS. Reduces neuronal excitability.", "disorders": ["Epilepsy (GABAergic deficit)", "Anxiety (benzodiazepines enhance GABAA)"], "synthesis": "Glutamate decarboxylation via GAD."},
    {"name": "Glutamate", "abbrev": "Glu", "type": "excitatory", "receptors": ["AMPA (ionotropic)", "NMDA (ionotropic, Ca2+ permeable)", "Kainate (ionotropic)", "mGluR1-8 (metabotropic)"], "function": "Primary excitatory neurotransmitter in CNS. Synaptic plasticity (LTP/LTD).", "disorders": ["Excitotoxicity in stroke/ischemia", "ALS (glutamate transporter defect)"]},
    {"name": "Norepinephrine", "abbrev": "NE", "type": "modulatory", "receptors": ["alpha1, alpha2, beta1, beta2, beta3 (all metabotropic)"], "function": "Arousal, vigilance, fight-or-flight response. Locus coeruleus is primary source.", "disorders": ["Depression (SNRIs)", "ADHD (norepinephrine reuptake inhibitors)", "PTSD (hyperadrenergic)"]},
]

ECOSYSTEM_BIOMES: list[dict[str, Any]] = [
    {"biome": "tropical_rainforest", "climate": "hot, humid (>200 cm rain/yr)", "net_primary_productivity_g_m2_yr": 2200, "biodiversity": "highest terrestrial biodiversity. >50% of Earth's species on 6% of land.", "dominant_flora": "Broadleaf evergreens, epiphytes, lianas. Stratified canopy layers.", "locations": ["Amazon Basin", "Congo Basin", "Southeast Asian archipelago"]},
    {"biome": "temperate_deciduous_forest", "climate": "moderate, seasonal (75-150 cm rain/yr)", "net_primary_productivity_g_m2_yr": 1200, "biodiversity": "moderate. Deciduous leaf drop creates seasonal nutrient cycling.", "dominant_flora": "Oak, maple, beech, hickory. Understory shrubs. Spring ephemerals.", "locations": ["Eastern North America", "Western Europe", "Eastern China"]},
    {"biome": "grassland_savanna", "climate": "warm, seasonal rainfall (50-150 cm/yr)", "net_primary_productivity_g_m2_yr": 900, "biodiversity": "Moderate. Large herbivore biomass high. Fire-adapted species.", "dominant_flora": "Grasses (Poaceae), scattered trees (acacia, baobab in savannas). Deep root systems.", "locations": ["African Serengeti", "North American Great Plains", "South American Pampas"]},
    {"biome": "desert", "climate": "arid (<25 cm rain/yr), extreme diurnal temperature variation", "net_primary_productivity_g_m2_yr": 90, "biodiversity": "Low but specialized. CAM/C4 photosynthesis. Nocturnal animals.", "dominant_flora": "Xerophytes: cacti, succulents, creosote bush. Annuals after rare rain events.", "locations": ["Sahara", "Sonoran", "Atacama", "Gobi"]},
    {"biome": "tundra", "climate": "cold, short growing season, permafrost", "net_primary_productivity_g_m2_yr": 140, "biodiversity": "Low. Permafrost stores ~1600 Gt carbon (thaw = positive climate feedback).", "dominant_flora": "Mosses, lichens, grasses, dwarf shrubs. Low stature. Active layer 0.3-1 m.", "locations": ["Arctic (Alaska, Canada, Siberia)", "Alpine tundra at high elevations"]},
    {"biome": "coral_reef", "climate": "warm shallow tropical water (<30 m depth, 23-29C)", "net_primary_productivity_g_m2_yr": 2500, "biodiversity": "Highest marine biodiversity. Symbiosis between coral polyps and zooxanthellae (dinoflagellates).", "dominant_flora": "Zooxanthellae (Symbiodinium spp.), calcareous algae, seagrass adjacent."},
    {"biome": "deep_ocean", "climate": "cold, aphotic (>200m), high pressure", "net_primary_productivity_g_m2_yr": 125, "biodiversity": "Specialized. Chemosynthetic communities at hydrothermal vents (tubeworms, clams).", "dominant_flora": "No photosynthesis. Chemosynthetic bacteria at vents. Marine snow from euphotic zone.", "locations": ["Abyssal plains", "Mid-ocean ridges", "Mariana Trench (>11 km)"]},
]

PHYSIOLOGICAL_SYSTEMS: list[dict[str, Any]] = [
    {"system": "cardiovascular", "organs": ["heart", "arteries", "veins", "capillaries"], "function": "Transport oxygen, nutrients, hormones, waste. Thermoregulation. Immune cell trafficking.", "key_parameters": "Cardiac output: 5 L/min resting. Blood volume: ~5 L. MAP = CO * TPR. Baroreceptor reflex for BP homeostasis."},
    {"system": "respiratory", "organs": ["lungs", "trachea", "bronchi", "alveoli", "diaphragm"], "function": "Gas exchange: O2 uptake, CO2 elimination. pH regulation (HCO3-/CO2 buffer).", "key_parameters": "Tidal volume: ~500 mL. Vital capacity: ~4.8 L. Fick's law governs alveolar-capillary diffusion. Hemoglobin O2 saturation curve: sigmoidal (Hill coefficient ~2.8)."},
    {"system": "nervous", "organs": ["brain", "spinal_cord", "peripheral_nerves", "sensory_organs"], "function": "Sensory input integration, motor output, cognition, homeostasis via autonomic NS.", "key_parameters": "Action potential: Na+ influx depolarization, K+ efflux repolarization. Resting potential: -70 mV. Saltatory conduction in myelinated axons."},
    {"system": "immune", "organs": ["bone_marrow", "thymus", "lymph_nodes", "spleen"], "function": "Defense against pathogens. Distinguishes self from non-self. Innate (fast, non-specific) and adaptive (slow, specific, memory) arms.", "key_parameters": "Innate: macrophages, neutrophils, NK cells, complement. Adaptive: B cells (antibodies), T cells (CD4+ helper, CD8+ cytotoxic). MHC-I (all nucleated cells), MHC-II (APCs)."},
    {"system": "digestive", "organs": ["mouth", "esophagus", "stomach", "small_intestine", "large_intestine", "liver", "pancreas"], "function": "Mechanical and chemical breakdown of food. Nutrient absorption (~90% in small intestine). Water reabsorption in colon. Gut microbiome: ~10^14 bacteria.", "key_parameters": "Stomach pH: 1.5-3.5. Small intestine surface area (villi+microvilli): ~300 m^2. Bile emulsifies fats. Pancreatic enzymes: trypsin, chymotrypsin, lipase, amylase."},
    {"system": "endocrine", "organs": ["pituitary", "thyroid", "adrenals", "pancreas", "gonads"], "function": "Hormone-mediated long-distance signaling. Homeostasis, growth, reproduction, metabolism.", "key_parameters": "HPA axis: hypothalamus (CRH) -> pituitary (ACTH) -> adrenal cortex (cortisol). Negative feedback loop. Insulin: beta cells, lowers blood glucose. Glucagon: alpha cells, raises blood glucose."},
]

BIOINFORMATICS_ALGORITHMS: list[dict[str, Any]] = [
    {"algorithm": "Needleman_Wunsch", "type": "global_alignment", "complexity": "O(n*m)", "description": "Dynamic programming for optimal global alignment of two sequences. Gap penalty and substitution matrix (BLOSUM, PAM).", "applications": ["Whole-sequence comparison", "Molecular evolution (ortholog alignment)"]},
    {"algorithm": "Smith_Waterman", "type": "local_alignment", "complexity": "O(n*m)", "description": "Dynamic programming for optimal local alignment. Finds highest-scoring subsegments. Does not penalize unaligned ends.", "applications": ["Domain identification", "Database search (BLAST is heuristic approximation)"]},
    {"algorithm": "BLAST", "type": "heuristic_search", "complexity": "O(n*m) average", "description": "Basic Local Alignment Search Tool. Finds short exact matches (seeds), extends them. Estimates statistical significance (E-value).", "variants": ["BLASTN (nucleotide)", "BLASTP (protein)", "BLASTX (translated nuc->prot)", "TBLASTN (prot->translated nuc)", "PSI-BLAST (iterative profile)"]},
    {"algorithm": "UPGMA", "type": "phylogenetic_tree", "complexity": "O(n^2)", "description": "Unweighted Pair Group Method with Arithmetic Mean. Hierarchical clustering assuming constant evolutionary rate (molecular clock). Ultrametric tree.", "limitation": "Assumes equal rates. May produce incorrect topology if rates vary. Neighbor-Joining is preferred alternative (allows rate variation)."},
]


def transcribe_dna(dna_sequence: str) -> str:
    if not dna_sequence:
        return ""
    result = ""
    for base in dna_sequence.upper():
        r = DNA_TO_RNA.get(base)
        if r is None:
            raise ValueError(f"Invalid DNA base: {base}")
        result += r
    return result


def translate_rna(rna_sequence: str) -> list[str]:
    if not rna_sequence:
        return []
    seq = rna_sequence.upper().replace(" ", "")
    if len(seq) % 3 != 0:
        raise ValueError("RNA sequence length must be a multiple of 3")
    result = []
    for i in range(0, len(seq), 3):
        codon = seq[i:i + 3]
        aa = CODON_TABLE.get(codon)
        if aa is None:
            raise ValueError(f"Invalid codon: {codon}")
        if aa == "STOP":
            break
        result.append(aa)
    return result


def classify_organism(species: str) -> dict[str, Any] | None:
    return ORGANISM_CLASSIFICATION.get(species)


def compute_gc_content(dna_sequence: str) -> float:
    if not dna_sequence:
        return 0.0
    seq = dna_sequence.upper()
    gc = seq.count("G") + seq.count("C")
    return gc / len(seq)


def reverse_complement(dna_sequence: str) -> str:
    complement = {"A": "T", "T": "A", "G": "C", "C": "G"}
    result = ""
    for base in reversed(dna_sequence.upper()):
        c = complement.get(base)
        if c is None:
            raise ValueError(f"Invalid DNA base: {base}")
        result += c
    return result


def compute_protein_mass(amino_acids: list[str]) -> float:
    total = 0.0
    water = 18.015
    for aa in amino_acids:
        if aa not in AMINO_ACID_PROPERTIES:
            raise ValueError(f"Unknown amino acid: {aa}")
        total += AMINO_ACID_PROPERTIES[aa]["mass_Da"]
    n = len(amino_acids)
    return total - (n - 1) * water if n > 0 else 0.0


def hardy_weinberg_expected(p: float) -> dict[str, float]:
    if not (0.0 <= p <= 1.0):
        raise ValueError("Allele frequency must be between 0 and 1")
    q = 1.0 - p
    return {"AA": p * p, "Aa": 2.0 * p * q, "aa": q * q}


def compute_fst(heterozygosity_total: float, heterozygosity_subpop: float) -> float:
    if heterozygosity_total <= 0:
        raise ValueError("Total heterozygosity must be positive")
    return 1.0 - heterozygosity_subpop / heterozygosity_total


def compute_mutation_rate(observed_mutations: int, sites: int, generations: int) -> float:
    if sites <= 0 or generations <= 0:
        raise ValueError("Sites and generations must be positive")
    return observed_mutations / (sites * generations)


def compute_reproductive_number(beta: float, gamma: float) -> float:
    if gamma <= 0:
        raise ValueError("Recovery rate must be positive")
    return beta / gamma


def compute_substitution_matrix_score(seq_a: str, seq_b: str) -> dict[str, Any]:
    transitions = 0
    transversions = 0
    transitions_pairs = {"AG", "GA", "CT", "TC"}
    if len(seq_a) != len(seq_b):
        raise ValueError("Sequences must be equal length")
    for a, b in zip(seq_a.upper(), seq_b.upper()):
        if a != b:
            pair = a + b
            if pair in transitions_pairs:
                transitions += 1
            else:
                transversions += 1
    return {"transitions": transitions, "transversions": transversions, "total_subs": transitions + transversions}


def compute_chemical_synapse_conductance(reversal_potential_mV: float, membrane_potential_mV: float, open_probability: float, max_conductance_nS: float) -> float:
    return open_probability * max_conductance_nS * (1.0 - membrane_potential_mV / reversal_potential_mV)


def compute_action_potential_velocity(diameter_um: float, myelinated: bool = False) -> float:
    factor = 6.0 if myelinated else 1.0
    return factor * math.sqrt(diameter_um)


def compute_population_growth_rate(birth_rate: float, death_rate: float, immigration: float = 0.0, emigration: float = 0.0) -> float:
    return birth_rate - death_rate + immigration - emigration


def compute_species_richness(species_counts: list[int]) -> int:
    return len([c for c in species_counts if c > 0])


def compute_shannon_index(species_counts: list[int]) -> float:
    total = sum(species_counts)
    if total <= 0:
        raise ValueError("Total count must be positive")
    h = 0.0
    for c in species_counts:
        if c > 0:
            p = c / total
            h -= p * math.log(p)
    return h


def compute_simpson_index(species_counts: list[int]) -> float:
    total = sum(species_counts)
    if total <= 0:
        raise ValueError("Total count must be positive")
    d = 0.0
    for c in species_counts:
        p = c / total
        d += p * p
    return d


def compute_dna_tm(dna_sequence: str) -> float:
    seq = dna_sequence.upper()
    length = len(seq)
    if length < 14:
        return 4.0 * (seq.count("G") + seq.count("C")) + 2.0 * (seq.count("A") + seq.count("T"))
    return 64.9 + 41.0 * (seq.count("G") + seq.count("C") - 16.4) / length


def list_known_organisms() -> list[str]:
    return list(ORGANISM_CLASSIFICATION.keys())


def list_neurotransmitters() -> list[str]:
    return [n["name"] for n in NEUROTRANSMITTERS]


def list_biomes() -> list[str]:
    return [b["biome"] for b in ECOSYSTEM_BIOMES]


def get_organism_taxonomy(species: str) -> dict[str, str] | None:
    data = ORGANISM_CLASSIFICATION.get(species)
    if data is None:
        return None
    return {rank: data[rank] for rank in TAXONOMIC_RANKS if rank in data}


def compute_neighbor_joining_dist_matrix(sequences: list[str]) -> list[list[float]]:
    n = len(sequences)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            diffs = sum(1 for a, b in zip(sequences[i], sequences[j]) if a != b)
            matrix[i][j] = diffs
            matrix[j][i] = diffs
    return matrix
