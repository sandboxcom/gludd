#!/usr/bin/env python3
"""Fail-closed hygiene checks for tracked, generator-owned documentation.

The MCP generators own one Markdown reference and two structured manifests.
This checker validates their serialization postconditions without regenerating or
mutating them.  Inventory comes from Git's index so a missing/untracked canonical
artifact cannot be mistaken for a successful empty scan.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parent.parent
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_ARTIFACTS = 32
GIT_TIMEOUT_SECONDS = 10

ArtifactKind = Literal["markdown", "json", "yaml"]
SUPPORTED_KINDS = frozenset({"markdown", "json", "yaml"})


@dataclass(frozen=True, slots=True)
class Artifact:
    """A repository-relative generated artifact and its serialization."""

    relative_path: str
    kind: ArtifactKind


@dataclass(frozen=True, slots=True)
class Finding:
    """A deterministic artifact hygiene violation."""

    path: str
    line: int | None
    message: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line is not None else self.path
        return f"{location}: {self.message}"


class InventoryError(RuntimeError):
    """Raised when the canonical Git-tracked inventory cannot be proven."""


ARTIFACTS: tuple[Artifact, ...] = (
    Artifact("docs/MCP_TOOL_REFERENCE.md", "markdown"),
    Artifact("docs/MCP_TOOLS_MANIFEST.json", "json"),
    Artifact("docs/MCP_TOOLS_TOPICS.yml", "yaml"),
)


def _validate_specs(specs: Sequence[Artifact]) -> None:
    if not specs:
        raise InventoryError("canonical artifact inventory is empty")
    if len(specs) > MAX_ARTIFACTS:
        raise InventoryError(
            f"canonical artifact inventory exceeds {MAX_ARTIFACTS} item limit",
        )

    paths: set[str] = set()
    for artifact in specs:
        candidate = Path(artifact.relative_path)
        if (
            not artifact.relative_path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or str(candidate) != artifact.relative_path
        ):
            raise InventoryError(
                f"unsafe canonical artifact path: {artifact.relative_path!r}",
            )
        if artifact.kind not in SUPPORTED_KINDS:
            raise InventoryError(
                f"unsupported canonical artifact kind: {artifact.kind}",
            )
        if artifact.relative_path in paths:
            raise InventoryError(
                f"duplicate canonical artifact path: {artifact.relative_path}",
            )
        paths.add(artifact.relative_path)


def discover_tracked_artifacts(
    root: Path,
    specs: Sequence[Artifact] = ARTIFACTS,
) -> list[tuple[Path, ArtifactKind]]:
    """Resolve canonical artifacts only when Git proves every path is tracked."""

    _validate_specs(specs)
    expected = [artifact.relative_path for artifact in specs]
    command = ["git", "-C", str(root), "ls-files", "-z", "--", *expected]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InventoryError(
            f"Git artifact inventory timed out after {GIT_TIMEOUT_SECONDS}s",
        ) from exc
    except OSError as exc:
        raise InventoryError(f"Git artifact inventory failed: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        suffix = f": {detail}" if detail else ""
        raise InventoryError(
            f"Git artifact inventory exited {result.returncode}{suffix}",
        )
    if result.stdout and not result.stdout.endswith(b"\x00"):
        raise InventoryError("Git artifact inventory returned unterminated output")
    try:
        decoded = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError("Git artifact inventory returned non-UTF-8 paths") from exc

    tracked = {path for path in decoded.split("\x00") if path}
    expected_set = set(expected)
    missing = sorted(expected_set - tracked)
    unexpected = sorted(tracked - expected_set)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"untracked/missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise InventoryError("Git artifact inventory mismatch: " + " ".join(details))

    return [(root / artifact.relative_path, artifact.kind) for artifact in specs]


def _finding(path: Path, message: str, line: int | None = None) -> Finding:
    return Finding(str(path), line, message)


def _read_artifact(path: Path) -> tuple[bytes | None, list[Finding]]:
    if path.is_symlink():
        return None, [_finding(path, "artifact must not be a symlink")]
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
    except OSError:
        return None, [_finding(path, "artifact is missing or unreadable")]
    if len(data) > MAX_ARTIFACT_BYTES:
        return None, [
            _finding(path, f"artifact exceeds {MAX_ARTIFACT_BYTES} byte limit"),
        ]
    return data, []


def _parse_finding(path: Path, text: str, kind: str) -> Finding | None:
    if kind == "markdown":
        # CommonMark treats arbitrary Unicode text as a valid document. Strict
        # UTF-8 decoding above is therefore the relevant parseability boundary.
        return None
    if kind == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return _finding(path, f"invalid JSON: {exc.msg}", exc.lineno)
        return None
    if kind == "yaml":
        try:
            list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = int(mark.line) + 1 if mark is not None else None
            problem = getattr(exc, "problem", None) or type(exc).__name__
            return _finding(path, f"invalid YAML: {problem}", line)
        return None
    return _finding(path, f"unsupported artifact kind: {kind}")


def check_artifact(path: Path, kind: str) -> list[Finding]:
    """Return every bounded, deterministic hygiene violation for one artifact."""

    if kind not in SUPPORTED_KINDS:
        return [_finding(path, f"unsupported artifact kind: {kind}")]

    data, read_findings = _read_artifact(path)
    if data is None:
        return read_findings
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [_finding(path, "not valid UTF-8")]
    if "\x00" in text:
        return [_finding(path, "contains a NUL byte")]

    findings: list[Finding] = []
    if "\r" in text:
        first_cr = text.index("\r")
        line = text.count("\n", 0, first_cr) + 1
        findings.append(_finding(path, "non-LF line ending", line))
    for line_number, line_text in enumerate(text.splitlines(), start=1):
        if line_text.endswith((" ", "\t")):
            findings.append(_finding(path, "trailing whitespace", line_number))
    if not data.endswith(b"\n"):
        findings.append(_finding(path, "missing final newline"))

    parse_finding = _parse_finding(path, text, kind)
    if parse_finding is not None:
        findings.append(parse_finding)
    return sorted(
        findings,
        key=lambda finding: (
            finding.path,
            finding.line if finding.line is not None else 0,
            finding.message,
        ),
    )


def check_artifacts(
    artifacts: Sequence[tuple[Path, ArtifactKind]],
) -> list[Finding]:
    """Check a bounded artifact inventory and return globally sorted findings."""

    if len(artifacts) > MAX_ARTIFACTS:
        return [
            Finding(
                "<inventory>",
                None,
                f"artifact inventory exceeds {MAX_ARTIFACTS} item limit",
            ),
        ]
    findings = [
        finding
        for path, kind in artifacts
        for finding in check_artifact(path, kind)
    ]
    return sorted(
        findings,
        key=lambda finding: (
            finding.path,
            finding.line if finding.line is not None else 0,
            finding.message,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate tracked generated Markdown/JSON/YAML artifacts.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root containing the canonical generated artifacts",
    )
    args = parser.parse_args(argv)

    try:
        artifacts = discover_tracked_artifacts(args.root)
    except InventoryError as exc:
        print(f"generated-artifact-hygiene: ERROR: {exc}", file=sys.stderr)
        return 2

    findings = check_artifacts(artifacts)
    for finding in findings:
        print(finding.render())
    if findings:
        print(
            "generated-artifact-hygiene: "
            f"FAIL violations={len(findings)} artifacts={len(artifacts)}",
            file=sys.stderr,
        )
        return 1

    print(f"generated-artifact-hygiene: PASS artifacts={len(artifacts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
