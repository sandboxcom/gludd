# `general_ludd.forensics` — Forensic Investigation Agent Collection

Ansible collection providing agents with forensic investigation capabilities
spanning chain of custody, physical evidence analysis, biological/DNA profiling,
trace evidence examination, and digital photo/video forensics.

## Roles

| Role | Purpose |
|---|---|
| `chain_of_custody_manager` | Create, log transfers, verify chain of custody for evidence items |
| `fingerprint_analyst` | Classify and match fingerprint patterns (latent, patent, plastic) |
| `dna_analyst` | STR analysis, CODIS matching, mitochondrial/Y-chromosome profiling |
| `trace_evidence_examiner` | Analyze fibers, hair, glass, paint, soil, GSR, toolmarks, footwear, tire tracks |
| `photo_forensics_analyst` | EXIF extraction, ELA, clone/splice detection, AI-generated image detection, camera identification |
| `forensics_coordinator` | Orchestrate multi-examiner forensic workflows with evidence routing |

## Knowledge Modules

| Module | Content |
|---|---|
| `chain_of_custody.py` | Evidence handling, packaging, labeling, storage, transport; digital signatures, timestamped transfers, contamination prevention |
| `materials_forensics.py` | Fingerprint classification, DNA STR/CODIS/mtDNA profiling, trace evidence analysis, impression evidence (toolmarks, footwear, tires) |
| `photo_forensics.py` | EXIF extraction, Error Level Analysis (ELA), clone/splice/resample detection, AI-generated image detection (GAN artifacts, diffusion model tells), camera identification via sensor pattern noise |

## Quick start

```yaml
- name: Run full forensic analysis on evidence
  hosts: localhost
  vars:
    case_id: "CASE-2026-001"
    evidence_path: "/evidence/photos/"
    analysis_types:
      - photo_forensics
      - fingerprint
      - trace_evidence
  roles:
    - general_ludd.forensics.forensics_coordinator
```

## Related Collections

| Collection | Shared Domain | Cross-Use |
|---|---|---|
| `general_ludd.physics` | Spectroscopy, chemistry, materials analysis | `spectroscopy.py` (IR/Raman/UV-Vis for material ID), `mass_spectrometry` (compound ID), `organic_chemistry.py` (reaction prediction), `thermodynamics.py` (enthalpy/phase diagrams for trace evidence) |
| `general_ludd.binary_re` | Binary forensics, reverse engineering | `ghidra_analyze` (binary forensic analysis), `deobfuscate` (artifact deobfuscation) |

Use `get_cross_collection_help("forensics")` from `physics.plugins.module_utils.cross_collection` to discover all related roles.

## Dependencies

- Python: PIL/Pillow, numpy, scipy (photo_forensics)
- No external API keys required
- All analysis is local and self-contained
