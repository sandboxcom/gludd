"""Collection importer for gludd terraform + OPA content.

Validates a user-submitted ansible-galaxy collection at IMPORT time. The
importer is intentionally side-effect free aside from read-only subprocess
calls (``terraform validate``, ``opa check``); it produces a list of
:class:`ImportIssue` records. An empty list means the collection passed all
checks.

Layout expected under the collection root:
  * ``galaxy.yml``                              — required ansible-galaxy metadata
  * ``plugins/terraform/modules/<name>/*.tf``   — user terraform modules
  * ``plugins/terraform/stacks/<name>/*.tf``    — user terraform stacks
  * ``plugins/terraform/policies/*.rego``       — additive OPA deny rules
  * ``plugins/terraform/providers.yaml``        — declared provider dependencies

Provider trust is anchored by the operator's trust list at
``infra/terraform/policies/data.json`` (``data.gludd.provider_trust_list``).
A collection may declare ``terraform_provider_trust`` in ``galaxy.yml`` and
``providers`` in ``providers.yaml``; both are intersected with the operator
trust list — anything outside it is an import error.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_DENY_REASSIGN_RE = re.compile(r"deny\s*[-+]?=")


@dataclass(frozen=True, slots=True)
class ImportIssue:
    severity: str
    message: str


class TerraformCollectionImporter:
    def __init__(
        self,
        collection_path: Path,
        operator_trust_data_path: Path = Path("infra/terraform/policies/data.json"),
    ) -> None:
        self.collection_path = collection_path
        self.operator_trust_data_path = operator_trust_data_path

    def import_collection(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        issues.extend(self._validate_terraform_dirs())
        issues.extend(self._validate_rego_policies())
        issues.extend(self._check_provider_trust())
        return issues

    def _validate_terraform_dirs(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        tf_root = self.collection_path / "plugins" / "terraform"
        if not tf_root.is_dir():
            return [
                ImportIssue(
                    severity="warn",
                    message="plugins/terraform/ not present; skipping terraform validation",
                )
            ]

        for module_dir in _iter_child_dirs(tf_root / "modules"):
            issues.extend(self._run_terraform_validate(module_dir))
        for stack_dir in _iter_child_dirs(tf_root / "stacks"):
            issues.extend(self._run_terraform_validate(stack_dir))
        return issues

    def _validate_rego_policies(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        policies_dir = self.collection_path / "plugins" / "terraform" / "policies"
        if not policies_dir.is_dir():
            return issues

        rego_files = sorted(policies_dir.glob("*.rego"))
        for rego in rego_files:
            source = rego.read_text(encoding="utf-8")
            if _DENY_REASSIGN_RE.search(source):
                issues.append(
                    ImportIssue(
                        severity="error",
                        message=(
                            f"deny reassignment forbidden in {rego.relative_to(self.collection_path)}: "
                            "the core deny set is additive; remove any 'deny -=', "
                            "'deny +=', or 'deny =' assignment"
                        ),
                    )
                )

        if rego_files:
            issues.extend(self._run_opa_check(policies_dir))
        return issues

    def _check_provider_trust(self) -> list[ImportIssue]:
        trust_list = self._load_operator_trust_list()
        issues: list[ImportIssue] = []

        galaxy_trust = self._read_galaxy_provider_trust()
        for provider in galaxy_trust:
            if not _provider_in_trust_list(provider, trust_list):
                issues.append(
                    ImportIssue(
                        severity="error",
                        message=(
                            f"galaxy.yml terraform_provider_trust references untrusted "
                            f"provider {provider!r} (not in operator trust list)"
                        ),
                    )
                )

        providers_yaml = self._read_providers_yaml()
        for provider in providers_yaml:
            if not _provider_in_trust_list(provider, trust_list):
                issues.append(
                    ImportIssue(
                        severity="error",
                        message=(
                            f"plugins/terraform/providers.yaml references untrusted "
                            f"provider {provider!r} (not in operator trust list)"
                        ),
                    )
                )
        return issues

    def _load_operator_trust_list(self) -> list[str]:
        try:
            raw = json.loads(self.operator_trust_data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"could not read operator trust list at {self.operator_trust_data_path}: {exc}"
            ) from exc
        gludd = raw.get("gludd", {}) if isinstance(raw, dict) else {}
        entries = gludd.get("provider_trust_list", [])
        return [str(entry) for entry in entries]

    def _read_galaxy_metadata(self) -> dict[str, Any]:
        galaxy_yml = self.collection_path / "galaxy.yml"
        if not galaxy_yml.is_file():
            return {}
        try:
            data = yaml.safe_load(galaxy_yml.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}

    def _read_galaxy_provider_trust(self) -> list[str]:
        meta = self._read_galaxy_metadata()
        raw = meta.get("terraform_provider_trust", [])
        if not isinstance(raw, list):
            return []
        return [str(p) for p in raw]

    def _read_providers_yaml(self) -> list[str]:
        path = self.collection_path / "plugins" / "terraform" / "providers.yaml"
        if not path.is_file():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return []
        if not isinstance(data, dict):
            return []
        providers = data.get("providers", [])
        if not isinstance(providers, list):
            return []
        return [str(p) for p in providers]

    def _run_terraform_validate(self, module_dir: Path) -> list[ImportIssue]:
        return _run_optional_binary(
            binary="terraform",
            argv=["terraform", "validate"],
            cwd=module_dir,
            relabel=str(module_dir.relative_to(self.collection_path)),
        )

    def _run_opa_check(self, policies_dir: Path) -> list[ImportIssue]:
        rego_files = [str(p) for p in sorted(policies_dir.glob("*.rego"))]
        return _run_optional_binary(
            binary="opa",
            argv=["opa", "check", *rego_files],
            cwd=policies_dir,
            relabel=str(policies_dir.relative_to(self.collection_path)),
        )


def _iter_child_dirs(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(p for p in parent.iterdir() if p.is_dir())


def _provider_in_trust_list(provider: str, trust_list: list[str]) -> bool:
    if provider in trust_list:
        return True
    return any(entry.endswith(f"/{provider}") for entry in trust_list)


def _run_optional_binary(
    *,
    binary: str,
    argv: list[str],
    cwd: Path,
    relabel: str,
) -> list[ImportIssue]:
    if shutil.which(argv[0]) is None:
        return [
            ImportIssue(
                severity="warn",
                message=f"{binary} not installed; skipped validation of {relabel}",
            )
        ]
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return []
    return [
        ImportIssue(
            severity="error",
            message=(
                f"{binary} validation failed for {relabel} "
                f"(exit {proc.returncode}): {proc.stdout.strip()} {proc.stderr.strip()}"
            ).strip(),
        )
    ]
