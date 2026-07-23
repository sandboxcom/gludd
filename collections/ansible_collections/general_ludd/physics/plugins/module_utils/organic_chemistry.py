"""Organic chemistry knowledge module for the physics collection.

Functional groups, reaction mechanisms, named reactions, and spectroscopy
data for structure determination and reactivity prediction.

Public surface::

    FUNCTIONAL_GROUPS      dict[group_name] -> structural + spectral data
    REACTION_MECHANISMS    dict[mechanism_name] -> kinetic + stereochemical profile
    NAMED_REACTIONS        dict[reaction_name] -> conditions + mechanism
    NMR_SHIFTS             dict[proton_environment] -> chemical shift ranges
    IR_BANDS               dict[bond_type] -> absorption wavenumber ranges
    MS_FRAGMENTATION       dict[functional_group] -> diagnostic fragments

    identify_functional_groups(formula)      -> list[dict]
    predict_reaction(substrate, reagent)     -> dict | None
    look_up_named_reaction(name)             -> dict | None
"""

from __future__ import annotations

from typing import Any


FUNCTIONAL_GROUPS: dict[str, dict[str, Any]] = {
    "alkane": {
        "formula_pattern": "C_nH_(2n+2); single C-C sigma bonds; sp3 carbon",
        "functional_atom": "C (sp3 hybridised)",
        "polarity": "non-polar; only weak van der Waals forces",
        "reactivity": "Generally unreactive; undergo radical halogenation and combustion. No functional group to attack nucleophilically or electrophilically. Free-radical chain mechanism with halogens (Br2/Cl2 + light). C-H bond homolysis.",
        "ir_signature": "C-H stretch 2850-2960 cm⁻¹ (strong); C-C stretch <1400 cm⁻¹",
        "nmr_proton_shift": [0.7, 1.7],
        "examples": ["methane CH4", "ethane C2H6", "propane C3H8", "cyclohexane C6H12"],
    },
    "alkene": {
        "formula_pattern": "C_nH_2n; C=C double bond; sp2 carbon",
        "functional_atom": "C=C (sp2 hybridised)",
        "polarity": "slightly polar; pi electrons are polarisable",
        "reactivity": "Electrophilic addition across the double bond (HX, X2, H2O/H+, BH3 then H2O2/OH-). Markovnikov regioselectivity. Catalytic hydrogenation (H2/Pd). The pi bond is the HOMO; attacks electrophiles.",
        "ir_signature": "=C-H stretch 3020-3100 cm⁻¹; C=C stretch 1620-1680 cm⁻¹ (variable intensity); =C-H out-of-plane bend 650-1000 cm⁻¹",
        "nmr_proton_shift": [4.6, 6.0],
        "examples": ["ethene C2H4", "propene C3H6", "1-butene C4H8"],
    },
    "alkyne": {
        "formula_pattern": "C_nH_(2n-2); C#C triple bond; sp carbon",
        "functional_atom": "C#C (sp hybridised)",
        "polarity": "slightly polar; terminal alkynes are weakly acidic (pKa ~ 25)",
        "reactivity": "Electrophilic addition (slower than alkenes). Terminal alkynes form acetylide anions with strong base (NaNH2) — nucleophilic. Catalytic hydrogenation to alkane or cis-alkene (Lindlar catalyst). Hydroboration-oxidation yields carbonyl.",
        "ir_signature": "≡C-H stretch 3260-3330 cm⁻¹ (sharp, terminal only); C≡C stretch 2100-2260 cm⁻¹ (weak or absent if internal)",
        "nmr_proton_shift": [2.0, 3.0],
        "examples": ["ethyne C2H2", "propyne C3H4", "1-butyne C4H6"],
    },
    "aromatic": {
        "formula_pattern": "cyclic conjugated pi system; Huckel 4n+2 rule; planar",
        "functional_atom": "C (sp2 hybridised in ring)",
        "polarity": "non-polar; pi cloud above and below ring plane",
        "reactivity": "Electrophilic aromatic substitution (SEAr): nitration (HNO3/H2SO4), halogenation (X2/FeX3), Friedel-Crafts alkylation/acylation, sulfonation. Ring is electron-rich; attacked by electrophiles. Does NOT undergo addition (aromatic stabilisation energy ~150 kJ/mol).",
        "ir_signature": "C-H stretch 3000-3100 cm⁻¹; C=C aromatic stretch 1450-1600 cm⁻¹ (2-3 bands); =C-H out-of-plane bend diagnostic for substitution pattern",
        "nmr_proton_shift": [6.5, 8.5],
        "examples": ["benzene C6H6", "toluene C7H8", "naphthalene C10H8", "phenol C6H5OH"],
    },
    "alcohol": {
        "formula_pattern": "R-OH; hydroxyl group bonded to sp3 carbon",
        "functional_atom": "O (sp3)",
        "polarity": "polar (protic); hydrogen bonding possible",
        "reactivity": "Nucleophilic substitution (SN1 or SN2 after protonation or conversion to tosylate/halide). Dehydration to alkene (H2SO4/heat, E1 mechanism). Oxidation: primary → aldehyde → carboxylic acid (PCC stops at aldehyde; CrO3 goes to acid); secondary → ketone; tertiary → no reaction. Deprotonation with Na or NaH to alkoxide.",
        "ir_signature": "O-H stretch 3200-3600 cm⁻¹ (broad, strong, H-bonded); C-O stretch 1050-1250 cm⁻¹",
        "nmr_proton_shift": [1.0, 5.5],
        "examples": ["methanol CH3OH", "ethanol C2H5OH", "isopropanol (CH3)2CHOH", "tert-butanol (CH3)3COH"],
    },
    "ether": {
        "formula_pattern": "R-O-R'; oxygen bonded to two carbon groups",
        "functional_atom": "O (sp3)",
        "polarity": "slightly polar; weak H-bond acceptor only",
        "reactivity": "Relatively unreactive. Cleavage by strong acids (HI or HBr) via SN1 or SN2. Form peroxides on standing in air (safety hazard). Williamson ether synthesis: alkoxide + alkyl halide → ether (SN2). Crown ethers complex metal cations.",
        "ir_signature": "C-O-C asymmetric stretch 1050-1150 cm⁻¹ (strong); no O-H band",
        "nmr_proton_shift": [3.3, 4.0],
        "examples": ["diethyl ether (C2H5)2O", "tetrahydrofuran (THF) C4H8O", "1,4-dioxane C4H8O2"],
    },
    "aldehyde": {
        "formula_pattern": "R-CHO; carbonyl group with at least one H attached",
        "functional_atom": "C=O (sp2)",
        "polarity": "polar; carbonyl is H-bond acceptor",
        "reactivity": "Nucleophilic addition at carbonyl carbon. Oxidised to carboxylic acid (KMnO4, CrO3, Tollens reagent). Reduced to primary alcohol (NaBH4, LiAlH4). Acetal formation with alcohols (reversible, acid-catalysed). Aldol condensation (enolate + aldehyde → β-hydroxy aldehyde). Grignard addition yields secondary alcohol.",
        "ir_signature": "C=O stretch 1720-1740 cm⁻¹ (strong); aldehyde C-H stretch 2720-2820 cm⁻¹ (characteristic doublet Fermi resonance)",
        "nmr_proton_shift": [9.0, 10.0],
        "examples": ["formaldehyde HCHO", "acetaldehyde CH3CHO", "benzaldehyde C6H5CHO"],
    },
    "ketone": {
        "formula_pattern": "R-CO-R'; carbonyl with two carbon groups",
        "functional_atom": "C=O (sp2)",
        "polarity": "polar; carbonyl is H-bond acceptor",
        "reactivity": "Nucleophilic addition at carbonyl carbon. Reduced to secondary alcohol (NaBH4, LiAlH4). Ketal/acetal formation. No oxidation (no alpha-H on carbonyl carbon to lose). Enolate chemistry: alpha-halogenation, aldol condensation, alkylation via enolate. Grignard addition yields tertiary alcohol. Wittig reaction converts to alkene. Less reactive than aldehydes (steric + inductive effects).",
        "ir_signature": "C=O stretch 1705-1725 cm⁻¹ (strong); no aldehyde C-H bands",
        "nmr_proton_shift": [2.0, 2.6],
        "examples": ["acetone CH3COCH3", "2-butanone CH3COC2H5", "acetophenone C6H5COCH3", "cyclohexanone C6H10O"],
    },
    "carboxylic_acid": {
        "formula_pattern": "R-COOH; carboxyl group",
        "functional_atom": "C=O + OH (sp2 carbon)",
        "polarity": "highly polar (protic); strong H-bond donor and acceptor; form dimers",
        "reactivity": "Acidic (pKa ~ 4-5). Deprotonated by NaOH/NaHCO3 to carboxylate. Nucleophilic acyl substitution: acid chloride formation (SOCl2 → RCOCl), esterification (ROH + H+ → RCOOR'), amide formation via acid chloride. Reduced to primary alcohol (LiAlH4, NOT NaBH4). Decarboxylation of beta-keto acids.",
        "ir_signature": "O-H stretch 2500-3300 cm⁻¹ (very broad, H-bonded dimer); C=O stretch 1700-1725 cm⁻¹ (strong); C-O stretch 1210-1320 cm⁻¹",
        "nmr_proton_shift": [10.0, 13.0],
        "examples": ["formic acid HCOOH", "acetic acid CH3COOH", "benzoic acid C6H5COOH"],
    },
    "amine": {
        "formula_pattern": "R-NH2, R2NH, or R3N; nitrogen with lone pair",
        "functional_atom": "N (sp3 hybridised)",
        "polarity": "polar; H-bond donor (1°/2°) and acceptor",
        "reactivity": "Basic (pKa of conjugate acid ~ 9-11). Nucleophile in SN2 reactions (alkylation → secondary/tertiary amine). Acylation to amide (acid chloride + amine). Diazotisation of primary aromatic amines (NaNO2/HCl → diazonium salt). Hoffmann elimination: exhaustive methylation + Ag2O/heat → least substituted alkene. Gabriel synthesis of primary amines.",
        "ir_signature": "N-H stretch 3300-3500 cm⁻¹ (1°: two peaks; 2°: one peak); N-H bend 1550-1650 cm⁻¹; C-N stretch 1020-1250 cm⁻¹",
        "nmr_proton_shift": [1.0, 5.0],
        "examples": ["methylamine CH3NH2", "dimethylamine (CH3)2NH", "aniline C6H5NH2", "pyridine C5H5N"],
    },
    "amide": {
        "formula_pattern": "R-CO-NR'R''; carbonyl adjacent to nitrogen",
        "functional_atom": "C=O + N (sp2 carbon; nitrogen has partial double bond character)",
        "polarity": "polar; strong H-bond donor (1°/2°) and acceptor; resonance-stabilised",
        "reactivity": "Much less reactive than other carboxylic acid derivatives (resonance stabilisation). Hydrolysis requires strong acid or base + heat. Reduced to amine (LiAlH4). Dehydration to nitrile (P2O5 or SOCl2/heat). Hofmann rearrangement: primary amide + Br2/NaOH → amine (one carbon less). Peptide bond is an amide.",
        "ir_signature": "N-H stretch 3150-3500 cm⁻¹ (1°: two peaks); C=O stretch 1630-1690 cm⁻¹ (Amide I band); N-H bend 1500-1560 cm⁻¹ (Amide II band)",
        "nmr_proton_shift": [5.0, 8.5],
        "examples": ["acetamide CH3CONH2", "N,N-dimethylformamide (DMF) HCON(CH3)2", "urea (NH2)2CO"],
    },
}


