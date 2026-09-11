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
A collection declares ``providers`` in ``providers.yaml``; every provider
source is intersected with the operator trust list — anything outside it is an
import error. Provider metadata stays outside ``galaxy.yml`` so that standard
Ansible Galaxy tooling accepts the collection schema without warnings.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from general_ludd.collections.terraform_metadata import (
    is_floating_version as _is_floating_version,
)
from general_ludd.collections.terraform_metadata import (
    iter_provider_entries as _iter_provider_entries,
)
from general_ludd.collections.terraform_metadata import (
    parse_required_providers as _parse_required_providers,
)
from general_ludd.collections.terraform_metadata import (
    parse_tfvars_keys as _parse_tfvars_keys,
)
from general_ludd.collections.terraform_metadata import (
    parse_variable_names as _parse_variable_names,
)

__all__ = ("_iter_provider_entries",)

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
        issues.extend(self._tfvars_schema_check())
        issues.extend(self._provider_pin_check())
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
        has_deny_reassignment = False
        for rego in rego_files:
            source = rego.read_text(encoding="utf-8")
            if _DENY_REASSIGN_RE.search(source):
                has_deny_reassignment = True
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

        # A policy that attempts to reassign the protected deny set is rejected
        # before OPA parsing; avoid reporting a secondary parser diagnostic for
        # the same invalid policy as a separate import issue.
        if rego_files and not has_deny_reassignment:
            issues.extend(self._run_opa_check(policies_dir))
        return issues

    def _check_provider_trust(self) -> list[ImportIssue]:
        trust_list = self._load_operator_trust_list()
        issues: list[ImportIssue] = []

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
        sources: list[str] = []
        for provider in providers:
            if isinstance(provider, str):
                sources.append(provider)
                continue
            if isinstance(provider, dict):
                source = provider.get("source")
                if isinstance(source, str) and source:
                    sources.append(source)
                    continue
            # Preserve fail-closed trust behavior for malformed entries: their
            # rendered value cannot match a valid operator provider address.
            sources.append(str(provider))
        return sources

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

    # ------------------------------------------------------------------
    # tfvars schema + provider pinning checks
    # ------------------------------------------------------------------

    def _tfvars_schema_check(self) -> list[ImportIssue]:
        """Warn when a stack's variables.tf declares vars missing from *.tfvars.example.

        For every stack under ``plugins/terraform/stacks/<name>/`` that ships a
        ``*.tfvars.example`` file, parse the declared ``variable "x" {}`` blocks
        out of ``variables.tf`` and the assigned ``key = value`` lines out of
        the example. Any declared variable with no example value is a warning.
        """
        issues: list[ImportIssue] = []
        stacks_root = self.collection_path / "plugins" / "terraform" / "stacks"
        for stack_dir in _iter_child_dirs(stacks_root):
            example = _find_first(stack_dir.glob("*.tfvars.example"))
            if example is None:
                continue
            variables_tf = stack_dir / "variables.tf"
            if not variables_tf.is_file():
                continue
            declared = _parse_variable_names(variables_tf.read_text(encoding="utf-8"))
            if not declared:
                continue
            examples = _parse_tfvars_keys(example.read_text(encoding="utf-8"))
            rel = str(stack_dir.relative_to(self.collection_path))
            for name in sorted(declared):
                if name not in examples:
                    issues.append(
                        ImportIssue(
                            severity="warn",
                            message=(
                                f"{rel}: variable {name!r} declared in variables.tf "
                                f"has no example value in {example.name}"
                            ),
                        )
                    )
        return issues

    def _provider_pin_check(self) -> list[ImportIssue]:
        """Warn when a required_providers block uses a floating ``>=`` version.

        Each ``required_providers`` block should pin its version (``~>`` or
        ``=``). A floating ``>=`` constraint is permitted by Terraform but
        invites unplanned provider upgrades that can silently change semantics.
        """
        issues: list[ImportIssue] = []
        tf_root = self.collection_path / "plugins" / "terraform"
        for tf_file in sorted(tf_root.rglob("*.tf")):
            try:
                text = tf_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for provider, version in _parse_required_providers(text).items():
                if _is_floating_version(version):
                    rel = str(tf_file.relative_to(self.collection_path))
                    issues.append(
                        ImportIssue(
                            severity="warn",
                            message=(
                                f"{rel}: provider {provider!r} uses floating version "
                                f"constraint {version!r}; pin with '~>' or '='"
                            ),
                        )
                    )
        return issues


def _iter_child_dirs(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(p for p in parent.iterdir() if p.is_dir())


def _find_first(iterator: Iterator[Path]) -> Path | None:
    for p in iterator:
        return p
    return None


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
