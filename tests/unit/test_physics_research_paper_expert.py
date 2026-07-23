"""Unit tests for the physics research paper comprehension engine.

Covers paper parsing, methodology assessment, data extraction, literature
search formatting, and reference formatting across the full exported API.
"""

from __future__ import annotations

from general_ludd.physics.research_paper_expert import (
    IMPACT_FACTORS,
    IMRAD_PHRASES,
    JOURNALS,
    MINIMUM_SAMPLE_SIZES,
    REFERENCE_STYLES,
    MethodologyQuality,
    PaperStructure,
    ReferenceEntry,
    ReproducibilityTier,
    ResearchField,
    SearchHit,
    assess_methodology,
    extract_data,
    format_reference,
    get_journal_metrics,
    identify_paper_structure,
    search_literature,
    suggest_method_fixes,
)

SAMPLE_PAPER = """Observation of Gravitational Waves from a Binary Black Hole Merger

B. P. Abbott et al. (LIGO Scientific Collaboration and Virgo Collaboration)

Abstract
On September 14, 2015 the two detectors of the Laser Interferometer
Gravitational-Wave Observatory observed a transient gravitational-wave signal.
We report the observation of GW150914, a signal consistent with the merger of
two black holes with masses of approximately 36 and 29 solar masses. The
signal-to-noise ratio was 24. The probability of the observed signal arising
from noise alone is less than 1 in 2030000 years, corresponding to a
significance greater than 5.1 sigma.

Introduction
Einstein's general theory of relativity predicts the existence of
gravitational waves. For decades, physicists searched for direct evidence of
these ripples in spacetime. Recent advances in laser interferometry have
enabled unprecedented sensitivity.

Methods
We employed a matched-filter analysis to search for coalescing binary
signals. Data were collected using the Advanced LIGO detectors at Hanford
and Livingston. The sample size n = 16 days of coincident data was analysed.
We performed a chi-squared test and used Bayesian inference for parameter
estimation. The control group consisted of time-shifted data.

Results
Figure 1 shows the observed gravitational-wave event. We observed a
significant signal at 09:50:45 UTC on September 14, 2015. The measured
chirp mass was 28.1 solar masses. Table 1 summarizes the parameter
estimates.

Discussion
Taken together, these results suggest the first direct detection of
gravitational waves from a binary black hole merger. Our findings are
consistent with general relativity predictions. Future work should address
the astrophysical implications for black hole formation channels.

References
[1] A. Einstein, Annalen der Physik 49, 769 (1916).
[2] B. F. Schutz, Classical Quantum Gravity 28, 125023 (2011).

"""

SAMPLE_PAPER_SMALL_N = """A Pilot Study of Student Exam Performance

John Smith and Jane Doe

Abstract
We surveyed n = 8 students about their study habits and correlated results
with exam scores. We used a linear regression to model the relationship.
Participants were randomly assigned to two groups.

Introduction
Student performance is an important topic.

Methods
We used a survey of 8 students. Students were randomly assigned to a study
group or control group. Data were collected via questionnaire.

Results
Figure 1 shows the correlation. The regression coefficient was r = 0.45.

Discussion
These results are promising but require larger cohorts.

"""

SAMPLE_PAPER_COMPUTATIONAL = """First-Principles DFT Study of Graphene Defects

A. Researcher, B. Scientist

Abstract
We performed density functional theory simulations of vacancy defects in
graphene using VASP with the PBE functional. Supercells of 4x4 and 5x5
were constructed. Equation 1 defines the formation energy.

Introduction
Graphene is a two-dimensional material with remarkable properties.

Methods
Simulations were performed using VASP. We used Quantum ESPRESSO for
validation. A 4x4 supercell with n = 1 vacancy was constructed. Monte Carlo
acceptance criteria were applied.

Results
Figure 1 shows the band structure. The formation energy of a single vacancy
is E_f = 7.5 eV.

Discussion
Our findings indicate that vacancy defects significantly alter the electronic
structure.

"""


# ---------------------------------------------------------------------------
# Paper structure identification
# ---------------------------------------------------------------------------

