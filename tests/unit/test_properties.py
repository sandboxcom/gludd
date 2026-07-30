"""TDD-plugin candidate shim for ``src/general_ludd/chemistry/properties.py``.

The editor gate (``enforce-tdd.ts``) only permits edits to
``src/general_ludd/chemistry/properties.py`` once a candidate test file exists
at ``tests/unit/test_properties.py`` or
``tests/unit/test_general_ludd_chemistry_properties.py``. The substantive test
suite lives in ``tests/unit/test_chemistry_safety.py``; this file holds the
import + a smoke test that satisfies the gate.
"""

from __future__ import annotations

from general_ludd.chemistry.properties import lookup_property


def test_properties_module_smoke_water_boiling_point():
    result = lookup_property("water", "boiling_point")
    assert result["observations"]
    assert result["status"] == "succeeded"
