# OSS Tools Survey — 4 New Expert Collections

**Created:** 2026-07-30
**Purpose:** Per COST-EFFICIENCY rule 10 ("Research existing tools BEFORE
writing new code"), survey mature OSS libraries that should be adopted as
adapters rather than reimplemented. Covers Chemistry, Materials, AI/ML, and
Git Release Captain expert collections.

## TL;DR

- **None** of the domain libraries (RDKit, pymatgen, ASE, pint, numpy/scipy)
  are currently dependencies. The existing `chemistry/core.py` and
  `materials/core.py` reimplement formula parsing, stoichiometry, and
  hazard lookup by hand — this is exactly the "custom code when a mature
  tool exists" anti-pattern the specs warn against.
- **Git Release** needs **zero new dependencies** — `git_automation/` and
  the release-pipeline scripts are already comprehensive; the spec calls
  for adapters, not a new git library.
- **AI/ML** already has langchain/langgraph/huggingface-hub; the only gap
  is a vector index for the retrieval service (use the existing aiosqlite
  + diskcache, or add `chromadb`/`sqlite-vec` if dense retrieval ships).

---

## 1. Chemistry (`general_ludd.chemistry`)

### Current state
- `src/general_ludd/chemistry/core.py` (1145 lines): custom Hill-notation
  formula parser, brute-force reaction balancer, hand-coded hazard registry
  + incompatibility matrix, stoichiometry math. No dependency on any
  chemistry library.
- Spec §7 (CHEM-011/012/013) requires adapters for quantum chemistry,
  molecular simulation, and thermodynamics — currently unbuilt.

### Recommended OSS tools (none currently installed)

| Tool | pip name | License | Covers spec IDs | Why prefer over custom |
|------|----------|---------|-----------------|------------------------|
| **RDKit** | `rdkit` (or `rdkit-pypi`) | BSD-3-Clause | CHEM-002, 005, 010 | Canonical SMILES/InChI, stereochemistry, tautomers, substructure/similarity search, descriptors, fingerprints, conformers. The current `_strip_smiles_to_formula` regex hack would be deleted. |
| **Open Babel** | `openbabel-wheel` | GPLv2 | CHEM-002, 010, 014 | 110+ chemical format conversions (molfile, SDF, CIF), batch file transforms the spec requires. Complements RDKit for rare formats. |
| **cclib** | `cclib` | LGPL/BSD | CHEM-011 | Parses Gaussian/ORCA/QChem/PSI4/Molpro output into typed objects (energies, geometries, frequencies, orbitals). The spec's quantum-adapter contract is a thin wrapper. |
| **ASE** | `ase` | LGPLv2+ | CHEM-012, 013, 011 | Atomistic simulation environment: geometry, force-field wrappers (LAMMPS, GROMACS, Quantum ESPRESSO), calculator abstraction. The MD/free-energy adapter protocol maps directly. |
| **pymatgen** | `pymatgen` | MIT | CHEM-002, 013, 011 | Materials Project API client, phase diagrams, pourbaix diagrams, electronic structure parsers. Shared with Materials expert (§2). |

### Integration path
1. Add `rdkit`, `cclib`, `ase`, `pymatgen`, `openbabel-wheel` to a new
   `chemistry` optional-dependency group in `pyproject.toml` (keeps base
   install lean — the spec says compute capabilities are default-off).
2. Rewrite `chemistry/core.py` formula parsing, identity resolution, and
   reaction balancing to delegate to RDKit, keeping the existing typed
   service API (`resolve_identity`, `analyze_reaction`, etc.) as the
   public surface. The service API is correct; the implementation is the
   adapter seam.
3. Implement CHEM-011/012 adapters as thin wrappers over cclib + ASE
   runner subprocess calls (matching the sandbox contract in §7.1).

---

## 2. Materials Engineering (`general_ludd.materials`)

### Current state
- `src/general_ludd/materials/core.py` (669 lines): hardcoded material
  registry (PA66-GF30, ABS, epoxy, AISI 1045, AA6061-T6), string-based
  units (no conversion service), analytical margin checks only.
- Spec §6 requires typed simulator adapters (CAD/mesh, structural FEA,
  CFD, multiphysics, forming, welding, additive) — none built.
- Spec MATE-DEC-004 requires unit traceability through a "single units
  service" — currently units are bare strings compared with `!=`.

### Recommended OSS tools (none currently installed)

| Tool | pip name | License | Covers spec IDs | Why prefer over custom |
|------|----------|---------|-----------------|------------------------|
| **pint** | `pint` | BSD-3-Clause | MATE-AT-001, DEC-004 | Unit registry with dimensional analysis and uncertainty propagation. The spec mandates "a single units service"; pint is the de-facto Python standard. Replaces every string-unit comparison. |
| **NumPy / SciPy** | `numpy`, `scipy` | BSD | MATE-AT-006 | Analytical hand-calc benchmarks (beam, torsion, buckling, fatigue, tolerance chains). `scipy.optimize` for screening indices. The spec requires independently-reviewed reference formulas — reimplementing these is reinventing the wheel. |
| **Materials Project API** | `mp-api` | MIT | MATE §4.1, 11 | Crystallographic, thermodynamic, mechanical property data via REST. Pairs with `pymatgen`. Replaces the hardcoded `MATERIALS` dict with a live, cited source registry. |
| **pyansys / CalculiX / FEniCS** | various | various | MATE §6, AT-007 | FEA adapters. The spec explicitly says "prefer maintained, validated tools over custom solvers." CalculiX (GPL) or FEniCS (LGPL) as subprocess adapters behind the `SimulationPlan` protocol. Do NOT write a solver. |

### Integration path
1. Add `pint`, `numpy`, `scipy`, `mp-api`, `pymatgen` to a new `materials`
   optional-dependency group.
2. Introduce `src/general_ludd/materials/units.py` (spec §12 file plan)
   wrapping a pint `UnitRegistry`; refactor all property records to carry
   pint `Quantity` values.
3. Convert `MATERIALS` dict into a `source_registry` that queries
   Materials Project + local fixture files; keep the small fixture set
   for offline tests.

---

## 3. AI/ML (`general_ludd.ai_ml`)

### Current state (well-served)
- **Already dependencies:** `langchain-openai`, `langgraph`, `langsmith`,
  `huggingface-hub` (base); `llama-cpp-python`, `vllm` (optional
  `local-inference`).
- `src/general_ludd/ai_ml/router.py`: typed `ExpertRouter`,
  `EvidenceStore` (content-addressed, tenant-isolated), `answer_question`,
  `discover_tools` — already implements AIML-001/002/003/007/018.
- `src/general_ludd/retrieval/`: `searcher.py`, `indexer.py`,
  `agentic_context.py`, `searx_client.py`, `web.py`, `research_index.py`
  — a hybrid retrieval stack is already scaffolded.

### Gap analysis

| Need | Status | Recommendation |
|------|--------|----------------|
| Vector / dense retrieval | **Missing.** Retrieval module is lexical + SearXNG. | Add `sqlite-vec` (MIT, zero-server, fits existing aiosqlite) or `chromadb` (Apache-2.0) only when AIML-006 dense retrieval ships. Defer — BM25 covers phase A. |
| Dataset engineering (AIML-005) | **Missing.** | Add `datasets` (Apache-2.0) + `pyarrow` for versioned dataset manifests. Pairs with `huggingface-hub` already installed. |
| Model format (AIML-008/009) | **Missing.** | Add `safetensors` (Apache-2.0) + `onnx` (Apache-2.0) to the `local-inference` group when adapter serving lands. Defer for now. |
| Speech (AIML-010/011) | **Missing.** | Add `transformers` (Apache-2.0) + `torchaudio` (BSD) or `faster-whisper` (MIT) when phase D ships. |
| Evaluation (AIML-016) | **Partial.** | Use `pytest` + the existing `EvidenceStore` for versioned eval suites; no new framework needed. |

### Integration path
1. **Phase A: no new deps.** The existing router + evidence store +
   retrieval stack satisfy the spec's phase-A deliverables.
2. **Phase B (retrieval):** add `sqlite-vec` as a lightweight vector
   index; wire `retrieval/indexer.py` to emit both lexical and dense
   candidates through the existing `searcher.py` interface.
3. **Phase C-D:** add `datasets`, `safetensors`, speech/vision libs to a
   new `ai_ml` optional group as each phase lands behind its feature flag.

---

## 4. Git Release Captain (`general_ludd.git_release`)

### Current state (comprehensive)
- `src/general_ludd/git_automation/` already has 17 modules:
  `repo.py`, `worktree.py`, `feature_branch.py`, `ship_commit.py`,
  `batch_push.py`, `release_ops.py` (release-cut, release-delete,
  release-recut, verify-readme-status), `verify_remote.py`, `ci_ops.py`,
  `pr_delivery.py`, `issue_ingestor.py`, `git_index.py`, `git_search.py`,
  `git_stats.py`, `locking.py`, `duplicate_targets.py`, `types.py`.
- Release pipeline scripts: `require_ci_green.py`,
  `verify_release_artifact.py`, `verify_release_completeness.py`,
  `check_green_branch_guard.py`, `check_readme_status_current.py`,
  `check_duplicate_targets.py`, `check_tdd_compliance.py`.
- `release_ops.py` wraps git via subprocess with timeout + sanitized env
  + leading-dash rejection (GRC-SEC-002 command allowlist pattern).

### Gap analysis

| Need | Status | Recommendation |
|------|--------|----------------|
| Git object/ref operations | **Covered** by `git_automation/`. | **No new library.** GitPython (BSD) or pygit2 (GPLv2) would duplicate working code and add a heavy native dep. The spec calls for typed adapters, which `release_ops.py` + `types.py` already provide. |
| History forensics / bisect | **Covered** by `git_search.py`, `git_index.py`, `git_stats.py`. | No new dep. |
| Release page / artifact ops | **Covered** by `gh` CLI wrapped in release scripts. | No new dep. |
| SBOM / provenance | **Covered** by `cyclonedx-py` (already in dev deps) + `gh attestation`. | No new dep. |
| Forge adapters (GitHub/GitLab/Forgejo) | **Partial** — GitHub via `gh`. | When GRC-P1 adds non-GitHub forges, add `python-gitlab` (LGPL) or `pyforgejo` per-provider. Defer. |

### Integration path
1. **Zero new dependencies.** The `git_release` collection is a thin
   Ansible role layer over the existing `git_automation/` service.
2. Wrap the existing `release_ops.py`, `verify_remote.py`,
   `ci_ops.py` functions as the GRC-001 typed adapter implementations
   (`RepoEvidence`, `HelperCandidate`, `ReleasePlan`, `ReleaseVerdict`
   contracts in spec §5).
3. If a typed in-memory git object model becomes necessary for
   `history_investigate` (blame/bisect/range-diff graph reasoning),
   evaluate `pygit2` then — but only if the subprocess approach proves
   too slow. It is not blocking phase P1.

---

## Summary: dependency additions by phase

| Collection | Phase A deps | Later phase deps |
|------------|--------------|------------------|
| Chemistry | `rdkit`, `pint` | `cclib`, `ase`, `pymatgen`, `openbabel-wheel` |
| Materials | `pint`, `numpy`, `scipy` | `mp-api`, `pymatgen`, FEA adapter libs |
| AI/ML | *(none — use existing)* | `sqlite-vec`, `datasets`, `safetensors` |
| Git Release | *(none — use existing)* | *(none unless multi-forge)* |

All additions should land in new `[project.optional-dependencies]` groups
(`chemistry`, `materials`, `ai_ml`) so the base install stays lean and
capabilities remain default-off per the specs.
