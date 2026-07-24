"""General Ludd Agent - autonomous coding system with Ansible runners and multi-model AI agents."""

from __future__ import annotations

import warnings

from general_ludd.compat.annotated_types import apply_annotated_types_runtime_patch

apply_annotated_types_runtime_patch()

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="fs",
)

__version__ = "0.1.0-beta.1"
