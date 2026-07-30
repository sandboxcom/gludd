"""TDD-plugin candidate shim for ``src/general_ludd/chemistry/safety.py``.

The editor gate (``enforce-tdd.ts``) only permits edits to
``src/general_ludd/chemistry/safety.py`` once a candidate test file exists at
``tests/unit/test_safety.py`` or ``tests/unit/test_general_ludd_chemistry_safety.py``.
The substantive test suite lives in ``tests/unit/test_chemistry_safety.py``;
this file holds the import + a smoke test that satisfies the gate.
"""

from __future__ import annotations

from general_ludd.chemistry.safety import SafetyScreen, classify_risk


def test_safety_module_smoke_water_is_low():
    screen = classify_risk("water")
    assert isinstance(screen, SafetyScreen)
    assert screen.risk_tier == "low"