REACTION_MECHANISMS: dict[str, dict[str, Any]] = {
    "SN1": {
        "substrate_type": "tertiary alkyl halide, allylic, benzylic; stable carbocation",
        "rate_determining_step": "unimolecular; leaving group departure forms carbocation; rate = k[substrate]",
        "stereochemistry": "racemisation (planar carbocation attacked from either face); some net inversion from ion-pair effects",
        "typical_solvent": "polar protic (water, alcohols, carboxylic acids) — stabilises carbocation and leaving group",
        "rate_law": "Rate = k[R-X]; first-order",
        "leaving_group": "good leaving group required (I-, Br-, Cl-, H2O, OTs); weak base",
        "nucleophile": "weak nucleophile sufficient; often the solvent itself (solvolysis)",
        "rearrangements": "carbocation rearrangements possible (hydride/alkyl shift to more stable cation)",
    },
    "SN2": {
        "substrate_type": "methyl > primary > secondary alkyl halide; tertiary unreactive (steric hindrance)",
        "rate_determining_step": "bimolecular; concerted backside attack; rate = k[substrate][nucleophile]",
        "stereochemistry": "Walden inversion; stereospecific (R → S or S → R); backside attack inverts configuration",
        "typical_solvent": "polar aprotic (DMSO, DMF, acetone, acetonitrile) — solvates cation, leaves nucleophile unsolvated and reactive",
        "rate_law": "Rate = k[R-X][Nu:-]; second-order",
        "leaving_group": "good leaving group required",
        "nucleophile": "strong nucleophile required; negatively charged or highly polarisable",
        "rearrangements": "no rearrangements (concerted mechanism, no carbocation intermediate)",
    },
    "E1": {
        "substrate_type": "tertiary > secondary alkyl halide; substrate that forms stable carbocation",
        "rate_determining_step": "unimolecular; leaving group departure forms carbocation; rate = k[substrate]",
        "stereochemistry": "E and Z products possible; Zaitsev rule: more substituted alkene is major product",
        "typical_solvent": "polar protic; heat favours elimination over substitution",
        "rate_law": "Rate = k[R-X]; first-order",
        "leaving_group": "good leaving group required",
        "base": "weak base sufficient; often the solvent; competes with SN1",
        "regioselectivity": "Zaitsev (more substituted alkene) favoured; conjugation stabilises",
    },
    "E2": {
        "substrate_type": "primary, secondary, tertiary alkyl halide; requires beta-hydrogen",
        "rate_determining_step": "bimolecular; concerted antiperiplanar elimination; rate = k[substrate][base]",
        "stereochemistry": "antiperiplanar requirement; H and leaving group must be coplanar and opposite sides; stereospecific",
        "typical_solvent": "polar aprotic or protic depending on base; heat favours elimination",
        "rate_law": "Rate = k[R-X][Base]; second-order",
        "leaving_group": "good leaving group required for concerted mechanism",
        "base": "strong bulky base (t-BuOK, LDA, DBU) favours E2 over SN2; Hoffmann rule (less substituted alkene) with bulky base",
        "regioselectivity": "Zaitsev with small base; Hoffmann with bulky base (t-BuOK)",
    },
    "electrophilic_addition": {
        "substrate_type": "alkene or alkyne; pi bond as electron source",
        "rate_determining_step": "electrophile attack on pi bond forms carbocation (or cyclic intermediate); then nucleophile captures",
        "stereochemistry": "anti addition for halogens (X2) — bromonium/chloronium ion intermediate; syn addition for H2/catalyst; Markovnikov for HX addition",
        "typical_solvent": "inert (CCl4, CH2Cl2) or the reagent as solvent; water for hydration",
        "rate_law": "Rate = k[alkene][electrophile]; second-order for rate-determining step",
        "regioselectivity": "Markovnikov: H adds to less substituted carbon (more stable carbocation); anti-Markovnikov with BH3/H2O2/OH-",
        "examples": ["HBr addition", "Br2 addition", "H2O/H+ hydration", "Hg(OAc)2/H2O oxymercuration"],
    },
    "nucleophilic_addition": {
        "substrate_type": "carbonyl compounds (aldehydes, ketones); polarised C=O bond",
        "rate_determining_step": "nucleophile attack on electrophilic carbonyl carbon; tetrahedral intermediate formation",
        "stereochemistry": "racemisation if prochiral carbonyl; Cram's rule for chiral alpha-carbon; Felkin-Anh for cyclic",
        "typical_solvent": "depends on nucleophile; anhydrous for Grignard/organolithium; protic OK for NaBH4",
        "rate_law": "Rate = k[carbonyl][nucleophile]; acid-catalysed variants possible",
        "nucleophiles": "Grignard RMgX, organolithium RLi, NaBH4, LiAlH4, cyanide CN-, acetylide RC≡C-, enolates, H2O, ROH, amines",
        "examples": ["Grignard + aldehyde → 2° alcohol", "NaBH4 reduction of ketone", "cyanohydrin formation"],
    },
    "elimination": {
        "substrate_type": "alkyl halides, alcohols (dehydration), tosylates; beta-hydrogen required",
        "rate_determining_step": "E1: carbocation formation (rate = k[substrate]); E2: concerted proton abstraction + leaving group departure",
        "stereochemistry": "E1cb (carbanion): syn or anti; E2: antiperiplanar; Hofmann elimination: syn elimination of quaternary ammonium hydroxide",
        "typical_solvent": "polar protic for E1; polar aprotic for E2; high temperature favours elimination over substitution",
        "rate_law": "E1: Rate = k[substrate]; E2: Rate = k[substrate][base]",
        "competing_reactions": "competes with substitution (SN1 vs E1, SN2 vs E2); high temp, strong bulky base, and tertiary substrate favour elimination",
    },
    "pericyclic": {
        "substrate_type": "conjugated pi systems; dienes, trienes, enones, allyl vinyl ethers",
        "rate_determining_step": "concerted bond reorganisation through cyclic transition state; no ionic or radical intermediates",
        "stereochemistry": "stereospecific and stereoselective governed by orbital symmetry (Woodward-Hoffmann rules); Diels-Alder: suprafacial with respect to both components; electrocyclic: conrotatory vs disrotatory depends on thermal/photochemical conditions",
        "typical_solvent": "no solvent effect on rate (no charge separation in transition state); non-polar solvents often used",
        "rate_law": "Rate = k[substrate] (unimolecular) or Rate = k[diene][dienophile] (bimolecular)",
        "subtypes": ["Diels-Alder [4+2] cycloaddition", "electrocyclic ring opening/closing", "sigmatropic rearrangement (Cope, Claisen)", "ene reaction", "cheletropic reactions"],
        "orbital_control": "HOMO-LUMO interaction controls; thermal allowed if HOMO(diene)-LUMO(dienophile) symmetry match; photochemical reverses requirements",
    },
}


