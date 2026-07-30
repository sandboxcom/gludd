"""Dispatcher bridge between ansible roles and ``general_ludd.chemistry.core``.

Roles call this module via ``ansible.builtin.command``; it loads the service
API from ``src/general_ludd/chemistry/core.py`` (falling back to a file-path
import inside the worktree) and returns a JSON result on stdout. Keeping the
dispatch logic here means task files stay declarative.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys


def _load_core():
    try:
        from general_ludd.chemistry import core  # type: ignore

        return core
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = []
        repo_root = os.environ.get("GLUDD_REPO_ROOT")
        if repo_root:
            candidates.append(os.path.join(repo_root, "src", "general_ludd", "chemistry", "core.py"))
        candidates.append(
            os.path.abspath(
                os.path.join(here, "..", "..", "..", "..", "..", "..", "src", "general_ludd", "chemistry", "core.py")
            )
        )
        for path in candidates:
            if os.path.isfile(path):
                spec = importlib.util.spec_from_file_location("chemistry_core_dispatch", path)
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
    raise RuntimeError("could not locate general_ludd.chemistry.core")


def _parse_json_arg(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def main():
    core = _load_core()
    action = os.environ.get("CHEMISTRY_ACTION", "route")
    raw = os.environ.get("CHEMISTRY_INPUT", "{}")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "failed", "errors": [{"code": "chem.bad_json", "message": str(exc)}]}))
        return 1

    if action == "route":
        result = core.route_chemistry_task(payload)
    elif action == "identity":
        result = core.resolve_identity(payload)
    elif action == "reaction":
        result = core.analyze_reaction(payload)
    elif action == "molar_mass":
        result = core.molar_mass(payload.get("formula", ""))
    elif action == "moles":
        result = core.stoichiometry_moles(
            mass_g=float(payload.get("mass_g", 0.0)),
            formula=payload.get("formula", ""),
            mass_uncertainty=float(payload.get("mass_uncertainty", 0.0)),
        )
    elif action == "dilution":
        result = core.stoichiometry_dilution(
            payload.get("c1"),
            payload.get("v1"),
            payload.get("c2"),
            payload.get("v2"),
        )
    elif action == "yield":
        result = core.stoichiometry_yield(
            actual_g=float(payload.get("actual_g", 0.0)),
            theoretical_g=float(payload.get("theoretical_g", 0.0)),
            actual_unc=float(payload.get("actual_unc", 0.0)),
            theoretical_unc=float(payload.get("theoretical_unc", 0.0)),
        )
    elif action == "hazard":
        result = core.screen_hazards(payload)
    else:
        result = {
            "status": "refused",
            "errors": [{"code": "chem.unknown_action", "message": f"unknown action {action!r}"}],
        }
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
