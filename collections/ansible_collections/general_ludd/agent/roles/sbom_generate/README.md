# sbom_generate

CycloneDX SBOM generation role for the `general_ludd.agent` collection.

## Description

Produces a CycloneDX SBOM using `syft`. The heavy syft binary is gated behind
`enable_syft: false` with `sbom_output_override` providing a minimal valid
CycloneDX document for molecule testing. Parses the SBOM to extract component
count and top dependencies. **REPORT-ONLY.**

## Variables

| Variable | Default | Description |
|---|---|---|
| `artifact_dir` | `/tmp/gludd-sbom-generate` | Artifact output path |
| `target_path` | `"."` | Path to scan for SBOM |
| `sbom_format` | `cyclonedx-json` | SBOM format |
| `enable_syft` | `false` | Run the real syft tool |
| `sbom_output_override` | minimal CycloneDX JSON | Canned SBOM for testing |
| `enable_git_push` | `false` | Always false — report-only |

## Artifacts

- `<artifact_dir>/sbom.cyclonedx.json` — CycloneDX SBOM
- `<artifact_dir>/sbom_generate.json` — component_count, top_deps, metadata
- `<artifact_dir>/sbom_generate.md` — human-readable report