NAMED_REACTIONS: dict[str, dict[str, Any]] = {
    "Diels-Alder": {
        "reactants": "conjugated diene (4 pi electrons) + dienophile (2 pi electrons, often substituted with electron-withdrawing group)",
        "catalyst_or_reagent": "none required; Lewis acid (AlCl3, BF3, ZnCl2) accelerates by lowering dienophile LUMO; heat for sluggish reactions",
        "mechanism_type": "pericyclic [4+2] cycloaddition; concerted via cyclic transition state; suprafacial with respect to both components",
        "products": "cyclohexene derivative; endo product kinetically favoured (secondary orbital overlap); stereospecific retention of dienophile geometry",
        "typical_conditions": "thermal (80-200°C in sealed tube for unreactive dienophiles); room temperature for electron-deficient dienophiles; high pressure accelerates",
        "regioselectivity": "ortho/para rule: electron-donating group on diene C1 + electron-withdrawing on dienophile → ortho or para in product",
    },
    "Grignard": {
        "reactants": "alkyl/aryl/vinyl halide (RX) + magnesium metal; forms organomagnesium reagent RMgX",
        "catalyst_or_reagent": "Mg turnings in anhydrous ether (Et2O) or THF; trace I2 or 1,2-dibromoethane to initiate; must be anhydrous",
        "mechanism_type": "nucleophilic addition (to carbonyls, CO2, epoxides, nitriles); nucleophilic substitution (with alkyl halides — poor); deprotonation of acidic protons",
        "products": "with formaldehyde → 1° alcohol; with aldehyde → 2° alcohol; with ketone → 3° alcohol; with CO2 → carboxylic acid; with ester → 3° alcohol (2 equivalents); with nitrile → ketone (after hydrolysis)",
        "typical_conditions": "anhydrous; inert atmosphere (N2 or Ar); 0°C to reflux; then aqueous NH4Cl workup; cannot use protic solvents (destroy Grignard reagent)",
        "limitations": "cannot prepare Grignard from substrates with acidic protons (OH, NH, SH, COOH); carbonyl compounds with enolisable protons undergo enolisation; sterically hindered halides react slowly",
    },
    "Wittig": {
        "reactants": "aldehyde or ketone + phosphonium ylide (prepared from alkyl halide + PPh3 + strong base)",
        "catalyst_or_reagent": "PPh3 + alkyl halide → phosphonium salt; then strong base (n-BuLi, NaH, t-BuOK) → ylide; stabilised ylides with EWG need milder base (NaOH, Na2CO3)",
        "mechanism_type": "nucleophilic addition of ylide carbanion to carbonyl → betaine → oxaphosphetane → cycloreversion to alkene + Ph3P=O",
        "products": "alkene (C=C); driving force is formation of very strong P=O bond (~575 kJ/mol); Z-alkene from unstabilised ylide (kinetic control); E-alkene from stabilised ylide (thermodynamic control)",
        "typical_conditions": "THF or Et2O, -78°C to RT under N2; stabilised ylides can use refluxing toluene or EtOH; stereochemistry controlled by ylide type",
    },
    "Friedel-Crafts": {
        "reactants": "aromatic compound + alkyl halide (alkylation) or acyl halide/anhydride (acylation)",
        "catalyst_or_reagent": "Lewis acid: AlCl3 (most common), FeCl3, BF3, HF; stoichiometric (complexes with product); catalytic for acylation with anhydrides",
        "mechanism_type": "electrophilic aromatic substitution (SEAr); Lewis acid generates electrophile (carbocation from RX + AlCl3; acylium ion from RCOCl + AlCl3)",
        "products": "alkylation: alkylbenzene; acylation: aryl ketone; acylation preferred — no polyalkylation (deactivating product), no rearrangements",
        "typical_conditions": "anhydrous; AlCl3 in CH2Cl2 or CS2; 0°C to reflux; acylation: 1.1 eq AlCl3; alkylation: frequently polyalkylation and carbocation rearrangements",
        "limitations": "ring deactivated by EWG (NO2, CN, SO3H, COR, COOR) do not react; aniline and phenols complex with AlCl3; alkylation often messy (polyalkylation, rearrangement)",
    },
    "Suzuki Coupling": {
        "reactants": "aryl/vinyl halide or triflate + aryl/vinyl boronic acid or ester",
        "catalyst_or_reagent": "Pd(0) catalyst (Pd(PPh3)4, Pd2(dba)3 + ligand); base (Na2CO3, K3PO4, Ba(OH)2, KF) in aqueous/organic mixture",
        "mechanism_type": "palladium-catalysed cross-coupling; catalytic cycle: oxidative addition → transmetallation → reductive elimination; Pd(0) → Pd(II) → Pd(0)",
        "products": "biaryl or conjugated diene; C-C bond formed between sp2 carbons; stereospecific retention of alkene geometry",
        "typical_conditions": "Pd(PPh3)4 1-5 mol%; Na2CO3 aq; toluene/EtOH/H2O or DME/H2O; 80-100°C, 2-24h under N2; microwave accelerates; base activates boronic acid",
        "advantages": "boronic acids are air-stable, low toxicity, tolerant of many functional groups; wide substrate scope; commercially available building blocks",
    },
}


