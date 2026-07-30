---
name: materials-engineer
description: "Use when selecting, comparing, or characterizing engineering materials; computing material properties (strength, corrosion, wear, band gap, dielectric, magnetic, optical); recommending materials against requirements; or reasoning about metals, ceramics, polymers, composites, semiconductors, glasses, nanomaterials, or biomaterials. Trigger keywords: material, alloy, ceramic, polymer, composite, semiconductor, crystal structure, tensile, yield strength, corrosion, galvanic, Hall-Petch, Archard wear, band gap, dielectric, refractive index, thermal shock, biocompatibility, nanoindentation, XRD, SEM, TEM, AFM, DSC, TGA, rule of mixtures, specific strength."
---

# Materials Engineer

A materials engineering expert that resolves material names to property sets,
ranks candidates against requirements, and applies the closed-form relationships
a materials scientist uses daily (Hall-Petch, Archard, rule of mixtures, dielectric
energy density, magnetic energy product, etc.). Backed by the typed service module
at `src/general_ludd/physics/materials_science.py`.

## When to Use

- "Which polymer has the highest tensile strength under $X budget?"
- "Compute the Hall-Petch strength for a 2 µm grain steel with k = 0.5."
- "Compare Ti-6Al-4V vs 7075-T6 aluminum for specific strength."
- "Estimate corrosion rate from weight loss / area / time / density."
- "What band gap corresponds to a 450 nm photon?"

If the query is about a chemical reaction, stoichiometry, or hazard screening,
use the `chemistry-expert` skill instead.

## Available Roles

The service exposes material families, characterization techniques, and property
calculators. There is no separate "role" dispatcher — every call is a direct
function entry point.

| Capability | Entry point |
|---|---|
| Resolve properties | `get_material_properties(material)` |
| Side-by-side compare | `compare_materials(materials_list)` |
| Recommend against spec | `recommend_material(requirements)` |
| Specific strength | `calculate_specific_strength(material_name)` |
| Polymer ranking / filter | `get_polymer_ranking(property_name)`, `filter_polymers(...)` |
| Composite rule of mixtures | `compute_rule_of_mixtures(vf, ef, em, ...)` |
| Corrosion (uniform + galvanic) | `compute_corrosion_rate(...)`, `compute_galvanic_corrosion_risk(...)` |
| Mechanical (Hall-Petch, Archard) | `compute_hall_petch_strength(...)`, `compute_archard_wear_volume(...)` |
| Electronic / optical | `compute_band_gap_from_wavelength(...)`, `compute_dielectric_energy_density(...)`, `compute_conductivity_from_resistivity(...)`, `compute_refractive_index_contrast(...)`, `compute_reflectivity_normal(...)` |
| Magnetic | `compute_max_energy_product(remanence_T, coercivity_kA_m)` |
| Thermal shock | `compute_thermal_shock_resistance(...)` |
| Biocompatibility | `compute_biocompatibility_score(youngs_modulus_GPa, ...)` |
| Nano surface/volume | `compute_surface_to_volume_ratio(...)` |

Controlled vocabularies: `MaterialFamily`, `CharacterizationTechnique`,
`CrystalStructure`, `NanomaterialType`, `MagneticMaterialType`,
`OpticalProperty`, `CompositeType`, `CorrosionType`.

## Safety Boundaries

- All property calculations are closed-form estimates from literature constants —
  never a substitute for certified lab measurements on a specific lot.
- Corrosion and galvanic-risk outputs are screening indicators; a positive galvanic
  risk does not forbid the pairing, it flags it for protective-coating review.
- Biocompatibility scores rank relative tissue-modulus match; they do NOT certify
  any device for clinical use.
- The module performs no network I/O and holds no secrets.

## Usage Examples

```python
from general_ludd.physics.materials_science import (
    recommend_material,
    MaterialRequirement,
    compute_hall_petch_strength,
)

recs = recommend_material(
    MaterialRequirement(min_tensile_strength_mpa=500, max_density_g_cm3=3.0)
)
# -> [{'material': 'titanium_ti6al4v', 'score': 0.92, ...}, ...]

sigma = compute_hall_petch_strength(d_grain_um=2.0, k_hall_petch=0.5, sigma_0=100)
```

## See Also

- `chemistry-expert` — reactions, stoichiometry, hazards, electrochemistry
- `docs/specs/FEATURE_MATERIALS_ENGINEER.md` — capability spec
- `src/general_ludd/physics/analytical_chemistry.py` — mass-spec / chromatography identification
