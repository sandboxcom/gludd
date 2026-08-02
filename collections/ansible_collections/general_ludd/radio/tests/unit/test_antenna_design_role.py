"""Tests for antenna_design role — validates task YAML, design logic, result shape."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_role_files_to_path():
    role_files = str(_COLLECTION_ROOT / "roles" / "antenna_design" / "files")
    if role_files not in sys.path:
        sys.path.insert(0, role_files)


def test_antenna_design_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "antenna_design" / "tasks" / "main.yml"
    assert tasks.exists()


def test_antenna_design_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "antenna_design" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "antenna_design_type" in content
    assert "antenna_design_freq_hz" in content
    assert "antenna_design_polarization" in content


def test_antenna_design_tasks_calls_python_script():
    tasks = _COLLECTION_ROOT / "roles" / "antenna_design" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "antenna_design.py" in content


def test_antenna_design_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "antenna_design" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "antenna_design_verdict" in content
    assert "role: antenna_design" in content
    assert "wavelength_m" in content
    assert "element_length_m" in content


def test_antenna_design_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "antenna_design" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert data["antenna_design_enabled"] is False
    assert data["antenna_design_type"] in ("dipole", "yagi", "loop", "patch", "discone")
    assert data["antenna_design_freq_hz"] > 0
    assert data["antenna_design_output_dir"]


def test_antenna_design_default_disabled():
    defaults = _COLLECTION_ROOT / "roles" / "antenna_design" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    assert data["antenna_design_enabled"] is False


def test_antenna_design_script_exists():
    script = _COLLECTION_ROOT / "roles" / "antenna_design" / "files" / "antenna_design.py"
    assert script.exists()
    content = script.read_text()
    assert "def design_dipole" in content
    assert "def design_yagi" in content
    assert "def design_loop" in content
    assert "def design_patch" in content
    assert "def design_discone" in content
    assert "SPEED_OF_LIGHT_MS" in content
    assert "DESIGNERS" in content


def test_velocity_factors_cover_materials():
    _add_role_files_to_path()
    from antenna_design import VELOCITY_FACTORS

    assert "copper" in VELOCITY_FACTORS
    assert "aluminum" in VELOCITY_FACTORS
    assert "pcb_fr4" in VELOCITY_FACTORS
    for mat, vf in VELOCITY_FACTORS.items():
        assert 0.0 < vf <= 1.0


def test_wire_gauge_table_has_common_sizes():
    _add_role_files_to_path()
    from antenna_design import WIRE_GAUGE_MM

    assert 14 in WIRE_GAUGE_MM
    assert 22 in WIRE_GAUGE_MM
    for gauge, diam in WIRE_GAUGE_MM.items():
        assert diam > 0


def test_design_dipole_2m_band():
    _add_role_files_to_path()
    from antenna_design import design_dipole

    result = design_dipole(freq_hz=146_000_000)
    d = result.to_dict() if hasattr(result, "to_dict") else result
    assert d["type"] == "dipole"
    assert d["freq_hz"] == 146_000_000
    assert 1.8 < d["wavelength_m"] < 2.2
    assert d["element_length_m"] > 0
    assert d["gain_dbi"] == pytest.approx(2.15, abs=0.5)
    assert d["polarization"] == "vertical"


def test_design_dipole_70cm_band():
    _add_role_files_to_path()
    from antenna_design import design_dipole

    result = design_dipole(freq_hz=440_000_000)
    d = result.to_dict() if hasattr(result, "to_dict") else result
    assert 0.6 < d["wavelength_m"] < 0.75
    assert d["element_length_m"] < d["wavelength_m"]


def test_design_yagi_has_elements():
    _add_role_files_to_path()
    from antenna_design import design_yagi

    result = design_yagi(freq_hz=146_000_000)
    assert result["type"] == "yagi"
    assert "elements" in result
    assert len(result["elements"]) >= 3
    element_names = [e["name"] for e in result["elements"]]
    assert "reflector" in element_names
    assert "driven_element" in element_names
    assert any("director" in n for n in element_names)
    assert result["gain_dbi"] > 5.0
    assert "boom_length_m" in result
    assert "radiation_pattern" in result


def test_design_yagi_reflector_longer_than_director():
    _add_role_files_to_path()
    from antenna_design import design_yagi

    result = design_yagi(freq_hz=440_000_000)
    reflector = [e for e in result["elements"] if e["name"] == "reflector"][0]
    director = [e for e in result["elements"] if "director" in e["name"]][0]
    assert reflector["length_m"] > director["length_m"]


def test_design_loop_has_circumference():
    _add_role_files_to_path()
    from antenna_design import design_loop

    result = design_loop(freq_hz=146_000_000)
    assert result["type"] == "loop"
    assert "loop_diameter_m" in result
    assert "circumference_m" in result
    assert result["circumference_m"] > 0
    assert result["loop_diameter_m"] > 0
    assert "matching" in result
    assert "radiation_pattern" in result


def test_design_patch_has_substrate():
    _add_role_files_to_path()
    from antenna_design import design_patch

    result = design_patch(freq_hz=2_400_000_000)
    assert result["type"] == "patch"
    assert "substrate" in result
    assert result["substrate"]["material"] == "FR-4"
    assert result["patch_width_mm"] > 0
    assert result["patch_length_mm"] > 0
    assert "feed_inset_mm" in result
    assert "radiation_pattern" in result


def test_design_discone_has_cone_geometry():
    _add_role_files_to_path()
    from antenna_design import design_discone

    result = design_discone(freq_hz=146_000_000)
    assert result["type"] == "discone"
    assert "disc_diameter_mm" in result
    assert "cone_height_mm" in result
    assert "cone_base_diameter_mm" in result
    assert "cone_angle_deg" in result
    assert result["disc_diameter_mm"] > 0
    assert result["cone_height_mm"] > 0
    assert "radiation_pattern" in result


def test_all_designers_covered_in_dispatch():
    _add_role_files_to_path()
    from antenna_design import DESIGNERS

    assert set(DESIGNERS.keys()) == {"dipole", "yagi", "loop", "patch", "discone"}


def test_antenna_dimensions_dataclass_to_dict():
    _add_role_files_to_path()
    from antenna_design import AntennaDimensions

    ad = AntennaDimensions(
        type="dipole",
        freq_hz=146_000_000,
        wavelength_m=2.05,
        half_wavelength_m=1.025,
        quarter_wavelength_m=0.5125,
    )
    d = ad.to_dict()
    assert d["type"] == "dipole"
    assert d["freq_mhz"] == pytest.approx(146.0, abs=0.1)
    assert d["wavelength_m"] == 2.05
    assert "element_length_in" in d
    assert "bandwidth_pct" in d


def test_antenna_design_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "antenna_design" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "antenna_design"