NMR_SHIFTS: dict[str, dict[str, Any]] = {
    "alkane": {"proton_shift_range": [0.7, 1.7], "description": "methylene and methyl protons; shielded by electron-rich environment", "splitting": "standard n+1 rule"},
    "alkene": {"proton_shift_range": [4.6, 6.0], "description": "vinylic protons; deshielded by magnetic anisotropy of pi bond", "splitting": "complex coupling patterns; geminal (0-3 Hz), cis (6-12 Hz), trans (12-18 Hz)"},
    "alkyne": {"proton_shift_range": [2.0, 3.0], "description": "terminal alkyne proton; shielded by diamagnetic anisotropy cone of triple bond", "splitting": "small long-range coupling (0-3 Hz)"},
    "aromatic": {"proton_shift_range": [6.5, 8.5], "description": "ring protons; deshielded by ring current (diamagnetic anisotropy)", "splitting": "ortho (6-9 Hz), meta (1-3 Hz), para (0-1 Hz)"},
    "alcohol": {"proton_shift_range": [1.0, 5.5], "description": "variable; concentration, temperature, and solvent dependent (H-bonding)", "splitting": "OH proton often broad singlet; exchangeable with D2O"},
    "ether": {"proton_shift_range": [3.3, 4.0], "description": "alpha-protons (CH-O); deshielded by electronegative oxygen", "splitting": "standard n+1"},
    "aldehyde": {"proton_shift_range": [9.0, 10.0], "description": "aldehyde proton; highly deshielded (carbonyl + magnetic anisotropy)", "splitting": "typically singlet or doublet (J ~ 1-3 Hz)"},
    "ketone": {"proton_shift_range": [2.0, 2.6], "description": "alpha-protons (CH-C=O); deshielded by carbonyl", "splitting": "standard n+1; alpha-protons are acidic (pKa ~ 20)"},
    "carboxylic_acid": {"proton_shift_range": [10.0, 13.0], "description": "acidic proton; highly deshielded; H-bonded dimer; variable", "splitting": "broad singlet; exchangeable"},
    "amine": {"proton_shift_range": [1.0, 5.0], "description": "N-H proton; variable; concentration dependent; exchangeable", "splitting": "broad; often broad singlet for aliphatic amines"},
    "amide": {"proton_shift_range": [5.0, 8.5], "description": "NH proton; deshielded by adjacent carbonyl; H-bonding shifts downfield", "splitting": "broad; often two peaks for primary amide (rotamers)"},
    "benzyl": {"proton_shift_range": [2.2, 3.0], "description": "benzylic protons (Ar-CH-); deshielded by ring current", "splitting": "standard n+1"},
    "allyl": {"proton_shift_range": [1.7, 2.5], "description": "allylic protons (C=C-CH-); slightly deshielded by pi system", "splitting": "allylic coupling (0-3 Hz) often observed"},
}


