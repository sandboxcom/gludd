"""Behavioral unit tests for the physics biology knowledge module."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "physics"
    / "plugins"
    / "module_utils"
    / "biology.py"
)

MODULE_NAME = "_biology_under_test"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bio() -> ModuleType:
    return _load_module()


class TestDataTables:
    def test_dna_to_rna_lookup(self, bio):
        assert bio.DNA_TO_RNA["A"] == "U"
        assert bio.DNA_TO_RNA["T"] == "A"
        assert bio.DNA_TO_RNA["G"] == "C"
        assert bio.DNA_TO_RNA["C"] == "G"

    def test_codon_table_has_64_entries(self, bio):
        assert len(bio.CODON_TABLE) == 64

    def test_codon_table_stop_codons(self, bio):
        assert bio.CODON_TABLE["UAA"] == "STOP"
        assert bio.CODON_TABLE["UAG"] == "STOP"
        assert bio.CODON_TABLE["UGA"] == "STOP"

    def test_codon_table_start_codon(self, bio):
        assert bio.CODON_TABLE["AUG"] == "Met"

    def test_amino_acid_properties(self, bio):
        assert len(bio.AMINO_ACID_PROPERTIES) == 20
        assert bio.AMINO_ACID_PROPERTIES["Gly"]["mass_Da"] == 75.07
        assert bio.AMINO_ACID_PROPERTIES["Pro"]["polarity"] == "nonpolar"

    def test_nucleotide_properties(self, bio):
        assert bio.NUCLEOTIDE_PROPERTIES["A"]["complement"] == "T"
        assert bio.NUCLEOTIDE_PROPERTIES["G"]["complement"] == "C"
        assert bio.NUCLEOTIDE_PROPERTIES["A"]["type"] == "purine"

    def test_genetic_inheritance(self, bio):
        patterns = [p["pattern"] for p in bio.GENETIC_INHERITANCE]
        assert "autosomal_dominant" in patterns
        assert "autosomal_recessive" in patterns
        assert "x_linked_recessive" in patterns
        assert "mitochondrial" in patterns

    def test_evolutionary_mechanisms(self, bio):
        mechanisms = [m["mechanism"] for m in bio.EVOLUTIONARY_MECHANISMS]
        assert "natural_selection" in mechanisms
        assert "genetic_drift" in mechanisms
        assert "gene_flow" in mechanisms
        assert "mutation" in mechanisms

    def test_taxonomic_ranks(self, bio):
        assert len(bio.TAXONOMIC_RANKS) == 8
        assert bio.TAXONOMIC_RANKS[0] == "domain"
        assert bio.TAXONOMIC_RANKS[-1] == "species"

    def test_organism_classification(self, bio):
        assert "Homo_sapiens" in bio.ORGANISM_CLASSIFICATION
        assert "Escherichia_coli" in bio.ORGANISM_CLASSIFICATION
        assert "Saccharomyces_cerevisiae" in bio.ORGANISM_CLASSIFICATION

    def test_drosophila_model_organism(self, bio):
        dm = bio.ORGANISM_CLASSIFICATION["Drosophila_melanogaster"]
        assert dm["model_organism"] is True
        assert dm["phylum"] == "Arthropoda"

    def test_ecoli_prokaryotic(self, bio):
        ecoli = bio.ORGANISM_CLASSIFICATION["Escherichia_coli"]
        assert ecoli["cell_type"] == "prokaryotic"
        assert ecoli["gram_stain"] == "negative"

    def test_archaea_entry(self, bio):
        mj = bio.ORGANISM_CLASSIFICATION["Methanococcus_jannaschii"]
        assert mj["domain"] == "Archaea"
        assert mj["metabolism"] == "methanogen"

    def test_neurotransmitters(self, bio):
        names = [n["name"] for n in bio.NEUROTRANSMITTERS]
        assert "Dopamine" in names
        assert "GABA" in names
        assert "Glutamate" in names
        assert "Serotonin" in names

    def test_neurotransmitter_properties(self, bio):
        gaba = next(n for n in bio.NEUROTRANSMITTERS if n["name"] == "GABA")
        assert gaba["type"] == "inhibitory"
        assert "Cl-" in str(gaba["receptors"])

    def test_ecosystem_biomes(self, bio):
        biomes = [b["biome"] for b in bio.ECOSYSTEM_BIOMES]
        assert "tropical_rainforest" in biomes
        assert "desert" in biomes
        assert "coral_reef" in biomes
        assert "deep_ocean" in biomes

    def test_physiological_systems(self, bio):
        systems = [s["system"] for s in bio.PHYSIOLOGICAL_SYSTEMS]
        assert "cardiovascular" in systems
        assert "nervous" in systems
        assert "immune" in systems
        assert "endocrine" in systems

    def test_bioinformatics_algorithms(self, bio):
        algos = [a["algorithm"] for a in bio.BIOINFORMATICS_ALGORITHMS]
        assert "Needleman_Wunsch" in algos
        assert "Smith_Waterman" in algos
        assert "BLAST" in algos

    def test_dna_replication_enzymes(self, bio):
        enzymes = [e["enzyme"] for e in bio.DNA_REPLICATION_ENZYMES]
        assert "Helicase" in enzymes
        assert "DNA_Polymerase_III" in enzymes
        assert "Ligase" in enzymes


class TestMolecularBiology:
    def test_transcribe_dna_simple(self, bio):
        rna = bio.transcribe_dna("ATGCGT")
        assert rna == "UACGCA"

    def test_transcribe_dna_all_bases(self, bio):
        rna = bio.transcribe_dna("ATGCATGCATGC")
        assert len(rna) == 12

    def test_transcribe_dna_lowercase(self, bio):
        rna = bio.transcribe_dna("atg")
        assert rna == "UAC"

    def test_transcribe_dna_empty(self, bio):
        assert bio.transcribe_dna("") == ""

    def test_transcribe_dna_invalid_base(self, bio):
        with pytest.raises(ValueError, match="Invalid DNA base"):
            bio.transcribe_dna("ATGB")

    def test_transcribe_dna_thymine_to_adenine(self, bio):
        rna = bio.transcribe_dna("TTTT")
        assert rna == "AAAA"

    def test_translate_rna_simple(self, bio):
        aa = bio.translate_rna("AUGUUUUGA")
        assert aa == ["Met", "Phe"]

    def test_translate_rna_stops_at_stop_codon(self, bio):
        aa = bio.translate_rna("AUGUUUUAGCCCGGA")
        assert aa == ["Met", "Phe"]

    def test_translate_rna_empty(self, bio):
        assert bio.translate_rna("") == []

    def test_translate_rna_not_multiple_of_three(self, bio):
        with pytest.raises(ValueError, match="multiple of 3"):
            bio.translate_rna("AUGC")

    def test_translate_rna_invalid_codon(self, bio):
        with pytest.raises(ValueError, match="Invalid codon"):
            bio.translate_rna("XXXYYYZZZ")

    def test_translate_rna_all_stops(self, bio):
        aa = bio.translate_rna("UGGUAAUAG")
        assert aa == ["Trp"]


class TestGenetics:
    def test_gc_content_even(self, bio):
        gc = bio.compute_gc_content("GCGCATAT")
        assert math.isclose(gc, 0.5)

    def test_gc_content_all_gc(self, bio):
        gc = bio.compute_gc_content("GGGGCCCC")
        assert math.isclose(gc, 1.0)

    def test_gc_content_empty(self, bio):
        assert bio.compute_gc_content("") == 0.0

    def test_reverse_complement(self, bio):
        rc = bio.reverse_complement("ATGC")
        assert rc == "GCAT"

    def test_reverse_complement_palindrome_mirror(self, bio):
        rc = bio.reverse_complement("GATC")
        assert rc == "GATC"

    def test_reverse_complement_invalid(self, bio):
        with pytest.raises(ValueError, match="Invalid DNA base"):
            bio.reverse_complement("ATGB")

    def test_hardy_weinberg_p05(self, bio):
        hw = bio.hardy_weinberg_expected(0.5)
        assert math.isclose(hw["AA"], 0.25)
        assert math.isclose(hw["Aa"], 0.5)
        assert math.isclose(hw["aa"], 0.25)
        assert math.isclose(hw["AA"] + hw["Aa"] + hw["aa"], 1.0)

    def test_hardy_weinberg_extreme(self, bio):
        hw = bio.hardy_weinberg_expected(1.0)
        assert math.isclose(hw["AA"], 1.0)
        assert math.isclose(hw["Aa"], 0.0)
        assert math.isclose(hw["aa"], 0.0)

    def test_hardy_weinberg_invalid(self, bio):
        with pytest.raises(ValueError):
            bio.hardy_weinberg_expected(1.5)
        with pytest.raises(ValueError):
            bio.hardy_weinberg_expected(-0.1)

    def test_fst_zero(self, bio):
        fst = bio.compute_fst(0.5, 0.5)
        assert math.isclose(fst, 0.0)

    def test_fst_subdivision(self, bio):
        fst = bio.compute_fst(0.5, 0.25)
        assert math.isclose(fst, 0.5)

    def test_fst_invalid(self, bio):
        with pytest.raises(ValueError):
            bio.compute_fst(0.0, 0.1)

    def test_mutation_rate(self, bio):
        mu = bio.compute_mutation_rate(5, 1000, 100)
        assert math.isclose(mu, 5e-5)

    def test_mutation_rate_invalid(self, bio):
        with pytest.raises(ValueError):
            bio.compute_mutation_rate(1, 0, 10)


class TestProteinAndSequence:
    def test_protein_mass_single_aa(self, bio):
        mass = bio.compute_protein_mass(["Gly"])
        assert math.isclose(mass, 75.07)

    def test_protein_mass_dipeptide(self, bio):
        mass = bio.compute_protein_mass(["Gly", "Ala"])
        expected = 75.07 + 89.09 - 18.015
        assert math.isclose(mass, expected)

    def test_protein_mass_empty(self, bio):
        assert bio.compute_protein_mass([]) == 0.0

    def test_protein_mass_unknown_aa(self, bio):
        with pytest.raises(ValueError, match="Unknown amino acid"):
            bio.compute_protein_mass(["Xxx"])

    def test_substitution_matrix(self, bio):
        result = bio.compute_substitution_matrix_score("ACGT", "AGGT")
        assert result["transitions"] == 0
        assert result["transversions"] == 1


class TestOrganismClassification:
    def test_classify_organism_known(self, bio):
        org = bio.classify_organism("Homo_sapiens")
        assert org is not None
        assert org["species"] == "Homo sapiens"
        assert org["class"] == "Mammalia"

    def test_classify_organism_e_coli(self, bio):
        org = bio.classify_organism("Escherichia_coli")
        assert org is not None
        assert org["domain"] == "Bacteria"

    def test_classify_organism_unknown(self, bio):
        assert bio.classify_organism("Unknown_species") is None

    def test_get_organism_taxonomy(self, bio):
        tax = bio.get_organism_taxonomy("Homo_sapiens")
        assert tax is not None
        assert tax["domain"] == "Eukarya"
        assert tax["genus"] == "Homo"
        assert tax["species"] == "Homo sapiens"

    def test_get_organism_taxonomy_unknown(self, bio):
        assert bio.get_organism_taxonomy("Mythical_creature") is None

    def test_list_known_organisms(self, bio):
        organisms = bio.list_known_organisms()
        assert "Homo_sapiens" in organisms
        assert len(organisms) >= 8


class TestNeuroscience:
    def test_compute_chemical_synapse_conductance(self, bio):
        g = bio.compute_chemical_synapse_conductance(-70.0, -50.0, 0.5, 10.0)
        assert g > 0

    def test_action_potential_velocity_unmyelinated(self, bio):
        v = bio.compute_action_potential_velocity(1.0, False)
        assert math.isclose(v, 1.0)

    def test_action_potential_velocity_myelinated(self, bio):
        v = bio.compute_action_potential_velocity(1.0, True)
        assert math.isclose(v, 6.0)

    def test_action_potential_velocity_scaling(self, bio):
        v1 = bio.compute_action_potential_velocity(4.0, True)
        v2 = bio.compute_action_potential_velocity(1.0, True)
        assert v1 > v2

    def test_list_neurotransmitters(self, bio):
        nts = bio.list_neurotransmitters()
        assert "Acetylcholine" in nts
        assert "Dopamine" in nts


class TestEcology:
    def test_population_growth_rate(self, bio):
        r = bio.compute_population_growth_rate(0.05, 0.02)
        assert math.isclose(r, 0.03)

    def test_population_growth_with_migration(self, bio):
        r = bio.compute_population_growth_rate(0.05, 0.02, 0.01, 0.005)
        assert math.isclose(r, 0.035)

    def test_species_richness(self, bio):
        sr = bio.compute_species_richness([10, 5, 0, 3, 0])
        assert sr == 3

    def test_shannon_index(self, bio):
        h = bio.compute_shannon_index([10, 10])
        assert math.isclose(h, math.log(2), rel_tol=0.01)

    def test_shannon_index_single_species(self, bio):
        h = bio.compute_shannon_index([100])
        assert math.isclose(h, 0.0)

    def test_simpson_index(self, bio):
        d = bio.compute_simpson_index([10, 10])
        assert math.isclose(d, 0.5)

    def test_shannon_index_invalid(self, bio):
        with pytest.raises(ValueError, match="Total count must be positive"):
            bio.compute_shannon_index([0, 0])

    def test_list_biomes(self, bio):
        biomes = bio.list_biomes()
        assert "tropical_rainforest" in biomes
        assert "tundra" in biomes


class TestBioinformatics:
    def test_dna_tm_short(self, bio):
        tm = bio.compute_dna_tm("ATGC")
        assert tm > 0

    def test_dna_tm_long(self, bio):
        tm = bio.compute_dna_tm("ATGCATGCATGCATGC")
        assert tm > 40

    def test_dna_tm_gc_rich(self, bio):
        tm_gc = bio.compute_dna_tm("GGGGCCCCGGGGCCCC")
        tm_at = bio.compute_dna_tm("AAAATTTTAAAATTTT")
        assert tm_gc > tm_at

    def test_reproductive_number(self, bio):
        r0 = bio.compute_reproductive_number(0.3, 0.1)
        assert math.isclose(r0, 3.0)

    def test_reproductive_number_invalid(self, bio):
        with pytest.raises(ValueError):
            bio.compute_reproductive_number(0.3, 0.0)

    def test_neighbor_joining_matrix(self, bio):
        seqs = ["ACGT", "ACCT", "AGGT"]
        dist = bio.compute_neighbor_joining_dist_matrix(seqs)
        assert len(dist) == 3
        assert len(dist[0]) == 3
        assert dist[0][1] == dist[1][0]
        assert dist[0][0] == 0.0

    def test_neighbor_joining_identical(self, bio):
        seqs = ["ACGT", "ACGT"]
        dist = bio.compute_neighbor_joining_dist_matrix(seqs)
        assert dist[0][1] == 0.0
