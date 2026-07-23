"""Inorganic chemistry knowledge module for the physics collection.

Periodic table data, coordination chemistry, solid state chemistry, and
phase diagrams.

Public surface::

    PERIODIC_TABLE            dict[symbol] -> full element data (all 118 elements)
    LIGANDS                   dict[ligand] -> spectrochemical series data
    CRYSTAL_FIELD_SPLITTING   dict[geometry] -> orbital splitting patterns
    SOLID_STATE_DEFECTS       dict[defect] -> defect type and description
    PHASE_DIAGRAMS            dict[name] -> phase diagram data

    get_element_data(symbol)                     -> dict | None
    compute_crystal_field_splitting(metal, ligand, geometry) -> dict | None
    get_reaction(product)                        -> dict | None
"""

from __future__ import annotations

from typing import Any


PERIODIC_TABLE: dict[str, dict[str, Any]] = {
    "H":  {"atomic_number":1,  "symbol":"H",  "name":"Hydrogen",   "atomic_mass":1.008,     "electron_configuration":"1s1",    "electronegativity":2.20, "oxidation_states":[-1,1],         "group":1,  "period":1, "block":"s", "melting_point_k":14.01,   "boiling_point_k":20.28,   "density_gcm3":0.00008988, "atomic_radius_pm":53,  "ionization_energy_ev":13.598},
    "He": {"atomic_number":2,  "symbol":"He", "name":"Helium",      "atomic_mass":4.002602,  "electron_configuration":"1s2",    "electronegativity":None, "oxidation_states":[0],             "group":18, "period":1, "block":"s", "melting_point_k":0.95,    "boiling_point_k":4.22,    "density_gcm3":0.0001785,  "atomic_radius_pm":31,  "ionization_energy_ev":24.587},
    "Li": {"atomic_number":3,  "symbol":"Li", "name":"Lithium",     "atomic_mass":6.94,      "electron_configuration":"[He]2s1", "electronegativity":0.98, "oxidation_states":[1],             "group":1,  "period":2, "block":"s", "melting_point_k":453.69,  "boiling_point_k":1615.0,  "density_gcm3":0.534,      "atomic_radius_pm":167, "ionization_energy_ev":5.392},
    "Be": {"atomic_number":4,  "symbol":"Be", "name":"Beryllium",   "atomic_mass":9.0121831, "electron_configuration":"[He]2s2", "electronegativity":1.57, "oxidation_states":[2],             "group":2,  "period":2, "block":"s", "melting_point_k":1560.0,  "boiling_point_k":2742.0,  "density_gcm3":1.85,       "atomic_radius_pm":112, "ionization_energy_ev":9.323},
    "B":  {"atomic_number":5,  "symbol":"B",  "name":"Boron",       "atomic_mass":10.81,     "electron_configuration":"[He]2s2 2p1","electronegativity":2.04,"oxidation_states":[3],             "group":13, "period":2, "block":"p", "melting_point_k":2349.0,  "boiling_point_k":4200.0,  "density_gcm3":2.34,       "atomic_radius_pm":87,  "ionization_energy_ev":8.298},
    "C":  {"atomic_number":6,  "symbol":"C",  "name":"Carbon",      "atomic_mass":12.011,    "electron_configuration":"[He]2s2 2p2","electronegativity":2.55,"oxidation_states":[-4,-3,-2,-1,1,2,3,4],"group":14,"period":2,"block":"p","melting_point_k":3823.0,"boiling_point_k":4098.0,"density_gcm3":2.267,"atomic_radius_pm":67,"ionization_energy_ev":11.260},
    "N":  {"atomic_number":7,  "symbol":"N",  "name":"Nitrogen",    "atomic_mass":14.007,    "electron_configuration":"[He]2s2 2p3","electronegativity":3.04,"oxidation_states":[-3,-2,-1,1,2,3,4,5],"group":15,"period":2,"block":"p","melting_point_k":63.15,"boiling_point_k":77.36,"density_gcm3":0.0012506,"atomic_radius_pm":56,"ionization_energy_ev":14.534},
    "O":  {"atomic_number":8,  "symbol":"O",  "name":"Oxygen",      "atomic_mass":15.999,    "electron_configuration":"[He]2s2 2p4","electronegativity":3.44,"oxidation_states":[-2,-1,1,2],      "group":16,"period":2,"block":"p","melting_point_k":54.36,"boiling_point_k":90.20,"density_gcm3":0.001429,"atomic_radius_pm":48,"ionization_energy_ev":13.618},
    "F":  {"atomic_number":9,  "symbol":"F",  "name":"Fluorine",    "atomic_mass":18.998403163,"electron_configuration":"[He]2s2 2p5","electronegativity":3.98,"oxidation_states":[-1],             "group":17,"period":2,"block":"p","melting_point_k":53.53,"boiling_point_k":85.03,"density_gcm3":0.001696,"atomic_radius_pm":42,"ionization_energy_ev":17.423},
    "Ne": {"atomic_number":10, "symbol":"Ne", "name":"Neon",        "atomic_mass":20.1797,   "electron_configuration":"[He]2s2 2p6","electronegativity":None,"oxidation_states":[0],             "group":18,"period":2,"block":"p","melting_point_k":24.56,"boiling_point_k":27.07,"density_gcm3":0.0008999,"atomic_radius_pm":38,"ionization_energy_ev":21.565},
    "Na": {"atomic_number":11, "symbol":"Na", "name":"Sodium",      "atomic_mass":22.98976928,"electron_configuration":"[Ne]3s1","electronegativity":0.93,"oxidation_states":[1],             "group":1,"period":3,"block":"s","melting_point_k":370.87,"boiling_point_k":1156.0,"density_gcm3":0.971,"atomic_radius_pm":190,"ionization_energy_ev":5.139},
    "Mg": {"atomic_number":12, "symbol":"Mg", "name":"Magnesium",   "atomic_mass":24.305,    "electron_configuration":"[Ne]3s2","electronegativity":1.31,"oxidation_states":[2],             "group":2,"period":3,"block":"s","melting_point_k":923.0,"boiling_point_k":1363.0,"density_gcm3":1.738,"atomic_radius_pm":145,"ionization_energy_ev":7.646},
    "Al": {"atomic_number":13, "symbol":"Al", "name":"Aluminium",   "atomic_mass":26.9815384,"electron_configuration":"[Ne]3s2 3p1","electronegativity":1.61,"oxidation_states":[3],             "group":13,"period":3,"block":"p","melting_point_k":933.47,"boiling_point_k":2792.0,"density_gcm3":2.698,"atomic_radius_pm":118,"ionization_energy_ev":5.986},
    "Si": {"atomic_number":14, "symbol":"Si", "name":"Silicon",     "atomic_mass":28.085,    "electron_configuration":"[Ne]3s2 3p2","electronegativity":1.90,"oxidation_states":[-4,2,4],          "group":14,"period":3,"block":"p","melting_point_k":1687.0,"boiling_point_k":3538.0,"density_gcm3":2.3296,"atomic_radius_pm":111,"ionization_energy_ev":8.152},
    "P":  {"atomic_number":15, "symbol":"P",  "name":"Phosphorus",  "atomic_mass":30.973761998,"electron_configuration":"[Ne]3s2 3p3","electronegativity":2.19,"oxidation_states":[-3,1,3,5],         "group":15,"period":3,"block":"p","melting_point_k":317.3,"boiling_point_k":553.7,"density_gcm3":1.82,"atomic_radius_pm":98,"ionization_energy_ev":10.487},
    "S":  {"atomic_number":16, "symbol":"S",  "name":"Sulfur",      "atomic_mass":32.06,     "electron_configuration":"[Ne]3s2 3p4","electronegativity":2.58,"oxidation_states":[-2,2,4,6],         "group":16,"period":3,"block":"p","melting_point_k":388.36,"boiling_point_k":717.87,"density_gcm3":2.067,"atomic_radius_pm":88,"ionization_energy_ev":10.360},
    "Cl": {"atomic_number":17, "symbol":"Cl", "name":"Chlorine",    "atomic_mass":35.45,     "electron_configuration":"[Ne]3s2 3p5","electronegativity":3.16,"oxidation_states":[-1,1,3,5,7],       "group":17,"period":3,"block":"p","melting_point_k":171.6,"boiling_point_k":239.11,"density_gcm3":0.003214,"atomic_radius_pm":79,"ionization_energy_ev":12.968},
    "Ar": {"atomic_number":18, "symbol":"Ar", "name":"Argon",       "atomic_mass":39.948,    "electron_configuration":"[Ne]3s2 3p6","electronegativity":None,"oxidation_states":[0],             "group":18,"period":3,"block":"p","melting_point_k":83.80,"boiling_point_k":87.30,"density_gcm3":0.0017837,"atomic_radius_pm":71,"ionization_energy_ev":15.760},
    "K":  {"atomic_number":19, "symbol":"K",  "name":"Potassium",   "atomic_mass":39.0983,   "electron_configuration":"[Ar]4s1","electronegativity":0.82,"oxidation_states":[1],             "group":1,"period":4,"block":"s","melting_point_k":336.53,"boiling_point_k":1032.0,"density_gcm3":0.862,"atomic_radius_pm":243,"ionization_energy_ev":4.341},
    "Ca": {"atomic_number":20, "symbol":"Ca", "name":"Calcium",     "atomic_mass":40.078,    "electron_configuration":"[Ar]4s2","electronegativity":1.00,"oxidation_states":[2],             "group":2,"period":4,"block":"s","melting_point_k":1115.0,"boiling_point_k":1757.0,"density_gcm3":1.54,"atomic_radius_pm":194,"ionization_energy_ev":6.113},
    "Sc": {"atomic_number":21, "symbol":"Sc", "name":"Scandium",    "atomic_mass":44.955908, "electron_configuration":"[Ar]3d1 4s2","electronegativity":1.36,"oxidation_states":[3],             "group":3,"period":4,"block":"d","melting_point_k":1814.0,"boiling_point_k":3109.0,"density_gcm3":2.989,"atomic_radius_pm":184,"ionization_energy_ev":6.561},
    "Ti": {"atomic_number":22, "symbol":"Ti", "name":"Titanium",    "atomic_mass":47.867,    "electron_configuration":"[Ar]3d2 4s2","electronegativity":1.54,"oxidation_states":[2,3,4],          "group":4,"period":4,"block":"d","melting_point_k":1941.0,"boiling_point_k":3560.0,"density_gcm3":4.54,"atomic_radius_pm":176,"ionization_energy_ev":6.828},
    "V":  {"atomic_number":23, "symbol":"V",  "name":"Vanadium",    "atomic_mass":50.9415,   "electron_configuration":"[Ar]3d3 4s2","electronegativity":1.63,"oxidation_states":[2,3,4,5],         "group":5,"period":4,"block":"d","melting_point_k":2183.0,"boiling_point_k":3680.0,"density_gcm3":6.11,"atomic_radius_pm":171,"ionization_energy_ev":6.746},
    "Cr": {"atomic_number":24, "symbol":"Cr", "name":"Chromium",    "atomic_mass":51.9961,   "electron_configuration":"[Ar]3d5 4s1","electronegativity":1.66,"oxidation_states":[2,3,6],           "group":6,"period":4,"block":"d","melting_point_k":2180.0,"boiling_point_k":2944.0,"density_gcm3":7.15,"atomic_radius_pm":166,"ionization_energy_ev":6.767},
    "Mn": {"atomic_number":25, "symbol":"Mn", "name":"Manganese",   "atomic_mass":54.938043, "electron_configuration":"[Ar]3d5 4s2","electronegativity":1.55,"oxidation_states":[2,3,4,6,7],       "group":7,"period":4,"block":"d","melting_point_k":1519.0,"boiling_point_k":2334.0,"density_gcm3":7.44,"atomic_radius_pm":161,"ionization_energy_ev":7.434},
    "Fe": {"atomic_number":26, "symbol":"Fe", "name":"Iron",        "atomic_mass":55.845,    "electron_configuration":"[Ar]3d6 4s2","electronegativity":1.83,"oxidation_states":[2,3,6],           "group":8,"period":4,"block":"d","melting_point_k":1811.0,"boiling_point_k":3134.0,"density_gcm3":7.874,"atomic_radius_pm":156,"ionization_energy_ev":7.902},
    "Co": {"atomic_number":27, "symbol":"Co", "name":"Cobalt",      "atomic_mass":58.933194, "electron_configuration":"[Ar]3d7 4s2","electronegativity":1.88,"oxidation_states":[2,3],             "group":9,"period":4,"block":"d","melting_point_k":1768.0,"boiling_point_k":3200.0,"density_gcm3":8.86,"atomic_radius_pm":152,"ionization_energy_ev":7.881},
    "Ni": {"atomic_number":28, "symbol":"Ni", "name":"Nickel",      "atomic_mass":58.6934,   "electron_configuration":"[Ar]3d8 4s2","electronegativity":1.91,"oxidation_states":[2,3],             "group":10,"period":4,"block":"d","melting_point_k":1728.0,"boiling_point_k":3186.0,"density_gcm3":8.912,"atomic_radius_pm":149,"ionization_energy_ev":7.640},
    "Cu": {"atomic_number":29, "symbol":"Cu", "name":"Copper",      "atomic_mass":63.546,    "electron_configuration":"[Ar]3d10 4s1","electronegativity":1.90,"oxidation_states":[1,2],             "group":11,"period":4,"block":"d","melting_point_k":1357.77,"boiling_point_k":2835.0,"density_gcm3":8.96,"atomic_radius_pm":145,"ionization_energy_ev":7.726},
    "Zn": {"atomic_number":30, "symbol":"Zn", "name":"Zinc",        "atomic_mass":65.38,     "electron_configuration":"[Ar]3d10 4s2","electronegativity":1.65,"oxidation_states":[2],              "group":12,"period":4,"block":"d","melting_point_k":692.88,"boiling_point_k":1180.0,"density_gcm3":7.134,"atomic_radius_pm":142,"ionization_energy_ev":9.394},
    "Ga": {"atomic_number":31, "symbol":"Ga", "name":"Gallium",     "atomic_mass":69.723,    "electron_configuration":"[Ar]3d10 4s2 4p1","electronegativity":1.81,"oxidation_states":[1,3],        "group":13,"period":4,"block":"p","melting_point_k":302.9146,"boiling_point_k":2477.0,"density_gcm3":5.907,"atomic_radius_pm":136,"ionization_energy_ev":5.999},
    "Ge": {"atomic_number":32, "symbol":"Ge", "name":"Germanium",   "atomic_mass":72.630,    "electron_configuration":"[Ar]3d10 4s2 4p2","electronegativity":2.01,"oxidation_states":[2,4],        "group":14,"period":4,"block":"p","melting_point_k":1211.4,"boiling_point_k":3106.0,"density_gcm3":5.323,"atomic_radius_pm":125,"ionization_energy_ev":7.899},
    "As": {"atomic_number":33, "symbol":"As", "name":"Arsenic",     "atomic_mass":74.921595, "electron_configuration":"[Ar]3d10 4s2 4p3","electronegativity":2.18,"oxidation_states":[-3,3,5],      "group":15,"period":4,"block":"p","melting_point_k":1090.0,"boiling_point_k":887.0,"density_gcm3":5.776,"atomic_radius_pm":114,"ionization_energy_ev":9.789},
    "Se": {"atomic_number":34, "symbol":"Se", "name":"Selenium",    "atomic_mass":78.971,    "electron_configuration":"[Ar]3d10 4s2 4p4","electronegativity":2.55,"oxidation_states":[-2,2,4,6],     "group":16,"period":4,"block":"p","melting_point_k":494.0,"boiling_point_k":958.0,"density_gcm3":4.809,"atomic_radius_pm":103,"ionization_energy_ev":9.752},
    "Br": {"atomic_number":35, "symbol":"Br", "name":"Bromine",     "atomic_mass":79.904,    "electron_configuration":"[Ar]3d10 4s2 4p5","electronegativity":2.96,"oxidation_states":[-1,1,3,5,7],  "group":17,"period":4,"block":"p","melting_point_k":265.95,"boiling_point_k":331.95,"density_gcm3":3.1228,"atomic_radius_pm":94,"ionization_energy_ev":11.814},
    "Kr": {"atomic_number":36, "symbol":"Kr", "name":"Krypton",     "atomic_mass":83.798,    "electron_configuration":"[Ar]3d10 4s2 4p6","electronegativity":3.00,"oxidation_states":[0,2],         "group":18,"period":4,"block":"p","melting_point_k":115.79,"boiling_point_k":119.93,"density_gcm3":0.003733,"atomic_radius_pm":88,"ionization_energy_ev":14.000},
    "Rb": {"atomic_number":37, "symbol":"Rb", "name":"Rubidium",    "atomic_mass":85.4678,   "electron_configuration":"[Kr]5s1","electronegativity":0.82,"oxidation_states":[1],             "group":1,"period":5,"block":"s","melting_point_k":312.46,"boiling_point_k":961.0,"density_gcm3":1.532,"atomic_radius_pm":265,"ionization_energy_ev":4.177},
    "Sr": {"atomic_number":38, "symbol":"Sr", "name":"Strontium",   "atomic_mass":87.62,     "electron_configuration":"[Kr]5s2","electronegativity":0.95,"oxidation_states":[2],             "group":2,"period":5,"block":"s","melting_point_k":1050.0,"boiling_point_k":1655.0,"density_gcm3":2.64,"atomic_radius_pm":219,"ionization_energy_ev":5.695},
    "Y":  {"atomic_number":39, "symbol":"Y",  "name":"Yttrium",     "atomic_mass":88.90584,  "electron_configuration":"[Kr]4d1 5s2","electronegativity":1.22,"oxidation_states":[3],             "group":3,"period":5,"block":"d","melting_point_k":1795.0,"boiling_point_k":3618.0,"density_gcm3":4.469,"atomic_radius_pm":212,"ionization_energy_ev":6.217},
    "Zr": {"atomic_number":40, "symbol":"Zr", "name":"Zirconium",   "atomic_mass":91.224,    "electron_configuration":"[Kr]4d2 5s2","electronegativity":1.33,"oxidation_states":[4],             "group":4,"period":5,"block":"d","melting_point_k":2128.0,"boiling_point_k":4682.0,"density_gcm3":6.506,"atomic_radius_pm":206,"ionization_energy_ev":6.634},
    "Nb": {"atomic_number":41, "symbol":"Nb", "name":"Niobium",     "atomic_mass":92.90637,  "electron_configuration":"[Kr]4d4 5s1","electronegativity":1.60,"oxidation_states":[3,5],            "group":5,"period":5,"block":"d","melting_point_k":2750.0,"boiling_point_k":5017.0,"density_gcm3":8.57,"atomic_radius_pm":198,"ionization_energy_ev":6.759},
    "Mo": {"atomic_number":42, "symbol":"Mo", "name":"Molybdenum",  "atomic_mass":95.95,     "electron_configuration":"[Kr]4d5 5s1","electronegativity":2.16,"oxidation_states":[2,3,4,5,6],       "group":6,"period":5,"block":"d","melting_point_k":2896.0,"boiling_point_k":4912.0,"density_gcm3":10.22,"atomic_radius_pm":190,"ionization_energy_ev":7.092},
    "Tc": {"atomic_number":43, "symbol":"Tc", "name":"Technetium",  "atomic_mass":98.0,      "electron_configuration":"[Kr]4d5 5s2","electronegativity":1.90,"oxidation_states":[4,7],            "group":7,"period":5,"block":"d","melting_point_k":2430.0,"boiling_point_k":4538.0,"density_gcm3":11.5,"atomic_radius_pm":183,"ionization_energy_ev":7.280},
    "Ru": {"atomic_number":44, "symbol":"Ru", "name":"Ruthenium",   "atomic_mass":101.07,    "electron_configuration":"[Kr]4d7 5s1","electronegativity":2.20,"oxidation_states":[2,3,4,8],         "group":8,"period":5,"block":"d","melting_point_k":2607.0,"boiling_point_k":4423.0,"density_gcm3":12.37,"atomic_radius_pm":178,"ionization_energy_ev":7.361},
    "Rh": {"atomic_number":45, "symbol":"Rh", "name":"Rhodium",     "atomic_mass":102.90549, "electron_configuration":"[Kr]4d8 5s1","electronegativity":2.28,"oxidation_states":[3],             "group":9,"period":5,"block":"d","melting_point_k":2237.0,"boiling_point_k":3968.0,"density_gcm3":12.41,"atomic_radius_pm":173,"ionization_energy_ev":7.459},
    "Pd": {"atomic_number":46, "symbol":"Pd", "name":"Palladium",   "atomic_mass":106.42,    "electron_configuration":"[Kr]4d10","electronegativity":2.20,"oxidation_states":[2,4],             "group":10,"period":5,"block":"d","melting_point_k":1828.05,"boiling_point_k":3236.0,"density_gcm3":12.02,"atomic_radius_pm":169,"ionization_energy_ev":8.337},
    "Ag": {"atomic_number":47, "symbol":"Ag", "name":"Silver",      "atomic_mass":107.8682,  "electron_configuration":"[Kr]4d10 5s1","electronegativity":1.93,"oxidation_states":[1],             "group":11,"period":5,"block":"d","melting_point_k":1234.93,"boiling_point_k":2435.0,"density_gcm3":10.501,"atomic_radius_pm":165,"ionization_energy_ev":7.576},
    "Cd": {"atomic_number":48, "symbol":"Cd", "name":"Cadmium",     "atomic_mass":112.414,   "electron_configuration":"[Kr]4d10 5s2","electronegativity":1.69,"oxidation_states":[2],             "group":12,"period":5,"block":"d","melting_point_k":594.22,"boiling_point_k":1040.0,"density_gcm3":8.69,"atomic_radius_pm":161,"ionization_energy_ev":8.994},
    "In": {"atomic_number":49, "symbol":"In", "name":"Indium",      "atomic_mass":114.818,   "electron_configuration":"[Kr]4d10 5s2 5p1","electronegativity":1.78,"oxidation_states":[1,3],       "group":13,"period":5,"block":"p","melting_point_k":429.75,"boiling_point_k":2345.0,"density_gcm3":7.31,"atomic_radius_pm":156,"ionization_energy_ev":5.786},
    "Sn": {"atomic_number":50, "symbol":"Sn", "name":"Tin",         "atomic_mass":118.710,   "electron_configuration":"[Kr]4d10 5s2 5p2","electronegativity":1.96,"oxidation_states":[2,4],       "group":14,"period":5,"block":"p","melting_point_k":505.08,"boiling_point_k":2875.0,"density_gcm3":7.287,"atomic_radius_pm":145,"ionization_energy_ev":7.344},
    "Sb": {"atomic_number":51, "symbol":"Sb", "name":"Antimony",    "atomic_mass":121.760,   "electron_configuration":"[Kr]4d10 5s2 5p3","electronegativity":2.05,"oxidation_states":[-3,3,5],     "group":15,"period":5,"block":"p","melting_point_k":903.78,"boiling_point_k":1860.0,"density_gcm3":6.685,"atomic_radius_pm":133,"ionization_energy_ev":8.608},
    "Te": {"atomic_number":52, "symbol":"Te", "name":"Tellurium",   "atomic_mass":127.60,    "electron_configuration":"[Kr]4d10 5s2 5p4","electronegativity":2.10,"oxidation_states":[-2,2,4,6],    "group":16,"period":5,"block":"p","melting_point_k":722.66,"boiling_point_k":1261.0,"density_gcm3":6.232,"atomic_radius_pm":123,"ionization_energy_ev":9.010},
    "I":  {"atomic_number":53, "symbol":"I",  "name":"Iodine",      "atomic_mass":126.90447, "electron_configuration":"[Kr]4d10 5s2 5p5","electronegativity":2.66,"oxidation_states":[-1,1,3,5,7],   "group":17,"period":5,"block":"p","melting_point_k":386.85,"boiling_point_k":457.4,"density_gcm3":4.93,"atomic_radius_pm":115,"ionization_energy_ev":10.451},
    "Xe": {"atomic_number":54, "symbol":"Xe", "name":"Xenon",       "atomic_mass":131.293,   "electron_configuration":"[Kr]4d10 5s2 5p6","electronegativity":2.60,"oxidation_states":[0,2,4,6,8],     "group":18,"period":5,"block":"p","melting_point_k":161.4,"boiling_point_k":165.03,"density_gcm3":0.005887,"atomic_radius_pm":108,"ionization_energy_ev":12.130},
    "Cs": {"atomic_number":55, "symbol":"Cs", "name":"Caesium",     "atomic_mass":132.90545196,"electron_configuration":"[Xe]6s1","electronegativity":0.79,"oxidation_states":[1],           "group":1,"period":6,"block":"s","melting_point_k":301.59,"boiling_point_k":944.0,"density_gcm3":1.873,"atomic_radius_pm":298,"ionization_energy_ev":3.894},
    "Ba": {"atomic_number":56, "symbol":"Ba", "name":"Barium",      "atomic_mass":137.327,   "electron_configuration":"[Xe]6s2","electronegativity":0.89,"oxidation_states":[2],             "group":2,"period":6,"block":"s","melting_point_k":1000.0,"boiling_point_k":2170.0,"density_gcm3":3.594,"atomic_radius_pm":253,"ionization_energy_ev":5.212},
    "La": {"atomic_number":57, "symbol":"La", "name":"Lanthanum",   "atomic_mass":138.90547, "electron_configuration":"[Xe]5d1 6s2","electronegativity":1.10,"oxidation_states":[3],            "group":3,"period":6,"block":"f","melting_point_k":1193.0,"boiling_point_k":3737.0,"density_gcm3":6.145,"atomic_radius_pm":187,"ionization_energy_ev":5.577},
    "Ce": {"atomic_number":58, "symbol":"Ce", "name":"Cerium",      "atomic_mass":140.116,   "electron_configuration":"[Xe]4f1 5d1 6s2","electronegativity":1.12,"oxidation_states":[3,4],       "group":None,"period":6,"block":"f","melting_point_k":1068.0,"boiling_point_k":3716.0,"density_gcm3":6.77,"atomic_radius_pm":182,"ionization_energy_ev":5.539},
    "Pr": {"atomic_number":59, "symbol":"Pr", "name":"Praseodymium","atomic_mass":140.90766, "electron_configuration":"[Xe]4f3 6s2","electronegativity":1.13,"oxidation_states":[3,4],           "group":None,"period":6,"block":"f","melting_point_k":1208.0,"boiling_point_k":3793.0,"density_gcm3":6.773,"atomic_radius_pm":182,"ionization_energy_ev":5.473},
    "Nd": {"atomic_number":60, "symbol":"Nd", "name":"Neodymium",   "atomic_mass":144.242,   "electron_configuration":"[Xe]4f4 6s2","electronegativity":1.14,"oxidation_states":[3],             "group":None,"period":6,"block":"f","melting_point_k":1297.0,"boiling_point_k":3347.0,"density_gcm3":7.007,"atomic_radius_pm":182,"ionization_energy_ev":5.525},
    "Pm": {"atomic_number":61, "symbol":"Pm", "name":"Promethium",  "atomic_mass":145.0,     "electron_configuration":"[Xe]4f5 6s2","electronegativity":None,"oxidation_states":[3],            "group":None,"period":6,"block":"f","melting_point_k":1315.0,"boiling_point_k":3273.0,"density_gcm3":7.26,"atomic_radius_pm":181,"ionization_energy_ev":5.582},
    "Sm": {"atomic_number":62, "symbol":"Sm", "name":"Samarium",    "atomic_mass":150.36,    "electron_configuration":"[Xe]4f6 6s2","electronegativity":1.17,"oxidation_states":[2,3],            "group":None,"period":6,"block":"f","melting_point_k":1345.0,"boiling_point_k":2067.0,"density_gcm3":7.52,"atomic_radius_pm":180,"ionization_energy_ev":5.644},
    "Eu": {"atomic_number":63, "symbol":"Eu", "name":"Europium",    "atomic_mass":151.964,   "electron_configuration":"[Xe]4f7 6s2","electronegativity":None,"oxidation_states":[2,3],            "group":None,"period":6,"block":"f","melting_point_k":1099.0,"boiling_point_k":1802.0,"density_gcm3":5.243,"atomic_radius_pm":199,"ionization_energy_ev":5.670},
    "Gd": {"atomic_number":64, "symbol":"Gd", "name":"Gadolinium",  "atomic_mass":157.25,    "electron_configuration":"[Xe]4f7 5d1 6s2","electronegativity":1.20,"oxidation_states":[3],        "group":None,"period":6,"block":"f","melting_point_k":1585.0,"boiling_point_k":3546.0,"density_gcm3":7.895,"atomic_radius_pm":180,"ionization_energy_ev":6.150},
    "Tb": {"atomic_number":65, "symbol":"Tb", "name":"Terbium",     "atomic_mass":158.925354,"electron_configuration":"[Xe]4f9 6s2","electronegativity":None,"oxidation_states":[3,4],         "group":None,"period":6,"block":"f","melting_point_k":1629.0,"boiling_point_k":3503.0,"density_gcm3":8.229,"atomic_radius_pm":178,"ionization_energy_ev":5.864},
    "Dy": {"atomic_number":66, "symbol":"Dy", "name":"Dysprosium",  "atomic_mass":162.500,   "electron_configuration":"[Xe]4f10 6s2","electronegativity":1.22,"oxidation_states":[3],            "group":None,"period":6,"block":"f","melting_point_k":1680.0,"boiling_point_k":2840.0,"density_gcm3":8.55,"atomic_radius_pm":177,"ionization_energy_ev":5.939},
    "Ho": {"atomic_number":67, "symbol":"Ho", "name":"Holmium",     "atomic_mass":164.930328,"electron_configuration":"[Xe]4f11 6s2","electronegativity":1.23,"oxidation_states":[3],           "group":None,"period":6,"block":"f","melting_point_k":1734.0,"boiling_point_k":2993.0,"density_gcm3":8.795,"atomic_radius_pm":176,"ionization_energy_ev":6.022},
    "Er": {"atomic_number":68, "symbol":"Er", "name":"Erbium",      "atomic_mass":167.259,   "electron_configuration":"[Xe]4f12 6s2","electronegativity":1.24,"oxidation_states":[3],            "group":None,"period":6,"block":"f","melting_point_k":1802.0,"boiling_point_k":3141.0,"density_gcm3":9.066,"atomic_radius_pm":175,"ionization_energy_ev":6.108},
    "Tm": {"atomic_number":69, "symbol":"Tm", "name":"Thulium",     "atomic_mass":168.934218,"electron_configuration":"[Xe]4f13 6s2","electronegativity":1.25,"oxidation_states":[3],           "group":None,"period":6,"block":"f","melting_point_k":1818.0,"boiling_point_k":2223.0,"density_gcm3":9.321,"atomic_radius_pm":174,"ionization_energy_ev":6.184},
    "Yb": {"atomic_number":70, "symbol":"Yb", "name":"Ytterbium",   "atomic_mass":173.045,   "electron_configuration":"[Xe]4f14 6s2","electronegativity":None,"oxidation_states":[2,3],          "group":None,"period":6,"block":"f","melting_point_k":1097.0,"boiling_point_k":1469.0,"density_gcm3":6.965,"atomic_radius_pm":194,"ionization_energy_ev":6.254},
    "Lu": {"atomic_number":71, "symbol":"Lu", "name":"Lutetium",    "atomic_mass":174.9668,  "electron_configuration":"[Xe]4f14 5d1 6s2","electronegativity":1.27,"oxidation_states":[3],       "group":3,"period":6,"block":"d","melting_point_k":1925.0,"boiling_point_k":3675.0,"density_gcm3":9.84,"atomic_radius_pm":174,"ionization_energy_ev":5.426},
    "Hf": {"atomic_number":72, "symbol":"Hf", "name":"Hafnium",     "atomic_mass":178.49,    "electron_configuration":"[Xe]4f14 5d2 6s2","electronegativity":1.30,"oxidation_states":[4],       "group":4,"period":6,"block":"d","melting_point_k":2506.0,"boiling_point_k":4876.0,"density_gcm3":13.31,"atomic_radius_pm":208,"ionization_energy_ev":6.825},
    "Ta": {"atomic_number":73, "symbol":"Ta", "name":"Tantalum",    "atomic_mass":180.94788, "electron_configuration":"[Xe]4f14 5d3 6s2","electronegativity":1.50,"oxidation_states":[5],       "group":5,"period":6,"block":"d","melting_point_k":3290.0,"boiling_point_k":5731.0,"density_gcm3":16.654,"atomic_radius_pm":200,"ionization_energy_ev":7.890},
    "W":  {"atomic_number":74, "symbol":"W",  "name":"Tungsten",    "atomic_mass":183.84,    "electron_configuration":"[Xe]4f14 5d4 6s2","electronegativity":2.36,"oxidation_states":[2,3,4,5,6],    "group":6,"period":6,"block":"d","melting_point_k":3695.0,"boiling_point_k":5828.0,"density_gcm3":19.25,"atomic_radius_pm":193,"ionization_energy_ev":7.980},
    "Re": {"atomic_number":75, "symbol":"Re", "name":"Rhenium",     "atomic_mass":186.207,   "electron_configuration":"[Xe]4f14 5d5 6s2","electronegativity":1.90,"oxidation_states":[4,6,7],       "group":7,"period":6,"block":"d","melting_point_k":3459.0,"boiling_point_k":5869.0,"density_gcm3":21.02,"atomic_radius_pm":188,"ionization_energy_ev":7.880},
    "Os": {"atomic_number":76, "symbol":"Os", "name":"Osmium",      "atomic_mass":190.23,    "electron_configuration":"[Xe]4f14 5d6 6s2","electronegativity":2.20,"oxidation_states":[2,3,4,6,8],     "group":8,"period":6,"block":"d","melting_point_k":3306.0,"boiling_point_k":5285.0,"density_gcm3":22.61,"atomic_radius_pm":185,"ionization_energy_ev":8.700},
    "Ir": {"atomic_number":77, "symbol":"Ir", "name":"Iridium",     "atomic_mass":192.217,   "electron_configuration":"[Xe]4f14 5d7 6s2","electronegativity":2.20,"oxidation_states":[3,4],         "group":9,"period":6,"block":"d","melting_point_k":2719.0,"boiling_point_k":4701.0,"density_gcm3":22.56,"atomic_radius_pm":180,"ionization_energy_ev":9.100},
    "Pt": {"atomic_number":78, "symbol":"Pt", "name":"Platinum",    "atomic_mass":195.084,   "electron_configuration":"[Xe]4f14 5d9 6s1","electronegativity":2.28,"oxidation_states":[2,4],        "group":10,"period":6,"block":"d","melting_point_k":2041.4,"boiling_point_k":4098.0,"density_gcm3":21.46,"atomic_radius_pm":177,"ionization_energy_ev":9.000},
    "Au": {"atomic_number":79, "symbol":"Au", "name":"Gold",        "atomic_mass":196.966570,"electron_configuration":"[Xe]4f14 5d10 6s1","electronegativity":2.54,"oxidation_states":[1,3],      "group":11,"period":6,"block":"d","melting_point_k":1337.33,"boiling_point_k":3129.0,"density_gcm3":19.282,"atomic_radius_pm":174,"ionization_energy_ev":9.226},
    "Hg": {"atomic_number":80, "symbol":"Hg", "name":"Mercury",     "atomic_mass":200.592,   "electron_configuration":"[Xe]4f14 5d10 6s2","electronegativity":2.00,"oxidation_states":[1,2],      "group":12,"period":6,"block":"d","melting_point_k":234.43,"boiling_point_k":629.88,"density_gcm3":13.5336,"atomic_radius_pm":171,"ionization_energy_ev":10.438},
    "Tl": {"atomic_number":81, "symbol":"Tl", "name":"Thallium",    "atomic_mass":204.38,    "electron_configuration":"[Xe]4f14 5d10 6s2 6p1","electronegativity":2.04,"oxidation_states":[1,3], "group":13,"period":6,"block":"p","melting_point_k":577.0,"boiling_point_k":1746.0,"density_gcm3":11.85,"atomic_radius_pm":170,"ionization_energy_ev":6.108},
    "Pb": {"atomic_number":82, "symbol":"Pb", "name":"Lead",        "atomic_mass":207.2,     "electron_configuration":"[Xe]4f14 5d10 6s2 6p2","electronegativity":2.33,"oxidation_states":[2,4], "group":14,"period":6,"block":"p","melting_point_k":600.61,"boiling_point_k":2022.0,"density_gcm3":11.342,"atomic_radius_pm":154,"ionization_energy_ev":7.417},
    "Bi": {"atomic_number":83, "symbol":"Bi", "name":"Bismuth",     "atomic_mass":208.98040, "electron_configuration":"[Xe]4f14 5d10 6s2 6p3","electronegativity":2.02,"oxidation_states":[3,5],"group":15,"period":6,"block":"p","melting_point_k":544.7,"boiling_point_k":1837.0,"density_gcm3":9.807,"atomic_radius_pm":143,"ionization_energy_ev":7.286},
    "Po": {"atomic_number":84, "symbol":"Po", "name":"Polonium",    "atomic_mass":209.0,     "electron_configuration":"[Xe]4f14 5d10 6s2 6p4","electronegativity":2.00,"oxidation_states":[2,4],"group":16,"period":6,"block":"p","melting_point_k":527.0,"boiling_point_k":1235.0,"density_gcm3":9.32,"atomic_radius_pm":135,"ionization_energy_ev":8.417},
    "At": {"atomic_number":85, "symbol":"At", "name":"Astatine",    "atomic_mass":210.0,     "electron_configuration":"[Xe]4f14 5d10 6s2 6p5","electronegativity":2.20,"oxidation_states":[-1,1,3,5,7],"group":17,"period":6,"block":"p","melting_point_k":575.0,"boiling_point_k":610.0,"density_gcm3":7.0,"atomic_radius_pm":127,"ionization_energy_ev":9.318},
    "Rn": {"atomic_number":86, "symbol":"Rn", "name":"Radon",       "atomic_mass":222.0,     "electron_configuration":"[Xe]4f14 5d10 6s2 6p6","electronegativity":None,"oxidation_states":[0],"group":18,"period":6,"block":"p","melting_point_k":202.0,"boiling_point_k":211.3,"density_gcm3":0.00973,"atomic_radius_pm":120,"ionization_energy_ev":10.749},
    "Fr": {"atomic_number":87, "symbol":"Fr", "name":"Francium",    "atomic_mass":223.0,     "electron_configuration":"[Rn]7s1","electronegativity":0.70,"oxidation_states":[1],             "group":1,"period":7,"block":"s","melting_point_k":300.0,"boiling_point_k":950.0,"density_gcm3":1.87,"atomic_radius_pm":None,"ionization_energy_ev":4.073},
    "Ra": {"atomic_number":88, "symbol":"Ra", "name":"Radium",      "atomic_mass":226.0,     "electron_configuration":"[Rn]7s2","electronegativity":0.90,"oxidation_states":[2],              "group":2,"period":7,"block":"s","melting_point_k":973.0,"boiling_point_k":1413.0,"density_gcm3":5.5,"atomic_radius_pm":None,"ionization_energy_ev":5.278},
    "Ac": {"atomic_number":89, "symbol":"Ac", "name":"Actinium",    "atomic_mass":227.0,     "electron_configuration":"[Rn]6d1 7s2","electronegativity":1.10,"oxidation_states":[3],            "group":3,"period":7,"block":"f","melting_point_k":1323.0,"boiling_point_k":3471.0,"density_gcm3":10.07,"atomic_radius_pm":None,"ionization_energy_ev":5.170},
    "Th": {"atomic_number":90, "symbol":"Th", "name":"Thorium",     "atomic_mass":232.0377,  "electron_configuration":"[Rn]6d2 7s2","electronegativity":1.30,"oxidation_states":[4],            "group":None,"period":7,"block":"f","melting_point_k":2115.0,"boiling_point_k":5061.0,"density_gcm3":11.72,"atomic_radius_pm":None,"ionization_energy_ev":6.307},
    "Pa": {"atomic_number":91, "symbol":"Pa", "name":"Protactinium","atomic_mass":231.03588, "electron_configuration":"[Rn]5f2 6d1 7s2","electronegativity":1.50,"oxidation_states":[4,5],      "group":None,"period":7,"block":"f","melting_point_k":1841.0,"boiling_point_k":4300.0,"density_gcm3":15.37,"atomic_radius_pm":None,"ionization_energy_ev":5.890},
    "U":  {"atomic_number":92, "symbol":"U",  "name":"Uranium",     "atomic_mass":238.02891, "electron_configuration":"[Rn]5f3 6d1 7s2","electronegativity":1.38,"oxidation_states":[3,4,5,6],     "group":None,"period":7,"block":"f","melting_point_k":1405.3,"boiling_point_k":4404.0,"density_gcm3":18.95,"atomic_radius_pm":None,"ionization_energy_ev":6.194},
    "Np": {"atomic_number":93, "symbol":"Np", "name":"Neptunium",   "atomic_mass":237.0,     "electron_configuration":"[Rn]5f4 6d1 7s2","electronegativity":1.36,"oxidation_states":[3,4,5,6,7],   "group":None,"period":7,"block":"f","melting_point_k":917.0,"boiling_point_k":4175.0,"density_gcm3":20.45,"atomic_radius_pm":None,"ionization_energy_ev":6.266},
    "Pu": {"atomic_number":94, "symbol":"Pu", "name":"Plutonium",   "atomic_mass":244.0,     "electron_configuration":"[Rn]5f6 7s2","electronegativity":1.28,"oxidation_states":[3,4,5,6,7],       "group":None,"period":7,"block":"f","melting_point_k":912.5,"boiling_point_k":3508.0,"density_gcm3":19.84,"atomic_radius_pm":None,"ionization_energy_ev":6.060},
    "Am": {"atomic_number":95, "symbol":"Am", "name":"Americium",   "atomic_mass":243.0,     "electron_configuration":"[Rn]5f7 7s2","electronegativity":1.30,"oxidation_states":[3,4,5,6],         "group":None,"period":7,"block":"f","melting_point_k":1449.0,"boiling_point_k":2880.0,"density_gcm3":13.69,"atomic_radius_pm":None,"ionization_energy_ev":5.974},
    "Cm": {"atomic_number":96, "symbol":"Cm", "name":"Curium",      "atomic_mass":247.0,     "electron_configuration":"[Rn]5f7 6d1 7s2","electronegativity":1.30,"oxidation_states":[3,4],        "group":None,"period":7,"block":"f","melting_point_k":1613.0,"boiling_point_k":3383.0,"density_gcm3":13.51,"atomic_radius_pm":None,"ionization_energy_ev":5.992},
    "Bk": {"atomic_number":97, "symbol":"Bk", "name":"Berkelium",   "atomic_mass":247.0,     "electron_configuration":"[Rn]5f9 7s2","electronegativity":1.30,"oxidation_states":[3,4],             "group":None,"period":7,"block":"f","melting_point_k":1259.0,"boiling_point_k":2900.0,"density_gcm3":14.79,"atomic_radius_pm":None,"ionization_energy_ev":6.198},
    "Cf": {"atomic_number":98, "symbol":"Cf", "name":"Californium", "atomic_mass":251.0,     "electron_configuration":"[Rn]5f10 7s2","electronegativity":1.30,"oxidation_states":[3,4],           "group":None,"period":7,"block":"f","melting_point_k":1173.0,"boiling_point_k":1743.0,"density_gcm3":15.1,"atomic_radius_pm":None,"ionization_energy_ev":6.282},
    "Es": {"atomic_number":99, "symbol":"Es", "name":"Einsteinium", "atomic_mass":252.0,     "electron_configuration":"[Rn]5f11 7s2","electronegativity":1.30,"oxidation_states":[3],            "group":None,"period":7,"block":"f","melting_point_k":1133.0,"boiling_point_k":None,"density_gcm3":8.84,"atomic_radius_pm":None,"ionization_energy_ev":6.420},
    "Fm": {"atomic_number":100,"symbol":"Fm", "name":"Fermium",     "atomic_mass":257.0,     "electron_configuration":"[Rn]5f12 7s2","electronegativity":1.30,"oxidation_states":[3],            "group":None,"period":7,"block":"f","melting_point_k":1800.0,"boiling_point_k":None,"density_gcm3":None,"atomic_radius_pm":None,"ionization_energy_ev":6.500},
    "Md": {"atomic_number":101,"symbol":"Md", "name":"Mendelevium", "atomic_mass":258.0,     "electron_configuration":"[Rn]5f13 7s2","electronegativity":1.30,"oxidation_states":[2,3],          "group":None,"period":7,"block":"f","melting_point_k":1100.0,"boiling_point_k":None,"density_gcm3":None,"atomic_radius_pm":None,"ionization_energy_ev":6.580},
    "No": {"atomic_number":102,"symbol":"No", "name":"Nobelium",    "atomic_mass":259.0,     "electron_configuration":"[Rn]5f14 7s2","electronegativity":1.30,"oxidation_states":[2],            "group":None,"period":7,"block":"f","melting_point_k":1100.0,"boiling_point_k":None,"density_gcm3":None,"atomic_radius_pm":None,"ionization_energy_ev":6.650},
    "Lr": {"atomic_number":103,"symbol":"Lr", "name":"Lawrencium",  "atomic_mass":266.0,     "electron_configuration":"[Rn]5f14 7s2 7p1","electronegativity":None,"oxidation_states":[3],        "group":3,"period":7,"block":"d","melting_point_k":1900.0,"boiling_point_k":None,"density_gcm3":None,"atomic_radius_pm":None,"ionization_energy_ev":4.900},
    "Rf": {"atomic_number":104,"symbol":"Rf", "name":"Rutherfordium","atomic_mass":267.0,    "electron_configuration":"[Rn]5f14 6d2 7s2","electronegativity":None,"oxidation_states":[4],       "group":4,"period":7,"block":"d","melting_point_k":2400.0,"boiling_point_k":5800.0,"density_gcm3":23.2,"atomic_radius_pm":None,"ionization_energy_ev":6.010},
    "Db": {"atomic_number":105,"symbol":"Db", "name":"Dubnium",     "atomic_mass":268.0,     "electron_configuration":"[Rn]5f14 6d3 7s2","electronegativity":None,"oxidation_states":[5],       "group":5,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":29.3,"atomic_radius_pm":None,"ionization_energy_ev":6.800},
    "Sg": {"atomic_number":106,"symbol":"Sg", "name":"Seaborgium",  "atomic_mass":269.0,     "electron_configuration":"[Rn]5f14 6d4 7s2","electronegativity":None,"oxidation_states":[6],       "group":6,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":35.0,"atomic_radius_pm":None,"ionization_energy_ev":7.500},
    "Bh": {"atomic_number":107,"symbol":"Bh", "name":"Bohrium",     "atomic_mass":270.0,     "electron_configuration":"[Rn]5f14 6d5 7s2","electronegativity":None,"oxidation_states":[7],       "group":7,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":37.1,"atomic_radius_pm":None,"ionization_energy_ev":7.700},
    "Hs": {"atomic_number":108,"symbol":"Hs", "name":"Hassium",     "atomic_mass":270.0,     "electron_configuration":"[Rn]5f14 6d6 7s2","electronegativity":None,"oxidation_states":[8],       "group":8,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":40.7,"atomic_radius_pm":None,"ionization_energy_ev":7.900},
    "Mt": {"atomic_number":109,"symbol":"Mt", "name":"Meitnerium",  "atomic_mass":278.0,     "electron_configuration":"[Rn]5f14 6d7 7s2","electronegativity":None,"oxidation_states":[],        "group":9,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":37.4,"atomic_radius_pm":None,"ionization_energy_ev":8.200},
    "Ds": {"atomic_number":110,"symbol":"Ds", "name":"Darmstadtium","atomic_mass":281.0,     "electron_configuration":"[Rn]5f14 6d8 7s2","electronegativity":None,"oxidation_states":[],        "group":10,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":34.8,"atomic_radius_pm":None,"ionization_energy_ev":8.500},
    "Rg": {"atomic_number":111,"symbol":"Rg", "name":"Roentgenium", "atomic_mass":282.0,     "electron_configuration":"[Rn]5f14 6d9 7s2","electronegativity":None,"oxidation_states":[],        "group":11,"period":7,"block":"d","melting_point_k":None,"boiling_point_k":None,"density_gcm3":28.7,"atomic_radius_pm":None,"ionization_energy_ev":8.700},
    "Cn": {"atomic_number":112,"symbol":"Cn", "name":"Copernicium", "atomic_mass":285.0,     "electron_configuration":"[Rn]5f14 6d10 7s2","electronegativity":None,"oxidation_states":[2],       "group":12,"period":7,"block":"d","melting_point_k":283.0,"boiling_point_k":340.0,"density_gcm3":23.7,"atomic_radius_pm":None,"ionization_energy_ev":9.100},
    "Nh": {"atomic_number":113,"symbol":"Nh", "name":"Nihonium",    "atomic_mass":286.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p1","electronegativity":None,"oxidation_states":[],   "group":13,"period":7,"block":"p","melting_point_k":700.0,"boiling_point_k":1400.0,"density_gcm3":16.0,"atomic_radius_pm":None,"ionization_energy_ev":7.310},
    "Fl": {"atomic_number":114,"symbol":"Fl", "name":"Flerovium",   "atomic_mass":289.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p2","electronegativity":None,"oxidation_states":[], "group":14,"period":7,"block":"p","melting_point_k":340.0,"boiling_point_k":420.0,"density_gcm3":14.0,"atomic_radius_pm":None,"ionization_energy_ev":8.450},
    "Mc": {"atomic_number":115,"symbol":"Mc", "name":"Moscovium",   "atomic_mass":290.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p3","electronegativity":None,"oxidation_states":[], "group":15,"period":7,"block":"p","melting_point_k":670.0,"boiling_point_k":1400.0,"density_gcm3":13.5,"atomic_radius_pm":None,"ionization_energy_ev":5.580},
    "Lv": {"atomic_number":116,"symbol":"Lv", "name":"Livermorium", "atomic_mass":293.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p4","electronegativity":None,"oxidation_states":[], "group":16,"period":7,"block":"p","melting_point_k":637.0,"boiling_point_k":1035.0,"density_gcm3":12.9,"atomic_radius_pm":None,"ionization_energy_ev":6.470},
    "Ts": {"atomic_number":117,"symbol":"Ts", "name":"Tennessine",  "atomic_mass":294.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p5","electronegativity":None,"oxidation_states":[], "group":17,"period":7,"block":"p","melting_point_k":623.0,"boiling_point_k":883.0,"density_gcm3":7.1,"atomic_radius_pm":None,"ionization_energy_ev":7.230},
    "Og": {"atomic_number":118,"symbol":"Og", "name":"Oganesson",   "atomic_mass":294.0,     "electron_configuration":"[Rn]5f14 6d10 7s2 7p6","electronegativity":None,"oxidation_states":[0], "group":18,"period":7,"block":"p","melting_point_k":325.0,"boiling_point_k":450.0,"density_gcm3":5.0,"atomic_radius_pm":None,"ionization_energy_ev":8.630},
}


LIGANDS: dict[str, dict[str, Any]] = {
    "CO": {
        "name": "Carbonyl",
        "formula": "CO",
        "donor_atom": "C",
        "field_strength": "strong",
        "delta_o_relative": 1.7,
        "denticity": "monodentate",
        "pi_bonding": "strong pi-acceptor (back-bonding from metal d-orbitals to CO pi* orbitals)",
        "spectrochemical_position": "highest; causes largest d-orbital splitting",
        "typical_geometry": "octahedral with d6 low-spin; stabilises low oxidation states",
    },
    "CN-": {
        "name": "Cyanide",
        "formula": "CN-",
        "donor_atom": "C",
        "field_strength": "strong",
        "delta_o_relative": 1.6,
        "denticity": "monodentate",
        "pi_bonding": "pi-acceptor",
        "spectrochemical_position": "just below CO",
    },
    "NO2-": {
        "name": "Nitro (N-bound)",
        "formula": "NO2-",
        "donor_atom": "N",
        "field_strength": "strong",
        "delta_o_relative": 1.45,
        "denticity": "monodentate",
        "pi_bonding": "pi-acceptor (N-bound); nitrito isomer (O-bound) is weaker field",
    },
    "en": {
        "name": "Ethylenediamine",
        "formula": "H2NCH2CH2NH2",
        "donor_atom": "N",
        "field_strength": "strong",
        "delta_o_relative": 1.25,
        "denticity": "bidentate",
        "chelate_effect": "five-membered chelate ring; entropic stabilisation",
    },
    "NH3": {
        "name": "Ammine",
        "formula": "NH3",
        "donor_atom": "N",
        "field_strength": "intermediate",
        "delta_o_relative": 1.00,
        "denticity": "monodentate",
        "pi_bonding": "sigma-donor only; no pi-acceptor or pi-donor capability",
        "typical_geometries": ["octahedral: [Co(NH3)6]3+", "square planar: [Pt(NH3)4]2+", "tetrahedral: [Zn(NH3)4]2+"],
    },
    "H2O": {
        "name": "Aqua",
        "formula": "H2O",
        "donor_atom": "O",
        "field_strength": "intermediate",
        "delta_o_relative": 0.80,
        "denticity": "monodentate",
        "pi_bonding": "weak pi-donor",
        "typical_geometries": ["octahedral: [Fe(H2O)6]2+ (high-spin)", "octahedral: [Co(H2O)6]3+ (low-spin)"],
    },
    "OH-": {
        "name": "Hydroxo",
        "formula": "OH-",
        "donor_atom": "O",
        "field_strength": "intermediate",
        "delta_o_relative": 0.75,
        "denticity": "monodentate",
        "pi_bonding": "pi-donor",
    },
    "F-": {
        "name": "Fluorido",
        "formula": "F-",
        "donor_atom": "F",
        "field_strength": "intermediate",
        "delta_o_relative": 0.70,
        "denticity": "monodentate",
        "pi_bonding": "pi-donor (small ion, good overlap)",
    },
    "Cl-": {
        "name": "Chlorido",
        "formula": "Cl-",
        "donor_atom": "Cl",
        "field_strength": "weak",
        "delta_o_relative": 0.55,
        "denticity": "monodentate",
        "pi_bonding": "pi-donor",
        "typical_geometries": ["tetrahedral: [CoCl4]2- (blue)", "octahedral: [CoCl6]3- (high-spin)"],
    },
    "Br-": {
        "name": "Bromido",
        "formula": "Br-",
        "donor_atom": "Br",
        "field_strength": "weak",
        "delta_o_relative": 0.45,
        "denticity": "monodentate",
        "pi_bonding": "pi-donor",
    },
    "I-": {
        "name": "Iodido",
        "formula": "I-",
        "donor_atom": "I",
        "field_strength": "weak",
        "delta_o_relative": 0.35,
        "denticity": "monodentate",
        "pi_bonding": "pi-donor (soft base; prefers soft metal centres)",
    },
}


CRYSTAL_FIELD_SPLITTING: dict[str, dict[str, Any]] = {
    "octahedral": {
        "orbitals": "d-orbitals split into t2g (dxy, dxz, dyz) lower energy and eg (dz2, dx2-y2) higher energy",
        "splitting_pattern": {"t2g": "lower energy (-0.4 Delta_o each)", "eg": "higher energy (+0.6 Delta_o each)"},
        "delta_notation": "Delta_o (octahedral splitting energy)",
        "electronic_configurations": {
            "d1-d3": "fill t2g first (Hund's rule)",
            "d4-d7": "low-spin if Delta_o > pairing energy P; high-spin if P > Delta_o",
            "d8-d10": "fill eg after t2g",
        },
        "geometry_description": "six ligands at vertices of octahedron; metal at centre",
        "cfse_formula": "CFSE = (-0.4 * n_t2g + 0.6 * n_eg) * Delta_o + pairing corrections",
    },
    "tetrahedral": {
        "orbitals": "d-orbitals split into e (dz2, dx2-y2) lower energy and t2 (dxy, dxz, dyz) higher energy; inverted vs octahedral",
        "splitting_pattern": {"e": "lower energy (-0.6 Delta_t each)", "t2": "higher energy (+0.4 Delta_t each)"},
        "delta_notation": "Delta_t (tetrahedral splitting energy); Delta_t ≈ 4/9 Delta_o for same metal-ligand pair",
        "electronic_configurations": {
            "all": "Always high-spin; Delta_t is too small to overcome pairing energy in all known cases",
        },
        "geometry_description": "four ligands at alternating corners of cube; metal at centre",
        "cfse_formula": "CFSE = (-0.6 * n_e + 0.4 * n_t2) * Delta_t",
    },
    "square_planar": {
        "orbitals": "d-orbitals split into four levels: dx2-y2 >> dxy > dz2 > dxz,dyz (degenerate); derived from extreme Jahn-Teller elongation of octahedron removing z-axis ligands",
        "splitting_pattern": {
            "dx2-y2": "highest energy (strongly antibonding, points at all four ligands)",
            "dxy": "second highest",
            "dz2": "third (no z-axis ligands stabilises)",
            "dxz, dyz": "lowest (degenerate pair; non-bonding in xy plane)",
        },
        "delta_notation": "splitting ~1.7 Delta_o for same metal-ligand; large enough for diamagnetic d8 (Ni2+, Pd2+, Pt2+, Au3+)",
        "typical_metals": "d8 (Ni2+, Pd2+, Pt2+, Au3+), d9 (Cu2+ Jahn-Teller distorted), d7 (Co2+ in vitamin B12 models)",
    },
}


SOLID_STATE_DEFECTS: dict[str, dict[str, Any]] = {
    "Schottky": {
        "description": "Pair of cation and anion vacancies maintaining stoichiometry. Both ions migrate to surface leaving vacant lattice sites. Common in ionic solids with similar cation/anion sizes (NaCl, KCl, CsCl, MgO).",
        "type": "point defect; stoichiometric vacancy pair",
        "effect_on_density": "decreases density (mass lost, volume constant)",
        "formation_energy": "2-3 eV for alkali halides",
        "temperature_dependence": "concentration increases exponentially with T: n/N ∝ exp(-E_f/2kT)",
    },
    "Frenkel": {
        "description": "Cation (or anion) displaced from lattice site to interstitial position, creating a vacancy-interstitial pair. Does NOT change stoichiometry. Common when cation is much smaller than anion (AgCl, AgBr, CaF2 — anti-Frenkel with F- interstitials).",
        "type": "point defect; interstitial-vacancy pair",
        "effect_on_density": "no change (mass and volume constant)",
        "formation_energy": "3-6 eV; lower for Ag halides (~1.1 eV for AgCl)",
        "temperature_dependence": "concentration increases exponentially: n/N ∝ exp(-E_f/2kT)",
    },
    "F-center": {
        "description": "Anion vacancy occupied by a trapped electron. The electron is in a potential well and has quantised energy levels like a particle-in-a-box. Responsible for colour in alkali halides (NaCl → yellow-brown after X-ray irradiation; KCl → violet). The F stands for Farbe (German for colour).",
        "type": "point defect; colour centre; electron trapped at anion vacancy",
        "formation": "heating alkali halide in alkali metal vapour (excess metal) or irradiation; F + F → M-centre (paired vacancies); F-aggregates produce different colours",
        "applications": "dosimeters (LiF F-centres for radiation dosimetry); laser gain media; photochromic materials",
    },
    "edge_dislocation": {
        "description": "Extra half-plane of atoms inserted into the crystal lattice, terminating at a dislocation line. The Burgers vector b is perpendicular to the dislocation line. Atoms near the core are compressed (above) and stretched (below).",
        "type": "line defect; edge dislocation",
        "burgers_vector": "perpendicular to dislocation line; magnitude = one lattice spacing",
        "mobility": "moves by glide (conservative) along slip plane; climb (non-conservative) requires vacancy diffusion; dislocation motion explains why metals are ductile (yield stress << theoretical shear strength)",
        "observation": "TEM (transmission electron microscopy); etch pit method",
    },
    "screw_dislocation": {
        "description": "Spiral ramp defect where atoms are displaced parallel to the dislocation line rather than perpendicular. The crystal is sheared by one lattice vector. Burgers vector b is parallel to the dislocation line.",
        "type": "line defect; screw dislocation",
        "burgers_vector": "parallel to dislocation line",
        "mobility": "moves by cross-slip between crystallographic planes; no climb needed; highly mobile in bcc metals",
        "role_in_crystal_growth": "screw dislocation provides perpetual step for crystal growth at low supersaturation (Frank-Read source explanation for spiral growth patterns on crystal faces)",
    },
}


PHASE_DIAGRAMS: dict[str, dict[str, Any]] = {
    "Fe-C": {
        "components": ["Fe", "C"],
        "name": "Iron-Carbon (metastable Fe-Fe3C)",
        "eutectic_composition": 4.30,
        "eutectic_temperature_c": 1147,
        "eutectoid_composition": 0.76,
        "eutectoid_temperature_c": 727,
        "peritectic": {"composition": 0.16, "temperature_c": 1493},
        "key_phases": {
            "austenite": "gamma-Fe; fcc; max 2.14 wt% C at 1147°C; non-magnetic; ductile; stable >727°C",
            "ferrite": "alpha-Fe; bcc; max 0.022 wt% C at 727°C; magnetic below 770°C; soft and ductile",
            "cementite": "Fe3C; 6.67 wt% C; hard and brittle; orthorhombic",
            "pearlite": "lamellar mixture of ferrite + cementite; forms at eutectoid; 0.76 wt% C",
            "ledeburite": "eutectic mixture of austenite + cementite; forms at 1147°C; exists in all cast irons",
        },
        "applications": "steel (0.008-2.14 wt% C); cast iron (2.14-6.67 wt% C); basis of all ferrous metallurgy",
    },
    "Cu-Ni": {
        "components": ["Cu", "Ni"],
        "name": "Copper-Nickel (isomorphous)",
        "eutectic_composition": None,
        "eutectic_temperature_c": None,
        "solidus_liquidus": "complete solid solubility; fcc solid solution across entire composition range",
        "key_phases": {
            "liquid": "single liquid phase above liquidus",
            "alpha": "fcc solid solution (Cu,Ni); complete miscibility; Hume-Rothery rules satisfied (same crystal structure fcc, similar atomic radii delta=2.5%, same valency)",
        },
        "applications": "cupronickel alloys (Cu-30Ni for marine condensers, Cu-25Ni coinage); Monel (Ni-30Cu for chemical processing equipment)",
        "lever_rule": "applicable in two-phase (liquid + alpha) region for determining phase fractions",
    },
    "Pb-Sn": {
        "components": ["Pb", "Sn"],
        "name": "Lead-Tin (simple eutectic)",
        "eutectic_composition": 61.9,
        "eutectic_temperature_c": 183,
        "eutectic_phases": "alpha (Pb-rich fcc) + beta (Sn-rich bct)",
        "key_phases": {
            "alpha": "Pb-rich fcc solid solution; max ~19% Sn at 183°C; soft; corrosion resistant",
            "beta": "Sn-rich body-centred tetragonal; max ~2.5% Pb at 183°C; forms as needles in eutectic",
        },
        "applications": "solder alloys (Sn-37Pb eutectic composition: sharp melting point, good wetting, low cost); phase diagram is the textbook simple eutectic example",
    },
    "Al2O3-SiO2": {
        "components": ["Al2O3", "SiO2"],
        "name": "Alumina-Silica (ceramic system)",
        "eutectic_composition": "~7.7 wt% Al2O3 (metastable: cristobalite + mullite)",
        "eutectic_temperature_c": 1587,
        "peritectic": "mullite melts incongruently at ~1828°C",
        "key_phases": {
            "mullite": "3Al2O3·2SiO2 to 2Al2O3·SiO2 solid solution range; only stable intermediate compound; high creep resistance; major phase in traditional ceramics and refractories",
            "cristobalite": "SiO2 polymorph stable 1470-1713°C",
            "corundum": "alpha-Al2O3; melts at 2054°C; second hardest natural material (9 Mohs)",
        },
        "applications": "refractory bricks (fireclay = 25-45% Al2O3); porcelain (kaolin-based); high-alumina ceramics",
    },
}


_INORGANIC_REACTIONS: dict[str, dict[str, Any]] = {
    "Fe2O3_reduction": {
        "reactants": ["Fe2O3 (hematite)", "CO (carbon monoxide) or C (coke)"],
        "products": ["Fe (molten iron)", "CO2 (carbon dioxide)"],
        "equation": "Fe2O3 + 3CO → 2Fe + 3CO2 (blast furnace at ~1500°C)",
        "conditions": "high temperature (800-2000°C in blast furnace); limestone flux (CaCO3) removes silica impurities as slag (CaSiO3)",
        "type": "reduction; pyrometallurgy",
        "industrial_process": "blast furnace ironmaking; 95% of all metal tonnage produced",
    },
    "Haber_Bosch": {
        "reactants": ["N2 (nitrogen from air)", "H2 (hydrogen from steam reforming of methane)"],
        "products": ["NH3 (ammonia)"],
        "equation": "N2 + 3H2 ⇌ 2NH3 (ΔH = -92.4 kJ/mol)",
        "conditions": "200-400 atm, 400-500°C; Fe3O4/K2O/Al2O3 catalyst; equilibrium limited but high pressure favours forward reaction (Le Chatelier: fewer moles of gas on product side)",
        "type": "heterogeneous catalysis; exothermic equilibrium reaction",
        "significance": "produces ~180 million tonnes/year NH3; 80% used for fertiliser; feeds ~40% of world population (nitrogen fixation)",
    },
    "Ostwald": {
        "reactants": ["NH3 (ammonia)", "O2 (oxygen)"],
        "products": ["HNO3 (nitric acid)"],
        "equation": "4NH3 + 5O2 → 4NO + 6H2O (Pt/Rh catalyst, 900°C); 2NO + O2 → 2NO2; 3NO2 + H2O → 2HNO3 + NO",
        "conditions": "Pt-Rh gauze catalyst at 850-950°C; rapid contact time (~1 ms) to avoid NH3 decomposition; subsequent absorption in water",
        "type": "catalytic oxidation; three-step process",
        "significance": "primary source of HNO3 for fertiliser (ammonium nitrate), explosives (TNT, nitroglycerin), and nylon precursors",
    },
    "Contact": {
        "reactants": ["SO2 (sulfur dioxide)", "O2 (oxygen)"],
        "products": ["H2SO4 (sulfuric acid)"],
        "equation": "2SO2 + O2 ⇌ 2SO3 (V2O5/K2O catalyst, 400-450°C); SO3 + H2SO4 → H2S2O7 (oleum); H2S2O7 + H2O → 2H2SO4",
        "conditions": "1-2 atm, 400-450°C; V2O5 catalyst (vanadium pentoxide on silica); SO3 absorbed in 98% H2SO4 (not water — would form stable acid mist)",
        "type": "heterogeneous catalysis; exothermic equilibrium",
        "significance": "most-produced chemical worldwide (~260 million tonnes/year); used in phosphate fertiliser, petroleum refining, metal processing",
    },
    "Hall_Heroult": {
        "reactants": ["Al2O3 (alumina, dissolved in molten cryolite Na3AlF6)", "C (graphite anodes)"],
        "products": ["Al (molten aluminium)", "CO2 (at anode)"],
        "equation": "2Al2O3 + 3C → 4Al + 3CO2 (electrolysis at ~960°C)",
        "conditions": "molten cryolite electrolyte (Na3AlF6 + AlF3 + CaF2) at 940-980°C; 4-5 V per cell, ~150 kA current; carbon anode consumed (0.4-0.5 kg C per kg Al); cell life 5-8 years",
        "type": "electrolysis; electrometallurgy",
        "significance": "only commercial process for aluminium production since 1886; ~65 million tonnes/year; extremely energy-intensive (~13-16 kWh/kg Al)",
    },
    "Chlor_Alkali": {
        "reactants": ["NaCl (brine)", "H2O"],
        "products": ["Cl2 (chlorine gas at anode)", "H2 (hydrogen gas at cathode)", "NaOH (sodium hydroxide in catholyte)"],
        "equation": "2NaCl + 2H2O → Cl2 + H2 + 2NaOH (membrane cell with Nafion cation-exchange membrane)",
        "conditions": "membrane cell: Nafion perfluorinated membrane separates anode and cathode compartments; brine fed to anode; 3.0-3.5 V; ~90-95°C; mercury and diaphragm cells being phased out for environmental reasons",
        "type": "electrolysis; electrochemical",
        "significance": "produces three commodity chemicals simultaneously; Cl2 for PVC and water treatment; NaOH for alumina refining and soap; H2 for Haber process and hydrogenation",
    },
}
REACTIONS = _INORGANIC_REACTIONS


def get_element_data(symbol: str) -> dict[str, Any] | None:
    """Return full element data for a given symbol (case-sensitive)."""
    return PERIODIC_TABLE.get(symbol)


_LIGAND_STRENGTH: dict[str, tuple[str, float]] = {
    ligand: (data["field_strength"], data["delta_o_relative"])
    for ligand, data in LIGANDS.items()
}


def compute_crystal_field_splitting(
    metal: str, ligand: str, geometry: str
) -> dict[str, Any] | None:
    """Compute approximate crystal-field splitting parameters.

    ``metal`` should be a string like ``"Fe2+"`` or ``"Ti3+"``.
    ``ligand`` is the ligand formula (e.g. ``"CO"``, ``"H2O"``).
    ``geometry`` must be one of ``"octahedral"``, ``"tetrahedral"``, or
    ``"square_planar"``.

    Returns a dict with keys ``delta_o_cm``, ``spin_state``, and
    ``splitting_diagram``, or ``None`` when inputs are unrecognised.
    """
    geometry = geometry.lower().strip()
    if geometry not in ("octahedral", "tetrahedral", "square_planar"):
        return None

    ligand_data = _LIGAND_STRENGTH.get(ligand)
    if ligand_data is None:
        return None

    metal_ion = metal.strip()
    metal_symbol = ""
    for ch in metal_ion:
        if ch.isalpha():
            metal_symbol += ch
        else:
            break
    if metal_symbol not in PERIODIC_TABLE:
        return None

    field_strength, delta_relative = ligand_data

    base_delta = {
        "octahedral": 1.0,
        "tetrahedral": 0.44,  # 4/9 of octahedral
        "square_planar": 1.7,
    }.get(geometry, 1.0)

    delta_o = int(10000 * base_delta * delta_relative)

    metal_ion = metal.strip()
    spin_state = "low-spin" if field_strength == "strong" else "high-spin"
    if geometry == "tetrahedral":
        spin_state = "high-spin (Delta_t too small for spin pairing in all known cases)"
    elif geometry == "square_planar" and field_strength == "strong":
        spin_state = "low-spin d8 diamagnetic"

    splitting_diagram = CRYSTAL_FIELD_SPLITTING.get(geometry, {}).get("splitting_pattern", {})

    return {
        "delta_o_cm": delta_o,
        "delta_relative": delta_relative,
        "spin_state": spin_state,
        "splitting_diagram": splitting_diagram,
        "geometry": geometry,
        "metal": metal_ion,
        "ligand": ligand,
    }


def get_reaction(product: str) -> dict[str, Any] | None:
    """Return data for a named inorganic reaction by product or process name."""
    return REACTIONS.get(product)