IR_BANDS: dict[str, dict[str, Any]] = {
    "hydroxyl": {"wavenumber_range_cm": [3200, 3600], "description": "O-H stretch; broad in alcohols due to H-bonding; sharp in free OH (dilute, non-polar solvent); carboxylic acids: very broad 2500-3300", "intensity": "strong, broad"},
    "amine_nh": {"wavenumber_range_cm": [3300, 3500], "description": "N-H stretch; primary: two peaks (asymmetric + symmetric); secondary: one peak; tertiary: no N-H stretch", "intensity": "medium"},
    "alkyne_ch": {"wavenumber_range_cm": [3260, 3330], "description": "terminal alkyne ≡C-H stretch; sharp; internal alkynes absent", "intensity": "strong, sharp"},
    "alkene_aromatic_ch": {"wavenumber_range_cm": [3000, 3100], "description": "sp2 C-H stretch; above 3000 cm⁻¹ distinguishes from alkane sp3 C-H", "intensity": "weak to medium"},
    "alkane_ch": {"wavenumber_range_cm": [2850, 2960], "description": "sp3 C-H stretch; below 3000 cm⁻¹; symmetric and asymmetric CH2/CH3 stretches", "intensity": "strong"},
    "aldehyde_ch": {"wavenumber_range_cm": [2720, 2820], "description": "aldehyde C-H stretch; characteristic Fermi doublet; diagnostic for aldehyde", "intensity": "medium"},
    "carbonyl": {"wavenumber_range_cm": [1630, 1820], "description": "C=O stretch; strongest IR band; exact position diagnostic of carbonyl type: acid chloride ~1800, ester ~1735, aldehyde ~1725, ketone ~1715, carboxylic acid ~1710 (dimer), amide ~1650, conjugated lowers by ~30 cm⁻¹", "intensity": "very strong"},
    "alkene_cc": {"wavenumber_range_cm": [1620, 1680], "description": "C=C stretch; variable intensity (zero if symmetric); conjugation lowers and increases intensity", "intensity": "variable"},
    "aromatic_cc": {"wavenumber_range_cm": [1450, 1600], "description": "aromatic C=C ring stretches; typically 2-3 bands; ~1600, ~1580, ~1500, ~1450 cm⁻¹", "intensity": "medium to strong"},
    "nitro": {"wavenumber_range_cm": [1515, 1560], "description": "N=O asymmetric stretch (stronger, higher) and symmetric stretch (1300-1370)", "intensity": "very strong"},
    "nitrile": {"wavenumber_range_cm": [2210, 2260], "description": "C≡N stretch; sharp; conjugation lowers frequency", "intensity": "medium, sharp"},
}


