"""Spot/preemptible instance config validator.

Checks that compute resource spot/preemptible configuration in Terraform
stacks matches the operator's configured preferences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = __import__("logging").getLogger(__name__)


@dataclass
class SpotValidatorFinding:
    stack_name: str
    severity: str  # "warning" or "ok"
    message: str
    use_spot_configured: bool
    use_spot_expected: bool


class SpotConfigValidator:
    """Validate that Terraform stacks match the operator's spot/preemptible preference.

    Reads each stack's terraform variables and compute resource blocks, comparing
    the ``use_spot`` default against the configured ``default_spot`` setting.
    """

    def __init__(self, default_spot: bool = True) -> None:
        self.default_spot = default_spot

    def validate(
        self, stack_name: str, stacks_dir: str | None = None
    ) -> list[SpotValidatorFinding]:
        findings: list[SpotValidatorFinding] = []
        stacks_path = Path(stacks_dir) if stacks_dir else Path("infra/terraform/stacks")
        stack_dir = stacks_path / stack_name

        if not stack_dir.is_dir():
            findings.append(
                SpotValidatorFinding(
                    stack_name=stack_name,
                    severity="warning",
                    message=f"Stack directory not found: {stack_dir}",
                    use_spot_configured=False,
                    use_spot_expected=self.default_spot,
                )
            )
            return findings

        configured = self._read_use_spot_default(stack_dir)
        match = configured == self.default_spot

        findings.append(
            SpotValidatorFinding(
                stack_name=stack_name,
                severity="ok" if match else "warning",
                message=(
                    f"use_spot={configured} matches expected={self.default_spot}"
                    if match
                    else f"use_spot={configured} does not match expected={self.default_spot}"
                ),
                use_spot_configured=configured,
                use_spot_expected=self.default_spot,
            )
        )

        return findings

    @staticmethod
    def _read_use_spot_default(stack_dir: Path) -> bool:
        variables_tf = stack_dir / "variables.tf"
        if not variables_tf.exists():
            return False
        text = variables_tf.read_text()
        m = re.search(
            r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL
        )
        if m is None:
            return False
        block = m.group(1)
        return bool(re.search(r'default\s*=\s*true', block))
