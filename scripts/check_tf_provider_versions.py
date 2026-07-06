#!/usr/bin/env python3
"""
check_tf_provider_versions.py

Gate script: asserts that every Terraform stack under ``infra/terraform/stacks``
pins its ``required_providers`` to the versions declared in the canonical
``infra/terraform/versions.tf`` contract.

Why this exists: Terraform resolves ``required_providers`` per-module (there is
no cross-module ``include``), so each stack repeats its own block. This script
keeps the repeated blocks in sync with the single source of truth so that the
shared plugin cache (``TF_PLUGIN_CACHE_DIR``) downloads exactly one version of
each third-party provider and every stack uses it.

Usage:
    python3 scripts/check_tf_provider_versions.py

Exit codes:
    0   Every stack's provider versions match the contract (or use providers
        not named in the contract — those are left to the trust-list gate).
    1   One or more stacks drift from the contract, or the contract is malformed.

Observable output:
    Always prints what it checked and any drift found.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Drift:
    stack: str
    provider: str
    detail: str

    def __str__(self) -> None:  # type: ignore[override]
        return f"{self.stack}: {self.provider} — {self.detail}"


# A provider entry is `name = { <attrs> }` with no braces inside <attrs> (the
# only nested braces in a required_providers block are the per-provider entries
# themselves, so [^{}] confines each match to exactly one brace level and makes
# the parse robust to nesting without a balanced-brace scanner).
_ENTRY_RE = re.compile(r'(?P<name>\w+)\s*=\s*\{(?P<attrs>[^{}]*?)\}', re.DOTALL)
_ATTR_RE = re.compile(r'(?P<key>\w+)\s*=\s*"(?P<val>[^"]*)"')


def _parse_required_providers(hcl: str) -> dict[str, tuple[str, str]]:
    """Return ``{source: (version, name)}`` for every provider declared in ``hcl``.

    Scans the whole file for ``name = { source = "...", version = "..." }``
    entries rather than first isolating the ``required_providers`` block — that
    avoids the nested-brace problem (the block contains one ``{}`` per provider).
    A missing ``version`` attribute is recorded as the empty string so callers
    can treat unpinned providers as drift.
    """
    out: dict[str, tuple[str, str]] = {}
    for entry in _ENTRY_RE.finditer(hcl):
        attrs = {m.group("key"): m.group("val") for m in _ATTR_RE.finditer(entry.group("attrs"))}
        source = attrs.get("source")
        if not source:
            continue
        out[source] = (attrs.get("version", ""), entry.group("name"))
    return out


def parse_versions_tf(path: Path) -> dict[str, str]:
    """Parse ``versions.tf`` into ``{source: version}``.

    ``versions.tf`` is the canonical contract: it pins one version per
    third-party provider so stacks stay aligned and the plugin cache holds a
    single copy of each.
    """
    raw = {src: ver for src, (ver, _name) in _parse_required_providers(path.read_text()).items()}
    missing = [src for src, ver in raw.items() if not ver]
    if missing:
        raise ValueError(
            f"versions.tf contract is missing version pin(s) for: {', '.join(missing)}"
        )
    return raw


def scan_stacks(stacks_dir: Path, contract: dict[str, str]) -> list[Drift]:
    """Compare every stack's ``required_providers`` against ``contract``.

    Only providers that appear in ``contract`` are checked. Providers absent
    from the contract are intentionally ignored here — they are policed by the
    separate provider trust-list gate (see ``infra/terraform/policies/``).
    """
    findings: list[Drift] = []
    for stack in sorted(d for d in stacks_dir.iterdir() if d.is_dir()):
        main_tf = stack / "main.tf"
        if not main_tf.exists():
            continue
        declared = _parse_required_providers(main_tf.read_text())
        for source, contract_version in contract.items():
            if source not in declared:
                continue
            stack_version, name = declared[source]
            if not stack_version:
                findings.append(
                    Drift(stack.name, source, f"provider '{name}' is missing a version pin")
                )
            elif stack_version != contract_version:
                findings.append(
                    Drift(
                        stack.name,
                        source,
                        f"pinned {stack_version}, contract says {contract_version}",
                    )
                )
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tf_root = repo_root / "infra" / "terraform"
    versions_tf = tf_root / "versions.tf"
    stacks_dir = tf_root / "stacks"

    if not versions_tf.exists():
        print(f"FAIL: canonical contract not found: {versions_tf}", file=sys.stderr)
        return 1
    if not stacks_dir.is_dir():
        print(f"FAIL: stacks directory not found: {stacks_dir}", file=sys.stderr)
        return 1

    try:
        contract = parse_versions_tf(versions_tf)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    findings = scan_stacks(stacks_dir, contract)
    print(f"Contract ({len(contract)} providers): {versions_tf.relative_to(repo_root)}")
    for src, ver in sorted(contract.items()):
        print(f"  {src:<32} {ver}")

    print(f"\nScanned {len([d for d in stacks_dir.iterdir() if d.is_dir()])} stack(s).")
    if findings:
        print(f"\nDRIFT — {len(findings)} stack provider(s) deviate from the contract:")
        for f in findings:
            print(f"  {f}")
        return 1
    print("OK — every stack's provider versions match the contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
