"""General Ludd Agent - autonomous coding system with Ansible runners and multi-model AI agents."""

from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="fs",
)

__version__ = "0.1.0-beta.2"
