"""Compatibility wrapper for particle experiment helpers."""
from __future__ import annotations

from .particle_experiments import (
    ACCELERATORS,
    DETECTORS,
    SKY_SURVEYS,
    ParticleConfig,
    analyze_decay_chain,
    compute_cross_section,
    get_detector_capabilities,
    get_experiment_info,
    get_running_status,
    get_survey_data_release,
    list_accelerators_by_status,
    list_accelerators_by_type,
    list_detectors_by_collider,
    list_sky_surveys_by_coverage,
    search_sky_survey,
    write_particle_result,
)

__all__ = [
    "ACCELERATORS",
    "DETECTORS",
    "SKY_SURVEYS",
    "ParticleConfig",
    "analyze_decay_chain",
    "compute_cross_section",
    "get_detector_capabilities",
    "get_experiment_info",
    "get_running_status",
    "get_survey_data_release",
    "list_accelerators_by_status",
    "list_accelerators_by_type",
    "list_detectors_by_collider",
    "list_sky_surveys_by_coverage",
    "search_sky_survey",
    "write_particle_result",
]