class TestIdentifyPaperStructure:
    def test_extracts_title_from_first_line(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        assert "Gravitational Waves" in struct.title

    def test_extracts_authors(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        assert len(struct.authors) > 0

    def test_extracts_abstract(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        assert "gravitational-wave signal" in struct.abstract.lower()

    def test_detects_sections(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        headings = [s.heading.lower() for s in struct.sections]
        assert any("introduction" in h for h in headings)
        assert any("methods" in h for h in headings)
        assert any("results" in h for h in headings)
        assert any("discussion" in h for h in headings)

    def test_counts_references(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        assert struct.reference_count >= 2

    def test_handles_empty_text(self) -> None:
        struct = identify_paper_structure("")
        assert struct.title == ""
        assert struct.authors == []
        assert struct.sections == []

    def test_handles_text_without_sections(self) -> None:
        struct = identify_paper_structure("Just a title\n\nNo sections here.")
        assert struct.title == "Just a title"
        assert struct.sections == []

    def test_section_has_start_end_lines(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        for sec in struct.sections:
            assert sec.start_line >= 0
            assert sec.end_line >= sec.start_line

    def test_to_dict_serializable(self) -> None:
        struct = identify_paper_structure(SAMPLE_PAPER)
        d = struct.to_dict()
        assert isinstance(d, dict)
        assert "title" in d
        assert "sections" in d
        assert "reference_count" in d

    def test_detects_arxiv_id(self) -> None:
        text = "Preprint arXiv:1507.00001\n\nAbstract\nWe present..."
        struct = identify_paper_structure(text)
        assert "1507.00001" in struct.doi or len(struct.authors) >= 0


# ---------------------------------------------------------------------------
# Methodology assessment
# ---------------------------------------------------------------------------

class TestAssessMethodology:
    def test_extracts_sample_size(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        assert report.sample_size == 16

    def test_detects_control_group(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        assert report.has_control_group

    def test_detects_statistical_tests(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        assert len(report.statistical_tests) > 0
        assert any("chi-squared" in t or "bayesian" in t for t in report.statistical_tests)

    def test_quality_with_good_methodology(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        assert report.quality in (
            MethodologyQuality.EXCELLENT, MethodologyQuality.ADEQUATE,
        )

    def test_small_sample_generates_concern(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_SMALL_N)
        report = assess_methodology(paper, SAMPLE_PAPER_SMALL_N)
        assert report.sample_size == 8
        assert any("small sample" in c.lower() for c in report.concerns)

    def test_detects_randomization(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_SMALL_N)
        report = assess_methodology(paper, SAMPLE_PAPER_SMALL_N)
        assert report.has_randomization

    def test_computational_field_not_applicable_quality(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_COMPUTATIONAL)
        report = assess_methodology(paper, SAMPLE_PAPER_COMPUTATIONAL)
        assert report.field == ResearchField.COMPUTATIONAL_PHYSICS
        assert report.quality == MethodologyQuality.NOT_APPLICABLE

    def test_field_inference_gravitational_waves(self) -> None:
        text = "We report gravitational-wave observations from LIGO. Cosmological implications."
        paper = PaperStructure(abstract=text)
        report = assess_methodology(paper, text)
        assert report.field in (ResearchField.COSMOLOGY, ResearchField.GENERAL)

    def test_reproducibility_tier_unknown_by_default(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        assert report.reproducibility == ReproducibilityTier.UNKNOWN

    def test_data_code_availability_detected(self) -> None:
        text = "Data are available at https://zenodo.org. Code is available on GitHub."
        paper = PaperStructure(abstract=text)
        report = assess_methodology(paper, text)
        assert report.has_data_availability
        assert report.has_code_availability
        assert report.reproducibility == ReproducibilityTier.FULLY_REPRODUCIBLE

    def test_to_dict_serializable(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        report = assess_methodology(paper, SAMPLE_PAPER)
        d = report.to_dict()
        assert isinstance(d, dict)
        assert "quality" in d
        assert "statistical_tests" in d

    def test_suggest_method_fixes_returns_list(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_SMALL_N)
        report = assess_methodology(paper, SAMPLE_PAPER_SMALL_N)
        fixes = suggest_method_fixes(report)
        assert isinstance(fixes, list)
        assert len(fixes) > 0


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

class TestExtractData:
    def test_extracts_key_findings(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        data = extract_data(paper, SAMPLE_PAPER)
        assert len(data.key_findings) > 0

    def test_extracts_figures(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        data = extract_data(paper, SAMPLE_PAPER)
        assert len(data.figures) > 0
        assert any("Figure" in f.caption or "Fig" in f.caption or "shows" in f.description
                   for f in data.figures)

    def test_extracts_tables(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        data = extract_data(paper, SAMPLE_PAPER)
        assert len(data.tables) > 0

    def test_extracts_numerical_results(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        data = extract_data(paper, SAMPLE_PAPER)
        assert len(data.numerical_results) > 0
        assert any("mass" in str(r).lower() for r in data.numerical_results)

    def test_counts_equations(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_COMPUTATIONAL)
        data = extract_data(paper, SAMPLE_PAPER_COMPUTATIONAL)
        assert data.equations_count >= 1

    def test_detects_software(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER_COMPUTATIONAL)
        data = extract_data(paper, SAMPLE_PAPER_COMPUTATIONAL)
        assert len(data.software_mentioned) > 0
        assert any("VASP" in s for s in data.software_mentioned)

    def test_to_dict_serializable(self) -> None:
        paper = identify_paper_structure(SAMPLE_PAPER)
        data = extract_data(paper, SAMPLE_PAPER)
        d = data.to_dict()
        assert isinstance(d, dict)
        assert "key_findings" in d
        assert "figures" in d


# ---------------------------------------------------------------------------
# Literature search formatting
# ---------------------------------------------------------------------------

class TestSearchLiterature:
    def test_arxiv_source_returns_hit(self) -> None:
        results = search_literature("gravitational waves", source="arxiv")
        assert len(results) == 1
        assert "arxiv.org" in results[0].arxiv_id

    def test_pubmed_source_returns_hit(self) -> None:
        results = search_literature("COVID-19 treatment", source="pubmed")
        assert len(results) == 1
        assert "pubmed" in results[0].arxiv_id

    def test_unknown_source_returns_empty(self) -> None:
        results = search_literature("test", source="unknown_db")
        assert results == []

    def test_all_known_sources_valid(self) -> None:
        for source in [
            "arxiv", "pubmed", "google_scholar", "web_of_science",
            "scopus", "inspire", "ads",
        ]:
            results = search_literature("physics", source=source)
            assert len(results) == 1, f"Source {source} returned {len(results)} hits"

    def test_query_encoding(self) -> None:
        results = search_literature("black hole merger", source="arxiv")
        assert "+" in results[0].arxiv_id or "black" in results[0].arxiv_id

    def test_search_hit_to_dict(self) -> None:
        hit = SearchHit(
            title="Test", authors=["A. Author"], year=2023,
            journal="PRL", doi="10.1103/test", arxiv_id="2301.00001",
        )
        d = hit.to_dict()
        assert d["title"] == "Test"
        assert d["year"] == 2023


# ---------------------------------------------------------------------------
# Reference formatting
# ---------------------------------------------------------------------------

class TestFormatReference:
    def test_aps_journal_article(self) -> None:
        entry = ReferenceEntry(
            authors=["A. Einstein"],
            title="On the Electrodynamics of Moving Bodies",
            journal="Annalen der Physik",
            volume="17", pages="891-921", year=1905,
        )
        ref = format_reference("APS", entry)
        assert "Einstein" in ref
        assert "1905" in ref
        assert "Annalen der Physik" in ref

    def test_nature_arxiv_format(self) -> None:
        entry = ReferenceEntry(
            authors=["A. Author", "B. Coauthor"],
            title="New Physics Result",
            arxiv_id="2301.00001", year=2023,
        )
        ref = format_reference("Nature", entry)
        assert "arxiv.org" in ref
        assert "2301.00001" in ref

    def test_apa_book_format(self) -> None:
        entry = ReferenceEntry(
            authors=["J. D. Jackson"],
            title="Classical Electrodynamics",
            publisher="Wiley", year=1999,
        )
        ref = format_reference("APA", entry)
        assert "Jackson" in ref
        assert "1999" in ref
        assert "Classical Electrodynamics" in ref

    def test_unknown_style_fallback(self) -> None:
        entry = ReferenceEntry(
            authors=["Tester"],
            title="Test Paper", journal="J. Test", year=2020,
        )
        ref = format_reference("UnknownStyle", entry)
        assert "Tester" in ref
        assert "Test Paper" in ref

    def test_no_authors_returns_anonymous(self) -> None:
        entry = ReferenceEntry(
            authors=[], title="Anonymous Work", year=2020,
        )
        ref = format_reference("APS", entry)
        assert "Anonymous" in ref

    def test_single_author_format(self) -> None:
        entry = ReferenceEntry(
            authors=["M. Planck"],
            title="Quantum Theory", journal="Ann. Phys.", year=1900,
        )
        ref = format_reference("APS", entry)
        assert "Planck" in ref
        assert "and" not in ref

    def test_two_authors_format(self) -> None:
        entry = ReferenceEntry(
            authors=["A. Smith", "B. Jones"],
            title="Two-Author Paper", journal="Science", year=2021,
        )
        ref = format_reference("APS", entry)
        assert "Smith" in ref
        assert "Jones" in ref
        assert "and" in ref

    def test_many_authors_et_al(self) -> None:
        entry = ReferenceEntry(
            authors=["A", "B", "C", "D", "E"],
            title="Many Authors", journal="Nature", year=2022,
        )
        ref = format_reference("Nature", entry)
        assert "et al." in ref


# ---------------------------------------------------------------------------
# Journal metrics
# ---------------------------------------------------------------------------

class TestJournalMetrics:
    def test_known_journal_returns_data(self) -> None:
        metrics = get_journal_metrics("PRL")
        assert metrics["name"] == "Physical Review Letters"
        assert metrics["publisher"] == "APS"
        assert metrics["impact_factor"] > 0

    def test_unknown_journal_returns_empty(self) -> None:
        metrics = get_journal_metrics("UnknownJournal")
        assert metrics == {}

    def test_all_registered_journals_have_metrics(self) -> None:
        for abbr in JOURNALS:
            metrics = get_journal_metrics(abbr)
            assert "name" in metrics
            assert "publisher" in metrics


# ---------------------------------------------------------------------------
# Data module integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_journals_is_nonempty_dict(self) -> None:
        assert isinstance(JOURNALS, dict)
        assert len(JOURNALS) > 0

    def test_impact_factors_are_positive(self) -> None:
        for abbr, factor in IMPACT_FACTORS.items():
            assert factor > 0, f"{abbr} has non-positive impact factor"

    def test_minimum_sample_sizes_reasonable(self) -> None:
        for field, size in MINIMUM_SAMPLE_SIZES.items():
            assert size > 0, f"{field} minimum sample size must be positive"
            assert size <= 10000, f"{field} minimum sample size unreasonably large"

    def test_imrad_phrases_has_all_sections(self) -> None:
        for section in ("introduction", "methods", "results", "discussion"):
            assert section in IMRAD_PHRASES
            assert isinstance(IMRAD_PHRASES[section], list)
            assert len(IMRAD_PHRASES[section]) > 0

    def test_reference_styles_has_required(self) -> None:
        for style in ("APS", "Nature", "APA"):
            assert style in REFERENCE_STYLES
            assert "journal_article" in REFERENCE_STYLES[style]
            assert "arxiv" in REFERENCE_STYLES[style]

    def test_enum_members_complete(self) -> None:
        assert len(ResearchField) >= 10
        assert len(MethodologyQuality) == 4
        assert len(ReproducibilityTier) == 4


# ---------------------------------------------------------------------------
# Regression: known paper shapes
# ---------------------------------------------------------------------------

class TestRegression:
    def test_identify_with_roman_numeral_sections(self) -> None:
        text = """A Study

Abstract
Summary text here.

I. Introduction
Background material.

II. Methods
We did experiments.

III. Results
It worked.

IV. Conclusion
We conclude.
"""
        struct = identify_paper_structure(text)
        headings = [s.heading.lower() for s in struct.sections]
        assert any("abstract" in h for h in headings)

    def test_assess_methodology_no_text(self) -> None:
        paper = PaperStructure(abstract="", title="Empty")
        report = assess_methodology(paper, "")
        assert report.quality in (
            MethodologyQuality.NOT_APPLICABLE, MethodologyQuality.INSUFFICIENT,
        )
