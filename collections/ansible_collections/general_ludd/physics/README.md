# `general_ludd.physics` -- Physics, Chemistry & Math Agent Collection

Ansible collection providing agents with physics, chemistry, mathematics, and
computational science capabilities.  Covers problem solving, equation rendering,
paper comprehension, and domain-specific analysis.

## Roles

| Role | Purpose |
|---|---|
| `quantum_mechanics` | Solve Schrodinger equation, compute eigenstates, simulate wavefunctions |
| `particle_physics` | Compute cross-sections, decay rates, Feynman diagrams, Standard Model data |
| `spectroscopy` | Simulate IR/Raman/UV-Vis/NMR spectra, peak assignment, coupling constants |
| `thermodynamics` | Compute enthalpies, entropies, free energies, phase diagrams |
| `electrodynamics` | Solve Maxwell's equations, compute fields, waves, potentials |
| `organic_chemistry` | Reaction prediction, retrosynthesis, functional group analysis |
| `spectroscopy_analysis` | Experimental spectrum fitting, baseline correction, peak deconvolution |
| `mass_spectrometry` | Isotope pattern prediction, fragmentation trees, m/z assignment |
| `math_solver` | Symbolic integration, ODE/PDE solving, matrix decomposition, optimization |
| `latex_renderer` | Compile LaTeX to PDF/DVI/SVG, manage BibTeX, render equations |
| `research_paper_expert` | Extract abstract/methods/data, assess methodology, summarize findings |

## Knowledge Modules

| Module | Content |
|---|---|
| `physical_constants.py` | CODATA values, unit conversions, particle properties, spectral lines |
| `chemical_data.py` | Periodic table, bond energies, functional groups, solvent properties |
| `math_identities.py` | Integration tables, series expansions, transform pairs, matrix identities |
| `spectral_libraries.py` | Common IR/Raman/NMR shifts, mass fragmentation patterns, UV-Vis chromophores |
| `research_workflows.py` | arxiv query builder, Semantic Scholar API, CrossRef/PubMed clients |
| `latex_templates.py` | Document classes, common preamble blocks, TikZ/PGFPlots snippets |
| `cross_collection.py` | Cross-collection registry: topic-to-role mapping, dispatch, and discovery |

## Cross-Collection Integration

The `cross_collection.py` module provides a registry and dispatch layer for
cross-collection role discovery. Agents query which collections serve a given topic.

```python
from physics.plugins.module_utils.cross_collection import get_cross_collection_help
help_data = get_cross_collection_help("forensics")
# Returns entries from physics, binary_re related to forensics
```

### Key functions

| Function | Purpose |
|---|---|
| `list_topics()` | All registered cross-collection topic keys |
| `get_cross_collection_help(topic)` | Collections/roles/modules for a topic + related topics |
| `call_collection_role(collection, role, args)` | Validated dispatch to a named collection role |
| `collections_for_topic(topic)` | Unique collection FQ names for a topic |
| `roles_for_collection(collection)` | All registered roles for a collection |

### Registered topic domains

propagation, signal_processing, electromagnetics, cryptography, reverse_engineering,
vulnerability, fuzzing, spectroscopy, mathematics, chemistry, quantum, governance, forensics, networking, computer_science

## Related Collections

| Collection | Shared Domain | Cross-Use |
|---|---|---|
| `general_ludd.radio` | EM propagation, antenna physics | `propagation_models.py` uses dB/log math from `math_identities.py` |
| `general_ludd.binary_re` | Entropy analysis, obfuscation | `entropy_analyzer.py` uses statistical methods from `math_modeler.py` |
| `general_ludd.security` | Post-quantum crypto | `quantum_computer.py` for Shor/Grover algorithm foundations |

## Quick start

```yaml
- name: Solve a quantum mechanics problem
  hosts: localhost
  vars:
    quantum_enabled: true
    quantum_problem: "infinite_square_well"
    quantum_well_width_nm: 1.0
  roles:
    - general_ludd.physics.quantum_mechanics
```

## Dependencies

- Python: numpy, scipy, sympy, matplotlib
- Optional per-role: qutip, rdkit, coolprop, cantera, cvxopt, lmfit, pdfplumber
- System CLI: pdflatex, bibtex, dvisvgm (latex_renderer)
- APIs: arxiv, Semantic Scholar, CrossRef, PubMed (research_paper_expert)

Loose-coupling: if a backend is absent the role returns `skipped` + `missing_tool`.
