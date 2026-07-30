#!/usr/bin/env python3
"""Scaffold missing Ansible collection roles for the expert services.

Each missing role gets a minimal three-file layout that mirrors the existing
collection roles:
  - defaults/main.yml  -> empty dict (safe-by-default, opt-in at call site)
  - meta/main.yml      -> author / description / license metadata
  - tasks/main.yml     -> include_vars + debug message referencing the
                          owning service API endpoint

The script is idempotent: roles that already have a ``meta/main.yml`` are
skipped so existing (rich) roles are never clobbered. Pass ``--force`` to
overwrite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLLECTIONS = ROOT / "collections" / "ansible_collections" / "general_ludd"

ROLES: dict[str, list[str]] = {
    "materials": [
        "joining_plan",
        "welding_plan",
        "machining_plan",
        "additive_plan",
        "textile_plan",
        "molding_plan",
        "multiphysics_model",
        "tolerance_model",
        "failure_analyze",
        "manufacturing_plan",
        "inspection_plan",
    ],
    "chemistry": [
        "chemistry_research",
        "property_lookup",
        "protocol_draft",
        "inventory_check",
        "cheminformatics",
        "quantum_workflow",
        "molecular_simulation",
        "thermo_kinetics",
        "spectra_analyze",
        "analytical_validate",
        "electrochemistry",
        "process_scaleup",
        "tool_discover",
        "chemistry_refresh",
        "chemistry_promote",
    ],
    "ai_ml": [
        "dataset_engineer",
        "retrieval_engineer",
        "model_select",
        "adapter_train",
        "model_distill",
        "speech_recognize",
        "speech_synthesize",
        "vision_understand",
        "image_create",
        "world_model",
        "simulate_domain",
        "reason_verify",
        "evaluate_model",
        "accelerator_job",
        "promote_release",
    ],
    "git_release": [
        "work_recover",
        "conflict_resolve",
        "helper_discover",
        "helper_select",
        "helper_build",
        "release_plan",
        "pipeline_triage",
        "artifact_build",
        "artifact_verify",
        "deploy_orchestrate",
        "release_recover",
    ],
}

DEFAULTS = "---\n{}\n"

META_TEMPLATE = """---
galaxy_info:
  role_name: {name}
  author: Agentic Harness Agent
  description: {name} role for the {col} service.
  license: MIT
  min_ansible_version: "2.14"
  galaxy_tags: [{col}]
dependencies: []
"""

TASKS_TEMPLATE = """---
# {name} role tasks — {col} service.

- name: Load {name} defaults
  ansible.builtin.include_vars:
    file: "{{{{ role_path }}}}/defaults/main.yml"
    name: _{name}_defaults

- name: Reference {name} service API
  ansible.builtin.debug:
    msg: "{name} routes via the {col} service API (/api/{col}/{name})."
"""


def scaffold_role(col: str, name: str, force: bool) -> str:
    base = COLLECTIONS / col / "roles" / name
    meta_file = base / "meta" / "main.yml"
    if meta_file.exists() and not force:
        return "skip"
    (base / "defaults").mkdir(parents=True, exist_ok=True)
    (base / "meta").mkdir(parents=True, exist_ok=True)
    (base / "tasks").mkdir(parents=True, exist_ok=True)
    (base / "defaults" / "main.yml").write_text(DEFAULTS)
    (base / "meta" / "main.yml").write_text(META_TEMPLATE.format(name=name, col=col))
    (base / "tasks" / "main.yml").write_text(TASKS_TEMPLATE.format(name=name, col=col))
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite existing roles")
    parser.add_argument("--collection", help="limit to one collection (materials|chemistry|...)")
    args = parser.parse_args()

    targets = ROLES
    if args.collection:
        if args.collection not in ROLES:
            print(f"unknown collection: {args.collection}", file=sys.stderr)
            return 2
        targets = {args.collection: ROLES[args.collection]}

    per_collection: dict[str, dict[str, int]] = {}
    for col, roles in targets.items():
        counts = {"created": 0, "skipped": 0}
        for name in roles:
            outcome = scaffold_role(col, name, args.force)
            counts["created" if outcome == "created" else "skipped"] += 1
        per_collection[col] = counts

    for col, counts in per_collection.items():
        print(f"{col}: created={counts['created']} skipped={counts['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
