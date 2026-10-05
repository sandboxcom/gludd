"""Source-level contract helpers for lean TypeScript plugin facades.

Runtime hooks may live in ``plugin/impl`` while the public plugin file remains
a small, auditable facade. Structural tests must inspect the complete contract
instead of encouraging marker padding in the facade.
"""

from dataclasses import dataclass
from pathlib import Path

from scripts.plugin_contract import plugin_contract_source as plugin_contract_source


@dataclass(frozen=True)
class PluginContractFile:
    """Path-like source reader for legacy structural test modules.

    Large test modules can adopt facade-aware reads without duplicating every
    assertion or pretending runtime implementation text lives in the facade.
    """

    path: Path

    def exists(self) -> bool:
        return self.path.exists()

    def read_text(self) -> str:
        return plugin_contract_source(self.path)
