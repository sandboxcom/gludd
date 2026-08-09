"""Count YAML parse errors under collections/ without short-circuit."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS_ROOT = ROOT / "collections" / "ansible_collections"

violations = 0
for yf in sorted(COLLECTIONS_ROOT.rglob("*.yml")):
    if not yf.is_file():
        continue
    try:
        yaml.safe_load(yf.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        violations += 1

print(f"Total YAML parse errors: {violations}")
sys.exit(0)