MS_FRAGMENTATION: dict[str, dict[str, Any]] = {
    "alkane": {
        "common_losses": ["15 (CH3)", "29 (C2H5)", "43 (C3H7)", "57 (C4H9)"],
        "diagnostic_ions": ["clusters of CnH(2n+1)+ ions spaced by 14 Da (CH2); M+ often weak; highest peak often C3H7+ or C4H9+"],
        "mclafferty": False,
    },
    "alcohol": {
        "common_losses": ["18 (H2O, dehydration)", "15 (CH3)"],
        "diagnostic_ions": ["M-18 (loss of water, especially for 1°/2°); alpha-cleavage produces oxonium ion [CH2=OH]+ m/z 31 for primary alcohols; 45, 59, 73 for higher; M+ often absent for 2°/3°"],
        "mclafferty": False,
    },
    "carbonyl": {
        "common_losses": ["28 (CO)", "alkyl groups from alpha-cleavage"],
        "diagnostic_ions": ["alpha-cleavage: acylium ion [RCO]+; aldehydes: M-1 (loss of H·) characteristic; ketones: two possible acylium ions; aromatic carbonyls: [ArCO]+ abundant"],
        "mclafferty": True,
    },
    "ester": {
        "common_losses": ["31 (OCH3, methyl ester)", "45 (OC2H5, ethyl ester)", "alkoxy radical from alpha-cleavage at alkoxy side"],
        "diagnostic_ions": ["alpha-cleavage at acyl side: [RCO]+; alpha-cleavage at alkoxy side: [COOR']+; McLafferty rearrangement strong if gamma-hydrogen present"],
        "mclafferty": True,
    },
    "amine": {
        "common_losses": ["17 (NH3 rare)", "alkyl groups from alpha-cleavage"],
        "diagnostic_ions": ["alpha-cleavage: iminium ion [CH2=NR2]+; nitrogen rule: odd M+ for odd number of nitrogens; base peak often from alpha-cleavage at nitrogen"],
        "mclafferty": False,
    },
    "aromatic": {
        "common_losses": ["15 (CH3, from methylbenzene)", "26 (C2H2)", "CO (from phenols)", "substituent loss"],
        "diagnostic_ions": ["tropylium ion m/z 91 (C7H7+, benzyl cleavage + rearrangement); phenyl cation m/z 77 (C6H5+); M+ typically strong (aromatic stabilisation); retro-Diels-Alder in tetralins"],
        "mclafferty": False,
    },
}


