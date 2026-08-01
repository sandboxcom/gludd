"""Resolve the behavioral source behind a lean TypeScript plugin facade."""

from __future__ import annotations

import re
from pathlib import Path

_LOCAL_IMPL_IMPORT = re.compile(
    r"\bfrom\s+[\"'](?P<path>\./impl/[A-Za-z0-9_.-]+\.ts)[\"']"
)


def plugin_contract_source(plugin_path: Path) -> str:
    """Return a facade plus each directly imported local implementation.

    Only ``./impl/*.ts`` imports are followed. This keeps structural checkers
    aware of the runtime contract without allowing path traversal or folding
    unrelated shared-library internals into a plugin's contract.
    """

    facade = plugin_path.resolve(strict=True)
    source = facade.read_text()
    matches = list(_LOCAL_IMPL_IMPORT.finditer(source))
    if not matches:
        return source

    chunks = [source]
    impl_root = (facade.parent / "impl").resolve(strict=True)
    seen: set[Path] = set()
    for match in matches:
        implementation = (facade.parent / match.group("path")).resolve(strict=True)
        if implementation.parent != impl_root or implementation in seen:
            continue
        seen.add(implementation)
        chunks.append(implementation.read_text())

    return "\n".join(chunks)
