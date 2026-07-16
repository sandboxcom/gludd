#!/usr/bin/env python3
"""Research paper comprehension engine for the NF.X Physics collection.

Consumes academic paper text and produces structured metadata, methodology
assessment, data extraction, and literature-search formatting. Supports
the major physics and general-science journals.

Pipeline::

    raw_text  -->  identify_paper_structure(text)  -->  PaperStructure
    paper     -->  assess_methodology(paper)        -->  MethodologyReport
    paper     -->  extract_data(paper)               -->  ExtractedData
    query     -->  search_literature(query, source)  -->  list[SearchHit]
    entry     -->  format_reference(style, entry)    -->  str

Journal knowledge (``JOURNALS``, ``IMPACT_FACTORS``), sample-size heuristics
(``MINIMUM_SAMPLE_SIZES``), and IMRaD writing helpers
(``IMRAD_PHRASES``) are embedded as module-level data so the engine is
self-contained with no network dependency.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

SECTION_HEADERS: list[str] = [
    "abstract",
    "introduction",
    "methods",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "bibliography",
    "acknowledgments",
    "supplementary",
    "appendix",
    "background",
    "related work",
    "future work",
]

JOURNALS: dict[str, dict[str, str]] = {
    "PRL": {"name": "Physical Review Letters", "publisher": "APS", "issn": "0031-9007"},
    "PRD": {"name": "Physical Review D", "publisher": "APS", "issn": "2470-0010"},
    "PRB": {"name": "Physical Review B", "publisher": "APS", "issn": "2469-9950"},
    "PRA": {"name": "Physical Review A", "publisher": "APS", "issn": "2469-9926"},
    "PRC": {"name": "Physical Review C", "publisher": "APS", "issn": "2469-9985"},
    "PRE": {"name": "Physical Review E", "publisher": "APS", "issn": "2470-0045"},
    "PRX": {"name": "Physical Review X", "publisher": "APS", "issn": "2160-3308"},
    "RMP": {"name": "Reviews of Modern Physics", "publisher": "APS", "issn": "0034-6861"},
    "Nature": {"name": "Nature", "publisher": "Springer Nature", "issn": "0028-0836"},
    "Science": {"name": "Science", "publisher": "AAAS", "issn": "0036-8075"},
    "JACS": {"name": "J. of the American Chemical Society", "publisher": "ACS", "issn": "0002-7863"},
    "Angewandte": {"name": "Angewandte Chemie", "publisher": "Wiley", "issn": "1433-7851"},
    "ApJ": {"name": "The Astrophysical Journal", "publisher": "AAS/IOP", "issn": "0004-637X"},
    "MNRAS": {"name": "Monthly Notices of the RAS", "publisher": "OUP", "issn": "0035-8711"},
}

IMPACT_FACTORS: dict[str, float] = {
    "Nature": 64.8,
    "Science": 56.9,
    "PRL": 8.6,
    "JACS": 15.0,
    "Angewandte": 16.6,
    "PRX": 12.5,
    "RMP": 50.0,
    "ApJ": 5.0,
    "MNRAS": 4.8,
    "PRD": 5.0,
    "PRB": 3.7,
    "PRA": 3.0,
    "PRC": 3.2,
    "PRE": 2.5,
}

MINIMUM_SAMPLE_SIZES: dict[str, int] = {
    "survey": 30,
    "clinical_trial": 60,
    "observational_astronomy": 1,
    "particle_physics_experiment": 1,
    "computational_simulation": 1,
    "qualitative_interview": 10,
    "machine_learning": 1000,
    "materials_synthesis": 3,
}

VALID_STATISTICAL_TESTS: frozenset[str] = frozenset({
    "t-test",
    "chi-squared",
    "ANOVA",
    "Mann-Whitney U",
    "Wilcoxon signed-rank",
    "Kruskal-Wallis",
    "Fisher exact",
    "Kolmogorov-Smirnov",
    "Shapiro-Wilk",
    "linear regression",
    "logistic regression",
    "Cox proportional hazards",
    "Kaplan-Meier",
    "Pearson correlation",
    "Spearman correlation",
    "principal component analysis",
    "factor analysis",
    "cluster analysis",
    "Bayesian inference",
    "Markov chain Monte Carlo",
    "F-test",
})

IMRAD_PHRASES: dict[str, list[str]] = {
    "introduction": [
        "Recent advances in ... have enabled ...",
        "Despite significant progress, ... remains poorly understood.",
        "Here we address the question of ...",
        "Previous studies have shown ... however ...",
        "To the best of our knowledge, no prior work has ...",
    ],
    "methods": [
        "We employed ... to measure ...",
        "Samples were prepared by ...",
        "Data were collected using ...",
        "The experimental setup consisted of ...",
        "Simulations were performed using ...",
    ],
    "results": [
        "Figure 1 shows ...",
        "We observed a significant ... (p < 0.05)",
        "The measured value of ... was ...",
        "A clear trend emerged in ...",
        "Consistent with our prediction, ...",
    ],
    "discussion": [
        "Taken together, these results suggest ...",
        "Our findings are consistent with ...",
        "An alternative interpretation is ...",
        "These results extend prior work by ...",
        "Several limitations should be noted.",
        "Future work should address ...",
    ],
}

REFERENCE_STYLES: dict[str, dict[str, str]] = {
    "APS": {
        "journal_article": "{authors}. {title}. {journal} {volume}, {pages} ({year}).",
        "book": "{authors}. {title} ({publisher}, {year}).",
        "arxiv": "{authors}. {title}. arXiv:{arxiv_id} [{category}] ({year}).",
    },
    "Nature": {
        "journal_article": "{authors}. {title}. {journal} {volume}, {pages} ({year}).",
        "book": "{authors}. {title}. {publisher} ({year}).",
        "arxiv": "{authors}. {title}. Preprint at https://arxiv.org/abs/{arxiv_id} ({year}).",
    },
    "APA": {
        "journal_article": "{authors} ({year}). {title}. {journal}, {volume}, {pages}.",
        "book": "{authors} ({year}). {title}. {publisher}.",
        "arxiv": "{authors} ({year}). {title}. arXiv:{arxiv_id}.",
    },
}


class ResearchField(enum.Enum):
    ASTROPHYSICS = "astrophysics"
    CONDENSED_MATTER = "condensed_matter"
    PARTICLE_PHYSICS = "particle_physics"
    QUANTUM_INFO = "quantum_information"
    COSMOLOGY = "cosmology"
    NUCLEAR_PHYSICS = "nuclear_physics"
    PLASMA_PHYSICS = "plasma_physics"
    BIOPHYSICS = "biophysics"
    CHEMICAL_PHYSICS = "chemical_physics"
    COMPUTATIONAL_PHYSICS = "computational_physics"
    GENERAL = "general"


class MethodologyQuality(enum.Enum):
    EXCELLENT = "excellent"
    ADEQUATE = "adequate"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"


class ReproducibilityTier(enum.Enum):
    FULLY_REPRODUCIBLE = "fully_reproducible"
    PARTIALLY_REPRODUCIBLE = "partially_reproducible"
    NOT_REPRODUCIBLE = "not_reproducible"
    UNKNOWN = "unknown"


@dataclass
class PaperSection:
    heading: str
    start_line: int
    end_line: int
    content_preview: str = ""


@dataclass
class PaperStructure:
    title: str = ""
    authors: list[str] = dc_field(default_factory=list)
    abstract: str = ""
    sections: list[PaperSection] = dc_field(default_factory=list)
    reference_count: int = 0
    doi: str = ""
    journal: str = ""
    year: int = 0
    keywords: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "sections": [
                {"heading": s.heading, "start_line": s.start_line, "end_line": s.end_line}
                for s in self.sections
            ],
            "reference_count": self.reference_count,
            "doi": self.doi,
            "journal": self.journal,
            "year": self.year,
            "keywords": self.keywords,
        }


@dataclass
class MethodologyReport:
    sample_size: int = 0
    sample_size_adequate: bool = True
    research_field: ResearchField = ResearchField.GENERAL
    has_control_group: bool = False
    has_blinding: bool = False
    has_randomization: bool = False
    statistical_tests: list[str] = dc_field(default_factory=list)
    quality: MethodologyQuality = MethodologyQuality.NOT_APPLICABLE
    reproducibility: ReproducibilityTier = ReproducibilityTier.UNKNOWN
    has_data_availability: bool = False
    has_code_availability: bool = False
    conflicts_of_interest: str = ""
    ethical_approval: bool = False
    concerns: list[str] = dc_field(default_factory=list)
    recommendations: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "sample_size_adequate": self.sample_size_adequate,
            "research_field": self.research_field.value,
            "has_control_group": self.has_control_group,
            "has_blinding": self.has_blinding,
            "has_randomization": self.has_randomization,
            "statistical_tests": self.statistical_tests,
            "quality": self.quality.value,
            "reproducibility": self.reproducibility.value,
            "has_data_availability": self.has_data_availability,
            "has_code_availability": self.has_code_availability,
            "conflicts_of_interest": self.conflicts_of_interest,
            "ethical_approval": self.ethical_approval,
            "concerns": self.concerns,
            "recommendations": self.recommendations,
        }


@dataclass
class ExtractedFigure:
    caption: str
    description: str
    figure_number: int = 0
    panel_count: int = 1


@dataclass
class ExtractedTable:
    caption: str
    description: str
    table_number: int = 0
    row_count: int = 0
    column_count: int = 0


@dataclass
class ExtractedData:
    title: str = ""
    key_findings: list[str] = dc_field(default_factory=list)
    figures: list[ExtractedFigure] = dc_field(default_factory=list)
    tables: list[ExtractedTable] = dc_field(default_factory=list)
    numerical_results: list[dict[str, Any]] = dc_field(default_factory=list)
    datasets_mentioned: list[str] = dc_field(default_factory=list)
    software_mentioned: list[str] = dc_field(default_factory=list)
    equations_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "key_findings": self.key_findings,
            "figures": [
                {"caption": f.caption, "description": f.description,
                 "figure_number": f.figure_number, "panel_count": f.panel_count}
                for f in self.figures
            ],
            "tables": [
                {"caption": t.caption, "description": t.description,
                 "table_number": t.table_number, "row_count": t.row_count,
                 "column_count": t.column_count}
                for t in self.tables
            ],
            "numerical_results": self.numerical_results,
            "datasets_mentioned": self.datasets_mentioned,
            "software_mentioned": self.software_mentioned,
            "equations_count": self.equations_count,
        }


@dataclass
class SearchHit:
    title: str
    authors: list[str]
    year: int
    journal: str
    doi: str
    arxiv_id: str = ""
    abstract: str = ""
    citation_count: int = 0
    relevance_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "journal": self.journal,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "abstract": self.abstract,
            "citation_count": self.citation_count,
            "relevance_score": self.relevance_score,
        }


@dataclass
class ReferenceEntry:
    authors: list[str] = dc_field(default_factory=list)
    title: str = ""
    journal: str = ""
    volume: str = ""
    pages: str = ""
    year: int = 0
    doi: str = ""
    arxiv_id: str = ""
    publisher: str = ""


SECTION_RE: re.Pattern[str] = re.compile(
    r"^(?:\d+[\.\s]+)?(abstract|introduction|methods|materials\s+and\s+methods|"
    r"results|discussion|conclusion|references|bibliography|acknowledgments?|"
    r"supplementary|appendix|background|related\s+work|future\s+work)",
    re.IGNORECASE | re.MULTILINE,
)

AUTHOR_RE: re.Pattern[str] = re.compile(
    r"(?:author[s]?[:;]?\s*)?([A-Z][a-z]+(?:\s+[A-Z]\.?)*\s+[A-Z][a-z]+)",
)

DOI_RE: re.Pattern[str] = re.compile(
    r"\b(10\.\d{4,}(?:\.\d+)*\/[^\s\"\']+)",
)

ARXIV_RE: re.Pattern[str] = re.compile(
    r"arxiv[:\.]?\s*(\d{4}\.\d{4,}(?:v\d+)?)",
    re.IGNORECASE,
)

FIGURE_RE: re.Pattern[str] = re.compile(
    r"(?:Fig(?:ure)?\.?\s*(\d+)[,:]?\s*)(.*?)(?=(?:Fig(?:ure)?\.?\s*\d+|Table\.?\s*\d+|$))",
    re.IGNORECASE | re.DOTALL,
)

TABLE_RE: re.Pattern[str] = re.compile(
    r"(?:Table\.?\s*(\d+)[,:]?\s*)(.*?)(?=(?:Table\.?\s*\d+|Fig(?:ure)?\.?\s*\d+|$))",
    re.IGNORECASE | re.DOTALL,
)

EQUATION_RE: re.Pattern[str] = re.compile(
    r"(?:equation|eq\.?)\s*\(?\d+\)?",
    re.IGNORECASE,
)

SAMPLE_SIZE_RE: re.Pattern[str] = re.compile(
    r"(?:n\s*=\s*|sample\s+size\s*(?:of|=|:)?\s*|N\s*=\s*)(\d+)",
    re.IGNORECASE,
)

STAT_TEST_RE: re.Pattern[str] = re.compile(
    r"(?:we\s+(?:used|performed|applied|conducted|ran|employed)|"
    r"analysed\s+(?:using|with)|tested\s+(?:using|with)|"
    r"(?:was|were)\s+(?:analysed|tested|assessed|evaluated)\s+(?:using|with|by))\s+"
    r"(?:a\s+|the\s+)?([\w\s-]+?(?:test|ANOVA|regression|correlation|"
    r"analysis|inference|MCMC|Kaplan-Meier|Cox))",
    re.IGNORECASE,
)

CONTROL_RE: re.Pattern[str] = re.compile(
    r"\b(?:control\s+group|control\s+sample|control\s+experiment|"
    r"placebo\s+group|sham\s+group|baseline\s+group)\b",
    re.IGNORECASE,
)

DATA_AVAIL_RE: re.Pattern[str] = re.compile(
    r"\b(?:data\s+(?:availability|are\s+available|can\s+be\s+(?:accessed|found|obtained)|"
    r"deposited|archived)|open\s+data|FAIR\s+data)\b",
    re.IGNORECASE,
)

CODE_AVAIL_RE: re.Pattern[str] = re.compile(
    r"\b(?:code\s+(?:availability|is\s+available|can\s+be\s+(?:accessed|found|obtained)|"
    r"repository|on\s+GitHub|on\s+GitLab)|open\s+source\s+code)\b",
    re.IGNORECASE,
)

SOFTWARE_RE: re.Pattern[str] = re.compile(
    r"\b(?:Matlab|MATLAB|Mathematica|Python|R\s|ROOT|Geant4|LAMMPS|VASP|"
    r"Quantum ESPRESSO|Gaussian|WIEN2k|COMSOL|ANSYS|Abaqus|"
    r"HEPMC|Rivet|FastJet|MADGRAPH|Pythia|Sherpa|Delphes|"
    r"Astropy|SciPy|NumPy|pandas|TensorFlow|PyTorch|JAX)\b",
)


def identify_paper_structure(text: str) -> PaperStructure:
    """Parse raw academic-paper text into structured metadata.

    Parameters
    ----------
    text:
        Full-text content of a paper (plain text, not PDF binary).

    Returns
    -------
    PaperStructure
        Extracted title, authors, abstract, section boundaries, reference
        count, DOI, journal, year, and keywords.  Defaults to empty values
        when a field cannot be detected.
    """
    lines = text.split("\n")
    structure = PaperStructure()

    if lines:
        structure.title = lines[0].strip()

    text_lower = text.lower()

    author_matches = AUTHOR_RE.findall(text[:2000])
    structure.authors = list(dict.fromkeys(a.strip() for a in author_matches))

    doi_match = DOI_RE.search(text)
    if doi_match:
        structure.doi = doi_match.group(1)

    for abbr, info in JOURNALS.items():
        if abbr in text[:1000] or info["name"].lower() in text_lower[:3000]:
            structure.journal = info["name"]
            break

    year_match = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", text[:2000])
    if year_match:
        structure.year = int(year_match.group(1))

    abstract_match = _extract_abstract(text)
    if abstract_match:
        structure.abstract = abstract_match

    section_matches = list(SECTION_RE.finditer(text))
    for i, match in enumerate(section_matches):
        heading = match.group(0).strip()
        start = match.start()
        end = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(text)
        preview = text[start:start + 80].strip()
        section = PaperSection(
            heading=heading,
            start_line=text[:start].count("\n"),
            end_line=text[:end].count("\n"),
            content_preview=preview,
        )
        structure.sections.append(section)

    ref_section_idx = None
    for i, sec in enumerate(structure.sections):
        if sec.heading.lower() in ("references", "bibliography"):
            ref_section_idx = i
            break
    if ref_section_idx is not None:
        ref_text = _section_content(text, structure.sections, ref_section_idx)
        structure.reference_count = len(
            re.findall(r"^\s*\[\d+\]", ref_text, re.MULTILINE)
        ) or len(re.findall(r"^\s*\d+\.\s", ref_text, re.MULTILINE))

    kw_match = re.search(
        r"(?:keywords?|index\s+terms?)[\s:]+(.+?)(?:\n\n|\n[A-Z])",
        text_lower, re.DOTALL,
    )
    if kw_match:
        structure.keywords = [
            k.strip() for k in re.split(r"[;,]", kw_match.group(1)) if k.strip()
        ]

    return structure


def _extract_abstract(text: str) -> str:
    abstract_match = re.search(
        r"\babstract\b\s*\n+(.*?)(?:\n\s*\n|\n(?:introduction|I\.\s))",
        text, re.IGNORECASE | re.DOTALL,
    )
    if abstract_match:
        return abstract_match.group(1).strip()
    header_idx = text.lower().find("abstract")
    if header_idx != -1:
        after = text[header_idx + 8:]
        intro_idx = re.search(
            r"\n(?:introduction|I\.\s)", after, re.IGNORECASE,
        )
        if intro_idx:
            return after[: intro_idx.start()].strip()
        return after[:3000].strip()
    return ""


def _section_content(
    text: str, sections: list[PaperSection], idx: int
) -> str:
    sec = sections[idx]
    lines = text.split("\n")
    return "\n".join(lines[sec.start_line : sec.end_line])


def assess_methodology(paper: PaperStructure, full_text: str = "") -> MethodologyReport:
    """Assess the methodological rigour of a parsed paper.

    Parameters
    ----------
    paper:
        Parsed paper structure from :func:`identify_paper_structure`.
    full_text:
        Optional full paper text for deeper scanning.  When empty the
        abstract and section contents already captured in *paper* are used.

    Returns
    -------
    MethodologyReport
        Sample-size adequacy, control-group / blinding / randomisation
        presence, statistical-test catalogue, reproducibility tier, data
        and code availability, conflict-of-interest detection, ethical
        approval, and any methodology concerns.
    """
    text = full_text or paper.abstract
    report = MethodologyReport()

    report.research_field = _infer_field(text)

    n_match = SAMPLE_SIZE_RE.search(text)
    if n_match:
        report.sample_size = int(n_match.group(1))

    field_key = report.research_field.value
    for pattern, minimum in [
        ("survey", 30),
        ("clinical", 60),
        ("particle", 1),
        ("materials_synthesis", 3),
    ]:
        if pattern in field_key:
            report.sample_size_adequate = report.sample_size >= minimum
            break
    else:
        report.sample_size_adequate = report.sample_size >= 30

    report.has_control_group = bool(CONTROL_RE.search(text))
    report.has_blinding = bool(
        re.search(r"\b(?:double.?blind|single.?blind|blinded)\b", text, re.IGNORECASE)
    )
    report.has_randomization = bool(
        re.search(r"\b(?:randomi[sz](?:ed|ation)|random\s+(?:assign|allocat))",
                  text, re.IGNORECASE)
    )

    test_matches = STAT_TEST_RE.findall(text)
    report.statistical_tests = [
        t.strip().lower() for t in test_matches
        if any(v in t.lower() for v in VALID_STATISTICAL_TESTS)
    ]
    report.statistical_tests = list(dict.fromkeys(report.statistical_tests))

    report.has_data_availability = bool(DATA_AVAIL_RE.search(text))
    report.has_code_availability = bool(CODE_AVAIL_RE.search(text))

    coi_match = re.search(
        r"(?:conflict[s]?\s+of\s+interest|competing\s+(?:interest|financial))(.*?)(?:\n\n|$)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if coi_match:
        report.conflicts_of_interest = coi_match.group(1).strip()

    report.ethical_approval = bool(
        re.search(r"\b(?:ethical\s+approval|IRB\s+approval|institutional\s+review|"
                  r"ethics\s+committee|IACUC)\b", text, re.IGNORECASE)
    )

    report.quality = _compute_quality(report)
    report.reproducibility = _compute_reproducibility(report)
    _populate_concerns_and_recommendations(report)

    return report


def _infer_field(text: str) -> ResearchField:
    field_keywords: dict[str, ResearchField] = {
        "galax": ResearchField.ASTROPHYSICS,
        "stellar": ResearchField.ASTROPHYSICS,
        "cosmolog": ResearchField.COSMOLOGY,
        "CMB": ResearchField.COSMOLOGY,
        "dark energy": ResearchField.COSMOLOGY,
        "dark matter": ResearchField.COSMOLOGY,
        "superconduct": ResearchField.CONDENSED_MATTER,
        "topological": ResearchField.CONDENSED_MATTER,
        "fermi surface": ResearchField.CONDENSED_MATTER,
        "higgs": ResearchField.PARTICLE_PHYSICS,
        "quark": ResearchField.PARTICLE_PHYSICS,
        "neutrino": ResearchField.PARTICLE_PHYSICS,
        "standard model": ResearchField.PARTICLE_PHYSICS,
        "LHC": ResearchField.PARTICLE_PHYSICS,
        "quantum computing": ResearchField.QUANTUM_INFO,
        "qubit": ResearchField.QUANTUM_INFO,
        "entanglement": ResearchField.QUANTUM_INFO,
        "nuclei": ResearchField.NUCLEAR_PHYSICS,
        "isotope": ResearchField.NUCLEAR_PHYSICS,
        "plasma": ResearchField.PLASMA_PHYSICS,
        "tokamak": ResearchField.PLASMA_PHYSICS,
        "protein": ResearchField.BIOPHYSICS,
        "DNA": ResearchField.BIOPHYSICS,
        "molecular dynamics": ResearchField.CHEMICAL_PHYSICS,
        "DFT": ResearchField.CHEMICAL_PHYSICS,
        "Monte Carlo": ResearchField.COMPUTATIONAL_PHYSICS,
    }
    text_lower = text.lower()
    for keyword, field in field_keywords.items():
        if keyword.lower() in text_lower:
            return field
    return ResearchField.GENERAL


def _compute_quality(report: MethodologyReport) -> MethodologyQuality:
    if report.research_field in (
        ResearchField.COMPUTATIONAL_PHYSICS,
        ResearchField.ASTROPHYSICS,
    ):
        return MethodologyQuality.NOT_APPLICABLE

    score = 0
    if report.sample_size_adequate:
        score += 1
    if report.has_control_group:
        score += 1
    if report.has_randomization:
        score += 1
    if report.statistical_tests:
        score += 1

    if score >= 3:
        return MethodologyQuality.EXCELLENT
    if score >= 2:
        return MethodologyQuality.ADEQUATE
    return MethodologyQuality.INSUFFICIENT


def _compute_reproducibility(report: MethodologyReport) -> ReproducibilityTier:
    if report.has_data_availability and report.has_code_availability:
        return ReproducibilityTier.FULLY_REPRODUCIBLE
    if report.has_data_availability or report.has_code_availability:
        return ReproducibilityTier.PARTIALLY_REPRODUCIBLE
    return ReproducibilityTier.UNKNOWN


def _populate_concerns_and_recommendations(report: MethodologyReport) -> None:
    if report.sample_size < 10 and report.research_field not in (
        ResearchField.PARTICLE_PHYSICS,
        ResearchField.COMPUTATIONAL_PHYSICS,
        ResearchField.ASTROPHYSICS,
    ):
        report.concerns.append(
            f"Very small sample size (n={report.sample_size}) for {report.research_field.value}"
        )
        report.recommendations.append("Increase sample size or justify small-N design")

    if not report.statistical_tests and report.research_field not in (
        ResearchField.COMPUTATIONAL_PHYSICS,
    ):
        report.concerns.append("No statistical tests detected in methods")
        report.recommendations.append("Report the specific statistical tests used")

    if (
        report.research_field not in (ResearchField.COMPUTATIONAL_PHYSICS, ResearchField.ASTROPHYSICS)
        and not report.has_control_group
        and report.research_field not in (ResearchField.PARTICLE_PHYSICS,)
    ):
        report.concerns.append("No control group detected")
        report.recommendations.append("Add control group or justify its absence")

    if not report.has_data_availability:
        report.recommendations.append("Include data availability statement")
    if not report.has_code_availability:
        report.recommendations.append("Consider making analysis code publicly available")

    if report.conflicts_of_interest:
        report.concerns.append("Conflicts of interest reported")
        report.recommendations.append("Review COI statements for completeness")


def extract_data(paper: PaperStructure, full_text: str = "") -> ExtractedData:
    """Extract figures, tables, numerical results, and software mentions.

    Parameters
    ----------
    paper:
        Parsed paper structure.
    full_text:
        Full paper text.  When empty the abstract and captured section
        previews are used.

    Returns
    -------
    ExtractedData
        Key findings, figure/table captions, numerical results, datasets,
        software mentions, and equation count.
    """
    text = full_text or _all_section_text(paper)
    data = ExtractedData(title=paper.title)

    finding_sentences = re.findall(
        r"(?:We\s+(?:find|found|show|demonstrate|report|observe|identify|discover)|"
        r"Our\s+(?:results|findings|data)\s+(?:show|demonstrate|indicate|suggest)|"
        r"(?:significantly|notably|strikingly|importantly),?\s+)([^.!?]+[.!?])",
        text,
    )
    data.key_findings = [s.strip() for s in finding_sentences[:10]]

    fig_matches = FIGURE_RE.findall(text)
    for num, caption in fig_matches[:20]:
        data.figures.append(
            ExtractedFigure(
                caption=caption.strip()[:200],
                description=caption.strip()[:100],
                figure_number=int(num) if num.isdigit() else 0,
                panel_count=caption.count("(") + caption.count("(a)") * 2,
            )
        )

    table_matches = TABLE_RE.findall(text)
    for num, caption in table_matches[:20]:
        rows = caption.count("\n")
        cols_cm = caption.count(",")
        data.tables.append(
            ExtractedTable(
                caption=caption.strip()[:200],
                description=caption.strip()[:100],
                table_number=int(num) if num.isdigit() else 0,
                row_count=max(rows, 1),
                column_count=max(cols_cm + 1, 1),
            )
        )

    numeric_pattern = re.compile(
        r"([\w\s-]+?)\s*(?:=|:|=~|≈)\s*([-+]?\d+\.?\d*(?:e[-+]?\d+)?"
        r"\s*(?:±\s*[-+]?\d+\.?\d*(?:e[-+]?\d+)?)?"
        r"\s*(?:[µμa-z]*[A-Z]?[a-z]*)(?:\s*\(?\d{2,4}\)?)?)",
    )
    for match in numeric_pattern.finditer(text):
        label = match.group(1).strip()
        value = match.group(2).strip()
        if len(label) > 3 and len(label) < 80 and not label.startswith("Fig"):
            data.numerical_results.append({"parameter": label, "value": value})

    data.datasets_mentioned = list(dict.fromkeys(
        d for d in re.findall(
            r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}\s+(?:survey|dataset|catalog|"
            r"database|archive|sample|release|DR\d+))\b",
            text,
        )
    ))

    data.software_mentioned = list(dict.fromkeys(
        s.group(0) for s in SOFTWARE_RE.finditer(text)
    ))

    data.equations_count = len(EQUATION_RE.findall(text))

    data.numerical_results = data.numerical_results[:30]
    data.datasets_mentioned = data.datasets_mentioned[:10]

    return data


def _all_section_text(paper: PaperStructure) -> str:
    parts: list[str] = [paper.abstract]
    for sec in paper.sections:
        if sec.content_preview:
            parts.append(sec.content_preview)
    return "\n\n".join(parts)


def search_literature(
    query: str, source: str = "arxiv", max_results: int = 10
) -> list[SearchHit]:
    """Format a literature-search placeholder for a given source.

    This is a **formatting layer** — it does not perform live queries.
    It resolves the source name, constructs a URL template, and returns
    a structured result that an executor can use to run the actual search.

    Supported *source* values: ``arxiv``, ``pubmed``, ``google_scholar``,
    ``web_of_science``, ``scopus``, ``inspire``, ``ads``.

    Parameters
    ----------
    query:
        Search query string.
    source:
        Database identifier (one of the supported names above).
    max_results:
        Placeholder count of desired hits.

    Returns
    -------
    list[SearchHit]
        A single placeholder :class:`SearchHit` describing the formatted
        search request.  When source is unrecognised an empty list is returned.
    """
    source = source.lower().strip()
    templates: dict[str, tuple[str, str]] = {
        "arxiv": ("https://arxiv.org/search/?query={query}&searchtype=all",
                   "arXiv preprint server"),
        "pubmed": ("https://pubmed.ncbi.nlm.nih.gov/?term={query}",
                   "PubMed biomedical literature database"),
        "google_scholar": ("https://scholar.google.com/scholar?q={query}",
                           "Google Scholar"),
        "web_of_science": ("https://www.webofscience.com/wos/woscc/basic-search",
                           "Web of Science"),
        "scopus": ("https://www.scopus.com/results/results.uri?sort=plf-f&src=s&st1={query}",
                   "Scopus"),
        "inspire": ("https://inspirehep.net/search?p={query}",
                    "INSPIRE-HEP high-energy physics database"),
        "ads": ("https://ui.adsabs.harvard.edu/search/q={query}",
                "NASA Astrophysics Data System"),
    }

    if source not in templates:
        return []

    url_template, description = templates[source]
    url = url_template.replace("{query}", query.replace(" ", "+"))

    return [
        SearchHit(
            title=f"Literature search: {query}",
            authors=[],
            year=0,
            journal=description,
            doi="",
            arxiv_id=url,
            abstract=f"Search on {source} for '{query}' (max {max_results} results).",
            citation_count=0,
            relevance_score=0.0,
        )
    ]


def format_reference(style: str, entry: ReferenceEntry) -> str:
    """Format a :class:`ReferenceEntry` into a citation string.

    Parameters
    ----------
    style:
        Citation style name (``APS``, ``Nature``, ``APA``).
    entry:
        Populated reference entry.

    Returns
    -------
    str
        Formatted citation string, or a plain fallback when the style
        is unknown.
    """
    style_templates = REFERENCE_STYLES.get(style)
    if style_templates is None:
        return f"{'; '.join(entry.authors)}. {entry.title}. {entry.journal} ({entry.year})"

    authors_str = _format_authors(entry.authors, style)

    if entry.arxiv_id:
        template = style_templates.get("arxiv", style_templates["journal_article"])
        return template.format(
            authors=authors_str,
            title=entry.title,
            arxiv_id=entry.arxiv_id,
            category="",
            year=entry.year or "n.d.",
        )

    if entry.journal:
        template = style_templates["journal_article"]
        return template.format(
            authors=authors_str,
            title=entry.title,
            journal=entry.journal,
            volume=entry.volume or "",
            pages=entry.pages or "",
            year=entry.year or "n.d.",
        )

    template = style_templates["book"]
    return template.format(
        authors=authors_str,
        title=entry.title,
        publisher=entry.publisher or "n.p.",
        year=entry.year or "n.d.",
    )


def _format_authors(authors: list[str], style: str) -> str:
    if not authors:
        return "Anonymous"
    if style == "APA" and len(authors) > 7:
        return f"{', '.join(authors[:6])}, ... {authors[-1]}"
    if style == "Nature" and len(authors) > 5:
        return f"{authors[0]} et al."
    if len(authors) > 3:
        return f"{authors[0]} et al."
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}"
    return f"{', '.join(authors[:-1])}, and {authors[-1]}"


def get_journal_metrics(journal_abbr: str) -> dict[str, Any]:
    """Return known metrics for a journal abbreviation.

    Parameters
    ----------
    journal_abbr:
        Journal abbreviation as it appears in ``JOURNALS`` keys
        (e.g. ``PRL``, ``Nature``).

    Returns
    -------
    dict
        ``name``, ``publisher``, ``issn``, ``impact_factor``, or an empty
        dict for unknown journals.
    """
    info = JOURNALS.get(journal_abbr)
    if info is None:
        return {}
    result: dict[str, Any] = dict(info)
    result["impact_factor"] = IMPACT_FACTORS.get(journal_abbr, 0.0)
    return result


def suggest_method_fixes(report: MethodologyReport) -> list[str]:
    """Return a deduplicated, sorted list of methodological improvements."""
    return sorted(set(report.recommendations))


__all__ = [
    "IMPACT_FACTORS",
    "IMRAD_PHRASES",
    "JOURNALS",
    "MINIMUM_SAMPLE_SIZES",
    "REFERENCE_STYLES",
    "ExtractedData",
    "ExtractedFigure",
    "ExtractedTable",
    "MethodologyQuality",
    "MethodologyReport",
    "PaperSection",
    "PaperStructure",
    "ReferenceEntry",
    "ReproducibilityTier",
    "ResearchField",
    "SearchHit",
    "assess_methodology",
    "extract_data",
    "format_reference",
    "get_journal_metrics",
    "identify_paper_structure",
    "search_literature",
    "suggest_method_fixes",
]