def identify_functional_groups(formula: str) -> list[dict[str, Any]]:
    """Identify candidate functional groups from a molecular formula string.

    Performs simple keyword-/substructure-based detection. Returns a list of
    dicts with keys ``group`` (the matching functional-group name) and
    ``confidence`` ("high", "medium", or "low").
    """
    if not formula or not isinstance(formula, str):
        return []

    formula_upper = formula.upper()
    results: list[dict[str, Any]] = []

    found_carbonyl_group = False

    if "COOH" in formula_upper or "CO2H" in formula_upper:
        results.append({"group": "carboxylic_acid", "confidence": "high"})
        found_carbonyl_group = True
    elif "CHO" in formula_upper:
        results.append({"group": "aldehyde", "confidence": "medium"})
        results.append({"group": "carbonyl", "confidence": "high"})
        found_carbonyl_group = True

    if "OH" in formula_upper and "COOH" not in formula_upper:
        results.append({"group": "alcohol", "confidence": "high"})

    if not found_carbonyl_group:
        _c_count = formula_upper.count("C")
        _o_count = formula_upper.count("O")
        _h_count = formula_upper.count("H")
        _has_co_substr = "CO" in formula_upper
        if _has_co_substr or (_c_count == 1 and _o_count == 1 and _h_count >= 1):
            if _o_count == 1 and _c_count == 1 and not any(r["group"] in ("alcohol",) for r in results):
                results.append({"group": "aldehyde", "confidence": "medium"})
                found_carbonyl_group = True
            elif _has_co_substr:
                results.append({"group": "ketone", "confidence": "medium"})
                found_carbonyl_group = True

    if "NH2" in formula_upper or "NH" in formula_upper:
        if "CO" in formula_upper:
            results.append({"group": "amide", "confidence": "medium"})
        else:
            results.append({"group": "amine", "confidence": "high"})

    if "O" in formula_upper and "OH" not in formula_upper and "CO" not in formula_upper:
        results.append({"group": "ether", "confidence": "low"})

    if "C6H6" in formula_upper or "BENZENE" in formula_upper or (formula_upper.startswith("C6H") and len(formula_upper) <= 10):
        results.append({"group": "aromatic", "confidence": "high"})
    elif "C6" in formula_upper and "H" in formula_upper:
        results.append({"group": "aromatic", "confidence": "low"})

    if "C2H2" == formula_upper:
        results.append({"group": "alkyne", "confidence": "medium"})
    elif "C2H4" == formula_upper:
        results.append({"group": "alkene", "confidence": "medium"})
    elif "C2H" == formula_upper[:3]:
        results.append({"group": "alkyne", "confidence": "low"})
    elif "CH4" == formula_upper:
        results.append({"group": "alkane", "confidence": "high"})

    HC_atoms = ""
    if "C" in formula_upper or "H" in formula_upper:
        for ch in formula_upper:
            if ch in "CH0123456789":
                HC_atoms += ch

    has_c = "C" in formula_upper
    has_h = "H" in formula_upper
    has_no_heteroatoms = True
    for ch in formula_upper:
        if ch in "ONPSFClBrI":
            has_no_heteroatoms = False
            break

    if has_c and has_h and has_no_heteroatoms:
        already = {r["group"] for r in results}
        if not already & {"alkane", "alkene", "alkyne", "aromatic"}:
            results.append({"group": "alkane", "confidence": "low"})

    return results


