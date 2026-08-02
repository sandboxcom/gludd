"""
cross_references -- Physics/math modules leveraged by the radio collection.

Propagation models and antenna design rely on electrodynamics and mathematical
identities from the physics collection. These imports provide access to the
underlying EM theory and computational math that radio models depend on.

Cross-collection imports:
    general_ludd.physics.plugins.module_utils.electrodynamics
        - antenna_gain(freq_hz, diameter_m, efficiency) for antenna design
        - compute_refraction(n1, n2, angle_deg) for atmospheric ducting
        - compute_polarization_state(type, angle_deg) for antenna polarization

    general_ludd.physics.plugins.module_utils.math_identities
        - dB/log conversions used across all path-loss models
        - series expansions for near-field calculations

    general_ludd.physics.plugins.module_utils.physical_constants
        - CODATA values (c, epsilon_0, mu_0) used in propagation models
"""

from __future__ import annotations

try:
    from ansible_collections.general_ludd.physics.plugins.module_utils.electrodynamics import (
        antenna_gain,
        compute_polarization_state,
        compute_refraction,
    )
    _HAS_PHYSICS_ELECTRODYNAMICS = True
except ImportError:
    _HAS_PHYSICS_ELECTRODYNAMICS = False

try:
    from ansible_collections.general_ludd.physics.plugins.module_utils.math_identities import (  # noqa: F401
        log_conversion,
    )
    _HAS_PHYSICS_MATH = True
except ImportError:
    _HAS_PHYSICS_MATH = False

try:
    from ansible_collections.general_ludd.physics.plugins.module_utils.physical_constants import (  # noqa: F401
        EPSILON_0,
        MU_0,
        C,
    )
    _HAS_PHYSICS_CONSTANTS = True
except ImportError:
    _HAS_PHYSICS_CONSTANTS = False


def physics_modules_available() -> dict[str, bool]:
    """Return which physics modules are importable."""
    return {
        "electrodynamics": _HAS_PHYSICS_ELECTRODYNAMICS,
        "math_identities": _HAS_PHYSICS_MATH,
        "physical_constants": _HAS_PHYSICS_CONSTANTS,
    }


__all__ = [
    "antenna_gain",
    "compute_polarization_state",
    "compute_refraction",
    "physics_modules_available",
]