_REACTION_MAP: dict[str, dict[str, str]] = {
    "alkene,HCl":  {"mechanism": "electrophilic_addition", "product_type": "alkyl chloride; Markovnikov addition"},
    "alkene,HBr":  {"mechanism": "electrophilic_addition", "product_type": "alkyl bromide; Markovnikov addition"},
    "alkene,Br2":  {"mechanism": "electrophilic_addition", "product_type": "vicinal dibromide; anti addition"},
    "alkene,H2O":  {"mechanism": "electrophilic_addition", "product_type": "alcohol; Markovnikov; acid-catalysed hydration"},
    "alcohol,HBr": {"mechanism": "SN1", "product_type": "alkyl bromide; via protonation then substitution"},
    "alcohol,HCl": {"mechanism": "SN1", "product_type": "alkyl chloride; ZnCl2 catalyst for 1° alcohols"},
    "alcohol,SOCl2": {"mechanism": "SN2", "product_type": "alkyl chloride; inversion of configuration; SO2 + HCl byproducts"},
    "ketone,NaBH4": {"mechanism": "nucleophilic_addition", "product_type": "secondary alcohol; hydride addition to carbonyl"},
    "aldehyde,NaBH4": {"mechanism": "nucleophilic_addition", "product_type": "primary alcohol; hydride addition to carbonyl"},
    "carboxylic_acid,LiAlH4": {"mechanism": "nucleophilic_addition", "product_type": "primary alcohol; reduction via aldehyde intermediate"},
    "amine,RX": {"mechanism": "SN2", "product_type": "secondary or tertiary amine; alkylation of amine"},
    "alkene,BH3": {"mechanism": "electrophilic_addition", "product_type": "alcohol; anti-Markovnikov after H2O2/OH- workup"},
}


def predict_reaction(substrate: str, reagent: str) -> dict[str, Any] | None:
    """Return a predicted reaction profile for a given substrate and reagent.

    ``substrate`` should be a functional-group name (see ``FUNCTIONAL_GROUPS``
    keys).  ``reagent`` is a molecular formula or common reagent abbreviation.
    Returns ``None`` when no prediction is available.
    """
    key = f"{substrate},{reagent}"
    if key in _REACTION_MAP:
        return _REACTION_MAP[key].copy()
    return None


def look_up_named_reaction(name: str) -> dict[str, Any] | None:
    """Return data for a named reaction, with case-insensitive matching."""
    for key, value in NAMED_REACTIONS.items():
        if key.lower() == name.lower():
            return value
    return None
